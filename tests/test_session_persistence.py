import asyncio
from datetime import datetime, timezone

from synapse.models.runtime_state import BrowserSessionState
from synapse.runtime.state_store import InMemoryRuntimeStateStore


def test_session_metadata_persistence() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        state = BrowserSessionState(
            session_id="session-1",
            agent_id="agent-1",
            current_url="https://example.com",
            cookies=[{"name": "sid", "value": "abc"}],
            last_active_at=datetime.now(timezone.utc),
            page_title="Example",
            tabs=[{"index": 0, "url": "https://example.com", "title": "Example"}],
        )
        await store.store_session(state.session_id, state.model_dump(mode="json"))
        restored = await store.get_session("session-1")
        assert restored is not None
        assert restored["current_url"] == "https://example.com"
        assert restored["cookies"][0]["name"] == "sid"
        sessions = await store.list_sessions(agent_id="agent-1")
        assert len(sessions) == 1

    asyncio.run(scenario())
