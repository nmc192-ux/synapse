import asyncio
import uuid

from synapse.models.browser import ClickRequest, ExtractRequest, OpenRequest, ScreenshotRequest, TypeRequest
from synapse.models.events import EventType, RuntimeEvent
from synapse.models.loop import AgentAction, AgentActionType, LoopObservation, LoopPlan, LoopReflection
from synapse.models.task import TaskRequest, TaskResult, TaskStatus
from synapse.runtime.browser import BrowserRuntime
from synapse.runtime.security import AgentSecuritySandbox
from synapse.transports.websocket_manager import WebSocketManager


class EventDrivenAgentLoop:
    def __init__(
        self,
        browser: BrowserRuntime,
        sockets: WebSocketManager,
        sandbox: AgentSecuritySandbox,
    ) -> None:
        self.browser = browser
        self.sockets = sockets
        self.sandbox = sandbox

    async def run(self, task: TaskRequest) -> TaskResult:
        if task.session_id is None:
            raise ValueError("Task session_id is required before starting the agent loop.")

        actions = self._build_actions(task)
        completed_actions: list[AgentAction] = []
        artifacts: dict[str, object] = {"actions": []}

        async with self.sockets.subscribe(f"{task.agent_id}:{task.task_id}") as event_queue:
            observed = await self._observe(task, event_queue)
            await self.sockets.broadcast(
                RuntimeEvent(
                    event_type=EventType.LOOP_OBSERVED,
                    session_id=task.session_id,
                    agent_id=task.agent_id,
                    payload=observed.model_dump(mode="json"),
                )
            )

            await self.sockets.broadcast(
                RuntimeEvent(
                    event_type=EventType.LOOP_PLANNED,
                    session_id=task.session_id,
                    agent_id=task.agent_id,
                    payload=LoopPlan(task_id=task.task_id, actions=actions).model_dump(mode="json"),
                )
            )

            for action in actions:
                result = await self._act(task, action)
                action.status = "completed"
                action.result = result
                completed_actions.append(action)
                artifacts["actions"].append(action.model_dump(mode="json"))
                await self.sockets.broadcast(
                    RuntimeEvent(
                        event_type=EventType.LOOP_ACTED,
                        session_id=task.session_id,
                        agent_id=task.agent_id,
                        payload=action.model_dump(mode="json"),
                    )
                )

            reflection = LoopReflection(
                task_id=task.task_id,
                completed_actions=len(completed_actions),
                remaining_actions=max(0, len(actions) - len(completed_actions)),
                notes=f"Executed {len(completed_actions)} actions via browser engine.",
            )
            await self.sockets.broadcast(
                RuntimeEvent(
                    event_type=EventType.LOOP_REFLECTED,
                    session_id=task.session_id,
                    agent_id=task.agent_id,
                    payload=reflection.model_dump(mode="json"),
                )
            )

        return TaskResult(
            task_id=task.task_id,
            status=TaskStatus.COMPLETED,
            message="Event-driven observe/plan/act/reflect loop completed.",
            artifacts=artifacts,
        )

    async def _observe(
        self,
        task: TaskRequest,
        event_queue: asyncio.Queue[RuntimeEvent],
    ) -> LoopObservation:
        events: list[RuntimeEvent] = []

        try:
            event = await asyncio.wait_for(event_queue.get(), timeout=0.1)
            events.append(event)
        except TimeoutError:
            pass

        last_event = events[-1] if events else None
        return LoopObservation(
            task_id=task.task_id,
            event_count=len(events),
            last_event_type=last_event.event_type.value if last_event else None,
            last_event_payload=last_event.payload if last_event else {},
        )

    async def _act(self, task: TaskRequest, action: AgentAction) -> dict[str, object]:
        if action.type == AgentActionType.OPEN:
            if not action.url:
                raise ValueError("open action requires url")
            self.sandbox.authorize_domain(task.agent_id, action.url)
            self.sandbox.consume_browser_action(task.agent_id)
            result = await self.browser.open(task.session_id, action.url)
            return result.model_dump(mode="json")

        if action.type == AgentActionType.CLICK:
            if not action.selector:
                raise ValueError("click action requires selector")
            self.sandbox.authorize_domain(task.agent_id, self.browser.current_url(task.session_id))
            self.sandbox.consume_browser_action(task.agent_id)
            result = await self.browser.click(task.session_id, action.selector)
            return result.model_dump(mode="json")

        if action.type == AgentActionType.TYPE:
            if not action.selector:
                raise ValueError("type action requires selector")
            self.sandbox.authorize_domain(task.agent_id, self.browser.current_url(task.session_id))
            self.sandbox.consume_browser_action(task.agent_id)
            result = await self.browser.type(task.session_id, action.selector, action.text or "")
            return result.model_dump(mode="json")

        if action.type == AgentActionType.EXTRACT:
            if not action.selector:
                raise ValueError("extract action requires selector")
            self.sandbox.authorize_domain(task.agent_id, self.browser.current_url(task.session_id))
            self.sandbox.consume_browser_action(task.agent_id)
            result = await self.browser.extract(task.session_id, action.selector, action.attribute)
            return result.model_dump(mode="json")

        if action.type == AgentActionType.SCREENSHOT:
            self.sandbox.authorize_domain(task.agent_id, self.browser.current_url(task.session_id))
            self.sandbox.consume_browser_action(task.agent_id)
            result = await self.browser.screenshot(task.session_id)
            return result.model_dump(mode="json")

        raise ValueError(f"Unsupported action type: {action.type}")

    def _build_actions(self, task: TaskRequest) -> list[AgentAction]:
        if task.actions:
            return [action.model_copy() for action in task.actions]

        actions: list[AgentAction] = []
        if task.start_url is not None:
            actions.append(
                AgentAction(
                    action_id=str(uuid.uuid4()),
                    type=AgentActionType.OPEN,
                    url=str(task.start_url),
                )
            )

        action_specs = task.constraints.get("action_plan", [])
        if isinstance(action_specs, list):
            for spec in action_specs:
                if not isinstance(spec, dict) or "type" not in spec:
                    continue
                actions.append(
                    AgentAction(
                        action_id=str(spec.get("action_id", uuid.uuid4())),
                        type=AgentActionType(spec["type"]),
                        selector=spec.get("selector"),
                        text=spec.get("text"),
                        url=spec.get("url"),
                        attribute=spec.get("attribute"),
                    )
                )

        if not actions:
            actions.append(
                AgentAction(
                    action_id=str(uuid.uuid4()),
                    type=AgentActionType.SCREENSHOT,
                )
            )

        return actions
