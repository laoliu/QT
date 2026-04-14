"""回测报告生成 — 汇总绩效、回撤、成本、持仓等综合指标"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class PerformanceMetrics:
    total_return: float = 0.0
    annual_return: float = 0.0
    annual_volatility: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    risk_free_rate: float = 0.03

    def to_dict(self) -> dict:
        return {k: round(v, 6) if isinstance(v, float) else v
                for k, v in self.__dict__.items()}


@dataclass
class DrawdownAnalysis:
    max_drawdown: float = 0.0
    max_drawdown_duration_days: int = 0
    avg_drawdown: float = 0.0
    avg_drawdown_duration_days: int = 0
    top_drawdowns: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "max_drawdown": round(self.max_drawdown, 6),
            "max_drawdown_duration_days": self.max_drawdown_duration_days,
            "avg_drawdown": round(self.avg_drawdown, 6),
            "avg_drawdown_duration_days": self.avg_drawdown_duration_days,
            "top_drawdowns": self.top_drawdowns,
        }


@dataclass
class CostAttribution:
    total_commission: float = 0.0
    total_slippage: float = 0.0
    total_tax: float = 0.0
    total_cost: float = 0.0
    cost_to_gross_return_ratio: float = 0.0

    def to_dict(self) -> dict:
        return {k: round(v, 6) for k, v in self.__dict__.items()}


@dataclass
class TradingStats:
    total_trades: int = 0
    avg_holding_days: float = 0.0
    daily_turnover: float = 0.0
    avg_trade_return: float = 0.0
    best_trade_return: float = 0.0
    worst_trade_return: float = 0.0

    def to_dict(self) -> dict:
        return {k: round(v, 6) if isinstance(v, float) else v
                for k, v in self.__dict__.items()}


@dataclass
class PositionStats:
    avg_position_count: float = 0.0
    max_position_count: int = 0
    avg_single_position_weight: float = 0.0
    max_single_position_weight: float = 0.0
    concentration_hhi: float = 0.0

    def to_dict(self) -> dict:
        return {k: round(v, 6) if isinstance(v, float) else v
                for k, v in self.__dict__.items()}


@dataclass
class RiskSummary:
    var_95: float = 0.0
    cvar_95: float = 0.0
    beta: float = 0.0
    alpha: float = 0.0
    information_ratio: float = 0.0
    tracking_error: float = 0.0

    def to_dict(self) -> dict:
        return {k: round(v, 6) for k, v in self.__dict__.items()}


@dataclass
class BenchmarkRelativeMetrics:
    excess_return: float = 0.0
    excess_sharpe: float = 0.0
    beta: float = 0.0
    alpha: float = 0.0
    correlation: float = 0.0

    def to_dict(self) -> dict:
        return {k: round(v, 6) for k, v in self.__dict__.items()}


@dataclass
class BacktestReport:
    strategy_name: str = ""
    period_start: str = ""
    period_end: str = ""
    performance: PerformanceMetrics = field(default_factory=PerformanceMetrics)
    drawdown: DrawdownAnalysis = field(default_factory=DrawdownAnalysis)
    cost: CostAttribution = field(default_factory=CostAttribution)
    trading: TradingStats = field(default_factory=TradingStats)
    position: PositionStats = field(default_factory=PositionStats)
    risk: RiskSummary = field(default_factory=RiskSummary)
    benchmark_relative: BenchmarkRelativeMetrics | None = None
    raw_nav: pd.Series | None = None

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "strategy_name": self.strategy_name,
            "period": f"{self.period_start} ~ {self.period_end}",
            "performance": self.performance.to_dict(),
            "drawdown": self.drawdown.to_dict(),
            "cost": self.cost.to_dict(),
            "trading": self.trading.to_dict(),
            "position": self.position.to_dict(),
            "risk": self.risk.to_dict(),
        }
        if self.benchmark_relative:
            d["benchmark_relative"] = self.benchmark_relative.to_dict()
        return d

    def summary_text(self) -> str:
        p = self.performance
        return (
            f"[{self.strategy_name}] {self.period_start}~{self.period_end}\n"
            f"  年化收益 {p.annual_return:.2%} | 夏普 {p.sharpe_ratio:.2f} | "
            f"最大回撤 {p.max_drawdown:.2%} | 胜率 {p.win_rate:.1%}"
        )


# ── 计算函数 ─────────────────────────────────────────────────────

def compute_performance_metrics(
    nav: pd.Series,
    *,
    risk_free_rate: float = 0.03,
) -> PerformanceMetrics:
    if nav.empty or len(nav) < 2:
        return PerformanceMetrics()
    total_ret = nav.iloc[-1] / nav.iloc[0] - 1
    days = (nav.index[-1] - nav.index[0]).days or 1
    ann_ret = (1 + total_ret) ** (365 / days) - 1

    daily_ret = nav.pct_change().dropna()
    ann_vol = daily_ret.std() * np.sqrt(252)
    sharpe = (ann_ret - risk_free_rate) / ann_vol if ann_vol > 0 else 0.0

    downside = daily_ret[daily_ret < 0]
    down_vol = downside.std() * np.sqrt(252) if len(downside) > 0 else 0.0
    sortino = (ann_ret - risk_free_rate) / down_vol if down_vol > 0 else 0.0

    running_max = nav.cummax()
    dd = (nav - running_max) / running_max
    max_dd = abs(dd.min())
    calmar = ann_ret / max_dd if max_dd > 0 else 0.0

    wins = (daily_ret > 0).sum()
    wr = wins / len(daily_ret) if len(daily_ret) > 0 else 0.0

    gross_profit = daily_ret[daily_ret > 0].sum()
    gross_loss = abs(daily_ret[daily_ret < 0].sum())
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    return PerformanceMetrics(
        total_return=total_ret, annual_return=ann_ret, annual_volatility=ann_vol,
        sharpe_ratio=sharpe, sortino_ratio=sortino, calmar_ratio=calmar,
        max_drawdown=max_dd, win_rate=wr, profit_factor=pf,
        risk_free_rate=risk_free_rate,
    )


def compute_drawdown_analysis(nav: pd.Series, *, top_n: int = 5) -> DrawdownAnalysis:
    if nav.empty or len(nav) < 2:
        return DrawdownAnalysis()
    running_max = nav.cummax()
    dd = (nav - running_max) / running_max

    max_dd = abs(dd.min())

    # 回撤期间
    in_dd = dd < 0
    groups: list[tuple[int, int]] = []
    start = None
    for i, v in enumerate(in_dd):
        if v and start is None:
            start = i
        elif not v and start is not None:
            groups.append((start, i))
            start = None
    if start is not None:
        groups.append((start, len(dd) - 1))

    durations = []
    dd_depths = []
    for s, e in groups:
        dur = (nav.index[e] - nav.index[s]).days
        durations.append(dur)
        dd_depths.append(abs(dd.iloc[s:e + 1].min()))

    avg_dd = np.mean(dd_depths) if dd_depths else 0.0
    avg_dur = int(np.mean(durations)) if durations else 0
    max_dur = max(durations) if durations else 0

    # Top N drawdowns
    sorted_idx = np.argsort(dd_depths)[::-1][:top_n] if dd_depths else []
    top_list = []
    for idx in sorted_idx:
        s, e = groups[idx]
        top_list.append({
            "depth": round(dd_depths[idx], 6),
            "start": str(nav.index[s].date()) if hasattr(nav.index[s], "date") else str(nav.index[s]),
            "end": str(nav.index[e].date()) if hasattr(nav.index[e], "date") else str(nav.index[e]),
            "duration_days": durations[idx],
        })

    return DrawdownAnalysis(
        max_drawdown=max_dd, max_drawdown_duration_days=max_dur,
        avg_drawdown=avg_dd, avg_drawdown_duration_days=avg_dur,
        top_drawdowns=top_list,
    )


def compute_cost_attribution(
    *,
    total_commission: float = 0.0,
    total_slippage: float = 0.0,
    total_tax: float = 0.0,
    gross_return: float = 0.0,
) -> CostAttribution:
    total = total_commission + total_slippage + total_tax
    ratio = total / abs(gross_return) if abs(gross_return) > 1e-10 else 0.0
    return CostAttribution(
        total_commission=total_commission, total_slippage=total_slippage,
        total_tax=total_tax, total_cost=total,
        cost_to_gross_return_ratio=ratio,
    )


def compute_trading_stats(trades: pd.DataFrame) -> TradingStats:
    """从交易记录 DataFrame 计算交易统计

    Expects columns: entry_date, exit_date, return (pct)
    """
    if trades.empty:
        return TradingStats()
    n = len(trades)
    avg_ret = trades["return"].mean() if "return" in trades else 0.0
    best = trades["return"].max() if "return" in trades else 0.0
    worst = trades["return"].min() if "return" in trades else 0.0

    holding = 0.0
    if "entry_date" in trades and "exit_date" in trades:
        trades = trades.copy()
        trades["entry_date"] = pd.to_datetime(trades["entry_date"])
        trades["exit_date"] = pd.to_datetime(trades["exit_date"])
        holding = (trades["exit_date"] - trades["entry_date"]).dt.days.mean()

    return TradingStats(
        total_trades=n, avg_holding_days=holding,
        avg_trade_return=avg_ret, best_trade_return=best, worst_trade_return=worst,
    )


def compute_position_stats(weights_df: pd.DataFrame) -> PositionStats:
    """从每日持仓权重 DataFrame 计算持仓统计

    weights_df: index=date, columns=symbols, values=weights (0~1)
    """
    if weights_df.empty:
        return PositionStats()
    non_zero = (weights_df > 1e-8).sum(axis=1)
    avg_cnt = non_zero.mean()
    max_cnt = int(non_zero.max())
    max_w = weights_df.max().max()
    avg_w = weights_df[weights_df > 1e-8].mean().mean()
    # HHI
    hhi_series = (weights_df ** 2).sum(axis=1)
    avg_hhi = hhi_series.mean()

    return PositionStats(
        avg_position_count=avg_cnt, max_position_count=max_cnt,
        avg_single_position_weight=avg_w, max_single_position_weight=max_w,
        concentration_hhi=avg_hhi,
    )


def compute_risk_summary(
    nav: pd.Series,
    benchmark_nav: pd.Series | None = None,
    *,
    risk_free_rate: float = 0.03,
) -> RiskSummary:
    if nav.empty or len(nav) < 10:
        return RiskSummary()
    daily_ret = nav.pct_change().dropna()
    var_95 = abs(np.percentile(daily_ret, 5))
    cvar_95 = abs(daily_ret[daily_ret <= -var_95].mean()) if (daily_ret <= -var_95).any() else var_95

    beta = 0.0
    alpha = 0.0
    ir = 0.0
    te = 0.0
    if benchmark_nav is not None and len(benchmark_nav) > 10:
        bm_ret = benchmark_nav.pct_change().dropna()
        aligned = pd.DataFrame({"s": daily_ret, "b": bm_ret}).dropna()
        if len(aligned) > 10:
            cov = np.cov(aligned["s"], aligned["b"])
            beta = cov[0, 1] / cov[1, 1] if cov[1, 1] > 0 else 0.0
            alpha = aligned["s"].mean() * 252 - beta * aligned["b"].mean() * 252
            excess = aligned["s"] - aligned["b"]
            te = excess.std() * np.sqrt(252)
            ir = excess.mean() * 252 / te if te > 0 else 0.0

    return RiskSummary(var_95=var_95, cvar_95=cvar_95, beta=beta, alpha=alpha,
                       information_ratio=ir, tracking_error=te)


def generate_report(
    *,
    strategy_name: str,
    nav: pd.Series,
    benchmark_nav: pd.Series | None = None,
    trades: pd.DataFrame | None = None,
    weights_df: pd.DataFrame | None = None,
    total_commission: float = 0.0,
    total_slippage: float = 0.0,
    total_tax: float = 0.0,
    risk_free_rate: float = 0.03,
) -> BacktestReport:
    """生成完整回测报告"""
    perf = compute_performance_metrics(nav, risk_free_rate=risk_free_rate)
    dd = compute_drawdown_analysis(nav)
    cost = compute_cost_attribution(
        total_commission=total_commission, total_slippage=total_slippage,
        total_tax=total_tax, gross_return=perf.total_return,
    )
    ts = compute_trading_stats(trades) if trades is not None else TradingStats()
    ps = compute_position_stats(weights_df) if weights_df is not None else PositionStats()
    risk = compute_risk_summary(nav, benchmark_nav, risk_free_rate=risk_free_rate)

    bm_rel = None
    if benchmark_nav is not None and len(benchmark_nav) > 2:
        bm_ret = benchmark_nav.iloc[-1] / benchmark_nav.iloc[0] - 1
        bm_days = (benchmark_nav.index[-1] - benchmark_nav.index[0]).days or 1
        bm_ann = (1 + bm_ret) ** (365 / bm_days) - 1
        bm_daily = benchmark_nav.pct_change().dropna()
        bm_vol = bm_daily.std() * np.sqrt(252)
        bm_sharpe = (bm_ann - risk_free_rate) / bm_vol if bm_vol > 0 else 0.0
        aligned = pd.DataFrame({"s": nav.pct_change(), "b": benchmark_nav.pct_change()}).dropna()
        corr = aligned["s"].corr(aligned["b"]) if len(aligned) > 2 else 0.0
        bm_rel = BenchmarkRelativeMetrics(
            excess_return=perf.annual_return - bm_ann,
            excess_sharpe=perf.sharpe_ratio - bm_sharpe,
            beta=risk.beta, alpha=risk.alpha, correlation=corr,
        )

    start = str(nav.index[0].date()) if hasattr(nav.index[0], "date") else str(nav.index[0])
    end = str(nav.index[-1].date()) if hasattr(nav.index[-1], "date") else str(nav.index[-1])

    return BacktestReport(
        strategy_name=strategy_name, period_start=start, period_end=end,
        performance=perf, drawdown=dd, cost=cost, trading=ts, position=ps,
        risk=risk, benchmark_relative=bm_rel, raw_nav=nav,
    )


def extract_metrics_dict(report: BacktestReport) -> dict:
    """提取报告中关键指标为扁平字典，方便比较"""
    p = report.performance
    return {
        "annual_return": p.annual_return,
        "sharpe": p.sharpe_ratio,
        "sortino": p.sortino_ratio,
        "max_drawdown": p.max_drawdown,
        "calmar": p.calmar_ratio,
        "win_rate": p.win_rate,
        "profit_factor": p.profit_factor,
        "var_95": report.risk.var_95,
        "cvar_95": report.risk.cvar_95,
    }
