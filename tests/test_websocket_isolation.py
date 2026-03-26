from __future__ import annotations

from tests.test_websocket_tenant_delivery import (
    _build_app,
)
from synapse.security.policies import PrincipalType, Scope


def test_general_websocket_feed_is_isolated_between_projects() -> None:
    client, authenticator, _ = _build_app()
    token_a = authenticator.issue_token(
        subject="operator-a",
        principal_type=PrincipalType.OPERATOR,
        scopes=[Scope.TASKS_READ.value],
        organization_id="org-1",
        project_id="project-a",
    )
    token_b = authenticator.issue_token(
        subject="operator-b",
        principal_type=PrincipalType.OPERATOR,
        scopes=[Scope.TASKS_READ.value],
        organization_id="org-1",
        project_id="project-b",
    )

    with client.websocket_connect(f"/api/ws?token={token_a}") as ws_a:
        with client.websocket_connect(f"/api/ws?token={token_b}") as ws_b:
            response = client.post(
                "/emit",
                json={
                    "event_type": "task.updated",
                    "organization_id": "org-1",
                    "project_id": "project-a",
                    "run_id": "run-project-a",
                    "agent_id": "agent-a",
                    "correlation_id": "corr-a",
                    "payload": {"label": "project-a-only"},
                },
            )
            assert response.status_code == 200

            event = ws_a.receive_json()
            assert event["project_id"] == "project-a"
            assert event["payload"]["label"] == "project-a-only"


def test_websocket_run_filter_only_delivers_matching_run() -> None:
    client, authenticator, _ = _build_app()
    token = authenticator.issue_token(
        subject="operator-a",
        principal_type=PrincipalType.OPERATOR,
        scopes=[Scope.TASKS_READ.value],
        organization_id="org-1",
        project_id="project-a",
    )

    with client.websocket_connect(f"/api/ws?token={token}&run_id=run-project-a") as websocket:
        client.post(
            "/emit",
            json={
                "event_type": "task.updated",
                "organization_id": "org-1",
                "project_id": "project-a",
                "run_id": "run-project-b",
                "agent_id": "agent-b",
                "correlation_id": "corr-b",
                "payload": {"label": "ignore-me"},
            },
        )
        client.post(
            "/emit",
            json={
                "event_type": "task.updated",
                "organization_id": "org-1",
                "project_id": "project-a",
                "run_id": "run-project-a",
                "agent_id": "agent-a",
                "correlation_id": "corr-a",
                "payload": {"label": "deliver-me"},
            },
        )

        event = websocket.receive_json()
        assert event["run_id"] == "run-project-a"
        assert event["payload"]["label"] == "deliver-me"
