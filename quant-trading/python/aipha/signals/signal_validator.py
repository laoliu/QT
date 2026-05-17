"""Signal quality validator.

Evaluates the quality of trading signals by measuring forward
returns at multiple horizons. Reports accuracy (% correct direction)
and average return per signal type.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

__all__ = ["SignalValidator", "ValidationResult"]

logger = logging.getLogger(__name__)

_DEFAULT_HORIZONS = [1, 3, 5, 10]


@dataclass
class SignalRecord:
    """A single signal to validate."""

    ts_code: str = ""
    signal_date: str = ""
    direction: str = "buy"  # buy | sell
    score: float = 0.0
    price_at_signal: float = 0.0


@dataclass
class ValidationResult:
    """Aggregated validation result for a signal set."""

    total_signals: int = 0
    buy_signals: int = 0
    sell_signals: int = 0
    accuracy: Dict[int, float] = field(default_factory=dict)
    avg_return: Dict[int, float] = field(default_factory=dict)
    details: List[Dict[str, Any]] = field(default_factory=list)


class SignalValidator:
    """Validate signal quality using forward returns.

    Parameters
    ----------
    horizons : list of int
        Forward return horizons in trading days to evaluate.
    """

    def __init__(
        self, horizons: Optional[List[int]] = None,
    ) -> None:
        self.horizons = horizons or _DEFAULT_HORIZONS

    def validate(
        self,
        signals: List[SignalRecord],
        price_data: Dict[str, List[Dict[str, Any]]],
    ) -> ValidationResult:
        """Validate signals against actual price data.

        Parameters
        ----------
        signals : list of SignalRecord
        price_data : dict
            ts_code -> list of {date, close} sorted by date ascending.
        """
        enriched: List[Dict[str, Any]] = []
        for sig in signals:
            prices = price_data.get(sig.ts_code, [])
            fwd = self._forward_returns(sig, prices)
            enriched.append({
                "ts_code": sig.ts_code,
                "signal_date": sig.signal_date,
                "direction": sig.direction,
                "price_at_signal": sig.price_at_signal,
                "forward_returns": fwd,
            })

        result = self._compute_summary(enriched)
        result.details = enriched
        return result

    def _forward_returns(
        self, sig: SignalRecord, prices: List[Dict[str, Any]],
    ) -> Dict[int, Optional[float]]:
        """Compute forward returns for each horizon."""
        fwd: Dict[int, Optional[float]] = {}
        # find signal date index
        idx = -1
        for i, p in enumerate(prices):
            if p.get("date", "") == sig.signal_date:
                idx = i
                break
        if idx < 0 or sig.price_at_signal <= 0:
            for h in self.horizons:
                fwd[h] = None
            return fwd
        for h in self.horizons:
            target_idx = idx + h
            if target_idx < len(prices):
                future_price = prices[target_idx].get("close", 0)
                ret = (future_price / sig.price_at_signal - 1) if sig.price_at_signal > 0 else 0
                fwd[h] = round(ret, 6)
            else:
                fwd[h] = None
        return fwd

    def _compute_summary(
        self, enriched: List[Dict[str, Any]],
    ) -> ValidationResult:
        buy_count = sum(1 for e in enriched if e["direction"] == "buy")
        sell_count = sum(1 for e in enriched if e["direction"] == "sell")
        accuracy: Dict[int, float] = {}
        avg_ret: Dict[int, float] = {}

        for h in self.horizons:
            correct = 0
            total = 0
            rets: List[float] = []
            for e in enriched:
                r = e["forward_returns"].get(h)
                if r is None:
                    continue
                total += 1
                rets.append(r)
                is_buy = e["direction"] == "buy"
                if (is_buy and r > 0) or (not is_buy and r < 0):
                    correct += 1
            accuracy[h] = round(correct / total, 4) if total > 0 else 0.0
            avg_ret[h] = round(sum(rets) / len(rets), 6) if rets else 0.0

        return ValidationResult(
            total_signals=len(enriched),
            buy_signals=buy_count,
            sell_signals=sell_count,
            accuracy=accuracy,
            avg_return=avg_ret,
        )
