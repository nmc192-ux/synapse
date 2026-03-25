from __future__ import annotations

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
from synapse.models.events import EventType
from synapse.models.runtime_state import BrowserSessionState
from synapse.models.task import ExtractionRequest, NavigationRequest
from synapse.runtime.budget_service import BudgetService
from synapse.runtime.event_bus import EventBus
from synapse.runtime.safety import AgentSafetyLayer, SecurityAlertError, SecurityFinding
from synapse.runtime.security import AgentSecuritySandbox
from synapse.runtime.session import BrowserSession
from synapse.runtime.state_store import RuntimeStateStore


class BrowserService:
    def __init__(
        self,
        browser,
        sandbox: AgentSecuritySandbox,
        safety: AgentSafetyLayer,
        events: EventBus,
        budget_service: BudgetService,
        state_store: RuntimeStateStore | None = None,
    ) -> None:
        self.browser = browser
        self.sandbox = sandbox
        self.safety = safety
        self.events = events
        self.budget_service = budget_service
        self.state_store = state_store

    def set_state_store(self, state_store: RuntimeStateStore) -> None:
        self.state_store = state_store

    async def create_session(self, session_id: str, agent_id: str | None = None) -> BrowserSession:
        session = await self.browser.create_session(session_id, agent_id=agent_id)
        await self.events.emit(
            EventType.SESSION_CREATED,
            session_id=session.session_id,
            payload={"session_id": session.session_id},
        )
        return session

    async def navigate(self, request: NavigationRequest) -> BrowserSession:
        self.sandbox.authorize_domain(request.agent_id, str(request.url))
        self.sandbox.consume_browser_action(request.agent_id)
        if request.agent_id:
            await self.budget_service.increment_page(request.agent_id)
        session = await self.browser.navigate(request.session_id, str(request.url))
        await self._enforce_page_safety(request.agent_id, session.session_id, "browser.navigate", session.page)
        await self.events.emit(
            EventType.PAGE_NAVIGATED,
            session_id=session.session_id,
            agent_id=request.agent_id,
            payload={"url": session.current_url},
        )
        return session

    async def open(self, request: OpenRequest) -> BrowserState:
        return await self._run_state_action(
            "open",
            request.agent_id,
            request.session_id,
            lambda: self.browser.open(request.session_id, str(request.url)),
            precheck_url=str(request.url),
            increment_page=True,
            payload={"action": "open"},
        )

    async def click(self, request: ClickRequest) -> BrowserState:
        return await self._run_state_action(
            "click",
            request.agent_id,
            request.session_id,
            lambda: self.browser.click(request.session_id, request.selector),
            payload={"action": "click", "selector": request.selector},
        )

    async def type(self, request: TypeRequest) -> BrowserState:
        return await self._run_state_action(
            "type",
            request.agent_id,
            request.session_id,
            lambda: self.browser.type(request.session_id, request.selector, request.text),
            payload={"action": "type", "selector": request.selector},
        )

    async def extract(self, request: ExtractionRequest | ExtractRequest) -> ExtractionResult:
        await self._ensure_current_page_safe(request.agent_id, request.session_id, "browser.extract")
        self.sandbox.authorize_domain(request.agent_id, self.browser.current_url(request.session_id))
        self.sandbox.consume_browser_action(request.agent_id)
        payload = await self.browser.extract(request.session_id, request.selector, request.attribute)
        await self._enforce_page_safety(request.agent_id, request.session_id, "browser.extract", payload.page)
        await self.events.emit(
            EventType.DATA_EXTRACTED,
            session_id=request.session_id,
            agent_id=request.agent_id,
            payload=payload.model_dump(mode="json"),
        )
        return payload

    async def screenshot(self, request: ScreenshotRequest) -> ScreenshotResult:
        await self._ensure_current_page_safe(request.agent_id, request.session_id, "browser.screenshot")
        self.sandbox.authorize_domain(request.agent_id, self.browser.current_url(request.session_id))
        self.sandbox.consume_browser_action(request.agent_id)
        result = await self.browser.screenshot(request.session_id)
        await self._enforce_page_safety(request.agent_id, request.session_id, "browser.screenshot", result.page)
        await self.events.emit(
            EventType.SCREENSHOT_CAPTURED,
            session_id=request.session_id,
            agent_id=request.agent_id,
            payload={"action": "screenshot", **result.model_dump(mode="json")},
        )
        return result

    async def get_layout(self, request: LayoutRequest) -> StructuredPageModel:
        await self._ensure_current_page_safe(request.agent_id, request.session_id, "browser.get_layout")
        self.sandbox.authorize_domain(request.agent_id, self.browser.current_url(request.session_id))
        self.sandbox.consume_browser_action(request.agent_id)
        return await self.browser.get_layout(request.session_id)

    async def find_element(self, request: FindElementRequest) -> list[PageElementMatch]:
        await self._ensure_current_page_safe(request.agent_id, request.session_id, "browser.find_element")
        self.sandbox.authorize_domain(request.agent_id, self.browser.current_url(request.session_id))
        self.sandbox.consume_browser_action(request.agent_id)
        return await self.browser.find_element(request.session_id, request.type, request.text)

    async def inspect(self, request: InspectRequest) -> PageInspection:
        await self._ensure_current_page_safe(request.agent_id, request.session_id, "browser.inspect")
        self.sandbox.authorize_domain(request.agent_id, self.browser.current_url(request.session_id))
        self.sandbox.consume_browser_action(request.agent_id)
        return await self.browser.inspect(request.session_id, request.selector)

    async def dismiss_popups(self, request: DismissRequest) -> BrowserState:
        await self._ensure_current_page_safe(request.agent_id, request.session_id, "browser.dismiss")
        self.sandbox.authorize_domain(request.agent_id, self.browser.current_url(request.session_id))
        self.sandbox.consume_browser_action(request.agent_id)
        state = await self.browser.dismiss_popups(request.session_id)
        await self.events.emit(
            EventType.POPUP_DISMISSED,
            session_id=request.session_id,
            agent_id=request.agent_id,
            payload=state.metadata,
        )
        return state

    async def upload(self, request: UploadRequest) -> UploadResult:
        await self._ensure_current_page_safe(request.agent_id, request.session_id, "browser.upload")
        self.sandbox.authorize_domain(request.agent_id, self.browser.current_url(request.session_id))
        self.sandbox.consume_browser_action(request.agent_id)
        result = await self.browser.upload(request.session_id, request.selector, request.file_paths)
        await self.events.emit(
            EventType.UPLOAD_COMPLETED,
            session_id=request.session_id,
            agent_id=request.agent_id,
            payload=result.model_dump(mode="json"),
        )
        return result

    async def download(self, request: DownloadRequest) -> DownloadResult:
        await self._ensure_current_page_safe(request.agent_id, request.session_id, "browser.download")
        self.sandbox.authorize_domain(request.agent_id, self.browser.current_url(request.session_id))
        self.sandbox.consume_browser_action(request.agent_id)
        result = await self.browser.download(request.session_id, request.trigger_selector, request.timeout_ms)
        await self.events.emit(
            EventType.DOWNLOAD_COMPLETED,
            session_id=request.session_id,
            agent_id=request.agent_id,
            payload=result.model_dump(mode="json"),
        )
        return result

    async def scroll_extract(self, request: ScrollExtractRequest) -> ScrollExtractResult:
        await self._ensure_current_page_safe(request.agent_id, request.session_id, "browser.scroll_extract")
        self.sandbox.authorize_domain(request.agent_id, self.browser.current_url(request.session_id))
        self.sandbox.consume_browser_action(request.agent_id)
        result = await self.browser.scroll_extract(
            request.session_id,
            selector=request.selector,
            attribute=request.attribute,
            max_scrolls=request.max_scrolls,
            scroll_step=request.scroll_step,
        )
        await self.events.emit(
            EventType.DATA_EXTRACTED,
            session_id=request.session_id,
            agent_id=request.agent_id,
            payload=result.model_dump(mode="json"),
        )
        return result

    async def list_sessions(self, agent_id: str | None = None) -> list[BrowserSessionState]:
        return await self.browser.list_sessions(agent_id=agent_id)

    async def get_session(self, session_id: str) -> BrowserSessionState:
        if self.state_store is None:
            raise KeyError(f"Session not found: {session_id}")
        payload = await self.state_store.get_session(session_id)
        if payload is None:
            raise KeyError(f"Session not found: {session_id}")
        return BrowserSessionState.model_validate(payload)

    async def save_session_state(self, session_id: str, agent_id: str | None = None, task_id: str | None = None) -> None:
        await self.browser.save_session_state(session_id)
        await self.events.emit(
            EventType.SESSION_SAVED,
            session_id=session_id,
            agent_id=agent_id,
            payload={"task_id": task_id, "session_id": session_id},
        )

    async def restore_session_state(self, session_id: str, agent_id: str | None = None, checkpoint_id: str | None = None):
        restored = await self.browser.restore_session_state(session_id)
        if restored is not None:
            await self.events.emit(
                EventType.SESSION_RESTORED,
                session_id=session_id,
                agent_id=agent_id,
                payload={"checkpoint_id": checkpoint_id, "session_id": session_id},
            )
        return restored

    async def _run_state_action(
        self,
        action: str,
        agent_id: str | None,
        session_id: str,
        executor,
        *,
        precheck_url: str | None = None,
        increment_page: bool = False,
        payload: dict[str, object] | None = None,
    ) -> BrowserState:
        try:
            if precheck_url is None:
                await self._ensure_current_page_safe(agent_id, session_id, f"browser.{action}")
                self.sandbox.authorize_domain(agent_id, self.browser.current_url(session_id))
            else:
                self.sandbox.authorize_domain(agent_id, precheck_url)
            self.sandbox.consume_browser_action(agent_id)
            if increment_page and agent_id:
                await self.budget_service.increment_page(agent_id)
            state = await executor()
            await self._enforce_page_safety(agent_id, state.session_id, f"browser.{action}", state.page)
            await self.events.emit(
                EventType.PAGE_NAVIGATED,
                session_id=state.session_id,
                agent_id=agent_id,
                payload={**(payload or {}), **state.model_dump(mode="json")},
            )
            await self._emit_browser_metadata_events(agent_id, state.session_id, state.metadata)
            return state
        except Exception as exc:
            await self._emit_browser_error(action, agent_id, session_id, exc)
            raise

    async def _ensure_current_page_safe(self, agent_id: str | None, session_id: str, action: str) -> None:
        page = await self.browser.get_layout(session_id)
        await self._enforce_page_safety(agent_id, session_id, action, page)

    async def _enforce_page_safety(
        self,
        agent_id: str | None,
        session_id: str | None,
        action: str,
        page: StructuredPageModel | None,
    ) -> None:
        if page is None:
            return
        finding = self.safety.inspect_page(page, action)
        if finding is not None:
            await self._raise_security_alert(agent_id, session_id, finding)

    async def _raise_security_alert(
        self,
        agent_id: str | None,
        session_id: str | None,
        finding: SecurityFinding,
    ) -> None:
        await self.events.emit(
            EventType.SECURITY_ALERT,
            session_id=session_id,
            agent_id=agent_id,
            payload=finding.model_dump(mode="json"),
        )
        raise SecurityAlertError(finding)

    async def _emit_browser_metadata_events(
        self,
        agent_id: str | None,
        session_id: str | None,
        metadata: dict[str, object],
    ) -> None:
        if metadata.get("route_changed"):
            await self.events.emit(
                EventType.NAVIGATION_ROUTE_CHANGED,
                agent_id=agent_id,
                session_id=session_id,
                payload=metadata,
            )
        if metadata.get("session_expired"):
            await self.events.emit(
                EventType.SESSION_EXPIRED,
                agent_id=agent_id,
                session_id=session_id,
                payload=metadata,
            )
        dismissed = metadata.get("dismissed_blockers")
        if isinstance(dismissed, list) and dismissed:
            await self.events.emit(
                EventType.POPUP_DISMISSED,
                agent_id=agent_id,
                session_id=session_id,
                payload={"dismissed_blockers": dismissed},
            )

    async def _emit_browser_error(
        self,
        action: str,
        agent_id: str | None,
        session_id: str | None,
        exc: Exception,
    ) -> None:
        await self.events.emit(
            EventType.BROWSER_ERROR,
            agent_id=agent_id,
            session_id=session_id,
            payload={"action": action, "error": str(exc)},
        )
