"""Extended market calendar provider.

Provides trading day checks, holiday detection, IPO calendar,
ex-dividend dates, and index rebalance information.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

__all__ = ["MarketCalendarExt"]

logger = logging.getLogger(__name__)


class MarketCalendarExt:
    """Extended market calendar with event-based queries."""

    def __init__(self, cache_dir: Optional[str] = None) -> None:
        self._cache_dir = cache_dir
        self._trading_days: Optional[List[str]] = None

    def is_trading_day(self, date: str) -> bool:
        """Check if a date (YYYYMMDD) is a trading day."""
        days = self._ensure_trading_days()
        return date in days

    def trading_days_in_range(
        self, start: str, end: str,
    ) -> List[str]:
        """Get list of trading days in a date range (YYYYMMDD)."""
        days = self._ensure_trading_days()
        return [d for d in days if start <= d <= end]

    def next_trading_day(self, date: str) -> Optional[str]:
        """Find the next trading day after date."""
        days = self._ensure_trading_days()
        for d in days:
            if d > date:
                return d
        return None

    def prev_trading_day(self, date: str) -> Optional[str]:
        """Find the previous trading day before date."""
        days = self._ensure_trading_days()
        prev = None
        for d in days:
            if d >= date:
                return prev
            prev = d
        return prev

    def holidays(
        self, year: int,
    ) -> List[Dict[str, Any]]:
        """Detect holidays by finding non-trading weekdays."""
        start = f"{year}0101"
        end = f"{year}1231"
        trading = set(self.trading_days_in_range(start, end))
        holidays: List[Dict[str, Any]] = []
        d = datetime(year, 1, 1)
        end_d = datetime(year, 12, 31)
        while d <= end_d:
            ds = d.strftime("%Y%m%d")
            if d.weekday() < 5 and ds not in trading:
                holidays.append({
                    "date": ds,
                    "weekday": d.strftime("%A"),
                })
            d += timedelta(days=1)
        return holidays

    def ipo_calendar(
        self, limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Fetch upcoming IPO subscription dates."""
        try:
            import akshare as ak
            df = ak.stock_xgsglb_em(symbol="全部股票")
            if df is None or df.empty:
                return []
            results = []
            for _, row in df.head(limit).iterrows():
                results.append({
                    "ts_code": str(row.get("股票代码", "")),
                    "name": str(row.get("股票简称", "")),
                    "ipo_date": str(row.get("申购日期", "")),
                    "price": float(row.get("发行价格", 0)),
                })
            return results
        except Exception as e:
            logger.warning("IPO calendar fetch failed: %s", e)
            return []

    def _ensure_trading_days(self) -> List[str]:
        if self._trading_days is not None:
            return self._trading_days
        try:
            import akshare as ak
            df = ak.tool_trade_date_hist_sina()
            if df is not None and not df.empty:
                col = df.columns[0]
                self._trading_days = sorted(
                    df[col].astype(str).str.replace("-", "").tolist()
                )
                return self._trading_days
        except Exception as e:
            logger.warning("Trading days fetch failed: %s", e)
        self._trading_days = []
        return self._trading_days
