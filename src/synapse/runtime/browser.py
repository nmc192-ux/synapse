from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import base64
from datetime import datetime, timezone
import logging

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from synapse.config import settings
from synapse.models.browser import (
    BrowserState,
    ExtractedElement,
    ExtractionResult,
    PageButton,
    PageElementMatch,
    PageForm,
    PageFormField,
    PageInput,
    PageInspection,
    PageLink,
    PageSection,
    PageTable,
    ScreenshotResult,
    StructuredPageModel,
)
from synapse.models.runtime_state import BrowserSessionState
from synapse.runtime.session import BrowserSession
from synapse.runtime.state_store import RuntimeStateStore


logger = logging.getLogger(__name__)


class BrowserRuntime:
    """Manages Playwright browser lifecycle and structured page interactions."""

    def __init__(self, state_store: RuntimeStateStore | None = None) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._contexts: dict[str, BrowserContext] = {}
        self._pages: dict[str, Page] = {}
        self._session_agents: dict[str, str | None] = {}
        self._state_store = state_store

    def set_state_store(self, state_store: RuntimeStateStore) -> None:
        self._state_store = state_store

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

    async def create_session(self, session_id: str, agent_id: str | None = None) -> BrowserSession:
        if self._browser is None:
            raise RuntimeError("Browser runtime is not started.")

        context = await self._browser.new_context()
        page = await context.new_page()
        self._contexts[session_id] = context
        self._pages[session_id] = page
        self._session_agents[session_id] = agent_id
        await self.save_session_state(session_id)
        return BrowserSession(session_id=session_id, page=await self._snapshot_page(page))

    async def open(self, session_id: str, url: str) -> BrowserState:
        page = self._require_page(session_id)
        await page.goto(url)
        await page.wait_for_load_state("domcontentloaded")
        await self._stabilize_page(page)
        await self.save_session_state(session_id)
        return BrowserState(session_id=session_id, page=await self._snapshot_page(page))

    async def click(self, session_id: str, selector: str) -> BrowserState:
        page = self._require_page(session_id)
        await page.locator(selector).first.click()
        await page.wait_for_load_state("domcontentloaded")
        await self._stabilize_page(page)
        await self.save_session_state(session_id)
        return BrowserState(session_id=session_id, page=await self._snapshot_page(page))

    async def type(self, session_id: str, selector: str, text: str) -> BrowserState:
        page = self._require_page(session_id)
        locator = page.locator(selector).first
        await locator.fill(text)
        await self._stabilize_page(page)
        await self.save_session_state(session_id)
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

        payload = ExtractionResult(
            session_id=session_id,
            matches=matches,
            page=await self._snapshot_page(page),
        )
        await self.save_session_state(session_id)
        return payload

    async def screenshot(self, session_id: str) -> ScreenshotResult:
        page = self._require_page(session_id)
        await self._stabilize_page(page)
        image_bytes = await page.screenshot(full_page=True, type="png")
        payload = ScreenshotResult(
            session_id=session_id,
            image_base64=base64.b64encode(image_bytes).decode("ascii"),
            page=await self._snapshot_page(page),
        )
        await self.save_session_state(session_id)
        return payload

    async def get_layout(self, session_id: str) -> StructuredPageModel:
        page = self._require_page(session_id)
        await self._stabilize_page(page)
        await self.save_session_state(session_id)
        return await self._snapshot_page(page)

    async def find_element(self, session_id: str, element_type: str, text: str) -> list[PageElementMatch]:
        spm = await self.get_layout(session_id)
        normalized_type = element_type.lower()
        normalized_text = text.lower()
        matches: list[PageElementMatch] = []

        if normalized_type == "sections":
            for section in spm.sections:
                haystack = " ".join(filter(None, [section.heading, section.text])).lower()
                if normalized_text in haystack:
                    matches.append(
                        PageElementMatch(
                            element_type="section",
                            text=section.heading or section.text,
                            selector_hint=section.selector_hint,
                        )
                    )
        elif normalized_type == "buttons":
            for button in spm.buttons:
                if normalized_text in button.text.lower():
                    matches.append(
                        PageElementMatch(
                            element_type="button",
                            text=button.text,
                            selector_hint=button.selector_hint,
                            metadata={"role": button.role, "disabled": button.disabled},
                        )
                    )
        elif normalized_type == "inputs":
            for item in spm.inputs:
                haystack = " ".join(filter(None, [item.name, item.placeholder, item.value])).lower()
                if normalized_text in haystack:
                    matches.append(
                        PageElementMatch(
                            element_type="input",
                            text=item.name or item.placeholder or item.value or "",
                            selector_hint=item.selector_hint,
                            metadata={"input_type": item.input_type},
                        )
                    )
        elif normalized_type == "forms":
            for form in spm.forms:
                haystack = " ".join(filter(None, [form.name, form.selector_hint])).lower()
                if normalized_text in haystack:
                    matches.append(
                        PageElementMatch(
                            element_type="form",
                            text=form.name or "",
                            selector_hint=form.selector_hint,
                            metadata={"method": form.method, "action": form.action},
                        )
                    )
        elif normalized_type == "tables":
            for table in spm.tables:
                haystack = " ".join(table.headers + [cell for row in table.rows for cell in row]).lower()
                if normalized_text in haystack:
                    matches.append(
                        PageElementMatch(
                            element_type="table",
                            text=" | ".join(table.headers),
                            selector_hint=table.selector_hint,
                            metadata={"row_count": len(table.rows)},
                        )
                    )
        elif normalized_type == "links":
            for link in spm.links:
                haystack = " ".join(filter(None, [link.text, link.href])).lower()
                if normalized_text in haystack:
                    matches.append(
                        PageElementMatch(
                            element_type="link",
                            text=link.text or link.href or "",
                            selector_hint=link.selector_hint,
                            metadata={"href": link.href},
                        )
                    )
        else:
            raise ValueError(f"Unsupported structured element type: {element_type}")

        return matches

    async def inspect(self, session_id: str, selector: str) -> PageInspection:
        page = self._require_page(session_id)
        locator = page.locator(selector).first
        box = await locator.bounding_box()
        attributes = await locator.evaluate(
            """
            (element) => Object.fromEntries(
              Array.from(element.attributes).map((attribute) => [attribute.name, attribute.value])
            )
            """
        )
        return PageInspection(
            selector=selector,
            text=await locator.text_content(),
            html_tag=await locator.evaluate("(element) => element.tagName.toLowerCase()"),
            attributes=attributes,
            is_visible=await locator.is_visible(),
            bounding_box=box,
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
        self._session_agents.pop(session_id, None)
        if self._state_store is not None:
            await self._state_store.delete_session(session_id)

    async def save_session_state(self, session_id: str) -> BrowserSessionState | None:
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

        state = BrowserSessionState(
            session_id=session_id,
            agent_id=self._session_agents.get(session_id),
            current_url=page.url or None,
            cookies=[dict(cookie) for cookie in cookies],
            last_active_at=datetime.now(timezone.utc),
            page_title=await page.title(),
            tabs=[{"index": 0, "url": page.url or None, "title": await page.title()}],
        )
        await self._state_store.store_session(session_id, state.model_dump(mode="json"))
        return state

    async def restore_session_state(self, session_id: str) -> BrowserSession | None:
        if self._state_store is None:
            return None
        payload = await self._state_store.get_session(session_id)
        if payload is None:
            return None
        state = BrowserSessionState.model_validate(payload)
        if session_id not in self._pages:
            await self.create_session(session_id, agent_id=state.agent_id)
        page = self._require_page(session_id)
        context = self._contexts[session_id]

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

        snapshot = await self._snapshot_page(page)
        await self.save_session_state(session_id)
        return BrowserSession(session_id=session_id, current_url=snapshot.url, page=snapshot)

    async def list_sessions(self, agent_id: str | None = None) -> list[BrowserSessionState]:
        if self._state_store is None:
            rows = []
            for session_id, page in self._pages.items():
                rows.append(
                    BrowserSessionState(
                        session_id=session_id,
                        agent_id=self._session_agents.get(session_id),
                        current_url=page.url or None,
                        last_active_at=datetime.now(timezone.utc),
                        page_title=await page.title(),
                        tabs=[{"index": 0, "url": page.url or None, "title": await page.title()}],
                    )
                )
            if agent_id is None:
                return rows
            return [row for row in rows if row.agent_id == agent_id]
        records = await self._state_store.list_sessions(agent_id=agent_id)
        return [BrowserSessionState.model_validate(record) for record in records]

    def _require_page(self, session_id: str) -> Page:
        page = self._pages.get(session_id)
        if page is None:
            raise KeyError(f"Unknown session: {session_id}")
        return page

    def current_url(self, session_id: str) -> str:
        return self._require_page(session_id).url

    async def _snapshot_page(self, page: Page) -> StructuredPageModel:
        snapshot = await page.evaluate(
            """
            () => {
              const LIMITS = {
                sections: 24,
                buttons: 32,
                inputs: 32,
                forms: 12,
                tables: 12,
                links: 40,
                tableHeaders: 20,
                tableRows: 12,
                formFields: 20,
              };

              const selectorHint = (element) => {
                const tag = element.tagName.toLowerCase();
                if (element.id) return `#${element.id}`;
                if (element.getAttribute("data-testid")) {
                  return `[data-testid="${element.getAttribute("data-testid")}"]`;
                }
                if (element.getAttribute("name")) {
                  return `${tag}[name="${element.getAttribute("name")}"]`;
                }
                if (element.classList?.length) {
                  return `${tag}.${Array.from(element.classList).slice(0, 2).join(".")}`;
                }
                return tag;
              };

              const compactText = (value) => (value || "").replace(/\\s+/g, " ").trim();
              const seen = {
                sections: new Set(),
                buttons: new Set(),
                inputs: new Set(),
                forms: new Set(),
                tables: new Set(),
                links: new Set(),
              };

              const sections = [];
              const buttons = [];
              const inputs = [];
              const forms = [];
              const tables = [];
              const links = [];

              const addUnique = (bucket, key, collection, entry, limit) => {
                if (!key || seen[bucket].has(key) || collection.length >= limit) {
                  return;
                }
                seen[bucket].add(key);
                collection.push(entry);
              };

              const roots = [];
              const collectRoots = (root, prefix = "") => {
                roots.push({ root, prefix });

                for (const element of Array.from(root.querySelectorAll("*"))) {
                  if (element.shadowRoot) {
                    const shadowPrefix = prefix ? `${prefix} >> shadow(${selectorHint(element)})` : `shadow(${selectorHint(element)})`;
                    collectRoots(element.shadowRoot, shadowPrefix);
                  }

                  if (element.tagName?.toLowerCase() === "iframe") {
                    try {
                      const frameDoc = element.contentDocument;
                      if (frameDoc?.documentElement) {
                        const framePrefix = prefix ? `${prefix} >> iframe(${selectorHint(element)})` : `iframe(${selectorHint(element)})`;
                        collectRoots(frameDoc, framePrefix);
                      }
                    } catch (_error) {
                      // Cross-origin frames cannot be traversed from the page context.
                    }
                  }
                }
              };

              const scopedSelector = (prefix, element) => {
                const base = selectorHint(element);
                return prefix ? `${prefix} >> ${base}` : base;
              };

              collectRoots(document);

              for (const { root, prefix } of roots) {
                for (const element of Array.from(root.querySelectorAll("main section, section, article")).slice(0, LIMITS.sections)) {
                  const key = scopedSelector(prefix, element);
                  addUnique(
                    "sections",
                    key,
                    sections,
                    {
                      heading: compactText(
                        element.querySelector("h1, h2, h3, h4, h5, h6")?.textContent || ""
                      ) || null,
                      text: compactText(element.textContent || "").slice(0, 400),
                      selector_hint: key
                    },
                    LIMITS.sections
                  );
                }

                for (const element of Array.from(root.querySelectorAll("button, [role='button'], input[type='submit'], input[type='button']")).slice(0, LIMITS.buttons)) {
                  const key = scopedSelector(prefix, element);
                  addUnique(
                    "buttons",
                    key,
                    buttons,
                    {
                      text: compactText(element.innerText || element.value || element.textContent || ""),
                      selector_hint: key,
                      role: element.getAttribute("role"),
                      disabled: Boolean(element.disabled)
                    },
                    LIMITS.buttons
                  );
                }

                for (const element of Array.from(root.querySelectorAll("input, textarea, select")).slice(0, LIMITS.inputs)) {
                  const key = scopedSelector(prefix, element);
                  addUnique(
                    "inputs",
                    key,
                    inputs,
                    {
                      name: element.getAttribute("name"),
                      input_type: element.getAttribute("type") || element.tagName.toLowerCase(),
                      placeholder: element.getAttribute("placeholder"),
                      selector_hint: key,
                      value: element.value || null
                    },
                    LIMITS.inputs
                  );
                }

                for (const form of Array.from(root.querySelectorAll("form")).slice(0, LIMITS.forms)) {
                  const key = scopedSelector(prefix, form);
                  addUnique(
                    "forms",
                    key,
                    forms,
                    {
                      name: form.getAttribute("name") || form.getAttribute("id"),
                      selector_hint: key,
                      method: form.getAttribute("method"),
                      action: form.getAttribute("action"),
                      fields: Array.from(form.querySelectorAll("input, textarea, select"))
                        .slice(0, LIMITS.formFields)
                        .map((field) => ({
                          name: field.getAttribute("name"),
                          field_type: field.getAttribute("type") || field.tagName.toLowerCase(),
                          selector_hint: scopedSelector(prefix, field)
                        }))
                    },
                    LIMITS.forms
                  );
                }

                for (const table of Array.from(root.querySelectorAll("table")).slice(0, LIMITS.tables)) {
                  const key = scopedSelector(prefix, table);
                  addUnique(
                    "tables",
                    key,
                    tables,
                    {
                      selector_hint: key,
                      headers: Array.from(table.querySelectorAll("thead th, tr th"))
                        .slice(0, LIMITS.tableHeaders)
                        .map((cell) => compactText(cell.textContent || "")),
                      rows: Array.from(table.querySelectorAll("tbody tr, tr"))
                        .slice(0, LIMITS.tableRows)
                        .map((row) => Array.from(row.querySelectorAll("td"))
                          .slice(0, LIMITS.tableHeaders)
                          .map((cell) => compactText(cell.textContent || "")))
                        .filter((row) => row.length > 0)
                    },
                    LIMITS.tables
                  );
                }

                for (const link of Array.from(root.querySelectorAll("a[href]")).slice(0, LIMITS.links)) {
                  const key = `${scopedSelector(prefix, link)}:${link.href}`;
                  addUnique(
                    "links",
                    key,
                    links,
                    {
                      text: compactText(link.textContent || ""),
                      href: link.href,
                      selector_hint: scopedSelector(prefix, link)
                    },
                    LIMITS.links
                  );
                }
              }

              return {
                url: window.location.href,
                title: document.title || "",
                sections,
                buttons,
                inputs,
                forms,
                tables,
                links,
              };
            }
            """
        )
        return StructuredPageModel(
            url=snapshot["url"],
            title=snapshot["title"],
            sections=[PageSection(**section) for section in snapshot["sections"]],
            buttons=[PageButton(**button) for button in snapshot["buttons"]],
            inputs=[PageInput(**item) for item in snapshot["inputs"]],
            forms=[
                PageForm(
                    name=form["name"],
                    selector_hint=form["selector_hint"],
                    method=form["method"],
                    action=form["action"],
                    fields=[PageFormField(**field) for field in form["fields"]],
                )
                for form in snapshot["forms"]
            ],
            tables=[PageTable(**table) for table in snapshot["tables"]],
            links=[PageLink(**link) for link in snapshot["links"]],
        )

    async def _stabilize_page(self, page: Page) -> None:
        try:
            await page.wait_for_load_state("networkidle", timeout=1000)
        except Exception:
            pass

        try:
            await page.evaluate(
                """
                async () => {
                  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
                  let lastMutation = Date.now();
                  const observer = new MutationObserver(() => {
                    lastMutation = Date.now();
                  });
                  observer.observe(document, {
                    subtree: true,
                    childList: true,
                    attributes: true,
                    characterData: true,
                  });

                  const maxScroll = Math.max(
                    document.body?.scrollHeight || 0,
                    document.documentElement?.scrollHeight || 0
                  );
                  const viewport = window.innerHeight || 800;
                  for (let offset = 0; offset <= maxScroll; offset += Math.max(200, Math.floor(viewport * 0.75))) {
                    window.scrollTo({ top: offset, behavior: "auto" });
                    await wait(100);
                  }
                  window.scrollTo({ top: 0, behavior: "auto" });

                  for (let index = 0; index < 8; index += 1) {
                    await wait(100);
                    if (Date.now() - lastMutation >= 250) {
                      break;
                    }
                  }

                  observer.disconnect();
                }
                """
            )
        except Exception:
            pass


@asynccontextmanager
async def browser_runtime_lifespan(runtime: BrowserRuntime) -> AsyncIterator[None]:
    await runtime.start()
    try:
        yield
    finally:
        await runtime.stop()
