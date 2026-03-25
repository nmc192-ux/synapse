from __future__ import annotations

import uuid

from synapse.models.runtime_event import EventSeverity, EventType
from synapse.models.task import TaskClaimRequest, TaskCreateRequest, TaskRecord, TaskRequest, TaskResult, TaskStatus, TaskUpdateRequest
from synapse.runtime.browser_service import BrowserService
from synapse.runtime.checkpoint_service import CheckpointService
from synapse.runtime.compression.base import CompressionProvider
from synapse.runtime.event_bus import EventBus
from synapse.runtime.llm import LLMProvider
from synapse.runtime.memory_service import MemoryService
from synapse.runtime.registry import AgentRegistry
from synapse.runtime.run_store import RunStore
from synapse.runtime.safety import AgentSafetyLayer, SecurityAlertError, SecurityFinding
from synapse.runtime.task_manager import TaskExecutionManager
from synapse.runtime.tool_service import ToolService
from synapse.models.run import RunState, RunStatus


class TaskRuntime:
    def __init__(
        self,
        agents: AgentRegistry,
        browser_service: BrowserService,
        tool_service: ToolService,
        memory_service: MemoryService,
        task_manager: TaskExecutionManager,
        checkpoint_service: CheckpointService,
        run_store: RunStore,
        events: EventBus,
        safety: AgentSafetyLayer,
        llm: LLMProvider | None = None,
        compression_provider: CompressionProvider | None = None,
    ) -> None:
        self.agents = agents
        self.browser_service = browser_service
        self.tool_service = tool_service
        self.memory_service = memory_service
        self.task_manager = task_manager
        self.checkpoint_service = checkpoint_service
        self.run_store = run_store
        self.events = events
        self.safety = safety
        self.llm = llm
        self.compression_provider = compression_provider

    async def create_task(self, request: TaskCreateRequest) -> TaskRecord:
        return await self.task_manager.create_task(request)

    async def claim_task(self, task_id: str, request: TaskClaimRequest) -> TaskRecord:
        return await self.task_manager.claim_task(task_id, request)

    async def update_task(self, task_id: str, request: TaskUpdateRequest) -> TaskRecord:
        return await self.task_manager.update_task(task_id, request)

    async def list_active_tasks(self) -> list[TaskRecord]:
        return await self.task_manager.list_active_tasks()

    async def execute_task(self, request: TaskRequest) -> TaskResult:
        run = await self._ensure_run(request)
        request = request.model_copy(update={"run_id": run.run_id})
        if hasattr(self.browser_service.budget_service, "ensure_run_budget"):
            await self.browser_service.budget_service.ensure_run_budget(request.agent_id, run.run_id)
        await self._enforce_task_safety(request)
        self.checkpoint_service.remember_task_context(request)
        if request.session_id is None:
            session = await self.browser_service.create_session(str(uuid.uuid4()), agent_id=request.agent_id, run_id=run.run_id)
            request = request.model_copy(update={"session_id": session.session_id})
            self.checkpoint_service.remember_task_context(request)

        for tool_call in request.tool_calls:
            await self.tool_service.call_tool(tool_call.tool_name, tool_call.arguments, agent_id=request.agent_id)

        adapter = self.agents.build_adapter(
            request.agent_id,
            browser=self.browser_service.browser,
            sockets=self.events.sockets,
            sandbox=self.browser_service.sandbox,
            safety=self.safety,
            memory_service=self.memory_service,
            budget_service=self.browser_service.budget_service,
            llm=self.llm,
            compression_provider=self.compression_provider,
        )
        try:
            result = await adapter.execute_task(request)
        except Exception:
            await self.run_store.update_status(
                run.run_id,
                RunStatus.FAILED,
                current_phase="failed",
                metadata={"session_id": request.session_id},
            )
            raise
        final_result = result.model_copy(
            update={
                "run_id": run.run_id,
                "status": result.status if result.status != TaskStatus.PENDING else TaskStatus.RUNNING,
                "artifacts": {**result.artifacts, "session_id": request.session_id, "run_id": run.run_id},
            }
        )
        run_status = RunStatus.COMPLETED if final_result.status == TaskStatus.COMPLETED else RunStatus.RUNNING
        await self.run_store.update_status(
            run.run_id,
            run_status,
            current_phase="completed" if run_status == RunStatus.COMPLETED else "running",
            metadata={"session_id": request.session_id},
        )
        await self.events.emit(
            EventType.TASK_UPDATED,
            run_id=run.run_id,
            session_id=request.session_id,
            agent_id=request.agent_id,
            task_id=request.task_id,
            source="task_runtime",
            payload=final_result.model_dump(mode="json"),
            correlation_id=run.correlation_id or request.task_id,
        )
        if request.session_id is not None and self.checkpoint_service.state_store is not None:
            await self.browser_service.save_session_state(
                request.session_id,
                agent_id=request.agent_id,
                task_id=request.task_id,
                run_id=run.run_id,
            )
        return final_result

    async def resume_task(self, checkpoint_id: str) -> TaskResult:
        checkpoint, request = await self.checkpoint_service.resume_context(checkpoint_id)
        result = await self.execute_task(request)
        await self.checkpoint_service.emit_resumed(checkpoint, result)
        return result

    async def list_runs(self, agent_id: str | None = None, task_id: str | None = None) -> list[RunState]:
        return await self.run_store.list(agent_id=agent_id, task_id=task_id)

    async def get_run(self, run_id: str) -> RunState:
        return await self.run_store.get(run_id)

    async def pause_run(self, run_id: str) -> RunState:
        run = await self.run_store.update_status(run_id, RunStatus.PAUSED, current_phase="paused")
        if run.checkpoint_id is None:
            checkpoint = await self.checkpoint_service.save_checkpoint(
                run.task_id,
                {
                    "agent_id": run.agent_id,
                    "run_id": run.run_id,
                    "current_goal": str(run.metadata.get("goal", "")),
                },
            )
            run = await self.run_store.update_status(run_id, RunStatus.PAUSED, checkpoint_id=checkpoint.checkpoint_id)
        return run

    async def resume_run(self, run_id: str) -> TaskResult:
        run = await self.run_store.get(run_id)
        if run.checkpoint_id is None:
            raise KeyError(f"Run has no checkpoint to resume: {run_id}")
        await self.run_store.update_status(run_id, RunStatus.RESUMED, current_phase="resumed")
        checkpoint, request = await self.checkpoint_service.resume_context(run.checkpoint_id)
        request = request.model_copy(update={"run_id": run_id})
        result = await self.execute_task(request)
        await self.checkpoint_service.emit_resumed(checkpoint, result)
        return result

    async def cancel_run(self, run_id: str) -> RunState:
        return await self.run_store.update_status(run_id, RunStatus.CANCELLED, current_phase="cancelled")

    async def _ensure_run(self, request: TaskRequest) -> RunState:
        if request.run_id:
            run = await self.run_store.get(request.run_id)
            return await self.run_store.update_status(
                run.run_id,
                RunStatus.RUNNING if run.status == RunStatus.PENDING else run.status,
                current_phase="starting" if run.current_phase is None else run.current_phase,
                metadata={"goal": request.goal},
            )
        return await self.run_store.create_run(
            task_id=request.task_id,
            agent_id=request.agent_id,
            correlation_id=request.task_id,
            metadata={"goal": request.goal},
        )

    async def _enforce_task_safety(self, request: TaskRequest) -> None:
        finding = self.safety.validate_task(request)
        if finding is not None:
            await self._raise_security_alert(request.agent_id, request.session_id, finding)

    async def _raise_security_alert(
        self,
        agent_id: str | None,
        session_id: str | None,
        finding: SecurityFinding,
    ) -> None:
        await self.events.emit(
            EventType.SECURITY_ALERT,
            run_id=getattr(finding, "metadata", {}).get("run_id") if isinstance(getattr(finding, "metadata", None), dict) else None,
            session_id=session_id,
            agent_id=agent_id,
            task_id=getattr(finding, "metadata", {}).get("task_id") if isinstance(getattr(finding, "metadata", None), dict) else None,
            source="task_runtime",
            payload=finding.model_dump(mode="json"),
            severity=EventSeverity.ERROR,
        )
        raise SecurityAlertError(finding)
