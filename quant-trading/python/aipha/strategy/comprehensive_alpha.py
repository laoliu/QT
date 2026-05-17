"""综合 Alpha 多因子策略

将价值、质量、动量、低波、股息、盈利稳定性、估值分位、Piotroski
等多维度因子综合打分，追求穿越牛熊的稳健超额收益。

与 BalancedMultiFactorStrategy 的区别：
- 默认启用 8 个因子维度（balanced 默认 4 个）
- 质量维度细分为 ROE + Piotroski + 应计盈余 + 盈利稳定性
- 价值维度引入估值历史分位（区分"一直便宜"与"变便宜了"）
- 股息作为标准因子而非可选

Alpha 来源假说：
- 多因子分散降低单因子周期性衰退风险
- Piotroski + 应计盈余提升质量维度区分度
- 估值分位捕捉"均值回归"机会
- 股息 + 低波提供下行保护

继承 EquityRotationStrategy 基类，复用预过滤/止损/调仓逻辑。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_accruals_quality_factor,
    compute_dividend_factor,
    compute_earnings_growth_factor,
    compute_earnings_stability_factor,
    compute_momentum_factor,
    compute_piotroski_factor,
    compute_quality_factor,
    compute_valuation_percentile_factor,
    compute_volatility_factor,
    compute_value_factor,
)
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import (
    EquityCandidateInfo,
    EquityRotationConfig,
    EquityRotationStrategy,
)
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class ComprehensiveAlphaConfig(EquityRotationConfig):
    """综合 Alpha 策略配置

    八因子体系：
    - 价值（PE）15% + 估值分位 10% = 25%
    - 质量（ROE）10% + Piotroski 8% + 应计盈余 5% + 盈利稳定性 5% = 28%
    - 动量 15%
    - 低波动 12%
    - 股息率 10%
    - 盈利增长 10%
    """

    # 选股范围
    top_k: int = 30
    rebalance_days: int = 20
    min_mv: float = 300_000.0       # 30 亿
    max_mv: float = 5_000_000.0     # 500 亿
    min_price: float = 5.0
    max_price: float = 200.0
    min_turnover: float = 1000.0
    min_listed_days: int = 120
    buffer_k: int = 10

    # 止损
    stop_loss_pct: float = 0.15
    trailing_stop_pct: float = 0.10

    # 加权
    inverse_vol_weighting: bool = True
    max_single_pct: float = 0.05
    cash_reserve_pct: float = 0.03

    # --- 八因子权重（合计 = 1.0）---
    weight_value: float = 0.15          # 低 PE
    weight_valuation_pct: float = 0.10  # 估值历史分位
    weight_quality: float = 0.10        # 高 ROE
    weight_piotroski: float = 0.08      # Piotroski F-Score
    weight_accruals: float = 0.05       # 应计盈余质量
    weight_stability: float = 0.05      # 盈利稳定性
    weight_momentum: float = 0.15       # 中期动量
    weight_volatility: float = 0.12     # 低波动
    weight_dividend: float = 0.10       # 高股息
    weight_growth: float = 0.10         # 盈利增长

    # --- 因子参数 ---
    value_metric: str = "pe_ttm"
    quality_metric: str = "roe"
    momentum_lookback: int = 60
    momentum_skip_recent: int = 5
    momentum_multi_period: bool = True
    volatility_lookback: int = 60
    piotroski_min_dims: int = 5
    valuation_min_history: int = 20
    stability_min_periods: int = 3


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------

@StrategyRegistry().register("comprehensive_alpha")
class ComprehensiveAlphaStrategy(EquityRotationStrategy):
    """综合 Alpha 多因子轮动策略

    八因子体系打分：价值 + 估值分位 + ROE + Piotroski + 应计盈余
    + 盈利稳定性 + 动量 + 低波动 + 股息 + 盈利增长。

    与 balanced_multifactor 区别在于：质量维度细分为 4 个子因子，
    引入估值历史百分位和盈利增长，因子覆盖度更全面。
    """

    name = "comprehensive_alpha"

    def __init__(
        self,
        config: ComprehensiveAlphaConfig | None = None,
        **kwargs,
    ):
        super().__init__()
        self._config = config or ComprehensiveAlphaConfig(**kwargs)

    @property
    def config(self) -> ComprehensiveAlphaConfig:
        """策略配置。"""
        return self._config

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """八因子加权排名融合。"""
        cfg = self._config
        if not candidates:
            return []

        weights = {
            "value": cfg.weight_value,
            "valuation_pct": cfg.weight_valuation_pct,
            "quality": cfg.weight_quality,
            "piotroski": cfg.weight_piotroski,
            "accruals": cfg.weight_accruals,
            "stability": cfg.weight_stability,
            "momentum": cfg.weight_momentum,
            "volatility": cfg.weight_volatility,
            "dividend": cfg.weight_dividend,
            "growth": cfg.weight_growth,
        }

        static_factors: list[tuple[str, dict[str, float]]] = []
        dynamic_factors: list[tuple[str, dict[str, float]]] = []

        # --- 静态因子 ---
        if weights["value"] > 0.001:
            f = compute_value_factor(candidates, metric=cfg.value_metric)
            static_factors.append(("value", f))

        if weights["valuation_pct"] > 0.001:
            f = compute_valuation_percentile_factor(
                candidates, metric=cfg.value_metric,
                min_history=cfg.valuation_min_history,
            )
            static_factors.append(("valuation_pct", f))

        if weights["quality"] > 0.001:
            f = compute_quality_factor(candidates, metric=cfg.quality_metric)
            static_factors.append(("quality", f))

        if weights["piotroski"] > 0.001:
            f = compute_piotroski_factor(
                candidates, min_dimensions=cfg.piotroski_min_dims,
            )
            static_factors.append(("piotroski", f))

        if weights["accruals"] > 0.001:
            f = compute_accruals_quality_factor(candidates)
            static_factors.append(("accruals", f))

        if weights["stability"] > 0.001:
            f = compute_earnings_stability_factor(
                candidates, min_periods=cfg.stability_min_periods,
            )
            static_factors.append(("stability", f))

        if weights["dividend"] > 0.001:
            f = compute_dividend_factor(candidates)
            static_factors.append(("dividend", f))

        if weights["growth"] > 0.001:
            f = compute_earnings_growth_factor(candidates)
            static_factors.append(("growth", f))

        # --- 动态因子（需要价格历史）---
        if weights["momentum"] > 0.001:
            f = compute_momentum_factor(
                self._price_history, candidates,
                lookback=cfg.momentum_lookback,
                skip_recent=cfg.momentum_skip_recent,
                multi_period=cfg.momentum_multi_period,
            )
            dynamic_factors.append(("momentum", f))

        if weights["volatility"] > 0.001:
            f = compute_volatility_factor(
                self._price_history, candidates,
                lookback=cfg.volatility_lookback,
            )
            dynamic_factors.append(("volatility", f))

        # warmup 保护
        _VALID_THRESHOLD = 0.30
        usable_dynamic: list[tuple[str, dict[str, float]]] = []
        for name, fv in dynamic_factors:
            n_valid = sum(1 for v in fv.values() if math.isfinite(v))
            if len(fv) > 0 and n_valid / len(fv) >= _VALID_THRESHOLD:
                usable_dynamic.append((name, fv))

        all_usable = static_factors + usable_dynamic
        if not all_usable:
            return []

        # 重新归一化权重
        usable_names = {name for name, _ in all_usable}
        raw_w = {name: weights.get(name, 0) for name in usable_names}
        w_total = sum(raw_w.values())
        if w_total <= 0:
            return []
        norm_w = {k: v / w_total for k, v in raw_w.items()}

        scores: dict[str, float] = {}
        for name, fv in all_usable:
            self.rank_normalize(candidates, fv, norm_w[name], scores)

        return self.rank_sort(scores)
