import asyncio

from synapse.config import Settings
from synapse.runtime.compression.base import create_compression_provider
from synapse.runtime.compression.noop import NoOpCompressionProvider
from synapse.runtime.compression.turboquant import TurboQuantCompressionProvider


def test_create_noop_provider() -> None:
    provider = create_compression_provider(Settings(compression_provider="noop"))
    assert isinstance(provider, NoOpCompressionProvider)


def test_create_turboquant_provider() -> None:
    provider = create_compression_provider(Settings(compression_provider="turboquant"))
    assert isinstance(provider, TurboQuantCompressionProvider)


def test_noop_provider_round_trips() -> None:
    async def scenario() -> None:
        provider = NoOpCompressionProvider()
        assert await provider.compress_text("hello") == "hello"
        assert await provider.compress_json({"a": 1}) == {"a": 1}
        assert (await provider.summarize_events([{"event_type": "x"}]))["count"] == 1
        assert (await provider.summarize_memory([{"memory_id": "m1"}]))["count"] == 1

    asyncio.run(scenario())


def test_turboquant_stub_returns_structured_summary() -> None:
    async def scenario() -> None:
        provider = TurboQuantCompressionProvider()
        summary = await provider.summarize_events(
            [{"event_type": "task.updated"}, {"event_type": "loop.acted"}],
            context={"task_id": "task-1"},
        )
        assert summary["provider"] == "turboquant"
        assert summary["mode"] == "stub"
        assert summary["event_count"] == 2

    asyncio.run(scenario())
