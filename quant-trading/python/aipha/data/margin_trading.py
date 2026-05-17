"""Margin trading and market breadth data provider.

Provides margin/short balance data and market breadth indicators
(advancers, decliners, limit-up/down counts).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

__all__ = ["MarginTradingProvider"]

logger = logging.getLogger(__name__)


class MarginTradingProvider:
    """Provider for margin trading and market breadth data."""

    def __init__(self, cache_dir: Optional[str] = None) -> None:
        self._cache_dir = cache_dir

    def market_breadth(self) -> Dict[str, Any]:
        """Fetch market breadth: advancers, decliners, limits."""
        try:
            import akshare as ak
            df = ak.stock_market_activity_legu()
            if df is None or df.empty:
                return {"advancers": 0, "decliners": 0}
            row = df.iloc[-1] if len(df) > 0 else {}
            return {
                "advancers": int(row.get("上涨家数", 0)),
                "decliners": int(row.get("下跌家数", 0)),
                "limit_up": int(row.get("涨停家数", 0)),
                "limit_down": int(row.get("跌停家数", 0)),
                "flat": int(row.get("平盘家数", 0)),
            }
        except Exception as e:
            logger.warning("Failed to fetch breadth: %s", e)
            return {"advancers": 0, "decliners": 0}

    def margin_summary(self, limit: int = 10) -> Dict[str, Any]:
        """Fetch margin trading summary with recent history."""
        try:
            import akshare as ak
            df = ak.stock_margin_detail_sse()
            if df is None or df.empty:
                return {}
            recent = df.tail(limit)
            latest = df.iloc[-1]
            history = []
            for _, row in recent.iterrows():
                history.append({
                    "date": str(row.get("信用交易日期", "")),
                    "rzye": float(row.get("融资余额", 0)),
                    "rqye": float(row.get("融券余额", 0)),
                    "rzmre": float(row.get("融资买入额", 0)),
                })
            return {
                "latest_date": str(latest.get("信用交易日期", "")),
                "rzye": float(latest.get("融资余额", 0)),
                "rqye": float(latest.get("融券余额", 0)),
                "rzmre": float(latest.get("融资买入额", 0)),
                "history": history,
            }
        except Exception as e:
            logger.warning("Failed to fetch margin: %s", e)
            return {}
