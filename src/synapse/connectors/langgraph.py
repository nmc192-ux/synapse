from __future__ import annotations

from typing import Any

from synapse.connectors.base import FrameworkConnector


class LangGraphConnector(FrameworkConnector):
    framework_name = "langgraph"

    def normalize_task(self, task: dict[str, Any]) -> dict[str, Any]:
        state = task.get("state", {})
        return {
            "goal": str(state.get("goal", task.get("goal", "LangGraph task"))),
            "start_url": state.get("url") or task.get("start_url"),
            "capture_layout": bool(state.get("capture_layout", True)),
            "extract": list(state.get("extract", [])),
            "inspect": list(state.get("inspect", [])),
            "actions": list(state.get("actions", [])),
            "screenshot": bool(state.get("screenshot", False)),
            "tool_calls": list(state.get("tool_calls", [])),
            "messages": list(state.get("messages", [])),
        }
