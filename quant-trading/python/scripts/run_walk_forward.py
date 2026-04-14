"""Walk-Forward 滚动前推验证。

使用滚动窗口对 3 个高潜力策略及其组合进行前推验证：
- IS 窗口训练/评估期，OOS 窗口检验期
- 默认：IS=3年，OOS=1年，步进=1年
- 输出每个 OOS 窗口的策略和组合表现

用法：
    python scripts/run_walk_forward.py
"""
from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

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
from aipha.market.rules_convertible_bond import MarketRulesConvertibleBond

# --- Import strategies ---
import aipha.strategy.small_size  # noqa: F401
import aipha.strategy.new_bond  # noqa: F401
import aipha.strategy.new_small  # noqa: F401
from aipha.strategy.small_size import SmallSizeStrategy, SmallSizeConfig
from aipha.strategy.new_bond import NewBondStrategy, NewBondConfig
from aipha.strategy.new_small import NewSmallStrategy, NewSmallConfig

# ============================================================================
# 策略配置（与 run_cb_portfolio.py 保持一致）
# ============================================================================

STRATEGIES = {
    "small_size": (
        SmallSizeStrategy,
        SmallSizeConfig,
        {
            "top_k": 20,
            "rebalance_days": 1,
            "max_price": 115.0,
            "max_issue_size": 60000.0,
            "min_issue_size": 3000.0,
            "stop_loss_pct": 0.15,
            "buffer_k": 5,
        },
    ),
    "new_small": (
        NewSmallStrategy,
        NewSmallConfig,
        {
            "top_k": 8,
            "rebalance_days": 3,
            "max_age_days": 90,
            "max_issue_size": 60000.0,
            "max_price": 140.0,
            "stop_loss_pct": 0.10,
        },
    ),
    "new_bond": (
        NewBondStrategy,
        NewBondConfig,
        {
            "top_k": 10,
            "rebalance_days": 5,
            "max_age_days": 60,
            "max_price": 115.0,
            "min_age_days": 3,
            "stop_loss_pct": 0.15,
            "buffer_k": 3,
        },
    ),
}

# Walk-Forward 窗口配置
IS_YEARS = 3
OOS_YEARS = 1
START_YEAR = 2017
END_YEAR = 2024


def build_engine(strategy, capital=500_000.0):
    """构建回测引擎。"""
    bt_config = BacktestConfig(
        initial_capital=capital,
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
    rules = MarketRulesConvertibleBond(
        lot_size=bt_config.lot_size,
        t_plus=0,
        commission_rate=Decimal("0.0001"),
        commission_min=Decimal("5"),
        stamp_tax_rate=Decimal("0"),
    )
    return BacktestEngine(
        config=bt_config,
        strategy=strategy,
        fill_model=NextBarOpenFillModel(),
        slippage=FixedBpsSlippageModel(bps=bt_config.slippage_bps),
        fee_model=fee_model,
        rules=rules,
    )


def compute_metrics(result: BacktestResult) -> dict:
    """从 BacktestResult 计算核心指标。"""
    initial = result.initial_capital
    final = result.final_nav
    total_return = (final - initial) / initial
    n_days = len(result.daily_snapshots)
    n_years = n_days / 252.0

    annual_return = (
        (1 + total_return) ** (1 / n_years) - 1
        if n_years > 0 and total_return > -1
        else total_return
    )

    peak = initial
    max_dd = 0.0
    for snap in result.daily_snapshots:
        if snap.nav > peak:
            peak = snap.nav
        dd = (peak - snap.nav) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    returns = [s.daily_return for s in result.daily_snapshots]
    if len(returns) > 1:
        avg_r = sum(returns) / len(returns)
        std_r = (sum((r - avg_r) ** 2 for r in returns) / (len(returns) - 1)) ** 0.5  # ddof=1 §13.2
        sharpe = (avg_r * 252 - 0.02) / (std_r * 252**0.5) if std_r > 0 else 0
    else:
        sharpe = 0

    calmar = annual_return / max_dd if max_dd > 0 else 0

    return {
        "annual_return_pct": round(annual_return * 100, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "sharpe": round(sharpe, 3),
        "calmar": round(calmar, 3),
        "n_trades": result.total_trades,
        "n_days": n_days,
    }


def portfolio_metrics_from_navs(
    strategy_navs: dict[str, dict[date, float]],
) -> dict:
    """从多策略 NAV 字典计算等权组合指标。"""
    all_dates = sorted(
        set().union(*[set(d.keys()) for d in strategy_navs.values()])
    )
    if not all_dates:
        return {"annual_return_pct": 0, "max_drawdown_pct": 0, "sharpe": 0}

    n_strats = len(strategy_navs)
    portfolio_nav = []
    for d in all_dates:
        nav = sum(strategy_navs[s].get(d, 1.0) for s in strategy_navs) / n_strats
        portfolio_nav.append(nav)

    portfolio_nav = np.array(portfolio_nav)
    total_return = portfolio_nav[-1] / portfolio_nav[0] - 1
    n_years = len(portfolio_nav) / 252.0
    annual_return = (
        (1 + total_return) ** (1 / n_years) - 1
        if n_years > 0 and total_return > -1
        else total_return
    )

    peak = portfolio_nav[0]
    max_dd = 0.0
    for v in portfolio_nav:
        if v > peak:
            peak = v
        dd = (peak - v) / peak
        if dd > max_dd:
            max_dd = dd

    returns = np.diff(portfolio_nav) / portfolio_nav[:-1]
    if len(returns) > 1:
        avg_r = float(np.mean(returns))
        std_r = float(np.std(returns, ddof=1))  # §13.2 样本标准差
        sharpe = (avg_r * 252 - 0.02) / (std_r * 252**0.5) if std_r > 0 else 0
    else:
        sharpe = 0

    calmar = annual_return / max_dd if max_dd > 0 else 0

    return {
        "annual_return_pct": round(annual_return * 100, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "sharpe": round(sharpe, 3),
        "calmar": round(calmar, 3),
    }


def main():
    # Load all data
    adapter = CBDataAdapter(cache_dir="data/cache/cb_tushare")
    all_bars = adapter.load("20170101", "20241231")
    all_dates = sorted(all_bars.keys())
    print(f"数据: {len(all_dates)} 交易日, {all_dates[0]} ~ {all_dates[-1]}")

    # Generate walk-forward windows
    windows = []
    for oos_start_year in range(START_YEAR + IS_YEARS, END_YEAR + 1, OOS_YEARS):
        is_start = date(oos_start_year - IS_YEARS, 1, 1)
        is_end = date(oos_start_year - 1, 12, 31)
        oos_start = date(oos_start_year, 1, 1)
        oos_end = date(min(oos_start_year + OOS_YEARS - 1, END_YEAR), 12, 31)
        windows.append((is_start, is_end, oos_start, oos_end))

    print(f"\nWalk-Forward 窗口: {len(windows)} 个")
    print(f"  IS={IS_YEARS}年, OOS={OOS_YEARS}年")

    # 汇总 OOS 表现
    oos_results = {s: [] for s in STRATEGIES}
    oos_portfolio_results = []

    for wi, (is_start, is_end, oos_start, oos_end) in enumerate(windows):
        print(f"\n{'='*70}")
        print(f"窗口 {wi+1}: IS={is_start}~{is_end}  OOS={oos_start}~{oos_end}")
        print(f"{'='*70}")

        # IS period
        is_bars = {d: v for d, v in all_bars.items() if is_start <= d <= is_end}
        oos_bars = {d: v for d, v in all_bars.items() if oos_start <= d <= oos_end}

        if not oos_bars:
            print("  OOS 无数据，跳过")
            continue

        print(f"  IS: {len(is_bars)} 天, OOS: {len(oos_bars)} 天")

        # Run each strategy on IS and OOS
        strategy_navs_oos = {}

        for name, (cls, cfg_cls, params) in STRATEGIES.items():
            # IS
            cfg = cfg_cls(**params)
            strat_is = cls(config=cfg)
            engine_is = build_engine(strat_is)
            result_is = engine_is.run(is_bars)
            m_is = compute_metrics(result_is)

            # OOS
            cfg_oos = cfg_cls(**params)
            strat_oos = cls(config=cfg_oos)
            engine_oos = build_engine(strat_oos)
            result_oos = engine_oos.run(oos_bars)
            m_oos = compute_metrics(result_oos)

            oos_results[name].append(m_oos)

            # Extract OOS NAV for portfolio
            nav_dict = {}
            for snap in result_oos.daily_snapshots:
                nav_dict[snap.trade_date] = snap.nav / result_oos.initial_capital
            strategy_navs_oos[name] = nav_dict

            print(f"  {name:15s}  IS: AR={m_is['annual_return_pct']:6.1f}% "
                  f"SR={m_is['sharpe']:6.3f}  |  "
                  f"OOS: AR={m_oos['annual_return_pct']:6.1f}% "
                  f"SR={m_oos['sharpe']:6.3f} MDD={m_oos['max_drawdown_pct']:5.1f}%")

        # Portfolio OOS
        pm = portfolio_metrics_from_navs(strategy_navs_oos)
        oos_portfolio_results.append(pm)
        print(f"  {'EW Portfolio':15s}  "
              f"{'':>30s}  |  "
              f"OOS: AR={pm['annual_return_pct']:6.1f}% "
              f"SR={pm['sharpe']:6.3f} MDD={pm['max_drawdown_pct']:5.1f}%")

    # Summary
    print(f"\n{'='*70}")
    print("Walk-Forward 汇总 (OOS 各窗口平均)")
    print(f"{'='*70}")

    for name in STRATEGIES:
        if not oos_results[name]:
            continue
        avg_ar = np.mean([m["annual_return_pct"] for m in oos_results[name]])
        avg_sr = np.mean([m["sharpe"] for m in oos_results[name]])
        avg_mdd = np.mean([m["max_drawdown_pct"] for m in oos_results[name]])
        min_sr = min(m["sharpe"] for m in oos_results[name])
        max_mdd = max(m["max_drawdown_pct"] for m in oos_results[name])
        print(f"  {name:15s}  Avg AR={avg_ar:6.1f}%  Avg SR={avg_sr:6.3f}  "
              f"Avg MDD={avg_mdd:5.1f}%  Min SR={min_sr:6.3f}  Max MDD={max_mdd:5.1f}%")

    if oos_portfolio_results:
        avg_ar = np.mean([m["annual_return_pct"] for m in oos_portfolio_results])
        avg_sr = np.mean([m["sharpe"] for m in oos_portfolio_results])
        avg_mdd = np.mean([m["max_drawdown_pct"] for m in oos_portfolio_results])
        min_sr = min(m["sharpe"] for m in oos_portfolio_results)
        max_mdd = max(m["max_drawdown_pct"] for m in oos_portfolio_results)
        print(f"  {'EW Portfolio':15s}  Avg AR={avg_ar:6.1f}%  Avg SR={avg_sr:6.3f}  "
              f"Avg MDD={avg_mdd:5.1f}%  Min SR={min_sr:6.3f}  Max MDD={max_mdd:5.1f}%")

    print("\n完成。")


if __name__ == "__main__":
    main()
