from __future__ import annotations

from typing import Any

from synapse.runtime.compression.base import CompressionProvider


class NoOpCompressionProvider(CompressionProvider):
    async def compress_text(
        self,
        text: str,
        context: dict[str, Any] | None = None,
    ) -> str:
        return text

    async def compress_json(
        self,
        data: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return dict(data)

    async def summarize_events(
        self,
        events: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "count": len(events),
            "events": [dict(event) for event in events],
            "provider": "noop",
        }

    async def summarize_memory(
        self,
        memories: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "count": len(memories),
            "memories": [dict(memory) for memory in memories],
            "provider": "noop",
        }
