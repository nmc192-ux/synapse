from __future__ import annotations

import asyncpg

from synapse.config import settings
from synapse.models.memory import MemoryRecord, MemorySearchRequest, MemorySearchResult, MemoryStoreRequest


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
            INSERT INTO synapse_memory (memory_id, agent_id, memory_type, content, embedding, timestamp)
            VALUES ($1, $2, $3, $4, $5::vector, $6)
            RETURNING memory_id, agent_id, memory_type, content, embedding::text AS embedding, timestamp
            """,
            request.memory_id,
            request.agent_id,
            request.memory_type.value,
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
                SELECT memory_id, agent_id, memory_type, content, embedding::text AS embedding, timestamp,
                       1 - (embedding <=> $2::vector) AS score
                FROM synapse_memory
                WHERE agent_id = $1
                  AND ($3::text IS NULL OR memory_type = $3)
                ORDER BY embedding <=> $2::vector
                LIMIT $4
                """,
                request.agent_id,
                self._vector_literal(request.embedding),
                request.memory_type.value if request.memory_type else None,
                request.limit,
            )
        else:
            rows = await pool.fetch(
                """
                SELECT memory_id, agent_id, memory_type, content, embedding::text AS embedding, timestamp,
                       CASE
                         WHEN $2::text IS NULL OR $2 = '' THEN 0.0
                         ELSE similarity(content, $2)
                       END AS score
                FROM synapse_memory
                WHERE agent_id = $1
                  AND ($3::text IS NULL OR memory_type = $3)
                ORDER BY score DESC, timestamp DESC
                LIMIT $4
                """,
                request.agent_id,
                request.query,
                request.memory_type.value if request.memory_type else None,
                request.limit,
            )

        return [MemorySearchResult(memory=self._to_record(row), score=float(row["score"])) for row in rows]

    async def get_recent(self, agent_id: str, limit: int = 10) -> list[MemoryRecord]:
        pool = self._require_pool()
        rows = await pool.fetch(
            """
            SELECT memory_id, agent_id, memory_type, content, embedding::text AS embedding, timestamp
            FROM synapse_memory
            WHERE agent_id = $1
            ORDER BY timestamp DESC
            LIMIT $2
            """,
            agent_id,
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
                memory_type TEXT NOT NULL,
                content TEXT NOT NULL,
                embedding VECTOR NOT NULL,
                timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        await pool.execute(
            """
            CREATE INDEX IF NOT EXISTS synapse_memory_agent_timestamp_idx
            ON synapse_memory (agent_id, timestamp DESC)
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
            memory_type=row["memory_type"],
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
