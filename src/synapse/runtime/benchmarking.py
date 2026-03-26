from __future__ import annotations

from datetime import datetime

from synapse.models.benchmark import (
    BenchmarkAggregate,
    BenchmarkCategory,
    BenchmarkReport,
    BenchmarkRunScore,
    BenchmarkScenario,
)
from synapse.models.loop import AgentAction, AgentActionType
from synapse.models.run import RunStatus
from synapse.runtime.run_store import RunStore
from synapse.runtime.state_store import RuntimeStateStore


class BenchmarkSuite:
    def __init__(
        self,
        run_store: RunStore,
        state_store: RuntimeStateStore | None = None,
    ) -> None:
        self.run_store = run_store
        self.state_store = state_store or run_store.state_store

    @staticmethod
    def default_fixture_scenarios(
        fixture_base_url: str,
        *,
        agent_id: str = "benchmark-agent",
        delegate_agent_id: str = "analysis-agent",
    ) -> list[BenchmarkScenario]:
        base = fixture_base_url.rstrip("/")
        return [
            BenchmarkScenario(
                scenario_id="fixture-extraction",
                name="Fixture Search Extraction",
                category=BenchmarkCategory.EXTRACTION,
                description="Search the fixture library and extract deterministic paper summaries.",
                entrypoint_url=f"{base}/search?q=agents",
                task_template={
                    "task_id": "benchmark-extraction",
                    "agent_id": agent_id,
                    "goal": "Open the search fixture, find paper summaries, and extract titles and authors.",
                    "start_url": f"{base}/search?q=agents",
                    "actions": [
                        AgentAction(action_id="open-search", type=AgentActionType.OPEN, url=f"{base}/search?q=agents").model_dump(mode="json"),
                        AgentAction(action_id="extract-results", type=AgentActionType.EXTRACT, selector=".search-result").model_dump(mode="json"),
                    ],
                },
                success_criteria=[
                    "At least one search-result card is extracted.",
                    "Paper title and author content are present in the extracted output.",
                ],
            ),
            BenchmarkScenario(
                scenario_id="fixture-form-completion",
                name="Fixture Form Completion",
                category=BenchmarkCategory.FORM_COMPLETION,
                description="Complete and submit the deterministic benchmark form.",
                entrypoint_url=f"{base}/form",
                task_template={
                    "task_id": "benchmark-form",
                    "agent_id": agent_id,
                    "goal": "Fill the benchmark form and submit it successfully.",
                    "start_url": f"{base}/form",
                },
                success_criteria=[
                    "Submitted confirmation page is reached.",
                    "Submitted fields match the entered fixture values.",
                ],
            ),
            BenchmarkScenario(
                scenario_id="fixture-spa-navigation",
                name="Fixture SPA Navigation",
                category=BenchmarkCategory.SPA_NAVIGATION,
                description="Navigate between SPA routes and confirm route-specific action output.",
                entrypoint_url=f"{base}/spa",
                task_template={
                    "task_id": "benchmark-spa",
                    "agent_id": agent_id,
                    "goal": "Navigate SPA routes and verify route changes without full reload.",
                    "start_url": f"{base}/spa",
                },
                success_criteria=[
                    "SPA route changes are observed.",
                    "Route action output is visible on the active route.",
                ],
            ),
            BenchmarkScenario(
                scenario_id="fixture-popups",
                name="Fixture Popup Dismissal",
                category=BenchmarkCategory.POPUPS,
                description="Dismiss fixture banners and modal overlays, then reach primary content.",
                entrypoint_url=f"{base}/popup",
                task_template={
                    "task_id": "benchmark-popups",
                    "agent_id": agent_id,
                    "goal": "Dismiss cookie banners and blocking modals to reach the underlying page content.",
                    "start_url": f"{base}/popup",
                },
                success_criteria=[
                    "Blocking modal is dismissed.",
                    "Cookie banner is dismissed.",
                    "Primary content status indicates all popups are cleared.",
                ],
            ),
            BenchmarkScenario(
                scenario_id="fixture-session-continuation",
                name="Fixture Session Continuation",
                category=BenchmarkCategory.SESSION_CONTINUATION,
                description="Sign in on the fixture login page and continue to the account history using persisted session state.",
                entrypoint_url=f"{base}/login",
                task_template={
                    "task_id": "benchmark-session",
                    "agent_id": agent_id,
                    "goal": "Authenticate on the fixture site and continue the session to the account history page.",
                    "start_url": f"{base}/login",
                },
                success_criteria=[
                    "Authenticated account page is reached.",
                    "Session cookies persist into the history page.",
                ],
            ),
            BenchmarkScenario(
                scenario_id="fixture-a2a-delegation",
                name="Fixture Delegated Browsing",
                category=BenchmarkCategory.A2A_DELEGATION,
                description="Delegate a search-and-extract browsing task to another agent through A2A messaging.",
                entrypoint_url=f"{base}/search?q=runtime",
                task_template={
                    "task_id": "benchmark-a2a",
                    "agent_id": agent_id,
                    "goal": "Delegate extraction of runtime benchmark search results to another agent.",
                    "start_url": f"{base}/search?q=runtime",
                    "delegate_to": delegate_agent_id,
                },
                success_criteria=[
                    "A2A delegation message is emitted.",
                    "Delegated agent completes the browsing task.",
                ],
                metadata={"delegated_agent_id": delegate_agent_id},
            ),
        ]

    async def build_report(
        self,
        run_ids: list[str],
        *,
        suite_name: str = "synapse-fixture-benchmarks",
        fixture_base_url: str | None = None,
        scenarios: list[BenchmarkScenario] | None = None,
    ) -> BenchmarkReport:
        scenario_map = {scenario.scenario_id: scenario for scenario in (scenarios or [])}
        scores = [await self.score_run(run_id, scenario_map=scenario_map) for run_id in run_ids]
        aggregate = self._aggregate(scores)
        return BenchmarkReport(
            suite_name=suite_name,
            fixture_base_url=fixture_base_url,
            scenarios=scenarios or [],
            scores=scores,
            aggregate=aggregate,
            metadata={"run_count": len(run_ids)},
        )

    async def score_run(
        self,
        run_id: str,
        *,
        scenario_map: dict[str, BenchmarkScenario] | None = None,
    ) -> BenchmarkRunScore:
        run = await self.run_store.get(run_id)
        budget = await self.run_store.get_budget(run_id)
        timeline = await self.run_store.get_timeline(run_id, limit=1000)
        scenario = self._scenario_for_run(run, scenario_map or {})
        events = timeline.entries
        compression_savings_ratio = self._compression_savings(events)
        retries = self._retry_count(events)
        operator_interventions = self._operator_intervention_count(events)
        delegated_messages = self._delegation_count(events)
        planning_iterations = sum(1 for event in events if event.event_type == "loop.planned")
        notes = self._notes_for_run(run.status, retries, operator_interventions, delegated_messages)

        completed_at = run.completed_at or run.updated_at
        latency_ms = max(0, int((completed_at - run.started_at).total_seconds() * 1000))
        return BenchmarkRunScore(
            run_id=run.run_id,
            task_id=run.task_id,
            agent_id=run.agent_id,
            scenario_id=scenario.scenario_id if scenario is not None else None,
            scenario_name=scenario.name if scenario is not None else None,
            category=scenario.category if scenario is not None else None,
            success=run.status == RunStatus.COMPLETED,
            status=run.status.value,
            latency_ms=latency_ms,
            tokens_used=budget.tokens_used if budget is not None else 0,
            pages_opened=budget.pages_opened if budget is not None else 0,
            tool_calls=budget.tool_calls if budget is not None else 0,
            memory_writes=budget.memory_writes if budget is not None else 0,
            llm_cost_estimate=budget.llm_cost_estimate if budget is not None else 0.0,
            tool_cost_estimate=budget.tool_cost_estimate if budget is not None else 0.0,
            compression_savings_ratio=compression_savings_ratio,
            retries=retries,
            operator_interventions=operator_interventions,
            delegated_messages=delegated_messages,
            planning_iterations=planning_iterations,
            notes=notes,
        )

    def _scenario_for_run(
        self,
        run,
        scenario_map: dict[str, BenchmarkScenario],
    ) -> BenchmarkScenario | None:
        metadata = run.metadata if isinstance(run.metadata, dict) else {}
        scenario_id = metadata.get("benchmark_scenario_id")
        if isinstance(scenario_id, str) and scenario_id in scenario_map:
            return scenario_map[scenario_id]
        scenario_name = metadata.get("benchmark_scenario")
        if isinstance(scenario_name, str):
            for scenario in scenario_map.values():
                if scenario.name == scenario_name:
                    return scenario
        return None

    @staticmethod
    def _compression_savings(events) -> float:
        raw_tokens = 0
        compressed_tokens = 0
        ratios: list[float] = []
        for event in events:
            payload = event.payload if isinstance(event.payload, dict) else {}
            if event.event_type == "loop.planned":
                raw_tokens += int(payload.get("raw_context_size", 0) or 0)
                compressed_tokens += int(payload.get("compressed_context_size", 0) or 0)
                ratio = payload.get("compression_ratio")
                if isinstance(ratio, (int, float)):
                    ratios.append(float(ratio))
            elif event.event_type == "memory.compressed":
                raw_tokens += int(payload.get("raw_memory_token_estimate", 0) or 0)
                compressed_tokens += int(payload.get("compressed_memory_token_estimate", 0) or 0)
                ratio = payload.get("memory_token_ratio")
                if isinstance(ratio, (int, float)):
                    ratios.append(float(ratio))
        if raw_tokens > 0:
            return round(max(0.0, 1.0 - (compressed_tokens / raw_tokens)), 4)
        if ratios:
            return round(max(0.0, 1.0 - (sum(ratios) / len(ratios))), 4)
        return 0.0

    @staticmethod
    def _retry_count(events) -> int:
        retries = 0
        for event in events:
            payload = event.payload if isinstance(event.payload, dict) else {}
            if event.event_type == "session.restored" and payload.get("recovery"):
                retries += 1
            elif event.event_type == "browser.error":
                error = payload.get("error")
                if isinstance(error, str) and any(token in error.lower() for token in ("retry", "stale", "recover")):
                    retries += 1
        return retries

    @staticmethod
    def _operator_intervention_count(events) -> int:
        return sum(
            1
            for event in events
            if event.event_type in {"approval.required", "browser.human_intervention.required"}
        )

    @staticmethod
    def _delegation_count(events) -> int:
        count = 0
        for event in events:
            payload = event.payload if isinstance(event.payload, dict) else {}
            if event.event_type == "a2a.message":
                count += 1
            elif event.event_type == "runtime.events.compressed" and payload.get("event_type") == "a2a.message":
                count += int(payload.get("event_count", 0) or 0)
        return count

    @staticmethod
    def _notes_for_run(
        status: RunStatus,
        retries: int,
        operator_interventions: int,
        delegated_messages: int,
    ) -> list[str]:
        notes: list[str] = []
        if status != RunStatus.COMPLETED:
            notes.append(f"Run finished with status {status.value}.")
        if retries:
            notes.append(f"Observed {retries} recovery or retry event(s).")
        if operator_interventions:
            notes.append(f"Operator intervention required {operator_interventions} time(s).")
        if delegated_messages:
            notes.append(f"Observed {delegated_messages} delegated or A2A message event(s).")
        return notes

    @staticmethod
    def _aggregate(scores: list[BenchmarkRunScore]) -> BenchmarkAggregate:
        total_runs = len(scores)
        successful_runs = sum(1 for score in scores if score.success)
        failed_runs = total_runs - successful_runs
        avg_latency_ms = sum(score.latency_ms for score in scores) / total_runs if total_runs else 0.0
        avg_compression = (
            sum(score.compression_savings_ratio for score in scores) / total_runs if total_runs else 0.0
        )
        return BenchmarkAggregate(
            total_runs=total_runs,
            successful_runs=successful_runs,
            failed_runs=failed_runs,
            success_rate=round((successful_runs / total_runs), 4) if total_runs else 0.0,
            avg_latency_ms=round(avg_latency_ms, 2),
            total_tokens_used=sum(score.tokens_used for score in scores),
            total_tool_calls=sum(score.tool_calls for score in scores),
            total_retries=sum(score.retries for score in scores),
            total_operator_interventions=sum(score.operator_interventions for score in scores),
            avg_compression_savings_ratio=round(avg_compression, 4),
            total_delegated_messages=sum(score.delegated_messages for score in scores),
        )
