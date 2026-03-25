from __future__ import annotations

import inspect
from pathlib import Path
import tempfile
from typing import Any

from synapse.models.browser import DownloadArtifact, DownloadResult


class DownloadManager:
    async def capture_download(self, page: Any, selector: str, timeout_ms: int, recovery) -> DownloadArtifact:
        bounded_timeout = max(1000, min(timeout_ms, 120_000))
        async with page.expect_download(timeout=bounded_timeout) as download_info:
            await recovery.retry_click(page, selector)
        download = download_info.value
        if inspect.isawaitable(download):
            download = await download
        target_path = Path(tempfile.gettempdir()) / download.suggested_filename
        await download.save_as(str(target_path))
        size = target_path.stat().st_size if target_path.exists() else None
        return DownloadArtifact(
            suggested_filename=download.suggested_filename,
            path=str(target_path),
            url=download.url,
            size_bytes=size,
            status="completed",
        )

    async def download(
        self,
        session_id: str,
        trigger_selector: str | None,
        timeout_ms: int,
        *,
        session_manager,
        extractor,
        recovery,
    ) -> DownloadResult:
        page = session_manager.require_page(session_id)
        await recovery.dismiss_blockers(page)
        selector = trigger_selector or "a[download], a[href*='download'], a[href$='.pdf']"
        artifact = await self.capture_download(page, selector=selector, timeout_ms=timeout_ms, recovery=recovery)
        session_manager.append_download(session_id, artifact.model_dump(mode="json"))
        snapshot = await extractor.snapshot_page(page)
        await session_manager.save_session_state(session_id, extractor)
        return DownloadResult(
            session_id=session_id,
            artifact=artifact,
            page=snapshot,
            metadata={"trigger_selector": selector},
        )
