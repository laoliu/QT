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
        self._premium_history: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=300))
        self._effective_max_age: int = self.config.max_age_days

    @property
    def config(self) -> CBComprehensiveConfig:
        return self._config  # type: ignore[return-value]

    def _on_start_extra(self) -> None:
        self._close_history.clear()
        self._premium_history.clear()
        self._effective_max_age = self.config.max_age_days

    def _on_bar_update_state(self, bars: dict[str, "Bar"], context: "StrategyContext") -> None:
        for code, bar in bars.items():
            self._close_history.setdefault(code, deque(maxlen=300))
            self._close_history[code].append(bar.close)
            meta = getattr(bar, "metadata", {}) or {}
            prem = meta.get("cb_over_rate", None)
            if prem is None:
                prem = getattr(bar, "premium_rate", None) or getattr(bar, "cb_over_rate", None)
            if prem is not None:
                self._premium_history.setdefault(code, deque(maxlen=300))
                self._premium_history[code].append(float(prem))

    def _should_rebalance(self) -> bool:
        if self._bar_count <= 1:
            return False
        return (self._bar_count - 1) % self.config.rebalance_days == 0

    def _relax_filters_one_step(self) -> bool:
        cfg = self.config
        if self._effective_max_age < cfg.fallback_max_age_days:
            self._effective_max_age = min(
                self._effective_max_age + 90,
                cfg.fallback_max_age_days,
            )
            return True
        return False

    def _maybe_tighten_filters(self) -> None:
        cfg = self.config
        if self._effective_max_age > cfg.max_age_days:
            self._effective_max_age = max(
                self._effective_max_age - 30,
                cfg.max_age_days,
            )

    def _extra_filter(self, candidate: CandidateInfo, context: "StrategyContext") -> bool:
        cfg = self.config
        issue_size = getattr(candidate, "issue_size", None) or float("inf")
        if issue_size < cfg.min_issue_size or issue_size > cfg.max_issue_size:
            return False
        premium = getattr(candidate, "premium_rate", None)
        if premium is not None and premium * 100 > cfg.max_premium:
            return False
        turnover = getattr(candidate, "amount", None) or 0.0
        if turnover < cfg.min_turnover:
            return False
        if cfg.use_age_filter:
            listed = getattr(candidate, "list_date", None)
            if listed:
                age = days_since_listed(listed, now=getattr(context, "current_time", None))
                if age < cfg.min_age_days or age > cfg.max_age_days:
                    return False
        return True

    def _compute_bb_deviation(self, symbol: str, close: float) -> float | None:
        hist = self._close_history.get(symbol)
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
        hist = self._close_history.get(symbol)
        lb = self.config.momentum_lookback
        if hist is None or len(hist) < lb:
            return None
        old = hist[-lb]
        return (hist[-1] - old) / old if old > 0 else None

    def _compute_premium_percentile(self, symbol: str, current_rate: float) -> float:
        hist = self._premium_history.get(symbol)
        lb = self.config.premium_pct_lookback
        min_hist = max(10, lb // 2)
        if hist is None or len(hist) < min_hist:
            return 0.5
        recent = list(hist)[-lb:] if len(hist) >= lb else list(hist)
        pct_rank = sum(1 for v in recent if v <= current_rate) / len(recent)
        return 1.0 - pct_rank

    def _compute_putback_proximity(self, cb_value: float | None) -> float:
        if cb_value is None or cb_value <= 0:
            return 0.0
        trigger = self.config.putback_trigger_ratio
        parity = cb_value / 100.0
        return trigger - parity

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

        # Rank normalize — ascending=False means lower value → higher rank (1.0)
        r_price = _rank_scores(prices, ascending=False)      # 低价优
        r_prem = _rank_scores(premiums, ascending=False)      # 低溢价优
        r_size = _rank_scores(sizes, ascending=False)         # 小规模优
        r_age = _rank_scores(ages, ascending=False)           # 低龄优
        r_bb = _rank_scores(bb_devs, ascending=False)         # 低BB偏差优（超跌）
        r_rating = _rank_scores(ratings, ascending=True)      # 高评级优
        r_delta = _rank_scores(deltas, ascending=True)        # 高delta优
        r_ytm = _rank_scores(ytms, ascending=True)            # 高YTM优
        r_turn = _rank_scores(turnovers, ascending=True)      # 高换手优
        r_mom = _rank_scores(moms, ascending=True)            # 高动量优
        r_ppct = _rank_scores(prem_pcts, ascending=True)      # 高百分位分优
        r_put = _rank_scores(putbacks, ascending=True)        # 高回售接近度优

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
