import asyncio

from synapse.models.browser import BrowserState, StructuredPageModel
from synapse.models.runtime_event import EventType
from synapse.models.runtime_state import BrowserSessionState, WorkerRuntimeStatus
from synapse.runtime.browser_workers import BrowserWorkerPool
from synapse.runtime.session import BrowserSession
from synapse.runtime.state_store import InMemoryRuntimeStateStore
from synapse.transports.websocket_manager import WebSocketManager


class _FakeBrowserRuntime:
    def __init__(self, worker_name: str) -> None:
        self.worker_name = worker_name
        self.started = False
        self.sessions: dict[str, str | None] = {}

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    def set_state_store(self, state_store) -> None:
        self.state_store = state_store

    async def create_session(self, session_id: str, agent_id: str | None = None, run_id: str | None = None) -> BrowserSession:
        self.sessions[session_id] = None
        return BrowserSession(session_id=session_id, current_url=None, page=StructuredPageModel(title="Blank", url="about:blank"))

    async def open(self, session_id: str, url: str) -> BrowserState:
        self.sessions[session_id] = url
        return BrowserState(
            session_id=session_id,
            page=StructuredPageModel(title=f"Page {self.worker_name}", url=url),
            metadata={"worker_name": self.worker_name},
        )

    async def navigate(self, session_id: str, url: str) -> BrowserSession:
        self.sessions[session_id] = url
        return BrowserSession(session_id=session_id, current_url=url, page=StructuredPageModel(title="Navigate", url=url))

    async def list_sessions(self, agent_id: str | None = None) -> list[BrowserSessionState]:
        return [
            BrowserSessionState(session_id=session_id, agent_id=agent_id, current_url=url)
            for session_id, url in self.sessions.items()
        ]


def test_browser_worker_pool_dispatches_and_preserves_session_affinity() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        sockets = WebSocketManager(state_store=store)
        runtimes: list[_FakeBrowserRuntime] = []

        def runtime_factory() -> _FakeBrowserRuntime:
            runtime = _FakeBrowserRuntime(worker_name=f"worker-{len(runtimes) + 1}")
            runtimes.append(runtime)
            return runtime

        pool = BrowserWorkerPool(
            state_store=store,
            sockets=sockets,
            worker_count=2,
            heartbeat_interval_seconds=0.05,
            runtime_factory=runtime_factory,
        )
        await pool.start()

        try:
            await pool.create_session("s1", agent_id="agent-1", run_id="run-1")
            state = await pool.open("s1", "https://example.com")
            assert state.page.url == "https://example.com"
            first_worker = pool._session_workers["s1"]

            await pool.open("s1", "https://example.com/docs")
            assert pool._session_workers["s1"] == first_worker
            assert pool.current_url("s1") == "https://example.com/docs"

            worker_states = pool.list_workers()
            assigned = next(item for item in worker_states if item.worker_id == first_worker)
            assert assigned.active_sessions == 1
        finally:
            await pool.stop()

    asyncio.run(scenario())


def test_browser_worker_pool_emits_status_and_heartbeat_events() -> None:
    async def scenario() -> None:
        sockets = WebSocketManager(state_store=InMemoryRuntimeStateStore())

        def runtime_factory() -> _FakeBrowserRuntime:
            return _FakeBrowserRuntime(worker_name="worker-1")

        pool = BrowserWorkerPool(
            state_store=InMemoryRuntimeStateStore(),
            sockets=sockets,
            worker_count=1,
            heartbeat_interval_seconds=0.01,
            runtime_factory=runtime_factory,
        )

        async with sockets.subscribe("browser-worker-test") as queue:
            await pool.start()
            events = []
            while len(events) < 2:
                events.append(await queue.get())
            await pool.stop()

        event_types = {event.event_type for event in events}
        assert EventType.BROWSER_WORKER_STATUS_UPDATED in event_types
        assert EventType.BROWSER_WORKER_HEARTBEAT in event_types
        assert pool.list_workers() == []

    asyncio.run(scenario())


def test_browser_worker_pool_lists_sessions_from_workers() -> None:
    async def scenario() -> None:
        pool = BrowserWorkerPool(
            state_store=InMemoryRuntimeStateStore(),
            sockets=WebSocketManager(state_store=InMemoryRuntimeStateStore()),
            worker_count=1,
            runtime_factory=lambda: _FakeBrowserRuntime(worker_name="worker-1"),
        )
        await pool.start()
        try:
            await pool.create_session("s1", agent_id="agent-1")
            await pool.open("s1", "https://example.com")
            sessions = await pool.list_sessions(agent_id="agent-1")
            assert len(sessions) == 1
            assert sessions[0].session_id == "s1"
            assert sessions[0].current_url == "https://example.com"
        finally:
            await pool.stop()

    asyncio.run(scenario())
