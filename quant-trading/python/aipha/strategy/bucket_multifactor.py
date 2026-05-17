"""A 股桶式多因子轮动策略 (Bucket Multi-Factor)

R70/R71 路线图 Phase 2 产出。

策略逻辑:
1. 将 Universe 按市值分为 2~4 个桶 (large / mid / small / micro)
2. 每个桶内独立选用最适配的因子组合打分
3. 各桶输出独立的 top_k 列表，按配置权重合并为组合
4. 桶间的因子权重差异化:
   - 大盘桶: Quality + Low_Vol 为主 (机构定价效率高，alpha 靠稳健)
   - 中盘桶: Value + Quality + Momentum 均衡
   - 小盘桶: Reversal + Size + Earnings_Growth (散户交易行为驱动)

Alpha 来源假说:
- 不同市值段的定价效率与投资者结构差异显著
- 单一因子组合在跨市值段表现不稳定 (R66/R70 分析)
- 桶内独立打分可捕获市值段特有 alpha
- 桶间分散化降低了对单一市值风格的暴露

继承 EquityRotationStrategy，覆盖 _score_candidates。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_accruals_quality_factor,
    compute_debt_factor,
    compute_dividend_factor,
    compute_dividend_persistence_factor,
    compute_earnings_growth_factor,
    compute_earnings_stability_factor,
    compute_fcfy_factor,
    compute_momentum_factor,
    compute_peg_factor,
    compute_piotroski_factor,
    compute_quality_factor,
    compute_size_factor,
    compute_str_factor,
    compute_turnover_factor,
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
# 桶配置
# ---------------------------------------------------------------------------

@dataclass
class BucketSpec:
    """单个市值桶的规格。"""
    name: str = "mid"
    mv_min: float = 0.0        # 市值下限 (万元)
    mv_max: float = float("inf")
    top_k: int = 10            # 桶内选股数
    weight_alloc: float = 0.33  # 桶在组合中的权重
    factor_weights: dict[str, float] = field(default_factory=dict)


@dataclass
class BucketMultiFactorConfig(EquityRotationConfig):
    """桶式多因子策略配置。

    默认三桶: large(30%) + mid(40%) + small(30%)
    桶规格通过 buckets 列表传入，若为空则使用默认三桶。
    """

    # 覆盖基类默认
    top_k: int = 30            # 组合总股数（各桶 top_k 之和）
    rebalance_days: int = 10   # 双周调仓
    min_price: float = 3.0
    max_price: float = 200.0
    min_turnover: float = 500.0
    min_listed_days: int = 120
    buffer_k: int = 8
    stop_loss_pct: float = 0.15
    trailing_stop_pct: float = 0.10
    inverse_vol_weighting: bool = True
    max_single_pct: float = 0.05
    cash_reserve_pct: float = 0.03

    # 桶规格（JSON 序列化为 list[dict]）
    # 如果通过 YAML 传入，解析为 BucketSpec 列表
    bucket_specs: list[dict] = field(default_factory=list)

    # 动量/波动率参数
    momentum_lookback: int = 60
    momentum_skip_recent: int = 5
    momentum_multi_period: bool = True
    volatility_lookback: int = 60
    str_lookback: int = 5

    # 价值/质量指标
    value_metric: str = "pe_ttm"
    quality_metric: str = "roe"

    def get_buckets(self) -> list[BucketSpec]:
        """解析桶规格。若未配置则返回默认三桶。"""
        if self.bucket_specs:
            return [BucketSpec(**spec) for spec in self.bucket_specs]
        return self._default_buckets()

    @staticmethod
    def _default_buckets() -> list[BucketSpec]:
        """默认三桶配置 (R70 推荐)。"""
        return [
            BucketSpec(
                name="large",
                mv_min=1_000_000.0,   # 100 亿
                mv_max=float("inf"),
                top_k=8,
                weight_alloc=0.30,
                factor_weights={
                    "quality": 0.30, "value": 0.20,
                    "volatility": 0.20, "dividend": 0.15,
                    "piotroski": 0.10, "debt": 0.05,
                },
            ),
            BucketSpec(
                name="mid",
                mv_min=200_000.0,     # 20 亿
                mv_max=1_000_000.0,   # 100 亿
                top_k=14,
                weight_alloc=0.40,
                factor_weights={
                    "value": 0.25, "quality": 0.20,
                    "momentum": 0.20, "volatility": 0.15,
                    "peg": 0.10, "fcfy": 0.10,
                },
            ),
            BucketSpec(
                name="small",
                mv_min=0.0,
                mv_max=200_000.0,     # 20 亿
                top_k=8,
                weight_alloc=0.30,
                factor_weights={
                    "str": 0.25, "size": 0.20,
                    "earnings_growth": 0.20, "momentum": 0.15,
                    "turnover": 0.10, "accruals_quality": 0.10,
                },
            ),
        ]


@StrategyRegistry().register("bucket_multifactor")
class BucketMultiFactorStrategy(EquityRotationStrategy):
    """A 股桶式多因子轮动策略。"""

    name = "bucket_multifactor"

    def __init__(self, config: BucketMultiFactorConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or BucketMultiFactorConfig(**kwargs)

    @property
    def config(self) -> BucketMultiFactorConfig:
        return self._config

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """桶式多因子打分: 分桶 → 桶内排名 → 合并。"""
        if not candidates:
            return []

        cfg = self._config
        buckets = cfg.get_buckets()

        # 1. 分桶
        bucket_candidates: dict[str, list[EquityCandidateInfo]] = {
            b.name: [] for b in buckets
        }
        for cand in candidates:
            mv = cand.circ_mv or 0.0
            for b in buckets:
                if b.mv_min <= mv < b.mv_max:
                    bucket_candidates[b.name].append(cand)
                    break

        # 2. 桶内独立打分
        bucket_ranked: dict[str, list[str]] = {}
        for b in buckets:
            cands_b = bucket_candidates[b.name]
            if not cands_b:
                bucket_ranked[b.name] = []
                continue
            ranked = self._score_bucket(
                cands_b, b.factor_weights, context,
            )
            bucket_ranked[b.name] = ranked[:b.top_k]

        # 3. 合并: 用桶内排名位次生成全局分数
        final_scores: dict[str, float] = {}
        for b in buckets:
            ranked = bucket_ranked[b.name]
            if not ranked:
                continue
            n = len(ranked)
            for i, sym in enumerate(ranked):
                # 桶内排名分 = (n - i) / n ∈ (0, 1]
                rank_score = (n - i) / n
                final_scores[sym] = b.weight_alloc * rank_score

        # 4. 按分数降序返回
        sorted_syms = sorted(
            final_scores, key=lambda s: final_scores[s], reverse=True,
        )
        return sorted_syms

    # ------------------------------------------------------------------
    # 桶内因子打分
    # ------------------------------------------------------------------

    # 因子计算函数映射
    _STATIC_FACTOR_FN = {
        "value": "_f_value",
        "quality": "_f_quality",
        "dividend": "_f_dividend",
        "earnings_growth": "_f_earnings_growth",
        "piotroski": "_f_piotroski",
        "earnings_stability": "_f_earnings_stability",
        "valuation_percentile": "_f_valuation_pct",
        "accruals_quality": "_f_accruals",
        "debt": "_f_debt",
        "turnover": "_f_turnover",
        "size": "_f_size",
        "peg": "_f_peg",
        "fcfy": "_f_fcfy",
        "div_persistence": "_f_div_persistence",
    }
    _DYNAMIC_FACTOR_FN = {
        "momentum": "_f_momentum",
        "volatility": "_f_volatility",
        "str": "_f_str",
    }

    def _score_bucket(
        self,
        cands: list[EquityCandidateInfo],
        factor_weights: dict[str, float],
        context: StrategyContext,
    ) -> list[str]:
        """桶内打分: 按因子权重排名融合。"""
        # 归一化权重
        total_w = sum(factor_weights.values())
        if total_w <= 0:
            return [c.symbol for c in cands]
        nw = {k: v / total_w for k, v in factor_weights.items() if v > 0.001}

        # 分静态/动态因子
        static_fvs: list[tuple[str, dict[str, float]]] = []
        dynamic_fvs: list[tuple[str, dict[str, float]]] = []

        for fname, w in nw.items():
            if fname in self._STATIC_FACTOR_FN:
                fn = getattr(self, self._STATIC_FACTOR_FN[fname])
                fv = fn(cands)
                static_fvs.append((fname, fv))
            elif fname in self._DYNAMIC_FACTOR_FN:
                fn = getattr(self, self._DYNAMIC_FACTOR_FN[fname])
                fv = fn(cands)
                dynamic_fvs.append((fname, fv))

        # warmup 检查: 动态因子有效率 < 30% 时跳过
        usable_dynamic = []
        for name, fv in dynamic_fvs:
            n_valid = sum(1 for v in fv.values() if math.isfinite(v))
            if len(fv) > 0 and n_valid / len(fv) >= 0.30:
                usable_dynamic.append((name, fv))

        all_usable = static_fvs + usable_dynamic
        if not all_usable:
            return [c.symbol for c in cands]

        # 重新归一化可用因子权重
        usable_names = {n for n, _ in all_usable}
        rw = {n: nw.get(n, 0) for n in usable_names}
        rw_total = sum(rw.values())
        if rw_total <= 0:
            return [c.symbol for c in cands]
        rw = {k: v / rw_total for k, v in rw.items()}

        scores: dict[str, float] = {}
        for name, fv in all_usable:
            self.rank_normalize(cands, fv, rw[name], scores)

        return self.rank_sort(scores)

    # ------------------------------------------------------------------
    # 因子包装方法
    # ------------------------------------------------------------------

    def _f_value(self, cands):
        return compute_value_factor(cands, metric=self._config.value_metric)

    def _f_quality(self, cands):
        return compute_quality_factor(cands, metric=self._config.quality_metric)

    def _f_dividend(self, cands):
        return compute_dividend_factor(cands)

    def _f_earnings_growth(self, cands):
        return compute_earnings_growth_factor(cands)

    def _f_piotroski(self, cands):
        return compute_piotroski_factor(cands)

    def _f_earnings_stability(self, cands):
        return compute_earnings_stability_factor(cands)

    def _f_valuation_pct(self, cands):
        return compute_valuation_percentile_factor(cands)

    def _f_accruals(self, cands):
        return compute_accruals_quality_factor(cands)

    def _f_debt(self, cands):
        return compute_debt_factor(cands)

    def _f_turnover(self, cands):
        return compute_turnover_factor(cands)

    def _f_size(self, cands):
        return compute_size_factor(cands)

    def _f_peg(self, cands):
        return compute_peg_factor(cands)

    def _f_fcfy(self, cands):
        return compute_fcfy_factor(cands)

    def _f_div_persistence(self, cands):
        return compute_dividend_persistence_factor(cands)

    def _f_momentum(self, cands):
        return compute_momentum_factor(
            self._price_history, cands,
            lookback=self._config.momentum_lookback,
            skip_recent=self._config.momentum_skip_recent,
            multi_period=self._config.momentum_multi_period,
        )

    def _f_volatility(self, cands):
        return compute_volatility_factor(
            self._price_history, cands,
            lookback=self._config.volatility_lookback,
        )

    def _f_str(self, cands):
        return compute_str_factor(
            self._price_history, cands,
            lookback=self._config.str_lookback,
        )
