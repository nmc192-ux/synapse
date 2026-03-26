from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from synapse.models.runtime_event import EventSeverity, EventType, RuntimeEvent
from synapse.models.runtime_state import BrowserWorkerState, WorkerRuntimeStatus
from synapse.runtime.queues import BrowserTaskEnvelope, BrowserTaskQueue, BrowserTaskResult


ResultHandler = Callable[[BrowserTaskResult], Awaitable[None]]
EventPublisher = Callable[[RuntimeEvent], Awaitable[None]]
HeartbeatCallback = Callable[[str], Awaitable[None]]


class BrowserWorker:
    def __init__(
        self,
        worker_id: str,
        queue: BrowserTaskQueue,
        runtime_factory: Callable[[], Any],
        result_handler: ResultHandler,
        event_publisher: EventPublisher | None = None,
        heartbeat_interval_seconds: float = 15.0,
        heartbeat_callback: HeartbeatCallback | None = None,
    ) -> None:
        self.worker_id = worker_id
        self.queue = queue
        self.runtime_factory = runtime_factory
        self.result_handler = result_handler
        self.event_publisher = event_publisher
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.heartbeat_callback = heartbeat_callback
        self.runtime: Any | None = None
        self.state = BrowserWorkerState(worker_id=worker_id, queue_name=queue.name)
        self._loop_task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self.runtime = self.runtime_factory()
        if hasattr(self.runtime, "set_event_publisher"):
            self.runtime.set_event_publisher(self.event_publisher)
        await self.queue.start()
        if hasattr(self.runtime, "start"):
            await self.runtime.start()
        self._running = True
        self.state.status = WorkerRuntimeStatus.IDLE
        await self._emit_status_event()
        self._loop_task = asyncio.create_task(self._run_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    def set_event_publisher(self, event_publisher: EventPublisher | None) -> None:
        self.event_publisher = event_publisher
        if self.runtime is not None and hasattr(self.runtime, "set_event_publisher"):
            self.runtime.set_event_publisher(event_publisher)

    async def stop(self) -> None:
        self._running = False
        for task in (self._loop_task, self._heartbeat_task):
            if task is not None:
                task.cancel()
        for task in (self._loop_task, self._heartbeat_task):
            if task is not None:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if self.runtime is not None and hasattr(self.runtime, "stop"):
            await self.runtime.stop()
        await self.queue.stop()
        self.state.status = WorkerRuntimeStatus.OFFLINE
        self.state.current_request_id = None
        await self._emit_status_event()

    async def _run_loop(self) -> None:
        assert self.runtime is not None
        while self._running:
            try:
                item = await self.queue.get(timeout=1.0)
            except TimeoutError:
                continue
            try:
                self.state.status = WorkerRuntimeStatus.BUSY
                self.state.current_request_id = item.request_id
                await self._emit_status_event()
                handler = getattr(self.runtime, item.action)
                payload = await handler(**item.arguments)
                await self.result_handler(
                    BrowserTaskResult(
                        action_id=item.action_id,
                        request_id=item.request_id,
                        worker_id=self.worker_id,
                        action=item.action,
                        run_id=item.run_id,
                        session_id=item.session_id,
                        success=True,
                        payload=payload,
                        fencing_token=item.fencing_token,
                    )
                )
                await self._emit_task_completed(item, success=True)
            except Exception as exc:
                await self.result_handler(
                    BrowserTaskResult(
                        action_id=item.action_id,
                        request_id=item.request_id,
                        worker_id=self.worker_id,
                        action=item.action,
                        run_id=item.run_id,
                        session_id=item.session_id,
                        success=False,
                        error=str(exc),
                        fencing_token=item.fencing_token,
                    )
                )
                await self._emit_task_completed(item, success=False, error=str(exc))
            finally:
                self.state.status = WorkerRuntimeStatus.IDLE if self._running else WorkerRuntimeStatus.OFFLINE
                self.state.current_request_id = None
                self.state.last_heartbeat = datetime.now(timezone.utc)
                await self._emit_status_event()

    async def _heartbeat_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self.heartbeat_interval_seconds)
            self.state.last_heartbeat = datetime.now(timezone.utc)
            if self.heartbeat_callback is not None:
                await self.heartbeat_callback(self.worker_id)
            if self.event_publisher is not None:
                await self.event_publisher(
                    RuntimeEvent(
                        event_type=EventType.BROWSER_WORKER_HEARTBEAT,
                        source="browser_worker",
                        payload={
                            "worker_id": self.worker_id,
                            "queue_name": self.queue.name,
                            "status": self.state.status.value,
                            "last_heartbeat": self.state.last_heartbeat.isoformat(),
                            "active_sessions": self.state.active_sessions,
                        },
                    )
                )

    async def _emit_status_event(self) -> None:
        if self.event_publisher is None:
            return
        await self.event_publisher(
            RuntimeEvent(
                event_type=EventType.BROWSER_WORKER_STATUS_UPDATED,
                source="browser_worker",
                payload=self.state.model_dump(mode="json"),
                severity=EventSeverity.INFO,
            )
        )

    async def _emit_task_completed(
        self,
        item: BrowserTaskEnvelope,
        *,
        success: bool,
        error: str | None = None,
    ) -> None:
        if self.event_publisher is None:
            return
        await self.event_publisher(
            RuntimeEvent(
                event_type=EventType.BROWSER_TASK_COMPLETED,
                run_id=item.run_id,
                agent_id=item.agent_id,
                task_id=item.task_id,
                session_id=item.session_id,
                source="browser_worker",
                payload={
                    "worker_id": self.worker_id,
                    "request_id": item.request_id,
                    "action_id": item.action_id,
                    "action": item.action,
                    "success": success,
                    "error": error,
                    "fencing_token": item.fencing_token,
                },
                severity=EventSeverity.ERROR if not success else EventSeverity.INFO,
                correlation_id=item.request_id,
            )
        )
