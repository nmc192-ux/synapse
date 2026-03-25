from __future__ import annotations

from pathlib import Path
from typing import Any


class RecoveryEngine:
    async def stabilize_page(self, page: Any) -> None:
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
                  const observer = new MutationObserver(() => { lastMutation = Date.now(); });
                  observer.observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
                  const maxScroll = Math.max(document.body?.scrollHeight || 0, document.documentElement?.scrollHeight || 0);
                  const viewport = window.innerHeight || 800;
                  for (let offset = 0; offset <= maxScroll; offset += Math.max(250, Math.floor(viewport * 0.75))) {
                    window.scrollTo({ top: offset, behavior: "auto" });
                    await wait(80);
                  }
                  window.scrollTo({ top: 0, behavior: "auto" });
                  for (let index = 0; index < 8; index += 1) {
                    await wait(100);
                    if (Date.now() - lastMutation >= 250) break;
                  }
                  observer.disconnect();
                }
                """
            )
        except Exception:
            pass

    async def wait_for_navigation_ready(self, page: Any) -> None:
        await page.wait_for_load_state("domcontentloaded")
        await self.stabilize_page(page)

    async def dismiss_blockers(self, page: Any) -> list[str]:
        selectors = [
            "button:has-text('Accept')",
            "button:has-text('I Agree')",
            "button:has-text('Agree')",
            "button:has-text('Allow all')",
            "button:has-text('Close')",
            "[aria-label='Close']",
            ".cookie-banner button",
            ".consent button",
            "[role='dialog'] button",
            ".modal button",
        ]
        dismissed: list[str] = []
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if await locator.count() > 0 and await locator.is_visible():
                    await locator.click(timeout=500)
                    dismissed.append(selector)
            except Exception:
                continue
        return dismissed

    async def retry_click(self, page: Any, selector: str, retries: int = 3) -> None:
        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                await page.locator(selector).first.click(timeout=2500)
                return
            except Exception as exc:
                last_error = exc
                if attempt < retries - 1:
                    await self.stabilize_page(page)
                    candidate = self.fallback_selector(selector)
                    if candidate:
                        selector = candidate
        raise RuntimeError(self.classify_browser_error("click", last_error or RuntimeError("click failed")))

    async def retry_type(self, page: Any, selector: str, text: str, retries: int = 3) -> None:
        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                await page.locator(selector).first.fill(text, timeout=2500)
                return
            except Exception as exc:
                last_error = exc
                if attempt < retries - 1:
                    await self.stabilize_page(page)
                    candidate = self.fallback_selector(selector)
                    if candidate:
                        selector = candidate
        raise RuntimeError(self.classify_browser_error("type", last_error or RuntimeError("type failed")))

    @staticmethod
    def fallback_selector(selector: str) -> str | None:
        stripped = selector.strip()
        if stripped.startswith("text="):
            text = stripped.removeprefix("text=").strip().strip("\"'")
            return f"button:has-text('{text}'), [role='button']:has-text('{text}'), a:has-text('{text}')"
        if stripped.startswith("#") or "[" in stripped:
            return stripped
        return None

    async def bounded_scroll(self, page: Any, max_scrolls: int, scroll_step: int) -> None:
        bounded_scrolls = max(1, min(max_scrolls, 20))
        bounded_step = max(200, min(scroll_step, 2000))
        await page.evaluate(
            """
            async ({ maxScrolls, step }) => {
              const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
              let loops = 0;
              let lastHeight = document.body.scrollHeight;
              while (loops < maxScrolls) {
                window.scrollBy(0, step);
                await wait(150);
                const height = document.body.scrollHeight;
                if (height === lastHeight) break;
                lastHeight = height;
                loops += 1;
              }
            }
            """,
            {"maxScrolls": bounded_scrolls, "step": bounded_step},
        )
        await self.stabilize_page(page)

    @staticmethod
    def detect_session_expired(session_manager, session_id: str, snapshot) -> bool:
        previous = session_manager.get_last_url(session_id)
        session_manager.set_last_url(session_id, snapshot.url)
        login_inputs = [
            field
            for field in snapshot.inputs
            if (field.input_type or "").lower() in {"password", "email"}
            or "login" in (field.placeholder or "").lower()
            or "sign in" in (field.placeholder or "").lower()
        ]
        return bool(previous and previous != snapshot.url and login_inputs)

    @staticmethod
    def route_change_metadata(before_url: str | None, after_url: str | None) -> dict[str, object]:
        if before_url is None or after_url is None or before_url == after_url:
            return {}
        before_path = before_url.split("#")[0]
        after_path = after_url.split("#")[0]
        return {
            "route_changed": True,
            "spa_route_change": before_path.split("?")[0] != after_path.split("?")[0],
            "from_url": before_url,
            "to_url": after_url,
        }

    @staticmethod
    def classify_browser_error(action: str, exc: Exception) -> str:
        message = str(exc).lower()
        if "timeout" in message:
            category = "timeout"
        elif "net::" in message or "dns" in message:
            category = "network"
        elif "stale" in message or "detached" in message:
            category = "stale_element"
        else:
            category = "interaction"
        return f"browser.{action}.{category}: {exc}"
