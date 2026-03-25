from __future__ import annotations

from typing import Any

from synapse.connectors.base import FrameworkConnector


class ClaudeCodeConnector(FrameworkConnector):
    framework_name = "claude_code"

    def normalize_task(self, task: dict[str, Any]) -> dict[str, Any]:
        workflow = task.get("workflow", {})
        return {
            "goal": str(task.get("goal", workflow.get("goal", "Claude Code task"))),
            "start_url": workflow.get("start_url") or task.get("start_url"),
            "capture_layout": bool(workflow.get("capture_layout", True)),
            "extract": list(workflow.get("extractors", task.get("extract", []))),
            "inspect": list(workflow.get("inspectors", [])),
            "actions": list(workflow.get("actions", [])),
            "screenshot": bool(workflow.get("screenshot", False)),
            "tool_calls": list(workflow.get("tool_calls", [])),
            "messages": list(workflow.get("messages", task.get("messages", []))),
        }
