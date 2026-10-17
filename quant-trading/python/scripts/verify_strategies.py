#!/usr/bin/env python3
"""策略收益准确性 & 稳健性验证脚本

验证清单：
1. 修正后 small_size (issue_size) 重跑默认参数和最优参数
2. new_bond 复现最优参数
3. 分年度收益拆解（2022/2023/2024）
4. 滑点敏感性测试（0.5/1/2/5/10 bps）
5. 换手率统计
6. 持仓容量分析
"""

from __future__ import annotations

import sys
import json
import time
import csv
from datetime import date, datetime
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.run_cb_strategy import create_strategy, build_engine, compute_metrics, load_data
from aipha.backtest.engine import BacktestConfig, BacktestEngine, BacktestResult
from aipha.data.cb_data_adapter import CBDataAdapter
from aipha.execution.fees import CommissionModel, FeeModelCNEquity, StampTaxModel, TransferFeeModel
from aipha.execution.fill_models import get_fill_model
from aipha.execution.slippage import FixedBpsSlippageModel
from aipha.market.rules_cn_equity import MarketRulesCNEquity
from aipha.utils.paths import project_path


def build_engine_custom(strategy, fill_model_name: str = "next_bar_open",
                        slippage_bps: float = 5.0,
                        initial_capital: float = 500_000.0):
    """构建回测引擎（自定义滑点）"""
    bt_config = BacktestConfig(
        initial_capital=initial_capital,
        lot_size=10,
        commission_rate=0.0001,
        commission_min=5.0,  # 13.1 CB权威值
        stamp_tax_rate=0.0,
        transfer_fee_rate=0.0,
        slippage_bps=slippage_bps,
        t_plus_n=0,
    )
    fee_model = FeeModelCNEquity(
        commission=CommissionModel(rate=0.0001, min_fee=5.0),  # §13.1 CB: commission_min=5
        stamp_tax=StampTaxModel(sell_rate=0.0),
        transfer_fee=TransferFeeModel(rate=0.0),
    )
    fill_model = get_fill_model(fill_model_name)
    slippage = FixedBpsSlippageModel(bps=slippage_bps)
    rules = MarketRulesCNEquity(lot_size=bt_config.lot_size)
    engine = BacktestEngine(
        config=bt_config, strategy=strategy,
        fill_model=fill_model, slippage=slippage,
        fee_model=fee_model, rules=rules,
    )
    return bt_config, engine


def compute_detailed_metrics(result: BacktestResult) -> dict:
    """扩展指标：含换手率"""
    m = compute_metrics(result)
    snapshots = result.daily_snapshots
    n_days = len(snapshots)

    # 日均换手率 (sum of daily abs position value changes / avg NAV)
    total_buy_value = 0.0
    total_sell_value = 0.0
    for t in result.trades:
        if t.side.name == "BUY":
            total_buy_value += t.qty * t.price
        else:
            total_sell_value += t.qty * t.price
    avg_nav = (result.initial_capital + result.final_nav) / 2
    total_turnover = (total_buy_value + total_sell_value) / 2
    annual_turnover = total_turnover / avg_nav / (n_days / 252) if n_days > 0 and avg_nav > 0 else 0
    m["annual_turnover_x"] = round(annual_turnover, 2)
    m["total_buy_value"] = round(total_buy_value, 0)
    m["total_sell_value"] = round(total_sell_value, 0)
    m["avg_trade_size"] = round(total_buy_value / (result.total_trades / 2) if result.total_trades > 0 else 0, 0)
    return m


def run_with_result(strategy_name, params, fill_model, bars):
    """运行并返回 result 对象"""
    strategy = create_strategy(strategy_name, params)
    _, engine = build_engine(strategy, fill_model)
    result = engine.run(bars)
    return result


def run_yearly_breakdown(strategy_name, params, fill_model, all_bars):
    """分年度收益拆解"""
    years = {}
    for td, bars_day in all_bars.items():
        y = td.year
        if y not in years:
            years[y] = {}
        years[y][td] = bars_day

    print(f"\n分年度收益 ({strategy_name}, {fill_model}):")
    print(f"{'年份':>6} {'年化':>8} {'回撤':>8} {'Sharpe':>8} {'交易次数':>8} {'换手':>8}")
    print("-" * 60)

    for y in sorted(years.keys()):
        try:
            strategy = create_strategy(strategy_name, params)
            _, engine = build_engine(strategy, fill_model)
            result = engine.run(years[y])
            m = compute_detailed_metrics(result)
            n_days = len(years[y])
            # 年化按实际天数调整
            total_ret = m["total_return_pct"] / 100
            annual_ret = (1 + total_ret) ** (252 / n_days) - 1 if n_days > 0 and total_ret > -1 else total_ret
            print(
                f"  {y} {annual_ret*100:>7.2f}% {m['max_drawdown_pct']:>7.2f}% "
                f"{m['sharpe']:>7.3f} {m['n_trades']:>8d} {m['annual_turnover_x']:>7.1f}x"
            )
        except Exception as e:
            print(f"  {y} 失败: {e}")


def run_slippage_sensitivity(strategy_name, params, all_bars):
    """滑点敏感性测试"""
    bps_levels = [0.0, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0]
    print(f"\n滑点敏感性 ({strategy_name}, NBO):")
    print(f"{'滑点(bps)':>10} {'年化':>8} {'回撤':>8} {'Sharpe':>8} {'总费用':>10}")
    print("-" * 55)

    for bps in bps_levels:
        try:
            strategy = create_strategy(strategy_name, params)
            _, engine = build_engine_custom(strategy, "next_bar_open", slippage_bps=bps)
            result = engine.run(all_bars)
            m = compute_metrics(result)
            print(
                f"  {bps:>7.1f} {m['annual_return_pct']:>7.2f}% "
                f"{m['max_drawdown_pct']:>7.2f}% {m['sharpe']:>7.3f} "
                f"{m['total_fees']:>10.0f}"
            )
        except Exception as e:
            print(f"  {bps:>7.1f} 失败: {e}")


def run_capacity_analysis(strategy_name, params, all_bars):
    """容量分析：不同初始资金量"""
    capitals = [100_000, 500_000, 1_000_000, 5_000_000, 10_000_000]
    print(f"\n容量分析 ({strategy_name}, NBO, 0.5bps):")
    print(f"{'初始资金':>12} {'年化':>8} {'回撤':>8} {'Sharpe':>8}")
    print("-" * 45)

    for cap in capitals:
        try:
            strategy = create_strategy(strategy_name, params)
            _, engine = build_engine_custom(strategy, "next_bar_open", slippage_bps=5.0,
                                           initial_capital=float(cap))
            result = engine.run(all_bars)
            m = compute_metrics(result)
            print(
                f"  {cap:>10,} {m['annual_return_pct']:>7.2f}% "
                f"{m['max_drawdown_pct']:>7.2f}% {m['sharpe']:>7.3f}"
            )
        except Exception as e:
            print(f"  {cap:>10,} 失败: {e}")


def main():
    print("=" * 70)
    print("策略收益验证 & 稳健性测试")
    print("=" * 70)

    bars = load_data()

    # ================================================================
    # 1. small_size 修正后重跑
    # ================================================================
    print("\n" + "=" * 70)
    print("1. SMALL_SIZE - 修正后 (issue_size 替代 remain_size)")
    print("=" * 70)

    # 1a. 默认参数
    print("\n--- 1a. 默认参数 ---")
    for fm in ["next_bar_open", "same_bar_close"]:
        r = run_with_result("small_size", {}, fm, bars)
        m = compute_detailed_metrics(r)
        print(f"  {fm:20s} 年化={m['annual_return_pct']:>7.2f}% 回撤={m['max_drawdown_pct']:>6.2f}% "
              f"Sharpe={m['sharpe']:>6.3f} 换手={m['annual_turnover_x']:.1f}x 交易={m['n_trades']}")

    # 1b. 之前最优参数（不再可用，但测试 issue_size 等效参数）
    print("\n--- 1b. 参数扫描 (issue_size) ---")
    sweep_params = [
        {"top_k": k, "rebalance_days": r, "max_price": p,
         "max_issue_size": iss, "min_issue_size": mi}
        for k in [10, 15, 20]
        for r in [1, 3, 5]
        for p in [115.0, 120.0, 130.0]
        for iss in [30000.0, 60000.0, 100000.0]
        for mi in [3000.0, 5000.0]
    ]
    best = {"annual_return_pct": -999}
    results_ss = []
    for i, params in enumerate(sweep_params, 1):
        try:
            r = run_with_result("small_size", params, "next_bar_open", bars)
            m = compute_detailed_metrics(r)
            m["params"] = json.dumps(params)
            results_ss.append(m)
            if m["annual_return_pct"] > best["annual_return_pct"]:
                best = m
            if i % 100 == 0:
                print(f"  进度: {i}/{len(sweep_params)}, 当前最优: {best['annual_return_pct']:.2f}%")
        except:
            pass

    results_ss.sort(key=lambda x: x["annual_return_pct"], reverse=True)
    print(f"\n  Small Size (issue_size) Top 10:")
    for i, m in enumerate(results_ss[:10], 1):
        print(f"  #{i:2d} 年化={m['annual_return_pct']:>7.2f}% 回撤={m['max_drawdown_pct']:>6.2f}% "
              f"Sharpe={m['sharpe']:>6.3f} 换手={m['annual_turnover_x']:.1f}x | {m['params']}")

    # save CSV
    project_path("results").mkdir(parents=True, exist_ok=True)
    if results_ss:
        keys = [k for k in results_ss[0] if k != "params"] + ["params"]
        with open(project_path("results", "verified_small_size_nbo.csv"), "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(results_ss)

    # ================================================================
    # 2. new_bond 复现
    # ================================================================
    print("\n" + "=" * 70)
    print("2. NEW_BOND - 复现验证 (list_date 无前视偏差)")
    print("=" * 70)

    nb_best_params = {"top_k": 10, "rebalance_days": 5, "max_age_days": 60,
                      "max_price": 120.0, "min_age_days": 5}

    for fm in ["next_bar_open", "same_bar_close"]:
        r = run_with_result("new_bond", nb_best_params, fm, bars)
        m = compute_detailed_metrics(r)
        print(f"  {fm:20s} 年化={m['annual_return_pct']:>7.2f}% 回撤={m['max_drawdown_pct']:>6.2f}% "
              f"Sharpe={m['sharpe']:>6.3f} 换手={m['annual_turnover_x']:.1f}x 交易={m['n_trades']}")

    # ================================================================
    # 3. 分年度拆解 (最优参数)
    # ================================================================
    print("\n" + "=" * 70)
    print("3. 分年度收益拆解")
    print("=" * 70)

    if results_ss:
        best_ss_params = json.loads(results_ss[0]["params"])
        run_yearly_breakdown("small_size", best_ss_params, "next_bar_open", bars)

    run_yearly_breakdown("new_bond", nb_best_params, "next_bar_open", bars)

    # ================================================================
    # 4. 滑点敏感性
    # ================================================================
    print("\n" + "=" * 70)
    print("4. 滑点敏感性测试")
    print("=" * 70)

    if results_ss:
        run_slippage_sensitivity("small_size", json.loads(results_ss[0]["params"]), bars)

    run_slippage_sensitivity("new_bond", nb_best_params, bars)

    # ================================================================
    # 5. 容量分析
    # ================================================================
    print("\n" + "=" * 70)
    print("5. 容量分析")
    print("=" * 70)

    if results_ss:
        run_capacity_analysis("small_size", json.loads(results_ss[0]["params"]), bars)

    run_capacity_analysis("new_bond", nb_best_params, bars)

    print("\n" + "=" * 70)
    print("验证完成")
    print("=" * 70)


if __name__ == "__main__":
    main()
