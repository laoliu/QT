"""Institutional chip accumulation detection engine.

Scores potential institutional accumulation using 5 dimensions:
fund flow, chip distribution, volume-price structure, turnover rate,
and large order net. Each dimension contributes 0-20 points for a
total score of 0-100.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

__all__ = ["ChipAccumulationDetector", "AccumulationResult"]

logger = logging.getLogger(__name__)

_THRESHOLD_STRONG = 70
_THRESHOLD_MODERATE = 50
_THRESHOLD_WEAK = 30


@dataclass
class AccumulationResult:
    """Result of accumulation detection for a single stock."""

    symbol: str = ""
    name: str = ""
    total_score: float = 0.0
    level: str = "none"
    fund_flow_score: float = 0.0
    chip_dist_score: float = 0.0
    vol_price_score: float = 0.0
    turnover_score: float = 0.0
    large_order_score: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)


class ChipAccumulationDetector:
    """Detect institutional accumulation patterns.

    Parameters
    ----------
    lookback : int
        Number of trading days to analyze (default 20).
    min_confidence : float
        Minimum score to include in scan results (default 50).
    """

    def __init__(
        self, lookback: int = 20, min_confidence: float = 50.0,
    ) -> None:
        self.lookback = lookback
        self.min_confidence = min_confidence

    def detect(
        self,
        bars: List[Dict[str, Any]],
        fund_flow: Optional[List[Dict[str, Any]]] = None,
        chip_data: Optional[Dict[str, Any]] = None,
        large_orders: Optional[List[Dict[str, Any]]] = None,
        symbol: str = "",
        name: str = "",
    ) -> AccumulationResult:
        """Detect accumulation for one stock.

        Parameters
        ----------
        bars : list of dict
            OHLCV bars with keys: open, high, low, close, volume, amount.
        fund_flow : list of dict or None
            Daily fund flow with keys: main_net, main_buy, main_sell.
        chip_data : dict or None
            Chip distribution data: benefit_pct, concentration, avg_cost.
        large_orders : list of dict or None
            Large order data: buy_amount, sell_amount.
        """
        recent = bars[-self.lookback:] if len(bars) > self.lookback else bars
        f1 = self._score_fund_flow(fund_flow or [])
        f2 = self._score_chip_distribution(chip_data or {})
        f3 = self._score_volume_price(recent)
        f4 = self._score_turnover(recent)
        f5 = self._score_large_orders(large_orders or [])
        total = f1 + f2 + f3 + f4 + f5
        if total >= _THRESHOLD_STRONG:
            level = "strong"
        elif total >= _THRESHOLD_MODERATE:
            level = "moderate"
        elif total >= _THRESHOLD_WEAK:
            level = "weak"
        else:
            level = "none"
        return AccumulationResult(
            symbol=symbol, name=name, total_score=round(total, 1),
            level=level, fund_flow_score=round(f1, 1),
            chip_dist_score=round(f2, 1), vol_price_score=round(f3, 1),
            turnover_score=round(f4, 1), large_order_score=round(f5, 1),
        )

    def scan(
        self,
        stocks: List[Dict[str, Any]],
    ) -> List[AccumulationResult]:
        """Batch scan multiple stocks.

        Each item in stocks should have keys:
        symbol, name, bars, fund_flow (opt), chip_data (opt), large_orders (opt).
        """
        results: List[AccumulationResult] = []
        for s in stocks:
            r = self.detect(
                bars=s.get("bars", []),
                fund_flow=s.get("fund_flow"),
                chip_data=s.get("chip_data"),
                large_orders=s.get("large_orders"),
                symbol=s.get("symbol", ""),
                name=s.get("name", ""),
            )
            if r.total_score >= self.min_confidence:
                results.append(r)
        results.sort(key=lambda r: r.total_score, reverse=True)
        return results

    # -- dimension scorers (each returns 0-20) --

    @staticmethod
    def _score_fund_flow(flow: List[Dict[str, Any]]) -> float:
        if not flow:
            return 10.0
        nets = [f.get("main_net", 0) for f in flow]
        positive_days = sum(1 for n in nets if n > 0)
        ratio = positive_days / len(nets) if nets else 0
        total_net = sum(nets)
        score = ratio * 12.0
        if total_net > 0:
            score += 8.0
        elif total_net > -abs(sum(abs(n) for n in nets)) * 0.1:
            score += 4.0
        return min(20.0, score)

    @staticmethod
    def _score_chip_distribution(chip: Dict[str, Any]) -> float:
        if not chip:
            return 10.0
        score = 10.0
        benefit = chip.get("benefit_pct", 50)
        if benefit < 15:
            score += 6.0
        elif benefit < 30:
            score += 3.0
        elif benefit > 90:
            score -= 5.0
        conc = chip.get("concentration", 0.1)
        if conc < 0.06:
            score += 4.0
        elif conc < 0.10:
            score += 2.0
        return max(0, min(20.0, score))

    @staticmethod
    def _score_volume_price(bars: List[Dict[str, Any]]) -> float:
        if len(bars) < 3:
            return 10.0
        up_vol, down_vol = 0.0, 0.0
        for i in range(1, len(bars)):
            v = bars[i].get("volume", 0)
            if bars[i].get("close", 0) >= bars[i - 1].get("close", 0):
                up_vol += v
            else:
                down_vol += v
        if down_vol <= 0:
            return 15.0
        ratio = up_vol / down_vol
        if ratio >= 2.0:
            return 18.0
        if ratio >= 1.5:
            return 15.0
        if ratio >= 1.0:
            return 12.0
        return 6.0

    @staticmethod
    def _score_turnover(bars: List[Dict[str, Any]]) -> float:
        rates = [b.get("turnover_rate", 0) for b in bars if b.get("turnover_rate")]
        if not rates:
            return 10.0
        avg = sum(rates) / len(rates)
        if 1.0 <= avg <= 5.0:
            return 16.0
        if 5.0 < avg <= 10.0:
            return 12.0
        if avg < 1.0:
            return 8.0
        return 5.0

    @staticmethod
    def _score_large_orders(orders: List[Dict[str, Any]]) -> float:
        if not orders:
            return 10.0
        total_buy = sum(o.get("buy_amount", 0) for o in orders)
        total_sell = sum(o.get("sell_amount", 0) for o in orders)
        if total_sell <= 0:
            return 16.0 if total_buy > 0 else 10.0
        ratio = total_buy / total_sell
        if ratio >= 2.0:
            return 18.0
        if ratio >= 1.3:
            return 14.0
        if ratio >= 1.0:
            return 11.0
        return 5.0
