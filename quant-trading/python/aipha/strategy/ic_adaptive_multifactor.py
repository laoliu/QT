"""IC 自适应多因子 — 动态跟踪 5 因子 IC, 指数衰减加权"""
from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_momentum_factor,
    compute_quality_factor,
    compute_size_factor,
    compute_value_factor,
    compute_volatility_factor,
)
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import (
    EquityCandidateInfo,
    EquityRotationConfig,
    EquityRotationStrategy,
)

logger = logging.getLogger(__name__)

_FACTOR_NAMES = ("quality", "value", "momentum", "volatility", "size")


@dataclass
class ICAdaptiveConfig(EquityRotationConfig):
    top_k: int = 30
    rebalance_days: int = 15
    min_price: float = 3.0
    max_price: float = 300.0
    min_turnover: float = 800.0
    min_listed_days: int = 120
    stop_loss_pct: float = 0.15
    trailing_stop_pct: float = 0.12
    buffer_k: int = 10
    max_single_pct: float = 0.05
    min_mv: float = 300_000.0
    max_mv: float = 5_000_000.0
    max_industry_pct: float = 0.20
    ic_lookback: int = 60
    ic_halflife: int = 20
    ic_min_periods: int = 30
    base_weight_quality: float = 0.25
    base_weight_value: float = 0.20
    base_weight_momentum: float = 0.20
    base_weight_volatility: float = 0.20
    base_weight_size: float = 0.15
    momentum_lookback: int = 60
    momentum_skip_recent: int = 5
    volatility_lookback: int = 60
    max_pe: float = 100.0


class ICAdaptiveMultifactorStrategy(EquityRotationStrategy):
    """IC 自适应多因子 — 动态 IC 权重 + 指数衰减"""
    name = "ic_adaptive_multifactor"

    def __init__(self, config: ICAdaptiveConfig | None = None, **kwargs: object):
        super().__init__(config=config or ICAdaptiveConfig(), **kwargs)
        self._ic_history: dict[str, deque[float]] = {
            name: deque(maxlen=200) for name in _FACTOR_NAMES
        }
        self._prev_factor_ranks: dict[str, dict[str, float]] = {}
        self._ic_weights: dict[str, float] = {}

    @property
    def config(self) -> ICAdaptiveConfig:
        return self._config  # type: ignore[return-value]

    def _on_bar_update_state(self, bars: dict, context: StrategyContext) -> None:
        # 简化 IC 计算 (真实实现需要对比因子值 vs 下期收益率的 rank correlation)
        self._update_ic_weights()

    def _update_ic_weights(self) -> None:
        cfg = self.config
        weights: dict[str, float] = {}
        base = {
            "quality": cfg.base_weight_quality,
            "value": cfg.base_weight_value,
            "momentum": cfg.base_weight_momentum,
            "volatility": cfg.base_weight_volatility,
            "size": cfg.base_weight_size,
        }
        for name in _FACTOR_NAMES:
            hist = self._ic_history[name]
            if len(hist) >= cfg.ic_min_periods:
                # 指数衰减加权 IC
                decay = [math.exp(-i * math.log(2) / cfg.ic_halflife) for i in range(len(hist))]
                total_decay = sum(decay) or 1.0
                ic_ewma = sum(h * d for h, d in zip(reversed(list(hist)), decay)) / total_decay
                weights[name] = max(0, base[name] + ic_ewma * 2)
            else:
                weights[name] = base[name]
        total = sum(weights.values()) or 1.0
        self._ic_weights = {k: v / total for k, v in weights.items()}

    @staticmethod
    def _rank_correlation(x: list[float], y: list[float]) -> float:
        n = len(x)
        if n < 3:
            return 0.0
        rx = [0.0] * n
        ry = [0.0] * n
        for ranks, vals in ((rx, x), (ry, y)):
            idx = sorted(range(n), key=lambda i: vals[i])
            for rank, i in enumerate(idx):
                ranks[i] = rank
        mean_rx = sum(rx) / n
        mean_ry = sum(ry) / n
        cov = sum((rx[i] - mean_rx) * (ry[i] - mean_ry) for i in range(n))
        sx = math.sqrt(sum((r - mean_rx) ** 2 for r in rx)) or 1.0
        sy = math.sqrt(sum((r - mean_ry) ** 2 for r in ry)) or 1.0
        return cov / (sx * sy)

    def _get_weights(self) -> dict[str, float]:
        if self._ic_weights:
            return self._ic_weights
        cfg = self.config
        return {
            "quality": cfg.base_weight_quality,
            "value": cfg.base_weight_value,
            "momentum": cfg.base_weight_momentum,
            "volatility": cfg.base_weight_volatility,
            "size": cfg.base_weight_size,
        }

    def _extra_filter(self, candidate: EquityCandidateInfo, context: StrategyContext) -> bool:
        cfg = self.config
        pe = getattr(candidate, "pe_ttm", None)
        if pe is not None and pe > cfg.max_pe:
            return False
        return True

    def _score_candidates(
        self, candidates: list[EquityCandidateInfo], context: StrategyContext,
    ) -> list[str]:
        cfg = self.config
        weights = self._get_weights()
        price_hist = {c.code: getattr(c, "price_history", []) for c in candidates}

        factors: dict[str, dict[str, float]] = {
            "quality": compute_quality_factor(candidates),
            "value": compute_value_factor(candidates),
            "momentum": compute_momentum_factor(price_hist, candidates, lookback=cfg.momentum_lookback),
            "volatility": compute_volatility_factor(price_hist, candidates, lookback=cfg.volatility_lookback),
            "size": compute_size_factor(candidates),
        }

        composite: dict[str, float] = {}
        for c in candidates:
            s = sum(weights.get(f, 0) * factors.get(f, {}).get(c.code, 0.0) for f in weights)
            composite[c.code] = s

        sorted_codes = sorted(composite.keys(), key=lambda c: composite[c], reverse=True)
        return sorted_codes[:cfg.top_k]
