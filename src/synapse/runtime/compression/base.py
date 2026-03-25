from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class CompressionProvider(ABC):
    @abstractmethod
    async def compress_text(
        self,
        text: str,
        context: dict[str, Any] | None = None,
    ) -> str:
        raise NotImplementedError

    @abstractmethod
    async def compress_json(
        self,
        data: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def summarize_events(
        self,
        events: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def summarize_memory(
        self,
        memories: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError


def create_compression_provider(settings: Any) -> CompressionProvider:
    provider_name = str(getattr(settings, "compression_provider", "noop")).strip().lower()
    if provider_name in {"", "noop", "none", "disabled"}:
        from synapse.runtime.compression.noop import NoOpCompressionProvider

        return NoOpCompressionProvider()
    if provider_name == "turboquant":
        from synapse.runtime.compression.turboquant import TurboQuantCompressionProvider

        return TurboQuantCompressionProvider()
    raise ValueError(f"Unsupported compression provider: {provider_name}")
