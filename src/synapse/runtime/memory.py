from __future__ import annotations

import asyncpg
from collections import defaultdict

from synapse.config import settings
from synapse.models.memory import MemoryRecord, MemoryScope, MemorySearchRequest, MemorySearchResult, MemoryStoreRequest, MemoryType


class AgentMemoryManager:
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

    async def store(self, request: MemoryStoreRequest) -> MemoryRecord:
        pool = self._require_pool()
        row = await pool.fetchrow(
            """
            INSERT INTO synapse_memory (
                memory_id,
                agent_id,
                run_id,
                task_id,
                memory_type,
                memory_scope,
                content,
                embedding,
                timestamp
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::vector, $9)
            RETURNING
                memory_id,
                agent_id,
                run_id,
                task_id,
                memory_type,
                memory_scope,
                content,
                embedding::text AS embedding,
                timestamp
            """,
            request.memory_id,
            request.agent_id,
            request.run_id,
            request.task_id,
            request.memory_type.value,
            request.memory_scope.value if request.memory_scope else None,
            request.content,
            self._vector_literal(request.embedding),
            request.timestamp,
        )
        return self._to_record(row)

    async def search(self, request: MemorySearchRequest) -> list[MemorySearchResult]:
        pool = self._require_pool()

        if request.embedding:
            rows = await pool.fetch(
                """
                SELECT memory_id, agent_id, run_id, task_id, memory_type, memory_scope, content, embedding::text AS embedding, timestamp,
                       1 - (embedding <=> $2::vector) AS score
                FROM synapse_memory
                WHERE agent_id = $1
                  AND ($3::text IS NULL OR run_id = $3)
                  AND ($4::text IS NULL OR task_id = $4)
                  AND ($5::text IS NULL OR memory_type = $5)
                  AND ($6::text IS NULL OR memory_scope = $6)
                ORDER BY embedding <=> $2::vector
                LIMIT $7
                """,
                request.agent_id,
                self._vector_literal(request.embedding),
                request.run_id,
                request.task_id,
                request.memory_type.value if request.memory_type else None,
                request.memory_scope.value if request.memory_scope else None,
                request.limit,
            )
        else:
            rows = await pool.fetch(
                """
                SELECT memory_id, agent_id, run_id, task_id, memory_type, memory_scope, content, embedding::text AS embedding, timestamp,
                       CASE
                        WHEN $2::text IS NULL OR $2 = '' THEN 0.0
                        ELSE similarity(content, $2)
                       END AS score
                FROM synapse_memory
                WHERE agent_id = $1
                  AND ($3::text IS NULL OR run_id = $3)
                  AND ($4::text IS NULL OR task_id = $4)
                  AND ($5::text IS NULL OR memory_type = $5)
                  AND ($6::text IS NULL OR memory_scope = $6)
                ORDER BY score DESC, timestamp DESC
                LIMIT $7
                """,
                request.agent_id,
                request.query,
                request.run_id,
                request.task_id,
                request.memory_type.value if request.memory_type else None,
                request.memory_scope.value if request.memory_scope else None,
                request.limit,
            )

        return [MemorySearchResult(memory=self._to_record(row), score=float(row["score"])) for row in rows]

    async def get_recent(
        self,
        agent_id: str,
        limit: int = 10,
        *,
        run_id: str | None = None,
        task_id: str | None = None,
        memory_scope: MemoryScope | None = None,
    ) -> list[MemoryRecord]:
        pool = self._require_pool()
        rows = await pool.fetch(
            """
            SELECT memory_id, agent_id, run_id, task_id, memory_type, memory_scope, content, embedding::text AS embedding, timestamp
            FROM synapse_memory
            WHERE agent_id = $1
              AND ($2::text IS NULL OR run_id = $2)
              AND ($3::text IS NULL OR task_id = $3)
              AND ($4::text IS NULL OR memory_scope = $4)
            ORDER BY timestamp DESC
            LIMIT $5
            """,
            agent_id,
            run_id,
            task_id,
            memory_scope.value if memory_scope else None,
            limit,
        )
        return [self._to_record(row) for row in rows]

    async def get_recent_by_type(
        self,
        agent_id: str,
        limit_per_type: int = 4,
        *,
        run_id: str | None = None,
        task_id: str | None = None,
        scopes: list[MemoryScope] | None = None,
    ) -> dict[MemoryType, list[MemoryRecord]]:
        pool = self._require_pool()
        scope_values = [scope.value for scope in scopes] if scopes else None
        rows = await pool.fetch(
            """
            SELECT memory_id, agent_id, run_id, task_id, memory_type, memory_scope, content, embedding::text AS embedding, timestamp
            FROM (
                SELECT
                    memory_id,
                    agent_id,
                    run_id,
                    task_id,
                    memory_type,
                    memory_scope,
                    content,
                    embedding,
                    timestamp,
                    ROW_NUMBER() OVER (PARTITION BY memory_type ORDER BY timestamp DESC) AS row_number
                FROM synapse_memory
                WHERE agent_id = $1
                  AND ($2::text IS NULL OR run_id = $2)
                  AND ($3::text IS NULL OR task_id = $3)
                  AND ($4::text[] IS NULL OR memory_scope = ANY($4))
            ) AS ranked
            WHERE row_number <= $5
            ORDER BY timestamp DESC
            """,
            agent_id,
            run_id,
            task_id,
            scope_values,
            limit_per_type,
        )
        grouped: dict[MemoryType, list[MemoryRecord]] = defaultdict(list)
        for row in rows:
            record = self._to_record(row)
            grouped[record.memory_type].append(record)
        return dict(grouped)

    async def get_run_memory(self, run_id: str, limit: int = 100) -> list[MemoryRecord]:
        pool = self._require_pool()
        rows = await pool.fetch(
            """
            SELECT memory_id, agent_id, run_id, task_id, memory_type, memory_scope, content, embedding::text AS embedding, timestamp
            FROM synapse_memory
            WHERE run_id = $1
            ORDER BY timestamp DESC
            LIMIT $2
            """,
            run_id,
            limit,
        )
        return [self._to_record(row) for row in rows]

    async def _ensure_schema(self) -> None:
        pool = self._require_pool()
        await pool.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await pool.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        await pool.execute(
            """
            CREATE TABLE IF NOT EXISTS synapse_memory (
                memory_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                run_id TEXT,
                task_id TEXT,
                memory_type TEXT NOT NULL,
                memory_scope TEXT,
                content TEXT NOT NULL,
                embedding VECTOR NOT NULL,
                timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        await pool.execute("ALTER TABLE synapse_memory ADD COLUMN IF NOT EXISTS run_id TEXT")
        await pool.execute("ALTER TABLE synapse_memory ADD COLUMN IF NOT EXISTS task_id TEXT")
        await pool.execute("ALTER TABLE synapse_memory ADD COLUMN IF NOT EXISTS memory_scope TEXT")
        await pool.execute(
            """
            CREATE INDEX IF NOT EXISTS synapse_memory_agent_timestamp_idx
            ON synapse_memory (agent_id, timestamp DESC)
            """
        )
        await pool.execute(
            """
            CREATE INDEX IF NOT EXISTS synapse_memory_run_timestamp_idx
            ON synapse_memory (run_id, timestamp DESC)
            """
        )

    def _require_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Memory manager is not started.")
        return self._pool

    def _to_record(self, row: asyncpg.Record) -> MemoryRecord:
        return MemoryRecord(
            memory_id=row["memory_id"],
            agent_id=row["agent_id"],
            run_id=row["run_id"],
            task_id=row["task_id"],
            memory_type=row["memory_type"],
            memory_scope=row["memory_scope"],
            content=row["content"],
            embedding=self._parse_vector(row["embedding"]),
            timestamp=row["timestamp"],
        )

    def _vector_literal(self, embedding: list[float]) -> str:
        values = embedding or [0.0]
        return "[" + ",".join(f"{float(value):.12g}" for value in values) + "]"

    def _parse_vector(self, value: str | None) -> list[float]:
        if not value:
            return []
        stripped = value.strip()[1:-1]
        if not stripped:
            return []
        return [float(item) for item in stripped.split(",")]
