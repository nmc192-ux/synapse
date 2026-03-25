from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect

from synapse.models.a2a import (
    A2AEnvelope,
    AgentDelegateRequest,
    AgentPresence,
    AgentRegistrationRequest,
    AgentWireMessage,
)
from synapse.models.agent import AgentDefinition, AgentDiscoveryEntry
from synapse.models.browser import (
    BrowserState,
    ClickRequest,
    ExtractionResult,
    ExtractRequest,
    FindElementRequest,
    InspectRequest,
    LayoutRequest,
    PageElementMatch,
    PageInspection,
    OpenRequest,
    ScreenshotRequest,
    ScreenshotResult,
    StructuredPageModel,
    TypeRequest,
)
from synapse.models.events import EventType, RuntimeEvent
from synapse.models.message import AgentMessage
from synapse.models.memory import MemoryRecord, MemorySearchRequest, MemorySearchResult, MemoryStoreRequest
from synapse.models.plugin import PluginDescriptor, PluginReloadRequest, ToolDescriptor
from synapse.models.task import (
    ExtractionRequest,
    NavigationRequest,
    TaskClaimRequest,
    TaskCreateRequest,
    TaskRecord,
    TaskRequest,
    TaskUpdateRequest,
    ToolCallRequest,
)
from synapse.runtime.orchestrator import RuntimeOrchestrator
from synapse.runtime.security import SandboxPermissionError, SandboxRateLimitError
from synapse.runtime.safety import SecurityAlertError
from synapse.runtime.session import BrowserSession


router = APIRouter()


def get_orchestrator() -> RuntimeOrchestrator:
    from synapse.main import orchestrator

    return orchestrator


@router.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/sessions", response_model=BrowserSession)
async def create_session(orchestrator: RuntimeOrchestrator = Depends(get_orchestrator)) -> BrowserSession:
    return await orchestrator.create_session()


@router.post("/navigate", response_model=BrowserSession)
async def navigate(
    request: NavigationRequest,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> BrowserSession:
    try:
        return await orchestrator.navigate(request)
    except SecurityAlertError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SandboxPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SandboxRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.post("/browser/open", response_model=BrowserState)
async def open_page(
    request: OpenRequest,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> BrowserState:
    try:
        return await orchestrator.open(request)
    except SecurityAlertError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SandboxPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SandboxRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.post("/browser/click", response_model=BrowserState)
async def click(
    request: ClickRequest,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> BrowserState:
    try:
        return await orchestrator.click(request)
    except SecurityAlertError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SandboxPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SandboxRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.post("/browser/type", response_model=BrowserState)
async def type_text(
    request: TypeRequest,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> BrowserState:
    try:
        return await orchestrator.type(request)
    except SecurityAlertError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SandboxPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SandboxRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.post("/extract", response_model=ExtractionResult)
async def extract(
    request: ExtractionRequest,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> ExtractionResult:
    try:
        return await orchestrator.extract(request)
    except SecurityAlertError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SandboxPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SandboxRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.post("/browser/extract", response_model=ExtractionResult)
async def structured_extract(
    request: ExtractRequest,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> ExtractionResult:
    try:
        return await orchestrator.structured_extract(request)
    except SecurityAlertError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SandboxPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SandboxRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.post("/browser/screenshot", response_model=ScreenshotResult)
async def screenshot(
    request: ScreenshotRequest,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> ScreenshotResult:
    try:
        return await orchestrator.screenshot(request)
    except SecurityAlertError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SandboxPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SandboxRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.post("/browser/layout", response_model=StructuredPageModel)
async def get_layout(
    request: LayoutRequest,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> StructuredPageModel:
    try:
        return await orchestrator.get_layout(request)
    except SecurityAlertError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SandboxPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SandboxRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.post("/browser/find", response_model=list[PageElementMatch])
async def find_element(
    request: FindElementRequest,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> list[PageElementMatch]:
    try:
        return await orchestrator.find_element(request)
    except SecurityAlertError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SandboxPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SandboxRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.post("/browser/inspect", response_model=PageInspection)
async def inspect(
    request: InspectRequest,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> PageInspection:
    try:
        return await orchestrator.inspect(request)
    except SecurityAlertError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SandboxPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SandboxRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.post("/agents", response_model=AgentDefinition)
async def register_agent(
    request: AgentDefinition,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> AgentDefinition:
    return await orchestrator.register_agent(request)


@router.get("/agents", response_model=list[AgentDefinition])
async def list_agents(orchestrator: RuntimeOrchestrator = Depends(get_orchestrator)) -> list[AgentDefinition]:
    return orchestrator.agents.list()


@router.post("/agents/register", response_model=AgentDefinition)
async def register_a2a_agent(
    request: AgentRegistrationRequest,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> AgentDefinition:
    return await orchestrator.register_a2a_agent(request)


@router.get("/agents/discover", response_model=list[AgentPresence])
async def discover_a2a_agents(
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> list[AgentPresence]:
    return await orchestrator.discover_agents()


@router.get("/agents/find", response_model=list[AgentDiscoveryEntry])
async def find_agents(
    capability: str,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> list[AgentDiscoveryEntry]:
    return await orchestrator.find_agents(capability)


@router.post("/agents/message", response_model=AgentWireMessage)
async def send_agent_message_wire(
    request: AgentWireMessage,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> AgentWireMessage:
    return await orchestrator.send_agent_wire_message(request)


@router.post("/agents/delegate", response_model=AgentWireMessage)
async def delegate_agent_task(
    request: AgentDelegateRequest,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> AgentWireMessage:
    return await orchestrator.delegate_agent_task(request)


@router.get("/a2a/agents", response_model=list[AgentPresence])
async def discover_agents(
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> list[AgentPresence]:
    return await orchestrator.discover_agents()


@router.post("/a2a/messages", response_model=A2AEnvelope)
async def send_a2a_message(
    request: A2AEnvelope,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> A2AEnvelope:
    return await orchestrator.send_a2a(request)


@router.post("/messages", response_model=AgentMessage)
async def send_message(
    request: AgentMessage,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> AgentMessage:
    return await orchestrator.send_message(request)


@router.get("/messages", response_model=list[AgentMessage])
async def list_messages(orchestrator: RuntimeOrchestrator = Depends(get_orchestrator)) -> list[AgentMessage]:
    return orchestrator.messages.list_messages()


@router.post("/memory/store", response_model=MemoryRecord)
async def store_memory(
    request: MemoryStoreRequest,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> MemoryRecord:
    return await orchestrator.store_memory(request)


@router.post("/memory/search", response_model=list[MemorySearchResult])
async def search_memory(
    request: MemorySearchRequest,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> list[MemorySearchResult]:
    return await orchestrator.search_memory(request)


@router.get("/memory/{agent_id}/recent", response_model=list[MemoryRecord])
async def get_recent_memory(
    agent_id: str,
    limit: int = 10,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> list[MemoryRecord]:
    return await orchestrator.get_recent_memory(agent_id, limit)


@router.get("/tools", response_model=list[ToolDescriptor])
async def list_tools(orchestrator: RuntimeOrchestrator = Depends(get_orchestrator)) -> list[ToolDescriptor]:
    return await orchestrator.list_tools()


@router.post("/tools/call")
async def call_tool(
    request: ToolCallRequest,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> dict[str, object]:
    try:
        return await orchestrator.call_tool(request.tool_name, request.arguments, agent_id=request.agent_id)
    except SecurityAlertError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SandboxPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SandboxRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.get("/plugins", response_model=list[PluginDescriptor])
async def list_plugins(orchestrator: RuntimeOrchestrator = Depends(get_orchestrator)) -> list[PluginDescriptor]:
    return await orchestrator.list_plugins()


@router.post("/plugins/reload", response_model=list[PluginDescriptor])
async def reload_plugins(
    request: PluginReloadRequest,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> list[PluginDescriptor]:
    return await orchestrator.reload_plugins(request)


@router.post("/tasks")
async def execute_task(
    request: TaskRequest,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
):
    try:
        return await orchestrator.execute_task(request)
    except SecurityAlertError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/tasks/create", response_model=TaskRecord)
async def create_task(
    request: TaskCreateRequest,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> TaskRecord:
    return await orchestrator.create_task_record(request)


@router.post("/tasks/{task_id}/claim", response_model=TaskRecord)
async def claim_task(
    task_id: str,
    request: TaskClaimRequest,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> TaskRecord:
    try:
        return await orchestrator.claim_task(task_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/update", response_model=TaskRecord)
async def update_task(
    task_id: str,
    request: TaskUpdateRequest,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> TaskRecord:
    try:
        return await orchestrator.update_task_record(task_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/tasks/active", response_model=list[TaskRecord])
async def list_active_tasks(
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> list[TaskRecord]:
    return await orchestrator.list_active_tasks()


@router.websocket("/ws")
async def websocket_events(websocket: WebSocket) -> None:
    runtime = get_orchestrator()
    await runtime.sockets.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        runtime.sockets.disconnect(websocket)


@router.websocket("/a2a/ws/{agent_id}")
async def websocket_a2a(websocket: WebSocket, agent_id: str) -> None:
    runtime = get_orchestrator()
    await runtime.a2a.connect(agent_id, websocket)
    try:
        while True:
            payload = await websocket.receive_json()
            wire_message = AgentWireMessage.model_validate({**payload, "agent": agent_id})
            response = await runtime.send_agent_wire_message(wire_message)
            if response is not None:
                await runtime.sockets.broadcast(
                    RuntimeEvent(
                        event_type=EventType.A2A_MESSAGE,
                        agent_id=agent_id,
                        payload=response.model_dump(mode="json"),
                    )
                )
    except WebSocketDisconnect:
        runtime.a2a.disconnect(agent_id)
