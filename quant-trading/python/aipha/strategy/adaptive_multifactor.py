"""自适应多因子策略 — IC 动态权重 + 市场温度择时"""
from __future__ import annotations

import logging
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_debt_factor,
    compute_dividend_factor,
    compute_earnings_growth_factor,
    compute_momentum_factor,
    compute_piotroski_factor,
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
class AdaptiveMultiFactorConfig(EquityRotationConfig):
    top_k: int = 25
    rebalance_days: int = 15
    min_mv: float = 300_000.0
    max_mv: float = 5_000_000.0
    min_price: float = 5.0
    max_price: float = 200.0
    min_turnover: float = 2000.0
    min_listed_days: int = 120
    buffer_k: int = 8
    buffer_k_defensive: int = 15
    stop_loss_pct: float = 0.12
    trailing_stop_pct: float = 0.08
    inverse_vol_weighting: bool = True
    max_single_pct: float = 0.06
    cash_reserve_pct: float = 0.03
    max_industry_pct: float = 0.20
    # 8 因子基础权重
    base_weight_value: float = 0.15
    base_weight_quality: float = 0.15
    base_weight_momentum: float = 0.15
    base_weight_volatility: float = 0.15
    base_weight_earnings_growth: float = 0.10
    base_weight_dividend: float = 0.10
    base_weight_piotroski: float = 0.10
    base_weight_debt: float = 0.10
    # IC 自适应
    ic_lookback: int = 20
    ic_weight_alpha: float = 0.7
    ic_min_abs: float = 0.02
    ic_decay_halflife: int = 10
    # 市场温度
    temperature_enabled: bool = True
    temp_trend_weight: float = 0.4
    temp_vol_weight: float = 0.3
    temp_breadth_weight: float = 0.3
    temp_ma_short: int = 20
    temp_ma_mid: int = 60
    temp_ma_long: int = 120
    temp_vol_short: int = 20
    temp_vol_long: int = 100
    min_position_pct: float = 0.30
    defensive_threshold: float = 0.30
    offensive_threshold: float = 0.70
    # 防守模式权重
    defensive_weight_volatility: float = 0.40
    defensive_weight_value: float = 0.25
    defensive_weight_dividend: float = 0.20
    defensive_weight_quality: float = 0.15
    # 通用参数
    momentum_lookback: int = 60
    momentum_skip_recent: int = 5
    momentum_multi_period: bool = True
    volatility_lookback: int = 60
    value_metric: str = "pe_ttm"
    quality_metric: str = "roe"


class AdaptiveMultiFactorStrategy(EquityRotationStrategy):
    """自适应多因子 — 8 因子 + IC 动态权重 + 市场温度仓位管理"""
    name = "adaptive_multifactor"

    def __init__(self, config: AdaptiveMultiFactorConfig | None = None, **kwargs: object):
        super().__init__(config=config or AdaptiveMultiFactorConfig(), **kwargs)
        self._index_prices: deque[float] = deque(maxlen=300)
        self._market_temperature: float = 0.5
        self._factor_ic_history: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=100))
        self._factor_returns: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=100))
        self._breadth_history: deque[float] = deque(maxlen=100)
        self._prev_factor_scores: dict[str, dict[str, float]] = {}

    @property
    def _index_hist(self) -> deque[float]:
        return self._index_prices

    @_index_hist.setter
    def _index_hist(self, val: deque[float]) -> None:
        self._index_prices = val

    @property
    def _ic_history(self) -> dict[str, deque[float]]:
        return self._factor_ic_history

    @_ic_history.setter
    def _ic_history(self, val: dict[str, deque[float]]) -> None:
        self._factor_ic_history = val

    @property
    def _temperature(self) -> float:
        return self._market_temperature

    @_temperature.setter
    def _temperature(self, val: float) -> None:
        self._market_temperature = val

    @property
    def config(self) -> AdaptiveMultiFactorConfig:
        return self._config  # type: ignore[return-value]

    def _on_bar_update_state(self, bars: dict[str, "Bar"], context: StrategyContext) -> None:
        if self.config.temperature_enabled:
            self._update_market_temperature(bars, context)

    def _update_factor_ic(self, factor_name: str, factor_values: dict[str, float], next_returns: dict[str, float]) -> None:
        """Compute IC between factor values and next-period returns."""
        common = [s for s in factor_values if s in next_returns]
        if len(common) < 10:
            return
        fv = [factor_values[s] for s in common]
        rv = [next_returns[s] for s in common]
        # Spearman rank correlation approximation via Pearson on ranks
        n = len(common)
        mean_f = sum(fv) / n
        mean_r = sum(rv) / n
        cov = sum((fv[i] - mean_f) * (rv[i] - mean_r) for i in range(n))
        var_f = sum((f - mean_f) ** 2 for f in fv)
        var_r = sum((r - mean_r) ** 2 for r in rv)
        denom = math.sqrt(var_f * var_r)
        ic = cov / denom if denom > 0 else 0.0
        self._factor_ic_history[factor_name].append(ic)

    def _update_market_temperature(self, bars: dict[str, "Bar"], context: StrategyContext) -> None:
        # 用所有股票均价作为指数代理
        if bars:
            avg_price = sum(b.close for b in bars.values()) / len(bars)
            self._index_hist.append(avg_price)
        trend = self._calc_trend_signal()
        vol = self._calc_volatility_signal()
        breadth = self._calc_breadth_signal()
        cfg = self.config
        self._market_temperature = (
            cfg.temp_trend_weight * trend
            + cfg.temp_vol_weight * vol
            + cfg.temp_breadth_weight * breadth
        )

    def _calc_trend_signal(self) -> float:
        cfg = self.config
        hist = list(self._index_hist)
        if len(hist) < cfg.temp_ma_long:
            return 0.5
        ma_short = sum(hist[-cfg.temp_ma_short:]) / cfg.temp_ma_short
        ma_long = sum(hist[-cfg.temp_ma_long:]) / cfg.temp_ma_long
        if ma_long <= 0:
            return 0.5
        return max(0, min(1, 0.5 + (ma_short - ma_long) / ma_long * 5))

    def _calc_volatility_signal(self) -> float:
        hist = list(self._index_hist)
        cfg = self.config
        if len(hist) < cfg.temp_vol_long:
            return 0.5
        recent = hist[-cfg.temp_vol_short:]
        returns = [(recent[i] - recent[i - 1]) / recent[i - 1]
                    for i in range(1, len(recent)) if recent[i - 1] > 0]
        if not returns:
            return 0.5
        vol = math.sqrt(sum(r ** 2 for r in returns) / len(returns))
        # 低波动 → 高信号 (乐观)
        return max(0, min(1, 1.0 - vol * 50))

    def _calc_breadth_signal(self) -> float:
        if not self._breadth_history:
            return 0.5
        return sum(self._breadth_history) / len(self._breadth_history)

    def _get_ic_weights(self) -> dict[str, float]:
        cfg = self.config
        base = {
            "value": cfg.base_weight_value,
            "quality": cfg.base_weight_quality,
            "momentum": cfg.base_weight_momentum,
            "volatility": cfg.base_weight_volatility,
            "earnings_growth": cfg.base_weight_earnings_growth,
            "dividend": cfg.base_weight_dividend,
            "piotroski": cfg.base_weight_piotroski,
            "debt": cfg.base_weight_debt,
        }
        # IC 自适应调整
        alpha = cfg.ic_weight_alpha
        for name, hist in self._ic_history.items():
            if name in base and len(hist) >= 5:
                avg_ic = sum(hist) / len(hist)
                if abs(avg_ic) > cfg.ic_min_abs:
                    base[name] = base[name] * (1 - alpha) + alpha * max(0, avg_ic * 5)
        # 归一化
        total = sum(base.values()) or 1.0
        return {k: v / total for k, v in base.items()}

    def _get_position_scale(self) -> float:
        cfg = self.config
        if not cfg.temperature_enabled:
            return 1.0
        if self._market_temperature < cfg.defensive_threshold:
            return cfg.min_position_pct
        if self._market_temperature > cfg.offensive_threshold:
            return 1.0
        # 线性插值
        frac = (self._market_temperature - cfg.defensive_threshold) / (
            cfg.offensive_threshold - cfg.defensive_threshold
        )
        return cfg.min_position_pct + frac * (1.0 - cfg.min_position_pct)

    def _is_defensive_mode(self) -> bool:
        return self._market_temperature < self.config.defensive_threshold

    def _score_candidates(
        self, candidates: list[EquityCandidateInfo], context: StrategyContext,
    ) -> list[str]:
        cfg = self.config
        price_hist = {c.code: getattr(c, "price_history", []) for c in candidates}

        if self._is_defensive_mode():
            # 防守模式: 重配低波动 + 价值 + 股息
            vol = compute_volatility_factor(price_hist, candidates, lookback=cfg.volatility_lookback)
            val = compute_value_factor(candidates, metric=cfg.value_metric)
            div = compute_dividend_factor(candidates)
            qual = compute_quality_factor(candidates, metric=cfg.quality_metric)
            composite: dict[str, float] = {}
            for c in candidates:
                composite[c.code] = (
                    cfg.defensive_weight_volatility * vol.get(c.code, 0.0)
                    + cfg.defensive_weight_value * val.get(c.code, 0.0)
                    + cfg.defensive_weight_dividend * div.get(c.code, 0.0)
                    + cfg.defensive_weight_quality * qual.get(c.code, 0.0)
                )
        else:
            weights = self._get_ic_weights()
            factors: dict[str, dict[str, float]] = {
                "value": compute_value_factor(candidates, metric=cfg.value_metric),
                "quality": compute_quality_factor(candidates, metric=cfg.quality_metric),
                "momentum": compute_momentum_factor(price_hist, candidates, lookback=cfg.momentum_lookback),
                "volatility": compute_volatility_factor(price_hist, candidates, lookback=cfg.volatility_lookback),
                "earnings_growth": compute_earnings_growth_factor(candidates),
                "dividend": compute_dividend_factor(candidates),
                "piotroski": compute_piotroski_factor(candidates),
                "debt": compute_debt_factor(candidates),
            }
            composite = {}
            for c in candidates:
                s = 0.0
                for fname, w in weights.items():
                    s += w * factors.get(fname, {}).get(c.code, 0.0)
                composite[c.code] = s

        sorted_codes = sorted(composite.keys(), key=lambda c: composite[c], reverse=True)
        return sorted_codes[:cfg.top_k]
