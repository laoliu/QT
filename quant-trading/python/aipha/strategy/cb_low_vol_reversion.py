"""可转债低波反转策略"""
from __future__ import annotations
import logging, math
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CBRotationConfig, CBRotationStrategy, CandidateInfo, days_since_listed
logger = logging.getLogger(__name__)

@dataclass
class CBLowVolReversionConfig(CBRotationConfig):
    top_k: int = 15; rebalance_days: int = 5; max_price: float = 130.0; min_turnover: float = 20.0
    min_days_to_maturity: int = 30; exclude_st: bool = True; stop_loss_pct: float = 0.15
    trailing_stop_pct: float = 0.0; lot_size: int = 10; buffer_k: int = 5; max_premium: float = 50.0
    use_age_filter: bool = True; min_age_days: int = 5
    weight_low_vol: float = 0.30; weight_reversal: float = 0.0; weight_bond_floor: float = 0.25
    weight_premium: float = 0.15; weight_stock_roe: float = 0.20; weight_issue_size: float = 0.10
    vol_lookback: int = 20; reversal_lookback: int = 10; use_stock_bridge: bool = True

class CBLowVolReversionStrategy(CBRotationStrategy):
    name = "cb_low_vol_reversion"
    def __init__(self, config: CBLowVolReversionConfig | None = None, stock_bridge: Any | None = None, **kw: object):
        super().__init__(config=config or CBLowVolReversionConfig(), **kw)
        self._stock_bridge = stock_bridge
    @property
    def config(self) -> CBLowVolReversionConfig: return self._config  # type: ignore

    def _should_rebalance(self) -> bool:
        if self._bar_count <= self.config.vol_lookback:
            return False
        return (self._bar_count - self._last_rebalance_bar) >= self.config.rebalance_days

    def _on_bar_update_state(self, bars: dict[str, "Bar"], context: StrategyContext) -> None:
        maxlen = max(self.config.vol_lookback, self.config.reversal_lookback) + 5
        for sym, bar in bars.items():
            close = getattr(bar, "close", None)
            if close is None: continue
            if sym not in self._close_history: self._close_history[sym] = deque(maxlen=maxlen)
            self._close_history[sym].append(close)
        # Fetch fundamentals from stock bridge
        if self._stock_bridge and self.config.use_stock_bridge:
            for sym, bar in bars.items():
                meta = getattr(bar, "metadata", {}) or {}
                stk_code = meta.get("stk_code")
                if stk_code:
                    try:
                        fund = self._stock_bridge.get_fundamentals(stk_code, context.current_time)
                        if fund is not None:
                            self._current_fundamentals[stk_code] = fund
                    except Exception:
                        pass

    def _compute_volatility(self, symbol: str, lookback: int) -> float | None:
        hist = self._close_history.get(symbol)
        if not hist or len(hist) < lookback + 1: return None
        prices = list(hist)[-(lookback + 1):]
        rets = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices)) if prices[i - 1] > 0]
        if len(rets) < 2: return None
        mu = sum(rets) / len(rets); var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
        return math.sqrt(var * 252) if var > 0 else 0.0

    def _compute_return(self, symbol: str, lookback: int) -> float | None:
        hist = self._close_history.get(symbol)
        if not hist or len(hist) < lookback + 1: return None
        prices = list(hist)
        return (prices[-1] / prices[-(lookback + 1)] - 1) if prices[-(lookback + 1)] > 0 else 0.0

    def _extra_filter(self, c: CandidateInfo, ctx: StrategyContext) -> bool:
        cfg = self.config
        prem = getattr(c, "cb_over_rate", None)
        if prem is None:
            prem = getattr(c, "premium_rate", None)
            if prem is not None: prem = prem * 100.0
        if prem is not None and prem > cfg.max_premium: return False
        # Age filter
        if cfg.use_age_filter and cfg.min_age_days > 0:
            list_date = getattr(c, "list_date", "")
            if list_date and ctx.current_time:
                age = days_since_listed(list_date, ctx.current_time)
                if age < cfg.min_age_days:
                    return False
        # History sufficiency filter
        hist = self._close_history.get(c.symbol)
        min_hist = min(cfg.vol_lookback, 10)
        if not hist or len(hist) < min_hist:
            return False
        return True

    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        from aipha.strategy.cb_ultra import _rank_scores
        cfg = self.config
        if not candidates:
            return []
        vol_raw: dict[str, float] = {}
        rev_raw: dict[str, float] = {}
        bf_raw: dict[str, float] = {}
        prem_raw: dict[str, float] = {}
        roe_raw: dict[str, float] = {}
        size_raw: dict[str, float] = {}
        for c in candidates:
            v = self._compute_volatility(c.code, cfg.vol_lookback)
            vol_raw[c.code] = -(v if v is not None else 99.0)
            r = self._compute_return(c.code, cfg.reversal_lookback)
            rev_raw[c.code] = -(r if r is not None else 0.0)
            bf_raw[c.code] = 100.0 / c.close if c.close > 0 else 0.0
            prem_raw[c.code] = -(getattr(c, "cb_over_rate", 50.0) or 50.0)
            # Stock ROE from fundamentals
            stk_code = c.metadata.get("stk_code", "") if c.metadata else ""
            fund = self._current_fundamentals.get(stk_code)
            roe_val = getattr(fund, "roe", None) if fund else None
            roe_raw[c.code] = roe_val if roe_val is not None else 0.0
            size_raw[c.code] = -(getattr(c, "issue_size", 1e9) or 1e9)
        rv = _rank_scores(vol_raw)
        rr = _rank_scores(rev_raw)
        rbf = _rank_scores(bf_raw)
        rpm = _rank_scores(prem_raw)
        rroe = _rank_scores(roe_raw)
        rsz = _rank_scores(size_raw)
        composite = {c.code: cfg.weight_low_vol * rv.get(c.code, 0) + cfg.weight_reversal * rr.get(c.code, 0) + cfg.weight_bond_floor * rbf.get(c.code, 0) + cfg.weight_premium * rpm.get(c.code, 0) + cfg.weight_stock_roe * rroe.get(c.code, 0) + cfg.weight_issue_size * rsz.get(c.code, 0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
