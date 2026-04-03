"""
Risk analysis and performance reporting engine.

Provides standalone risk analysis for portfolio returns,
separate from the backtest report manager. This module powers
the risk-analysis TypeScript tool.

Migrated from jtstock/stock_tools/report_manager.py with additional
modern risk metrics (Sortino, Calmar, VaR, CVaR).
"""

from __future__ import annotations

import json
import math
import sys
from typing import Any, Optional


def analyze_risk(config: dict) -> dict:
    """Analyze risk metrics from portfolio data.

    Config:
    {
      "equityCurve": [{ "date": "2020-01-01", "value": 100000 }, ...],
      "initialCash": 100000,
      "riskFreeRate": 0.03,       // annual risk-free rate
      "benchmarkCurve": [{ "date": "2020-01-01", "value": ... }],  // optional
      "trades": [{ "profit": 100, "returnPct": 0.02 }, ...]         // optional
    }
    """
    curve = config.get("equityCurve", [])
    init_cash = config.get("initialCash", 100000)
    risk_free = config.get("riskFreeRate", 0.03)
    benchmark = config.get("benchmarkCurve", [])
    trades = config.get("trades", [])

    if len(curve) < 2:
        return {"error": "Insufficient data points for risk analysis (need >= 2)"}

    values = [p["value"] for p in curve]
    dates = [p["date"] for p in curve]
    daily_returns = _calc_returns(values)

    # Basic metrics
    total_return = (values[-1] / values[0] - 1) if values[0] > 0 else 0
    trading_days = len(daily_returns)
    years = trading_days / 252

    annualized_return = _annualized_return(total_return, years) if years > 0 else 0

    # Volatility
    volatility = _std_dev(daily_returns) * math.sqrt(252) if daily_returns else 0

    # Drawdown analysis
    dd_info = _max_drawdown(values)

    # Sharpe
    sharpe = _sharpe_ratio(daily_returns, risk_free)

    # Sortino
    sortino = _sortino_ratio(daily_returns, risk_free)

    # Calmar
    calmar = annualized_return / dd_info["maxDrawdown"] if dd_info["maxDrawdown"] > 0 else 0

    # VaR and CVaR (95%)
    var_95 = _value_at_risk(daily_returns, 0.05)
    cvar_95 = _conditional_var(daily_returns, 0.05)

    # Win/loss from trades
    trade_stats = _trade_statistics(trades)

    # Monthly returns table
    monthly = _monthly_returns(curve)

    # Benchmark comparison
    benchmark_stats = {}
    if benchmark and len(benchmark) >= 2:
        bench_values = [p["value"] for p in benchmark]
        bench_returns = _calc_returns(bench_values)
        bench_total = (bench_values[-1] / bench_values[0] - 1) if bench_values[0] > 0 else 0
        bench_ann = _annualized_return(bench_total, years) if years > 0 else 0

        alpha, beta = _alpha_beta(daily_returns, bench_returns, risk_free)
        tracking_error = _tracking_error(daily_returns, bench_returns)
        info_ratio = _information_ratio(daily_returns, bench_returns)

        benchmark_stats = {
            "totalReturn": round(bench_total, 4),
            "annualizedReturn": round(bench_ann, 4),
            "alpha": round(alpha, 4),
            "beta": round(beta, 4),
            "trackingError": round(tracking_error, 4),
            "informationRatio": round(info_ratio, 4),
            "correlation": round(_correlation(daily_returns, bench_returns), 4),
        }

    result = {
        "totalReturn": round(total_return, 4),
        "annualizedReturn": round(annualized_return, 4),
        "annualizedVolatility": round(volatility, 4),
        "maxDrawdown": round(dd_info["maxDrawdown"], 4),
        "maxDrawdownDuration": dd_info["maxDrawdownDuration"],
        "maxDrawdownStart": dd_info["maxDrawdownStart"],
        "maxDrawdownEnd": dd_info["maxDrawdownEnd"],
        "sharpeRatio": round(sharpe, 4),
        "sortinoRatio": round(sortino, 4),
        "calmarRatio": round(calmar, 4),
        "var95": round(var_95, 4),
        "cvar95": round(cvar_95, 4),
        "tradingDays": trading_days,
        "monthlyReturns": monthly,
        **trade_stats,
    }

    if benchmark_stats:
        result["benchmark"] = benchmark_stats

    return result


# ── Helper Functions ─────────────────────────────────────────────


def _calc_returns(values: list[float]) -> list[float]:
    """Calculate daily returns from a value series."""
    returns = []
    for i in range(1, len(values)):
        if values[i - 1] > 0:
            returns.append(values[i] / values[i - 1] - 1)
        else:
            returns.append(0.0)
    return returns


def _annualized_return(total_return: float, years: float) -> float:
    """Convert total return to annualized return."""
    if years <= 0:
        return 0.0
    if total_return <= -1:
        return -1.0
    return (1 + total_return) ** (1 / years) - 1


def _mean(data: list[float]) -> float:
    return sum(data) / len(data) if data else 0.0


def _std_dev(data: list[float]) -> float:
    if len(data) < 2:
        return 0.0
    m = _mean(data)
    variance = sum((x - m) ** 2 for x in data) / (len(data) - 1)
    return math.sqrt(variance)


def _max_drawdown(values: list[float]) -> dict:
    """Compute max drawdown, duration, and dates."""
    if not values:
        return {"maxDrawdown": 0, "maxDrawdownDuration": 0,
                "maxDrawdownStart": "", "maxDrawdownEnd": ""}

    peak = values[0]
    max_dd = 0.0
    dd_start_idx = 0
    dd_end_idx = 0
    peak_idx = 0

    curr_dd_start = 0
    max_duration = 0
    dur_start = 0
    in_drawdown = False

    for i, v in enumerate(values):
        if v >= peak:
            peak = v
            peak_idx = i
            if in_drawdown:
                duration = i - dur_start
                if duration > max_duration:
                    max_duration = duration
                in_drawdown = False
        else:
            dd = (peak - v) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
                dd_start_idx = peak_idx
                dd_end_idx = i
            if not in_drawdown:
                in_drawdown = True
                dur_start = peak_idx

    if in_drawdown:
        duration = len(values) - 1 - dur_start
        if duration > max_duration:
            max_duration = duration

    return {
        "maxDrawdown": max_dd,
        "maxDrawdownDuration": max_duration,
        "maxDrawdownStart": str(dd_start_idx),
        "maxDrawdownEnd": str(dd_end_idx),
    }


def _sharpe_ratio(daily_returns: list[float], risk_free_annual: float = 0.03) -> float:
    """Annualized Sharpe ratio."""
    if len(daily_returns) < 2:
        return 0.0
    rf_daily = risk_free_annual / 252
    excess = [r - rf_daily for r in daily_returns]
    mean_excess = _mean(excess)
    std = _std_dev(excess)
    if std == 0:
        return 0.0
    return (mean_excess / std) * math.sqrt(252)


def _sortino_ratio(daily_returns: list[float], risk_free_annual: float = 0.03) -> float:
    """Annualized Sortino ratio (downside deviation only)."""
    if len(daily_returns) < 2:
        return 0.0
    rf_daily = risk_free_annual / 252
    excess = [r - rf_daily for r in daily_returns]
    mean_excess = _mean(excess)
    downside = [min(r, 0) ** 2 for r in excess]
    downside_dev = math.sqrt(sum(downside) / len(downside))
    if downside_dev == 0:
        return 0.0
    return (mean_excess / downside_dev) * math.sqrt(252)


def _value_at_risk(daily_returns: list[float], alpha: float = 0.05) -> float:
    """Historical VaR at given confidence level."""
    if not daily_returns:
        return 0.0
    sorted_returns = sorted(daily_returns)
    idx = int(len(sorted_returns) * alpha)
    return abs(sorted_returns[max(idx, 0)])


def _conditional_var(daily_returns: list[float], alpha: float = 0.05) -> float:
    """Conditional VaR (Expected Shortfall)."""
    if not daily_returns:
        return 0.0
    sorted_returns = sorted(daily_returns)
    idx = max(int(len(sorted_returns) * alpha), 1)
    tail = sorted_returns[:idx]
    return abs(_mean(tail))


def _correlation(x: list[float], y: list[float]) -> float:
    """Pearson correlation between two return series."""
    n = min(len(x), len(y))
    if n < 2:
        return 0.0
    mx, my = _mean(x[:n]), _mean(y[:n])
    cov = sum((x[i] - mx) * (y[i] - my) for i in range(n)) / n
    sx = math.sqrt(sum((x[i] - mx) ** 2 for i in range(n)) / n)
    sy = math.sqrt(sum((y[i] - my) ** 2 for i in range(n)) / n)
    if sx == 0 or sy == 0:
        return 0.0
    return cov / (sx * sy)


def _alpha_beta(
    returns: list[float], bench_returns: list[float], risk_free: float = 0.03
) -> tuple[float, float]:
    """CAPM alpha and beta."""
    n = min(len(returns), len(bench_returns))
    if n < 2:
        return 0.0, 1.0
    rf_daily = risk_free / 252

    excess_r = [returns[i] - rf_daily for i in range(n)]
    excess_b = [bench_returns[i] - rf_daily for i in range(n)]

    mean_r = _mean(excess_r)
    mean_b = _mean(excess_b)

    cov = sum((excess_r[i] - mean_r) * (excess_b[i] - mean_b) for i in range(n)) / n
    var_b = sum((excess_b[i] - mean_b) ** 2 for i in range(n)) / n

    beta = cov / var_b if var_b > 0 else 1.0
    alpha = (mean_r - beta * mean_b) * 252  # annualize
    return alpha, beta


def _tracking_error(returns: list[float], bench_returns: list[float]) -> float:
    """Annualized tracking error."""
    n = min(len(returns), len(bench_returns))
    if n < 2:
        return 0.0
    diff = [returns[i] - bench_returns[i] for i in range(n)]
    return _std_dev(diff) * math.sqrt(252)


def _information_ratio(returns: list[float], bench_returns: list[float]) -> float:
    """Information ratio = annualized excess return / tracking error."""
    n = min(len(returns), len(bench_returns))
    if n < 2:
        return 0.0
    diff = [returns[i] - bench_returns[i] for i in range(n)]
    te = _std_dev(diff) * math.sqrt(252)
    if te == 0:
        return 0.0
    return (_mean(diff) * 252) / te


def _trade_statistics(trades: list[dict]) -> dict:
    """Compute win rate, profit factor from trade list."""
    if not trades:
        return {
            "winRate": 0.0,
            "profitFactor": 0.0,
            "avgWin": 0.0,
            "avgLoss": 0.0,
            "totalTrades": 0,
        }

    wins = [t["profit"] for t in trades if t.get("profit", 0) > 0]
    losses = [t["profit"] for t in trades if t.get("profit", 0) < 0]

    total = len(wins) + len(losses)
    win_rate = len(wins) / total if total > 0 else 0

    avg_win = _mean(wins) if wins else 0
    avg_loss = abs(_mean(losses)) if losses else 0
    profit_factor = avg_win / avg_loss if avg_loss > 0 else 0

    return {
        "winRate": round(win_rate, 4),
        "profitFactor": round(profit_factor, 4),
        "avgWin": round(avg_win, 2),
        "avgLoss": round(avg_loss, 2),
        "totalTrades": total,
    }


def _monthly_returns(curve: list[dict]) -> list[dict]:
    """Compute monthly return table from equity curve."""
    if len(curve) < 2:
        return []

    monthly: list[dict] = []
    prev_value = curve[0]["value"]
    prev_month = curve[0]["date"][:7]  # "YYYY-MM"

    for point in curve[1:]:
        month = point["date"][:7]
        if month != prev_month:
            ret = (point["value"] / prev_value - 1) if prev_value > 0 else 0
            monthly.append({"month": prev_month, "return": round(ret, 4)})
            prev_value = point["value"]
            prev_month = month

    # Last partial month
    last_val = curve[-1]["value"]
    ret = (last_val / prev_value - 1) if prev_value > 0 else 0
    monthly.append({"month": prev_month, "return": round(ret, 4)})

    return monthly


if __name__ == "__main__":
    config = json.loads(sys.stdin.read())
    result = analyze_risk(config)
    print(json.dumps(result, ensure_ascii=False))
