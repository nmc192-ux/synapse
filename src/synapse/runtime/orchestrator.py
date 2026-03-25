import uuid

from synapse.models.a2a import A2AEnvelope, AgentPresence
from synapse.models.agent import AgentDefinition
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
    ) -> None:
        self.browser = browser
        self.agents = agents
        self.tools = tools
        self.messages = messages
        self.a2a = a2a
        self.memory_manager = memory_manager
        self.task_manager = task_manager
        self.sockets = sockets

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
        session = await self.browser.navigate(request.session_id, str(request.url))
        await self.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.PAGE_NAVIGATED,
                session_id=session.session_id,
                payload={"url": session.current_url},
            )
        )
        return session

    async def open(self, request: OpenRequest) -> BrowserState:
        state = await self.browser.open(request.session_id, str(request.url))
        await self.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.PAGE_NAVIGATED,
                session_id=state.session_id,
                payload=state.model_dump(mode="json"),
            )
        )
        return state

    async def click(self, request: ClickRequest) -> BrowserState:
        state = await self.browser.click(request.session_id, request.selector)
        await self.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.PAGE_NAVIGATED,
                session_id=state.session_id,
                payload={"action": "click", "selector": request.selector, **state.model_dump(mode="json")},
            )
        )
        return state

    async def type(self, request: TypeRequest) -> BrowserState:
        state = await self.browser.type(request.session_id, request.selector, request.text)
        await self.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.PAGE_NAVIGATED,
                session_id=state.session_id,
                payload={"action": "type", "selector": request.selector, **state.model_dump(mode="json")},
            )
        )
        return state

    async def extract(self, request: ExtractionRequest) -> ExtractionResult:
        payload = await self.browser.extract(request.session_id, request.selector, request.attribute)
        await self.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.DATA_EXTRACTED,
                session_id=request.session_id,
                payload=payload.model_dump(mode="json"),
            )
        )
        return payload

    async def structured_extract(self, request: ExtractRequest) -> ExtractionResult:
        payload = await self.browser.extract(request.session_id, request.selector, request.attribute)
        await self.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.DATA_EXTRACTED,
                session_id=request.session_id,
                payload=payload.model_dump(mode="json"),
            )
        )
        return payload

    async def screenshot(self, request: ScreenshotRequest) -> ScreenshotResult:
        result = await self.browser.screenshot(request.session_id)
        await self.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.SCREENSHOT_CAPTURED,
                session_id=request.session_id,
                payload={"action": "screenshot", **result.model_dump(mode="json")},
            )
        )
        return result

    async def get_layout(self, request: LayoutRequest) -> StructuredPageModel:
        return await self.browser.get_layout(request.session_id)

    async def find_element(self, request: FindElementRequest) -> list[PageElementMatch]:
        return await self.browser.find_element(request.session_id, request.type, request.text)

    async def inspect(self, request: InspectRequest) -> PageInspection:
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

    async def call_tool(self, tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
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

    async def create_task_record(self, request: TaskCreateRequest) -> TaskRecord:
        return await self.task_manager.create_task(request)

    async def claim_task(self, task_id: str, request: TaskClaimRequest) -> TaskRecord:
        return await self.task_manager.claim_task(task_id, request)

    async def update_task_record(self, task_id: str, request: TaskUpdateRequest) -> TaskRecord:
        return await self.task_manager.update_task(task_id, request)

    async def list_active_tasks(self) -> list[TaskRecord]:
        return await self.task_manager.list_active_tasks()

    async def execute_task(self, request: TaskRequest) -> TaskResult:
        if request.session_id is None:
            session = await self.create_session()
            request = request.model_copy(update={"session_id": session.session_id})

        for tool_call in request.tool_calls:
            await self.call_tool(tool_call.tool_name, tool_call.arguments)

        adapter = self.agents.build_adapter(
            request.agent_id,
            browser=self.browser,
            sockets=self.sockets,
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
