import asyncio

from synapse.models.browser import DownloadArtifact, StructuredPageModel
from synapse.runtime.browser import BrowserRuntime
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
        return StructuredPageModel(title="Example", url=page.url)

    async def fake_stabilize(_: _FakePage) -> None:
        return None

    runtime.spm_extractor.snapshot_page = fake_snapshot  # type: ignore[method-assign]
    runtime.recovery_engine.stabilize_page = fake_stabilize  # type: ignore[method-assign]
    return runtime


def test_popup_dismissal() -> None:
    async def scenario() -> None:
        page = _FakePage()
        page.set_locator("button:has-text('Accept')", _FakeLocator(visible=True))
        runtime = _runtime_with_fake_page(page)
        dismissed = await runtime._dismiss_blockers(page)
        assert dismissed

    asyncio.run(scenario())


def test_session_restore() -> None:
    async def scenario() -> None:
        page = _FakePage()
        runtime = _runtime_with_fake_page(page)
        await runtime.save_session_state("s1")
        restored = await runtime.restore_session_state("s1")
        assert restored is not None
        assert restored.current_url == "https://example.com"

    asyncio.run(scenario())


def test_download_flow() -> None:
    async def scenario() -> None:
        page = _FakePage()
        runtime = _runtime_with_fake_page(page)

        async def fake_capture(_: _FakePage, selector: str, timeout_ms: int, recovery=None) -> DownloadArtifact:
            return DownloadArtifact(suggested_filename="file.pdf", path="/tmp/file.pdf", status="completed")

        runtime.download_manager.capture_download = fake_capture  # type: ignore[method-assign]
        result = await runtime.download("s1", trigger_selector="a.download")
        assert result.artifact.suggested_filename == "file.pdf"

    asyncio.run(scenario())


def test_upload_flow() -> None:
    async def scenario() -> None:
        page = _FakePage()
        page.set_locator("input[type='file']", _FakeLocator(visible=True))
        runtime = _runtime_with_fake_page(page)
        result = await runtime.upload("s1", "input[type='file']", ["/tmp/a.txt"])
        assert result.uploaded_files == ["/tmp/a.txt"]

    asyncio.run(scenario())


def test_spa_route_handling() -> None:
    metadata = BrowserRuntime._route_change_metadata("https://example.com/app#1", "https://example.com/profile#2")
    assert metadata["route_changed"] is True
    assert metadata["spa_route_change"] is True


def test_stale_element_retry() -> None:
    async def scenario() -> None:
        page = _FakePage()
        page.set_locator("button.submit", _FakeLocator(visible=True, click_failures=1))
        runtime = _runtime_with_fake_page(page)
        await runtime._retry_click(page, "button.submit", retries=2)
        assert page.locator("button.submit").clicked == 1

    asyncio.run(scenario())
