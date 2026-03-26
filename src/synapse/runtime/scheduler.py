from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field

from synapse.config import settings
from synapse.models.run import RunState, RunStatus
from synapse.models.runtime_event import EventSeverity, EventType
from synapse.models.runtime_state import BrowserWorkerState, RunLeaseRecord, WorkerRuntimeStatus
from synapse.runtime.event_bus import EventBus
from synapse.runtime.run_store import RunStore


class RunLease(BaseModel):
    run_id: str
    worker_id: str
    leased_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
    attempts: int = 1
    next_retry_at: datetime | None = None


class RunScheduler:
    def __init__(
        self,
        run_store: RunStore,
        browser_workers,
        events: EventBus,
        *,
        lease_timeout_seconds: float | None = None,
        cleanup_interval_seconds: float | None = None,
        max_assignment_retries: int | None = None,
        retry_base_delay_seconds: float | None = None,
    ) -> None:
        self.run_store = run_store
        self.browser_workers = browser_workers
        self.events = events
        self.lease_timeout_seconds = lease_timeout_seconds or settings.scheduler_lease_timeout_seconds
        self.cleanup_interval_seconds = cleanup_interval_seconds or settings.scheduler_cleanup_interval_seconds
        self.max_assignment_retries = max_assignment_retries or settings.scheduler_max_assignment_retries
        self.retry_base_delay_seconds = retry_base_delay_seconds or settings.scheduler_retry_base_delay_seconds
        self._cleanup_task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop(self) -> None:
        self._running = False
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

    async def assign_run(self, run_id: str) -> RunLease:
        run = await self.run_store.get(run_id)
        await self.cleanup_expired_leases()
        existing = await self._load_lease(run_id)
        if existing is not None and self._worker_available(existing.worker_id):
            return await self.renew_lease(run_id)
        next_retry_at = run.metadata.get("next_retry_at")
        if isinstance(next_retry_at, str):
            retry_at = datetime.fromisoformat(next_retry_at)
            if retry_at > datetime.now(timezone.utc):
                raise RuntimeError("Run is waiting for retry backoff.")

        worker = self._select_worker()
        if worker is None:
            await self._emit_worker_unavailable(run)
            await self.requeue_run(run_id, reason="No browser workers available.")
            raise RuntimeError("No browser workers available.")

        attempts = self._assignment_attempts(run) + 1
        lease = RunLease(
            run_id=run_id,
            worker_id=worker.worker_id,
            leased_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=self.lease_timeout_seconds),
            attempts=attempts,
        )
        await self._persist_lease(lease)
        await self.run_store.update_status(
            run_id,
            RunStatus.RUNNING if run.status != RunStatus.PAUSED else RunStatus.RESUMED,
            current_phase=run.current_phase or "assigned",
            metadata={
                "assigned_worker_id": worker.worker_id,
                "assignment_attempts": attempts,
                "lease_expires_at": lease.expires_at.isoformat(),
                "next_retry_at": None,
            },
        )
        await self.events.emit(
            EventType.RUN_ASSIGNED,
            run_id=run_id,
            agent_id=run.agent_id,
            task_id=run.task_id,
            source="scheduler",
            payload={
                "worker_id": worker.worker_id,
                "lease_expires_at": lease.expires_at.isoformat(),
                "attempts": attempts,
            },
            correlation_id=run.correlation_id,
        )
        return lease

    async def renew_lease(self, run_id: str) -> RunLease:
        run = await self.run_store.get(run_id)
        lease = await self._load_lease(run_id)
        if lease is None:
            raise KeyError(f"Run lease not found: {run_id}")
        record = await self.run_store.renew_lease(
            run_id,
            lease_timeout_seconds=self.lease_timeout_seconds,
        )
        lease = self._from_record(record)
        await self.run_store.update_metadata(
            run_id,
            {"lease_expires_at": lease.expires_at.isoformat(), "assigned_worker_id": lease.worker_id},
        )
        return lease

    async def release_run(self, run_id: str) -> None:
        await self.run_store.delete_lease(run_id)
        await self.run_store.update_metadata(run_id, {"lease_expires_at": None, "assigned_worker_id": None})

    async def mark_assignment_failed(self, run_id: str, *, reason: str) -> RunLease:
        run = await self.run_store.get(run_id)
        attempts = self._assignment_attempts(run) + 1
        if attempts > self.max_assignment_retries:
            await self.run_store.update_status(
                run_id,
                RunStatus.FAILED,
                current_phase="failed",
                metadata={"assignment_failure_reason": reason, "assignment_attempts": attempts},
            )
            raise RuntimeError(reason)
        await self.run_store.update_metadata(
            run_id,
            {"assignment_failure_reason": reason, "assignment_attempts": attempts},
        )
        return await self.requeue_run(run_id, reason=reason, attempts=attempts, reassign=True)

    async def requeue_run(
        self,
        run_id: str,
        *,
        reason: str,
        attempts: int | None = None,
        reassign: bool = False,
        recovered: bool = False,
    ) -> RunLease | None:
        await self.run_store.delete_lease(run_id)
        run = await self.run_store.get(run_id)
        next_attempts = attempts if attempts is not None else self._assignment_attempts(run)
        backoff_seconds = self._retry_delay_seconds(next_attempts)
        next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=backoff_seconds)
        await self.run_store.update_status(
            run_id,
            RunStatus.PENDING,
            current_phase="queued",
            metadata={
                "assigned_worker_id": None,
                "lease_expires_at": None,
                "requeue_reason": reason,
                "assignment_attempts": next_attempts,
                "next_retry_at": next_retry_at.isoformat(),
                "retry_backoff_seconds": backoff_seconds,
            },
        )
        await self.events.emit(
            EventType.RUN_REQUEUED,
            run_id=run_id,
            agent_id=run.agent_id,
            task_id=run.task_id,
            source="scheduler",
            payload={"reason": reason, "attempts": next_attempts, "backoff_seconds": backoff_seconds},
            severity=EventSeverity.WARNING,
            correlation_id=run.correlation_id,
        )
        if recovered:
            await self.events.emit(
                EventType.RUN_RECOVERED,
                run_id=run_id,
                agent_id=run.agent_id,
                task_id=run.task_id,
                source="scheduler",
                payload={"reason": reason, "attempts": next_attempts, "backoff_seconds": backoff_seconds},
                severity=EventSeverity.WARNING,
                correlation_id=run.correlation_id,
            )
        if reassign:
            if backoff_seconds > 0:
                await asyncio.sleep(backoff_seconds)
            return await self.assign_run(run_id)
        return None

    async def cleanup_expired_leases(self) -> list[str]:
        now = datetime.now(timezone.utc)
        leases = await self.run_store.list_expired_leases(now=now)
        expired = [lease.run_id for lease in leases if lease.lease_expiration <= now]
        for run_id in expired:
            await self.requeue_run(run_id, reason="Worker lease expired.", reassign=True, recovered=True)
        return expired

    async def _cleanup_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self.cleanup_interval_seconds)
            await self.cleanup_expired_leases()

    def _select_worker(self) -> BrowserWorkerState | None:
        workers = [
            worker
            for worker in self.browser_workers.list_workers()
            if worker.status not in {WorkerRuntimeStatus.OFFLINE, WorkerRuntimeStatus.FAILED}
        ]
        if not workers:
            return None
        workers.sort(key=lambda item: (item.active_sessions, item.last_heartbeat))
        return workers[0]

    def _worker_available(self, worker_id: str) -> bool:
        return any(
            worker.worker_id == worker_id
            and worker.status not in {WorkerRuntimeStatus.OFFLINE, WorkerRuntimeStatus.FAILED}
            for worker in self.browser_workers.list_workers()
        )

    @staticmethod
    def _assignment_attempts(run: RunState) -> int:
        attempts = run.metadata.get("assignment_attempts")
        return int(attempts) if isinstance(attempts, int) else 0

    async def _emit_worker_unavailable(self, run: RunState) -> None:
        await self.events.emit(
            EventType.WORKER_UNAVAILABLE,
            run_id=run.run_id,
            agent_id=run.agent_id,
            task_id=run.task_id,
            source="scheduler",
            payload={"reason": "No browser workers available."},
            severity=EventSeverity.WARNING,
            correlation_id=run.correlation_id,
        )

    async def renew_worker_leases(self, worker_id: str) -> None:
        leases = await self.run_store.list_leases(worker_id=worker_id)
        for lease in leases:
            if self._worker_available(worker_id):
                await self.renew_lease(lease.run_id)

    async def _persist_lease(self, lease: RunLease) -> None:
        await self.run_store.save_lease(
            RunLeaseRecord(
                run_id=lease.run_id,
                worker_id=lease.worker_id,
                lease_acquired_at=lease.leased_at,
                lease_expiration=lease.expires_at,
                attempts=lease.attempts,
                next_retry_at=lease.next_retry_at,
            )
        )

    async def _load_lease(self, run_id: str) -> RunLease | None:
        record = await self.run_store.get_lease(run_id)
        if record is None:
            return None
        return self._from_record(record)

    @staticmethod
    def _from_record(record: RunLeaseRecord) -> RunLease:
        return RunLease(
            run_id=record.run_id,
            worker_id=record.worker_id,
            leased_at=record.lease_acquired_at,
            expires_at=record.lease_expiration,
            attempts=record.attempts,
            next_retry_at=record.next_retry_at,
        )

    def _retry_delay_seconds(self, attempts: int) -> float:
        exponent = max(0, attempts - 1)
        return round(self.retry_base_delay_seconds * (2**exponent), 3)
