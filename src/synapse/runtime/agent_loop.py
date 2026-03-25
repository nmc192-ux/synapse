import asyncio

from synapse.models.agent import AgentBudgetUsage, AgentDefinition
from synapse.models.browser import StructuredPageModel
from synapse.models.events import EventType, RuntimeEvent
from synapse.models.loop import AgentAction, AgentActionType, LoopEvaluation, LoopObservation, LoopPlan, LoopReflection
from synapse.models.memory import MemoryStoreRequest, MemoryType
from synapse.models.task import TaskRequest, TaskResult, TaskStatus
from synapse.runtime.budget import AgentBudgetLimitExceeded, AgentBudgetManager
from synapse.runtime.browser import BrowserRuntime
from synapse.runtime.llm import LLMProvider
from synapse.runtime.memory import AgentMemoryManager
from synapse.runtime.planning import NavigationEvaluator, NavigationPlanner
from synapse.runtime.security import AgentSecuritySandbox
from synapse.runtime.safety import AgentSafetyLayer, SecurityAlertError
from synapse.transports.websocket_manager import WebSocketManager


class EventDrivenAgentLoop:
    def __init__(
        self,
        definition: AgentDefinition,
        browser: BrowserRuntime,
        sockets: WebSocketManager,
        sandbox: AgentSecuritySandbox,
        safety: AgentSafetyLayer,
        memory_manager: AgentMemoryManager,
        budget_manager: AgentBudgetManager,
        llm: LLMProvider | None = None,
    ) -> None:
        self.definition = definition
        self.browser = browser
        self.sockets = sockets
        self.sandbox = sandbox
        self.safety = safety
        self.memory_manager = memory_manager
        self.budget_manager = budget_manager
        self.planner = NavigationPlanner(llm=llm)
        self.evaluator = NavigationEvaluator(llm=llm)

    async def run(self, task: TaskRequest) -> TaskResult:
        if task.session_id is None:
            raise ValueError("Task session_id is required before starting the agent loop.")

        completed_actions: list[AgentAction] = []
        artifacts: dict[str, object] = {"actions": []}
        self.budget_manager.get_or_create(self.definition)
        await self._increment_tokens(task, task.goal)
        await self._check_limits(task)

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

            current_page = await self._current_page(task)
            if current_page is not None:
                await self._increment_tokens(task, self._page_text(current_page))
            await self._store_observation_memory(task, observed, current_page)
            memory_summary = await self._memory_summary(task.agent_id)
            remaining_actions = await self.planner.generate_plan(
                task=task,
                completed_actions=completed_actions,
                current_page=current_page,
                memory_summary=memory_summary,
            )
            await self._broadcast_plan(task, remaining_actions)

            while remaining_actions:
                action = remaining_actions.pop(0)
                before_url = current_page.url if current_page is not None else None
                await self._increment_step(task)
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

                current_page = self._page_from_result(result) or await self._current_page(task)
                if current_page is not None:
                    if before_url is None or current_page.url != before_url:
                        await self._increment_page(task)
                    await self._increment_tokens(task, self._page_text(current_page))
                evaluation = await self.evaluator.evaluate(
                    task,
                    action,
                    result,
                    completed_actions=completed_actions,
                    remaining_actions=remaining_actions,
                    current_page=current_page,
                )
                remaining_actions = [candidate.model_copy() for candidate in evaluation.next_actions]
                await self.sockets.broadcast(
                    RuntimeEvent(
                        event_type=EventType.LOOP_EVALUATED,
                        session_id=task.session_id,
                        agent_id=task.agent_id,
                        payload=evaluation.model_dump(mode="json"),
                    )
                )
                await self._increment_tokens(task, evaluation.notes)
                await self._store_evaluation_memory(task, action, evaluation, current_page)
                if remaining_actions:
                    await self._broadcast_plan(task, remaining_actions)

            reflection = LoopReflection(
                task_id=task.task_id,
                completed_actions=len(completed_actions),
                remaining_actions=len(remaining_actions),
                notes=f"Planner executed {len(completed_actions)} browser actions and evaluator updated the plan after each step.",
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
            await self._ensure_page_safe(task, result.page, "browser.open")
            return result.model_dump(mode="json")

        if action.type == AgentActionType.CLICK:
            if not action.selector:
                raise ValueError("click action requires selector")
            self.sandbox.authorize_domain(task.agent_id, self.browser.current_url(task.session_id))
            self.sandbox.consume_browser_action(task.agent_id)
            await self._ensure_current_page_safe(task, "browser.click")
            result = await self.browser.click(task.session_id, action.selector)
            await self._ensure_page_safe(task, result.page, "browser.click")
            return result.model_dump(mode="json")

        if action.type == AgentActionType.TYPE:
            if not action.selector:
                raise ValueError("type action requires selector")
            self.sandbox.authorize_domain(task.agent_id, self.browser.current_url(task.session_id))
            self.sandbox.consume_browser_action(task.agent_id)
            await self._ensure_current_page_safe(task, "browser.type")
            result = await self.browser.type(task.session_id, action.selector, action.text or "")
            await self._ensure_page_safe(task, result.page, "browser.type")
            return result.model_dump(mode="json")

        if action.type == AgentActionType.EXTRACT:
            if not action.selector:
                raise ValueError("extract action requires selector")
            self.sandbox.authorize_domain(task.agent_id, self.browser.current_url(task.session_id))
            self.sandbox.consume_browser_action(task.agent_id)
            await self._ensure_current_page_safe(task, "browser.extract")
            result = await self.browser.extract(task.session_id, action.selector, action.attribute)
            await self._ensure_page_safe(task, result.page, "browser.extract")
            return result.model_dump(mode="json")

        if action.type == AgentActionType.SCREENSHOT:
            self.sandbox.authorize_domain(task.agent_id, self.browser.current_url(task.session_id))
            self.sandbox.consume_browser_action(task.agent_id)
            await self._ensure_current_page_safe(task, "browser.screenshot")
            result = await self.browser.screenshot(task.session_id)
            await self._ensure_page_safe(task, result.page, "browser.screenshot")
            return result.model_dump(mode="json")

        raise ValueError(f"Unsupported action type: {action.type}")

    async def _ensure_current_page_safe(self, task: TaskRequest, action: str) -> None:
        page = await self.browser.get_layout(task.session_id)
        await self._ensure_page_safe(task, page, action)

    async def _ensure_page_safe(self, task: TaskRequest, page: StructuredPageModel, action: str) -> None:
        finding = self.safety.inspect_page(page, action)
        if finding is not None:
            await self.sockets.broadcast(
                RuntimeEvent(
                    event_type=EventType.SECURITY_ALERT,
                    session_id=task.session_id,
                    agent_id=task.agent_id,
                    payload=finding.model_dump(mode="json"),
                )
            )
            raise SecurityAlertError(finding)

    async def _broadcast_plan(self, task: TaskRequest, actions: list[AgentAction]) -> None:
        await self.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.LOOP_PLANNED,
                session_id=task.session_id,
                agent_id=task.agent_id,
                payload=LoopPlan(task_id=task.task_id, actions=actions).model_dump(mode="json"),
            )
        )

    async def _current_page(self, task: TaskRequest) -> StructuredPageModel | None:
        try:
            return await self.browser.get_layout(task.session_id)
        except Exception:
            return None

    @staticmethod
    def _page_from_result(action_result: dict[str, object]) -> StructuredPageModel | None:
        page = action_result.get("page")
        if isinstance(page, dict):
            return StructuredPageModel.model_validate(page)
        return None

    async def _store_observation_memory(
        self,
        task: TaskRequest,
        observation: LoopObservation,
        current_page: StructuredPageModel | None,
    ) -> None:
        page_summary = ""
        if current_page is not None:
            page_summary = f" page={current_page.title} url={current_page.url}"

        await self.memory_manager.store(
            MemoryStoreRequest(
                agent_id=task.agent_id,
                memory_type=MemoryType.SHORT_TERM,
                content=(
                    f"observe cycle task={task.task_id} goal={task.goal} "
                    f"events={observation.event_count}{page_summary}"
                ),
            )
        )
        await self._increment_memory_write(task)

    async def _store_evaluation_memory(
        self,
        task: TaskRequest,
        action: AgentAction,
        evaluation: LoopEvaluation,
        current_page: StructuredPageModel | None,
    ) -> None:
        page_summary = ""
        if current_page is not None:
            page_summary = f" page={current_page.title}"

        await self.memory_manager.store(
            MemoryStoreRequest(
                agent_id=task.agent_id,
                memory_type=MemoryType.TASK,
                content=(
                    f"evaluate cycle task={task.task_id} action={action.type.value} "
                    f"success={evaluation.success} notes={evaluation.notes}{page_summary}"
                ),
            )
        )
        await self._increment_memory_write(task)

    async def _memory_summary(self, agent_id: str) -> str:
        try:
            recent = await self.memory_manager.get_recent(agent_id, limit=5)
        except Exception:
            return ""
        if not recent:
            return ""
        ordered = sorted(recent, key=lambda item: item.timestamp)
        return " | ".join(item.content for item in ordered if item.content)

    async def _increment_step(self, task: TaskRequest) -> None:
        usage = self.budget_manager.increment_step(self.definition)
        await self._broadcast_budget_update(task, usage)
        await self._check_limits(task)

    async def _increment_page(self, task: TaskRequest) -> None:
        usage = self.budget_manager.increment_page(self.definition)
        await self._broadcast_budget_update(task, usage)
        await self._check_limits(task)

    async def _increment_tokens(self, task: TaskRequest, content: str) -> None:
        estimated = max(1, len(content) // 4) if content else 0
        if estimated == 0:
            return
        usage = self.budget_manager.increment_tokens(self.definition, estimated)
        await self._broadcast_budget_update(task, usage)
        await self._check_limits(task)

    async def _increment_memory_write(self, task: TaskRequest) -> None:
        usage = self.budget_manager.increment_memory_write(self.definition)
        await self._broadcast_budget_update(task, usage)
        await self._check_limits(task)

    async def _check_limits(self, task: TaskRequest) -> None:
        try:
            usage, warnings = self.budget_manager.check_limits(self.definition)
        except AgentBudgetLimitExceeded as exc:
            if self.definition.execution_policy.save_checkpoint_on_limit or self.definition.execution_policy.pause_on_hard_limit:
                self.budget_manager.save_checkpoint(
                    task.agent_id,
                    {
                        "task_id": task.task_id,
                        "session_id": task.session_id,
                        "usage": self.budget_manager.get_usage(task.agent_id).model_dump(mode="json"),
                    },
                    reason=str(exc),
                )
                if self.definition.execution_policy.pause_on_hard_limit:
                    raise AgentBudgetLimitExceeded("Agent paused: hard budget limit reached.") from exc
            raise

        if self.definition.execution_policy.stop_on_soft_limit and any("exceeded" in warning for warning in warnings):
            raise AgentBudgetLimitExceeded("Agent terminated: soft budget limit exceeded.")

        for warning in warnings:
            await self._broadcast_budget_update(task, usage, warning=warning)

    async def _broadcast_budget_update(
        self,
        task: TaskRequest,
        usage: AgentBudgetUsage,
        warning: str | None = None,
    ) -> None:
        payload: dict[str, object] = {"usage": usage.model_dump(mode="json")}
        if warning is not None:
            payload["warning"] = warning
        await self.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.BUDGET_UPDATED,
                session_id=task.session_id,
                agent_id=task.agent_id,
                payload=payload,
            )
        )

    @staticmethod
    def _page_text(page: StructuredPageModel) -> str:
        return " ".join(
            filter(
                None,
                [page.title, *(section.text for section in page.sections), *(link.text for link in page.links)],
            )
        )[:4000]
