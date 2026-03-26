import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from synapse.models.agent import AgentDefinition, AgentKind
from synapse.models.run import RunStatus
from synapse.models.runtime_event import EventType
from synapse.models.runtime_state import BrowserWorkerState, WorkerRuntimeStatus
from synapse.models.task import TaskRequest, TaskResult, TaskStatus
from synapse.runtime.budget import AgentBudgetManager
from synapse.runtime.checkpoint_service import CheckpointService
from synapse.runtime.event_bus import EventBus
from synapse.runtime.memory_service import MemoryService
from synapse.runtime.registry import AgentRegistry
from synapse.runtime.run_store import RunStore
from synapse.runtime.scheduler import RunLease, RunScheduler
from synapse.runtime.state_store import InMemoryRuntimeStateStore
from synapse.runtime.task_runtime import TaskRuntime
from synapse.transports.websocket_manager import WebSocketManager


class _StubWorkerPool:
    def __init__(self, workers: list[BrowserWorkerState]) -> None:
        self._workers = workers

    def list_workers(self) -> list[BrowserWorkerState]:
        return [worker.model_copy(deep=True) for worker in self._workers]


class _StubBrowserService:
    def __init__(self) -> None:
        self.browser = object()
        self.sandbox = SimpleNamespace(set_run_policy=lambda run_id, policy: None)
        self.budget_service = SimpleNamespace(
            budget_manager=AgentBudgetManager(),
            ensure_run_budget=self._ensure_run_budget,
        )
        self.created_sessions: list[dict[str, object]] = []
        self.saved_sessions: list[dict[str, object]] = []

    async def _ensure_run_budget(self, agent_id: str, run_id: str | None = None) -> None:
        return None

    async def create_session(
        self,
        session_id: str,
        agent_id: str | None = None,
        run_id: str | None = None,
        worker_id: str | None = None,
    ):
        self.created_sessions.append(
            {"session_id": session_id, "agent_id": agent_id, "run_id": run_id, "worker_id": worker_id}
        )
        return SimpleNamespace(session_id=session_id)

    async def save_session_state(
        self,
        session_id: str,
        agent_id: str | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        self.saved_sessions.append({"session_id": session_id, "agent_id": agent_id, "task_id": task_id, "run_id": run_id})


class _StubToolService:
    async def call_tool(self, tool_name: str, arguments: dict[str, object], agent_id: str | None = None, run_id: str | None = None):
        return {"ok": True}


class _StubTaskManager:
    async def create_task(self, request):
        return request

    async def claim_task(self, task_id, request):
        return request

    async def update_task(self, task_id, request):
        return request

    async def list_active_tasks(self):
        return []


class _StubSafety:
    def validate_task(self, request):
        return None


class _StubMemoryManager:
    async def store(self, request):
        return request

    async def search(self, request):
        return []

    async def get_recent(self, agent_id: str, limit: int = 10):
        return []

    async def get_recent_by_type(self, agent_id: str, limit_per_type: int = 4):
        return {}


class _StubAdapter:
    async def execute_task(self, request: TaskRequest) -> TaskResult:
        return TaskResult(task_id=request.task_id, run_id=request.run_id, status=TaskStatus.COMPLETED, message="ok")


def test_scheduler_assigns_run_to_available_worker() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        run_store = RunStore(store)
        bus = EventBus(WebSocketManager(state_store=store))
        scheduler = RunScheduler(
            run_store,
            _StubWorkerPool(
                [
                    BrowserWorkerState(worker_id="worker-1", queue_name="q1", status=WorkerRuntimeStatus.IDLE, active_sessions=2),
                    BrowserWorkerState(worker_id="worker-2", queue_name="q2", status=WorkerRuntimeStatus.IDLE, active_sessions=0),
                ]
            ),
            bus,
            cleanup_interval_seconds=60,
        )
        run = await run_store.create_run(task_id="task-1", agent_id="agent-1")

        async with bus.subscribe("scheduler-test") as queue:
            lease = await scheduler.assign_run(run.run_id)
            event = await queue.get()
            assert event.event_type == EventType.RUN_ASSIGNED
            assert lease.worker_id == "worker-2"

        persisted = await run_store.get(run.run_id)
        assert persisted.metadata["assigned_worker_id"] == "worker-2"

    asyncio.run(scenario())


def test_scheduler_requeues_when_no_workers_available() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        run_store = RunStore(store)
        bus = EventBus(WebSocketManager(state_store=store))
        scheduler = RunScheduler(run_store, _StubWorkerPool([]), bus, cleanup_interval_seconds=60)
        run = await run_store.create_run(task_id="task-1", agent_id="agent-1")

        async with bus.subscribe("scheduler-test") as queue:
            with pytest.raises(RuntimeError):
                await scheduler.assign_run(run.run_id)
            event_types = { (await queue.get()).event_type, (await queue.get()).event_type }
            assert EventType.WORKER_UNAVAILABLE in event_types
            assert EventType.RUN_REQUEUED in event_types

        persisted = await run_store.get(run.run_id)
        assert persisted.status == RunStatus.PENDING

    asyncio.run(scenario())


def test_scheduler_requeues_expired_leases() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        run_store = RunStore(store)
        bus = EventBus(WebSocketManager(state_store=store))
        scheduler = RunScheduler(
            run_store,
            _StubWorkerPool([BrowserWorkerState(worker_id="worker-1", queue_name="q1", status=WorkerRuntimeStatus.IDLE)]),
            bus,
            cleanup_interval_seconds=60,
        )
        run = await run_store.create_run(task_id="task-1", agent_id="agent-1")
        lease = RunLease(
            run_id=run.run_id,
            worker_id="worker-1",
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        scheduler._leases[run.run_id] = lease
        await run_store.update_metadata(run.run_id, {"assigned_worker_id": "worker-1", "assignment_attempts": 1})

        await scheduler.cleanup_expired_leases()

        persisted = await run_store.get(run.run_id)
        assert persisted.metadata["assigned_worker_id"] == "worker-1"
        assert persisted.metadata["assignment_attempts"] >= 2

    asyncio.run(scenario())


def test_task_runtime_uses_scheduler_assignment_for_session_creation() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        agents = AgentRegistry(state_store=store)
        agent = agents.register(AgentDefinition(agent_id="agent-1", kind=AgentKind.CUSTOM, name="Agent One"))
        await agents.save_to_store(agent)
        agents.build_adapter = lambda *args, **kwargs: _StubAdapter()  # type: ignore[method-assign]

        browser_service = _StubBrowserService()
        events = EventBus(WebSocketManager(state_store=store))
        checkpoint_service = CheckpointService(store, browser_service, events)
        run_store = RunStore(store)
        scheduler = RunScheduler(
            run_store,
            _StubWorkerPool([BrowserWorkerState(worker_id="worker-7", queue_name="q7", status=WorkerRuntimeStatus.IDLE)]),
            events,
            cleanup_interval_seconds=60,
        )
        runtime = TaskRuntime(
            agents=agents,
            browser_service=browser_service,
            tool_service=_StubToolService(),
            memory_service=MemoryService(_StubMemoryManager()),
            task_manager=_StubTaskManager(),
            checkpoint_service=checkpoint_service,
            run_store=run_store,
            events=events,
            safety=_StubSafety(),
            llm=None,
            scheduler=scheduler,
        )

        result = await runtime.execute_task(TaskRequest(task_id="task-1", agent_id="agent-1", goal="Do work"))
        assert result.run_id is not None
        assert browser_service.created_sessions[0]["worker_id"] == "worker-7"

    asyncio.run(scenario())
