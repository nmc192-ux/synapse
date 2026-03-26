import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from synapse.api.routes import get_authenticator, get_orchestrator, router
from synapse.config import Settings
from synapse.models.agent import AgentDefinition, AgentKind
from synapse.models.runtime_state import RuntimeCheckpoint
from synapse.models.task import TaskResult, TaskStatus
from synapse.runtime.messaging import AgentMessageBus
from synapse.runtime.orchestrator import RuntimeOrchestrator
from synapse.runtime.registry import AgentRegistry
from synapse.runtime.state_store import InMemoryRuntimeStateStore
from synapse.runtime.tools import ToolRegistry
from synapse.security.auth import Authenticator
from synapse.transports.websocket_manager import WebSocketManager


class _StubBrowser:
    async def create_session(self, session_id: str, agent_id: str | None = None, run_id: str | None = None):
        return type("Session", (), {"session_id": session_id})()

    async def restore_session_state(self, session_id: str):
        return None

    async def save_session_state(self, session_id: str, run_id: str | None = None):
        return None


class _StubA2A:
    def __init__(self) -> None:
        self._executor = None

    def set_task_executor(self, executor):
        self._executor = executor


class _StubMemory:
    async def store(self, request):
        return request

    async def search(self, request):
        return []

    async def get_recent(self, agent_id: str, limit: int = 10):
        return []


class _StubTaskManager:
    async def create_task(self, request):
        return request

    async def claim_task(self, task_id, request):
        return request

    async def update_task(self, task_id, request):
        return request

    async def list_active_tasks(self):
        return []


class _StubSandbox:
    def authorize_domain(self, agent_id, url):
        return None

    def consume_browser_action(self, agent_id):
        return None

    def authorize_tool(self, agent_id, tool_name):
        return None

    def consume_tool_call(self, agent_id):
        return None


class _StubSafety:
    def inspect_page(self, page, action):
        return None

    def validate_task(self, request):
        return None

    def validate_tool_call(self, tool_name, arguments):
        return None


class _StubBudget:
    def get_or_create(self, agent):
        return None

    def get_usage(self, agent_id):
        from synapse.models.agent import AgentBudgetUsage

        return AgentBudgetUsage()

    def increment_page(self, agent):
        return self.get_usage(agent.agent_id)

    def increment_tool_call(self, agent):
        return self.get_usage(agent.agent_id)

    def increment_memory_write(self, agent):
        return self.get_usage(agent.agent_id)

    def check_limits(self, agent):
        return self.get_usage(agent.agent_id), []

    def save_checkpoint(self, agent_id, state, reason=None):
        return {"agent_id": agent_id, "state": state, "reason": reason}


async def _build_orchestrator() -> RuntimeOrchestrator:
    store = InMemoryRuntimeStateStore()
    agents = AgentRegistry(state_store=store)
    agent = agents.register(AgentDefinition(agent_id="agent-1", kind=AgentKind.CUSTOM, name="Agent One"))
    await agents.save_to_store(agent)

    orchestrator = RuntimeOrchestrator(
        browser=_StubBrowser(),
        agents=agents,
        tools=ToolRegistry(),
        messages=AgentMessageBus(),
        a2a=_StubA2A(),
        memory_manager=_StubMemory(),
        task_manager=_StubTaskManager(),
        sockets=WebSocketManager(state_store=store),
        sandbox=_StubSandbox(),
        safety=_StubSafety(),
        budget_manager=_StubBudget(),
        state_store=store,
        llm=None,
    )
    return orchestrator


def test_checkpoint_save_and_resume() -> None:
    async def scenario() -> None:
        orchestrator = await _build_orchestrator()
        checkpoint = await orchestrator.save_checkpoint(
            "task-1",
            {
                "agent_id": "agent-1",
                "current_goal": "Resume task",
                "browser_session_reference": "session-1",
                "pending_actions": [{"type": "screenshot"}],
            },
        )
        assert isinstance(checkpoint, RuntimeCheckpoint)

        async def fake_execute_task(request):
            return TaskResult(task_id=request.task_id, status=TaskStatus.COMPLETED, message="resumed", artifacts={})

        orchestrator.execute_task = fake_execute_task  # type: ignore[method-assign]
        result = await orchestrator.resume_task(checkpoint.checkpoint_id)
        assert result.status == TaskStatus.COMPLETED

    asyncio.run(scenario())


def test_checkpoint_api_endpoints() -> None:
    async def scenario() -> tuple[RuntimeOrchestrator, RuntimeCheckpoint]:
        orchestrator = await _build_orchestrator()
        checkpoint = await orchestrator.save_checkpoint(
            "task-2",
            {
                "agent_id": "agent-1",
                "current_goal": "Seed",
                "browser_session_reference": "session-2",
            },
        )
        return orchestrator, checkpoint

    orchestrator, checkpoint = asyncio.run(scenario())
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_orchestrator] = lambda: orchestrator
    app.dependency_overrides[get_authenticator] = lambda: Authenticator(Settings(auth_required=False))
    client = TestClient(app)
    list_response = client.get("/api/checkpoints")
    assert list_response.status_code == 200
    assert len(list_response.json()) >= 1

    get_response = client.get(f"/api/checkpoints/{checkpoint.checkpoint_id}")
    assert get_response.status_code == 200
    assert get_response.json()["checkpoint_id"] == checkpoint.checkpoint_id

    agents_response = client.get("/api/agents")
    assert agents_response.status_code == 200
    assert any(agent["agent_id"] == "agent-1" for agent in agents_response.json())
