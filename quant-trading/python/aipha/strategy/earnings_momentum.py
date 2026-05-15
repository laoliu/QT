"""盈利动量策略 — CAN SLIM E+S 风格

因子权重: 盈利加速(0.30) + 营收增速(0.20) + SUE(0.20) + 价格动量(0.15) + ROE(0.10) + 低波(0.05)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from aipha.strategy.equity_rotation_base import (
    EquityRotationConfig, EquityRotationStrategy, CandidateInfo,
    rank_normalize, StrategyContext,
)


@dataclass
class EarningsMomentumConfig(EquityRotationConfig):
    top_k: int = 25
    rebalance_days: int = 15
    min_earnings_growth: float = 10.0
    min_revenue_growth: float = 5.0
    min_roe: float = 5.0
    max_pe: float = 150.0
    weight_earnings_accel: float = 0.30
    weight_revenue_growth: float = 0.20
    weight_sue: float = 0.20
    weight_momentum: float = 0.15
    weight_quality: float = 0.10
    weight_volatility: float = 0.05
    momentum_lookback: int = 20
    volatility_lookback: int = 60
    stop_loss_pct: float = 0.15


class EarningsMomentumStrategy(EquityRotationStrategy):
    name = "earnings_momentum"

    def __init__(self, config: EarningsMomentumConfig | None = None, **kwargs: object) -> None:
        super().__init__(config=config or EarningsMomentumConfig(), **kwargs)

    @property
    def config(self) -> EarningsMomentumConfig:
        return self._config

    @classmethod
    def param_space(cls) -> "ParamSpace":
        from aipha.strategy.param_space import ParamSpace, ParamSpec, ParamType
        return ParamSpace("earnings_momentum", [
            ParamSpec("top_k", ParamType.INT, default=25, low=10, high=50, step=5, is_critical=True),
            ParamSpec("rebalance_days", ParamType.INT, default=15, low=5, high=30, step=5),
            ParamSpec("stop_loss_pct", ParamType.FLOAT, default=0.15, low=0.05, high=0.30, step=0.05, is_critical=True),
            ParamSpec("weight_earnings_accel", ParamType.FLOAT, default=0.30, low=0.10, high=0.50, step=0.05),
            ParamSpec("momentum_lookback", ParamType.INT, default=20, low=10, high=60, step=10),
            ParamSpec("weight_revenue_growth", ParamType.FLOAT, default=0.20, low=0.05, high=0.40, step=0.05),
        ])

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        """盈利动量过滤：检查盈利增长、营收增长、ROE、PE."""
        cfg = self._config
        m = candidate.metadata

        pe = m.get("pe_ttm")
        if pe is not None:
            if pe <= 0 or pe > cfg.max_pe:
                return False

        earnings = m.get("dt_netprofit_yoy")
        if earnings is not None and earnings < cfg.min_earnings_growth:
            return False

        revenue = m.get("revenue_yoy")
        if revenue is not None and revenue < cfg.min_revenue_growth:
            return False

        roe = m.get("roe")
        if roe is not None and roe < cfg.min_roe:
            return False

        return True

    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        if not candidates:
            return []

        cfg = self._config

        earn_vals = [c.metadata.get("dt_netprofit_yoy") for c in candidates]
        rev_vals = [c.metadata.get("revenue_yoy") for c in candidates]
        sue_vals = [c.metadata.get("sue") for c in candidates]
        mom_vals = [self._calc_momentum(c.symbol, 60, 5) for c in candidates]
        roe_vals = [c.metadata.get("roe") for c in candidates]
        vol_vals = [self._calc_volatility(c.symbol, 60) for c in candidates]

        # Convert None to NaN for rank_normalize (median fill)
        def _to_float(v):
            return float(v) if v is not None else float('nan')

        earn_r = rank_normalize([_to_float(v) for v in earn_vals])
        rev_r = rank_normalize([_to_float(v) for v in rev_vals])
        sue_r = rank_normalize([_to_float(v) for v in sue_vals])
        mom_r = rank_normalize([_to_float(v) for v in mom_vals])
        roe_r = rank_normalize([_to_float(v) for v in roe_vals])
        vol_r = rank_normalize([-_to_float(v) if v else 0.0 for v in vol_vals])

        scored = []
        for i, c in enumerate(candidates):
            s = (cfg.weight_earnings_accel * earn_r[i]
                 + cfg.weight_revenue_growth * rev_r[i]
                 + cfg.weight_sue * sue_r[i]
                 + cfg.weight_momentum * mom_r[i]
                 + cfg.weight_quality * roe_r[i]
                 + cfg.weight_volatility * vol_r[i])
            scored.append((c.symbol, s))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored]
