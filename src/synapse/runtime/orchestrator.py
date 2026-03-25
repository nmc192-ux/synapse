import uuid

from synapse.models.a2a import A2AEnvelope, A2AMessageType, AgentDelegateRequest, AgentPresence, AgentRegistrationRequest, AgentWireMessage
from synapse.models.agent import AgentDefinition, AgentDiscoveryEntry
from synapse.models.browser import (
    BrowserState,
    ClickRequest,
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
    StructuredPageModel,
    TypeRequest,
)
from synapse.models.events import EventType, RuntimeEvent
from synapse.models.message import AgentMessage
from synapse.models.memory import MemoryRecord, MemorySearchRequest, MemorySearchResult, MemoryStoreRequest
from synapse.models.plugin import PluginDescriptor, PluginReloadRequest, ToolDescriptor
from synapse.models.task import ExtractionRequest, NavigationRequest, TaskRequest, TaskResult, TaskStatus
from synapse.models.task import TaskClaimRequest, TaskCreateRequest, TaskRecord, TaskUpdateRequest
from synapse.runtime.browser import BrowserRuntime
from synapse.runtime.messaging import AgentMessageBus
from synapse.runtime.a2a import A2AHub
from synapse.runtime.memory import AgentMemoryManager
from synapse.runtime.registry import AgentRegistry
from synapse.runtime.security import AgentSecuritySandbox
from synapse.runtime.safety import AgentSafetyLayer, SecurityAlertError, SecurityFinding
from synapse.runtime.session import BrowserSession
from synapse.runtime.task_manager import TaskExecutionManager
from synapse.runtime.tools import ToolRegistry
from synapse.transports.websocket_manager import WebSocketManager


class RuntimeOrchestrator:
    def __init__(
        self,
        browser: BrowserRuntime,
        agents: AgentRegistry,
        tools: ToolRegistry,
        messages: AgentMessageBus,
        a2a: A2AHub,
        memory_manager: AgentMemoryManager,
        task_manager: TaskExecutionManager,
        sockets: WebSocketManager,
        sandbox: AgentSecuritySandbox,
        safety: AgentSafetyLayer,
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

    async def create_session(self, session_id: str | None = None) -> BrowserSession:
        resolved_session_id = session_id or str(uuid.uuid4())
        session = await self.browser.create_session(resolved_session_id)
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
        self.sandbox.authorize_domain(request.agent_id, str(request.url))
        self.sandbox.consume_browser_action(request.agent_id)
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
        return state

    async def click(self, request: ClickRequest) -> BrowserState:
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
        return state

    async def type(self, request: TypeRequest) -> BrowserState:
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
        return state

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

    async def register_agent(self, definition: AgentDefinition) -> AgentDefinition:
        agent = self.agents.register(definition)
        await self.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.AGENT_REGISTERED,
                agent_id=agent.agent_id,
                payload=agent.model_dump(mode="json"),
            )
        )
        return agent

    async def register_a2a_agent(self, request: AgentRegistrationRequest) -> AgentDefinition:
        agent = self.a2a.register_agent(request)
        await self.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.AGENT_REGISTERED,
                agent_id=agent.agent_id,
                payload=agent.model_dump(mode="json"),
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
        return await self.memory_manager.store(request)

    async def search_memory(self, request: MemorySearchRequest) -> list[MemorySearchResult]:
        return await self.memory_manager.search(request)

    async def get_recent_memory(self, agent_id: str, limit: int = 10) -> list[MemoryRecord]:
        return await self.memory_manager.get_recent(agent_id, limit)

    async def discover_agents(self) -> list[AgentPresence]:
        return self.a2a.list_agents()

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
        if request.session_id is None:
            session = await self.create_session()
            request = request.model_copy(update={"session_id": session.session_id})

        for tool_call in request.tool_calls:
            await self.call_tool(tool_call.tool_name, tool_call.arguments, agent_id=request.agent_id)

        adapter = self.agents.build_adapter(
            request.agent_id,
            browser=self.browser,
            sockets=self.sockets,
            sandbox=self.sandbox,
            safety=self.safety,
            memory_manager=self.memory_manager,
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
