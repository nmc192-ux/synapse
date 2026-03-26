from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status

from synapse.models.a2a import (
    A2AEnvelope,
    AgentDelegateRequest,
    AgentPresence,
    AgentRegistrationRequest,
    AgentWireMessage,
)
from synapse.models.agent import AgentBudgetUsage, AgentCheckpoint, AgentDefinition, AgentDiscoveryEntry
from synapse.models.capability import CapabilityAdvertisementRequest, CapabilityRecord
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
    PageElementMatch,
    PageInspection,
    OpenRequest,
    ScreenshotRequest,
    ScreenshotResult,
    ScrollExtractRequest,
    ScrollExtractResult,
    StructuredPageModel,
    TypeRequest,
    UploadRequest,
    UploadResult,
)
from synapse.models.runtime_event import EventType, RunReplayView, RunTimeline, RuntimeEvent
from synapse.models.message import AgentMessage
from synapse.models.memory import MemoryRecord, MemorySearchRequest, MemorySearchResult, MemoryStoreRequest
from synapse.models.plugin import PluginDescriptor, PluginReloadRequest, ToolDescriptor
from synapse.models.run import RunState
from synapse.models.runtime_state import BrowserNetworkEntry, BrowserSessionState, BrowserTraceEntry, ConnectionState, RuntimeCheckpoint
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
from synapse.runtime.session_profiles import SessionProfile, SessionProfileCreateRequest, SessionProfileLoadRequest
from synapse.runtime.budget import AgentBudgetLimitExceeded
from synapse.runtime.security import SandboxPermissionError, SandboxRateLimitError
from synapse.runtime.safety import SecurityAlertError
from synapse.runtime.session import BrowserSession
from synapse.security.auth import AuthPrincipal, authenticate_websocket, get_authenticator, require_scopes
from synapse.security.policies import Scope


router = APIRouter()

BrowserControlPrincipal = Annotated[AuthPrincipal, Depends(require_scopes(Scope.BROWSER_CONTROL.value))]
TasksReadPrincipal = Annotated[AuthPrincipal, Depends(require_scopes(Scope.TASKS_READ.value))]
TasksWritePrincipal = Annotated[AuthPrincipal, Depends(require_scopes(Scope.TASKS_WRITE.value))]
MemoryReadPrincipal = Annotated[AuthPrincipal, Depends(require_scopes(Scope.MEMORY_READ.value))]
MemoryWritePrincipal = Annotated[AuthPrincipal, Depends(require_scopes(Scope.MEMORY_WRITE.value))]
A2ASendPrincipal = Annotated[AuthPrincipal, Depends(require_scopes(Scope.A2A_SEND.value))]
A2AReceivePrincipal = Annotated[AuthPrincipal, Depends(require_scopes(Scope.A2A_RECEIVE.value))]
AdminPrincipal = Annotated[AuthPrincipal, Depends(require_scopes(Scope.ADMIN.value))]


def get_orchestrator() -> RuntimeOrchestrator:
    from synapse.main import orchestrator

    return orchestrator


@router.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/sessions", response_model=BrowserSession)
async def create_session(_principal: BrowserControlPrincipal, orchestrator: RuntimeOrchestrator = Depends(get_orchestrator)) -> BrowserSession:
    return await orchestrator.create_session()


@router.post("/navigate", response_model=BrowserSession)
async def navigate(
    request: NavigationRequest,
    _principal: BrowserControlPrincipal,
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
    except AgentBudgetLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.post("/browser/open", response_model=BrowserState)
async def open_page(
    request: OpenRequest,
    _principal: BrowserControlPrincipal,
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
    except AgentBudgetLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.post("/browser/click", response_model=BrowserState)
async def click(
    request: ClickRequest,
    _principal: BrowserControlPrincipal,
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
    except AgentBudgetLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.post("/browser/type", response_model=BrowserState)
async def type_text(
    request: TypeRequest,
    _principal: BrowserControlPrincipal,
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
    except AgentBudgetLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.post("/extract", response_model=ExtractionResult)
async def extract(
    request: ExtractionRequest,
    _principal: BrowserControlPrincipal,
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
    except AgentBudgetLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.post("/browser/extract", response_model=ExtractionResult)
async def structured_extract(
    request: ExtractRequest,
    _principal: BrowserControlPrincipal,
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
    except AgentBudgetLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.post("/browser/screenshot", response_model=ScreenshotResult)
async def screenshot(
    request: ScreenshotRequest,
    _principal: BrowserControlPrincipal,
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
    except AgentBudgetLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.post("/browser/layout", response_model=StructuredPageModel)
async def get_layout(
    request: LayoutRequest,
    _principal: BrowserControlPrincipal,
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
    except AgentBudgetLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.post("/browser/find", response_model=list[PageElementMatch])
async def find_element(
    request: FindElementRequest,
    _principal: BrowserControlPrincipal,
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
    except AgentBudgetLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.post("/browser/inspect", response_model=PageInspection)
async def inspect(
    request: InspectRequest,
    _principal: BrowserControlPrincipal,
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


@router.post("/browser/dismiss", response_model=BrowserState)
async def dismiss_popups(
    request: DismissRequest,
    _principal: BrowserControlPrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> BrowserState:
    try:
        return await orchestrator.dismiss_popups(request)
    except SecurityAlertError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SandboxPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SandboxRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.post("/browser/upload", response_model=UploadResult)
async def upload(
    request: UploadRequest,
    _principal: BrowserControlPrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> UploadResult:
    try:
        return await orchestrator.upload(request)
    except SecurityAlertError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SandboxPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SandboxRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.post("/browser/download", response_model=DownloadResult)
async def download(
    request: DownloadRequest,
    _principal: BrowserControlPrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> DownloadResult:
    try:
        return await orchestrator.download(request)
    except SecurityAlertError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SandboxPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SandboxRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.post("/browser/scroll_extract", response_model=ScrollExtractResult)
async def scroll_extract(
    request: ScrollExtractRequest,
    _principal: BrowserControlPrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> ScrollExtractResult:
    try:
        return await orchestrator.scroll_extract(request)
    except SecurityAlertError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SandboxPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SandboxRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.post("/agents", response_model=AgentDefinition)
async def register_agent(
    request: AgentDefinition,
    _principal: AdminPrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> AgentDefinition:
    return await orchestrator.register_agent(request)


@router.get("/agents", response_model=list[AgentDefinition])
async def list_agents(_principal: TasksReadPrincipal, orchestrator: RuntimeOrchestrator = Depends(get_orchestrator)) -> list[AgentDefinition]:
    return await orchestrator.get_persisted_agents()


@router.get("/agents/{agent_id}/budget", response_model=AgentBudgetUsage)
async def get_agent_budget(
    agent_id: str,
    _principal: TasksReadPrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> AgentBudgetUsage:
    try:
        return await orchestrator.get_agent_budget(agent_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/agents/{agent_id}/checkpoint", response_model=AgentCheckpoint)
async def save_agent_checkpoint(
    agent_id: str,
    state: dict[str, object],
    _principal: AdminPrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> AgentCheckpoint:
    try:
        return await orchestrator.save_agent_checkpoint(agent_id, state)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/agents/register", response_model=AgentDefinition)
async def register_a2a_agent(
    request: AgentRegistrationRequest,
    _principal: AdminPrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> AgentDefinition:
    return await orchestrator.register_a2a_agent(request)


@router.get("/agents/discover", response_model=list[AgentPresence])
async def discover_a2a_agents(
    _principal: A2AReceivePrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> list[AgentPresence]:
    return await orchestrator.discover_agents()


@router.get("/agents/find", response_model=list[AgentDiscoveryEntry])
async def find_agents(
    capability: str,
    _principal: A2AReceivePrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> list[AgentDiscoveryEntry]:
    return await orchestrator.find_agents(capability)


@router.post("/agents/capabilities", response_model=CapabilityRecord)
async def advertise_agent_capabilities(
    request: CapabilityAdvertisementRequest,
    _principal: AdminPrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> CapabilityRecord:
    return await orchestrator.advertise_capabilities(request)


@router.get("/agents/capabilities", response_model=list[CapabilityRecord])
async def list_agent_capabilities(
    _principal: TasksReadPrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> list[CapabilityRecord]:
    return await orchestrator.list_capabilities()


@router.post("/agents/message", response_model=AgentWireMessage)
async def send_agent_message_wire(
    request: AgentWireMessage,
    _principal: A2ASendPrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> AgentWireMessage:
    try:
        return await orchestrator.send_agent_wire_message(request)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/agents/delegate", response_model=AgentWireMessage)
async def delegate_agent_task(
    request: AgentDelegateRequest,
    _principal: A2ASendPrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> AgentWireMessage:
    try:
        return await orchestrator.delegate_agent_task(request)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/agents/{agent_id}", response_model=AgentDefinition)
async def get_agent(
    agent_id: str,
    _principal: TasksReadPrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> AgentDefinition:
    try:
        return await orchestrator.get_persisted_agent(agent_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/agents/{agent_id}/status")
async def get_agent_status(
    agent_id: str,
    _principal: TasksReadPrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> dict[str, object]:
    try:
        return await orchestrator.get_agent_status(agent_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/a2a/agents", response_model=list[AgentPresence])
async def discover_agents(
    _principal: A2AReceivePrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> list[AgentPresence]:
    return await orchestrator.discover_agents()


@router.post("/a2a/messages", response_model=A2AEnvelope)
async def send_a2a_message(
    request: A2AEnvelope,
    _principal: A2ASendPrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> A2AEnvelope:
    try:
        return await orchestrator.send_a2a(request)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/messages", response_model=AgentMessage)
async def send_message(
    request: AgentMessage,
    _principal: A2ASendPrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> AgentMessage:
    return await orchestrator.send_message(request)


@router.get("/messages", response_model=list[AgentMessage])
async def list_messages(_principal: A2AReceivePrincipal, orchestrator: RuntimeOrchestrator = Depends(get_orchestrator)) -> list[AgentMessage]:
    return orchestrator.messages.list_messages()


@router.post("/memory/store", response_model=MemoryRecord)
async def store_memory(
    request: MemoryStoreRequest,
    _principal: MemoryWritePrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> MemoryRecord:
    try:
        return await orchestrator.store_memory(request)
    except AgentBudgetLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.post("/memory/search", response_model=list[MemorySearchResult])
async def search_memory(
    request: MemorySearchRequest,
    _principal: MemoryReadPrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> list[MemorySearchResult]:
    return await orchestrator.search_memory(request)


@router.get("/memory/{agent_id}/recent", response_model=list[MemoryRecord])
async def get_recent_memory(
    agent_id: str,
    _principal: MemoryReadPrincipal,
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
    _principal: BrowserControlPrincipal,
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
    except AgentBudgetLimitExceeded as exc:
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
    _principal: TasksWritePrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
):
    try:
        return await orchestrator.execute_task(request)
    except SecurityAlertError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except AgentBudgetLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.post("/tasks/create", response_model=TaskRecord)
async def create_task(
    request: TaskCreateRequest,
    _principal: TasksWritePrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> TaskRecord:
    return await orchestrator.create_task_record(request)


@router.post("/tasks/{task_id}/claim", response_model=TaskRecord)
async def claim_task(
    task_id: str,
    request: TaskClaimRequest,
    _principal: TasksWritePrincipal,
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
    _principal: TasksWritePrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> TaskRecord:
    try:
        return await orchestrator.update_task_record(task_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/tasks/active", response_model=list[TaskRecord])
async def list_active_tasks(
    _principal: TasksReadPrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> list[TaskRecord]:
    return await orchestrator.list_active_tasks()


@router.get("/sessions", response_model=list[BrowserSessionState])
async def list_sessions(
    _principal: BrowserControlPrincipal,
    agent_id: str | None = None,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> list[BrowserSessionState]:
    return await orchestrator.list_sessions(agent_id=agent_id)


@router.get("/sessions/{session_id}", response_model=BrowserSessionState)
async def get_session(
    session_id: str,
    _principal: BrowserControlPrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> BrowserSessionState:
    try:
        return await orchestrator.get_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/profiles/create", response_model=SessionProfile)
async def create_session_profile(
    request: SessionProfileCreateRequest,
    _principal: BrowserControlPrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> SessionProfile:
    try:
        return await orchestrator.create_session_profile(request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/profiles/{profile_id}/load", response_model=SessionProfile)
async def load_session_profile(
    profile_id: str,
    request: SessionProfileLoadRequest,
    _principal: BrowserControlPrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> SessionProfile:
    try:
        return await orchestrator.load_session_profile(profile_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/profiles", response_model=list[SessionProfile])
async def list_session_profiles(
    _principal: TasksReadPrincipal,
    agent_id: str | None = None,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> list[SessionProfile]:
    return await orchestrator.list_session_profiles(agent_id=agent_id)


@router.delete("/profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session_profile(
    profile_id: str,
    _principal: BrowserControlPrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> None:
    try:
        await orchestrator.delete_session_profile(profile_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/connections", response_model=list[ConnectionState])
async def list_connections(
    _principal: TasksReadPrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> list[ConnectionState]:
    return await orchestrator.list_connections()


@router.get("/connections/{agent_id}", response_model=ConnectionState)
async def get_connection(
    agent_id: str,
    _principal: TasksReadPrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> ConnectionState:
    try:
        return await orchestrator.get_connection(agent_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/checkpoints", response_model=list[RuntimeCheckpoint])
async def list_checkpoints(
    _principal: TasksReadPrincipal,
    agent_id: str | None = None,
    task_id: str | None = None,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> list[RuntimeCheckpoint]:
    return await orchestrator.list_checkpoints(agent_id=agent_id, task_id=task_id)


@router.get("/checkpoints/{checkpoint_id}", response_model=RuntimeCheckpoint)
async def get_checkpoint(
    checkpoint_id: str,
    _principal: TasksReadPrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> RuntimeCheckpoint:
    try:
        return await orchestrator.get_checkpoint(checkpoint_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/runs", response_model=list[RunState])
async def list_runs(
    _principal: TasksReadPrincipal,
    agent_id: str | None = None,
    task_id: str | None = None,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> list[RunState]:
    return await orchestrator.list_runs(agent_id=agent_id, task_id=task_id)


@router.get("/runs/{run_id}", response_model=RunState)
async def get_run(
    run_id: str,
    _principal: TasksReadPrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> RunState:
    try:
        return await orchestrator.get_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/runs/{run_id}/events")
async def get_run_events(
    run_id: str,
    _principal: TasksReadPrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> list[dict[str, object]]:
    return await orchestrator.get_run_events(run_id)


@router.get("/runs/{run_id}/timeline", response_model=RunTimeline)
async def get_run_timeline(
    run_id: str,
    _principal: TasksReadPrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> RunTimeline:
    try:
        return await orchestrator.get_run_timeline(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/runs/{run_id}/replay", response_model=RunReplayView)
async def get_run_replay(
    run_id: str,
    _principal: TasksReadPrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> RunReplayView:
    try:
        return await orchestrator.get_run_replay(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/runs/{run_id}/trace", response_model=list[BrowserTraceEntry])
async def get_run_trace(
    run_id: str,
    _principal: TasksReadPrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> list[BrowserTraceEntry]:
    try:
        return await orchestrator.get_run_trace(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/runs/{run_id}/network", response_model=list[BrowserNetworkEntry])
async def get_run_network(
    run_id: str,
    _principal: TasksReadPrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> list[BrowserNetworkEntry]:
    try:
        return await orchestrator.get_run_network(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/runs/{run_id}/checkpoints", response_model=list[RuntimeCheckpoint])
async def get_run_checkpoints(
    run_id: str,
    _principal: TasksReadPrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> list[RuntimeCheckpoint]:
    return await orchestrator.get_run_checkpoints(run_id)


@router.get("/runs/{run_id}/children", response_model=list[RunState])
async def get_child_runs(
    run_id: str,
    _principal: TasksReadPrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> list[RunState]:
    return await orchestrator.get_child_runs(run_id)


@router.post("/runs/{run_id}/pause", response_model=RunState)
async def pause_run(
    run_id: str,
    _principal: TasksWritePrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> RunState:
    try:
        return await orchestrator.pause_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/runs/{run_id}/resume")
async def resume_run(
    run_id: str,
    _principal: TasksWritePrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
):
    try:
        return await orchestrator.resume_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/runs/{run_id}/cancel", response_model=RunState)
async def cancel_run(
    run_id: str,
    _principal: TasksWritePrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> RunState:
    try:
        return await orchestrator.cancel_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/checkpoint", response_model=RuntimeCheckpoint)
async def save_task_checkpoint(
    task_id: str,
    state: dict[str, object],
    _principal: TasksWritePrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
) -> RuntimeCheckpoint:
    try:
        return await orchestrator.save_checkpoint(task_id, state)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/tasks/resume/{checkpoint_id}")
async def resume_task(
    checkpoint_id: str,
    _principal: TasksWritePrincipal,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
):
    try:
        return await orchestrator.resume_task(checkpoint_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.websocket("/ws")
async def websocket_events(
    websocket: WebSocket,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
    authenticator = Depends(get_authenticator),
) -> None:
    try:
        principal = authenticate_websocket(
            websocket,
            authenticator,
            required_scopes=(Scope.TASKS_READ.value,),
        )
    except HTTPException:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await orchestrator.sockets.connect(websocket, principal=principal)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        orchestrator.sockets.disconnect(websocket)


@router.websocket("/a2a/ws/{agent_id}")
async def websocket_a2a(
    websocket: WebSocket,
    agent_id: str,
    orchestrator: RuntimeOrchestrator = Depends(get_orchestrator),
    authenticator = Depends(get_authenticator),
) -> None:
    try:
        authenticate_websocket(
            websocket,
            authenticator,
            required_scopes=(Scope.A2A_RECEIVE.value,),
            agent_id=agent_id,
        )
    except HTTPException:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await orchestrator.a2a.connect(agent_id, websocket)
    try:
        while True:
            payload = await websocket.receive_json()
            await orchestrator.a2a.heartbeat(agent_id)
            wire_message = AgentWireMessage.model_validate({**payload, "agent": agent_id})
            response = await orchestrator.send_agent_wire_message(wire_message)
            await orchestrator.a2a.cleanup_stale_connections()
            if response is not None:
                await orchestrator.sockets.broadcast(
                    RuntimeEvent(
                        event_type=EventType.A2A_MESSAGE,
                        agent_id=agent_id,
                        task_id=(response.payload.get("task", {}) or {}).get("task_id") if isinstance(response.payload, dict) and isinstance(response.payload.get("task"), dict) else None,
                        source="api.websocket",
                        payload=response.model_dump(mode="json"),
                        correlation_id=response.correlation_id,
                    )
                )
    except WebSocketDisconnect:
        await orchestrator.a2a.disconnect(agent_id)
