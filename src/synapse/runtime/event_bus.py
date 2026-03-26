from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from collections import defaultdict
from typing import Any

from fastapi import WebSocket

from synapse.models.runtime_event import EventSeverity, EventType, RuntimeEvent, infer_event_phase
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
        self._recent_event_groups: dict[
            tuple[str, str | None, str | None, str | None, str | None, str | None, str],
            list[RuntimeEvent],
        ] = defaultdict(list)
        self._compressible_event_types = {
            EventType.BUDGET_UPDATED,
            EventType.CONNECTION_HEARTBEAT,
            EventType.AGENT_STATUS_UPDATED,
            EventType.POPUP_DISMISSED,
            EventType.NAVIGATION_ROUTE_CHANGED,
            EventType.A2A_MESSAGE,
        }
        self._listeners: list[Callable[[RuntimeEvent], Awaitable[None]]] = []
        self._context_resolver: Callable[[RuntimeEvent], Awaitable[dict[str, object]]] | None = None

    def set_state_store(self, state_store: RuntimeStateStore) -> None:
        self.sockets.set_state_store(state_store)

    def set_compression_provider(self, compression_provider: CompressionProvider | None) -> None:
        self.compression_provider = compression_provider or NoOpCompressionProvider()
        self.sockets.set_compression_provider(self.compression_provider)

    def set_context_resolver(
        self,
        resolver: Callable[[RuntimeEvent], Awaitable[dict[str, object]]] | None,
    ) -> None:
        self._context_resolver = resolver

    async def connect(self, websocket: WebSocket) -> None:
        await self.sockets.connect(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self.sockets.disconnect(websocket)

    @asynccontextmanager
    async def subscribe(self, subscriber_id: str) -> AsyncIterator[object]:
        async with self.sockets.subscribe(subscriber_id) as queue:
            yield queue

    async def publish(self, event: RuntimeEvent) -> None:
        normalized = await self._enrich_event(self._normalize_event(event))
        await self.sockets.broadcast(normalized)
        for listener in list(self._listeners):
            await listener(normalized)
        await self._maybe_publish_compressed_summary(normalized)

    def add_listener(self, listener: Callable[[RuntimeEvent], Awaitable[None]]) -> None:
        self._listeners.append(listener)

    async def emit(
        self,
        event_type: EventType,
        *,
        organization_id: str | None = None,
        project_id: str | None = None,
        run_id: str | None = None,
        agent_id: str | None = None,
        task_id: str | None = None,
        session_id: str | None = None,
        source: str = "runtime",
        phase: str | None = None,
        payload: dict[str, object] | None = None,
        severity: EventSeverity = EventSeverity.INFO,
        correlation_id: str | None = None,
    ) -> None:
        await self.publish(
            RuntimeEvent(
                event_type=event_type,
                organization_id=organization_id,
                project_id=project_id,
                run_id=run_id,
                agent_id=agent_id,
                task_id=task_id,
                session_id=session_id,
                source=source,
                phase=phase,
                payload=payload or {},
                severity=severity,
                correlation_id=correlation_id,
            )
        )

    async def get_compact_history(
        self,
        *,
        organization_id: str | None = None,
        project_id: str | None = None,
        run_id: str | None = None,
        agent_id: str | None = None,
        task_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, object]:
        return await self.sockets.get_compact_event_history(
            organization_id=organization_id,
            project_id=project_id,
            run_id=run_id,
            agent_id=agent_id,
            task_id=task_id,
            limit=limit,
        )

    async def _maybe_publish_compressed_summary(self, event: RuntimeEvent) -> None:
        if event.event_type not in self._compressible_event_types:
            return
        key = (
            event.event_type.value,
            event.organization_id,
            event.project_id,
            event.run_id,
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
                "run_id": event.run_id,
                "task_id": event.task_id,
                "event_type": event.event_type.value,
                "channel": "event_bus",
            },
        )
        compact_event = RuntimeEvent(
            event_type=EventType.RUNTIME_EVENTS_COMPRESSED,
            organization_id=event.organization_id,
            project_id=event.project_id,
            run_id=event.run_id,
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
        await self.sockets.broadcast(await self._enrich_event(compact_event))
        self._recent_event_groups[key] = bucket[-2:]

    @staticmethod
    def _normalize_event(event: RuntimeEvent) -> RuntimeEvent:
        if event.phase is not None:
            return event
        return event.model_copy(update={"phase": infer_event_phase(event.event_type)})

    async def _enrich_event(self, event: RuntimeEvent) -> RuntimeEvent:
        updates: dict[str, object] = {}
        if self._context_resolver is not None:
            resolved = await self._context_resolver(event)
            for key, value in resolved.items():
                if getattr(event, key, None) is None and value is not None:
                    updates[key] = value
        if event.correlation_id is None and "correlation_id" not in updates:
            updates["correlation_id"] = event.run_id or event.task_id or event.session_id or event.event_id
        if updates:
            return event.model_copy(update=updates)
        return event
