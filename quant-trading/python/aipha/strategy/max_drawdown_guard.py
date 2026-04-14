"""最大回撤防护策略 — 多因子选股 + 回撤动态仓位缩减"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_momentum_factor,
    compute_quality_factor,
    compute_value_factor,
    compute_volatility_factor,
)
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import (
    EquityCandidateInfo,
    EquityRotationConfig,
    EquityRotationStrategy,
)

if TYPE_CHECKING:
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


@dataclass
class MaxDrawdownGuardConfig(EquityRotationConfig):
    top_k: int = 30
    rebalance_days: int = 10
    min_price: float = 3.0
    max_price: float = 200.0
    min_turnover: float = 500.0
    min_listed_days: int = 120
    stop_loss_pct: float = 0.20
    trailing_stop_pct: float = 0.15
    buffer_k: int = 8
    max_single_pct: float = 0.05
    min_mv: float = 300_000.0
    max_mv: float = float("inf")
    max_industry_pct: float = 0.20
    weight_quality: float = 0.25
    weight_value: float = 0.25
    weight_momentum: float = 0.25
    weight_vol: float = 0.25
    dd_start_threshold: float = 0.10
    dd_max_threshold: float = 0.20
    min_position_scale: float = 0.30
    max_pe: float = 80.0
    momentum_lookback: int = 60
    _peak_nav: float = 0.0


class MaxDrawdownGuardStrategy(EquityRotationStrategy):
    """最大回撤防护策略

    动态监控组合回撤, 回撤 > dd_start_threshold 时线性缩仓,
    回撤 > dd_max_threshold 时缩至 min_position_scale。
    """
    name = "max_drawdown_guard"

    def __init__(self, config: MaxDrawdownGuardConfig | None = None, **kwargs: object):
        cfg = config or MaxDrawdownGuardConfig()
        super().__init__(config=cfg, **kwargs)
        self._dd_config = cfg
        self._peak_nav = 0.0
        self._position_scale = 1.0

    @property
    def config(self) -> MaxDrawdownGuardConfig:
        return self._dd_config

    def _on_bar_update_state(self, bars: dict[str, "Bar"], context: StrategyContext) -> None:
        # 更新 peak nav & position scale
        nav = getattr(context, "portfolio_value", None)
        if nav is None:
            return
        self._peak_nav = max(self._peak_nav, nav)
        if self._peak_nav > 0:
            dd = (self._peak_nav - nav) / self._peak_nav
            cfg = self._dd_config
            if dd < cfg.dd_start_threshold:
                self._position_scale = 1.0
            elif dd >= cfg.dd_max_threshold:
                self._position_scale = cfg.min_position_scale
            else:
                # 线性缩放
                frac = (dd - cfg.dd_start_threshold) / (cfg.dd_max_threshold - cfg.dd_start_threshold)
                self._position_scale = 1.0 - frac * (1.0 - cfg.min_position_scale)

    def _extra_filter(self, candidate: EquityCandidateInfo, context: StrategyContext) -> bool:
        cfg = self._dd_config
        pe = getattr(candidate, "pe_ttm", None)
        if pe is not None and pe > cfg.max_pe:
            return False
        mv = getattr(candidate, "circ_mv", None)
        if mv is not None:
            if mv < cfg.min_mv or mv > cfg.max_mv:
                return False
        return True

    def _score_candidates(
        self, candidates: list[EquityCandidateInfo], context: StrategyContext,
    ) -> list[str]:
        if not candidates:
            return []
        cfg = self._dd_config
        price_hist: dict[str, list[float]] = {}
        for c in candidates:
            hist = getattr(c, "price_history", None)
            if hist:
                price_hist[c.code] = hist

        quality = compute_quality_factor(candidates)
        value = compute_value_factor(candidates)
        momentum = compute_momentum_factor(price_hist, candidates, lookback=cfg.momentum_lookback)
        vol = compute_volatility_factor(price_hist, candidates, lookback=cfg.momentum_lookback)

        # 合成得分
        from aipha.factors.equity_factors import compute_overreaction_composite
        composite = compute_overreaction_composite(
            {"quality": quality, "value": value, "momentum": momentum, "vol": vol},
            {"quality": cfg.weight_quality, "value": cfg.weight_value,
             "momentum": cfg.weight_momentum, "vol": cfg.weight_vol},
        )
        sorted_codes = sorted(composite.keys(), key=lambda c: composite[c], reverse=True)
        return sorted_codes[:cfg.top_k]
