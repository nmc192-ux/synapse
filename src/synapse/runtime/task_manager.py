from __future__ import annotations

from datetime import datetime, timezone

import asyncpg

from synapse.config import settings
from synapse.models.task import (
    TaskClaimRequest,
    TaskCreateRequest,
    TaskRecord,
    TaskStatus,
    TaskUpdateRequest,
)


class TaskExecutionManager:
    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or settings.postgres_dsn
        self._pool: asyncpg.Pool | None = None

    async def start(self) -> None:
        if self._pool is not None:
            return

        self._pool = await asyncpg.create_pool(dsn=self._dsn)
        await self._ensure_schema()

    async def stop(self) -> None:
        if self._pool is None:
            return
        await self._pool.close()
        self._pool = None

    async def create_task(self, request: TaskCreateRequest) -> TaskRecord:
        pool = self._require_pool()
        timestamp = datetime.now(timezone.utc)
        row = await pool.fetchrow(
            """
            INSERT INTO synapse_tasks (id, goal, constraints, status, assigned_agent, result, timestamp)
            VALUES ($1, $2, $3::jsonb, $4, $5, $6::jsonb, $7)
            RETURNING id, goal, constraints, status, assigned_agent, result, timestamp
            """,
            request.id,
            request.goal,
            request.constraints,
            TaskStatus.CLAIMED.value if request.assigned_agent else TaskStatus.PENDING.value,
            request.assigned_agent,
            {},
            timestamp,
        )
        return self._to_record(row)

    async def claim_task(self, task_id: str, request: TaskClaimRequest) -> TaskRecord:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    """
                    SELECT id, goal, constraints, status, assigned_agent, result, timestamp
                    FROM synapse_tasks
                    WHERE id = $1
                    FOR UPDATE
                    """,
                    task_id,
                )
                if row is None:
                    raise KeyError(f"Task not found: {task_id}")

                if row["assigned_agent"] and row["assigned_agent"] != request.assigned_agent:
                    raise ValueError(f"Task {task_id} is already assigned to {row['assigned_agent']}.")

                updated = await connection.fetchrow(
                    """
                    UPDATE synapse_tasks
                    SET assigned_agent = $2, status = $3
                    WHERE id = $1
                    RETURNING id, goal, constraints, status, assigned_agent, result, timestamp
                    """,
                    task_id,
                    request.assigned_agent,
                    TaskStatus.CLAIMED.value,
                )
        return self._to_record(updated)

    async def update_task(self, task_id: str, request: TaskUpdateRequest) -> TaskRecord:
        pool = self._require_pool()
        row = await pool.fetchrow(
            """
            UPDATE synapse_tasks
            SET status = $2,
                assigned_agent = COALESCE($3, assigned_agent),
                result = CASE
                    WHEN result IS NULL THEN $4::jsonb
                    ELSE result || $4::jsonb
                END,
                timestamp = $5
            WHERE id = $1
            RETURNING id, goal, constraints, status, assigned_agent, result, timestamp
            """,
            task_id,
            request.status.value,
            request.assigned_agent,
            request.result,
            request.timestamp,
        )
        if row is None:
            raise KeyError(f"Task not found: {task_id}")
        return self._to_record(row)

    async def list_active_tasks(self) -> list[TaskRecord]:
        pool = self._require_pool()
        rows = await pool.fetch(
            """
            SELECT id, goal, constraints, status, assigned_agent, result, timestamp
            FROM synapse_tasks
            WHERE status = ANY($1::text[])
            ORDER BY timestamp DESC
            """,
            [TaskStatus.PENDING.value, TaskStatus.CLAIMED.value, TaskStatus.RUNNING.value],
        )
        return [self._to_record(row) for row in rows]

    async def _ensure_schema(self) -> None:
        pool = self._require_pool()
        await pool.execute(
            """
            CREATE TABLE IF NOT EXISTS synapse_tasks (
                id TEXT PRIMARY KEY,
                goal TEXT NOT NULL,
                constraints JSONB NOT NULL DEFAULT '{}'::jsonb,
                status TEXT NOT NULL,
                assigned_agent TEXT NULL,
                result JSONB NOT NULL DEFAULT '{}'::jsonb,
                timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

    def _require_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Task manager is not started.")
        return self._pool

    def _to_record(self, row: asyncpg.Record) -> TaskRecord:
        return TaskRecord(
            id=row["id"],
            goal=row["goal"],
            constraints=dict(row["constraints"] or {}),
            status=TaskStatus(row["status"]),
            assigned_agent=row["assigned_agent"],
            result=dict(row["result"] or {}),
            timestamp=row["timestamp"],
        )
