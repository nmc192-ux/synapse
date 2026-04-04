import asyncio

from synapse.runtime.state_store import InMemoryRuntimeStateStore, RedisRuntimeStateStore


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}
        self.lists: dict[str, list[str]] = {}
        self.expirations: dict[str, int] = {}

    async def set(self, key: str, value: str, ex: int | None = None):
        self.values[key] = value
        if ex is not None:
            self.expirations[key] = ex

    async def get(self, key: str):
        return self.values.get(key)

    async def sadd(self, key: str, member: str):
        self.sets.setdefault(key, set()).add(member)

    async def smembers(self, key: str):
        return set(self.sets.get(key, set()))

    async def srem(self, key: str, member: str):
        self.sets.setdefault(key, set()).discard(member)

    async def lpush(self, key: str, member: str):
        self.lists.setdefault(key, []).insert(0, member)

    async def ltrim(self, key: str, start: int, end: int):
        values = self.lists.get(key, [])
        self.lists[key] = values[start : end + 1]

    async def lrange(self, key: str, start: int, end: int):
        values = self.lists.get(key, [])
        if end == -1:
            return values[start:]
        return values[start : end + 1]

    async def lrem(self, key: str, count: int, member: str):
        values = self.lists.get(key, [])
        if count == 0:
            self.lists[key] = [value for value in values if value != member]
            return
        removed = 0
        kept: list[str] = []
        for value in values:
            if value == member and removed < abs(count):
                removed += 1
                continue
            kept.append(value)
        self.lists[key] = kept

    async def expire(self, key: str, ttl: int):
        self.expirations[key] = ttl

    async def mget(self, keys: list[str]):
        return [self.values.get(key) for key in keys]

    async def delete(self, key: str):
        self.values.pop(key, None)

    async def keys(self, pattern: str):
        if pattern == "synapse:worker-requests:*:*":
            return [key for key in self.values if key.startswith("synapse:worker-requests:")]
        if pattern == "synapse:worker-results:*:*":
            return [key for key in self.values if key.startswith("synapse:worker-results:")]
        return []


def test_runtime_state_store_crud() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()

        await store.register_agent({"agent_id": "a1", "agent": {"agent_id": "a1"}})
        assert await store.get_agent("a1") is not None
        assert len(await store.list_agents()) == 1

        await store.store_session("s1", {"session_id": "s1", "agent_id": "a1", "current_url": "https://example.com"})
        assert (await store.get_session("s1"))["current_url"] == "https://example.com"
        assert len(await store.list_sessions(agent_id="a1")) == 1
        await store.delete_session("s1")
        assert await store.get_session("s1") is None

        await store.store_connection("a1", {"agent_id": "a1", "status": "active"})
        assert (await store.get_connection("a1"))["status"] == "active"
        await store.delete_connection("a1")
        assert await store.get_connection("a1") is None

        await store.store_checkpoint("c1", {"checkpoint_id": "c1", "agent_id": "a1", "task_id": "t1"})
        assert (await store.get_checkpoint("c1"))["task_id"] == "t1"
        assert len(await store.list_checkpoints(agent_id="a1")) == 1
        await store.delete_checkpoint("c1")
        assert await store.get_checkpoint("c1") is None

        await store.store_run("r1", {"run_id": "r1", "agent_id": "a1", "task_id": "t1", "status": "running"})
        assert (await store.get_run("r1"))["status"] == "running"
        assert len(await store.list_runs(agent_id="a1")) == 1

        await store.store_runtime_event("e1", {"event_id": "e1", "run_id": "r1", "agent_id": "a1", "task_id": "t1"})
        events = await store.get_runtime_events(run_id="r1", agent_id="a1")
        assert len(events) == 1
        assert events[0]["event_id"] == "e1"

    asyncio.run(scenario())


def test_redis_runtime_state_store_applies_ttl_to_transient_payloads() -> None:
    async def scenario() -> None:
        store = RedisRuntimeStateStore("redis://example.test/0")
        fake = _FakeRedis()
        store._redis = fake  # type: ignore[assignment]

        await store.store_runtime_event("e1", {"event_id": "e1", "run_id": "r1"})
        await store.store_worker_request("r1", "a1", {"run_id": "r1", "action_id": "a1", "worker_id": "w1", "status": "running"})
        await store.store_worker_result("r1", "a1", {"run_id": "r1", "action_id": "a1", "worker_id": "w1"})
        await store.store_run("r1", {"run_id": "r1", "agent_id": "agent-1", "task_id": "task-1"})
        await store.store_session_ownership("s1", {"session_id": "s1", "worker_id": "w1", "controller_id": "c1"})
        await store.store_run_lease("r1", {"run_id": "r1", "worker_id": "w1", "token": 1})
        await store.store_audit_log("log-1", {"audit_log_id": "log-1", "project_id": "project-1"})

        assert fake.expirations["synapse:events:e1"] > 0
        assert fake.expirations["synapse:worker-requests:r1:a1"] > 0
        assert fake.expirations["synapse:worker-results:r1:a1"] > 0
        assert fake.expirations["synapse:runs:r1"] > 0
        assert fake.expirations["synapse:session-ownership:s1"] > 0
        assert fake.expirations["synapse:run-leases:r1"] > 0
        assert fake.expirations["synapse:audit-logs:log-1"] > 0
        assert fake.expirations["synapse:events:index"] > 0
        assert fake.expirations["synapse:worker-requests:index:r1"] > 0
        assert fake.expirations["synapse:worker-results:index:r1"] > 0

    asyncio.run(scenario())


def test_redis_runtime_state_store_prunes_missing_worker_request_index_members() -> None:
    async def scenario() -> None:
        store = RedisRuntimeStateStore("redis://example.test/0")
        fake = _FakeRedis()
        store._redis = fake  # type: ignore[assignment]

        await fake.sadd("synapse:worker-requests:index:r1", "a1")
        rows = await store.list_worker_requests(run_id="r1")

        assert rows == []
        assert "a1" not in fake.sets["synapse:worker-requests:index:r1"]

    asyncio.run(scenario())


def test_redis_runtime_state_store_prunes_missing_runtime_event_payloads() -> None:
    async def scenario() -> None:
        store = RedisRuntimeStateStore("redis://example.test/0")
        fake = _FakeRedis()
        store._redis = fake  # type: ignore[assignment]

        await fake.lpush("synapse:events:index", "e1")
        events = await store.get_runtime_events(limit=10)

        assert events == []
        assert "e1" not in fake.lists["synapse:events:index"]

    asyncio.run(scenario())
