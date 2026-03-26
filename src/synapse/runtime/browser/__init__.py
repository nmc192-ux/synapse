from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

from synapse.config import settings
from synapse.models.browser import (
    BrowserState,
    DownloadArtifact,
    DownloadResult,
    ExtractionResult,
    ExtractRequest,
    FindElementRequest,
    LayoutRequest,
    OpenRequest,
    PageElementMatch,
    PageInspection,
    ScreenshotResult,
    ScrollExtractResult,
    StructuredPageModel,
    UploadResult,
)
from synapse.models.runtime_state import BrowserSessionState
from synapse.runtime.browser.download_manager import DownloadManager
from synapse.runtime.browser.interaction_engine import InteractionEngine
from synapse.runtime.browser.recovery_engine import RecoveryEngine
from synapse.runtime.browser.session_manager import SessionManager
from synapse.runtime.browser.spm_extractor import SPMExtractor
from synapse.runtime.browser.upload_manager import UploadManager
from synapse.runtime.session import BrowserSession
from synapse.runtime.state_store import RuntimeStateStore


class BrowserRuntime:
    """Thin facade that composes the focused browser runtime services."""

    def __init__(self, state_store: RuntimeStateStore | None = None, profile_manager=None) -> None:
        self.session_manager = SessionManager(settings=settings, state_store=state_store, profile_manager=profile_manager)
        self.spm_extractor = SPMExtractor()
        self.recovery_engine = RecoveryEngine()
        self.download_manager = DownloadManager()
        self.upload_manager = UploadManager()
        self.interaction_engine = InteractionEngine(
            session_manager=self.session_manager,
            spm_extractor=self.spm_extractor,
            recovery_engine=self.recovery_engine,
            download_manager=self.download_manager,
            upload_manager=self.upload_manager,
        )

    def set_state_store(self, state_store: RuntimeStateStore) -> None:
        self.session_manager.set_state_store(state_store)

    def set_event_publisher(self, event_publisher) -> None:
        self.session_manager.set_event_publisher(event_publisher)

    @property
    def _pages(self):
        return self.session_manager._pages

    @property
    def _contexts(self):
        return self.session_manager._contexts

    @property
    def _session_agents(self):
        return self.session_manager._session_agents

    async def start(self) -> None:
        await self.session_manager.start()

    async def stop(self) -> None:
        await self.session_manager.stop()

    async def create_session(
        self,
        session_id: str,
        agent_id: str | None = None,
        run_id: str | None = None,
    ) -> BrowserSession:
        return await self.session_manager.create_session(session_id, self._extractor_proxy(), agent_id=agent_id, run_id=run_id)

    async def open(self, session_id: str, url: str) -> BrowserState:
        return await self.interaction_engine.open(session_id, url)

    async def click(self, session_id: str, selector: str) -> BrowserState:
        return await self.interaction_engine.click(session_id, selector)

    async def type(self, session_id: str, selector: str, text: str) -> BrowserState:
        return await self.interaction_engine.type(session_id, selector, text)

    async def extract(self, session_id: str, selector: str, attribute: str | None = None) -> ExtractionResult:
        return await self.interaction_engine.extract(session_id, selector, attribute)

    async def screenshot(self, session_id: str) -> ScreenshotResult:
        return await self.interaction_engine.screenshot(session_id)

    async def get_layout(self, session_id: str) -> StructuredPageModel:
        return await self.interaction_engine.get_layout(session_id)

    async def find_element(self, session_id: str, element_type: str, text: str) -> list[PageElementMatch]:
        return await self.interaction_engine.find_element(session_id, element_type, text)

    async def inspect(self, session_id: str, selector: str) -> PageInspection:
        return await self.interaction_engine.inspect(session_id, selector)

    async def navigate(self, session_id: str, url: str) -> BrowserSession:
        state = await self.open(session_id, url)
        return BrowserSession(session_id=session_id, current_url=state.page.url, page=state.page)

    async def dismiss_popups(self, session_id: str) -> BrowserState:
        return await self.interaction_engine.dismiss_popups(session_id)

    async def upload(self, session_id: str, selector: str, file_paths: list[str]) -> UploadResult:
        return await self.upload_manager.upload(
            session_id=session_id,
            selector=selector,
            file_paths=file_paths,
            session_manager=self.session_manager,
            extractor=self.spm_extractor,
            recovery=self.recovery_engine,
        )

    async def download(
        self,
        session_id: str,
        trigger_selector: str | None = None,
        timeout_ms: int = 15000,
    ) -> DownloadResult:
        return await self.download_manager.download(
            session_id=session_id,
            trigger_selector=trigger_selector,
            timeout_ms=timeout_ms,
            session_manager=self.session_manager,
            extractor=self.spm_extractor,
            recovery=self.recovery_engine,
        )

    async def scroll_extract(
        self,
        session_id: str,
        selector: str,
        attribute: str | None = None,
        max_scrolls: int = 8,
        scroll_step: int = 700,
    ) -> ScrollExtractResult:
        return await self.interaction_engine.scroll_extract(session_id, selector, attribute, max_scrolls, scroll_step)

    async def close_session(self, session_id: str) -> None:
        await self.session_manager.close_session(session_id)

    async def save_session_state(self, session_id: str, run_id: str | None = None) -> BrowserSessionState | None:
        return await self.session_manager.save_session_state(session_id, self._extractor_proxy(), run_id=run_id)

    async def restore_session_state(self, session_id: str) -> BrowserSession | None:
        return await self.session_manager.restore_session_state(session_id, self._extractor_proxy())

    async def apply_attached_profile(self, session_id: str) -> bool:
        return await self.session_manager.apply_attached_profile(session_id)

    async def list_sessions(self, agent_id: str | None = None) -> list[BrowserSessionState]:
        return await self.session_manager.list_sessions(self._extractor_proxy(), agent_id=agent_id)

    def current_url(self, session_id: str) -> str:
        return self.session_manager.current_url(session_id)

    async def _dismiss_blockers(self, page: Any) -> list[str]:
        return await self.recovery_engine.dismiss_blockers(page)

    async def _retry_click(self, page: Any, selector: str, retries: int = 3) -> None:
        await self.recovery_engine.retry_click(page, selector, retries=retries)

    async def _retry_type(self, page: Any, selector: str, text: str, retries: int = 3) -> None:
        await self.recovery_engine.retry_type(page, selector, text, retries=retries)

    async def _capture_download(self, page: Any, selector: str, timeout_ms: int) -> DownloadArtifact:
        return await self.download_manager.capture_download(page, selector, timeout_ms, self.recovery_engine)

    async def _snapshot_page(self, page: Any) -> StructuredPageModel:
        return await self.spm_extractor.snapshot_page(page)

    async def _stabilize_page(self, page: Any) -> None:
        await self.recovery_engine.stabilize_page(page)

    @staticmethod
    def _route_change_metadata(before_url: str | None, after_url: str | None) -> dict[str, object]:
        return RecoveryEngine.route_change_metadata(before_url, after_url)

    @staticmethod
    def _classify_browser_error(action: str, exc: Exception) -> str:
        return RecoveryEngine.classify_browser_error(action, exc)

    def _extractor_proxy(self):
        return SimpleNamespace(snapshot_page=self._snapshot_page)


@asynccontextmanager
async def browser_runtime_lifespan(runtime: BrowserRuntime) -> AsyncIterator[None]:
    await runtime.start()
    try:
        yield
    finally:
        await runtime.stop()
