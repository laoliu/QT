"""Sector rotation analysis provider.

Provides sector ranking, constituent details, rotation detection,
and momentum scoring for industry and concept boards.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

__all__ = ["SectorAnalysisProvider"]

logger = logging.getLogger(__name__)


class SectorAnalysisProvider:
    """Provider for sector analysis and rotation detection."""

    def __init__(self, cache_dir: Optional[str] = None) -> None:
        self._cache_dir = cache_dir

    def sector_ranking(
        self, category: str = "industry", limit: int = 30,
    ) -> List[Dict[str, Any]]:
        """Fetch sector ranking sorted by change_pct.

        Parameters
        ----------
        category : str
            'industry' for 行业板块, 'concept' for 概念板块.
        """
        try:
            import akshare as ak
            if category == "concept":
                df = ak.stock_board_concept_name_em()
            else:
                df = ak.stock_board_industry_name_em()
            if df is None or df.empty:
                return []
            results = []
            for _, row in df.head(limit).iterrows():
                results.append({
                    "name": str(row.get("板块名称", "")),
                    "code": str(row.get("板块代码", "")),
                    "change_pct": float(row.get("涨跌幅", 0)),
                    "turnover": float(row.get("换手率", 0)),
                    "amount": float(row.get("成交额", 0)),
                    "stock_count": int(row.get("上涨家数", 0)),
                    "leader": str(row.get("领涨股票", "")),
                })
            return results
        except Exception as e:
            logger.warning("Sector ranking failed: %s", e)
            return []

    def sector_detail(
        self, sector_name: str, limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Fetch constituent stocks of a sector."""
        try:
            import akshare as ak
            df = ak.stock_board_industry_cons_em(symbol=sector_name)
            if df is None or df.empty:
                df = ak.stock_board_concept_cons_em(symbol=sector_name)
            if df is None or df.empty:
                return []
            results = []
            for _, row in df.head(limit).iterrows():
                results.append({
                    "ts_code": str(row.get("代码", "")),
                    "name": str(row.get("名称", "")),
                    "change_pct": float(row.get("涨跌幅", 0)),
                    "close": float(row.get("最新价", 0)),
                    "turnover": float(row.get("换手率", 0)),
                })
            return results
        except Exception as e:
            logger.warning("Sector detail failed: %s", e)
            return []

    def rotation_signal(
        self, lookback_days: int = 5,
    ) -> Dict[str, Any]:
        """Detect sector rotation by comparing recent vs prior ranking."""
        current = self.sector_ranking("industry", limit=50)
        if not current:
            return {"rising": [], "falling": [], "stable": []}

        # Sort by change and classify
        sorted_sectors = sorted(
            current, key=lambda s: s["change_pct"], reverse=True,
        )
        top_n = 5
        rising = [s["name"] for s in sorted_sectors[:top_n]]
        falling = [s["name"] for s in sorted_sectors[-top_n:]]

        return {
            "rising": rising,
            "falling": falling,
            "top_sectors": sorted_sectors[:top_n],
            "bottom_sectors": sorted_sectors[-top_n:],
        }
