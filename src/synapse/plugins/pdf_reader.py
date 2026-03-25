import asyncio
from pathlib import Path

from pypdf import PdfReader

from synapse.runtime.tools import ToolRegistry


def register(registry: ToolRegistry) -> None:
    registry.register_plugin(
        name="pdf_reader",
        module=__name__,
        capabilities=["pdf_text_extraction", "pdf_metadata", "pdf_page_read"],
        endpoint="pdf.read",
    )

    async def pdf_read(arguments: dict[str, object]) -> dict[str, object]:
        path = str(arguments.get("path", "")).strip()
        page_limit = int(arguments.get("page_limit", 10))
        if not path:
            raise ValueError("pdf.read requires a 'path'.")

        return await asyncio.to_thread(_read_pdf, path, page_limit)

    registry.register(
        "pdf.read",
        pdf_read,
        description="Read PDF metadata and extract text by page.",
        plugin_name="pdf_reader",
    )


def _read_pdf(path: str, page_limit: int) -> dict[str, object]:
    reader = PdfReader(path)
    pages = []
    for index, page in enumerate(reader.pages[:page_limit]):
        pages.append(
            {
                "page_number": index + 1,
                "text": (page.extract_text() or "").strip(),
            }
        )

    file_path = Path(path)
    return {
        "path": str(file_path),
        "page_count": len(reader.pages),
        "metadata": {str(key): str(value) for key, value in (reader.metadata or {}).items()},
        "pages": pages,
    }
