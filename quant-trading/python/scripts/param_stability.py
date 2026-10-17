#!/usr/bin/env python3
"""参数稳定性分析 — 邻域扰动 ±20%

对 small_size 和 new_bond 的推荐参数，对每个关键参数分别做 ±20% 扰动，
检查邻域内 Sharpe 均值是否 > 某阈值（说明不是"孤岛"参数）。

对数值参数做 ±20%、±10% 扰动（5 个点: -20%, -10%, 0, +10%, +20%）。
对整数参数取 round()。

稳定性判定:
  - 邻域内所有组合年化收益 > 0
  - 邻域平均 Sharpe ≥ 0.3
  - 邻域 Sharpe 标准差 < 0.5 (低方差)

用法:
    python scripts/param_stability.py
"""
from __future__ import annotations

import json
import logging
import sys
import time
from datetime import date
from pathlib import Path


from aipha.backtest.engine import BacktestConfig, BacktestEngine, BacktestResult
from aipha.data.cb_data_adapter import CBDataAdapter
from aipha.execution.fees import (
    CommissionModel,
    FeeModelCNEquity,
    StampTaxModel,
    TransferFeeModel,
)
from aipha.execution.fill_models import NextBarOpenFillModel
from aipha.execution.slippage import FixedBpsSlippageModel
from aipha.market.rules_cn_equity import MarketRulesCNEquity
from aipha.market.rules_convertible_bond import MarketRulesConvertibleBond
from aipha.utils.paths import project_path

import aipha.strategy.small_size  # noqa: F401
import aipha.strategy.new_bond  # noqa: F401

from aipha.strategy.small_size import SmallSizeConfig, SmallSizeStrategy
from aipha.strategy.new_bond import NewBondConfig, NewBondStrategy

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


# ============================================================================
# 基准参数
# ============================================================================

SMALL_SIZE_BASE = dict(
    top_k=15,
    rebalance_days=5,
    max_price=120.0,
    max_issue_size=30000.0,
    min_issue_size=5000.0,
    min_turnover=50.0,
    min_days_to_maturity=60,
    stop_loss_pct=0.15,
    lot_size=10,
)

NEW_BOND_BASE = dict(
    top_k=10,
    rebalance_days=5,
    max_age_days=60,
    min_age_days=5,
    max_price=120.0,
    min_turnover=50.0,
    stop_loss_pct=0.15,
    lot_size=10,
)

# 要扰动的参数及其类型
SMALL_SIZE_PERTURB_KEYS = {
    "top_k": "int",
    "rebalance_days": "int",
    "max_price": "float",
    "max_issue_size": "float",
    "stop_loss_pct": "float",
}

NEW_BOND_PERTURB_KEYS = {
    "top_k": "int",
    "rebalance_days": "int",
    "max_age_days": "int",
    "max_price": "float",
    "stop_loss_pct": "float",
}

PERTURBATIONS = [-0.20, -0.10, 0.0, 0.10, 0.20]


# ============================================================================
# 工具函数
# ============================================================================

def perturb_value(base_val, delta: float, param_type: str):
    """对参数值做扰动"""
    new_val = base_val * (1 + delta)
    if param_type == "int":
        new_val = max(1, round(new_val))
    else:
        new_val = round(new_val, 4)
    return new_val


def create_strategy(name: str, params: dict):
    if name == "small_size":
        return SmallSizeStrategy(config=SmallSizeConfig(**params))
    elif name == "new_bond":
        return NewBondStrategy(config=NewBondConfig(**params))
    raise ValueError(name)


def build_engine(strategy) -> BacktestEngine:
    bt_config = BacktestConfig(
        initial_capital=500_000.0,
        lot_size=10,
        commission_rate=0.0001,
        commission_min=5.0,  # 13.1 CB权威值
        stamp_tax_rate=0.0,
        transfer_fee_rate=0.0,
        slippage_bps=5.0,
        t_plus_n=0,
    )
    fee_model = FeeModelCNEquity(
        commission=CommissionModel(rate=0.0001, min_fee=5.0),  # §13.1 CB: commission_min=5
        stamp_tax=StampTaxModel(sell_rate=0.0),
        transfer_fee=TransferFeeModel(rate=0.0),
    )
    return BacktestEngine(
        config=bt_config,
        strategy=strategy,
        fill_model=NextBarOpenFillModel(),
        slippage=FixedBpsSlippageModel(bps=5.0),
        fee_model=fee_model,
        rules=MarketRulesConvertibleBond(),  # §13.1
    )


def compute_metrics(result: BacktestResult) -> dict:
    initial = result.initial_capital
    final = result.final_nav
    total_return = (final - initial) / initial
    snapshots = result.daily_snapshots
    n_days = len(snapshots)
    n_years = n_days / 252.0

    annual_return = (
        (1 + total_return) ** (1 / n_years) - 1
        if n_years > 0 and total_return > -1
        else total_return
    )

    peak = initial
    max_dd = 0.0
    for snap in snapshots:
        if snap.nav > peak:
            peak = snap.nav
        dd = (peak - snap.nav) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    returns = [s.daily_return for s in snapshots]
    if len(returns) > 1:
        avg_ret = sum(returns) / len(returns)
        std_ret = (sum((r - avg_ret) ** 2 for r in returns) / (len(returns) - 1)) ** 0.5  # ddof=1 §13.2
        sharpe = (avg_ret * 252 - 0.02) / (std_ret * 252 ** 0.5) if std_ret > 0 else 0
    else:
        sharpe = 0.0

    calmar = annual_return / max_dd if max_dd > 0 else 0.0

    return {
        "annual_return_pct": round(annual_return * 100, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "sharpe": round(sharpe, 3),
        "calmar": round(calmar, 3),
        "n_trades": result.total_trades,
    }


def run_stability_for_strategy(
    strategy_name: str,
    base_params: dict,
    perturb_keys: dict,
    bars_by_date: dict,
) -> dict:
    """对一个策略进行参数稳定性分析"""
    print(f"\n{'='*78}")
    print(f"参数稳定性分析: {strategy_name}")
    print(f"{'='*78}")
    print(f"基准参数: {base_params}")

    all_results = []   # 每行: {param, delta, value, metrics}

    for param_key, param_type in perturb_keys.items():
        base_val = base_params[param_key]
        print(f"\n  扰动 {param_key} (基准={base_val}):")

        for delta in PERTURBATIONS:
            new_val = perturb_value(base_val, delta, param_type)

            # 构造扰动后参数
            params = dict(base_params)
            params[param_key] = new_val

            t0 = time.time()
            try:
                strategy = create_strategy(strategy_name, params)
                engine = build_engine(strategy)
                result = engine.run(bars_by_date)
                metrics = compute_metrics(result)
                elapsed = time.time() - t0

                marker = " *" if delta == 0 else ""
                print(
                    f"    {param_key}={new_val:>10} (Δ={delta:>+5.0%})  "
                    f"年化={metrics['annual_return_pct']:>7.2f}%  "
                    f"Sharpe={metrics['sharpe']:>6.3f}  "
                    f"DD={metrics['max_drawdown_pct']:>6.2f}%  "
                    f"({elapsed:.1f}s){marker}"
                )

                all_results.append({
                    "param": param_key,
                    "delta": delta,
                    "value": new_val,
                    **metrics,
                })
            except Exception as e:
                print(f"    {param_key}={new_val} (Δ={delta:>+5.0%})  ERROR: {e}")
                all_results.append({
                    "param": param_key,
                    "delta": delta,
                    "value": new_val,
                    "annual_return_pct": None,
                    "sharpe": None,
                    "error": str(e),
                })

    # 汇总统计
    valid = [r for r in all_results if r.get("sharpe") is not None]
    sharpes = [r["sharpe"] for r in valid]
    returns = [r["annual_return_pct"] for r in valid]

    if sharpes:
        avg_sharpe = sum(sharpes) / len(sharpes)
        std_sharpe = (sum((s - avg_sharpe) ** 2 for s in sharpes) / (len(sharpes) - 1)) ** 0.5  # ddof=1
        min_sharpe = min(sharpes)
        max_sharpe = max(sharpes)
        avg_return = sum(returns) / len(returns)
        all_positive = all(r > 0 for r in returns)
    else:
        avg_sharpe = std_sharpe = min_sharpe = max_sharpe = avg_return = 0
        all_positive = False

    # 逐参数统计
    print(f"\n  {'='*74}")
    print(f"  逐参数邻域 Sharpe 均值:")
    per_param = {}
    for param_key in perturb_keys:
        param_sharpes = [r["sharpe"] for r in valid if r["param"] == param_key]
        if param_sharpes:
            pavg = sum(param_sharpes) / len(param_sharpes)
            pstd = (sum((s - pavg) ** 2 for s in param_sharpes) / len(param_sharpes)) ** 0.5
            pmin = min(param_sharpes)
            per_param[param_key] = {"avg": pavg, "std": pstd, "min": pmin}
            print(f"    {param_key:25s}  avg={pavg:.3f}  std={pstd:.3f}  min={pmin:.3f}")

    summary = {
        "strategy": strategy_name,
        "n_tests": len(valid),
        "avg_sharpe": round(avg_sharpe, 3),
        "std_sharpe": round(std_sharpe, 3),
        "min_sharpe": round(min_sharpe, 3),
        "max_sharpe": round(max_sharpe, 3),
        "avg_return_pct": round(avg_return, 2),
        "all_positive_return": all_positive,
        "per_param": per_param,
    }

    # 判定
    SHARPE_AVG_THRESHOLD = 0.3
    SHARPE_STD_THRESHOLD = 0.5
    stable = (
        avg_sharpe >= SHARPE_AVG_THRESHOLD
        and std_sharpe < SHARPE_STD_THRESHOLD
        and all_positive
    )
    verdict = "STABLE" if stable else "UNSTABLE"
    summary["verdict"] = verdict

    print(f"\n  汇总: Sharpe avg={avg_sharpe:.3f} std={std_sharpe:.3f} "
          f"min={min_sharpe:.3f} max={max_sharpe:.3f}")
    print(f"  全部年化>0: {'YES' if all_positive else 'NO'}")
    print(f"  判定: [{verdict}]")

    return {"summary": summary, "details": all_results}


def main():
    print("=" * 78)
    print("参数稳定性分析 (Neighborhood Perturbation ±20%)")
    print("=" * 78)

    # 加载数据
    print("\n加载可转债数据 2022-2024 ...")
    t0 = time.time()
    adapter = CBDataAdapter(cache_dir="data/cache/cb_tushare")
    bars = adapter.load("20220104", "20241231")
    print(f"  加载完成: {len(bars)} 个交易日 ({time.time() - t0:.1f}s)")

    results = {}

    # small_size 稳定性
    results["small_size"] = run_stability_for_strategy(
        "small_size", SMALL_SIZE_BASE, SMALL_SIZE_PERTURB_KEYS, bars
    )

    # new_bond 稳定性 (使用 OOS 验证通过的 reb=5 参数)
    results["new_bond"] = run_stability_for_strategy(
        "new_bond", NEW_BOND_BASE, NEW_BOND_PERTURB_KEYS, bars
    )

    # 总结
    print(f"\n{'='*78}")
    print("参数稳定性总结")
    print(f"{'='*78}")
    for name, res in results.items():
        s = res["summary"]
        print(f"  {name:15s}  [{s['verdict']:8s}]  "
              f"Sharpe avg={s['avg_sharpe']:.3f} std={s['std_sharpe']:.3f}  "
              f"全正={'Y' if s['all_positive_return'] else 'N'}")

    # 保存
    output_path = project_path("results", "param_stability.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n结果已保存: {output_path}")

    all_stable = all(r["summary"]["verdict"] == "STABLE" for r in results.values())
    return results, all_stable


if __name__ == "__main__":
    results, all_stable = main()
    sys.exit(0 if all_stable else 1)
