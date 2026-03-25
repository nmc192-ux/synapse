from __future__ import annotations

import base64
from typing import Any

from synapse.models.browser import BrowserState, ExtractedElement, ExtractionResult, ScreenshotResult, ScrollExtractResult


class InteractionEngine:
    def __init__(
        self,
        *,
        session_manager,
        spm_extractor,
        recovery_engine,
        download_manager,
        upload_manager,
    ) -> None:
        self.session_manager = session_manager
        self.spm_extractor = spm_extractor
        self.recovery_engine = recovery_engine
        self.download_manager = download_manager
        self.upload_manager = upload_manager

    async def open(self, session_id: str, url: str) -> BrowserState:
        page = self.session_manager.require_page(session_id)
        before_url = page.url
        try:
            await page.goto(url)
            await self.recovery_engine.wait_for_navigation_ready(page)
            dismissed = await self.recovery_engine.dismiss_blockers(page)
            snapshot = await self.spm_extractor.snapshot_page(page)
            metadata = self.recovery_engine.route_change_metadata(before_url, snapshot.url)
            metadata["dismissed_blockers"] = dismissed
            metadata["session_expired"] = self.recovery_engine.detect_session_expired(self.session_manager, session_id, snapshot)
            await self.session_manager.save_session_state(session_id, self.spm_extractor)
            return BrowserState(session_id=session_id, page=snapshot, metadata=metadata)
        except Exception as exc:
            raise RuntimeError(self.recovery_engine.classify_browser_error("open", exc)) from exc

    async def click(self, session_id: str, selector: str) -> BrowserState:
        page = self.session_manager.require_page(session_id)
        before_url = page.url
        await self.recovery_engine.dismiss_blockers(page)
        await self.recovery_engine.retry_click(page, selector)
        await self.recovery_engine.wait_for_navigation_ready(page)
        dismissed = await self.recovery_engine.dismiss_blockers(page)
        snapshot = await self.spm_extractor.snapshot_page(page)
        metadata = self.recovery_engine.route_change_metadata(before_url, snapshot.url)
        metadata["dismissed_blockers"] = dismissed
        metadata["session_expired"] = self.recovery_engine.detect_session_expired(self.session_manager, session_id, snapshot)
        await self.session_manager.save_session_state(session_id, self.spm_extractor)
        return BrowserState(session_id=session_id, page=snapshot, metadata=metadata)

    async def type(self, session_id: str, selector: str, text: str) -> BrowserState:
        page = self.session_manager.require_page(session_id)
        await self.recovery_engine.dismiss_blockers(page)
        await self.recovery_engine.retry_type(page, selector, text)
        await self.recovery_engine.stabilize_page(page)
        snapshot = await self.spm_extractor.snapshot_page(page)
        await self.session_manager.save_session_state(session_id, self.spm_extractor)
        return BrowserState(
            session_id=session_id,
            page=snapshot,
            metadata={"typed_selector": selector, "session_expired": self.recovery_engine.detect_session_expired(self.session_manager, session_id, snapshot)},
        )

    async def extract(self, session_id: str, selector: str, attribute: str | None = None) -> ExtractionResult:
        page = self.session_manager.require_page(session_id)
        await self.recovery_engine.dismiss_blockers(page)
        locator = page.locator(selector)
        count = await locator.count()
        matches: list[ExtractedElement] = []
        for index in range(count):
            item = locator.nth(index)
            matches.append(
                ExtractedElement(
                    selector=selector,
                    text=await item.text_content(),
                    attribute=attribute,
                    attribute_value=await item.get_attribute(attribute) if attribute else None,
                    visible=await item.is_visible(),
                )
            )
        payload = ExtractionResult(session_id=session_id, matches=matches, page=await self.spm_extractor.snapshot_page(page))
        await self.session_manager.save_session_state(session_id, self.spm_extractor)
        return payload

    async def screenshot(self, session_id: str) -> ScreenshotResult:
        page = self.session_manager.require_page(session_id)
        await self.recovery_engine.dismiss_blockers(page)
        await self.recovery_engine.stabilize_page(page)
        image_bytes = await page.screenshot(full_page=True, type="png")
        payload = ScreenshotResult(
            session_id=session_id,
            image_base64=base64.b64encode(image_bytes).decode("ascii"),
            page=await self.spm_extractor.snapshot_page(page),
        )
        await self.session_manager.save_session_state(session_id, self.spm_extractor)
        return payload

    async def get_layout(self, session_id: str):
        page = self.session_manager.require_page(session_id)
        await self.recovery_engine.dismiss_blockers(page)
        await self.recovery_engine.stabilize_page(page)
        await self.session_manager.save_session_state(session_id, self.spm_extractor)
        return await self.spm_extractor.snapshot_page(page)

    async def find_element(self, session_id: str, element_type: str, text: str):
        spm = await self.get_layout(session_id)
        return self.spm_extractor.find_element(spm, element_type, text)

    async def inspect(self, session_id: str, selector: str):
        page = self.session_manager.require_page(session_id)
        return await self.spm_extractor.inspect(page, selector)

    async def dismiss_popups(self, session_id: str) -> BrowserState:
        page = self.session_manager.require_page(session_id)
        dismissed = await self.recovery_engine.dismiss_blockers(page)
        snapshot = await self.spm_extractor.snapshot_page(page)
        await self.session_manager.save_session_state(session_id, self.spm_extractor)
        return BrowserState(session_id=session_id, page=snapshot, metadata={"dismissed_blockers": dismissed})

    async def scroll_extract(
        self,
        session_id: str,
        selector: str,
        attribute: str | None = None,
        max_scrolls: int = 8,
        scroll_step: int = 700,
    ) -> ScrollExtractResult:
        page = self.session_manager.require_page(session_id)
        await self.recovery_engine.dismiss_blockers(page)
        await self.recovery_engine.bounded_scroll(page, max_scrolls=max_scrolls, scroll_step=scroll_step)
        extracted = await self.extract(session_id=session_id, selector=selector, attribute=attribute)
        return ScrollExtractResult(
            session_id=session_id,
            matches=extracted.matches,
            page=extracted.page,
            metadata={"max_scrolls": max_scrolls, "scroll_step": scroll_step},
        )
