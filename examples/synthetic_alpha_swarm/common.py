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
    "a2a_messages_sent",
    "a2a_messages_succeeded",
    "a2a_messages_failed",
    "scheduler_recovery_events",
    "plugin_denials",
    "average_run_latency_seconds",
    "per_project_failure_rate",
]

ALPHA_GATE_THRESHOLDS = {
    "hold_failure_rate": 0.15,
    "hold_unresolved_requests": 1,
    "hold_stale_ownership_incidents": 5,
    "hold_browser_crash_count": 3,
    "hold_plugin_denials": 1,
    "continue_failure_rate": 0.05,
    "continue_average_latency_seconds": 120.0,
    "continue_scheduler_recoveries": 5,
    "expand_average_latency_seconds": 75.0,
    "expand_stale_ownership_incidents": 1,
}

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

RUNTIME_EVENT_TELEMETRY_MAP: dict[str, str] = {
    "worker.request.running": "browser.request_running",
    "worker.request.slow": "browser.request_slow",
    "worker.request.stuck": "browser.request_stuck",
    "worker.request.recovered": "scheduler.request_recovered",
    "worker.result.replayed": "scheduler.result_replayed",
    "worker.ownership.stale": "scheduler.stale_ownership",
    "run.dispatch.reconciled": "scheduler.recovered",
}

DEFAULT_SYNTHETIC_ALPHA_CLIENT_TIMEOUT_SECONDS = 180.0
STALE_OWNERSHIP_TOKENS = (
    "worker.ownership.stale",
    "stale ownership",
    "ownership stale",
    "stale session ownership",
    "no browser worker assigned to session",
)

_RUNTIME_EVENT_LISTENERS: dict[str, "ProjectRuntimeEventListener"] = {}


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


def env_bool(name: str, default: bool) -> bool:
    raw = optional_env(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


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


def reports_dir() -> Path:
    raw = optional_env("SYNTHETIC_ALPHA_SWARM_REPORTS_DIR", str(Path.home() / "synapse-logs" / "reports" / "synthetic_alpha"))
    path = Path(raw or str(Path.home() / "synapse-logs" / "reports" / "synthetic_alpha"))
    if not path.is_absolute():
        path = REPO_ROOT / path
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


def runtime_reports_dir() -> Path:
    path = runtime_dir() / "reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def telemetry_dir() -> Path:
    default_path = Path.home() / "synapse-logs" / "synthetic_alpha_swarm" / "telemetry"
    raw = optional_env("SYNTHETIC_ALPHA_SWARM_TELEMETRY_DIR", str(default_path))
    path = Path(raw or str(default_path))
    if not path.is_absolute():
        path = REPO_ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def runtime_telemetry_dir() -> Path:
    path = runtime_dir() / "telemetry"
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
        timeout=swarm_client_timeout_seconds(),
        api_key=creds.api_key,
        project_id=creds.project_id,
        agent_id=agent_id,
    )


def build_role_client(role_name: str, agent_id: str | None = None) -> SynapseClient:
    creds = build_role_credentials(role_name)
    return SynapseClient(
        base_url=env("SYNTHETIC_ALPHA_SWARM_BASE_URL", "http://127.0.0.1:8000"),
        timeout=swarm_client_timeout_seconds(),
        api_key=creds.api_key,
        project_id=creds.project_id,
        agent_id=agent_id,
    )


def swarm_client_timeout_seconds() -> float:
    return env_float(
        "SYNTHETIC_ALPHA_SWARM_CLIENT_TIMEOUT_SECONDS",
        DEFAULT_SYNTHETIC_ALPHA_CLIENT_TIMEOUT_SECONDS,
    )


def role_signing_key(role_name: str, agent_id: str) -> str:
    prefix = role_env_prefix(role_name)
    return optional_env(f"{prefix}_A2A_SIGNING_KEY", f"{agent_id}-verification-key") or f"{agent_id}-verification-key"


def project_credentials_for_alias(alias: str | None) -> ProjectCredentials | None:
    if not alias:
        return None
    return build_project_credentials(alias)


def telemetry_event_file_name(timestamp: datetime | None = None) -> str:
    current = timestamp or utc_now()
    return f"telemetry_{current.strftime('%Y%m%d')}.jsonl"


def append_text_line(target: Path, line: str) -> None:
    with target.open("a", encoding="utf-8") as handle:
        handle.write(line)


def record_telemetry_event(
    event_type: str,
    *,
    project_alias: str | None = None,
    project_id: str | None = None,
    role: str | None = None,
    agent_id: str | None = None,
    run_id: str | None = None,
    status: str | None = None,
    details: dict[str, Any] | None = None,
    timestamp: datetime | None = None,
) -> dict[str, str]:
    current = timestamp or utc_now()
    payload = {
        "event_type": event_type,
        "timestamp": current.isoformat(),
        "project_alias": project_alias,
        "project_id": project_id,
        "role": role,
        "agent_id": agent_id,
        "run_id": run_id,
        "status": status,
        "details": details or {},
    }
    serialized = json.dumps(payload, sort_keys=True) + "\n"
    repo_target = runtime_telemetry_dir() / telemetry_event_file_name(current)
    log_target = telemetry_dir() / telemetry_event_file_name(current)
    append_text_line(repo_target, serialized)
    append_text_line(log_target, serialized)
    return {"repo": str(repo_target), "log": str(log_target)}


def telemetry_dedupe_key(event_type: str, details: dict[str, Any] | None = None) -> str | None:
    details = details if isinstance(details, dict) else {}
    for key in ("event_id", "dedupe_key"):
        value = details.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def normalize_runtime_event_to_telemetry(
    event: dict[str, Any],
    *,
    project_alias: str,
) -> dict[str, Any] | None:
    source_type = str(event.get("event_type") or "")
    mapped_type = RUNTIME_EVENT_TELEMETRY_MAP.get(source_type)
    if mapped_type is None:
        return None
    payload = event.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    details: dict[str, Any] = {"source_event_type": source_type}
    event_id = event.get("event_id")
    if isinstance(event_id, str) and event_id:
        details["event_id"] = event_id
    for key in (
        "worker_id",
        "request_id",
        "action_id",
        "action",
        "queue_name",
        "fencing_token",
        "status",
        "age_seconds",
        "controller_id",
        "recovered",
    ):
        value = payload.get(key)
        if value is not None:
            details[key] = value
    request_id = payload.get("request_id")
    action_id = payload.get("action_id")
    run_id = event.get("run_id")
    if source_type == "worker.request.slow":
        details["request_signal_key"] = request_health_dedupe_key(
            str(run_id) if run_id is not None else None,
            str(request_id) if request_id is not None else None,
            str(action_id) if action_id is not None else None,
            "slow",
            str(payload.get("status") or payload.get("age_seconds") or payload.get("updated_at") or ""),
        )
    elif source_type == "worker.request.stuck":
        details["request_signal_key"] = request_health_dedupe_key(
            str(run_id) if run_id is not None else None,
            str(request_id) if request_id is not None else None,
            str(action_id) if action_id is not None else None,
            "stuck",
            str(payload.get("status") or payload.get("age_seconds") or payload.get("updated_at") or ""),
        )
    elif source_type == "worker.request.recovered":
        details["request_signal_key"] = request_health_dedupe_key(
            str(run_id) if run_id is not None else None,
            str(request_id) if request_id is not None else None,
            str(action_id) if action_id is not None else None,
            "recovered",
            str(payload.get("status") or payload.get("age_seconds") or payload.get("updated_at") or ""),
        )
    severity = event.get("severity")
    status = str(severity).lower() if isinstance(severity, str) else None
    timestamp = event.get("timestamp")
    parsed_timestamp = None
    if isinstance(timestamp, str):
        try:
            parsed_timestamp = datetime.fromisoformat(timestamp)
        except ValueError:
            parsed_timestamp = None
    return {
        "event_type": mapped_type,
        "project_alias": project_alias,
        "project_id": str(event.get("project_id")) if event.get("project_id") is not None else None,
        "run_id": str(event.get("run_id")) if event.get("run_id") is not None else None,
        "status": status,
        "details": details,
        "timestamp": parsed_timestamp,
    }


def request_health_delay_reason(reason: str | None) -> bool:
    lowered = (reason or "").strip().lower()
    return any(token in lowered for token in ("slow", "delay", "stuck", "timeout", "retry", "backoff"))


def request_health_dedupe_key(
    run_id: str | None,
    request_id: str | None,
    action_id: str | None,
    health_state: str,
    marker: str | None,
    *,
    suffix: str | None = None,
) -> str:
    stable_id = action_id or request_id or "unknown-request"
    stable_marker = marker or "unknown-marker"
    base = f"{run_id or 'unknown-run'}:{stable_id}:{health_state}:{stable_marker}"
    return f"{base}:{suffix}" if suffix else base


def normalize_worker_request_health_to_telemetry(
    health_view: dict[str, Any],
    *,
    project_alias: str,
    project_id: str | None,
    run_id: str,
) -> list[dict[str, Any]]:
    request = health_view.get("request")
    request = request if isinstance(request, dict) else {}
    result = health_view.get("result")
    result = result if isinstance(result, dict) else {}
    health_state = str(health_view.get("health_state") or "").strip().lower()
    if not health_state:
        return []

    status_reason = request.get("status_reason")
    request_id = str(request.get("request_id") or "") or None
    action_id = str(request.get("action_id") or "") or None
    worker_id = str(request.get("worker_id") or result.get("worker_id") or "") or None
    action = str(request.get("action") or result.get("action") or "") or None
    session_id = str(request.get("session_id") or result.get("session_id") or "") or None
    marker = str(
        request.get("updated_at")
        or request.get("completed_at")
        or result.get("completed_at")
        or request.get("started_at")
        or request.get("created_at")
        or ""
    ) or None
    status_value = str(request.get("status") or result.get("status") or health_state or "observed")
    timestamp_raw = request.get("updated_at") or request.get("completed_at") or result.get("completed_at")
    parsed_timestamp = None
    if isinstance(timestamp_raw, str):
        try:
            parsed_timestamp = datetime.fromisoformat(timestamp_raw)
        except ValueError:
            parsed_timestamp = None

    details = {
        "request_id": request_id,
        "action_id": action_id,
        "action": action,
        "worker_id": worker_id,
        "session_id": session_id,
        "health_state": health_state,
        "has_result": bool(health_view.get("has_result")),
        "is_active": bool(health_view.get("is_active")),
        "total_age_seconds": health_view.get("total_age_seconds"),
        "execution_age_seconds": health_view.get("execution_age_seconds"),
        "progress_age_seconds": health_view.get("progress_age_seconds"),
        "status_reason": status_reason,
        "updated_at": request.get("updated_at"),
        "completed_at": request.get("completed_at") or result.get("completed_at"),
    }

    events: list[dict[str, Any]] = []
    mapped_type = {
        "slow": "browser.request_health.slow",
        "stuck": "browser.request_health.stuck",
        "recovered": "browser.request_health.recovered",
    }.get(health_state)
    if mapped_type is not None:
        event_details = dict(details)
        event_details["dedupe_key"] = request_health_dedupe_key(run_id, request_id, action_id, health_state, marker)
        events.append(
            {
                "event_type": mapped_type,
                "project_alias": project_alias,
                "project_id": project_id,
                "run_id": run_id,
                "status": status_value,
                "details": event_details,
                "timestamp": parsed_timestamp,
            }
        )

    completed_after_slow = bool(health_view.get("has_result")) and (
        health_state in {"slow", "recovered"}
        or request_health_delay_reason(status_reason)
        or (isinstance(details["total_age_seconds"], (int, float)) and float(details["total_age_seconds"]) >= 30.0)
    )
    if completed_after_slow:
        event_details = dict(details)
        event_details["dedupe_key"] = request_health_dedupe_key(
            run_id,
            request_id,
            action_id,
            "completed_after_slow",
            marker,
            suffix="completed-after-slow",
        )
        events.append(
            {
                "event_type": "browser.request_health.completed_after_slow",
                "project_alias": project_alias,
                "project_id": project_id,
                "run_id": run_id,
                "status": status_value,
                "details": event_details,
                "timestamp": parsed_timestamp,
            }
        )
    return events


def load_telemetry_events_since(since: datetime) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    threshold = since.isoformat()
    for candidate in sorted(runtime_telemetry_dir().glob("telemetry_*.jsonl")):
        for line in candidate.read_text().splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if str(item.get("timestamp", "")) >= threshold:
                events.append(item)
    return events


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
        record_telemetry_event(
            "a2a.sent",
            project_alias=creds.project_alias,
            project_id=creds.project_id,
            role=role_name,
            agent_id=sender_agent_id,
            status="pending",
            details={
                "recipient_agent_id": recipient_agent_id,
                "message_type": getattr(message_type, "value", str(message_type)),
                "nonce": message.nonce,
            },
        )
        try:
            response = client.send_signed_a2a_message(message)
        except Exception as exc:
            record_telemetry_event(
                "a2a.failed",
                project_alias=creds.project_alias,
                project_id=creds.project_id,
                role=role_name,
                agent_id=sender_agent_id,
                status="failed",
                details={
                    "recipient_agent_id": recipient_agent_id,
                    "message_type": getattr(message_type, "value", str(message_type)),
                    "nonce": message.nonce,
                    "error": str(exc),
                },
            )
            raise
        record_telemetry_event(
            "a2a.succeeded",
            project_alias=creds.project_alias,
            project_id=creds.project_id,
            role=role_name,
            agent_id=sender_agent_id,
            status="ok",
            details={
                "recipient_agent_id": recipient_agent_id,
                "message_id": response.message_id,
                "message_type": getattr(message_type, "value", str(message_type)),
                "nonce": response.nonce,
            },
        )
        return response


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
                    with connect(url, open_timeout=15, close_timeout=5, ping_interval=None) as websocket:
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
        creds = build_role_credentials(self.role_name)
        record_telemetry_event(
            "a2a.received",
            project_alias=creds.project_alias,
            project_id=creds.project_id,
            role=self.role_name,
            agent_id=self.agent_id,
            status="received",
            details={
                "sender_agent_id": message.sender_agent_id,
                "message_id": message.message_id,
                "message_type": getattr(message.type, "value", str(message.type)),
            },
        )
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


class ProjectRuntimeEventListener:
    def __init__(self, *, project_alias: str) -> None:
        self.project_alias = project_alias
        self._thread: threading.Thread | None = None

    def start(self) -> "ProjectRuntimeEventListener":
        if self._thread is not None and self._thread.is_alive():
            return self
        self._thread = threading.Thread(
            target=self._run,
            name=f"{self.project_alias}-runtime-event-listener",
            daemon=True,
        )
        self._thread.start()
        return self

    def _run(self) -> None:
        from websockets.sync.client import connect

        backoff = 1.0
        while True:
            creds = build_project_credentials(self.project_alias)
            try:
                with build_project_client(self.project_alias) as client:
                    url = client.build_websocket_url("/api/ws")
                    record_telemetry_event(
                        "runtime_feed.connected",
                        project_alias=self.project_alias,
                        project_id=creds.project_id,
                        status="connected",
                        details={"channel": "/api/ws"},
                    )
                    with connect(url, open_timeout=15, close_timeout=5, ping_interval=None) as websocket:
                        backoff = 1.0
                        while True:
                            payload = websocket.recv()
                            if not isinstance(payload, str):
                                continue
                            event = json.loads(payload)
                            telemetry = normalize_runtime_event_to_telemetry(event, project_alias=self.project_alias)
                            if telemetry is None:
                                continue
                            record_telemetry_event(**telemetry)
            except Exception as exc:  # pragma: no cover - long-running runtime path
                record_telemetry_event(
                    "runtime_feed.error",
                    project_alias=self.project_alias,
                    project_id=creds.project_id,
                    status="degraded",
                    details={"error": str(exc)},
                )
                sleep_with_jitter(backoff, jitter_seconds=min(5.0, backoff * 0.25))
                backoff = min(backoff * 2, 30.0)


def ensure_project_runtime_listener(project_alias: str) -> None:
    listener = _RUNTIME_EVENT_LISTENERS.get(project_alias)
    if listener is None:
        listener = ProjectRuntimeEventListener(project_alias=project_alias).start()
        _RUNTIME_EVENT_LISTENERS[project_alias] = listener


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

    def get_run_events(self, run_id: str) -> list[dict[str, Any]]:
        response = self._request("GET", f"/api/runs/{run_id}/events")
        return list(response.json())

    def get_run_worker_requests(
        self,
        run_id: str,
        *,
        session_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, str] = {}
        if session_id:
            params["session_id"] = session_id
        if status:
            params["status"] = status
        response = self._request("GET", f"/api/runs/{run_id}/worker-requests", params=params or None)
        return list(response.json())

    def get_agent_status(self, agent_id: str) -> dict[str, object]:
        response = self._request("GET", f"/api/agents/{agent_id}/status")
        return dict(response.json())

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
        timeout=swarm_client_timeout_seconds(),
    )


def build_project_admin_api(alias: str) -> PlatformAPI:
    creds = build_project_credentials(alias)
    prefix = f"SYNTHETIC_ALPHA_SWARM_{alias.upper()}_ADMIN"
    alias_api_key = optional_env(f"{prefix}_API_KEY")
    alias_bearer_token = optional_env(f"{prefix}_BEARER_TOKEN")
    global_api_key = optional_env("SYNTHETIC_ALPHA_SWARM_ADMIN_API_KEY")
    global_bearer_token = optional_env("SYNTHETIC_ALPHA_SWARM_ADMIN_BEARER_TOKEN")
    api_key = alias_api_key or global_api_key or creds.api_key
    bearer_token = alias_bearer_token or (None if alias_api_key else global_bearer_token)
    return PlatformAPI(
        base_url=env("SYNTHETIC_ALPHA_SWARM_BASE_URL", "http://127.0.0.1:8000"),
        api_key=api_key,
        bearer_token=bearer_token,
        project_id=creds.project_id,
        timeout=swarm_client_timeout_seconds(),
    )


def safe_list_audit_logs(api: PlatformAPI, project_id: str) -> list[dict[str, Any]]:
    try:
        return api.list_audit_logs(project_id)
    except (httpx.HTTPStatusError, PermissionError) as exc:
        write_json_artifact(
            f"audit_log_access_{project_id}_{timestamp_slug()}.json",
            {
                "project_id": project_id,
                "error": str(exc),
                "recorded_at": utc_now().isoformat(),
            },
        )
        record_telemetry_event(
            "audit_logs.unavailable",
            project_id=project_id,
            status="degraded",
            details={"error": str(exc)},
        )
        return []


def build_role_api(role_name: str) -> PlatformAPI:
    creds = build_role_credentials(role_name)
    return PlatformAPI(
        base_url=env("SYNTHETIC_ALPHA_SWARM_BASE_URL", "http://127.0.0.1:8000"),
        api_key=creds.api_key,
        project_id=creds.project_id,
        timeout=swarm_client_timeout_seconds(),
    )


def build_admin_api() -> PlatformAPI:
    return PlatformAPI(
        base_url=env("SYNTHETIC_ALPHA_SWARM_BASE_URL", "http://127.0.0.1:8000"),
        api_key=optional_env("SYNTHETIC_ALPHA_SWARM_ADMIN_API_KEY"),
        bearer_token=optional_env("SYNTHETIC_ALPHA_SWARM_ADMIN_BEARER_TOKEN"),
        project_id=optional_env("SYNTHETIC_ALPHA_SWARM_ADMIN_PROJECT_ID"),
        timeout=swarm_client_timeout_seconds(),
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
    try:
        with build_role_client(role_name, agent_id=definition.agent_id) as client:
            return client.register_agent(definition)
    except PermissionError as exc:
        if "Missing required scopes: admin" not in str(exc):
            raise
    with build_admin_api() as admin_api:
        return admin_api.register_agent(definition)


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


def is_stale_ownership_signal(blob: str) -> bool:
    return any(token in blob for token in STALE_OWNERSHIP_TOKENS)


def classify_blob(blob: str) -> str:
    if any(token in blob for token in ["captcha", "challenge", "turnstile"]):
        return "challenge/captcha"
    if any(token in blob for token in ["session restore", "session_restore", "auth", "cookie", "login"]):
        return "auth/session issue"
    if any(token in blob for token in ["plugin", "tool error", "github.search", "web.search"]):
        return "plugin issue"
    if any(token in blob for token in ["policy", "domain", "approval", "forbidden", "blocked", "denied"]):
        return "policy denial"
    if any(token in blob for token in ["scheduler", "lease", "ownership", "stale", "queue", "requeue", "recovered"]):
        return "scheduler issue"
    if any(token in blob for token in ["browser", "playwright", "page crashed", "crash", "selector"]):
        return "browser issue"
    return "other"


def classify_failure(run: RunState, interventions: list[OperatorInterventionRecord] | None = None) -> str:
    blob = " ".join(
        [
            lower_blob(run.metadata),
            str(run.current_phase or "").lower(),
            *(lower_blob(item.payload) + " " + item.reason.lower() for item in (interventions or []) if item.run_id == run.run_id),
        ]
    )
    return classify_blob(blob)


def browser_error_category_from_payload(payload: Any) -> str:
    return classify_blob(lower_blob(payload))


def intervention_reason_bucket(reason: str) -> str:
    lowered = reason.strip().lower()
    if not lowered:
        return "unspecified"
    if any(token in lowered for token in ["captcha", "challenge", "turnstile"]):
        return "challenge/captcha"
    if any(token in lowered for token in ["auth", "login", "session", "cookie"]):
        return "auth/session issue"
    if any(token in lowered for token in ["policy", "blocked", "denied", "forbidden"]):
        return "policy denial"
    if any(token in lowered for token in ["browser", "selector", "playwright"]):
        return "browser issue"
    return lowered


def per_agent_outcomes(runs: list[RunState]) -> dict[str, dict[str, float | int]]:
    summary: dict[str, dict[str, float | int]] = {}
    for run in runs:
        agent_id = run.agent_id or "unknown"
        row = summary.setdefault(agent_id, {"runs_started": 0, "runs_completed": 0, "runs_failed": 0})
        row["runs_started"] = int(row["runs_started"]) + 1
        if run.status.value == "completed":
            row["runs_completed"] = int(row["runs_completed"]) + 1
        if run.status.value == "failed":
            row["runs_failed"] = int(row["runs_failed"]) + 1
    for row in summary.values():
        started = int(row["runs_started"])
        completed = int(row["runs_completed"])
        failed = int(row["runs_failed"])
        row["success_rate"] = round((completed / started), 4) if started else 0.0
        row["failure_rate"] = round((failed / started), 4) if started else 0.0
    return summary


def count_by_reason(interventions: list[OperatorInterventionRecord]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in interventions:
        bucket = intervention_reason_bucket(item.reason)
        counts[bucket] = counts.get(bucket, 0) + 1
    return counts


def agents_requiring_intervention(interventions: list[OperatorInterventionRecord]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in interventions:
        agent_id = item.agent_id or "unknown"
        counts[agent_id] = counts.get(agent_id, 0) + 1
    return counts


def browser_errors_by_category(
    runs: list[RunState],
    audit_logs: list[dict[str, Any]],
    telemetry_events: list[dict[str, Any]],
) -> dict[str, int]:
    counts = {bucket: 0 for bucket in FAILURE_BUCKETS}
    for run in runs:
        if run.status.value == "failed":
            bucket = classify_blob(lower_blob(run.metadata) + " " + str(run.current_phase or "").lower())
            counts[bucket] = counts.get(bucket, 0) + 1
    for item in audit_logs:
        blob = lower_blob(item)
        if "plugin.execution" in blob:
            continue
        if any(token in blob for token in ["browser", "captcha", "challenge", "selector", "playwright", "policy", "auth", "session"]):
            bucket = classify_blob(blob)
            counts[bucket] = counts.get(bucket, 0) + 1
    for event in telemetry_events:
        event_type = str(event.get("event_type"))
        if event_type in {"browser.error", "browser.retry"}:
            bucket = browser_error_category_from_payload(event.get("details", {}))
            counts[bucket] = counts.get(bucket, 0) + 1
    counts["browser issue"] = counts.get("browser issue", 0) + count_unique_request_signals(
        telemetry_events,
        {"browser.request_slow", "browser.request_health.slow", "browser.request_health.completed_after_slow"},
    )
    counts["scheduler issue"] = counts.get("scheduler issue", 0) + count_unique_request_signals(
        telemetry_events,
        {"browser.request_stuck", "browser.request_health.stuck", "browser.request_health.recovered"},
    )
    return counts


def count_matching_audit_logs(audit_logs: list[dict[str, Any]], *tokens: str) -> int:
    lowered = [token.lower() for token in tokens]
    return sum(any(token in lower_blob(item) for token in lowered) for item in audit_logs)


def count_unique_telemetry_events(telemetry_events: list[dict[str, Any]], *event_types: str) -> int:
    wanted = set(event_types)
    seen_ids: set[str] = set()
    count = 0
    for event in telemetry_events:
        if str(event.get("event_type")) not in wanted:
            continue
        details = event.get("details")
        details = details if isinstance(details, dict) else {}
        event_id = telemetry_dedupe_key(str(event.get("event_type")), details)
        if isinstance(event_id, str) and event_id:
            if event_id in seen_ids:
                continue
            seen_ids.add(event_id)
        count += 1
    return count


def unique_telemetry_events(telemetry_events: list[dict[str, Any]], *event_types: str) -> list[dict[str, Any]]:
    wanted = set(event_types)
    seen_ids: set[str] = set()
    unique: list[dict[str, Any]] = []
    for event in telemetry_events:
        if wanted and str(event.get("event_type")) not in wanted:
            continue
        details = event.get("details")
        details = details if isinstance(details, dict) else {}
        event_id = telemetry_dedupe_key(str(event.get("event_type")), details)
        if isinstance(event_id, str) and event_id:
            if event_id in seen_ids:
                continue
            seen_ids.add(event_id)
        unique.append(event)
    return unique


def count_stale_ownership_incidents(
    run_blobs: list[str],
    audit_blobs: list[str],
    telemetry_events: list[dict[str, Any]],
) -> int:
    explicit_tokens = [
        "stale ownership",
        "ownership stale",
        "worker.ownership.stale",
        "lease expired",
        "worker lease expired",
    ]
    total = sum(any(token in blob for token in explicit_tokens) for blob in run_blobs + audit_blobs)
    total += count_unique_telemetry_events(telemetry_events, "scheduler.stale_ownership")
    total += count_unique_telemetry_events(
        [
            event
            for event in telemetry_events
            if str(event.get("event_type")) == "scheduler.loop_error"
            and any(token in lower_blob(event.get("details", {})) for token in explicit_tokens)
        ],
        "scheduler.loop_error",
    )
    return total


def request_health_summary(telemetry_events: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "slow": 0,
        "stuck": 0,
        "recovered": 0,
        "completed_after_slow": 0,
        "unresolved": 0,
    }
    for event in unique_telemetry_events(
        telemetry_events,
        "browser.request_slow",
        "browser.request_stuck",
        "scheduler.request_recovered",
        "browser.request_health.slow",
        "browser.request_health.stuck",
        "browser.request_health.recovered",
        "browser.request_health.completed_after_slow",
    ):
        event_type = str(event.get("event_type"))
        details = event.get("details")
        details = details if isinstance(details, dict) else {}
        if event_type in {"browser.request_slow", "browser.request_health.slow"}:
            counts["slow"] += 1
        elif event_type in {"browser.request_stuck", "browser.request_health.stuck"}:
            counts["stuck"] += 1
            if event_type == "browser.request_stuck" or (details.get("is_active") and not details.get("has_result")):
                counts["unresolved"] += 1
        elif event_type in {"scheduler.request_recovered", "browser.request_health.recovered"}:
            counts["recovered"] += 1
        elif event_type == "browser.request_health.completed_after_slow":
            counts["completed_after_slow"] += 1
    return counts


def request_signal_identity(event: dict[str, Any]) -> str | None:
    event_type = str(event.get("event_type") or "")
    details = event.get("details")
    details = details if isinstance(details, dict) else {}
    signal_key = details.get("request_signal_key")
    if isinstance(signal_key, str) and signal_key:
        return signal_key
    dedupe_key = details.get("dedupe_key")
    if isinstance(dedupe_key, str) and dedupe_key:
        return dedupe_key
    run_id = event.get("run_id")
    action_id = details.get("action_id")
    request_id = details.get("request_id")
    marker = details.get("updated_at") or details.get("completed_at") or details.get("status_reason")
    if event_type in {"browser.request_slow", "browser.request_health.slow"}:
        return request_health_dedupe_key(str(run_id) if run_id is not None else None, request_id, action_id, "slow", str(marker or ""))
    if event_type in {"browser.request_stuck", "browser.request_health.stuck"}:
        return request_health_dedupe_key(str(run_id) if run_id is not None else None, request_id, action_id, "stuck", str(marker or ""))
    if event_type in {"scheduler.request_recovered", "browser.request_health.recovered"}:
        return request_health_dedupe_key(str(run_id) if run_id is not None else None, request_id, action_id, "recovered", str(marker or ""))
    return None


def count_unique_request_signals(telemetry_events: list[dict[str, Any]], event_types: set[str]) -> int:
    count = 0
    seen_signals: set[str] = set()
    seen_events: set[str] = set()
    for event in telemetry_events:
        event_type = str(event.get("event_type") or "")
        if event_type not in event_types:
            continue
        signal_id = request_signal_identity(event)
        if signal_id:
            if signal_id in seen_signals:
                continue
            seen_signals.add(signal_id)
            count += 1
            continue
        details = event.get("details")
        details = details if isinstance(details, dict) else {}
        event_id = telemetry_dedupe_key(event_type, details)
        if event_id:
            if event_id in seen_events:
                continue
            seen_events.add(event_id)
        count += 1
    return count


def compute_project_metrics(
    project_alias: str,
    runs: list[RunState],
    interventions: list[OperatorInterventionRecord],
    audit_logs: list[dict[str, Any]],
    telemetry_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    telemetry_events = telemetry_events or []
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
    duplicate_result_recoveries += count_unique_telemetry_events(telemetry_events, "scheduler.result_replayed")
    stale_ownership_incidents = count_stale_ownership_incidents(run_blobs, audit_blobs, telemetry_events)

    failure_rate = (failed / started) if started else 0.0
    classifications = {bucket: 0 for bucket in FAILURE_BUCKETS}
    for run in runs:
        if run.status.value == "failed":
            classifications[classify_failure(run, interventions)] += 1
    browser_categories = browser_errors_by_category(runs, audit_logs, telemetry_events)
    intervention_reasons = count_by_reason(interventions)
    agent_outcomes = per_agent_outcomes(runs)
    intervention_agents = agents_requiring_intervention(interventions)
    a2a_sent = sum(1 for event in telemetry_events if str(event.get("event_type")) == "a2a.sent")
    a2a_succeeded = sum(1 for event in telemetry_events if str(event.get("event_type")) == "a2a.succeeded")
    a2a_failed = sum(1 for event in telemetry_events if str(event.get("event_type")) == "a2a.failed")
    scheduler_recoveries = count_unique_request_signals(
        telemetry_events,
        {"scheduler.request_recovered", "browser.request_health.recovered"},
    )
    scheduler_recoveries += count_unique_telemetry_events(telemetry_events, "scheduler.recovered")
    scheduler_recoveries += count_matching_audit_logs(audit_logs, "run.requeued", "run.recovered", "worker.request.recovered", "worker.result.replayed")
    plugin_denials = sum(
        1
        for item in audit_logs
        if str(item.get("action")) == "plugin.execution"
        and (
            bool((item.get("metadata") or {}).get("policy_violations"))
            or "policy" in lower_blob(item.get("metadata", {}))
            or "denied" in lower_blob(item.get("metadata", {}))
        )
    )
    plugin_denials += sum(1 for event in telemetry_events if str(event.get("event_type")) == "plugin.denial")
    request_health = request_health_summary(telemetry_events)
    snapshot = {
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
        "a2a_messages_sent": a2a_sent,
        "a2a_messages_succeeded": a2a_succeeded,
        "a2a_messages_failed": a2a_failed,
        "scheduler_recovery_events": scheduler_recoveries,
        "plugin_denials": plugin_denials,
        "average_run_latency_seconds": round(sum(latencies) / len(latencies), 2) if latencies else 0.0,
        "per_project_failure_rate": round(failure_rate, 4),
        "request_health_summary": request_health,
        "failure_classification": classifications,
        "browser_errors_by_category": browser_categories,
        "intervention_count_by_reason": intervention_reasons,
        "per_agent_outcomes": agent_outcomes,
        "agents_requiring_intervention": intervention_agents,
    }
    snapshot["alpha_gate"] = assess_project_alpha_gate(snapshot)
    return snapshot


def assess_project_alpha_gate(snapshot: dict[str, Any]) -> dict[str, Any]:
    request_health = snapshot.get("request_health_summary", {})
    unresolved = int(request_health.get("unresolved", 0))
    stuck = int(request_health.get("stuck", 0))
    recovered = int(request_health.get("recovered", 0))
    completed_after_slow = int(request_health.get("completed_after_slow", 0))
    safe_degraded_recoveries = (
        int(snapshot.get("scheduler_recovery_events", 0))
        + int(snapshot.get("duplicate_result_recoveries", 0))
        + recovered
        + completed_after_slow
    )
    unresolved_degradation = (
        unresolved
        + max(0, stuck - recovered)
        + int(snapshot.get("stale_ownership_incidents", 0))
    )
    unsafe_failures = (
        int(snapshot.get("runs_failed", 0))
        + int(snapshot.get("plugin_denials", 0))
    )
    reasons: list[str] = []
    recommendation = "continue"
    failure_rate = float(snapshot.get("per_project_failure_rate", 0.0))
    stale_ownership = int(snapshot.get("stale_ownership_incidents", 0))
    browser_crash_count = int(snapshot.get("browser_crash_count", 0))
    plugin_denials = int(snapshot.get("plugin_denials", 0))
    average_latency = float(snapshot.get("average_run_latency_seconds", 0.0))
    scheduler_recoveries = int(snapshot.get("scheduler_recovery_events", 0))

    if plugin_denials >= int(ALPHA_GATE_THRESHOLDS["hold_plugin_denials"]):
        reasons.append("plugin policy denials observed")
    if unresolved >= int(ALPHA_GATE_THRESHOLDS["hold_unresolved_requests"]):
        reasons.append("unresolved browser request health signals observed")
    if failure_rate >= float(ALPHA_GATE_THRESHOLDS["hold_failure_rate"]):
        reasons.append("failure rate exceeded restricted alpha hold threshold")
    if stale_ownership >= int(ALPHA_GATE_THRESHOLDS["hold_stale_ownership_incidents"]):
        reasons.append("stale ownership incidents exceeded hold threshold")
    if browser_crash_count >= int(ALPHA_GATE_THRESHOLDS["hold_browser_crash_count"]):
        reasons.append("browser crash count exceeded hold threshold")

    if reasons:
        recommendation = "hold"
    else:
        if (
            failure_rate <= float(ALPHA_GATE_THRESHOLDS["continue_failure_rate"])
            and average_latency <= float(ALPHA_GATE_THRESHOLDS["expand_average_latency_seconds"])
            and stale_ownership <= int(ALPHA_GATE_THRESHOLDS["expand_stale_ownership_incidents"])
            and unresolved == 0
            and plugin_denials == 0
        ):
            recommendation = "expand"
            reasons.append("request health and reliability signals are within expansion thresholds")
        else:
            if average_latency > float(ALPHA_GATE_THRESHOLDS["continue_average_latency_seconds"]):
                reasons.append("average latency remains above continue target")
            if scheduler_recoveries > int(ALPHA_GATE_THRESHOLDS["continue_scheduler_recoveries"]):
                reasons.append("scheduler recovery rate remains elevated")
            if stale_ownership > 0:
                reasons.append("stale ownership still requires monitoring")
            if not reasons:
                reasons.append("restricted alpha reliability remains within continue thresholds")

    release_blockers = list(reasons)
    return {
        "recommendation": recommendation,
        "safe_degraded_recoveries": safe_degraded_recoveries,
        "unresolved_degradation": unresolved_degradation,
        "unsafe_failures": unsafe_failures,
        "manual_interventions": int(snapshot.get("intervention_count", 0)),
        "release_blockers": release_blockers,
        "reasons": reasons,
    }


def overall_metrics(project_snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {key: 0 for key in METRIC_KEYS if key not in {"average_run_latency_seconds", "per_project_failure_rate"}}
    latencies: list[float] = []
    failure_rates: dict[str, float] = {}
    classifications = {bucket: 0 for bucket in FAILURE_BUCKETS}
    browser_categories = {bucket: 0 for bucket in FAILURE_BUCKETS}
    intervention_reasons: dict[str, int] = {}
    agent_outcomes: dict[str, dict[str, float | int]] = {}
    intervention_agents: dict[str, int] = {}
    request_health_totals = {
        "slow": 0,
        "stuck": 0,
        "recovered": 0,
        "completed_after_slow": 0,
        "unresolved": 0,
    }
    alpha_gate_projects: dict[str, dict[str, Any]] = {}
    for snapshot in project_snapshots:
        for key in totals:
            totals[key] += int(snapshot.get(key, 0))
        latencies.append(float(snapshot.get("average_run_latency_seconds", 0.0)))
        failure_rates[str(snapshot.get("project_alias"))] = float(snapshot.get("per_project_failure_rate", 0.0))
        for bucket, count in snapshot.get("failure_classification", {}).items():
            classifications[bucket] = classifications.get(bucket, 0) + int(count)
        for bucket, count in snapshot.get("browser_errors_by_category", {}).items():
            browser_categories[bucket] = browser_categories.get(bucket, 0) + int(count)
        for reason, count in snapshot.get("intervention_count_by_reason", {}).items():
            intervention_reasons[reason] = intervention_reasons.get(reason, 0) + int(count)
        for agent_id, values in snapshot.get("per_agent_outcomes", {}).items():
            current = agent_outcomes.setdefault(agent_id, {"runs_started": 0, "runs_completed": 0, "runs_failed": 0})
            current["runs_started"] = int(current["runs_started"]) + int(values.get("runs_started", 0))
            current["runs_completed"] = int(current["runs_completed"]) + int(values.get("runs_completed", 0))
            current["runs_failed"] = int(current["runs_failed"]) + int(values.get("runs_failed", 0))
        for agent_id, count in snapshot.get("agents_requiring_intervention", {}).items():
            intervention_agents[agent_id] = intervention_agents.get(agent_id, 0) + int(count)
        for key, count in snapshot.get("request_health_summary", {}).items():
            request_health_totals[key] = request_health_totals.get(key, 0) + int(count)
        alpha_gate_projects[str(snapshot.get("project_alias"))] = dict(snapshot.get("alpha_gate", {}))
    for values in agent_outcomes.values():
        started = int(values["runs_started"])
        completed = int(values["runs_completed"])
        failed = int(values["runs_failed"])
        values["success_rate"] = round((completed / started), 4) if started else 0.0
        values["failure_rate"] = round((failed / started), 4) if started else 0.0
    totals["average_run_latency_seconds"] = round(sum(latencies) / len(latencies), 2) if latencies else 0.0
    totals["per_project_failure_rate"] = failure_rates
    totals["failure_classification"] = classifications
    totals["browser_errors_by_category"] = browser_categories
    totals["intervention_count_by_reason"] = dict(sorted(intervention_reasons.items(), key=lambda item: (-item[1], item[0])))
    totals["per_agent_outcomes"] = dict(sorted(agent_outcomes.items()))
    totals["agents_requiring_intervention"] = dict(sorted(intervention_agents.items(), key=lambda item: (-item[1], item[0])))
    totals["request_health_summary"] = request_health_totals
    totals["alpha_gate"] = assess_overall_alpha_gate(project_snapshots, alpha_gate_projects)
    return totals


def assess_overall_alpha_gate(
    project_snapshots: list[dict[str, Any]],
    project_assessments: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    assessments = project_assessments or {
        str(snapshot.get("project_alias")): dict(snapshot.get("alpha_gate", {}))
        for snapshot in project_snapshots
    }
    recommendations = [str(assessment.get("recommendation", "continue")) for assessment in assessments.values()]
    if any(item == "hold" for item in recommendations):
        recommendation = "hold"
    elif recommendations and all(item == "expand" for item in recommendations):
        recommendation = "expand"
    else:
        recommendation = "continue"
    safe_degraded_recoveries = sum(int(assessment.get("safe_degraded_recoveries", 0)) for assessment in assessments.values())
    unresolved_degradation = sum(int(assessment.get("unresolved_degradation", 0)) for assessment in assessments.values())
    unsafe_failures = sum(int(assessment.get("unsafe_failures", 0)) for assessment in assessments.values())
    manual_interventions = sum(int(assessment.get("manual_interventions", 0)) for assessment in assessments.values())
    release_blockers: list[str] = []
    reasons: list[str] = []
    for alias, assessment in sorted(assessments.items()):
        for blocker in assessment.get("release_blockers", []):
            release_blockers.append(f"{alias}: {blocker}")
        for reason in assessment.get("reasons", []):
            reasons.append(f"{alias}: {reason}")
    if not reasons:
        reasons.append("all projects remain within current restricted alpha thresholds")
    return {
        "recommendation": recommendation,
        "safe_degraded_recoveries": safe_degraded_recoveries,
        "unresolved_degradation": unresolved_degradation,
        "unsafe_failures": unsafe_failures,
        "manual_interventions": manual_interventions,
        "release_blockers": release_blockers,
        "reasons": reasons,
        "projects": assessments,
    }


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


def filter_telemetry_events_since(telemetry_events: list[dict[str, Any]], since: datetime) -> list[dict[str, Any]]:
    threshold = since.isoformat()
    return [item for item in telemetry_events if str(item.get("timestamp", "")) >= threshold]


def sync_project_runtime_events(project_alias: str, since: datetime) -> int:
    recorded = 0
    with build_project_api(project_alias) as api:
        runs = filter_runs_since(api.list_runs(), since)
        for run in runs:
            try:
                events = api.get_run_events(run.run_id)
            except (httpx.HTTPStatusError, PermissionError) as exc:
                record_telemetry_event(
                    "runtime_events.unavailable",
                    project_alias=project_alias,
                    project_id=api.project_id,
                    run_id=run.run_id,
                    status="degraded",
                    details={"error": str(exc)},
                )
                continue
            for event in events:
                timestamp = event.get("timestamp")
                if isinstance(timestamp, str):
                    try:
                        event_time = datetime.fromisoformat(timestamp)
                    except ValueError:
                        event_time = None
                    if event_time is not None and event_time < since:
                        continue
                telemetry = normalize_runtime_event_to_telemetry(event, project_alias=project_alias)
                if telemetry is None:
                    continue
                record_telemetry_event(**telemetry)
                recorded += 1
    return recorded


def sync_project_request_health(project_alias: str, since: datetime) -> int:
    recorded = 0
    existing = load_telemetry_events_since(since)
    seen_keys = {
        key
        for event in existing
        for key in [telemetry_dedupe_key(str(event.get("event_type") or ""), event.get("details") if isinstance(event.get("details"), dict) else {})]
        if key
    }
    with build_project_api(project_alias) as api:
        runs = filter_runs_since(api.list_runs(), since)
        for run in runs:
            try:
                health_views = api.get_run_worker_requests(run.run_id)
            except (httpx.HTTPStatusError, PermissionError) as exc:
                record_telemetry_event(
                    "worker_request_health.unavailable",
                    project_alias=project_alias,
                    project_id=api.project_id,
                    run_id=run.run_id,
                    status="degraded",
                    details={"error": str(exc)},
                )
                continue
            for view in health_views:
                normalized = normalize_worker_request_health_to_telemetry(
                    view,
                    project_alias=project_alias,
                    project_id=api.project_id,
                    run_id=run.run_id,
                )
                for telemetry in normalized:
                    dedupe_key = telemetry_dedupe_key(
                        str(telemetry.get("event_type") or ""),
                        telemetry.get("details") if isinstance(telemetry.get("details"), dict) else {},
                    )
                    if dedupe_key and dedupe_key in seen_keys:
                        continue
                    if dedupe_key:
                        seen_keys.add(dedupe_key)
                    record_telemetry_event(**telemetry)
                    recorded += 1
    return recorded


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


def write_report_json(name: str, payload: Any) -> dict[str, str]:
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    repo_target = runtime_reports_dir() / name
    log_target = reports_dir() / name
    repo_target.write_text(serialized)
    log_target.write_text(serialized)
    return {"repo": str(repo_target), "log": str(log_target)}


def write_report_text(name: str, payload: str) -> dict[str, str]:
    repo_target = runtime_reports_dir() / name
    log_target = reports_dir() / name
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


def is_timeout_error(exc: Exception) -> bool:
    return isinstance(exc, (TimeoutError, httpx.TimeoutException))


def is_stale_ownership_error(exc: Exception) -> bool:
    return is_stale_ownership_signal(str(exc).strip().lower())


def is_retryable_swarm_error(exc: Exception) -> bool:
    return is_retryable_http_error(exc) or is_timeout_error(exc) or is_stale_ownership_error(exc)


def retry_with_backoff(
    action: Callable[[], Any],
    *,
    label: str,
    attempts: int = 4,
    base_delay_seconds: float = 5.0,
    max_delay_seconds: float = 60.0,
    telemetry_context: dict[str, Any] | None = None,
) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            result = action()
            if telemetry_context and attempt > 1:
                record_telemetry_event(
                    "scheduler.recovered",
                    project_alias=str(telemetry_context.get("project_alias") or "") or None,
                    project_id=str(telemetry_context.get("project_id") or "") or None,
                    role=str(telemetry_context.get("role") or "") or None,
                    agent_id=str(telemetry_context.get("agent_id") or "") or None,
                    run_id=str(telemetry_context.get("run_id") or "") or None,
                    status="recovered",
                    details={"label": label, "attempt": attempt},
                )
            return result
        except Exception as exc:
            last_error = exc
            if telemetry_context and label.startswith(("browser.", "browser-runner", "chaos-monkey")):
                status_code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                retryable = is_retryable_swarm_error(exc)
                event_name = "browser.retry" if attempt < attempts and retryable else "browser.error"
                if is_stale_ownership_error(exc):
                    record_telemetry_event(
                        "scheduler.stale_ownership",
                        project_alias=str(telemetry_context.get("project_alias") or "") or None,
                        project_id=str(telemetry_context.get("project_id") or "") or None,
                        role=str(telemetry_context.get("role") or "") or None,
                        agent_id=str(telemetry_context.get("agent_id") or "") or None,
                        run_id=str(telemetry_context.get("run_id") or "") or None,
                        status="retrying" if event_name == "browser.retry" else "failed",
                        details={"label": label, "attempt": attempt, "error": str(exc)},
                    )
                record_telemetry_event(
                    event_name,
                    project_alias=str(telemetry_context.get("project_alias") or "") or None,
                    project_id=str(telemetry_context.get("project_id") or "") or None,
                    role=str(telemetry_context.get("role") or "") or None,
                    agent_id=str(telemetry_context.get("agent_id") or "") or None,
                    run_id=str(telemetry_context.get("run_id") or "") or None,
                    status="retrying" if event_name == "browser.retry" else "failed",
                    details={
                        "label": label,
                        "attempt": attempt,
                        "status_code": status_code,
                        "error": str(exc),
                        "category": browser_error_category_from_payload({"label": label, "error": str(exc), "status_code": status_code}),
                    },
                )
            if attempt >= attempts or not is_retryable_swarm_error(exc):
                raise
            delay = min(max_delay_seconds, base_delay_seconds * (2 ** (attempt - 1)))
            print({"label": label, "attempt": attempt, "retry_in_seconds": delay, "error": str(exc)})
            sleep_with_jitter(delay, jitter_seconds=min(5.0, delay * 0.2))
    raise RuntimeError(f"{label} failed without raising an exception") from last_error


def run_forever(
    step: Callable[[], None],
    *,
    once: bool,
    interval_seconds: float,
    role_name: str | None = None,
    agent_id: str | None = None,
    project_alias: str | None = None,
) -> None:
    consecutive_failures = 0
    while True:
        started_at = time.monotonic()
        try:
            step()
            if consecutive_failures > 0:
                creds = project_credentials_for_alias(project_alias)
                record_telemetry_event(
                    "scheduler.recovered",
                    project_alias=project_alias,
                    project_id=creds.project_id if creds else None,
                    role=role_name,
                    agent_id=agent_id,
                    status="recovered",
                    details={"consecutive_failures": consecutive_failures},
                )
            consecutive_failures = 0
            if once:
                return
            elapsed = time.monotonic() - started_at
            sleep_with_jitter(max(0.0, interval_seconds - elapsed), jitter_seconds=min(30.0, interval_seconds * 0.1))
        except Exception as exc:
            consecutive_failures += 1
            creds = project_credentials_for_alias(project_alias)
            record_telemetry_event(
                "scheduler.loop_error",
                project_alias=project_alias,
                project_id=creds.project_id if creds else None,
                role=role_name,
                agent_id=agent_id,
                status="error",
                details={"error": str(exc), "consecutive_failures": consecutive_failures},
            )
            if once:
                raise
            delay = min(max(5.0, interval_seconds / 3), 300.0, 5.0 * (2 ** (consecutive_failures - 1)))
            print({"loop_error": str(exc), "consecutive_failures": consecutive_failures, "retry_in_seconds": delay})
            sleep_with_jitter(delay, jitter_seconds=min(10.0, delay * 0.25))


def role_project_alias(role_name: str) -> str:
    if role_name in {"browser-runner-2", "chaos-monkey"}:
        return "chaos"
    return "steady"
