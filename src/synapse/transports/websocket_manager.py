import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import WebSocket

from synapse.models.events import RuntimeEvent


class WebSocketManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._subscribers: dict[str, asyncio.Queue[RuntimeEvent]] = {}

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
