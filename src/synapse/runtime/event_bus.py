from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from collections import defaultdict
from typing import Any

from fastapi import WebSocket

from synapse.models.runtime_event import EventSeverity, EventType, RuntimeEvent
from synapse.runtime.compression.base import CompressionProvider
from synapse.runtime.compression.noop import NoOpCompressionProvider
from synapse.runtime.state_store import RuntimeStateStore
from synapse.transports.websocket_manager import WebSocketManager


class EventBus:
    def __init__(
        self,
        sockets: WebSocketManager,
        compression_provider: CompressionProvider | None = None,
    ) -> None:
        self.sockets = sockets
        self.compression_provider = compression_provider or NoOpCompressionProvider()
        self._recent_event_groups: dict[tuple[str, str | None, str | None, str], list[RuntimeEvent]] = defaultdict(list)
        self._compressible_event_types = {
            EventType.BUDGET_UPDATED,
            EventType.CONNECTION_HEARTBEAT,
            EventType.AGENT_STATUS_UPDATED,
            EventType.POPUP_DISMISSED,
            EventType.NAVIGATION_ROUTE_CHANGED,
            EventType.A2A_MESSAGE,
        }

    def set_state_store(self, state_store: RuntimeStateStore) -> None:
        self.sockets.set_state_store(state_store)

    def set_compression_provider(self, compression_provider: CompressionProvider | None) -> None:
        self.compression_provider = compression_provider or NoOpCompressionProvider()
        self.sockets.set_compression_provider(self.compression_provider)

    async def connect(self, websocket: WebSocket) -> None:
        await self.sockets.connect(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self.sockets.disconnect(websocket)

    @asynccontextmanager
    async def subscribe(self, subscriber_id: str) -> AsyncIterator[object]:
        async with self.sockets.subscribe(subscriber_id) as queue:
            yield queue

    async def publish(self, event: RuntimeEvent) -> None:
        await self.sockets.broadcast(event)
        await self._maybe_publish_compressed_summary(event)

    async def emit(
        self,
        event_type: EventType,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
        session_id: str | None = None,
        source: str = "runtime",
        payload: dict[str, object] | None = None,
        severity: EventSeverity = EventSeverity.INFO,
        correlation_id: str | None = None,
    ) -> None:
        await self.publish(
            RuntimeEvent(
                event_type=event_type,
                agent_id=agent_id,
                task_id=task_id,
                session_id=session_id,
                source=source,
                payload=payload or {},
                severity=severity,
                correlation_id=correlation_id,
            )
        )

    async def get_compact_history(
        self,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, object]:
        return await self.sockets.get_compact_event_history(agent_id=agent_id, task_id=task_id, limit=limit)

    async def _maybe_publish_compressed_summary(self, event: RuntimeEvent) -> None:
        if event.event_type not in self._compressible_event_types:
            return
        key = (
            event.event_type.value,
            event.agent_id,
            event.task_id,
            event.source,
        )
        bucket = self._recent_event_groups[key]
        bucket.append(event)
        if len(bucket) > 5:
            bucket.pop(0)
        if len(bucket) < 3:
            return

        event_dicts = [entry.model_dump(mode="json") for entry in bucket]
        summary = await self.compression_provider.summarize_events(
            event_dicts,
            context={
                "agent_id": event.agent_id,
                "task_id": event.task_id,
                "event_type": event.event_type.value,
                "channel": "event_bus",
            },
        )
        compact_event = RuntimeEvent(
            event_type=EventType.RUNTIME_EVENTS_COMPRESSED,
            agent_id=event.agent_id,
            task_id=event.task_id,
            session_id=event.session_id,
            source="event_bus",
            payload={
                "summary": summary,
                "raw_event_ids": [entry.event_id for entry in bucket],
                "event_type": event.event_type.value,
                "event_count": len(bucket),
            },
            correlation_id=event.correlation_id,
        )
        await self.sockets.broadcast(compact_event)
        self._recent_event_groups[key] = bucket[-2:]
