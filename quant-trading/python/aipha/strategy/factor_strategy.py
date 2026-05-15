"""因子驱动策略 — 多因子打分 → 选股 → 下单"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, Optional

from aipha.domain.models import Bar, Side
from aipha.factors.base import Factor, FactorData, FactorResult
from aipha.strategy.base import IntentType, OrderIntent, Strategy, StrategyContext

WeightMethod = Literal["equal", "factor_score", "min_variance", "risk_parity", "max_sharpe"]


@dataclass
class FactorStrategyConfig:
    top_k: Optional[int] = 10
    top_pct: Optional[float] = None
    rebalance_days: int = 20
    weight_method: WeightMethod = "equal"
    equal_weight: bool = True
    normalize_method: str = "rank"
    ascending: bool = False
    lookback_days: int = 60

    def __post_init__(self) -> None:
        if self.top_k is not None and self.top_pct is not None:
            raise ValueError("Cannot specify both top_k and top_pct")
        if self.top_k is None and self.top_pct is None:
            raise ValueError("Must specify either top_k or top_pct")


class FactorBasedStrategy(Strategy):
    """因子驱动策略: 多因子打分选股 + 灵活权重分配"""

    def __init__(
        self,
        factors: list[Factor],
        config: Optional[FactorStrategyConfig] = None,
        weights: Optional[dict[str, float]] = None,
        name: str = "FACTOR_STRATEGY",
    ):
        self._factors = factors
        self._config = config or FactorStrategyConfig()
        self._factor_weights = weights or {f.name: 1.0 for f in factors}
        self._name = name
        self._bar_count = 0
        self._last_rebalance_bar = 0
        self._price_history: dict[str, list[float]] = {}

    @property
    def name(self) -> str:
        return self._name

    def on_start(self) -> None:
        pass

    def on_end(self) -> None:
        pass

    def reset(self) -> None:
        self._bar_count = 0
        self._last_rebalance_bar = 0
        self._price_history.clear()

    def on_bar(self, bars: dict[str, Bar], context: StrategyContext) -> list[OrderIntent]:
        self._bar_count += 1
        self._update_price_history(bars)

        if not self._should_rebalance():
            return []

        universe = list(bars.keys())
        current_date = self._get_current_date(bars, context)
        if current_date is None:
            return []

        # 计算因子
        factor_results = self._compute_factors(context, universe, current_date)
        if not factor_results:
            return []

        # 合成打分
        combined = self._combine_factors(factor_results)
        selected = self._select_stocks(combined)
        if not selected:
            return []

        # 权重
        target_weights = self._calculate_weights(selected)
        self._last_rebalance_bar = self._bar_count
        return self._generate_intents(bars, context, target_weights)

    def _get_current_date(self, bars: dict[str, Bar], context: StrategyContext) -> Optional[date]:
        for bar in bars.values():
            dt = getattr(bar, "datetime", None) or getattr(bar, "date", None)
            if dt:
                return dt.date() if hasattr(dt, "date") else dt
        return None

    def _should_rebalance(self) -> bool:
        if self._last_rebalance_bar == 0:
            return True
        return (self._bar_count - self._last_rebalance_bar) >= self._config.rebalance_days

    def _update_price_history(self, bars: dict[str, Bar]) -> None:
        for sym, bar in bars.items():
            self._price_history.setdefault(sym, []).append(bar.close)

    def _compute_factors(
        self, context: StrategyContext, universe: list[str], current_date: date,
    ) -> dict[str, list[FactorResult]]:
        results: dict[str, list[FactorResult]] = {}
        for factor in self._factors:
            factor_results: list[FactorResult] = []
            for sym in universe:
                fd = self._build_factor_data(context, sym, current_date)
                if fd is not None:
                    try:
                        fr = factor.compute(fd)
                        if fr is not None and math.isfinite(fr.value):
                            factor_results.append(fr)
                    except Exception:
                        pass
            results[factor.name] = factor_results
        return results

    def _build_factor_data(
        self, context: StrategyContext, symbol: str, current_date: date,
    ) -> Optional[FactorData]:
        prices = self._price_history.get(symbol, [])
        if not prices:
            return None
        return FactorData(
            symbol=symbol,
            trade_date=current_date,
            close=prices[-1],
            returns=[(prices[i] / prices[i - 1] - 1) for i in range(1, len(prices))] if len(prices) > 1 else None,
            fundamentals=getattr(context, "fundamentals", {}).get(symbol) if hasattr(context, "fundamentals") else None,
        )

    def _combine_factors(
        self, factor_results: dict[str, list[FactorResult]],
    ) -> list[FactorResult]:
        """加权合成多因子分数"""
        scores: dict[str, float] = {}
        counts: dict[str, int] = {}
        for f_name, results in factor_results.items():
            w = self._factor_weights.get(f_name, 1.0)
            # rank normalize
            sorted_r = sorted(results, key=lambda r: r.value)
            n = len(sorted_r)
            for rank, r in enumerate(sorted_r):
                norm = (rank + 1) / n if n > 0 else 0
                scores[r.symbol] = scores.get(r.symbol, 0) + w * norm
                counts[r.symbol] = counts.get(r.symbol, 0) + 1

        # 只保留出现在所有因子中的
        n_factors = len(factor_results)
        combined = [
            FactorResult(symbol=sym, factor_name="COMBINED", value=s / n_factors,
                         trade_date=None)
            for sym, s in scores.items()
            if counts.get(sym, 0) >= n_factors
        ]
        combined.sort(key=lambda r: r.value, reverse=not self._config.ascending)
        return combined

    def _select_stocks(self, scores: list[FactorResult]) -> list[FactorResult]:
        cfg = self._config
        if cfg.top_pct is not None:
            k = max(1, int(len(scores) * cfg.top_pct))
        else:
            k = cfg.top_k or 10
        return scores[:k]

    def _calculate_weights(self, selected: list[FactorResult]) -> dict[str, float]:
        cfg = self._config
        n = len(selected)
        if n == 0:
            return {}
        if cfg.weight_method == "factor_score":
            total = sum(max(r.value, 0) for r in selected)
            if total > 0:
                return {r.symbol: max(r.value, 0) / total for r in selected}
        # default: equal weight
        w = 1.0 / n
        return {r.symbol: w for r in selected}

    def _generate_intents(
        self, bars: dict[str, Bar], context: StrategyContext,
        target_weights: dict[str, float],
    ) -> list[OrderIntent]:
        intents: list[OrderIntent] = []
        current = set(context.positions.keys())
        targets = set(target_weights.keys())

        # SELL positions not in targets
        for sym in current - targets:
            qty = context.positions.get(sym, 0)
            if qty > 0:
                intents.append(OrderIntent(
                    symbol=sym, intent_type=IntentType.EXIT,
                    side=Side.SELL, qty=qty, reason="factor_out",
                ))

        # BUY targets not in positions
        for sym in targets - current:
            if sym in bars:
                target_value = context.nav * target_weights[sym]
                price = bars[sym].close
                if price > 0:
                    qty = int(target_value / price / 100) * 100
                    if qty > 0:
                        intents.append(OrderIntent(
                            symbol=sym, intent_type=IntentType.ENTRY,
                            side=Side.BUY, qty=qty, reason="factor_in",
                            metadata={"target_weight": target_weights[sym]},
                        ))

        # Adjust existing positions that are also in targets
        for sym in current & targets:
            if sym in bars:
                price = bars[sym].close
                if price > 0:
                    target_qty = int(context.nav * target_weights[sym] / price / 100) * 100
                    cur_qty = context.positions.get(sym, 0)
                    delta = target_qty - cur_qty
                    if delta > 0:
                        intents.append(OrderIntent(
                            symbol=sym, intent_type=IntentType.ENTRY,
                            side=Side.BUY, qty=delta, reason="factor_in",
                            metadata={"target_weight": target_weights[sym]},
                        ))
                    elif delta < 0:
                        intents.append(OrderIntent(
                            symbol=sym, intent_type=IntentType.EXIT,
                            side=Side.SELL, qty=abs(delta), reason="factor_out",
                        ))

        return intents

    @classmethod
    def param_space(cls) -> Any:
        from aipha.strategy.param_space import ParamSpace, ParamSpec, ParamType
        return ParamSpace("factor_based", [
            ParamSpec("top_k", ParamType.INT, 10, low=5, high=50, step=5, is_critical=True),
            ParamSpec("rebalance_days", ParamType.INT, 20, low=5, high=60, step=5, is_critical=True),
            ParamSpec("lookback_days", ParamType.INT, 60, low=20, high=120, step=20),
            ParamSpec("weight_method", ParamType.CHOICE, default="equal",
                      choices=("equal", "factor_score", "min_variance", "risk_parity", "max_sharpe")),
        ])
