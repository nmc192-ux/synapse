import asyncio

from synapse.models.agent import AgentBudgetUsage
from synapse.models.benchmark import BenchmarkCategory
from synapse.models.run import RunStatus
from synapse.runtime.benchmarking import BenchmarkSuite
from synapse.runtime.run_store import RunStore
from synapse.runtime.state_store import InMemoryRuntimeStateStore


def test_default_fixture_scenarios_cover_required_workflows() -> None:
    scenarios = BenchmarkSuite.default_fixture_scenarios("http://127.0.0.1:8011")
    categories = {scenario.category for scenario in scenarios}
    assert len(scenarios) == 6
    assert categories == {
        BenchmarkCategory.EXTRACTION,
        BenchmarkCategory.FORM_COMPLETION,
        BenchmarkCategory.SPA_NAVIGATION,
        BenchmarkCategory.POPUPS,
        BenchmarkCategory.SESSION_CONTINUATION,
        BenchmarkCategory.A2A_DELEGATION,
    }


def test_benchmark_suite_scores_runs_and_builds_report() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        run_store = RunStore(store)
        suite = BenchmarkSuite(run_store, store)
        scenarios = suite.default_fixture_scenarios("http://127.0.0.1:8011")

        run = await run_store.create_run(
            task_id="task-1",
            agent_id="agent-1",
            correlation_id="task-1",
            metadata={
                "benchmark_scenario_id": "fixture-a2a-delegation",
                "benchmark_scenario": "Fixture Delegated Browsing",
            },
        )
        await run_store.update_budget(
            run.run_id,
            AgentBudgetUsage(
                tokens_used=4200,
                pages_opened=3,
                tool_calls=2,
                memory_writes=4,
                llm_cost_estimate=0.0084,
                tool_cost_estimate=0.001,
            ),
        )
        await store.store_runtime_event(
            "evt-planned",
            {
                "event_id": "evt-planned",
                "run_id": run.run_id,
                "event_type": "loop.planned",
                "timestamp": "2026-03-26T10:00:00+00:00",
                "phase": "plan",
                "payload": {"raw_context_size": 1000, "compressed_context_size": 450, "compression_ratio": 0.45},
                "correlation_id": run.run_id,
                "severity": "info",
                "source": "agent_loop",
            },
        )
        await store.store_runtime_event(
            "evt-memory",
            {
                "event_id": "evt-memory",
                "run_id": run.run_id,
                "event_type": "memory.compressed",
                "timestamp": "2026-03-26T10:00:01+00:00",
                "phase": "reflect",
                "payload": {
                    "raw_memory_token_estimate": 600,
                    "compressed_memory_token_estimate": 240,
                    "memory_token_ratio": 0.4,
                },
                "correlation_id": run.run_id,
                "severity": "info",
                "source": "memory_service",
            },
        )
        await store.store_runtime_event(
            "evt-retry",
            {
                "event_id": "evt-retry",
                "run_id": run.run_id,
                "event_type": "session.restored",
                "timestamp": "2026-03-26T10:00:02+00:00",
                "phase": "session",
                "payload": {"recovery": "retry_with_profile"},
                "correlation_id": run.run_id,
                "severity": "warning",
                "source": "browser_service",
            },
        )
        await store.store_runtime_event(
            "evt-approval",
            {
                "event_id": "evt-approval",
                "run_id": run.run_id,
                "event_type": "approval.required",
                "timestamp": "2026-03-26T10:00:03+00:00",
                "phase": "act",
                "payload": {"action": "human_intervention"},
                "correlation_id": run.run_id,
                "severity": "warning",
                "source": "browser_service",
            },
        )
        await store.store_runtime_event(
            "evt-a2a",
            {
                "event_id": "evt-a2a",
                "run_id": run.run_id,
                "event_type": "a2a.message",
                "timestamp": "2026-03-26T10:00:04+00:00",
                "phase": "a2a",
                "payload": {"type": "REQUEST_TASK"},
                "correlation_id": run.run_id,
                "severity": "info",
                "source": "runtime_controller",
            },
        )
        await run_store.update_status(run.run_id, RunStatus.COMPLETED, current_phase="completed")

        score = await suite.score_run(run.run_id, scenario_map={scenario.scenario_id: scenario for scenario in scenarios})
        assert score.success is True
        assert score.category == BenchmarkCategory.A2A_DELEGATION
        assert score.tokens_used == 4200
        assert score.retries == 1
        assert score.operator_interventions == 1
        assert score.delegated_messages == 1
        assert score.compression_savings_ratio == 0.5687

        report = await suite.build_report(
            [run.run_id],
            fixture_base_url="http://127.0.0.1:8011",
            scenarios=scenarios,
        )
        assert report.aggregate.success_rate == 1.0
        assert report.aggregate.total_tokens_used == 4200
        assert report.aggregate.total_retries == 1
        assert report.aggregate.total_operator_interventions == 1
        assert report.aggregate.total_delegated_messages == 1

    asyncio.run(scenario())
