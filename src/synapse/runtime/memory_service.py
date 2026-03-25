from __future__ import annotations

from typing import TYPE_CHECKING

from synapse.models.memory import MemoryRecord, MemorySearchRequest, MemorySearchResult, MemoryStoreRequest
from synapse.runtime.memory import AgentMemoryManager
from synapse.runtime.state_store import RuntimeStateStore

if TYPE_CHECKING:
    from synapse.runtime.budget_service import BudgetService


class MemoryService:
    def __init__(
        self,
        memory_manager: AgentMemoryManager,
        budget_service: BudgetService | None = None,
        state_store: RuntimeStateStore | None = None,
    ) -> None:
        self.memory_manager = memory_manager
        self.budget_service = budget_service
        self.state_store = state_store

    def set_state_store(self, state_store: RuntimeStateStore | None) -> None:
        self.state_store = state_store

    async def store(self, request: MemoryStoreRequest) -> MemoryRecord:
        record = await self.memory_manager.store(request)
        if self.budget_service is not None:
            try:
                await self.budget_service.increment_memory_write(request.agent_id)
            except KeyError:
                pass
        return record

    async def search(self, request: MemorySearchRequest) -> list[MemorySearchResult]:
        return await self.memory_manager.search(request)

    async def get_recent(self, agent_id: str, limit: int = 10) -> list[MemoryRecord]:
        return await self.memory_manager.get_recent(agent_id, limit)

    async def summarize_recent(self, agent_id: str, limit: int = 5) -> str:
        recent = await self.get_recent(agent_id, limit=limit)
        if not recent:
            return "No memory available."
        return "\n".join(record.content for record in recent if record.content.strip())

    async def get_recent_memory_dicts(self, agent_id: str, limit: int = 5) -> list[dict[str, object]]:
        recent = await self.get_recent(agent_id, limit=limit)
        return [record.model_dump(mode="json") for record in recent]

    async def get_recent_runtime_events(
        self,
        agent_id: str,
        task_id: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, object]]:
        if self.state_store is None:
            return []
        return await self.state_store.get_runtime_events(agent_id=agent_id, task_id=task_id, limit=limit)
