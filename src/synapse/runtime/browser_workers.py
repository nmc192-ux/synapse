from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from synapse.config import settings
from synapse.models.run import RunStatus
from synapse.models.runtime_event import EventSeverity, EventType, RuntimeEvent
from synapse.models.runtime_state import (
    BrowserSessionState,
    BrowserSessionOwnershipRecord,
    BrowserTaskRequestRecord,
    BrowserTaskResultRecord,
    BrowserWorkerState,
    RunLeaseStatus,
    WorkerHealthStatus,
    WorkerRuntimeStatus,
)
from synapse.runtime.execution_plane import ExecutionPlaneRuntime, RuntimeEventPublisher
from synapse.runtime.queues import BrowserTaskEnvelope, BrowserTaskQueue, BrowserTaskResult, create_browser_task_queue
from synapse.runtime.run_store import RunStore
from synapse.runtime.session import BrowserSession
from synapse.runtime.state_store import RuntimeStateStore
from synapse.workers.browser_worker import BrowserWorker


RuntimeFactory = Callable[[], ExecutionPlaneRuntime]
OwnershipReason = tuple[bool, str | None, str | None]


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
        durable_result_timeout_seconds: float | None = None,
        controller_id: str | None = None,
    ) -> None:
        self.state_store = state_store
        self._event_publisher: RuntimeEventPublisher | None = None
        self.controller_id = controller_id or str(uuid.uuid4())
        self.worker_count = max(1, worker_count or settings.browser_worker_count)
        self.heartbeat_interval_seconds = (
            heartbeat_interval_seconds or settings.browser_worker_heartbeat_interval_seconds
        )
        self._runtime_factory = runtime_factory or self._default_runtime_factory
        self._queue_factory = queue_factory or create_browser_task_queue
        self._run_store = run_store
        self._lease_timeout_seconds = lease_timeout_seconds or settings.scheduler_lease_timeout_seconds
        configured_durable_timeout = durable_result_timeout_seconds or settings.browser_worker_durable_result_timeout_seconds
        self._durable_result_timeout_seconds = max(self._lease_timeout_seconds, configured_durable_timeout)
        self._workers: dict[str, BrowserWorker] = {}
        self._session_workers: dict[str, str] = {}
        self._session_runs: dict[str, str | None] = {}
        self._session_urls: dict[str, str | None] = {}
        self._pending: dict[str, asyncio.Future[BrowserTaskResult]] = {}
        self._request_alerts: set[tuple[str, EventType]] = set()
        self._next_worker_index = 0

    def set_state_store(self, state_store: RuntimeStateStore) -> None:
        self.state_store = state_store
        for worker in self._workers.values():
            if hasattr(worker.runtime, "set_state_store") and worker.runtime is not None:
                worker.runtime.set_state_store(state_store)

    def set_run_store(self, run_store: RunStore | None) -> None:
        self._run_store = run_store

    def set_event_publisher(self, publisher: RuntimeEventPublisher | None) -> None:
        self._event_publisher = publisher
        for worker in self._workers.values():
            worker.set_event_publisher(publisher)

    async def start(self) -> None:
        if self._workers:
            return
        for index in range(self.worker_count):
            worker_id = f"{self.controller_id}:browser-worker-{index + 1}"
            queue_name = f"{settings.browser_worker_queue_prefix}:{worker_id}"
            worker = BrowserWorker(
                worker_id=worker_id,
                queue=self._queue_factory(queue_name),
                runtime_factory=self._runtime_factory,
                result_handler=self._handle_result,
                event_publisher=self._event_publisher,
                heartbeat_interval_seconds=self.heartbeat_interval_seconds,
                heartbeat_callback=self._on_worker_heartbeat,
                request_started_callback=self._on_request_started,
                request_progress_callback=self._on_request_progress,
            )
            self._workers[worker_id] = worker
            await worker.start()
            worker.state.controller_id = self.controller_id
            worker.state.health_status = WorkerHealthStatus.HEALTHY
            await self._persist_worker_state(worker_id)
        await self._recover_durable_state()

    async def stop(self) -> None:
        for worker in self._workers.values():
            await worker.stop()
            await self._persist_worker_state(worker.worker_id)
        self._workers.clear()
        self._session_workers.clear()
        self._session_runs.clear()
        self._session_urls.clear()
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()
        self._request_alerts.clear()

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
        self._session_runs[session_id] = run_id
        await self._persist_session_ownership(session_id, worker_id, run_id=run_id)
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
        self._clear_session_assignment(session_id)
        await self._delete_session_ownership(session_id)
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
        self._session_runs[session_id] = None
        await self._persist_session_ownership(session_id, worker_id, run_id=None)
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
            target_worker_id = await self._choose_dispatchable_worker_id()
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

    async def list_registered_workers(self) -> list[BrowserWorkerState]:
        if self._run_store is not None:
            registered = await self._run_store.list_workers()
            if registered:
                now = datetime.now(timezone.utc)
                dispatchable: list[BrowserWorkerState] = []
                for worker in registered:
                    if worker.controller_id != self.controller_id:
                        continue
                    refreshed = self._with_computed_health(worker, now)
                    if not self._worker_dispatchable(refreshed):
                        continue
                    if refreshed.health_status in {WorkerHealthStatus.HEALTHY, WorkerHealthStatus.DEGRADED}:
                        dispatchable.append(refreshed)
                if dispatchable:
                    return dispatchable
        return self.list_workers()

    @staticmethod
    def _worker_dispatchable(worker: BrowserWorkerState) -> bool:
        drain_state = str(worker.metadata.get("drain_state", "")).lower() if isinstance(worker.metadata, dict) else ""
        if drain_state in {"draining", "maintenance"}:
            return False
        if isinstance(worker.metadata, dict) and worker.metadata.get("dispatchable") is False:
            return False
        return True

    async def _on_worker_heartbeat(self, worker_id: str) -> None:
        if self._run_store is None:
            await self._persist_worker_state(worker_id)
            return
        leases = await self._run_store.list_leases(worker_id=worker_id)
        for lease in leases:
            await self._run_store.renew_lease(
                lease.run_id,
                lease_timeout_seconds=self._lease_timeout_seconds,
                token=lease.token,
            )
        self._refresh_worker_state(worker_id)

    async def _dispatch_session(self, action: str, session_id: str, arguments: dict[str, Any]):
        worker_id = await self._require_worker_id(session_id)
        payload = await self._dispatch(
            worker_id,
            BrowserTaskEnvelope(
                action=action,
                session_id=session_id,
                run_id=self._session_runs.get(session_id),
                arguments=arguments,
            ),
        )
        self._refresh_worker_state(worker_id)
        return payload

    async def _dispatch(self, worker_id: str, item: BrowserTaskEnvelope):
        worker = self._workers.get(worker_id)
        if worker is None:
            await self._emit_worker_event(
                EventType.WORKER_UNAVAILABLE,
                run_id=item.run_id,
                session_id=item.session_id,
                payload={
                    "worker_id": worker_id,
                    "action_id": item.action_id,
                    "request_id": item.request_id or item.action_id,
                    "reason_code": "remote_worker",
                    "reason": "request is assigned to a worker owned by another controller",
                },
                severity=EventSeverity.WARNING,
            )
            raise RuntimeError(
                f"Browser request cannot be dispatched locally because worker {worker_id} is not owned by controller {self.controller_id}."
            )
        existing_result = await self._load_existing_result(item)
        if existing_result is not None:
            if not existing_result.success:
                raise RuntimeError(existing_result.error or f"Browser worker task failed: {item.action}")
            return existing_result.payload
        if item.request_id is None:
            item.request_id = item.action_id
        existing_request = await self._load_existing_request(item.run_id, item.action_id)
        if existing_request is not None and existing_request.status in {
            "queued",
            "dispatched",
            "running",
            "slow",
            "stuck",
            "recovered",
        }:
            abandoned = await self._maybe_finalize_abandoned_request(existing_request)
            if abandoned is None:
                return await self._wait_for_durable_result(existing_request)
        item.fencing_token = await self._current_fencing_token(item.run_id, worker_id)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[BrowserTaskResult] = loop.create_future()
        self._pending[item.action_id] = future
        self._request_alerts.discard((item.action_id, EventType.WORKER_REQUEST_SLOW))
        self._request_alerts.discard((item.action_id, EventType.WORKER_REQUEST_STUCK))
        await self._persist_request(worker_id, item, status="dispatched")
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
                        "action_id": item.action_id,
                        "action": item.action,
                        "queue_name": worker.queue.name,
                        "fencing_token": item.fencing_token,
                    },
                    correlation_id=item.request_id,
                )
            )
        result = await self._await_result(item, future)
        if not result.success:
            raise RuntimeError(result.error or f"Browser worker task failed: {item.action}")
        return result.payload

    async def _handle_result(self, result: BrowserTaskResult) -> None:
        if not await self._accept_result(result):
            return
        future = self._pending.pop(result.action_id, None)
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

    async def _choose_dispatchable_worker_id(self) -> str:
        workers = await self.list_registered_workers()
        if not workers:
            raise RuntimeError("No dispatchable browser workers available.")
        workers.sort(key=lambda item: (item.active_sessions, item.last_heartbeat))
        return workers[0].worker_id

    async def _require_worker_id(self, session_id: str) -> str:
        worker_id = self._session_workers.get(session_id)
        if worker_id is None:
            ownership = await self._load_session_ownership(session_id)
            if ownership is not None:
                if ownership.status == "stale":
                    self._clear_session_assignment(session_id)
                    raise KeyError(f"No browser worker assigned to session: {session_id}")
                stale, reason_code, reason = await self._worker_ownership_status(ownership)
                if stale:
                    await self._emit_worker_event(
                        EventType.WORKER_OWNERSHIP_STALE,
                        session_id=session_id,
                        run_id=ownership.run_id,
                        payload={
                            "session_id": session_id,
                            "worker_id": ownership.worker_id,
                            "controller_id": ownership.controller_id,
                            "reason_code": reason_code,
                            "reason": reason,
                        },
                    )
                    await self._mark_session_ownership_stale(
                        ownership,
                        reason_code=reason_code,
                        reason=reason,
                    )
                else:
                    worker_id = ownership.worker_id
                    self._session_workers[session_id] = worker_id
                    self._session_runs[session_id] = ownership.run_id
                    if ownership.current_url:
                        self._session_urls[session_id] = ownership.current_url
        if worker_id is None:
            raise KeyError(f"No browser worker assigned to session: {session_id}")
        return worker_id

    def _refresh_worker_state(self, worker_id: str) -> None:
        worker = self._workers[worker_id]
        worker.state.active_sessions = sum(1 for assigned in self._session_workers.values() if assigned == worker_id)
        worker.state.last_heartbeat = datetime.now(timezone.utc)
        worker.state.health_status = WorkerHealthStatus.HEALTHY
        worker.state.controller_id = self.controller_id
        worker.state.current_runs = sorted(
            {
                run_id
                for session_id, assigned in self._session_workers.items()
                if assigned == worker_id
                for run_id in [self._session_runs.get(session_id)]
                if run_id is not None
            }
        )
        worker.state.owned_sessions = sorted(
            session_id for session_id, assigned in self._session_workers.items() if assigned == worker_id
        )
        asyncio.create_task(self._persist_worker_state(worker_id))

    def _update_session_url(self, session_id: str, payload: Any) -> None:
        url: str | None = None
        if hasattr(payload, "page") and hasattr(payload.page, "url"):
            url = str(payload.page.url)
        elif hasattr(payload, "current_url"):
            current_url = getattr(payload, "current_url")
            url = str(current_url) if current_url else None
        if url is not None:
            self._session_urls[session_id] = url
            worker_id = self._session_workers.get(session_id)
            run_id = self._session_runs.get(session_id)
            if worker_id is not None:
                asyncio.create_task(self._persist_session_ownership(session_id, worker_id, run_id=run_id, current_url=url))

    async def _assigned_worker_for_run(self, run_id: str) -> str | None:
        if self._run_store is not None:
            lease = await self._run_store.get_lease(run_id)
            if lease is not None:
                return lease.worker_id
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

    async def _persist_worker_state(self, worker_id: str) -> None:
        if self._run_store is None:
            return
        worker = self._workers.get(worker_id)
        if worker is None:
            return
        await self._run_store.save_worker(worker.state.model_copy(deep=True))

    async def _persist_request(self, worker_id: str, item: BrowserTaskEnvelope, *, status: str) -> None:
        if self._run_store is None:
            return
        now = datetime.now(timezone.utc)
        payload = dict(item.arguments)
        payload.update(
            {
                "lifecycle_stage": "dispatched",
                "lifecycle_action": item.action,
                "progress_heartbeat_count": 0,
                "bootstrap_degraded": False,
            }
        )
        await self._run_store.save_worker_request(
            BrowserTaskRequestRecord(
                action_id=item.action_id,
                request_id=item.request_id,
                run_id=item.run_id,
                worker_id=worker_id,
                action=item.action,
                session_id=item.session_id,
                task_id=item.task_id,
                agent_id=item.agent_id,
                fencing_token=item.fencing_token,
                status=status,
                payload=payload,
                dispatched_at=now if status in {"dispatched", "running", "slow", "stuck"} else None,
                started_at=now if status in {"running", "slow", "stuck"} else None,
                last_progress_at=now if status in {"dispatched", "running", "slow", "stuck"} else None,
            )
        )

    async def _on_request_started(self, worker_id: str, item: BrowserTaskEnvelope) -> None:
        request = await self._load_existing_request(item.run_id, item.action_id)
        now = datetime.now(timezone.utc)
        if request is not None:
            payload = {
                **request.payload,
                "lifecycle_stage": "started",
                "lifecycle_started_at": now.isoformat(),
            }
            if request.action == "create_session" and bool(request.payload.get("bootstrap_dispatch_degraded")):
                payload["bootstrap_degraded"] = True
            await self._run_store.save_worker_request(
                request.model_copy(
                    update={
                        "worker_id": worker_id,
                        "status": "running",
                        "payload": payload,
                        "started_at": request.started_at or now,
                        "dispatched_at": request.dispatched_at or now,
                        "last_progress_at": now,
                        "updated_at": now,
                        "status_reason": None,
                    }
                )
            )
        await self._emit_worker_event(
            EventType.WORKER_REQUEST_RUNNING,
            run_id=item.run_id,
            session_id=item.session_id,
            payload={
                "worker_id": worker_id,
                "action_id": item.action_id,
                "request_id": item.request_id,
                "action": item.action,
            },
        )

    async def _on_request_progress(self, worker_id: str, item: BrowserTaskEnvelope) -> None:
        request = await self._load_existing_request(item.run_id, item.action_id)
        if request is None or self._run_store is None:
            return
        now = datetime.now(timezone.utc)
        if request.status not in {"dispatched", "running", "slow", "stuck", "recovered"}:
            return
        progress_count = self._progress_heartbeat_count(request) + 1
        updates: dict[str, object] = {
            "worker_id": worker_id,
            "last_progress_at": now,
            "updated_at": now,
            "payload": {
                **request.payload,
                "lifecycle_stage": "progressing",
                "last_progress_heartbeat_at": now.isoformat(),
                "progress_heartbeat_count": progress_count,
            },
        }
        emitted_event: tuple[EventType, dict[str, object]] | None = None
        if request.status == "slow":
            if request.action == "create_session":
                updates["status_reason"] = "session bootstrap still progressing after degraded delay"
            else:
                updates["status"] = "running"
                updates["status_reason"] = "request resumed after slow progress gap"
                emitted_event = (
                    EventType.WORKER_REQUEST_RUNNING,
                    {
                        "worker_id": worker_id,
                        "action_id": request.action_id,
                        "request_id": request.request_id,
                        "action": request.action,
                        "resumed": True,
                        "previous_status": "slow",
                    },
                )
        elif request.status == "stuck":
            updates["status"] = "recovered"
            updates["recovered_at"] = now
            updates["status_reason"] = "request resumed after stalled progress gap"
            emitted_event = (
                EventType.WORKER_REQUEST_RECOVERED,
                {
                    "worker_id": worker_id,
                    "action_id": request.action_id,
                    "request_id": request.request_id,
                    "action": request.action,
                    "previous_status": "stuck",
                },
            )
        elif request.status == "recovered":
            updates["status"] = "running"
            updates["status_reason"] = "request resumed after controller recovery"
            emitted_event = (
                EventType.WORKER_REQUEST_RUNNING,
                {
                    "worker_id": worker_id,
                    "action_id": request.action_id,
                    "request_id": request.request_id,
                    "action": request.action,
                    "resumed": True,
                    "previous_status": "recovered",
                },
            )
        await self._run_store.save_worker_request(request.model_copy(update=updates))
        if emitted_event is not None:
            event_type, payload = emitted_event
            await self._emit_worker_event(
                event_type,
                run_id=request.run_id,
                session_id=request.session_id,
                payload=payload,
            )

    async def _accept_result(self, result: BrowserTaskResult) -> bool:
        if self._run_store is None:
            return True
        if not await self._run_store.validate_fencing_token(result.run_id, result.worker_id, result.fencing_token):
            await self._reject_stale_result(result)
            return False
        existing = await self._run_store.get_worker_result(result.run_id, result.action_id)
        if existing is not None:
            await self._emit_worker_event(
                EventType.WORKER_RESULT_REPLAYED,
                run_id=result.run_id,
                payload={
                    "worker_id": result.worker_id,
                    "action_id": result.action_id,
                    "request_id": result.request_id or result.action_id,
                },
            )
            return True
        payload = result.payload if isinstance(result.payload, dict) else {"value": result.payload}
        await self._run_store.save_worker_result(
            BrowserTaskResultRecord(
                action_id=result.action_id,
                request_id=result.request_id,
                run_id=result.run_id,
                worker_id=result.worker_id,
                action=result.action,
                session_id=result.session_id,
                success=result.success,
                payload=payload,
                error=result.error,
                fencing_token=result.fencing_token,
                status="completed" if result.success else "failed",
                completed_at=result.completed_at,
            )
        )
        request = await self._run_store.get_worker_request(result.run_id, result.action_id)
        if request is not None:
            now = datetime.now(timezone.utc)
            completion_reason = self._completion_status_reason(request, success=result.success, error=result.error)
            await self._run_store.save_worker_request(
                request.model_copy(
                    update={
                        "status": "completed" if result.success else "failed",
                        "completed_at": now,
                        "last_progress_at": now,
                        "updated_at": now,
                        "status_reason": completion_reason,
                    }
                )
            )
        self._request_alerts.discard((result.action_id, EventType.WORKER_REQUEST_SLOW))
        self._request_alerts.discard((result.action_id, EventType.WORKER_REQUEST_STUCK))
        return True

    async def _load_existing_result(self, item: BrowserTaskEnvelope) -> BrowserTaskResult | None:
        if self._run_store is None:
            return None
        existing = await self._run_store.get_worker_result(item.run_id, item.action_id)
        if existing is None:
            return None
        return BrowserTaskResult(
            action_id=existing.action_id,
            request_id=existing.request_id,
            worker_id=existing.worker_id,
            action=existing.action,
            run_id=existing.run_id,
            session_id=existing.session_id,
            success=existing.success,
            payload=existing.payload,
            error=existing.error,
            fencing_token=existing.fencing_token,
            completed_at=existing.completed_at,
        )

    async def _load_existing_request(self, run_id: str | None, action_id: str) -> BrowserTaskRequestRecord | None:
        if self._run_store is None:
            return None
        return await self._run_store.get_worker_request(run_id, action_id)

    async def _current_fencing_token(self, run_id: str | None, worker_id: str) -> int | None:
        if self._run_store is None or run_id is None:
            return None
        lease = await self._run_store.get_lease(run_id)
        if lease is None or lease.worker_id != worker_id:
            return None
        return lease.token

    async def _await_result(
        self,
        item: BrowserTaskEnvelope,
        future: asyncio.Future[BrowserTaskResult],
    ) -> BrowserTaskResult:
        poll_interval = min(0.25, max(self.heartbeat_interval_seconds, 0.05))
        started = datetime.now(timezone.utc)
        while True:
            remaining = self._durable_result_timeout_seconds - (datetime.now(timezone.utc) - started).total_seconds()
            if remaining <= 0:
                await self._maybe_mark_request_stuck(item)
                return await self._wait_for_durable_result(item)
            try:
                return await asyncio.wait_for(asyncio.shield(future), timeout=min(poll_interval, remaining))
            except TimeoutError:
                await self._maybe_mark_request_slow(item)

    async def _wait_for_durable_result(
        self,
        item_or_request: BrowserTaskEnvelope | BrowserTaskRequestRecord,
    ) -> BrowserTaskResult:
        if self._run_store is None:
            raise RuntimeError("Durable worker result tracking is not configured.")
        run_id = item_or_request.run_id
        action_id = item_or_request.action_id
        started = datetime.now(timezone.utc)
        while (datetime.now(timezone.utc) - started).total_seconds() <= self._durable_result_timeout_seconds:
            existing = await self._run_store.get_worker_result(run_id, action_id)
            if existing is not None:
                return BrowserTaskResult(
                    action_id=existing.action_id,
                    request_id=existing.request_id,
                    worker_id=existing.worker_id,
                    action=existing.action,
                    run_id=existing.run_id,
                    session_id=existing.session_id,
                    success=existing.success,
                    payload=existing.payload,
                    error=existing.error,
                    fencing_token=existing.fencing_token,
                    completed_at=existing.completed_at,
                )
            await self._maybe_mark_request_stuck(item_or_request)
            request = await self._request_record(item_or_request)
            if request is not None:
                if request.status == "operator_required":
                    raise TimeoutError(request.status_reason or f"Request requires operator intervention: {action_id}")
                abandoned = await self._maybe_finalize_abandoned_request(request)
                if abandoned is not None:
                    raise TimeoutError(abandoned.status_reason or f"Durable worker result abandoned: {action_id}")
            await asyncio.sleep(0.05)
        raise TimeoutError(f"Timed out waiting for durable worker result: {action_id}")

    async def _maybe_mark_request_slow(self, item_or_request: BrowserTaskEnvelope | BrowserTaskRequestRecord) -> None:
        if self._run_store is None:
            return
        request = await self._request_record(item_or_request)
        if request is None or request.status not in {"dispatched", "running"}:
            return
        anchor = request.started_at or request.dispatched_at or request.created_at
        age_seconds = (datetime.now(timezone.utc) - anchor).total_seconds()
        threshold = max(self.heartbeat_interval_seconds * 2, self._lease_timeout_seconds / 2)
        if age_seconds < threshold:
            return
        alert_key = (request.action_id, EventType.WORKER_REQUEST_SLOW)
        if alert_key in self._request_alerts:
            return
        self._request_alerts.add(alert_key)
        now = datetime.now(timezone.utc)
        payload = dict(request.payload)
        slow_reason = self._slow_status_reason(request, threshold)
        payload.update(
            {
                "lifecycle_stage": "slow",
                "bootstrap_degraded": request.action == "create_session",
                "bootstrap_degraded_at": now.isoformat() if request.action == "create_session" else payload.get("bootstrap_degraded_at"),
                "bootstrap_dispatch_degraded": request.action == "create_session" and request.started_at is None,
                "bootstrap_dispatch_degraded_at": (
                    now.isoformat()
                    if request.action == "create_session" and request.started_at is None
                    else payload.get("bootstrap_dispatch_degraded_at")
                ),
            }
        )
        updated = request.model_copy(
            update={
                "status": "slow",
                "status_reason": slow_reason,
                "payload": payload,
                "updated_at": now,
            }
        )
        await self._run_store.save_worker_request(updated)
        await self._emit_worker_event(
            EventType.WORKER_REQUEST_SLOW,
            run_id=request.run_id,
            session_id=request.session_id,
            payload={
                "worker_id": request.worker_id,
                "action_id": request.action_id,
                "request_id": request.request_id,
                "status": updated.status,
                "age_seconds": round(age_seconds, 3),
            },
            severity=EventSeverity.WARNING,
        )

    async def _maybe_mark_request_stuck(self, item_or_request: BrowserTaskEnvelope | BrowserTaskRequestRecord) -> None:
        if self._run_store is None:
            return
        request = await self._request_record(item_or_request)
        if request is None or request.status not in {"dispatched", "running", "slow", "stuck", "recovered"}:
            return
        anchor = request.last_progress_at or request.started_at or request.dispatched_at or request.created_at
        age_seconds = (datetime.now(timezone.utc) - anchor).total_seconds()
        if age_seconds < self._lease_timeout_seconds:
            return
        ownership_conflict_count = self._ownership_conflict_count(request)
        bootstrap_degraded = bool(request.payload.get("bootstrap_degraded")) if isinstance(request.payload, dict) else False
        bootstrap_dispatch_degraded = bool(request.payload.get("bootstrap_dispatch_degraded")) if isinstance(request.payload, dict) else False
        progress_heartbeats = self._progress_heartbeat_count(request)
        if request.status == "recovered":
            await self._mark_request_operator_required(
                request,
                reason="request stalled again after a recovery path and requires operator intervention",
                reason_code="repeat_stall_after_recovery",
                age_seconds=age_seconds,
            )
            await self._maybe_escalate_run_for_ownership_conflicts(
                request.run_id,
                reason="request stalled again after a recovery path and requires operator intervention",
            )
            return
        if ownership_conflict_count >= 2:
            await self._mark_request_operator_required(
                request,
                reason="repeated session ownership conflicts require operator intervention",
                reason_code="ownership_conflict",
                age_seconds=age_seconds,
            )
            await self._maybe_escalate_run_for_ownership_conflicts(
                request.run_id,
                reason="repeated session ownership conflicts require operator intervention",
            )
            return
        if request.action == "create_session" and (request.started_at is None or bootstrap_dispatch_degraded):
            await self._mark_request_operator_required(
                request,
                reason="session bootstrap did not start on a worker before timeout and requires operator intervention",
                reason_code="session_bootstrap_not_started",
                age_seconds=age_seconds,
            )
            return
        if request.action == "create_session" and (
            request.status == "slow" or bootstrap_degraded or progress_heartbeats > 0
        ):
            await self._mark_request_operator_required(
                request,
                reason="session bootstrap stalled after degraded progress and requires operator intervention",
                reason_code="session_bootstrap_stalled",
                age_seconds=age_seconds,
            )
            return
        if request.action == "create_session" and request.started_at is not None and progress_heartbeats == 0:
            await self._mark_request_operator_required(
                request,
                reason="session bootstrap started on a worker but did not make durable progress before timeout and requires operator intervention",
                reason_code="session_bootstrap_started_no_progress",
                age_seconds=age_seconds,
            )
            return
        if self._should_force_aged_degraded_operator_review(request, age_seconds):
            await self._mark_request_operator_required(
                request,
                reason=self._aged_degraded_reason(request),
                reason_code="aged_degraded_request",
                age_seconds=age_seconds,
            )
            return
        alert_key = (request.action_id, EventType.WORKER_REQUEST_STUCK)
        if alert_key in self._request_alerts:
            return
        self._request_alerts.add(alert_key)
        now = datetime.now(timezone.utc)
        updated = request.model_copy(
            update={
                "status": "stuck",
                "status_reason": self._stuck_status_reason(request),
                "updated_at": now,
            }
        )
        await self._run_store.save_worker_request(updated)
        await self._emit_worker_event(
            EventType.WORKER_REQUEST_STUCK,
            run_id=request.run_id,
            session_id=request.session_id,
            payload={
                "worker_id": request.worker_id,
                "action_id": request.action_id,
                "request_id": request.request_id,
                "status": updated.status,
                "age_seconds": round(age_seconds, 3),
            },
            severity=EventSeverity.WARNING,
        )

    def _stuck_status_reason(self, request: BrowserTaskRequestRecord) -> str:
        progress_heartbeats = self._progress_heartbeat_count(request)
        if request.action == "create_session":
            return f"session bootstrap exceeded {self._lease_timeout_seconds:.2f}s without durable completion"
        if request.started_at is not None and progress_heartbeats == 0:
            return f"request started on a worker but reported no durable progress within {self._lease_timeout_seconds:.2f}s"
        if progress_heartbeats > 0:
            return f"request stopped reporting durable progress before completion within {self._lease_timeout_seconds:.2f}s"
        return f"request exceeded {self._lease_timeout_seconds:.2f}s without a durable result"

    @staticmethod
    def _slow_status_reason(request: BrowserTaskRequestRecord, threshold: float) -> str:
        if request.action == "create_session" and request.started_at is None:
            return f"session bootstrap has not started on a worker after {threshold:.2f}s"
        if request.action == "create_session":
            return f"session bootstrap exceeded {threshold:.2f}s without durable completion"
        return f"request exceeded {threshold:.2f}s without a durable result"

    @staticmethod
    def _progress_heartbeat_count(request: BrowserTaskRequestRecord) -> int:
        raw = request.payload.get("progress_heartbeat_count")
        if isinstance(raw, int):
            return raw
        if isinstance(raw, float):
            return int(raw)
        if isinstance(raw, str):
            try:
                return int(raw)
            except ValueError:
                return 0
        return 0

    def _should_force_aged_degraded_operator_review(
        self,
        request: BrowserTaskRequestRecord,
        age_seconds: float,
    ) -> bool:
        if request.status not in {"slow", "stuck"}:
            return False
        if request.run_id is None:
            return False
        return age_seconds >= self._aged_degraded_threshold_seconds()

    def _aged_degraded_threshold_seconds(self) -> float:
        return max(self._lease_timeout_seconds * 3, 0.15)

    def _aged_degraded_reason(self, request: BrowserTaskRequestRecord) -> str:
        progress_heartbeats = self._progress_heartbeat_count(request)
        if request.started_at is not None and progress_heartbeats > 0:
            return "request remained degraded after repeated progress heartbeats and requires operator intervention"
        if request.started_at is not None:
            return "request remained degraded after worker start without durable convergence and requires operator intervention"
        return "request remained degraded without durable convergence and requires operator intervention"

    async def _request_record(
        self,
        item_or_request: BrowserTaskEnvelope | BrowserTaskRequestRecord,
    ) -> BrowserTaskRequestRecord | None:
        if isinstance(item_or_request, BrowserTaskRequestRecord):
            return item_or_request
        return await self._load_existing_request(item_or_request.run_id, item_or_request.action_id)

    async def _maybe_finalize_abandoned_request(
        self,
        request: BrowserTaskRequestRecord,
    ) -> BrowserTaskRequestRecord | None:
        if self._run_store is None or request.run_id is None:
            return None
        if request.status not in {"stuck", "recovered"}:
            return None
        lease = await self._run_store.get_lease(request.run_id)
        reason_code: str | None = None
        reason: str | None = None
        if lease is None:
            reason_code = "lease_missing"
            reason = "durable result wait ended because the run lease is no longer present"
        elif lease.status != RunLeaseStatus.ACTIVE:
            reason_code = "lease_inactive"
            reason = "durable result wait ended because the run lease is no longer active"
        elif lease.worker_id != request.worker_id:
            reason_code = "lease_moved"
            reason = f"durable result wait ended after lease ownership moved to {lease.worker_id}"
        if reason_code is None or reason is None:
            return None
        now = datetime.now(timezone.utc)
        updated = request.model_copy(
            update={
                "status": "abandoned",
                "status_reason": reason,
                "updated_at": now,
                "completed_at": request.completed_at or now,
            }
        )
        await self._run_store.save_worker_request(updated)
        self._request_alerts.discard((request.action_id, EventType.WORKER_REQUEST_SLOW))
        self._request_alerts.discard((request.action_id, EventType.WORKER_REQUEST_STUCK))
        await self._emit_worker_event(
            EventType.WORKER_UNAVAILABLE,
            run_id=request.run_id,
            session_id=request.session_id,
            payload={
                "worker_id": request.worker_id,
                "action_id": request.action_id,
                "request_id": request.request_id,
                "reason_code": reason_code,
                "reason": reason,
            },
            severity=EventSeverity.WARNING,
        )
        return updated

    async def _lease_conflict_for_request(
        self,
        request: BrowserTaskRequestRecord,
    ) -> tuple[str | None, str | None]:
        if self._run_store is None or request.run_id is None:
            return None, None
        lease = await self._run_store.get_lease(request.run_id)
        if lease is None:
            return "lease_missing", "durable result wait ended because the run lease is no longer present"
        if lease.status != RunLeaseStatus.ACTIVE:
            return "lease_inactive", "durable result wait ended because the run lease is no longer active"
        if lease.worker_id != request.worker_id:
            return "lease_moved", f"durable result wait ended after lease ownership moved to {lease.worker_id}"
        return None, None

    @staticmethod
    def _ownership_conflict_count(request: BrowserTaskRequestRecord) -> int:
        raw = request.payload.get("ownership_conflict_count")
        return raw if isinstance(raw, int) else 0

    async def _reject_stale_result(self, result: BrowserTaskResult) -> None:
        if self._run_store is None:
            return
        request = await self._run_store.get_worker_request(result.run_id, result.action_id)
        if request is None or request.status in {"completed", "failed"}:
            return
        reason = "late worker result rejected because lease ownership changed before durable completion"
        updated = request.model_copy(
            update={
                "status": "abandoned",
                "status_reason": reason,
                "updated_at": datetime.now(timezone.utc),
                "completed_at": request.completed_at or datetime.now(timezone.utc),
            }
        )
        await self._run_store.save_worker_request(updated)
        self._request_alerts.discard((request.action_id, EventType.WORKER_REQUEST_SLOW))
        self._request_alerts.discard((request.action_id, EventType.WORKER_REQUEST_STUCK))
        await self._emit_worker_event(
            EventType.WORKER_UNAVAILABLE,
            run_id=request.run_id,
            session_id=request.session_id,
            payload={
                "worker_id": result.worker_id,
                "action_id": result.action_id,
                "request_id": result.request_id or result.action_id,
                "reason_code": "stale_fencing_result",
                "reason": reason,
            },
            severity=EventSeverity.WARNING,
        )

    async def _mark_request_operator_required(
        self,
        request: BrowserTaskRequestRecord,
        *,
        reason: str,
        reason_code: str,
        age_seconds: float | None = None,
    ) -> BrowserTaskRequestRecord | None:
        if self._run_store is None:
            return None
        if request.status == "operator_required" and request.status_reason == reason:
            return request
        now = datetime.now(timezone.utc)
        updated = request.model_copy(
            update={
                "status": "operator_required",
                "status_reason": reason,
                "updated_at": now,
            }
        )
        await self._run_store.save_worker_request(updated)
        self._request_alerts.discard((request.action_id, EventType.WORKER_REQUEST_SLOW))
        self._request_alerts.discard((request.action_id, EventType.WORKER_REQUEST_STUCK))
        await self._emit_worker_event(
            EventType.BROWSER_HUMAN_INTERVENTION_REQUIRED,
            run_id=request.run_id,
            session_id=request.session_id,
            payload={
                "worker_id": request.worker_id,
                "action_id": request.action_id,
                "request_id": request.request_id,
                "reason_code": reason_code,
                "reason": reason,
                "age_seconds": round(age_seconds, 3) if age_seconds is not None else None,
            },
            severity=EventSeverity.WARNING,
        )
        return updated

    def _completion_status_reason(
        self,
        request: BrowserTaskRequestRecord,
        *,
        success: bool,
        error: str | None,
    ) -> str | None:
        if not success:
            return error or request.status_reason
        if request.status == "slow":
            return "completed after slow progress gap"
        if request.status == "stuck":
            return "completed after stalled progress gap"
        if request.status == "recovered":
            return "completed after controller recovery delay"
        if isinstance(request.status_reason, str) and request.status_reason:
            lowered = request.status_reason.lower()
            if "slow" in lowered:
                return "completed after slow progress gap"
            if "stall" in lowered or "stuck" in lowered:
                return "completed after stalled progress gap"
            if "recover" in lowered:
                return "completed after controller recovery delay"
        return None

    async def _persist_session_ownership(
        self,
        session_id: str,
        worker_id: str,
        *,
        run_id: str | None,
        current_url: str | None = None,
        status: str = "active",
    ) -> None:
        if self._run_store is None:
            return
        existing = await self._run_store.get_session_ownership(session_id)
        project_id = None
        if run_id is not None:
            try:
                run = await self._run_store.get(run_id)
            except KeyError:
                run = None
            if run is not None:
                project_id = run.project_id
        await self._run_store.save_session_ownership(
            BrowserSessionOwnershipRecord(
                session_id=session_id,
                worker_id=worker_id,
                controller_id=self.controller_id,
                run_id=run_id,
                project_id=project_id or (existing.project_id if existing is not None else None),
                current_url=current_url or self._session_urls.get(session_id),
                status=status,
                updated_at=datetime.now(timezone.utc),
                created_at=existing.created_at if existing is not None else datetime.now(timezone.utc),
            )
        )

    async def _delete_session_ownership(self, session_id: str) -> None:
        self._clear_session_assignment(session_id)
        if self._run_store is not None:
            await self._run_store.delete_session_ownership(session_id)

    async def _load_session_ownership(self, session_id: str) -> BrowserSessionOwnershipRecord | None:
        if self._run_store is None:
            return None
        return await self._run_store.get_session_ownership(session_id)

    async def _mark_session_ownership_stale(
        self,
        ownership: BrowserSessionOwnershipRecord,
        *,
        reason_code: str | None,
        reason: str | None,
    ) -> None:
        if self._run_store is None:
            return
        self._clear_session_assignment(ownership.session_id)
        if ownership.status == "stale" and ownership.status_reason == reason:
            return
        await self._run_store.save_session_ownership(
            ownership.model_copy(
                update={
                    "status": "stale",
                    "status_reason": reason,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
        )
        active_requests = await self._run_store.list_worker_requests(session_id=ownership.session_id)
        now = datetime.now(timezone.utc)
        for request in active_requests:
            if request.status not in {"queued", "dispatched", "running", "slow", "recovered"}:
                continue
            next_conflict_count = self._ownership_conflict_count(request) + 1
            updated_payload = {
                **request.payload,
                "ownership_conflict_count": next_conflict_count,
                "ownership_conflict_reason_code": reason_code,
                "ownership_conflict_reason": reason,
            }
            lease_reason_code, lease_reason = await self._lease_conflict_for_request(request)
            if lease_reason_code is not None and lease_reason is not None:
                updated = request.model_copy(
                    update={
                        "status": "abandoned",
                        "status_reason": lease_reason,
                        "payload": updated_payload,
                        "updated_at": now,
                        "completed_at": request.completed_at or now,
                    }
                )
                await self._run_store.save_worker_request(updated)
                self._request_alerts.discard((request.action_id, EventType.WORKER_REQUEST_SLOW))
                self._request_alerts.discard((request.action_id, EventType.WORKER_REQUEST_STUCK))
                await self._emit_worker_event(
                    EventType.WORKER_UNAVAILABLE,
                    run_id=request.run_id,
                    session_id=request.session_id,
                    payload={
                        "worker_id": request.worker_id,
                        "action_id": request.action_id,
                        "request_id": request.request_id,
                        "reason_code": lease_reason_code,
                        "reason": lease_reason,
                    },
                    severity=EventSeverity.WARNING,
                )
                continue
            if request.status == "recovered" or next_conflict_count >= 2:
                operator_reason = (
                    "repeated session ownership conflicts require operator intervention"
                    if next_conflict_count >= 2 and request.status != "recovered"
                    else "request requires operator intervention after ownership conflict during recovery"
                )
                operator_request = request.model_copy(update={"payload": updated_payload})
                await self._mark_request_operator_required(
                    operator_request,
                    reason=operator_reason,
                    reason_code=reason_code or "ownership_conflict",
                )
                await self._maybe_escalate_run_for_ownership_conflicts(operator_request.run_id, reason=operator_reason)
                continue
            updated = request.model_copy(
                update={
                    "status": "stuck",
                    "status_reason": self._ownership_request_reason(reason),
                    "payload": updated_payload,
                    "updated_at": now,
                }
            )
            await self._run_store.save_worker_request(updated)
            alert_key = (request.action_id, EventType.WORKER_REQUEST_STUCK)
            self._request_alerts.add(alert_key)
            await self._emit_worker_event(
                EventType.WORKER_REQUEST_STUCK,
                run_id=request.run_id,
                session_id=request.session_id,
                payload={
                    "worker_id": request.worker_id,
                    "action_id": request.action_id,
                    "request_id": request.request_id,
                    "status": updated.status,
                    "reason_code": reason_code,
                    "reason": reason,
                },
                severity=EventSeverity.WARNING,
            )

    async def _worker_ownership_status(self, ownership: BrowserSessionOwnershipRecord) -> OwnershipReason:
        if self._run_store is None:
            if ownership.worker_id in self._workers:
                return False, None, None
            return True, "worker_missing", "worker is not available in the current controller process"
        workers = await self._run_store.list_workers()
        for worker in workers:
            if worker.worker_id != ownership.worker_id:
                continue
            refreshed = self._with_computed_health(worker, datetime.now(timezone.utc))
            if refreshed.controller_id != self.controller_id:
                return True, "foreign_controller", "session is owned by a different controller"
            if refreshed.health_status == WorkerHealthStatus.STALE:
                return True, "worker_stale", "owning worker heartbeat is stale"
            if refreshed.health_status == WorkerHealthStatus.UNAVAILABLE:
                return True, "worker_unavailable", "owning worker is unavailable"
            return False, None, None
        if ownership.worker_id in self._workers and ownership.controller_id == self.controller_id:
            return False, None, None
        return True, "worker_missing", "owning worker is missing from the durable registry"

    async def _maybe_escalate_run_for_ownership_conflicts(self, run_id: str | None, *, reason: str) -> None:
        if self._run_store is None or run_id is None:
            return
        summary = await self._run_store.ownership_conflict_summary(run_id)
        if summary["operator_required_requests"] == 0 and summary["repeated_conflicts"] < 2:
            return
        try:
            run = await self._run_store.get(run_id)
        except KeyError:
            return
        if run.status == RunStatus.WAITING_FOR_OPERATOR:
            return
        await self._run_store.set_operator_intervention(
            run_id,
            intervention={
                "reason": reason,
                "payload": {
                    "source": "browser_workers",
                    "category": "runtime_recovery",
                    "ownership_conflict_summary": summary,
                    "ui": {
                        "operator_required": True,
                        "action_label": "Review Run",
                        "reason": reason,
                        "category": "runtime_recovery",
                        "run_context": {
                            "run_id": run.run_id,
                            "task_id": run.task_id,
                            "agent_id": run.agent_id,
                            "goal": run.metadata.get("goal"),
                        },
                    },
                },
            },
            status=RunStatus.WAITING_FOR_OPERATOR,
        )
        await self._run_store.update_metadata(
            run_id,
            {
                "operator_review_reason": reason,
                "assigned_worker_id": None,
                "lease_expires_at": None,
                "lease_token": None,
                "ownership_conflict_summary": summary,
            },
        )

    def _ownership_request_reason(self, reason: str | None) -> str:
        base = "session ownership conflict blocked continued dispatch"
        if isinstance(reason, str) and reason:
            return f"{base}: {reason}"
        return base

    def _clear_session_assignment(self, session_id: str) -> None:
        self._session_workers.pop(session_id, None)
        self._session_runs.pop(session_id, None)
        self._session_urls.pop(session_id, None)

    def _with_computed_health(self, worker: BrowserWorkerState, now: datetime) -> BrowserWorkerState:
        age = (now - worker.last_heartbeat).total_seconds()
        if worker.status in {WorkerRuntimeStatus.OFFLINE, WorkerRuntimeStatus.FAILED}:
            health = WorkerHealthStatus.UNAVAILABLE
        elif age > self.heartbeat_interval_seconds * 4:
            health = WorkerHealthStatus.STALE
        elif age > self.heartbeat_interval_seconds * 2:
            health = WorkerHealthStatus.DEGRADED
        else:
            health = WorkerHealthStatus.HEALTHY
        return worker.model_copy(update={"health_status": health})

    async def _recover_durable_state(self) -> None:
        if self._run_store is None:
            return
        for ownership in await self._run_store.list_session_ownerships(controller_id=self.controller_id):
            if ownership.worker_id in self._workers and ownership.status == "active":
                self._session_workers[ownership.session_id] = ownership.worker_id
                self._session_runs[ownership.session_id] = ownership.run_id
                if ownership.current_url is not None:
                    self._session_urls[ownership.session_id] = ownership.current_url
            else:
                stale, reason_code, reason = await self._worker_ownership_status(ownership)
                if stale:
                    await self._emit_worker_event(
                        EventType.WORKER_OWNERSHIP_STALE,
                        session_id=ownership.session_id,
                        run_id=ownership.run_id,
                        payload={
                            **ownership.model_dump(mode="json"),
                            "reason_code": reason_code,
                            "reason": reason,
                            "stale": True,
                        },
                    )
                    await self._mark_session_ownership_stale(
                        ownership,
                        reason_code=reason_code,
                        reason=reason,
                    )
        for worker in self._workers:
            requests = await self._run_store.list_worker_requests(worker_id=worker)
            for request in requests:
                if request.status not in {"queued", "dispatched", "running", "slow", "stuck"}:
                    continue
                result = await self._run_store.get_worker_result(request.run_id, request.action_id)
                if result is not None:
                    await self._run_store.save_worker_request(
                        request.model_copy(update={"status": result.status, "updated_at": datetime.now(timezone.utc)})
                    )
                    await self._emit_worker_event(
                        EventType.WORKER_RESULT_REPLAYED,
                        run_id=request.run_id,
                        session_id=request.session_id,
                        payload={
                            "worker_id": request.worker_id,
                            "action_id": request.action_id,
                            "request_id": request.request_id,
                        },
                    )
                else:
                    now = datetime.now(timezone.utc)
                    await self._run_store.save_worker_request(
                        request.model_copy(
                            update={
                                "status": "recovered",
                                "recovered_at": now,
                                "updated_at": now,
                                "status_reason": "controller restart recovered in-flight request",
                            }
                        )
                    )
                    await self._emit_worker_event(
                        EventType.WORKER_REQUEST_RECOVERED,
                        run_id=request.run_id,
                        session_id=request.session_id,
                        payload={
                            "worker_id": request.worker_id,
                            "action_id": request.action_id,
                            "request_id": request.request_id,
                        },
                    )
        recovered_run_ids = {
            ownership.run_id
            for ownership in await self._run_store.list_session_ownerships(controller_id=self.controller_id)
            if ownership.run_id is not None and ownership.status == "active"
        }
        for run_id in sorted(recovered_run_ids):
            await self._emit_worker_event(
                EventType.RUN_DISPATCH_RECONCILED,
                run_id=run_id,
                payload={"controller_id": self.controller_id, "recovered": True},
            )

    async def _emit_worker_event(
        self,
        event_type: EventType,
        *,
        run_id: str | None = None,
        session_id: str | None = None,
        payload: dict[str, object],
        severity: EventSeverity = EventSeverity.INFO,
    ) -> None:
        if self._event_publisher is None:
            return
        await self._event_publisher(
            RuntimeEvent(
                event_type=event_type,
                run_id=run_id,
                session_id=session_id,
                source="browser_worker_pool",
                payload=payload,
                severity=severity,
                correlation_id=str(payload.get("request_id") or payload.get("action_id") or run_id or session_id),
            )
        )
