from __future__ import annotations

from datetime import datetime, timezone

from synapse.models.run import RunState, RunStatus
from synapse.runtime.state_store import RuntimeStateStore


class RunStore:
    def __init__(self, state_store: RuntimeStateStore | None) -> None:
        self.state_store = state_store
        self._in_memory_runs: dict[str, RunState] = {}

    def set_state_store(self, state_store: RuntimeStateStore | None) -> None:
        self.state_store = state_store

    async def create_run(
        self,
        *,
        task_id: str,
        agent_id: str,
        correlation_id: str | None = None,
        parent_run_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> RunState:
        run = RunState(
            task_id=task_id,
            agent_id=agent_id,
            status=RunStatus.RUNNING,
            correlation_id=correlation_id,
            parent_run_id=parent_run_id,
            metadata=metadata or {},
        )
        await self.save(run)
        return run

    async def save(self, run: RunState) -> RunState:
        run.updated_at = datetime.now(timezone.utc)
        self._in_memory_runs[run.run_id] = run.model_copy(deep=True)
        if self.state_store is not None:
            await self.state_store.store_run(run.run_id, run.model_dump(mode="json"))
        return run

    async def get(self, run_id: str) -> RunState:
        if self.state_store is not None:
            payload = await self.state_store.get_run(run_id)
            if payload is not None:
                run = RunState.model_validate(payload)
                self._in_memory_runs[run_id] = run
                return run
        run = self._in_memory_runs.get(run_id)
        if run is None:
            raise KeyError(f"Run not found: {run_id}")
        return run.model_copy(deep=True)

    async def list(
        self,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
    ) -> list[RunState]:
        if self.state_store is not None:
            rows = await self.state_store.list_runs(agent_id=agent_id, task_id=task_id)
            return [RunState.model_validate(row) for row in rows]
        runs = list(self._in_memory_runs.values())
        if agent_id is not None:
            runs = [run for run in runs if run.agent_id == agent_id]
        if task_id is not None:
            runs = [run for run in runs if run.task_id == task_id]
        return [run.model_copy(deep=True) for run in runs]

    async def update_status(
        self,
        run_id: str,
        status: RunStatus,
        *,
        checkpoint_id: str | None = None,
        current_step: int | None = None,
        current_phase: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> RunState:
        run = await self.get(run_id)
        run.status = status
        if checkpoint_id is not None:
            run.checkpoint_id = checkpoint_id
        if current_step is not None:
            run.current_step = current_step
        if current_phase is not None:
            run.current_phase = current_phase
        if metadata:
            run.metadata.update(metadata)
        if status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}:
            run.completed_at = datetime.now(timezone.utc)
        return await self.save(run)
