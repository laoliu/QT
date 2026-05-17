"""可转债次新小盘复合策略

Alpha 来源：
1. new_bond（次新效应 13.40%）× small_size（小规模效应 11.88%）的因子交集
2. 小规模次新债 = 低关注度 + IPO 定价偏差 → 定价效率最低的子集
3. 上市初期抛压结束后，低发行规模的转债估值修复更快

策略逻辑：
1. 筛选上市天数在 [min_age, max_age] 区间内的次新债
2. 在次新候选中，按 issue_size（发行规模，静态字段无前视偏差）升序排列
3. 持有最小的 top_k 只等权
4. 个券自然"毕业"或发行规模超标时轮出

对数据要求：list_date + issue_size 字段
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
    days_since_listed,
)
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass
class NewSmallConfig(CBRotationConfig):
    """次新小盘复合策略配置"""

    top_k: int = 10

    # 次新筛选
    min_age_days: int = 5      # 最少上市天数（避免首日异常）
    max_age_days: int = 90     # 最大上市天数

    # 规模筛选
    max_issue_size: float = 50000.0   # 最大发行规模（万元）
    min_issue_size: float = 3000.0    # 最小发行规模（万元），防极端微盘

    # 次新债到期不是关注点
    min_days_to_maturity: int = 0

    min_turnover: float = 30.0

    # 自适应降级：当次新候选不足时，max_age_days 自动放宽到此值
    fallback_max_age_days: int = 0  # 0=不启用, >0 时为降级上限


@StrategyRegistry().register("new_small")
class NewSmallStrategy(CBRotationStrategy):
    """次新小盘复合策略

    在次新债中精选发行规模最小的标的，叠加两大高 alpha 因子。
    使用 issue_size（静态字段）避免 remain_size 前视偏差。
    """

    name = "new_small"

    def __init__(self, config: NewSmallConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or NewSmallConfig(**kwargs)
        self._effective_max_age: int = self._config.max_age_days

    @property
    def config(self) -> NewSmallConfig:
        return self._config

    def _on_start_extra(self) -> None:
        """策略启动时重置有效最大上市天数为配置默认值。"""
        self._effective_max_age = self._config.max_age_days

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        """只保留上市天数在 [min_age, effective_max_age] 内的次新债。

        当启用自适应降级（fallback_max_age_days > 0）且候选不足时，
        effective_max_age 会被自动放宽，避免长期空仓。
        """
        cfg = self._config
        if not candidate.list_date:
            return False
        age = days_since_listed(candidate.list_date, context.current_time)
        return cfg.min_age_days <= age <= self._effective_max_age

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """按发行规模升序（最小优先），取 top_k。

        自适应降级：跟踪候选数量，下次调仓自动调整 effective_max_age。
        """
        cfg = self._config
        # 注意：自适应降级已移至基类 _filter_with_fallback()，此处不再重复

        candidates.sort(key=lambda c: (c.issue_size, c.symbol))
        return [c.symbol for c in candidates[:cfg.top_k]]


@dataclass
class NewSmallEnsembleConfig(NewSmallConfig):
    """次新小盘复合策略参数集成配置。

    通过多组近优参数（top_k/max_age_days/max_issue_size 等）生成多个候选集合，
    再用投票/排名聚合生成最终目标池，从而平滑参数的"悬崖效应"。
    """

    variants: list[dict] = field(default_factory=list)
    weights: list[float] = field(default_factory=list)
    aggregation: str = "reciprocal_rank"


@StrategyRegistry().register("new_small_ensemble")
class NewSmallEnsembleStrategy(CBRotationStrategy):
    """次新小盘复合策略（参数集成版）。

    目标：降低 new_small 对 top_k / max_age_days / max_issue_size 等参数的悬崖敏感性。
    方法：用多组近优参数生成多个排名列表，再按倒数排名加权投票聚合。
    """

    name = "new_small_ensemble"

    def __init__(self, config: NewSmallEnsembleConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or NewSmallEnsembleConfig(**kwargs)

    @property
    def config(self) -> NewSmallEnsembleConfig:
        return self._config

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        """保持与 NewSmallStrategy 一致的次新过滤（使用最宽松的 max_age）。"""
        cfg = self._config
        if not candidate.list_date:
            return False

        # 使用所有 variant 中最宽松的 max_age（确保候选池足够大）
        max_age = cfg.max_age_days
        for v in cfg.variants:
            max_age = max(max_age, int(v.get("max_age_days", cfg.max_age_days)))

        age = days_since_listed(candidate.list_date, context.current_time)
        return cfg.min_age_days <= age <= max_age

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """多组近优参数排名聚合，生成最终目标池。"""
        cfg = self._config

        # 无 variants 时退化为普通 new_small 逻辑
        if not cfg.variants:
            candidates.sort(key=lambda c: (c.issue_size, c.symbol))
            return [c.symbol for c in candidates[: cfg.top_k]]

        if cfg.weights and len(cfg.weights) != len(cfg.variants):
            raise ValueError(
                "weights length must match variants length when provided"
            )

        weights = cfg.weights or [1.0] * len(cfg.variants)
        symbol_score: dict[str, float] = {}
        symbol_issue_size: dict[str, float] = {}

        for variant, weight in zip(cfg.variants, weights, strict=True):
            v_top_k = int(variant.get("top_k", cfg.top_k))
            v_max_price = float(variant.get("max_price", cfg.max_price))
            v_min_age = int(variant.get("min_age_days", cfg.min_age_days))
            v_max_age = int(variant.get("max_age_days", cfg.max_age_days))
            v_min_issue_size = float(variant.get("min_issue_size", cfg.min_issue_size))
            v_max_issue_size = float(variant.get("max_issue_size", cfg.max_issue_size))

            filtered = []
            for c in candidates:
                if c.close <= 0 or c.close > v_max_price:
                    continue
                if not c.list_date:
                    continue
                age = days_since_listed(c.list_date, context.current_time)
                if not (v_min_age <= age <= v_max_age):
                    continue
                if not (v_min_issue_size <= c.issue_size <= v_max_issue_size):
                    continue
                filtered.append(c)

            filtered.sort(key=lambda c: (c.issue_size, c.symbol))

            for rank, c in enumerate(filtered[:v_top_k]):
                if cfg.aggregation != "reciprocal_rank":
                    raise ValueError(f"unsupported aggregation: {cfg.aggregation!r}")
                score = weight * (1.0 / (rank + 1.0))
                symbol_score[c.symbol] = symbol_score.get(c.symbol, 0.0) + score
                symbol_issue_size.setdefault(c.symbol, c.issue_size)

        ranked = sorted(
            symbol_score.items(),
            key=lambda kv: (-kv[1], symbol_issue_size.get(kv[0], float("inf")), kv[0]),
        )

        return [s for s, _ in ranked[: cfg.top_k]]

