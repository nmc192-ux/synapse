import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING

from fastapi import WebSocket

from synapse.models.runtime_event import RuntimeEvent
from synapse.runtime.compression.base import CompressionProvider
from synapse.runtime.compression.noop import NoOpCompressionProvider
from synapse.runtime.state_store import RuntimeStateStore


logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from synapse.security.auth import AuthPrincipal


@dataclass(slots=True)
class WebSocketSubscription:
    websocket: WebSocket
    principal: "AuthPrincipal | None"
    organization_id: str | None = None
    project_id: str | None = None
    run_id: str | None = None


class WebSocketManager:
    def __init__(
        self,
        state_store: RuntimeStateStore | None = None,
        compression_provider: CompressionProvider | None = None,
    ) -> None:
        self._connections: dict[WebSocket, WebSocketSubscription] = {}
        self._subscribers: dict[str, tuple[asyncio.Queue[RuntimeEvent], str | None, str | None]] = {}
        self._principals: dict[WebSocket, "AuthPrincipal"] = {}
        self._state_store = state_store
        self._compression_provider = compression_provider or NoOpCompressionProvider()

    def set_state_store(self, state_store: RuntimeStateStore) -> None:
        self._state_store = state_store

    def set_compression_provider(self, compression_provider: CompressionProvider | None) -> None:
        self._compression_provider = compression_provider or NoOpCompressionProvider()

    async def connect(
        self,
        websocket: WebSocket,
        principal: "AuthPrincipal | None" = None,
        *,
        organization_id: str | None = None,
        project_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        await websocket.accept()
        subscription = WebSocketSubscription(
            websocket=websocket,
            principal=principal,
            organization_id=organization_id if organization_id is not None else getattr(principal, "organization_id", None),
            project_id=project_id if project_id is not None else getattr(principal, "project_id", None),
            run_id=run_id,
        )
        self._connections[websocket] = subscription
        if principal is not None:
            self._principals[websocket] = principal

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.pop(websocket, None)
        self._principals.pop(websocket, None)

    def get_principal(self, websocket: WebSocket) -> "AuthPrincipal | None":
        return self._principals.get(websocket)

    @asynccontextmanager
    async def subscribe(
        self,
        subscriber_id: str,
        *,
        project_id: str | None = None,
        run_id: str | None = None,
    ) -> AsyncIterator[asyncio.Queue[RuntimeEvent]]:
        queue: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
        self._subscribers[subscriber_id] = (queue, project_id, run_id)
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
        for connection, subscription in list(self._connections.items()):
            if not self._should_deliver(subscription.project_id, subscription.run_id, event):
                continue
            try:
                await connection.send_json(event.model_dump(mode="json"))
            except RuntimeError:
                dead_connections.append(connection)

        for connection in dead_connections:
            self.disconnect(connection)

        for queue, project_id, run_id in self._subscribers.values():
            if self._should_deliver(project_id, run_id, event):
                await queue.put(event)

    async def get_compact_event_history(
        self,
        *,
        organization_id: str | None = None,
        project_id: str | None = None,
        run_id: str | None = None,
        agent_id: str | None = None,
        task_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, object]:
        if self._state_store is None:
            return {"count": 0, "events": [], "summary": {}, "provider": "noop"}
        events = await self._state_store.get_runtime_events(run_id=run_id, agent_id=agent_id, task_id=task_id, limit=limit)
        if organization_id is not None:
            events = [event for event in events if event.get("organization_id") == organization_id]
        if project_id is not None:
            events = [event for event in events if event.get("project_id") == project_id]
        summary = await self._compression_provider.summarize_events(
            events,
            context={
                "organization_id": organization_id,
                "project_id": project_id,
                "run_id": run_id,
                "agent_id": agent_id,
                "task_id": task_id,
                "channel": "runtime_history",
            },
        )
        return {
            "count": len(events),
            "events": events,
            "summary": summary,
        }

    @staticmethod
    def _should_deliver(project_id: str | None, run_id: str | None, event: RuntimeEvent) -> bool:
        if project_id is not None and event.project_id != project_id:
            return False
        if run_id is not None and event.run_id != run_id:
            return False
        return True
