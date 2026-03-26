import asyncio
from datetime import datetime, timezone
from contextlib import suppress

from synapse.models.browser import BrowserState, StructuredPageModel
from synapse.models.runtime_event import EventType
from synapse.models.runtime_state import (
    BrowserSessionOwnershipRecord,
    BrowserSessionState,
    BrowserTaskRequestRecord,
    BrowserTaskResultRecord,
    BrowserWorkerState,
    RunLeaseRecord,
    WorkerHealthStatus,
    WorkerRuntimeStatus,
)
from synapse.runtime.event_bus import EventBus
from synapse.runtime.browser_workers import BrowserWorkerPool
from synapse.runtime.queues import BrowserTaskEnvelope, BrowserTaskResult
from synapse.runtime.run_store import RunStore
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
            worker_count=2,
            heartbeat_interval_seconds=0.05,
            runtime_factory=runtime_factory,
        )
        bus = EventBus(sockets)
        bus.set_context_resolver(lambda event: _event_context())
        pool.set_event_publisher(bus.publish)
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


def test_browser_worker_pool_blocks_projectless_status_events_but_emits_run_scoped_events() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        sockets = WebSocketManager(state_store=store)
        bus = EventBus(sockets)
        bus.set_context_resolver(lambda event: _event_context())

        def runtime_factory() -> _FakeBrowserRuntime:
            return _FakeBrowserRuntime(worker_name="worker-1")

        pool = BrowserWorkerPool(
            state_store=store,
            worker_count=1,
            heartbeat_interval_seconds=0.01,
            runtime_factory=runtime_factory,
        )
        pool.set_event_publisher(bus.publish)

        async with sockets.subscribe("browser-worker-test") as queue:
            await pool.start()
            await pool.create_session("s1", agent_id="agent-1", run_id="run-1")
            await pool.open("s1", "https://example.com")
            events = []
            while EventType.BROWSER_TASK_COMPLETED not in {event.event_type for event in events}:
                events.append(await queue.get())
            await pool.stop()

        event_types = {event.event_type for event in events}
        assert EventType.BROWSER_TASK_DISPATCHED in event_types
        assert EventType.BROWSER_TASK_COMPLETED in event_types
        assert pool.list_workers() == []

    asyncio.run(scenario())


async def _event_context() -> dict[str, object]:
    return {"organization_id": "org-1", "project_id": "project-1"}


def test_browser_worker_pool_lists_sessions_from_workers() -> None:
    async def scenario() -> None:
        pool = BrowserWorkerPool(
            state_store=InMemoryRuntimeStateStore(),
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


def test_browser_worker_pool_renews_durable_leases_on_heartbeat() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        run_store = RunStore(store)
        pool = BrowserWorkerPool(
            state_store=store,
            worker_count=1,
            heartbeat_interval_seconds=0.01,
            runtime_factory=lambda: _FakeBrowserRuntime(worker_name="worker-1"),
            run_store=run_store,
            lease_timeout_seconds=0.05,
            controller_id="controller-heartbeat",
        )
        run_id = "run-1"
        worker_id = "controller-heartbeat:browser-worker-1"
        await run_store.save_lease(
            RunLeaseRecord(
                run_id=run_id,
                worker_id=worker_id,
                acquired_at=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc),
                token=1,
            )
        )
        await pool.start()
        try:
            await asyncio.sleep(0.03)
            lease = await run_store.get_lease(run_id)
            assert lease is not None
            assert lease.expires_at > datetime.now(timezone.utc)
        finally:
            await pool.stop()

    asyncio.run(scenario())


def test_browser_worker_pool_recovers_persisted_result_after_restart() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        run_store = RunStore(store)
        pool = BrowserWorkerPool(
            state_store=store,
            worker_count=1,
            runtime_factory=lambda: _FakeBrowserRuntime(worker_name="worker-1"),
            run_store=run_store,
            controller_id="controller-replay",
        )
        worker_id = "controller-replay:browser-worker-1"
        await run_store.save_worker_result(
            BrowserTaskResultRecord(
                action_id="action-1",
                run_id="run-1",
                worker_id=worker_id,
                action="get_layout",
                success=True,
                payload={"restored": True},
                fencing_token=3,
            )
        )

        await pool.start()
        try:
            payload = await pool._dispatch(
                worker_id,
                BrowserTaskEnvelope(action_id="action-1", run_id="run-1", action="get_layout"),
            )
            assert payload == {"restored": True}
        finally:
            await pool.stop()

    asyncio.run(scenario())


def test_browser_worker_pool_recovers_session_ownership_after_restart() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        run_store = RunStore(store)
        controller_id = "controller-a"
        await run_store.save_session_ownership(
            BrowserSessionOwnershipRecord(
                session_id="s1",
                worker_id=f"{controller_id}:browser-worker-1",
                controller_id=controller_id,
                run_id="run-1",
                current_url="https://example.com/recovered",
            )
        )

        pool = BrowserWorkerPool(
            state_store=store,
            worker_count=1,
            runtime_factory=lambda: _FakeBrowserRuntime(worker_name="worker-1"),
            run_store=run_store,
            controller_id=controller_id,
        )
        await pool.start()
        try:
            assert pool.current_url("s1") == "https://example.com/recovered"
            assert pool._session_workers["s1"] == f"{controller_id}:browser-worker-1"
        finally:
            await pool.stop()

    asyncio.run(scenario())


def test_browser_worker_pool_marks_stale_session_ownership() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        run_store = RunStore(store)
        sockets = WebSocketManager(state_store=store)
        bus = EventBus(sockets)
        bus.set_context_resolver(lambda event: _event_context())
        await run_store.save_session_ownership(
            BrowserSessionOwnershipRecord(
                session_id="s-stale",
                worker_id="foreign-worker",
                controller_id="foreign-controller",
                run_id="run-1",
            )
        )
        await run_store.save_worker(
            BrowserWorkerState(
                worker_id="foreign-worker",
                queue_name="q-foreign",
                controller_id="foreign-controller",
                health_status=WorkerHealthStatus.STALE,
            )
        )

        pool = BrowserWorkerPool(
            state_store=store,
            worker_count=1,
            runtime_factory=lambda: _FakeBrowserRuntime(worker_name="worker-1"),
            run_store=run_store,
            controller_id="controller-a",
        )
        pool.set_event_publisher(bus.publish)
        await pool.start()
        try:
            async with sockets.subscribe("stale-ownership") as queue:
                try:
                    await pool.open("s-stale", "https://example.com")
                except KeyError:
                    pass
                else:
                    raise AssertionError("expected stale ownership to block dispatch")
                event = await asyncio.wait_for(queue.get(), timeout=0.2)
                assert event.event_type == EventType.WORKER_OWNERSHIP_STALE
            stale = await run_store.get_session_ownership("s-stale")
            assert stale is not None
            assert stale.status == "stale"
        finally:
            await pool.stop()

    asyncio.run(scenario())


def test_browser_worker_pool_recovers_outstanding_request_after_controller_restart() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        run_store = RunStore(store)
        sockets = WebSocketManager(state_store=store)
        bus = EventBus(sockets)
        bus.set_context_resolver(lambda event: _event_context())
        controller_id = "controller-a"
        await run_store.save_worker_request(
            BrowserTaskRequestRecord(
                action_id="action-1",
                request_id="request-1",
                run_id="run-1",
                worker_id=f"{controller_id}:browser-worker-1",
                action="open",
                session_id="s1",
                status="dispatched",
                payload={"url": "https://example.com"},
            )
        )
        await run_store.save_session_ownership(
            BrowserSessionOwnershipRecord(
                session_id="s1",
                worker_id=f"{controller_id}:browser-worker-1",
                controller_id=controller_id,
                run_id="run-1",
            )
        )

        pool = BrowserWorkerPool(
            state_store=store,
            worker_count=1,
            runtime_factory=lambda: _FakeBrowserRuntime(worker_name="worker-1"),
            run_store=run_store,
            controller_id=controller_id,
        )
        pool.set_event_publisher(bus.publish)
        async with sockets.subscribe("recovery") as queue:
            await pool.start()
            try:
                events = []
                while {
                    EventType.WORKER_REQUEST_RECOVERED,
                    EventType.RUN_DISPATCH_RECONCILED,
                } - {event.event_type for event in events}:
                    events.append(await asyncio.wait_for(queue.get(), timeout=0.2))
            finally:
                await pool.stop()

        event_types = {event.event_type for event in events}
        assert EventType.WORKER_REQUEST_RECOVERED in event_types
        assert EventType.RUN_DISPATCH_RECONCILED in event_types
        request = await run_store.get_worker_request("run-1", "action-1")
        assert request is not None
        assert request.status == "recovered"

    asyncio.run(scenario())


def test_browser_worker_pool_replays_duplicate_result_idempotently() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        run_store = RunStore(store)
        sockets = WebSocketManager(state_store=store)
        bus = EventBus(sockets)
        bus.set_context_resolver(lambda event: _event_context())
        pool = BrowserWorkerPool(
            state_store=store,
            worker_count=1,
            runtime_factory=lambda: _FakeBrowserRuntime(worker_name="worker-1"),
            run_store=run_store,
            controller_id="controller-dup",
        )
        pool.set_event_publisher(bus.publish)
        worker_id = "controller-dup:browser-worker-1"
        await run_store.save_lease(
            RunLeaseRecord(
                run_id="run-1",
                worker_id=worker_id,
                acquired_at=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc),
                token=2,
            )
        )
        await pool.start()
        try:
            result = BrowserTaskResult(
                action_id="action-dup",
                request_id="request-dup",
                run_id="run-1",
                worker_id=worker_id,
                action="open",
                session_id="s1",
                success=True,
                payload={"ok": True},
                fencing_token=2,
            )
            async with sockets.subscribe("dup-result") as queue:
                await pool._handle_result(result)
                await pool._handle_result(result)
                replay = await asyncio.wait_for(queue.get(), timeout=0.2)
                assert replay.event_type == EventType.WORKER_RESULT_REPLAYED
            stored = await run_store.list_worker_results(run_id="run-1")
            assert len(stored) == 1
            assert stored[0].payload == {"ok": True}
        finally:
            await pool.stop()

    asyncio.run(scenario())


def test_browser_worker_pool_rejects_stale_fencing_result() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        run_store = RunStore(store)
        pool = BrowserWorkerPool(
            state_store=store,
            worker_count=1,
            runtime_factory=lambda: _FakeBrowserRuntime(worker_name="worker-1"),
            run_store=run_store,
            controller_id="controller-stale-token",
        )
        worker_id = "controller-stale-token:browser-worker-1"
        await run_store.save_lease(
            RunLeaseRecord(
                run_id="run-1",
                worker_id=worker_id,
                acquired_at=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc),
                token=2,
            )
        )
        await pool.start()
        try:
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            pool._pending["action-stale"] = future

            await pool._handle_result(
                BrowserTaskResult(
                    action_id="action-stale",
                    run_id="run-1",
                    worker_id=worker_id,
                    action="open",
                    success=True,
                    payload={"stale": True},
                    fencing_token=1,
                )
            )

            assert not future.done()
            assert await run_store.get_worker_result("run-1", "action-stale") is None
        finally:
            pending = pool._pending.pop("action-stale", None)
            if pending is not None:
                pending.cancel()
                with suppress(asyncio.CancelledError):
                    await pending
            await pool.stop()

    asyncio.run(scenario())
