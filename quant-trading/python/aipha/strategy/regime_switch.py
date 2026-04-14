"""市场情绪切换策略 — 牛/熊/中性三种情绪下动态调整因子权重"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_momentum_factor,
    compute_quality_factor,
    compute_size_factor,
    compute_value_factor,
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
class RegimeSwitchConfig(EquityRotationConfig):
    top_k: int = 30
    rebalance_days: int = 10
    min_price: float = 3.0
    max_price: float = 200.0
    min_turnover: float = 500.0
    min_listed_days: int = 120
    stop_loss_pct: float = 0.18
    trailing_stop_pct: float = 0.15
    buffer_k: int = 8
    max_single_pct: float = 0.05
    min_mv: float = 300_000.0
    max_mv: float = float("inf")
    max_industry_pct: float = 0.20
    # 牛市权重
    bull_weight_momentum: float = 0.50
    bull_weight_value: float = 0.10
    bull_weight_quality: float = 0.25
    bull_weight_size: float = 0.15
    # 熊市权重
    bear_weight_momentum: float = 0.10
    bear_weight_value: float = 0.50
    bear_weight_quality: float = 0.25
    bear_weight_size: float = 0.15
    # 中性权重
    neutral_weight_momentum: float = 0.25
    neutral_weight_value: float = 0.30
    neutral_weight_quality: float = 0.25
    neutral_weight_size: float = 0.20
    # 情绪判断
    ma_short: int = 20
    ma_long: int = 60
    momentum_lookback: int = 60
    bull_momentum_threshold: float = 0.08
    bear_momentum_threshold: float = -0.06
    max_pe: float = 80.0
    _index_history: list[float] = field(default_factory=list)
    _current_regime: str = "NEUTRAL"


class RegimeSwitchStrategy(EquityRotationStrategy):
    """市场情绪切换 — 根据指数动量判断牛/熊/中性, 动态切换因子权重"""
    name = "regime_switch"

    def __init__(self, config: RegimeSwitchConfig | None = None, **kwargs: object):
        super().__init__(config=config or RegimeSwitchConfig(), **kwargs)
        self._index_hist: list[float] = []
        self._regime = "NEUTRAL"

    @property
    def config(self) -> RegimeSwitchConfig:
        return self._config  # type: ignore[return-value]

    def _detect_regime(self) -> str:
        cfg = self.config
        hist = self._index_hist
        if len(hist) < cfg.ma_long:
            return "NEUTRAL"
        ma_s = sum(hist[-cfg.ma_short:]) / cfg.ma_short
        ma_l = sum(hist[-cfg.ma_long:]) / cfg.ma_long
        if ma_l <= 0:
            return "NEUTRAL"
        mom = (ma_s - ma_l) / ma_l
        if mom > cfg.bull_momentum_threshold:
            return "BULL"
        if mom < cfg.bear_momentum_threshold:
            return "BEAR"
        return "NEUTRAL"

    def _on_bar_update_state(self, bars: dict[str, "Bar"], context: StrategyContext) -> None:
        # 用所有标的均价作为市场代理
        if bars:
            avg = sum(b.close for b in bars.values()) / len(bars)
            self._index_hist.append(avg)
        self._regime = self._detect_regime()

    def _extra_filter(self, candidate: EquityCandidateInfo, context: StrategyContext) -> bool:
        pe = getattr(candidate, "pe_ttm", None)
        if pe is not None and pe > self.config.max_pe:
            return False
        return True

    def _score_candidates(
        self, candidates: list[EquityCandidateInfo], context: StrategyContext,
    ) -> list[str]:
        cfg = self.config
        regime = self._regime
        if regime == "BULL":
            w_mom, w_val, w_qual, w_sz = (
                cfg.bull_weight_momentum, cfg.bull_weight_value,
                cfg.bull_weight_quality, cfg.bull_weight_size,
            )
        elif regime == "BEAR":
            w_mom, w_val, w_qual, w_sz = (
                cfg.bear_weight_momentum, cfg.bear_weight_value,
                cfg.bear_weight_quality, cfg.bear_weight_size,
            )
        else:
            w_mom, w_val, w_qual, w_sz = (
                cfg.neutral_weight_momentum, cfg.neutral_weight_value,
                cfg.neutral_weight_quality, cfg.neutral_weight_size,
            )

        price_hist = {c.code: getattr(c, "price_history", []) for c in candidates}
        mom = compute_momentum_factor(price_hist, candidates, lookback=cfg.momentum_lookback)
        val = compute_value_factor(candidates)
        qual = compute_quality_factor(candidates)
        sz = compute_size_factor(candidates)

        composite: dict[str, float] = {}
        for c in candidates:
            composite[c.code] = (
                w_mom * mom.get(c.code, 0.0)
                + w_val * val.get(c.code, 0.0)
                + w_qual * qual.get(c.code, 0.0)
                + w_sz * sz.get(c.code, 0.0)
            )
        sorted_codes = sorted(composite.keys(), key=lambda c: composite[c], reverse=True)
        return sorted_codes[:cfg.top_k]
