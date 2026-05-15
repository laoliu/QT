"""小盘股轮动策略"""
from __future__ import annotations
import logging, math
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING
from aipha.factors.equity_factors import compute_momentum_factor, compute_quality_factor, compute_size_factor, compute_turnover_factor, compute_value_factor, compute_volatility_factor
from aipha.strategy.base import StrategyContext, OrderIntent
from aipha.domain.models import Side
from aipha.strategy.equity_rotation_base import EquityCandidateInfo, EquityRotationConfig, EquityRotationStrategy
logger = logging.getLogger(__name__)

@dataclass
class SmallCapConfig(EquityRotationConfig):
    top_k: int = 30; rebalance_days: int = 15; min_mv: float = 100_000.0; max_mv: float = 2_000_000.0
    min_price: float = 3.0; max_price: float = 50.0; min_turnover: float = 500.0; min_listed_days: int = 60
    buffer_k: int = 8; momentum_invert: bool = False; lot_size: int = 100
    weight_small_cap: float = 0.30; weight_momentum: float = 0.15; weight_turnover: float = 0.10
    weight_volatility: float = 0.15; weight_quality: float = 0.15; weight_value: float = 0.15
    regime_adaptive: bool = False
    regime_bull_delta_small_cap: float = 0.05; regime_bull_delta_momentum: float = 0.05
    regime_bull_delta_value: float = -0.05; regime_bull_delta_quality: float = -0.05
    regime_bear_delta_small_cap: float = -0.10; regime_bear_delta_momentum: float = -0.05
    regime_bear_delta_value: float = 0.05; regime_bear_delta_quality: float = 0.10
    regime_bear_delta_volatility: float = 0.05

class SmallCapRotationStrategy(EquityRotationStrategy):
    name = "smallcap_rotation"
    def __init__(self, config: SmallCapConfig | None = None, **kw: object):
        super().__init__(config=config or SmallCapConfig(), **kw)
        self._price_history: dict[str, list[float]] = defaultdict(list)
        self._entry_prices: dict[str, float] = {}
    @property
    def config(self) -> SmallCapConfig: return self._config  # type: ignore
    def on_start(self) -> None:
        super().on_start()
        self._price_history.clear()
        self._entry_prices.clear()
    def on_fill(self, symbol: str, side, qty: int, price: float) -> None:
        super().on_fill(symbol, side, qty, price)
        if side == Side.BUY:
            self._entry_prices[symbol] = price
        elif side == Side.SELL:
            self._entry_prices.pop(symbol, None)
    def _update_history(self, bars: dict) -> None:
        for sym, bar in bars.items():
            self._price_history[sym].append(bar.close)
    def _filter_universe(self, bars: dict, ctx: StrategyContext) -> list[EquityCandidateInfo]:
        cfg = self.config; candidates = []
        for sym, bar in bars.items():
            if getattr(bar, "is_suspended", False):
                continue
            meta = getattr(bar, "metadata", {}) or {}
            name = meta.get("name", "")
            if "ST" in name:
                continue
            mv = meta.get("circ_mv", 0) or 0
            if mv < cfg.min_mv or mv > cfg.max_mv:
                continue
            if bar.close < cfg.min_price or bar.close > cfg.max_price:
                continue
            list_date = meta.get("list_date", "")
            if list_date and ctx.current_time:
                try:
                    from datetime import datetime as _dt
                    ld = _dt.strptime(list_date, "%Y%m%d")
                    if (ctx.current_time - ld).days < cfg.min_listed_days:
                        continue
                except Exception:
                    pass
            candidates.append(EquityCandidateInfo(
                symbol=sym, close=bar.close,
                metadata=meta, circ_mv=mv,
                price_history=self._price_history.get(sym, []),
            ))
        return candidates
    def calc_momentum(self, symbol: str) -> float:
        prices = self._price_history.get(symbol, [])
        if len(prices) < 2:
            return 0.0
        return (prices[-1] - prices[0]) / prices[0] if prices[0] > 0 else 0.0
    def calc_volatility(self, symbol: str) -> float:
        prices = self._price_history.get(symbol, [])
        if len(prices) < 3:
            return 0.0
        rets = [(prices[i] - prices[i-1]) / prices[i-1] for i in range(1, len(prices)) if prices[i-1] > 0]
        if len(rets) < 2:
            return 0.0
        mu = sum(rets) / len(rets)
        var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
        return math.sqrt(var * 252) if var > 0 else 0.0
    def _check_stop_loss(self, bars: dict, ctx: StrategyContext) -> list:
        intents = []
        cfg = self.config
        for sym, qty in (ctx.positions or {}).items():
            if qty <= 0:
                continue
            entry = self._entry_prices.get(sym)
            if entry is None:
                continue
            bar = bars.get(sym)
            if bar is None:
                continue
            loss = (entry - bar.close) / entry if entry > 0 else 0
            if loss >= cfg.stop_loss_pct:
                intents.append(OrderIntent(symbol=sym, side=Side.SELL, qty=qty))
        return intents
    def _generate_rebalance(self, targets: list[str], ctx: StrategyContext, bars: dict) -> list:
        intents = []
        for sym, qty in (ctx.positions or {}).items():
            if sym not in targets and qty > 0:
                intents.append(OrderIntent(symbol=sym, side=Side.SELL, qty=qty))
        for sym in targets:
            if (ctx.positions or {}).get(sym, 0) == 0 and sym in bars:
                bar = bars[sym]
                target_val = ctx.nav / len(targets) if targets else 0
                qty = int(target_val / bar.close / self.config.lot_size) * self.config.lot_size if bar.close > 0 else 0
                if qty > 0:
                    intents.append(OrderIntent(symbol=sym, side=Side.BUY, qty=qty))
        return intents
    def _get_regime_weights(self, context: StrategyContext) -> dict[str, float]:
        cfg = self.config
        w = {"small_cap": cfg.weight_small_cap, "momentum": cfg.weight_momentum, "turnover": cfg.weight_turnover, "volatility": cfg.weight_volatility, "quality": cfg.weight_quality, "value": cfg.weight_value}
        if cfg.regime_adaptive:
            regime = getattr(context, "market_regime", None) or (context.metadata or {}).get("market_regime", "NEUTRAL")
            if regime == "BULL":
                w["small_cap"] += cfg.regime_bull_delta_small_cap; w["momentum"] += cfg.regime_bull_delta_momentum
                w["value"] += cfg.regime_bull_delta_value; w["quality"] += cfg.regime_bull_delta_quality
            elif regime == "BEAR":
                w["small_cap"] += cfg.regime_bear_delta_small_cap; w["momentum"] += cfg.regime_bear_delta_momentum
                w["value"] += cfg.regime_bear_delta_value; w["quality"] += cfg.regime_bear_delta_quality
                w["volatility"] += getattr(cfg, "regime_bear_delta_volatility", 0.0)
        w = {k: max(0, v) for k, v in w.items()}; total = sum(w.values()) or 1.0
        return {k: v / total for k, v in w.items()}
    def _score_candidates(self, candidates: list[EquityCandidateInfo], context: StrategyContext) -> list[str]:
        cfg = self.config; w = self._get_regime_weights(context)
        ph = {c.code: self._price_history.get(c.code, getattr(c, "price_history", [])) for c in candidates}
        factors = {"small_cap": compute_size_factor(candidates), "momentum": compute_momentum_factor(ph, candidates), "turnover": compute_turnover_factor(candidates), "volatility": compute_volatility_factor(ph, candidates), "quality": compute_quality_factor(candidates), "value": compute_value_factor(candidates)}
        if cfg.momentum_invert and "momentum" in factors:
            factors["momentum"] = {k: -v for k, v in factors["momentum"].items()}
        composite = {c.code: sum(w.get(f, 0) * factors.get(f, {}).get(c.code, 0.0) for f in w) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
