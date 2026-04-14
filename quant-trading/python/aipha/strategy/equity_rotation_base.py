"""多因子轮动策略基类 — 提供 rank-normalize + 加权评分通用逻辑

所有 AlphQ 风格的 A 股策略继承此类，只需实现:
  1. config 属性 (TopK/权重/过滤阈值)
  2. _score_candidates() 或 _compute_factors()
"""
from __future__ import annotations

from abc import abstractmethod
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from aipha.strategy.base import Strategy, StrategyContext, OrderIntent, IntentType
from aipha.domain.models import Bar, Side


@dataclass
class EquityRotationConfig:
    top_k: int = 30
    rebalance_days: int = 10
    max_single_weight: float = 0.05
    trailing_stop: float | None = None
    buffer_k: float = 0.2
    min_listed_days: int = 120
    min_mv: float = 0           # 万元
    max_mv: float = float("inf")
    min_turnover: float = 0


@dataclass
class CandidateInfo:
    symbol: str
    close: float
    metadata: dict[str, Any] = field(default_factory=dict)


# Backward-compatible alias used by many strategy modules
EquityCandidateInfo = CandidateInfo


def rank_normalize(values: list[float | None], fill: str = "median") -> list[float]:
    """Rank -> [0,1] 百分位，NaN 填充策略 (median/zero)。"""
    arr = np.array(values, dtype=float)
    valid = ~np.isnan(arr)
    n_valid = valid.sum()
    if n_valid == 0:
        return [0.5] * len(arr)
    ranks = np.empty_like(arr)
    ranks[:] = np.nan
    order = np.argsort(arr[valid])
    r = np.empty(n_valid)
    r[order] = np.linspace(0, 1, n_valid)
    ranks[valid] = r
    fill_val = 0.5 if fill == "median" else 0.0
    ranks[~valid] = fill_val
    return ranks.tolist()


class EquityRotationStrategy(Strategy):
    """多因子轮动策略基类。"""

    def __init__(self) -> None:
        self._bar_count = 0
        self._last_rebalance = 0
        self._current_targets: dict[str, float] = {}  # symbol -> weight
        self._price_history: dict[str, deque] = {}
        self._peak_prices: dict[str, float] = {}

    @property
    @abstractmethod
    def config(self) -> EquityRotationConfig:
        ...

    def on_bar(self, bars: dict[str, Bar], context: StrategyContext) -> list[OrderIntent]:
        self._bar_count += 1
        cfg = self.config

        # 更新价格历史
        for sym, bar in bars.items():
            if sym not in self._price_history:
                self._price_history[sym] = deque(maxlen=120)
            self._price_history[sym].append(bar.close)

        # 止损检查
        intents = self._check_stops(bars, context)

        # 检查是否需要调仓
        if self._bar_count - self._last_rebalance < cfg.rebalance_days:
            return intents

        # 构建候选
        candidates = self._build_candidates(bars)
        if not candidates:
            return intents

        # 打分排序
        scored = self._score_candidates(candidates, context)
        top = scored[: cfg.top_k]

        # 等权分配
        if top:
            w = min(1.0 / len(top), cfg.max_single_weight)
            self._current_targets = {sym: w for sym in top}
        else:
            self._current_targets = {}

        # 生成目标仓位意图
        for sym, weight in self._current_targets.items():
            target_value = context.nav * weight
            price = bars[sym].close if sym in bars else 0
            if price <= 0:
                continue
            target_qty = int(target_value / price / 100) * 100
            current = context.get_position(sym)
            diff = target_qty - current
            if abs(diff) >= 100:
                side = Side.BUY if diff > 0 else Side.SELL
                intents.append(OrderIntent(
                    symbol=sym, side=side, qty=abs(diff),
                    intent_type=IntentType.TARGET_POSITION,
                    reason=f"{self.name}_rebalance",
                ))

        # 清仓不在目标中的持仓
        for sym, pos in context.positions.items():
            if pos > 0 and sym not in self._current_targets:
                intents.append(OrderIntent(
                    symbol=sym, side=Side.SELL, qty=pos,
                    intent_type=IntentType.ORDER,
                    reason=f"{self.name}_exit",
                ))

        self._last_rebalance = self._bar_count
        return intents

    def _build_candidates(self, bars: dict[str, Bar]) -> list[CandidateInfo]:
        cfg = self.config
        out = []
        for sym, bar in bars.items():
            if bar.volume <= cfg.min_turnover:
                continue
            out.append(CandidateInfo(
                symbol=sym, close=bar.close,
                metadata=getattr(bar, "metadata", {}) or {},
            ))
        return out

    @abstractmethod
    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """返回排序后的 symbol 列表 (降序得分)。"""
        ...

    def _check_stops(self, bars: dict[str, Bar], context: StrategyContext) -> list[OrderIntent]:
        cfg = self.config
        intents = []
        if cfg.trailing_stop is None:
            return intents
        for sym, pos in context.positions.items():
            if pos <= 0 or sym not in bars:
                continue
            price = bars[sym].close
            peak = self._peak_prices.get(sym, price)
            if price > peak:
                self._peak_prices[sym] = price
                peak = price
            drawdown = 1 - price / peak if peak > 0 else 0
            if drawdown >= cfg.trailing_stop:
                intents.append(OrderIntent(
                    symbol=sym, side=Side.SELL, qty=pos,
                    intent_type=IntentType.ORDER,
                    reason=f"{self.name}_trailing_stop",
                ))
        return intents

    def _calc_momentum(self, symbol: str, lookback: int = 20, skip: int = 1) -> float | None:
        hist = self._price_history.get(symbol)
        if hist is None or len(hist) < lookback + skip:
            return None
        current = hist[-1 - skip]
        past = hist[-lookback - skip]
        return current / past - 1.0 if past > 0 else None

    def _calc_volatility(self, symbol: str, window: int = 20) -> float | None:
        hist = self._price_history.get(symbol)
        if hist is None or len(hist) < window + 1:
            return None
        prices = list(hist)[-window - 1:]
        rets = [prices[i] / prices[i - 1] - 1 for i in range(1, len(prices))]
        return float(np.std(rets)) if rets else None
