"""可转债 YTM 增强策略 — 攻守兼备

Alpha 来源：
1. 纯 pseudo_ytm 策略（4.17%年化）过于防守，只买 < 100 元的债
2. 纯 low_premium 策略（-6.82%）过于进攻，追高易回撤
3. 两者按权重融合 → 在债底保护区间选择"最有进攻性"的标的
4. 高 YTM 排名 = 距面值越远（安全垫） + 低溢价率排名 = 正股弹性最大

策略逻辑：
1. 筛选 close <= max_price 且到期时间 >= min_years_to_maturity
2. 计算 pseudo_ytm ≈ (100 - close) / close / years_to_maturity × 100
3. 计算溢价率 cb_over_rate
4. 对 YTM 排名（降序=高收益优先）和溢价率排名（升序=低溢价优先）
   加权合成综合分数
5. 取综合评分最优的 top_k 只
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
    days_to_maturity,
)
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass
class YTMPlusConfig(CBRotationConfig):
    """YTM 增强策略配置"""

    top_k: int = 15
    rebalance_days: int = 10

    # 价格上限（放宽到面值以上以覆盖"浅溢价+低溢价率"区间）
    max_price: float = 115.0
    face_value: float = 100.0

    # YTM 与溢价率的权重配比
    ytm_weight: float = 0.5
    premium_weight: float = 0.5

    # 到期筛选
    min_years_to_maturity: float = 0.5

    # 通用筛选
    min_turnover: float = 20.0
    max_premium: float = 50.0     # 溢价率上限（防极端高溢价）

    # 风控
    stop_loss_pct: float = 0.10


@StrategyRegistry().register("ytm_plus")
class YTMPlusStrategy(CBRotationStrategy):
    """YTM 增强策略

    在低价转债中融合到期收益率和转股溢价率两个因子，
    既保留债底安全垫又兼顾正股上涨弹性。
    """

    name = "ytm_plus"

    def __init__(self, config: YTMPlusConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or YTMPlusConfig(**kwargs)

    @property
    def config(self) -> YTMPlusConfig:
        """策略配置。"""
        return self._config

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        """低于面值 + 到期年限足够 + 溢价率不过高。"""
        cfg = self._config
        if candidate.close >= cfg.face_value:
            return False
        if not candidate.maturity_date:
            return False
        years = days_to_maturity(candidate.maturity_date, context.current_time) / 365.0
        if years < cfg.min_years_to_maturity:
            return False
        if candidate.cb_over_rate is None or candidate.cb_over_rate > cfg.max_premium:
            return False
        return True

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """双因子排名融合：YTM 排名（降序）+ 溢价率排名（升序），加权求和后升序取 top_k。

        需要至少 2 个候选才有排名意义。
        """
        cfg = self._config

        if len(candidates) < 2:
            return []

        # 计算 YTM 和溢价率
        with_ytm: list[tuple[str, float, float]] = []
        for c in candidates:
            years = days_to_maturity(c.maturity_date, context.current_time) / 365.0
            if years <= 0:
                continue
            ytm = (cfg.face_value - c.close) / c.close / years * 100.0
            premium = c.cb_over_rate if c.cb_over_rate is not None else 0
            with_ytm.append((c.symbol, ytm, premium))

        if len(with_ytm) < 2:
            return [s for s, _, _ in with_ytm]

        # YTM 降序排名（越高越好）
        with_ytm.sort(key=lambda x: -x[1])
        ytm_rank = {s: rank for rank, (s, _, _) in enumerate(with_ytm)}

        # 溢价率升序排名（越低越好）
        with_ytm.sort(key=lambda x: x[2])
        prem_rank = {s: rank for rank, (s, _, _) in enumerate(with_ytm)}

        # 加权综合分数（越低越好）
        scored = []
        for symbol, ytm, premium in with_ytm:
            score = cfg.ytm_weight * ytm_rank[symbol] + cfg.premium_weight * prem_rank[symbol]
            scored.append((symbol, score))

        scored.sort(key=lambda x: (x[1], x[0]))
        return [s for s, _ in scored[: cfg.top_k]]
