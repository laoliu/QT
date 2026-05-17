"""Dragon tiger board (龙虎榜) data provider.

Fetches and aggregates LHB data including daily lists, per-stock
history, institutional stats, and hot-money desk tracking.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

__all__ = ["DragonTigerProvider"]

logger = logging.getLogger(__name__)


@dataclass
class LHBEntry:
    """A single dragon-tiger board entry."""

    ts_code: str = ""
    name: str = ""
    trade_date: str = ""
    reason: str = ""
    buy_amount: float = 0.0
    sell_amount: float = 0.0
    net_amount: float = 0.0
    turnover: float = 0.0
    change_pct: float = 0.0


class DragonTigerProvider:
    """Provider for dragon-tiger board data.

    Wraps akshare LHB APIs with caching and error handling.
    """

    def __init__(self, cache_dir: Optional[str] = None) -> None:
        self._cache_dir = cache_dir

    def daily_list(
        self, date: str, limit: int = 50,
    ) -> List[LHBEntry]:
        """Fetch daily LHB list for a given date (YYYYMMDD)."""
        try:
            import akshare as ak
            df = ak.stock_lhb_detail_em(start_date=date, end_date=date)
            if df is None or df.empty:
                return []
            entries: List[LHBEntry] = []
            for _, row in df.head(limit).iterrows():
                entries.append(LHBEntry(
                    ts_code=str(row.get("代码", "")),
                    name=str(row.get("名称", "")),
                    trade_date=date,
                    reason=str(row.get("上榜原因", "")),
                    buy_amount=float(row.get("买入额", 0)),
                    sell_amount=float(row.get("卖出额", 0)),
                    net_amount=float(row.get("净买额", 0)),
                ))
            return entries
        except Exception as e:
            logger.warning("Failed to fetch LHB daily: %s", e)
            return []

    def stock_history(
        self, ts_code: str, limit: int = 20,
    ) -> List[LHBEntry]:
        """Fetch LHB history for a specific stock."""
        try:
            import akshare as ak
            code = ts_code.split(".")[0]
            df = ak.stock_lhb_stock_statistic_em(symbol=code)
            if df is None or df.empty:
                return []
            entries: List[LHBEntry] = []
            for _, row in df.head(limit).iterrows():
                entries.append(LHBEntry(
                    ts_code=ts_code,
                    name=str(row.get("名称", "")),
                    trade_date=str(row.get("日期", "")),
                    buy_amount=float(row.get("买入额", 0)),
                    sell_amount=float(row.get("卖出额", 0)),
                    net_amount=float(row.get("净买额", 0)),
                ))
            return entries
        except Exception as e:
            logger.warning("Failed to fetch LHB stock: %s", e)
            return []
