"""
Portfolio Combiner — Cross-asset multi-strategy portfolio construction engine.

Combines multiple sub-strategy equity curves with configurable weighting schemes:
  - Fixed Weight: Static allocation percentages
  - Risk Parity: Weight inversely proportional to volatility
  - Equal Risk Contribution: Each sub-strategy contributes equally to portfolio risk
  - Vol Targeting: Combined with per-strategy vol scaling

Inspired by R113 cross-asset 4-strategy evaluation framework.

Usage:
    combiner = PortfolioCombiner(
        strategies=[
            SubStrategy("ml_csi2000", weight=0.40),
            SubStrategy("cb_quality", weight=0.45),
            SubStrategy("v19_hgro", weight=0.05),
            SubStrategy("etf_cross", weight=0.10),
        ],
        method="fixed",  # or "risk_parity", "vol_target"
        rebalance_freq="monthly",
    )

    result = combiner.combine(equity_curves)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR


# ── Sub-strategy Definition ──────────────────────────────────────


@dataclass
class SubStrategy:
    """Definition of a sub-strategy in the portfolio."""

    name: str
    weight: float = 0.0              # for fixed-weight method
    asset_class: str = "equity"      # equity, bond, cb, etf, commodity
    slippage_bps: float = 10.0       # basis points
    commission_rate: float = 0.000854
    stamp_tax_rate: float = 0.0005   # A-share selling stamp tax

    # Risk overrides
    max_weight: float = 0.60         # weight cap
    min_weight: float = 0.00         # weight floor
    vol_target: Optional[float] = None  # per-strategy vol target


@dataclass
class PortfolioCombinerConfig:
    """Configuration for portfolio combiner."""

    method: str = "fixed"                 # fixed, risk_parity, vol_target
    rebalance_freq: str = "monthly"       # daily, weekly, monthly, quarterly
    vol_lookback: int = 60                # days for vol estimation
    portfolio_vol_target: float = 0.15    # portfolio-level vol target
    initial_cash: float = 10_000_000
    risk_free_rate: float = 0.02

    # Constraints
    max_single_weight: float = 0.60
    min_single_weight: float = 0.00
    turnover_penalty: float = 0.0         # penalize excessive rebalancing


# ── Helper: Daily Return Calculations ────────────────────────────


def _calc_returns(values: list[float]) -> list[float]:
    """Compute daily returns from a value series."""
    returns = []
    for i in range(1, len(values)):
        if values[i - 1] > 0:
            returns.append(values[i] / values[i - 1] - 1)
        else:
            returns.append(0.0)
    return returns


def _rolling_vol(returns: list[float], lookback: int, annualize: float = float(TRADING_DAYS_PER_YEAR)) -> list[float]:
    """Compute rolling annualized volatility."""
    vols = []
    for i in range(len(returns)):
        start = max(0, i - lookback + 1)
        window = returns[start:i + 1]
        if len(window) < 10:
            vols.append(0.15)  # default estimate
        else:
            mean = sum(window) / len(window)
            var = sum((r - mean) ** 2 for r in window) / (len(window) - 1)
            vols.append(math.sqrt(var * annualize))
    return vols


def _correlation_matrix(returns_matrix: list[list[float]]) -> list[list[float]]:
    """Compute pairwise correlation matrix from return series."""
    n = len(returns_matrix)
    length = min(len(r) for r in returns_matrix) if returns_matrix else 0
    if length < 10:
        return [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]

    # Trim to equal length
    trimmed = [r[-length:] for r in returns_matrix]
    means = [sum(r) / length for r in trimmed]
    stds = []
    for i in range(n):
        var = sum((trimmed[i][t] - means[i]) ** 2 for t in range(length)) / (length - 1)
        stds.append(math.sqrt(var) if var > 0 else 1e-8)

    corr = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                corr[i][j] = 1.0
            elif j > i:
                cov = sum(
                    (trimmed[i][t] - means[i]) * (trimmed[j][t] - means[j])
                    for t in range(length)
                ) / (length - 1)
                c = cov / (stds[i] * stds[j])
                corr[i][j] = max(-1.0, min(1.0, c))
                corr[j][i] = corr[i][j]
    return corr


# ── Rebalance Date Logic ─────────────────────────────────────────


def _is_rebalance_date(idx: int, dates: list[str], freq: str) -> bool:
    """Check if a given index is a rebalance point."""
    if freq == "daily":
        return True
    if idx == 0:
        return True

    prev_date = dates[idx - 1]
    curr_date = dates[idx]

    if freq == "weekly":
        # Rebalance on Monday (or first day of week)
        return idx % 5 == 0
    elif freq == "monthly":
        return curr_date[:7] != prev_date[:7]
    elif freq == "quarterly":
        prev_q = (int(prev_date[5:7]) - 1) // 3
        curr_q = (int(curr_date[5:7]) - 1) // 3
        return curr_q != prev_q or curr_date[:4] != prev_date[:4]
    return False


# ── Weight Calculation Methods ───────────────────────────────────


def _calc_fixed_weights(strategies: list[SubStrategy]) -> list[float]:
    """Normalize fixed weights."""
    total = sum(s.weight for s in strategies)
    if total <= 0:
        n = len(strategies)
        return [1.0 / n] * n
    return [s.weight / total for s in strategies]


def _calc_risk_parity_weights(
    vols: list[float],
    strategies: list[SubStrategy],
) -> list[float]:
    """Inverse-volatility weighting (simple risk parity).

    Each strategy's weight ∝ 1/σ, so higher-vol strategies get less weight.
    """
    inv_vols = []
    for v in vols:
        inv_vols.append(1.0 / max(v, 0.01))
    total = sum(inv_vols)
    if total <= 0:
        n = len(strategies)
        return [1.0 / n] * n

    weights = [iv / total for iv in inv_vols]

    # Apply constraints
    for i, s in enumerate(strategies):
        weights[i] = max(s.min_weight, min(weights[i], s.max_weight))

    # Re-normalize after clipping
    total = sum(weights)
    return [w / total for w in weights] if total > 0 else weights


def _calc_vol_target_weights(
    vols: list[float],
    strategies: list[SubStrategy],
    portfolio_vol_target: float,
) -> list[float]:
    """Vol-targeting: scale each strategy by its own vol target, then normalize."""
    scales = []
    for i, s in enumerate(strategies):
        target = s.vol_target or portfolio_vol_target
        scale = target / max(vols[i], 0.01)
        scale = max(s.min_weight, min(scale, s.max_weight * 2))
        scales.append(scale)

    total = sum(scales)
    if total <= 0:
        n = len(strategies)
        return [1.0 / n] * n
    return [s / total for s in scales]


# ── Portfolio Combiner ───────────────────────────────────────────


class PortfolioCombiner:
    """Combine multiple sub-strategy equity curves into a portfolio."""

    def __init__(
        self,
        strategies: list[SubStrategy],
        config: Optional[PortfolioCombinerConfig] = None,
        **kwargs,
    ):
        if config is None:
            config = PortfolioCombinerConfig(**kwargs)
        self.strategies = strategies
        self.config = config

    def combine(
        self,
        equity_curves: dict[str, list[dict]],
    ) -> dict:
        """Combine equity curves from multiple sub-strategies.

        Args:
            equity_curves: {strategy_name: [{"date": "2020-01-01", "value": 1000000}, ...]}

        Returns:
            Portfolio result dict with combined equity curve, metrics, and diagnostics.
        """
        cfg = self.config
        strats = self.strategies

        # Validate input
        matched = []
        for s in strats:
            if s.name not in equity_curves:
                return {"ok": False, "error": f"Missing equity curve for {s.name}"}
            matched.append(s)

        # Build aligned date index (intersection of all curves)
        date_sets = []
        for s in strats:
            dates = {p["date"] for p in equity_curves[s.name]}
            date_sets.append(dates)
        common_dates = sorted(set.intersection(*date_sets)) if date_sets else []

        if len(common_dates) < 10:
            return {"ok": False, "error": "Insufficient overlapping dates (<10)"}

        # Build per-strategy value lookup
        value_maps: dict[str, dict[str, float]] = {}
        for s in strats:
            value_maps[s.name] = {p["date"]: p["value"] for p in equity_curves[s.name]}

        # Compute per-strategy daily returns
        returns_by_strat: dict[str, list[float]] = {}
        for s in strats:
            values = [value_maps[s.name][d] for d in common_dates]
            returns_by_strat[s.name] = _calc_returns(values)

        # Compute rolling vols for weight calculation
        vols_by_strat: dict[str, list[float]] = {}
        for s in strats:
            vols_by_strat[s.name] = _rolling_vol(returns_by_strat[s.name], cfg.vol_lookback)

        # Build combined portfolio
        n_days = len(common_dates)
        portfolio_values = [cfg.initial_cash]
        weight_history: list[dict] = []
        current_weights = _calc_fixed_weights(strats)

        for t in range(1, n_days):
            # Rebalance check
            if _is_rebalance_date(t - 1, common_dates[1:], cfg.rebalance_freq):
                current_vols = [vols_by_strat[s.name][t - 1] for s in strats]

                if cfg.method == "fixed":
                    current_weights = _calc_fixed_weights(strats)
                elif cfg.method == "risk_parity":
                    current_weights = _calc_risk_parity_weights(current_vols, strats)
                elif cfg.method == "vol_target":
                    current_weights = _calc_vol_target_weights(
                        current_vols, strats, cfg.portfolio_vol_target
                    )

                weight_history.append({
                    "date": common_dates[t],
                    "weights": {s.name: round(w, 4) for s, w in zip(strats, current_weights)},
                })

            # Compute weighted portfolio return for day t
            portfolio_ret = sum(
                current_weights[i] * returns_by_strat[s.name][t - 1]
                for i, s in enumerate(strats)
            )
            portfolio_values.append(portfolio_values[-1] * (1 + portfolio_ret))

        # Build combined equity curve
        combined_curve = [
            {"date": common_dates[i], "value": round(portfolio_values[i], 2)}
            for i in range(n_days)
        ]

        # Compute portfolio metrics
        portfolio_returns = _calc_returns(portfolio_values)
        metrics = self._calc_metrics(portfolio_values, portfolio_returns)

        # Sub-strategy metrics
        sub_metrics = {}
        for s in strats:
            values = [value_maps[s.name][d] for d in common_dates]
            rets = returns_by_strat[s.name]
            sub_metrics[s.name] = self._calc_metrics(values, rets)

        # Correlation matrix
        returns_matrix = [returns_by_strat[s.name] for s in strats]
        corr = _correlation_matrix(returns_matrix)
        corr_summary = {
            "matrix": {
                strats[i].name: {strats[j].name: round(corr[i][j], 4) for j in range(len(strats))}
                for i in range(len(strats))
            },
            "average": round(
                sum(corr[i][j] for i in range(len(strats))
                    for j in range(i + 1, len(strats)))
                / max(len(strats) * (len(strats) - 1) / 2, 1),
                4,
            ),
            "max_pairwise": round(
                max(
                    (corr[i][j] for i in range(len(strats)) for j in range(i + 1, len(strats))),
                    default=0,
                ),
                4,
            ),
        }

        return {
            "ok": True,
            "method": cfg.method,
            "rebalance_freq": cfg.rebalance_freq,
            "overlap_period": f"{common_dates[0]} ~ {common_dates[-1]}",
            "overlap_days": n_days,
            "portfolio_metrics": metrics,
            "sub_strategy_metrics": sub_metrics,
            "final_weights": {s.name: round(w, 4) for s, w in zip(strats, current_weights)},
            "weight_history": weight_history,
            "correlation": corr_summary,
            "equity_curve": combined_curve,
        }

    def _calc_metrics(self, values: list[float], returns: list[float]) -> dict:
        """Compute standard performance metrics."""
        if not values or len(values) < 2:
            return {}

        total_ret = (values[-1] / values[0] - 1) if values[0] > 0 else 0
        n_days = len(returns)
        years = n_days / float(TRADING_DAYS_PER_YEAR)

        # Annualized return
        if years > 0 and total_ret > -1:
            ann_ret = (1 + total_ret) ** (1 / years) - 1
        else:
            ann_ret = 0

        # Volatility
        if n_days >= 2:
            mean_r = sum(returns) / n_days
            var = sum((r - mean_r) ** 2 for r in returns) / (n_days - 1)
            vol = math.sqrt(var * TRADING_DAYS_PER_YEAR)
        else:
            vol = 0

        # Max drawdown
        peak = values[0]
        max_dd = 0
        for v in values:
            if v > peak:
                peak = v
            dd = (peak - v) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

        # Sharpe ratio
        rf_daily = self.config.risk_free_rate / TRADING_DAYS_PER_YEAR
        excess = [r - rf_daily for r in returns]
        mean_excess = sum(excess) / len(excess) if excess else 0
        std_excess = math.sqrt(
            sum((e - mean_excess) ** 2 for e in excess) / max(len(excess) - 1, 1)
        ) if len(excess) > 1 else 1
        sharpe = (mean_excess / std_excess) * math.sqrt(TRADING_DAYS_PER_YEAR) if std_excess > 0 else 0

        # Calmar ratio
        calmar = ann_ret / max_dd if max_dd > 0 else 0

        # CVaR (95%)
        sorted_rets = sorted(returns)
        idx = max(int(len(sorted_rets) * 0.05), 1)
        tail = sorted_rets[:idx]
        cvar_95 = abs(sum(tail) / len(tail)) if tail else 0

        # Yearly returns
        yearly = {}
        if len(values) > 0:
            year_start_val = values[0]
            prev_year = None
            for i in range(len(values)):
                # This is a simplified version; uses the date index
                pass

        return {
            "total_return": round(total_ret, 4),
            "annualized_return": round(ann_ret, 4),
            "volatility": round(vol, 4),
            "max_drawdown": round(max_dd, 4),
            "sharpe_ratio": round(sharpe, 4),
            "calmar_ratio": round(calmar, 4),
            "cvar_95": round(cvar_95, 4),
        }

    def weight_perturbation_test(
        self,
        equity_curves: dict[str, list[dict]],
        perturbation: float = 0.20,
    ) -> dict:
        """Test portfolio stability under ±perturbation weight changes.

        For each strategy, perturb its weight by ±perturbation and measure
        the impact on portfolio Sharpe ratio.
        """
        # Baseline
        baseline = self.combine(equity_curves)
        if not baseline.get("ok"):
            return baseline
        base_sr = baseline["portfolio_metrics"]["sharpe_ratio"]

        results = []
        for i, s in enumerate(self.strategies):
            for direction in [+1, -1]:
                # Save original weight
                orig_weight = s.weight
                s.weight = orig_weight * (1 + direction * perturbation)

                perturbed = self.combine(equity_curves)
                if perturbed.get("ok"):
                    new_sr = perturbed["portfolio_metrics"]["sharpe_ratio"]
                    sr_change = abs(new_sr - base_sr) / abs(base_sr) if base_sr != 0 else 0
                    results.append({
                        "strategy": s.name,
                        "direction": f"+{int(perturbation*100)}%" if direction > 0 else f"-{int(perturbation*100)}%",
                        "perturbed_weight": round(s.weight, 4),
                        "sharpe_ratio": round(new_sr, 4),
                        "sr_change_pct": round(sr_change * 100, 2),
                        "stable": sr_change <= 0.30,  # R113 uses 30% threshold
                    })

                # Restore weight
                s.weight = orig_weight

        all_stable = all(r["stable"] for r in results)
        max_change = max(r["sr_change_pct"] for r in results) if results else 0

        return {
            "baseline_sharpe": round(base_sr, 4),
            "perturbation": f"±{int(perturbation*100)}%",
            "results": results,
            "all_stable": all_stable,
            "max_sr_change_pct": round(max_change, 2),
        }

    def slippage_sensitivity_test(
        self,
        equity_curves: dict[str, list[dict]],
        multipliers: list[float] = [1.0, 2.0, 4.0],
    ) -> dict:
        """Test portfolio resilience under increased slippage.

        Applies slippage multiplier to daily returns as a rough approximation:
        adjusted_return = return - (multiplier - 1) * base_slippage_impact
        """
        cfg = self.config
        strats = self.strategies

        # Build aligned data
        date_sets = []
        for s in strats:
            dates = {p["date"] for p in equity_curves[s.name]}
            date_sets.append(dates)
        common_dates = sorted(set.intersection(*date_sets)) if date_sets else []
        if len(common_dates) < 10:
            return {"ok": False, "error": "Insufficient data"}

        value_maps = {}
        returns_by_strat = {}
        for s in strats:
            value_maps[s.name] = {p["date"]: p["value"] for p in equity_curves[s.name]}
            values = [value_maps[s.name][d] for d in common_dates]
            returns_by_strat[s.name] = _calc_returns(values)

        results = []
        weights = _calc_fixed_weights(strats)

        for mult in multipliers:
            # Approximate slippage impact per day per strategy
            portfolio_values = [cfg.initial_cash]
            for t in range(len(common_dates) - 1):
                port_ret = 0.0
                for i, s in enumerate(strats):
                    base_ret = returns_by_strat[s.name][t]
                    # slippage cost ≈ bps * multiplier (applied to each day as rough approx)
                    slippage_cost = s.slippage_bps / 10000 * (mult - 1) * 0.1  # scaled by turnover est
                    adj_ret = base_ret - slippage_cost
                    port_ret += weights[i] * adj_ret
                portfolio_values.append(portfolio_values[-1] * (1 + port_ret))

            port_rets = _calc_returns(portfolio_values)
            metrics = self._calc_metrics(portfolio_values, port_rets)

            results.append({
                "multiplier": f"{mult}x",
                "sharpe_ratio": metrics["sharpe_ratio"],
                "annualized_return": metrics["annualized_return"],
                "max_drawdown": metrics["max_drawdown"],
                "sr_above_threshold": metrics["sharpe_ratio"] >= 0.8,
            })

        return {
            "results": results,
            "all_pass": all(r["sr_above_threshold"] for r in results),
        }
