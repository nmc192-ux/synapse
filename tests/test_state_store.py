import asyncio

from synapse.runtime.state_store import InMemoryRuntimeStateStore


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

        await store.store_runtime_event("e1", {"event_id": "e1", "agent_id": "a1", "task_id": "t1"})
        events = await store.get_runtime_events(agent_id="a1")
        assert len(events) == 1
        assert events[0]["event_id"] == "e1"

    asyncio.run(scenario())
