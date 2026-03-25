from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import base64

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from synapse.config import settings
from synapse.models.browser import (
    BrowserState,
    ExtractedElement,
    ExtractionResult,
    PageData,
    PageElement,
    ScreenshotResult,
)
from synapse.runtime.session import BrowserSession


class BrowserRuntime:
    """Manages Playwright browser lifecycle and structured page interactions."""

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._contexts: dict[str, BrowserContext] = {}
        self._pages: dict[str, Page] = {}

    async def start(self) -> None:
        if self._browser is not None:
            return

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=settings.browser_headless,
            channel=settings.browser_channel,
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

    async def create_session(self, session_id: str) -> BrowserSession:
        if self._browser is None:
            raise RuntimeError("Browser runtime is not started.")

        context = await self._browser.new_context()
        page = await context.new_page()
        self._contexts[session_id] = context
        self._pages[session_id] = page
        return BrowserSession(session_id=session_id, page=await self._snapshot_page(page))

    async def open(self, session_id: str, url: str) -> BrowserState:
        page = self._require_page(session_id)
        await page.goto(url)
        await page.wait_for_load_state("domcontentloaded")
        return BrowserState(session_id=session_id, page=await self._snapshot_page(page))

    async def click(self, session_id: str, selector: str) -> BrowserState:
        page = self._require_page(session_id)
        await page.locator(selector).first.click()
        await page.wait_for_load_state("domcontentloaded")
        return BrowserState(session_id=session_id, page=await self._snapshot_page(page))

    async def type(self, session_id: str, selector: str, text: str) -> BrowserState:
        page = self._require_page(session_id)
        locator = page.locator(selector).first
        await locator.fill(text)
        return BrowserState(
            session_id=session_id,
            page=await self._snapshot_page(page),
            metadata={"typed_selector": selector},
        )

    async def extract(
        self,
        session_id: str,
        selector: str,
        attribute: str | None = None,
    ) -> ExtractionResult:
        page = self._require_page(session_id)
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

        return ExtractionResult(
            session_id=session_id,
            matches=matches,
            page=await self._snapshot_page(page),
        )

    async def screenshot(self, session_id: str) -> ScreenshotResult:
        page = self._require_page(session_id)
        image_bytes = await page.screenshot(full_page=True, type="png")
        return ScreenshotResult(
            session_id=session_id,
            image_base64=base64.b64encode(image_bytes).decode("ascii"),
            page=await self._snapshot_page(page),
        )

    async def navigate(self, session_id: str, url: str) -> BrowserSession:
        state = await self.open(session_id, url)
        return BrowserSession(session_id=session_id, current_url=state.page.url, page=state.page)

    async def close_session(self, session_id: str) -> None:
        page = self._pages.pop(session_id, None)
        if page is not None:
            await page.close()

        context = self._contexts.pop(session_id, None)
        if context is not None:
            await context.close()

    def _require_page(self, session_id: str) -> Page:
        page = self._pages.get(session_id)
        if page is None:
            raise KeyError(f"Unknown session: {session_id}")
        return page

    async def _snapshot_page(self, page: Page) -> PageData:
        snapshot = await page.evaluate(
            """
            () => {
              const text = document.body?.innerText ?? "";
              const excerpt = text.replace(/\\s+/g, " ").trim().slice(0, 2000);
              const interactiveSelector = [
                "a[href]",
                "button",
                "input",
                "textarea",
                "select",
                "[role]"
              ].join(",");

              const elements = Array.from(document.querySelectorAll(interactiveSelector))
                .slice(0, 50)
                .map((element) => {
                  const tag = element.tagName.toLowerCase();
                  const role = element.getAttribute("role");
                  const textContent = (element.innerText || element.textContent || "")
                    .replace(/\\s+/g, " ")
                    .trim()
                    .slice(0, 200);
                  const selectorHint =
                    element.id
                      ? `#${element.id}`
                      : element.getAttribute("data-testid")
                        ? `[data-testid="${element.getAttribute("data-testid")}"]`
                        : element.getAttribute("name")
                          ? `${tag}[name="${element.getAttribute("name")}"]`
                          : tag;
                  const style = window.getComputedStyle(element);
                  return {
                    tag,
                    role,
                    text: textContent || null,
                    selector_hint: selectorHint,
                    href: element.getAttribute("href"),
                    input_type: element.getAttribute("type"),
                    visible: style.display !== "none" && style.visibility !== "hidden"
                  };
                });

              const links = Array.from(document.querySelectorAll("a[href]"))
                .slice(0, 25)
                .map((link) => link.href);

              return {
                url: window.location.href,
                title: document.title || "",
                text_excerpt: excerpt,
                links,
                elements
              };
            }
            """
        )
        return PageData(
            url=snapshot["url"],
            title=snapshot["title"],
            text_excerpt=snapshot["text_excerpt"],
            links=list(snapshot["links"]),
            elements=[PageElement(**element) for element in snapshot["elements"]],
        )


@asynccontextmanager
async def browser_runtime_lifespan(runtime: BrowserRuntime) -> AsyncIterator[None]:
    await runtime.start()
    try:
        yield
    finally:
        await runtime.stop()
