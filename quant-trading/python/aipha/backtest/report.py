"""Backtest report generation and performance analytics.

This module provides comprehensive performance metrics, risk analytics,
and report generation for backtest results.

Key components:
- PerformanceMetrics: Core performance statistics
- DrawdownAnalysis: Drawdown-related metrics
- CostAttribution: Fee and cost breakdown
- TradingStats: Trading activity statistics
- BacktestReport: Comprehensive report combining all analytics

Design principles:
- All metrics are computed from BacktestResult (no side effects)
- Clear separation between computation and presentation
- All formulas are documented and testable
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, TYPE_CHECKING

import numpy as np

from aipha.market.trading_calendar_constants import (
    TRADING_DAYS_PER_YEAR,
)

if TYPE_CHECKING:
    from aipha.backtest.engine import BacktestResult

logger = logging.getLogger(__name__)


# ============================================================================
# 常量
# ============================================================================

# TRADING_DAYS_PER_YEAR 从 market.trading_calendar_constants 导入 (§13.8: 244)
RISK_FREE_RATE_DEFAULT = 0.02  # 2% 年化无风险利率


# ============================================================================
# 性能指标
# ============================================================================


@dataclass(frozen=True, slots=True)
class PerformanceMetrics:
    """核心性能指标

    Attributes:
        total_return: 累计收益率 (final_nav / initial_capital - 1)
        annualized_return: 年化收益率
        volatility: 年化波动率 (日收益标准差 * sqrt(TRADING_DAYS_PER_YEAR))
        sharpe_ratio: 夏普比率 (年化超额收益 / 年化波动率)
        sortino_ratio: 索提诺比率 (年化超额收益 / 下行波动率)
        calmar_ratio: 卡玛比率 (年化收益 / 最大回撤)
        trading_days: 回测交易日数
    """

    total_return: float
    annualized_return: float
    volatility: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    trading_days: int


@dataclass(frozen=True, slots=True)
class DrawdownAnalysis:
    """回撤分析

    Attributes:
        max_drawdown: 最大回撤 (绝对值，正数表示亏损)
        max_drawdown_pct: 最大回撤百分比
        max_drawdown_start: 最大回撤开始日期
        max_drawdown_end: 最大回撤结束日期（谷底）
        max_drawdown_recovery: 最大回撤恢复日期 (None if not recovered)
        max_drawdown_duration: 最大回撤持续天数
        avg_drawdown: 平均回撤
        drawdown_series: 每日回撤序列
    """

    max_drawdown: float
    max_drawdown_pct: float
    max_drawdown_start: date | None
    max_drawdown_end: date | None
    max_drawdown_recovery: date | None
    max_drawdown_duration: int
    avg_drawdown: float
    drawdown_series: list[float] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CostAttribution:
    """成本归因

    Attributes:
        total_fees: 总费用
        commission_total: 总佣金
        stamp_tax_total: 总印花税
        transfer_fee_total: 总过户费
        slippage_cost: 滑点成本估计
        cost_vs_nav_pct: 费用占最终 NAV 百分比
        cost_drag_annualized: 年化费用拖累
    """

    total_fees: float
    commission_total: float
    stamp_tax_total: float
    transfer_fee_total: float
    slippage_cost: float
    cost_vs_nav_pct: float
    cost_drag_annualized: float


@dataclass(frozen=True, slots=True)
class TradingStats:
    """交易统计

    Attributes:
        total_trades: 总交易次数
        buy_trades: 买入次数
        sell_trades: 卖出次数
        avg_trade_size: 平均交易金额
        avg_holding_period: 平均持仓天数 (估计)
        turnover_rate: 换手率 (单边年化)
        win_trades: 盈利交易数
        loss_trades: 亏损交易数
        win_rate: 胜率
        avg_win: 平均盈利
        avg_loss: 平均亏损
        profit_factor: 盈亏比 (总盈利 / 总亏损)
    """

    total_trades: int
    buy_trades: int
    sell_trades: int
    avg_trade_size: float
    avg_holding_period: float
    turnover_rate: float
    win_trades: int
    loss_trades: int
    win_rate: float
    avg_win: float
    avg_loss: float
    profit_factor: float


@dataclass(frozen=True, slots=True)
class PositionStats:
    """持仓统计

    Attributes:
        avg_positions: 平均持仓数量
        max_positions: 最大持仓数量
        min_positions: 最小持仓数量（非零天）
        avg_concentration: 平均集中度（最大持仓占比）
        max_concentration: 最大集中度
        idle_ratio: 空仓日比例（持仓数=0 的交易日占总交易日比例，R94-V14）
    """

    avg_positions: float
    max_positions: int
    min_positions: int
    avg_concentration: float
    max_concentration: float
    idle_ratio: float = 0.0


# ============================================================================
# 综合报告
# ============================================================================


@dataclass
class FactorICBrief:
    """因子 IC 分析摘要（轻量版，嵌入回测报告）

    Attributes:
        factor_name: 因子名称
        ic_mean: IC 均值
        ic_std: IC 标准差
        ir: 信息比率
        ic_positive_rate: IC > 0 的比率
        t_stat: t 统计量
        p_value: p 值
        sample_days: 有效计算天数
    """

    factor_name: str
    ic_mean: float
    ic_std: float
    ir: float
    ic_positive_rate: float
    t_stat: float
    p_value: float
    sample_days: int

    def to_dict(self) -> dict:
        """转换为字典表示。"""
        return {
            "factor_name": self.factor_name,
            "ic_mean": round(self.ic_mean, 6),
            "ic_std": round(self.ic_std, 6),
            "ir": round(self.ir, 4),
            "ic_positive_rate": round(self.ic_positive_rate, 4),
            "t_stat": round(self.t_stat, 4),
            "p_value": round(self.p_value, 6),
            "sample_days": self.sample_days,
        }


@dataclass
class RiskSummary:
    """风险分析摘要（嵌入回测报告）

    从 NAV 日收益率自动计算 VaR/CVaR 指标。

    Attributes:
        var_95: 95% 置信度 VaR（日）
        cvar_95: 95% 置信度 CVaR（日）
        var_99: 99% 置信度 VaR（日）
        cvar_99: 99% 置信度 CVaR（日）
        worst_day: 最大单日亏损
        worst_week: 最大连续 5 日亏损（滚动）
        tail_ratio: 上尾/下尾比（95 分位）
    """

    var_95: float
    cvar_95: float
    var_99: float
    cvar_99: float
    worst_day: float
    worst_week: float
    tail_ratio: float

    def to_dict(self) -> dict:
        """转换为字典表示。"""
        return {
            "var_95": round(self.var_95, 6),
            "cvar_95": round(self.cvar_95, 6),
            "var_99": round(self.var_99, 6),
            "cvar_99": round(self.cvar_99, 6),
            "worst_day": round(self.worst_day, 6),
            "worst_week": round(self.worst_week, 6),
            "tail_ratio": round(self.tail_ratio, 4),
        }


@dataclass(frozen=True, slots=True)
class BenchmarkRelativeMetrics:
    """基准相对收益指标

    评估策略相对于基准指数（如沪深300）的超额表现。

    Attributes:
        active_return: 主动收益（策略年化 - 基准年化）
        tracking_error: 跟踪误差（主动收益的年化标准差）
        information_ratio: 信息比率（主动收益 / 跟踪误差）
        active_max_drawdown: 超额收益最大回撤
        benchmark_name: 基准名称
    """

    active_return: float
    tracking_error: float
    information_ratio: float
    active_max_drawdown: float
    benchmark_name: str = ""

    def to_dict(self) -> dict:
        """转换为字典表示。"""
        return {
            "active_return": round(self.active_return, 6),
            "tracking_error": round(self.tracking_error, 6),
            "information_ratio": round(self.information_ratio, 4),
            "active_max_drawdown": round(self.active_max_drawdown, 6),
            "benchmark_name": self.benchmark_name,
        }


@dataclass
class BacktestReport:
    """综合回测报告

    Combines all analytics into a single report structure.
    """

    # 基本信息
    strategy_name: str
    start_date: date
    end_date: date
    initial_capital: float
    final_nav: float

    # 分析结果
    performance: PerformanceMetrics
    drawdown: DrawdownAnalysis
    costs: CostAttribution
    trading: TradingStats
    positions: PositionStats

    # 元数据
    config: dict = field(default_factory=dict)
    generated_at: str = ""

    # 可选：因子 IC 分析
    factor_analysis: list[FactorICBrief] = field(default_factory=list)

    # 可选：风险摘要（VaR/CVaR）
    risk_summary: RiskSummary | None = None

    # 可选：基准相对指标（IR/TE/Active Return）
    benchmark_relative: BenchmarkRelativeMetrics | None = None

    # 可选：Brinson 绩效归因
    brinson_attribution: Any | None = None

    # 可选：因子暴露分析
    factor_exposure: dict | None = None

    # 可选：双路径执行差异（回测 NextBarOpen 假设 vs 实盘口径）
    # 由 shadow_runner.ShadowComparator 产出。回测收益里落在这块的部分
    # 实盘拿不到，因此它是策略能否上线的必查项而非可选项。
    execution_divergence: dict | None = None

    def summary(self) -> str:
        """生成文本摘要"""
        lines = [
            f"=== 回测报告: {self.strategy_name} ===",
            f"期间: {self.start_date} ~ {self.end_date}",
            f"初始资金: {self.initial_capital:,.0f}",
            f"最终净值: {self.final_nav:,.2f}",
            "",
            "【收益指标】",
            f"  累计收益: {self.performance.total_return:.2%}",
            f"  年化收益: {self.performance.annualized_return:.2%}",
            f"  年化波动: {self.performance.volatility:.2%}",
            f"  夏普比率: {self.performance.sharpe_ratio:.2f}",
            f"  索提诺比率: {self.performance.sortino_ratio:.2f}",
            f"  卡玛比率: {self.performance.calmar_ratio:.2f}",
            "",
            "【风险指标】",
            f"  最大回撤: {self.drawdown.max_drawdown_pct:.2%}",
            f"  回撤开始: {self.drawdown.max_drawdown_start}",
            f"  回撤谷底: {self.drawdown.max_drawdown_end}",
            f"  回撤持续: {self.drawdown.max_drawdown_duration} 天",
            f"  平均回撤: {self.drawdown.avg_drawdown:.2%}",
            "",
            "【交易统计】",
            f"  总交易数: {self.trading.total_trades}",
            f"  买入/卖出: {self.trading.buy_trades}/{self.trading.sell_trades}",
            f"  换手率(年化): {self.trading.turnover_rate:.2%}",
            f"  胜率: {self.trading.win_rate:.1%}",
            f"  盈亏比: {self.trading.profit_factor:.2f}",
            "",
            "【成本分析】",
            f"  总费用: {self.costs.total_fees:,.2f}",
            f"    佣金: {self.costs.commission_total:,.2f}",
            f"    印花税: {self.costs.stamp_tax_total:,.2f}",
            f"    过户费: {self.costs.transfer_fee_total:,.2f}",
            f"  费用占比: {self.costs.cost_vs_nav_pct:.2%}",
            "",
            "【持仓统计】",
            f"  平均持仓数: {self.positions.avg_positions:.1f}",
            f"  最大持仓数: {self.positions.max_positions}",
            f"  最大集中度: {self.positions.max_concentration:.1%}",
            f"  空仓日比例: {self.positions.idle_ratio:.1%}",
        ]

        if self.risk_summary:
            rs = self.risk_summary
            lines.extend([
                "",
                "【风险分析】",
                f"  VaR(95%): {rs.var_95:.2%}",
                f"  CVaR(95%): {rs.cvar_95:.2%}",
                f"  VaR(99%): {rs.var_99:.2%}",
                f"  CVaR(99%): {rs.cvar_99:.2%}",
                f"  最差单日: {rs.worst_day:.2%}",
                f"  最差周度: {rs.worst_week:.2%}",
                f"  尾部比率: {rs.tail_ratio:.2f}",
            ])

        if self.factor_analysis:
            lines.extend(["", "【因子 IC 分析】"])
            for fa in self.factor_analysis:
                lines.append(
                    f"  {fa.factor_name}: IC={fa.ic_mean:.4f}, "
                    f"IR={fa.ir:.4f}, IC>0={fa.ic_positive_rate:.1%}, "
                    f"p={fa.p_value:.4f} ({fa.sample_days}天)"
                )

        if self.benchmark_relative:
            bm = self.benchmark_relative
            bm_label = bm.benchmark_name or "基准"
            lines.extend([
                "",
                f"【基准相对指标 vs {bm_label}】",
                f"  主动收益(年化): {bm.active_return:.2%}",
                f"  跟踪误差: {bm.tracking_error:.2%}",
                f"  信息比率: {bm.information_ratio:.2f}",
                f"  超额最大回撤: {bm.active_max_drawdown:.2%}",
            ])

        if self.brinson_attribution is not None:
            ba = self.brinson_attribution
            lines.extend([
                "",
                "【Brinson 绩效归因】",
                f"  配置效应: {ba.cumulative_allocation:.4%}",
                f"  选择效应: {ba.cumulative_selection:.4%}",
                f"  交互效应: {ba.cumulative_interaction:.4%}",
                f"  总超额:   {ba.cumulative_active:.4%}",
                f"  归因期数: {len(ba.periods)}",
            ])

        if self.factor_exposure is not None:
            fe_summary = self.factor_exposure.get("summary", [])
            if fe_summary:
                lines.extend(["", "【因子暴露分析】"])
                for fs in fe_summary:
                    name = fs.get("factor", "?")
                    mean = fs.get("mean", 0.0)
                    std = fs.get("std", 0.0)
                    latest = fs.get("latest", 0.0)
                    lines.append(
                        f"  {name}: 均值={mean:.4f}, 标准差={std:.4f}, 最新={latest:.4f}"
                    )

        if self.execution_divergence:
            ed = self.execution_divergence
            lines.extend([
                "",
                "【执行口径差异】(回测假设 vs 实盘口径)",
                f"  比较笔数:     {ed.get('total_trades', 0)}",
                f"  平均成交价差: {ed.get('mean_price_diff_bps', 0.0):+.2f} bps",
                f"  最大成交价差: {ed.get('max_price_diff_bps', 0.0):.2f} bps",
                f"  累计净额差:   {ed.get('total_net_diff', 0.0):+,.2f}",
            ])

        return "\n".join(lines)


# ============================================================================
# 计算函数
# ============================================================================


def compute_performance_metrics(
    result: BacktestResult,
    risk_free_rate: float = RISK_FREE_RATE_DEFAULT,
) -> PerformanceMetrics:
    """计算性能指标

    Args:
        result: 回测结果
        risk_free_rate: 年化无风险利率

    Returns:
        PerformanceMetrics
    """
    if not result.daily_snapshots:
        return PerformanceMetrics(
            total_return=0.0,
            annualized_return=0.0,
            volatility=0.0,
            sharpe_ratio=0.0,
            sortino_ratio=0.0,
            calmar_ratio=0.0,
            trading_days=0,
        )

    # 提取日收益序列
    daily_returns = np.array([s.daily_return for s in result.daily_snapshots])
    n_days = len(daily_returns)

    # 总收益
    total_return = result.final_nav / result.initial_capital - 1.0

    # 年化收益 (复利)
    years = n_days / TRADING_DAYS_PER_YEAR
    if years > 0:
        annualized_return = (1 + total_return) ** (1 / years) - 1
    else:
        annualized_return = 0.0

    # 波动率
    if n_days > 1:
        volatility = float(np.std(daily_returns, ddof=1)) * np.sqrt(TRADING_DAYS_PER_YEAR)
    else:
        volatility = 0.0

    # 夏普比率 — §13.2 委托共享模块
    from aipha.utils.metrics import compute_sharpe_ratio as _sharpe
    sharpe_ratio = _sharpe(daily_returns, risk_free_rate=risk_free_rate,
                           trading_days=TRADING_DAYS_PER_YEAR)

    # 索提诺比率 — §13.2 委托共享模块 (Semi-Deviation 标准)
    from aipha.utils.metrics import compute_sortino_ratio as _sortino
    sortino_ratio = _sortino(daily_returns, risk_free_rate=risk_free_rate,
                             trading_days=TRADING_DAYS_PER_YEAR)

    # 卡玛比率 (需要先计算最大回撤)
    drawdown = compute_drawdown_analysis(result)
    if drawdown.max_drawdown_pct > 0:
        calmar_ratio = annualized_return / drawdown.max_drawdown_pct
    else:
        calmar_ratio = float("inf") if annualized_return > 0 else 0.0

    return PerformanceMetrics(
        total_return=total_return,
        annualized_return=annualized_return,
        volatility=volatility,
        sharpe_ratio=sharpe_ratio,
        sortino_ratio=sortino_ratio,
        calmar_ratio=calmar_ratio,
        trading_days=n_days,
    )


def compute_drawdown_analysis(result: BacktestResult) -> DrawdownAnalysis:
    """计算回撤分析

    Args:
        result: 回测结果

    Returns:
        DrawdownAnalysis
    """
    if not result.daily_snapshots:
        return DrawdownAnalysis(
            max_drawdown=0.0,
            max_drawdown_pct=0.0,
            max_drawdown_start=None,
            max_drawdown_end=None,
            max_drawdown_recovery=None,
            max_drawdown_duration=0,
            avg_drawdown=0.0,
            drawdown_series=[],
        )

    navs = np.array([s.nav for s in result.daily_snapshots])
    dates = [s.trade_date for s in result.daily_snapshots]

    # 计算回撤序列
    running_max = np.maximum.accumulate(navs)
    drawdowns = (running_max - navs) / running_max
    drawdown_series = drawdowns.tolist()

    # 找最大回撤
    max_dd_idx = int(np.argmax(drawdowns))
    max_drawdown_pct = float(drawdowns[max_dd_idx])
    max_drawdown = float(running_max[max_dd_idx] - navs[max_dd_idx])

    # 最大回撤的开始（前一个高点）
    peak_before = np.argmax(navs[: max_dd_idx + 1]) if max_dd_idx > 0 else 0
    max_drawdown_start = dates[peak_before] if peak_before < len(dates) else None
    max_drawdown_end = dates[max_dd_idx]

    # 恢复日期
    max_drawdown_recovery = None
    for i in range(max_dd_idx + 1, len(navs)):
        if navs[i] >= running_max[max_dd_idx]:
            max_drawdown_recovery = dates[i]
            break

    # 回撤持续天数
    if max_drawdown_recovery:
        max_drawdown_duration = (max_drawdown_recovery - max_drawdown_start).days if max_drawdown_start else 0
    else:
        max_drawdown_duration = (dates[-1] - max_drawdown_start).days if max_drawdown_start else 0

    # 平均回撤
    avg_drawdown = float(np.mean(drawdowns))

    return DrawdownAnalysis(
        max_drawdown=max_drawdown,
        max_drawdown_pct=max_drawdown_pct,
        max_drawdown_start=max_drawdown_start,
        max_drawdown_end=max_drawdown_end,
        max_drawdown_recovery=max_drawdown_recovery,
        max_drawdown_duration=max_drawdown_duration,
        avg_drawdown=avg_drawdown,
        drawdown_series=drawdown_series,
    )


def compute_cost_attribution(result: BacktestResult) -> CostAttribution:
    """计算成本归因

    Args:
        result: 回测结果

    Returns:
        CostAttribution
    """
    total_fees = float(result.total_fees.total)
    commission = float(result.total_fees.commission)
    stamp_tax = float(result.total_fees.stamp_tax)
    transfer_fee = float(result.total_fees.transfer_fee)

    # 滑点成本估算
    # 从交易记录中计算：对于有 signal_price（策略信号价）的交易，
    # 滑点 = |fill_price - signal_price| * qty；
    # 否则用佣金前名义金额 × slippage_bps 估算（如果配置了 slippage_bps）
    slippage_cost = 0.0
    if result.trades:
        slippage_bps = result.config.get("slippage_bps", 0.0)
        for t in result.trades:
            signal_price = getattr(t, "signal_price", None)
            if signal_price and signal_price > 0:
                slippage_cost += abs(t.price - signal_price) * t.qty
            elif slippage_bps > 0:
                slippage_cost += t.notional * slippage_bps / 10000.0

    # 费用占比
    cost_vs_nav_pct = total_fees / result.final_nav if result.final_nav > 0 else 0.0

    # 年化费用拖累
    n_days = len(result.daily_snapshots)
    years = n_days / TRADING_DAYS_PER_YEAR if n_days > 0 else 1.0
    cost_drag_annualized = total_fees / result.initial_capital / years if years > 0 else 0.0

    return CostAttribution(
        total_fees=total_fees,
        commission_total=commission,
        stamp_tax_total=stamp_tax,
        transfer_fee_total=transfer_fee,
        slippage_cost=slippage_cost,
        cost_vs_nav_pct=cost_vs_nav_pct,
        cost_drag_annualized=cost_drag_annualized,
    )


# ============================================================================
# 盈亏配对辅助函数（FIFO）
# ============================================================================


def _compute_trade_pnl(trades: list) -> tuple[int, int]:
    """FIFO 配对计算盈亏交易数

    按标的分组，将买入按时间排队，卖出时从队头取出配对。
    差价 > 0 为盈利交易，差价 <= 0 为亏损交易。

    Args:
        trades: TradeRecord 列表

    Returns:
        (win_count, loss_count) 盈利/亏损平仓次数
    """
    from collections import deque

    from aipha.domain.models import Side

    # 按标的分组的买入队列: {symbol: deque[(price, remaining_qty)]}
    buy_queues: dict[str, deque] = {}
    win = 0
    loss = 0

    for t in sorted(trades, key=lambda x: x.trade_time):
        if t.side == Side.BUY:
            q = buy_queues.setdefault(t.symbol, deque())
            q.append([t.price, t.qty])
        elif t.side == Side.SELL:
            q = buy_queues.get(t.symbol, deque())
            remain = t.qty
            while remain > 0 and q:
                buy_price, buy_qty = q[0]
                matched = min(remain, buy_qty)
                pnl = (t.price - buy_price) * matched
                if pnl > 0:
                    win += 1
                else:
                    loss += 1
                remain -= matched
                q[0][1] -= matched
                if q[0][1] <= 0:
                    q.popleft()

    return win, loss


def _compute_pnl_details(trades: list) -> dict[str, float]:
    """FIFO 配对计算平均盈亏和盈亏比

    Returns:
        dict with keys: avg_win, avg_loss, profit_factor
    """
    from collections import deque

    from aipha.domain.models import Side

    buy_queues: dict[str, deque] = {}
    total_win = 0.0
    total_loss = 0.0
    win_count = 0
    loss_count = 0

    for t in sorted(trades, key=lambda x: x.trade_time):
        if t.side == Side.BUY:
            q = buy_queues.setdefault(t.symbol, deque())
            q.append([t.price, t.qty])
        elif t.side == Side.SELL:
            q = buy_queues.get(t.symbol, deque())
            remain = t.qty
            while remain > 0 and q:
                buy_price, buy_qty = q[0]
                matched = min(remain, buy_qty)
                pnl = (t.price - buy_price) * matched
                if pnl > 0:
                    total_win += pnl
                    win_count += 1
                else:
                    total_loss += abs(pnl)
                    loss_count += 1
                remain -= matched
                q[0][1] -= matched
                if q[0][1] <= 0:
                    q.popleft()

    avg_win = total_win / win_count if win_count > 0 else 0.0
    avg_loss = total_loss / loss_count if loss_count > 0 else 0.0
    profit_factor = total_win / total_loss if total_loss > 0 else (
        float("inf") if total_win > 0 else 0.0
    )

    return {
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
    }


def compute_trading_stats(result: BacktestResult) -> TradingStats:
    """计算交易统计

    Args:
        result: 回测结果

    Returns:
        TradingStats
    """
    from aipha.domain.models import Side

    if not result.trades:
        return TradingStats(
            total_trades=0,
            buy_trades=0,
            sell_trades=0,
            avg_trade_size=0.0,
            avg_holding_period=0.0,
            turnover_rate=0.0,
            win_trades=0,
            loss_trades=0,
            win_rate=0.0,
            avg_win=0.0,
            avg_loss=0.0,
            profit_factor=0.0,
        )

    trades = result.trades
    total_trades = len(trades)

    buy_trades = sum(1 for t in trades if t.side == Side.BUY)
    sell_trades = sum(1 for t in trades if t.side == Side.SELL)

    # 平均交易金额
    total_notional = sum(t.notional for t in trades)
    avg_trade_size = total_notional / total_trades

    # 换手率（单边年化）
    n_days = len(result.daily_snapshots)
    years = n_days / TRADING_DAYS_PER_YEAR if n_days > 0 else 1.0
    avg_nav = (result.initial_capital + result.final_nav) / 2
    turnover_rate = (total_notional / 2) / avg_nav / years if avg_nav > 0 and years > 0 else 0.0

    # 持仓周期估计 (简化：用天数/交易次数)
    avg_holding_period = n_days / (sell_trades + 1) if sell_trades > 0 else float(n_days)

    # 盈亏统计：FIFO 配对买卖交易
    win_trades, loss_trades = _compute_trade_pnl(trades)
    total_closed = win_trades + loss_trades
    win_rate = win_trades / total_closed if total_closed > 0 else 0.0

    # 计算平均盈亏和盈亏比
    pnl_details = _compute_pnl_details(trades)
    avg_win = pnl_details["avg_win"]
    avg_loss = pnl_details["avg_loss"]
    profit_factor = pnl_details["profit_factor"]

    return TradingStats(
        total_trades=total_trades,
        buy_trades=buy_trades,
        sell_trades=sell_trades,
        avg_trade_size=avg_trade_size,
        avg_holding_period=avg_holding_period,
        turnover_rate=turnover_rate,
        win_trades=win_trades,
        loss_trades=loss_trades,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        profit_factor=profit_factor,
    )


def compute_position_stats(result: BacktestResult) -> PositionStats:
    """计算持仓统计

    Args:
        result: 回测结果

    Returns:
        PositionStats
    """
    if not result.daily_snapshots:
        return PositionStats(
            avg_positions=0.0,
            max_positions=0,
            min_positions=0,
            avg_concentration=0.0,
            max_concentration=0.0,
            idle_ratio=1.0,
        )

    snapshots = result.daily_snapshots

    # 持仓数量统计
    n_positions = [len(s.positions) for s in snapshots]
    avg_positions = float(np.mean(n_positions))
    max_positions = max(n_positions)

    # 非零天最小持仓
    non_zero = [n for n in n_positions if n > 0]
    min_positions = min(non_zero) if non_zero else 0

    # 集中度（最大持仓占比）
    concentrations = []
    for s in snapshots:
        if s.position_values and s.nav > 0:
            max_pos_value = max(s.position_values.values())
            concentrations.append(max_pos_value / s.nav)
        else:
            concentrations.append(0.0)

    avg_concentration = float(np.mean(concentrations)) if concentrations else 0.0
    max_concentration = max(concentrations) if concentrations else 0.0

    return PositionStats(
        avg_positions=avg_positions,
        max_positions=max_positions,
        min_positions=min_positions,
        avg_concentration=avg_concentration,
        max_concentration=max_concentration,
        idle_ratio=1.0 - len(non_zero) / len(n_positions) if n_positions else 0.0,
    )


def compute_factor_ic_briefs(
    factor_exposures: dict[str, dict[date, dict[str, float]]],
    return_data: dict[date, dict[str, float]],
) -> list[FactorICBrief]:
    """从因子暴露和收益数据计算各因子 IC 摘要

    本函数是 factors.analysis.FactorAnalyzer 与回测报告之间的桥梁，
    不直接依赖 scipy（依赖 factors.analysis 模块）。

    Args:
        factor_exposures: {因子名: {日期: {标的: 因子值}}}
        return_data: {日期: {标的: 日收益率}}

    Returns:
        各因子 IC 分析摘要列表
    """
    briefs: list[FactorICBrief] = []

    try:
        from aipha.factors.analysis import FactorAnalyzer
    except ImportError:
        logger.warning("factors.analysis 不可用，跳过因子 IC 计算")
        return briefs

    for factor_name, factor_data in factor_exposures.items():
        try:
            analyzer = FactorAnalyzer(
                factor_data=factor_data,
                return_data=return_data,
                factor_name=factor_name,
            )
            ic_result = analyzer.compute_ic(method="spearman", forward_days=1)
            briefs.append(
                FactorICBrief(
                    factor_name=factor_name,
                    ic_mean=ic_result.ic_mean,
                    ic_std=ic_result.ic_std,
                    ir=ic_result.ir,
                    ic_positive_rate=ic_result.ic_positive_rate,
                    t_stat=ic_result.t_stat,
                    p_value=ic_result.p_value,
                    sample_days=len(ic_result.ic_series),
                )
            )
        except Exception as e:
            logger.warning(f"因子 {factor_name} IC 计算失败: {e}")
            continue

    # 按 |IC| 降序排序
    briefs.sort(key=lambda b: abs(b.ic_mean), reverse=True)
    return briefs


def compute_risk_summary(daily_returns: list[float]) -> RiskSummary | None:
    """从日收益率序列计算风险摘要

    Args:
        daily_returns: 日收益率序列

    Returns:
        RiskSummary 或 None（数据不足时）
    """
    if len(daily_returns) < 10:
        return None

    sorted_rets = sorted(daily_returns)
    n = len(sorted_rets)

    # Historical VaR/CVaR
    idx_95 = max(0, int(n * 0.05) - 1)
    idx_99 = max(0, int(n * 0.01) - 1)

    var_95 = -sorted_rets[idx_95]
    var_99 = -sorted_rets[idx_99]

    # CVaR = 均值 of 尾部
    tail_95 = sorted_rets[: idx_95 + 1] if idx_95 >= 0 else sorted_rets[:1]
    tail_99 = sorted_rets[: idx_99 + 1] if idx_99 >= 0 else sorted_rets[:1]
    cvar_95 = -sum(tail_95) / max(len(tail_95), 1)
    cvar_99 = -sum(tail_99) / max(len(tail_99), 1)

    # worst day
    worst_day = min(daily_returns)

    # worst week (rolling 5-day)
    if n >= 5:
        worst_week = min(
            sum(daily_returns[i : i + 5]) for i in range(n - 4)
        )
    else:
        worst_week = sum(daily_returns)

    # tail ratio: right 95th / |left 5th|
    right_95 = sorted_rets[int(n * 0.95)] if n > 0 else 0.0
    left_05 = abs(sorted_rets[idx_95]) if sorted_rets[idx_95] != 0 else 1e-10
    tail_ratio = right_95 / left_05 if left_05 > 1e-12 else 0.0

    return RiskSummary(
        var_95=var_95,
        cvar_95=cvar_95,
        var_99=var_99,
        cvar_99=cvar_99,
        worst_day=worst_day,
        worst_week=worst_week,
        tail_ratio=tail_ratio,
    )


def compute_benchmark_relative(
    strategy_daily_returns: list[float],
    benchmark_daily_returns: list[float],
    benchmark_name: str = "",
) -> BenchmarkRelativeMetrics | None:
    """计算基准相对收益指标

    Args:
        strategy_daily_returns: 策略日收益率序列
        benchmark_daily_returns: 基准日收益率序列（长度须与策略一致）
        benchmark_name: 基准名称（如 "沪深300"）

    Returns:
        BenchmarkRelativeMetrics 或 None（数据不足时）
    """
    n = min(len(strategy_daily_returns), len(benchmark_daily_returns))
    if n < 5:
        return None

    # 主动日收益率 = 策略日收益 - 基准日收益
    active_rets = [
        strategy_daily_returns[i] - benchmark_daily_returns[i]
        for i in range(n)
    ]

    # 年化主动收益
    mean_active = sum(active_rets) / n
    active_return = mean_active * TRADING_DAYS_PER_YEAR

    # 跟踪误差 = 年化 std(active_rets)
    var_active = sum((r - mean_active) ** 2 for r in active_rets) / max(n - 1, 1)
    tracking_error = var_active ** 0.5 * TRADING_DAYS_PER_YEAR ** 0.5

    # 信息比率 = active_return / tracking_error
    if tracking_error > 1e-10:
        information_ratio = active_return / tracking_error
    else:
        information_ratio = 0.0

    # 超额收益累计曲线的最大回撤
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in active_rets:
        cum += r
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd

    return BenchmarkRelativeMetrics(
        active_return=active_return,
        tracking_error=tracking_error,
        information_ratio=information_ratio,
        active_max_drawdown=max_dd,
        benchmark_name=benchmark_name,
    )


def _compute_brinson_from_snapshots(
    snapshots: list,
    sector_mapping: dict[str, str],
    benchmark_sector_weights: dict[str, float],
    benchmark_sector_returns: dict[str, float],
) -> Any:
    """从回测快照和行业映射计算 Brinson 多期归因。

    将组合每日持仓 → 按行业聚合权重和收益率 → 调用 brinson_single_period。
    对无行业映射的标的归入 "其他" 行业。

    Args:
        snapshots: DailySnapshot 列表
        sector_mapping: {symbol: sector_name}
        benchmark_sector_weights: 基准行业权重 {sector: weight}（视为静态基准）
        benchmark_sector_returns: 基准行业收益率 {sector: daily_return}（视为静态基准）

    Returns:
        BrinsonMultiPeriodResult
    """
    from aipha.reporting.brinson_attribution import (
        brinson_multi_period,
    )

    period_data: dict[date, tuple[
        dict[str, float], dict[str, float],
        dict[str, float], dict[str, float],
    ]] = {}

    for i in range(1, len(snapshots)):
        prev_snap = snapshots[i - 1]
        curr_snap = snapshots[i]
        trade_date = curr_snap.trade_date

        # 组合按行业聚合权重（基于前一日持仓市值占 NAV 比例）
        prev_nav = prev_snap.nav if prev_snap.nav > 0 else 1.0
        sector_weights_p: dict[str, float] = {}
        sector_values_prev: dict[str, float] = {}
        sector_values_curr: dict[str, float] = {}

        for sym, val in prev_snap.position_values.items():
            sector = sector_mapping.get(sym, "其他")
            sector_weights_p[sector] = sector_weights_p.get(sector, 0.0) + val / prev_nav
            sector_values_prev[sector] = sector_values_prev.get(sector, 0.0) + val

        for sym, val in curr_snap.position_values.items():
            sector = sector_mapping.get(sym, "其他")
            sector_values_curr[sector] = sector_values_curr.get(sector, 0.0) + val

        # 组合按行业收益率 = (当期市值 - 前期市值) / 前期市值
        sector_returns_p: dict[str, float] = {}
        all_sectors = set(sector_values_prev) | set(sector_values_curr)
        for sector in all_sectors:
            prev_val = sector_values_prev.get(sector, 0.0)
            curr_val = sector_values_curr.get(sector, 0.0)
            if prev_val > 0:
                sector_returns_p[sector] = (curr_val - prev_val) / prev_val
            else:
                sector_returns_p[sector] = 0.0

        if sector_weights_p:
            period_data[trade_date] = (
                sector_weights_p,
                benchmark_sector_weights,
                sector_returns_p,
                benchmark_sector_returns,
            )

    if not period_data:
        return None

    return brinson_multi_period(period_data)


def compute_factor_exposure_from_snapshots(
    snapshots: list,
    factor_scores_by_date: dict[date, dict[str, dict[str, float]]],
    factor_names: list[str] | None = None,
) -> dict | None:
    """从回测快照和因子分数计算因子暴露时间序列。

    Args:
        snapshots: DailySnapshot 列表
        factor_scores_by_date: 每日因子分数
            格式: {trade_date: {factor_name: {symbol: score}}}
        factor_names: 因子名列表。若 None 则从 factor_scores_by_date 自动推断。

    Returns:
        暴露分析字典（含 snapshots + summary），可直接存入 BacktestReport.factor_exposure。
        若数据不足则返回 None。
    """
    if not snapshots or not factor_scores_by_date:
        return None

    from aipha.reporting.factor_exposure import FactorExposureAnalyzer

    # 推断因子名
    if factor_names is None:
        all_factors: set[str] = set()
        for daily_scores in factor_scores_by_date.values():
            all_factors.update(daily_scores.keys())
        factor_names = sorted(all_factors)

    if not factor_names:
        return None

    analyzer = FactorExposureAnalyzer(factor_names=factor_names)

    for snap in snapshots:
        daily_scores = factor_scores_by_date.get(snap.trade_date)
        if daily_scores is None or not snap.position_values:
            continue
        analyzer.record_from_position_values(
            trade_date=snap.trade_date,
            position_values=snap.position_values,
            nav=snap.nav,
            factor_scores=daily_scores,
        )

    if analyzer.num_snapshots == 0:
        return None

    return analyzer.to_dict()


def generate_report(
    result: BacktestResult,
    risk_free_rate: float = RISK_FREE_RATE_DEFAULT,
    benchmark_daily_returns: list[float] | None = None,
    benchmark_name: str = "",
    sector_mapping: dict[str, str] | None = None,
    benchmark_sector_weights: dict[str, float] | None = None,
    benchmark_sector_returns: dict[str, float] | None = None,
    factor_scores_by_date: dict[date, dict[str, dict[str, float]]] | None = None,
    price_lookup: Callable[[str, date], tuple[float, float] | None] | None = None,
) -> BacktestReport:
    """生成完整回测报告

    Args:
        result: 回测结果
        risk_free_rate: 无风险利率
        benchmark_daily_returns: 基准日收益率序列（可选，用于计算 IR/TE）
        benchmark_name: 基准名称（如 "沪深300"）
        sector_mapping: 股票 → 行业映射 {symbol: sector}（可选，Brinson 用）
        benchmark_sector_weights: 基准行业权重 {sector: weight}（可选，Brinson 用）
        benchmark_sector_returns: 基准行业收益率 {sector: return}（可选，Brinson 用）
        factor_scores_by_date: 每日因子分数（可选，因子暴露分析用）
            格式: {trade_date: {factor_name: {symbol: score}}}
        price_lookup: (symbol, trade_date) → (当日收盘价, 次日开盘价)。
            提供时计算双路径执行差异，量化「回测按次日开盘成交」与
            「实盘按当日收盘成交」两种口径的收益落差。

    Returns:
        BacktestReport
    """
    from datetime import datetime as dt

    # 计算日收益率序列用于风险摘要
    risk_summary = None
    if len(result.daily_snapshots) >= 2:
        nav_list = [s.nav for s in result.daily_snapshots]
        daily_rets = [
            nav_list[i] / nav_list[i - 1] - 1
            for i in range(1, len(nav_list))
            if nav_list[i - 1] > 0
        ]
        risk_summary = compute_risk_summary(daily_rets)

    # 计算基准相对指标
    benchmark_relative = None
    if benchmark_daily_returns and len(result.daily_snapshots) >= 2:
        nav_list = [s.nav for s in result.daily_snapshots]
        strat_rets = [
            nav_list[i] / nav_list[i - 1] - 1
            for i in range(1, len(nav_list))
            if nav_list[i - 1] > 0
        ]
        benchmark_relative = compute_benchmark_relative(
            strat_rets, benchmark_daily_returns, benchmark_name,
        )

    # 计算 Brinson 归因（需要行业映射 + 基准行业数据）
    brinson_result = None
    if (
        sector_mapping
        and benchmark_sector_weights
        and benchmark_sector_returns
        and len(result.daily_snapshots) >= 2
    ):
        brinson_result = _compute_brinson_from_snapshots(
            result.daily_snapshots, sector_mapping,
            benchmark_sector_weights, benchmark_sector_returns,
        )

    # 计算因子暴露分析
    factor_exposure_result = None
    if factor_scores_by_date and len(result.daily_snapshots) >= 1:
        factor_exposure_result = compute_factor_exposure_from_snapshots(
            result.daily_snapshots, factor_scores_by_date,
        )

    # 计算双路径执行差异（需要行情查询才能拿到收盘价/次日开盘价）
    execution_divergence = None
    if price_lookup is not None and result.trades:
        execution_divergence = _compute_execution_divergence(
            result.trades, price_lookup,
        )

    return BacktestReport(
        strategy_name=result.strategy_name,
        start_date=result.start_date,
        end_date=result.end_date,
        initial_capital=result.initial_capital,
        final_nav=result.final_nav,
        performance=compute_performance_metrics(result, risk_free_rate),
        drawdown=compute_drawdown_analysis(result),
        costs=compute_cost_attribution(result),
        trading=compute_trading_stats(result),
        positions=compute_position_stats(result),
        config=result.config,
        generated_at=dt.now().isoformat(),
        risk_summary=risk_summary,
        benchmark_relative=benchmark_relative,
        brinson_attribution=brinson_result,
        factor_exposure=factor_exposure_result,
        execution_divergence=execution_divergence,
    )


def _compute_execution_divergence(
    trades: list,
    price_lookup: Callable[[str, date], tuple[float, float] | None],
) -> dict | None:
    """量化回测执行口径与实盘口径的差异。

    行情缺失或比较器不可用时返回 None —— 附加分析不应让主报告失败。
    """
    try:
        from aipha.backtest.shadow_runner import ShadowComparator, build_trade_events

        events = build_trade_events(trades, price_lookup)
        if not events:
            return None
        return ShadowComparator.for_cn_equity().compare_trades(events).to_dict()
    except Exception as exc:  # pragma: no cover - 防御性
        logger.warning("执行口径差异计算失败，跳过: %s", exc)
        return None


def _compute_yearly_returns(
    result: BacktestResult,
) -> dict[str, float]:
    """从 daily_snapshots 计算逐年收益率。

    Returns:
        {"2015": 0.18, "2016": -0.05, ...} — 每年年末 NAV vs 年初 NAV 的年度收益率。
    """
    if not result.daily_snapshots:
        return {}

    snapshots_by_year: dict[str, list] = {}
    for snap in result.daily_snapshots:
        year = str(snap.trade_date.year)
        if year not in snapshots_by_year:
            snapshots_by_year[year] = []
        snapshots_by_year[year].append(snap)

    yearly: dict[str, float] = {}
    prev_nav = result.initial_capital
    for year in sorted(snapshots_by_year.keys()):
        year_end_nav = snapshots_by_year[year][-1].nav
        ret = (year_end_nav - prev_nav) / prev_nav if prev_nav > 0 else 0.0
        yearly[year] = round(ret, 6)
        prev_nav = year_end_nav
    return yearly


def extract_metrics_dict(
    result: BacktestResult,
    risk_free_rate: float = RISK_FREE_RATE_DEFAULT,
    n_trials: int | None = None,
    pbo: float | None = None,
    oos_sharpe: float | None = None,
) -> dict[str, Any]:
    """从回测结果提取扁平化指标字典，用于质量门禁检查。

    键名与 QualityGate.metric 对应。包含：
    - 收益指标: total_return, annualized_return, sharpe_ratio, sortino_ratio, calmar_ratio, volatility
    - 风险指标: max_drawdown_pct, avg_drawdown
    - 交易指标: win_rate, profit_factor, turnover_rate, total_trades
    - 逐年指标: yearly_returns（{年份: 年化收益率} 字典）
    - 持仓指标: avg_positions, max_positions, max_concentration
    - 过拟合指标: dsr, dsr_p_value, pbo, oos_sharpe_decay（仅在提供
      ``n_trials`` / ``pbo`` / ``oos_sharpe`` 时出现）

    Args:
        result: 回测结果
        risk_free_rate: 年化无风险利率
        n_trials: 本批试验总次数，用于 DSR。不传则不产出 DSR 指标，
            对应门禁记为「未检验」
        pbo: 外部算好的回测过拟合概率（如 ComboCPCV 输出）
        oos_sharpe: 样本外夏普，用于计算 IS→OOS 衰减

    Returns:
        指标名→数值 的字典
    """
    perf = compute_performance_metrics(result, risk_free_rate)
    dd = compute_drawdown_analysis(result)
    trading = compute_trading_stats(result)
    positions = compute_position_stats(result)

    metrics = {
        # 收益
        "total_return": perf.total_return,
        "annualized_return": perf.annualized_return,
        "annual_return": perf.annualized_return,  # 别名，兼容 reporting/report.py 命名
        "volatility": perf.volatility,
        "sharpe_ratio": perf.sharpe_ratio,
        "sortino_ratio": perf.sortino_ratio,
        "calmar_ratio": perf.calmar_ratio,
        "trading_days": float(perf.trading_days),
        # 风险
        "max_drawdown_pct": dd.max_drawdown_pct,
        "avg_drawdown": dd.avg_drawdown,
        # 交易
        "total_trades": float(trading.total_trades),
        "win_rate": trading.win_rate,
        "profit_factor": trading.profit_factor,
        "turnover_rate": trading.turnover_rate,
        # 持仓
        "avg_positions": positions.avg_positions,
        "max_positions": float(positions.max_positions),
        "max_concentration": positions.max_concentration,
        "idle_ratio": positions.idle_ratio,
    }

    nav_list = [s.nav for s in result.daily_snapshots]
    daily_rets = [
        nav_list[i] / nav_list[i - 1] - 1
        for i in range(1, len(nav_list))
        if nav_list[i - 1] > 0
    ]

    # VaR/CVaR 风险指标
    if len(result.daily_snapshots) >= 2:
        rs = compute_risk_summary(daily_rets)
        if rs is not None:
            metrics["var_95"] = rs.var_95
            metrics["cvar_95"] = rs.cvar_95
            metrics["var_99"] = rs.var_99
            metrics["cvar_99"] = rs.cvar_99

    # 逐年收益（>=TRADING_DAYS_PER_YEAR 个交易日才有意义）
    if len(result.daily_snapshots) >= TRADING_DAYS_PER_YEAR:
        metrics["yearly_returns"] = _compute_yearly_returns(result)

    # 过拟合指标 — 需要回测之外的输入，缺则不产出（门禁记为未检验）
    if n_trials is not None or pbo is not None or oos_sharpe is not None:
        from aipha.backtest.quality_gates import compute_overfitting_metrics

        metrics.update(compute_overfitting_metrics(
            daily_returns=daily_rets,
            sharpe_ratio=perf.sharpe_ratio,
            n_trials=n_trials or 0,
            pbo=pbo,
            oos_sharpe=oos_sharpe,
        ))

    return metrics
