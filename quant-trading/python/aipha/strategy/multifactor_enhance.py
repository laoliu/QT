"""多因子增强策略"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from aipha.domain.models import Side
from aipha.factors.equity_factors import (compute_value_factor, compute_quality_factor,
    compute_momentum_factor, compute_volatility_factor, compute_size_factor,
    compute_piotroski_factor, compute_valuation_percentile_factor,
    compute_accruals_quality_factor, compute_dividend_factor)
from aipha.strategy.base import IntentType, OrderIntent, StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo, EquityRotationConfig, EquityRotationStrategy
logger = logging.getLogger(__name__)

@dataclass
class MultiFactorEnhanceConfig(EquityRotationConfig):
    top_k: int = 50; rebalance_days: int = 20; benchmark: str = "000300.SH"
    weight_value: float = 0.25; weight_quality: float = 0.20; weight_momentum: float = 0.20
    weight_volatility: float = 0.20; weight_size: float = 0.15
    weight_piotroski: float = 0.0; weight_valuation_pct: float = 0.0
    weight_accruals: float = 0.0; weight_dividend: float = 0.0
    value_metric: str = "pe_ttm"; volatility_lookback: int = 60; max_single_pct: float = 0.05; piotroski_min_dims: int = 5
    stop_loss_pct: float = 0.20; buffer_k: float = 10

class MultiFactorEnhanceStrategy(EquityRotationStrategy):
    name = "multifactor_enhance"
    def __init__(self, config: MultiFactorEnhanceConfig | None = None, **kw: object):
        super().__init__(config=config or MultiFactorEnhanceConfig(), **kw)
        self.__entry_prices: dict[str, float] = {}

    @property
    def _entry_prices(self) -> dict[str, float]:
        return self.__entry_prices

    @_entry_prices.setter
    def _entry_prices(self, value: dict[str, float]) -> None:
        self.__entry_prices = value

    @property
    def config(self) -> MultiFactorEnhanceConfig: return self._config  # type: ignore

    def on_start(self) -> None:
        super().on_start()
        self.__entry_prices.clear()
        # Sync max_single_pct to max_single_weight
        if hasattr(self._config, "max_single_pct"):
            self._config.max_single_weight = self._config.max_single_pct

    def on_fill(self, symbol: str, side: Side, qty: int, price: float) -> None:
        if side == Side.BUY:
            self.__entry_prices[symbol] = price
        else:
            self.__entry_prices.pop(symbol, None)

    def _generate_rebalance(self, target_syms: list[str], ctx: StrategyContext, bars: dict[str, object]) -> list[OrderIntent]:
        intents: list[OrderIntent] = []
        # Sell positions not in targets
        for sym, pos in ctx.positions.items():
            if pos > 0 and sym not in target_syms:
                intents.append(OrderIntent(
                    symbol=sym, side=Side.SELL, qty=pos,
                    intent_type=IntentType.ORDER,
                    reason=f"{self.name}_exit",
                ))
        # Buy targets
        if not target_syms:
            return intents
        cfg = self.config
        n = len(target_syms)
        w = min(1.0 / n, cfg.max_single_pct)
        for sym in target_syms:
            bar = bars.get(sym) if isinstance(bars, dict) else None
            if bar is None: continue
            price = getattr(bar, "close", 0)
            if price <= 0: continue
            target_value = ctx.nav * w
            qty = int(target_value / price / 100) * 100
            if qty > 0:
                intents.append(OrderIntent(
                    symbol=sym, side=Side.BUY, qty=qty,
                    intent_type=IntentType.ENTRY,
                    reason=f"{self.name}_rebalance",
                ))
        return intents

    def _score_candidates(self, candidates: list[EquityCandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self.config; ph = {c.code: getattr(c, "price_history", []) for c in candidates}
        factors: dict[str, dict[str, float]] = {"value": compute_value_factor(candidates, metric=cfg.value_metric), "quality": compute_quality_factor(candidates),
            "momentum": compute_momentum_factor(ph, candidates), "volatility": compute_volatility_factor(ph, candidates, lookback=cfg.volatility_lookback),
            "size": compute_size_factor(candidates)}
        if cfg.weight_piotroski: factors["piotroski"] = compute_piotroski_factor(candidates)
        if cfg.weight_valuation_pct: factors["valuation_pct"] = compute_valuation_percentile_factor(candidates)
        if cfg.weight_accruals: factors["accruals"] = compute_accruals_quality_factor(candidates)
        if cfg.weight_dividend: factors["dividend"] = compute_dividend_factor(candidates)
        weights = {n: getattr(cfg, f"weight_{n}", 0) for n in factors}
        # For missing factor values, use -inf so they rank last
        composite = {c.code: sum(weights.get(n, 0) * factors[n].get(c.code, float("-inf") if weights.get(n, 0) > 0 else 0) for n in factors) for c in candidates}
        # Store full ranking for buffer_k
        self._last_full_ranking = sorted(composite, key=composite.get, reverse=True)
        return self._last_full_ranking[:cfg.top_k]  # type: ignore

    def on_bar(self, bars, context):
        # Override to implement buffer_k
        from aipha.domain.models import Bar as _Bar
        self._last_full_ranking = []
        intents = super().on_bar(bars, context)
        # Apply buffer_k: don't sell positions that rank within top_k + buffer_k
        cfg = self.config
        buffer_k = int(cfg.buffer_k)
        if buffer_k > 0 and self._last_full_ranking:
            extended = set(self._last_full_ranking[:cfg.top_k + buffer_k])
            intents = [i for i in intents if not (i.side == Side.SELL and i.symbol in extended)]
        return intents
