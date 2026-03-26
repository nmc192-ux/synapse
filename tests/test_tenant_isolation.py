from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from synapse.api.routes import get_authenticator, get_orchestrator, router
from synapse.config import Settings
from synapse.models.agent import AgentDefinition, AgentKind, AgentDiscoveryEntry
from synapse.models.capability import CapabilityAdvertisementRequest, CapabilityRecord
from synapse.models.runtime_state import BrowserSessionState, RuntimeCheckpoint
from synapse.models.run import RunState
from synapse.runtime.session_profiles import SessionProfile, SessionProfileLoadRequest
from synapse.security.auth import Authenticator
from synapse.security.policies import PrincipalType, Scope


class _TenantIsolationOrchestrator:
    def __init__(self) -> None:
        self.agents = {
            "agent-a": AgentDefinition(
                agent_id="agent-a",
                kind=AgentKind.CUSTOM,
                name="Agent A",
                organization_id="org-1",
                project_id="project-a",
            ),
            "agent-b": AgentDefinition(
                agent_id="agent-b",
                kind=AgentKind.CUSTOM,
                name="Agent B",
                organization_id="org-1",
                project_id="project-b",
            ),
        }
        self.runs = {
            "run-a": RunState(
                run_id="run-a",
                task_id="task-a",
                agent_id="agent-a",
                project_id="project-a",
                correlation_id="corr-a",
                metadata={},
            ),
            "run-b": RunState(
                run_id="run-b",
                task_id="task-b",
                agent_id="agent-b",
                project_id="project-b",
                correlation_id="corr-b",
                metadata={},
            ),
        }
        self.sessions = {
            "session-a": BrowserSessionState(session_id="session-a", agent_id="agent-a", run_id="run-a", project_id="project-a"),
            "session-b": BrowserSessionState(session_id="session-b", agent_id="agent-b", run_id="run-b", project_id="project-b"),
        }
        now = datetime.now(timezone.utc)
        self.profiles = {
            "profile-a": SessionProfile(profile_id="profile-a", name="Profile A", project_id="project-a", organization_id="org-1", agent_id="agent-a"),
            "profile-b": SessionProfile(profile_id="profile-b", name="Profile B", project_id="project-b", organization_id="org-1", agent_id="agent-b"),
        }
        self.checkpoints = {
            "checkpoint-a": RuntimeCheckpoint(
                checkpoint_id="checkpoint-a",
                task_id="task-a",
                agent_id="agent-a",
                run_id="run-a",
                project_id="project-a",
                current_goal="Goal A",
                created_at=now,
                updated_at=now,
            ),
            "checkpoint-b": RuntimeCheckpoint(
                checkpoint_id="checkpoint-b",
                task_id="task-b",
                agent_id="agent-b",
                run_id="run-b",
                project_id="project-b",
                current_goal="Goal B",
                created_at=now,
                updated_at=now,
            ),
        }
        self.capabilities = [
            CapabilityRecord(
                agent_id="agent-a",
                capabilities=["browse"],
                description="Project A browser",
                endpoint="http://agent-a",
                latency=120,
                availability=True,
                reputation=0.8,
                updated_at=now,
            ),
            CapabilityRecord(
                agent_id="agent-b",
                capabilities=["browse"],
                description="Project B browser",
                endpoint="http://agent-b",
                latency=140,
                availability=True,
                reputation=0.6,
                updated_at=now,
            ),
        ]

    async def list_runs(self, agent_id: str | None = None, task_id: str | None = None):
        runs = list(self.runs.values())
        if agent_id is not None:
            runs = [run for run in runs if run.agent_id == agent_id]
        if task_id is not None:
            runs = [run for run in runs if run.task_id == task_id]
        return runs

    async def get_run(self, run_id: str) -> RunState:
        return self.runs[run_id]

    async def get_run_events(self, run_id: str):
        run = self.runs[run_id]
        return [{"run_id": run.run_id, "project_id": run.project_id, "task_id": run.task_id}]

    async def list_sessions(self, agent_id: str | None = None):
        sessions = list(self.sessions.values())
        if agent_id is not None:
            sessions = [session for session in sessions if session.agent_id == agent_id]
        return sessions

    async def get_session(self, session_id: str) -> BrowserSessionState:
        return self.sessions[session_id]

    async def list_session_profiles(self, agent_id: str | None = None):
        profiles = list(self.profiles.values())
        if agent_id is not None:
            profiles = [profile for profile in profiles if profile.agent_id == agent_id]
        return profiles

    async def load_session_profile(self, profile_id: str, request: SessionProfileLoadRequest):
        return self.profiles[profile_id]

    async def delete_session_profile(self, profile_id: str) -> None:
        self.profiles.pop(profile_id, None)

    async def list_checkpoints(self, agent_id: str | None = None, task_id: str | None = None):
        checkpoints = list(self.checkpoints.values())
        if agent_id is not None:
            checkpoints = [checkpoint for checkpoint in checkpoints if checkpoint.agent_id == agent_id]
        if task_id is not None:
            checkpoints = [checkpoint for checkpoint in checkpoints if checkpoint.task_id == task_id]
        return checkpoints

    async def get_checkpoint(self, checkpoint_id: str) -> RuntimeCheckpoint:
        return self.checkpoints[checkpoint_id]

    async def get_persisted_agent(self, agent_id: str) -> AgentDefinition:
        return self.agents[agent_id]

    async def get_persisted_agents(self) -> list[AgentDefinition]:
        return list(self.agents.values())

    async def list_capabilities(self) -> list[CapabilityRecord]:
        return list(self.capabilities)

    async def advertise_capabilities(self, request: CapabilityAdvertisementRequest) -> CapabilityRecord:
        return CapabilityRecord(
            agent_id=request.agent_id,
            capabilities=request.capabilities,
            description=request.description,
            endpoint=request.endpoint,
            latency_ms=request.latency_ms,
            availability=request.availability,
            reputation=request.reputation,
        )

    async def find_agents(self, capability: str) -> list[AgentDiscoveryEntry]:
        return [
            AgentDiscoveryEntry(id="agent-a", score=0.8, capabilities=["browse"], reputation=0.8, latency=120, availability=True),
            AgentDiscoveryEntry(id="agent-b", score=0.6, capabilities=["browse"], reputation=0.6, latency=140, availability=True),
        ]


def _build_client() -> tuple[TestClient, Authenticator]:
    authenticator = Authenticator(
        Settings(
            auth_required=True,
            jwt_secret="tenant-secret",
            jwt_issuer="synapse-test",
            jwt_audience="synapse-test-api",
        )
    )
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_orchestrator] = lambda: _TenantIsolationOrchestrator()
    app.dependency_overrides[get_authenticator] = lambda: authenticator
    return TestClient(app), authenticator


def _headers(authenticator: Authenticator, *, scopes: list[str]) -> dict[str, str]:
    token = authenticator.issue_token(
        subject="operator-a",
        principal_type=PrincipalType.OPERATOR,
        scopes=scopes,
        organization_id="org-1",
        project_id="project-a",
    )
    return {"Authorization": f"Bearer {token}"}


def test_cross_project_run_and_event_access_is_denied() -> None:
    client, authenticator = _build_client()
    headers = _headers(authenticator, scopes=[Scope.TASKS_READ.value])

    runs = client.get("/api/runs", headers=headers)
    assert runs.status_code == 200
    assert [run["run_id"] for run in runs.json()] == ["run-a"]

    denied = client.get("/api/runs/run-b", headers=headers)
    assert denied.status_code == 403

    denied_events = client.get("/api/runs/run-b/events", headers=headers)
    assert denied_events.status_code == 403


def test_cross_project_session_profile_and_checkpoint_access_is_denied() -> None:
    client, authenticator = _build_client()
    read_headers = _headers(authenticator, scopes=[Scope.TASKS_READ.value, Scope.BROWSER_CONTROL.value])

    sessions = client.get("/api/sessions", headers=read_headers)
    assert sessions.status_code == 200
    assert [session["session_id"] for session in sessions.json()] == ["session-a"]

    denied_session = client.get("/api/sessions/session-b", headers=read_headers)
    assert denied_session.status_code == 403

    profiles = client.get("/api/profiles", headers=read_headers)
    assert profiles.status_code == 200
    assert [profile["profile_id"] for profile in profiles.json()] == ["profile-a"]

    denied_profile = client.post("/api/profiles/profile-b/load", json={"run_id": "run-b"}, headers=read_headers)
    assert denied_profile.status_code == 403

    checkpoints = client.get("/api/checkpoints", headers=read_headers)
    assert checkpoints.status_code == 200
    assert [checkpoint["checkpoint_id"] for checkpoint in checkpoints.json()] == ["checkpoint-a"]

    denied_checkpoint = client.get("/api/checkpoints/checkpoint-b", headers=read_headers)
    assert denied_checkpoint.status_code == 403


def test_capability_visibility_and_cross_project_advertisement_are_scoped() -> None:
    client, authenticator = _build_client()
    read_headers = _headers(authenticator, scopes=[Scope.TASKS_READ.value, Scope.A2A_RECEIVE.value])
    admin_headers = _headers(authenticator, scopes=[Scope.ADMIN.value])

    capabilities = client.get("/api/agents/capabilities", headers=read_headers)
    assert capabilities.status_code == 200
    assert [record["agent_id"] for record in capabilities.json()] == ["agent-a"]

    discover = client.get("/api/agents/find?capability=browse", headers=read_headers)
    assert discover.status_code == 200
    assert [entry["id"] for entry in discover.json()] == ["agent-a"]

    denied = client.post(
        "/api/agents/capabilities",
        json={
            "agent_id": "agent-b",
            "capabilities": ["browse"],
            "description": "cross-project",
            "endpoint": "http://agent-b",
            "latency": 90,
            "availability": True,
            "reputation": 0.5,
        },
        headers=admin_headers,
    )
    assert denied.status_code == 403
