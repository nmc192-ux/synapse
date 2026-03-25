from __future__ import annotations

from synapse.models.memory import MemoryRecord, MemorySearchRequest, MemorySearchResult, MemoryStoreRequest
from synapse.runtime.budget_service import BudgetService
from synapse.runtime.memory import AgentMemoryManager


class MemoryService:
    def __init__(
        self,
        memory_manager: AgentMemoryManager,
        budget_service: BudgetService | None = None,
    ) -> None:
        self.memory_manager = memory_manager
        self.budget_service = budget_service

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
