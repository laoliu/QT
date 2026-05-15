"""可转债正股质量V2（含现金流+盈利稳定性+成长）"""
from __future__ import annotations
import logging
import math
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CBRotationConfig, CBRotationStrategy, CandidateInfo
logger = logging.getLogger(__name__)

@dataclass
class StockQualityV2Config(CBRotationConfig):
    top_k: int = 15; rebalance_days: int = 5
    weight_price: float = 0.15; weight_premium: float = 0.15; weight_stock_roe: float = 0.15
    weight_stock_ep: float = 0.10; weight_stock_vol: float = 0.10; weight_cash_flow: float = 0.15
    weight_earnings_stability: float = 0.10; weight_growth: float = 0.10
    vol_lookback: int = 60; use_stock_bridge: bool = True; use_age_filter: bool = True
    min_age_days: int = 3; max_premium: float = 80.0
    default_roe: float = 0.0; default_ep: float = 0.0
    default_ocf_ni_ratio: float = 1.0; default_roe_std: float = 0.05; default_revenue_growth: float = 0.0

class CBStockQualityV2Strategy(CBRotationStrategy):
    name = "cb_stock_quality_v2"
    def __init__(self, config: StockQualityV2Config | None = None, stock_bridge: Any | None = None, **kw: object):
        super().__init__(config=config or StockQualityV2Config(), **kw)
        self._stock_bridge = stock_bridge
        self._bridge = stock_bridge
        self._stk_price_history: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=200))
        self._current_fundamentals: dict[str, Any] = {}
    @property
    def config(self) -> StockQualityV2Config: return self._config  # type: ignore

    def _on_start_extra(self) -> None:
        self._stk_price_history.clear()
        self._current_fundamentals.clear()

    def _extra_filter(self, c: CandidateInfo, ctx: StrategyContext) -> bool:
        prem = getattr(c, "cb_over_rate", None)
        if prem is None:
            prem = getattr(c, "premium_rate", None)
            if prem is not None: prem = prem * 100.0
        if prem is not None and prem > self.config.max_premium: return False
        cfg = self.config
        if cfg.use_age_filter and cfg.min_age_days > 0:
            ld = getattr(c, "list_date", None) or (c.metadata or {}).get("list_date")
            if ld:
                try:
                    ld_dt = datetime.strptime(str(ld), "%Y%m%d")
                    age = (ctx.current_time - ld_dt).days
                    if age < cfg.min_age_days:
                        return False
                except Exception:
                    pass
        return True

    def _on_bar_update_state(self, bars: dict[str, Any], ctx: StrategyContext) -> None:
        for code, bar in bars.items():
            meta = getattr(bar, "metadata", None) or {}
            stk_code = meta.get("stk_code", "")
            if not stk_code:
                continue
            cb_value = meta.get("cb_value") or bar.close
            if cb_value:
                self._stk_price_history[stk_code].append(float(cb_value))
            if self._bridge and stk_code not in self._current_fundamentals:
                try:
                    fund = self._bridge.get_fundamentals(stk_code, ctx.current_time)
                    self._current_fundamentals[stk_code] = fund
                except Exception:
                    pass

    def _compute_stk_vol(self, stk_code: str) -> float | None:
        hist = self._stk_price_history.get(stk_code)
        if hist is None or len(hist) < 10:
            return None
        prices = list(hist)[-self.config.vol_lookback:]
        returns = [(prices[i] - prices[i - 1]) / prices[i - 1]
                    for i in range(1, len(prices)) if prices[i - 1] > 0]
        if len(returns) < 5:
            return None
        mean = sum(returns) / len(returns)
        var = sum((r - mean) ** 2 for r in returns) / max(len(returns) - 1, 1)
        return math.sqrt(var * 252)

    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        from aipha.strategy.cb_ultra import _rank_scores
        if not candidates:
            return []
        cfg = self.config
        codes = [c.symbol for c in candidates]
        # Collect raw factor values
        prices = []; prems = []; roes = []; eps = []; vols = []; cfs = []; stabs = []; grows = []
        for c in candidates:
            prices.append(getattr(c, "close", 200.0) or 200.0)
            prem = getattr(c, "cb_over_rate", 50.0)
            if prem is None: prem = 50.0
            prems.append(prem)
            stk_code = (c.metadata or {}).get("stk_code", "")
            fund = self._current_fundamentals.get(stk_code) if stk_code else None
            roe = cfg.default_roe; ep = cfg.default_ep
            ocf_ni = cfg.default_ocf_ni_ratio; roe_std = cfg.default_roe_std
            rev_growth = cfg.default_revenue_growth; stock_vol = 0.3
            if fund:
                if hasattr(fund, "roe") and fund.roe is not None: roe = fund.roe
                if hasattr(fund, "ep") and fund.ep is not None: ep = fund.ep
                if hasattr(fund, "ocf_ni_ratio") and fund.ocf_ni_ratio is not None: ocf_ni = fund.ocf_ni_ratio
                if hasattr(fund, "roe_std") and fund.roe_std is not None: roe_std = fund.roe_std
                if hasattr(fund, "revenue_growth") and fund.revenue_growth is not None: rev_growth = fund.revenue_growth
            if stk_code:
                v = self._compute_stk_vol(stk_code)
                if v is not None: stock_vol = v
            roes.append(roe); eps.append(ep); vols.append(stock_vol)
            cfs.append(ocf_ni); stabs.append(roe_std); grows.append(rev_growth)
        # Rank normalize: lower=better for price, premium, vol, stability
        r_price = _rank_scores(prices, ascending=False)
        r_prem = _rank_scores(prems, ascending=False)
        r_roe = _rank_scores(roes, ascending=True)
        r_ep = _rank_scores(eps, ascending=True)
        r_vol = _rank_scores(vols, ascending=False)
        r_cf = _rank_scores(cfs, ascending=True)
        r_stab = _rank_scores(stabs, ascending=False)
        r_grow = _rank_scores(grows, ascending=True)
        composite = {}
        for i, code in enumerate(codes):
            composite[code] = (
                cfg.weight_price * r_price[i] +
                cfg.weight_premium * r_prem[i] +
                cfg.weight_stock_roe * r_roe[i] +
                cfg.weight_stock_ep * r_ep[i] +
                cfg.weight_stock_vol * r_vol[i] +
                cfg.weight_cash_flow * r_cf[i] +
                cfg.weight_earnings_stability * r_stab[i] +
                cfg.weight_growth * r_grow[i]
            )
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
