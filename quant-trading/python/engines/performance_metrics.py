"""
Core performance metrics calculation engine.

Computes comprehensive trading performance indicators from trade records
and optional equity snapshots. Designed for live/paper trading report
generation, complementing the backtest-oriented ReportManager.

Metrics coverage:
  - Returns: total return, annualized return
  - Risk-adjusted: Sharpe ratio, Sortino ratio, Calmar ratio
  - Drawdown: max drawdown, max drawdown duration, current drawdown
  - Trade quality: win rate, payoff ratio, profit factor, expectancy
  - Holding period: average, max, min holding days
  - Streaks: max consecutive wins/losses
  - Cost: total commission, commission ratio
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class TradeRecord:
    """A single closed trade."""

    trade_id: str
    symbol: str
    entry_time: str          # ISO datetime
    exit_time: str           # ISO datetime
    entry_price: float
    exit_price: float
    quantity: int
    gross_pnl: float
    commission: float
    net_pnl: float
    holding_days: Optional[int] = None
    strategy_id: Optional[str] = None
    name: Optional[str] = None


@dataclass
class EquitySnapshot:
    """Daily equity snapshot."""

    date: str           # YYYY-MM-DD
    equity: float
    cash: float = 0.0
    market_value: float = 0.0
    daily_pnl: float = 0.0


@dataclass
class PerformanceMetricsResult:
    """Complete performance metrics output."""

    # Returns
    total_pnl: float = 0.0
    total_return_pct: float = 0.0
    annualized_return_pct: float = 0.0

    # Risk-adjusted
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0

    # Drawdown
    max_drawdown_pct: float = 0.0
    max_drawdown_amount: float = 0.0
    max_drawdown_days: int = 0
    current_drawdown_pct: float = 0.0

    # Trade quality
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    payoff_ratio: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    avg_trade_return: float = 0.0

    # Holding period
    avg_holding_days: float = 0.0
    max_holding_days: int = 0
    min_holding_days: int = 0

    # Streaks
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0

    # Cost
    total_commission: float = 0.0
    commission_ratio: float = 0.0

    # Metadata
    trading_days: int = 0
    calculated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "totalPnl": round(self.total_pnl, 2),
            "totalReturnPct": round(self.total_return_pct, 2),
            "annualizedReturnPct": round(self.annualized_return_pct, 2),
            "sharpeRatio": round(self.sharpe_ratio, 4),
            "sortinoRatio": round(self.sortino_ratio, 4),
            "calmarRatio": _finite_round(self.calmar_ratio, 4),
            "maxDrawdownPct": round(self.max_drawdown_pct, 2),
            "maxDrawdownAmount": round(self.max_drawdown_amount, 2),
            "maxDrawdownDays": self.max_drawdown_days,
            "currentDrawdownPct": round(self.current_drawdown_pct, 2),
            "totalTrades": self.total_trades,
            "winningTrades": self.winning_trades,
            "losingTrades": self.losing_trades,
            "winRate": round(self.win_rate, 2),
            "payoffRatio": _finite_round(self.payoff_ratio, 3),
            "profitFactor": _finite_round(self.profit_factor, 3),
            "expectancy": round(self.expectancy, 2),
            "avgTradeReturn": round(self.avg_trade_return, 2),
            "avgHoldingDays": round(self.avg_holding_days, 1),
            "maxHoldingDays": self.max_holding_days,
            "minHoldingDays": self.min_holding_days,
            "maxConsecutiveWins": self.max_consecutive_wins,
            "maxConsecutiveLosses": self.max_consecutive_losses,
            "totalCommission": round(self.total_commission, 2),
            "commissionRatio": round(self.commission_ratio, 2),
            "tradingDays": self.trading_days,
            "calculatedAt": self.calculated_at,
        }


def _finite_round(value: float, decimals: int) -> Any:
    if math.isinf(value):
        return "Infinity"
    if math.isnan(value):
        return "NaN"
    return round(value, decimals)


# ── Core computation ────────────────────────────────────────


def compute_performance_metrics(
    trades: list[dict],
    equity_snapshots: Optional[list[dict]] = None,
    initial_capital: float = 100_000,
    risk_free_rate: float = 0.02,
    trading_days_per_year: int = 252,
) -> PerformanceMetricsResult:
    """Compute comprehensive performance metrics from trade records.

    Args:
        trades: List of trade dicts with keys: netPnl, grossPnl, commission,
                entryTime, exitTime, holdingDays (optional).
        equity_snapshots: Optional list of {date, equity} dicts.
        initial_capital: Starting capital for return calculations.
        risk_free_rate: Annual risk-free rate for Sharpe/Sortino.
        trading_days_per_year: Trading days in a year (252 for A-shares).

    Returns:
        PerformanceMetricsResult with all computed metrics.
    """
    from datetime import datetime

    # Filter to closed trades only, sort by exit time
    closed = [
        t for t in trades
        if t.get("exitTime") is not None
    ]
    closed.sort(key=lambda t: t.get("exitTime", ""))

    if not closed:
        return PerformanceMetricsResult(
            calculated_at=datetime.now().isoformat()
        )

    # ── Returns ──
    total_pnl = sum(t.get("netPnl", 0) for t in closed)
    total_gross_pnl = sum(t.get("grossPnl", 0) for t in closed)
    total_commission = sum(t.get("commission", 0) for t in closed)
    total_return_pct = (total_pnl / initial_capital) * 100

    # Calendar span
    first_date = _parse_datetime(closed[0]["entryTime"])
    last_date = _parse_datetime(closed[-1]["exitTime"])
    calendar_days = max(1, (last_date - first_date).days)
    trading_days = max(1, round(calendar_days * (trading_days_per_year / 365)))

    # Annualized return
    years = calendar_days / 365
    if years >= 1 / 365:
        annualized_return_pct = (
            (1 + total_pnl / initial_capital) ** (1 / years) - 1
        ) * 100
    else:
        annualized_return_pct = total_return_pct

    # ── Win/loss analysis ──
    winners = [t for t in closed if t.get("netPnl", 0) > 0]
    losers = [t for t in closed if t.get("netPnl", 0) <= 0]
    win_rate = (len(winners) / len(closed)) * 100

    total_win_amount = sum(t["netPnl"] for t in winners)
    total_loss_amount = abs(sum(t["netPnl"] for t in losers))

    avg_win = total_win_amount / len(winners) if winners else 0
    avg_loss = total_loss_amount / len(losers) if losers else 0

    if avg_loss > 0:
        payoff_ratio = avg_win / avg_loss
    elif avg_win > 0:
        payoff_ratio = float("inf")
    else:
        payoff_ratio = 0.0

    if total_loss_amount > 0:
        profit_factor = total_win_amount / total_loss_amount
    elif total_win_amount > 0:
        profit_factor = float("inf")
    else:
        profit_factor = 0.0

    # Expectancy = (winP * avgWin) - (loseP * avgLoss)
    win_p = len(winners) / len(closed)
    lose_p = len(losers) / len(closed)
    expectancy = win_p * avg_win - lose_p * avg_loss

    avg_trade_return = total_pnl / len(closed)

    # ── Holding period ──
    holding_days_list = []
    for t in closed:
        hd = t.get("holdingDays")
        if hd is not None and hd > 0:
            holding_days_list.append(hd)
        else:
            hd = _compute_holding_days(t["entryTime"], t["exitTime"])
            if hd > 0:
                holding_days_list.append(hd)

    avg_holding_days = (
        sum(holding_days_list) / len(holding_days_list)
        if holding_days_list
        else 0
    )
    max_holding_days = max(holding_days_list) if holding_days_list else 0
    min_holding_days = min(holding_days_list) if holding_days_list else 0

    # ── Consecutive streaks ──
    max_con_wins, max_con_losses = _compute_streaks(closed)

    # ── Drawdown ──
    dd = _compute_drawdown(closed, equity_snapshots, initial_capital)

    # ── Risk-adjusted metrics ──
    risk_adj = _compute_risk_adjusted(
        closed,
        equity_snapshots,
        initial_capital,
        risk_free_rate,
        trading_days_per_year,
        annualized_return_pct,
        dd["max_drawdown_pct"],
    )

    # ── Cost ──
    commission_ratio = (
        (total_commission / total_gross_pnl) * 100
        if total_gross_pnl > 0
        else 0.0
    )

    return PerformanceMetricsResult(
        total_pnl=total_pnl,
        total_return_pct=total_return_pct,
        annualized_return_pct=annualized_return_pct,
        sharpe_ratio=risk_adj["sharpe_ratio"],
        sortino_ratio=risk_adj["sortino_ratio"],
        calmar_ratio=risk_adj["calmar_ratio"],
        max_drawdown_pct=dd["max_drawdown_pct"],
        max_drawdown_amount=dd["max_drawdown_amount"],
        max_drawdown_days=dd["max_drawdown_days"],
        current_drawdown_pct=dd["current_drawdown_pct"],
        total_trades=len(closed),
        winning_trades=len(winners),
        losing_trades=len(losers),
        win_rate=win_rate,
        payoff_ratio=payoff_ratio,
        profit_factor=profit_factor,
        expectancy=expectancy,
        avg_trade_return=avg_trade_return,
        avg_holding_days=avg_holding_days,
        max_holding_days=max_holding_days,
        min_holding_days=min_holding_days,
        max_consecutive_wins=max_con_wins,
        max_consecutive_losses=max_con_losses,
        total_commission=total_commission,
        commission_ratio=commission_ratio,
        trading_days=trading_days,
        calculated_at=datetime.now().isoformat(),
    )


# ── Helpers ─────────────────────────────────────────────────


def _parse_datetime(dt_str: str):
    """Parse an ISO datetime string."""
    from datetime import datetime

    # Handle both 'YYYY-MM-DDTHH:MM:SSZ' and 'YYYY-MM-DD HH:MM:SS'
    dt_str = dt_str.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(dt_str)
    except ValueError:
        # Fallback for dates without time
        return datetime.fromisoformat(dt_str[:10])


def _compute_holding_days(entry_time: str, exit_time: str) -> int:
    """Calculate holding days from entry/exit timestamps."""
    entry = _parse_datetime(entry_time)
    exit_ = _parse_datetime(exit_time)
    return max(1, round((exit_ - entry).total_seconds() / 86400))


def _compute_streaks(trades: list[dict]) -> tuple[int, int]:
    """Compute max consecutive wins and losses."""
    max_wins = 0
    max_losses = 0
    cur_wins = 0
    cur_losses = 0

    for t in trades:
        if t.get("netPnl", 0) > 0:
            cur_wins += 1
            cur_losses = 0
            if cur_wins > max_wins:
                max_wins = cur_wins
        else:
            cur_losses += 1
            cur_wins = 0
            if cur_losses > max_losses:
                max_losses = cur_losses

    return max_wins, max_losses


def _compute_drawdown(
    trades: list[dict],
    equity_snapshots: Optional[list[dict]],
    initial_capital: float,
) -> dict:
    """Compute drawdown metrics from equity snapshots or trade records."""
    if equity_snapshots and len(equity_snapshots) > 0:
        series = sorted(equity_snapshots, key=lambda s: s["date"])
        equity_series = [
            {"date": s["date"], "equity": s["equity"]}
            for s in series
        ]
    else:
        equity_series = _build_equity_curve_from_trades(trades, initial_capital)

    if not equity_series:
        return {
            "max_drawdown_pct": 0.0,
            "max_drawdown_amount": 0.0,
            "max_drawdown_days": 0,
            "current_drawdown_pct": 0.0,
        }

    peak = -float("inf")
    max_dd_pct = 0.0
    max_dd_amount = 0.0
    peak_date = equity_series[0]["date"]
    max_dd_days = 0
    in_drawdown = False
    dd_start_date = equity_series[0]["date"]

    for point in equity_series:
        eq = point["equity"]
        if eq > peak:
            peak = eq
            peak_date = point["date"]
            in_drawdown = False

        dd_amount = peak - eq
        dd_pct = (dd_amount / peak) * 100 if peak > 0 else 0

        if dd_amount > 0 and not in_drawdown:
            in_drawdown = True
            dd_start_date = peak_date

        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct
            max_dd_amount = dd_amount
            days_diff = _days_between(dd_start_date, point["date"])
            if days_diff > max_dd_days:
                max_dd_days = days_diff

    last_equity = equity_series[-1]["equity"]
    current_dd_pct = ((peak - last_equity) / peak) * 100 if peak > 0 else 0

    return {
        "max_drawdown_pct": max_dd_pct,
        "max_drawdown_amount": max_dd_amount,
        "max_drawdown_days": max_dd_days,
        "current_drawdown_pct": current_dd_pct,
    }


def _build_equity_curve_from_trades(
    trades: list[dict], initial_capital: float
) -> list[dict]:
    """Build cumulative equity curve from trade records by exit date."""
    if not trades:
        return []

    daily_pnl: dict[str, float] = {}
    for t in trades:
        date = (t.get("exitTime") or "")[:10]
        if not date:
            continue
        daily_pnl[date] = daily_pnl.get(date, 0) + t.get("netPnl", 0)

    sorted_dates = sorted(daily_pnl.keys())
    curve = []
    cum_equity = initial_capital
    for date in sorted_dates:
        cum_equity += daily_pnl[date]
        curve.append({"date": date, "equity": cum_equity})

    return curve


def _days_between(d1: str, d2: str) -> int:
    """Days between two date strings."""
    from datetime import datetime

    t1 = datetime.fromisoformat(d1[:10])
    t2 = datetime.fromisoformat(d2[:10])
    return max(0, abs((t2 - t1).days))


def _compute_risk_adjusted(
    trades: list[dict],
    equity_snapshots: Optional[list[dict]],
    initial_capital: float,
    risk_free_rate: float,
    trading_days_per_year: int,
    annualized_return_pct: float,
    max_drawdown_pct: float,
) -> dict:
    """Compute Sharpe, Sortino, and Calmar ratios."""
    daily_returns: list[float]

    if equity_snapshots and len(equity_snapshots) >= 2:
        sorted_snaps = sorted(equity_snapshots, key=lambda s: s["date"])
        daily_returns = []
        for i in range(1, len(sorted_snaps)):
            prev = sorted_snaps[i - 1]["equity"]
            if prev > 0:
                daily_returns.append(
                    (sorted_snaps[i]["equity"] - prev) / prev
                )
    else:
        daily_returns = _build_daily_returns_from_trades(trades, initial_capital)

    n = len(daily_returns)
    if n < 2:
        return {"sharpe_ratio": 0.0, "sortino_ratio": 0.0, "calmar_ratio": 0.0}

    daily_rf = risk_free_rate / trading_days_per_year
    excess_returns = [r - daily_rf for r in daily_returns]

    avg_excess = sum(excess_returns) / n

    # Sharpe
    variance = sum((r - avg_excess) ** 2 for r in excess_returns) / (n - 1)
    std_dev = math.sqrt(variance)
    sharpe = (
        (avg_excess / std_dev) * math.sqrt(trading_days_per_year)
        if std_dev > 0
        else 0.0
    )

    # Sortino (downside deviation only)
    negative_excess = [r for r in excess_returns if r < 0]
    if negative_excess:
        downside_variance = sum(r ** 2 for r in negative_excess) / len(
            negative_excess
        )
    else:
        downside_variance = 0.0
    downside_dev = math.sqrt(downside_variance)
    sortino = (
        (avg_excess / downside_dev) * math.sqrt(trading_days_per_year)
        if downside_dev > 0
        else 0.0
    )

    # Calmar
    if max_drawdown_pct > 0:
        calmar = annualized_return_pct / max_drawdown_pct
    elif annualized_return_pct > 0:
        calmar = float("inf")
    else:
        calmar = 0.0

    return {
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "calmar_ratio": calmar,
    }


def _build_daily_returns_from_trades(
    trades: list[dict], initial_capital: float
) -> list[float]:
    """Build daily return series from trade records."""
    if not trades:
        return []

    daily_pnl: dict[str, float] = {}
    for t in trades:
        date = (t.get("exitTime") or "")[:10]
        if not date:
            continue
        daily_pnl[date] = daily_pnl.get(date, 0) + t.get("netPnl", 0)

    sorted_dates = sorted(daily_pnl.keys())
    returns = []
    cum_equity = initial_capital

    for date in sorted_dates:
        pnl = daily_pnl[date]
        daily_return = pnl / cum_equity if cum_equity > 0 else 0
        returns.append(daily_return)
        cum_equity += pnl

    return returns


# ── Formatting ──────────────────────────────────────────────


def format_performance_report(metrics: PerformanceMetricsResult) -> str:
    """Format performance metrics as a human-readable report."""
    hr = "\u2500" * 50

    def fmt(value: float, decimals: int = 2) -> str:
        if math.isinf(value) or math.isnan(value):
            return "N/A"
        return f"{value:.{decimals}f}"

    def fmt_pct(value: float) -> str:
        if math.isinf(value) or math.isnan(value):
            return "N/A"
        sign = "+" if value >= 0 else ""
        return f"{sign}{value:.2f}%"

    def fmt_ratio(value: float) -> str:
        if math.isinf(value):
            return "\u221e"
        if math.isnan(value):
            return "N/A"
        return f"{value:.3f}"

    m = metrics
    lines = [
        "Trading Performance Report",
        f"Calculated: {m.calculated_at[:19]}",
        hr,
        "",
        "[Returns]",
        f"  Total PnL:            {fmt(m.total_pnl)}",
        f"  Total Return:         {fmt_pct(m.total_return_pct)}",
        f"  Annualized Return:    {fmt_pct(m.annualized_return_pct)}",
        "",
        "[Risk-Adjusted]",
        f"  Sharpe Ratio:         {fmt(m.sharpe_ratio, 3)}",
        f"  Sortino Ratio:        {fmt(m.sortino_ratio, 3)}",
        f"  Calmar Ratio:         {fmt_ratio(m.calmar_ratio)}",
        "",
        "[Drawdown]",
        f"  Max Drawdown:         {fmt_pct(m.max_drawdown_pct)}",
        f"  Max Drawdown Amount:  {fmt(m.max_drawdown_amount)}",
        f"  Max Drawdown Days:    {m.max_drawdown_days}",
        f"  Current Drawdown:     {fmt_pct(m.current_drawdown_pct)}",
        "",
        "[Trade Quality]",
        f"  Total Trades:         {m.total_trades} (W {m.winning_trades} / L {m.losing_trades})",
        f"  Win Rate:             {fmt_pct(m.win_rate)}",
        f"  Payoff Ratio:         {fmt_ratio(m.payoff_ratio)}",
        f"  Profit Factor:        {fmt_ratio(m.profit_factor)}",
        f"  Expectancy:           {fmt(m.expectancy)}",
        f"  Avg Trade Return:     {fmt(m.avg_trade_return)}",
        "",
        "[Holding Period]",
        f"  Average:              {fmt(m.avg_holding_days, 1)} days",
        f"  Longest:              {m.max_holding_days} days",
        f"  Shortest:             {m.min_holding_days} days",
        "",
        "[Streaks]",
        f"  Max Consecutive Wins:   {m.max_consecutive_wins}",
        f"  Max Consecutive Losses: {m.max_consecutive_losses}",
        "",
        "[Costs]",
        f"  Total Commission:     {fmt(m.total_commission)}",
        f"  Commission Ratio:     {fmt_pct(m.commission_ratio)}",
        "",
        f"Trading Days Span: {m.trading_days}",
        hr,
    ]
    return "\n".join(lines)


# ── CLI Entry Point ─────────────────────────────────────────


if __name__ == "__main__":
    config = json.loads(sys.stdin.read())
    result = compute_performance_metrics(
        trades=config.get("trades", []),
        equity_snapshots=config.get("equitySnapshots"),
        initial_capital=config.get("initialCapital", 100_000),
        risk_free_rate=config.get("riskFreeRate", 0.02),
        trading_days_per_year=config.get("tradingDaysPerYear", 252),
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False))
