from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from synapse.models.agent import AgentDefinition
from synapse.models.task import TaskRequest, TaskResult
from synapse.runtime.agent_loop import EventDrivenAgentLoop
from synapse.runtime.budget_service import BudgetService
from synapse.runtime.compression.base import CompressionProvider
from synapse.runtime.llm import LLMProvider
from synapse.runtime.memory_service import MemoryService
from synapse.runtime.security import AgentSecuritySandbox
from synapse.runtime.safety import AgentSafetyLayer
from synapse.transports.websocket_manager import WebSocketManager

if TYPE_CHECKING:
    from synapse.runtime.browser import BrowserRuntime


class AgentAdapter(ABC):
    def __init__(
        self,
        definition: AgentDefinition,
        browser: "BrowserRuntime",
        sockets: WebSocketManager,
        sandbox: AgentSecuritySandbox,
        safety: AgentSafetyLayer,
        memory_service: MemoryService,
        budget_service: BudgetService,
        llm: LLMProvider | None = None,
        compression_provider: CompressionProvider | None = None,
    ) -> None:
        self.definition = definition
        self.loop = EventDrivenAgentLoop(
            definition=definition,
            browser=browser,
            sockets=sockets,
            sandbox=sandbox,
            safety=safety,
            memory_service=memory_service,
            budget_service=budget_service,
            llm=llm,
            compression_provider=compression_provider,
        )

    @abstractmethod
    async def execute_task(self, task: TaskRequest) -> TaskResult:
        """Execute a task through the backing agent system."""
