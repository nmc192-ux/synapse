from types import SimpleNamespace

from synapse.runtime.llm import AnthropicProvider, LocalModelProvider, OpenAIProvider, create_llm_provider


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
