import asyncio
from datetime import datetime, timedelta, timezone
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


class _SlowBrowserRuntime(_FakeBrowserRuntime):
    def __init__(self, worker_name: str, *, delay_seconds: float) -> None:
        super().__init__(worker_name)
        self.delay_seconds = delay_seconds

    async def open(self, session_id: str, url: str) -> BrowserState:
        await asyncio.sleep(self.delay_seconds)
        return await super().open(session_id, url)


class _SlowCreateSessionRuntime(_FakeBrowserRuntime):
    def __init__(self, worker_name: str, *, delay_seconds: float) -> None:
        super().__init__(worker_name)
        self.delay_seconds = delay_seconds

    async def create_session(self, session_id: str, agent_id: str | None = None, run_id: str | None = None) -> BrowserSession:
        await asyncio.sleep(self.delay_seconds)
        return await super().create_session(session_id, agent_id=agent_id, run_id=run_id)


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
        await run_store.save_worker_request(
            BrowserTaskRequestRecord(
                action_id="action-stale-ownership",
                request_id="request-stale-ownership",
                run_id="run-1",
                worker_id="foreign-worker",
                action="open",
                session_id="s-stale",
                status="running",
                payload={"session_id": "s-stale", "url": "https://example.com"},
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
                assert event.payload["reason_code"] == "foreign_controller"
            stale = await run_store.get_session_ownership("s-stale")
            assert stale is not None
            assert stale.status == "stale"
            assert stale.status_reason == "session is owned by a different controller"
            request = await run_store.get_worker_request("run-1", "action-stale-ownership")
            assert request is not None
            assert request.status == "stuck"
            assert request.status_reason == "session ownership conflict blocked continued dispatch: session is owned by a different controller"
        finally:
            await pool.stop()

    asyncio.run(scenario())


def test_browser_worker_pool_does_not_reemit_already_stale_ownership() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        run_store = RunStore(store)
        sockets = WebSocketManager(state_store=store)
        bus = EventBus(sockets)
        bus.set_context_resolver(lambda event: _event_context())
        await run_store.save_session_ownership(
            BrowserSessionOwnershipRecord(
                session_id="s-stale-repeat",
                worker_id="foreign-worker",
                controller_id="foreign-controller",
                run_id="run-repeat",
                status="stale",
                status_reason="session is owned by a different controller",
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
            async with sockets.subscribe("stale-repeat") as queue:
                try:
                    await pool.open("s-stale-repeat", "https://example.com")
                except KeyError:
                    pass
                else:
                    raise AssertionError("expected stale ownership to block dispatch")
                with suppress(asyncio.TimeoutError):
                    event = await asyncio.wait_for(queue.get(), timeout=0.1)
                    raise AssertionError(f"expected no repeated stale event, got {event.event_type}")
        finally:
            await pool.stop()

    asyncio.run(scenario())


def test_browser_worker_pool_classifies_missing_worker_ownership_reason() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        run_store = RunStore(store)
        sockets = WebSocketManager(state_store=store)
        bus = EventBus(sockets)
        bus.set_context_resolver(lambda event: _event_context())
        await run_store.save_session_ownership(
            BrowserSessionOwnershipRecord(
                session_id="s-missing",
                worker_id="missing-worker",
                controller_id="controller-z",
                run_id="run-2",
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
            async with sockets.subscribe("missing-ownership") as queue:
                try:
                    await pool.open("s-missing", "https://example.com/missing")
                except KeyError:
                    pass
                else:
                    raise AssertionError("expected missing ownership to block dispatch")
                event = await asyncio.wait_for(queue.get(), timeout=0.2)
                assert event.event_type == EventType.WORKER_OWNERSHIP_STALE
                assert event.payload["reason_code"] == "worker_missing"
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
            request = await run_store.get_worker_request("run-1", "action-stale")
            assert request is None
        finally:
            pending = pool._pending.pop("action-stale", None)
            if pending is not None:
                pending.cancel()
                with suppress(asyncio.CancelledError):
                    await pending
            await pool.stop()

    asyncio.run(scenario())


def test_browser_worker_pool_marks_stale_fencing_request_failed_when_record_exists() -> None:
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
            controller_id="controller-stale-token-record",
        )
        pool.set_event_publisher(bus.publish)
        worker_id = "controller-stale-token-record:browser-worker-1"
        await run_store.save_lease(
            RunLeaseRecord(
                run_id="run-1",
                worker_id=worker_id,
                acquired_at=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc),
                token=2,
            )
        )
        await run_store.save_worker_request(
            BrowserTaskRequestRecord(
                action_id="action-stale-record",
                request_id="request-stale-record",
                run_id="run-1",
                worker_id=worker_id,
                action="open",
                session_id="s1",
                status="stuck",
                fencing_token=1,
                status_reason="request exceeded 0.10s without a durable result",
            )
        )
        await pool.start()
        try:
            async with sockets.subscribe("stale-result", organization_id="org-1", project_id="project-1") as queue:
                await pool._handle_result(
                    BrowserTaskResult(
                        action_id="action-stale-record",
                        request_id="request-stale-record",
                        run_id="run-1",
                        worker_id=worker_id,
                        action="open",
                        session_id="s1",
                        success=True,
                        payload={"stale": True},
                        fencing_token=1,
                    )
                )
                event = await asyncio.wait_for(queue.get(), timeout=0.2)
                assert event.event_type == EventType.WORKER_UNAVAILABLE
                assert event.payload["reason_code"] == "stale_fencing_result"
        finally:
            await pool.stop()

        request = await run_store.get_worker_request("run-1", "action-stale-record")
        assert request is not None
        assert request.status == "abandoned"
        assert request.status_reason == "late worker result rejected because lease ownership changed before durable completion"

    asyncio.run(scenario())


def test_browser_worker_pool_abandons_stuck_request_when_lease_moves() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        run_store = RunStore(store)
        sockets = WebSocketManager(state_store=store)
        bus = EventBus(sockets)
        bus.set_context_resolver(lambda event: _event_context())
        controller_id = "controller-lease-moved"
        worker_id = f"{controller_id}:browser-worker-1"
        pool = BrowserWorkerPool(
            state_store=store,
            worker_count=1,
            runtime_factory=lambda: _FakeBrowserRuntime(worker_name="worker-1"),
            run_store=run_store,
            controller_id=controller_id,
        )
        pool.set_event_publisher(bus.publish)
        await run_store.save_lease(
            RunLeaseRecord(
                run_id="run-1",
                worker_id="controller-remote:browser-worker-1",
                acquired_at=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
                token=4,
            )
        )
        request = BrowserTaskRequestRecord(
            action_id="action-lease-moved",
            request_id="request-lease-moved",
            run_id="run-1",
            worker_id=worker_id,
            action="open",
            session_id="s1",
            status="stuck",
            status_reason="request exceeded 0.10s without a durable result",
        )
        await run_store.save_worker_request(request)

        await pool.start()
        try:
            async with sockets.subscribe("lease-moved", organization_id="org-1", project_id="project-1") as queue:
                updated = await pool._maybe_finalize_abandoned_request(request)
                assert updated is not None
                assert updated.status == "abandoned"
                assert updated.status_reason == "durable result wait ended after lease ownership moved to controller-remote:browser-worker-1"
                event = await asyncio.wait_for(queue.get(), timeout=0.2)
                assert event.event_type == EventType.WORKER_UNAVAILABLE
                assert event.payload["reason_code"] == "lease_moved"
        finally:
            await pool.stop()

        stored = await run_store.get_worker_request("run-1", "action-lease-moved")
        assert stored is not None
        assert stored.status == "abandoned"

    asyncio.run(scenario())


def test_browser_worker_pool_rejects_remote_run_assignment_for_local_dispatch() -> None:
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
            controller_id="controller-local",
        )
        pool.set_event_publisher(bus.publish)
        await run_store.save_lease(
            RunLeaseRecord(
                run_id="run-remote",
                worker_id="controller-remote:browser-worker-1",
                token=3,
                acquired_at=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
            )
        )
        await pool.start()
        try:
            async with sockets.subscribe("remote-worker", organization_id="org-1", project_id="project-1") as queue:
                try:
                    await pool.call_tool("web.search", {"query": "synapse"}, run_id="run-remote")
                except RuntimeError as exc:
                    assert "cannot be dispatched locally" in str(exc)
                else:
                    raise AssertionError("expected local dispatch to reject remote assigned worker")
                event = await asyncio.wait_for(queue.get(), timeout=0.2)
                assert event.event_type == EventType.WORKER_UNAVAILABLE
                assert event.payload["reason_code"] == "remote_worker"
        finally:
            await pool.stop()

    asyncio.run(scenario())


def test_browser_worker_pool_marks_long_running_request_slow_but_completes_with_progress() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        run_store = RunStore(store)
        sockets = WebSocketManager(state_store=store)
        bus = EventBus(sockets)
        bus.set_context_resolver(lambda event: _event_context())
        controller_id = "controller-slow"
        pool = BrowserWorkerPool(
            state_store=store,
            worker_count=1,
            heartbeat_interval_seconds=0.01,
            lease_timeout_seconds=0.1,
            runtime_factory=lambda: _SlowBrowserRuntime(worker_name="worker-1", delay_seconds=0.07),
            run_store=run_store,
            controller_id=controller_id,
        )
        pool.set_event_publisher(bus.publish)

        await pool.start()
        try:
            await pool.create_session("s1", agent_id="agent-1", run_id="run-1")
            async with sockets.subscribe("slow-request", organization_id="org-1", project_id="project-1") as queue:
                state = await pool.open("s1", "https://example.com/slow")
                assert state.page.url == "https://example.com/slow"
                events = await asyncio.wait_for(_collect_browser_events(queue, expected=4), timeout=0.5)
        finally:
            await pool.stop()

        event_types = [event.event_type for event in events]
        assert EventType.WORKER_REQUEST_RUNNING in event_types
        assert EventType.WORKER_REQUEST_SLOW in event_types
        assert EventType.WORKER_REQUEST_STUCK not in event_types

        requests = await run_store.list_worker_requests(run_id="run-1", session_id="s1")
        request = next(item for item in requests if item.action == "open")
        assert request is not None
        assert request.started_at is not None
        assert request.last_progress_at is not None
        assert request.completed_at is not None
        assert request.status == "completed"

    asyncio.run(scenario())


def test_browser_worker_pool_waits_past_lease_timeout_for_slow_create_session() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        run_store = RunStore(store)
        sockets = WebSocketManager(state_store=store)
        bus = EventBus(sockets)
        bus.set_context_resolver(lambda event: _event_context())
        controller_id = "controller-slow-create"
        pool = BrowserWorkerPool(
            state_store=store,
            worker_count=1,
            heartbeat_interval_seconds=0.01,
            lease_timeout_seconds=0.05,
            durable_result_timeout_seconds=0.2,
            runtime_factory=lambda: _SlowCreateSessionRuntime(worker_name="worker-1", delay_seconds=0.12),
            run_store=run_store,
            controller_id=controller_id,
        )
        pool.set_event_publisher(bus.publish)

        await pool.start()
        try:
            async with sockets.subscribe("slow-create-session", organization_id="org-1", project_id="project-1") as queue:
                session = await pool.create_session("s1", agent_id="agent-1", run_id="run-1")
                assert session.session_id == "s1"
                events = await asyncio.wait_for(_collect_browser_events(queue, expected=4), timeout=0.6)
        finally:
            await pool.stop()

        event_types = [event.event_type for event in events]
        assert EventType.WORKER_REQUEST_SLOW in event_types
        request = await run_store.list_worker_requests(run_id="run-1", session_id="s1")
        create_request = next(item for item in request if item.action == "create_session")
        assert create_request.status == "completed"
        assert create_request.status_reason in {
            "completed after slow progress gap",
            "completed after stalled progress gap",
        }

    asyncio.run(scenario())


def test_browser_worker_pool_marks_request_stuck_when_progress_stalls() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        run_store = RunStore(store)
        sockets = WebSocketManager(state_store=store)
        bus = EventBus(sockets)
        bus.set_context_resolver(lambda event: _event_context())
        controller_id = "controller-stuck"
        worker_id = f"{controller_id}:browser-worker-1"
        pool = BrowserWorkerPool(
            state_store=store,
            worker_count=1,
            heartbeat_interval_seconds=0.2,
            lease_timeout_seconds=0.05,
            runtime_factory=lambda: _FakeBrowserRuntime(worker_name="worker-1"),
            run_store=run_store,
            controller_id=controller_id,
        )
        pool.set_event_publisher(bus.publish)
        stale_time = datetime.now(timezone.utc) - timedelta(seconds=1)
        await run_store.save_lease(
            RunLeaseRecord(
                run_id="run-1",
                worker_id=worker_id,
                token=2,
                acquired_at=stale_time,
                expires_at=stale_time,
            )
        )
        request = BrowserTaskRequestRecord(
            action_id="action-stuck",
            request_id="request-stuck",
            run_id="run-1",
            worker_id=worker_id,
            action="open",
            session_id="s1",
            status="running",
            payload={"session_id": "s1", "url": "https://example.com/stuck"},
            dispatched_at=stale_time,
            started_at=stale_time,
            last_progress_at=stale_time,
        )
        await run_store.save_worker_request(request)

        await pool.start()
        try:
            async with sockets.subscribe("stuck-request", organization_id="org-1", project_id="project-1") as queue:
                await pool._maybe_mark_request_stuck(request)
                event = await asyncio.wait_for(queue.get(), timeout=0.2)
                assert event.event_type == EventType.WORKER_REQUEST_STUCK
                await pool._handle_result(
                    BrowserTaskResult(
                        action_id="action-stuck",
                        request_id="request-stuck",
                        run_id="run-1",
                        worker_id=worker_id,
                        action="open",
                        session_id="s1",
                        success=True,
                        payload={"ok": True},
                        fencing_token=2,
                    )
                )
        finally:
            await pool.stop()

        stored = await run_store.get_worker_request("run-1", "action-stuck")
        assert stored is not None
        assert stored.completed_at is not None
        assert stored.status == "completed"

    asyncio.run(scenario())


def test_browser_worker_pool_progress_recovers_slow_request() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        run_store = RunStore(store)
        sockets = WebSocketManager(state_store=store)
        bus = EventBus(sockets)
        bus.set_context_resolver(lambda event: _event_context())
        controller_id = "controller-progress-slow"
        worker_id = f"{controller_id}:browser-worker-1"
        pool = BrowserWorkerPool(
            state_store=store,
            worker_count=1,
            heartbeat_interval_seconds=0.2,
            lease_timeout_seconds=0.3,
            runtime_factory=lambda: _FakeBrowserRuntime(worker_name="worker-1"),
            run_store=run_store,
            controller_id=controller_id,
        )
        pool.set_event_publisher(bus.publish)
        await pool.start()
        try:
            now = datetime.now(timezone.utc)
            await run_store.save_worker_request(
                BrowserTaskRequestRecord(
                    action_id="action-slow",
                    request_id="request-slow",
                    run_id="run-1",
                    worker_id=worker_id,
                    action="open",
                    session_id="s1",
                    status="slow",
                    status_reason="request exceeded 0.10s without a durable result",
                    payload={"session_id": "s1", "url": "https://example.com/slow"},
                    dispatched_at=now - timedelta(seconds=0.3),
                    started_at=now - timedelta(seconds=0.25),
                    last_progress_at=now - timedelta(seconds=0.2),
                )
            )
            async with sockets.subscribe("slow-recovered", organization_id="org-1", project_id="project-1") as queue:
                await pool._on_request_progress(
                    worker_id,
                    BrowserTaskEnvelope(
                        action_id="action-slow",
                        request_id="request-slow",
                        run_id="run-1",
                        session_id="s1",
                        action="open",
                    ),
                )
                event = await asyncio.wait_for(queue.get(), timeout=0.2)
                assert event.event_type == EventType.WORKER_REQUEST_RUNNING
                assert event.payload["previous_status"] == "slow"
                assert event.payload["resumed"] is True
        finally:
            await pool.stop()

        stored = await run_store.get_worker_request("run-1", "action-slow")
        assert stored is not None
        assert stored.status == "running"
        assert stored.status_reason == "request resumed after slow progress gap"

    asyncio.run(scenario())


def test_browser_worker_pool_progress_recovers_stuck_request_and_preserves_completion_reason() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        run_store = RunStore(store)
        sockets = WebSocketManager(state_store=store)
        bus = EventBus(sockets)
        bus.set_context_resolver(lambda event: _event_context())
        controller_id = "controller-progress-stuck"
        worker_id = f"{controller_id}:browser-worker-1"
        pool = BrowserWorkerPool(
            state_store=store,
            worker_count=1,
            heartbeat_interval_seconds=0.2,
            lease_timeout_seconds=0.05,
            runtime_factory=lambda: _FakeBrowserRuntime(worker_name="worker-1"),
            run_store=run_store,
            controller_id=controller_id,
        )
        pool.set_event_publisher(bus.publish)
        await pool.start()
        try:
            stale_time = datetime.now(timezone.utc) - timedelta(seconds=1)
            await run_store.save_lease(
                RunLeaseRecord(
                    run_id="run-1",
                    worker_id=worker_id,
                    token=2,
                    acquired_at=stale_time,
                    expires_at=datetime.now(timezone.utc) + timedelta(seconds=5),
                )
            )
            await run_store.save_worker_request(
                BrowserTaskRequestRecord(
                    action_id="action-stuck-progress",
                    request_id="request-stuck-progress",
                    run_id="run-1",
                    worker_id=worker_id,
                    action="open",
                    session_id="s1",
                    status="stuck",
                    status_reason="request exceeded 0.05s without a durable result",
                    payload={"session_id": "s1", "url": "https://example.com/stuck"},
                    dispatched_at=stale_time,
                    started_at=stale_time,
                    last_progress_at=stale_time,
                )
            )
            async with sockets.subscribe("stuck-recovered", organization_id="org-1", project_id="project-1") as queue:
                await pool._on_request_progress(
                    worker_id,
                    BrowserTaskEnvelope(
                        action_id="action-stuck-progress",
                        request_id="request-stuck-progress",
                        run_id="run-1",
                        session_id="s1",
                        action="open",
                    ),
                )
                event = await asyncio.wait_for(queue.get(), timeout=0.2)
                assert event.event_type == EventType.WORKER_REQUEST_RECOVERED
                assert event.payload["previous_status"] == "stuck"

            await pool._handle_result(
                BrowserTaskResult(
                    action_id="action-stuck-progress",
                    request_id="request-stuck-progress",
                    run_id="run-1",
                    worker_id=worker_id,
                    action="open",
                    session_id="s1",
                    success=True,
                    payload={"ok": True},
                    fencing_token=2,
                )
            )
        finally:
            await pool.stop()

        stored = await run_store.get_worker_request("run-1", "action-stuck-progress")
        assert stored is not None
        assert stored.status == "completed"
        assert stored.status_reason == "completed after controller recovery delay"

    asyncio.run(scenario())


async def _collect_browser_events(queue: asyncio.Queue, *, expected: int) -> list:
    events = []
    seen = set()
    while len(seen) < expected:
        event = await queue.get()
        if event.event_type not in {
            EventType.BROWSER_TASK_DISPATCHED,
            EventType.WORKER_REQUEST_RUNNING,
            EventType.WORKER_REQUEST_SLOW,
            EventType.WORKER_REQUEST_STUCK,
            EventType.BROWSER_TASK_COMPLETED,
        }:
            continue
        events.append(event)
        seen.add(event.event_type)
        if EventType.BROWSER_TASK_COMPLETED in seen and len(seen) >= expected:
            break
    return events
