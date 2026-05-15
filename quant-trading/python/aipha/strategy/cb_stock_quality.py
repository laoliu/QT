"""可转债正股质量策略 — 用正股基本面 (ROE/EP/波动率) 选可转债"""
from __future__ import annotations

import logging
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
class StockQualityCBConfig(CBRotationConfig):
    top_k: int = 15
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
    weight_price: float = 0.25
    weight_premium: float = 0.20
    weight_stock_roe: float = 0.20
    weight_stock_ep: float = 0.15
    weight_stock_vol: float = 0.10
    weight_issue_size: float = 0.10
    vol_lookback: int = 60
    use_stock_bridge: bool = True
    default_roe: float = 0.0
    default_ep: float = 0.0


class CBStockQualityStrategy(CBRotationStrategy):
    """正股质量 — 选 ROE 高 / EP 高 / 波动率低的正股对应的可转债"""
    name = "cb_stock_quality"

    def __init__(
        self,
        config: StockQualityCBConfig | None = None,
        stock_bridge: Any | None = None,
        **kwargs: Any,
    ):
        super().__init__(config=config or StockQualityCBConfig(), **kwargs)
        self._stock_bridge = stock_bridge
        self._stk_price_history: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=200))
        self._current_fundamentals: dict[str, Any] = {}

    @property
    def config(self) -> StockQualityCBConfig:
        return self._config  # type: ignore[return-value]

    def _on_start_extra(self) -> None:
        self._stk_price_history.clear()
        self._current_fundamentals.clear()

    def _on_bar_update_state(self, bars: dict[str, "Bar"], context: "StrategyContext") -> None:
        for code, bar in bars.items():
            meta = getattr(bar, "metadata", None) or {}
            stk = meta.get("stk_code")
            stk_close = meta.get("cb_value")
            if stk and stk_close is not None:
                self._stk_price_history[stk].append(float(stk_close))
                # Fetch fundamentals from bridge
                if self._stock_bridge and stk not in self._current_fundamentals:
                    try:
                        from datetime import date as _date
                        fund = self._stock_bridge.get_fundamentals(stk, _date.today())
                        self._current_fundamentals[stk] = fund
                    except Exception:
                        pass

    def _extra_filter(self, candidate: CandidateInfo, context: "StrategyContext") -> bool:
        cfg = self.config
        if cfg.use_age_filter:
            listed = getattr(candidate, "list_date", None)
            if listed and days_since_listed(listed, context.current_time) < cfg.min_age_days:
                return False
        premium = getattr(candidate, "premium_rate", None)
        if premium is not None and premium * 100 > cfg.max_premium:
            return False
        issue_size = getattr(candidate, "issue_size", None) or float("inf")
        if issue_size > cfg.max_issue_size:
            return False
        turnover = getattr(candidate, "amount", None) or 0.0
        return turnover >= cfg.min_turnover

    def _compute_stk_vol(self, stk_code: str) -> float | None:
        import math
        hist = self._stk_price_history.get(stk_code)
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

        prices = [c.price for c in candidates]
        premiums = [(getattr(c, "premium_rate", 0.0) or 0.0) * 100 for c in candidates]
        sizes = [getattr(c, "issue_size", 10000.0) or 10000.0 for c in candidates]
        roes, eps, vols = [], [], []

        for c in candidates:
            stk = c.metadata.get("stk_code") if c.metadata else None
            # 尝试从 _current_fundamentals 获取基本面
            roe = cfg.default_roe
            ep = cfg.default_ep
            vol = 0.3  # 默认中等波动
            if stk and stk in self._current_fundamentals:
                fund = self._current_fundamentals[stk]
                if fund.roe is not None:
                    roe = fund.roe
                if fund.ep is not None:
                    ep = fund.ep
            elif self._stock_bridge and stk:
                try:
                    from datetime import date as _date
                    fund = self._stock_bridge.get_fundamentals(stk, _date.today())
                    if fund.roe is not None:
                        roe = fund.roe
                    if fund.ep is not None:
                        ep = fund.ep
                except Exception:
                    pass
            # 正股波动率
            if stk:
                v = self._compute_stk_vol(stk)
                if v is not None:
                    vol = v
            roes.append(roe)
            eps.append(ep)
            vols.append(vol)

        r_price = _rank_scores(prices, ascending=False)   # 低价高分
        r_prem = _rank_scores(premiums, ascending=False)  # 低溢价高分
        r_roe = _rank_scores(roes, ascending=True)        # 高 ROE 高分
        r_ep = _rank_scores(eps, ascending=True)          # 高 EP 高分
        r_vol = _rank_scores(vols, ascending=False)       # 低波动高分
        r_size = _rank_scores(sizes, ascending=False)     # 小规模高分

        scored: list[tuple[str, float]] = []
        for i, c in enumerate(candidates):
            s = (cfg.weight_price * r_price[i]
                 + cfg.weight_premium * r_prem[i]
                 + cfg.weight_stock_roe * r_roe[i]
                 + cfg.weight_stock_ep * r_ep[i]
                 + cfg.weight_stock_vol * r_vol[i]
                 + cfg.weight_issue_size * r_size[i])
            scored.append((c.code, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored[:cfg.top_k]]
