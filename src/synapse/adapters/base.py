from abc import ABC, abstractmethod

from synapse.models.agent import AgentDefinition
from synapse.models.task import TaskRequest, TaskResult
from synapse.runtime.agent_loop import EventDrivenAgentLoop
from synapse.runtime.browser import BrowserRuntime
from synapse.transports.websocket_manager import WebSocketManager


class AgentAdapter(ABC):
    def __init__(
        self,
        definition: AgentDefinition,
        browser: BrowserRuntime,
        sockets: WebSocketManager,
    ) -> None:
        self.definition = definition
        self.loop = EventDrivenAgentLoop(browser=browser, sockets=sockets)

    @abstractmethod
    async def execute_task(self, task: TaskRequest) -> TaskResult:
        """Execute a task through the backing agent system."""
