"""
Core performance metrics calculation engine for trading portfolios.

Computes key trading performance indicators from trade records and
equity curve data. Works for both live trading and backtesting.

Metrics computed:
  - Total return, annualized return (CAGR)
  - Sharpe ratio, Sortino ratio, Calmar ratio
  - Maximum drawdown (depth, duration, recovery time)
  - Win rate, profit/loss ratio, profit factor
  - Average holding period (days)
  - Consecutive win/loss streaks
  - Best/worst single trade
  - Daily/monthly return distribution
  - Volatility (annualized)
  - VaR / CVaR (95%)
  - Composite performance score

Input format:
  {
    "trades": [
      {
        "tradeId": "...",
        "symbol": "...",
        "entryTime": "2024-01-01",
        "exitTime": "2024-01-15",
        "entryPrice": 10.0,
        "exitPrice": 11.0,
        "quantity": 100,
        "grossPnl": 100.0,
        "commission": 5.0,
        "netPnl": 95.0,
        "holdingDays": 14,
        "strategyId": "..."
      },
      ...
    ],
    "equityCurve": [
      { "date": "2024-01-01", "value": 100000.0 },
      ...
    ],
    "initialCash": 100000.0,
    "riskFreeRate": 0.03,
    "action": "full_report"
  }

Actions:
  full_report    — all metrics
  trade_stats    — trade-level statistics only
  risk_metrics   — risk metrics from equity curve only
  holding_analysis — holding period analysis
"""

from __future__ import annotations

import json
import math
import sys
from typing import Any, Optional


# ── Main Entry ──────────────────────────────────────────────────


def compute_performance_metrics(config: dict) -> dict:
    """Compute comprehensive performance metrics.

    See module docstring for config format.
    """
    action = config.get("action", "full_report")
    trades = config.get("trades", [])
    curve = config.get("equityCurve", [])
    init_cash = config.get("initialCash", 100000)
    risk_free = config.get("riskFreeRate", 0.03)

    if action == "trade_stats":
        return _compute_trade_stats(trades)
    elif action == "risk_metrics":
        return _compute_risk_metrics(curve, init_cash, risk_free)
    elif action == "holding_analysis":
        return _compute_holding_analysis(trades)
    else:
        # full_report
        result = {}
        if trades:
            result["tradeStats"] = _compute_trade_stats(trades)
            result["holdingAnalysis"] = _compute_holding_analysis(trades)
        if curve and len(curve) >= 2:
            result["riskMetrics"] = _compute_risk_metrics(curve, init_cash, risk_free)
        if trades or (curve and len(curve) >= 2):
            result["compositeScore"] = _compute_composite_score(
                result.get("tradeStats", {}),
                result.get("riskMetrics", {}),
            )
        return result


# ── Trade Statistics ────────────────────────────────────────────


def _compute_trade_stats(trades: list[dict]) -> dict:
    """Compute trade-level performance statistics."""
    if not trades:
        return {"error": "No trades provided"}

    completed = [t for t in trades if t.get("exitTime")]
    if not completed:
        return {"error": "No completed trades found", "openTrades": len(trades)}

    pnls = [t.get("netPnl", 0) for t in completed]
    gross_pnls = [t.get("grossPnl", 0) for t in completed]
    commissions = [t.get("commission", 0) for t in completed]

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    flat = [p for p in pnls if p == 0]

    total_trades = len(completed)
    win_count = len(wins)
    loss_count = len(losses)

    # Win rate
    win_rate = win_count / total_trades if total_trades > 0 else 0

    # Average win / loss
    avg_win = sum(wins) / win_count if win_count > 0 else 0
    avg_loss = abs(sum(losses) / loss_count) if loss_count > 0 else 0

    # Profit/loss ratio (盈亏比)
    profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else (
        float("inf") if avg_win > 0 else 0
    )

    # Profit factor
    total_wins = sum(wins)
    total_losses = abs(sum(losses))
    profit_factor = total_wins / total_losses if total_losses > 0 else (
        float("inf") if total_wins > 0 else 0
    )

    # Best / worst single trade
    best_trade = max(pnls) if pnls else 0
    worst_trade = min(pnls) if pnls else 0
    best_trade_idx = pnls.index(best_trade) if pnls else -1
    worst_trade_idx = pnls.index(worst_trade) if pnls else -1

    best_info = _trade_summary(completed[best_trade_idx]) if best_trade_idx >= 0 else None
    worst_info = _trade_summary(completed[worst_trade_idx]) if worst_trade_idx >= 0 else None

    # Consecutive streaks
    streaks = _compute_streaks(pnls)

    # Return distribution
    return_pcts = []
    for t in completed:
        entry_val = t.get("entryPrice", 0) * t.get("quantity", 0)
        if entry_val > 0:
            return_pcts.append(t.get("netPnl", 0) / entry_val)

    # Expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss
    expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss

    # Total commission impact
    total_commission = sum(commissions)
    total_gross = sum(gross_pnls)
    total_net = sum(pnls)

    result = {
        "totalTrades": total_trades,
        "winCount": win_count,
        "lossCount": loss_count,
        "flatCount": len(flat),
        "winRate": _r4(win_rate),
        "avgWin": _r2(avg_win),
        "avgLoss": _r2(avg_loss),
        "profitLossRatio": _r4(profit_loss_ratio),
        "profitFactor": _r4(profit_factor),
        "expectancy": _r2(expectancy),
        "totalGrossPnl": _r2(total_gross),
        "totalNetPnl": _r2(total_net),
        "totalCommission": _r2(total_commission),
        "bestTrade": {
            "pnl": _r2(best_trade),
            **(best_info or {}),
        },
        "worstTrade": {
            "pnl": _r2(worst_trade),
            **(worst_info or {}),
        },
        "maxConsecutiveWins": streaks["maxConsecutiveWins"],
        "maxConsecutiveLosses": streaks["maxConsecutiveLosses"],
        "currentStreak": streaks["currentStreak"],
        "currentStreakType": streaks["currentStreakType"],
    }

    if return_pcts:
        result["avgReturnPct"] = _r4(_mean(return_pcts))
        result["medianReturnPct"] = _r4(_median(return_pcts))
        result["returnStdDev"] = _r4(_std_dev(return_pcts))

    return result


def _trade_summary(trade: dict) -> dict:
    """Extract summary info from a single trade record."""
    return {
        "symbol": trade.get("symbol", ""),
        "entryTime": trade.get("entryTime", ""),
        "exitTime": trade.get("exitTime", ""),
        "holdingDays": trade.get("holdingDays", 0),
    }


def _compute_streaks(pnls: list[float]) -> dict:
    """Compute consecutive win/loss streaks."""
    max_wins = 0
    max_losses = 0
    current_streak = 0
    current_type = "flat"  # "win", "loss", or "flat"

    streak = 0
    prev_type = "flat"

    for p in pnls:
        if p > 0:
            cur_type = "win"
        elif p < 0:
            cur_type = "loss"
        else:
            cur_type = "flat"

        if cur_type == prev_type and cur_type != "flat":
            streak += 1
        else:
            if prev_type == "win":
                max_wins = max(max_wins, streak)
            elif prev_type == "loss":
                max_losses = max(max_losses, streak)
            streak = 1 if cur_type != "flat" else 0

        prev_type = cur_type

    # Handle last streak
    if prev_type == "win":
        max_wins = max(max_wins, streak)
    elif prev_type == "loss":
        max_losses = max(max_losses, streak)

    # Current streak (from the end)
    current_streak = 0
    current_type = "flat"
    for p in reversed(pnls):
        if p > 0:
            t = "win"
        elif p < 0:
            t = "loss"
        else:
            continue
        if current_type == "flat":
            current_type = t
            current_streak = 1
        elif t == current_type:
            current_streak += 1
        else:
            break

    return {
        "maxConsecutiveWins": max_wins,
        "maxConsecutiveLosses": max_losses,
        "currentStreak": current_streak,
        "currentStreakType": current_type,
    }


# ── Risk Metrics (Equity Curve Based) ──────────────────────────


def _compute_risk_metrics(
    curve: list[dict], init_cash: float, risk_free: float
) -> dict:
    """Compute risk metrics from equity curve."""
    if len(curve) < 2:
        return {"error": "Insufficient data points (need >= 2)"}

    values = [p["value"] for p in curve]
    daily_returns = _calc_returns(values)

    total_return = (values[-1] / values[0] - 1) if values[0] > 0 else 0
    trading_days = len(daily_returns)
    years = trading_days / 252

    # Annualized return (CAGR)
    annualized_return = _annualized_return(total_return, years) if years > 0 else 0

    # Volatility
    volatility = _std_dev(daily_returns) * math.sqrt(252) if daily_returns else 0

    # Drawdown analysis
    dd_info = _max_drawdown_analysis(values, curve)

    # Sharpe, Sortino, Calmar
    sharpe = _sharpe_ratio(daily_returns, risk_free)
    sortino = _sortino_ratio(daily_returns, risk_free)
    calmar = annualized_return / dd_info["maxDrawdown"] if dd_info["maxDrawdown"] > 0 else 0

    # VaR / CVaR (95%)
    var_95 = _value_at_risk(daily_returns, 0.05)
    cvar_95 = _conditional_var(daily_returns, 0.05)

    # Monthly returns
    monthly = _monthly_returns(curve)

    # Win/loss days
    win_days = sum(1 for r in daily_returns if r > 0)
    loss_days = sum(1 for r in daily_returns if r < 0)
    day_win_rate = win_days / trading_days if trading_days > 0 else 0

    return {
        "totalReturn": _r4(total_return),
        "annualizedReturn": _r4(annualized_return),
        "annualizedVolatility": _r4(volatility),
        "maxDrawdown": _r4(dd_info["maxDrawdown"]),
        "maxDrawdownDuration": dd_info["maxDrawdownDuration"],
        "maxDrawdownStart": dd_info["maxDrawdownStart"],
        "maxDrawdownEnd": dd_info["maxDrawdownEnd"],
        "maxDrawdownRecoveryDays": dd_info["maxDrawdownRecoveryDays"],
        "sharpeRatio": _r4(sharpe),
        "sortinoRatio": _r4(sortino),
        "calmarRatio": _r4(calmar),
        "var95": _r4(var_95),
        "cvar95": _r4(cvar_95),
        "tradingDays": trading_days,
        "winDays": win_days,
        "lossDays": loss_days,
        "dayWinRate": _r4(day_win_rate),
        "avgDailyReturn": _r4(_mean(daily_returns)) if daily_returns else 0,
        "bestDay": _r4(max(daily_returns)) if daily_returns else 0,
        "worstDay": _r4(min(daily_returns)) if daily_returns else 0,
        "monthlyReturns": monthly,
    }


def _max_drawdown_analysis(values: list[float], curve: list[dict]) -> dict:
    """Enhanced drawdown analysis with dates and recovery tracking."""
    if not values:
        return {
            "maxDrawdown": 0, "maxDrawdownDuration": 0,
            "maxDrawdownStart": "", "maxDrawdownEnd": "",
            "maxDrawdownRecoveryDays": 0,
        }

    peak = values[0]
    max_dd = 0.0
    dd_start_idx = 0
    dd_end_idx = 0
    peak_idx = 0
    recovery_idx = -1

    for i, v in enumerate(values):
        if v >= peak:
            peak = v
            peak_idx = i
        else:
            dd = (peak - v) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
                dd_start_idx = peak_idx
                dd_end_idx = i

    # Find recovery point (when value returns to the peak at dd_start_idx)
    peak_at_start = values[dd_start_idx]
    recovery_days = 0
    for i in range(dd_end_idx + 1, len(values)):
        if values[i] >= peak_at_start:
            recovery_idx = i
            recovery_days = i - dd_end_idx
            break

    start_date = curve[dd_start_idx]["date"] if dd_start_idx < len(curve) else ""
    end_date = curve[dd_end_idx]["date"] if dd_end_idx < len(curve) else ""

    return {
        "maxDrawdown": max_dd,
        "maxDrawdownDuration": dd_end_idx - dd_start_idx,
        "maxDrawdownStart": start_date,
        "maxDrawdownEnd": end_date,
        "maxDrawdownRecoveryDays": recovery_days if recovery_idx >= 0 else -1,
    }


# ── Holding Period Analysis ─────────────────────────────────────


def _compute_holding_analysis(trades: list[dict]) -> dict:
    """Analyze holding periods from trade records."""
    completed = [t for t in trades if t.get("exitTime")]
    if not completed:
        return {"error": "No completed trades"}

    holding_days = []
    for t in completed:
        hd = t.get("holdingDays")
        if hd is not None and hd >= 0:
            holding_days.append(hd)
        elif t.get("entryTime") and t.get("exitTime"):
            # Calculate from dates
            hd = _calc_holding_days(t["entryTime"], t["exitTime"])
            holding_days.append(hd)

    if not holding_days:
        return {"error": "No holding period data available"}

    # Group by short/medium/long term
    short_term = [d for d in holding_days if d <= 5]      # T+5 or less
    medium_term = [d for d in holding_days if 5 < d <= 20]  # 1-4 weeks
    long_term = [d for d in holding_days if d > 20]        # > 1 month

    # Holding days vs PnL correlation
    win_holding = []
    loss_holding = []
    for t in completed:
        hd = t.get("holdingDays", 0)
        if hd is None:
            continue
        if t.get("netPnl", 0) > 0:
            win_holding.append(hd)
        elif t.get("netPnl", 0) < 0:
            loss_holding.append(hd)

    return {
        "avgHoldingDays": _r2(_mean(holding_days)),
        "medianHoldingDays": _r2(_median(holding_days)),
        "minHoldingDays": min(holding_days),
        "maxHoldingDays": max(holding_days),
        "shortTermTrades": len(short_term),
        "mediumTermTrades": len(medium_term),
        "longTermTrades": len(long_term),
        "shortTermPct": _r4(len(short_term) / len(holding_days)),
        "mediumTermPct": _r4(len(medium_term) / len(holding_days)),
        "longTermPct": _r4(len(long_term) / len(holding_days)),
        "avgWinHoldingDays": _r2(_mean(win_holding)) if win_holding else 0,
        "avgLossHoldingDays": _r2(_mean(loss_holding)) if loss_holding else 0,
    }


def _calc_holding_days(entry_time: str, exit_time: str) -> int:
    """Calculate holding days from date strings (YYYY-MM-DD...)."""
    try:
        from datetime import datetime
        entry = datetime.strptime(entry_time[:10], "%Y-%m-%d")
        exit_d = datetime.strptime(exit_time[:10], "%Y-%m-%d")
        return max((exit_d - entry).days, 0)
    except (ValueError, TypeError):
        return 0


# ── Composite Score ─────────────────────────────────────────────


def _compute_composite_score(trade_stats: dict, risk_metrics: dict) -> dict:
    """Compute a composite performance score (0-100).

    Scoring dimensions (weighted):
      - Profitability (25%): total return, annualized return
      - Risk-adjusted (25%): Sharpe, Sortino
      - Risk control (25%): max drawdown, volatility
      - Consistency (25%): win rate, profit/loss ratio, streaks
    """
    scores = {}

    # Profitability (25%)
    ann_ret = risk_metrics.get("annualizedReturn", 0)
    # 15%+ annual return → full score; 0% → 0; negative → negative score
    profitability = min(max(ann_ret / 0.15, -1), 1) * 100
    scores["profitability"] = _r2(max(profitability, 0))

    # Risk-adjusted returns (25%)
    sharpe = risk_metrics.get("sharpeRatio", 0)
    # Sharpe >= 2.0 → full score; 0 → 0
    risk_adj = min(max(sharpe / 2.0, -1), 1) * 100
    scores["riskAdjusted"] = _r2(max(risk_adj, 0))

    # Risk control (25%)
    max_dd = abs(risk_metrics.get("maxDrawdown", 0))
    # Max drawdown < 5% → 100; > 30% → 0
    if max_dd <= 0.05:
        risk_ctrl = 100
    elif max_dd >= 0.30:
        risk_ctrl = 0
    else:
        risk_ctrl = (0.30 - max_dd) / 0.25 * 100
    scores["riskControl"] = _r2(risk_ctrl)

    # Consistency (25%)
    win_rate = trade_stats.get("winRate", 0)
    pl_ratio = trade_stats.get("profitLossRatio", 0)
    if isinstance(pl_ratio, float) and math.isinf(pl_ratio):
        pl_ratio = 3.0

    # Win rate score: 60%+ → full; 30% → 0
    wr_score = min(max((win_rate - 0.3) / 0.3, 0), 1) * 50
    # P/L ratio score: 2.0+ → full; 0 → 0
    pl_score = min(max(pl_ratio / 2.0, 0), 1) * 50
    consistency = wr_score + pl_score
    scores["consistency"] = _r2(consistency)

    # Composite (weighted average)
    composite = (
        scores["profitability"] * 0.25
        + scores["riskAdjusted"] * 0.25
        + scores["riskControl"] * 0.25
        + scores["consistency"] * 0.25
    )
    scores["composite"] = _r2(composite)

    # Rating
    if composite >= 80:
        scores["rating"] = "A"
    elif composite >= 60:
        scores["rating"] = "B"
    elif composite >= 40:
        scores["rating"] = "C"
    elif composite >= 20:
        scores["rating"] = "D"
    else:
        scores["rating"] = "F"

    return scores


# ── Helper Functions ────────────────────────────────────────────


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
    if years <= 0:
        return 0.0
    if total_return <= -1:
        return -1.0
    return (1 + total_return) ** (1 / years) - 1


def _mean(data: list[float]) -> float:
    return sum(data) / len(data) if data else 0.0


def _median(data: list[float]) -> float:
    if not data:
        return 0.0
    sorted_d = sorted(data)
    n = len(sorted_d)
    if n % 2 == 0:
        return (sorted_d[n // 2 - 1] + sorted_d[n // 2]) / 2
    return sorted_d[n // 2]


def _std_dev(data: list[float]) -> float:
    if len(data) < 2:
        return 0.0
    m = _mean(data)
    variance = sum((x - m) ** 2 for x in data) / (len(data) - 1)
    return math.sqrt(variance)


def _sharpe_ratio(daily_returns: list[float], risk_free_annual: float = 0.03) -> float:
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
    if not daily_returns:
        return 0.0
    sorted_returns = sorted(daily_returns)
    idx = int(len(sorted_returns) * alpha)
    return abs(sorted_returns[max(idx, 0)])


def _conditional_var(daily_returns: list[float], alpha: float = 0.05) -> float:
    if not daily_returns:
        return 0.0
    sorted_returns = sorted(daily_returns)
    idx = max(int(len(sorted_returns) * alpha), 1)
    tail = sorted_returns[:idx]
    return abs(_mean(tail))


def _monthly_returns(curve: list[dict]) -> list[dict]:
    if len(curve) < 2:
        return []
    monthly: list[dict] = []
    prev_value = curve[0]["value"]
    prev_month = curve[0]["date"][:7]
    for point in curve[1:]:
        month = point["date"][:7]
        if month != prev_month:
            ret = (point["value"] / prev_value - 1) if prev_value > 0 else 0
            monthly.append({"month": prev_month, "return": round(ret, 4)})
            prev_value = point["value"]
            prev_month = month
    last_val = curve[-1]["value"]
    ret = (last_val / prev_value - 1) if prev_value > 0 else 0
    monthly.append({"month": prev_month, "return": round(ret, 4)})
    return monthly


def _r2(v: float) -> float:
    """Round to 2 decimal places, handling inf."""
    if isinstance(v, float) and (math.isinf(v) or math.isnan(v)):
        return 0.0
    return round(v, 2)


def _r4(v: float) -> float:
    """Round to 4 decimal places, handling inf."""
    if isinstance(v, float) and (math.isinf(v) or math.isnan(v)):
        return 0.0
    return round(v, 4)


# ── CLI Entry Point ─────────────────────────────────────────────

if __name__ == "__main__":
    config = json.loads(sys.stdin.read())
    result = compute_performance_metrics(config)
    print(json.dumps(result, ensure_ascii=False))
