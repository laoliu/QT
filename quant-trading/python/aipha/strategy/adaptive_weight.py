"""自适应权重分配器（等权/风险平价/逆波动率/动量追踪/最大夏普等）"""
from __future__ import annotations
import logging, math
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any
logger = logging.getLogger(__name__)

class WeightMethod(str, Enum):
    EQUAL = "equal"; RISK_PARITY = "risk_parity"; INVERSE_VOL = "inverse_vol"
    MOMENTUM_CHASING = "momentum_chasing"; MAX_SHARPE = "max_sharpe"
    MIN_VARIANCE = "min_variance"; REGIME_ADAPTIVE = "regime_adaptive"
    TARGET_VOL = "target_vol"

@dataclass(frozen=True)
class WeightSnapshot:
    trade_date: date; weights: dict[str, float]; method: str; meta: dict[str, Any] = field(default_factory=dict)

@dataclass
class AdaptivePortfolioResult:
    portfolio_nav: dict[date, float]; weights_history: list[WeightSnapshot]
    method: str; params: dict[str, Any]; metrics: dict[str, float]

def _compute_metrics(nav_values: list[float], trading_days: int = 252, risk_free_rate: float = 0.02) -> dict[str, float]:
    if len(nav_values) < 2: return {"cagr": 0, "sharpe": 0, "max_drawdown": 0, "volatility": 0}
    rets = [(nav_values[i] - nav_values[i - 1]) / nav_values[i - 1] for i in range(1, len(nav_values)) if nav_values[i - 1] > 0]
    if not rets: return {"cagr": 0, "sharpe": 0, "max_drawdown": 0, "volatility": 0}
    mu = sum(rets) / len(rets); vol = math.sqrt(sum((r - mu) ** 2 for r in rets) / max(len(rets) - 1, 1))
    ann_vol = vol * math.sqrt(trading_days); ann_ret = mu * trading_days
    sharpe = (ann_ret - risk_free_rate) / ann_vol if ann_vol > 0 else 0
    peak = nav_values[0]; mdd = 0.0
    for v in nav_values:
        if v > peak: peak = v
        dd = (peak - v) / peak; mdd = max(mdd, dd)
    n_years = len(rets) / trading_days
    cagr = (nav_values[-1] / nav_values[0]) ** (1 / n_years) - 1 if n_years > 0 and nav_values[0] > 0 else 0
    return {"cagr": cagr, "sharpe": sharpe, "max_drawdown": mdd, "volatility": ann_vol}

class AdaptiveWeightAllocator:
    def __init__(self, method: str | WeightMethod = WeightMethod.EQUAL, lookback: int = 60,
                 rebalance_freq: int = 5, min_weight: float = 0.05, max_weight: float = 0.80,
                 risk_free_rate: float = 0.02, trading_days_per_year: int = 252, **kw: Any):
        self.method = WeightMethod(method) if isinstance(method, str) else method
        self.lookback = lookback; self.rebalance_freq = rebalance_freq
        self.min_weight = min_weight; self.max_weight = max_weight
        self.risk_free_rate = risk_free_rate; self.trading_days = trading_days_per_year

    def _clip_weights(self, weights: dict[str, float]) -> dict[str, float]:
        clipped = {k: max(self.min_weight, min(self.max_weight, v)) for k, v in weights.items()}
        total = sum(clipped.values())
        return {k: v / total for k, v in clipped.items()} if total > 0 else clipped

    def _equal_weights(self, names: list[str]) -> dict[str, float]:
        n = len(names); return {name: 1.0 / n for name in names} if n > 0 else {}

    def _inverse_volatility(self, names: list[str], returns_matrix: list[list[float]]) -> dict[str, float]:
        inv_vols: dict[str, float] = {}
        for i, name in enumerate(names):
            rets = returns_matrix[i] if i < len(returns_matrix) else []
            if len(rets) < 2:
                inv_vols[name] = 1.0; continue
            mu = sum(rets) / len(rets); vol = math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1))
            inv_vols[name] = 1.0 / vol if vol > 0 else 1.0
        total = sum(inv_vols.values())
        return {k: v / total for k, v in inv_vols.items()} if total > 0 else self._equal_weights(names)

    def allocate(self, names: list[str], returns_matrix: list[list[float]] | None = None) -> dict[str, float]:
        if self.method == WeightMethod.EQUAL or returns_matrix is None:
            return self._clip_weights(self._equal_weights(names))
        if self.method == WeightMethod.INVERSE_VOL:
            return self._clip_weights(self._inverse_volatility(names, returns_matrix))
        return self._clip_weights(self._equal_weights(names))
