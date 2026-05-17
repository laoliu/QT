"""可转债双低轮动策略

实现可转债双低轮动策略：
- 双低指标 = 转债价格 + 转股溢价率
- 定期轮动：卖出高双低，买入低双低
- 支持多种筛选条件（价格上限、溢价率上限、排除强赎等）

策略参数：
- top_k: 持仓数量（如持有前20只双低最低的可转债）
- rebalance_days: 轮动周期（如每5个交易日换仓）
- max_price: 最高价格限制（如 < 130元）
- max_premium: 最高溢价率限制（如 < 50%）
- exclude_redeemed: 是否排除已公告强赎

历史收益参考（2015-2025回测）：
- 年化收益：15-25%
- 最大回撤：10-15%
- 夏普比率：1.5-2.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
)
from aipha.strategy.registry import StrategyRegistry

logger = logging.getLogger(__name__)


@dataclass
class DoubleLowMetrics:
    """双低指标"""

    ts_code: str
    bond_price: float
    premium_rate: float  # 转股溢价率（%）
    double_low: float  # 双低指标

    @classmethod
    def from_bar_and_premium(
        cls,
        ts_code: str,
        bond_price: float,
        premium_rate: float,
    ) -> DoubleLowMetrics:
        """从价格和溢价率计算双低指标"""
        return cls(
            ts_code=ts_code,
            bond_price=bond_price,
            premium_rate=premium_rate,
            double_low=bond_price + premium_rate,
        )


@dataclass
class DoubleLowConfig(CBRotationConfig):
    """双低策略配置"""

    top_k: int = 20
    rebalance_days: int = 5
    max_price: float = 130.0
    min_price: float = 0.0  # 最低价格筛选
    max_premium: float = 50.0
    min_turnover: float = 20.0
    min_days_to_maturity: int = 30
    exclude_st: bool = True
    stop_loss_pct: float = 0.10
    lot_size: int = 10

    # 双低评分权重
    price_weight: float = 0.5
    premium_weight: float = 0.5
    equal_weight: bool = True  # True 时忽略 price/premium_weight，使用等权

    # 小规模加分
    low_size_threshold: float = 0.0  # 发行规模 ≤ 此值时加分（万元），0=不启用
    low_size_bonus: float = 0.0  # 加分幅度（负值=降低双低指标=更优排名）


@StrategyRegistry().register("double_low")
class DoubleLowStrategy(CBRotationStrategy):
    """可转债双低轮动策略

    双低策略是可转债市场最经典的量化策略之一：
    1. 计算所有可转债的"双低指标"（价格 + 溢价率）
    2. 选择双低指标最低的 top_k 只
    3. 定期轮动（卖出不在目标池的，买入新进入的）

    使用 issue_size（静态字段）替代 remain_size，消除前视偏差。
    """

    name = "double_low"

    def __init__(self, config: DoubleLowConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or DoubleLowConfig(**kwargs)

    @property
    def config(self) -> DoubleLowConfig:
        """策略配置。"""
        return self._config

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        """溢价率上限 + 价格下限 + 需要溢价率数据。"""
        if candidate.cb_over_rate is None:
            return False
        if candidate.cb_over_rate > self._config.max_premium:
            return False
        if self._config.min_price > 0 and candidate.close < self._config.min_price:
            return False
        return True

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """按双低指标排序，取 top_k。

        支持加权模式和小规模加分。
        """
        cfg = self._config
        pw = 0.5 if cfg.equal_weight else cfg.price_weight
        rw = 0.5 if cfg.equal_weight else cfg.premium_weight

        def _dl_score(c: CandidateInfo) -> float:
            if c.cb_over_rate is None:
                return float("inf")  # 溢价率缺失 → 排到末尾，不参与排名
            base = pw * c.close + rw * c.cb_over_rate
            # 小规模加分（负值降低分数=排名更优）
            if (
                cfg.low_size_threshold > 0
                and c.issue_size is not None
                and c.issue_size <= cfg.low_size_threshold
            ):
                base -= cfg.low_size_bonus
            return base

        scored = sorted(candidates, key=_dl_score)
        return [c.symbol for c in scored[: cfg.top_k]]


@dataclass
class ConversionArbitrageConfig(CBRotationConfig):
    """转股套利策略配置"""

    top_k: int = 10
    rebalance_days: int = 1
    min_spread_pct: float = -5.0  # 最小套利空间（溢价率 ≤ 此值，即转股价值显著 > 价格）
    max_price: float = 130.0
    min_turnover: float = 50.0
    min_days_to_maturity: int = 30
    stop_loss_pct: float = 0.08
    lot_size: int = 10


@StrategyRegistry().register("conversion_arbitrage")
class ConversionArbitrageStrategy(CBRotationStrategy):
    """转股套利策略

    当可转债价格 < 转股价值（溢价率为负）时，存在潜在套利空间。
    策略选择溢价率最低（套利空间最大）的 top_k 只转债。

    注意：
    - CB 是 T+0，但转股后正股需 T+1 卖出
    - 策略实际用法：做多溢价率为负（或极低）的转债，等待套利空间收敛
    - 需要考虑交易成本和滑点
    """

    name = "conversion_arbitrage"

    def __init__(self, config: ConversionArbitrageConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or ConversionArbitrageConfig(**kwargs)

    @property
    def config(self) -> ConversionArbitrageConfig:
        """策略配置。"""
        return self._config

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        """筛选具有套利空间的转债（溢价率低于阈值）。"""
        if candidate.cb_over_rate is None:
            return False
        if candidate.cb_value is None:
            return False
        # 溢价率 ≤ min_spread_pct 表示存在套利空间
        return candidate.cb_over_rate <= self._config.min_spread_pct

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """按溢价率升序排列（溢价率越低 = 套利空间越大），取 top_k。"""
        scored = sorted(
            candidates,
            key=lambda c: c.cb_over_rate if c.cb_over_rate is not None else 999,
        )
        return [c.symbol for c in scored[: self._config.top_k]]
