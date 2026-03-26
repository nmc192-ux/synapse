from __future__ import annotations

import uuid

from synapse.models.a2a import A2AEnvelope, A2AMessageType, AgentDelegateRequest, AgentPresence, AgentRegistrationRequest, AgentWireMessage
from synapse.models.agent import AgentBudgetUsage, AgentCheckpoint, AgentDefinition, AgentDiscoveryEntry
from synapse.models.capability import CapabilityAdvertisementRequest, CapabilityRecord
from synapse.models.benchmark import BenchmarkReport, BenchmarkRunScore, BenchmarkScenario
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
from synapse.models.runtime_event import EventType, RunReplayView, RunTimeline
from synapse.models.message import AgentMessage
from synapse.models.memory import MemoryRecord, MemorySearchRequest, MemorySearchResult, MemoryStoreRequest
from synapse.models.platform import (
    APIKeyCreateRequest,
    APIKeyIssueResponse,
    APIKeyRecord,
    AuditLogRecord,
    AgentOwnership,
    AgentOwnershipRequest,
    Organization,
    OrganizationCreateRequest,
    PlatformUser,
    Project,
    ProjectCreateRequest,
    UserCreateRequest,
)
from synapse.models.plugin import PluginDescriptor, PluginReloadRequest, ToolDescriptor
from synapse.models.run import RunGraph, RunState, RunStatus
from synapse.models.runtime_state import (
    BrowserNetworkEntry,
    BrowserSessionState,
    BrowserTraceEntry,
    BrowserWorkerState,
    ConnectionState,
    OperatorInterventionRecord,
    OperatorInterventionState,
    RuntimeCheckpoint,
)
from synapse.models.task import ExtractionRequest, NavigationRequest, TaskClaimRequest, TaskCreateRequest, TaskRecord, TaskRequest, TaskResult, TaskUpdateRequest
from synapse.runtime.a2a import A2AHub
from synapse.runtime.benchmarking import BenchmarkSuite
from synapse.runtime.budget import AgentBudgetManager
from synapse.runtime.budget_service import BudgetService
from synapse.runtime.capabilities import CapabilityRegistry
from synapse.runtime.browser_service import BrowserService
from synapse.runtime.checkpoint_service import CheckpointService
from synapse.runtime.compression.base import CompressionProvider
from synapse.runtime.event_bus import EventBus
from synapse.runtime.llm import LLMProvider
from synapse.runtime.memory import AgentMemoryManager
from synapse.runtime.memory_service import MemoryService
from synapse.runtime.messaging import AgentMessageBus
from synapse.runtime.platform_service import PlatformService
from synapse.runtime.registry import AgentRegistry
from synapse.runtime.run_store import RunStore
from synapse.runtime.scheduler import RunScheduler
from synapse.runtime.security import AgentSecuritySandbox
from synapse.runtime.session_profiles import SessionProfile, SessionProfileCreateRequest, SessionProfileLoadRequest, SessionProfileManager
from synapse.runtime.safety import AgentSafetyLayer
from synapse.runtime.state_store import RuntimeStateStore
from synapse.runtime.task_manager import TaskExecutionManager
from synapse.runtime.task_runtime import TaskRuntime
from synapse.runtime.tool_service import ToolService
from synapse.runtime.tools import ToolRegistry
from synapse.security.auth import Authenticator
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
        session_profiles: SessionProfileManager | None = None,
        llm: LLMProvider | None = None,
        compression_provider: CompressionProvider | None = None,
        authenticator: Authenticator | None = None,
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
        self.session_profiles = session_profiles or SessionProfileManager(state_store=state_store)
        self.llm = llm
        self.compression_provider = compression_provider
        self.authenticator = authenticator

        self.event_bus = EventBus(sockets, compression_provider=compression_provider)
        self.event_bus.set_context_resolver(self._resolve_event_context)
        self.event_bus.add_listener(self._handle_runtime_event)
        self.session_profiles.set_event_publisher(self.event_bus.publish)
        if hasattr(browser, "set_event_publisher"):
            browser.set_event_publisher(self.event_bus.publish)
        self.run_store = RunStore(state_store)
        self.capabilities = CapabilityRegistry(agents)
        self.platform = PlatformService(state_store, authenticator, agents)
        if self.authenticator is not None:
            self.authenticator.set_api_key_validator(self.platform.authenticate_api_key_principal)
            self.authenticator.set_service_agent_authorizer(self.platform.can_service_act_for_agent)
        self.benchmarks = BenchmarkSuite(self.run_store, state_store)
        self.scheduler = RunScheduler(self.run_store, browser, self.event_bus)
        self.budget_service = BudgetService(budget_manager, agents, self.event_bus, self.run_store)
        self.browser_service = BrowserService(browser, sandbox, safety, self.event_bus, self.budget_service, state_store)
        self.memory_service = MemoryService(
            memory_manager,
            self.budget_service,
            state_store=state_store,
            events=self.event_bus,
            compression_provider=compression_provider,
        )
        self.tool_service = ToolService(
            tools,
            sandbox,
            safety,
            self.event_bus,
            self.budget_service,
            state_store=state_store,
            execution_plane=browser,
        )
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
            scheduler=self.scheduler,
            a2a=a2a,
        )
        if hasattr(self.a2a, "set_event_publisher"):
            self.a2a.set_event_publisher(self.event_bus.publish)

    @property
    def state_store(self) -> RuntimeStateStore | None:
        return self._state_store

    @state_store.setter
    def state_store(self, state_store: RuntimeStateStore | None) -> None:
        self._state_store = state_store
        self.run_store.set_state_store(state_store) if hasattr(self, "run_store") else None
        if hasattr(self, "benchmarks"):
            self.benchmarks.state_store = state_store
        self.session_profiles.set_state_store(state_store) if hasattr(self, "session_profiles") else None
        self.platform.set_state_store(state_store) if hasattr(self, "platform") else None
        self.browser_service.set_state_store(state_store) if hasattr(self, "browser_service") else None
        self.checkpoint_service.set_state_store(state_store) if hasattr(self, "checkpoint_service") else None
        self.memory_service.set_state_store(state_store) if hasattr(self, "memory_service") else None
        self.tool_service.set_state_store(state_store) if hasattr(self, "tool_service") else None
        self.tools.set_state_store(state_store) if hasattr(self.tools, "set_state_store") else None
        self.tool_service.set_execution_plane(self.browser) if hasattr(self, "tool_service") else None
        self.sandbox.set_state_store(state_store) if hasattr(self, "sandbox") else None
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

    async def advertise_capabilities(self, request: CapabilityAdvertisementRequest) -> CapabilityRecord:
        record = await self.capabilities.advertise(request)
        await self.event_bus.emit(
            EventType.AGENT_REGISTERED,
            agent_id=record.agent_id,
            source="runtime_controller",
            payload=record.model_dump(mode="json"),
        )
        return record

    async def list_capabilities(self) -> list[CapabilityRecord]:
        return await self.capabilities.list_capabilities()

    async def call_tool(self, tool_name: str, arguments: dict[str, object], agent_id: str | None) -> dict[str, object]:
        run_id = arguments.get("run_id") if isinstance(arguments.get("run_id"), str) else None
        return await self.tool_service.call_tool(tool_name, arguments, agent_id, run_id=run_id)

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

    async def create_organization(self, request: OrganizationCreateRequest) -> Organization:
        return await self.platform.create_organization(request)

    async def list_organizations(self) -> list[Organization]:
        return await self.platform.list_organizations()

    async def create_project(self, request: ProjectCreateRequest) -> Project:
        return await self.platform.create_project(request)

    async def list_projects(self, organization_id: str | None = None) -> list[Project]:
        return await self.platform.list_projects(organization_id=organization_id)

    async def create_user(self, request: UserCreateRequest) -> PlatformUser:
        return await self.platform.create_user(request)

    async def list_users(self, organization_id: str | None = None, project_id: str | None = None) -> list[PlatformUser]:
        return await self.platform.list_users(organization_id=organization_id, project_id=project_id)

    async def create_api_key(self, request: APIKeyCreateRequest) -> APIKeyIssueResponse:
        return await self.platform.create_api_key(request)

    async def list_api_keys(self, project_id: str | None = None) -> list[APIKeyRecord]:
        return await self.platform.list_api_keys(project_id=project_id)

    async def assign_agent_ownership(self, agent_id: str, request: AgentOwnershipRequest) -> AgentOwnership:
        return await self.platform.assign_agent_ownership(agent_id, request)

    async def get_agent_ownership(self, agent_id: str) -> AgentOwnership | None:
        return await self.platform.get_agent_ownership(agent_id)

    async def log_audit_action(
        self,
        *,
        actor_id: str,
        actor_type: str,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        project_id: str | None = None,
        organization_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> AuditLogRecord:
        return await self.platform.log_audit_action(
            actor_id=actor_id,
            actor_type=actor_type,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            project_id=project_id,
            organization_id=organization_id,
            metadata=metadata,
        )

    async def list_audit_logs(self, project_id: str | None = None, limit: int = 100) -> list[AuditLogRecord]:
        return await self.platform.list_audit_logs(project_id=project_id, limit=limit)

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

    async def list_worker_health(self) -> list[ConnectionState | BrowserWorkerState]:
        if hasattr(self.browser, "list_workers"):
            return self.browser.list_workers()
        return []

    async def get_session(self, session_id: str) -> BrowserSessionState:
        return await self.browser_service.get_session(session_id)

    async def create_session_profile(self, request: SessionProfileCreateRequest) -> SessionProfile:
        return await self.session_profiles.create_profile(request)

    async def load_session_profile(self, profile_id: str, request: SessionProfileLoadRequest) -> SessionProfile:
        return await self.session_profiles.load_profile(profile_id, run_id=request.run_id)

    async def list_session_profiles(self, agent_id: str | None = None) -> list[SessionProfile]:
        return await self.session_profiles.list_profiles(agent_id=agent_id)

    async def delete_session_profile(self, profile_id: str) -> None:
        await self.session_profiles.delete_profile(profile_id)

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

    async def get_child_runs(self, run_id: str) -> list[RunState]:
        return await self.task_runtime.list_child_runs(run_id)

    async def get_run_events(self, run_id: str) -> list[dict[str, object]]:
        if self.state_store is None:
            return []
        return await self.state_store.get_runtime_events(run_id=run_id, limit=200)

    async def _resolve_event_context(self, event) -> dict[str, object]:
        resolved: dict[str, object] = {}
        run = None
        session = None
        agent = None

        if event.run_id is not None:
            try:
                run = await self.run_store.get(event.run_id)
            except KeyError:
                run = None

        if run is None and event.session_id is not None:
            try:
                session = await self.browser_service.get_session(event.session_id)
            except KeyError:
                session = None
            else:
                if event.run_id is None and session.run_id is not None:
                    resolved["run_id"] = session.run_id
                    try:
                        run = await self.run_store.get(session.run_id)
                    except KeyError:
                        run = None
                if event.agent_id is None and session.agent_id is not None:
                    resolved["agent_id"] = session.agent_id
                if event.project_id is None and session.project_id is not None:
                    resolved["project_id"] = session.project_id

        agent_id = resolved.get("agent_id") if isinstance(resolved.get("agent_id"), str) else event.agent_id
        if run is not None:
            if event.agent_id is None and "agent_id" not in resolved:
                resolved["agent_id"] = run.agent_id
                agent_id = run.agent_id
            if event.task_id is None:
                resolved["task_id"] = run.task_id
            if event.project_id is None and run.project_id is not None and "project_id" not in resolved:
                resolved["project_id"] = run.project_id
            if event.correlation_id is None and run.correlation_id is not None:
                resolved["correlation_id"] = run.correlation_id

        if isinstance(agent_id, str):
            try:
                agent = self.agents.get(agent_id)
            except KeyError:
                agent = None
            else:
                if event.organization_id is None and agent.organization_id is not None:
                    resolved["organization_id"] = agent.organization_id
                if event.project_id is None and resolved.get("project_id") is None and agent.project_id is not None:
                    resolved["project_id"] = agent.project_id

        if event.organization_id is None and resolved.get("organization_id") is None:
            project_id = resolved.get("project_id") if isinstance(resolved.get("project_id"), str) else event.project_id
            if isinstance(project_id, str):
                try:
                    project = await self.platform.get_project(project_id)
                except Exception:
                    project = None
                if project is not None:
                    resolved["organization_id"] = project.organization_id

        return resolved

    async def get_run_timeline(self, run_id: str, limit: int = 500) -> RunTimeline:
        return await self.run_store.get_timeline(run_id, limit=limit)

    async def get_run_replay(self, run_id: str, limit: int = 500) -> RunReplayView:
        checkpoints = [
            checkpoint.model_dump(mode="json")
            for checkpoint in await self.checkpoint_service.list_checkpoints(run_id=run_id)
        ]
        return await self.run_store.get_replay(run_id, checkpoints=checkpoints, limit=limit)

    async def get_run_graph(self, run_id: str) -> RunGraph:
        return await self.run_store.get_graph(run_id)

    async def get_run_trace(self, run_id: str, limit: int = 500) -> list[BrowserTraceEntry]:
        return await self.run_store.get_trace(run_id, limit=limit)

    async def get_run_network(self, run_id: str, limit: int = 500) -> list[BrowserNetworkEntry]:
        return await self.run_store.get_network(run_id, limit=limit)

    def get_benchmark_scenarios(
        self,
        fixture_base_url: str,
        *,
        agent_id: str = "benchmark-agent",
        delegate_agent_id: str = "analysis-agent",
    ) -> list[BenchmarkScenario]:
        return self.benchmarks.default_fixture_scenarios(
            fixture_base_url,
            agent_id=agent_id,
            delegate_agent_id=delegate_agent_id,
        )

    async def score_run_benchmark(
        self,
        run_id: str,
        *,
        scenarios: list[BenchmarkScenario] | None = None,
    ) -> BenchmarkRunScore:
        scenario_map = {scenario.scenario_id: scenario for scenario in scenarios or []}
        return await self.benchmarks.score_run(run_id, scenario_map=scenario_map)

    async def build_benchmark_report(
        self,
        run_ids: list[str],
        *,
        suite_name: str = "synapse-fixture-benchmarks",
        fixture_base_url: str | None = None,
        scenarios: list[BenchmarkScenario] | None = None,
    ) -> BenchmarkReport:
        return await self.benchmarks.build_report(
            run_ids,
            suite_name=suite_name,
            fixture_base_url=fixture_base_url,
            scenarios=scenarios,
        )

    async def get_run_checkpoints(self, run_id: str) -> list[RuntimeCheckpoint]:
        return await self.checkpoint_service.list_checkpoints(run_id=run_id)

    async def pause_run(self, run_id: str) -> RunState:
        return await self.task_runtime.pause_run(run_id)

    async def resume_run(self, run_id: str):
        return await self.task_runtime.resume_run(run_id)

    async def cancel_run(self, run_id: str) -> RunState:
        return await self.task_runtime.cancel_run(run_id)

    async def list_interventions(
        self,
        *,
        project_id: str | None = None,
        run_id: str | None = None,
        state: OperatorInterventionState | str | None = None,
    ) -> list[OperatorInterventionRecord]:
        return await self.run_store.list_interventions(project_id=project_id, run_id=run_id, state=state)

    async def get_intervention(self, intervention_id: str) -> OperatorInterventionRecord:
        return await self.run_store.get_intervention(intervention_id)

    async def approve_run(
        self,
        run_id: str,
        *,
        operator_id: str | None = None,
        intervention_id: str | None = None,
    ):
        intervention = await self._resolve_intervention(run_id, intervention_id=intervention_id)
        return await self.approve_intervention(intervention.intervention_id, operator_id=operator_id)

    async def reject_run(
        self,
        run_id: str,
        *,
        operator_id: str | None = None,
        reason: str | None = None,
        intervention_id: str | None = None,
    ) -> RunState:
        intervention = await self._resolve_intervention(run_id, intervention_id=intervention_id)
        return await self.reject_intervention(intervention.intervention_id, operator_id=operator_id, reason=reason)

    async def provide_run_input(
        self,
        run_id: str,
        *,
        operator_id: str | None = None,
        input_payload: dict[str, object] | None = None,
        intervention_id: str | None = None,
    ) -> RunState:
        intervention = await self._resolve_intervention(run_id, intervention_id=intervention_id)
        return await self.provide_intervention_input(
            intervention.intervention_id,
            operator_id=operator_id,
            input_payload=input_payload,
        )

    async def approve_intervention(
        self,
        intervention_id: str,
        *,
        operator_id: str | None = None,
    ):
        intervention = await self.run_store.get_intervention(intervention_id)
        run = await self.run_store.get(intervention.run_id)
        operator_context = {
            "decision": "approved",
            "operator_id": operator_id,
            "intervention_id": intervention.intervention_id,
            "reason": intervention.reason,
            "input": intervention.payload.get("operator_input", {}),
            "payload": intervention.payload,
        }
        updated_intervention = await self.run_store.update_intervention(
            intervention_id,
            state=OperatorInterventionState.APPROVED,
            payload={"operator_id": operator_id, "operator_decision": "approved"},
            resolved=True,
        )
        await self.run_store.update_status(
            run.run_id,
            RunStatus.RESUMED,
            current_phase="operator_approved",
            metadata={
                "operator_decision": "approved",
                "operator_decision_at": run.updated_at.isoformat(),
                "operator_id": operator_id,
                "operator_context": operator_context,
                "operator_intervention": updated_intervention.model_dump(mode="json"),
            },
        )
        await self.event_bus.emit(
            EventType.INTERVENTION_RESOLVED,
            organization_id=updated_intervention.organization_id,
            project_id=updated_intervention.project_id,
            run_id=run.run_id,
            agent_id=run.agent_id,
            task_id=run.task_id,
            source="runtime_controller",
            payload={
                "intervention": updated_intervention.model_dump(mode="json"),
                "operator_context": operator_context,
                "ui": {"status": "approved", "resume_requested": True},
            },
            correlation_id=run.correlation_id,
        )
        if run.checkpoint_id is not None:
            return await self.task_runtime.resume_run(run.run_id, operator_context=operator_context)
        return await self.run_store.get(run.run_id)

    async def reject_intervention(
        self,
        intervention_id: str,
        *,
        operator_id: str | None = None,
        reason: str | None = None,
    ) -> RunState:
        intervention = await self.run_store.get_intervention(intervention_id)
        run = await self.run_store.get(intervention.run_id)
        updated_intervention = await self.run_store.update_intervention(
            intervention_id,
            state=OperatorInterventionState.REJECTED,
            payload={"operator_id": operator_id, "operator_decision": "rejected", "reason": reason},
            resolved=True,
        )
        rejected = await self.run_store.update_status(
            run.run_id,
            RunStatus.CANCELLED,
            current_phase="operator_rejected",
            metadata={
                "operator_decision": "rejected",
                "operator_decision_reason": reason,
                "operator_id": operator_id,
                "operator_context": {
                    "decision": "rejected",
                    "operator_id": operator_id,
                    "intervention_id": intervention_id,
                    "reason": reason or intervention.reason,
                },
                "operator_intervention": updated_intervention.model_dump(mode="json"),
            },
        )
        await self.event_bus.emit(
            EventType.INTERVENTION_RESOLVED,
            organization_id=updated_intervention.organization_id,
            project_id=updated_intervention.project_id,
            run_id=run.run_id,
            agent_id=rejected.agent_id,
            task_id=rejected.task_id,
            source="runtime_controller",
            payload={
                "intervention": updated_intervention.model_dump(mode="json"),
                "reason": reason,
                "ui": {"status": "rejected", "resume_requested": False},
            },
            correlation_id=rejected.correlation_id,
        )
        return rejected

    async def provide_intervention_input(
        self,
        intervention_id: str,
        *,
        operator_id: str | None = None,
        input_payload: dict[str, object] | None = None,
    ) -> RunState:
        intervention = await self.run_store.get_intervention(intervention_id)
        run = await self.run_store.get(intervention.run_id)
        operator_input_history = run.metadata.get("operator_input_history")
        if not isinstance(operator_input_history, list):
            operator_input_history = []
        entry = {
            "operator_id": operator_id,
            "input": input_payload or {},
            "timestamp": run.updated_at.isoformat(),
        }
        updated = await self.run_store.update_metadata(
            run.run_id,
            {
                "operator_input": entry,
                "operator_input_history": [*operator_input_history, entry],
                "operator_context": {
                    "decision": "input_provided",
                    "operator_id": operator_id,
                    "intervention_id": intervention_id,
                    "input": input_payload or {},
                },
            },
        )
        updated_intervention = await self.run_store.update_intervention(
            intervention_id,
            state=OperatorInterventionState.INPUT_PROVIDED,
            payload={"operator_id": operator_id, "operator_input": input_payload or {}, "operator_input_entry": entry},
        )
        updated = await self.run_store.update_metadata(
            run.run_id,
            {"operator_intervention": updated_intervention.model_dump(mode="json")},
        )
        await self.event_bus.emit(
            EventType.INTERVENTION_UPDATED,
            organization_id=updated_intervention.organization_id,
            project_id=updated_intervention.project_id,
            run_id=updated.run_id,
            agent_id=updated.agent_id,
            task_id=updated.task_id,
            source="runtime_controller",
            payload={
                "intervention": updated_intervention.model_dump(mode="json"),
                "input": input_payload or {},
                "ui": {"status": "input_provided"},
            },
            correlation_id=updated.correlation_id,
        )
        return updated

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
        return await self.capabilities.find(capability)

    async def _handle_runtime_event(self, event) -> None:
        intervention_events = {
            EventType.APPROVAL_REQUIRED,
            EventType.BROWSER_CAPTCHA_DETECTED,
            EventType.BROWSER_CHALLENGE_DETECTED,
            EventType.BROWSER_HUMAN_INTERVENTION_REQUIRED,
        }
        if event.run_id is None or event.event_type not in intervention_events:
            return
        try:
            run = await self.run_store.get(event.run_id)
        except KeyError:
            return
        if run.status == RunStatus.CANCELLED:
            return
        checkpoint_id = run.checkpoint_id
        if checkpoint_id is None:
            checkpoint = await self.checkpoint_service.save_checkpoint(
                run.task_id,
                {
                    "agent_id": run.agent_id,
                    "run_id": run.run_id,
                    "current_goal": str(run.metadata.get("goal", "")),
                    "memory_snapshot_reference": str(run.metadata.get("memory_snapshot_reference", "")) or None,
                },
            )
            checkpoint_id = checkpoint.checkpoint_id
        intervention = self.safety.build_operator_intervention_payload(
            event_type=event.event_type.value,
            run_id=event.run_id,
            agent_id=event.agent_id,
            task_id=event.task_id,
            payload=event.payload,
            source=event.source,
        )
        intervention_record = OperatorInterventionRecord(
            run_id=event.run_id,
            project_id=run.project_id,
            organization_id=event.organization_id,
            agent_id=event.agent_id,
            task_id=event.task_id,
            checkpoint_id=checkpoint_id,
            reason=str(intervention["reason"]),
            payload={
                **intervention,
                "checkpoint_id": checkpoint_id,
                "ui": {
                    "operator_required": True,
                    "action_label": "Review Run",
                    "reason": intervention["reason"],
                    "category": intervention["category"],
                    "run_context": {
                        "run_id": run.run_id,
                        "task_id": run.task_id,
                        "agent_id": run.agent_id,
                        "goal": run.metadata.get("goal"),
                    },
                },
            },
        )
        await self.run_store.set_operator_intervention(
            event.run_id,
            intervention=intervention_record,
            checkpoint_id=checkpoint_id,
        )
        await self.event_bus.emit(
            EventType.INTERVENTION_QUEUED,
            organization_id=event.organization_id,
            project_id=run.project_id,
            run_id=run.run_id,
            agent_id=run.agent_id,
            task_id=run.task_id,
            source="runtime_controller",
            payload=intervention_record.model_dump(mode="json"),
            correlation_id=run.correlation_id,
        )

    async def _resolve_intervention(
        self,
        run_id: str,
        *,
        intervention_id: str | None = None,
    ) -> OperatorInterventionRecord:
        if intervention_id is not None:
            return await self.run_store.get_intervention(intervention_id)
        latest = await self.run_store.latest_intervention_for_run(run_id)
        if latest is None:
            raise KeyError(f"No intervention found for run: {run_id}")
        return latest

    @staticmethod
    def _operator_intervention(run: RunState) -> dict[str, object]:
        payload = run.metadata.get("operator_intervention")
        return dict(payload) if isinstance(payload, dict) else {}

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
        target_agent = request.target_agent
        if target_agent is None:
            capability = request.payload.get("required_capability") if isinstance(request.payload, dict) else None
            if isinstance(capability, str):
                matches = await self.find_agents(capability)
                if matches:
                    target_agent = matches[0].id
        if target_agent is None:
            raise ValueError("No target agent available for delegation.")
        message = self.a2a.sign_wire_message(
            AgentWireMessage(
            type=A2AMessageType.TASK_REQUEST,
            agent=request.agent,
            target_agent=target_agent,
            payload=request.payload,
            )
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
