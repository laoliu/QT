"""Base browser session management using Playwright.

Design:
- Uses playwright-stealth to avoid bot detection
- Persistent browser context to reuse cookies / login state
- Cookie export/import for cross-session continuity
- Configurable headless/headed mode
- Retry & rate-limiting built in
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class BrowserSession:
    """Manages a Playwright browser session with anti-detection.

    Usage:
        async with BrowserSession(data_dir="data/tracker/browser") as session:
            page = await session.new_page()
            await page.goto("https://xueqiu.com")
    """

    # Default stealth viewport & user-agent values
    DEFAULT_VIEWPORT = {"width": 1920, "height": 1080}
    DEFAULT_UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        data_dir: str | Path = "data/tracker/browser",
        headless: bool = True,
        slow_mo: int = 0,
        proxy: dict[str, str] | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._headless = headless
        self._slow_mo = slow_mo
        self._proxy = proxy
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._cookies_path = self.data_dir / "cookies.json"

    async def __aenter__(self) -> BrowserSession:
        await self.start()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.stop()

    async def start(self) -> None:
        """Launch browser and create context."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise ImportError(
                "playwright is required. Install with: pip install playwright && playwright install chromium"
            )

        self._playwright = await async_playwright().start()

        launch_opts: dict[str, Any] = {
            "headless": self._headless,
            "slow_mo": self._slow_mo,
        }
        if self._proxy:
            launch_opts["proxy"] = self._proxy

        self._browser = await self._playwright.chromium.launch(**launch_opts)

        context_opts: dict[str, Any] = {
            "viewport": self.DEFAULT_VIEWPORT,
            "user_agent": self.DEFAULT_UA,
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
        }

        self._context = await self._browser.new_context(**context_opts)

        # Apply stealth patches
        await self._apply_stealth()

        # Restore cookies if available
        await self._load_cookies()

        logger.info("Browser session started (headless=%s)", self._headless)

    async def stop(self) -> None:
        """Save cookies and close browser."""
        if self._context:
            await self._save_cookies()
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("Browser session stopped")

    async def new_page(self) -> Any:
        """Create a new page in the browser context."""
        if not self._context:
            raise RuntimeError("Browser session not started. Call start() first.")
        page = await self._context.new_page()
        return page

    # -- Cookie management --------------------------------------------------

    async def _save_cookies(self) -> None:
        """Save cookies to file for next session."""
        if not self._context:
            return
        cookies = await self._context.cookies()
        self._cookies_path.write_text(
            json.dumps(cookies, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.debug("Saved %d cookies to %s", len(cookies), self._cookies_path)

    async def _load_cookies(self) -> None:
        """Load cookies from file if available."""
        if not self._cookies_path.exists():
            logger.debug("No saved cookies found")
            return
        try:
            cookies = json.loads(self._cookies_path.read_text(encoding="utf-8"))
            await self._context.add_cookies(cookies)
            logger.info("Loaded %d cookies from %s", len(cookies), self._cookies_path)
        except Exception as e:
            logger.warning("Failed to load cookies: %s", e)

    async def import_cookies(self, cookies_file: str | Path) -> None:
        """Import cookies from an external file (e.g., exported from browser)."""
        path = Path(cookies_file)
        if not path.exists():
            raise FileNotFoundError(f"Cookies file not found: {path}")
        cookies = json.loads(path.read_text(encoding="utf-8"))
        if self._context:
            await self._context.add_cookies(cookies)
        # Also save as our persistent cookies
        self._cookies_path.write_text(
            json.dumps(cookies, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Imported %d cookies from %s", len(cookies), path)

    # -- Stealth (anti-detection) --------------------------------------------

    async def _apply_stealth(self) -> None:
        """Apply stealth JavaScript patches to the browser context.

        These patches override common bot-detection fingerprints.
        """
        if not self._context:
            return

        # Inject stealth scripts before every page load
        stealth_js = """
        // Override navigator.webdriver
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

        // Override chrome.runtime (used by some detection scripts)
        window.chrome = { runtime: {} };

        // Override permissions query
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) =>
            parameters.name === 'notifications'
                ? Promise.resolve({ state: Notification.permission })
                : originalQuery(parameters);

        // Override plugins length (bots typically have 0 plugins)
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5],
        });

        // Override languages
        Object.defineProperty(navigator, 'languages', {
            get: () => ['zh-CN', 'zh', 'en'],
        });
        """
        await self._context.add_init_script(stealth_js)
        logger.debug("Stealth patches applied")

    # -- Rate limiting -------------------------------------------------------

    @staticmethod
    async def random_delay(min_sec: float = 1.0, max_sec: float = 3.0) -> None:
        """Sleep for a random interval to mimic human behavior."""
        delay = random.uniform(min_sec, max_sec)
        await asyncio.sleep(delay)

    @staticmethod
    def human_delay() -> float:
        """Return a random delay suitable for simulating human interaction."""
        return random.uniform(0.5, 2.0)

    # -- Retry helper --------------------------------------------------------

    @staticmethod
    async def retry_async(
        coro_factory: Any,
        max_retries: int = 3,
        base_delay: float = 2.0,
    ) -> Any:
        """Retry an async callable with exponential backoff.

        Args:
            coro_factory: A callable that returns a coroutine.
            max_retries: Maximum number of retries.
            base_delay: Initial wait time between retries.
        """
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                return await coro_factory()
            except Exception as e:
                last_exc = e
                if attempt < max_retries:
                    wait = base_delay * (2**attempt) + random.uniform(0, 1)
                    logger.warning(
                        "Attempt %d/%d failed: %s. Retrying in %.1fs...",
                        attempt + 1,
                        max_retries + 1,
                        e,
                        wait,
                    )
                    await asyncio.sleep(wait)
        raise last_exc  # type: ignore[misc]

    # -- Diagnostics --------------------------------------------------------

    async def check_login_status(self, url: str, login_indicator: str) -> bool:
        """Check if we're logged in by visiting a URL and checking for an element.

        Args:
            url: URL to navigate to.
            login_indicator: CSS selector that's present when logged in.

        Returns:
            True if the login_indicator element is found.
        """
        page = await self.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await BrowserSession.random_delay(1.0, 2.0)
            return await page.query_selector(login_indicator) is not None
        finally:
            await page.close()
