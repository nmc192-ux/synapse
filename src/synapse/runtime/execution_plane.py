from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from synapse.models.runtime_event import RuntimeEvent
from synapse.runtime.browser import BrowserRuntime
from synapse.runtime.state_store import RuntimeStateStore
from synapse.runtime.tools import ToolRegistry


RuntimeEventPublisher = Callable[[RuntimeEvent], Awaitable[None]]


class ExecutionPlane(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def set_state_store(self, state_store: RuntimeStateStore) -> None: ...
    def set_event_publisher(self, publisher: RuntimeEventPublisher | None) -> None: ...


class ExecutionPlaneRuntime:
    def __init__(
        self,
        browser_runtime: BrowserRuntime | None = None,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self.browser_runtime = browser_runtime or BrowserRuntime()
        self.tool_registry = tool_registry

    async def start(self) -> None:
        await self.browser_runtime.start()

    async def stop(self) -> None:
        await self.browser_runtime.stop()

    def set_state_store(self, state_store: RuntimeStateStore) -> None:
        self.browser_runtime.set_state_store(state_store)

    def set_event_publisher(self, publisher: RuntimeEventPublisher | None) -> None:
        if hasattr(self.browser_runtime, "set_event_publisher"):
            self.browser_runtime.set_event_publisher(publisher)

    async def create_session(self, session_id: str, agent_id: str | None = None, run_id: str | None = None):
        return await self.browser_runtime.create_session(session_id, agent_id=agent_id, run_id=run_id)

    async def open(self, session_id: str, url: str):
        return await self.browser_runtime.open(session_id, url)

    async def click(self, session_id: str, selector: str):
        return await self.browser_runtime.click(session_id, selector)

    async def type(self, session_id: str, selector: str, text: str):
        return await self.browser_runtime.type(session_id, selector, text)

    async def extract(self, session_id: str, selector: str, attribute: str | None = None):
        return await self.browser_runtime.extract(session_id, selector, attribute)

    async def screenshot(self, session_id: str):
        return await self.browser_runtime.screenshot(session_id)

    async def get_layout(self, session_id: str):
        return await self.browser_runtime.get_layout(session_id)

    async def find_element(self, session_id: str, element_type: str, text: str):
        return await self.browser_runtime.find_element(session_id, element_type, text)

    async def inspect(self, session_id: str, selector: str):
        return await self.browser_runtime.inspect(session_id, selector)

    async def navigate(self, session_id: str, url: str):
        return await self.browser_runtime.navigate(session_id, url)

    async def dismiss_popups(self, session_id: str):
        return await self.browser_runtime.dismiss_popups(session_id)

    async def upload(self, session_id: str, selector: str, file_paths: list[str]):
        return await self.browser_runtime.upload(session_id, selector, file_paths)

    async def download(self, session_id: str, trigger_selector: str | None = None, timeout_ms: int = 15000):
        return await self.browser_runtime.download(session_id, trigger_selector, timeout_ms)

    async def scroll_extract(
        self,
        session_id: str,
        selector: str,
        attribute: str | None = None,
        max_scrolls: int = 8,
        scroll_step: int = 700,
    ):
        return await self.browser_runtime.scroll_extract(session_id, selector, attribute, max_scrolls, scroll_step)

    async def close_session(self, session_id: str) -> None:
        await self.browser_runtime.close_session(session_id)

    async def save_session_state(self, session_id: str, run_id: str | None = None):
        return await self.browser_runtime.save_session_state(session_id, run_id=run_id)

    async def restore_session_state(self, session_id: str):
        return await self.browser_runtime.restore_session_state(session_id)

    async def list_sessions(self, agent_id: str | None = None):
        return await self.browser_runtime.list_sessions(agent_id=agent_id)

    async def call_tool(self, tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
        if self.tool_registry is None:
            raise RuntimeError("Execution plane does not have a tool registry configured.")
        return await self.tool_registry.call(tool_name, arguments)
