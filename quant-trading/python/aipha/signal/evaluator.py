"""信号评估器 — 信号质量分析 (统计/收益/衰减)"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

import numpy as np


class SignalType(Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass
class Signal:
    symbol: str
    signal_type: SignalType
    timestamp: datetime
    price: float
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SignalStats:
    total_signals: int = 0
    buy_signals: int = 0
    sell_signals: int = 0
    hold_signals: int = 0
    unique_symbols: int = 0
    avg_confidence: float = 0.0
    signals_per_day: float = 0.0


@dataclass
class ReturnStats:
    avg_return: float = 0.0
    median_return: float = 0.0
    std_return: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    best_return: float = 0.0
    worst_return: float = 0.0
    total_pnl: float = 0.0


@dataclass
class DecayStats:
    holding_periods: list[int] = field(default_factory=list)
    avg_returns: list[float] = field(default_factory=list)
    win_rates: list[float] = field(default_factory=list)
    optimal_period: int = 1


@dataclass
class SignalEvaluationReport:
    signal_stats: SignalStats
    return_stats: ReturnStats
    decay_stats: DecayStats
    by_symbol: dict[str, ReturnStats] = field(default_factory=dict)

    def summary(self) -> str:
        s, r, d = self.signal_stats, self.return_stats, self.decay_stats
        lines = [
            "=" * 50, "信号评估报告", "=" * 50, "",
            f"总信号: {s.total_signals} (买{s.buy_signals}/卖{s.sell_signals}) 覆盖{s.unique_symbols}标的",
            f"日均: {s.signals_per_day:.1f}信号  平均置信: {s.avg_confidence:.2%}", "",
            f"平均收益: {r.avg_return:.2%}  胜率: {r.win_rate:.2%}  盈亏比: {r.profit_factor:.2f}",
            f"最佳: {r.best_return:.2%}  最差: {r.worst_return:.2%}", "",
            f"最优持有期: {d.optimal_period}天",
        ]
        for i, p in enumerate(d.holding_periods):
            ret = d.avg_returns[i] if i < len(d.avg_returns) else 0
            wr = d.win_rates[i] if i < len(d.win_rates) else 0
            lines.append(f"  {p}日: 收益={ret:.2%} 胜率={wr:.2%}")
        return "\n".join(lines)


class SignalEvaluator:
    """信号评估器 — 分析信号质量、收益、衰减"""

    def __init__(self, signals: list[Signal], price_data: dict[str, dict[datetime, float]],
                 holding_periods: list[int] | None = None):
        self.signals = signals
        self.price_data = price_data
        self.holding_periods = holding_periods or [1, 3, 5, 10, 20]

    def evaluate(self) -> SignalEvaluationReport:
        stats = self._signal_stats()
        ret_stats, by_sym = self._return_stats()
        decay = self._decay_stats()
        return SignalEvaluationReport(stats, ret_stats, decay, by_sym)

    def _signal_stats(self) -> SignalStats:
        if not self.signals:
            return SignalStats()
        buy = sum(1 for s in self.signals if s.signal_type == SignalType.BUY)
        sell = sum(1 for s in self.signals if s.signal_type == SignalType.SELL)
        hold = sum(1 for s in self.signals if s.signal_type == SignalType.HOLD)
        syms = set(s.symbol for s in self.signals)
        avg_conf = sum(s.confidence for s in self.signals) / len(self.signals)
        ts = sorted(s.timestamp for s in self.signals)
        days = max((ts[-1] - ts[0]).days, 1) if len(ts) >= 2 else 1
        return SignalStats(len(self.signals), buy, sell, hold, len(syms), avg_conf, len(self.signals) / days)

    def _calc_returns(self, period: int) -> dict[tuple[str, datetime], float]:
        results = {}
        for sig in self.signals:
            if sig.signal_type == SignalType.HOLD or sig.symbol not in self.price_data:
                continue
            prices = self.price_data[sig.symbol]
            entry = prices.get(sig.timestamp)
            if entry is None:
                continue
            exit_t = sig.timestamp + timedelta(days=period)
            exit_p = self._nearest_price(prices, exit_t)
            if exit_p is None:
                continue
            ret = (exit_p - entry) / entry if sig.signal_type == SignalType.BUY else (entry - exit_p) / entry
            results[(sig.symbol, sig.timestamp)] = ret
        return results

    def _nearest_price(self, prices: dict[datetime, float], target: datetime, max_d: int = 5):
        if target in prices:
            return prices[target]
        for d in range(1, max_d + 1):
            t = target + timedelta(days=d)
            if t in prices:
                return prices[t]
        return None

    def _return_stats(self) -> tuple[ReturnStats, dict[str, ReturnStats]]:
        rets = self._calc_returns(1)
        if not rets:
            return ReturnStats(), {}
        arr = np.array(list(rets.values()))
        overall = self._stats_from_array(arr)
        by_sym: dict[str, list[float]] = {}
        for (sym, _), r in rets.items():
            by_sym.setdefault(sym, []).append(r)
        return overall, {s: self._stats_from_array(np.array(v)) for s, v in by_sym.items()}

    @staticmethod
    def _stats_from_array(arr: np.ndarray) -> ReturnStats:
        if len(arr) == 0:
            return ReturnStats()
        wins = arr[arr > 0]
        losses = arr[arr < 0]
        tw = float(np.sum(wins)) if len(wins) else 0
        tl = abs(float(np.sum(losses))) if len(losses) else 0
        pf = tw / tl if tl > 0 else (float("inf") if tw > 0 else 0)
        return ReturnStats(
            avg_return=float(np.mean(arr)), median_return=float(np.median(arr)),
            std_return=float(np.std(arr, ddof=1)) if len(arr) > 1 else 0,
            win_rate=len(wins) / len(arr), profit_factor=pf,
            avg_win=float(np.mean(wins)) if len(wins) else 0,
            avg_loss=float(np.mean(losses)) if len(losses) else 0,
            best_return=float(np.max(arr)), worst_return=float(np.min(arr)),
            total_pnl=float(np.sum(arr)),
        )

    def _decay_stats(self) -> DecayStats:
        avg_rets, wrs = [], []
        for p in self.holding_periods:
            rets = self._calc_returns(p)
            if rets:
                arr = np.array(list(rets.values()))
                avg_rets.append(float(np.mean(arr)))
                wrs.append(len(arr[arr > 0]) / len(arr))
            else:
                avg_rets.append(0.0)
                wrs.append(0.0)
        opt = self.holding_periods[int(np.argmax(avg_rets))] if avg_rets else 1
        return DecayStats(self.holding_periods, avg_rets, wrs, opt)


def evaluate_signals(signals, price_data, holding_periods=None) -> SignalEvaluationReport:
    return SignalEvaluator(signals, price_data, holding_periods).evaluate()
