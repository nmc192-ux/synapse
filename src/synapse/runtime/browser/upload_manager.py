from __future__ import annotations

from pathlib import Path
from typing import Any

from synapse.models.browser import UploadResult


class UploadManager:
    async def upload(
        self,
        *,
        session_id: str,
        selector: str,
        file_paths: list[str],
        session_manager,
        extractor,
        recovery,
    ) -> UploadResult:
        validated_files = self._validate_files(file_paths)
        page = session_manager.require_page(session_id)
        await recovery.dismiss_blockers(page)
        await page.locator(selector).first.set_input_files(validated_files)
        await recovery.stabilize_page(page)
        snapshot = await extractor.snapshot_page(page)
        await session_manager.save_session_state(session_id, extractor)
        return UploadResult(
            session_id=session_id,
            uploaded_files=validated_files,
            page=snapshot,
            metadata={"selector": selector, "uploaded_count": len(validated_files)},
        )

    @staticmethod
    def _validate_files(file_paths: list[str]) -> list[str]:
        if not file_paths:
            raise ValueError("At least one upload file path is required.")
        return [str(Path(path)) for path in file_paths]
