"""困境反转策略 — 买入 "fallen angel" 反转标的

核心: 盈利从负转正 + 低估值 + Piotroski F-Score ≥ 4
因子权重: 盈利转正(0.35) + BP价值(0.30) + F-Score(0.20) + 小市值(0.15)
"""
from __future__ import annotations

from dataclasses import dataclass

from aipha.strategy.equity_rotation_base import (
    EquityRotationConfig, EquityRotationStrategy, CandidateInfo,
    rank_normalize, StrategyContext,
)


@dataclass
class TurnaroundConfig(EquityRotationConfig):
    top_k: int = 20
    rebalance_days: int = 20
    trailing_stop: float = 0.15
    min_fscore: int = 4


class TurnaroundPlayStrategy(EquityRotationStrategy):
    name = "turnaround_play"

    def __init__(self, config: TurnaroundConfig | None = None, **kwargs: object) -> None:
        super().__init__(config=config or TurnaroundConfig(), **kwargs)

    @property
    def config(self) -> TurnaroundConfig:
        return self._config

    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self._config
        filtered = []
        for c in candidates:
            m = c.metadata
            earn_growth = m.get("dt_netprofit_yoy", 0) or 0
            # 需要盈利从负转正或大幅改善
            if earn_growth < 0.10:
                continue
            fscore = m.get("fscore", 5)
            if fscore < cfg.min_fscore:
                continue
            filtered.append(c)

        if not filtered:
            return []

        earn_vals = [c.metadata.get("dt_netprofit_yoy", 0) for c in filtered]
        bp_vals = []
        for c in filtered:
            pb = c.metadata.get("pb")
            bp_vals.append(1.0 / pb if pb and pb > 0 else None)
        fscore_vals = [c.metadata.get("fscore", 5) for c in filtered]
        size_vals = [-(c.metadata.get("total_mv", 1e18)) for c in filtered]

        earn_r = rank_normalize(earn_vals)
        bp_r = rank_normalize(bp_vals)
        fs_r = rank_normalize(fscore_vals)
        size_r = rank_normalize(size_vals)

        scored = []
        for i, c in enumerate(filtered):
            s = 0.35 * earn_r[i] + 0.30 * bp_r[i] + 0.20 * fs_r[i] + 0.15 * size_r[i]
            scored.append((c.symbol, s))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored]
