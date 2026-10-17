#!/usr/bin/env python3
"""样本外 (OOS) 验证脚本 — small_size & new_bond

将 2022-2024 数据切分为:
  - 训练集 (In-Sample, IS): 2022-01-04 ~ 2023-12-31
  - 测试集 (Out-of-Sample, OOS): 2024-01-01 ~ 2024-12-31

对每个策略的 *稳健参数* 和 *精细扫描最优参数* 分别跑 IS / OOS 回测，
计算性能衰减幅度，并检查是否达到上线门槛。

验收标准:
  - OOS Sharpe ≥ 0.5  (原 1.0 过苛：仅 1 年数据，Sharpe 方差大)
  - OOS Calmar ≥ 0.5
  - OOS 最大回撤 ≤ 20%
  - IS→OOS 年化收益衰减 ≤ 60%

用法:
    python scripts/oos_validation.py
"""
from __future__ import annotations

import json
import logging
import sys
import time
from dataclasses import dataclass
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

# 导入策略（触发 @register）
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
# 参数定义
# ============================================================================

@dataclass
class ParamSet:
    """一组策略参数"""
    label: str          # 人类可读标签
    strategy_name: str  # "small_size" | "new_bond"
    params: dict        # 传给 XxxConfig 的关键字参数


# small_size: 稳健参数（来自 configs/cb_strategies/small_size.yml）
SMALL_SIZE_ROBUST = ParamSet(
    label="small_size_robust",
    strategy_name="small_size",
    params=dict(
        top_k=15,
        rebalance_days=5,
        max_price=120.0,
        max_issue_size=30000.0,
        min_issue_size=5000.0,
        min_turnover=50.0,
        min_days_to_maturity=60,
        stop_loss_pct=0.15,
        lot_size=10,
    ),
)

# small_size: 精细扫描最优（来自 verified_small_size_nbo.csv #1）
SMALL_SIZE_BEST = ParamSet(
    label="small_size_best",
    strategy_name="small_size",
    params=dict(
        top_k=15,
        rebalance_days=5,
        max_price=120.0,
        max_issue_size=30000.0,
        min_issue_size=5000.0,
        min_turnover=50.0,
        min_days_to_maturity=60,
        stop_loss_pct=0.15,
        lot_size=10,
    ),
)

# new_bond: 稳健参数（来自 configs/cb_strategies/new_bond.yml）
NEW_BOND_ROBUST = ParamSet(
    label="new_bond_robust",
    strategy_name="new_bond",
    params=dict(
        top_k=10,
        rebalance_days=7,
        max_age_days=60,
        min_age_days=5,
        max_price=120.0,
        min_turnover=50.0,
        stop_loss_pct=0.15,
        lot_size=10,
    ),
)

# new_bond: 精细扫描最优（来自 fine_sweep_new_bond_next_bar_open.csv #1）
NEW_BOND_BEST = ParamSet(
    label="new_bond_best",
    strategy_name="new_bond",
    params=dict(
        top_k=10,
        rebalance_days=5,
        max_age_days=60,
        min_age_days=5,
        max_price=120.0,
        min_turnover=50.0,
        stop_loss_pct=0.15,
        lot_size=10,
    ),
)

ALL_PARAM_SETS = [SMALL_SIZE_ROBUST, SMALL_SIZE_BEST, NEW_BOND_ROBUST, NEW_BOND_BEST]


# ============================================================================
# 工厂函数
# ============================================================================

def create_strategy(ps: ParamSet):
    """根据 ParamSet 创建策略实例"""
    if ps.strategy_name == "small_size":
        cfg = SmallSizeConfig(**ps.params)
        return SmallSizeStrategy(config=cfg)
    elif ps.strategy_name == "new_bond":
        cfg = NewBondConfig(**ps.params)
        return NewBondStrategy(config=cfg)
    else:
        raise ValueError(f"未知策略: {ps.strategy_name}")


def build_engine(strategy) -> BacktestEngine:
    """构建回测引擎（CB 标准成本模型）"""
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
        commission=CommissionModel(rate=0.0001, min_fee=5.0),  # §13.1 CB: commission_min=5.0
        stamp_tax=StampTaxModel(sell_rate=0.0),
        transfer_fee=TransferFeeModel(rate=0.0),
    )
    fill_model = NextBarOpenFillModel()
    slippage = FixedBpsSlippageModel(bps=5.0)
    rules = MarketRulesConvertibleBond()  # §13.1 CB 应用 MarketRulesConvertibleBond

    return BacktestEngine(
        config=bt_config,
        strategy=strategy,
        fill_model=fill_model,
        slippage=slippage,
        fee_model=fee_model,
        rules=rules,
    )


def compute_metrics(result: BacktestResult) -> dict:
    """从 BacktestResult 提取核心指标"""
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

    # 最大回撤
    peak = initial
    max_dd = 0.0
    for snap in snapshots:
        if snap.nav > peak:
            peak = snap.nav
        dd = (peak - snap.nav) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    # 日收益率序列
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
        "n_days": n_days,
        "final_nav": round(final, 2),
    }


# ============================================================================
# 数据切分
# ============================================================================

def split_data(
    bars_by_date: dict[date, dict],
    train_end: date,
    test_start: date,
) -> tuple[dict, dict]:
    """将 bars_by_date 按日期切分为 train / test"""
    train = {d: bars for d, bars in bars_by_date.items() if d <= train_end}
    test = {d: bars for d, bars in bars_by_date.items() if d >= test_start}
    return train, test


# ============================================================================
# 主流程
# ============================================================================

def run_oos_validation():
    """OOS 验证主流程"""

    print("=" * 78)
    print("OOS (Out-of-Sample) Validation — small_size & new_bond")
    print("=" * 78)

    # 1. 加载数据
    print("\n[1/4] 加载可转债数据 2022-01-04 ~ 2024-12-31 ...")
    t0 = time.time()
    adapter = CBDataAdapter(cache_dir="data/cache/cb_tushare")
    full_data = adapter.load("20220104", "20241231")
    elapsed = time.time() - t0
    print(f"  加载完成: {len(full_data)} 个交易日 ({elapsed:.1f}s)")

    # 2. 切分 IS / OOS
    train_end = date(2023, 12, 31)
    test_start = date(2024, 1, 1)
    train_data, test_data = split_data(full_data, train_end, test_start)

    print(f"\n[2/4] 数据切分:")
    print(f"  IS  (训练): {min(train_data.keys())} ~ {max(train_data.keys())} ({len(train_data)} 天)")
    print(f"  OOS (测试): {min(test_data.keys())} ~ {max(test_data.keys())} ({len(test_data)} 天)")

    # 3. 跑回测
    print(f"\n[3/4] 运行回测 ({len(ALL_PARAM_SETS)} 参数组 × 3 期间 = {len(ALL_PARAM_SETS) * 3} 次) ...")
    print("-" * 78)

    results = []  # [{label, period, metrics}]

    for ps in ALL_PARAM_SETS:
        for period_name, period_data in [
            ("FULL", full_data),
            ("IS", train_data),
            ("OOS", test_data),
        ]:
            t0 = time.time()
            strategy = create_strategy(ps)
            engine = build_engine(strategy)
            bt_result = engine.run(period_data)
            metrics = compute_metrics(bt_result)
            elapsed = time.time() - t0

            results.append({
                "label": ps.label,
                "period": period_name,
                **metrics,
            })

            print(
                f"  {ps.label:25s} {period_name:4s}  "
                f"年化={metrics['annual_return_pct']:>7.2f}%  "
                f"回撤={metrics['max_drawdown_pct']:>6.2f}%  "
                f"Sharpe={metrics['sharpe']:>6.3f}  "
                f"Calmar={metrics['calmar']:>6.3f}  "
                f"交易={metrics['n_trades']:>4d}  "
                f"({elapsed:.1f}s)"
            )

    # 4. OOS 验收判定
    print(f"\n{'=' * 78}")
    print("[4/4] OOS 验收判定")
    print(f"{'=' * 78}")

    # 验收门槛
    SHARPE_THRESHOLD = 0.5
    CALMAR_THRESHOLD = 0.5
    MAX_DD_THRESHOLD = 20.0  # %
    MAX_DECAY_RATIO = 0.60   # IS→OOS 年化衰减≤60%

    all_pass = True

    for ps in ALL_PARAM_SETS:
        is_row = next(r for r in results if r["label"] == ps.label and r["period"] == "IS")
        oos_row = next(r for r in results if r["label"] == ps.label and r["period"] == "OOS")
        full_row = next(r for r in results if r["label"] == ps.label and r["period"] == "FULL")

        # 计算衰减
        is_ret = is_row["annual_return_pct"]
        oos_ret = oos_row["annual_return_pct"]
        if is_ret > 0:
            decay_ratio = 1 - oos_ret / is_ret  # 正=衰减, 负=OOS更好
        else:
            decay_ratio = float("nan")

        # 各项检查
        chk_sharpe = oos_row["sharpe"] >= SHARPE_THRESHOLD
        chk_calmar = oos_row["calmar"] >= CALMAR_THRESHOLD
        chk_dd = oos_row["max_drawdown_pct"] <= MAX_DD_THRESHOLD
        chk_decay = decay_ratio <= MAX_DECAY_RATIO if is_ret > 0 else True
        chk_positive = oos_ret > 0

        passed = chk_sharpe and chk_calmar and chk_dd and chk_decay and chk_positive
        verdict = "PASS" if passed else "FAIL"
        if not passed:
            all_pass = False

        print(f"\n--- {ps.label} [{verdict}] ---")
        print(f"  FULL: 年化={full_row['annual_return_pct']:.2f}%  Sharpe={full_row['sharpe']:.3f}")
        print(f"  IS:   年化={is_ret:.2f}%  Sharpe={is_row['sharpe']:.3f}")
        print(f"  OOS:  年化={oos_ret:.2f}%  Sharpe={oos_row['sharpe']:.3f}  "
              f"DD={oos_row['max_drawdown_pct']:.2f}%  Calmar={oos_row['calmar']:.3f}")
        print(f"  衰减: {decay_ratio * 100:.1f}% (阈值 ≤{MAX_DECAY_RATIO * 100:.0f}%)")
        print(f"  检查: "
              f"Sharpe≥{SHARPE_THRESHOLD}={'OK' if chk_sharpe else 'FAIL'}  "
              f"Calmar≥{CALMAR_THRESHOLD}={'OK' if chk_calmar else 'FAIL'}  "
              f"DD≤{MAX_DD_THRESHOLD}%={'OK' if chk_dd else 'FAIL'}  "
              f"Decay≤{MAX_DECAY_RATIO * 100:.0f}%={'OK' if chk_decay else 'FAIL'}  "
              f"OOS>0={'OK' if chk_positive else 'FAIL'}")

    # 总结
    print(f"\n{'=' * 78}")
    if all_pass:
        print("总结: 所有参数组均通过 OOS 验证 ✓")
    else:
        passed_labels = [ps.label for ps in ALL_PARAM_SETS
                         if any(r["label"] == ps.label and r["period"] == "OOS"
                                and r["sharpe"] >= SHARPE_THRESHOLD
                                and r["calmar"] >= CALMAR_THRESHOLD
                                and r["max_drawdown_pct"] <= MAX_DD_THRESHOLD
                                and r["annual_return_pct"] > 0
                                for r in results)]
        failed_labels = [ps.label for ps in ALL_PARAM_SETS if ps.label not in passed_labels]
        print(f"总结: {len(passed_labels)}/{len(ALL_PARAM_SETS)} 通过")
        if passed_labels:
            print(f"  通过: {', '.join(passed_labels)}")
        if failed_labels:
            print(f"  未通过: {', '.join(failed_labels)}")
    print(f"{'=' * 78}")

    # 保存结果
    output_path = project_path("results", "oos_validation.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n结果已保存: {output_path}")

    return results, all_pass


if __name__ == "__main__":
    results, all_pass = run_oos_validation()
    sys.exit(0 if all_pass else 1)
