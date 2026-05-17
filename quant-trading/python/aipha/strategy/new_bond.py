"""可转债次新债效应策略

Alpha 来源：
1. 新上市转债定价常有偏差（IPO 折价/溢价自然衰减）
2. 上市初期波动率较大，做市商/套利者参与少 → 定价效率低
3. 次新债被动卖盘（打新套现）消化后有回升空间

策略逻辑：
1. 筛选上市天数在 [min_age, max_age] 区间内的可转债
2. 在次新区间内按价格升序排列（低价优先 = 打新破发品种）
3. 持有 top_k 只等权
4. 随着时间推移，个券自然"毕业"（超过 max_age），自动轮出

对数据要求：需要 list_date 字段（已在 metadata 中）
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
class NewBondConfig(CBRotationConfig):
    """次新债策略配置"""

    top_k: int = 15
    min_age_days: int = 5      # 最少上市天数（避免新债首日异常）
    max_age_days: int = 90     # 最大上市天数（超过不算次新）
    max_price: float = 130.0   # 次新债价格上限（放宽至130，兼顾供给不足年份）

    # 自适应降级：当次新候选不足时，max_age_days 自动放宽到此值
    fallback_max_age_days: int = 0  # 0=不启用, >0 时为降级上限


@StrategyRegistry().register("new_bond")
class NewBondStrategy(CBRotationStrategy):
    """可转债次新债效应策略

    持有上市不久、价格较低的次新转债。
    利用 IPO 后定价偏差和被动卖盘消化后的回升效应。
    """

    name = "new_bond"

    def __init__(self, config: NewBondConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or NewBondConfig(**kwargs)
        self._effective_max_age: int = self._config.max_age_days

    @property
    def config(self) -> NewBondConfig:
        """策略配置。"""
        return self._config

    def _on_start_extra(self) -> None:
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
        """按价格升序（低价次新优先），取 top_k。

        自适应降级：跟踪候选数量，下次调仓自动调整 effective_max_age。
        """
        cfg = self._config
        # 注意：自适应降级已移至基类 _filter_with_fallback()，此处不再重复

        candidates.sort(key=lambda c: c.close)
        return [c.symbol for c in candidates[:cfg.top_k]]


@dataclass
class NewBondEnsembleConfig(NewBondConfig):
    """次新债策略参数集成配置。

    通过多组近优参数（top_k/max_age_days/max_price 等）生成多个候选集合，
    再用投票/排名聚合生成最终目标池，从而平滑参数的"悬崖效应"。
    """

    variants: list[dict] = field(default_factory=list)
    weights: list[float] = field(default_factory=list)
    aggregation: str = "reciprocal_rank"


@StrategyRegistry().register("new_bond_ensemble")
class NewBondEnsembleStrategy(CBRotationStrategy):
    """次新债效应策略（参数集成版）。

    目标：降低 new_bond 对 top_k / max_age_days / max_price 等参数的悬崖敏感性。
    方法：用多组近优参数生成多个排名列表，再按倒数排名加权投票聚合。
    """

    name = "new_bond_ensemble"

    def __init__(self, config: NewBondEnsembleConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or NewBondEnsembleConfig(**kwargs)

    @property
    def config(self) -> NewBondEnsembleConfig:
        """策略配置。"""
        return self._config

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        """保持与 NewBondStrategy 一致的次新过滤（使用最宽松的 max_age）。"""
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
        cfg = self._config

        # 无 variants 时退化为普通 new_bond 逻辑
        if not cfg.variants:
            candidates.sort(key=lambda c: (c.close, c.symbol))
            return [c.symbol for c in candidates[: cfg.top_k]]

        if cfg.weights and len(cfg.weights) != len(cfg.variants):
            raise ValueError(
                "weights length must match variants length when provided"
            )

        weights = cfg.weights or [1.0] * len(cfg.variants)
        symbol_score: dict[str, float] = {}
        symbol_close: dict[str, float] = {}

        for variant, weight in zip(cfg.variants, weights, strict=True):
            v_top_k = int(variant.get("top_k", cfg.top_k))
            v_max_price = float(variant.get("max_price", cfg.max_price))
            v_min_age = int(variant.get("min_age_days", cfg.min_age_days))
            v_max_age = int(variant.get("max_age_days", cfg.max_age_days))

            filtered = []
            for c in candidates:
                if c.close <= 0 or c.close > v_max_price:
                    continue
                if not c.list_date:
                    continue
                age = days_since_listed(c.list_date, context.current_time)
                if not (v_min_age <= age <= v_max_age):
                    continue
                filtered.append(c)

            filtered.sort(key=lambda c: (c.close, c.symbol))

            for rank, c in enumerate(filtered[:v_top_k]):
                if cfg.aggregation != "reciprocal_rank":
                    raise ValueError(f"unsupported aggregation: {cfg.aggregation!r}")
                score = weight * (1.0 / (rank + 1.0))
                symbol_score[c.symbol] = symbol_score.get(c.symbol, 0.0) + score
                symbol_close.setdefault(c.symbol, c.close)

        ranked = sorted(
            symbol_score.items(),
            key=lambda kv: (-kv[1], symbol_close.get(kv[0], float("inf")), kv[0]),
        )

        return [s for s, _ in ranked[: cfg.top_k]]

