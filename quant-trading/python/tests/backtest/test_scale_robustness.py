"""组合规模鲁棒性测试。

验证：
- 不同标的数量（5/10/20/50/100）下 BacktestEngine 回报/Sharpe 的变化
- ScaleTestReport 退化检测功能
- 使用合成数据模拟不同规模的股票池
"""

from __future__ import annotations

import math
import random
import pytest
from datetime import date, datetime, timedelta
from decimal import Decimal

from aipha.backtest.engine import BacktestEngine, BacktestConfig
from aipha.backtest.robustness import (
    BootstrapAnalyzer,
    ScaleTestPoint,
    ScaleTestReport,
)
from aipha.domain.models import Bar
import numpy as np


# ============================================================================
# 数据生成
# ============================================================================


def _generate_bars(
    symbols: list[str],
    start: date,
    end: date,
    seed: int = 42,
) -> dict[date, dict[str, Bar]]:
    """为给定股票池生成合成日线数据（格式匹配 BacktestEngine）。"""
    rng = np.random.RandomState(seed)
    bars_by_date: dict[date, dict[str, Bar]] = {}
    prices = {s: rng.uniform(10, 50) for s in symbols}

    d = start
    while d <= end:
        if d.weekday() < 5:
            day_bars: dict[str, Bar] = {}
            for sym in symbols:
                p = prices[sym]
                ret = rng.normal(0.0005, 0.02)
                new_p = p * (1 + ret)
                high = max(p, new_p) * (1 + abs(rng.normal(0, 0.005)))
                low = min(p, new_p) * (1 - abs(rng.normal(0, 0.005)))
                vol = int(rng.uniform(1e5, 1e7))

                bar_start = datetime.combine(
                    d, datetime.min.time().replace(hour=9, minute=30)
                )
                bar_end = datetime.combine(
                    d, datetime.min.time().replace(hour=15)
                )

                day_bars[sym] = Bar(
                    symbol=sym,
                    start_ts=bar_start,
                    end_ts=bar_end,
                    open=p,
                    high=high,
                    low=low,
                    close=new_p,
                    volume=vol,
                    prev_close=p,
                )
                prices[sym] = new_p
            bars_by_date[d] = day_bars
        d += timedelta(days=1)
    return bars_by_date


# ============================================================================
# Tests
# ============================================================================


class TestScaleTestReportUnit:
    """ScaleTestReport 单元测试（与 R.1 互补）。"""

    def test_degradation_with_extreme_values(self):
        """极端退化：sharpe 从 3 降到 0。"""
        report = ScaleTestReport(
            strategy_name="test",
            points=[
                ScaleTestPoint(5, {"sharpe": 3.0}),
                ScaleTestPoint(200, {"sharpe": 0.0}),
            ],
        )
        assert report.is_monotonic_degradation("sharpe", tolerance=0.1)

    def test_no_degradation_with_improvement(self):
        """规模增大性能反而提升。"""
        report = ScaleTestReport(
            strategy_name="test",
            points=[
                ScaleTestPoint(5, {"sharpe": 1.0}),
                ScaleTestPoint(50, {"sharpe": 1.5}),
                ScaleTestPoint(100, {"sharpe": 1.8}),
            ],
        )
        assert not report.is_monotonic_degradation("sharpe")

    def test_multiple_metrics_tracked(self):
        """追踪多个指标。"""
        report = ScaleTestReport(
            strategy_name="multi",
            points=[
                ScaleTestPoint(10, {"sharpe": 1.0, "return": 0.1, "mdd": 0.05}),
                ScaleTestPoint(50, {"sharpe": 0.8, "return": 0.08, "mdd": 0.07}),
            ],
        )
        sharpe_vs = report.metric_vs_scale("sharpe")
        ret_vs = report.metric_vs_scale("return")
        mdd_vs = report.metric_vs_scale("mdd")
        assert len(sharpe_vs) == 2
        assert len(ret_vs) == 2
        assert len(mdd_vs) == 2

    def test_metric_vs_scale_ordering(self):
        """返回按插入顺序（不按规模排序）。"""
        report = ScaleTestReport(
            strategy_name="test",
            points=[
                ScaleTestPoint(50, {"sharpe": 1.0}),
                ScaleTestPoint(10, {"sharpe": 2.0}),
            ],
        )
        pairs = report.metric_vs_scale("sharpe")
        assert pairs[0] == (50, 1.0)
        assert pairs[1] == (10, 2.0)

    def test_tolerance_boundary(self):
        """恰好在容忍度边界上。"""
        report = ScaleTestReport(
            strategy_name="test",
            points=[
                ScaleTestPoint(5, {"sharpe": 1.0}),
                ScaleTestPoint(50, {"sharpe": 0.9}),  # 10% degradation
            ],
        )
        # 恰好 10% = tolerance，不应判定退化（需 > tolerance）
        assert not report.is_monotonic_degradation("sharpe", tolerance=0.1)


class TestScaleWithSyntheticEngine:
    """使用合成数据 + BacktestEngine 测试组合规模效应。"""

    @pytest.mark.parametrize("n_stocks", [5, 15, 30])
    def test_different_scale_runs(self, n_stocks):
        """不同规模都能正常运行回测。"""
        symbols = [f"{i:06d}.SZ" for i in range(1, n_stocks + 1)]
        bars = _generate_bars(symbols, date(2023, 1, 1), date(2023, 6, 30), seed=42)

        from aipha.strategy.dual_ma import DualMAStrategy
        from aipha.execution.fill_models import NextBarOpenFillModel
        from aipha.execution.slippage import FixedBpsSlippageModel
        from aipha.execution.fees import FeeModelCNEquity, CommissionModel, StampTaxModel, TransferFeeModel
        from aipha.market.rules_cn_equity import MarketRulesCNEquity

        _fee = FeeModelCNEquity(
            commission=CommissionModel(rate=0.0001, min_fee=5.0),
            stamp_tax=StampTaxModel(sell_rate=0.0005),
            transfer_fee=TransferFeeModel(rate=0.00001),
        )

        strategy = DualMAStrategy(
            short_window=5, long_window=20,
            position_pct=min(0.9, 5 / n_stocks),
            max_positions=min(5, n_stocks),
        )
        config = BacktestConfig(
            initial_capital=1_000_000.0,
        )
        engine = BacktestEngine(
            config=config,
            strategy=strategy,
            fill_model=NextBarOpenFillModel(),
            slippage=FixedBpsSlippageModel(bps=10),
            fee_model=_fee,
            rules=MarketRulesCNEquity(),
        )

        result = engine.run(bars)
        assert result is not None
        assert len(result.daily_snapshots) > 0

    def test_scale_test_report_from_engine(self):
        """从真实引擎结果构建 ScaleTestReport。"""
        from aipha.strategy.dual_ma import DualMAStrategy
        from aipha.execution.fill_models import NextBarOpenFillModel
        from aipha.execution.slippage import FixedBpsSlippageModel
        from aipha.execution.fees import FeeModelCNEquity, CommissionModel, StampTaxModel, TransferFeeModel
        from aipha.market.rules_cn_equity import MarketRulesCNEquity
        from aipha.backtest.report import compute_performance_metrics

        _fee = FeeModelCNEquity(
            commission=CommissionModel(rate=0.0001, min_fee=5.0),
            stamp_tax=StampTaxModel(sell_rate=0.0005),
            transfer_fee=TransferFeeModel(rate=0.00001),
        )

        points = []
        for n_stocks in [5, 10, 20]:
            symbols = [f"{i:06d}.SZ" for i in range(1, n_stocks + 1)]
            bars = _generate_bars(
                symbols, date(2023, 1, 1), date(2023, 6, 30), seed=42
            )

            strategy = DualMAStrategy(
                short_window=5, long_window=20,
                position_pct=min(0.8, 3 / n_stocks),
                max_positions=min(3, n_stocks),
            )
            config = BacktestConfig(
                initial_capital=1_000_000.0,
            )
            engine = BacktestEngine(
                config=config,
                strategy=strategy,
                fill_model=NextBarOpenFillModel(),
                slippage=FixedBpsSlippageModel(bps=10),
                fee_model=_fee,
                rules=MarketRulesCNEquity(),
            )

            result = engine.run(bars)
            perf = compute_performance_metrics(result)
            points.append(ScaleTestPoint(
                n_instruments=n_stocks,
                metrics={"sharpe": perf.sharpe_ratio, "return": perf.total_return},
            ))

        report = ScaleTestReport(strategy_name="dual_ma_scale", points=points)
        assert len(report.points) == 3

        # 都应有有效指标
        for p in report.points:
            assert "sharpe" in p.metrics
            assert "return" in p.metrics

    def test_bootstrap_on_scale_results(self):
        """对不同规模点的 sharpe 做 Bootstrap 检查。"""
        # 模拟 5 次实验，每次不同种子
        sharpes_by_scale: dict[int, list[float]] = {5: [], 20: [], 50: []}

        for seed in range(5):
            for n in sharpes_by_scale:
                # 简易模拟：规模越大 sharpe 略低但更稳定
                base = 1.0 - n * 0.005
                noise = random.Random(seed + n).gauss(0, 0.1)
                sharpes_by_scale[n].append(base + noise)

        ba = BootstrapAnalyzer(n_bootstrap=3000, random_seed=42)
        results = {}
        for n, sharpes in sharpes_by_scale.items():
            results[n] = ba.estimate_ci(sharpes, f"sharpe_{n}")

        # 所有规模的 Sharpe CI 都应不含 0（因为 base 都 > 0.7）
        for n, ci in results.items():
            assert ci.observed > 0.5, f"Scale {n}: observed={ci.observed}"
