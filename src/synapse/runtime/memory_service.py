from __future__ import annotations

import math
from collections import defaultdict
from typing import TYPE_CHECKING

from synapse.models.memory import MemoryRecord, MemoryScope, MemorySearchRequest, MemorySearchResult, MemoryStoreRequest, MemoryType
from synapse.models.runtime_event import EventType
from synapse.runtime.compression.base import CompressionProvider
from synapse.runtime.compression.noop import NoOpCompressionProvider
from synapse.runtime.llm import estimate_token_count
from synapse.runtime.memory import AgentMemoryManager
from synapse.runtime.state_store import RuntimeStateStore

if TYPE_CHECKING:
    from synapse.runtime.budget_service import BudgetService
    from synapse.runtime.event_bus import EventBus


class MemoryService:
    def __init__(
        self,
        memory_manager: AgentMemoryManager,
        budget_service: BudgetService | None = None,
        state_store: RuntimeStateStore | None = None,
        events: EventBus | None = None,
        compression_provider: CompressionProvider | None = None,
    ) -> None:
        self.memory_manager = memory_manager
        self.budget_service = budget_service
        self.state_store = state_store
        self.events = events
        self.compression_provider = compression_provider or NoOpCompressionProvider()

    def set_state_store(self, state_store: RuntimeStateStore | None) -> None:
        self.state_store = state_store

    async def store(self, request: MemoryStoreRequest) -> MemoryRecord:
        scoped_request = self._with_default_scope(request)
        record = await self.memory_manager.store(scoped_request)
        if self.budget_service is not None:
            try:
                await self.budget_service.increment_memory_write(scoped_request.agent_id, run_id=scoped_request.run_id)
            except KeyError:
                pass
        return record

    async def search(self, request: MemorySearchRequest) -> list[MemorySearchResult]:
        return await self.memory_manager.search(request)

    async def get_recent(
        self,
        agent_id: str,
        limit: int = 10,
        *,
        run_id: str | None = None,
        task_id: str | None = None,
        memory_scope: MemoryScope | None = None,
    ) -> list[MemoryRecord]:
        return await self.memory_manager.get_recent(
            agent_id,
            limit,
            run_id=run_id,
            task_id=task_id,
            memory_scope=memory_scope,
        )

    async def summarize_recent(self, agent_id: str, limit: int = 5) -> str:
        recent = await self.get_recent(agent_id, limit=limit)
        if not recent:
            return "No memory available."
        return "\n".join(record.content for record in recent if record.content.strip())

    async def get_recent_memory_dicts(self, agent_id: str, limit: int = 5) -> list[dict[str, object]]:
        recent = await self.get_recent(agent_id, limit=limit)
        return [record.model_dump(mode="json") for record in recent]

    async def get_planner_memory_context(
        self,
        agent_id: str,
        *,
        run_id: str | None = None,
        task_id: str | None = None,
        limit_per_type: int = 4,
    ) -> dict[str, object]:
        grouped = await self._get_scoped_planner_memories(
            agent_id,
            run_id=run_id,
            task_id=task_id,
            limit_per_type=limit_per_type,
        )
        ordered_types = [MemoryType.SHORT_TERM, MemoryType.TASK, MemoryType.LONG_TERM]
        retrieved_records = [
            record
            for memory_type in ordered_types
            for record in grouped.get(memory_type, [])
        ]
        deduplicated = self._deduplicate_memories(retrieved_records)
        grouped_deduplicated = self._group_memories(deduplicated, group_by="type")

        compressed_clusters: list[dict[str, object]] = []
        summary_lines: list[str] = []
        for memory_type in ordered_types:
            cluster = grouped_deduplicated.get(memory_type, [])
            if not cluster:
                continue
            summary = await self.compression_provider.summarize_memory(
                cluster,
                context={
                    "agent_id": agent_id,
                    "run_id": run_id,
                    "task_id": task_id,
                    "memory_type": memory_type.value,
                    "channel": "planner_memory",
                },
            )
            compressed_clusters.append(
                {
                    "memory_type": memory_type.value,
                    "summary": summary,
                    "entries": cluster,
                }
            )
            summary_text = self._summary_text(summary)
            if summary_text:
                summary_lines.append(f"{memory_type.value}: {summary_text}")

        retrieved_count = len(retrieved_records)
        compressed_count = sum(len(cluster["entries"]) for cluster in compressed_clusters)
        raw_token_estimate = estimate_token_count([record.model_dump(mode="json") for record in retrieved_records])
        compressed_token_estimate = estimate_token_count(compressed_clusters)
        compression_ratio = round((compressed_count / retrieved_count) if retrieved_count else 1.0, 4)
        token_ratio = round((compressed_token_estimate / raw_token_estimate) if raw_token_estimate else 1.0, 4)

        payload = {
            "agent_id": agent_id,
            "run_id": run_id,
            "task_id": task_id,
            "memory_summary": "\n".join(summary_lines) if summary_lines else "No memory available.",
            "memories": compressed_clusters,
            "retrieved_memory_count": retrieved_count,
            "compressed_memory_count": compressed_count,
            "memory_compression_ratio": compression_ratio,
            "raw_memory_token_estimate": raw_token_estimate,
            "compressed_memory_token_estimate": compressed_token_estimate,
            "memory_token_ratio": token_ratio,
        }
        if self.events is not None:
            await self.events.emit(
                EventType.MEMORY_COMPRESSED,
                run_id=run_id,
                agent_id=agent_id,
                task_id=task_id,
                source="memory_service",
                payload=payload,
                correlation_id=task_id,
            )
        return payload

    async def get_recent_runtime_events(
        self,
        agent_id: str,
        run_id: str | None = None,
        task_id: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, object]]:
        if self.state_store is None:
            return []
        return await self.state_store.get_runtime_events(run_id=run_id, agent_id=agent_id, task_id=task_id, limit=limit)

    async def get_run_memory(self, run_id: str, limit: int = 100) -> list[MemoryRecord]:
        return await self.memory_manager.get_run_memory(run_id, limit=limit)

    async def summarize_run_context(self, run_id: str, limit: int = 25) -> dict[str, object]:
        records = await self.get_run_memory(run_id, limit=limit)
        if not records:
            return {
                "run_id": run_id,
                "summary": "No run memory available.",
                "memories": [],
                "retrieved_memory_count": 0,
                "compressed_memory_count": 0,
                "memory_compression_ratio": 1.0,
            }

        deduplicated = self._deduplicate_memories(records)
        grouped = self._group_memories(deduplicated, group_by="scope")
        summaries: list[dict[str, object]] = []
        summary_lines: list[str] = []

        for scope in (MemoryScope.RUN, MemoryScope.TASK, MemoryScope.AGENT, MemoryScope.LONG_TERM):
            cluster = grouped.get(scope.value, [])
            if not cluster:
                continue
            summary = await self.compression_provider.summarize_memory(
                cluster,
                context={"run_id": run_id, "memory_scope": scope.value, "channel": "run_summary"},
            )
            summaries.append({"memory_scope": scope.value, "summary": summary, "entries": cluster})
            summary_text = self._summary_text(summary)
            if summary_text:
                summary_lines.append(f"{scope.value}: {summary_text}")

        return {
            "run_id": run_id,
            "summary": "\n".join(summary_lines) if summary_lines else "No run memory available.",
            "memories": summaries,
            "retrieved_memory_count": len(records),
            "compressed_memory_count": len(deduplicated),
            "memory_compression_ratio": round(len(deduplicated) / len(records), 4) if records else 1.0,
        }

    async def _get_scoped_planner_memories(
        self,
        agent_id: str,
        *,
        run_id: str | None,
        task_id: str | None,
        limit_per_type: int,
    ) -> dict[MemoryType, list[MemoryRecord]]:
        merged: dict[MemoryType, list[MemoryRecord]] = defaultdict(list)

        async def add_records(records_by_type: dict[MemoryType, list[MemoryRecord]]) -> None:
            for memory_type, records in records_by_type.items():
                bucket = merged[memory_type]
                seen_ids = {record.memory_id for record in bucket}
                for record in records:
                    if record.memory_id in seen_ids:
                        continue
                    bucket.append(record)
                    seen_ids.add(record.memory_id)
                    if len(bucket) >= limit_per_type:
                        break

        if run_id is not None:
            await add_records(
                await self.memory_manager.get_recent_by_type(
                    agent_id,
                    limit_per_type=limit_per_type,
                    run_id=run_id,
                    scopes=[MemoryScope.RUN],
                )
            )
        if task_id is not None:
            await add_records(
                await self.memory_manager.get_recent_by_type(
                    agent_id,
                    limit_per_type=limit_per_type,
                    task_id=task_id,
                    scopes=[MemoryScope.TASK],
                )
            )
        await add_records(
            await self.memory_manager.get_recent_by_type(
                agent_id,
                limit_per_type=limit_per_type,
                scopes=[MemoryScope.AGENT, MemoryScope.LONG_TERM],
            )
        )
        return dict(merged)

    def _deduplicate_memories(self, records: list[MemoryRecord]) -> list[MemoryRecord]:
        clusters: list[list[MemoryRecord]] = []
        for record in records:
            placed = False
            for cluster in clusters:
                if self._is_similar_memory(record, cluster[0]):
                    cluster.append(record)
                    placed = True
                    break
            if not placed:
                clusters.append([record])

        deduplicated: list[MemoryRecord] = []
        for cluster in clusters:
            cluster.sort(key=self._salience_score, reverse=True)
            deduplicated.append(cluster[0])
        deduplicated.sort(key=self._salience_score, reverse=True)
        return deduplicated

    def _group_memories(self, records: list[MemoryRecord], *, group_by: str) -> dict[str, list[dict[str, object]]]:
        grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
        for record in records:
            if group_by == "scope":
                grouping_key = record.memory_scope.value if record.memory_scope else record.memory_type.value
            else:
                grouping_key = record.memory_type.value
            grouped[grouping_key].append(
                {
                    "memory_id": record.memory_id,
                    "memory_type": record.memory_type.value,
                    "memory_scope": record.memory_scope.value if record.memory_scope else None,
                    "run_id": record.run_id,
                    "task_id": record.task_id,
                    "content": record.content,
                    "timestamp": record.timestamp.isoformat(),
                    "salience": round(self._salience_score(record), 4),
                }
            )
        return dict(grouped)

    def _salience_score(self, record: MemoryRecord) -> float:
        type_weights = {
            "short_term": 1.0,
            "task": 2.0,
            "long_term": 3.0,
        }
        content = record.content.lower()
        signal_words = ("important", "remember", "success", "failed", "error", "warning", "blocked")
        signal_bonus = sum(0.35 for word in signal_words if word in content)
        recency_bonus = record.timestamp.timestamp() / 1_000_000_000_000
        return type_weights.get(record.memory_type.value, 1.0) + signal_bonus + recency_bonus

    def _is_similar_memory(self, left: MemoryRecord, right: MemoryRecord) -> bool:
        if left.memory_type != right.memory_type:
            return False
        if left.memory_scope != right.memory_scope:
            return False
        if left.run_id != right.run_id and MemoryScope.RUN in {left.memory_scope, right.memory_scope}:
            return False
        left_text = self._normalize_content(left.content)
        right_text = self._normalize_content(right.content)
        if left_text == right_text:
            return True
        if left_text[:96] == right_text[:96]:
            return True
        if left.embedding and right.embedding and len(left.embedding) == len(right.embedding):
            return self._cosine_similarity(left.embedding, right.embedding) >= 0.985
        return False

    @staticmethod
    def _normalize_content(content: str) -> str:
        return " ".join(content.lower().split())

    @staticmethod
    def _cosine_similarity(left: list[float], right: list[float]) -> float:
        numerator = sum(l * r for l, r in zip(left, right, strict=False))
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return numerator / (left_norm * right_norm)

    @staticmethod
    def _summary_text(summary: object) -> str:
        if isinstance(summary, str):
            return summary
        if isinstance(summary, dict):
            if isinstance(summary.get("summary"), str):
                return str(summary["summary"])
            if isinstance(summary.get("count"), int):
                return f"{summary['count']} summarized memories"
        return ""

    @staticmethod
    def _with_default_scope(request: MemoryStoreRequest) -> MemoryStoreRequest:
        if request.memory_scope is not None:
            return request
        if request.memory_type == MemoryType.LONG_TERM:
            return request.model_copy(update={"memory_scope": MemoryScope.LONG_TERM})
        if request.task_id is not None and request.memory_type == MemoryType.TASK:
            return request.model_copy(update={"memory_scope": MemoryScope.TASK})
        if request.run_id is not None:
            return request.model_copy(update={"memory_scope": MemoryScope.RUN})
        return request.model_copy(update={"memory_scope": MemoryScope.AGENT})
