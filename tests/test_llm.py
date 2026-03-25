import asyncio
from types import SimpleNamespace

from synapse.models.loop import AgentActionType
from synapse.models.task import TaskRequest
from synapse.runtime.llm import AnthropicProvider, LocalModelProvider, OpenAIProvider, create_llm_provider
from synapse.runtime.planning import NavigationPlanner


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
