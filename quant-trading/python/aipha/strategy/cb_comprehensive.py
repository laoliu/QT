"""可转债综合策略 — 10+ 因子加权 (双低增强的全面版)"""
from __future__ import annotations

import logging
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from aipha.strategy.cb_enhanced_factors import delta_exposure, enhanced_ytm, turnover_activity
from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
    days_since_listed,
)
from aipha.strategy.cb_ultra import _rank_scores
from aipha.strategy.double_low_plus import _get_rating_score

if TYPE_CHECKING:
    from aipha.domain.models import Bar
    from aipha.strategy.base import StrategyContext

logger = logging.getLogger(__name__)


@dataclass
class CBComprehensiveConfig(CBRotationConfig):
    top_k: int = 10
    rebalance_days: int = 3
    max_price: float = 145.0
    max_issue_size: float = 30000.0
    min_issue_size: float = 1000.0
    min_turnover: float = 15.0
    min_days_to_maturity: int = 30
    exclude_st: bool = True
    stop_loss_pct: float = 0.30
    lot_size: int = 10
    use_age_filter: bool = True
    min_age_days: int = 3
    max_age_days: int = 180
    fallback_max_age_days: int = 730
    max_premium: float = 70.0
    lookback: int = 20
    bb_k: float = 2.0
    momentum_lookback: int = 10
    # 12 因子权重
    weight_price: float = 0.12
    weight_premium: float = 0.12
    weight_size: float = 0.10
    weight_age: float = 0.06
    weight_mean_reversion: float = 0.15
    weight_rating: float = 0.10
    weight_delta: float = 0.10
    weight_ytm: float = 0.10
    weight_turnover: float = 0.05
    weight_momentum: float = 0.10
    weight_premium_percentile: float = 0.0
    weight_putback: float = 0.0
    premium_pct_lookback: int = 40
    putback_trigger_ratio: float = 0.85
    min_candidates: int = 5


class CBComprehensiveStrategy(CBRotationStrategy):
    """可转债综合 — 12因子 rank fusion"""
    name = "cb_comprehensive"

    def __init__(self, config: CBComprehensiveConfig | None = None, **kwargs: object):
        super().__init__(config=config or CBComprehensiveConfig(), **kwargs)
        self._price_hist: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=300))
        self._premium_hist: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=300))

    @property
    def config(self) -> CBComprehensiveConfig:
        return self._config  # type: ignore[return-value]

    def _on_start_extra(self) -> None:
        self._price_hist.clear()
        self._premium_hist.clear()

    def _on_bar_update_state(self, bars: dict[str, "Bar"], context: "StrategyContext") -> None:
        for code, bar in bars.items():
            self._price_hist[code].append(bar.close)
            prem = getattr(bar, "premium_rate", None) or getattr(bar, "cb_over_rate", None)
            if prem is not None:
                self._premium_hist[code].append(float(prem))

    def _should_rebalance(self) -> bool:
        return self._bar_count % self.config.rebalance_days == 0

    def _extra_filter(self, candidate: CandidateInfo, context: "StrategyContext") -> bool:
        cfg = self.config
        issue_size = getattr(candidate, "issue_size", None) or float("inf")
        if issue_size < cfg.min_issue_size or issue_size > cfg.max_issue_size:
            return False
        premium = getattr(candidate, "premium_rate", None)
        if premium is not None and premium * 100 > cfg.max_premium:
            return False
        turnover = getattr(candidate, "turnover", None) or 0.0
        if turnover < cfg.min_turnover:
            return False
        if cfg.use_age_filter:
            listed = getattr(candidate, "list_date", None)
            if listed:
                age = days_since_listed(listed)
                if age < cfg.min_age_days or age > cfg.max_age_days:
                    return False
        return True

    def _compute_bb_deviation(self, symbol: str, close: float) -> float | None:
        hist = self._price_hist.get(symbol)
        lb = self.config.lookback
        if hist is None or len(hist) < lb:
            return None
        prices = list(hist)[-lb:]
        mean = sum(prices) / lb
        std = math.sqrt(sum((p - mean) ** 2 for p in prices) / lb)
        if std < 1e-9:
            return None
        return (close - mean) / std

    def _compute_momentum(self, symbol: str) -> float | None:
        hist = self._price_hist.get(symbol)
        lb = self.config.momentum_lookback
        if hist is None or len(hist) < lb:
            return None
        old = hist[-lb]
        return (hist[-1] - old) / old if old > 0 else None

    def _compute_premium_percentile(self, symbol: str, current_rate: float) -> float:
        hist = self._premium_hist.get(symbol)
        lb = self.config.premium_pct_lookback
        if hist is None or len(hist) < lb:
            return 0.5
        recent = list(hist)[-lb:]
        below = sum(1 for v in recent if v > current_rate)
        return below / len(recent)

    def _compute_putback_proximity(self, cb_value: float | None) -> float:
        if cb_value is None:
            return 0.0
        return max(0.0, 1.0 - cb_value / 100.0) if cb_value < 100.0 else 0.0

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: "StrategyContext",
    ) -> list[str]:
        cfg = self.config
        now = datetime.now()

        prices = [c.price for c in candidates]
        premiums = [(getattr(c, "premium_rate", 0.0) or 0.0) * 100 for c in candidates]
        sizes = [getattr(c, "issue_size", 10000.0) or 10000.0 for c in candidates]
        ages, bb_devs, moms = [], [], []
        deltas, ytms, turnovers, ratings = [], [], [], []
        prem_pcts, putbacks = [], []

        for c in candidates:
            # 年龄
            listed = getattr(c, "list_date", None)
            ages.append(float(days_since_listed(listed)) if listed else 100.0)
            # 均值回归
            d = self._compute_bb_deviation(c.code, c.price)
            bb_devs.append(d if d is not None else 0.0)
            # 动量
            m = self._compute_momentum(c.code)
            moms.append(m if m is not None else 0.0)
            # delta
            deltas.append(delta_exposure(c))
            # YTM
            mat = getattr(c, "maturity_date", None)
            ytms.append(enhanced_ytm(c.price, mat, now) if mat else 0.0)
            # 换手率活跃度
            turnovers.append(turnover_activity(c))
            # 评级
            ratings.append(float(_get_rating_score(getattr(c, "rating", "") or "")))
            # 溢价率百分位
            prem_rate = getattr(c, "premium_rate", 0.0) or 0.0
            prem_pcts.append(self._compute_premium_percentile(c.code, prem_rate))
            # 回售接近度
            cb_value = getattr(c, "cb_value", None)
            putbacks.append(self._compute_putback_proximity(cb_value))

        # Rank normalize
        r_price = _rank_scores(prices, ascending=True)
        r_prem = _rank_scores(premiums, ascending=True)
        r_size = _rank_scores(sizes, ascending=True)
        r_age = _rank_scores(ages, ascending=True)
        r_bb = _rank_scores(bb_devs, ascending=True)
        r_rating = _rank_scores(ratings, ascending=False)
        r_delta = _rank_scores(deltas, ascending=False)
        r_ytm = _rank_scores(ytms, ascending=False)
        r_turn = _rank_scores(turnovers, ascending=False)
        r_mom = _rank_scores(moms, ascending=False)
        r_ppct = _rank_scores(prem_pcts, ascending=False)
        r_put = _rank_scores(putbacks, ascending=False)

        scored: list[tuple[str, float]] = []
        for i, c in enumerate(candidates):
            s = (cfg.weight_price * r_price[i]
                 + cfg.weight_premium * r_prem[i]
                 + cfg.weight_size * r_size[i]
                 + cfg.weight_age * r_age[i]
                 + cfg.weight_mean_reversion * r_bb[i]
                 + cfg.weight_rating * r_rating[i]
                 + cfg.weight_delta * r_delta[i]
                 + cfg.weight_ytm * r_ytm[i]
                 + cfg.weight_turnover * r_turn[i]
                 + cfg.weight_momentum * r_mom[i]
                 + cfg.weight_premium_percentile * r_ppct[i]
                 + cfg.weight_putback * r_put[i])
            scored.append((c.code, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored[:cfg.top_k]]
