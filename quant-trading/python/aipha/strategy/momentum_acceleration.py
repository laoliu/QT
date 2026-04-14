"""动量加速策略 — 风险调整动量 + 盈利加速 + 放量验证

因子权重: 风险调整动量(0.35) + 盈利增长(0.30) + 放量确认(0.20) + ROE(0.15)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from aipha.strategy.equity_rotation_base import (
    EquityRotationConfig, EquityRotationStrategy, CandidateInfo,
    rank_normalize, StrategyContext,
)


@dataclass
class MomentumAccelConfig(EquityRotationConfig):
    top_k: int = 25
    rebalance_days: int = 5
    trailing_stop: float = 0.12
    volume_surge_threshold: float = 1.5


class MomentumAccelerationStrategy(EquityRotationStrategy):
    name = "momentum_acceleration"

    def __init__(self) -> None:
        super().__init__()
        self._config = MomentumAccelConfig()

    @property
    def config(self) -> MomentumAccelConfig:
        return self._config

    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        if not candidates:
            return []

        # 风险调整动量 = ret / vol
        ram_vals = []
        for c in candidates:
            mom = self._calc_momentum(c.symbol, 60, 5)
            vol = self._calc_volatility(c.symbol, 60)
            if mom is not None and vol and vol > 0:
                ram_vals.append(mom / vol)
            else:
                ram_vals.append(None)

        earn_vals = [c.metadata.get("dt_netprofit_yoy", 0) for c in candidates]
        roe_vals = [c.metadata.get("roe", 0) for c in candidates]

        # 放量确认: 近5日均量 / 近60日均量
        vol_surge = []
        for c in candidates:
            hist = self._price_history.get(c.symbol)
            if hist and len(hist) >= 60:
                # 用价格历史长度近似—实际应有 volume 历史
                vol_surge.append(c.metadata.get("vol_ratio", 1.0) or 1.0)
            else:
                vol_surge.append(None)

        ram_r = rank_normalize(ram_vals)
        earn_r = rank_normalize(earn_vals)
        surge_r = rank_normalize(vol_surge)
        roe_r = rank_normalize(roe_vals)

        scored = []
        for i, c in enumerate(candidates):
            s = 0.35 * ram_r[i] + 0.30 * earn_r[i] + 0.20 * surge_r[i] + 0.15 * roe_r[i]
            scored.append((c.symbol, s))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored]
