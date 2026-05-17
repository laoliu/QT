"""可转债小规模轮动策略

Alpha 来源：
1. 小规模发行的转债市场关注度低 → 定价偏差更大，alpha 更容易存在
2. 小盘转债流动性溢价补偿 → 长期持有可获超额收益
3. 小发行规模转债的条款博弈价值（下修/回售等）更集中

参考：集思录/宁稳社区"小盘转债"策略、jackluson 开源项目三低转债策略中的规模因子

重要说明：
    使用 issue_size（发行规模，万元）而非 remain_size（剩余规模）作为规模因子。
    原因：remain_size 仅有最新快照数据（非历史时点 PIT），用于回测会产生
    严重前视偏差（大量转股后余额缩小的转债会被错误选入历史时点）。
    issue_size 是静态字段，无前视偏差问题。

策略逻辑：
1. 过滤基本条件（流动性、到期时间、ST、价格）
2. 按发行规模升序排列（越小越优先）
3. 可叠加价格过滤（只选低价区间）
4. 持有最小发行规模的 top_k 只
5. 每 rebalance_days 个 bar 轮动
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING

from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
)
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass
class SmallSizeConfig(CBRotationConfig):
    """小规模低余额策略配置"""

    top_k: int = 20
    rebalance_days: int = 5
    max_price: float = 130.0
    max_issue_size: float = 100000.0  # 最大发行规模（万元），只选小规模发行
    min_issue_size: float = 5000.0    # 最小发行规模（万元），防极端微盘
    min_turnover: float = 50.0
    min_days_to_maturity: int = 60
    exclude_st: bool = True
    stop_loss_pct: float = 0.15
    lot_size: int = 10
    min_tradable_candidates: int = 0  # P0-2: 候选池不足此值时跳过调仓，防极端集中度


@dataclass
class SmallSizeEnsembleConfig(SmallSizeConfig):
    """小规模策略参数集成配置。

    通过多组近优参数（top_k/max_price/max_issue_size 等）生成多个候选集合，
    再用投票/排名聚合生成最终目标池，从而平滑参数的“悬崖效应”。

    说明：
    - variants 里的字段为 SmallSizeConfig 的子集（缺省则使用本配置的同名字段）
    - weights 为空时默认等权
    """

    variants: list[dict] = field(default_factory=list)
    weights: list[float] = field(default_factory=list)

    # 排名聚合：reciprocal-rank，分数 = 1/(rank+1)
    aggregation: str = "reciprocal_rank"


@StrategyRegistry().register("small_size")
class SmallSizeStrategy(CBRotationStrategy):
    """小规模轮动策略

    核心逻辑：持有发行规模最小的 N 只可转债，等权分配，定期轮动。
    小发行规模 → 市场关注度低 → 定价偏差大 → 超额收益。
    使用 issue_size（静态字段）避免 remain_size 的前视偏差。
    """

    name = "small_size"

    def __init__(self, config: SmallSizeConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or SmallSizeConfig(**kwargs)

    @property
    def config(self) -> SmallSizeConfig:
        return self._config

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """按发行规模升序（最小优先），取 top_k。

        P0-2: 当候选池不足 min_tradable_candidates 时跳过调仓，
        避免极端集中度问题（如 2018 年仅 3 只标的）。
        """
        min_req = self._config.min_tradable_candidates
        if min_req > 0 and len(candidates) < min_req:
            logger.warning(
                "SmallSize: 候选池仅 %d 只 (< min_tradable_candidates=%d)，"
                "跳过本次调仓以避免极端集中度",
                len(candidates), min_req,
            )
            return []  # 返回空列表 = 不调仓，保持现有持仓
        candidates.sort(key=lambda c: (c.issue_size, c.symbol))
        return [c.symbol for c in candidates[: self._config.top_k]]


@StrategyRegistry().register("small_size_ensemble")
class SmallSizeEnsembleStrategy(CBRotationStrategy):
    """小规模轮动策略（参数集成版）。

    目标：降低 small_size 对 top_k / max_price / max_issue_size 等参数的悬崖敏感性。
    方法：用多组近优参数生成多个排名列表，再按倒数排名加权投票聚合。
    """

    name = "small_size_ensemble"

    def __init__(self, config: SmallSizeEnsembleConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or SmallSizeEnsembleConfig(**kwargs)

    @property
    def config(self) -> SmallSizeEnsembleConfig:
        return self._config

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """多组近优参数倒数排名加权聚合，平滑参数悬崖效应。"""
        cfg = self._config

        # 无 variants 时退化为普通 small_size 逻辑
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
            v_min_turnover = float(variant.get("min_turnover", cfg.min_turnover))
            v_min_issue_size = float(variant.get("min_issue_size", cfg.min_issue_size))
            v_max_issue_size = float(variant.get("max_issue_size", cfg.max_issue_size))

            # 变体的额外收紧过滤（基类预过滤已保证候选是“可能可交易”）
            filtered = [
                c
                for c in candidates
                if c.close > 0
                and c.close <= v_max_price
                and c.amount >= v_min_turnover
                and v_min_issue_size <= c.issue_size <= v_max_issue_size
            ]
            filtered.sort(key=lambda c: (c.issue_size, c.symbol))

            for rank, c in enumerate(filtered[:v_top_k]):
                if cfg.aggregation != "reciprocal_rank":
                    raise ValueError(
                        f"unsupported aggregation: {cfg.aggregation!r}"
                    )
                score = weight * (1.0 / (rank + 1.0))
                symbol_score[c.symbol] = symbol_score.get(c.symbol, 0.0) + score
                symbol_issue_size.setdefault(c.symbol, c.issue_size)

        ranked = sorted(
            symbol_score.items(),
            key=lambda kv: (
                -kv[1],
                symbol_issue_size.get(kv[0], float("inf")),
                kv[0],
            ),
        )

        return [s for s, _ in ranked[: cfg.top_k]]
