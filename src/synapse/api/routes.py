from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from synapse.models.a2a import A2AEnvelope, AgentPresence
from synapse.models.agent import AgentDefinition
from synapse.models.browser import (
    BrowserState,
    ClickRequest,
    ExtractionResult,
    ExtractRequest,
    OpenRequest,
    ScreenshotRequest,
    ScreenshotResult,
    TypeRequest,
)
from synapse.models.events import EventType, RuntimeEvent
from synapse.models.message import AgentMessage
from synapse.models.plugin import PluginDescriptor, PluginReloadRequest, ToolDescriptor
from synapse.models.task import ExtractionRequest, NavigationRequest, TaskRequest
from synapse.runtime.orchestrator import RuntimeOrchestrator
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
    return await orchestrator.navigate(request)


@router.post("/browser/open", response_model=BrowserState)
async def open_page(
    request: OpenRequest,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> BrowserState:
    return await orchestrator.open(request)


@router.post("/browser/click", response_model=BrowserState)
async def click(
    request: ClickRequest,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> BrowserState:
    return await orchestrator.click(request)


@router.post("/browser/type", response_model=BrowserState)
async def type_text(
    request: TypeRequest,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> BrowserState:
    return await orchestrator.type(request)


@router.post("/extract", response_model=ExtractionResult)
async def extract(
    request: ExtractionRequest,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> ExtractionResult:
    return await orchestrator.extract(request)


@router.post("/browser/extract", response_model=ExtractionResult)
async def structured_extract(
    request: ExtractRequest,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> ExtractionResult:
    return await orchestrator.structured_extract(request)


@router.post("/browser/screenshot", response_model=ScreenshotResult)
async def screenshot(
    request: ScreenshotRequest,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> ScreenshotResult:
    return await orchestrator.screenshot(request)


@router.post("/agents", response_model=AgentDefinition)
async def register_agent(
    request: AgentDefinition,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> AgentDefinition:
    return await orchestrator.register_agent(request)


@router.get("/agents", response_model=list[AgentDefinition])
async def list_agents(orchestrator: RuntimeOrchestrator = Depends(get_orchestrator)) -> list[AgentDefinition]:
    return orchestrator.agents.list()


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


@router.get("/tools", response_model=list[ToolDescriptor])
async def list_tools(orchestrator: RuntimeOrchestrator = Depends(get_orchestrator)) -> list[ToolDescriptor]:
    return await orchestrator.list_tools()


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
    return await orchestrator.execute_task(request)


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
            response = await runtime.a2a.handle_message(agent_id, payload)
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
