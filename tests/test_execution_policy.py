import asyncio

import pytest

from synapse.models.a2a import AgentRegistrationRequest
from synapse.models.a2a import A2AMessageType, A2AEnvelope
from synapse.models.agent import AgentDefinition, AgentKind
from synapse.models.runtime_event import EventType
from synapse.models.task import TaskRequest
from synapse.runtime.a2a import A2AHub
from synapse.runtime.budget import AgentBudgetManager
from synapse.runtime.budget_service import BudgetService
from synapse.runtime.browser_service import BrowserService
from synapse.runtime.event_bus import EventBus
from synapse.runtime.registry import AgentRegistry
from synapse.runtime.safety import AgentSafetyLayer
from synapse.runtime.security import AgentSecuritySandbox, SandboxApprovalRequiredError, SandboxPermissionError
from synapse.runtime.state_store import InMemoryRuntimeStateStore
from synapse.runtime.tool_service import ToolService
from synapse.runtime.tools import ToolRegistry
from synapse.transports.websocket_manager import WebSocketManager


class _PolicyBrowser:
    async def get_layout(self, session_id: str):
        return type(
            "Page",
            (),
            {
                "title": "Example",
                "url": "https://example.com",
                "sections": [],
                "buttons": [],
                "inputs": [],
                "forms": [],
                "tables": [],
                "links": [],
            },
        )()

    def current_url(self, session_id: str) -> str:
        return "https://example.com"

    async def upload(self, session_id: str, selector: str, file_paths: list[str]):
        raise AssertionError("upload should not run when approval is required")


def test_sandbox_enforces_cross_domain_jump_limit() -> None:
    registry = AgentRegistry()
    registry.register(
        AgentDefinition(
            agent_id="agent-1",
            kind=AgentKind.CUSTOM,
            name="Agent 1",
            security={
                "allowed_domains": ["example.com", "arxiv.org", "github.com"],
                "max_cross_domain_jumps": 1,
            },
        )
    )
    sandbox = AgentSecuritySandbox(registry)

    sandbox.authorize_navigation("agent-1", "https://arxiv.org", current_url="https://example.com")
    sandbox.record_navigation("agent-1", previous_url="https://example.com", current_url="https://arxiv.org")

    with pytest.raises(SandboxPermissionError):
        sandbox.authorize_navigation("agent-1", "https://github.com", current_url="https://arxiv.org")


def test_browser_service_emits_approval_required_for_upload_policy() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        registry = AgentRegistry(state_store=store)
        registry.register(
            AgentDefinition(
                agent_id="agent-1",
                kind=AgentKind.CUSTOM,
                name="Agent 1",
                security={"allowed_domains": ["example.com"]},
            )
        )
        sandbox = AgentSecuritySandbox(registry, state_store=store)
        bus = EventBus(WebSocketManager(state_store=store))
        budget = BudgetService(AgentBudgetManager(), registry, bus)
        service = BrowserService(_PolicyBrowser(), sandbox, AgentSafetyLayer(), bus, budget, state_store=store)

        await store.store_run(
            "run-1",
            {
                "run_id": "run-1",
                "task_id": "task-1",
                "agent_id": "agent-1",
                "status": "running",
                "metadata": {"security_policy": {"uploads_allowed": True, "dangerous_action_requires_approval": True}},
            },
        )
        await store.store_session(
            "session-1",
            {"session_id": "session-1", "agent_id": "agent-1", "run_id": "run-1", "current_url": "https://example.com"},
        )

        async with bus.subscribe("policy-test") as queue:
            with pytest.raises(SandboxApprovalRequiredError):
                await service.upload(
                    request=type(
                        "UploadRequest",
                        (),
                        {
                            "session_id": "session-1",
                            "agent_id": "agent-1",
                            "selector": "input[type=file]",
                            "file_paths": ["/tmp/demo.txt"],
                        },
                    )()
                )
            event = await queue.get()
            assert event.event_type == EventType.APPROVAL_REQUIRED
            assert event.payload["action"] == "upload"

    asyncio.run(scenario())


def test_tool_service_blocks_external_request_to_disallowed_domain() -> None:
    async def scenario() -> None:
        registry = AgentRegistry()
        registry.register(
            AgentDefinition(
                agent_id="agent-1",
                kind=AgentKind.CUSTOM,
                name="Agent 1",
                security={
                    "allowed_domains": ["example.com"],
                    "blocked_domains": ["api.example.com"],
                    "allowed_tools": ["api.request"],
                },
            )
        )
        bus = EventBus(WebSocketManager(state_store=InMemoryRuntimeStateStore()))
        budget = BudgetService(AgentBudgetManager(), registry, bus)
        tools = ToolRegistry()
        async def handler(arguments):
            return {"ok": True}

        tools.register("api.request", handler, plugin_name=None)
        service = ToolService(tools, AgentSecuritySandbox(registry), AgentSafetyLayer(), bus, budget)

        with pytest.raises(SandboxPermissionError):
            await service.call_tool("api.request", {"url": "https://api.example.com/search"}, agent_id="agent-1")

    asyncio.run(scenario())


def test_a2a_delegation_emits_approval_required() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        registry = AgentRegistry(state_store=store)
        sockets = WebSocketManager(state_store=store)
        sandbox = AgentSecuritySandbox(registry, state_store=store)
        hub = A2AHub(registry, state_store=store, sockets=sockets, sandbox=sandbox)
        hub.register_agent(AgentRegistrationRequest(agent_id="agent-1", name="Agent 1", security={"allowed_domains": ["example.com"]}, verification_key="k1"))
        hub.register_agent(AgentRegistrationRequest(agent_id="agent-2", name="Agent 2", security={"allowed_domains": ["example.com"]}, verification_key="k2"))
        await store.store_run(
            "run-1",
            {
                "run_id": "run-1",
                "task_id": "task-1",
                "agent_id": "agent-1",
                "status": "running",
                "metadata": {"security_policy": {"dangerous_action_requires_approval": True}},
            },
        )

        async def execute_task(task: TaskRequest):
            raise AssertionError("delegated task should not execute when approval is required")

        hub.set_task_executor(execute_task)
        bus = EventBus(sockets)
        envelope = A2AEnvelope(
            type=A2AMessageType.DELEGATE,
            sender_agent_id="agent-1",
            recipient_agent_id="agent-2",
            payload={
                "task": {
                    "task_id": "task-1",
                    "agent_id": "agent-2",
                    "run_id": "run-1",
                    "goal": "Delegate analysis",
                    "constraints": {},
                }
            },
        )

        async with bus.subscribe("policy-test") as queue:
            with pytest.raises(ValueError):
                await hub.handle_message("agent-1", envelope.model_dump(mode="json"))
            event = await queue.get()
            while event.event_type != EventType.APPROVAL_REQUIRED:
                event = await queue.get()
            assert event.event_type == EventType.APPROVAL_REQUIRED
            assert event.payload["action"] == "delegate"

    asyncio.run(scenario())
