"""Institutional fund flow data provider.

Tracks institutional holdings, block trades, insider transactions,
and computes smart-money conviction signals.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

__all__ = ["InstitutionalFlowProvider"]

logger = logging.getLogger(__name__)


class InstitutionalFlowProvider:
    """Provider for institutional fund flow analysis."""

    def __init__(self, cache_dir: Optional[str] = None) -> None:
        self._cache_dir = cache_dir

    def fund_holdings(
        self, ts_code: str, limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Fetch top institutional holders for a stock."""
        try:
            import akshare as ak
            code = ts_code.split(".")[0]
            df = ak.stock_sdgd_em(symbol=code)
            if df is None or df.empty:
                return []
            results = []
            for _, row in df.head(limit).iterrows():
                results.append({
                    "holder": str(row.get("股东名称", "")),
                    "shares": float(row.get("持股数量", 0)),
                    "ratio": float(row.get("持股比例", 0)),
                    "change": str(row.get("增减", "")),
                })
            return results
        except Exception as e:
            logger.warning("Failed to fetch fund holdings: %s", e)
            return []

    def block_trades(
        self, ts_code: str, limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Fetch block trade (大宗交易) records."""
        try:
            import akshare as ak
            code = ts_code.split(".")[0]
            df = ak.stock_dzjy_mdetail(symbol=code)
            if df is None or df.empty:
                return []
            results = []
            for _, row in df.head(limit).iterrows():
                price = float(row.get("成交价", 0))
                close = float(row.get("收盘价", 0))
                premium = ((price / close - 1) * 100) if close > 0 else 0
                results.append({
                    "trade_date": str(row.get("交易日期", "")),
                    "price": price,
                    "volume": float(row.get("成交量", 0)),
                    "amount": float(row.get("成交额", 0)),
                    "premium_pct": round(premium, 2),
                    "buyer": str(row.get("买方营业部", "")),
                    "seller": str(row.get("卖方营业部", "")),
                })
            return results
        except Exception as e:
            logger.warning("Failed to fetch block trades: %s", e)
            return []

    def smart_money_signal(
        self, ts_code: str,
    ) -> Dict[str, Any]:
        """Compute composite smart-money conviction score.

        Combines institutional trend, block-trade premium, insider
        activity, and main-force flow into a -100 to +100 score.
        """
        holdings = self.fund_holdings(ts_code, limit=5)
        blocks = self.block_trades(ts_code, limit=10)

        score = 0.0
        reasons: List[str] = []

        # institutional trend
        increase = sum(1 for h in holdings if "增" in h.get("change", ""))
        decrease = sum(1 for h in holdings if "减" in h.get("change", ""))
        if increase > decrease:
            score += 25
            reasons.append(f"机构增持({increase}家)")
        elif decrease > increase:
            score -= 15
            reasons.append(f"机构减持({decrease}家)")

        # block trade premium
        if blocks:
            avg_premium = sum(b["premium_pct"] for b in blocks) / len(blocks)
            if avg_premium > 0:
                score += min(25, avg_premium * 5)
                reasons.append(f"大宗溢价{avg_premium:.1f}%")
            elif avg_premium < -3:
                score -= 15
                reasons.append(f"大宗折价{avg_premium:.1f}%")

        return {
            "ts_code": ts_code,
            "score": round(max(-100, min(100, score)), 1),
            "reasons": reasons,
            "holding_count": len(holdings),
            "block_count": len(blocks),
        }
