from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import WebSocket

from synapse.models.runtime_event import EventSeverity, EventType, RuntimeEvent
from synapse.runtime.state_store import RuntimeStateStore
from synapse.transports.websocket_manager import WebSocketManager


class EventBus:
    def __init__(self, sockets: WebSocketManager) -> None:
        self.sockets = sockets

    def set_state_store(self, state_store: RuntimeStateStore) -> None:
        self.sockets.set_state_store(state_store)

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
