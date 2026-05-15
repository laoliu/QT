"""回购信号策略 — 高自由现金流 + 低估值 + 高ROE

模拟回购倾向: FCF 充裕、估值便宜、盈利能力强的公司更可能回购。
因子权重: FCFY(0.30) + EP(0.30) + ROE(0.20) + 盈利增长(0.20)
"""
from __future__ import annotations

from dataclasses import dataclass

from aipha.strategy.equity_rotation_base import (
    EquityRotationConfig, EquityRotationStrategy, CandidateInfo,
    rank_normalize, StrategyContext,
)


@dataclass
class BuybackSignalConfig(EquityRotationConfig):
    top_k: int = 30
    rebalance_days: int = 20
    min_roe: float = 0.08


class BuybackSignalStrategy(EquityRotationStrategy):
    name = "buyback_signal"

    def __init__(self, config: BuybackSignalConfig | None = None, **kwargs: object) -> None:
        super().__init__(config=config or BuybackSignalConfig(), **kwargs)

    @property
    def config(self) -> BuybackSignalConfig:
        return self._config

    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self._config
        filtered = [c for c in candidates if (c.metadata.get("roe", 0) or 0) >= cfg.min_roe]
        if not filtered:
            return []

        fcfy_vals = [c.metadata.get("fcfy", 0) for c in filtered]
        ep_vals = []
        for c in filtered:
            pe = c.metadata.get("pe_ttm")
            ep_vals.append(1.0 / pe if pe and pe > 0 else None)
        roe_vals = [c.metadata.get("roe", 0) for c in filtered]
        earn_vals = [c.metadata.get("dt_netprofit_yoy", 0) for c in filtered]

        fcfy_r = rank_normalize(fcfy_vals)
        ep_r = rank_normalize(ep_vals)
        roe_r = rank_normalize(roe_vals)
        earn_r = rank_normalize(earn_vals)

        scored = []
        for i, c in enumerate(filtered):
            s = 0.30 * fcfy_r[i] + 0.30 * ep_r[i] + 0.20 * roe_r[i] + 0.20 * earn_r[i]
            scored.append((c.symbol, s))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored]
