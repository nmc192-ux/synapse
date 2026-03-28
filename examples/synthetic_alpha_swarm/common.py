from __future__ import annotations

import argparse
import json
import os
import random
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import time
from typing import Any
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import httpx

from synapse.models.a2a import A2AMessageType, AgentWireMessage
from synapse.models.agent import (
    AgentChallengePolicy,
    AgentDefinition,
    AgentExecutionLimits,
    AgentKind,
    AgentRateLimits,
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
    "https://developer.mozilla.org/en-US/blog/",
    "https://en.wikipedia.org/wiki/Web_browser",
    "https://arxiv.org/abs/1706.03762",
    "https://github.com/openai/openai-python/blob/main/README.md",
]

ROLE_SCOPES: dict[str, list[str]] = {
    "director": ["admin", "tasks:read", "tasks:write", "browser:control", "a2a:send", "a2a:receive"],
    "browser-runner-1": ["admin", "tasks:read", "tasks:write", "browser:control", "memory:read", "memory:write", "a2a:send", "a2a:receive"],
    "browser-runner-2": ["admin", "tasks:read", "tasks:write", "browser:control", "memory:read", "memory:write", "a2a:send", "a2a:receive"],
    "auditor": ["admin", "tasks:read", "a2a:receive"],
    "reporter": ["admin", "tasks:read", "a2a:receive"],
    "chaos-monkey": ["admin", "tasks:read", "tasks:write", "browser:control", "a2a:send", "a2a:receive"],
}

FAILURE_BUCKETS = [
    "browser issue",
    "scheduler issue",
    "policy denial",
    "challenge/captcha",
    "auth/session issue",
    "plugin issue",
    "other",
]

METRIC_KEYS = [
    "runs_started",
    "runs_completed",
    "runs_failed",
    "intervention_count",
    "browser_crash_count",
    "captcha_challenge_count",
    "session_restore_failures",
    "duplicate_result_recoveries",
    "stale_ownership_incidents",
    "average_run_latency_seconds",
    "per_project_failure_rate",
]

SCHEDULE_WINDOWS = {
    "quarter_hourly": 15 * 60,
    "hourly": 60 * 60,
    "daily": 24 * 60 * 60,
}

ROLE_ENV_PREFIXES = {
    "director": "DIRECTOR",
    "browser-runner-1": "BROWSERRUNNER1",
    "browser-runner-2": "BROWSERRUNNER2",
    "auditor": "AUDITOR",
    "reporter": "REPORTER",
    "chaos-monkey": "CHAOSMONKEY",
}


@dataclass(frozen=True)
class ProjectCredentials:
    alias: str
    project_id: str
    api_key: str


@dataclass(frozen=True)
class RoleCredentials:
    role: str
    project_alias: str
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


def env_int(name: str, default: int) -> int:
    raw = optional_env(name)
    if raw is None:
        return default
    return int(raw)


def env_float(name: str, default: float) -> float:
    raw = optional_env(name)
    if raw is None:
        return default
    return float(raw)


def csv_env(name: str, default_items: list[str]) -> list[str]:
    raw = optional_env(name)
    if raw is None:
        return list(default_items)
    return [item.strip() for item in raw.split(",") if item.strip()]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp_slug() -> str:
    return utc_now().strftime("%Y%m%dT%H%M%SZ")


def logs_dir() -> Path:
    path = Path.home() / "synapse-logs" / "synthetic_alpha_swarm"
    path.mkdir(parents=True, exist_ok=True)
    return path


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


def role_env_prefix(role_name: str) -> str:
    try:
        return ROLE_ENV_PREFIXES[role_name]
    except KeyError as exc:
        raise RuntimeError(f"Unknown synthetic-alpha role: {role_name}") from exc


def build_role_credentials(role_name: str) -> RoleCredentials:
    project_alias = role_project_alias(role_name)
    project = build_project_credentials(project_alias)
    prefix = role_env_prefix(role_name)
    return RoleCredentials(
        role=role_name,
        project_alias=project_alias,
        project_id=project.project_id,
        api_key=optional_env(f"{prefix}_API_KEY", project.api_key) or project.api_key,
    )


def build_project_client(alias: str, agent_id: str | None = None) -> SynapseClient:
    creds = build_project_credentials(alias)
    return SynapseClient(
        base_url=env("SYNTHETIC_ALPHA_SWARM_BASE_URL", "http://127.0.0.1:8000"),
        api_key=creds.api_key,
        project_id=creds.project_id,
        agent_id=agent_id,
    )


def build_role_client(role_name: str, agent_id: str | None = None) -> SynapseClient:
    creds = build_role_credentials(role_name)
    return SynapseClient(
        base_url=env("SYNTHETIC_ALPHA_SWARM_BASE_URL", "http://127.0.0.1:8000"),
        api_key=creds.api_key,
        project_id=creds.project_id,
        agent_id=agent_id,
    )


def role_signing_key(role_name: str, agent_id: str) -> str:
    prefix = role_env_prefix(role_name)
    return optional_env(f"{prefix}_A2A_SIGNING_KEY", f"{agent_id}-verification-key") or f"{agent_id}-verification-key"


def send_signed_role_message(
    *,
    role_name: str,
    sender_agent_id: str,
    recipient_agent_id: str,
    payload: dict[str, object],
    message_type: A2AMessageType | str = A2AMessageType.SEND_MESSAGE,
    nonce: str | None = None,
) -> AgentWireMessage:
    creds = build_role_credentials(role_name)
    with build_role_client(role_name, agent_id=sender_agent_id) as client:
        message = client.sign_a2a_message(
            agent_id=sender_agent_id,
            target_agent=recipient_agent_id,
            message_type=message_type,
            payload=payload,
            signing_key=role_signing_key(role_name, sender_agent_id),
            organization_id=env("SYNTHETIC_ALPHA_SWARM_ORGANIZATION_ID"),
            project_id=creds.project_id,
            nonce=nonce,
        )
        return client.send_signed_a2a_message(message)


class RoleA2AListener:
    def __init__(
        self,
        *,
        role_name: str,
        agent_id: str,
        message_handler: Callable[[AgentWireMessage], None] | None = None,
    ) -> None:
        self.role_name = role_name
        self.agent_id = agent_id
        self.message_handler = message_handler
        self._thread: threading.Thread | None = None

    def start(self) -> "RoleA2AListener":
        if self._thread is not None and self._thread.is_alive():
            return self
        self._thread = threading.Thread(target=self._run, name=f"{self.agent_id}-a2a-listener", daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        from websockets.sync.client import connect

        backoff = 1.0
        while True:
            try:
                with build_role_client(self.role_name, agent_id=self.agent_id) as client:
                    url = client.build_websocket_url(path=f"/api/a2a/ws/{self.agent_id}")
                    with connect(url, open_timeout=15, close_timeout=5) as websocket:
                        backoff = 1.0
                        while True:
                            payload = websocket.recv()
                            if not isinstance(payload, str):
                                continue
                            message = AgentWireMessage.model_validate(json.loads(payload))
                            self._record(message)
                            if self.message_handler is not None:
                                self.message_handler(message)
            except Exception as exc:  # pragma: no cover - long-running runtime path
                print({
                    "role": self.role_name,
                    "agent_id": self.agent_id,
                    "a2a_listener_error": str(exc),
                    "retry_in_seconds": round(backoff, 2),
                })
                sleep_with_jitter(backoff, jitter_seconds=min(5.0, backoff * 0.25))
                backoff = min(backoff * 2, 30.0)

    def _record(self, message: AgentWireMessage) -> None:
        write_json_artifact(
            f"a2a_{self.agent_id}_{timestamp_slug()}.json",
            {
                "role": self.role_name,
                "agent_id": self.agent_id,
                "received_at": utc_now().isoformat(),
                "message": message.model_dump(mode="json"),
            },
        )


def start_role_a2a_listener(
    role_name: str,
    agent_id: str,
    *,
    message_handler: Callable[[AgentWireMessage], None] | None = None,
) -> RoleA2AListener:
    return RoleA2AListener(role_name=role_name, agent_id=agent_id, message_handler=message_handler).start()


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


def build_role_api(role_name: str) -> PlatformAPI:
    creds = build_role_credentials(role_name)
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


def continuous_role_limits() -> AgentExecutionLimits:
    return AgentExecutionLimits(
        max_steps=env_int("SYNTHETIC_ALPHA_SWARM_ROLE_MAX_STEPS", 20000),
        max_pages=env_int("SYNTHETIC_ALPHA_SWARM_ROLE_MAX_PAGES", 20000),
        max_tool_calls=env_int("SYNTHETIC_ALPHA_SWARM_ROLE_MAX_TOOL_CALLS", 5000),
        max_runtime_seconds=env_int("SYNTHETIC_ALPHA_SWARM_ROLE_MAX_RUNTIME_SECONDS", 2_592_000),
        max_tokens=env_int("SYNTHETIC_ALPHA_SWARM_ROLE_MAX_TOKENS", 2_000_000),
        max_memory_writes=env_int("SYNTHETIC_ALPHA_SWARM_ROLE_MAX_MEMORY_WRITES", 5000),
    )


def continuous_role_rate_limits() -> AgentRateLimits:
    return AgentRateLimits(
        browser_actions_per_minute=env_int("SYNTHETIC_ALPHA_SWARM_ROLE_BROWSER_ACTIONS_PER_MINUTE", 30),
        tool_calls_per_minute=env_int("SYNTHETIC_ALPHA_SWARM_ROLE_TOOL_CALLS_PER_MINUTE", 15),
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
            rate_limits=continuous_role_rate_limits(),
        ),
        limits=continuous_role_limits(),
        metadata={
            "role": role,
            "synthetic_alpha": "true",
            "safe_domains": ",".join(default_safe_domains()),
        },
    )


def register_role_agent(role_name: str, definition: AgentDefinition) -> AgentDefinition:
    with build_role_client(role_name, agent_id=definition.agent_id) as client:
        return client.register_agent(definition)


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
        "latency_seconds": run_latency_seconds(run),
    }


def run_latency_seconds(run: RunState) -> float:
    completed_at = run.completed_at or run.updated_at
    return max(0.0, (completed_at - run.started_at).total_seconds())


def lower_blob(payload: Any) -> str:
    return json.dumps(payload, default=str, sort_keys=True).lower()


def classify_failure(run: RunState, interventions: list[OperatorInterventionRecord] | None = None) -> str:
    blob = " ".join(
        [
            lower_blob(run.metadata),
            str(run.current_phase or "").lower(),
            *(lower_blob(item.payload) + " " + item.reason.lower() for item in (interventions or []) if item.run_id == run.run_id),
        ]
    )
    if any(token in blob for token in ["captcha", "challenge", "turnstile"]):
        return "challenge/captcha"
    if any(token in blob for token in ["session restore", "session_restore", "auth", "cookie", "login"]):
        return "auth/session issue"
    if any(token in blob for token in ["plugin", "tool error", "github.search", "web.search"]):
        return "plugin issue"
    if any(token in blob for token in ["policy", "domain", "approval", "forbidden", "blocked"]):
        return "policy denial"
    if any(token in blob for token in ["scheduler", "lease", "ownership", "stale", "queue"]):
        return "scheduler issue"
    if any(token in blob for token in ["browser", "playwright", "page crashed", "crash", "selector"]):
        return "browser issue"
    return "other"


def compute_project_metrics(
    project_alias: str,
    runs: list[RunState],
    interventions: list[OperatorInterventionRecord],
    audit_logs: list[dict[str, Any]],
) -> dict[str, Any]:
    started = len(runs)
    completed = sum(1 for run in runs if run.status.value == "completed")
    failed = sum(1 for run in runs if run.status.value == "failed")
    latencies = [run_latency_seconds(run) for run in runs if run.status.value in {"completed", "failed", "cancelled"}]
    run_blobs = [lower_blob(run.metadata) + " " + str(run.current_phase or "").lower() for run in runs]
    intervention_blobs = [item.reason.lower() + " " + lower_blob(item.payload) for item in interventions]
    audit_blobs = [lower_blob(item) for item in audit_logs]

    browser_crash_count = sum("crash" in blob or "playwright" in blob for blob in run_blobs + audit_blobs)
    captcha_challenge_count = sum(any(token in blob for token in ["captcha", "challenge", "turnstile"]) for blob in run_blobs + intervention_blobs)
    session_restore_failures = sum(any(token in blob for token in ["session restore", "session_restore", "restore failed"]) for blob in run_blobs + audit_blobs)
    duplicate_result_recoveries = sum(any(token in blob for token in ["duplicate", "idempotent", "recovered duplicate"]) for blob in run_blobs + audit_blobs)
    stale_ownership_incidents = sum(any(token in blob for token in ["stale ownership", "stale", "lease expired", "ownership"]) for blob in run_blobs + audit_blobs)

    failure_rate = (failed / started) if started else 0.0
    classifications = {bucket: 0 for bucket in FAILURE_BUCKETS}
    for run in runs:
        if run.status.value == "failed":
            classifications[classify_failure(run, interventions)] += 1

    return {
        "project_alias": project_alias,
        "runs_started": started,
        "runs_completed": completed,
        "runs_failed": failed,
        "intervention_count": len(interventions),
        "browser_crash_count": browser_crash_count,
        "captcha_challenge_count": captcha_challenge_count,
        "session_restore_failures": session_restore_failures,
        "duplicate_result_recoveries": duplicate_result_recoveries,
        "stale_ownership_incidents": stale_ownership_incidents,
        "average_run_latency_seconds": round(sum(latencies) / len(latencies), 2) if latencies else 0.0,
        "per_project_failure_rate": round(failure_rate, 4),
        "failure_classification": classifications,
    }


def overall_metrics(project_snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {key: 0 for key in METRIC_KEYS if key not in {"average_run_latency_seconds", "per_project_failure_rate"}}
    latencies: list[float] = []
    failure_rates: dict[str, float] = {}
    classifications = {bucket: 0 for bucket in FAILURE_BUCKETS}
    for snapshot in project_snapshots:
        for key in totals:
            totals[key] += int(snapshot.get(key, 0))
        latencies.append(float(snapshot.get("average_run_latency_seconds", 0.0)))
        failure_rates[str(snapshot.get("project_alias"))] = float(snapshot.get("per_project_failure_rate", 0.0))
        for bucket, count in snapshot.get("failure_classification", {}).items():
            classifications[bucket] = classifications.get(bucket, 0) + int(count)
    totals["average_run_latency_seconds"] = round(sum(latencies) / len(latencies), 2) if latencies else 0.0
    totals["per_project_failure_rate"] = failure_rates
    totals["failure_classification"] = classifications
    return totals


def window_start_for(label: str, now: datetime | None = None) -> datetime:
    current = now or utc_now()
    if label == "daily":
        return current - timedelta(days=1)
    if label == "weekly":
        return current - timedelta(days=7)
    return current - timedelta(hours=24)


def filter_runs_since(runs: list[RunState], since: datetime) -> list[RunState]:
    return [run for run in runs if run.started_at >= since or run.updated_at >= since]


def filter_interventions_since(interventions: list[OperatorInterventionRecord], since: datetime) -> list[OperatorInterventionRecord]:
    return [item for item in interventions if item.created_at >= since]


def filter_audit_logs_since(audit_logs: list[dict[str, Any]], since: datetime) -> list[dict[str, Any]]:
    threshold = since.isoformat()
    filtered: list[dict[str, Any]] = []
    for item in audit_logs:
        timestamp = str(item.get("timestamp", ""))
        if timestamp and timestamp >= threshold:
            filtered.append(item)
    return filtered


def write_json_artifact(name: str, payload: Any) -> dict[str, str]:
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    repo_target = runtime_dir() / name
    log_target = logs_dir() / name
    repo_target.write_text(serialized)
    log_target.write_text(serialized)
    return {"repo": str(repo_target), "log": str(log_target)}


def write_text_artifact(name: str, payload: str) -> dict[str, str]:
    repo_target = runtime_dir() / name
    log_target = logs_dir() / name
    repo_target.write_text(payload)
    log_target.write_text(payload)
    return {"repo": str(repo_target), "log": str(log_target)}


def load_json_state(name: str, default: Any) -> Any:
    target = runtime_dir() / name
    if not target.exists():
        return default
    return json.loads(target.read_text())


def save_json_state(name: str, payload: Any) -> Path:
    target = runtime_dir() / name
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return target


def parse_loop_args(description: str, *, default_interval: float = 300.0) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--once", action="store_true", help="Run a single iteration and exit.")
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=float(optional_env("SYNTHETIC_ALPHA_SWARM_INTERVAL_SECONDS", str(default_interval)) or str(default_interval)),
        help="Loop interval for continuous execution.",
    )
    return parser.parse_args()


def sleep_with_jitter(base_seconds: float, jitter_seconds: float | None = None) -> None:
    if base_seconds <= 0:
        return
    jitter = jitter_seconds if jitter_seconds is not None else min(1.0, max(0.25, base_seconds * 0.15))
    delay = max(0.0, base_seconds + random.uniform(0.0, jitter))
    time.sleep(delay)


def is_retryable_http_error(exc: Exception) -> bool:
    if not isinstance(exc, httpx.HTTPStatusError):
        return False
    return exc.response.status_code in {429, 502, 503, 504}


def retry_with_backoff(
    action: Callable[[], Any],
    *,
    label: str,
    attempts: int = 4,
    base_delay_seconds: float = 5.0,
    max_delay_seconds: float = 60.0,
) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return action()
        except Exception as exc:
            last_error = exc
            if attempt >= attempts or not is_retryable_http_error(exc):
                raise
            delay = min(max_delay_seconds, base_delay_seconds * (2 ** (attempt - 1)))
            print({"label": label, "attempt": attempt, "retry_in_seconds": delay, "error": str(exc)})
            sleep_with_jitter(delay, jitter_seconds=min(5.0, delay * 0.2))
    raise RuntimeError(f"{label} failed without raising an exception") from last_error


def run_forever(step: Callable[[], None], *, once: bool, interval_seconds: float) -> None:
    consecutive_failures = 0
    while True:
        started_at = time.monotonic()
        try:
            step()
            consecutive_failures = 0
            if once:
                return
            elapsed = time.monotonic() - started_at
            sleep_with_jitter(max(0.0, interval_seconds - elapsed), jitter_seconds=min(30.0, interval_seconds * 0.1))
        except Exception as exc:
            consecutive_failures += 1
            if once:
                raise
            delay = min(max(5.0, interval_seconds / 3), 300.0, 5.0 * (2 ** (consecutive_failures - 1)))
            print({"loop_error": str(exc), "consecutive_failures": consecutive_failures, "retry_in_seconds": delay})
            sleep_with_jitter(delay, jitter_seconds=min(10.0, delay * 0.25))


def role_project_alias(role_name: str) -> str:
    if role_name in {"browser-runner-2", "chaos-monkey"}:
        return "chaos"
    return "steady"
