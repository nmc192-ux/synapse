from __future__ import annotations

from typing import Any

from synapse.connectors.base import FrameworkConnector


class OpenClawConnector(FrameworkConnector):
    framework_name = "openclaw"

    def normalize_task(self, task: dict[str, Any]) -> dict[str, Any]:
        browser_plan = task.get("browser_plan", {})
        return {
            "goal": str(task.get("goal", "OpenClaw task")),
            "start_url": browser_plan.get("url") or task.get("start_url"),
            "capture_layout": True,
            "extract": list(browser_plan.get("extract", task.get("extract", []))),
            "inspect": list(browser_plan.get("inspect", [])),
            "actions": list(browser_plan.get("actions", task.get("actions", []))),
            "screenshot": bool(task.get("capture_screenshot", task.get("screenshot", False))),
            "tool_calls": list(task.get("tool_calls", [])),
            "messages": list(task.get("messages", [])),
        }
