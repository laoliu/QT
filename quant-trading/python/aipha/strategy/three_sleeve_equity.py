"""月出渐分明复刻 — 三袖 A 股轮动策略

三袖结构：
- Sleeve 1 (micro-cap value): 微盘价值，按 BP(1/PB) 排序
- Sleeve 2 (large-cap value): 大盘低估值，按 EP(1/PE) 排序
- Sleeve 3 (quality-growth): 中盘质量成长，ROE + 成长 + EP 复合打分

每个 sleeve 独立选股后合并为最终持仓列表。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from aipha.factors.equity_factors import (
    compute_bp_factor,
    compute_earnings_growth_factor,
    compute_ep_factor,
    compute_quality_factor,
)
from aipha.strategy.equity_rotation_base import (
    EquityCandidateInfo,
    EquityRotationConfig,
    EquityRotationStrategy,
)
from aipha.strategy.registry import StrategyRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class ThreeSleeveConfig(EquityRotationConfig):
    """三袖策略配置。"""

    # --- 袖位分配（stock count per sleeve） ---
    sleeve_micro_k: int = 15      # 微盘价值持仓数
    sleeve_large_k: int = 8       # 大盘低估值持仓数
    sleeve_mid_k: int = 7         # 中盘质量成长持仓数

    # --- 市值划分阈值（万元） ---
    micro_cap_max: float = 300_000.0    # 微盘上限 30 亿
    large_cap_min: float = 5_000_000.0  # 大盘下限 500 亿

    # --- 因子参数 ---
    # Sleeve 3 (quality-growth) 权重
    qg_weight_roe: float = 0.35
    qg_weight_growth: float = 0.35
    qg_weight_ep: float = 0.30

    # 质量/价值指标
    ep_metric: str = "pe_ttm"
    roe_metric: str = "roe"
    growth_metric: str = "dt_netprofit_yoy"

    def __post_init__(self) -> None:
        """确保 top_k 与袖位分配一致。"""
        self.top_k = self.sleeve_micro_k + self.sleeve_large_k + self.sleeve_mid_k


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------

@StrategyRegistry().register("three_sleeve_equity")
class ThreeSleeveEquityStrategy(EquityRotationStrategy):
    """三袖 A 股轮动策略（月出渐分明复刻）。"""

    def __init__(self, config: ThreeSleeveConfig | None = None, **kwargs) -> None:
        if config is None and kwargs:
            valid = {
                k: v for k, v in kwargs.items()
                if k in ThreeSleeveConfig.__dataclass_fields__
            }
            config = ThreeSleeveConfig(**valid)
        super().__init__()
        self._cfg = config or ThreeSleeveConfig()

    @property
    def config(self) -> ThreeSleeveConfig:
        return self._cfg

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: "StrategyContext",
    ) -> list[str]:
        """三袖独立选股后合并。"""
        if not candidates:
            return []
        cfg = self._cfg

        # 按市值分池
        micro_pool = [c for c in candidates if c.circ_mv <= cfg.micro_cap_max]
        large_pool = [c for c in candidates if c.circ_mv >= cfg.large_cap_min]
        mid_pool = [
            c for c in candidates
            if cfg.micro_cap_max < c.circ_mv < cfg.large_cap_min
        ]

        # Sleeve 1: micro-cap value (BP ranking)
        micro_selected = self._select_micro(micro_pool, cfg.sleeve_micro_k)

        # Sleeve 2: large-cap value (EP ranking)
        large_selected = self._select_large(large_pool, cfg.sleeve_large_k)

        # Sleeve 3: quality-growth mid-cap (ROE + Growth + EP)
        mid_selected = self._select_quality_growth(mid_pool, cfg.sleeve_mid_k)

        # 合并（保持顺序：micro → large → mid）
        result = micro_selected + large_selected + mid_selected
        return result

    def _select_micro(
        self, pool: list[EquityCandidateInfo], k: int,
    ) -> list[str]:
        """微盘价值选股：按 BP (1/PB) 降序。"""
        if not pool or k <= 0:
            return []
        bp_vals = compute_bp_factor(pool)
        scored = [
            (sym, v) for sym, v in bp_vals.items()
            if math.isfinite(v)
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s for s, _ in scored[:k]]

    def _select_large(
        self, pool: list[EquityCandidateInfo], k: int,
    ) -> list[str]:
        """大盘低估值选股：按 EP (1/PE) 降序。"""
        if not pool or k <= 0:
            return []
        ep_vals = compute_ep_factor(pool, metric=self._cfg.ep_metric)
        scored = [
            (sym, v) for sym, v in ep_vals.items()
            if math.isfinite(v)
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s for s, _ in scored[:k]]

    def _select_quality_growth(
        self, pool: list[EquityCandidateInfo], k: int,
    ) -> list[str]:
        """中盘质量成长选股：ROE + 盈利增速 + EP 复合排名。"""
        if not pool or k <= 0:
            return []
        cfg = self._cfg

        scores: dict[str, float] = {c.symbol: 0.0 for c in pool}

        # ROE quality
        if cfg.qg_weight_roe > 0:
            roe_vals = compute_quality_factor(pool, metric=cfg.roe_metric)
            self.rank_normalize(pool, roe_vals, weight=cfg.qg_weight_roe,
                                scores=scores)

        # Earnings growth
        if cfg.qg_weight_growth > 0:
            growth_vals = compute_earnings_growth_factor(
                pool, field=cfg.growth_metric,
            )
            self.rank_normalize(pool, growth_vals, weight=cfg.qg_weight_growth,
                                scores=scores)

        # EP value
        if cfg.qg_weight_ep > 0:
            ep_vals = compute_ep_factor(pool, metric=cfg.ep_metric)
            self.rank_normalize(pool, ep_vals, weight=cfg.qg_weight_ep,
                                scores=scores)

        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [s for s, _ in sorted_scores[:k]]
