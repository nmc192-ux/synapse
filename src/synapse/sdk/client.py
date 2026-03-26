from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlencode, urlparse, urlunparse

import httpx

from synapse.models.a2a import A2AMessageType, AgentWireMessage
from synapse.models.agent import AgentBudgetUsage, AgentDefinition, AgentExecutionLimits, AgentKind
from synapse.models.browser import (
    BrowserState,
    ClickRequest,
    DismissRequest,
    DownloadRequest,
    DownloadResult,
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
    ScrollExtractRequest,
    ScrollExtractResult,
    StructuredPageModel,
    TypeRequest,
    UploadRequest,
    UploadResult,
)
from synapse.models.message import AgentMessage
from synapse.models.memory import MemoryRecord, MemorySearchRequest, MemorySearchResult, MemoryStoreRequest, MemoryType
from synapse.models.plugin import ToolDescriptor
from synapse.runtime.session import BrowserSession
from synapse.security.signing import MessageSigner


class SynapseClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        timeout: float = 30.0,
        agent_id: str | None = None,
        api_key: str | None = None,
        bearer_token: str | None = None,
        project_id: str | None = None,
        token_refresh_callback: Callable[[], str] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transport)
        self.agent_id = agent_id
        self.api_key = api_key
        self.bearer_token = bearer_token
        self.project_id = project_id
        self.token_refresh_callback = token_refresh_callback
        self._message_signer = MessageSigner()
        self._apply_auth_headers()

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
        response = self._request("POST", "/api/sessions")
        return BrowserSession.model_validate(response.json())

    def register_agent(self, agent: AgentDefinition) -> AgentDefinition:
        response = self._request("POST", "/api/agents", json=agent.model_dump(mode="json"))
        return AgentDefinition.model_validate(response.json())

    def list_tools(self) -> list[ToolDescriptor]:
        response = self._request("GET", "/api/tools")
        return [ToolDescriptor.model_validate(item) for item in response.json()]

    def call_tool(self, tool_name: str, arguments: dict[str, object] | None = None) -> dict[str, object]:
        response = self._request(
            "POST",
            "/api/tools/call",
            json={"agent_id": self.agent_id, "tool_name": tool_name, "arguments": arguments or {}},
        )
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
        response = self._request("POST", "/api/messages", json=message.model_dump(mode="json"))
        return AgentMessage.model_validate(response.json())

    def sign_a2a_message(
        self,
        *,
        agent_id: str,
        target_agent: str | None,
        message_type: A2AMessageType | str,
        payload: dict[str, object] | None = None,
        signing_key: str,
        organization_id: str | None = None,
        project_id: str | None = None,
        key_id: str = "default",
        nonce: str | None = None,
    ) -> AgentWireMessage:
        message = AgentWireMessage(
            type=message_type,
            agent=agent_id,
            sender_id=agent_id,
            target_agent=target_agent,
            recipient_id=target_agent,
            organization_id=organization_id,
            project_id=project_id or self.project_id,
            payload=payload or {},
        )
        return self._message_signer.sign_wire_message(
            message,
            signing_key=signing_key,
            key_id=key_id,
            nonce=nonce,
        )

    def send_signed_a2a_message(self, message: AgentWireMessage) -> AgentWireMessage:
        response = self._request("POST", "/api/agents/message", json=message.model_dump(mode="json"))
        return AgentWireMessage.model_validate(response.json())

    def get_budget(self, agent_id: str | None = None) -> AgentBudgetUsage:
        resolved_agent_id = agent_id or self.agent_id
        if resolved_agent_id is None:
            raise ValueError("agent_id is required to fetch budget usage.")
        response = self._request("GET", f"/api/agents/{resolved_agent_id}/budget")
        return AgentBudgetUsage.model_validate(response.json())

    def save_checkpoint(
        self,
        state: dict[str, object] | None = None,
        agent_id: str | None = None,
    ) -> dict[str, object]:
        resolved_agent_id = agent_id or self.agent_id
        if resolved_agent_id is None:
            raise ValueError("agent_id is required to save a checkpoint.")
        response = self._request(
            "POST",
            f"/api/agents/{resolved_agent_id}/checkpoint",
            json=state or {},
        )
        return dict(response.json())

    def set_bearer_token(self, token: str | None) -> None:
        self.bearer_token = token
        self._apply_auth_headers()

    def set_api_key(self, api_key: str | None) -> None:
        self.api_key = api_key
        self._apply_auth_headers()

    def set_project_id(self, project_id: str | None) -> None:
        self.project_id = project_id
        self._apply_auth_headers()

    def build_websocket_url(self, path: str = "/api/ws") -> str:
        query: dict[str, str] = {}
        if self.bearer_token:
            query["token"] = self.bearer_token
        elif self.api_key:
            query["api_key"] = self.api_key
        if self.project_id:
            query["project_id"] = self.project_id
        base = self.base_url.replace("http://", "ws://").replace("https://", "wss://")
        parsed = urlparse(f"{base}{path}")
        return urlunparse(parsed._replace(query=urlencode(query)))

    def _request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        self._apply_auth_headers()
        response = self._http.request(method, path, **kwargs)
        if response.status_code == 401 and self.token_refresh_callback is not None:
            refreshed = self.token_refresh_callback()
            if refreshed:
                self.bearer_token = refreshed
                self._apply_auth_headers()
                response = self._http.request(method, path, **kwargs)
        self._raise_for_status(response, method, path)
        return response

    def _apply_auth_headers(self) -> None:
        headers: dict[str, str] = {}
        if self.project_id:
            headers["X-Synapse-Project-Id"] = self.project_id
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        elif self.api_key:
            headers["X-API-Key"] = self.api_key
        self._http.headers.update(headers)
        for key in ("Authorization", "X-API-Key", "X-Synapse-Project-Id"):
            if key not in headers and key in self._http.headers:
                self._http.headers.pop(key, None)

    def _raise_for_status(self, response: httpx.Response, method: str, path: str) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = self._extract_error_detail(response)
            if response.status_code == 401:
                raise PermissionError(
                    f"Authentication failed for {method.upper()} {path}: {detail}. "
                    "Bearer tokens take precedence over API keys; check hosted credentials and refresh configuration."
                ) from exc
            if response.status_code == 403:
                project_suffix = f" in project '{self.project_id}'" if self.project_id else ""
                raise PermissionError(
                    f"Authorization failed for {method.upper()} {path}{project_suffix}: {detail}."
                ) from exc
            raise

    @staticmethod
    def _extract_error_detail(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            text = response.text.strip()
            return text or response.reason_phrase
        if isinstance(payload, dict):
            detail = payload.get("detail")
            if isinstance(detail, str):
                return detail
        return response.reason_phrase


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
        response = self._client._request("POST", "/api/browser/open", json=payload.model_dump(mode="json"))
        return BrowserState.model_validate(response.json())

    def click(self, selector: str) -> BrowserState:
        payload = ClickRequest(session_id=self.session_id, agent_id=self._agent_id, selector=selector)
        response = self._client._request("POST", "/api/browser/click", json=payload.model_dump(mode="json"))
        return BrowserState.model_validate(response.json())

    def type(self, selector: str, text: str) -> BrowserState:
        payload = TypeRequest(session_id=self.session_id, agent_id=self._agent_id, selector=selector, text=text)
        response = self._client._request("POST", "/api/browser/type", json=payload.model_dump(mode="json"))
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
        response = self._client._request("POST", "/api/browser/extract", json=payload.model_dump(mode="json"))
        return ExtractionResult.model_validate(response.json())

    def screenshot(self) -> ScreenshotResult:
        payload = ScreenshotRequest(session_id=self.session_id, agent_id=self._agent_id)
        response = self._client._request("POST", "/api/browser/screenshot", json=payload.model_dump(mode="json"))
        return ScreenshotResult.model_validate(response.json())

    def list_tools(self) -> list[ToolDescriptor]:
        return self._client.list_tools()

    def get_layout(self) -> StructuredPageModel:
        payload = LayoutRequest(session_id=self.session_id, agent_id=self._agent_id)
        response = self._client._request("POST", "/api/browser/layout", json=payload.model_dump(mode="json"))
        return StructuredPageModel.model_validate(response.json())

    def find_element(self, type: str, text: str) -> list[PageElementMatch]:
        payload = FindElementRequest(session_id=self.session_id, agent_id=self._agent_id, type=type, text=text)
        response = self._client._request("POST", "/api/browser/find", json=payload.model_dump(mode="json"))
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
        response = self._client._request("POST", "/api/browser/inspect", json=payload.model_dump(mode="json"))
        return PageInspection.model_validate(response.json())

    def call_tool(self, tool_name: str, arguments: dict[str, object] | None = None) -> dict[str, object]:
        return self._client.call_tool(tool_name, arguments)

    def dismiss_popups(self) -> BrowserState:
        payload = DismissRequest(session_id=self.session_id, agent_id=self._agent_id)
        response = self._client._request("POST", "/api/browser/dismiss", json=payload.model_dump(mode="json"))
        return BrowserState.model_validate(response.json())

    def upload(self, selector: str, file_paths: list[str]) -> UploadResult:
        payload = UploadRequest(session_id=self.session_id, agent_id=self._agent_id, selector=selector, file_paths=file_paths)
        response = self._client._request("POST", "/api/browser/upload", json=payload.model_dump(mode="json"))
        return UploadResult.model_validate(response.json())

    def download(self, trigger_selector: str | None = None, timeout_ms: int = 15000) -> DownloadResult:
        payload = DownloadRequest(
            session_id=self.session_id,
            agent_id=self._agent_id,
            trigger_selector=trigger_selector,
            timeout_ms=timeout_ms,
        )
        response = self._client._request("POST", "/api/browser/download", json=payload.model_dump(mode="json"))
        return DownloadResult.model_validate(response.json())

    def scroll_extract(
        self,
        selector: str,
        attribute: str | None = None,
        max_scrolls: int = 8,
        scroll_step: int = 700,
    ) -> ScrollExtractResult:
        payload = ScrollExtractRequest(
            session_id=self.session_id,
            agent_id=self._agent_id,
            selector=selector,
            attribute=attribute,
            max_scrolls=max_scrolls,
            scroll_step=scroll_step,
        )
        response = self._client._request("POST", "/api/browser/scroll_extract", json=payload.model_dump(mode="json"))
        return ScrollExtractResult.model_validate(response.json())

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

    def sign_a2a_message(
        self,
        *,
        message_type: A2AMessageType | str,
        payload: dict[str, object] | None = None,
        signing_key: str,
        target_agent: str | None = None,
        key_id: str = "default",
        nonce: str | None = None,
    ) -> AgentWireMessage:
        if self._agent_id is None:
            raise ValueError("agent_id is required to sign an A2A message.")
        return self._client.sign_a2a_message(
            agent_id=self._agent_id,
            target_agent=target_agent,
            message_type=message_type,
            payload=payload,
            signing_key=signing_key,
            key_id=key_id,
            nonce=nonce,
        )

    def send_signed_a2a_message(self, message: AgentWireMessage) -> AgentWireMessage:
        return self._client.send_signed_a2a_message(message)

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
        response = self._client._request("POST", "/api/memory/store", json=payload.model_dump(mode="json"))
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
        response = self._client._request("POST", "/api/memory/search", json=payload.model_dump(mode="json"))
        return [MemorySearchResult.model_validate(item) for item in response.json()]

    def get_recent(self, agent_id: str, limit: int = 10) -> list[MemoryRecord]:
        response = self._client._request("GET", f"/api/memory/{agent_id}/recent", params={"limit": limit})
        return [MemoryRecord.model_validate(item) for item in response.json()]


class SynapseAgent:
    def __init__(
        self,
        name: str,
        base_url: str = "http://127.0.0.1:8000",
        kind: AgentKind | str = AgentKind.CUSTOM,
        limits: dict[str, int] | AgentExecutionLimits | None = None,
        timeout: float = 30.0,
        api_key: str | None = None,
        bearer_token: str | None = None,
        project_id: str | None = None,
        token_refresh_callback: Callable[[], str] | None = None,
    ) -> None:
        self.name = name
        self.client = SynapseClient(
            base_url=base_url,
            timeout=timeout,
            agent_id=name,
            api_key=api_key,
            bearer_token=bearer_token,
            project_id=project_id,
            token_refresh_callback=token_refresh_callback,
        )
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
        api_key: str | None = None,
        bearer_token: str | None = None,
        project_id: str | None = None,
        token_refresh_callback: Callable[[], str] | None = None,
    ) -> None:
        self.client = SynapseClient(
            base_url=base_url,
            timeout=timeout,
            agent_id=agent_id,
            api_key=api_key,
            bearer_token=bearer_token,
            project_id=project_id,
            token_refresh_callback=token_refresh_callback,
        )
        super().__init__(self.client, agent_id=agent_id)

    def close(self) -> None:
        self.client.close()
