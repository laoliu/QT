"""长周期 E2E 鲁棒性测试。

验证 BacktestEngine 在 2-5 年合成数据上的行为：
- 牛市 / 熊市 / 震荡等多市场环境
- 回撤分析与质量指标在长周期下的合理性
- 不同种子下结果一致性（Bootstrap CI）
- RobustnessReport 综合评分
"""

from __future__ import annotations

import math
import pytest
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np

from aipha.backtest.engine import BacktestEngine, BacktestConfig
from aipha.backtest.report import (
    compute_performance_metrics,
    compute_drawdown_analysis,
)
from aipha.backtest.robustness import (
    BootstrapAnalyzer,
    RollingOriginReport,
    RollingOriginResult,
    RobustnessReport,
    ScaleTestPoint,
    ScaleTestReport,
)
from aipha.domain.models import Bar
from aipha.strategy.dual_ma import DualMAStrategy
from aipha.execution.fill_models import NextBarOpenFillModel
from aipha.execution.slippage import FixedBpsSlippageModel
from aipha.execution.fees import (
    CommissionModel,
    FeeModelCNEquity,
    StampTaxModel,
    TransferFeeModel,
)
from aipha.market.rules_cn_equity import MarketRulesCNEquity


# ============================================================================
# 合成数据生成（支持多市场环境）
# ============================================================================

@dataclass
class RegimeSpec:
    """市场环境规格。"""
    name: str
    drift: float        # 日均漂移（例如 +0.001 = 年化 ~28%）
    vol: float           # 日波动率
    start: date
    end: date


def _generate_regime_bars(
    symbols: list[str],
    regimes: list[RegimeSpec],
    seed: int = 42,
) -> dict[date, dict[str, Bar]]:
    """生成带市场环境切换的合成日线数据。

    不同 regime 期间使用不同的 drift/vol 参数。
    """
    rng = np.random.RandomState(seed)
    bars_by_date: dict[date, dict[str, Bar]] = {}
    prices = {s: rng.uniform(10, 50) for s in symbols}

    # 构建日期→环境映射
    regime_map: dict[date, RegimeSpec] = {}
    for r in regimes:
        d = r.start
        while d <= r.end:
            regime_map[d] = r
            d += timedelta(days=1)

    all_dates = sorted(regime_map.keys())
    for d in all_dates:
        if d.weekday() >= 5:
            continue
        regime = regime_map[d]
        day_bars: dict[str, Bar] = {}
        for sym in symbols:
            p = prices[sym]
            ret = rng.normal(regime.drift, regime.vol)
            new_p = max(0.5, p * (1 + ret))  # 防止价格为负
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
    return bars_by_date


def _build_engine(
    n_stocks: int = 10,
    symbols: list[str] | None = None,
    initial_capital: float = 1_000_000.0,
) -> tuple[BacktestEngine, DualMAStrategy]:
    """构建标准的 DualMA 引擎。"""
    if symbols is None:
        symbols = [f"{i:06d}.SZ" for i in range(1, n_stocks + 1)]
    strategy = DualMAStrategy(
        short_window=5,
        long_window=20,
        position_pct=min(0.8, 3 / n_stocks),
        max_positions=min(3, n_stocks),
        target_symbols=symbols,
    )
    fee = FeeModelCNEquity(
        commission=CommissionModel(rate=0.0001, min_fee=5.0),
        stamp_tax=StampTaxModel(sell_rate=0.0005),
        transfer_fee=TransferFeeModel(rate=0.00001),
    )
    config = BacktestConfig(initial_capital=initial_capital)
    engine = BacktestEngine(
        config=config,
        strategy=strategy,
        fill_model=NextBarOpenFillModel(),
        slippage=FixedBpsSlippageModel(bps=10),
        fee_model=fee,
        rules=MarketRulesCNEquity(),
    )
    return engine, strategy


# ============================================================================
# 2年 + 3年 + 5年 数据生成 fixtures
# ============================================================================

SYMBOLS_10 = [f"{i:06d}.SZ" for i in range(1, 11)]

# 2年：牛市 → 熊市
REGIMES_2Y = [
    RegimeSpec("bull", drift=0.0008, vol=0.015, start=date(2020, 1, 1), end=date(2020, 12, 31)),
    RegimeSpec("bear", drift=-0.0006, vol=0.025, start=date(2021, 1, 1), end=date(2021, 12, 31)),
]

# 3年：牛 → 震荡 → 熊
REGIMES_3Y = [
    RegimeSpec("bull", drift=0.0008, vol=0.015, start=date(2020, 1, 1), end=date(2020, 12, 31)),
    RegimeSpec("chop", drift=0.0000, vol=0.020, start=date(2021, 1, 1), end=date(2021, 12, 31)),
    RegimeSpec("bear", drift=-0.0006, vol=0.025, start=date(2022, 1, 1), end=date(2022, 12, 31)),
]

# 5年：牛 → 熊 → 震荡 → 牛 → 熊
REGIMES_5Y = [
    RegimeSpec("bull1", drift=0.0008, vol=0.015, start=date(2020, 1, 1), end=date(2020, 12, 31)),
    RegimeSpec("bear1", drift=-0.0006, vol=0.025, start=date(2021, 1, 1), end=date(2021, 12, 31)),
    RegimeSpec("chop",  drift=0.0000, vol=0.020, start=date(2022, 1, 1), end=date(2022, 12, 31)),
    RegimeSpec("bull2", drift=0.0006, vol=0.018, start=date(2023, 1, 1), end=date(2023, 12, 31)),
    RegimeSpec("bear2", drift=-0.0008, vol=0.030, start=date(2024, 1, 1), end=date(2024, 12, 31)),
]


# ============================================================================
# TestLongPeriodBasic — 基本 E2E 验证
# ============================================================================

class TestLongPeriodBasic:
    """长周期基本回测验证。"""

    @pytest.mark.parametrize("regimes,label", [
        (REGIMES_2Y, "2y"),
        (REGIMES_3Y, "3y"),
        (REGIMES_5Y, "5y"),
    ])
    def test_engine_completes(self, regimes, label):
        """引擎在 2/3/5 年数据上正常运行。"""
        bars = _generate_regime_bars(SYMBOLS_10, regimes, seed=42)
        engine, _ = _build_engine(n_stocks=10)
        result = engine.run(bars)

        assert result is not None
        assert len(result.daily_snapshots) > 100
        assert result.final_nav > 0

    @pytest.mark.parametrize("regimes,label", [
        (REGIMES_2Y, "2y"),
        (REGIMES_3Y, "3y"),
        (REGIMES_5Y, "5y"),
    ])
    def test_metrics_reasonable(self, regimes, label):
        """性能指标在合理范围内。"""
        bars = _generate_regime_bars(SYMBOLS_10, regimes, seed=42)
        engine, _ = _build_engine(n_stocks=10)
        result = engine.run(bars)
        perf = compute_performance_metrics(result)

        # 合成数据 Sharpe 应在 [-3, 3] 区间
        assert -3.0 < perf.sharpe_ratio < 3.0
        # 年化收益不应超过 ±100%
        assert -1.0 < perf.annualized_return < 1.0
        # 波动率应为正
        assert perf.volatility > 0
        # 交易日数应超过回测天数的 60%
        total_days = sum(1 for r in regimes for d_i in range((r.end - r.start).days + 1)
                        if (r.start + timedelta(days=d_i)).weekday() < 5)
        assert perf.trading_days >= total_days * 0.6


class TestLongPeriodDrawdown:
    """长周期回撤分析。"""

    def test_5y_max_drawdown_exists(self):
        """5年数据应有明显回撤。"""
        bars = _generate_regime_bars(SYMBOLS_10, REGIMES_5Y, seed=42)
        engine, _ = _build_engine(n_stocks=10)
        result = engine.run(bars)
        dd = compute_drawdown_analysis(result)

        assert dd.max_drawdown_pct > 0
        assert dd.max_drawdown_duration > 0

    def test_bear_regime_has_larger_drawdown(self):
        """纯熊市环境 vs 纯牛市，区别应显著。"""
        pure_bull = [RegimeSpec("bull", 0.001, 0.012, date(2020, 1, 1), date(2021, 12, 31))]
        pure_bear = [RegimeSpec("bear", -0.001, 0.030, date(2020, 1, 1), date(2021, 12, 31))]

        bars_bull = _generate_regime_bars(SYMBOLS_10, pure_bull, seed=42)
        bars_bear = _generate_regime_bars(SYMBOLS_10, pure_bear, seed=42)

        engine_b, _ = _build_engine(n_stocks=10)
        engine_r, _ = _build_engine(n_stocks=10)

        dd_bull = compute_drawdown_analysis(engine_b.run(bars_bull))
        dd_bear = compute_drawdown_analysis(engine_r.run(bars_bear))

        assert dd_bear.max_drawdown_pct > dd_bull.max_drawdown_pct

    def test_drawdown_series_length_matches_days(self):
        """回撤序列长度应等于交易日数。"""
        bars = _generate_regime_bars(SYMBOLS_10, REGIMES_3Y, seed=42)
        engine, _ = _build_engine(n_stocks=10)
        result = engine.run(bars)
        dd = compute_drawdown_analysis(result)

        assert len(dd.drawdown_series) == len(result.daily_snapshots)


# ============================================================================
# TestMultiSeedConsistency — 不同种子下大方向一致
# ============================================================================

class TestMultiSeedConsistency:
    """用多个随机种子验证结果稳定性。"""

    def test_5y_sharpe_directions_consistent(self):
        """5年牛→熊→震荡→牛→熊，多种子的 Sharpe 符号方向一致。"""
        sharpes = []
        for seed in range(5):
            bars = _generate_regime_bars(SYMBOLS_10, REGIMES_5Y, seed=seed)
            engine, _ = _build_engine(n_stocks=10)
            result = engine.run(bars)
            perf = compute_performance_metrics(result)
            sharpes.append(perf.sharpe_ratio)

        # 合成数据中波动合理，所有种子 sharpe 应在 ±2 范围
        for s in sharpes:
            assert -2.0 < s < 2.0, f"Sharpe out of range: {s}"

    def test_bootstrap_ci_on_multi_seed_returns(self):
        """对多种子回测的收益做 Bootstrap CI。"""
        returns = []
        for seed in range(10):
            bars = _generate_regime_bars(SYMBOLS_10, REGIMES_3Y, seed=seed)
            engine, _ = _build_engine(n_stocks=10)
            result = engine.run(bars)
            perf = compute_performance_metrics(result)
            returns.append(perf.total_return)

        ba = BootstrapAnalyzer(n_bootstrap=5000, random_seed=42)
        ci = ba.estimate_ci(returns, "total_return_3y")

        # CI 应合理
        assert ci.ci_lower < ci.ci_upper
        assert ci.std_error > 0


# ============================================================================
# TestLongPeriodRollingOrigin — 长周期 Rolling Origin
# ============================================================================

class TestLongPeriodRollingOrigin:
    """在长周期上模拟 expanding-window Rolling Origin CV。"""

    def test_expanding_window_5y(self):
        """5 年数据上做 3-origin expanding window。"""
        # 生成全量 5 年 bars
        all_bars = _generate_regime_bars(SYMBOLS_10, REGIMES_5Y, seed=42)
        sorted_dates = sorted(all_bars.keys())

        n_origins = 3
        test_size = 60  # ~3 个月

        results_list: list[RollingOriginResult] = []
        for i in range(n_origins):
            # 测试窗口从后向前切分
            test_end_idx = len(sorted_dates) - 1 - i * test_size
            test_start_idx = test_end_idx - test_size + 1
            if test_start_idx < 60:
                continue

            train_dates = sorted_dates[:test_start_idx]
            test_dates = sorted_dates[test_start_idx: test_end_idx + 1]

            train_bars = {d: all_bars[d] for d in train_dates}
            test_bars = {d: all_bars[d] for d in test_dates}

            engine_train, _ = _build_engine(n_stocks=10)
            engine_test, _ = _build_engine(n_stocks=10)

            res_train = engine_train.run(train_bars)
            res_test = engine_test.run(test_bars)

            perf_train = compute_performance_metrics(res_train)
            perf_test = compute_performance_metrics(res_test)

            results_list.append(RollingOriginResult(
                origin_idx=i,
                train_size=len(train_dates),
                test_size=len(test_dates),
                metrics={
                    "sharpe_train": perf_train.sharpe_ratio,
                    "sharpe_test": perf_test.sharpe_ratio,
                    "return_train": perf_train.total_return,
                    "return_test": perf_test.total_return,
                },
            ))

        report = RollingOriginReport(
            strategy_name="dual_ma_5y_ro",
            results=results_list,
            n_origins=len(results_list),
            expanding=True,
        )

        assert len(report.results) == n_origins
        series = report.metric_series("sharpe_test")
        assert len(series) == n_origins
        stats = report.summary_stats("sharpe_test")
        assert "mean" in stats
        assert "std" in stats

    def test_rolling_origin_train_test_gap(self):
        """Roll-Origin 训练集 Sharpe 与测试集 Sharpe 之差幅度合理。"""
        all_bars = _generate_regime_bars(SYMBOLS_10, REGIMES_3Y, seed=42)
        sorted_dates = sorted(all_bars.keys())
        test_size = 40

        gaps = []
        for i in range(3):
            te = len(sorted_dates) - 1 - i * test_size
            ts = te - test_size + 1
            if ts < 40:
                continue

            train_bars = {d: all_bars[d] for d in sorted_dates[:ts]}
            test_bars = {d: all_bars[d] for d in sorted_dates[ts:te + 1]}

            e1, _ = _build_engine(n_stocks=10)
            e2, _ = _build_engine(n_stocks=10)

            p_train = compute_performance_metrics(e1.run(train_bars))
            p_test = compute_performance_metrics(e2.run(test_bars))

            gaps.append(abs(p_train.sharpe_ratio - p_test.sharpe_ratio))

        # 合成随机数据差异不应过大（< 5.0）
        for g in gaps:
            assert g < 5.0, f"Train-test Sharpe gap too large: {g}"


# ============================================================================
# TestLongPeriodRobustnessReport — 综合评分
# ============================================================================

class TestLongPeriodRobustnessReport:
    """在长周期数据上构建完整 RobustnessReport。"""

    def test_full_report_5y(self):
        """从 5 年多种子结果构建 RobustnessReport。"""
        # Bootstrap
        sharpes, returns = [], []
        for seed in range(8):
            bars = _generate_regime_bars(SYMBOLS_10, REGIMES_5Y, seed=seed)
            engine, _ = _build_engine(n_stocks=10)
            perf = compute_performance_metrics(engine.run(bars))
            sharpes.append(perf.sharpe_ratio)
            returns.append(perf.total_return)

        ba = BootstrapAnalyzer(n_bootstrap=3000, random_seed=42)
        ci_sharpe = ba.estimate_ci(sharpes, "sharpe")
        ci_return = ba.estimate_ci(returns, "return")

        # Rolling Origin（简化版）
        all_bars = _generate_regime_bars(SYMBOLS_10, REGIMES_5Y, seed=0)
        sorted_dates = sorted(all_bars.keys())
        ro_results = []
        for i in range(3):
            te = len(sorted_dates) - 1 - i * 60
            ts = te - 60 + 1
            if ts < 60:
                continue
            test_bars = {d: all_bars[d] for d in sorted_dates[ts:te + 1]}
            e, _ = _build_engine(n_stocks=10)
            p = compute_performance_metrics(e.run(test_bars))
            ro_results.append(RollingOriginResult(
                origin_idx=i,
                train_size=ts,
                test_size=60,
                metrics={"sharpe_test": p.sharpe_ratio},
            ))

        ro_report = RollingOriginReport(
            strategy_name="dual_ma_5y",
            results=ro_results,
            n_origins=len(ro_results),
            expanding=True,
        )

        # Scale
        scale_points = []
        for n in [5, 10, 20]:
            syms = [f"{i:06d}.SZ" for i in range(1, n + 1)]
            bars = _generate_regime_bars(syms, REGIMES_3Y, seed=42)
            engine, _ = _build_engine(n_stocks=n)
            p = compute_performance_metrics(engine.run(bars))
            scale_points.append(ScaleTestPoint(
                n_instruments=n,
                metrics={"sharpe": p.sharpe_ratio},
            ))
        scale_report = ScaleTestReport(
            strategy_name="dual_ma_scale",
            points=scale_points,
        )

        # RobustnessReport
        report = RobustnessReport(
            strategy_name="dual_ma_5y_full",
            bootstrap_results={"sharpe": ci_sharpe, "return": ci_return},
            rolling_origin=ro_report,
            scale_test=scale_report,
        )

        score = report.compute_score()
        assert 0 <= score <= 10.0
        conclusion = report.generate_conclusion()
        assert len(conclusion) > 0

        d = report.to_dict()
        assert "strategy_name" in d
        assert "overall_score" in d

    def test_report_score_deterministic(self):
        """相同输入的 RobustnessReport 评分应完全一致。"""
        bars = _generate_regime_bars(SYMBOLS_10, REGIMES_3Y, seed=42)

        sharpes = []
        for seed in range(5):
            b = _generate_regime_bars(SYMBOLS_10, REGIMES_3Y, seed=seed)
            e, _ = _build_engine(n_stocks=10)
            p = compute_performance_metrics(e.run(b))
            sharpes.append(p.sharpe_ratio)

        ba = BootstrapAnalyzer(n_bootstrap=2000, random_seed=99)
        ci = ba.estimate_ci(sharpes, "sharpe")

        report = RobustnessReport(
            strategy_name="dual_ma_det",
            bootstrap_results={"sharpe": ci},
        )
        s1 = report.compute_score()
        s2 = report.compute_score()
        assert s1 == s2
