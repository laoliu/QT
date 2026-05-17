"""Earnings calendar and analyst ratings provider.

Tracks upcoming earnings disclosure dates, profit forecasts,
earnings surprises, and analyst consensus ratings.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

__all__ = ["EarningsCalendarProvider"]

logger = logging.getLogger(__name__)


class EarningsCalendarProvider:
    """Provider for earnings calendar and analyst data."""

    def __init__(self, cache_dir: Optional[str] = None) -> None:
        self._cache_dir = cache_dir

    def upcoming_earnings(
        self, days: int = 30, limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Fetch upcoming earnings disclosure dates."""
        try:
            import akshare as ak
            df = ak.stock_report_disclosure()
            if df is None or df.empty:
                return []
            results = []
            for _, row in df.head(limit).iterrows():
                results.append({
                    "ts_code": str(row.get("代码", "")),
                    "name": str(row.get("名称", "")),
                    "report_date": str(row.get("预计披露日期", "")),
                    "report_type": str(row.get("报告类型", "")),
                })
            return results
        except Exception as e:
            logger.warning("Failed to fetch upcoming earnings: %s", e)
            return []

    def earnings_surprise(
        self, ts_code: str, limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Fetch earnings beat/miss history (业绩快报)."""
        try:
            import akshare as ak
            df = ak.stock_yjkb_em()
            if df is None or df.empty:
                return []
            code = ts_code.split(".")[0]
            filtered = df[df["股票代码"].astype(str) == code]
            results = []
            for _, row in filtered.head(limit).iterrows():
                results.append({
                    "report_period": str(row.get("报告期", "")),
                    "revenue": float(row.get("营业收入", 0)),
                    "net_profit": float(row.get("归母净利润", 0)),
                    "yoy_growth": float(row.get("同比增长", 0)),
                })
            return results
        except Exception as e:
            logger.warning("Failed to fetch earnings surprise: %s", e)
            return []

    def analyst_ratings(
        self, ts_code: str, limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Fetch analyst ratings and target prices."""
        try:
            import akshare as ak
            code = ts_code.split(".")[0]
            df = ak.stock_analyst_rank_em(symbol=code)
            if df is None or df.empty:
                return []
            results = []
            for _, row in df.head(limit).iterrows():
                results.append({
                    "analyst": str(row.get("分析师", "")),
                    "institution": str(row.get("机构", "")),
                    "rating": str(row.get("评级", "")),
                    "target_price": float(row.get("目标价", 0)),
                    "date": str(row.get("日期", "")),
                })
            return results
        except Exception as e:
            logger.warning("Failed to fetch analyst ratings: %s", e)
            return []
