"""可转债强赎临门博弈策略

Alpha 来源：
1. 转债发行人有动力促转股 → 转股价值接近 130%（强赎触发价）时，
   市场预期强制赎回 → 溢价率压缩 → 转债价格向平价收敛
2. 临近触发点的转债具有"伪 Gamma"特性：一旦突破，涨幅集中
3. 低溢价 + 高 cb_value = 正股如果再涨 5-10%，就触发强赎条款

策略逻辑：
1. 筛选 cb_value 在 [lower_cb_value, upper_cb_value] 区间内的标的
   （转股价值接近但尚未达到强赎触发价 130 元）
2. 叠加低溢价率过滤（确保转债价格贴近平价）
3. 按 cb_value 降序排列（最接近触发点的优先）
4. 持有 top_k 只等权
5. 一旦 cb_value > upper_cb_value（可能已触发强赎），自动阶段性获利卖出

风险：正股下跌 → cb_value 远离触发点 → 溢价率可能扩大，需止损。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.domain.models import Side
from aipha.strategy.base import OrderIntent, StrategyContext
from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
)
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


@dataclass
class ForcedRedeemPlayConfig(CBRotationConfig):
    """强赎临门博弈策略配置"""

    top_k: int = 10

    # 转股价值区间（接近但未达到强赎触发价 130）
    lower_cb_value: float = 100.0   # 转股价值下限
    upper_cb_value: float = 130.0   # 转股价值上限（强赎通常为 130%）

    # 溢价率限制（低溢价 = 高 Delta = 正股弹性好）
    max_premium: float = 20.0

    # 通用筛选
    max_price: float = 150.0
    min_turnover: float = 30.0
    min_days_to_maturity: int = 60  # 太近到期的不会触发强赎
    stop_loss_pct: float = 0.12


@StrategyRegistry().register("forced_redeem_play")
class ForcedRedeemPlayStrategy(CBRotationStrategy):
    """强赎临门博弈策略

    瞄准转股价值即将达到强赎触发线的转债。
    低溢价 + 高 cb_value → 正股再涨一点就触发强赎，
    市场提前定价 → 溢价压缩 → 转债向平价快速收敛。
    """

    name = "forced_redeem_play"

    def __init__(self, config: ForcedRedeemPlayConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or ForcedRedeemPlayConfig(**kwargs)

    @property
    def config(self) -> ForcedRedeemPlayConfig:
        """策略配置。"""
        return self._config

    def _custom_exit_check(
        self, bars: dict[str, Bar], context: StrategyContext,
    ) -> list[OrderIntent]:
        """cb_value 超过上限 × 1.05 → 获利了结（可能已触发强赎）。"""
        cfg = self._config
        intents: list[OrderIntent] = []
        for symbol, qty in list(context.positions.items()):
            if qty <= 0:
                continue
            bar = bars.get(symbol)
            if bar is None:
                continue
            meta = bar.metadata or {}
            cv = meta.get("cb_value", 0)
            if cv > cfg.upper_cb_value * 1.05:
                intents.append(OrderIntent(
                    symbol=symbol, side=Side.SELL, qty=qty,
                    reason=f"强赎获利: cv={cv:.1f}",
                ))
        return intents

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        """cb_value 在 [lower, upper] 区间，且溢价率不超限。"""
        cfg = self._config
        if candidate.cb_value is None:
            return False
        if candidate.cb_value < cfg.lower_cb_value or candidate.cb_value > cfg.upper_cb_value:
            return False
        if candidate.cb_over_rate is None or candidate.cb_over_rate > cfg.max_premium:
            return False
        return True

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """按 cb_value 降序（最接近强赎触发点优先），取 top_k。"""
        candidates.sort(key=lambda c: (-(c.cb_value or 0), c.symbol))
        return [c.symbol for c in candidates[: self._config.top_k]]
