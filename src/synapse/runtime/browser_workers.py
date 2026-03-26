from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from synapse.config import settings
from synapse.models.runtime_event import EventType, RuntimeEvent
from synapse.models.runtime_state import BrowserSessionState, BrowserWorkerState
from synapse.runtime.execution_plane import ExecutionPlaneRuntime, RuntimeEventPublisher
from synapse.runtime.queues import BrowserTaskEnvelope, BrowserTaskQueue, BrowserTaskResult, create_browser_task_queue
from synapse.runtime.run_store import RunStore
from synapse.runtime.session import BrowserSession
from synapse.runtime.state_store import RuntimeStateStore
from synapse.workers.browser_worker import BrowserWorker


RuntimeFactory = Callable[[], ExecutionPlaneRuntime]


class BrowserWorkerPool:
    def __init__(
        self,
        *,
        state_store: RuntimeStateStore | None = None,
        worker_count: int | None = None,
        heartbeat_interval_seconds: float | None = None,
        runtime_factory: RuntimeFactory | None = None,
        queue_factory: Callable[[str], BrowserTaskQueue] | None = None,
        run_store: RunStore | None = None,
        lease_timeout_seconds: float | None = None,
    ) -> None:
        self.state_store = state_store
        self._event_publisher: RuntimeEventPublisher | None = None
        self.worker_count = max(1, worker_count or settings.browser_worker_count)
        self.heartbeat_interval_seconds = (
            heartbeat_interval_seconds or settings.browser_worker_heartbeat_interval_seconds
        )
        self._runtime_factory = runtime_factory or self._default_runtime_factory
        self._queue_factory = queue_factory or create_browser_task_queue
        self._run_store = run_store
        self._lease_timeout_seconds = lease_timeout_seconds or settings.scheduler_lease_timeout_seconds
        self._workers: dict[str, BrowserWorker] = {}
        self._session_workers: dict[str, str] = {}
        self._session_urls: dict[str, str | None] = {}
        self._pending: dict[str, asyncio.Future[BrowserTaskResult]] = {}
        self._next_worker_index = 0

    def set_state_store(self, state_store: RuntimeStateStore) -> None:
        self.state_store = state_store
        for worker in self._workers.values():
            if hasattr(worker.runtime, "set_state_store") and worker.runtime is not None:
                worker.runtime.set_state_store(state_store)

    def set_event_publisher(self, publisher: RuntimeEventPublisher | None) -> None:
        self._event_publisher = publisher
        for worker in self._workers.values():
            worker.set_event_publisher(publisher)

    async def start(self) -> None:
        if self._workers:
            return
        for index in range(self.worker_count):
            worker_id = f"browser-worker-{index + 1}"
            queue_name = f"{settings.browser_worker_queue_prefix}:{worker_id}"
            worker = BrowserWorker(
                worker_id=worker_id,
                queue=self._queue_factory(queue_name),
                runtime_factory=self._runtime_factory,
                result_handler=self._handle_result,
                event_publisher=self._event_publisher,
                heartbeat_interval_seconds=self.heartbeat_interval_seconds,
                heartbeat_callback=self._on_worker_heartbeat,
            )
            self._workers[worker_id] = worker
            await worker.start()

    async def stop(self) -> None:
        for worker in self._workers.values():
            await worker.stop()
        self._workers.clear()
        self._session_workers.clear()
        self._session_urls.clear()
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()

    async def create_session(
        self,
        session_id: str,
        agent_id: str | None = None,
        run_id: str | None = None,
        worker_id: str | None = None,
    ) -> BrowserSession:
        worker_id = worker_id or self._choose_worker_id(session_id=session_id)
        payload = await self._dispatch(
            worker_id,
            BrowserTaskEnvelope(
                action="create_session",
                session_id=session_id,
                agent_id=agent_id,
                run_id=run_id,
                arguments={"session_id": session_id, "agent_id": agent_id, "run_id": run_id},
            ),
        )
        self._session_workers[session_id] = worker_id
        self._refresh_worker_state(worker_id)
        return payload

    async def open(self, session_id: str, url: str):
        payload = await self._dispatch_session("open", session_id, {"session_id": session_id, "url": url})
        self._update_session_url(session_id, payload)
        return payload

    async def click(self, session_id: str, selector: str):
        payload = await self._dispatch_session("click", session_id, {"session_id": session_id, "selector": selector})
        self._update_session_url(session_id, payload)
        return payload

    async def type(self, session_id: str, selector: str, text: str):
        payload = await self._dispatch_session(
            "type",
            session_id,
            {"session_id": session_id, "selector": selector, "text": text},
        )
        self._update_session_url(session_id, payload)
        return payload

    async def extract(self, session_id: str, selector: str, attribute: str | None = None):
        return await self._dispatch_session(
            "extract",
            session_id,
            {"session_id": session_id, "selector": selector, "attribute": attribute},
        )

    async def screenshot(self, session_id: str):
        return await self._dispatch_session("screenshot", session_id, {"session_id": session_id})

    async def get_layout(self, session_id: str):
        payload = await self._dispatch_session("get_layout", session_id, {"session_id": session_id})
        self._update_session_url(session_id, payload)
        return payload

    async def find_element(self, session_id: str, element_type: str, text: str):
        return await self._dispatch_session(
            "find_element",
            session_id,
            {"session_id": session_id, "element_type": element_type, "text": text},
        )

    async def inspect(self, session_id: str, selector: str):
        return await self._dispatch_session("inspect", session_id, {"session_id": session_id, "selector": selector})

    async def navigate(self, session_id: str, url: str) -> BrowserSession:
        payload = await self._dispatch_session("navigate", session_id, {"session_id": session_id, "url": url})
        self._update_session_url(session_id, payload)
        return payload

    async def dismiss_popups(self, session_id: str):
        payload = await self._dispatch_session("dismiss_popups", session_id, {"session_id": session_id})
        self._update_session_url(session_id, payload)
        return payload

    async def upload(self, session_id: str, selector: str, file_paths: list[str]):
        payload = await self._dispatch_session(
            "upload",
            session_id,
            {"session_id": session_id, "selector": selector, "file_paths": file_paths},
        )
        self._update_session_url(session_id, payload)
        return payload

    async def download(self, session_id: str, trigger_selector: str | None = None, timeout_ms: int = 15000):
        payload = await self._dispatch_session(
            "download",
            session_id,
            {"session_id": session_id, "trigger_selector": trigger_selector, "timeout_ms": timeout_ms},
        )
        self._update_session_url(session_id, payload)
        return payload

    async def scroll_extract(
        self,
        session_id: str,
        selector: str,
        attribute: str | None = None,
        max_scrolls: int = 8,
        scroll_step: int = 700,
    ):
        payload = await self._dispatch_session(
            "scroll_extract",
            session_id,
            {
                "session_id": session_id,
                "selector": selector,
                "attribute": attribute,
                "max_scrolls": max_scrolls,
                "scroll_step": scroll_step,
            },
        )
        self._update_session_url(session_id, payload)
        return payload

    async def close_session(self, session_id: str) -> None:
        worker_id = self._require_worker_id(session_id)
        await self._dispatch(
            worker_id,
            BrowserTaskEnvelope(
                action="close_session",
                session_id=session_id,
                arguments={"session_id": session_id},
            ),
        )
        self._session_workers.pop(session_id, None)
        self._session_urls.pop(session_id, None)
        self._refresh_worker_state(worker_id)

    async def save_session_state(self, session_id: str, run_id: str | None = None):
        return await self._dispatch_session(
            "save_session_state",
            session_id,
            {"session_id": session_id, "run_id": run_id},
        )

    async def restore_session_state(self, session_id: str, worker_id: str | None = None):
        worker_id = worker_id or self._choose_worker_id(session_id=session_id)
        payload = await self._dispatch(
            worker_id,
            BrowserTaskEnvelope(
                action="restore_session_state",
                session_id=session_id,
                arguments={"session_id": session_id},
            ),
        )
        self._session_workers[session_id] = worker_id
        self._update_session_url(session_id, payload)
        self._refresh_worker_state(worker_id)
        return payload

    async def list_sessions(self, agent_id: str | None = None) -> list[BrowserSessionState]:
        sessions: list[BrowserSessionState] = []
        for worker_id in self._workers:
            payload = await self._dispatch(
                worker_id,
                BrowserTaskEnvelope(
                    action="list_sessions",
                    agent_id=agent_id,
                    arguments={"agent_id": agent_id},
                ),
            )
            sessions.extend(payload)
        return sessions

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, object],
        *,
        run_id: str | None = None,
        session_id: str | None = None,
        worker_id: str | None = None,
    ) -> dict[str, object]:
        target_worker_id = worker_id
        if target_worker_id is None and session_id is not None:
            target_worker_id = self._session_workers.get(session_id)
        if target_worker_id is None and run_id is not None:
            target_worker_id = await self._assigned_worker_for_run(run_id)
        if target_worker_id is None:
            target_worker_id = self._choose_worker_id()
        return await self._dispatch(
            target_worker_id,
            BrowserTaskEnvelope(
                action="call_tool",
                session_id=session_id,
                run_id=run_id,
                arguments={"tool_name": tool_name, "arguments": arguments},
            ),
        )

    def current_url(self, session_id: str) -> str:
        url = self._session_urls.get(session_id)
        if url is not None:
            return url
        raise KeyError(f"Unknown session URL: {session_id}")

    def list_workers(self) -> list[BrowserWorkerState]:
        return [worker.state.model_copy(deep=True) for worker in self._workers.values()]

    async def _on_worker_heartbeat(self, worker_id: str) -> None:
        if self._run_store is None:
            return
        leases = await self._run_store.list_leases(worker_id=worker_id)
        for lease in leases:
            await self._run_store.renew_lease(
                lease.run_id,
                lease_timeout_seconds=self._lease_timeout_seconds,
            )

    async def _dispatch_session(self, action: str, session_id: str, arguments: dict[str, Any]):
        worker_id = self._require_worker_id(session_id)
        payload = await self._dispatch(
            worker_id,
            BrowserTaskEnvelope(
                action=action,
                session_id=session_id,
                arguments=arguments,
            ),
        )
        self._refresh_worker_state(worker_id)
        return payload

    async def _dispatch(self, worker_id: str, item: BrowserTaskEnvelope):
        worker = self._workers[worker_id]
        loop = asyncio.get_running_loop()
        future: asyncio.Future[BrowserTaskResult] = loop.create_future()
        self._pending[item.request_id] = future
        await worker.queue.put(item)
        if self._event_publisher is not None:
            await self._event_publisher(
                RuntimeEvent(
                    event_type=EventType.BROWSER_TASK_DISPATCHED,
                    run_id=item.run_id,
                    agent_id=item.agent_id,
                    task_id=item.task_id,
                    session_id=item.session_id,
                    source="browser_worker_pool",
                    payload={
                        "worker_id": worker_id,
                        "request_id": item.request_id,
                        "action": item.action,
                        "queue_name": worker.queue.name,
                    },
                    correlation_id=item.request_id,
                )
            )
        result = await future
        if not result.success:
            raise RuntimeError(result.error or f"Browser worker task failed: {item.action}")
        return result.payload

    async def _handle_result(self, result: BrowserTaskResult) -> None:
        future = self._pending.pop(result.request_id, None)
        if future is not None and not future.done():
            future.set_result(result)

    def _choose_worker_id(self, *, session_id: str | None = None) -> str:
        if session_id is not None and session_id in self._session_workers:
            return self._session_workers[session_id]
        worker_ids = sorted(self._workers)
        if not worker_ids:
            raise RuntimeError("Browser worker pool is not started.")
        worker_id = worker_ids[self._next_worker_index % len(worker_ids)]
        self._next_worker_index += 1
        return worker_id

    def _require_worker_id(self, session_id: str) -> str:
        worker_id = self._session_workers.get(session_id)
        if worker_id is None:
            raise KeyError(f"No browser worker assigned to session: {session_id}")
        return worker_id

    def _refresh_worker_state(self, worker_id: str) -> None:
        worker = self._workers[worker_id]
        worker.state.active_sessions = sum(1 for assigned in self._session_workers.values() if assigned == worker_id)
        worker.state.last_heartbeat = datetime.now(timezone.utc)

    def _update_session_url(self, session_id: str, payload: Any) -> None:
        url: str | None = None
        if hasattr(payload, "page") and hasattr(payload.page, "url"):
            url = str(payload.page.url)
        elif hasattr(payload, "current_url"):
            current_url = getattr(payload, "current_url")
            url = str(current_url) if current_url else None
        if url is not None:
            self._session_urls[session_id] = url

    async def _assigned_worker_for_run(self, run_id: str) -> str | None:
        if self.state_store is None:
            return None
        payload = await self.state_store.get_run(run_id)
        if payload is None:
            return None
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            return None
        worker_id = metadata.get("assigned_worker_id")
        return str(worker_id) if isinstance(worker_id, str) and worker_id else None

    def _default_runtime_factory(self) -> ExecutionPlaneRuntime:
        runtime = ExecutionPlaneRuntime()
        if self.state_store is not None:
            runtime.set_state_store(self.state_store)
        return runtime
