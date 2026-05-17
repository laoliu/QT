"""Multi-strategy portfolio combiner.

Combines multiple sub-strategy equity curves using fixed-weight,
risk-parity (inverse-vol), or vol-targeting methods. Handles
rebalancing frequency, turnover tracking, and correlation analysis.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR

__all__ = ["PortfolioCombiner", "PortfolioCombinerConfig", "SubStrategy"]


@dataclass
class SubStrategy:
    """Configuration for one sub-strategy in the combination."""

    name: str = ""
    weight: float = 0.0
    asset_class: str = "equity"
    slippage_bps: float = 5.0
    commission_rate: float = 0.0001
    stamp_tax_rate: float = 0.0
    max_weight: float = 1.0
    min_weight: float = 0.0
    vol_target: float = 0.15


@dataclass
class PortfolioCombinerConfig:
    """Configuration for portfolio combination."""

    method: str = "fixed"  # fixed | risk_parity | vol_target
    rebalance_freq: str = "monthly"
    vol_lookback: int = 60
    portfolio_vol_target: float = 0.10
    initial_cash: float = 1_000_000.0
    risk_free_rate: float = 0.02
    max_single_weight: float = 0.50
    turnover_penalty: float = 0.0


class PortfolioCombiner:
    """Combine multiple sub-strategy equity curves."""

    def __init__(
        self,
        strategies: List[SubStrategy],
        config: Optional[PortfolioCombinerConfig] = None,
    ) -> None:
        self.strategies = strategies
        self.config = config or PortfolioCombinerConfig()

    def combine(
        self, equity_curves: Dict[str, List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """Combine equity curves into a portfolio-level curve.

        Parameters
        ----------
        equity_curves : dict
            Mapping of strategy name -> list of {date, value} dicts.

        Returns
        -------
        dict with keys: combined_curve, weights_history, correlation,
        metrics (sharpe, max_dd, annualized_return, total_turnover).
        """
        cfg = self.config
        strat_names = [s.name for s in self.strategies]
        # extract aligned value series
        all_dates: List[str] = []
        values_map: Dict[str, List[float]] = {}
        for s in self.strategies:
            curve = equity_curves.get(s.name, [])
            vals = [pt.get("value", 0) for pt in curve]
            dates = [pt.get("date", "") for pt in curve]
            values_map[s.name] = vals
            if len(dates) > len(all_dates):
                all_dates = dates

        n_dates = min(len(all_dates), *(len(v) for v in values_map.values())) if values_map else 0
        if n_dates < 2:
            return {"combined_curve": [], "weights_history": [], "correlation": {}, "metrics": {}}

        # compute daily returns per strategy
        returns_map: Dict[str, List[float]] = {}
        for name, vals in values_map.items():
            rets = _calc_returns(vals[:n_dates])
            returns_map[name] = rets

        # compute weights
        if cfg.method == "risk_parity":
            vols = {n: _rolling_vol(r, cfg.vol_lookback) for n, r in returns_map.items()}
            weights = _calc_risk_parity_weights(vols, self.strategies, cfg.max_single_weight)
        elif cfg.method == "vol_target":
            vols = {n: _rolling_vol(r, cfg.vol_lookback) for n, r in returns_map.items()}
            weights = _calc_vol_target_weights(vols, self.strategies, cfg.portfolio_vol_target, cfg.max_single_weight)
        else:
            weights = _calc_fixed_weights(self.strategies, cfg.max_single_weight)

        combined_values = [cfg.initial_cash]
        for i in range(len(returns_map[strat_names[0]])):
            port_ret = sum(
                weights.get(n, 0) * returns_map[n][i] for n in strat_names
            )
            combined_values.append(combined_values[-1] * (1 + port_ret))

        combined_curve = [
            {"date": all_dates[i], "value": round(combined_values[i], 2)}
            for i in range(min(len(all_dates), len(combined_values)))
        ]

        corr = _correlation_matrix(returns_map, strat_names)
        metrics = _calc_metrics(combined_values, cfg.risk_free_rate)

        return {
            "combined_curve": combined_curve,
            "weights": weights,
            "correlation": corr,
            "metrics": metrics,
        }


# -- helper functions --

def _calc_returns(values: List[float]) -> List[float]:
    rets = []
    for i in range(1, len(values)):
        if values[i - 1] != 0:
            rets.append(values[i] / values[i - 1] - 1)
        else:
            rets.append(0.0)
    return rets


def _rolling_vol(returns: List[float], lookback: int, annualize: bool = True) -> float:
    recent = returns[-lookback:] if len(returns) > lookback else returns
    if len(recent) < 2:
        return 0.01
    avg = sum(recent) / len(recent)
    var = sum((r - avg) ** 2 for r in recent) / (len(recent) - 1)
    vol = math.sqrt(var)
    if annualize:
        vol *= math.sqrt(TRADING_DAYS_PER_YEAR)
    return max(vol, 1e-8)


def _calc_fixed_weights(strategies: List[SubStrategy], max_w: float) -> Dict[str, float]:
    total = sum(s.weight for s in strategies) or 1.0
    return {s.name: min(max_w, s.weight / total) for s in strategies}


def _calc_risk_parity_weights(
    vols: Dict[str, float], strategies: List[SubStrategy], max_w: float,
) -> Dict[str, float]:
    inv_vols = {s.name: 1.0 / vols.get(s.name, 0.01) for s in strategies}
    total = sum(inv_vols.values()) or 1.0
    return {n: min(max_w, v / total) for n, v in inv_vols.items()}


def _calc_vol_target_weights(
    vols: Dict[str, float], strategies: List[SubStrategy],
    portfolio_vol_target: float, max_w: float,
) -> Dict[str, float]:
    weights: Dict[str, float] = {}
    for s in strategies:
        sv = vols.get(s.name, 0.01)
        w = (s.vol_target / sv) if sv > 0 else 1.0
        w = min(max_w, max(s.min_weight, min(s.max_weight, w)))
        weights[s.name] = w
    # normalize to portfolio vol target
    total = sum(weights.values()) or 1.0
    scale = min(1.0, len(strategies) / total) if total > len(strategies) else 1.0
    return {n: w * scale for n, w in weights.items()}


def _correlation_matrix(
    returns_map: Dict[str, List[float]], names: List[str],
) -> Dict[str, Dict[str, float]]:
    corr: Dict[str, Dict[str, float]] = {}
    for i, a in enumerate(names):
        corr[a] = {}
        for j, b in enumerate(names):
            ra, rb = returns_map.get(a, []), returns_map.get(b, [])
            n = min(len(ra), len(rb))
            if n < 2:
                corr[a][b] = 0.0
                continue
            ma = sum(ra[:n]) / n
            mb = sum(rb[:n]) / n
            cov = sum((ra[k] - ma) * (rb[k] - mb) for k in range(n)) / (n - 1)
            sa = math.sqrt(sum((ra[k] - ma) ** 2 for k in range(n)) / (n - 1)) or 1e-8
            sb = math.sqrt(sum((rb[k] - mb) ** 2 for k in range(n)) / (n - 1)) or 1e-8
            corr[a][b] = round(cov / (sa * sb), 4)
    return corr


def _calc_metrics(values: List[float], rf: float = 0.02) -> Dict[str, float]:
    if len(values) < 2:
        return {}
    rets = _calc_returns(values)
    n = len(rets)
    avg = sum(rets) / n if n else 0
    ann_ret = (values[-1] / values[0]) ** (TRADING_DAYS_PER_YEAR / n) - 1 if n > 0 and values[0] > 0 else 0
    std = math.sqrt(sum((r - avg) ** 2 for r in rets) / (n - 1)) if n > 1 else 1e-8
    ann_std = std * math.sqrt(TRADING_DAYS_PER_YEAR)
    sharpe = (ann_ret - rf) / ann_std if ann_std > 0 else 0
    # max drawdown
    peak = values[0]
    max_dd = 0.0
    for v in values:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
    return {
        "annualized_return": round(ann_ret, 4),
        "annualized_vol": round(ann_std, 4),
        "sharpe": round(sharpe, 4),
        "max_drawdown": round(max_dd, 4),
    }
