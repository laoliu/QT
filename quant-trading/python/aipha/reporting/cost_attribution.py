"""交易成本归因分析 (TCA)

提供按维度（side / symbol / period）的成本拆分与效率指标，
补充 backtest.report.CostAttribution 的汇总视角。

对齐 copilot-instructions.md：
- 成本与滑点必须是可插拔模型
- 回测必须包含费用归因
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR


@dataclass(frozen=True)
class CostComponent:
    """单维度成本拆分"""

    commission: float
    stamp_tax: float
    transfer_fee: float
    trade_count: int
    total_notional: float

    @property
    def total(self) -> float:
        """佣金 + 印花税 + 过户费总和。"""
        return self.commission + self.stamp_tax + self.transfer_fee

    @property
    def cost_rate_bps(self) -> float:
        """成本率(bps)"""
        if self.total_notional <= 0:
            return 0.0
        return self.total / self.total_notional * 10_000

    def to_dict(self) -> dict:
        """将单维度成本拆分序列化为字典，值保留两位小数。"""
        return {
            "commission": round(self.commission, 2),
            "stamp_tax": round(self.stamp_tax, 2),
            "transfer_fee": round(self.transfer_fee, 2),
            "total": round(self.total, 2),
            "trade_count": self.trade_count,
            "total_notional": round(self.total_notional, 2),
            "cost_rate_bps": round(self.cost_rate_bps, 2),
        }


@dataclass(frozen=True)
class TradeCostRecord:
    """单笔交易成本信息（用于聚合的输入）"""

    trade_date: date
    symbol: str
    side: str       # "BUY" or "SELL"
    qty: int
    price: float
    notional: float
    commission: float
    stamp_tax: float
    transfer_fee: float

    @property
    def total_fee(self) -> float:
        """单笔交易的佣金 + 印花税 + 过户费总和。"""
        return self.commission + self.stamp_tax + self.transfer_fee


@dataclass(frozen=True)
class CostAttributionReport:
    """完整成本归因报告"""

    total: CostComponent
    by_side: dict[str, CostComponent]
    by_symbol: dict[str, CostComponent]
    by_month: dict[str, CostComponent]
    cost_drag_annualized: float   # 年化费用拖累(%)
    avg_cost_per_trade: float

    def to_dict(self) -> dict:
        """将完整成本归因报告序列化为字典（含分维度 top10）。"""
        return {
            "total": self.total.to_dict(),
            "by_side": {k: v.to_dict() for k, v in self.by_side.items()},
            "by_symbol_top10": {
                k: v.to_dict()
                for k, v in sorted(
                    self.by_symbol.items(),
                    key=lambda x: x[1].total,
                    reverse=True,
                )[:10]
            },
            "by_month": {k: v.to_dict() for k, v in sorted(self.by_month.items())},
            "cost_drag_annualized_pct": round(self.cost_drag_annualized, 4),
            "avg_cost_per_trade": round(self.avg_cost_per_trade, 4),
        }


def build_cost_attribution(
    trades: list[TradeCostRecord],
    initial_nav: float = 1_000_000.0,
    final_nav: float | None = None,
    trading_days: int = TRADING_DAYS_PER_YEAR,
    total_calendar_days: int | None = None,
) -> CostAttributionReport:
    """从交易记录列表构建成本归因报告。

    Args:
        trades: 交易成本记录列表
        initial_nav: 初始净值
        final_nav: 最终净值（若提供，用于计算费用拖累）
        trading_days: 年化因子
        total_calendar_days: 回测总自然日数

    Returns:
        CostAttributionReport
    """
    if not trades:
        empty = CostComponent(0, 0, 0, 0, 0)
        return CostAttributionReport(
            total=empty,
            by_side={},
            by_symbol={},
            by_month={},
            cost_drag_annualized=0.0,
            avg_cost_per_trade=0.0,
        )

    # ---- 聚合 ----
    side_agg: dict[str, list[TradeCostRecord]] = defaultdict(list)
    symbol_agg: dict[str, list[TradeCostRecord]] = defaultdict(list)
    month_agg: dict[str, list[TradeCostRecord]] = defaultdict(list)

    for t in trades:
        side_agg[t.side].append(t)
        symbol_agg[t.symbol].append(t)
        month_key = t.trade_date.strftime("%Y-%m")
        month_agg[month_key].append(t)

    total = _aggregate(trades)
    by_side = {k: _aggregate(v) for k, v in side_agg.items()}
    by_symbol = {k: _aggregate(v) for k, v in symbol_agg.items()}
    by_month = {k: _aggregate(v) for k, v in month_agg.items()}

    # 年化费用拖累
    cost_drag = 0.0
    if final_nav and final_nav > 0 and total_calendar_days and total_calendar_days > 0:
        years = total_calendar_days / 365.0
        cost_drag = (total.total / initial_nav) / years * 100  # 百分比

    avg_cost = total.total / total.trade_count if total.trade_count > 0 else 0.0

    return CostAttributionReport(
        total=total,
        by_side=by_side,
        by_symbol=by_symbol,
        by_month=by_month,
        cost_drag_annualized=cost_drag,
        avg_cost_per_trade=avg_cost,
    )


def _aggregate(records: list[TradeCostRecord]) -> CostComponent:
    """聚合交易记录为成本组件"""
    return CostComponent(
        commission=sum(r.commission for r in records),
        stamp_tax=sum(r.stamp_tax for r in records),
        transfer_fee=sum(r.transfer_fee for r in records),
        trade_count=len(records),
        total_notional=sum(r.notional for r in records),
    )


def format_cost_report(report: CostAttributionReport) -> str:
    """格式化成本归因文本报告"""
    lines = ["=" * 60, "交易成本归因报告 (TCA)", "=" * 60, ""]

    # 总计
    t = report.total
    lines.append(f"总交易笔数: {t.trade_count}")
    lines.append(f"总成交额: {t.total_notional:,.2f}")
    lines.append(f"总费用: {t.total:,.2f}")
    lines.append(f"  佣金: {t.commission:,.2f}")
    lines.append(f"  印花税: {t.stamp_tax:,.2f}")
    lines.append(f"  过户费: {t.transfer_fee:,.2f}")
    lines.append(f"综合费率: {t.cost_rate_bps:.2f} bps")
    lines.append(f"平均每笔费用: {report.avg_cost_per_trade:.2f}")
    if report.cost_drag_annualized > 0:
        lines.append(f"年化费用拖累: {report.cost_drag_annualized:.4f}%")
    lines.append("")

    # 按方向
    lines.append("-" * 40)
    lines.append("按交易方向:")
    for side, c in sorted(report.by_side.items()):
        lines.append(
            f"  {side}: {c.trade_count}笔, "
            f"费用 {c.total:,.2f}, "
            f"费率 {c.cost_rate_bps:.2f}bps"
        )
    lines.append("")

    # 前10大费用股票
    lines.append("-" * 40)
    lines.append("费用前10标的:")
    top10 = sorted(
        report.by_symbol.items(), key=lambda x: x[1].total, reverse=True
    )[:10]
    for sym, c in top10:
        lines.append(
            f"  {sym}: {c.trade_count}笔, "
            f"费用 {c.total:,.2f}, "
            f"费率 {c.cost_rate_bps:.2f}bps"
        )

    return "\n".join(lines)
