import asyncio

from synapse.models.browser import DownloadArtifact, PageButton, PageForm, PageFormField, PageInput, PageLink, PageSection, PageTable, StructuredPageModel
from synapse.runtime.browser import BrowserRuntime
from synapse.runtime.browser.download_manager import DownloadManager
from synapse.runtime.browser.interaction_engine import InteractionEngine
from synapse.runtime.browser.recovery_engine import RecoveryEngine
from synapse.runtime.browser.session_manager import SessionManager
from synapse.runtime.browser.spm_extractor import SPMExtractor
from synapse.runtime.browser.upload_manager import UploadManager
from synapse.runtime.state_store import InMemoryRuntimeStateStore


class _FakeLocator:
    def __init__(self, visible: bool = False, click_failures: int = 0) -> None:
        self._visible = visible
        self._click_failures = click_failures
        self.clicked = 0
        self.uploaded: list[str] = []

    @property
    def first(self) -> "_FakeLocator":
        return self

    async def count(self) -> int:
        return 1 if self._visible else 0

    async def is_visible(self) -> bool:
        return self._visible

    async def click(self, timeout: int | None = None) -> None:
        if self._click_failures > 0:
            self._click_failures -= 1
            raise RuntimeError("stale element")
        self.clicked += 1

    async def fill(self, text: str, timeout: int | None = None) -> None:
        return None

    async def set_input_files(self, file_paths: list[str]) -> None:
        self.uploaded = file_paths

    async def text_content(self):
        return "hello"

    async def get_attribute(self, name: str):
        return None

    async def bounding_box(self):
        return None

    async def evaluate(self, script: str):
        if "tagName" in script:
            return "button"
        return {}


class _FakePage:
    def __init__(self) -> None:
        self.url = "https://example.com"
        self._locators: dict[str, _FakeLocator] = {}

    def set_locator(self, selector: str, locator: _FakeLocator) -> None:
        self._locators[selector] = locator

    def locator(self, selector: str) -> _FakeLocator:
        return self._locators.get(selector, _FakeLocator(visible=False))

    async def title(self) -> str:
        return "Example"

    async def goto(self, url: str) -> None:
        self.url = url

    async def wait_for_load_state(self, state: str, timeout: int | None = None) -> None:
        return None

    async def screenshot(self, full_page: bool = True, type: str = "png") -> bytes:
        return b"image"

    async def evaluate(self, script: str, arg=None):
        return {"local_storage": {}, "session_storage": {}}


class _FakeContext:
    async def cookies(self):
        return [{"name": "sessionid", "value": "abc"}]

    async def add_cookies(self, cookies):
        return None


def _runtime_with_fake_page(page: _FakePage) -> BrowserRuntime:
    runtime = BrowserRuntime(state_store=InMemoryRuntimeStateStore())
    runtime.session_manager._pages["s1"] = page
    runtime.session_manager._contexts["s1"] = _FakeContext()
    runtime.session_manager._session_agents["s1"] = "agent-1"

    async def fake_snapshot(_: _FakePage) -> StructuredPageModel:
        return runtime.spm_extractor.attach_compressed_views(
            StructuredPageModel(
            title="Example",
            url=page.url,
            sections=[PageSection(heading="Overview", text="Testing")],
            buttons=[PageButton(text="Continue", selector_hint="button.submit")],
            )
        )

    async def fake_stabilize(_: _FakePage) -> None:
        return None

    runtime.spm_extractor.snapshot_page = fake_snapshot  # type: ignore[method-assign]
    runtime.recovery_engine.stabilize_page = fake_stabilize  # type: ignore[method-assign]
    return runtime


def test_session_manager_save_restore() -> None:
    async def scenario() -> None:
        page = _FakePage()
        manager = SessionManager(settings=type("Settings", (), {"browser_headless": True, "browser_channel": None})(), state_store=InMemoryRuntimeStateStore())
        manager._pages["s1"] = page
        manager._contexts["s1"] = _FakeContext()
        manager._session_agents["s1"] = "agent-1"

        class _Extractor:
            async def snapshot_page(self, page):
                return StructuredPageModel(title="Example", url=page.url)

        await manager.save_session_state("s1", _Extractor())
        restored = await manager.restore_session_state("s1", _Extractor())
        assert restored is not None
        assert restored.current_url == "https://example.com"

    asyncio.run(scenario())


def test_spm_extractor_find_element() -> None:
    extractor = SPMExtractor()
    page = extractor.attach_compressed_views(StructuredPageModel(
        title="Example",
        url="https://example.com",
        sections=[PageSection(heading="Papers", text="paper listing", selector_hint="section.paper")],
        buttons=[PageButton(text="Continue", selector_hint="button.submit")],
    ))
    matches = extractor.find_element(page, "sections", "paper")
    assert matches[0].selector_hint == "section.paper"


def test_spm_extractor_builds_compact_spm() -> None:
    extractor = SPMExtractor()
    page = extractor.attach_compressed_views(
        StructuredPageModel(
            title="Catalog",
            url="https://example.com/catalog",
            sections=[
                PageSection(heading="Featured Papers", text="A long listing of research papers."),
                PageSection(heading="Top Authors", text="Profiles and links."),
            ],
            buttons=[
                PageButton(text="Load more", selector_hint="button.load"),
                PageButton(text="Load more", selector_hint="button.load.secondary"),
            ],
            inputs=[PageInput(name="query", input_type="search", selector_hint="input.search")],
            forms=[
                PageForm(
                    name="search",
                    selector_hint="form.search",
                    method="get",
                    action="/search",
                    fields=[PageFormField(name="query", field_type="search", selector_hint="input.search")],
                )
            ],
            tables=[
                PageTable(
                    selector_hint="table.results",
                    headers=["Title", "Author"],
                    rows=[["Paper A", "Alice"], ["Paper B", "Bob"], ["Paper C", "Carol"]],
                )
            ],
            links=[
                PageLink(text="Paper A", href="https://example.com/paper-a", selector_hint="a.paper-a"),
                PageLink(text="Paper B", href="https://example.com/paper-b", selector_hint="a.paper-b"),
            ],
        )
    )

    assert page.full_spm["title"] == "Catalog"
    assert page.compact_spm is not None
    assert page.compact_spm.page_summary
    assert any(region.region_type == "content" for region in page.compact_spm.semantic_regions)
    assert any(group.element_type == "button" for group in page.compact_spm.grouped_elements)
    assert any(item.action == "click" for item in page.compact_spm.actionable_elements)


def test_recovery_engine_helpers() -> None:
    assert RecoveryEngine.route_change_metadata("https://example.com/app#1", "https://example.com/profile#2")["route_changed"] is True
    assert RecoveryEngine.classify_browser_error("click", RuntimeError("stale element")) == "browser.click.stale_element: stale element"
    assert RecoveryEngine.fallback_selector("text=Continue") is not None


def test_download_manager_capture() -> None:
    async def scenario() -> None:
        manager = DownloadManager()

        class _Download:
            suggested_filename = "file.pdf"
            url = "https://example.com/file.pdf"

            async def save_as(self, path: str) -> None:
                with open(path, "wb") as handle:
                    handle.write(b"pdf")

        class _ExpectDownload:
            def __init__(self) -> None:
                self.value = _Download()

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

        class _Page(_FakePage):
            def expect_download(self, timeout: int):
                return _ExpectDownload()

        page = _Page()
        page.set_locator("a.download", _FakeLocator(visible=True))
        artifact = await manager.capture_download(page, "a.download", 5000, RecoveryEngine())
        assert isinstance(artifact, DownloadArtifact)
        assert artifact.suggested_filename == "file.pdf"

    asyncio.run(scenario())


def test_upload_manager_validates_and_uploads() -> None:
    async def scenario() -> None:
        runtime = _runtime_with_fake_page(_FakePage())
        runtime.session_manager.require_page("s1").set_locator("input[type='file']", _FakeLocator(visible=True))
        result = await UploadManager().upload(
            session_id="s1",
            selector="input[type='file']",
            file_paths=["/tmp/a.txt"],
            session_manager=runtime.session_manager,
            extractor=runtime.spm_extractor,
            recovery=runtime.recovery_engine,
        )
        assert result.uploaded_files == ["/tmp/a.txt"]

    asyncio.run(scenario())


def test_interaction_engine_click_retries() -> None:
    async def scenario() -> None:
        page = _FakePage()
        page.set_locator("button.submit", _FakeLocator(visible=True, click_failures=1))
        runtime = _runtime_with_fake_page(page)
        state = await runtime.interaction_engine.click("s1", "button.submit")
        assert state.session_id == "s1"
        assert page.locator("button.submit").clicked == 1

    asyncio.run(scenario())
