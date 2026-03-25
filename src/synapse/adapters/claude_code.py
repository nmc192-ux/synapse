from synapse.adapters.base import AgentAdapter
from synapse.models.task import TaskRequest, TaskResult


class ClaudeCodeAdapter(AgentAdapter):
    async def execute_task(self, task: TaskRequest) -> TaskResult:
        return await self.loop.run(task)
