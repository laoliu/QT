"""强赎博弈策略 — 买入即将触发强赎条款但转债价值被低估的转债"""
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

if TYPE_CHECKING:
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


@dataclass
class ForcedRedeemPlayConfig(CBRotationConfig):
    top_k: int = 10
    lower_cb_value: float = 100.0
    upper_cb_value: float = 130.0
    max_premium: float = 20.0
    max_price: float = 150.0
    min_turnover: float = 30.0
    min_days_to_maturity: int = 60
    stop_loss_pct: float = 0.12
    rebalance_days: int = 3


class ForcedRedeemPlayStrategy(CBRotationStrategy):
    """强赎博弈 — 转债价值在 [lower, upper] 区间, 溢价率低, 等待强赎触发后上涨"""
    name = "forced_redeem_play"

    def __init__(self, config: ForcedRedeemPlayConfig | None = None, **kwargs: object):
        super().__init__(config=config or ForcedRedeemPlayConfig(), **kwargs)

    @property
    def config(self) -> ForcedRedeemPlayConfig:
        return self._config  # type: ignore[return-value]

    def _custom_exit_check(
        self, bars: dict[str, "Bar"], context: StrategyContext,
    ) -> list[OrderIntent]:
        """强赎触发后立即卖出"""
        intents: list[OrderIntent] = []
        positions = getattr(context, "positions", {})
        for code, qty in positions.items():
            if qty <= 0 or code not in bars:
                continue
            bar = bars[code]
            # 如果已触发强赎公告, 立即退出
            force_redeem = getattr(bar, "force_redeem_triggered", False)
            if force_redeem:
                intents.append(OrderIntent(
                    symbol=code, intent_type="EXIT",
                    side=Side.SELL, quantity=qty,
                ))
        return intents

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        cfg = self.config
        cb_value = getattr(candidate, "cb_value", None)
        if cb_value is None:
            return False
        if cb_value < cfg.lower_cb_value or cb_value > cfg.upper_cb_value:
            return False
        premium = getattr(candidate, "premium_rate", None)
        if premium is not None and premium * 100 > cfg.max_premium:
            return False
        turnover = getattr(candidate, "turnover", None) or 0.0
        return turnover >= cfg.min_turnover

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        cfg = self.config
        scored: list[tuple[str, float]] = []
        for c in candidates:
            cb_value = getattr(c, "cb_value", 100.0)
            premium = (getattr(c, "premium_rate", 0.0) or 0.0) * 100
            # 越接近强赎触发价 (130) 且溢价率越低, 得分越高
            proximity = cb_value / 130.0  # 越接近 1 越好
            score = proximity * 0.6 - premium * 0.4  # 高接近度 + 低溢价率
            scored.append((c.code, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored[:cfg.top_k]]
