from __future__ import annotations

from datetime import datetime, timezone
import logging
import asyncio
from typing import Any

try:
    from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright
except Exception:  # pragma: no cover
    Browser = Any  # type: ignore[assignment]
    BrowserContext = Any  # type: ignore[assignment]
    Page = Any  # type: ignore[assignment]
    Playwright = Any  # type: ignore[assignment]

    async def async_playwright() -> Any:  # type: ignore[misc]
        raise RuntimeError("playwright package is not installed.")

from synapse.models.runtime_state import BrowserSessionState
from synapse.models.runtime_event import EventSeverity, EventType, RuntimeEvent
from synapse.runtime.session import BrowserSession
from synapse.runtime.state_store import RuntimeStateStore


logger = logging.getLogger(__name__)


class SessionManager:
    def __init__(
        self,
        settings: Any,
        state_store: RuntimeStateStore | None = None,
        profile_manager=None,
        event_publisher=None,
    ) -> None:
        self.settings = settings
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._contexts: dict[str, BrowserContext] = {}
        self._pages: dict[str, Page] = {}
        self._session_agents: dict[str, str | None] = {}
        self._session_runs: dict[str, str | None] = {}
        self._state_store = state_store
        self._profile_manager = profile_manager
        self._downloads: dict[str, list[dict[str, object]]] = {}
        self._last_urls: dict[str, str | None] = {}
        self._event_publisher = event_publisher

    def set_state_store(self, state_store: RuntimeStateStore) -> None:
        self._state_store = state_store
        if hasattr(self._profile_manager, "set_state_store"):
            self._profile_manager.set_state_store(state_store)

    def set_event_publisher(self, event_publisher) -> None:
        self._event_publisher = event_publisher

    async def start(self) -> None:
        if self._browser is not None:
            return
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.settings.browser_headless,
            channel=self.settings.browser_channel,
        )

    async def stop(self) -> None:
        for context in self._contexts.values():
            await context.close()
        self._contexts.clear()
        self._pages.clear()
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def create_session(
        self,
        session_id: str,
        extractor,
        agent_id: str | None = None,
        run_id: str | None = None,
    ) -> BrowserSession:
        if self._browser is None:
            raise RuntimeError("Browser runtime is not started.")
        context = await self._browser.new_context()
        page = await context.new_page()
        self._contexts[session_id] = context
        self._pages[session_id] = page
        self._session_agents[session_id] = agent_id
        self._session_runs[session_id] = run_id
        self._downloads.setdefault(session_id, [])
        self._last_urls[session_id] = None
        self._attach_runtime_listeners(session_id, page)
        if run_id is not None and self._profile_manager is not None:
            await self._profile_manager.apply_profile_to_browser(run_id, context, page)
        await self.save_session_state(session_id, extractor, run_id=run_id)
        return BrowserSession(session_id=session_id, page=await extractor.snapshot_page(page))

    async def close_session(self, session_id: str) -> None:
        page = self._pages.pop(session_id, None)
        if page is not None:
            await page.close()
        context = self._contexts.pop(session_id, None)
        if context is not None:
            await context.close()
        self._session_agents.pop(session_id, None)
        self._session_runs.pop(session_id, None)
        self._downloads.pop(session_id, None)
        self._last_urls.pop(session_id, None)
        if self._state_store is not None:
            await self._state_store.delete_session(session_id)

    async def save_session_state(self, session_id: str, extractor, run_id: str | None = None) -> BrowserSessionState | None:
        if self._state_store is None:
            return None
        page = self._pages.get(session_id)
        context = self._contexts.get(session_id)
        if page is None or context is None:
            return None
        try:
            cookies = await context.cookies()
        except Exception:
            cookies = []
        storage = await self._snapshot_storage(page)
        snapshot = await extractor.snapshot_page(page)
        auth_state = self._auth_state_for_snapshot(snapshot, cookies)
        state = BrowserSessionState(
            session_id=session_id,
            agent_id=self._session_agents.get(session_id),
            run_id=run_id or self._session_runs.get(session_id),
            current_url=page.url or None,
            cookies=[dict(cookie) for cookie in cookies],
            local_storage=storage["local_storage"],
            session_storage=storage["session_storage"],
            last_active_at=datetime.now(timezone.utc),
            page_title=await page.title(),
            tabs=[{"index": 0, "url": page.url or None, "title": await page.title()}],
            auth_state=auth_state,
            downloads=list(self._downloads.get(session_id, [])),
        )
        await self._state_store.store_session(session_id, state.model_dump(mode="json"))
        self._session_runs[session_id] = state.run_id
        self._last_urls[session_id] = state.current_url
        return state

    async def restore_session_state(self, session_id: str, extractor) -> BrowserSession | None:
        if self._state_store is None:
            return None
        payload = await self._state_store.get_session(session_id)
        if payload is None:
            return None
        state = BrowserSessionState.model_validate(payload)
        if session_id not in self._pages:
            await self.create_session(session_id, extractor, agent_id=state.agent_id, run_id=state.run_id)
        page = self.require_page(session_id)
        context = self.require_context(session_id)
        self._attach_runtime_listeners(session_id, page)
        if state.cookies:
            try:
                await context.add_cookies(state.cookies)
            except Exception as exc:
                logger.warning("Failed to restore cookies for session %s: %s", session_id, exc)
        if state.current_url:
            try:
                await page.goto(state.current_url)
                await page.wait_for_load_state("domcontentloaded")
            except Exception as exc:
                logger.warning("Failed to navigate session %s to %s: %s", session_id, state.current_url, exc)
        await self._restore_storage(page, state.local_storage, state.session_storage)
        self._downloads[session_id] = list(state.downloads)
        self._session_runs[session_id] = state.run_id
        snapshot = await extractor.snapshot_page(page)
        await self.save_session_state(session_id, extractor, run_id=state.run_id)
        return BrowserSession(session_id=session_id, current_url=snapshot.url, page=snapshot)

    async def apply_attached_profile(self, session_id: str) -> bool:
        if self._profile_manager is None:
            return False
        run_id = self._session_runs.get(session_id)
        if not isinstance(run_id, str) or not run_id:
            return False
        page = self.require_page(session_id)
        context = self.require_context(session_id)
        profile = await self._profile_manager.apply_profile_to_browser(run_id, context, page)
        return profile is not None

    async def list_sessions(self, extractor, agent_id: str | None = None) -> list[BrowserSessionState]:
        if self._state_store is None:
            rows: list[BrowserSessionState] = []
            for session_id, page in self._pages.items():
                rows.append(
                    BrowserSessionState(
                        session_id=session_id,
                        agent_id=self._session_agents.get(session_id),
                        run_id=self._session_runs.get(session_id),
                        current_url=page.url or None,
                        local_storage={},
                        session_storage={},
                        last_active_at=datetime.now(timezone.utc),
                        page_title=await page.title(),
                        tabs=[{"index": 0, "url": page.url or None, "title": await page.title()}],
                        auth_state={},
                        downloads=list(self._downloads.get(session_id, [])),
                    )
                )
            return rows if agent_id is None else [row for row in rows if row.agent_id == agent_id]
        records = await self._state_store.list_sessions(agent_id=agent_id)
        return [BrowserSessionState.model_validate(record) for record in records]

    def require_page(self, session_id: str) -> Page:
        page = self._pages.get(session_id)
        if page is None:
            raise KeyError(f"Unknown session: {session_id}")
        return page

    def require_context(self, session_id: str) -> BrowserContext:
        context = self._contexts.get(session_id)
        if context is None:
            raise KeyError(f"Unknown session context: {session_id}")
        return context

    def current_url(self, session_id: str) -> str:
        return self.require_page(session_id).url

    def append_download(self, session_id: str, artifact: dict[str, object]) -> None:
        self._downloads.setdefault(session_id, []).append(artifact)

    def set_last_url(self, session_id: str, url: str | None) -> None:
        self._last_urls[session_id] = url

    def get_last_url(self, session_id: str) -> str | None:
        return self._last_urls.get(session_id)

    def _attach_runtime_listeners(self, session_id: str, page: Page) -> None:
        if not hasattr(page, "on"):
            return
        try:
            page.on("console", lambda message: self._schedule_event(self._on_console(session_id, message)))
            page.on("requestfailed", lambda request: self._schedule_event(self._on_request_failed(session_id, request)))
            page.on("framenavigated", lambda frame: self._schedule_event(self._on_frame_navigated(session_id, frame)))
            page.on("popup", lambda popup: self._schedule_event(self._on_popup(session_id, popup)))
        except Exception:
            return

    def _schedule_event(self, coro) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(coro)

    async def _on_console(self, session_id: str, message: Any) -> None:
        text_attr = getattr(message, "text", None)
        text = text_attr() if callable(text_attr) else text_attr
        type_attr = getattr(message, "type", None)
        level = type_attr() if callable(type_attr) else type_attr
        location_attr = getattr(message, "location", None)
        location = location_attr() if callable(location_attr) else location_attr
        await self._emit_runtime_event(
            session_id,
            EventType.BROWSER_CONSOLE_LOGGED,
            payload={
                "level": str(level or "info"),
                "message": str(text or ""),
                "location": dict(location) if isinstance(location, dict) else {},
            },
        )

    async def _on_request_failed(self, session_id: str, request: Any) -> None:
        failure_attr = getattr(request, "failure", None)
        failure = failure_attr() if callable(failure_attr) else failure_attr
        method_attr = getattr(request, "method", None)
        method = method_attr() if callable(method_attr) else method_attr
        resource_attr = getattr(request, "resource_type", None)
        resource_type = resource_attr() if callable(resource_attr) else resource_attr
        url = getattr(request, "url", None)
        await self._emit_runtime_event(
            session_id,
            EventType.BROWSER_NETWORK_FAILED,
            payload={
                "url": str(url or ""),
                "method": str(method or "GET"),
                "resource_type": str(resource_type or "unknown"),
                "failure_text": self._failure_text(failure),
                "status": "failed",
            },
            severity=EventSeverity.WARNING,
        )

    async def _on_frame_navigated(self, session_id: str, frame: Any) -> None:
        url = getattr(frame, "url", None)
        name_attr = getattr(frame, "name", None)
        name = name_attr() if callable(name_attr) else name_attr
        parent_attr = getattr(frame, "parent_frame", None)
        parent_frame = parent_attr() if callable(parent_attr) else parent_attr
        await self._emit_runtime_event(
            session_id,
            EventType.BROWSER_NAVIGATION_TRACED,
            payload={
                "url": str(url or ""),
                "frame_name": str(name or ""),
                "is_main_frame": parent_frame is None,
            },
        )

    async def _on_popup(self, session_id: str, popup: Any) -> None:
        url = getattr(popup, "url", None)
        title_attr = getattr(popup, "title", None)
        title = None
        try:
            title = await title_attr() if callable(title_attr) else title_attr
        except Exception:
            title = None
        await self._emit_runtime_event(
            session_id,
            EventType.BROWSER_POPUP_OPENED,
            payload={"popup_url": str(url or ""), "popup_title": str(title or "")},
            severity=EventSeverity.WARNING,
        )

    async def _emit_runtime_event(
        self,
        session_id: str,
        event_type: EventType,
        *,
        payload: dict[str, object],
        severity: EventSeverity = EventSeverity.INFO,
    ) -> None:
        if self._event_publisher is None:
            return
        await self._event_publisher(
            RuntimeEvent(
                event_type=event_type,
                run_id=self._session_runs.get(session_id),
                agent_id=self._session_agents.get(session_id),
                session_id=session_id,
                source="browser_session",
                payload=payload,
                severity=severity,
                correlation_id=self._session_runs.get(session_id) or session_id,
            )
        )

    @staticmethod
    def _failure_text(failure: Any) -> str:
        if isinstance(failure, dict):
            text = failure.get("errorText")
            return str(text or "")
        error_text = getattr(failure, "error_text", None)
        if callable(error_text):
            try:
                return str(error_text())
            except Exception:
                return str(failure)
        return str(failure or "")

    async def _snapshot_storage(self, page: Page) -> dict[str, dict[str, str]]:
        try:
            payload = await page.evaluate(
                """
                () => {
                  const collect = (storage) => {
                    const out = {};
                    for (let i = 0; i < storage.length; i += 1) {
                      const key = storage.key(i);
                      if (key) out[key] = storage.getItem(key) || "";
                    }
                    return out;
                  };
                  return { local_storage: collect(window.localStorage), session_storage: collect(window.sessionStorage) };
                }
                """
            )
            return {
                "local_storage": dict(payload.get("local_storage", {})),
                "session_storage": dict(payload.get("session_storage", {})),
            }
        except Exception:
            return {"local_storage": {}, "session_storage": {}}

    async def _restore_storage(self, page: Page, local_storage: dict[str, str], session_storage: dict[str, str]) -> None:
        if not local_storage and not session_storage:
            return
        try:
            await page.evaluate(
                """
                ({ localStorageData, sessionStorageData }) => {
                  Object.entries(localStorageData || {}).forEach(([key, value]) => window.localStorage.setItem(key, value));
                  Object.entries(sessionStorageData || {}).forEach(([key, value]) => window.sessionStorage.setItem(key, value));
                }
                """,
                {"localStorageData": local_storage, "sessionStorageData": session_storage},
            )
        except Exception as exc:
            logger.warning("Failed to restore storage state: %s", exc)

    @staticmethod
    def _auth_state_for_snapshot(snapshot, cookies: list[dict[str, object]]) -> dict[str, object]:
        cookie_names = {str(cookie.get("name", "")).lower() for cookie in cookies}
        has_auth_cookie = any(name for name in cookie_names if any(token in name for token in ["session", "auth", "token", "sid"]))
        login_inputs = [
            field
            for field in snapshot.inputs
            if (field.input_type or "").lower() in {"password", "email"}
            or "login" in (field.placeholder or "").lower()
            or "sign in" in (field.placeholder or "").lower()
        ]
        return {"authenticated": bool(has_auth_cookie and not login_inputs), "has_auth_cookie": bool(has_auth_cookie)}
