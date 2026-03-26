import asyncio
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from synapse.api.routes import get_authenticator, get_orchestrator, router
from synapse.config import Settings
from synapse.models.agent import AgentDefinition, AgentKind
from synapse.models.run import RunStatus
from synapse.models.runtime_event import EventType
from synapse.models.task import TaskRequest, TaskResult, TaskStatus
from synapse.runtime.budget import AgentBudgetManager
from synapse.runtime.checkpoint_service import CheckpointService
from synapse.runtime.event_bus import EventBus
from synapse.runtime.memory_service import MemoryService
from synapse.runtime.orchestrator import RuntimeOrchestrator
from synapse.runtime.registry import AgentRegistry
from synapse.runtime.run_store import RunStore
from synapse.runtime.state_store import InMemoryRuntimeStateStore
from synapse.runtime.task_runtime import TaskRuntime
from synapse.security.auth import Authenticator
from synapse.transports.websocket_manager import WebSocketManager


class _StubBrowserService:
    def __init__(self) -> None:
        self.browser = object()
        self.sandbox = object()
        self.budget_service = SimpleNamespace(budget_manager=AgentBudgetManager())
        self.saved_sessions: list[dict[str, object]] = []

    async def create_session(self, session_id: str, agent_id: str | None = None, run_id: str | None = None):
        return SimpleNamespace(session_id=session_id)

    async def save_session_state(
        self,
        session_id: str,
        agent_id: str | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        self.saved_sessions.append({"session_id": session_id, "agent_id": agent_id, "task_id": task_id, "run_id": run_id})

    async def restore_session_state(self, session_id: str, agent_id: str | None = None, checkpoint_id: str | None = None, run_id: str | None = None):
        return None


class _StubToolService:
    async def call_tool(self, tool_name: str, arguments: dict[str, object], agent_id: str | None = None) -> dict[str, object]:
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
        return TaskResult(
            task_id=request.task_id,
            run_id=request.run_id,
            status=TaskStatus.COMPLETED,
            message="done",
            artifacts={"echo_run_id": request.run_id},
        )


def test_run_store_crud() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        run_store = RunStore(store)
        run = await run_store.create_run(task_id="task-1", agent_id="agent-1", correlation_id="task-1")
        assert run.status == RunStatus.RUNNING
        fetched = await run_store.get(run.run_id)
        assert fetched.task_id == "task-1"
        runs = await run_store.list(agent_id="agent-1")
        assert len(runs) == 1

    asyncio.run(scenario())


def test_task_runtime_creates_and_persists_run_state() -> None:
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
        )

        request = TaskRequest(task_id="task-1", agent_id="agent-1", goal="Do work")
        result = await runtime.execute_task(request)

        assert result.run_id is not None
        persisted = await run_store.get(result.run_id)
        assert persisted.status == RunStatus.COMPLETED
        assert browser_service.saved_sessions[0]["run_id"] == result.run_id
        events_for_run = await store.get_runtime_events(run_id=result.run_id)
        assert any(event["run_id"] == result.run_id for event in events_for_run)

    asyncio.run(scenario())


def test_run_api_endpoints() -> None:
    async def scenario() -> RuntimeOrchestrator:
        store = InMemoryRuntimeStateStore()
        agents = AgentRegistry(state_store=store)
        agent = agents.register(AgentDefinition(agent_id="agent-1", kind=AgentKind.CUSTOM, name="Agent One"))
        await agents.save_to_store(agent)
        orchestrator = RuntimeOrchestrator(
            browser=_StubBrowserService(),
            agents=agents,
            tools=SimpleNamespace(),
            messages=SimpleNamespace(),
            a2a=SimpleNamespace(),
            memory_manager=_StubMemoryManager(),
            task_manager=_StubTaskManager(),
            sockets=WebSocketManager(state_store=store),
            sandbox=SimpleNamespace(),
            safety=_StubSafety(),
            budget_manager=AgentBudgetManager(),
            state_store=store,
            llm=None,
        )
        run = await orchestrator.run_store.create_run(task_id="task-2", agent_id="agent-1", correlation_id="task-2")
        await orchestrator.event_bus.emit(EventType.TASK_UPDATED, run_id=run.run_id, agent_id="agent-1", task_id="task-2", payload={"ok": True})
        return orchestrator

    orchestrator = asyncio.run(scenario())
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_orchestrator] = lambda: orchestrator
    app.dependency_overrides[get_authenticator] = lambda: Authenticator(Settings(auth_required=False))
    client = TestClient(app)

    list_response = client.get("/api/runs")
    assert list_response.status_code == 200
    run_id = list_response.json()[0]["run_id"]

    get_response = client.get(f"/api/runs/{run_id}")
    assert get_response.status_code == 200

    events_response = client.get(f"/api/runs/{run_id}/events")
    assert events_response.status_code == 200
    assert any(event["run_id"] == run_id for event in events_response.json())

    timeline_response = client.get(f"/api/runs/{run_id}/timeline")
    assert timeline_response.status_code == 200
    assert timeline_response.json()["run_id"] == run_id
    assert timeline_response.json()["event_count"] >= 1
    assert "task" in timeline_response.json()["phases"]

    replay_response = client.get(f"/api/runs/{run_id}/replay")
    assert replay_response.status_code == 200
    assert replay_response.json()["run_id"] == run_id
    assert len(replay_response.json()["timeline"]) >= 1

    cancel_response = client.post(f"/api/runs/{run_id}/cancel")
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "cancelled"


def test_run_timeline_orders_events_and_groups_replay() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        run_store = RunStore(store)
        run = await run_store.create_run(task_id="task-1", agent_id="agent-1", correlation_id="task-1")

        await store.store_runtime_event(
            "evt-2",
            {
                "event_id": "evt-2",
                "run_id": run.run_id,
                "event_type": "loop.acted",
                "timestamp": "2026-03-26T10:00:02+00:00",
                "phase": "act",
                "payload": {"action": "click"},
                "correlation_id": "task-1",
                "severity": "info",
                "source": "agent_loop",
            },
        )
        await store.store_runtime_event(
            "evt-1",
            {
                "event_id": "evt-1",
                "run_id": run.run_id,
                "event_type": "loop.planned",
                "timestamp": "2026-03-26T10:00:01+00:00",
                "phase": "plan",
                "payload": {"actions": [{"type": "click"}]},
                "correlation_id": "task-1",
                "severity": "info",
                "source": "agent_loop",
            },
        )
        await store.store_runtime_event(
            "evt-3",
            {
                "event_id": "evt-3",
                "run_id": run.run_id,
                "event_type": "budget.updated",
                "timestamp": "2026-03-26T10:00:03+00:00",
                "phase": "evaluate",
                "payload": {"usage": {"steps_used": 1}},
                "correlation_id": "task-1",
                "severity": "info",
                "source": "budget_service",
            },
        )

        timeline = await run_store.get_timeline(run.run_id)
        replay = await run_store.get_replay(run.run_id, checkpoints=[{"checkpoint_id": "cp-1"}])

        assert [entry.event_id for entry in timeline.entries] == ["evt-1", "evt-2", "evt-3"]
        assert timeline.phases == ["plan", "act", "evaluate"]
        assert replay.phase_transitions[0]["phase"] == "plan"
        assert replay.browser_actions[0]["event_id"] == "evt-2"
        assert replay.planner_outputs[0]["event_id"] == "evt-1"
        assert replay.budget_updates[0]["event_id"] == "evt-3"
        assert replay.checkpoints == [{"checkpoint_id": "cp-1"}]

    asyncio.run(scenario())
