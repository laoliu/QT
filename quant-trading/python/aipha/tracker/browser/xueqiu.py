"""Xueqiu (雪球) data collector.

Handles:
1. Fetching portfolio holdings from Xueqiu's ZH portfolio pages
2. Fetching user posts (帖子) for stock mention parsing
3. Login check & cookie refresh

Xueqiu data paths:
- Portfolio page: https://xueqiu.com/P/ZHxxxxxx
- Portfolio API:  https://stock.xueqiu.com/v5/stock/portfolio/stock/list.json?pid=ZHxxxxxx
- User posts:    https://xueqiu.com/v4/statuses/user_timeline.json?user_id=xxx
- User profile:  https://xueqiu.com/u/xxx
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from decimal import Decimal
from typing import Any

from ..models import (
    Holding,
    PortfolioSnapshot,
    PortfolioSource,
)
from .base import BrowserSession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

XUEQIU_BASE = "https://xueqiu.com"
XUEQIU_STOCK_API = "https://stock.xueqiu.com"

# selectors for login detection
XUEQIU_LOGIN_INDICATOR = "a.nav__login__btn--name"  # logged-in user name link


class XueqiuCollector:
    """Collects portfolio and post data from Xueqiu.

    Usage:
        async with BrowserSession() as session:
            collector = XueqiuCollector(session)
            snapshot = await collector.fetch_portfolio("ZH123456", author_id="xzlx")
    """

    def __init__(self, session: BrowserSession) -> None:
        self._session = session

    # -- Login ---------------------------------------------------------------

    async def check_login(self) -> bool:
        """Check if current session is logged into Xueqiu."""
        return await self._session.check_login_status(
            XUEQIU_BASE,
            XUEQIU_LOGIN_INDICATOR,
        )

    async def login_interactive(self, timeout: int = 120) -> bool:
        """Open login page in headed mode for manual login.

        The browser must be launched with headless=False for this to work.
        Waits up to `timeout` seconds for the user to complete login.

        Returns: True if login succeeded.
        """
        import asyncio

        page = await self._session.new_page()
        try:
            await page.goto(f"{XUEQIU_BASE}", wait_until="domcontentloaded")
            logger.info("请在浏览器中手动登录雪球，等待最多 %d 秒...", timeout)

            # Wait for the login indicator to appear
            try:
                await page.wait_for_selector(
                    XUEQIU_LOGIN_INDICATOR,
                    timeout=timeout * 1000,
                )
                logger.info("登录成功!")
                await BrowserSession.random_delay(2, 3)
                return True
            except Exception:
                logger.warning("登录超时或失败")
                return False
        finally:
            await page.close()

    # -- Portfolio fetching --------------------------------------------------

    async def fetch_portfolio(
        self,
        portfolio_code: str,
        author_id: str,
        author_name: str,
    ) -> PortfolioSnapshot | None:
        """Fetch holdings of a Xueqiu portfolio (组合).

        Uses retry_async for resilience against transient network errors.

        Args:
            portfolio_code: e.g. 'ZH123456'
            author_id: Internal author identifier
            author_name: Display name

        Returns:
            PortfolioSnapshot or None if fetch fails
        """

        async def _do_fetch() -> PortfolioSnapshot | None:
            return await self._fetch_portfolio_once(
                portfolio_code, author_id, author_name,
            )

        try:
            return await BrowserSession.retry_async(_do_fetch, max_retries=2, base_delay=3.0)
        except Exception as e:
            logger.error("All retries exhausted for portfolio %s: %s", portfolio_code, e)
            return None

    async def _fetch_portfolio_once(
        self,
        portfolio_code: str,
        author_id: str,
        author_name: str,
    ) -> PortfolioSnapshot | None:
        """Single attempt to fetch portfolio holdings.
        """
        page = await self._session.new_page()
        try:
            holdings_data: list[dict[str, Any]] = []

            # Set up response interception for portfolio API
            async def handle_response(response: Any) -> None:
                url = response.url
                if "portfolio/stock/list" in url or "portfolio/stock.json" in url:
                    try:
                        data = await response.json()
                        if isinstance(data, dict) and "stocks" in data:
                            holdings_data.extend(data["stocks"])
                        elif isinstance(data, dict) and "data" in data:
                            inner = data["data"]
                            if isinstance(inner, dict) and "stocks" in inner:
                                holdings_data.extend(inner["stocks"])
                    except Exception as e:
                        logger.debug("Failed to parse portfolio response: %s", e)

            page.on("response", handle_response)

            # Navigate to portfolio page
            url = f"{XUEQIU_BASE}/P/{portfolio_code}"
            logger.info("Fetching portfolio: %s", url)
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await BrowserSession.random_delay(2, 4)

            # If no API was intercepted, try scraping the page directly
            if not holdings_data:
                holdings_data = await self._scrape_portfolio_page(page)

            if not holdings_data:
                logger.warning("No holdings data found for %s", portfolio_code)
                return None

            # Parse holdings
            holdings = self._parse_holdings(holdings_data)
            if not holdings:
                logger.warning("Failed to parse any holdings from data")
                return None

            now = datetime.now()
            return PortfolioSnapshot(
                snapshot_id=PortfolioSnapshot.new_id(),
                source=PortfolioSource.XUEQIU,
                author_id=author_id,
                author_name=author_name,
                portfolio_code=portfolio_code,
                snapshot_time=now,
                fetch_time=now,
                holdings=tuple(holdings),
            )

        except Exception as e:
            logger.error("Failed to fetch portfolio %s: %s", portfolio_code, e)
            return None
        finally:
            await page.close()

    async def _scrape_portfolio_page(self, page: Any) -> list[dict[str, Any]]:
        """Fallback: scrape portfolio holdings from the rendered page DOM.

        Returns a list of dicts with keys: symbol, name, weight.
        """
        results: list[dict[str, Any]] = []
        try:
            # Try to find the stock table on the portfolio page
            rows = await page.query_selector_all(
                "table.stock-table tbody tr, "
                ".portfolio-stocks .stock-item, "
                ".portfolio-stock-list .item"
            )

            for row in rows:
                try:
                    text = await row.inner_text()
                    # Try to extract stock code, name, and weight from the row text
                    parsed = self._parse_table_row(text)
                    if parsed:
                        results.append(parsed)
                except Exception:
                    continue

            logger.info("Scraped %d holdings from page DOM", len(results))
        except Exception as e:
            logger.debug("DOM scraping failed: %s", e)

        return results

    @staticmethod
    def _parse_table_row(text: str) -> dict[str, Any] | None:
        """Parse a table row text into holding data.

        Expected format variations:
            - "SH600519 贵州茅台 30.5%"
            - "600519 贵州茅台 30.5"
        """
        # Match stock code patterns
        code_match = re.search(r'([A-Z]{0,2}\d{6})', text)
        if not code_match:
            return None

        # Match percentage — search only AFTER the code to avoid matching
        # the stock code's digits (e.g. '600519') as weight.
        text_after_code = text[code_match.end():]
        pct_match = re.search(r'(\d+\.?\d*)\s*%', text_after_code)
        if not pct_match:
            # Fallback: try matching a standalone number after the code
            pct_match = re.search(r'(?:^|\s)(\d+\.?\d*)(?:\s|$)', text_after_code)
        weight = float(pct_match.group(1)) if pct_match else 0

        # Extract name (Chinese characters)
        name_match = re.search(r'([\u4e00-\u9fa5]+)', text)
        name = name_match.group(1) if name_match else "unknown"

        code = code_match.group(1)

        return {
            "symbol": code,
            "name": name,
            "weight": weight,
        }

    @staticmethod
    def _parse_holdings(data: list[dict[str, Any]]) -> list[Holding]:
        """Parse raw API/scraped data into Holding objects."""
        holdings: list[Holding] = []

        for item in data:
            try:
                # Handle different key naming conventions from Xueqiu
                symbol = (
                    item.get("code")
                    or item.get("symbol")
                    or item.get("stock_symbol")
                    or ""
                )
                name = (
                    item.get("name")
                    or item.get("stock_name")
                    or ""
                )

                # Weight can be in percent (0-100) or fraction (0-1)
                weight_raw = (
                    item.get("weight")
                    or item.get("proportion")
                    or item.get("percent")
                    or 0
                )
                weight = float(weight_raw)
                if weight > 1:
                    weight = weight / 100.0

                # Normalize symbol format: SH600519, SZ000001 etc.
                from ..utils import normalize_symbol as _normalize_sym
                symbol = _normalize_sym(symbol)

                if symbol and name:
                    holdings.append(
                        Holding(
                            symbol=symbol,
                            name=name,
                            weight=Decimal(str(round(weight, 6))),
                            shares=item.get("shares") or item.get("volume"),
                            cost=Decimal(str(item["cost"])) if item.get("cost") else None,
                        )
                    )
            except Exception as e:
                logger.debug("Failed to parse holding item %s: %s", item, e)

        return holdings

    # -- User posts ----------------------------------------------------------

    async def fetch_user_posts(
        self,
        user_id: str,
        count: int = 20,
    ) -> list[dict[str, Any]]:
        """Fetch recent posts from a Xueqiu user.

        Args:
            user_id: Xueqiu numeric user ID.
            count: Number of posts to fetch.

        Returns:
            List of post dicts with keys: id, text, created_at, retweet_count, etc.
        """
        page = await self._session.new_page()
        try:
            posts: list[dict[str, Any]] = []

            # Intercept the timeline API
            async def handle_response(response: Any) -> None:
                url = response.url
                if "statuses/user_timeline" in url or "statuses/original" in url:
                    try:
                        data = await response.json()
                        if isinstance(data, dict):
                            items = data.get("statuses") or data.get("list") or []
                            for item in items:
                                posts.append(
                                    self._parse_post(item)
                                )
                    except Exception as e:
                        logger.debug("Failed to parse posts response: %s", e)

            page.on("response", handle_response)

            # Navigate to user page
            url = f"{XUEQIU_BASE}/u/{user_id}"
            logger.info("Fetching posts for user: %s", user_id)
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await BrowserSession.random_delay(2, 4)

            # Scroll to load more posts if needed
            for _ in range(min(3, count // 10)):
                if len(posts) >= count:
                    break
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await BrowserSession.random_delay(1.5, 3.0)

            logger.info("Fetched %d posts for user %s", len(posts), user_id)
            return posts[:count]

        except Exception as e:
            logger.error("Failed to fetch posts for user %s: %s", user_id, e)
            return []
        finally:
            await page.close()

    @staticmethod
    def _parse_post(item: dict[str, Any]) -> dict[str, Any]:
        """Parse a raw post item into a clean dict."""
        # Xueqiu timestamps are in milliseconds
        created_ts = item.get("created_at", 0)
        if isinstance(created_ts, (int, float)) and created_ts > 1e12:
            created_ts = created_ts / 1000
        created_at = datetime.fromtimestamp(created_ts) if created_ts else None

        return {
            "post_id": str(item.get("id", "")),
            "text": item.get("text") or item.get("description") or "",
            "title": item.get("title", ""),
            "created_at": created_at.isoformat() if created_at else None,
            "user_id": str(item.get("user_id") or item.get("user", {}).get("id", "")),
            "retweet_count": item.get("retweet_count", 0),
            "reply_count": item.get("reply_count", 0),
            "like_count": item.get("like_count", 0),
            "source": "xueqiu",
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
