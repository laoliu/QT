"""负溢价套利"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CBRotationConfig, CBRotationStrategy, CandidateInfo
logger = logging.getLogger(__name__)

@dataclass
class NegativePremiumConfig(CBRotationConfig):
    top_k: int = 10; rebalance_days: int = 3
    entry_threshold: float = -2.0; exit_threshold: float = 0.0
    weight_premium: float = 0.50; weight_volume: float = 0.25; weight_days_left: float = 0.25
    min_remaining_days: int = 10; max_price: float = 200.0
    min_amount: float = 500.0

class NegativePremiumStrategy(CBRotationStrategy):
    name = "negative_premium"
    def __init__(self, config: NegativePremiumConfig | None = None, **kw: object):
        super().__init__(config=config or NegativePremiumConfig(), **kw)
    @property
    def config(self) -> NegativePremiumConfig: return self._config  # type: ignore
    def _extra_filter(self, c: CandidateInfo, ctx: StrategyContext) -> bool:
        cfg = self.config
        p = getattr(c, "cb_over_rate", None)
        if p is None:
            p = getattr(c, "premium_rate", None)
            if p is not None:
                p = p * 100.0
        if p is None or p > cfg.entry_threshold: return False
        price = getattr(c, "close", None)
        if price is not None and price > cfg.max_price: return False
        amt = getattr(c, "amount", 0) or 0
        if amt < cfg.min_amount: return False
        return True
    def _custom_exit_check(self, bars: dict, context: StrategyContext) -> list:
        from aipha.strategy.base import OrderIntent
        from aipha.domain.models import Side
        intents: list = []
        cfg = self.config
        for sym, bar in bars.items():
            if sym not in context.positions:
                continue
            p = getattr(bar, "cb_over_rate", None)
            if p is None and hasattr(bar, "metadata"):
                p = bar.metadata.get("cb_over_rate")
            if p is not None and p > cfg.exit_threshold:
                qty = context.positions[sym]
                intents.append(OrderIntent(symbol=sym, side=Side.SELL, qty=qty))
        return intents
    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        from aipha.strategy.cb_ultra import _rank_scores
        cfg = self.config
        prem = {c.code: -(getattr(c, "premium_rate", 0.0) or 0.0) for c in candidates}
        vol = {c.code: getattr(c, "volume", 0.0) or 0.0 for c in candidates}
        days = {c.code: getattr(c, "remain_days", 0.0) or 0.0 for c in candidates}
        rp, rv, rd = _rank_scores(prem), _rank_scores(vol), _rank_scores(days)
        composite = {c.code: cfg.weight_premium * rp.get(c.code, 0) + cfg.weight_volume * rv.get(c.code, 0) + cfg.weight_days_left * rd.get(c.code, 0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
