"""Chip distribution (筹码分布/CYQ) analysis provider.

Fetches chip distribution data and scores accumulation/distribution
patterns based on winner ratio, concentration, and support/resistance.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

__all__ = ["ChipDistributionProvider", "score_chip_distribution"]

logger = logging.getLogger(__name__)


class ChipDistributionProvider:
    """Provider for chip distribution data via akshare."""

    def __init__(self, cache_dir: Optional[str] = None) -> None:
        self._cache_dir = cache_dir

    def fetch(self, ts_code: str) -> Optional[Dict[str, Any]]:
        """Fetch chip distribution for a stock.

        Returns dict with keys: benefit_pct, avg_cost, concentration_70,
        concentration_90, support, resistance.
        """
        try:
            import akshare as ak
            code = ts_code.split(".")[0]
            df = ak.stock_cyq_em(symbol=code)
            if df is None or df.empty:
                return None
            row = df.iloc[-1]
            return {
                "benefit_pct": float(row.get("获利比例", 50)),
                "avg_cost": float(row.get("平均成本", 0)),
                "concentration_70": float(row.get("集中度70", 0.1)),
                "concentration_90": float(row.get("集中度90", 0.15)),
                "support": float(row.get("支撑位", 0)),
                "resistance": float(row.get("压力位", 0)),
            }
        except Exception as e:
            logger.warning("Failed to fetch CYQ for %s: %s", ts_code, e)
            return None


def score_chip_distribution(
    cyq: Dict[str, Any],
    current_price: float,
) -> Tuple[float, List[str], str]:
    """Score chip distribution data.

    Returns
    -------
    (score_delta, reasons, evidence_type)
        score_delta: -10 to +10 adjustment score.
        reasons: human-readable explanations.
        evidence_type: 'build' | 'distribution' | 'neutral'.
    """
    score = 0.0
    reasons: List[str] = []

    benefit = cyq.get("benefit_pct", 50)
    if benefit < 15:
        score += 4.0
        reasons.append(f"获利盘极低({benefit:.0f}%)，底部吸筹特征")
    elif benefit < 30:
        score += 2.0
        reasons.append(f"获利盘偏低({benefit:.0f}%)")
    elif benefit > 90:
        score -= 4.0
        reasons.append(f"获利盘极高({benefit:.0f}%)，派发特征")
    elif benefit > 75:
        score -= 2.0
        reasons.append(f"获利盘偏高({benefit:.0f}%)")

    conc70 = cyq.get("concentration_70", 0.1)
    if conc70 < 0.06:
        score += 3.0
        reasons.append(f"筹码高度集中(conc70={conc70:.3f})，庄家控盘")
    elif conc70 < 0.10:
        score += 1.5
        reasons.append(f"筹码集中度适中(conc70={conc70:.3f})")

    support = cyq.get("support", 0)
    resistance = cyq.get("resistance", 0)
    if support > 0 and current_price > 0:
        dist = (current_price - support) / current_price
        if dist < 0.03:
            score += 2.0
            reasons.append("接近支撑位")
    if resistance > 0 and current_price > 0:
        dist = (resistance - current_price) / current_price
        if dist < 0.03:
            score -= 1.5
            reasons.append("接近压力位")

    if score >= 3.0:
        evidence = "build"
    elif score <= -2.0:
        evidence = "distribution"
    else:
        evidence = "neutral"

    return round(score, 2), reasons, evidence
