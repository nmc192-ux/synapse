import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

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
from synapse.models.events import EventType, RuntimeEvent
from synapse.models.message import AgentMessage
from synapse.models.memory import MemoryRecord, MemorySearchRequest, MemorySearchResult, MemoryStoreRequest
from synapse.models.plugin import PluginDescriptor, PluginReloadRequest, ToolDescriptor
from synapse.models.runtime_state import BrowserSessionState, ConnectionState, RuntimeCheckpoint
from synapse.models.task import ExtractionRequest, NavigationRequest, TaskRequest, TaskResult, TaskStatus
from synapse.models.task import TaskClaimRequest, TaskCreateRequest, TaskRecord, TaskUpdateRequest
from synapse.runtime.messaging import AgentMessageBus
from synapse.runtime.a2a import A2AHub
from synapse.runtime.budget import AgentBudgetLimitExceeded, AgentBudgetManager
from synapse.runtime.llm import LLMProvider
from synapse.runtime.memory import AgentMemoryManager
from synapse.runtime.registry import AgentRegistry
from synapse.runtime.security import AgentSecuritySandbox
from synapse.runtime.safety import AgentSafetyLayer, SecurityAlertError, SecurityFinding
from synapse.runtime.session import BrowserSession
from synapse.runtime.task_manager import TaskExecutionManager
from synapse.runtime.tools import ToolRegistry
from synapse.runtime.state_store import RuntimeStateStore
from synapse.transports.websocket_manager import WebSocketManager

if TYPE_CHECKING:
    from synapse.runtime.browser import BrowserRuntime


class RuntimeOrchestrator:
    def __init__(
        self,
        browser: "BrowserRuntime",
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
        self.state_store = state_store
        self.llm = llm
        self._task_context: dict[str, TaskRequest] = {}

    async def create_session(self, session_id: str | None = None, agent_id: str | None = None) -> BrowserSession:
        resolved_session_id = session_id or str(uuid.uuid4())
        session = await self.browser.create_session(resolved_session_id, agent_id=agent_id)
        await self.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.SESSION_CREATED,
                session_id=session.session_id,
                payload={"session_id": session.session_id},
            )
        )
        return session

    async def navigate(self, request: NavigationRequest) -> BrowserSession:
        self.sandbox.authorize_domain(request.agent_id, str(request.url))
        self.sandbox.consume_browser_action(request.agent_id)
        if request.agent_id:
            usage = self.budget_manager.increment_page(self.agents.get(request.agent_id))
            await self._broadcast_budget_update(request.agent_id, usage)
            await self._check_budget_limits(request.agent_id)
        session = await self.browser.navigate(request.session_id, str(request.url))
        await self._enforce_page_safety(
            agent_id=request.agent_id,
            session_id=session.session_id,
            action="browser.navigate",
            page=session.page,
        )
        await self.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.PAGE_NAVIGATED,
                session_id=session.session_id,
                payload={"url": session.current_url},
            )
        )
        return session

    async def open(self, request: OpenRequest) -> BrowserState:
        try:
            self.sandbox.authorize_domain(request.agent_id, str(request.url))
            self.sandbox.consume_browser_action(request.agent_id)
            if request.agent_id:
                usage = self.budget_manager.increment_page(self.agents.get(request.agent_id))
                await self._broadcast_budget_update(request.agent_id, usage)
                await self._check_budget_limits(request.agent_id)
            state = await self.browser.open(request.session_id, str(request.url))
            await self._enforce_page_safety(
                agent_id=request.agent_id,
                session_id=state.session_id,
                action="browser.open",
                page=state.page,
            )
            await self.sockets.broadcast(
                RuntimeEvent(
                    event_type=EventType.PAGE_NAVIGATED,
                    session_id=state.session_id,
                    payload=state.model_dump(mode="json"),
                )
            )
            await self._emit_browser_metadata_events(request.agent_id, state.session_id, state.metadata)
            return state
        except Exception as exc:
            await self._emit_browser_error("open", request.agent_id, request.session_id, exc)
            raise

    async def click(self, request: ClickRequest) -> BrowserState:
        try:
            await self._ensure_current_page_safe(request.agent_id, request.session_id, "browser.click")
            self.sandbox.authorize_domain(request.agent_id, self.browser.current_url(request.session_id))
            self.sandbox.consume_browser_action(request.agent_id)
            state = await self.browser.click(request.session_id, request.selector)
            await self._enforce_page_safety(
                agent_id=request.agent_id,
                session_id=state.session_id,
                action="browser.click",
                page=state.page,
            )
            await self.sockets.broadcast(
                RuntimeEvent(
                    event_type=EventType.PAGE_NAVIGATED,
                    session_id=state.session_id,
                    payload={"action": "click", "selector": request.selector, **state.model_dump(mode="json")},
                )
            )
            await self._emit_browser_metadata_events(request.agent_id, state.session_id, state.metadata)
            return state
        except Exception as exc:
            await self._emit_browser_error("click", request.agent_id, request.session_id, exc)
            raise

    async def type(self, request: TypeRequest) -> BrowserState:
        try:
            await self._ensure_current_page_safe(request.agent_id, request.session_id, "browser.type")
            self.sandbox.authorize_domain(request.agent_id, self.browser.current_url(request.session_id))
            self.sandbox.consume_browser_action(request.agent_id)
            state = await self.browser.type(request.session_id, request.selector, request.text)
            await self._enforce_page_safety(
                agent_id=request.agent_id,
                session_id=state.session_id,
                action="browser.type",
                page=state.page,
            )
            await self.sockets.broadcast(
                RuntimeEvent(
                    event_type=EventType.PAGE_NAVIGATED,
                    session_id=state.session_id,
                    payload={"action": "type", "selector": request.selector, **state.model_dump(mode="json")},
                )
            )
            await self._emit_browser_metadata_events(request.agent_id, state.session_id, state.metadata)
            return state
        except Exception as exc:
            await self._emit_browser_error("type", request.agent_id, request.session_id, exc)
            raise

    async def extract(self, request: ExtractionRequest) -> ExtractionResult:
        await self._ensure_current_page_safe(request.agent_id, request.session_id, "browser.extract")
        self.sandbox.authorize_domain(request.agent_id, self.browser.current_url(request.session_id))
        self.sandbox.consume_browser_action(request.agent_id)
        payload = await self.browser.extract(request.session_id, request.selector, request.attribute)
        await self._enforce_page_safety(
            agent_id=request.agent_id,
            session_id=request.session_id,
            action="browser.extract",
            page=payload.page,
        )
        await self.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.DATA_EXTRACTED,
                session_id=request.session_id,
                payload=payload.model_dump(mode="json"),
            )
        )
        return payload

    async def structured_extract(self, request: ExtractRequest) -> ExtractionResult:
        await self._ensure_current_page_safe(request.agent_id, request.session_id, "browser.extract")
        self.sandbox.authorize_domain(request.agent_id, self.browser.current_url(request.session_id))
        self.sandbox.consume_browser_action(request.agent_id)
        payload = await self.browser.extract(request.session_id, request.selector, request.attribute)
        await self._enforce_page_safety(
            agent_id=request.agent_id,
            session_id=request.session_id,
            action="browser.extract",
            page=payload.page,
        )
        await self.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.DATA_EXTRACTED,
                session_id=request.session_id,
                payload=payload.model_dump(mode="json"),
            )
        )
        return payload

    async def screenshot(self, request: ScreenshotRequest) -> ScreenshotResult:
        await self._ensure_current_page_safe(request.agent_id, request.session_id, "browser.screenshot")
        self.sandbox.authorize_domain(request.agent_id, self.browser.current_url(request.session_id))
        self.sandbox.consume_browser_action(request.agent_id)
        result = await self.browser.screenshot(request.session_id)
        await self._enforce_page_safety(
            agent_id=request.agent_id,
            session_id=request.session_id,
            action="browser.screenshot",
            page=result.page,
        )
        await self.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.SCREENSHOT_CAPTURED,
                session_id=request.session_id,
                payload={"action": "screenshot", **result.model_dump(mode="json")},
            )
        )
        return result

    async def get_layout(self, request: LayoutRequest) -> StructuredPageModel:
        await self._ensure_current_page_safe(request.agent_id, request.session_id, "browser.get_layout")
        self.sandbox.authorize_domain(request.agent_id, self.browser.current_url(request.session_id))
        self.sandbox.consume_browser_action(request.agent_id)
        return await self.browser.get_layout(request.session_id)

    async def find_element(self, request: FindElementRequest) -> list[PageElementMatch]:
        await self._ensure_current_page_safe(request.agent_id, request.session_id, "browser.find_element")
        self.sandbox.authorize_domain(request.agent_id, self.browser.current_url(request.session_id))
        self.sandbox.consume_browser_action(request.agent_id)
        return await self.browser.find_element(request.session_id, request.type, request.text)

    async def inspect(self, request: InspectRequest) -> PageInspection:
        await self._ensure_current_page_safe(request.agent_id, request.session_id, "browser.inspect")
        self.sandbox.authorize_domain(request.agent_id, self.browser.current_url(request.session_id))
        self.sandbox.consume_browser_action(request.agent_id)
        return await self.browser.inspect(request.session_id, request.selector)

    async def dismiss_popups(self, request: DismissRequest) -> BrowserState:
        await self._ensure_current_page_safe(request.agent_id, request.session_id, "browser.dismiss")
        self.sandbox.authorize_domain(request.agent_id, self.browser.current_url(request.session_id))
        self.sandbox.consume_browser_action(request.agent_id)
        state = await self.browser.dismiss_popups(request.session_id)
        await self.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.POPUP_DISMISSED,
                session_id=request.session_id,
                agent_id=request.agent_id,
                payload=state.metadata,
            )
        )
        return state

    async def upload(self, request: UploadRequest) -> UploadResult:
        await self._ensure_current_page_safe(request.agent_id, request.session_id, "browser.upload")
        self.sandbox.authorize_domain(request.agent_id, self.browser.current_url(request.session_id))
        self.sandbox.consume_browser_action(request.agent_id)
        result = await self.browser.upload(request.session_id, request.selector, request.file_paths)
        await self.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.UPLOAD_COMPLETED,
                session_id=request.session_id,
                agent_id=request.agent_id,
                payload=result.model_dump(mode="json"),
            )
        )
        return result

    async def download(self, request: DownloadRequest) -> DownloadResult:
        await self._ensure_current_page_safe(request.agent_id, request.session_id, "browser.download")
        self.sandbox.authorize_domain(request.agent_id, self.browser.current_url(request.session_id))
        self.sandbox.consume_browser_action(request.agent_id)
        result = await self.browser.download(
            request.session_id,
            trigger_selector=request.trigger_selector,
            timeout_ms=request.timeout_ms,
        )
        await self.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.DOWNLOAD_COMPLETED,
                session_id=request.session_id,
                agent_id=request.agent_id,
                payload=result.model_dump(mode="json"),
            )
        )
        return result

    async def scroll_extract(self, request: ScrollExtractRequest) -> ScrollExtractResult:
        await self._ensure_current_page_safe(request.agent_id, request.session_id, "browser.scroll_extract")
        self.sandbox.authorize_domain(request.agent_id, self.browser.current_url(request.session_id))
        self.sandbox.consume_browser_action(request.agent_id)
        result = await self.browser.scroll_extract(
            request.session_id,
            selector=request.selector,
            attribute=request.attribute,
            max_scrolls=request.max_scrolls,
            scroll_step=request.scroll_step,
        )
        await self.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.DATA_EXTRACTED,
                session_id=request.session_id,
                agent_id=request.agent_id,
                payload=result.model_dump(mode="json"),
            )
        )
        return result

    async def register_agent(self, definition: AgentDefinition) -> AgentDefinition:
        agent = self.agents.register(definition)
        await self.agents.save_to_store(agent)
        self.budget_manager.get_or_create(agent)
        await self.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.AGENT_REGISTERED,
                agent_id=agent.agent_id,
                payload=agent.model_dump(mode="json"),
            )
        )
        await self.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.AGENT_STATUS_UPDATED,
                agent_id=agent.agent_id,
                payload={"agent_id": agent.agent_id, "status": "idle"},
            )
        )
        return agent

    async def register_a2a_agent(self, request: AgentRegistrationRequest) -> AgentDefinition:
        agent = self.a2a.register_agent(request)
        await self.agents.save_to_store(agent)
        self.budget_manager.get_or_create(agent)
        await self.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.AGENT_REGISTERED,
                agent_id=agent.agent_id,
                payload=agent.model_dump(mode="json"),
            )
        )
        await self.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.AGENT_STATUS_UPDATED,
                agent_id=agent.agent_id,
                payload={"agent_id": agent.agent_id, "status": "idle"},
            )
        )
        return agent

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, object],
        agent_id: str | None,
    ) -> dict[str, object]:
        await self._enforce_tool_safety(agent_id, tool_name, arguments)
        self.sandbox.authorize_tool(agent_id, tool_name)
        self.sandbox.consume_tool_call(agent_id)
        if agent_id:
            usage = self.budget_manager.increment_tool_call(self.agents.get(agent_id))
            await self._broadcast_budget_update(agent_id, usage)
            await self._check_budget_limits(agent_id)
        result = await self.tools.call(tool_name, arguments)
        await self.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.TOOL_CALLED,
                payload={"tool_name": tool_name, "arguments": arguments, "result": result},
            )
        )
        return result

    async def list_tools(self) -> list[ToolDescriptor]:
        return self.tools.list_tools()

    async def list_plugins(self) -> list[PluginDescriptor]:
        return self.tools.list_plugins()

    async def reload_plugins(self, request: PluginReloadRequest) -> list[PluginDescriptor]:
        self.tools.load_plugins(module_names=request.modules)
        return self.tools.list_plugins()

    async def send_message(self, message: AgentMessage) -> AgentMessage:
        stored = self.messages.publish(message)
        await self.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.AGENT_MESSAGE,
                agent_id=message.sender_agent_id,
                payload=stored.model_dump(mode="json"),
            )
        )
        return stored

    async def store_memory(self, request: MemoryStoreRequest) -> MemoryRecord:
        record = await self.memory_manager.store(request)
        try:
            usage = self.budget_manager.increment_memory_write(self.agents.get(request.agent_id))
            await self._broadcast_budget_update(request.agent_id, usage)
            await self._check_budget_limits(request.agent_id)
        except KeyError:
            pass
        return record

    async def search_memory(self, request: MemorySearchRequest) -> list[MemorySearchResult]:
        return await self.memory_manager.search(request)

    async def get_recent_memory(self, agent_id: str, limit: int = 10) -> list[MemoryRecord]:
        return await self.memory_manager.get_recent(agent_id, limit)

    async def get_agent_budget(self, agent_id: str) -> AgentBudgetUsage:
        self.agents.get(agent_id)
        return self.budget_manager.get_usage(agent_id)

    async def save_agent_checkpoint(
        self,
        agent_id: str,
        state: dict[str, object],
        reason: str | None = None,
    ) -> AgentCheckpoint:
        self.agents.get(agent_id)
        checkpoint = self.budget_manager.save_checkpoint(agent_id, state, reason)
        await self.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.BUDGET_UPDATED,
                agent_id=agent_id,
                payload={
                    "usage": self.budget_manager.get_usage(agent_id).model_dump(mode="json"),
                    "checkpoint_reason": reason,
                },
            )
        )
        return checkpoint

    async def discover_agents(self) -> list[AgentPresence]:
        return self.a2a.list_agents()

    async def get_persisted_agents(self) -> list[AgentDefinition]:
        rows = await self.agents.list_persisted_agents()
        agents: list[AgentDefinition] = []
        for row in rows:
            payload = row.get("agent")
            if isinstance(payload, dict):
                agents.append(AgentDefinition.model_validate(payload))
        return agents

    async def get_persisted_agent(self, agent_id: str) -> AgentDefinition:
        row = await self.agents.get_persisted_agent(agent_id)
        if row is None:
            raise KeyError(f"Agent not found: {agent_id}")
        payload = row.get("agent")
        if not isinstance(payload, dict):
            raise KeyError(f"Agent not found: {agent_id}")
        return AgentDefinition.model_validate(payload)

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
        return await self.browser.list_sessions(agent_id=agent_id)

    async def get_session(self, session_id: str) -> BrowserSessionState:
        if self.state_store is None:
            raise KeyError(f"Session not found: {session_id}")
        payload = await self.state_store.get_session(session_id)
        if payload is None:
            raise KeyError(f"Session not found: {session_id}")
        return BrowserSessionState.model_validate(payload)

    async def list_connections(self) -> list[ConnectionState]:
        return await self.a2a.list_persisted_connections()

    async def get_connection(self, agent_id: str) -> ConnectionState:
        connection = await self.a2a.get_persisted_connection(agent_id)
        if connection is None:
            raise KeyError(f"Connection not found: {agent_id}")
        return connection

    async def save_checkpoint(self, task_id: str, state: dict[str, object]) -> RuntimeCheckpoint:
        context = self._task_context.get(task_id)
        agent_id = str(state.get("agent_id") or (context.agent_id if context is not None else ""))
        if not agent_id:
            raise KeyError(f"Unable to resolve agent for task: {task_id}")

        checkpoint = RuntimeCheckpoint(
            task_id=task_id,
            agent_id=agent_id,
            current_goal=str(state.get("current_goal") or (context.goal if context is not None else "")),
            planner_state=state.get("planner_state", {}) if isinstance(state.get("planner_state"), dict) else {},
            memory_snapshot_reference=str(state.get("memory_snapshot_reference")) if state.get("memory_snapshot_reference") is not None else None,
            browser_session_reference=str(state.get("browser_session_reference") or (context.session_id if context is not None else "")) or None,
            last_action=state.get("last_action", {}) if isinstance(state.get("last_action"), dict) else {},
            pending_actions=state.get("pending_actions", []) if isinstance(state.get("pending_actions"), list) else [],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        if self.state_store is not None:
            await self.state_store.store_checkpoint(checkpoint.checkpoint_id, checkpoint.model_dump(mode="json"))
        await self.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.CHECKPOINT_SAVED,
                agent_id=checkpoint.agent_id,
                session_id=checkpoint.browser_session_reference,
                payload=checkpoint.model_dump(mode="json"),
            )
        )
        return checkpoint

    async def list_checkpoints(
        self,
        agent_id: str | None = None,
        task_id: str | None = None,
    ) -> list[RuntimeCheckpoint]:
        if self.state_store is None:
            return []
        rows = await self.state_store.list_checkpoints(agent_id=agent_id, task_id=task_id)
        return [RuntimeCheckpoint.model_validate(row) for row in rows]

    async def get_checkpoint(self, checkpoint_id: str) -> RuntimeCheckpoint:
        if self.state_store is None:
            raise KeyError(f"Checkpoint not found: {checkpoint_id}")
        payload = await self.state_store.get_checkpoint(checkpoint_id)
        if payload is None:
            raise KeyError(f"Checkpoint not found: {checkpoint_id}")
        return RuntimeCheckpoint.model_validate(payload)

    async def delete_checkpoint(self, checkpoint_id: str) -> None:
        if self.state_store is None:
            return
        await self.state_store.delete_checkpoint(checkpoint_id)

    async def resume_task(self, checkpoint_id: str) -> TaskResult:
        checkpoint = await self.get_checkpoint(checkpoint_id)
        if checkpoint.browser_session_reference:
            restored = await self.browser.restore_session_state(checkpoint.browser_session_reference)
            if restored is not None:
                await self.sockets.broadcast(
                    RuntimeEvent(
                        event_type=EventType.SESSION_RESTORED,
                        agent_id=checkpoint.agent_id,
                        session_id=checkpoint.browser_session_reference,
                        payload={"checkpoint_id": checkpoint_id, "session_id": checkpoint.browser_session_reference},
                    )
                )

        constraints: dict[str, object] = {}
        if checkpoint.pending_actions:
            constraints["action_plan"] = checkpoint.pending_actions
        if checkpoint.planner_state:
            constraints["planner_state"] = checkpoint.planner_state

        request = TaskRequest(
            task_id=checkpoint.task_id,
            agent_id=checkpoint.agent_id,
            goal=checkpoint.current_goal,
            session_id=checkpoint.browser_session_reference,
            constraints=constraints,
        )
        result = await self.execute_task(request)
        await self.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.CHECKPOINT_RESUMED,
                agent_id=checkpoint.agent_id,
                session_id=checkpoint.browser_session_reference,
                payload={"checkpoint_id": checkpoint_id, "task_id": checkpoint.task_id, "result": result.model_dump(mode="json")},
            )
        )
        return result

    async def find_agents(self, capability: str) -> list[AgentDiscoveryEntry]:
        return self.a2a.find_agents(capability)

    async def send_a2a(self, envelope: A2AEnvelope) -> A2AEnvelope:
        response = await self.a2a.handle_message(envelope.sender_agent_id, envelope.model_dump(mode="json"))
        if response is None:
            raise RuntimeError("A2A message did not produce a response.")
        await self.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.A2A_MESSAGE,
                agent_id=envelope.sender_agent_id,
                payload=response.model_dump(mode="json"),
            )
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
        return await self.task_manager.create_task(request)

    async def claim_task(self, task_id: str, request: TaskClaimRequest) -> TaskRecord:
        return await self.task_manager.claim_task(task_id, request)

    async def update_task_record(self, task_id: str, request: TaskUpdateRequest) -> TaskRecord:
        return await self.task_manager.update_task(task_id, request)

    async def list_active_tasks(self) -> list[TaskRecord]:
        return await self.task_manager.list_active_tasks()

    async def execute_task(self, request: TaskRequest) -> TaskResult:
        await self._enforce_task_safety(request)
        self.budget_manager.get_or_create(self.agents.get(request.agent_id))
        if request.session_id is None:
            session = await self.create_session(agent_id=request.agent_id)
            request = request.model_copy(update={"session_id": session.session_id})
        self._task_context[request.task_id] = request

        for tool_call in request.tool_calls:
            await self.call_tool(tool_call.tool_name, tool_call.arguments, agent_id=request.agent_id)

        adapter = self.agents.build_adapter(
            request.agent_id,
            browser=self.browser,
            sockets=self.sockets,
            sandbox=self.sandbox,
            safety=self.safety,
            memory_manager=self.memory_manager,
            budget_manager=self.budget_manager,
            llm=self.llm,
        )
        result = await adapter.execute_task(request)
        final_result = result.model_copy(
            update={
                "status": result.status if result.status != TaskStatus.PENDING else TaskStatus.RUNNING,
                "artifacts": {
                    **result.artifacts,
                    "session_id": request.session_id,
                },
            }
        )
        await self.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.TASK_UPDATED,
                session_id=request.session_id,
                agent_id=request.agent_id,
                payload=final_result.model_dump(mode="json"),
            )
        )
        if self.state_store is not None:
            await self.browser.save_session_state(request.session_id)
            await self.sockets.broadcast(
                RuntimeEvent(
                    event_type=EventType.SESSION_SAVED,
                    session_id=request.session_id,
                    agent_id=request.agent_id,
                    payload={"task_id": request.task_id, "session_id": request.session_id},
                )
            )
        return final_result

    async def _ensure_current_page_safe(self, agent_id: str | None, session_id: str, action: str) -> None:
        page = await self.browser.get_layout(session_id)
        await self._enforce_page_safety(agent_id, session_id, action, page)

    async def _enforce_page_safety(
        self,
        agent_id: str | None,
        session_id: str | None,
        action: str,
        page: StructuredPageModel | None,
    ) -> None:
        if page is None:
            return
        finding = self.safety.inspect_page(page, action)
        if finding is not None:
            await self._raise_security_alert(agent_id, session_id, finding)

    async def _enforce_task_safety(self, request: TaskRequest) -> None:
        finding = self.safety.validate_task(request)
        if finding is not None:
            await self._raise_security_alert(request.agent_id, request.session_id, finding)

    async def _enforce_tool_safety(
        self,
        agent_id: str | None,
        tool_name: str,
        arguments: dict[str, object],
    ) -> None:
        finding = self.safety.validate_tool_call(tool_name, arguments)
        if finding is not None:
            await self._raise_security_alert(agent_id, None, finding)

    async def _raise_security_alert(
        self,
        agent_id: str | None,
        session_id: str | None,
        finding: SecurityFinding,
    ) -> None:
        await self.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.SECURITY_ALERT,
                session_id=session_id,
                agent_id=agent_id,
                payload=finding.model_dump(mode="json"),
            )
        )
        raise SecurityAlertError(finding)

    async def _broadcast_budget_update(
        self,
        agent_id: str,
        usage: AgentBudgetUsage,
        warning: str | None = None,
    ) -> None:
        payload: dict[str, object] = {"usage": usage.model_dump(mode="json")}
        if warning is not None:
            payload["warning"] = warning
        await self.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.BUDGET_UPDATED,
                agent_id=agent_id,
                payload=payload,
            )
        )

    async def _check_budget_limits(self, agent_id: str) -> None:
        agent = self.agents.get(agent_id)
        try:
            usage, warnings = self.budget_manager.check_limits(agent)
        except AgentBudgetLimitExceeded as exc:
            if agent.execution_policy.save_checkpoint_on_limit or agent.execution_policy.pause_on_hard_limit:
                self.budget_manager.save_checkpoint(
                    agent_id,
                    {
                        "usage": self.budget_manager.get_usage(agent_id).model_dump(mode="json"),
                    },
                    reason=str(exc),
                )
            await self._broadcast_budget_update(agent_id, self.budget_manager.get_usage(agent_id), warning=str(exc))
            raise

        if agent.execution_policy.stop_on_soft_limit and any("exceeded" in warning for warning in warnings):
            raise AgentBudgetLimitExceeded("Agent terminated: soft budget limit exceeded.")

        for warning in warnings:
            await self._broadcast_budget_update(agent_id, usage, warning=warning)

    async def _emit_browser_metadata_events(
        self,
        agent_id: str | None,
        session_id: str | None,
        metadata: dict[str, object],
    ) -> None:
        if metadata.get("route_changed"):
            await self.sockets.broadcast(
                RuntimeEvent(
                    event_type=EventType.NAVIGATION_ROUTE_CHANGED,
                    agent_id=agent_id,
                    session_id=session_id,
                    payload=metadata,
                )
            )
        if metadata.get("session_expired"):
            await self.sockets.broadcast(
                RuntimeEvent(
                    event_type=EventType.SESSION_EXPIRED,
                    agent_id=agent_id,
                    session_id=session_id,
                    payload=metadata,
                )
            )
        dismissed = metadata.get("dismissed_blockers")
        if isinstance(dismissed, list) and dismissed:
            await self.sockets.broadcast(
                RuntimeEvent(
                    event_type=EventType.POPUP_DISMISSED,
                    agent_id=agent_id,
                    session_id=session_id,
                    payload={"dismissed_blockers": dismissed},
                )
            )

    async def _emit_browser_error(
        self,
        action: str,
        agent_id: str | None,
        session_id: str | None,
        exc: Exception,
    ) -> None:
        await self.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.BROWSER_ERROR,
                agent_id=agent_id,
                session_id=session_id,
                payload={"action": action, "error": str(exc)},
            )
        )
