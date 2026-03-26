from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from synapse.config import settings

try:
    from redis.asyncio import Redis
except Exception:  # pragma: no cover - optional import for environments without redis package.
    Redis = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)


class RuntimeStateStore(ABC):
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    @abstractmethod
    async def register_agent(self, agent: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    async def list_agents(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def store_session(self, session_id: str, session_data: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    async def list_sessions(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def delete_session(self, session_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def store_connection(self, agent_id: str, connection_data: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_connection(self, agent_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    async def list_connections(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def delete_connection(self, agent_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def store_checkpoint(self, checkpoint_id: str, checkpoint_data: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_checkpoint(self, checkpoint_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    async def list_checkpoints(self, agent_id: str | None = None, task_id: str | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def delete_checkpoint(self, checkpoint_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def store_profile(self, profile_id: str, profile_data: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_profile(self, profile_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    async def list_profiles(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def delete_profile(self, profile_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def store_run(self, run_id: str, run_data: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    async def list_runs(
        self,
        agent_id: str | None = None,
        task_id: str | None = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def store_run_lease(self, run_id: str, lease_data: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_run_lease(self, run_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    async def list_run_leases(self, worker_id: str | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def delete_run_lease(self, run_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def acquire_run_lease(self, run_id: str, lease_data: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def renew_run_lease(self, run_id: str, worker_id: str, token: int, lease_data: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def store_worker(self, worker_id: str, worker_data: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_worker(self, worker_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    async def list_workers(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def store_session_ownership(self, session_id: str, ownership_data: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_session_ownership(self, session_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    async def list_session_ownerships(
        self,
        worker_id: str | None = None,
        controller_id: str | None = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def delete_session_ownership(self, session_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def store_worker_request(self, run_id: str | None, action_id: str, request_data: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_worker_request(self, run_id: str | None, action_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    async def list_worker_requests(
        self,
        *,
        run_id: str | None = None,
        worker_id: str | None = None,
        session_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def store_worker_result(self, run_id: str | None, action_id: str, result_data: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_worker_result(self, run_id: str | None, action_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    async def list_worker_results(
        self,
        *,
        run_id: str | None = None,
        worker_id: str | None = None,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def store_intervention(self, intervention_id: str, intervention_data: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_intervention(self, intervention_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    async def list_interventions(
        self,
        project_id: str | None = None,
        run_id: str | None = None,
        state: str | None = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def store_runtime_event(self, event_id: str, event_data: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_runtime_events(
        self,
        run_id: str | None = None,
        agent_id: str | None = None,
        task_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def store_organization(self, organization_id: str, organization_data: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def list_organizations(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def store_project(self, project_id: str, project_data: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def list_projects(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def store_user(self, user_id: str, user_data: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def list_users(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def store_api_key(self, api_key_id: str, api_key_data: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def list_api_keys(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def store_agent_ownership(self, agent_id: str, ownership_data: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_agent_ownership(self, agent_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    async def store_audit_log(self, audit_log_id: str, audit_log_data: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def list_audit_logs(self, project_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        raise NotImplementedError


class InMemoryRuntimeStateStore(RuntimeStateStore):
    def __init__(self) -> None:
        self._lease_lock = asyncio.Lock()
        self._agents: dict[str, dict[str, Any]] = {}
        self._sessions: dict[str, dict[str, Any]] = {}
        self._connections: dict[str, dict[str, Any]] = {}
        self._checkpoints: dict[str, dict[str, Any]] = {}
        self._profiles: dict[str, dict[str, Any]] = {}
        self._runs: dict[str, dict[str, Any]] = {}
        self._events: dict[str, dict[str, Any]] = {}
        self._event_ids: list[str] = []
        self._run_leases: dict[str, dict[str, Any]] = {}
        self._lease_counter: int = 0
        self._workers: dict[str, dict[str, Any]] = {}
        self._session_ownership: dict[str, dict[str, Any]] = {}
        self._worker_requests: dict[tuple[str | None, str], dict[str, Any]] = {}
        self._worker_results: dict[tuple[str | None, str], dict[str, Any]] = {}
        self._interventions: dict[str, dict[str, Any]] = {}
        self._organizations: dict[str, dict[str, Any]] = {}
        self._projects: dict[str, dict[str, Any]] = {}
        self._users: dict[str, dict[str, Any]] = {}
        self._api_keys: dict[str, dict[str, Any]] = {}
        self._agent_ownership: dict[str, dict[str, Any]] = {}
        self._audit_logs: dict[str, dict[str, Any]] = {}
        self._audit_log_ids: list[str] = []

    async def register_agent(self, agent: dict[str, Any]) -> None:
        self._agents[agent["agent_id"]] = dict(agent)

    async def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        record = self._agents.get(agent_id)
        return dict(record) if record is not None else None

    async def list_agents(self) -> list[dict[str, Any]]:
        return [dict(value) for value in self._agents.values()]

    async def store_session(self, session_id: str, session_data: dict[str, Any]) -> None:
        self._sessions[session_id] = dict(session_data)

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        record = self._sessions.get(session_id)
        return dict(record) if record is not None else None

    async def list_sessions(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        sessions = [dict(value) for value in self._sessions.values()]
        if agent_id is None:
            return sessions
        return [session for session in sessions if session.get("agent_id") == agent_id]

    async def delete_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    async def store_connection(self, agent_id: str, connection_data: dict[str, Any]) -> None:
        self._connections[agent_id] = dict(connection_data)

    async def get_connection(self, agent_id: str) -> dict[str, Any] | None:
        record = self._connections.get(agent_id)
        return dict(record) if record is not None else None

    async def list_connections(self) -> list[dict[str, Any]]:
        return [dict(value) for value in self._connections.values()]

    async def delete_connection(self, agent_id: str) -> None:
        self._connections.pop(agent_id, None)

    async def store_checkpoint(self, checkpoint_id: str, checkpoint_data: dict[str, Any]) -> None:
        self._checkpoints[checkpoint_id] = dict(checkpoint_data)

    async def get_checkpoint(self, checkpoint_id: str) -> dict[str, Any] | None:
        record = self._checkpoints.get(checkpoint_id)
        return dict(record) if record is not None else None

    async def list_checkpoints(self, agent_id: str | None = None, task_id: str | None = None) -> list[dict[str, Any]]:
        rows = [dict(value) for value in self._checkpoints.values()]
        if agent_id is not None:
            rows = [row for row in rows if row.get("agent_id") == agent_id]
        if task_id is not None:
            rows = [row for row in rows if row.get("task_id") == task_id]
        return rows

    async def delete_checkpoint(self, checkpoint_id: str) -> None:
        self._checkpoints.pop(checkpoint_id, None)

    async def store_profile(self, profile_id: str, profile_data: dict[str, Any]) -> None:
        self._profiles[profile_id] = dict(profile_data)

    async def get_profile(self, profile_id: str) -> dict[str, Any] | None:
        record = self._profiles.get(profile_id)
        return dict(record) if record is not None else None

    async def list_profiles(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        rows = [dict(value) for value in self._profiles.values()]
        if agent_id is not None:
            rows = [row for row in rows if row.get("agent_id") == agent_id]
        return rows

    async def delete_profile(self, profile_id: str) -> None:
        self._profiles.pop(profile_id, None)

    async def store_run(self, run_id: str, run_data: dict[str, Any]) -> None:
        self._runs[run_id] = dict(run_data)

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        record = self._runs.get(run_id)
        return dict(record) if record is not None else None

    async def list_runs(
        self,
        agent_id: str | None = None,
        task_id: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = [dict(value) for value in self._runs.values()]
        if agent_id is not None:
            rows = [row for row in rows if row.get("agent_id") == agent_id]
        if task_id is not None:
            rows = [row for row in rows if row.get("task_id") == task_id]
        return rows

    async def store_run_lease(self, run_id: str, lease_data: dict[str, Any]) -> None:
        payload = dict(lease_data)
        token = payload.get("token")
        if isinstance(token, int):
            self._lease_counter = max(self._lease_counter, token)
        self._run_leases[run_id] = payload

    async def get_run_lease(self, run_id: str) -> dict[str, Any] | None:
        record = self._run_leases.get(run_id)
        return dict(record) if record is not None else None

    async def list_run_leases(self, worker_id: str | None = None) -> list[dict[str, Any]]:
        rows = [dict(value) for value in self._run_leases.values()]
        if worker_id is not None:
            rows = [row for row in rows if row.get("worker_id") == worker_id]
        return rows

    async def delete_run_lease(self, run_id: str) -> None:
        self._run_leases.pop(run_id, None)

    async def acquire_run_lease(self, run_id: str, lease_data: dict[str, Any]) -> dict[str, Any]:
        async with self._lease_lock:
            current = self._run_leases.get(run_id)
            if current is not None:
                expires_at = current.get("expires_at")
                status = current.get("status")
                if status == "active" and isinstance(expires_at, str) and datetime.fromisoformat(expires_at) > datetime.now(timezone.utc):
                    return dict(current)
            self._lease_counter += 1
            payload = dict(lease_data)
            payload["token"] = self._lease_counter
            self._run_leases[run_id] = payload
            return dict(payload)

    async def renew_run_lease(self, run_id: str, worker_id: str, token: int, lease_data: dict[str, Any]) -> dict[str, Any]:
        async with self._lease_lock:
            current = self._run_leases.get(run_id)
            if current is None:
                raise KeyError(f"Run lease not found: {run_id}")
            if current.get("worker_id") != worker_id or int(current.get("token", -1)) != token:
                raise PermissionError("Stale fencing token for run lease renewal.")
            payload = dict(lease_data)
            payload["token"] = token
            self._run_leases[run_id] = payload
            return dict(payload)

    async def store_worker(self, worker_id: str, worker_data: dict[str, Any]) -> None:
        self._workers[worker_id] = dict(worker_data)

    async def get_worker(self, worker_id: str) -> dict[str, Any] | None:
        record = self._workers.get(worker_id)
        return dict(record) if record is not None else None

    async def list_workers(self) -> list[dict[str, Any]]:
        return [dict(value) for value in self._workers.values()]

    async def store_session_ownership(self, session_id: str, ownership_data: dict[str, Any]) -> None:
        self._session_ownership[session_id] = dict(ownership_data)

    async def get_session_ownership(self, session_id: str) -> dict[str, Any] | None:
        record = self._session_ownership.get(session_id)
        return dict(record) if record is not None else None

    async def list_session_ownerships(
        self,
        worker_id: str | None = None,
        controller_id: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = [dict(value) for value in self._session_ownership.values()]
        if worker_id is not None:
            rows = [row for row in rows if row.get("worker_id") == worker_id]
        if controller_id is not None:
            rows = [row for row in rows if row.get("controller_id") == controller_id]
        return rows

    async def delete_session_ownership(self, session_id: str) -> None:
        self._session_ownership.pop(session_id, None)

    async def store_worker_request(self, run_id: str | None, action_id: str, request_data: dict[str, Any]) -> None:
        self._worker_requests[(run_id, action_id)] = dict(request_data)

    async def get_worker_request(self, run_id: str | None, action_id: str) -> dict[str, Any] | None:
        record = self._worker_requests.get((run_id, action_id))
        return dict(record) if record is not None else None

    async def list_worker_requests(
        self,
        *,
        run_id: str | None = None,
        worker_id: str | None = None,
        session_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = [dict(value) for value in self._worker_requests.values()]
        if run_id is not None:
            rows = [row for row in rows if row.get("run_id") == run_id]
        if worker_id is not None:
            rows = [row for row in rows if row.get("worker_id") == worker_id]
        if session_id is not None:
            rows = [row for row in rows if row.get("session_id") == session_id]
        if status is not None:
            rows = [row for row in rows if row.get("status") == status]
        rows.sort(key=lambda row: str(row.get("created_at", "")))
        return rows

    async def store_worker_result(self, run_id: str | None, action_id: str, result_data: dict[str, Any]) -> None:
        self._worker_results[(run_id, action_id)] = dict(result_data)

    async def get_worker_result(self, run_id: str | None, action_id: str) -> dict[str, Any] | None:
        record = self._worker_results.get((run_id, action_id))
        return dict(record) if record is not None else None

    async def list_worker_results(
        self,
        *,
        run_id: str | None = None,
        worker_id: str | None = None,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = [dict(value) for value in self._worker_results.values()]
        if run_id is not None:
            rows = [row for row in rows if row.get("run_id") == run_id]
        if worker_id is not None:
            rows = [row for row in rows if row.get("worker_id") == worker_id]
        if session_id is not None:
            rows = [row for row in rows if row.get("session_id") == session_id]
        rows.sort(key=lambda row: str(row.get("completed_at", "")))
        return rows

    async def store_intervention(self, intervention_id: str, intervention_data: dict[str, Any]) -> None:
        self._interventions[intervention_id] = dict(intervention_data)

    async def get_intervention(self, intervention_id: str) -> dict[str, Any] | None:
        record = self._interventions.get(intervention_id)
        return dict(record) if record is not None else None

    async def list_interventions(
        self,
        project_id: str | None = None,
        run_id: str | None = None,
        state: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = [dict(value) for value in self._interventions.values()]
        if project_id is not None:
            rows = [row for row in rows if row.get("project_id") == project_id]
        if run_id is not None:
            rows = [row for row in rows if row.get("run_id") == run_id]
        if state is not None:
            rows = [row for row in rows if row.get("state") == state]
        rows.sort(key=lambda row: str(row.get("created_at", "")), reverse=True)
        return rows

    async def store_runtime_event(self, event_id: str, event_data: dict[str, Any]) -> None:
        self._events[event_id] = dict(event_data)
        self._event_ids.insert(0, event_id)
        if len(self._event_ids) > 10_000:
            stale = self._event_ids.pop()
            self._events.pop(stale, None)

    async def get_runtime_events(
        self,
        run_id: str | None = None,
        agent_id: str | None = None,
        task_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for event_id in self._event_ids:
            event = self._events[event_id]
            if run_id is not None and event.get("run_id") != run_id:
                continue
            if agent_id is not None and event.get("agent_id") != agent_id:
                continue
            if task_id is not None and event.get("task_id") != task_id:
                continue
            rows.append(dict(event))
            if len(rows) >= limit:
                break
        return rows

    async def store_organization(self, organization_id: str, organization_data: dict[str, Any]) -> None:
        self._organizations[organization_id] = dict(organization_data)

    async def list_organizations(self) -> list[dict[str, Any]]:
        return [dict(value) for value in self._organizations.values()]

    async def store_project(self, project_id: str, project_data: dict[str, Any]) -> None:
        self._projects[project_id] = dict(project_data)

    async def list_projects(self) -> list[dict[str, Any]]:
        return [dict(value) for value in self._projects.values()]

    async def store_user(self, user_id: str, user_data: dict[str, Any]) -> None:
        self._users[user_id] = dict(user_data)

    async def list_users(self) -> list[dict[str, Any]]:
        return [dict(value) for value in self._users.values()]

    async def store_api_key(self, api_key_id: str, api_key_data: dict[str, Any]) -> None:
        self._api_keys[api_key_id] = dict(api_key_data)

    async def list_api_keys(self) -> list[dict[str, Any]]:
        return [dict(value) for value in self._api_keys.values()]

    async def store_agent_ownership(self, agent_id: str, ownership_data: dict[str, Any]) -> None:
        self._agent_ownership[agent_id] = dict(ownership_data)

    async def get_agent_ownership(self, agent_id: str) -> dict[str, Any] | None:
        record = self._agent_ownership.get(agent_id)
        return dict(record) if record is not None else None

    async def store_audit_log(self, audit_log_id: str, audit_log_data: dict[str, Any]) -> None:
        self._audit_logs[audit_log_id] = dict(audit_log_data)
        self._audit_log_ids.insert(0, audit_log_id)
        if len(self._audit_log_ids) > 10_000:
            stale = self._audit_log_ids.pop()
            self._audit_logs.pop(stale, None)

    async def list_audit_logs(self, project_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for audit_log_id in self._audit_log_ids:
            record = self._audit_logs[audit_log_id]
            if project_id is not None and record.get("project_id") != project_id:
                continue
            rows.append(dict(record))
            if len(rows) >= limit:
                break
        return rows


class RedisRuntimeStateStore(RuntimeStateStore):
    def __init__(self, redis_url: str, *, max_events: int = 10_000) -> None:
        self._redis_url = redis_url
        self._max_events = max_events
        self._redis: Redis | None = None

    async def start(self) -> None:
        if Redis is None:
            raise RuntimeError("redis package is not available.")
        if self._redis is not None:
            return
        self._redis = Redis.from_url(self._redis_url, decode_responses=True)
        await self._redis.ping()

    async def stop(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    async def register_agent(self, agent: dict[str, Any]) -> None:
        redis = self._require_redis()
        agent_id = str(agent["agent_id"])
        await redis.set(self._agent_key(agent_id), json.dumps(agent))
        await redis.sadd("synapse:agents:index", agent_id)

    async def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        redis = self._require_redis()
        payload = await redis.get(self._agent_key(agent_id))
        return self._decode(payload)

    async def list_agents(self) -> list[dict[str, Any]]:
        redis = self._require_redis()
        ids = await redis.smembers("synapse:agents:index")
        keys = [self._agent_key(agent_id) for agent_id in ids]
        return await self._mget_json(keys)

    async def store_session(self, session_id: str, session_data: dict[str, Any]) -> None:
        redis = self._require_redis()
        await redis.set(self._session_key(session_id), json.dumps(session_data))
        await redis.sadd("synapse:sessions:index", session_id)
        agent_id = session_data.get("agent_id")
        if isinstance(agent_id, str) and agent_id:
            await redis.sadd(f"synapse:sessions:agent:{agent_id}", session_id)

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        redis = self._require_redis()
        payload = await redis.get(self._session_key(session_id))
        return self._decode(payload)

    async def list_sessions(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        redis = self._require_redis()
        if agent_id is None:
            ids = await redis.smembers("synapse:sessions:index")
        else:
            ids = await redis.smembers(f"synapse:sessions:agent:{agent_id}")
        keys = [self._session_key(session_id) for session_id in ids]
        return await self._mget_json(keys)

    async def delete_session(self, session_id: str) -> None:
        redis = self._require_redis()
        session = await self.get_session(session_id)
        await redis.delete(self._session_key(session_id))
        await redis.srem("synapse:sessions:index", session_id)
        if session is not None and isinstance(session.get("agent_id"), str):
            await redis.srem(f"synapse:sessions:agent:{session['agent_id']}", session_id)

    async def store_connection(self, agent_id: str, connection_data: dict[str, Any]) -> None:
        redis = self._require_redis()
        await redis.set(self._connection_key(agent_id), json.dumps(connection_data))
        await redis.sadd("synapse:connections:index", agent_id)

    async def get_connection(self, agent_id: str) -> dict[str, Any] | None:
        redis = self._require_redis()
        payload = await redis.get(self._connection_key(agent_id))
        return self._decode(payload)

    async def list_connections(self) -> list[dict[str, Any]]:
        redis = self._require_redis()
        ids = await redis.smembers("synapse:connections:index")
        keys = [self._connection_key(agent_id) for agent_id in ids]
        return await self._mget_json(keys)

    async def delete_connection(self, agent_id: str) -> None:
        redis = self._require_redis()
        await redis.delete(self._connection_key(agent_id))
        await redis.srem("synapse:connections:index", agent_id)

    async def store_checkpoint(self, checkpoint_id: str, checkpoint_data: dict[str, Any]) -> None:
        redis = self._require_redis()
        await redis.set(self._checkpoint_key(checkpoint_id), json.dumps(checkpoint_data))
        await redis.sadd("synapse:checkpoints:index", checkpoint_id)
        if isinstance(checkpoint_data.get("agent_id"), str):
            await redis.sadd(f"synapse:checkpoints:agent:{checkpoint_data['agent_id']}", checkpoint_id)
        if isinstance(checkpoint_data.get("task_id"), str):
            await redis.sadd(f"synapse:checkpoints:task:{checkpoint_data['task_id']}", checkpoint_id)

    async def get_checkpoint(self, checkpoint_id: str) -> dict[str, Any] | None:
        redis = self._require_redis()
        payload = await redis.get(self._checkpoint_key(checkpoint_id))
        return self._decode(payload)

    async def list_checkpoints(self, agent_id: str | None = None, task_id: str | None = None) -> list[dict[str, Any]]:
        redis = self._require_redis()
        if agent_id is not None:
            ids = await redis.smembers(f"synapse:checkpoints:agent:{agent_id}")
        elif task_id is not None:
            ids = await redis.smembers(f"synapse:checkpoints:task:{task_id}")
        else:
            ids = await redis.smembers("synapse:checkpoints:index")
        keys = [self._checkpoint_key(checkpoint_id) for checkpoint_id in ids]
        records = await self._mget_json(keys)
        if task_id is not None:
            records = [record for record in records if record.get("task_id") == task_id]
        return records

    async def delete_checkpoint(self, checkpoint_id: str) -> None:
        redis = self._require_redis()
        checkpoint = await self.get_checkpoint(checkpoint_id)
        await redis.delete(self._checkpoint_key(checkpoint_id))
        await redis.srem("synapse:checkpoints:index", checkpoint_id)
        if checkpoint is not None:
            if isinstance(checkpoint.get("agent_id"), str):
                await redis.srem(f"synapse:checkpoints:agent:{checkpoint['agent_id']}", checkpoint_id)
            if isinstance(checkpoint.get("task_id"), str):
                await redis.srem(f"synapse:checkpoints:task:{checkpoint['task_id']}", checkpoint_id)

    async def store_profile(self, profile_id: str, profile_data: dict[str, Any]) -> None:
        redis = self._require_redis()
        await redis.set(self._profile_key(profile_id), json.dumps(profile_data))
        await redis.sadd("synapse:profiles:index", profile_id)
        agent_id = profile_data.get("agent_id")
        if isinstance(agent_id, str) and agent_id:
            await redis.sadd(f"synapse:profiles:agent:{agent_id}", profile_id)

    async def get_profile(self, profile_id: str) -> dict[str, Any] | None:
        redis = self._require_redis()
        payload = await redis.get(self._profile_key(profile_id))
        return self._decode(payload)

    async def list_profiles(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        redis = self._require_redis()
        if agent_id is None:
            ids = await redis.smembers("synapse:profiles:index")
        else:
            ids = await redis.smembers(f"synapse:profiles:agent:{agent_id}")
        keys = [self._profile_key(profile_id) for profile_id in ids]
        return await self._mget_json(keys)

    async def delete_profile(self, profile_id: str) -> None:
        redis = self._require_redis()
        profile = await self.get_profile(profile_id)
        await redis.delete(self._profile_key(profile_id))
        await redis.srem("synapse:profiles:index", profile_id)
        if profile is not None and isinstance(profile.get("agent_id"), str):
            await redis.srem(f"synapse:profiles:agent:{profile['agent_id']}", profile_id)

    async def store_run(self, run_id: str, run_data: dict[str, Any]) -> None:
        redis = self._require_redis()
        await redis.set(self._run_key(run_id), json.dumps(run_data))
        await redis.sadd("synapse:runs:index", run_id)
        if isinstance(run_data.get("agent_id"), str):
            await redis.sadd(f"synapse:runs:agent:{run_data['agent_id']}", run_id)
        if isinstance(run_data.get("task_id"), str):
            await redis.sadd(f"synapse:runs:task:{run_data['task_id']}", run_id)

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        redis = self._require_redis()
        payload = await redis.get(self._run_key(run_id))
        return self._decode(payload)

    async def list_runs(
        self,
        agent_id: str | None = None,
        task_id: str | None = None,
    ) -> list[dict[str, Any]]:
        redis = self._require_redis()
        if agent_id is not None:
            ids = await redis.smembers(f"synapse:runs:agent:{agent_id}")
        elif task_id is not None:
            ids = await redis.smembers(f"synapse:runs:task:{task_id}")
        else:
            ids = await redis.smembers("synapse:runs:index")
        keys = [self._run_key(run_id) for run_id in ids]
        records = await self._mget_json(keys)
        if task_id is not None:
            records = [record for record in records if record.get("task_id") == task_id]
        return records

    async def store_run_lease(self, run_id: str, lease_data: dict[str, Any]) -> None:
        redis = self._require_redis()
        previous = await self.get_run_lease(run_id)
        token = lease_data.get("token")
        if isinstance(token, int):
            current_counter = await redis.get("synapse:run-leases:token")
            current_value = int(current_counter) if current_counter is not None else 0
            if token > current_value:
                await redis.set("synapse:run-leases:token", token)
        await redis.set(self._run_lease_key(run_id), json.dumps(lease_data))
        await redis.sadd("synapse:run-leases:index", run_id)
        if previous is not None and isinstance(previous.get("worker_id"), str):
            await redis.srem(f"synapse:run-leases:worker:{previous['worker_id']}", run_id)
        worker_id = lease_data.get("worker_id")
        if isinstance(worker_id, str) and worker_id:
            await redis.sadd(f"synapse:run-leases:worker:{worker_id}", run_id)

    async def get_run_lease(self, run_id: str) -> dict[str, Any] | None:
        redis = self._require_redis()
        payload = await redis.get(self._run_lease_key(run_id))
        return self._decode(payload)

    async def list_run_leases(self, worker_id: str | None = None) -> list[dict[str, Any]]:
        redis = self._require_redis()
        if worker_id is None:
            ids = await redis.smembers("synapse:run-leases:index")
        else:
            ids = await redis.smembers(f"synapse:run-leases:worker:{worker_id}")
        keys = [self._run_lease_key(run_id) for run_id in ids]
        rows = await self._mget_json(keys)
        if worker_id is not None:
            rows = [row for row in rows if row.get("worker_id") == worker_id]
        return rows

    async def delete_run_lease(self, run_id: str) -> None:
        redis = self._require_redis()
        lease = await self.get_run_lease(run_id)
        await redis.delete(self._run_lease_key(run_id))
        await redis.srem("synapse:run-leases:index", run_id)
        if lease is not None and isinstance(lease.get("worker_id"), str):
            await redis.srem(f"synapse:run-leases:worker:{lease['worker_id']}", run_id)

    async def acquire_run_lease(self, run_id: str, lease_data: dict[str, Any]) -> dict[str, Any]:
        redis = self._require_redis()
        payload = dict(lease_data)
        result = await redis.eval(
            """
            local lease_key = KEYS[1]
            local counter_key = KEYS[2]
            local now_iso = ARGV[1]
            local payload = cjson.decode(ARGV[2])
            local current_raw = redis.call("GET", lease_key)
            if current_raw then
              local current = cjson.decode(current_raw)
              if current["status"] == "active" and current["expires_at"] and tostring(current["expires_at"]) > now_iso then
                return current_raw
              end
            end
            local token = redis.call("INCR", counter_key)
            payload["token"] = token
            local encoded = cjson.encode(payload)
            redis.call("SET", lease_key, encoded)
            return encoded
            """,
            2,
            self._run_lease_key(run_id),
            "synapse:run-leases:token",
            datetime.now(timezone.utc).isoformat(),
            json.dumps(payload),
        )
        resolved = self._decode(result) or payload
        worker_id = resolved.get("worker_id")
        if isinstance(worker_id, str) and worker_id:
            await redis.sadd("synapse:run-leases:index", run_id)
            await redis.sadd(f"synapse:run-leases:worker:{worker_id}", run_id)
        return resolved

    async def renew_run_lease(self, run_id: str, worker_id: str, token: int, lease_data: dict[str, Any]) -> dict[str, Any]:
        redis = self._require_redis()
        payload = dict(lease_data)
        result = await redis.eval(
            """
            local lease_key = KEYS[1]
            local current_raw = redis.call("GET", lease_key)
            if not current_raw then
              return cjson.encode({error="missing"})
            end
            local current = cjson.decode(current_raw)
            if tostring(current["worker_id"]) ~= ARGV[1] or tonumber(current["token"]) ~= tonumber(ARGV[2]) then
              return cjson.encode({error="stale"})
            end
            local payload = cjson.decode(ARGV[3])
            payload["token"] = tonumber(ARGV[2])
            local encoded = cjson.encode(payload)
            redis.call("SET", lease_key, encoded)
            return encoded
            """,
            1,
            self._run_lease_key(run_id),
            worker_id,
            str(token),
            json.dumps(payload),
        )
        decoded = self._decode(result)
        if decoded is None:
            raise RuntimeError(f"Failed to renew run lease: {run_id}")
        if decoded.get("error") == "missing":
            raise KeyError(f"Run lease not found: {run_id}")
        if decoded.get("error") == "stale":
            raise PermissionError("Stale fencing token for run lease renewal.")
        resolved = decoded
        await redis.sadd("synapse:run-leases:index", run_id)
        await redis.sadd(f"synapse:run-leases:worker:{worker_id}", run_id)
        return resolved

    async def store_worker(self, worker_id: str, worker_data: dict[str, Any]) -> None:
        redis = self._require_redis()
        await redis.set(self._worker_key(worker_id), json.dumps(worker_data))
        await redis.sadd("synapse:workers:index", worker_id)

    async def get_worker(self, worker_id: str) -> dict[str, Any] | None:
        redis = self._require_redis()
        payload = await redis.get(self._worker_key(worker_id))
        return self._decode(payload)

    async def list_workers(self) -> list[dict[str, Any]]:
        redis = self._require_redis()
        ids = await redis.smembers("synapse:workers:index")
        return await self._mget_json([self._worker_key(worker_id) for worker_id in ids])

    async def store_session_ownership(self, session_id: str, ownership_data: dict[str, Any]) -> None:
        redis = self._require_redis()
        previous = await self.get_session_ownership(session_id)
        await redis.set(self._session_ownership_key(session_id), json.dumps(ownership_data))
        await redis.sadd("synapse:session-ownership:index", session_id)
        if previous is not None:
            if isinstance(previous.get("worker_id"), str):
                await redis.srem(f"synapse:session-ownership:worker:{previous['worker_id']}", session_id)
            if isinstance(previous.get("controller_id"), str):
                await redis.srem(f"synapse:session-ownership:controller:{previous['controller_id']}", session_id)
        worker_id = ownership_data.get("worker_id")
        controller_id = ownership_data.get("controller_id")
        if isinstance(worker_id, str) and worker_id:
            await redis.sadd(f"synapse:session-ownership:worker:{worker_id}", session_id)
        if isinstance(controller_id, str) and controller_id:
            await redis.sadd(f"synapse:session-ownership:controller:{controller_id}", session_id)

    async def get_session_ownership(self, session_id: str) -> dict[str, Any] | None:
        redis = self._require_redis()
        payload = await redis.get(self._session_ownership_key(session_id))
        return self._decode(payload)

    async def list_session_ownerships(
        self,
        worker_id: str | None = None,
        controller_id: str | None = None,
    ) -> list[dict[str, Any]]:
        redis = self._require_redis()
        if worker_id is not None:
            ids = await redis.smembers(f"synapse:session-ownership:worker:{worker_id}")
        elif controller_id is not None:
            ids = await redis.smembers(f"synapse:session-ownership:controller:{controller_id}")
        else:
            ids = await redis.smembers("synapse:session-ownership:index")
        rows = await self._mget_json([self._session_ownership_key(session_id) for session_id in ids])
        if worker_id is not None:
            rows = [row for row in rows if row.get("worker_id") == worker_id]
        if controller_id is not None:
            rows = [row for row in rows if row.get("controller_id") == controller_id]
        return rows

    async def delete_session_ownership(self, session_id: str) -> None:
        redis = self._require_redis()
        existing = await self.get_session_ownership(session_id)
        await redis.delete(self._session_ownership_key(session_id))
        await redis.srem("synapse:session-ownership:index", session_id)
        if existing is not None:
            if isinstance(existing.get("worker_id"), str):
                await redis.srem(f"synapse:session-ownership:worker:{existing['worker_id']}", session_id)
            if isinstance(existing.get("controller_id"), str):
                await redis.srem(f"synapse:session-ownership:controller:{existing['controller_id']}", session_id)

    async def store_worker_request(self, run_id: str | None, action_id: str, request_data: dict[str, Any]) -> None:
        redis = self._require_redis()
        await redis.set(self._worker_request_key(run_id, action_id), json.dumps(request_data))
        await redis.sadd(self._worker_request_index_key(run_id), action_id)
        worker_id = request_data.get("worker_id")
        session_id = request_data.get("session_id")
        if isinstance(worker_id, str) and worker_id:
            await redis.sadd(f"synapse:worker-requests:worker:{worker_id}", self._worker_request_storage_key(run_id, action_id))
        if isinstance(session_id, str) and session_id:
            await redis.sadd(f"synapse:worker-requests:session:{session_id}", self._worker_request_storage_key(run_id, action_id))
        if isinstance(request_data.get("status"), str):
            await redis.sadd(
                f"synapse:worker-requests:status:{request_data['status']}",
                self._worker_request_storage_key(run_id, action_id),
            )

    async def get_worker_request(self, run_id: str | None, action_id: str) -> dict[str, Any] | None:
        redis = self._require_redis()
        payload = await redis.get(self._worker_request_key(run_id, action_id))
        return self._decode(payload)

    async def list_worker_requests(
        self,
        *,
        run_id: str | None = None,
        worker_id: str | None = None,
        session_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        redis = self._require_redis()
        if worker_id is not None:
            storage_keys = await redis.smembers(f"synapse:worker-requests:worker:{worker_id}")
        elif session_id is not None:
            storage_keys = await redis.smembers(f"synapse:worker-requests:session:{session_id}")
        elif status is not None:
            storage_keys = await redis.smembers(f"synapse:worker-requests:status:{status}")
        elif run_id is not None:
            storage_keys = [self._worker_request_storage_key(run_id, action_id) for action_id in await redis.smembers(self._worker_request_index_key(run_id))]
        else:
            keys = await redis.keys("synapse:worker-requests:*:*")
            storage_keys = [item for item in (self._storage_key_from_worker_request_key(key) for key in keys) if item]
        rows = await self._mget_json([self._worker_request_key_from_storage_key(item) for item in storage_keys])
        if run_id is not None:
            rows = [row for row in rows if row.get("run_id") == run_id]
        if worker_id is not None:
            rows = [row for row in rows if row.get("worker_id") == worker_id]
        if session_id is not None:
            rows = [row for row in rows if row.get("session_id") == session_id]
        if status is not None:
            rows = [row for row in rows if row.get("status") == status]
        rows.sort(key=lambda row: str(row.get("created_at", "")))
        return rows

    async def store_worker_result(self, run_id: str | None, action_id: str, result_data: dict[str, Any]) -> None:
        redis = self._require_redis()
        await redis.set(self._worker_result_key(run_id, action_id), json.dumps(result_data))
        await redis.sadd(self._worker_result_index_key(run_id), action_id)
        worker_id = result_data.get("worker_id")
        session_id = result_data.get("session_id")
        if isinstance(worker_id, str) and worker_id:
            await redis.sadd(f"synapse:worker-results:worker:{worker_id}", self._worker_result_storage_key(run_id, action_id))
        if isinstance(session_id, str) and session_id:
            await redis.sadd(f"synapse:worker-results:session:{session_id}", self._worker_result_storage_key(run_id, action_id))

    async def get_worker_result(self, run_id: str | None, action_id: str) -> dict[str, Any] | None:
        redis = self._require_redis()
        payload = await redis.get(self._worker_result_key(run_id, action_id))
        return self._decode(payload)

    async def list_worker_results(
        self,
        *,
        run_id: str | None = None,
        worker_id: str | None = None,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        redis = self._require_redis()
        if worker_id is not None:
            storage_keys = await redis.smembers(f"synapse:worker-results:worker:{worker_id}")
        elif session_id is not None:
            storage_keys = await redis.smembers(f"synapse:worker-results:session:{session_id}")
        elif run_id is not None:
            storage_keys = [self._worker_result_storage_key(run_id, action_id) for action_id in await redis.smembers(self._worker_result_index_key(run_id))]
        else:
            keys = await redis.keys("synapse:worker-results:*:*")
            storage_keys = [item for item in (self._storage_key_from_worker_result_key(key) for key in keys) if item]
        rows = await self._mget_json([self._worker_result_key_from_storage_key(item) for item in storage_keys])
        if run_id is not None:
            rows = [row for row in rows if row.get("run_id") == run_id]
        if worker_id is not None:
            rows = [row for row in rows if row.get("worker_id") == worker_id]
        if session_id is not None:
            rows = [row for row in rows if row.get("session_id") == session_id]
        rows.sort(key=lambda row: str(row.get("completed_at", "")))
        return rows

    async def store_intervention(self, intervention_id: str, intervention_data: dict[str, Any]) -> None:
        redis = self._require_redis()
        previous = await self.get_intervention(intervention_id)
        await redis.set(self._intervention_key(intervention_id), json.dumps(intervention_data))
        await redis.sadd("synapse:interventions:index", intervention_id)
        if previous is not None:
            if isinstance(previous.get("project_id"), str):
                await redis.srem(f"synapse:interventions:project:{previous['project_id']}", intervention_id)
            if isinstance(previous.get("run_id"), str):
                await redis.srem(f"synapse:interventions:run:{previous['run_id']}", intervention_id)
            if isinstance(previous.get("state"), str):
                await redis.srem(f"synapse:interventions:state:{previous['state']}", intervention_id)
        if isinstance(intervention_data.get("project_id"), str):
            await redis.sadd(f"synapse:interventions:project:{intervention_data['project_id']}", intervention_id)
        if isinstance(intervention_data.get("run_id"), str):
            await redis.sadd(f"synapse:interventions:run:{intervention_data['run_id']}", intervention_id)
        if isinstance(intervention_data.get("state"), str):
            await redis.sadd(f"synapse:interventions:state:{intervention_data['state']}", intervention_id)

    async def get_intervention(self, intervention_id: str) -> dict[str, Any] | None:
        redis = self._require_redis()
        payload = await redis.get(self._intervention_key(intervention_id))
        return self._decode(payload)

    async def list_interventions(
        self,
        project_id: str | None = None,
        run_id: str | None = None,
        state: str | None = None,
    ) -> list[dict[str, Any]]:
        redis = self._require_redis()
        if project_id is not None:
            ids = await redis.smembers(f"synapse:interventions:project:{project_id}")
        elif run_id is not None:
            ids = await redis.smembers(f"synapse:interventions:run:{run_id}")
        elif state is not None:
            ids = await redis.smembers(f"synapse:interventions:state:{state}")
        else:
            ids = await redis.smembers("synapse:interventions:index")
        keys = [self._intervention_key(intervention_id) for intervention_id in ids]
        rows = await self._mget_json(keys)
        if run_id is not None:
            rows = [row for row in rows if row.get("run_id") == run_id]
        if state is not None:
            rows = [row for row in rows if row.get("state") == state]
        rows.sort(key=lambda row: str(row.get("created_at", "")), reverse=True)
        return rows

    async def store_runtime_event(self, event_id: str, event_data: dict[str, Any]) -> None:
        redis = self._require_redis()
        await redis.set(self._event_key(event_id), json.dumps(event_data))
        await redis.lpush("synapse:events:index", event_id)
        await redis.ltrim("synapse:events:index", 0, self._max_events - 1)
        run_id = event_data.get("run_id")
        if isinstance(run_id, str) and run_id:
            await redis.lpush(f"synapse:events:run:{run_id}", event_id)
            await redis.ltrim(f"synapse:events:run:{run_id}", 0, self._max_events - 1)
        agent_id = event_data.get("agent_id")
        if isinstance(agent_id, str) and agent_id:
            await redis.lpush(f"synapse:events:agent:{agent_id}", event_id)
            await redis.ltrim(f"synapse:events:agent:{agent_id}", 0, self._max_events - 1)
        task_id = event_data.get("task_id")
        if isinstance(task_id, str) and task_id:
            await redis.lpush(f"synapse:events:task:{task_id}", event_id)
            await redis.ltrim(f"synapse:events:task:{task_id}", 0, self._max_events - 1)

    async def get_runtime_events(
        self,
        run_id: str | None = None,
        agent_id: str | None = None,
        task_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        redis = self._require_redis()
        if run_id is not None:
            ids = await redis.lrange(f"synapse:events:run:{run_id}", 0, max(0, limit - 1))
        elif agent_id is not None:
            ids = await redis.lrange(f"synapse:events:agent:{agent_id}", 0, max(0, limit - 1))
        elif task_id is not None:
            ids = await redis.lrange(f"synapse:events:task:{task_id}", 0, max(0, limit - 1))
        else:
            ids = await redis.lrange("synapse:events:index", 0, max(0, limit - 1))
        keys = [self._event_key(event_id) for event_id in ids]
        return await self._mget_json(keys)

    async def store_organization(self, organization_id: str, organization_data: dict[str, Any]) -> None:
        redis = self._require_redis()
        await redis.set(self._organization_key(organization_id), json.dumps(organization_data))
        await redis.sadd("synapse:organizations:index", organization_id)

    async def list_organizations(self) -> list[dict[str, Any]]:
        redis = self._require_redis()
        ids = await redis.smembers("synapse:organizations:index")
        return await self._mget_json([self._organization_key(item_id) for item_id in ids])

    async def store_project(self, project_id: str, project_data: dict[str, Any]) -> None:
        redis = self._require_redis()
        await redis.set(self._project_key(project_id), json.dumps(project_data))
        await redis.sadd("synapse:projects:index", project_id)

    async def list_projects(self) -> list[dict[str, Any]]:
        redis = self._require_redis()
        ids = await redis.smembers("synapse:projects:index")
        return await self._mget_json([self._project_key(item_id) for item_id in ids])

    async def store_user(self, user_id: str, user_data: dict[str, Any]) -> None:
        redis = self._require_redis()
        await redis.set(self._user_key(user_id), json.dumps(user_data))
        await redis.sadd("synapse:users:index", user_id)

    async def list_users(self) -> list[dict[str, Any]]:
        redis = self._require_redis()
        ids = await redis.smembers("synapse:users:index")
        return await self._mget_json([self._user_key(item_id) for item_id in ids])

    async def store_api_key(self, api_key_id: str, api_key_data: dict[str, Any]) -> None:
        redis = self._require_redis()
        await redis.set(self._api_key_key(api_key_id), json.dumps(api_key_data))
        await redis.sadd("synapse:api-keys:index", api_key_id)

    async def list_api_keys(self) -> list[dict[str, Any]]:
        redis = self._require_redis()
        ids = await redis.smembers("synapse:api-keys:index")
        return await self._mget_json([self._api_key_key(item_id) for item_id in ids])

    async def store_agent_ownership(self, agent_id: str, ownership_data: dict[str, Any]) -> None:
        redis = self._require_redis()
        await redis.set(self._agent_ownership_key(agent_id), json.dumps(ownership_data))
        await redis.sadd("synapse:agent-ownership:index", agent_id)

    async def get_agent_ownership(self, agent_id: str) -> dict[str, Any] | None:
        redis = self._require_redis()
        payload = await redis.get(self._agent_ownership_key(agent_id))
        return self._decode(payload)

    async def store_audit_log(self, audit_log_id: str, audit_log_data: dict[str, Any]) -> None:
        redis = self._require_redis()
        await redis.set(self._audit_log_key(audit_log_id), json.dumps(audit_log_data))
        await redis.lpush("synapse:audit-logs:index", audit_log_id)
        await redis.ltrim("synapse:audit-logs:index", 0, self._max_events - 1)
        project_id = audit_log_data.get("project_id")
        if isinstance(project_id, str) and project_id:
            await redis.lpush(f"synapse:audit-logs:project:{project_id}", audit_log_id)
            await redis.ltrim(f"synapse:audit-logs:project:{project_id}", 0, self._max_events - 1)

    async def list_audit_logs(self, project_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        redis = self._require_redis()
        if project_id is not None:
            ids = await redis.lrange(f"synapse:audit-logs:project:{project_id}", 0, max(0, limit - 1))
        else:
            ids = await redis.lrange("synapse:audit-logs:index", 0, max(0, limit - 1))
        return await self._mget_json([self._audit_log_key(item_id) for item_id in ids])

    def _require_redis(self) -> Redis:
        if self._redis is None:
            raise RuntimeError("Redis runtime store is not started.")
        return self._redis

    async def _mget_json(self, keys: list[str]) -> list[dict[str, Any]]:
        if not keys:
            return []
        redis = self._require_redis()
        payloads = await redis.mget(keys)
        rows: list[dict[str, Any]] = []
        for payload in payloads:
            decoded = self._decode(payload)
            if decoded is not None:
                rows.append(decoded)
        return rows

    @staticmethod
    def _decode(payload: str | None) -> dict[str, Any] | None:
        if payload is None:
            return None
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            return None
        return decoded if isinstance(decoded, dict) else None

    @staticmethod
    def _agent_key(agent_id: str) -> str:
        return f"synapse:agents:{agent_id}"

    @staticmethod
    def _session_key(session_id: str) -> str:
        return f"synapse:sessions:{session_id}"

    @staticmethod
    def _connection_key(agent_id: str) -> str:
        return f"synapse:connections:{agent_id}"

    @staticmethod
    def _checkpoint_key(checkpoint_id: str) -> str:
        return f"synapse:checkpoints:{checkpoint_id}"

    @staticmethod
    def _profile_key(profile_id: str) -> str:
        return f"synapse:profiles:{profile_id}"

    @staticmethod
    def _run_key(run_id: str) -> str:
        return f"synapse:runs:{run_id}"

    @staticmethod
    def _run_lease_key(run_id: str) -> str:
        return f"synapse:run-leases:{run_id}"

    @staticmethod
    def _worker_key(worker_id: str) -> str:
        return f"synapse:workers:{worker_id}"

    @staticmethod
    def _session_ownership_key(session_id: str) -> str:
        return f"synapse:session-ownership:{session_id}"

    @staticmethod
    def _worker_request_key(run_id: str | None, action_id: str) -> str:
        run_part = run_id or "global"
        return f"synapse:worker-requests:{run_part}:{action_id}"

    @staticmethod
    def _worker_request_index_key(run_id: str | None) -> str:
        run_part = run_id or "global"
        return f"synapse:worker-requests:index:{run_part}"

    @staticmethod
    def _worker_request_storage_key(run_id: str | None, action_id: str) -> str:
        run_part = run_id or "global"
        return f"{run_part}:{action_id}"

    @staticmethod
    def _worker_request_key_from_storage_key(storage_key: str) -> str:
        run_part, _, action_id = storage_key.partition(":")
        return RedisRuntimeStateStore._worker_request_key(None if run_part == "global" else run_part, action_id)

    @staticmethod
    def _storage_key_from_worker_request_key(key: str) -> str:
        prefix = "synapse:worker-requests:"
        storage_key = key.removeprefix(prefix)
        if storage_key.startswith("index:"):
            return ""
        return storage_key

    @staticmethod
    def _worker_result_key(run_id: str | None, action_id: str) -> str:
        run_part = run_id or "global"
        return f"synapse:worker-results:{run_part}:{action_id}"

    @staticmethod
    def _worker_result_index_key(run_id: str | None) -> str:
        run_part = run_id or "global"
        return f"synapse:worker-results:index:{run_part}"

    @staticmethod
    def _worker_result_storage_key(run_id: str | None, action_id: str) -> str:
        run_part = run_id or "global"
        return f"{run_part}:{action_id}"

    @staticmethod
    def _worker_result_key_from_storage_key(storage_key: str) -> str:
        run_part, _, action_id = storage_key.partition(":")
        return RedisRuntimeStateStore._worker_result_key(None if run_part == "global" else run_part, action_id)

    @staticmethod
    def _storage_key_from_worker_result_key(key: str) -> str:
        prefix = "synapse:worker-results:"
        storage_key = key.removeprefix(prefix)
        if storage_key.startswith("index:"):
            return ""
        return storage_key

    @staticmethod
    def _intervention_key(intervention_id: str) -> str:
        return f"synapse:interventions:{intervention_id}"

    @staticmethod
    def _event_key(event_id: str) -> str:
        return f"synapse:events:{event_id}"

    @staticmethod
    def _organization_key(organization_id: str) -> str:
        return f"synapse:organizations:{organization_id}"

    @staticmethod
    def _project_key(project_id: str) -> str:
        return f"synapse:projects:{project_id}"

    @staticmethod
    def _user_key(user_id: str) -> str:
        return f"synapse:users:{user_id}"

    @staticmethod
    def _api_key_key(api_key_id: str) -> str:
        return f"synapse:api-keys:{api_key_id}"

    @staticmethod
    def _agent_ownership_key(agent_id: str) -> str:
        return f"synapse:agent-ownership:{agent_id}"

    @staticmethod
    def _audit_log_key(audit_log_id: str) -> str:
        return f"synapse:audit-logs:{audit_log_id}"


async def create_runtime_state_store() -> RuntimeStateStore:
    if not settings.redis_url:
        logger.info("Redis URL not configured. Using in-memory runtime state store.")
        return InMemoryRuntimeStateStore()

    redis_store = RedisRuntimeStateStore(settings.redis_url)
    try:
        await redis_store.start()
        logger.info("Connected to Redis runtime state store at %s", settings.redis_url)
        return redis_store
    except Exception as exc:  # pragma: no cover - network failures are environment-specific.
        if settings.redis_required:
            raise RuntimeError("Redis runtime state store is required but unavailable.") from exc
        if not settings.runtime_state_fallback_memory:
            raise RuntimeError("Redis runtime state store unavailable and fallback is disabled.") from exc
        logger.warning("Redis runtime store unavailable (%s); falling back to in-memory store.", exc)
        return InMemoryRuntimeStateStore()
