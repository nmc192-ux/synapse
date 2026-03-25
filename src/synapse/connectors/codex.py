from __future__ import annotations

from typing import Any

from synapse.connectors.base import FrameworkConnector


class CodexConnector(FrameworkConnector):
    framework_name = "codex"

    def normalize_task(self, task: dict[str, Any]) -> dict[str, Any]:
        plan = task.get("plan", {})
        observations = plan.get("observations", {})
        return {
            "goal": str(task.get("goal", "Codex task")),
            "start_url": task.get("start_url") or observations.get("url"),
            "capture_layout": bool(plan.get("capture_layout", True)),
            "extract": list(plan.get("extract", [])),
            "inspect": list(plan.get("inspect", [])),
            "actions": list(plan.get("actions", [])),
            "screenshot": bool(plan.get("screenshot", False)),
            "tool_calls": list(plan.get("tool_calls", task.get("tool_calls", []))),
            "messages": list(task.get("messages", [])),
        }
