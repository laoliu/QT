"""亏损追踪与归因分析模块。

提供完整的交易生命周期追踪、亏损原因归因、回撤-持仓关联分析，
帮助系统化地记录、追踪和分析造成亏损的因素，以迭代优化策略。

核心组件:
- RoundTrip: 一笔完整的开仓→平仓生命周期记录
- RoundTripTracker: FIFO 配对构建 round-trip 记录
- LossAnalyzer: 亏损因素分类与统计分析
- DrawdownAttributor: 回撤区间与持仓关联归因

设计原则:
- 基于已有 BacktestResult / TradeRecord 构建，不侵入引擎核心
- 所有分析均为纯函数/只读计算（No side effects）
- 输出结构化数据，便于下游消费（web_review / 报告 / 归档）
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aipha.backtest.engine import BacktestResult, DailySnapshot, TradeRecord

logger = logging.getLogger(__name__)


# ============================================================================
# Round-Trip（完整交易生命周期）
# ============================================================================


@dataclass(frozen=True, slots=True)
class RoundTrip:
    """一笔完整的 开仓→平仓 生命周期记录。

    Attributes:
        symbol: 标的代码
        entry_date: 入场日期
        exit_date: 出场日期
        entry_price: 入场均价
        exit_price: 出场均价
        qty: 交易数量（成交股数）
        gross_pnl: 毛盈亏（不含费用）
        fees: 买入+卖出产生的总费用
        net_pnl: 净盈亏（毛盈亏 - 费用）
        pnl_pct: 净收益率 (net_pnl / 入场成本)
        holding_days: 持仓天数（日历日）
        exit_reason: 出场原因（从 OrderIntent.reason 传递）
        entry_reason: 入场原因
    """

    symbol: str
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    qty: int
    gross_pnl: float
    fees: float
    net_pnl: float
    pnl_pct: float
    holding_days: int
    exit_reason: str = ""
    entry_reason: str = ""


# ============================================================================
# 亏损原因分类
# ============================================================================


class ExitCategory:
    """退出原因分类枚举（字符串常量，避免 Enum 序列化问题）"""

    STOP_LOSS = "止损"
    ROTATION = "轮动调仓"
    FORCE_REDEEM = "强赎退出"
    ENGINE_STOP_LOSS = "引擎止损"
    MANUAL = "手动退出"
    UNKNOWN = "未知"


def classify_exit_reason(reason: str | None) -> str:
    """将 OrderIntent.reason 字符串分类为标准退出类别。

    Args:
        reason: TradeRecord 或 OrderIntent 的 reason 字段

    Returns:
        ExitCategory 常量
    """
    if not reason:
        return ExitCategory.UNKNOWN

    r = reason.lower()

    if "止损" in r or "stop" in r:
        if "引擎" in r or "engine" in r:
            return ExitCategory.ENGINE_STOP_LOSS
        return ExitCategory.STOP_LOSS

    if "轮动" in r or "rotation" in r or "rebalance" in r or "调仓" in r:
        return ExitCategory.ROTATION

    if "强赎" in r or "redeem" in r or "called" in r:
        return ExitCategory.FORCE_REDEEM

    if "手动" in r or "manual" in r:
        return ExitCategory.MANUAL

    return ExitCategory.UNKNOWN


# ============================================================================
# Round-Trip 追踪器
# ============================================================================


@dataclass
class _BuyLot:
    """买入批次（FIFO 配对用）"""

    price: float
    qty: int
    date: date
    fees: float
    reason: str


class RoundTripTracker:
    """基于 FIFO 的 Round-Trip（完整交易生命周期）构建器。

    从 TradeRecord 列表构建每笔交易从入场到出场的完整记录，
    包括盈亏、持仓天数、退出原因等信息。

    用法::

        tracker = RoundTripTracker()
        round_trips = tracker.build(result.trades)

        # 所有亏损交易
        losses = [rt for rt in round_trips if rt.net_pnl < 0]
    """

    def build(self, trades: list[TradeRecord]) -> list[RoundTrip]:
        """从交易记录列表构建 round-trip 列表。

        Args:
            trades: BacktestResult.trades

        Returns:
            RoundTrip 列表（按出场日期排序）
        """
        from aipha.domain.models import Side

        # 按标的分组的买入队列
        buy_queues: dict[str, deque[_BuyLot]] = {}
        round_trips: list[RoundTrip] = []

        for t in sorted(trades, key=lambda x: x.trade_time):
            reason_str = getattr(t, "reason", "") or ""

            if t.side == Side.BUY:
                q = buy_queues.setdefault(t.symbol, deque())
                q.append(_BuyLot(
                    price=t.price,
                    qty=t.qty,
                    date=t.trade_date,
                    fees=t.fees.total,
                    reason=reason_str,
                ))

            elif t.side == Side.SELL:
                q = buy_queues.get(t.symbol, deque())
                remain = t.qty
                sell_fee_per_share = t.fees.total / t.qty if t.qty > 0 else 0

                while remain > 0 and q:
                    lot = q[0]
                    matched = min(remain, lot.qty)

                    # 计算该配对的费用
                    buy_fee = lot.fees * (matched / (matched + lot.qty - matched))
                    sell_fee = sell_fee_per_share * matched
                    total_fees = buy_fee + sell_fee

                    gross_pnl = (t.price - lot.price) * matched
                    net_pnl = gross_pnl - total_fees
                    entry_cost = lot.price * matched
                    pnl_pct = net_pnl / entry_cost if entry_cost > 0 else 0.0
                    holding_days = (t.trade_date - lot.date).days

                    round_trips.append(RoundTrip(
                        symbol=t.symbol,
                        entry_date=lot.date,
                        exit_date=t.trade_date,
                        entry_price=lot.price,
                        exit_price=t.price,
                        qty=matched,
                        gross_pnl=gross_pnl,
                        fees=total_fees,
                        net_pnl=net_pnl,
                        pnl_pct=pnl_pct,
                        holding_days=holding_days,
                        exit_reason=reason_str,
                        entry_reason=lot.reason,
                    ))

                    remain -= matched
                    lot.qty -= matched
                    if lot.qty <= 0:
                        q.popleft()

        round_trips.sort(key=lambda rt: rt.exit_date)
        return round_trips


# ============================================================================
# 亏损分析器
# ============================================================================


@dataclass(frozen=True, slots=True)
class LossSummary:
    """亏损分析汇总。

    Attributes:
        total_round_trips: 总 round-trip 数
        loss_count: 亏损 round-trip 数
        win_count: 盈利 round-trip 数
        total_loss: 总亏损金额（负数）
        total_profit: 总盈利金额（正数）
        avg_loss: 平均单笔亏损
        avg_profit: 平均单笔盈利
        worst_loss: 最大单笔亏损
        worst_loss_pct: 最大单笔亏损百分比
        avg_loss_holding_days: 亏损交易平均持仓天数
        avg_win_holding_days: 盈利交易平均持仓天数
        loss_by_category: 按退出原因分类的亏损统计 {category: (count, total_loss)}
        loss_by_month: 按月份分类的亏损统计 {YYYY-MM: (count, total_loss)}
        top_losers: 亏损最大的 round-trip 列表
    """

    total_round_trips: int
    loss_count: int
    win_count: int
    total_loss: float
    total_profit: float
    avg_loss: float
    avg_profit: float
    worst_loss: float
    worst_loss_pct: float
    avg_loss_holding_days: float
    avg_win_holding_days: float
    loss_by_category: dict[str, tuple[int, float]]
    loss_by_month: dict[str, tuple[int, float]]
    top_losers: list[RoundTrip]


class LossAnalyzer:
    """亏损因素分析器。

    对 round-trip 列表进行多维度亏损分析，包括：
    1. 亏损金额/频率统计
    2. 退出原因归因（止损/轮动/强赎/引擎止损）
    3. 时间维度分布（哪些月份亏损集中）
    4. Top N 最大亏损交易详情

    用法::

        analyzer = LossAnalyzer(top_n=20)
        summary = analyzer.analyze(round_trips)
    """

    def __init__(self, top_n: int = 20):
        self.top_n = top_n

    def analyze(self, round_trips: list[RoundTrip]) -> LossSummary:
        """对 round-trip 列表进行亏损分析。

        Args:
            round_trips: RoundTripTracker.build() 的输出

        Returns:
            LossSummary
        """
        if not round_trips:
            return LossSummary(
                total_round_trips=0, loss_count=0, win_count=0,
                total_loss=0.0, total_profit=0.0,
                avg_loss=0.0, avg_profit=0.0,
                worst_loss=0.0, worst_loss_pct=0.0,
                avg_loss_holding_days=0.0, avg_win_holding_days=0.0,
                loss_by_category={}, loss_by_month={},
                top_losers=[],
            )

        losses = [rt for rt in round_trips if rt.net_pnl < 0]
        wins = [rt for rt in round_trips if rt.net_pnl >= 0]

        total_loss = sum(rt.net_pnl for rt in losses)
        total_profit = sum(rt.net_pnl for rt in wins)
        avg_loss = total_loss / len(losses) if losses else 0.0
        avg_profit = total_profit / len(wins) if wins else 0.0

        worst = min(losses, key=lambda rt: rt.net_pnl) if losses else None
        worst_loss = worst.net_pnl if worst else 0.0
        worst_loss_pct = worst.pnl_pct if worst else 0.0

        avg_loss_days = (
            sum(rt.holding_days for rt in losses) / len(losses) if losses else 0.0
        )
        avg_win_days = (
            sum(rt.holding_days for rt in wins) / len(wins) if wins else 0.0
        )

        # 按退出原因分类
        loss_by_category: dict[str, tuple[int, float]] = {}
        for rt in losses:
            cat = classify_exit_reason(rt.exit_reason)
            cnt, amt = loss_by_category.get(cat, (0, 0.0))
            loss_by_category[cat] = (cnt + 1, amt + rt.net_pnl)

        # 按月份分类
        loss_by_month: dict[str, tuple[int, float]] = {}
        for rt in losses:
            key = rt.exit_date.strftime("%Y-%m")
            cnt, amt = loss_by_month.get(key, (0, 0.0))
            loss_by_month[key] = (cnt + 1, amt + rt.net_pnl)

        # Top N 亏损
        top_losers = sorted(losses, key=lambda rt: rt.net_pnl)[:self.top_n]

        return LossSummary(
            total_round_trips=len(round_trips),
            loss_count=len(losses),
            win_count=len(wins),
            total_loss=total_loss,
            total_profit=total_profit,
            avg_loss=avg_loss,
            avg_profit=avg_profit,
            worst_loss=worst_loss,
            worst_loss_pct=worst_loss_pct,
            avg_loss_holding_days=avg_loss_days,
            avg_win_holding_days=avg_win_days,
            loss_by_category=loss_by_category,
            loss_by_month=loss_by_month,
            top_losers=top_losers,
        )


# ============================================================================
# 回撤-持仓归因
# ============================================================================


@dataclass(frozen=True, slots=True)
class DrawdownPeriod:
    """回撤区间信息。

    Attributes:
        peak_date: 峰值日期
        trough_date: 谷底日期
        recovery_date: 恢复日期 (None if not recovered)
        drawdown_pct: 回撤百分比 (正数表示亏损)
        duration_days: 从峰值到谷底的日历天数
        recovery_days: 从谷底到恢复的日历天数 (None if not recovered)
        position_contributions: 各持仓对回撤的贡献 {symbol: contribution_pct}
        trades_during: 回撤期间发生的交易列表
    """

    peak_date: date
    trough_date: date
    recovery_date: date | None
    drawdown_pct: float
    duration_days: int
    recovery_days: int | None
    position_contributions: dict[str, float]
    trades_during: list[RoundTrip]


class DrawdownAttributor:
    """回撤-持仓归因分析器。

    将每次重大回撤与期间持仓变动关联，识别导致回撤的具体标的，
    帮助理解"哪些持仓造成了这次亏损"。

    用法::

        attributor = DrawdownAttributor(min_dd_pct=0.03)
        periods = attributor.analyze(result)
    """

    def __init__(self, min_dd_pct: float = 0.03, max_periods: int = 10):
        """
        Args:
            min_dd_pct: 最小回撤百分比阈值（只分析超过此阈值的回撤）
            max_periods: 最多返回多少个回撤区间
        """
        self.min_dd_pct = min_dd_pct
        self.max_periods = max_periods

    def analyze(self, result: BacktestResult) -> list[DrawdownPeriod]:
        """分析回测结果中的重大回撤区间。

        Args:
            result: BacktestResult

        Returns:
            DrawdownPeriod 列表（按回撤幅度降序）
        """
        snapshots = result.daily_snapshots
        if len(snapshots) < 2:
            return []

        # 1. 识别回撤区间
        raw_periods = self._find_drawdown_periods(snapshots)

        # 2. 构建 round-trip（用于关联）
        tracker = RoundTripTracker()
        round_trips = tracker.build(result.trades)

        # 3. 对每个回撤区间做持仓归因
        attributed: list[DrawdownPeriod] = []
        for period_info in raw_periods[:self.max_periods]:
            contributions = self._attribute_positions(
                snapshots, period_info, result
            )
            trades_during = [
                rt for rt in round_trips
                if period_info["peak_date"] <= rt.exit_date <= (
                    period_info.get("recovery_date") or period_info["trough_date"]
                )
            ]

            attributed.append(DrawdownPeriod(
                peak_date=period_info["peak_date"],
                trough_date=period_info["trough_date"],
                recovery_date=period_info.get("recovery_date"),
                drawdown_pct=period_info["dd_pct"],
                duration_days=period_info["duration_days"],
                recovery_days=period_info.get("recovery_days"),
                position_contributions=contributions,
                trades_during=trades_during,
            ))

        return attributed

    def _find_drawdown_periods(
        self, snapshots: list[DailySnapshot],
    ) -> list[dict]:
        """识别所有超过阈值的回撤区间。"""
        navs = [s.nav for s in snapshots]
        dates_list = [s.trade_date for s in snapshots]

        peak = navs[0]
        peak_idx = 0
        max_dd_in_segment = 0.0
        max_dd_idx = 0
        periods: list[dict] = []

        for i in range(1, len(navs)):
            if navs[i] > peak:
                # 新高 → 记录前一段回撤
                if max_dd_in_segment > self.min_dd_pct:
                    periods.append({
                        "peak_date": dates_list[peak_idx],
                        "trough_date": dates_list[max_dd_idx],
                        "recovery_date": dates_list[i],
                        "dd_pct": max_dd_in_segment,
                        "duration_days": (
                            dates_list[max_dd_idx] - dates_list[peak_idx]
                        ).days,
                        "recovery_days": (
                            dates_list[i] - dates_list[max_dd_idx]
                        ).days,
                    })
                peak = navs[i]
                peak_idx = i
                max_dd_in_segment = 0.0
            else:
                dd = (peak - navs[i]) / peak
                if dd > max_dd_in_segment:
                    max_dd_in_segment = dd
                    max_dd_idx = i

        # 最后一段（可能未恢复）
        if max_dd_in_segment > self.min_dd_pct:
            periods.append({
                "peak_date": dates_list[peak_idx],
                "trough_date": dates_list[max_dd_idx],
                "recovery_date": None,
                "dd_pct": max_dd_in_segment,
                "duration_days": (
                    dates_list[max_dd_idx] - dates_list[peak_idx]
                ).days,
                "recovery_days": None,
            })

        periods.sort(key=lambda x: x["dd_pct"], reverse=True)
        return periods

    def _attribute_positions(
        self,
        snapshots: list[DailySnapshot],
        period_info: dict,
        result: BacktestResult,
    ) -> dict[str, float]:
        """计算回撤区间内各持仓对回撤的贡献。

        方法：比较 peak_date 和 trough_date 的持仓价值变化，
        按标的拆分各自的市值变动占总回撤的比例。
        """
        peak_date = period_info["peak_date"]
        trough_date = period_info["trough_date"]

        # 找到对应快照
        snap_map = {s.trade_date: s for s in snapshots}
        peak_snap = snap_map.get(peak_date)
        trough_snap = snap_map.get(trough_date)

        if not peak_snap or not trough_snap:
            return {}

        total_dd = peak_snap.nav - trough_snap.nav
        if total_dd <= 0:
            return {}

        contributions: dict[str, float] = {}

        # 收集回撤期间所有曾持仓的标的
        all_symbols: set[str] = set()
        for s in snapshots:
            if peak_date <= s.trade_date <= trough_date:
                all_symbols.update(s.position_values.keys())

        # 对每个标的，计算在 peak→trough 期间的市值变动
        for symbol in all_symbols:
            peak_val = peak_snap.position_values.get(symbol, 0.0)
            trough_val = trough_snap.position_values.get(symbol, 0.0)

            # 正值表示该标的贡献了回撤（市值下降）
            symbol_loss = peak_val - trough_val

            # 需要考虑期间买卖：如果期间卖出了，peak_val > 0 但 trough_val 可能=0
            # 但实际亏损可能不是全部。这里做简化处理，用市值差做近似归因。
            if abs(symbol_loss) > 0.01:
                contributions[symbol] = symbol_loss / total_dd

        # 按贡献度排序
        contributions = dict(
            sorted(contributions.items(), key=lambda kv: kv[1], reverse=True)
        )

        return contributions


# ============================================================================
# 一站式分析入口
# ============================================================================


def analyze_losses(
    result: BacktestResult,
    top_n_losers: int = 20,
    min_dd_pct: float = 0.03,
    max_dd_periods: int = 10,
) -> dict:
    """一站式亏损分析入口。

    Args:
        result: 回测结果
        top_n_losers: Top N 亏损交易数
        min_dd_pct: 回撤分析最小阈值
        max_dd_periods: 最大回撤区间数

    Returns:
        dict with keys:
            - round_trips: 全部 round-trip 列表
            - loss_summary: LossSummary 实例
            - drawdown_periods: DrawdownPeriod 列表
    """
    tracker = RoundTripTracker()
    round_trips = tracker.build(result.trades)

    analyzer = LossAnalyzer(top_n=top_n_losers)
    loss_summary = analyzer.analyze(round_trips)

    attributor = DrawdownAttributor(
        min_dd_pct=min_dd_pct, max_periods=max_dd_periods
    )
    dd_periods = attributor.analyze(result)

    return {
        "round_trips": round_trips,
        "loss_summary": loss_summary,
        "drawdown_periods": dd_periods,
    }


def format_loss_report(analysis: dict, name_map: dict[str, str] | None = None) -> str:
    """将亏损分析结果格式化为可读文本报告。

    Args:
        analysis: analyze_losses() 的返回值
        name_map: 可选的 symbol→名称 映射

    Returns:
        格式化的文本报告
    """
    summary: LossSummary = analysis["loss_summary"]
    dd_periods: list[DrawdownPeriod] = analysis["drawdown_periods"]

    def _name(symbol: str) -> str:
        if name_map and symbol in name_map:
            return f"{symbol}({name_map[symbol]})"
        return symbol

    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("亏损追踪分析报告")
    lines.append("=" * 70)

    # 1. 基本统计
    lines.append("\n一、交易盈亏概览")
    lines.append(f"  总交易对数: {summary.total_round_trips}")
    lines.append(f"  盈利交易: {summary.win_count}  亏损交易: {summary.loss_count}")
    win_rate = (
        summary.win_count / summary.total_round_trips * 100
        if summary.total_round_trips > 0 else 0
    )
    lines.append(f"  胜率: {win_rate:.1f}%")
    lines.append(f"  总盈利: {summary.total_profit:+,.0f}  总亏损: {summary.total_loss:+,.0f}")
    lines.append(f"  平均单笔盈利: {summary.avg_profit:+,.0f}  平均单笔亏损: {summary.avg_loss:+,.0f}")
    lines.append(f"  最大单笔亏损: {summary.worst_loss:+,.0f} ({summary.worst_loss_pct:+.2%})")
    lines.append(
        f"  盈利交易平均持仓: {summary.avg_win_holding_days:.0f}天  "
        f"亏损交易平均持仓: {summary.avg_loss_holding_days:.0f}天"
    )

    # 2. 退出原因归因
    lines.append("\n二、亏损退出原因归因")
    if summary.loss_by_category:
        for cat, (cnt, amt) in sorted(
            summary.loss_by_category.items(), key=lambda x: x[1][1]
        ):
            lines.append(f"  {cat}: {cnt}笔, 亏损{amt:+,.0f}")
    else:
        lines.append("  （无亏损交易）")

    # 3. 月度亏损分布
    lines.append("\n三、月度亏损分布")
    if summary.loss_by_month:
        for month, (cnt, amt) in sorted(summary.loss_by_month.items()):
            lines.append(f"  {month}: {cnt}笔, 亏损{amt:+,.0f}")
    else:
        lines.append("  （无亏损交易）")

    # 4. Top 亏损交易
    lines.append(f"\n四、最大亏损交易 Top {len(summary.top_losers)}")
    for i, rt in enumerate(summary.top_losers, 1):
        cat = classify_exit_reason(rt.exit_reason)
        lines.append(
            f"  [{i:2d}] {_name(rt.symbol)}  "
            f"买入{rt.entry_date} @{rt.entry_price:.2f} → "
            f"卖出{rt.exit_date} @{rt.exit_price:.2f}  "
            f"持{rt.holding_days}天  "
            f"盈亏{rt.net_pnl:+,.0f} ({rt.pnl_pct:+.2%})  "
            f"[{cat}]"
        )

    # 5. 重大回撤归因
    lines.append(f"\n五、重大回撤区间归因 (共{len(dd_periods)}个)")
    for i, period in enumerate(dd_periods, 1):
        recov = period.recovery_date or "未恢复"
        recov_days = f"{period.recovery_days}天" if period.recovery_days else "N/A"
        lines.append(
            f"\n  [{i}] 回撤 {period.drawdown_pct:.2%}  "
            f"峰值={period.peak_date} → 谷底={period.trough_date} "
            f"({period.duration_days}天) 恢复={recov} ({recov_days})"
        )
        # 持仓贡献
        if period.position_contributions:
            lines.append("      持仓贡献:")
            for sym, contrib in list(period.position_contributions.items())[:10]:
                direction = "↓" if contrib > 0 else "↑"
                lines.append(
                    f"        {_name(sym)} {direction} {abs(contrib):.1%}"
                )
        # 期间交易
        loss_trades = [t for t in period.trades_during if t.net_pnl < 0]
        if loss_trades:
            lines.append(f"      期间亏损交易: {len(loss_trades)}笔")
            for rt in sorted(loss_trades, key=lambda x: x.net_pnl)[:5]:
                cat = classify_exit_reason(rt.exit_reason)
                lines.append(
                    f"        {_name(rt.symbol)} "
                    f"亏损{rt.net_pnl:+,.0f} ({rt.pnl_pct:+.2%}) [{cat}]"
                )

    lines.append("\n" + "=" * 70)
    return "\n".join(lines)
