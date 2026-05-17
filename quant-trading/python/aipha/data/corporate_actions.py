"""Corporate actions tracker.

Tracks dividends, rights issues, stock splits, allotments, and
name changes for A-share stocks.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

__all__ = ["CorporateActionsProvider"]

logger = logging.getLogger(__name__)


class CorporateActionsProvider:
    """Provider for corporate action events."""

    def __init__(self, cache_dir: Optional[str] = None) -> None:
        self._cache_dir = cache_dir

    def dividends(
        self, ts_code: str, limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Fetch dividend history."""
        try:
            import akshare as ak
            code = ts_code.split(".")[0]
            df = ak.stock_history_dividend_detail(
                symbol=code, indicator="分红"
            )
            if df is None or df.empty:
                return []
            results = []
            for _, row in df.head(limit).iterrows():
                results.append({
                    "report_date": str(row.get("报告期", "")),
                    "ex_date": str(row.get("除权除息日", "")),
                    "cash_div": float(row.get("每股分红", 0)),
                    "stock_div": float(row.get("每股送股", 0)),
                    "transfer": float(row.get("每股转增", 0)),
                })
            return results
        except Exception as e:
            logger.warning("Failed to fetch dividends: %s", e)
            return []

    def rights_issue(
        self, ts_code: str, limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Fetch rights issue (配股) records."""
        try:
            import akshare as ak
            df = ak.stock_pg_em()
            if df is None or df.empty:
                return []
            code = ts_code.split(".")[0]
            filtered = df[df["代码"].astype(str) == code]
            results = []
            for _, row in filtered.head(limit).iterrows():
                results.append({
                    "date": str(row.get("配股日期", "")),
                    "price": float(row.get("配股价格", 0)),
                    "ratio": float(row.get("配股比例", 0)),
                })
            return results
        except Exception as e:
            logger.warning("Failed to fetch rights issue: %s", e)
            return []

    def allotment(
        self, ts_code: str, limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Fetch stock allotment (增发) records."""
        try:
            import akshare as ak
            df = ak.stock_zf_em()
            if df is None or df.empty:
                return []
            code = ts_code.split(".")[0]
            filtered = df[df["代码"].astype(str) == code]
            results = []
            for _, row in filtered.head(limit).iterrows():
                results.append({
                    "date": str(row.get("增发日期", "")),
                    "price": float(row.get("增发价格", 0)),
                    "shares": float(row.get("增发数量", 0)),
                    "amount": float(row.get("增发金额", 0)),
                })
            return results
        except Exception as e:
            logger.warning("Failed to fetch allotment: %s", e)
            return []

    def summary(self, ts_code: str) -> Dict[str, Any]:
        """Aggregate all corporate actions for a stock."""
        return {
            "ts_code": ts_code,
            "dividends": self.dividends(ts_code, limit=5),
            "rights_issue": self.rights_issue(ts_code, limit=3),
            "allotment": self.allotment(ts_code, limit=3),
        }
