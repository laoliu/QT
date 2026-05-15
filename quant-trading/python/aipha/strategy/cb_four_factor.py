"""可转债四因子策略 — 宏观/动量/流动性/价值 四维轮动"""
from __future__ import annotations

import logging
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
    days_since_listed,
)
from aipha.strategy.cb_ultra import _rank_scores

if TYPE_CHECKING:
    from datetime import date
    from aipha.domain.models import Bar
    from aipha.strategy.base import StrategyContext

logger = logging.getLogger(__name__)


@dataclass
class FourFactorCBConfig(CBRotationConfig):
    top_k: int = 20
    rebalance_days: int = 5
    max_price: float = 150.0
    max_issue_size: float = float("inf")
    min_turnover: float = 20.0
    min_days_to_maturity: int = 30
    stop_loss_pct: float = 0.25
    lot_size: int = 10
    use_age_filter: bool = True
    min_age_days: int = 3
    max_premium: float = 80.0
    weight_macro: float = 0.15
    weight_momentum: float = 0.25
    weight_liquidity: float = 0.20
    weight_value: float = 0.40
    momentum_lookback: int = 20
    volume_lookback: int = 20
    value_price_w: float = 0.40
    value_premium_w: float = 0.40
    value_size_w: float = 0.20


class CBFourFactorStrategy(CBRotationStrategy):
    """四因子可转债轮动 — 宏观择时 + 动量 + 流动性 + 价值"""
    name = "cb_four_factor"

    def __init__(self, config: FourFactorCBConfig | None = None, **kwargs: Any):
        super().__init__(config=config or FourFactorCBConfig(), **kwargs)
        self._price_hist: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=200))
        self._vol_hist: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=200))
        self._macro_indicator: float = 1.0  # 外部注入或默认中性

    @property
    def config(self) -> FourFactorCBConfig:
        return self._config  # type: ignore[return-value]

    def _on_start_extra(self) -> None:
        self._price_hist.clear()
        self._vol_hist.clear()
        self._macro_indicator = 1.0

    def _on_bar_update_state(self, bars: dict[str, "Bar"], context: "StrategyContext") -> None:
        for code, bar in bars.items():
            self._price_hist[code].append(bar.close)
            amount = getattr(bar, "amount", None) or getattr(bar, "turnover_val", None)
            if amount is not None:
                self._vol_hist[code].append(float(amount))

    def _extra_filter(self, candidate: CandidateInfo, context: "StrategyContext") -> bool:
        cfg = self.config
        if cfg.use_age_filter:
            listed = getattr(candidate, "list_date", None)
            if listed:
                age = days_since_listed(listed, now=getattr(context, "current_time", None))
                if age < cfg.min_age_days:
                    return False
        premium = getattr(candidate, "premium_rate", None)
        if premium is not None and premium * 100 > cfg.max_premium:
            return False
        issue_size = getattr(candidate, "issue_size", None) or float("inf")
        if issue_size > cfg.max_issue_size:
            return False
        turnover = getattr(candidate, "amount", None) or 0.0
        return turnover >= cfg.min_turnover

    def _compute_macro_factor(self) -> float:
        return self._macro_indicator

    def _compute_momentum(self, code: str, lookback: int) -> float:
        hist = self._price_hist.get(code)
        if hist is None or len(hist) < lookback:
            return 0.0
        old = hist[-lookback]
        if old <= 0:
            return 0.0
        return (hist[-1] - old) / old

    def _compute_turnover_score(self, symbol: str, current_amount: float | None) -> float:
        hist = self._vol_hist.get(symbol)
        if hist is None or len(hist) < 5:
            return 0.5
        avg = sum(hist) / len(hist)
        if avg <= 0:
            return 0.5
        current = current_amount or 0.0
        return min(2.0, current / avg)

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: "StrategyContext",
    ) -> list[str]:
        cfg = self.config
        macro = self._compute_macro_factor()

        momentums = [self._compute_momentum(c.code, cfg.momentum_lookback) for c in candidates]
        turnovers: list[float] = []
        for c in candidates:
            amt = getattr(c, "amount", None)
            turnovers.append(self._compute_turnover_score(c.code, amt))

        # 价值复合: 价格 + 溢价率 + 规模
        value_scores: list[float] = []
        for c in candidates:
            p_score = max(0, 1.0 - c.price / 200.0)
            prem = (getattr(c, "premium_rate", 0.0) or 0.0)
            prem_score = max(0, 1.0 - prem)
            sz = getattr(c, "issue_size", 10000.0) or 10000.0
            sz_score = max(0, 1.0 - sz / 50000.0)
            vs = (cfg.value_price_w * p_score
                  + cfg.value_premium_w * prem_score
                  + cfg.value_size_w * sz_score)
            value_scores.append(vs)

        r_mom = _rank_scores(momentums, ascending=False)
        r_liq = _rank_scores(turnovers, ascending=False)
        r_val = _rank_scores(value_scores, ascending=False)

        scored: list[tuple[str, float]] = []
        for i, c in enumerate(candidates):
            s = (cfg.weight_macro * macro
                 + cfg.weight_momentum * r_mom[i]
                 + cfg.weight_liquidity * r_liq[i]
                 + cfg.weight_value * r_val[i])
            scored.append((c.code, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored[:cfg.top_k]]
