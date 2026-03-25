from __future__ import annotations

import uuid

from synapse.models.a2a import A2AEnvelope, A2AMessageType, AgentDelegateRequest, AgentPresence, AgentRegistrationRequest, AgentWireMessage
from synapse.models.agent import AgentBudgetUsage, AgentCheckpoint, AgentDefinition, AgentDiscoveryEntry
from synapse.models.browser import (
    BrowserState,
    ClickRequest,
    DismissRequest,
    DownloadRequest,
    DownloadResult,
    ExtractionResult,
    ExtractRequest,
    FindElementRequest,
    InspectRequest,
    LayoutRequest,
    OpenRequest,
    PageElementMatch,
    PageInspection,
    ScreenshotRequest,
    ScreenshotResult,
    ScrollExtractRequest,
    ScrollExtractResult,
    StructuredPageModel,
    TypeRequest,
    UploadRequest,
    UploadResult,
)
from synapse.models.runtime_event import EventType
from synapse.models.message import AgentMessage
from synapse.models.memory import MemoryRecord, MemorySearchRequest, MemorySearchResult, MemoryStoreRequest
from synapse.models.plugin import PluginDescriptor, PluginReloadRequest, ToolDescriptor
from synapse.models.run import RunState
from synapse.models.runtime_state import BrowserSessionState, ConnectionState, RuntimeCheckpoint
from synapse.models.task import ExtractionRequest, NavigationRequest, TaskClaimRequest, TaskCreateRequest, TaskRecord, TaskRequest, TaskResult, TaskUpdateRequest
from synapse.runtime.a2a import A2AHub
from synapse.runtime.budget import AgentBudgetManager
from synapse.runtime.budget_service import BudgetService
from synapse.runtime.browser_service import BrowserService
from synapse.runtime.checkpoint_service import CheckpointService
from synapse.runtime.compression.base import CompressionProvider
from synapse.runtime.event_bus import EventBus
from synapse.runtime.llm import LLMProvider
from synapse.runtime.memory import AgentMemoryManager
from synapse.runtime.memory_service import MemoryService
from synapse.runtime.messaging import AgentMessageBus
from synapse.runtime.registry import AgentRegistry
from synapse.runtime.run_store import RunStore
from synapse.runtime.security import AgentSecuritySandbox
from synapse.runtime.safety import AgentSafetyLayer
from synapse.runtime.state_store import RuntimeStateStore
from synapse.runtime.task_manager import TaskExecutionManager
from synapse.runtime.task_runtime import TaskRuntime
from synapse.runtime.tool_service import ToolService
from synapse.runtime.tools import ToolRegistry
from synapse.transports.websocket_manager import WebSocketManager


class RuntimeController:
    def __init__(
        self,
        browser,
        agents: AgentRegistry,
        tools: ToolRegistry,
        messages: AgentMessageBus,
        a2a: A2AHub,
        memory_manager: AgentMemoryManager,
        task_manager: TaskExecutionManager,
        sockets: WebSocketManager,
        sandbox: AgentSecuritySandbox,
        safety: AgentSafetyLayer,
        budget_manager: AgentBudgetManager,
        state_store: RuntimeStateStore | None = None,
        llm: LLMProvider | None = None,
        compression_provider: CompressionProvider | None = None,
    ) -> None:
        self.browser = browser
        self.agents = agents
        self.tools = tools
        self.messages = messages
        self.a2a = a2a
        self.memory_manager = memory_manager
        self.task_manager = task_manager
        self.sockets = sockets
        self.sandbox = sandbox
        self.safety = safety
        self.budget_manager = budget_manager
        self._state_store = state_store
        self.llm = llm
        self.compression_provider = compression_provider

        self.event_bus = EventBus(sockets, compression_provider=compression_provider)
        self.run_store = RunStore(state_store)
        self.budget_service = BudgetService(budget_manager, agents, self.event_bus, self.run_store)
        self.browser_service = BrowserService(browser, sandbox, safety, self.event_bus, self.budget_service, state_store)
        self.memory_service = MemoryService(
            memory_manager,
            self.budget_service,
            state_store=state_store,
            events=self.event_bus,
            compression_provider=compression_provider,
        )
        self.tool_service = ToolService(tools, sandbox, safety, self.event_bus, self.budget_service)
        self.checkpoint_service = CheckpointService(state_store, self.browser_service, self.event_bus)
        self.task_runtime = TaskRuntime(
            agents=agents,
            browser_service=self.browser_service,
            tool_service=self.tool_service,
            memory_service=self.memory_service,
            task_manager=task_manager,
            checkpoint_service=self.checkpoint_service,
            run_store=self.run_store,
            events=self.event_bus,
            safety=safety,
            llm=llm,
            compression_provider=compression_provider,
        )

    @property
    def state_store(self) -> RuntimeStateStore | None:
        return self._state_store

    @state_store.setter
    def state_store(self, state_store: RuntimeStateStore | None) -> None:
        self._state_store = state_store
        self.run_store.set_state_store(state_store) if hasattr(self, "run_store") else None
        self.browser_service.set_state_store(state_store) if hasattr(self, "browser_service") else None
        self.checkpoint_service.set_state_store(state_store) if hasattr(self, "checkpoint_service") else None
        self.memory_service.set_state_store(state_store) if hasattr(self, "memory_service") else None
        if state_store is not None and hasattr(self, "event_bus"):
            self.event_bus.set_state_store(state_store)

    async def create_session(self, session_id: str | None = None, agent_id: str | None = None):
        return await self.browser_service.create_session(session_id or str(uuid.uuid4()), agent_id=agent_id)

    async def navigate(self, request: NavigationRequest):
        return await self.browser_service.navigate(request)

    async def open(self, request: OpenRequest) -> BrowserState:
        return await self.browser_service.open(request)

    async def click(self, request: ClickRequest) -> BrowserState:
        return await self.browser_service.click(request)

    async def type(self, request: TypeRequest) -> BrowserState:
        return await self.browser_service.type(request)

    async def extract(self, request: ExtractionRequest) -> ExtractionResult:
        return await self.browser_service.extract(request)

    async def structured_extract(self, request: ExtractRequest) -> ExtractionResult:
        return await self.browser_service.extract(request)

    async def screenshot(self, request: ScreenshotRequest) -> ScreenshotResult:
        return await self.browser_service.screenshot(request)

    async def get_layout(self, request: LayoutRequest) -> StructuredPageModel:
        return await self.browser_service.get_layout(request)

    async def find_element(self, request: FindElementRequest) -> list[PageElementMatch]:
        return await self.browser_service.find_element(request)

    async def inspect(self, request: InspectRequest) -> PageInspection:
        return await self.browser_service.inspect(request)

    async def dismiss_popups(self, request: DismissRequest) -> BrowserState:
        return await self.browser_service.dismiss_popups(request)

    async def upload(self, request: UploadRequest) -> UploadResult:
        return await self.browser_service.upload(request)

    async def download(self, request: DownloadRequest) -> DownloadResult:
        return await self.browser_service.download(request)

    async def scroll_extract(self, request: ScrollExtractRequest) -> ScrollExtractResult:
        return await self.browser_service.scroll_extract(request)

    async def register_agent(self, definition: AgentDefinition) -> AgentDefinition:
        agent = self.agents.register(definition)
        await self.agents.save_to_store(agent)
        self.budget_manager.get_or_create(agent)
        await self.event_bus.emit(EventType.AGENT_REGISTERED, agent_id=agent.agent_id, source="runtime_controller", payload=agent.model_dump(mode="json"))
        await self.event_bus.emit(
            EventType.AGENT_STATUS_UPDATED,
            agent_id=agent.agent_id,
            source="runtime_controller",
            payload={"agent_id": agent.agent_id, "status": "idle"},
        )
        return agent

    async def register_a2a_agent(self, request: AgentRegistrationRequest) -> AgentDefinition:
        agent = self.a2a.register_agent(request)
        await self.agents.save_to_store(agent)
        self.budget_manager.get_or_create(agent)
        await self.event_bus.emit(EventType.AGENT_REGISTERED, agent_id=agent.agent_id, source="runtime_controller", payload=agent.model_dump(mode="json"))
        await self.event_bus.emit(
            EventType.AGENT_STATUS_UPDATED,
            agent_id=agent.agent_id,
            source="runtime_controller",
            payload={"agent_id": agent.agent_id, "status": "idle"},
        )
        return agent

    async def call_tool(self, tool_name: str, arguments: dict[str, object], agent_id: str | None) -> dict[str, object]:
        return await self.tool_service.call_tool(tool_name, arguments, agent_id)

    async def list_tools(self) -> list[ToolDescriptor]:
        return self.tool_service.list_tools()

    async def list_plugins(self) -> list[PluginDescriptor]:
        return self.tool_service.list_plugins()

    async def reload_plugins(self, request: PluginReloadRequest) -> list[PluginDescriptor]:
        return self.tool_service.reload_plugins(request)

    async def send_message(self, message: AgentMessage) -> AgentMessage:
        stored = self.messages.publish(message)
        await self.event_bus.emit(
            EventType.AGENT_MESSAGE,
            agent_id=message.sender_agent_id,
            source="runtime_controller",
            payload=stored.model_dump(mode="json"),
        )
        return stored

    async def store_memory(self, request: MemoryStoreRequest) -> MemoryRecord:
        return await self.memory_service.store(request)

    async def search_memory(self, request: MemorySearchRequest) -> list[MemorySearchResult]:
        return await self.memory_service.search(request)

    async def get_recent_memory(self, agent_id: str, limit: int = 10) -> list[MemoryRecord]:
        return await self.memory_service.get_recent(agent_id, limit)

    async def get_agent_budget(self, agent_id: str) -> AgentBudgetUsage:
        return self.budget_service.get_usage(agent_id)

    async def get_run_budget(self, run_id: str) -> AgentBudgetUsage:
        return await self.budget_service.get_run_budget(run_id)

    async def get_run_memory(self, run_id: str, limit: int = 100) -> list[MemoryRecord]:
        return await self.memory_service.get_run_memory(run_id, limit=limit)

    async def summarize_run_context(self, run_id: str, limit: int = 25) -> dict[str, object]:
        return await self.memory_service.summarize_run_context(run_id, limit=limit)

    async def save_agent_checkpoint(self, agent_id: str, state: dict[str, object], reason: str | None = None) -> AgentCheckpoint:
        return await self.budget_service.save_agent_checkpoint(agent_id, state, reason)

    async def discover_agents(self) -> list[AgentPresence]:
        return self.a2a.list_agents()

    async def get_persisted_agents(self) -> list[AgentDefinition]:
        rows = await self.agents.list_persisted_agents()
        return [AgentDefinition.model_validate(row["agent"]) for row in rows if isinstance(row.get("agent"), dict)]

    async def get_persisted_agent(self, agent_id: str) -> AgentDefinition:
        row = await self.agents.get_persisted_agent(agent_id)
        if row is None or not isinstance(row.get("agent"), dict):
            raise KeyError(f"Agent not found: {agent_id}")
        return AgentDefinition.model_validate(row["agent"])

    async def get_agent_status(self, agent_id: str) -> dict[str, object]:
        self.agents.get(agent_id)
        status = self.agents.get_agent_status(agent_id)
        return {
            "agent_id": status["agent_id"],
            "status": status["status"],
            "availability": status["availability"],
            "last_seen_at": status["last_seen_at"].isoformat(),
        }

    async def list_sessions(self, agent_id: str | None = None) -> list[BrowserSessionState]:
        return await self.browser_service.list_sessions(agent_id=agent_id)

    async def get_session(self, session_id: str) -> BrowserSessionState:
        return await self.browser_service.get_session(session_id)

    async def list_connections(self) -> list[ConnectionState]:
        return await self.a2a.list_persisted_connections()

    async def get_connection(self, agent_id: str) -> ConnectionState:
        connection = await self.a2a.get_persisted_connection(agent_id)
        if connection is None:
            raise KeyError(f"Connection not found: {agent_id}")
        return connection

    async def save_checkpoint(self, task_id: str, state: dict[str, object]) -> RuntimeCheckpoint:
        return await self.checkpoint_service.save_checkpoint(task_id, state)

    async def list_checkpoints(self, agent_id: str | None = None, task_id: str | None = None) -> list[RuntimeCheckpoint]:
        return await self.checkpoint_service.list_checkpoints(agent_id=agent_id, task_id=task_id)

    async def list_runs(self, agent_id: str | None = None, task_id: str | None = None) -> list[RunState]:
        return await self.task_runtime.list_runs(agent_id=agent_id, task_id=task_id)

    async def get_run(self, run_id: str) -> RunState:
        return await self.task_runtime.get_run(run_id)

    async def get_run_events(self, run_id: str) -> list[dict[str, object]]:
        if self.state_store is None:
            return []
        return await self.state_store.get_runtime_events(run_id=run_id, limit=200)

    async def get_run_checkpoints(self, run_id: str) -> list[RuntimeCheckpoint]:
        return await self.checkpoint_service.list_checkpoints(run_id=run_id)

    async def pause_run(self, run_id: str) -> RunState:
        return await self.task_runtime.pause_run(run_id)

    async def resume_run(self, run_id: str):
        return await self.task_runtime.resume_run(run_id)

    async def cancel_run(self, run_id: str) -> RunState:
        return await self.task_runtime.cancel_run(run_id)

    async def get_checkpoint(self, checkpoint_id: str) -> RuntimeCheckpoint:
        return await self.checkpoint_service.get_checkpoint(checkpoint_id)

    async def delete_checkpoint(self, checkpoint_id: str) -> None:
        await self.checkpoint_service.delete_checkpoint(checkpoint_id)

    async def resume_task(self, checkpoint_id: str) -> TaskResult:
        checkpoint, request = await self.checkpoint_service.resume_context(checkpoint_id)
        result = await self.execute_task(request)
        await self.checkpoint_service.emit_resumed(checkpoint, result)
        return result

    async def find_agents(self, capability: str) -> list[AgentDiscoveryEntry]:
        return self.a2a.find_agents(capability)

    async def send_a2a(self, envelope: A2AEnvelope) -> A2AEnvelope:
        response = await self.a2a.handle_message(envelope.sender_agent_id, envelope.model_dump(mode="json"))
        if response is None:
            raise RuntimeError("A2A message did not produce a response.")
        task_payload = response.payload.get("task") if isinstance(response.payload, dict) else None
        run_id = task_payload.get("run_id") if isinstance(task_payload, dict) else None
        await self.event_bus.emit(
            EventType.A2A_MESSAGE,
            run_id=str(run_id) if run_id is not None else None,
            agent_id=envelope.sender_agent_id,
            source="runtime_controller",
            payload=response.model_dump(mode="json"),
            correlation_id=envelope.message_id,
        )
        return response

    async def send_agent_wire_message(self, message: AgentWireMessage) -> AgentWireMessage:
        envelope = self.a2a.from_wire_message(message)
        response = await self.send_a2a(envelope)
        return self.a2a.to_wire_message(response)

    async def delegate_agent_task(self, request: AgentDelegateRequest) -> AgentWireMessage:
        message = AgentWireMessage(
            type=A2AMessageType.REQUEST_TASK,
            agent=request.agent,
            target_agent=request.target_agent,
            payload=request.payload,
        )
        return await self.send_agent_wire_message(message)

    async def create_task_record(self, request: TaskCreateRequest) -> TaskRecord:
        return await self.task_runtime.create_task(request)

    async def claim_task(self, task_id: str, request: TaskClaimRequest) -> TaskRecord:
        return await self.task_runtime.claim_task(task_id, request)

    async def update_task_record(self, task_id: str, request: TaskUpdateRequest) -> TaskRecord:
        return await self.task_runtime.update_task(task_id, request)

    async def list_active_tasks(self) -> list[TaskRecord]:
        return await self.task_runtime.list_active_tasks()

    async def execute_task(self, request: TaskRequest) -> TaskResult:
        self.budget_service.ensure_budget(request.agent_id)
        return await self.task_runtime.execute_task(request)
