from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
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


class InMemoryRuntimeStateStore(RuntimeStateStore):
    def __init__(self) -> None:
        self._agents: dict[str, dict[str, Any]] = {}
        self._sessions: dict[str, dict[str, Any]] = {}
        self._connections: dict[str, dict[str, Any]] = {}
        self._checkpoints: dict[str, dict[str, Any]] = {}
        self._profiles: dict[str, dict[str, Any]] = {}
        self._runs: dict[str, dict[str, Any]] = {}
        self._events: dict[str, dict[str, Any]] = {}
        self._event_ids: list[str] = []

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
    def _event_key(event_id: str) -> str:
        return f"synapse:events:{event_id}"


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
