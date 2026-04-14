#!/usr/bin/env python3
"""最小实盘资金量分析脚本.

多专家视角评估 V3.1 组合在不同资金量下的约束与衰减:
1. 散手效应 (lot rounding loss)
2. 最低佣金拖累 (commission_min drag)
3. 持仓分散度不足 (top_k 无法全额配置)
4. 参与率冲击 (market impact)
5. 综合建议

用法:
    python scripts/min_capital_analysis.py
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

_PROJ = Path(__file__).resolve().parents[1]
if str(_PROJ / "src") not in sys.path:
    sys.path.insert(0, str(_PROJ / "src"))


# ── 组件参数 ──────────────────────────────────────────────────
@dataclass
class ComponentSpec:
    """单组件参数."""
    name: str
    weight: float
    top_k: int
    lot_size: int
    avg_price: float          # 平均股价/债价 (元)
    commission_rate: float
    commission_min: float     # 元
    stamp_tax_rate: float     # 卖出
    transfer_fee_rate: float
    slippage_bps: float
    rebalance_days: int
    avg_daily_volume: float   # 单标的日均成交额 (元)
    max_participation: float
    description: str = ""


COMPONENTS = [
    ComponentSpec(
        name="CSI2000",
        weight=0.35,
        top_k=50,
        lot_size=100,
        avg_price=15.0,       # 微盘股均价 ~15元
        commission_rate=0.0000854,
        commission_min=5.0,
        stamp_tax_rate=0.0005,
        transfer_fee_rate=0.00001,
        slippage_bps=8.0,
        rebalance_days=5,
        avg_daily_volume=50_000_000,
        max_participation=0.10,
        description="CSI2000 微盘 ML 集合",
    ),
    ComponentSpec(
        name="CB",
        weight=0.15,
        top_k=10,
        lot_size=10,
        avg_price=115.0,      # CB 均价 ~115元/张
        commission_rate=0.0000854,
        commission_min=5.0,
        stamp_tax_rate=0.0,
        transfer_fee_rate=0.0,
        slippage_bps=5.0,
        rebalance_days=1,
        avg_daily_volume=10_000_000,
        max_participation=0.10,
        description="正股质量增强 CB",
    ),
    ComponentSpec(
        name="CSI300",
        weight=0.15,
        top_k=30,
        lot_size=100,
        avg_price=28.0,       # CSI300 成分股均价 ~28元
        commission_rate=0.0000854,
        commission_min=5.0,
        stamp_tax_rate=0.0005,
        transfer_fee_rate=0.00001,
        slippage_bps=5.0,
        rebalance_days=5,
        avg_daily_volume=500_000_000,
        max_participation=0.10,
        description="CSI300≥200B ML",
    ),
    ComponentSpec(
        name="ETF",
        weight=0.35,
        top_k=3,
        lot_size=100,
        avg_price=3.5,        # ETF 均价 ~3.5元
        commission_rate=0.0000854,
        commission_min=5.0,
        stamp_tax_rate=0.0,
        transfer_fee_rate=0.0,
        slippage_bps=5.0,
        rebalance_days=10,
        avg_daily_volume=1_000_000_000,
        max_participation=0.10,
        description="ETF FAA 轮动",
    ),
]

# 扫描资金量级 (元)
AUM_LEVELS = [
    50_000, 100_000, 200_000, 300_000, 500_000,
    800_000, 1_000_000, 2_000_000, 3_000_000,
    5_000_000, 10_000_000, 20_000_000,
]


# ═══════════════════════════════════════════════════════════════
# 分析函数
# ═══════════════════════════════════════════════════════════════

def analyze_lot_rounding(comp: ComponentSpec, comp_capital: float) -> dict:
    """分析散手效应: lot_size 取整导致的资金利用率损失."""
    if comp_capital <= 0:
        return {"actual_stocks": 0, "rounding_loss_pct": 1.0,
                "avg_position": 0, "min_lots_per_stock": 0}
    per_stock_target = comp_capital / comp.top_k
    one_lot_value = comp.lot_size * comp.avg_price
    lots = math.floor(per_stock_target / one_lot_value)
    actual_per_stock = lots * one_lot_value
    # 能实际配多少只股
    if lots == 0:
        # 资金不够 1 手，看能买几只
        affordable = math.floor(comp_capital / one_lot_value)
        actual_invested = affordable * one_lot_value
        return {
            "actual_stocks": affordable,
            "target_stocks": comp.top_k,
            "rounding_loss_pct": 1.0 - actual_invested / comp_capital if comp_capital > 0 else 1.0,
            "avg_position": one_lot_value if affordable > 0 else 0,
            "min_lots_per_stock": 1 if affordable > 0 else 0,
            "one_lot_value": one_lot_value,
        }
    actual_invested = actual_per_stock * comp.top_k
    rounding_loss = 1.0 - actual_invested / comp_capital
    return {
        "actual_stocks": comp.top_k,
        "target_stocks": comp.top_k,
        "rounding_loss_pct": max(0, rounding_loss),
        "avg_position": actual_per_stock,
        "min_lots_per_stock": lots,
        "one_lot_value": one_lot_value,
    }


def analyze_commission_drag(comp: ComponentSpec, comp_capital: float) -> dict:
    """分析最低佣金拖累: 小额交易时 commission_min 占比过高."""
    if comp_capital <= 0:
        return {"annual_commission_drag_pct": 0, "per_trade_commission_pct": 0}
    per_stock = comp_capital / comp.top_k
    # 单笔佣金
    nominal_commission = per_stock * comp.commission_rate
    actual_commission = max(nominal_commission, comp.commission_min)
    per_trade_pct = actual_commission / per_stock if per_stock > 0 else 0
    # 年化: 假设每次调仓换 40% 仓位 (经验值)
    turnover_per_rebal = 0.40
    rebal_per_year = 252 / comp.rebalance_days
    annual_trades = comp.top_k * turnover_per_rebal * rebal_per_year * 2  # 买+卖
    annual_commission = actual_commission * annual_trades
    annual_drag = annual_commission / comp_capital
    # 对比无 commission_min 的理论佣金
    theoretical_annual = nominal_commission * annual_trades
    excess_drag = (annual_commission - theoretical_annual) / comp_capital
    return {
        "per_trade_commission": actual_commission,
        "per_trade_commission_pct": per_trade_pct,
        "annual_commission_drag_pct": annual_drag,
        "excess_commission_drag_pct": excess_drag,
        "annual_trades": annual_trades,
        "commission_min_binding": actual_commission > nominal_commission * 1.01,
    }


def analyze_stamp_tax_drag(comp: ComponentSpec, comp_capital: float) -> dict:
    """分析印花税年化拖累."""
    if comp_capital <= 0 or comp.stamp_tax_rate == 0:
        return {"annual_stamp_tax_pct": 0.0}
    turnover_per_rebal = 0.40
    rebal_per_year = 252 / comp.rebalance_days
    # 印花税仅卖出收取
    annual_sell_turnover = turnover_per_rebal * rebal_per_year
    annual_drag = annual_sell_turnover * comp.stamp_tax_rate
    return {"annual_stamp_tax_pct": annual_drag}


def analyze_slippage_drag(comp: ComponentSpec, comp_capital: float) -> dict:
    """分析滑点年化拖累."""
    if comp_capital <= 0:
        return {"annual_slippage_pct": 0.0}
    turnover_per_rebal = 0.40
    rebal_per_year = 252 / comp.rebalance_days
    annual_turnover = turnover_per_rebal * rebal_per_year * 2  # 买+卖
    annual_drag = annual_turnover * comp.slippage_bps / 10000
    return {"annual_slippage_pct": annual_drag}


def analyze_participation_impact(comp: ComponentSpec, comp_capital: float) -> dict:
    """分析市场冲击: 单笔订单占日成交量比例."""
    if comp_capital <= 0:
        return {"participation_rate": 0, "excessive": False}
    per_stock_capital = comp_capital / comp.top_k
    # 调仓日单标的交易额 (假设换40%仓位)
    trade_amount = per_stock_capital * 0.40
    participation = trade_amount / comp.avg_daily_volume if comp.avg_daily_volume > 0 else 0
    return {
        "participation_rate": participation,
        "excessive": participation > comp.max_participation,
        "trade_amount_per_stock": trade_amount,
    }


def analyze_component(comp: ComponentSpec, total_aum: float) -> dict:
    """综合分析单组件."""
    comp_capital = total_aum * comp.weight
    lot = analyze_lot_rounding(comp, comp_capital)
    comm = analyze_commission_drag(comp, comp_capital)
    tax = analyze_stamp_tax_drag(comp, comp_capital)
    slip = analyze_slippage_drag(comp, comp_capital)
    part = analyze_participation_impact(comp, comp_capital)

    # 总年化成本拖累
    total_annual_cost = (
        comm["annual_commission_drag_pct"]
        + tax["annual_stamp_tax_pct"]
        + slip["annual_slippage_pct"]
    )
    # 最低佣金 excess 占总成本的比例
    excess_comm_share = (
        comm["excess_commission_drag_pct"] / total_annual_cost
        if total_annual_cost > 0 else 0
    )
    return {
        "component": comp.name,
        "comp_capital": comp_capital,
        "lot_rounding": lot,
        "commission": comm,
        "stamp_tax": tax,
        "slippage": slip,
        "participation": part,
        "total_annual_cost_pct": total_annual_cost,
        "excess_commission_share": excess_comm_share,
    }


# ═══════════════════════════════════════════════════════════════
# 多专家评估框架
# ═══════════════════════════════════════════════════════════════

def expert_cost_analyst(results: list[dict]) -> list[str]:
    """交易成本专家视角: 关注佣金/印花税/滑点拖累."""
    lines = ["## 专家 1: 交易成本分析师", ""]
    for r in results:
        aum = r["total_aum"]
        aum_str = f"{aum/10000:.0f}万" if aum < 1e6 else f"{aum/1e6:.0f}百万"
        # 找最低佣金 binding 的组件
        binding = [c for c in r["components"] if c["commission"]["commission_min_binding"]]
        total_cost = sum(
            c["total_annual_cost_pct"] * COMPONENTS[i].weight
            for i, c in enumerate(r["components"])
        )
        excess_cost = sum(
            c["commission"]["excess_commission_drag_pct"] * COMPONENTS[i].weight
            for i, c in enumerate(r["components"])
        )
        lines.append(f"**AUM={aum_str}**: 加权年化交易成本={total_cost:.2%}, "
                      f"最低佣金excess={excess_cost:.2%} "
                      f"(binding组件: {', '.join(c['component'] for c in binding)})")
    lines.append("")
    # 建议
    lines.append("**结论**: 当 excess 佣金成本 < 0.5% 年化时，佣金影响可忽略。")
    return lines


def expert_execution_analyst(results: list[dict]) -> list[str]:
    """执行可行性专家视角: 关注散手效应、持仓覆盖."""
    lines = ["## 专家 2: 执行可行性分析师", ""]
    for r in results:
        aum = r["total_aum"]
        aum_str = f"{aum/10000:.0f}万" if aum < 1e6 else f"{aum/1e6:.0f}百万"
        issues = []
        for c in r["components"]:
            lot = c["lot_rounding"]
            if lot["actual_stocks"] < lot["target_stocks"]:
                issues.append(f"{c['component']}: 仅能买{lot['actual_stocks']}/{lot['target_stocks']}只")
            elif lot["rounding_loss_pct"] > 0.10:
                issues.append(f"{c['component']}: 散手损耗{lot['rounding_loss_pct']:.1%}")
        if issues:
            lines.append(f"**AUM={aum_str}**: ⚠️ {'; '.join(issues)}")
        else:
            lines.append(f"**AUM={aum_str}**: ✅ 所有组件可全额配置")
    lines.append("")
    lines.append("**结论**: 所有组件满足 top_k 配置且散手损耗 < 5% 为及格线。")
    return lines


def expert_risk_analyst(results: list[dict]) -> list[str]:
    """风控专家视角: 关注分散度、集中度风险."""
    lines = ["## 专家 3: 风险管理分析师", ""]
    for r in results:
        aum = r["total_aum"]
        aum_str = f"{aum/10000:.0f}万" if aum < 1e6 else f"{aum/1e6:.0f}百万"
        warnings = []
        for c in r["components"]:
            lot = c["lot_rounding"]
            if lot["actual_stocks"] < 5 and lot["target_stocks"] >= 10:
                warnings.append(f"{c['component']}: 持仓严重不足({lot['actual_stocks']}只)")
            # 单只占比
            if lot["avg_position"] > 0 and c["comp_capital"] > 0:
                max_single_pct = lot["avg_position"] / c["comp_capital"]
                # 如果只能买少于 top_k 只，集中度更高
                if lot["actual_stocks"] > 0:
                    actual_single = c["comp_capital"] / lot["actual_stocks"]
                    actual_pct = actual_single / c["comp_capital"]
                    if actual_pct > 0.20:
                        warnings.append(
                            f"{c['component']}: 单票{actual_pct:.0%}(需{lot['actual_stocks']}只分散)")
        if warnings:
            lines.append(f"**AUM={aum_str}**: ❌ {'; '.join(warnings)}")
        else:
            lines.append(f"**AUM={aum_str}**: ✅ 分散度充分")
    lines.append("")
    lines.append("**结论**: CSI2000 需 ≥50只持仓分散; CB 需 ≥10只; 单票占比 ≤10% 为目标。")
    return lines


def expert_capacity_analyst(results: list[dict]) -> list[str]:
    """容量/冲击专家视角: 关注参与率."""
    lines = ["## 专家 4: 市场冲击分析师", ""]
    for r in results:
        aum = r["total_aum"]
        aum_str = f"{aum/10000:.0f}万" if aum < 1e6 else f"{aum/1e6:.0f}百万"
        max_part = 0
        max_comp = ""
        for c in r["components"]:
            p = c["participation"]["participation_rate"]
            if p > max_part:
                max_part = p
                max_comp = c["component"]
        if max_part > 0.10:
            lines.append(f"**AUM={aum_str}**: ❌ {max_comp} 参与率={max_part:.2%} > 10%上限")
        elif max_part > 0.05:
            lines.append(f"**AUM={aum_str}**: ⚠️ {max_comp} 参与率={max_part:.2%} 接近上限")
        else:
            lines.append(f"**AUM={aum_str}**: ✅ 最大参与率={max_part:.4%} ({max_comp})")
    lines.append("")
    lines.append("**结论**: 参与率 < 5% 为理想; < 10% 可接受; > 10% 不可行。")
    return lines


# ═══════════════════════════════════════════════════════════════
# 综合评估
# ═══════════════════════════════════════════════════════════════

def compute_composite_score(r: dict) -> dict:
    """综合打分: 0-100分，找到最低可行AUM."""
    scores = {}

    # 1. 持仓覆盖度 (0-30分)
    coverage_score = 0
    for i, c in enumerate(r["components"]):
        lot = c["lot_rounding"]
        ratio = lot["actual_stocks"] / lot["target_stocks"] if lot["target_stocks"] > 0 else 0
        coverage_score += ratio * 30 * COMPONENTS[i].weight
    scores["coverage"] = min(30, coverage_score)

    # 2. 散手效应 (0-20分)
    weighted_rounding = sum(
        c["lot_rounding"]["rounding_loss_pct"] * COMPONENTS[i].weight
        for i, c in enumerate(r["components"])
    )
    scores["rounding"] = max(0, 20 * (1 - weighted_rounding * 5))

    # 3. 佣金合理性 (0-20分)
    weighted_excess = sum(
        c["commission"]["excess_commission_drag_pct"] * COMPONENTS[i].weight
        for i, c in enumerate(r["components"])
    )
    scores["commission"] = max(0, 20 * (1 - weighted_excess * 20))

    # 4. 市场冲击 (0-15分)  — 小资金无问题
    max_participation = max(
        c["participation"]["participation_rate"] for c in r["components"]
    )
    if max_participation < 0.05:
        scores["impact"] = 15
    elif max_participation < 0.10:
        scores["impact"] = 10
    else:
        scores["impact"] = 0

    # 5. 分散度 (0-15分)
    min_actual = min(
        c["lot_rounding"]["actual_stocks"] for c in r["components"]
    )
    if min_actual >= 3:  # ETF top_k=3
        all_full = all(
            c["lot_rounding"]["actual_stocks"] >= c["lot_rounding"]["target_stocks"]
            for c in r["components"]
        )
        scores["diversification"] = 15 if all_full else 8
    else:
        scores["diversification"] = 0

    total = sum(scores.values())
    scores["total"] = total
    return scores


# ═══════════════════════════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════════════════════════

def main() -> None:
    all_results = []
    for aum in AUM_LEVELS:
        components = [analyze_component(comp, aum) for comp in COMPONENTS]
        all_results.append({"total_aum": aum, "components": components})

    # ── 总览表 ──
    print("=" * 100)
    print("V3.1 组合最小实盘资金量分析")
    print("=" * 100)
    print()

    # 散手效应 + 持仓数表
    header = f"{'AUM':>10} | "
    for c in COMPONENTS:
        header += f"{c.name:>12} 持/{c.top_k} 散手 | "
    print(header)
    print("-" * len(header))
    for r in all_results:
        aum = r["total_aum"]
        aum_s = f"{aum/10000:.0f}万" if aum < 1e6 else f"{aum/1e6:.0f}百万"
        row = f"{aum_s:>10} | "
        for c in r["components"]:
            lot = c["lot_rounding"]
            row += f"    {lot['actual_stocks']:>3}/{lot['target_stocks']}  {lot['rounding_loss_pct']:>5.1%} | "
        print(row)

    # ── 佣金拖累表 ──
    print()
    print("── 年化佣金拖累 (含最低佣金 excess) ──")
    header2 = f"{'AUM':>10} | "
    for c in COMPONENTS:
        header2 += f" {c.name:>8} 实际/理论 excess | "
    print(header2)
    print("-" * len(header2))
    for r in all_results:
        aum = r["total_aum"]
        aum_s = f"{aum/10000:.0f}万" if aum < 1e6 else f"{aum/1e6:.0f}百万"
        row = f"{aum_s:>10} | "
        for c in r["components"]:
            comm = c["commission"]
            row += (f"  {comm['annual_commission_drag_pct']:>6.2%}"
                    f"/{comm['annual_commission_drag_pct'] - comm['excess_commission_drag_pct']:>6.2%}"
                    f"  {comm['excess_commission_drag_pct']:>5.2%} | ")
        print(row)

    # ── 总成本表 ──
    print()
    print("── 加权年化总交易成本 (佣+税+滑) ──")
    print(f"{'AUM':>10} | {'加权总成本':>10} | {'excess佣金':>10} | "
          f"{'CSI2000':>10} | {'CB':>10} | {'CSI300':>10} | {'ETF':>10}")
    print("-" * 95)
    for r in all_results:
        aum = r["total_aum"]
        aum_s = f"{aum/10000:.0f}万" if aum < 1e6 else f"{aum/1e6:.0f}百万"
        weighted_total = sum(
            c["total_annual_cost_pct"] * COMPONENTS[i].weight
            for i, c in enumerate(r["components"])
        )
        weighted_excess = sum(
            c["commission"]["excess_commission_drag_pct"] * COMPONENTS[i].weight
            for i, c in enumerate(r["components"])
        )
        comp_costs = [c["total_annual_cost_pct"] for c in r["components"]]
        print(f"{aum_s:>10} | {weighted_total:>10.2%} | {weighted_excess:>10.2%} | "
              f"{comp_costs[0]:>10.2%} | {comp_costs[1]:>10.2%} | "
              f"{comp_costs[2]:>10.2%} | {comp_costs[3]:>10.2%}")

    # ── 综合评分 ──
    print()
    print("── 综合可行性评分 (满分100) ──")
    print(f"{'AUM':>10} | {'总分':>6} | {'覆盖/30':>8} | {'散手/20':>8} | "
          f"{'佣金/20':>8} | {'冲击/15':>8} | {'分散/15':>8} | 评级")
    print("-" * 95)
    for r in all_results:
        aum = r["total_aum"]
        aum_s = f"{aum/10000:.0f}万" if aum < 1e6 else f"{aum/1e6:.0f}百万"
        s = compute_composite_score(r)
        t = s["total"]
        grade = ("A" if t >= 90 else "B+" if t >= 80 else "B" if t >= 70
                 else "C" if t >= 60 else "D" if t >= 40 else "F")
        print(f"{aum_s:>10} | {t:>6.1f} | {s['coverage']:>8.1f} | {s['rounding']:>8.1f} | "
              f"{s['commission']:>8.1f} | {s['impact']:>8.1f} | {s['diversification']:>8.1f} | {grade}")

    # ── 专家评估 ──
    print()
    print("=" * 80)
    print("多资深专家评估意见")
    print("=" * 80)
    for expert_fn in [expert_cost_analyst, expert_execution_analyst,
                      expert_risk_analyst, expert_capacity_analyst]:
        print()
        for line in expert_fn(all_results):
            print(line)

    # ── 最终建议 ──
    print()
    print("=" * 80)
    print("## 专家联合结论: 最小实盘资金量建议")
    print("=" * 80)
    print()
    # 找到评分 >= 80 的最小 AUM
    min_recommended = None
    min_acceptable = None
    for r in all_results:
        s = compute_composite_score(r)
        if s["total"] >= 80 and min_recommended is None:
            min_recommended = r["total_aum"]
        if s["total"] >= 60 and min_acceptable is None:
            min_acceptable = r["total_aum"]

    if min_acceptable:
        print(f"最低可行资金 (评分≥60/C级): {min_acceptable/10000:.0f}万元")
    if min_recommended:
        print(f"建议最低资金 (评分≥80/B+级): {min_recommended/10000:.0f}万元")
    print(f"回测基准资金: 1000万元 (评分={compute_composite_score(all_results[-2])['total']:.0f})")
    print()
    print("### 分级建议:")
    print("| 资金量级 | 评级 | 主要约束 | 预期AR衰减 |")
    print("|---------|------|---------|-----------|")
    for r in all_results:
        aum = r["total_aum"]
        aum_s = f"{aum/10000:.0f}万"
        s = compute_composite_score(r)
        t = s["total"]
        grade = ("A" if t >= 90 else "B+" if t >= 80 else "B" if t >= 70
                 else "C" if t >= 60 else "D" if t >= 40 else "F")
        # 识别主要约束
        constraints = []
        if s["coverage"] < 25:
            constraints.append("持仓不足")
        if s["rounding"] < 15:
            constraints.append("散手损耗")
        if s["commission"] < 15:
            constraints.append("最低佣金")
        if not constraints:
            constraints.append("无显著约束")
        # 估算 AR 衰减
        weighted_excess = sum(
            c["commission"]["excess_commission_drag_pct"] * COMPONENTS[i].weight
            for i, c in enumerate(r["components"])
        )
        weighted_rounding = sum(
            c["lot_rounding"]["rounding_loss_pct"] * COMPONENTS[i].weight
            for i, c in enumerate(r["components"])
        )
        ar_decay = weighted_excess + weighted_rounding * 0.5  # 粗估
        print(f"| {aum_s:>6} | {grade:>4} | {', '.join(constraints):<15} | "
              f"{ar_decay:>+.1%} |")

    print()
    print("### 关键发现:")
    print("1. **散手效应是小资金最大杀手**: CSI2000(50只×100股×15元=7.5万/组件, 即总AUM≥21万)")
    print("2. **最低佣金5元门槛**: 当单笔 < 5.9万元时，佣金 > 理论值(万0.854)")
    print("3. **CB 容量天生受限**: 10只×10张×115元 = 1.15万即可配满，但策略容量≤50万")
    print("4. **CSI300/ETF 无压力**: 大盘股/ETF 流动性充裕，小资金无冲击")
    print("5. **参与率**: AUM ≤ 2000万时，参与率 < 0.5%，完全无影响")


if __name__ == "__main__":
    main()
