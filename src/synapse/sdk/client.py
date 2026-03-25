from __future__ import annotations

import httpx

from synapse.models.agent import AgentBudgetUsage, AgentDefinition, AgentExecutionLimits, AgentKind
from synapse.models.browser import (
    BrowserState,
    ClickRequest,
    ExtractionResult,
    ExtractRequest,
    FindElementRequest,
    InspectRequest,
    LayoutRequest,
    OpenRequest,
    PageElementMatch,
    PageInspection,
    ScreenshotRequest,
    ScreenshotResult,
    StructuredPageModel,
    TypeRequest,
)
from synapse.models.message import AgentMessage
from synapse.models.memory import MemoryRecord, MemorySearchRequest, MemorySearchResult, MemoryStoreRequest, MemoryType
from synapse.models.plugin import ToolDescriptor
from synapse.runtime.session import BrowserSession


class SynapseClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        timeout: float = 30.0,
        agent_id: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout)
        self.agent_id = agent_id

    @property
    def browser(self) -> SynapseBrowser:
        return SynapseBrowser(self, agent_id=self.agent_id)

    @property
    def memory(self) -> SynapseMemory:
        return SynapseMemory(self)

    def __enter__(self) -> SynapseClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def create_session(self) -> BrowserSession:
        response = self._http.post("/api/sessions")
        response.raise_for_status()
        return BrowserSession.model_validate(response.json())

    def register_agent(self, agent: AgentDefinition) -> AgentDefinition:
        response = self._http.post("/api/agents", json=agent.model_dump(mode="json"))
        response.raise_for_status()
        return AgentDefinition.model_validate(response.json())

    def list_tools(self) -> list[ToolDescriptor]:
        response = self._http.get("/api/tools")
        response.raise_for_status()
        return [ToolDescriptor.model_validate(item) for item in response.json()]

    def call_tool(self, tool_name: str, arguments: dict[str, object] | None = None) -> dict[str, object]:
        response = self._http.post(
            "/api/tools/call",
            json={"agent_id": self.agent_id, "tool_name": tool_name, "arguments": arguments or {}},
        )
        response.raise_for_status()
        return dict(response.json())

    def send_agent_message(
        self,
        sender_agent_id: str,
        recipient_agent_id: str,
        content: str,
        metadata: dict[str, object] | None = None,
    ) -> AgentMessage:
        message = AgentMessage(
            sender_agent_id=sender_agent_id,
            recipient_agent_id=recipient_agent_id,
            content=content,
            metadata=metadata or {},
        )
        response = self._http.post("/api/messages", json=message.model_dump(mode="json"))
        response.raise_for_status()
        return AgentMessage.model_validate(response.json())

    def get_budget(self, agent_id: str | None = None) -> AgentBudgetUsage:
        resolved_agent_id = agent_id or self.agent_id
        if resolved_agent_id is None:
            raise ValueError("agent_id is required to fetch budget usage.")
        response = self._http.get(f"/api/agents/{resolved_agent_id}/budget")
        response.raise_for_status()
        return AgentBudgetUsage.model_validate(response.json())

    def save_checkpoint(
        self,
        state: dict[str, object] | None = None,
        agent_id: str | None = None,
    ) -> dict[str, object]:
        resolved_agent_id = agent_id or self.agent_id
        if resolved_agent_id is None:
            raise ValueError("agent_id is required to save a checkpoint.")
        response = self._http.post(
            f"/api/agents/{resolved_agent_id}/checkpoint",
            json=state or {},
        )
        response.raise_for_status()
        return dict(response.json())


class SynapseBrowser:
    def __init__(
        self,
        client: SynapseClient,
        session_id: str | None = None,
        agent_id: str | None = None,
    ) -> None:
        self._client = client
        self._session_id = session_id
        self._agent_id = agent_id

    @property
    def session_id(self) -> str:
        if self._session_id is None:
            self._session_id = self._client.create_session().session_id
        return self._session_id

    def open(self, url: str) -> BrowserState:
        payload = OpenRequest(session_id=self.session_id, agent_id=self._agent_id, url=url)
        response = self._client._http.post("/api/browser/open", json=payload.model_dump(mode="json"))
        response.raise_for_status()
        return BrowserState.model_validate(response.json())

    def click(self, selector: str) -> BrowserState:
        payload = ClickRequest(session_id=self.session_id, agent_id=self._agent_id, selector=selector)
        response = self._client._http.post("/api/browser/click", json=payload.model_dump(mode="json"))
        response.raise_for_status()
        return BrowserState.model_validate(response.json())

    def type(self, selector: str, text: str) -> BrowserState:
        payload = TypeRequest(session_id=self.session_id, agent_id=self._agent_id, selector=selector, text=text)
        response = self._client._http.post("/api/browser/type", json=payload.model_dump(mode="json"))
        response.raise_for_status()
        return BrowserState.model_validate(response.json())

    def extract(
        self,
        selector: str | PageElementMatch | list[PageElementMatch] | tuple[PageElementMatch, ...],
        attribute: str | None = None,
    ) -> ExtractionResult:
        if isinstance(selector, PageElementMatch):
            resolved_selector = selector.selector_hint
            if resolved_selector is None:
                raise ValueError("PageElementMatch is missing selector_hint.")
            selector = resolved_selector

        if isinstance(selector, (list, tuple)):
            results = [self.extract(match, attribute=attribute) for match in selector]
            if not results:
                return ExtractionResult(session_id=self.session_id, matches=[], page=self.get_layout())
            merged_matches = [item for result in results for item in result.matches]
            return ExtractionResult(
                session_id=self.session_id,
                matches=merged_matches,
                page=results[-1].page,
            )

        payload = ExtractRequest(
            session_id=self.session_id,
            agent_id=self._agent_id,
            selector=selector,
            attribute=attribute,
        )
        response = self._client._http.post("/api/browser/extract", json=payload.model_dump(mode="json"))
        response.raise_for_status()
        return ExtractionResult.model_validate(response.json())

    def screenshot(self) -> ScreenshotResult:
        payload = ScreenshotRequest(session_id=self.session_id, agent_id=self._agent_id)
        response = self._client._http.post("/api/browser/screenshot", json=payload.model_dump(mode="json"))
        response.raise_for_status()
        return ScreenshotResult.model_validate(response.json())

    def list_tools(self) -> list[ToolDescriptor]:
        return self._client.list_tools()

    def get_layout(self) -> StructuredPageModel:
        payload = LayoutRequest(session_id=self.session_id, agent_id=self._agent_id)
        response = self._client._http.post("/api/browser/layout", json=payload.model_dump(mode="json"))
        response.raise_for_status()
        return StructuredPageModel.model_validate(response.json())

    def find_element(self, type: str, text: str) -> list[PageElementMatch]:
        payload = FindElementRequest(session_id=self.session_id, agent_id=self._agent_id, type=type, text=text)
        response = self._client._http.post("/api/browser/find", json=payload.model_dump(mode="json"))
        response.raise_for_status()
        return [PageElementMatch.model_validate(item) for item in response.json()]

    def find(self, text: str, element_types: list[str] | None = None) -> list[PageElementMatch]:
        resolved_types = element_types or ["sections", "buttons", "inputs", "forms", "tables", "links"]
        matches: list[PageElementMatch] = []
        seen: set[tuple[str, str | None, str]] = set()
        for element_type in resolved_types:
            for match in self.find_element(element_type, text):
                key = (match.element_type, match.selector_hint, match.text)
                if key in seen:
                    continue
                seen.add(key)
                matches.append(match)
        return matches

    def inspect(self, selector: str) -> PageInspection:
        payload = InspectRequest(session_id=self.session_id, agent_id=self._agent_id, selector=selector)
        response = self._client._http.post("/api/browser/inspect", json=payload.model_dump(mode="json"))
        response.raise_for_status()
        return PageInspection.model_validate(response.json())

    def call_tool(self, tool_name: str, arguments: dict[str, object] | None = None) -> dict[str, object]:
        return self._client.call_tool(tool_name, arguments)

    def send_agent_message(
        self,
        sender_agent_id: str,
        recipient_agent_id: str,
        content: str,
        metadata: dict[str, object] | None = None,
    ) -> AgentMessage:
        return self._client.send_agent_message(
            sender_agent_id=sender_agent_id,
            recipient_agent_id=recipient_agent_id,
            content=content,
            metadata=metadata,
        )

    def fork(self, session_id: str | None = None) -> SynapseBrowser:
        return SynapseBrowser(
            self._client,
            session_id=session_id or self._session_id,
            agent_id=self._agent_id,
        )


class SynapseMemory:
    def __init__(self, client: SynapseClient) -> None:
        self._client = client

    def store(
        self,
        agent_id: str,
        memory_type: MemoryType | str,
        content: str,
        embedding: list[float] | None = None,
    ) -> MemoryRecord:
        payload = MemoryStoreRequest(
            agent_id=agent_id,
            memory_type=memory_type,
            content=content,
            embedding=embedding or [],
        )
        response = self._client._http.post("/api/memory/store", json=payload.model_dump(mode="json"))
        response.raise_for_status()
        return MemoryRecord.model_validate(response.json())

    def search(
        self,
        agent_id: str,
        query: str | None = None,
        embedding: list[float] | None = None,
        memory_type: MemoryType | str | None = None,
        limit: int = 5,
    ) -> list[MemorySearchResult]:
        payload = MemorySearchRequest(
            agent_id=agent_id,
            query=query,
            embedding=embedding or [],
            memory_type=memory_type,
            limit=limit,
        )
        response = self._client._http.post("/api/memory/search", json=payload.model_dump(mode="json"))
        response.raise_for_status()
        return [MemorySearchResult.model_validate(item) for item in response.json()]

    def get_recent(self, agent_id: str, limit: int = 10) -> list[MemoryRecord]:
        response = self._client._http.get(f"/api/memory/{agent_id}/recent", params={"limit": limit})
        response.raise_for_status()
        return [MemoryRecord.model_validate(item) for item in response.json()]


class SynapseAgent:
    def __init__(
        self,
        name: str,
        base_url: str = "http://127.0.0.1:8000",
        kind: AgentKind | str = AgentKind.CUSTOM,
        limits: dict[str, int] | AgentExecutionLimits | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.name = name
        self.client = SynapseClient(base_url=base_url, timeout=timeout, agent_id=name)
        self.kind = AgentKind(kind)
        self.limits = (
            limits if isinstance(limits, AgentExecutionLimits) or limits is None else AgentExecutionLimits(**limits)
        )

    @property
    def browser(self) -> SynapseBrowser:
        return self.client.browser

    @property
    def memory(self) -> SynapseMemory:
        return self.client.memory

    def register(self, **definition_overrides: object) -> AgentDefinition:
        agent = AgentDefinition(
            agent_id=self.name,
            kind=self.kind,
            name=self.name,
            limits=self.limits,
            **definition_overrides,
        )
        return self.client.register_agent(agent)

    def get_budget(self) -> AgentBudgetUsage:
        return self.client.get_budget(self.name)

    def save_checkpoint(self, state: dict[str, object] | None = None) -> dict[str, object]:
        return self.client.save_checkpoint(state=state, agent_id=self.name)

    def close(self) -> None:
        self.client.close()


class Synapse(SynapseBrowser):
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        timeout: float = 30.0,
        agent_id: str | None = None,
    ) -> None:
        self.client = SynapseClient(base_url=base_url, timeout=timeout, agent_id=agent_id)
        super().__init__(self.client, agent_id=agent_id)

    def close(self) -> None:
        self.client.close()
