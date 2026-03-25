from __future__ import annotations

from datetime import datetime, timezone

from synapse.models.agent import AgentBudgetUsage
from synapse.models.run import RunState, RunStatus
from synapse.models.runtime_event import RunReplayView, RunTimeline, RunTimelineEntry, infer_event_phase
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

    async def update_budget(self, run_id: str, usage: AgentBudgetUsage) -> RunState:
        return await self.update_metadata(run_id, {"budget": usage.model_dump(mode="json")})

    async def get_budget(self, run_id: str) -> AgentBudgetUsage | None:
        run = await self.get(run_id)
        payload = run.metadata.get("budget")
        if not isinstance(payload, dict):
            return None
        return AgentBudgetUsage.model_validate(payload)

    async def update_metadata(self, run_id: str, metadata: dict[str, object]) -> RunState:
        run = await self.get(run_id)
        run.metadata.update(metadata)
        return await self.save(run)

    async def get_timeline(self, run_id: str, limit: int = 500) -> RunTimeline:
        run = await self.get(run_id)
        raw_events = await self._get_run_events(run_id, limit=limit)
        entries = [self._to_timeline_entry(run_id, event) for event in raw_events]
        phases = list(dict.fromkeys(entry.phase for entry in entries))
        return RunTimeline(
            run_id=run_id,
            status=run.status.value,
            started_at=run.started_at,
            updated_at=run.updated_at,
            event_count=len(entries),
            phases=phases,
            entries=entries,
        )

    async def get_replay(self, run_id: str, *, checkpoints: list[dict[str, object]] | None = None, limit: int = 500) -> RunReplayView:
        timeline = await self.get_timeline(run_id, limit=limit)
        phase_transitions: list[dict[str, object]] = []
        browser_actions: list[dict[str, object]] = []
        planner_outputs: list[dict[str, object]] = []
        evaluation_results: list[dict[str, object]] = []
        budget_updates: list[dict[str, object]] = []

        last_phase: str | None = None
        for entry in timeline.entries:
            if entry.phase != last_phase:
                phase_transitions.append(
                    {
                        "phase": entry.phase,
                        "timestamp": entry.timestamp,
                        "event_id": entry.event_id,
                        "event_type": entry.event_type,
                    }
                )
                last_phase = entry.phase
            if entry.event_type in {"page.navigated", "data.extracted", "screenshot.captured", "loop.acted", "tool.called", "download.completed", "upload.completed"}:
                browser_actions.append(entry.model_dump(mode="json"))
            if entry.event_type in {"loop.planned", "planner.context.compressed"}:
                planner_outputs.append(entry.model_dump(mode="json"))
            if entry.event_type == "loop.evaluated":
                evaluation_results.append(entry.model_dump(mode="json"))
            if entry.event_type == "budget.updated":
                budget_updates.append(entry.model_dump(mode="json"))

        replay_checkpoints = checkpoints if checkpoints is not None else []
        return RunReplayView(
            run_id=run_id,
            phase_transitions=phase_transitions,
            browser_actions=browser_actions,
            planner_outputs=planner_outputs,
            evaluation_results=evaluation_results,
            checkpoints=replay_checkpoints,
            budget_updates=budget_updates,
            timeline=timeline.entries,
        )

    async def _get_run_events(self, run_id: str, limit: int) -> list[dict[str, object]]:
        if self.state_store is None:
            return []
        rows = await self.state_store.get_runtime_events(run_id=run_id, limit=limit)
        return sorted(rows, key=self._event_sort_key)

    @staticmethod
    def _to_timeline_entry(run_id: str, event: dict[str, object]) -> RunTimelineEntry:
        timestamp = event.get("timestamp")
        phase = event.get("phase")
        return RunTimelineEntry(
            event_id=str(event.get("event_id")),
            run_id=run_id,
            timestamp=datetime.fromisoformat(str(timestamp)) if isinstance(timestamp, str) else datetime.now(timezone.utc),
            event_type=str(event.get("event_type")),
            phase=str(phase) if isinstance(phase, str) and phase else infer_event_phase(str(event.get("event_type"))),
            payload=event.get("payload", {}) if isinstance(event.get("payload"), dict) else {},
            correlation_id=str(event.get("correlation_id")) if event.get("correlation_id") is not None else None,
            source=str(event.get("source", "runtime")),
            severity=str(event.get("severity", "info")),
            task_id=str(event.get("task_id")) if event.get("task_id") is not None else None,
            session_id=str(event.get("session_id")) if event.get("session_id") is not None else None,
        )

    @staticmethod
    def _event_sort_key(event: dict[str, object]) -> tuple[str, str]:
        timestamp = str(event.get("timestamp", ""))
        event_id = str(event.get("event_id", ""))
        return (timestamp, event_id)
