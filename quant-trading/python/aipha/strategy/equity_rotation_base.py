"""多因子轮动策略基类 — 提供 rank-normalize + 加权评分通用逻辑

所有 AlphQ 风格的 A 股策略继承此类，只需实现:
  1. config 属性 (TopK/权重/过滤阈值)
  2. _score_candidates() 或 _compute_factors()
"""
from __future__ import annotations

from abc import abstractmethod
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from aipha.strategy.base import Strategy, StrategyContext, OrderIntent, IntentType
from aipha.domain.models import Bar, Side


def days_since_listed(list_date_str: str, now: datetime | None = None) -> int:
    """Calculate days since listing. If now is None, use datetime.now()."""
    if now is None:
        now = datetime.now()
    try:
        if "-" in list_date_str:
            ld = datetime.strptime(list_date_str[:10], "%Y-%m-%d")
        else:
            ld = datetime.strptime(list_date_str[:8], "%Y%m%d")
        return (now - ld).days
    except (ValueError, TypeError):
        return 0


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
    lot_size: int = 100
    min_price: float = 0.0
    max_price: float = float("inf")
    stop_loss_pct: float = 0.15
    exclude_st: bool = True


@dataclass
class CandidateInfo:
    symbol: str
    close: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def __init__(self, symbol: str, close: float, metadata: dict[str, Any] | None = None, **kwargs: Any):
        self.symbol = symbol
        self.close = close
        self.metadata = dict(metadata) if metadata else {}
        # Store extra kwargs (circ_mv, turnover_rate, amount, etc.) in metadata
        for k, v in kwargs.items():
            if k not in self.metadata:
                self.metadata[k] = v

    @property
    def code(self) -> str:
        """Backward-compatible alias for symbol."""
        return self.symbol

    @property
    def price(self) -> float:
        """Backward-compatible alias for close."""
        return self.close

    def __getattr__(self, name: str) -> Any:
        """Fallback: look up missing attributes in metadata."""
        if name.startswith("_"):
            raise AttributeError(name)
        md = self.__dict__.get("metadata", {})
        if name in md:
            return md[name]
        raise AttributeError(f"'CandidateInfo' object has no attribute {name!r}")


# Backward-compatible alias used by many strategy modules
EquityCandidateInfo = CandidateInfo


def rank_normalize(values: list[float | None], fill: str = "median") -> list[float]:
    """Rank -> [0,1] 百分位，NaN 填充策略 (median/zero)。相同值取平均排名。"""
    arr = np.array(values, dtype=float)
    valid = ~np.isnan(arr)
    n_valid = int(valid.sum())
    if n_valid == 0:
        return [0.5] * len(arr)
    ranks = np.empty_like(arr)
    ranks[:] = np.nan
    valid_vals = arr[valid]
    order = np.argsort(valid_vals)
    lin = np.linspace(0, 1, n_valid)
    r = np.empty(n_valid)
    r[order] = lin
    # Average ranks for tied values
    sorted_vals = valid_vals[order]
    i = 0
    while i < n_valid:
        j = i + 1
        while j < n_valid and sorted_vals[j] == sorted_vals[i]:
            j += 1
        if j > i + 1:
            avg = np.mean(lin[i:j])
            for k in range(i, j):
                r[order[k]] = avg
        i = j
    ranks[valid] = r
    fill_val = 0.5 if fill == "median" else 0.0
    ranks[~valid] = fill_val
    return ranks.tolist()


class EquityRotationStrategy(Strategy):
    """多因子轮动策略基类。"""

    def __init__(self, config: EquityRotationConfig | None = None, **kwargs: object) -> None:
        if config is not None:
            self._config = config
        # Apply kwargs to config if they match config fields
        if kwargs and hasattr(self, '_config'):
            import dataclasses
            field_names = {f.name for f in dataclasses.fields(self._config)}
            for k, v in kwargs.items():
                if k in field_names:
                    object.__setattr__(self._config, k, v)
        self._bar_count = 0
        self._last_rebalance = 0
        self._current_targets: dict[str, float] = {}  # symbol -> weight
        self._price_history: dict[str, deque] = {}
        self._peak_prices: dict[str, float] = {}
        self._timing_scale: float = 1.0
        self._vol_scale: float = 1.0

    @property
    def _max_prices(self) -> dict[str, float]:
        """Alias for _peak_prices."""
        return self._peak_prices

    @_max_prices.setter
    def _max_prices(self, value: dict) -> None:
        self._peak_prices = value

    @property
    @abstractmethod
    def config(self) -> EquityRotationConfig:
        ...

    def on_start(self) -> None:
        self._bar_count = 0
        self._last_rebalance = 0
        self._current_targets.clear()
        self._price_history.clear()
        self._peak_prices.clear()
        self._timing_scale = 1.0
        self._vol_scale = 1.0

    def on_bar(self, bars: dict[str, Bar], context: StrategyContext) -> list[OrderIntent]:
        self._bar_count += 1
        cfg = self.config

        # 更新价格历史
        for sym, bar in bars.items():
            if sym not in self._price_history:
                self._price_history[sym] = deque(maxlen=120)
            self._price_history[sym].append(bar.close)

        # Hook for subclass state updates
        self._on_bar_update_state(bars, context)

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

    def _build_candidates(self, bars: dict[str, Bar], context: StrategyContext | None = None) -> list[CandidateInfo]:
        cfg = self.config
        now = getattr(context, "current_time", None) if context else None
        out = []
        for sym, bar in bars.items():
            meta = getattr(bar, "metadata", {}) or {}
            # Filter suspended
            if getattr(bar, "is_suspended", False) or meta.get("is_suspended", False):
                continue
            # Filter by volume/turnover
            vol = getattr(bar, "volume", 0) or 0
            amount = meta.get("amount", vol)
            if amount <= cfg.min_turnover:
                continue
            # Filter by price
            close = bar.close
            if close < cfg.min_price or close > cfg.max_price:
                continue
            # Filter ST
            if cfg.exclude_st:
                name = meta.get("name", "") or meta.get("stock_name", "") or ""
                if "ST" in name.upper():
                    continue
            # Filter by listing days
            list_date = meta.get("list_date")
            if list_date and cfg.min_listed_days > 0:
                try:
                    from datetime import datetime as _dt
                    ld = _dt.strptime(str(list_date)[:8], "%Y%m%d")
                    ref = now or _dt.now()
                    age = (ref - ld).days
                    if age < cfg.min_listed_days:
                        continue
                except (ValueError, TypeError):
                    pass
            out.append(CandidateInfo(
                symbol=sym, close=close,
                metadata=meta,
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
        for sym, pos in context.positions.items():
            if pos <= 0 or sym not in bars:
                continue
            price = bars[sym].close
            # Fixed stop loss
            entry = self._entry_prices.get(sym)
            if entry and entry > 0 and cfg.stop_loss_pct > 0:
                pnl = (price - entry) / entry
                if pnl < -cfg.stop_loss_pct:
                    intents.append(OrderIntent(
                        symbol=sym, side=Side.SELL, qty=pos,
                        intent_type=IntentType.ORDER,
                        reason=f"止损 {pnl:.2%}",
                    ))
                    continue
            # Trailing stop
            if cfg.trailing_stop is not None:
                peak = self._peak_prices.get(sym, price)
                if price > peak:
                    self._peak_prices[sym] = price
                    peak = price
                drawdown = 1 - price / peak if peak > 0 else 0
                if drawdown >= cfg.trailing_stop:
                    intents.append(OrderIntent(
                        symbol=sym, side=Side.SELL, qty=pos,
                        intent_type=IntentType.ORDER,
                        reason=f"追踪止损 {drawdown:.2%}",
                    ))
        return intents

    # Alias for backward compat
    _check_stop_loss = _check_stops

    def _calc_momentum(self, symbol: str, lookback: int = 20, skip: int = 1) -> float | None:
        hist = self._price_history.get(symbol)
        if hist is None or len(hist) < lookback + skip:
            return 0.0
        current = hist[-1 - skip]
        past = hist[-lookback - skip]
        return current / past - 1.0 if past > 0 else 0.0

    def _calc_volatility(self, symbol: str, window: int = 20) -> float | None:
        hist = self._price_history.get(symbol)
        if hist is None or len(hist) < window + 1:
            return 0.0
        prices = list(hist)[-window - 1:]
        rets = [prices[i] / prices[i - 1] - 1 for i in range(1, len(prices))]
        return float(np.std(rets)) if rets else 0.0

    def _update_history(self, bars: dict[str, Bar], context: StrategyContext | None = None) -> None:
        """Update price history from bars (backward-compatible helper)."""
        for sym, bar in bars.items():
            if sym not in self._price_history:
                self._price_history[sym] = deque(maxlen=120)
            self._price_history[sym].append(bar.close)

    def _on_bar_update_state(self, bars: dict[str, Bar], context: StrategyContext) -> None:
        """Hook for subclass state updates. Called during on_bar."""
        pass

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        """Override in subclasses to add custom filtering. Default: accept all."""
        return True

    def _filter_universe(
        self,
        candidates_or_bars: list[CandidateInfo] | dict[str, Bar],
        context: StrategyContext,
    ) -> list[CandidateInfo]:
        """Filter candidates using config and _extra_filter.
        
        Accepts either a list of CandidateInfo or a dict of bars.
        """
        if isinstance(candidates_or_bars, dict):
            candidates = self._build_candidates(candidates_or_bars, context)
        else:
            candidates = candidates_or_bars
        return [c for c in candidates if self._extra_filter(c, context)]

    @property
    def _entry_prices(self) -> dict[str, float]:
        """Current entry price tracking (peak prices as proxy)."""
        return self._peak_prices

    @_entry_prices.setter
    def _entry_prices(self, value: dict) -> None:
        self._peak_prices = value

    def delta_exposure(self, *args, **kwargs) -> float:
        """Return target vs current exposure delta. Default: 0.0."""
        return 0.0

    # Public aliases for _calc_* methods
    calc_momentum = _calc_momentum
    calc_volatility = _calc_volatility
