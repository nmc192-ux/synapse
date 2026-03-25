import asyncio

from synapse.models.agent import AgentDefinition
from synapse.models.browser import StructuredPageModel
from synapse.models.loop import AgentAction, AgentActionType, LoopEvaluation, LoopObservation, LoopPlan, LoopReflection
from synapse.models.memory import MemoryScope, MemoryStoreRequest, MemoryType
from synapse.models.runtime_event import EventSeverity, EventType, RuntimeEvent
from synapse.models.task import TaskRequest, TaskResult, TaskStatus
from synapse.runtime.budget import AgentBudgetLimitExceeded
from synapse.runtime.browser import BrowserRuntime
from synapse.runtime.budget_service import BudgetService
from synapse.runtime.compression.base import CompressionProvider
from synapse.runtime.compression.noop import NoOpCompressionProvider
from synapse.runtime.llm import LLMProvider
from synapse.runtime.memory_service import MemoryService
from synapse.runtime.planning import NavigationEvaluator, NavigationPlanner, NavigationReflector
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
        memory_service: MemoryService,
        budget_service: BudgetService,
        llm: LLMProvider | None = None,
        compression_provider: CompressionProvider | None = None,
    ) -> None:
        self.definition = definition
        self.browser = browser
        self.sockets = sockets
        self.sandbox = sandbox
        self.safety = safety
        self.memory_service = memory_service
        self.budget_service = budget_service
        self.llm = llm
        self.compression_provider = compression_provider or NoOpCompressionProvider()
        self.planner = NavigationPlanner(llm=llm, compression=self.compression_provider)
        self.evaluator = NavigationEvaluator(llm=llm, compression=self.compression_provider)
        self.reflector = NavigationReflector(llm=llm)

    async def run(self, task: TaskRequest) -> TaskResult:
        if task.session_id is None:
            raise ValueError("Task session_id is required before starting the agent loop.")

        completed_actions: list[AgentAction] = []
        artifacts: dict[str, object] = {"actions": []}
        await self.budget_service.ensure_run_budget(task.agent_id, task.run_id or task.task_id)
        await self._increment_tokens(task, task.goal)
        await self._check_limits(task)

        async with self.sockets.subscribe(f"{task.agent_id}:{task.task_id}") as event_queue:
            observed = await self._observe(task, event_queue)
            await self.sockets.broadcast(
                RuntimeEvent(
                    event_type=EventType.LOOP_OBSERVED,
                    run_id=task.run_id,
                    session_id=task.session_id,
                    agent_id=task.agent_id,
                    task_id=task.task_id,
                    source="agent_loop",
                    payload=observed.model_dump(mode="json"),
                    correlation_id=task.task_id,
                )
            )

            current_page = await self._current_page(task)
            if current_page is not None:
                await self._increment_tokens(task, self._page_text(current_page))
            await self._store_observation_memory(task, observed, current_page)
            memory_context = await self.memory_service.get_planner_memory_context(
                task.agent_id,
                run_id=task.run_id,
                task_id=task.task_id,
                limit_per_type=4,
            )
            memory_summary = str(memory_context.get("memory_summary", ""))
            recent_memories = list(memory_context.get("memories", []))
            recent_events = await self.memory_service.get_recent_runtime_events(
                run_id=task.run_id,
                agent_id=task.agent_id,
                task_id=task.task_id,
                limit=10,
            )
            remaining_actions = await self.planner.generate_plan(
                task=task,
                completed_actions=completed_actions,
                current_page=current_page,
                memory_summary=memory_summary,
                recent_memories=recent_memories,
                recent_events=recent_events,
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
                        run_id=task.run_id,
                        session_id=task.session_id,
                        agent_id=task.agent_id,
                        task_id=task.task_id,
                        source="agent_loop",
                        payload=action.model_dump(mode="json"),
                        correlation_id=task.task_id,
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
                    memory_summary=await self._memory_summary(task),
                )
                remaining_actions = [candidate.model_copy() for candidate in evaluation.next_actions]
                await self.sockets.broadcast(
                    RuntimeEvent(
                        event_type=EventType.LOOP_EVALUATED,
                        run_id=task.run_id,
                        session_id=task.session_id,
                        agent_id=task.agent_id,
                        task_id=task.task_id,
                        source="agent_loop",
                        payload=evaluation.model_dump(mode="json"),
                        correlation_id=task.task_id,
                    )
                )
                await self._increment_tokens(task, evaluation.notes)
                await self._store_evaluation_memory(task, action, evaluation, current_page)
                await self._store_reflection_memory(task, completed_actions, current_page)
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
                    run_id=task.run_id,
                    session_id=task.session_id,
                    agent_id=task.agent_id,
                    task_id=task.task_id,
                    source="agent_loop",
                    payload=reflection.model_dump(mode="json"),
                    correlation_id=task.task_id,
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
                    run_id=task.run_id,
                    session_id=task.session_id,
                    agent_id=task.agent_id,
                    task_id=task.task_id,
                    source="agent_loop",
                    payload=finding.model_dump(mode="json"),
                    severity=EventSeverity.ERROR,
                    correlation_id=task.task_id,
                )
            )
            raise SecurityAlertError(finding)

    async def _broadcast_plan(self, task: TaskRequest, actions: list[AgentAction]) -> None:
        telemetry = self.planner.get_last_context_telemetry()
        if telemetry:
            await self.sockets.broadcast(
                RuntimeEvent(
                    event_type=EventType.PLANNER_CONTEXT_COMPRESSED,
                    run_id=task.run_id,
                    session_id=task.session_id,
                    agent_id=task.agent_id,
                    task_id=task.task_id,
                    source="agent_loop",
                    payload=telemetry,
                    correlation_id=task.task_id,
                )
            )
        await self.sockets.broadcast(
            RuntimeEvent(
                event_type=EventType.LOOP_PLANNED,
                run_id=task.run_id,
                session_id=task.session_id,
                agent_id=task.agent_id,
                task_id=task.task_id,
                source="agent_loop",
                payload=LoopPlan(
                    task_id=task.task_id,
                    actions=actions,
                    raw_context_size=int(telemetry.get("raw_context_size", 0)),
                    compressed_context_size=int(telemetry.get("compressed_context_size", 0)),
                    compression_ratio=float(telemetry.get("compression_ratio", 1.0)),
                ).model_dump(mode="json"),
                correlation_id=task.task_id,
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

        await self.memory_service.store(
            MemoryStoreRequest(
                agent_id=task.agent_id,
                run_id=task.run_id,
                task_id=task.task_id,
                memory_type=MemoryType.SHORT_TERM,
                memory_scope=MemoryScope.RUN,
                content=(
                    f"observe cycle task={task.task_id} goal={task.goal} "
                    f"events={observation.event_count}{page_summary}"
                ),
            )
        )

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

        await self.memory_service.store(
            MemoryStoreRequest(
                agent_id=task.agent_id,
                run_id=task.run_id,
                task_id=task.task_id,
                memory_type=MemoryType.TASK,
                memory_scope=MemoryScope.TASK,
                content=(
                    f"evaluate cycle task={task.task_id} action={action.type.value} "
                    f"success={evaluation.success} notes={evaluation.notes}{page_summary}"
                ),
            )
        )

    async def _memory_summary(self, task: TaskRequest) -> str:
        try:
            if task.run_id:
                summary = await self.memory_service.summarize_run_context(task.run_id)
                return str(summary.get("summary", ""))
            recent = await self.memory_service.get_recent(task.agent_id, limit=5)
        except Exception:
            return ""
        if not recent:
            return ""
        ordered = sorted(recent, key=lambda item: item.timestamp)
        return " | ".join(item.content for item in ordered if item.content)

    async def _store_reflection_memory(
        self,
        task: TaskRequest,
        completed_actions: list[AgentAction],
        current_page: StructuredPageModel | None,
    ) -> None:
        reflection = await self.reflector.reflect(
            task=task,
            completed_actions=completed_actions,
            current_page=current_page,
            memory_summary=await self._memory_summary(task),
        )
        if not reflection:
            return

        await self.memory_service.store(
            MemoryStoreRequest(
                agent_id=task.agent_id,
                run_id=task.run_id,
                task_id=task.task_id,
                memory_type=MemoryType.LONG_TERM,
                memory_scope=MemoryScope.LONG_TERM,
                content=f"reflect cycle task={task.task_id} summary={reflection}",
            )
        )
        await self._increment_tokens(task, reflection)

    async def _increment_step(self, task: TaskRequest) -> None:
        await self.budget_service.increment_step(
            task.agent_id,
            run_id=task.run_id,
            task_id=task.task_id,
            session_id=task.session_id,
            correlation_id=task.run_id or task.task_id,
        )

    async def _increment_page(self, task: TaskRequest) -> None:
        await self.budget_service.increment_page(
            task.agent_id,
            run_id=task.run_id,
            task_id=task.task_id,
            session_id=task.session_id,
            correlation_id=task.run_id or task.task_id,
        )

    async def _increment_tokens(self, task: TaskRequest, content: str) -> None:
        estimated = max(1, len(content) // 4) if content else 0
        if estimated == 0:
            return
        await self.budget_service.increment_tokens(
            task.agent_id,
            estimated,
            run_id=task.run_id,
            task_id=task.task_id,
            session_id=task.session_id,
            correlation_id=task.run_id or task.task_id,
        )

    async def _increment_memory_write(self, task: TaskRequest) -> None:
        await self.budget_service.increment_memory_write(
            task.agent_id,
            run_id=task.run_id,
            task_id=task.task_id,
            session_id=task.session_id,
            correlation_id=task.run_id or task.task_id,
        )

    async def _check_limits(self, task: TaskRequest) -> None:
        try:
            await self.budget_service.check_limits(
                task.agent_id,
                run_id=task.run_id,
                task_id=task.task_id,
                session_id=task.session_id,
                correlation_id=task.run_id or task.task_id,
            )
        except AgentBudgetLimitExceeded as exc:
            if self.definition.execution_policy.save_checkpoint_on_limit or self.definition.execution_policy.pause_on_hard_limit:
                usage = await self.budget_service.get_run_budget(task.run_id) if task.run_id else self.budget_service.get_usage(task.agent_id)
                await self.budget_service.save_agent_checkpoint(
                    task.agent_id,
                    {
                        "task_id": task.task_id,
                        "session_id": task.session_id,
                        "run_id": task.run_id,
                        "usage": usage.model_dump(mode="json"),
                    },
                    reason=str(exc),
                )
                if self.definition.execution_policy.pause_on_hard_limit:
                    raise AgentBudgetLimitExceeded("Agent paused: hard budget limit reached.") from exc
            raise

    @staticmethod
    def _page_text(page: StructuredPageModel) -> str:
        return " ".join(
            filter(
                None,
                [page.title, *(section.text for section in page.sections), *(link.text for link in page.links)],
            )
        )[:4000]
