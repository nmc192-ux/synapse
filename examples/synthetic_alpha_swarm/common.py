from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sys
import time
from typing import Any, Callable
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import httpx

from synapse.models.agent import (
    AgentChallengePolicy,
    AgentDefinition,
    AgentExecutionLimits,
    AgentKind,
    AgentSecurityPolicy,
)
from synapse.models.platform import (
    APIKeyCreateRequest,
    APIKeyIssueResponse,
    Organization,
    OrganizationCreateRequest,
    PlatformUser,
    Project,
    ProjectCreateRequest,
    UserCreateRequest,
)
from synapse.models.run import RunState
from synapse.models.runtime_state import BrowserWorkerState, OperatorInterventionRecord
from synapse.models.task import TaskRequest
from synapse.sdk import SynapseClient

DEFAULT_SAFE_DOMAINS = [
    "docs.python.org",
    "fastapi.tiangolo.com",
    "developer.mozilla.org",
    "en.wikipedia.org",
    "arxiv.org",
    "github.com",
    "raw.githubusercontent.com",
]

DEFAULT_SAFE_URLS = [
    "https://docs.python.org/3/library/pathlib.html",
    "https://fastapi.tiangolo.com/tutorial/",
    "https://en.wikipedia.org/wiki/Web_browser",
    "https://arxiv.org/abs/1706.03762",
    "https://github.com/openai/openai-python/blob/main/README.md",
]

ROLE_SCOPES: dict[str, list[str]] = {
    "director": ["admin", "tasks:read", "tasks:write", "browser:control"],
    "browser-runner-1": ["admin", "tasks:read", "tasks:write", "browser:control", "memory:read", "memory:write"],
    "browser-runner-2": ["admin", "tasks:read", "tasks:write", "browser:control", "memory:read", "memory:write"],
    "auditor": ["admin", "tasks:read"],
    "reporter": ["admin", "tasks:read"],
    "chaos-monkey": ["admin", "tasks:read", "tasks:write", "browser:control"],
}


@dataclass(frozen=True)
class ProjectCredentials:
    alias: str
    project_id: str
    api_key: str


def env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or not value.strip():
        raise RuntimeError(f"{name} is required.")
    return value.strip()


def optional_env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    if value is None:
        return None
    value = value.strip()
    return value or None


def csv_env(name: str, default_items: list[str]) -> list[str]:
    raw = optional_env(name)
    if raw is None:
        return list(default_items)
    return [item.strip() for item in raw.split(",") if item.strip()]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp_slug() -> str:
    return utc_now().strftime("%Y%m%dT%H%M%SZ")


def runtime_dir() -> Path:
    default_path = Path(__file__).resolve().parent / "runtime"
    raw = optional_env("SYNTHETIC_ALPHA_SWARM_OUTPUT_DIR", str(default_path)) or str(default_path)
    path = Path(raw)
    if not path.is_absolute():
        path = REPO_ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_safe_domains() -> list[str]:
    return csv_env("SYNTHETIC_ALPHA_SWARM_SAFE_DOMAINS", DEFAULT_SAFE_DOMAINS)


def default_safe_urls() -> list[str]:
    return csv_env("SYNTHETIC_ALPHA_SWARM_SAFE_URLS", DEFAULT_SAFE_URLS)


def build_project_credentials(alias: str) -> ProjectCredentials:
    prefix = f"SYNTHETIC_ALPHA_SWARM_{alias.upper()}"
    return ProjectCredentials(
        alias=alias,
        project_id=env(f"{prefix}_PROJECT_ID"),
        api_key=env(f"{prefix}_API_KEY"),
    )


def build_project_client(alias: str, agent_id: str | None = None) -> SynapseClient:
    creds = build_project_credentials(alias)
    return SynapseClient(
        base_url=env("SYNTHETIC_ALPHA_SWARM_BASE_URL", "http://127.0.0.1:8000"),
        api_key=creds.api_key,
        project_id=creds.project_id,
        agent_id=agent_id,
    )


class PlatformAPI:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        bearer_token: str | None = None,
        project_id: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)
        self.project_id = project_id
        self._api_key = api_key
        self._bearer_token = bearer_token
        self._apply_headers()

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "PlatformAPI":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def list_organizations(self) -> list[Organization]:
        response = self._request("GET", "/api/platform/organizations")
        return [Organization.model_validate(item) for item in response.json()]

    def create_organization(self, request: OrganizationCreateRequest) -> Organization:
        response = self._request("POST", "/api/platform/organizations", json=request.model_dump(mode="json"))
        return Organization.model_validate(response.json())

    def list_projects(self, organization_id: str | None = None) -> list[Project]:
        params = {"organization_id": organization_id} if organization_id else None
        response = self._request("GET", "/api/platform/projects", params=params)
        return [Project.model_validate(item) for item in response.json()]

    def create_project(self, request: ProjectCreateRequest) -> Project:
        response = self._request("POST", "/api/platform/projects", json=request.model_dump(mode="json"))
        return Project.model_validate(response.json())

    def list_users(self, organization_id: str | None = None, project_id: str | None = None) -> list[PlatformUser]:
        params: dict[str, str] = {}
        if organization_id:
            params["organization_id"] = organization_id
        if project_id:
            params["project_id"] = project_id
        response = self._request("GET", "/api/platform/users", params=params or None)
        return [PlatformUser.model_validate(item) for item in response.json()]

    def create_user(self, request: UserCreateRequest) -> PlatformUser:
        response = self._request("POST", "/api/platform/users", json=request.model_dump(mode="json"))
        return PlatformUser.model_validate(response.json())

    def create_api_key(self, request: APIKeyCreateRequest) -> APIKeyIssueResponse:
        response = self._request("POST", "/api/platform/api-keys", json=request.model_dump(mode="json"))
        return APIKeyIssueResponse.model_validate(response.json())

    def create_project_api_key(self, project_id: str, request: APIKeyCreateRequest) -> APIKeyIssueResponse:
        response = self._request(
            "POST",
            f"/api/cloud/projects/{project_id}/api-keys",
            json=request.model_dump(mode="json"),
        )
        return APIKeyIssueResponse.model_validate(response.json())

    def list_workers(self) -> list[BrowserWorkerState]:
        response = self._request("GET", "/api/cloud/admin/workers")
        return [BrowserWorkerState.model_validate(item) for item in response.json()]

    def register_agent(self, definition: AgentDefinition) -> AgentDefinition:
        response = self._request("POST", "/api/agents", json=definition.model_dump(mode="json"))
        return AgentDefinition.model_validate(response.json())

    def create_project_run(self, project_id: str, request: TaskRequest) -> RunState:
        response = self._request(
            "POST",
            f"/api/cloud/projects/{project_id}/runs",
            json=request.model_dump(mode="json"),
        )
        return RunState.model_validate(response.json())

    def list_runs(self) -> list[RunState]:
        response = self._request("GET", "/api/runs")
        return [RunState.model_validate(item) for item in response.json()]

    def list_interventions(self) -> list[OperatorInterventionRecord]:
        response = self._request("GET", "/api/interventions")
        return [OperatorInterventionRecord.model_validate(item) for item in response.json()]

    def list_audit_logs(self, project_id: str) -> list[dict[str, Any]]:
        response = self._request("GET", f"/api/cloud/projects/{project_id}/audit-logs")
        return list(response.json())

    def _apply_headers(self) -> None:
        headers: dict[str, str] = {}
        if self.project_id:
            headers["X-Synapse-Project-Id"] = self.project_id
        if self._bearer_token:
            headers["Authorization"] = f"Bearer {self._bearer_token}"
        elif self._api_key:
            headers["X-API-Key"] = self._api_key
        self._http.headers.clear()
        self._http.headers.update(headers)

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self._http.request(method, path, **kwargs)
        response.raise_for_status()
        return response


def build_project_api(alias: str) -> PlatformAPI:
    creds = build_project_credentials(alias)
    return PlatformAPI(
        base_url=env("SYNTHETIC_ALPHA_SWARM_BASE_URL", "http://127.0.0.1:8000"),
        api_key=creds.api_key,
        project_id=creds.project_id,
    )


def build_admin_api() -> PlatformAPI:
    return PlatformAPI(
        base_url=env("SYNTHETIC_ALPHA_SWARM_BASE_URL", "http://127.0.0.1:8000"),
        api_key=optional_env("SYNTHETIC_ALPHA_SWARM_ADMIN_API_KEY"),
        bearer_token=optional_env("SYNTHETIC_ALPHA_SWARM_ADMIN_BEARER_TOKEN"),
        project_id=optional_env("SYNTHETIC_ALPHA_SWARM_ADMIN_PROJECT_ID"),
    )


def build_agent_definition(
    *,
    agent_id: str,
    name: str,
    description: str,
    kind: AgentKind,
    role: str,
    allowed_tools: list[str] | None = None,
    extra_tags: list[str] | None = None,
    challenge_policy: AgentChallengePolicy = AgentChallengePolicy.ESCALATE_TO_OPERATOR,
) -> AgentDefinition:
    return AgentDefinition(
        agent_id=agent_id,
        kind=kind,
        name=name,
        description=description,
        capability_tags=["synthetic-alpha", "swarm", role, *(extra_tags or [])],
        security=AgentSecurityPolicy(
            allowed_domains=default_safe_domains(),
            allowed_tools=allowed_tools or [],
            uploads_allowed=False,
            downloads_allowed=False,
            dangerous_action_requires_approval=True,
            challenge_policy=challenge_policy,
            max_cross_domain_jumps=2,
        ),
        limits=AgentExecutionLimits(
            max_steps=24,
            max_pages=8,
            max_tool_calls=8,
            max_runtime_seconds=180,
            max_tokens=16000,
            max_memory_writes=24,
        ),
        metadata={
            "role": role,
            "synthetic_alpha": "true",
            "safe_domains": ",".join(default_safe_domains()),
        },
    )


def register_role_agent(alias: str, definition: AgentDefinition) -> AgentDefinition:
    with build_project_api(alias) as api:
        return api.register_agent(definition)


def build_run_plan(
    *,
    project_alias: str,
    agent_id: str,
    label: str,
    goal: str,
    start_url: str,
    context_label: str,
    expected_outcome: str = "observe",
    extra_constraints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "plan_id": f"{label}-{uuid4().hex[:8]}",
        "project_alias": project_alias,
        "task_request": TaskRequest(
            task_id=f"{label}-{uuid4().hex[:10]}",
            agent_id=agent_id,
            goal=goal,
            start_url=start_url,
            constraints={
                "synthetic_alpha": True,
                "context_label": context_label,
                "expected_outcome": expected_outcome,
                "safe_domains": default_safe_domains(),
                **(extra_constraints or {}),
            },
        ),
    }


def write_json_artifact(name: str, payload: Any) -> Path:
    target = runtime_dir() / name
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return target


def write_text_artifact(name: str, payload: str) -> Path:
    target = runtime_dir() / name
    target.write_text(payload)
    return target


def summarize_run(run: RunState) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "task_id": run.task_id,
        "agent_id": run.agent_id,
        "project_id": run.project_id,
        "status": run.status.value,
        "current_phase": run.current_phase,
        "current_step": run.current_step,
        "metadata": run.metadata,
    }


def parse_loop_args(description: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--once", action="store_true", help="Run a single iteration and exit.")
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=float(optional_env("SYNTHETIC_ALPHA_SWARM_INTERVAL_SECONDS", "300") or "300"),
        help="Loop interval for continuous execution.",
    )
    return parser.parse_args()


def run_forever(step: Callable[[], None], *, once: bool, interval_seconds: float) -> None:
    while True:
        started_at = time.monotonic()
        step()
        if once:
            return
        elapsed = time.monotonic() - started_at
        time.sleep(max(0.0, interval_seconds - elapsed))


def role_project_alias(role_name: str) -> str:
    if role_name in {"browser-runner-2", "chaos-monkey"}:
        return "chaos"
    return "steady"
