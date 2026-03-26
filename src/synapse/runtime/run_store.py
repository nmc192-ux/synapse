from __future__ import annotations

from datetime import datetime, timedelta, timezone

from synapse.models.agent import AgentBudgetUsage
from synapse.models.run import RunGraph, RunGraphEdge, RunGraphNode, RunState, RunStatus
from synapse.models.runtime_event import RunReplayView, RunTimeline, RunTimelineEntry, infer_event_phase
from synapse.models.runtime_state import BrowserNetworkEntry, BrowserTraceEntry, RunLeaseRecord
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
        project_id: str | None = None,
        correlation_id: str | None = None,
        parent_run_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> RunState:
        run = RunState(
            task_id=task_id,
            agent_id=agent_id,
            project_id=project_id,
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

    async def set_operator_intervention(
        self,
        run_id: str,
        *,
        intervention: dict[str, object],
        status: RunStatus = RunStatus.WAITING_FOR_OPERATOR,
        checkpoint_id: str | None = None,
    ) -> RunState:
        run = await self.get(run_id)
        history = run.metadata.get("operator_intervention_history")
        if not isinstance(history, list):
            history = []
        history = [*history, intervention]
        metadata = {
            "operator_intervention": intervention,
            "operator_intervention_history": history,
        }
        return await self.update_status(
            run_id,
            status,
            checkpoint_id=checkpoint_id,
            current_phase="operator_intervention",
            metadata=metadata,
        )

    async def save_lease(self, lease: RunLeaseRecord) -> RunLeaseRecord:
        if self.state_store is not None:
            await self.state_store.store_run_lease(lease.run_id, lease.model_dump(mode="json"))
        return lease

    async def renew_lease(
        self,
        run_id: str,
        *,
        lease_timeout_seconds: float,
        worker_id: str | None = None,
        reset_acquired_at: bool = False,
    ) -> RunLeaseRecord:
        lease = await self.get_lease(run_id)
        if lease is None:
            raise KeyError(f"Run lease not found: {run_id}")
        now = datetime.now(timezone.utc)
        updates: dict[str, object] = {
            "lease_expiration": now + timedelta(seconds=lease_timeout_seconds),
        }
        if worker_id is not None:
            updates["worker_id"] = worker_id
        if reset_acquired_at:
            updates["lease_acquired_at"] = now
        lease = lease.model_copy(update=updates)
        return await self.save_lease(lease)

    async def get_lease(self, run_id: str) -> RunLeaseRecord | None:
        if self.state_store is None:
            return None
        payload = await self.state_store.get_run_lease(run_id)
        if payload is None:
            return None
        return RunLeaseRecord.model_validate(payload)

    async def list_leases(self, worker_id: str | None = None) -> list[RunLeaseRecord]:
        if self.state_store is None:
            return []
        rows = await self.state_store.list_run_leases(worker_id=worker_id)
        return [RunLeaseRecord.model_validate(row) for row in rows]

    async def list_expired_leases(
        self,
        *,
        now: datetime | None = None,
        worker_id: str | None = None,
    ) -> list[RunLeaseRecord]:
        reference = now or datetime.now(timezone.utc)
        leases = await self.list_leases(worker_id=worker_id)
        return [lease for lease in leases if lease.lease_expiration <= reference]

    async def delete_lease(self, run_id: str) -> None:
        if self.state_store is not None:
            await self.state_store.delete_run_lease(run_id)

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

    async def get_trace(self, run_id: str, limit: int = 500) -> list[BrowserTraceEntry]:
        await self.get(run_id)
        raw_events = await self._get_run_events(run_id, limit=limit)
        trace_entries: list[BrowserTraceEntry] = []
        for event in raw_events:
            entry = self._to_trace_entry(run_id, event)
            if entry is not None:
                trace_entries.append(entry)
        return trace_entries

    async def get_network(self, run_id: str, limit: int = 500) -> list[BrowserNetworkEntry]:
        await self.get(run_id)
        raw_events = await self._get_run_events(run_id, limit=limit)
        network_entries: list[BrowserNetworkEntry] = []
        for event in raw_events:
            entry = self._to_network_entry(run_id, event)
            if entry is not None:
                network_entries.append(entry)
        return network_entries

    async def get_graph(self, run_id: str) -> RunGraph:
        runs = await self.list()
        run_map = {run.run_id: run for run in runs}
        root = await self.get(run_id)
        while root.parent_run_id is not None and root.parent_run_id in run_map:
            root = run_map[root.parent_run_id]

        descendant_ids = self._collect_descendants(root.run_id, run_map)
        ordered_runs = [run_map[node_run_id] for node_run_id in descendant_ids]

        nodes = [self._to_graph_node(run) for run in ordered_runs]
        edges = [self._to_graph_edge(run_map[run_id]) for run_id in descendant_ids if run_map[run_id].parent_run_id is not None]

        completed_runs = sum(1 for run in ordered_runs if run.status == RunStatus.COMPLETED)
        failed_runs = sum(1 for run in ordered_runs if run.status == RunStatus.FAILED)
        active_runs = sum(
            1
            for run in ordered_runs
            if run.status in {
                RunStatus.PENDING,
                RunStatus.RUNNING,
                RunStatus.RESUMED,
                RunStatus.PAUSED,
                RunStatus.WAITING_FOR_OPERATOR,
            }
        )

        return RunGraph(
            root_run_id=root.run_id,
            nodes=nodes,
            edges=edges,
            total_runs=len(nodes),
            completed_runs=completed_runs,
            failed_runs=failed_runs,
            active_runs=active_runs,
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

    @staticmethod
    def _collect_descendants(root_run_id: str, run_map: dict[str, RunState]) -> list[str]:
        ordered: list[str] = []
        stack = [root_run_id]
        while stack:
            current = stack.pop(0)
            if current in ordered:
                continue
            ordered.append(current)
            children = sorted(
                [run.run_id for run in run_map.values() if run.parent_run_id == current],
                key=lambda child_run_id: run_map[child_run_id].started_at,
            )
            stack.extend(children)
        return ordered

    @staticmethod
    def _to_graph_node(run: RunState) -> RunGraphNode:
        delegation_state = None
        if run.parent_run_id is not None:
            delegation_state = "delegated"
        if isinstance(run.metadata.get("delegated_run_id"), str):
            delegation_state = "delegating"
        return RunGraphNode(
            run_id=run.run_id,
            task_id=run.task_id,
            agent_id=run.agent_id,
            project_id=run.project_id,
            status=run.status,
            parent_run_id=run.parent_run_id,
            current_phase=run.current_phase,
            started_at=run.started_at,
            updated_at=run.updated_at,
            completed_at=run.completed_at,
            delegation_state=delegation_state,
            metadata=dict(run.metadata),
        )

    @staticmethod
    def _to_graph_edge(run: RunState) -> RunGraphEdge:
        metadata = dict(run.metadata)
        return RunGraphEdge(
            source_run_id=str(run.parent_run_id),
            target_run_id=run.run_id,
            edge_type="delegation",
            status=run.status.value,
            delegated_to_agent_id=run.agent_id,
            required_capability=str(metadata.get("required_capability")) if metadata.get("required_capability") is not None else None,
            created_at=run.started_at,
            metadata=metadata,
        )

    @staticmethod
    def _to_trace_entry(run_id: str, event: dict[str, object]) -> BrowserTraceEntry | None:
        event_type = str(event.get("event_type", ""))
        payload = event.get("payload", {}) if isinstance(event.get("payload"), dict) else {}
        timestamp = event.get("timestamp")
        if event_type not in {
            "browser.console.logged",
            "browser.network.failed",
            "browser.navigation.traced",
            "browser.popup.opened",
            "popup.dismissed",
            "download.completed",
            "upload.completed",
            "page.navigated",
            "browser.error",
        }:
            return None
        category = {
            "browser.console.logged": "console",
            "browser.network.failed": "network",
            "browser.navigation.traced": "navigation",
            "browser.popup.opened": "popup",
            "popup.dismissed": "popup",
            "download.completed": "download",
            "upload.completed": "upload",
            "page.navigated": "navigation",
            "browser.error": "error",
        }[event_type]
        message = payload.get("message")
        if not isinstance(message, str):
            message = payload.get("error") if isinstance(payload.get("error"), str) else None
        url = payload.get("url")
        if not isinstance(url, str):
            url = payload.get("page_url") if isinstance(payload.get("page_url"), str) else None
        level = payload.get("level")
        if not isinstance(level, str):
            level = str(event.get("severity", "info"))
        return BrowserTraceEntry(
            event_id=str(event.get("event_id")),
            run_id=run_id,
            session_id=str(event.get("session_id")) if event.get("session_id") is not None else None,
            timestamp=datetime.fromisoformat(str(timestamp)) if isinstance(timestamp, str) else datetime.now(timezone.utc),
            event_type=event_type,
            category=category,
            level=level,
            message=message,
            url=url,
            metadata=payload,
        )

    @staticmethod
    def _to_network_entry(run_id: str, event: dict[str, object]) -> BrowserNetworkEntry | None:
        if str(event.get("event_type", "")) != "browser.network.failed":
            return None
        payload = event.get("payload", {}) if isinstance(event.get("payload"), dict) else {}
        url = payload.get("url")
        if not isinstance(url, str):
            return None
        timestamp = event.get("timestamp")
        return BrowserNetworkEntry(
            event_id=str(event.get("event_id")),
            run_id=run_id,
            session_id=str(event.get("session_id")) if event.get("session_id") is not None else None,
            timestamp=datetime.fromisoformat(str(timestamp)) if isinstance(timestamp, str) else datetime.now(timezone.utc),
            url=url,
            method=str(payload.get("method")) if payload.get("method") is not None else None,
            resource_type=str(payload.get("resource_type")) if payload.get("resource_type") is not None else None,
            failure_text=str(payload.get("failure_text")) if payload.get("failure_text") is not None else None,
            status=str(payload.get("status", "failed")),
            metadata=payload,
        )
