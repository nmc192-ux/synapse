import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging
import uuid

from fastapi import WebSocket

from synapse.models.events import RuntimeEvent
from synapse.runtime.state_store import RuntimeStateStore


logger = logging.getLogger(__name__)


class WebSocketManager:
    def __init__(self, state_store: RuntimeStateStore | None = None) -> None:
        self._connections: set[WebSocket] = set()
        self._subscribers: dict[str, asyncio.Queue[RuntimeEvent]] = {}
        self._state_store = state_store

    def set_state_store(self, state_store: RuntimeStateStore) -> None:
        self._state_store = state_store

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)

    @asynccontextmanager
    async def subscribe(self, subscriber_id: str) -> AsyncIterator[asyncio.Queue[RuntimeEvent]]:
        queue: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
        self._subscribers[subscriber_id] = queue
        try:
            yield queue
        finally:
            self._subscribers.pop(subscriber_id, None)

    async def broadcast(self, event: RuntimeEvent) -> None:
        if self._state_store is not None:
            try:
                await self._state_store.store_runtime_event(
                    str(uuid.uuid4()),
                    {
                        "event_type": event.event_type.value,
                        "agent_id": event.agent_id,
                        "task_id": str(event.payload.get("task_id")) if event.payload.get("task_id") is not None else None,
                        "session_id": event.session_id,
                        "payload": event.model_dump(mode="json"),
                        "timestamp": event.timestamp.isoformat(),
                    },
                )
            except Exception as exc:
                logger.warning("Failed to persist runtime event: %s", exc)

        dead_connections: list[WebSocket] = []
        for connection in self._connections:
            try:
                await connection.send_json(event.model_dump(mode="json"))
            except RuntimeError:
                dead_connections.append(connection)

        for connection in dead_connections:
            self.disconnect(connection)

        for queue in self._subscribers.values():
            await queue.put(event)
