from __future__ import annotations

import json
import uuid

from synapse.models.browser import StructuredPageModel
from synapse.models.loop import AgentAction, AgentActionType, LoopEvaluation
from synapse.models.task import TaskRequest
from synapse.runtime.llm import LLMProvider
from synapse.runtime.prompts import PLANNER_PROMPT


class NavigationPlanner:
    def __init__(self, llm: LLMProvider | None = None) -> None:
        self.llm = llm

    async def plan(
        self,
        task: TaskRequest,
        completed_actions: list[AgentAction],
        current_page: StructuredPageModel | None = None,
    ) -> list[AgentAction]:
        return await self.generate_plan(
            task=task,
            completed_actions=completed_actions,
            current_page=current_page,
            memory_summary="",
        )

    async def generate_plan(
        self,
        task: TaskRequest,
        completed_actions: list[AgentAction],
        current_page: StructuredPageModel | None = None,
        memory_summary: str = "",
    ) -> list[AgentAction]:
        explicit_actions = self._explicit_actions(task)
        if explicit_actions:
            completed_ids = {action.action_id for action in completed_actions}
            return [action for action in explicit_actions if action.action_id not in completed_ids]

        llm_actions = await self._plan_with_llm(
            task=task,
            completed_actions=completed_actions,
            current_page=current_page,
            memory_summary=memory_summary,
        )
        if llm_actions:
            return llm_actions

        return self._heuristic_plan(task, completed_actions, current_page)

    async def _plan_with_llm(
        self,
        task: TaskRequest,
        completed_actions: list[AgentAction],
        current_page: StructuredPageModel | None,
        memory_summary: str,
    ) -> list[AgentAction]:
        if self.llm is None:
            return []

        page_context = current_page.model_dump(mode="json") if current_page is not None else {}
        previous_actions = [
            {
                "type": action.type.value,
                "selector": action.selector,
                "text": action.text,
                "url": action.url,
                "status": action.status,
            }
            for action in completed_actions
        ]
        prompt = PLANNER_PROMPT.format(
            goal=task.goal,
            page_state=json.dumps(page_context, ensure_ascii=True),
            memory_summary=memory_summary or "No memory available.",
            previous_actions=json.dumps(previous_actions, ensure_ascii=True),
            constraints=json.dumps(task.constraints, ensure_ascii=True),
        )
        system = (
            "You are Synapse planner. Return strict JSON that matches the required output schema."
        )

        try:
            raw = await self.llm.generate(prompt=prompt, system=system)
        except Exception:
            return []

        decoded = self._decode_llm_json(raw)
        if decoded is None:
            return []
        actions_payload = decoded.get("actions")
        if not isinstance(actions_payload, list):
            return []

        actions: list[AgentAction] = []
        for spec in actions_payload:
            if not isinstance(spec, dict):
                continue
            action_type = spec.get("type")
            if action_type not in {item.value for item in AgentActionType}:
                continue
            try:
                actions.append(
                    AgentAction(
                        action_id=str(spec.get("action_id", uuid.uuid4())),
                        type=AgentActionType(action_type),
                        selector=spec.get("selector"),
                        text=spec.get("text"),
                        url=spec.get("url"),
                        attribute=spec.get("attribute"),
                    )
                )
            except ValueError:
                continue

        completed_ids = {action.action_id for action in completed_actions}
        return [action for action in actions if action.action_id not in completed_ids]

    @staticmethod
    def _decode_llm_json(raw: str) -> dict[str, object] | None:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
        try:
            decoded = json.loads(cleaned)
        except json.JSONDecodeError:
            return None
        return decoded if isinstance(decoded, dict) else None

    def _heuristic_plan(
        self,
        task: TaskRequest,
        completed_actions: list[AgentAction],
        current_page: StructuredPageModel | None = None,
    ) -> list[AgentAction]:
        goal = task.goal.lower()
        plan: list[AgentAction] = []
        completed_types = {action.type for action in completed_actions}

        if task.start_url is not None and AgentActionType.OPEN not in completed_types:
            plan.append(self._action(AgentActionType.OPEN, url=str(task.start_url)))

        if "type" in goal and AgentActionType.TYPE not in completed_types:
            text_value = self._constraint_str(task.constraints, "text") or self._constraint_str(task.constraints, "input_text")
            selector = self._constraint_str(task.constraints, "selector") or self._first_input_selector(current_page)
            if selector and text_value is not None:
                plan.append(self._action(AgentActionType.TYPE, selector=selector, text=text_value))

        if "click" in goal and AgentActionType.CLICK not in completed_types:
            selector = self._constraint_str(task.constraints, "selector") or self._first_button_selector(current_page)
            if selector:
                plan.append(self._action(AgentActionType.CLICK, selector=selector))

        if "extract" in goal and AgentActionType.EXTRACT not in completed_types:
            selector = self._constraint_str(task.constraints, "selector") or self._extract_selector(current_page)
            if selector:
                plan.append(self._action(AgentActionType.EXTRACT, selector=selector))

        if ("screenshot" in goal or not plan) and AgentActionType.SCREENSHOT not in completed_types:
            plan.append(self._action(AgentActionType.SCREENSHOT))

        return plan

    @staticmethod
    def _explicit_actions(task: TaskRequest) -> list[AgentAction]:
        if task.actions:
            return [action.model_copy() for action in task.actions]

        action_specs = task.constraints.get("action_plan", [])
        if not isinstance(action_specs, list):
            return []

        actions: list[AgentAction] = []
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
        return actions

    @staticmethod
    def _action(
        action_type: AgentActionType,
        selector: str | None = None,
        text: str | None = None,
        url: str | None = None,
    ) -> AgentAction:
        return AgentAction(
            action_id=str(uuid.uuid4()),
            type=action_type,
            selector=selector,
            text=text,
            url=url,
        )

    @staticmethod
    def _constraint_str(constraints: dict[str, object], key: str) -> str | None:
        value = constraints.get(key)
        return value if isinstance(value, str) else None

    @staticmethod
    def _first_button_selector(page: StructuredPageModel | None) -> str | None:
        if page and page.buttons:
            return page.buttons[0].selector_hint
        return None

    @staticmethod
    def _first_input_selector(page: StructuredPageModel | None) -> str | None:
        if page and page.inputs:
            return page.inputs[0].selector_hint
        return None

    @staticmethod
    def _extract_selector(page: StructuredPageModel | None) -> str:
        if page and page.sections and page.sections[0].selector_hint:
            return page.sections[0].selector_hint
        return "h1"


class NavigationEvaluator:
    def __init__(self, llm: LLMProvider | None = None) -> None:
        self.planner = NavigationPlanner(llm=llm)

    async def evaluate(
        self,
        task: TaskRequest,
        action: AgentAction,
        action_result: dict[str, object],
        completed_actions: list[AgentAction],
        remaining_actions: list[AgentAction],
        current_page: StructuredPageModel | None = None,
    ) -> LoopEvaluation:
        success, notes = self._success_for_action(action, action_result)
        next_actions = [candidate.model_copy() for candidate in remaining_actions]

        if success and not next_actions:
            planned = await self.planner.plan(task, completed_actions=completed_actions, current_page=current_page)
            next_actions = [candidate for candidate in planned if candidate.action_id != action.action_id]

        return LoopEvaluation(
            task_id=task.task_id,
            action_id=action.action_id,
            success=success,
            notes=notes,
            next_actions=next_actions,
        )

    @staticmethod
    def _success_for_action(action: AgentAction, action_result: dict[str, object]) -> tuple[bool, str]:
        if action.type == AgentActionType.OPEN:
            page = action_result.get("page")
            success = isinstance(page, dict) and isinstance(page.get("url"), str)
            return success, "Navigation succeeded." if success else "Navigation did not return a page URL."

        if action.type == AgentActionType.EXTRACT:
            matches = action_result.get("matches")
            success = isinstance(matches, list) and len(matches) > 0
            return success, "Extraction returned matches." if success else "Extraction returned no matches."

        if action.type == AgentActionType.SCREENSHOT:
            success = isinstance(action_result.get("image_base64"), str)
            return success, "Screenshot captured." if success else "Screenshot artifact missing."

        success = "page" in action_result
        return success, f"{action.type.value} completed." if success else f"{action.type.value} did not return page state."
