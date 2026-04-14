#!/usr/bin/env python3
"""通用可转债策略回测脚本

支持所有已注册策略的回测、参数扫描、批量对比。
策略推荐配置见 configs/cb_strategies/*.yml

用法：
    # 单策略回测（使用默认参数）
    python scripts/run_cb_strategy.py --strategy low_price

    # 参数扫描（输出 CSV）
    python scripts/run_cb_strategy.py --strategy low_price --sweep

    # 批量对比所有策略
    python scripts/run_cb_strategy.py --compare-all

    # 指定成交模型
    python scripts/run_cb_strategy.py --strategy double_low --fill-model next_bar_open
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

# 添加 src
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aipha.backtest.engine import BacktestConfig, BacktestEngine, BacktestResult
from aipha.data.cb_data_adapter import CBDataAdapter
from aipha.execution.fees import (
    CommissionModel,
    FeeModelCNEquity,
    StampTaxModel,
    TransferFeeModel,
)
from aipha.execution.fill_models import get_fill_model
from aipha.execution.slippage import FixedBpsSlippageModel
from aipha.market.rules_convertible_bond import MarketRulesConvertibleBond

# 必须导入所有策略模块以触发 @register 装饰器
import aipha.strategy.triple_low  # noqa: F401
import aipha.strategy.double_low  # noqa: F401
import aipha.strategy.low_price  # noqa: F401
import aipha.strategy.pseudo_ytm  # noqa: F401
import aipha.strategy.cb_momentum  # noqa: F401
import aipha.strategy.low_premium  # noqa: F401
import aipha.strategy.negative_premium  # noqa: F401
import aipha.strategy.small_size  # noqa: F401
import aipha.strategy.cb_mean_reversion  # noqa: F401
import aipha.strategy.cb_multi_factor  # noqa: F401
import aipha.strategy.new_bond  # noqa: F401
import aipha.strategy.vol_breakout  # noqa: F401
import aipha.strategy.new_small  # noqa: F401
import aipha.strategy.ytm_plus  # noqa: F401
import aipha.strategy.cb_value_momentum  # noqa: F401
import aipha.strategy.turnover_surge  # noqa: F401
import aipha.strategy.forced_redeem_play  # noqa: F401
import aipha.strategy.small_size_enhanced  # noqa: F401
import aipha.strategy.defensive_value  # noqa: F401

from aipha.strategy.registry import StrategyRegistry
from aipha.strategy.triple_low import TripleLowConfig, TripleLowStrategy
from aipha.strategy.double_low import DoubleLowStrategy
from aipha.strategy.low_price import LowPriceConfig, LowPriceStrategy
from aipha.strategy.pseudo_ytm import PseudoYTMConfig, PseudoYTMStrategy
from aipha.strategy.cb_momentum import CBMomentumConfig, CBMomentumStrategy
from aipha.strategy.low_premium import LowPremiumConfig, LowPremiumStrategy
from aipha.strategy.negative_premium import NegativePremiumConfig, NegativePremiumStrategy
from aipha.strategy.small_size import SmallSizeConfig, SmallSizeStrategy
from aipha.strategy.cb_mean_reversion import CBMeanReversionConfig, CBMeanReversionStrategy
from aipha.strategy.cb_multi_factor import MultiFactorConfig, MultiFactorStrategy
from aipha.strategy.new_bond import NewBondConfig, NewBondStrategy
from aipha.strategy.vol_breakout import VolBreakoutConfig, VolBreakoutStrategy
from aipha.strategy.new_small import NewSmallConfig, NewSmallStrategy
from aipha.strategy.ytm_plus import YTMPlusConfig, YTMPlusStrategy
from aipha.strategy.cb_value_momentum import CBValueMomentumConfig, CBValueMomentumStrategy
from aipha.strategy.turnover_surge import TurnoverSurgeConfig, TurnoverSurgeStrategy
from aipha.strategy.forced_redeem_play import ForcedRedeemPlayConfig, ForcedRedeemPlayStrategy
from aipha.strategy.small_size_enhanced import SmallSizeEnhancedConfig, SmallSizeEnhancedStrategy
from aipha.strategy.defensive_value import DefensiveValueConfig, DefensiveValueStrategy

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ============================================================================
# 策略工厂：从参数字典创建策略实例
# ============================================================================

STRATEGY_CONFIGS = {
    "triple_low": (TripleLowStrategy, TripleLowConfig),
    "double_low": (DoubleLowStrategy, None),
    "low_price": (LowPriceStrategy, LowPriceConfig),
    "pseudo_ytm": (PseudoYTMStrategy, PseudoYTMConfig),
    "cb_momentum": (CBMomentumStrategy, CBMomentumConfig),
    "low_premium": (LowPremiumStrategy, LowPremiumConfig),
    "negative_premium": (NegativePremiumStrategy, NegativePremiumConfig),
    "small_size": (SmallSizeStrategy, SmallSizeConfig),
    "cb_mean_reversion": (CBMeanReversionStrategy, CBMeanReversionConfig),
    "multi_factor": (MultiFactorStrategy, MultiFactorConfig),
    "new_bond": (NewBondStrategy, NewBondConfig),
    "vol_breakout": (VolBreakoutStrategy, VolBreakoutConfig),
    "new_small": (NewSmallStrategy, NewSmallConfig),
    "ytm_plus": (YTMPlusStrategy, YTMPlusConfig),
    "cb_value_momentum": (CBValueMomentumStrategy, CBValueMomentumConfig),
    "turnover_surge": (TurnoverSurgeStrategy, TurnoverSurgeConfig),
    "forced_redeem_play": (ForcedRedeemPlayStrategy, ForcedRedeemPlayConfig),
    "small_size_enhanced": (SmallSizeEnhancedStrategy, SmallSizeEnhancedConfig),
    "defensive_value": (DefensiveValueStrategy, DefensiveValueConfig),
}


def create_strategy(name: str, params: dict | None = None):
    """创建策略实例"""
    if name not in STRATEGY_CONFIGS:
        raise ValueError(f"未知策略: {name}. 可用: {list(STRATEGY_CONFIGS.keys())}")

    cls, config_cls = STRATEGY_CONFIGS[name]
    params = params or {}

    if name == "double_low":
        return cls(**params)
    elif name == "triple_low":
        cfg = config_cls(**params)
        return cls(config=cfg)
    else:
        cfg = config_cls(**params)
        return cls(config=cfg)


def build_engine(strategy, fill_model_name: str = "next_bar_open",
                 initial_capital: float = 500_000.0):
    """构建回测引擎"""
    bt_config = BacktestConfig(
        initial_capital=initial_capital,
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

    fill_model = get_fill_model(fill_model_name)
    slippage = FixedBpsSlippageModel(bps=bt_config.slippage_bps)
    rules = MarketRulesConvertibleBond(
        lot_size=bt_config.lot_size,
        t_plus=0,
        commission_rate=Decimal("0.0001"),
        commission_min=Decimal("5"),
        stamp_tax_rate=Decimal("0"),
    )

    engine = BacktestEngine(
        config=bt_config,
        strategy=strategy,
        fill_model=fill_model,
        slippage=slippage,
        fee_model=fee_model,
        rules=rules,
    )
    return bt_config, engine


# ============================================================================
# 指标计算
# ============================================================================

def compute_metrics(result: BacktestResult) -> dict:
    """从 BacktestResult 计算核心指标"""
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

    # 夏普
    returns = [s.daily_return for s in snapshots]
    if len(returns) > 1:
        avg_ret = sum(returns) / len(returns)
        std_ret = (sum((r - avg_ret) ** 2 for r in returns) / (len(returns) - 1)) ** 0.5  # ddof=1 §13.2
        sharpe = (avg_ret * 252 - 0.02) / (std_ret * 252**0.5) if std_ret > 0 else 0
    else:
        sharpe = 0

    calmar = annual_return / max_dd if max_dd > 0 else 0

    # 换手
    total_turnover = sum(
        abs(getattr(snap, 'turnover', 0)) for snap in snapshots
    )

    return {
        "total_return_pct": round(total_return * 100, 2),
        "annual_return_pct": round(annual_return * 100, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "sharpe": round(sharpe, 3),
        "calmar": round(calmar, 3),
        "n_trades": result.total_trades,
        "total_fees": round(result.total_fees.total, 2) if hasattr(result.total_fees, 'total') else 0,
        "final_nav": round(final, 2),
    }


def print_metrics(name: str, fill_model: str, params: dict, metrics: dict):
    """打印单次回测指标"""
    print(f"\n{'='*70}")
    print(f"策略: {name} | 成交模型: {fill_model}")
    print(f"参数: {params}")
    print(f"{'='*70}")
    print(f"  总收益:    {metrics['total_return_pct']:>8.2f}%")
    print(f"  年化收益:  {metrics['annual_return_pct']:>8.2f}%")
    print(f"  最大回撤:  {metrics['max_drawdown_pct']:>8.2f}%")
    print(f"  夏普比率:  {metrics['sharpe']:>8.3f}")
    print(f"  卡玛比率:  {metrics['calmar']:>8.3f}")
    print(f"  交易次数:  {metrics['n_trades']:>8d}")
    print(f"  总费用:    {metrics['total_fees']:>8.2f}")
    print(f"  期末净值:  {metrics['final_nav']:>12.2f}")
    print(f"{'='*70}")


# ============================================================================
# 参数扫描
# ============================================================================

# 每个策略的参数网格
SWEEP_GRIDS = {
    "double_low": [
        {"top_k": k, "rebalance_days": r, "max_price": 130.0, "max_premium": 50.0}
        for k in [10, 15, 20, 30]
        for r in [3, 5, 10]
    ],
    "low_price": [
        {"top_k": k, "rebalance_days": r, "max_price": p}
        for k in [10, 20, 30, 50]
        for r in [3, 5, 10]
        for p in [110.0, 115.0, 120.0]
    ],
    "pseudo_ytm": [
        {"top_k": k, "rebalance_days": r, "max_price": p}
        for k in [5, 10, 15, 20]
        for r in [5, 10, 20]
        for p in [95.0, 100.0, 105.0]
    ],
    "cb_momentum": [
        {"top_k": k, "lookback_days": lb, "rebalance_days": r}
        for k in [5, 10, 15]
        for lb in [10, 20, 40]
        for r in [3, 5, 10]
    ],
    "low_premium": [
        {"top_k": k, "rebalance_days": r, "max_price": p}
        for k in [10, 15, 20]
        for r in [3, 5, 10]
        for p in [130.0, 150.0, 200.0]
    ],
    "negative_premium": [
        {"top_k": k, "rebalance_days": r, "entry_threshold": t}
        for k in [5, 10, 15]
        for r in [1, 3, 5]
        for t in [-1.0, -2.0, -3.0]
    ],
    "triple_low": [
        {"top_k": k, "rebalance_days": r, "max_premium": mp, "stop_loss_pct": sl}
        for k in [5, 10, 15, 20]
        for r in [1, 3, 5, 10]
        for mp in [30.0, 35.0, 40.0, 50.0]
        for sl in [0.08, 0.10, 0.15]
    ],
    "small_size": [
        {"top_k": k, "rebalance_days": r, "max_price": p, "max_issue_size": iss,
         "min_issue_size": mis, "stop_loss_pct": sl}
        for k in [10, 15, 20, 30]
        for r in [1, 3, 5]
        for p in [115.0, 130.0]
        for iss in [30000.0, 60000.0, 100000.0]
        for mis in [3000.0, 5000.0, 8000.0]
        for sl in [0.10, 0.15, 0.20]
    ],
    "cb_mean_reversion": [
        {"top_k": k, "rebalance_days": r, "lookback": lb, "bb_k": bk}
        for k in [10, 15, 20]
        for r in [1, 3, 5]
        for lb in [10, 20, 30]
        for bk in [1.5, 2.0, 2.5]
    ],
    "multi_factor": [
        {"top_k": k, "rebalance_days": r, "price_weight": pw, "premium_weight": prw,
         "cb_value_weight": cw, "size_weight": sw, "max_premium": mp}
        for k in [15, 20, 25]
        for r in [1, 3, 5]
        for pw, prw, cw, sw in [
            (0.40, 0.30, 0.15, 0.15),
            (0.35, 0.30, 0.15, 0.20),
            (0.25, 0.25, 0.25, 0.25),
            (0.30, 0.35, 0.20, 0.15),
            (0.20, 0.20, 0.30, 0.30),
        ]
        for mp in [30.0, 40.0]
    ],
    "new_bond": [
        {"top_k": k, "rebalance_days": r, "max_age_days": a, "max_price": p,
         "min_age_days": ma, "stop_loss_pct": sl}
        for k in [5, 10, 15, 20]
        for r in [3, 5, 7, 10]
        for a in [60, 90, 120, 180]
        for p in [115.0, 130.0]
        for ma in [3, 5, 10]
        for sl in [0.10, 0.15, 0.20]
    ],
    "vol_breakout": [
        {"top_k": k, "rebalance_days": r, "atr_short": ash, "atr_long": al,
         "squeeze_threshold": sq, "breakout_lookback": bl}
        for k in [5, 10, 15]
        for r in [1, 3, 5]
        for ash in [3, 5]
        for al in [15, 20]
        for sq in [0.5, 0.6, 0.7]
        for bl in [10, 15]
    ],
    # === 新增 5 个策略的参数网格 ===
    "new_small": [
        {"top_k": k, "rebalance_days": r, "max_age_days": a,
         "max_issue_size": iss, "max_price": p, "stop_loss_pct": sl}
        for k in [5, 8, 10, 15, 20]
        for r in [3, 5, 7, 10]
        for a in [60, 90, 120, 180]
        for iss in [20000.0, 40000.0, 60000.0, 80000.0]
        for p in [120.0, 130.0, 140.0]
        for sl in [0.10, 0.15, 0.20]
    ],
    "ytm_plus": [
        {"top_k": k, "rebalance_days": r, "max_price": p,
         "ytm_weight": yw, "premium_weight": pw, "min_years_to_maturity": ym}
        for k in [10, 15, 20, 25]
        for r in [5, 10, 15, 20]
        for p in [100.0, 105.0, 110.0, 115.0]
        for yw, pw in [(0.7, 0.3), (0.6, 0.4), (0.5, 0.5), (0.4, 0.6), (0.3, 0.7)]
        for ym in [0.5, 1.0]
    ],
    "cb_value_momentum": [
        {"top_k": k, "lookback_days": lb, "rebalance_days": r,
         "max_price": p, "max_premium": mp}
        for k in [5, 10, 15]
        for lb in [10, 20, 30, 40]
        for r in [3, 5, 10]
        for p in [130.0, 150.0]
        for mp in [20.0, 30.0, 50.0]
    ],
    "turnover_surge": [
        {"top_k": k, "lookback_days": lb, "rebalance_days": r,
         "min_surge_ratio": sr, "max_price": p, "max_premium": mp,
         "require_positive_return": rpr}
        for k in [5, 10, 15]
        for lb in [10, 20, 30]
        for r in [1, 3, 5]
        for sr in [1.5, 2.0, 2.5, 3.0]
        for p in [130.0, 150.0]
        for mp in [30.0, 50.0]
        for rpr in [True, False]
    ],
    "forced_redeem_play": [
        {"top_k": k, "rebalance_days": r, "lower_cb_value": lcv,
         "upper_cb_value": ucv, "max_premium": mp, "stop_loss_pct": sl}
        for k in [5, 10, 15]
        for r in [3, 5, 10]
        for lcv in [90.0, 100.0, 110.0]
        for ucv in [125.0, 130.0, 140.0]
        for mp in [15.0, 20.0, 30.0]
        for sl in [0.10, 0.12, 0.15]
    ],
    "small_size_enhanced": [
        {"top_k": k, "rebalance_days": r, "max_price": p, "max_issue_size": iss,
         "buffer_k": bk, "size_weight": sw, "premium_weight": pw,
         "momentum_weight": mw, "max_premium": mp}
        for k in [15, 20, 25]
        for r in [1, 3, 5]
        for p in [115.0, 130.0]
        for iss in [40000.0, 60000.0, 80000.0]
        for bk in [3, 5, 8]
        for sw, pw, mw in [
            (0.50, 0.30, 0.20),
            (0.40, 0.35, 0.25),
            (0.60, 0.25, 0.15),
        ]
        for mp in [40.0, 50.0]
    ],
    "defensive_value": [
        {"top_k": k, "rebalance_days": r, "min_issue_size": mis,
         "max_price": p, "max_premium": mp, "buffer_k": 5,
         "ytm_weight": yw, "premium_weight": pw, "maturity_weight": mw,
         "max_years_to_maturity": 5.0, "stop_loss_pct": sl}
        for k in [20, 25, 30]
        for r in [5, 10, 15]
        for mis in [20000.0, 30000.0, 50000.0]
        for p in [110.0, 115.0, 120.0]
        for mp in [35.0, 45.0]
        for yw, pw, mw in [
            (0.40, 0.35, 0.25),
            (0.50, 0.30, 0.20),
        ]
        for sl in [0.10, 0.12]
    ],
}


def run_single(strategy_name: str, params: dict, fill_model_name: str,
               bars_by_date: dict) -> dict:
    """运行单次回测，返回指标"""
    strategy = create_strategy(strategy_name, params)
    _, engine = build_engine(strategy, fill_model_name)
    result = engine.run(bars_by_date)
    metrics = compute_metrics(result)
    metrics["strategy"] = strategy_name
    metrics["fill_model"] = fill_model_name
    metrics["params"] = json.dumps(params, ensure_ascii=False)
    return metrics


def run_sweep(strategy_name: str, fill_model_name: str, bars_by_date: dict,
              output_csv: str | None = None) -> list[dict]:
    """参数扫描"""
    grid = SWEEP_GRIDS.get(strategy_name, [])
    if not grid:
        print(f"未定义参数网格: {strategy_name}")
        return []

    results = []
    total = len(grid)

    print(f"\n开始参数扫描: {strategy_name} ({total} 组合) × {fill_model_name}")
    print("-" * 70)

    for i, params in enumerate(grid, 1):
        t0 = time.time()
        try:
            metrics = run_single(strategy_name, params, fill_model_name, bars_by_date)
            results.append(metrics)
            elapsed = time.time() - t0
            print(
                f"  [{i:3d}/{total}] "
                f"年化={metrics['annual_return_pct']:>7.2f}% "
                f"回撤={metrics['max_drawdown_pct']:>6.2f}% "
                f"Sharpe={metrics['sharpe']:>6.3f} "
                f"({elapsed:.1f}s) {params}"
            )
        except Exception as e:
            print(f"  [{i:3d}/{total}] 失败: {e} | {params}")

    # 按年化收益排序
    results.sort(key=lambda x: x["annual_return_pct"], reverse=True)

    # 输出 CSV
    if output_csv:
        _write_csv(results, output_csv)
        print(f"\n结果已保存: {output_csv}")

    # 打印 Top 5
    print(f"\n{'='*70}")
    print(f"Top 5 参数组合 ({strategy_name} + {fill_model_name}):")
    print(f"{'='*70}")
    for i, m in enumerate(results[:5], 1):
        print(
            f"  #{i} 年化={m['annual_return_pct']:>7.2f}% "
            f"回撤={m['max_drawdown_pct']:>6.2f}% "
            f"Sharpe={m['sharpe']:>6.3f} "
            f"Calmar={m['calmar']:>6.3f} "
            f"| {m['params']}"
        )

    return results


def run_compare_all(bars_by_date: dict, fill_model_name: str = "next_bar_open",
                    output_csv: str | None = None) -> list[dict]:
    """用默认参数对比所有策略"""
    strategies = list(STRATEGY_CONFIGS.keys())
    results = []

    print(f"\n策略横向对比 ({fill_model_name})")
    print("=" * 80)

    for name in strategies:
        try:
            metrics = run_single(name, {}, fill_model_name, bars_by_date)
            results.append(metrics)
            print(
                f"  {name:20s} | "
                f"年化={metrics['annual_return_pct']:>7.2f}% "
                f"回撤={metrics['max_drawdown_pct']:>6.2f}% "
                f"Sharpe={metrics['sharpe']:>6.3f} "
                f"Calmar={metrics['calmar']:>6.3f} "
                f"交易={metrics['n_trades']:>5d}"
            )
        except Exception as e:
            print(f"  {name:20s} | 失败: {e}")

    results.sort(key=lambda x: x.get("sharpe", 0), reverse=True)

    if output_csv:
        _write_csv(results, output_csv)
        print(f"\n结果已保存: {output_csv}")

    return results


def run_sweep_all(bars_by_date: dict, fill_model_name: str = "next_bar_open",
                  output_csv: str | None = None) -> list[dict]:
    """对所有有参数网格的策略执行参数扫描"""
    all_results = []
    strategies_with_grids = [s for s in STRATEGY_CONFIGS if s in SWEEP_GRIDS and SWEEP_GRIDS[s]]

    total_combos = sum(len(SWEEP_GRIDS[s]) for s in strategies_with_grids)
    print(f"\n全量参数扫描: {len(strategies_with_grids)} 策略, {total_combos} 总组合")
    print("=" * 80)

    global_idx = 0
    for strategy_name in strategies_with_grids:
        grid = SWEEP_GRIDS[strategy_name]
        print(f"\n--- {strategy_name} ({len(grid)} 组合) ---")

        for i, params in enumerate(grid, 1):
            global_idx += 1
            t0 = time.time()
            try:
                metrics = run_single(strategy_name, params, fill_model_name, bars_by_date)
                all_results.append(metrics)
                elapsed = time.time() - t0
                if i <= 3 or i == len(grid) or i % 50 == 0:
                    print(
                        f"  [{global_idx:4d}/{total_combos}] {strategy_name}#{i} "
                        f"年化={metrics['annual_return_pct']:>7.2f}% "
                        f"Sharpe={metrics['sharpe']:>6.3f} ({elapsed:.1f}s)"
                    )
            except Exception as e:
                if i <= 3:
                    print(f"  [{global_idx:4d}/{total_combos}] 失败: {e}")

    # 按年化收益排序
    all_results.sort(key=lambda x: x["annual_return_pct"], reverse=True)

    if output_csv:
        _write_csv(all_results, output_csv)
        print(f"\n全量结果已保存: {output_csv}")

    # 打印全局 Top 10
    print(f"\n{'='*80}")
    print(f"全局 Top 10 参数组合 ({fill_model_name}):")
    print(f"{'='*80}")
    for i, m in enumerate(all_results[:10], 1):
        print(
            f"  #{i:2d} {m['strategy']:20s} 年化={m['annual_return_pct']:>7.2f}% "
            f"回撤={m['max_drawdown_pct']:>6.2f}% "
            f"Sharpe={m['sharpe']:>6.3f} "
            f"| {m['params']}"
        )

    return all_results


def _write_csv(results: list[dict], path: str):
    """写入 CSV"""
    if not results:
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    keys = list(results[0].keys())
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(results)


# ============================================================================
# 数据加载
# ============================================================================

def load_data(cache_dir: str = "data/cache/cb_tushare",
              start: str = "20220101", end: str = "20250905") -> dict:
    """加载 CB 数据"""
    adapter = CBDataAdapter(cache_dir=cache_dir)
    bars = adapter.load(start, end)
    print(f"数据加载: {len(bars)} 交易日 ({start} ~ {end})")
    return bars


# ============================================================================
# HTML 报告生成
# ============================================================================

def generate_sweep_html_report(results: list[dict], output_path: str):
    """生成参数扫描 HTML 对比报告（Chart.js 可视化）"""
    if not results:
        return

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # 汇总数据
    strategies = sorted(set(r["strategy"] for r in results))
    strategy_best = {}
    for s in strategies:
        s_results = [r for r in results if r["strategy"] == s]
        if s_results:
            best = max(s_results, key=lambda x: x["sharpe"])
            strategy_best[s] = best

    # Top 50 全局
    top50 = results[:50]

    # 颜色池
    colors = [
        "#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
        "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac",
        "#86bcb6", "#8cd17d", "#b6992d", "#499894", "#e49444",
        "#d37295", "#a0cbe8", "#ffbe7d",
    ]

    # 策略对比柱状图数据
    bar_labels = json.dumps([s for s in strategies if s in strategy_best])
    bar_annual = json.dumps([strategy_best[s]["annual_return_pct"] for s in strategies if s in strategy_best])
    bar_sharpe = json.dumps([strategy_best[s]["sharpe"] for s in strategies if s in strategy_best])
    bar_maxdd = json.dumps([strategy_best[s]["max_drawdown_pct"] for s in strategies if s in strategy_best])
    bar_colors = json.dumps([colors[i % len(colors)] for i in range(len(strategies))])

    # 散点图数据（所有组合：年化 vs 回撤）
    scatter_datasets = []
    for idx, s in enumerate(strategies):
        s_results = [r for r in results if r["strategy"] == s]
        points = [{"x": r["max_drawdown_pct"], "y": r["annual_return_pct"]} for r in s_results]
        scatter_datasets.append({
            "label": s,
            "data": points,
            "backgroundColor": colors[idx % len(colors)] + "88",
            "borderColor": colors[idx % len(colors)],
            "pointRadius": 3,
        })
    scatter_json = json.dumps(scatter_datasets)

    # Top 50 表格行
    top50_rows = ""
    for i, r in enumerate(top50, 1):
        status = "🏆" if i <= 3 else ""
        top50_rows += f"""<tr>
            <td>{status}{i}</td>
            <td><strong>{r['strategy']}</strong></td>
            <td style="color:{'#2d7d2d' if r['annual_return_pct']>0 else '#d32f2f'}">{r['annual_return_pct']:.2f}%</td>
            <td>{r['max_drawdown_pct']:.2f}%</td>
            <td>{r['sharpe']:.3f}</td>
            <td>{r['calmar']:.3f}</td>
            <td>{r['n_trades']}</td>
            <td>{r['final_nav']:,.0f}</td>
            <td><code>{r['params']}</code></td>
        </tr>"""

    # 每策略最优推荐
    best_cards = ""
    for s in strategies:
        if s not in strategy_best:
            continue
        b = strategy_best[s]
        s_results = [r for r in results if r["strategy"] == s]
        card_class = "card-excellent" if b["sharpe"] >= 0.8 else ("card-good" if b["sharpe"] >= 0.4 else "card-fair")
        best_cards += f"""
        <div class="strategy-card {card_class}">
            <h3>{s}</h3>
            <div class="metric-row"><span>最优年化</span><span style="color:{'#2d7d2d' if b['annual_return_pct']>0 else '#d32f2f'}">{b['annual_return_pct']:.2f}%</span></div>
            <div class="metric-row"><span>最大回撤</span><span>{b['max_drawdown_pct']:.2f}%</span></div>
            <div class="metric-row"><span>Sharpe</span><span>{b['sharpe']:.3f}</span></div>
            <div class="metric-row"><span>Calmar</span><span>{b['calmar']:.3f}</span></div>
            <div class="metric-row"><span>参数组合数</span><span>{len(s_results)}</span></div>
            <div class="params-box"><code>{b['params']}</code></div>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>可转债策略参数扫描报告</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background:#f5f5f5; color:#333; }}
.container {{ max-width:1400px; margin:0 auto; padding:20px; }}
h1 {{ text-align:center; margin:20px 0; color:#1a237e; }}
h2 {{ margin:30px 0 15px; color:#283593; border-bottom:2px solid #3f51b5; padding-bottom:5px; }}
.summary-bar {{ display:flex; gap:20px; justify-content:center; flex-wrap:wrap; margin:20px 0; }}
.summary-item {{ background:white; padding:15px 25px; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.1); text-align:center; }}
.summary-item .number {{ font-size:28px; font-weight:bold; color:#1565c0; }}
.summary-item .label {{ color:#666; font-size:13px; }}
.chart-container {{ background:white; padding:20px; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.1); margin:20px 0; }}
.charts-row {{ display:grid; grid-template-columns:1fr 1fr; gap:20px; }}
@media (max-width:900px) {{ .charts-row {{ grid-template-columns:1fr; }} }}
.strategy-cards {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(280px, 1fr)); gap:15px; }}
.strategy-card {{ background:white; padding:15px; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.1); border-left:4px solid #ccc; }}
.card-excellent {{ border-left-color:#2d7d2d; }}
.card-good {{ border-left-color:#f9a825; }}
.card-fair {{ border-left-color:#ef6c00; }}
.strategy-card h3 {{ margin-bottom:10px; color:#1a237e; }}
.metric-row {{ display:flex; justify-content:space-between; padding:3px 0; font-size:14px; }}
.params-box {{ margin-top:8px; background:#f5f5f5; padding:6px 8px; border-radius:4px; font-size:11px; overflow-x:auto; }}
table {{ width:100%; border-collapse:collapse; background:white; border-radius:8px; overflow:hidden; box-shadow:0 2px 4px rgba(0,0,0,0.1); }}
th {{ background:#3f51b5; color:white; padding:10px 8px; text-align:left; font-size:13px; position:sticky; top:0; }}
td {{ padding:8px; border-bottom:1px solid #eee; font-size:13px; }}
tr:hover {{ background:#f0f4ff; }}
code {{ font-size:11px; word-break:break-all; }}
.footer {{ text-align:center; color:#999; margin:30px 0; font-size:12px; }}
</style>
</head>
<body>
<div class="container">
<h1>可转债策略参数扫描报告</h1>
<div class="summary-bar">
    <div class="summary-item"><div class="number">{len(strategies)}</div><div class="label">策略数量</div></div>
    <div class="summary-item"><div class="number">{len(results):,}</div><div class="label">参数组合</div></div>
    <div class="summary-item"><div class="number">{results[0]['annual_return_pct']:.1f}%</div><div class="label">最高年化</div></div>
    <div class="summary-item"><div class="number">{results[0]['strategy']}</div><div class="label">最优策略</div></div>
    <div class="summary-item"><div class="number">{results[0]['sharpe']:.3f}</div><div class="label">最高Sharpe</div></div>
</div>

<h2>一、策略最优参数对比</h2>
<div class="charts-row">
<div class="chart-container">
    <canvas id="barChart" height="300"></canvas>
</div>
<div class="chart-container">
    <canvas id="sharpeChart" height="300"></canvas>
</div>
</div>

<h2>二、风险-收益散点图（全部参数组合）</h2>
<div class="chart-container">
    <canvas id="scatterChart" height="400"></canvas>
</div>

<h2>三、各策略最优参数推荐</h2>
<div class="strategy-cards">
{best_cards}
</div>

<h2>四、全局 Top 50 参数组合</h2>
<div style="overflow-x:auto;">
<table>
<thead><tr>
    <th>#</th><th>策略</th><th>年化收益</th><th>最大回撤</th>
    <th>Sharpe</th><th>Calmar</th><th>交易数</th><th>期末净值</th><th>参数</th>
</tr></thead>
<tbody>{top50_rows}</tbody>
</table>
</div>

<p class="footer">
    Aipha-Q 可转债策略参数扫描报告 | 数据期: 2022-2024 | FillModel: next_bar_open |
    生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}
</p>
</div>

<script>
// 年化收益柱状图
new Chart(document.getElementById('barChart'), {{
    type: 'bar',
    data: {{
        labels: {bar_labels},
        datasets: [{{
            label: '最优年化收益 (%)',
            data: {bar_annual},
            backgroundColor: {bar_colors},
        }}]
    }},
    options: {{
        responsive: true,
        plugins: {{ title: {{ display:true, text:'各策略最优年化收益 (%)' }} }},
        scales: {{ y: {{ beginAtZero: true }} }}
    }}
}});

// Sharpe 柱状图
new Chart(document.getElementById('sharpeChart'), {{
    type: 'bar',
    data: {{
        labels: {bar_labels},
        datasets: [
            {{ label: 'Sharpe', data: {bar_sharpe}, backgroundColor: '#4e79a7' }},
            {{ label: '最大回撤 (%)', data: {bar_maxdd}, backgroundColor: '#e15759' }}
        ]
    }},
    options: {{
        responsive: true,
        plugins: {{ title: {{ display:true, text:'Sharpe vs 最大回撤' }} }},
    }}
}});

// 散点图
new Chart(document.getElementById('scatterChart'), {{
    type: 'scatter',
    data: {{ datasets: {scatter_json} }},
    options: {{
        responsive: true,
        plugins: {{ title: {{ display:true, text:'年化收益 vs 最大回撤 (全部参数组合)' }} }},
        scales: {{
            x: {{ title: {{ display:true, text:'最大回撤 (%)' }} }},
            y: {{ title: {{ display:true, text:'年化收益 (%)' }} }}
        }}
    }}
}});
</script>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"HTML 报告已生成: {output_path}")
    print(f"  包含 {len(strategies)} 策略, {len(results)} 参数组合, Top50 明细")



# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="通用可转债策略回测")
    parser.add_argument("--strategy", "-s", type=str, help="策略名称")
    parser.add_argument("--fill-model", "-f", type=str, default="next_bar_open",
                        help="成交模型 (默认 next_bar_open)")
    parser.add_argument("--sweep", action="store_true", help="参数扫描模式")
    parser.add_argument("--sweep-all", action="store_true", help="全量参数扫描所有策略")
    parser.add_argument("--compare-all", action="store_true", help="所有策略对比")
    parser.add_argument("--output", "-o", type=str, default=None, help="输出 CSV 路径")
    parser.add_argument("--html-report", nargs="?", const="auto", default=None,
                        help="输出 HTML 对比报告 (可选指定路径, 不指定则自动生成)")
    parser.add_argument("--start", type=str, default="20220101")
    parser.add_argument("--end", type=str, default="20250905")
    parser.add_argument("--cache-dir", type=str, default="data/cache/cb_tushare")
    args = parser.parse_args()

    # 加载数据
    bars_by_date = load_data(args.cache_dir, args.start, args.end)
    if not bars_by_date:
        print("无数据可回测")
        sys.exit(1)

    results = []

    if args.sweep_all:
        out = args.output or f"results/sweep_all_{args.fill_model}.csv"
        results = run_sweep_all(bars_by_date, args.fill_model, out)
    elif args.compare_all:
        out = args.output or f"results/compare_all_{args.fill_model}.csv"
        # 跑两种成交模型
        run_compare_all(bars_by_date, "next_bar_open", out.replace(".csv", "_nbo.csv"))
        run_compare_all(bars_by_date, "same_bar_close", out.replace(".csv", "_sbc.csv"))
    elif args.sweep:
        if not args.strategy:
            print("参数扫描需指定 --strategy")
            sys.exit(1)
        out = args.output or f"results/sweep_{args.strategy}_{args.fill_model}.csv"
        results = run_sweep(args.strategy, args.fill_model, bars_by_date, out)
    elif args.strategy:
        metrics = run_single(args.strategy, {}, args.fill_model, bars_by_date)
        print_metrics(args.strategy, args.fill_model, {}, metrics)
    else:
        parser.print_help()

    # HTML 报告
    if args.html_report and results:
        report_path = args.html_report
        if report_path == "auto":
            report_path = f"results/sweep_report_{args.fill_model}.html"
        generate_sweep_html_report(results, report_path)
        print(f"\nHTML 报告已保存: {report_path}")


if __name__ == "__main__":
    main()
