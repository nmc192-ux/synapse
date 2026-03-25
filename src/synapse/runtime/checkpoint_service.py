from __future__ import annotations

from datetime import datetime, timezone

from synapse.models.runtime_event import EventType
from synapse.models.runtime_state import RuntimeCheckpoint
from synapse.models.task import TaskRequest
from synapse.runtime.browser_service import BrowserService
from synapse.runtime.event_bus import EventBus
from synapse.runtime.state_store import RuntimeStateStore


class CheckpointService:
    def __init__(
        self,
        state_store: RuntimeStateStore | None,
        browser_service: BrowserService,
        events: EventBus,
    ) -> None:
        self.state_store = state_store
        self.browser_service = browser_service
        self.events = events
        self._task_context: dict[str, TaskRequest] = {}

    def set_state_store(self, state_store: RuntimeStateStore | None) -> None:
        self.state_store = state_store

    def remember_task_context(self, request: TaskRequest) -> None:
        self._task_context[request.task_id] = request

    async def save_checkpoint(self, task_id: str, state: dict[str, object]) -> RuntimeCheckpoint:
        context = self._task_context.get(task_id)
        agent_id = str(state.get("agent_id") or (context.agent_id if context is not None else ""))
        run_id = str(state.get("run_id") or (context.run_id if context is not None and context.run_id is not None else "")) or None
        if not agent_id:
            raise KeyError(f"Unable to resolve agent for task: {task_id}")

        checkpoint = RuntimeCheckpoint(
            task_id=task_id,
            agent_id=agent_id,
            run_id=run_id,
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
        await self.events.emit(
            EventType.CHECKPOINT_SAVED,
            run_id=checkpoint.run_id,
            agent_id=checkpoint.agent_id,
            task_id=checkpoint.task_id,
            session_id=checkpoint.browser_session_reference,
            source="checkpoint_service",
            phase="checkpoint",
            payload=checkpoint.model_dump(mode="json"),
            correlation_id=checkpoint.checkpoint_id,
        )
        return checkpoint

    async def list_checkpoints(
        self,
        agent_id: str | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
    ) -> list[RuntimeCheckpoint]:
        if self.state_store is None:
            return []
        rows = await self.state_store.list_checkpoints(agent_id=agent_id, task_id=task_id)
        if run_id is not None:
            rows = [row for row in rows if row.get("run_id") == run_id]
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

    async def resume_context(self, checkpoint_id: str) -> tuple[RuntimeCheckpoint, TaskRequest]:
        checkpoint = await self.get_checkpoint(checkpoint_id)
        if checkpoint.browser_session_reference:
            await self.browser_service.restore_session_state(
                checkpoint.browser_session_reference,
                agent_id=checkpoint.agent_id,
                checkpoint_id=checkpoint_id,
                run_id=checkpoint.run_id,
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
            run_id=checkpoint.run_id,
            session_id=checkpoint.browser_session_reference,
            constraints=constraints,
        )
        return checkpoint, request

    async def emit_resumed(self, checkpoint: RuntimeCheckpoint, result) -> None:
        await self.events.emit(
            EventType.CHECKPOINT_RESUMED,
            run_id=checkpoint.run_id,
            agent_id=checkpoint.agent_id,
            task_id=checkpoint.task_id,
            session_id=checkpoint.browser_session_reference,
            source="checkpoint_service",
            phase="checkpoint",
            payload={
                "checkpoint_id": checkpoint.checkpoint_id,
                "task_id": checkpoint.task_id,
                "result": result.model_dump(mode="json"),
            },
            correlation_id=checkpoint.checkpoint_id,
        )
