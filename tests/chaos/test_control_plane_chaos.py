from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from synapse.models.runtime_event import EventType
from synapse.models.runtime_state import (
    BrowserSessionOwnershipRecord,
    BrowserTaskRequestRecord,
    BrowserTaskResultRecord,
    RunLeaseRecord,
)
from synapse.runtime.browser_workers import BrowserWorkerPool
from synapse.runtime.event_bus import EventBus
from synapse.runtime.run_store import RunStore
from synapse.runtime.scheduler import RunScheduler
from synapse.runtime.state_store import InMemoryRuntimeStateStore
from synapse.transports.websocket_manager import WebSocketManager
from tests.chaos.helpers import ChaosScenarioReport, collect_events, event_types
from tests.test_browser_workers import _FakeBrowserRuntime
from tests.test_scheduler import _StubWorkerPool, _event_context
from synapse.models.runtime_state import BrowserWorkerState, WorkerHealthStatus, WorkerRuntimeStatus


class _FlakyLeaseStore(InMemoryRuntimeStateStore):
    def __init__(self) -> None:
        super().__init__()
        self._fail_once = True

    async def acquire_run_lease(self, run_id: str, lease_data: dict[str, object]) -> dict[str, object]:
        if self._fail_once:
            self._fail_once = False
            raise ConnectionError("temporary redis outage")
        return await super().acquire_run_lease(run_id, lease_data)


def test_redis_unavailability_during_lease_acquire_fails_closed() -> None:
    async def scenario() -> None:
        store = _FlakyLeaseStore()
        run_store = RunStore(store)
        bus = EventBus(WebSocketManager(state_store=store))
        bus.set_context_resolver(lambda event: _event_context())
        scheduler = RunScheduler(
            run_store,
            _StubWorkerPool(
                [
                    BrowserWorkerState(
                        worker_id="controller-a:browser-worker-1",
                        queue_name="q1",
                        status=WorkerRuntimeStatus.IDLE,
                        health_status=WorkerHealthStatus.HEALTHY,
                        controller_id="controller-a",
                    )
                ]
            ),
            bus,
            cleanup_interval_seconds=60,
        )
        run = await run_store.create_run(task_id="task-chaos-redis", agent_id="agent-1")

        try:
            await scheduler.assign_run(run.run_id)
        except ConnectionError:
            pass
        else:
            raise AssertionError("expected temporary lease store failure")

        assert await run_store.get_lease(run.run_id) is None
        persisted = await run_store.get(run.run_id)
        report = ChaosScenarioReport(
            scenario="redis-temporary-unavailability",
            severity="high",
            failure_mode="lease acquisition unavailable",
            safe=True,
            recovered=False,
            manual_intervention_required=False,
            expected_behavior="run must fail closed without persisting duplicate ownership",
            evidence={"run_status": persisted.status.value, "lease_present": False},
        )
        assert report.as_dict()["safe"] is True

    asyncio.run(scenario())


def test_controller_restart_recovers_outstanding_dispatch_and_preserves_single_ownership() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        run_store = RunStore(store)
        sockets = WebSocketManager(state_store=store)
        bus = EventBus(sockets)
        bus.set_context_resolver(lambda event: _event_context())
        controller_id = "controller-chaos"

        await run_store.save_worker_request(
            BrowserTaskRequestRecord(
                action_id="action-1",
                request_id="request-1",
                run_id="run-1",
                worker_id=f"{controller_id}:browser-worker-1",
                action="open",
                session_id="s1",
                status="running",
                payload={"url": "https://example.com"},
            )
        )
        await run_store.save_session_ownership(
            BrowserSessionOwnershipRecord(
                session_id="s1",
                worker_id=f"{controller_id}:browser-worker-1",
                controller_id=controller_id,
                run_id="run-1",
                current_url="https://example.com",
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

        async with sockets.subscribe("chaos-recovery", organization_id="org-1", project_id="project-1") as queue:
            await pool.start()
            try:
                events = []
                while {
                    EventType.WORKER_REQUEST_RECOVERED,
                    EventType.RUN_DISPATCH_RECONCILED,
                } - {event.event_type for event in events}:
                    events.append(await asyncio.wait_for(queue.get(), timeout=0.3))
            finally:
                await pool.stop()

        types = event_types(events)
        assert EventType.WORKER_REQUEST_RECOVERED.value in types
        assert EventType.RUN_DISPATCH_RECONCILED.value in types
        ownerships = await run_store.list_session_ownerships(controller_id=controller_id)
        assert len(ownerships) == 1
        request = await run_store.get_worker_request("run-1", "action-1")
        assert request is not None
        assert request.status == "recovered"

    asyncio.run(scenario())


def test_worker_crash_mid_command_recovers_request_without_duplicate_result() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        run_store = RunStore(store)
        sockets = WebSocketManager(state_store=store)
        bus = EventBus(sockets)
        bus.set_context_resolver(lambda event: _event_context())
        controller_id = "controller-worker-crash"
        worker_id = f"{controller_id}:browser-worker-1"

        await run_store.save_worker_request(
            BrowserTaskRequestRecord(
                action_id="action-crash",
                request_id="request-crash",
                run_id="run-crash",
                worker_id=worker_id,
                action="click",
                session_id="s-crash",
                status="running",
                payload={"selector": "#submit"},
            )
        )
        await run_store.save_session_ownership(
            BrowserSessionOwnershipRecord(
                session_id="s-crash",
                worker_id=worker_id,
                controller_id=controller_id,
                run_id="run-crash",
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
        async with sockets.subscribe("worker-crash", organization_id="org-1", project_id="project-1") as queue:
            await pool.start()
            try:
                events = await collect_events(queue, 2)
            finally:
                await pool.stop()
        assert EventType.WORKER_REQUEST_RECOVERED.value in event_types(events)
        assert await run_store.get_worker_result("run-crash", "action-crash") is None

        report = ChaosScenarioReport(
            scenario="worker-crash-mid-browser-action",
            severity="high",
            failure_mode="worker exited before result persisted",
            safe=True,
            recovered=True,
            manual_intervention_required=True,
            expected_behavior="outstanding request is surfaced and reconciled without silent continuation",
            evidence={"events": sorted(event_types(events))},
        )
        assert report.as_dict()["manual_intervention_required"] is True

    asyncio.run(scenario())


def test_duplicate_result_delivery_is_idempotent_under_chaos_replay() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        run_store = RunStore(store)
        sockets = WebSocketManager(state_store=store)
        bus = EventBus(sockets)
        bus.set_context_resolver(lambda event: _event_context())
        controller_id = "controller-dup-chaos"
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
                run_id="run-dup",
                worker_id=worker_id,
                token=4,
                acquired_at=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc),
            )
        )
        await pool.start()
        try:
            from synapse.runtime.queues import BrowserTaskResult

            result = BrowserTaskResult(
                action_id="action-dup",
                request_id="request-dup",
                run_id="run-dup",
                worker_id=worker_id,
                action="open",
                session_id="s-dup",
                success=True,
                payload={"ok": True},
                fencing_token=4,
            )
            async with sockets.subscribe("dup-chaos", organization_id="org-1", project_id="project-1") as queue:
                await pool._handle_result(result)
                await pool._handle_result(result)
                replay = await asyncio.wait_for(queue.get(), timeout=0.2)
                assert replay.event_type == EventType.WORKER_RESULT_REPLAYED
            stored = await run_store.list_worker_results(run_id="run-dup")
            assert len(stored) == 1
            assert stored[0].payload == {"ok": True}
        finally:
            await pool.stop()

    asyncio.run(scenario())


def test_stale_session_ownership_conflict_is_blocked_and_marked_stale() -> None:
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
                run_id="run-stale",
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
            controller_id="controller-local",
        )
        pool.set_event_publisher(bus.publish)
        await pool.start()
        try:
            async with sockets.subscribe("stale-chaos", organization_id="org-1", project_id="project-1") as queue:
                try:
                    await pool.open("s-stale", "https://example.com")
                except KeyError:
                    pass
                else:
                    raise AssertionError("expected stale ownership conflict")
                event = await asyncio.wait_for(queue.get(), timeout=0.2)
                assert event.event_type == EventType.WORKER_OWNERSHIP_STALE
            ownership = await run_store.get_session_ownership("s-stale")
            assert ownership is not None
            assert ownership.status == "stale"
        finally:
            await pool.stop()

    asyncio.run(scenario())


def test_multi_controller_assignment_simulation_keeps_single_run_owner() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        run_store = RunStore(store)
        bus = EventBus(WebSocketManager(state_store=store))
        bus.set_context_resolver(lambda event: _event_context())
        registered = [
            BrowserWorkerState(
                worker_id="controller-a:browser-worker-1",
                queue_name="qa",
                controller_id="controller-a",
                health_status=WorkerHealthStatus.HEALTHY,
                status=WorkerRuntimeStatus.IDLE,
            )
        ]
        scheduler_a = RunScheduler(run_store, _StubWorkerPool([], registered=registered), bus, cleanup_interval_seconds=60)
        scheduler_b = RunScheduler(run_store, _StubWorkerPool([], registered=registered), bus, cleanup_interval_seconds=60)
        run = await run_store.create_run(task_id="task-race-loop", agent_id="agent-1")

        leases = await asyncio.gather(
            *[scheduler_a.assign_run(run.run_id), scheduler_b.assign_run(run.run_id)] * 5
        )
        worker_ids = {lease.worker_id for lease in leases}
        tokens = {lease.token for lease in leases}
        assert worker_ids == {"controller-a:browser-worker-1"}
        assert len(tokens) == 1
        persisted = await run_store.get_lease(run.run_id)
        assert persisted is not None
        assert persisted.worker_id == "controller-a:browser-worker-1"

    asyncio.run(scenario())
