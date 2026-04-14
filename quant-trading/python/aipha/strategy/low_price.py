"""低价策略（按价格排序选最低价转债）"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CBRotationConfig, CBRotationStrategy, CandidateInfo
logger = logging.getLogger(__name__)

@dataclass
class LowPriceConfig(CBRotationConfig):
    top_k: int = 30; rebalance_days: int = 5; max_price: float = 115.0

class LowPriceStrategy(CBRotationStrategy):
    name = "low_price"
    def __init__(self, config: LowPriceConfig | None = None, **kw: object):
        super().__init__(config=config or LowPriceConfig(), **kw)
    @property
    def config(self) -> LowPriceConfig: return self._config  # type: ignore
    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        price = {c.code: getattr(c, "close", 999.0) or 999.0 for c in candidates}
        return sorted(price, key=price.get)[:self.config.top_k]  # type: ignore
