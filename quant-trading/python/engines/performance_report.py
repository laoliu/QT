"""
Performance report generation and formatted output engine.

Generates comprehensive trading performance reports with:
  - Summary dashboard (overall P&L, risk metrics, key ratios)
  - Time-period breakdown (daily/weekly/monthly/yearly)
  - Per-symbol performance details
  - Chart-ready data series (equity curve, drawdown, return distribution)
  - Text and HTML formatted output

Input: trades list + optional equity curve, following the project's
TradeRecord / equity curve conventions from cn_backtest_engine.py
and the TypeScript performance-attribution service.

Usage (CLI / TypeScript bridge via QUANT_CONFIG):
  python -m engines.performance_report  (reads JSON from stdin)

  { "action": "full_report" | "by_period" | "by_symbol" | "chart_data" | "text_summary" | "html_summary",
    "trades": [...],
    "equityCurve": [...],             // optional
    "initialCash": 1000000,           // optional
    "riskFreeRate": 0.03,             // optional
    "from": "2025-01-01",             // optional date filter
    "to": "2025-12-31",              // optional date filter
    "periodType": "daily"|"weekly"|"monthly",  // for by_period
    "limit": 20 }                     // optional
"""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Optional


# ── Data structures ─────────────────────────────────────────────


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        return default


# ── Core computation ────────────────────────────────────────────


def _filter_trades(trades: list[dict], from_date: Optional[str] = None,
                   to_date: Optional[str] = None) -> list[dict]:
    """Filter trades by exit date range, keeping only closed trades."""
    filtered = [t for t in trades if t.get("exitTime")]
    if from_date:
        filtered = [t for t in filtered if (t.get("exitTime") or "") >= from_date]
    if to_date:
        filtered = [t for t in filtered if (t.get("exitTime") or "") <= to_date]
    filtered.sort(key=lambda t: t.get("exitTime") or "")
    return filtered


def _iso_week(date_str: str) -> str:
    """Get ISO week string YYYY-WNN from a date string."""
    try:
        d = datetime.strptime(date_str[:10], "%Y-%m-%d")
        iso = d.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    except (ValueError, IndexError):
        return date_str[:7]


def _compute_overall(trades: list[dict]) -> dict:
    """Compute overall trading statistics from a list of closed trades."""
    if not trades:
        return {
            "totalTrades": 0, "totalNetPnl": 0, "totalGrossPnl": 0,
            "totalCommission": 0, "overallWinRate": 0, "avgWin": 0,
            "avgLoss": 0, "profitFactor": 0, "maxConsecutiveWins": 0,
            "maxConsecutiveLosses": 0, "avgHoldingDays": 0,
            "largestWin": 0, "largestLoss": 0, "expectancy": 0,
        }

    total_net = sum(_safe_float(t.get("netPnl")) for t in trades)
    total_gross = sum(_safe_float(t.get("grossPnl")) for t in trades)
    total_commission = sum(_safe_float(t.get("commission")) for t in trades)

    wins = [t for t in trades if _safe_float(t.get("netPnl")) > 0]
    losses = [t for t in trades if _safe_float(t.get("netPnl")) <= 0]

    total_win_amount = sum(_safe_float(t.get("netPnl")) for t in wins)
    total_loss_amount = abs(sum(_safe_float(t.get("netPnl")) for t in losses))

    avg_win = total_win_amount / len(wins) if wins else 0
    avg_loss = total_loss_amount / len(losses) if losses else 0

    profit_factor = total_win_amount / total_loss_amount if total_loss_amount > 0 else (
        float("inf") if total_win_amount > 0 else 0
    )

    win_rate = len(wins) / len(trades) if trades else 0

    # Consecutive wins/losses
    max_consecutive_wins = 0
    max_consecutive_losses = 0
    cur_wins = 0
    cur_losses = 0
    for t in trades:
        if _safe_float(t.get("netPnl")) > 0:
            cur_wins += 1
            cur_losses = 0
            max_consecutive_wins = max(max_consecutive_wins, cur_wins)
        else:
            cur_losses += 1
            cur_wins = 0
            max_consecutive_losses = max(max_consecutive_losses, cur_losses)

    # Largest win/loss
    largest_win = max((_safe_float(t.get("netPnl")) for t in trades), default=0)
    largest_loss = min((_safe_float(t.get("netPnl")) for t in trades), default=0)

    # Average holding days
    holding_days = [_safe_float(t.get("holdingDays")) for t in trades if t.get("holdingDays") is not None]
    avg_holding = sum(holding_days) / len(holding_days) if holding_days else 0

    # Expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)
    loss_rate = 1 - win_rate
    expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)

    return {
        "totalTrades": len(trades),
        "totalNetPnl": round(total_net, 2),
        "totalGrossPnl": round(total_gross, 2),
        "totalCommission": round(total_commission, 2),
        "overallWinRate": round(win_rate, 4),
        "avgWin": round(avg_win, 2),
        "avgLoss": round(avg_loss, 2),
        "profitFactor": round(profit_factor, 4) if profit_factor != float("inf") else "Infinity",
        "maxConsecutiveWins": max_consecutive_wins,
        "maxConsecutiveLosses": max_consecutive_losses,
        "avgHoldingDays": round(avg_holding, 1),
        "largestWin": round(largest_win, 2),
        "largestLoss": round(largest_loss, 2),
        "expectancy": round(expectancy, 2),
    }


def _compute_by_period(trades: list[dict], period_type: str = "daily") -> list[dict]:
    """Group trades by time period and compute P&L for each."""
    if period_type == "weekly":
        key_fn = lambda t: _iso_week(t.get("exitTime") or "")
    elif period_type == "monthly":
        key_fn = lambda t: (t.get("exitTime") or "")[:7]
    elif period_type == "yearly":
        key_fn = lambda t: (t.get("exitTime") or "")[:4]
    else:  # daily
        key_fn = lambda t: (t.get("exitTime") or "")[:10]

    groups: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        groups[key_fn(t)].append(t)

    result = []
    cum_pnl = 0.0
    for period in sorted(groups.keys()):
        period_trades = groups[period]
        net_pnl = sum(_safe_float(t.get("netPnl")) for t in period_trades)
        wins = sum(1 for t in period_trades if _safe_float(t.get("netPnl")) > 0)
        losses = len(period_trades) - wins
        cum_pnl += net_pnl
        result.append({
            "period": period,
            "trades": len(period_trades),
            "netPnl": round(net_pnl, 2),
            "cumPnl": round(cum_pnl, 2),
            "wins": wins,
            "losses": losses,
            "winRate": round(wins / len(period_trades), 4) if period_trades else 0,
        })
    return result


def _compute_by_symbol(trades: list[dict]) -> list[dict]:
    """Group trades by symbol and compute per-symbol statistics."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        groups[t.get("symbol", "unknown")].append(t)

    result = []
    total_net = sum(_safe_float(t.get("netPnl")) for t in trades)
    for symbol in sorted(groups.keys()):
        sym_trades = groups[symbol]
        net_pnl = sum(_safe_float(t.get("netPnl")) for t in sym_trades)
        wins = sum(1 for t in sym_trades if _safe_float(t.get("netPnl")) > 0)
        holding_days = [_safe_float(t.get("holdingDays")) for t in sym_trades if t.get("holdingDays") is not None]
        avg_holding = sum(holding_days) / len(holding_days) if holding_days else 0

        result.append({
            "symbol": symbol,
            "name": sym_trades[0].get("name", ""),
            "trades": len(sym_trades),
            "wins": wins,
            "losses": len(sym_trades) - wins,
            "netPnl": round(net_pnl, 2),
            "avgPnl": round(net_pnl / len(sym_trades), 2) if sym_trades else 0,
            "winRate": round(wins / len(sym_trades), 4) if sym_trades else 0,
            "avgHoldingDays": round(avg_holding, 1),
            "contributionPct": round((net_pnl / abs(total_net)) * 100, 1) if total_net != 0 else 0,
            "largestWin": round(max((_safe_float(t.get("netPnl")) for t in sym_trades), default=0), 2),
            "largestLoss": round(min((_safe_float(t.get("netPnl")) for t in sym_trades), default=0), 2),
        })

    result.sort(key=lambda s: abs(s["netPnl"]), reverse=True)
    return result


def _compute_by_strategy(trades: list[dict]) -> list[dict]:
    """Group trades by strategy and compute per-strategy statistics."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        groups[t.get("strategyId") or "unknown"].append(t)

    result = []
    total_net = sum(_safe_float(t.get("netPnl")) for t in trades)
    for strategy_id in sorted(groups.keys()):
        strat_trades = groups[strategy_id]
        net_pnl = sum(_safe_float(t.get("netPnl")) for t in strat_trades)
        gross_pnl = sum(_safe_float(t.get("grossPnl")) for t in strat_trades)
        wins = [t for t in strat_trades if _safe_float(t.get("netPnl")) > 0]
        losses = [t for t in strat_trades if _safe_float(t.get("netPnl")) <= 0]

        win_total = sum(_safe_float(t.get("netPnl")) for t in wins)
        loss_total = abs(sum(_safe_float(t.get("netPnl")) for t in losses))

        result.append({
            "strategyId": strategy_id,
            "trades": len(strat_trades),
            "wins": len(wins),
            "losses": len(losses),
            "winRate": round(len(wins) / len(strat_trades), 4) if strat_trades else 0,
            "grossPnl": round(gross_pnl, 2),
            "netPnl": round(net_pnl, 2),
            "avgWin": round(win_total / len(wins), 2) if wins else 0,
            "avgLoss": round(loss_total / len(losses), 2) if losses else 0,
            "largestWin": round(max((_safe_float(t.get("netPnl")) for t in strat_trades), default=0), 2),
            "largestLoss": round(min((_safe_float(t.get("netPnl")) for t in strat_trades), default=0), 2),
            "profitFactor": round(win_total / loss_total, 4) if loss_total > 0 else (
                "Infinity" if win_total > 0 else 0
            ),
            "contributionPct": round((net_pnl / abs(total_net)) * 100, 1) if total_net != 0 else 0,
        })

    result.sort(key=lambda s: abs(s["netPnl"]), reverse=True)
    return result


# ── Chart data preparation ──────────────────────────────────────


def _prepare_chart_data(trades: list[dict], equity_curve: list[dict],
                        initial_cash: float = 1000000) -> dict:
    """Prepare chart-ready data series for visualization.

    Returns:
      - equityCurve: [{date, value, drawdown}] — portfolio value over time
      - cumulativePnl: [{date, pnl}] — cumulative trade P&L over time
      - dailyReturns: [{date, return}] — daily return percentage
      - returnDistribution: [{bucket, count}] — histogram of trade returns
      - monthlyHeatmap: [{year, month, return}] — monthly return heatmap data
      - drawdownSeries: [{date, drawdown}] — drawdown over time
      - pnlByDayOfWeek: [{dayOfWeek, avgPnl, trades}] — average P&L by day of week
    """
    result: dict[str, Any] = {}

    # 1. Equity curve (pass-through if provided, or synthesize from trades)
    if equity_curve:
        result["equityCurve"] = equity_curve
    else:
        # Synthesize from trades
        cum = initial_cash
        eq = [{"date": trades[0].get("entryTime", "")[:10], "value": initial_cash}] if trades else []
        for t in trades:
            cum += _safe_float(t.get("netPnl"))
            eq.append({
                "date": (t.get("exitTime") or "")[:10],
                "value": round(cum, 2),
            })
        result["equityCurve"] = eq

    # 2. Cumulative P&L series
    cum_pnl = 0.0
    cum_series = []
    for t in trades:
        cum_pnl += _safe_float(t.get("netPnl"))
        cum_series.append({
            "date": (t.get("exitTime") or "")[:10],
            "pnl": round(cum_pnl, 2),
        })
    result["cumulativePnl"] = cum_series

    # 3. Daily returns from equity curve
    eq = result["equityCurve"]
    daily_returns = []
    for i in range(1, len(eq)):
        prev_val = _safe_float(eq[i - 1].get("value"))
        cur_val = _safe_float(eq[i].get("value"))
        ret = (cur_val / prev_val - 1) if prev_val > 0 else 0
        daily_returns.append({
            "date": eq[i].get("date", ""),
            "return": round(ret, 6),
        })
    result["dailyReturns"] = daily_returns

    # 4. Return distribution histogram (trade returns)
    trade_returns = [_safe_float(t.get("netPnl")) for t in trades]
    if trade_returns:
        min_r = min(trade_returns)
        max_r = max(trade_returns)
        if min_r == max_r:
            result["returnDistribution"] = [{"bucketMin": min_r, "bucketMax": max_r, "count": len(trade_returns)}]
        else:
            n_buckets = min(20, max(5, len(trade_returns) // 5))
            bucket_size = (max_r - min_r) / n_buckets
            buckets = [0] * n_buckets
            for r in trade_returns:
                idx = min(int((r - min_r) / bucket_size), n_buckets - 1)
                buckets[idx] += 1
            result["returnDistribution"] = [
                {
                    "bucketMin": round(min_r + i * bucket_size, 2),
                    "bucketMax": round(min_r + (i + 1) * bucket_size, 2),
                    "count": buckets[i],
                }
                for i in range(n_buckets)
            ]
    else:
        result["returnDistribution"] = []

    # 5. Monthly heatmap
    monthly: dict[str, float] = defaultdict(float)
    for t in trades:
        key = (t.get("exitTime") or "")[:7]
        if key:
            monthly[key] += _safe_float(t.get("netPnl"))
    result["monthlyHeatmap"] = [
        {
            "year": int(k[:4]),
            "month": int(k[5:7]),
            "netPnl": round(v, 2),
        }
        for k, v in sorted(monthly.items())
    ]

    # 6. Drawdown series
    if eq and len(eq) >= 2:
        peak = _safe_float(eq[0].get("value"))
        dd_series = []
        for point in eq:
            val = _safe_float(point.get("value"))
            if val > peak:
                peak = val
            dd = (peak - val) / peak if peak > 0 else 0
            dd_series.append({
                "date": point.get("date", ""),
                "drawdown": round(dd, 6),
            })
        result["drawdownSeries"] = dd_series
    else:
        result["drawdownSeries"] = []

    # 7. P&L by day of week
    dow_pnl: dict[int, list[float]] = defaultdict(list)
    for t in trades:
        exit_time = t.get("exitTime") or ""
        if len(exit_time) >= 10:
            try:
                d = datetime.strptime(exit_time[:10], "%Y-%m-%d")
                dow_pnl[d.weekday()].append(_safe_float(t.get("netPnl")))
            except ValueError:
                pass
    dow_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    result["pnlByDayOfWeek"] = [
        {
            "dayOfWeek": dow_names[dow],
            "dayIndex": dow,
            "avgPnl": round(sum(pnls) / len(pnls), 2) if pnls else 0,
            "totalPnl": round(sum(pnls), 2),
            "trades": len(pnls),
        }
        for dow, pnls in sorted(dow_pnl.items())
    ]

    return result


# ── Risk metrics from equity curve ──────────────────────────────


def _compute_risk_metrics(equity_curve: list[dict], risk_free_rate: float = 0.03) -> dict:
    """Compute risk metrics from an equity curve series."""
    if len(equity_curve) < 2:
        return {}

    values = [_safe_float(p.get("value")) for p in equity_curve]
    daily_returns = []
    for i in range(1, len(values)):
        if values[i - 1] > 0:
            daily_returns.append(values[i] / values[i - 1] - 1)

    if not daily_returns:
        return {}

    trading_days = len(daily_returns)
    years = trading_days / 252

    # Total return
    total_return = (values[-1] / values[0] - 1) if values[0] > 0 else 0

    # Annualized return
    ann_return = ((1 + total_return) ** (1 / years) - 1) if years > 0 and total_return > -1 else 0

    # Volatility
    mean_ret = sum(daily_returns) / len(daily_returns)
    variance = sum((r - mean_ret) ** 2 for r in daily_returns) / (len(daily_returns) - 1) if len(daily_returns) > 1 else 0
    volatility = math.sqrt(variance) * math.sqrt(252)

    # Sharpe
    rf_daily = risk_free_rate / 252
    excess = [r - rf_daily for r in daily_returns]
    mean_excess = sum(excess) / len(excess)
    excess_var = sum((e - mean_excess) ** 2 for e in excess) / len(excess) if excess else 0
    excess_std = math.sqrt(excess_var)
    sharpe = (mean_excess / excess_std) * math.sqrt(252) if excess_std > 0 else 0

    # Sortino
    downside = [min(e, 0) ** 2 for e in excess]
    downside_dev = math.sqrt(sum(downside) / len(downside)) if downside else 0
    sortino = (mean_excess / downside_dev) * math.sqrt(252) if downside_dev > 0 else 0

    # Max drawdown
    peak = values[0]
    max_dd = 0.0
    for v in values:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    # Calmar
    calmar = ann_return / max_dd if max_dd > 0 else 0

    # VaR (95%)
    sorted_returns = sorted(daily_returns)
    var_idx = max(int(len(sorted_returns) * 0.05), 0)
    var_95 = abs(sorted_returns[var_idx]) if sorted_returns else 0

    return {
        "totalReturn": round(total_return, 4),
        "annualizedReturn": round(ann_return, 4),
        "annualizedVolatility": round(volatility, 4),
        "sharpeRatio": round(sharpe, 4),
        "sortinoRatio": round(sortino, 4),
        "calmarRatio": round(calmar, 4),
        "maxDrawdown": round(max_dd, 4),
        "var95": round(var_95, 4),
        "tradingDays": trading_days,
    }


# ── Text formatting ─────────────────────────────────────────────


def _format_cny(amount: float) -> str:
    """Format a CNY amount with sign."""
    prefix = "+" if amount >= 0 else ""
    return f"{prefix}¥{amount:,.2f}"


def _format_pct(value: float) -> str:
    """Format a percentage value."""
    return f"{value * 100:.1f}%"


def generate_text_summary(trades: list[dict], equity_curve: list[dict],
                          initial_cash: float = 1000000,
                          risk_free_rate: float = 0.03,
                          from_date: Optional[str] = None,
                          to_date: Optional[str] = None) -> str:
    """Generate a plain-text performance report summary."""
    filtered = _filter_trades(trades, from_date, to_date)
    overall = _compute_overall(filtered)
    by_strategy = _compute_by_strategy(filtered)
    by_symbol = _compute_by_symbol(filtered)
    by_month = _compute_by_period(filtered, "monthly")
    risk = _compute_risk_metrics(equity_curve, risk_free_rate) if equity_curve else {}

    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("            交易绩效报告")
    lines.append("=" * 60)

    # Date range
    if filtered:
        first_date = (filtered[0].get("exitTime") or "")[:10]
        last_date = (filtered[-1].get("exitTime") or "")[:10]
        lines.append(f"报告期间: {first_date} ~ {last_date}")
    lines.append("")

    # Overall
    lines.append("── 整体表现 ──────────────────────────────")
    lines.append(f"  总交易笔数:    {overall['totalTrades']}")
    lines.append(f"  净盈亏:        {_format_cny(overall['totalNetPnl'])}")
    lines.append(f"  毛盈亏:        {_format_cny(overall['totalGrossPnl'])}")
    lines.append(f"  手续费合计:    ¥{overall['totalCommission']:,.2f}")
    lines.append(f"  胜率:          {_format_pct(overall['overallWinRate'])}")
    pf = overall['profitFactor']
    pf_str = "∞" if pf == "Infinity" else f"{pf:.2f}" if isinstance(pf, (int, float)) else str(pf)
    lines.append(f"  盈亏比:        {pf_str}")
    lines.append(f"  平均盈利:      {_format_cny(overall['avgWin'])}")
    lines.append(f"  平均亏损:      ¥{overall['avgLoss']:,.2f}")
    lines.append(f"  最大单笔盈利:  {_format_cny(overall['largestWin'])}")
    lines.append(f"  最大单笔亏损:  {_format_cny(overall['largestLoss'])}")
    lines.append(f"  连续盈利最长:  {overall['maxConsecutiveWins']} 笔")
    lines.append(f"  连续亏损最长:  {overall['maxConsecutiveLosses']} 笔")
    lines.append(f"  平均持仓天数:  {overall['avgHoldingDays']} 天")
    lines.append(f"  期望值:        {_format_cny(overall['expectancy'])}/笔")
    lines.append("")

    # Risk metrics
    if risk:
        lines.append("── 风险指标 ──────────────────────────────")
        lines.append(f"  总收益率:      {_format_pct(risk.get('totalReturn', 0))}")
        lines.append(f"  年化收益率:    {_format_pct(risk.get('annualizedReturn', 0))}")
        lines.append(f"  年化波动率:    {_format_pct(risk.get('annualizedVolatility', 0))}")
        lines.append(f"  夏普比率:      {risk.get('sharpeRatio', 0):.4f}")
        lines.append(f"  索提诺比率:    {risk.get('sortinoRatio', 0):.4f}")
        lines.append(f"  卡尔马比率:    {risk.get('calmarRatio', 0):.4f}")
        lines.append(f"  最大回撤:      {_format_pct(risk.get('maxDrawdown', 0))}")
        lines.append(f"  VaR(95%):      {_format_pct(risk.get('var95', 0))}")
        lines.append(f"  交易天数:      {risk.get('tradingDays', 0)}")
        lines.append("")

    # Strategy breakdown (top 5)
    if by_strategy:
        lines.append("── 策略分析 (按净盈亏排序) ────────────────")
        for s in by_strategy[:5]:
            pf_s = s['profitFactor']
            pf_s_str = "∞" if pf_s == "Infinity" else f"{pf_s:.2f}" if isinstance(pf_s, (int, float)) else str(pf_s)
            lines.append(f"  {s['strategyId']}:")
            lines.append(f"    {s['trades']}笔  胜率{_format_pct(s['winRate'])}  净盈亏{_format_cny(s['netPnl'])}  盈亏比{pf_s_str}  贡献{s['contributionPct']:.1f}%")
        lines.append("")

    # Symbol breakdown (top 10)
    if by_symbol:
        lines.append("── 标的分析 (按净盈亏排序) ────────────────")
        for s in by_symbol[:10]:
            name_str = f"({s['name']})" if s.get("name") else ""
            lines.append(f"  {s['symbol']}{name_str}:")
            lines.append(f"    {s['trades']}笔  胜率{_format_pct(s['winRate'])}  净盈亏{_format_cny(s['netPnl'])}  均持{s['avgHoldingDays']}天  贡献{s['contributionPct']:.1f}%")
        lines.append("")

    # Monthly P&L
    if by_month:
        lines.append("── 月度盈亏 ──────────────────────────────")
        lines.append(f"  {'月份':<10} {'笔数':>5} {'净盈亏':>12} {'累计':>12} {'胜率':>7}")
        lines.append("  " + "-" * 48)
        for m in by_month:
            lines.append(
                f"  {m['period']:<10} {m['trades']:>5} {_format_cny(m['netPnl']):>12} "
                f"{_format_cny(m['cumPnl']):>12} {_format_pct(m['winRate']):>7}"
            )
        lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines)


# ── HTML formatting ─────────────────────────────────────────────


def generate_html_summary(trades: list[dict], equity_curve: list[dict],
                          initial_cash: float = 1000000,
                          risk_free_rate: float = 0.03,
                          from_date: Optional[str] = None,
                          to_date: Optional[str] = None) -> str:
    """Generate an HTML performance report summary."""
    filtered = _filter_trades(trades, from_date, to_date)
    overall = _compute_overall(filtered)
    by_strategy = _compute_by_strategy(filtered)
    by_symbol = _compute_by_symbol(filtered)
    by_month = _compute_by_period(filtered, "monthly")
    risk = _compute_risk_metrics(equity_curve, risk_free_rate) if equity_curve else {}
    chart_data = _prepare_chart_data(filtered, equity_curve, initial_cash)

    # Date range
    date_range = ""
    if filtered:
        first_date = (filtered[0].get("exitTime") or "")[:10]
        last_date = (filtered[-1].get("exitTime") or "")[:10]
        date_range = f"{first_date} ~ {last_date}"

    def _pnl_color(val: float) -> str:
        return "color:#e74c3c" if val < 0 else "color:#27ae60"

    def _pnl_html(val: float) -> str:
        return f'<span style="{_pnl_color(val)}">{_format_cny(val)}</span>'

    html_parts: list[str] = []
    html_parts.append("<!DOCTYPE html>")
    html_parts.append('<html lang="zh-CN"><head><meta charset="UTF-8">')
    html_parts.append("<title>交易绩效报告</title>")
    html_parts.append("<style>")
    html_parts.append("""
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       max-width: 960px; margin: 0 auto; padding: 20px; background: #f8f9fa; color: #333; }
h1 { text-align: center; border-bottom: 2px solid #2c3e50; padding-bottom: 10px; }
h2 { color: #2c3e50; border-left: 4px solid #3498db; padding-left: 10px; margin-top: 30px; }
.date-range { text-align: center; color: #666; margin-bottom: 20px; }
.metrics-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 12px; margin: 16px 0; }
.metric-card { background: #fff; border-radius: 8px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
.metric-card .label { font-size: 12px; color: #888; text-transform: uppercase; }
.metric-card .value { font-size: 24px; font-weight: 700; margin-top: 4px; }
table { width: 100%; border-collapse: collapse; margin: 12px 0; background: #fff;
        border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
th { background: #2c3e50; color: #fff; padding: 10px 12px; text-align: left; font-size: 13px; }
td { padding: 8px 12px; border-bottom: 1px solid #eee; font-size: 13px; }
tr:hover { background: #f0f7ff; }
.positive { color: #27ae60; }
.negative { color: #e74c3c; }
.chart-placeholder { background: #fff; border-radius: 8px; padding: 20px; margin: 12px 0;
                     box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
.chart-placeholder pre { font-size: 11px; overflow-x: auto; }
""")
    html_parts.append("</style></head><body>")
    html_parts.append("<h1>交易绩效报告</h1>")
    if date_range:
        html_parts.append(f'<div class="date-range">{date_range}</div>')

    # Overall metrics grid
    html_parts.append("<h2>整体表现</h2>")
    html_parts.append('<div class="metrics-grid">')

    pf = overall['profitFactor']
    pf_str = "∞" if pf == "Infinity" else f"{pf:.2f}" if isinstance(pf, (int, float)) else str(pf)

    metrics = [
        ("总交易", str(overall["totalTrades"]), ""),
        ("净盈亏", _format_cny(overall["totalNetPnl"]), _pnl_color(overall["totalNetPnl"])),
        ("胜率", _format_pct(overall["overallWinRate"]), ""),
        ("盈亏比", pf_str, ""),
        ("平均盈利", _format_cny(overall["avgWin"]), "color:#27ae60"),
        ("平均亏损", f"¥{overall['avgLoss']:,.2f}", "color:#e74c3c"),
        ("最大单笔盈利", _format_cny(overall["largestWin"]), "color:#27ae60"),
        ("最大单笔亏损", _format_cny(overall["largestLoss"]), "color:#e74c3c"),
        ("连续盈利", f"{overall['maxConsecutiveWins']}笔", ""),
        ("连续亏损", f"{overall['maxConsecutiveLosses']}笔", ""),
        ("平均持仓", f"{overall['avgHoldingDays']}天", ""),
        ("期望值/笔", _format_cny(overall["expectancy"]), _pnl_color(overall["expectancy"])),
    ]
    for label, value, style in metrics:
        style_attr = f' style="{style}"' if style else ""
        html_parts.append(f'<div class="metric-card"><div class="label">{label}</div><div class="value"{style_attr}>{value}</div></div>')
    html_parts.append("</div>")

    # Risk metrics
    if risk:
        html_parts.append("<h2>风险指标</h2>")
        html_parts.append('<div class="metrics-grid">')
        risk_metrics = [
            ("总收益率", _format_pct(risk.get("totalReturn", 0)), _pnl_color(risk.get("totalReturn", 0))),
            ("年化收益率", _format_pct(risk.get("annualizedReturn", 0)), _pnl_color(risk.get("annualizedReturn", 0))),
            ("年化波动率", _format_pct(risk.get("annualizedVolatility", 0)), ""),
            ("夏普比率", f"{risk.get('sharpeRatio', 0):.4f}", ""),
            ("索提诺比率", f"{risk.get('sortinoRatio', 0):.4f}", ""),
            ("卡尔马比率", f"{risk.get('calmarRatio', 0):.4f}", ""),
            ("最大回撤", _format_pct(risk.get("maxDrawdown", 0)), "color:#e74c3c"),
            ("VaR(95%)", _format_pct(risk.get("var95", 0)), ""),
        ]
        for label, value, style in risk_metrics:
            style_attr = f' style="{style}"' if style else ""
            html_parts.append(f'<div class="metric-card"><div class="label">{label}</div><div class="value"{style_attr}>{value}</div></div>')
        html_parts.append("</div>")

    # Strategy table
    if by_strategy:
        html_parts.append("<h2>策略分析</h2>")
        html_parts.append("<table><thead><tr>")
        html_parts.append("<th>策略</th><th>笔数</th><th>胜率</th><th>净盈亏</th><th>盈亏比</th><th>贡献</th>")
        html_parts.append("</tr></thead><tbody>")
        for s in by_strategy:
            pf_s = s['profitFactor']
            pf_s_str = "∞" if pf_s == "Infinity" else f"{pf_s:.2f}" if isinstance(pf_s, (int, float)) else str(pf_s)
            pnl_cls = "positive" if s["netPnl"] >= 0 else "negative"
            html_parts.append(f'<tr><td>{s["strategyId"]}</td><td>{s["trades"]}</td>')
            html_parts.append(f'<td>{_format_pct(s["winRate"])}</td>')
            html_parts.append(f'<td class="{pnl_cls}">{_format_cny(s["netPnl"])}</td>')
            html_parts.append(f'<td>{pf_s_str}</td><td>{s["contributionPct"]:.1f}%</td></tr>')
        html_parts.append("</tbody></table>")

    # Symbol table
    if by_symbol:
        html_parts.append("<h2>标的分析</h2>")
        html_parts.append("<table><thead><tr>")
        html_parts.append("<th>标的</th><th>名称</th><th>笔数</th><th>胜率</th><th>净盈亏</th><th>均持天数</th><th>贡献</th>")
        html_parts.append("</tr></thead><tbody>")
        for s in by_symbol[:20]:
            pnl_cls = "positive" if s["netPnl"] >= 0 else "negative"
            html_parts.append(f'<tr><td>{s["symbol"]}</td><td>{s.get("name", "")}</td>')
            html_parts.append(f'<td>{s["trades"]}</td><td>{_format_pct(s["winRate"])}</td>')
            html_parts.append(f'<td class="{pnl_cls}">{_format_cny(s["netPnl"])}</td>')
            html_parts.append(f'<td>{s["avgHoldingDays"]}</td><td>{s["contributionPct"]:.1f}%</td></tr>')
        html_parts.append("</tbody></table>")

    # Monthly P&L table
    if by_month:
        html_parts.append("<h2>月度盈亏</h2>")
        html_parts.append("<table><thead><tr>")
        html_parts.append("<th>月份</th><th>笔数</th><th>净盈亏</th><th>累计</th><th>胜率</th>")
        html_parts.append("</tr></thead><tbody>")
        for m in by_month:
            pnl_cls = "positive" if m["netPnl"] >= 0 else "negative"
            cum_cls = "positive" if m["cumPnl"] >= 0 else "negative"
            html_parts.append(f'<tr><td>{m["period"]}</td><td>{m["trades"]}</td>')
            html_parts.append(f'<td class="{pnl_cls}">{_format_cny(m["netPnl"])}</td>')
            html_parts.append(f'<td class="{cum_cls}">{_format_cny(m["cumPnl"])}</td>')
            html_parts.append(f'<td>{_format_pct(m["winRate"])}</td></tr>')
        html_parts.append("</tbody></table>")

    # Chart data as JSON (for frontend consumption)
    if chart_data.get("equityCurve"):
        html_parts.append("<h2>图表数据</h2>")
        html_parts.append('<div class="chart-placeholder">')
        html_parts.append("<p>以下 JSON 可供前端图表库（ECharts / Chart.js 等）直接使用：</p>")
        chart_json = {
            "equityCurve": chart_data.get("equityCurve", [])[-30:],  # Last 30 points for preview
            "monthlyHeatmap": chart_data.get("monthlyHeatmap", []),
            "pnlByDayOfWeek": chart_data.get("pnlByDayOfWeek", []),
        }
        html_parts.append(f"<pre>{json.dumps(chart_json, ensure_ascii=False, indent=2)}</pre>")
        html_parts.append("</div>")

    html_parts.append("</body></html>")
    return "\n".join(html_parts)


# ── Action dispatch ─────────────────────────────────────────────


def run_performance_report(config: dict) -> dict:
    """Main entry point — dispatches to the appropriate action.

    Config:
    {
      "action": "full_report" | "by_period" | "by_symbol" | "by_strategy" |
                "chart_data" | "text_summary" | "html_summary",
      "trades": [...],
      "equityCurve": [...],
      "initialCash": 1000000,
      "riskFreeRate": 0.03,
      "from": "2025-01-01",
      "to": "2025-12-31",
      "periodType": "daily" | "weekly" | "monthly" | "yearly",
      "limit": 20
    }
    """
    action = config.get("action", "full_report")
    trades = config.get("trades", [])
    equity_curve = config.get("equityCurve", [])
    initial_cash = _safe_float(config.get("initialCash"), 1000000)
    risk_free_rate = _safe_float(config.get("riskFreeRate"), 0.03)
    from_date = config.get("from")
    to_date = config.get("to")
    period_type = config.get("periodType", "daily")
    limit = _safe_int(config.get("limit"), 20)

    filtered = _filter_trades(trades, from_date, to_date)

    if not filtered and action not in ("text_summary", "html_summary"):
        return {"error": "暂无已完结交易记录"}

    if action == "full_report":
        overall = _compute_overall(filtered)
        risk = _compute_risk_metrics(equity_curve, risk_free_rate) if equity_curve else {}
        return {
            **overall,
            "risk": risk,
            "byStrategy": _compute_by_strategy(filtered)[:limit],
            "bySymbol": _compute_by_symbol(filtered)[:limit],
            "byDay": _compute_by_period(filtered, "daily")[-limit:],
            "byWeek": _compute_by_period(filtered, "weekly")[-limit:],
            "byMonth": _compute_by_period(filtered, "monthly"),
            "byYear": _compute_by_period(filtered, "yearly"),
        }

    elif action == "by_period":
        return {
            "periodType": period_type,
            "periods": _compute_by_period(filtered, period_type)[-limit:],
        }

    elif action == "by_symbol":
        return {
            "symbols": _compute_by_symbol(filtered)[:limit],
        }

    elif action == "by_strategy":
        return {
            "strategies": _compute_by_strategy(filtered)[:limit],
        }

    elif action == "chart_data":
        return _prepare_chart_data(filtered, equity_curve, initial_cash)

    elif action == "text_summary":
        text = generate_text_summary(trades, equity_curve, initial_cash,
                                     risk_free_rate, from_date, to_date)
        return {"format": "text", "content": text}

    elif action == "html_summary":
        html = generate_html_summary(trades, equity_curve, initial_cash,
                                     risk_free_rate, from_date, to_date)
        return {"format": "html", "content": html}

    else:
        return {"error": f"未知操作: {action}"}


# ── CLI entry point ─────────────────────────────────────────────

if __name__ == "__main__":
    config = json.loads(sys.stdin.read())
    result = run_performance_report(config)
    print(json.dumps(result, ensure_ascii=False))
