import asyncio
from types import SimpleNamespace

from synapse.models.loop import AgentAction, AgentActionType
from synapse.models.task import TaskRequest
from synapse.runtime.llm import AnthropicProvider, LocalModelProvider, OpenAIProvider, create_llm_provider
from synapse.runtime.planning import NavigationEvaluator, NavigationPlanner, NavigationReflector


def test_create_llm_provider_returns_none_when_disabled() -> None:
    settings = SimpleNamespace(llm_provider=None)
    assert create_llm_provider(settings) is None


def test_create_llm_provider_builds_openai_provider() -> None:
    settings = SimpleNamespace(
        llm_provider="openai",
        llm_request_timeout_seconds=30.0,
        openai_api_key="test-key",
        openai_model="gpt-4o-mini",
        openai_base_url="https://api.openai.com/v1",
    )
    provider = create_llm_provider(settings)
    assert isinstance(provider, OpenAIProvider)
    assert provider.model == "gpt-4o-mini"


def test_create_llm_provider_builds_anthropic_provider() -> None:
    settings = SimpleNamespace(
        llm_provider="anthropic",
        llm_request_timeout_seconds=30.0,
        anthropic_api_key="test-key",
        anthropic_model="claude-3-5-sonnet-latest",
        anthropic_base_url="https://api.anthropic.com/v1",
        anthropic_api_version="2023-06-01",
    )
    provider = create_llm_provider(settings)
    assert isinstance(provider, AnthropicProvider)
    assert provider.model == "claude-3-5-sonnet-latest"


def test_create_llm_provider_builds_local_provider() -> None:
    settings = SimpleNamespace(
        llm_provider="local",
        llm_request_timeout_seconds=15.0,
        local_model_endpoint="http://127.0.0.1:11434/api/generate",
        local_model_name="llama3.1",
        local_model_api_key=None,
    )
    provider = create_llm_provider(settings)
    assert isinstance(provider, LocalModelProvider)
    assert provider.endpoint == "http://127.0.0.1:11434/api/generate"


def test_navigation_planner_generate_plan_uses_llm_json_actions() -> None:
    class StubProvider:
        async def generate(self, prompt: str, system: str | None = None) -> str:
            return '{"actions":[{"type":"open","url":"https://example.com"},{"type":"click","selector":"button.submit"}]}'

    planner = NavigationPlanner(llm=StubProvider())
    task = TaskRequest(task_id="task-1", agent_id="agent-1", goal="Open and click")

    actions = asyncio.run(planner.generate_plan(task, completed_actions=[], memory_summary="recent memory"))
    assert [action.type for action in actions] == [AgentActionType.OPEN, AgentActionType.CLICK]
    assert actions[0].url == "https://example.com"


def test_navigation_planner_generate_plan_falls_back_on_invalid_llm_output() -> None:
    class BadProvider:
        async def generate(self, prompt: str, system: str | None = None) -> str:
            return "not json"

    planner = NavigationPlanner(llm=BadProvider())
    task = TaskRequest(task_id="task-2", agent_id="agent-2", goal="Extract heading and screenshot")

    actions = asyncio.run(planner.generate_plan(task, completed_actions=[]))
    assert [action.type for action in actions] == [AgentActionType.EXTRACT, AgentActionType.SCREENSHOT]


def test_navigation_planner_records_compression_telemetry() -> None:
    class StubProvider:
        async def generate(self, prompt: str, system: str | None = None) -> str:
            return '{"actions":[{"type":"screenshot"}]}'

    class StubCompression:
        async def compress_text(self, text: str, context: dict | None = None) -> str:
            return text[:5]

        async def compress_json(self, data: dict, context: dict | None = None) -> dict:
            return {"compressed": True, "keys": sorted(data.keys())}

        async def summarize_events(self, events: list[dict], context: dict | None = None) -> dict:
            return {"count": len(events)}

        async def summarize_memory(self, memories: list[dict], context: dict | None = None) -> dict:
            return {"count": len(memories)}

    planner = NavigationPlanner(llm=StubProvider(), compression=StubCompression())
    task = TaskRequest(task_id="task-telemetry", agent_id="agent-1", goal="Open and inspect")

    actions = asyncio.run(
        planner.generate_plan(
            task,
            completed_actions=[],
            memory_summary="recent memory",
            recent_memories=[{"memory_id": "m1", "content": "memory"}],
            recent_events=[{"event_type": "task.updated"}],
        )
    )

    telemetry = planner.get_last_context_telemetry()
    assert actions[0].type == AgentActionType.SCREENSHOT
    assert telemetry["raw_context_size"] > 0
    assert telemetry["compressed_context_size"] > 0
    assert telemetry["compression_ratio"] == round(
        telemetry["compressed_context_size"] / telemetry["raw_context_size"],
        4,
    )
    assert "raw_context" in telemetry
    assert "compressed_context" in telemetry


def test_navigation_evaluator_evaluate_action_uses_llm_response() -> None:
    class EvalProvider:
        async def generate(self, prompt: str, system: str | None = None) -> str:
            return (
                '{"success":true,"reason":"Action completed successfully.",'
                '"next_actions":[{"type":"click","selector":"button.next"}]}'
            )

    evaluator = NavigationEvaluator(llm=EvalProvider())
    last_action = AgentAction(action_id="a1", type=AgentActionType.OPEN, url="https://example.com")
    result = asyncio.run(
        evaluator.evaluate_action(
            goal="Open page",
            last_action=last_action,
            page_state=None,
            memory="recent memory",
        )
    )

    assert result is not None
    assert result["success"] is True
    assert result["reason"] == "Action completed successfully."
    next_actions = result["next_actions"]
    assert isinstance(next_actions, list)
    assert next_actions[0].type == AgentActionType.CLICK


def test_navigation_evaluator_evaluate_action_returns_none_on_invalid_json() -> None:
    class BadEvalProvider:
        async def generate(self, prompt: str, system: str | None = None) -> str:
            return "not-json"

    evaluator = NavigationEvaluator(llm=BadEvalProvider())
    last_action = AgentAction(action_id="a2", type=AgentActionType.EXTRACT, selector="h1")
    result = asyncio.run(
        evaluator.evaluate_action(
            goal="Extract heading",
            last_action=last_action,
            page_state=None,
            memory="",
        )
    )
    assert result is None


def test_navigation_reflector_uses_llm_summary() -> None:
    class ReflectProvider:
        async def generate(self, prompt: str, system: str | None = None) -> str:
            return '{"summary":"Persist successful extraction strategy.","should_continue":true}'

    reflector = NavigationReflector(llm=ReflectProvider())
    task = TaskRequest(task_id="task-r1", agent_id="agent-1", goal="Collect papers")
    summary = asyncio.run(reflector.reflect(task=task, completed_actions=[], current_page=None, memory_summary="m1"))
    assert summary == "Persist successful extraction strategy."


def test_navigation_reflector_falls_back_on_invalid_json() -> None:
    class BadReflectProvider:
        async def generate(self, prompt: str, system: str | None = None) -> str:
            return "invalid"

    reflector = NavigationReflector(llm=BadReflectProvider())
    task = TaskRequest(task_id="task-r2", agent_id="agent-2", goal="Collect papers")
    summary = asyncio.run(reflector.reflect(task=task, completed_actions=[], current_page=None))
    assert "Completed 0 actions" in summary
