"""可转债 Gamma 凸性策略 — 偏好 balanced 区间高 gamma 转债"""
from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aipha.factors.cb_delta_zone import (
    DeltaZone,
    classify_delta_zone,
    compute_delta_proxy,
    compute_gamma_proxy,
)
from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
    days_since_listed,
)
from aipha.strategy.cb_ultra import _rank_scores

if TYPE_CHECKING:
    from aipha.domain.models import Bar
    from aipha.strategy.base import StrategyContext

logger = logging.getLogger(__name__)


@dataclass
class GammaConvexityCBConfig(CBRotationConfig):
    top_k: int = 15
    rebalance_days: int = 5
    max_price: float = 160.0
    max_issue_size: float = float("inf")
    min_turnover: float = 20.0
    min_days_to_maturity: int = 60
    stop_loss_pct: float = 0.20
    lot_size: int = 10
    delta_min: float = 0.25
    delta_max: float = 0.75
    use_age_filter: bool = True
    min_age_days: int = 3
    max_premium: float = 60.0
    weight_gamma: float = 0.25
    weight_premium: float = 0.25
    weight_stock_vol: float = 0.25
    weight_price: float = 0.15
    weight_issue_size: float = 0.10
    vol_lookback: int = 60
    use_stock_bridge: bool = True


class CBGammaConvexityStrategy(CBRotationStrategy):
    """Gamma 凸性策略 — 选 delta 在 balanced 区间且 gamma 高的转债"""
    name = "cb_gamma_convexity"

    def __init__(
        self,
        config: GammaConvexityCBConfig | None = None,
        stock_bridge: Any | None = None,
        **kwargs: Any,
    ):
        super().__init__(config=config or GammaConvexityCBConfig(), **kwargs)
        self._stock_bridge = stock_bridge
        self._stk_price_hist: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=200))

    @property
    def config(self) -> GammaConvexityCBConfig:
        return self._config  # type: ignore[return-value]

    def _on_start_extra(self) -> None:
        self._stk_price_hist.clear()

    def _on_bar_update_state(self, bars: dict[str, "Bar"], context: "StrategyContext") -> None:
        for code, bar in bars.items():
            stk = getattr(bar, "stk_code", None)
            stk_close = getattr(bar, "stk_close", None)
            if stk and stk_close:
                self._stk_price_hist[stk].append(float(stk_close))

    def _extra_filter(self, candidate: CandidateInfo, context: "StrategyContext") -> bool:
        cfg = self.config
        cb_value = getattr(candidate, "cb_value", None)
        delta = compute_delta_proxy(cb_value)
        if delta is not None:
            if delta < cfg.delta_min or delta > cfg.delta_max:
                return False
        premium = getattr(candidate, "premium_rate", None)
        if premium is not None and premium * 100 > cfg.max_premium:
            return False
        if cfg.use_age_filter:
            listed = getattr(candidate, "list_date", None)
            if listed and days_since_listed(listed) < cfg.min_age_days:
                return False
        turnover = getattr(candidate, "turnover", None) or 0.0
        return turnover >= cfg.min_turnover

    def _compute_stk_vol(self, stk_code: str) -> float | None:
        import math
        hist = self._stk_price_hist.get(stk_code)
        if hist is None or len(hist) < 20:
            return None
        prices = list(hist)[-self.config.vol_lookback:]
        returns = [(prices[i] - prices[i - 1]) / prices[i - 1]
                    for i in range(1, len(prices)) if prices[i - 1] > 0]
        if len(returns) < 10:
            return None
        mean = sum(returns) / len(returns)
        var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        return math.sqrt(var * 252)

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: "StrategyContext",
    ) -> list[str]:
        cfg = self.config
        gammas: list[float] = []
        premiums: list[float] = []
        stk_vols: list[float] = []
        prices: list[float] = []
        sizes: list[float] = []

        for c in candidates:
            cb_value = getattr(c, "cb_value", None)
            g = compute_gamma_proxy(cb_value) or 0.0
            gammas.append(g)
            premiums.append((getattr(c, "premium_rate", 0.0) or 0.0) * 100)
            stk = getattr(c, "stk_code", None)
            vol = self._compute_stk_vol(stk) if stk else None
            stk_vols.append(vol or 0.0)
            prices.append(c.price)
            sizes.append(getattr(c, "issue_size", 10000.0) or 10000.0)

        r_gamma = _rank_scores(gammas, ascending=False)    # 高 gamma 高分
        r_prem = _rank_scores(premiums, ascending=True)    # 低溢价高分
        r_vol = _rank_scores(stk_vols, ascending=False)    # 高正股波动高分
        r_price = _rank_scores(prices, ascending=True)     # 低价高分
        r_size = _rank_scores(sizes, ascending=True)       # 小规模高分

        scored: list[tuple[str, float]] = []
        for i, c in enumerate(candidates):
            s = (cfg.weight_gamma * r_gamma[i]
                 + cfg.weight_premium * r_prem[i]
                 + cfg.weight_stock_vol * r_vol[i]
                 + cfg.weight_price * r_price[i]
                 + cfg.weight_issue_size * r_size[i])
            scored.append((c.code, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored[:cfg.top_k]]
