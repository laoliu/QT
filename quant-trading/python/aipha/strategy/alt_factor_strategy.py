"""另类因子策略（筹码集中度/资金流/融资/分析师等）"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo, EquityRotationConfig, EquityRotationStrategy
logger = logging.getLogger(__name__)

_DEFAULT_WEIGHTS: dict[str, float] = {"chips_concentration": 1.5, "flow_buy_ratio": 1.2, "margin_buy_intensity": 1.0, "analyst_buy_ratio": 0.6, "inst_holding_change": 0.8, "northbound_net": 1.0, "block_trade_premium": 0.5, "repurchase_ratio": 0.8, "short_interest_ratio": -1.0, "insider_net_buy": 0.7}

@dataclass
class AltFactorConfig(EquityRotationConfig):
    top_k: int = 30; rebalance_days: int = 20
    factor_weights: dict[str, float] = field(default_factory=lambda: dict(_DEFAULT_WEIGHTS))
    rank_normalize: bool = True; winsorize_pct: float = 0.02; min_factor_coverage: float = 0.3

class AltFactorStrategy(EquityRotationStrategy):
    name = "alt_factor"
    def __init__(self, config: AltFactorConfig | None = None, **kw: object):
        super().__init__(config=config or AltFactorConfig(), **kw)
    @property
    def config(self) -> AltFactorConfig: return self._config  # type: ignore
    def _score_candidates(self, candidates: list[EquityCandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self.config; fw = cfg.factor_weights
        composite: dict[str, float] = {}
        for c in candidates:
            score = 0.0; coverage = 0
            for fname, w in fw.items():
                val = getattr(c, fname, None)
                if val is not None:
                    score += w * val; coverage += 1
            total_factors = len(fw) or 1
            if coverage / total_factors < cfg.min_factor_coverage: continue
            composite[c.code] = score
        if cfg.rank_normalize and composite:
            ranked = sorted(composite, key=composite.get, reverse=True)  # type: ignore
            n = len(ranked)
            composite = {code: 1.0 - i / n for i, code in enumerate(ranked)}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
