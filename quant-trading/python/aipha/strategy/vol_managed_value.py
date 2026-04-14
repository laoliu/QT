"""波动管理价值策略 — Moreira & Muir (2017) 波动目标

基础: 价值选股 (EP + BP + ROE + 低换手)
叠加: 动态仓位 = target_vol / realized_vol
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from aipha.strategy.equity_rotation_base import (
    EquityRotationConfig, EquityRotationStrategy, CandidateInfo,
    rank_normalize, StrategyContext,
)
from aipha.strategy.base import OrderIntent, IntentType
from aipha.domain.models import Bar, Side


@dataclass
class VolManagedValueConfig(EquityRotationConfig):
    top_k: int = 30
    rebalance_days: int = 10
    target_vol: float = 0.15
    vol_lookback: int = 60


class VolManagedValueStrategy(EquityRotationStrategy):
    name = "vol_managed_value"

    def __init__(self) -> None:
        super().__init__()
        self._config = VolManagedValueConfig()
        self._portfolio_returns: list[float] = []

    @property
    def config(self) -> VolManagedValueConfig:
        return self._config

    def on_bar(self, bars: dict[str, Bar], context: StrategyContext) -> list[OrderIntent]:
        # 记录组合收益用于波动估计
        if hasattr(self, "_last_nav") and self._last_nav > 0:
            self._portfolio_returns.append(context.nav / self._last_nav - 1)
        self._last_nav = context.nav

        intents = super().on_bar(bars, context)

        # 动态缩放
        scale = self._vol_scale()
        if scale < 1.0:
            for i, intent in enumerate(intents):
                if intent.side == Side.BUY:
                    new_qty = max(100, int(intent.qty * scale / 100) * 100)
                    intents[i] = OrderIntent(
                        symbol=intent.symbol, side=intent.side, qty=new_qty,
                        intent_type=intent.intent_type, reason=intent.reason,
                    )
        return intents

    def _vol_scale(self) -> float:
        cfg = self._config
        if len(self._portfolio_returns) < cfg.vol_lookback:
            return 1.0
        recent = self._portfolio_returns[-cfg.vol_lookback:]
        realized = float(np.std(recent)) * np.sqrt(252)
        if realized <= 0:
            return 1.0
        return min(cfg.target_vol / realized, 1.5)

    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        if not candidates:
            return []

        ep_vals = []
        bp_vals = []
        for c in candidates:
            pe = c.metadata.get("pe_ttm")
            ep_vals.append(1.0 / pe if pe and pe > 0 else None)
            pb = c.metadata.get("pb")
            bp_vals.append(1.0 / pb if pb and pb > 0 else None)

        roe_vals = [c.metadata.get("roe", 0) for c in candidates]
        to_vals = [-(c.metadata.get("turnover_rate", 0) or 0) for c in candidates]  # 低换手为优

        ep_r = rank_normalize(ep_vals)
        bp_r = rank_normalize(bp_vals)
        roe_r = rank_normalize(roe_vals)
        to_r = rank_normalize(to_vals)

        scored = []
        for i, c in enumerate(candidates):
            s = 0.30 * ep_r[i] + 0.20 * bp_r[i] + 0.25 * roe_r[i] + 0.25 * to_r[i]
            scored.append((c.symbol, s))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored]
