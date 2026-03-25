from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from synapse.models.browser import BrowserState, ExtractionResult, PageInspection, ScreenshotResult, StructuredPageModel
from synapse.sdk import SynapseClient


@dataclass
class ConnectorResult:
    framework: str
    goal: str
    opened: BrowserState | None = None
    layout: StructuredPageModel | None = None
    extracted: list[ExtractionResult] = field(default_factory=list)
    inspections: list[PageInspection] = field(default_factory=list)
    screenshots: list[ScreenshotResult] = field(default_factory=list)
    tool_results: list[dict[str, object]] = field(default_factory=list)
    messages: list[dict[str, object]] = field(default_factory=list)


class FrameworkConnector:
    framework_name = "framework"

    def __init__(self, client: SynapseClient) -> None:
        self.client = client

    def run(self, task: dict[str, Any]) -> ConnectorResult:
        normalized = self.normalize_task(task)
        browser = self.client.browser
        result = ConnectorResult(framework=self.framework_name, goal=normalized["goal"])

        if start_url := normalized.get("start_url"):
            result.opened = browser.open(str(start_url))

        if normalized.get("capture_layout", True):
            result.layout = browser.get_layout()

        for item in normalized.get("extract", []):
            selector = str(item["selector"])
            attribute = item.get("attribute")
            result.extracted.append(browser.extract(selector, attribute=attribute))

        for selector in normalized.get("inspect", []):
            result.inspections.append(browser.inspect(str(selector)))

        for action in normalized.get("actions", []):
            action_type = action.get("type")
            if action_type == "click":
                browser.click(str(action["selector"]))
            elif action_type == "type":
                browser.type(str(action["selector"]), str(action.get("text", "")))

        if normalized.get("screenshot"):
            result.screenshots.append(browser.screenshot())

        for tool_call in normalized.get("tool_calls", []):
            result.tool_results.append(
                browser.call_tool(str(tool_call["tool_name"]), dict(tool_call.get("arguments", {})))
            )

        for message in normalized.get("messages", []):
            sent = browser.send_agent_message(
                sender_agent_id=str(message["sender_agent_id"]),
                recipient_agent_id=str(message["recipient_agent_id"]),
                content=str(message["content"]),
                metadata=dict(message.get("metadata", {})),
            )
            result.messages.append(sent.model_dump(mode="json"))

        return result

    def normalize_task(self, task: dict[str, Any]) -> dict[str, Any]:
        return {
            "goal": str(task.get("goal", "Execute framework task")),
            "start_url": task.get("start_url"),
            "capture_layout": bool(task.get("capture_layout", True)),
            "extract": list(task.get("extract", [])),
            "inspect": list(task.get("inspect", [])),
            "actions": list(task.get("actions", [])),
            "screenshot": bool(task.get("screenshot", False)),
            "tool_calls": list(task.get("tool_calls", [])),
            "messages": list(task.get("messages", [])),
        }
