from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from synapse.models.agent import AgentDefinition
from synapse.models.task import TaskRequest, TaskResult
from synapse.runtime.agent_loop import EventDrivenAgentLoop
from synapse.runtime.budget import AgentBudgetManager
from synapse.runtime.llm import LLMProvider
from synapse.runtime.memory import AgentMemoryManager
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
        memory_manager: AgentMemoryManager,
        budget_manager: AgentBudgetManager,
        llm: LLMProvider | None = None,
    ) -> None:
        self.definition = definition
        self.loop = EventDrivenAgentLoop(
            definition=definition,
            browser=browser,
            sockets=sockets,
            sandbox=sandbox,
            safety=safety,
            memory_manager=memory_manager,
            budget_manager=budget_manager,
            llm=llm,
        )

    @abstractmethod
    async def execute_task(self, task: TaskRequest) -> TaskResult:
        """Execute a task through the backing agent system."""
