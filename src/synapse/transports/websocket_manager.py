import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging

from fastapi import WebSocket

from synapse.models.runtime_event import RuntimeEvent
from synapse.runtime.compression.base import CompressionProvider
from synapse.runtime.compression.noop import NoOpCompressionProvider
from synapse.runtime.state_store import RuntimeStateStore


logger = logging.getLogger(__name__)


class WebSocketManager:
    def __init__(
        self,
        state_store: RuntimeStateStore | None = None,
        compression_provider: CompressionProvider | None = None,
    ) -> None:
        self._connections: set[WebSocket] = set()
        self._subscribers: dict[str, asyncio.Queue[RuntimeEvent]] = {}
        self._state_store = state_store
        self._compression_provider = compression_provider or NoOpCompressionProvider()

    def set_state_store(self, state_store: RuntimeStateStore) -> None:
        self._state_store = state_store

    def set_compression_provider(self, compression_provider: CompressionProvider | None) -> None:
        self._compression_provider = compression_provider or NoOpCompressionProvider()

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
                    event.event_id,
                    event.model_dump(mode="json"),
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

    async def get_compact_event_history(
        self,
        *,
        run_id: str | None = None,
        agent_id: str | None = None,
        task_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, object]:
        if self._state_store is None:
            return {"count": 0, "events": [], "summary": {}, "provider": "noop"}
        events = await self._state_store.get_runtime_events(run_id=run_id, agent_id=agent_id, task_id=task_id, limit=limit)
        summary = await self._compression_provider.summarize_events(
            events,
            context={"run_id": run_id, "agent_id": agent_id, "task_id": task_id, "channel": "runtime_history"},
        )
        return {
            "count": len(events),
            "events": events,
            "summary": summary,
        }
