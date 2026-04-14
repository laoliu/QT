"""Walk-Forward + BacktestEngine 集成 E2E 测试

验证 WalkForwardValidator / purged_walk_forward_validate 能驱动
真实 BacktestEngine 完成多 fold 样本外验证，全流程闭环：
  data(合成) → split → engine.run → metrics → ValidationReport → judge

使用合成 equity 数据，无需 TuShare Token。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
import pytest

from aipha.backtest.engine import BacktestConfig, BacktestEngine, BacktestResult
from aipha.backtest.report import (
    compute_drawdown_analysis,
    compute_performance_metrics,
)
from aipha.backtest.validation import (
    WalkForwardValidator,
    WalkForwardThresholds,
    TimeSeriesSplitter,
    PurgedWalkForwardSplitter,
    ValidationReport,
    judge_walk_forward,
    walk_forward_validate,
    purged_walk_forward_validate,
    compute_wf_score,
    auto_n_splits,
)
from aipha.domain.models import Bar
from aipha.execution.fees import (
    CommissionModel,
    FeeModelCNEquity,
    StampTaxModel,
    TransferFeeModel,
)
from aipha.execution.fill_models import NextBarOpenFillModel
from aipha.execution.slippage import FixedBpsSlippageModel
from aipha.market.rules_cn_equity import MarketRulesCNEquity
from aipha.strategy.registry import create_strategy_from_config


# ============================================================================
# 合成数据生成（长时间序列，覆盖 WF 多 fold）
# ============================================================================


def _generate_long_equity_bars(
    n_stocks: int = 10,
    n_days: int = 500,
    start_date: date | None = None,
    seed: int = 123,
) -> dict[date, dict[str, Bar]]:
    """生成含 equity 元数据的长期合成 Bar 数据

    Args:
        n_stocks: 股票数量
        n_days: 交易日数量（跳过周末后的自然日数约 n_days * 7/5）
        start_date: 起始日期
        seed: 随机种子
    """
    rng = np.random.RandomState(seed)
    if start_date is None:
        start_date = date(2021, 1, 4)

    symbols = [f"{600000 + i:06d}.SH" for i in range(n_stocks)]
    prices = {s: rng.uniform(15, 60) for s in symbols}
    circ_mvs = {s: rng.uniform(500_000, 3_000_000) for s in symbols}

    industries = ["银行", "白酒", "医药", "电子", "新能源", "地产", "钢铁"]

    bars_by_date: dict[date, dict[str, Bar]] = {}
    current = start_date
    day_count = 0

    while day_count < n_days:
        # 跳过周末
        while current.weekday() >= 5:
            current += timedelta(days=1)

        day_bars: dict[str, Bar] = {}
        for i, sym in enumerate(symbols):
            ret = rng.normal(0.0005, 0.02)
            prices[sym] *= 1 + ret
            p = prices[sym]

            bar_start = datetime.combine(
                current, datetime.min.time().replace(hour=9, minute=30)
            )
            bar_end = datetime.combine(
                current, datetime.min.time().replace(hour=15)
            )

            volume = int(rng.uniform(1_000_000, 15_000_000))
            amount = volume * p / 10000

            meta = {
                "circ_mv": circ_mvs[sym] * (1 + rng.normal(0, 0.005)),
                "total_mv": circ_mvs[sym] * 1.3,
                "pe_ttm": rng.uniform(8, 50),
                "pb": rng.uniform(0.6, 6),
                "roe": rng.uniform(3, 20),
                "turnover_rate": rng.uniform(0.8, 6),
                "amount": amount,
                "list_date": "20150101",
                "name": f"测试股票{i}",
                "industry": industries[i % len(industries)],
                "dt_netprofit_yoy": rng.uniform(-15, 40),
                "sue": rng.normal(0, 1.5),
                "dv_ratio": rng.uniform(0.0, 8.0),
                "debt_to_assets": rng.uniform(15, 70),
                "grossprofit_margin": rng.uniform(10, 50),
            }

            day_bars[sym] = Bar(
                symbol=sym,
                start_ts=bar_start,
                end_ts=bar_end,
                open=p * (1 + rng.normal(0, 0.003)),
                high=p * (1 + abs(rng.normal(0, 0.012))),
                low=p * (1 - abs(rng.normal(0, 0.012))),
                close=p,
                volume=volume,
                prev_close=p / (1 + ret),
                metadata=meta,
            )

        bars_by_date[current] = day_bars
        current += timedelta(days=1)
        day_count += 1

    return bars_by_date


# ============================================================================
# 工具函数：BacktestEngine run_fn 适配器
# ============================================================================


def _make_engine_run_fn(
    all_bars: dict[date, dict[str, Bar]],
    strategy_name: str = "multifactor_enhance",
    strategy_params: dict[str, Any] | None = None,
) -> callable:
    """创建适配 WalkForwardValidator 的 run_fn

    返回的 callable 签名: (start_date, end_date) -> dict[str, float]
    其中 dict 含 return, sharpe, max_drawdown
    """
    if strategy_params is None:
        strategy_params = {"top_k": 5, "rebalance_days": 10}

    config = BacktestConfig(
        initial_capital=1_000_000,
        lot_size=100,
        commission_rate=0.0001,
        commission_min=5.0,
        stamp_tax_rate=0.0005,
        transfer_fee_rate=0.00001,
        slippage_bps=5.0,
        t_plus_n=1,
    )

    def run_fn(start: date, end: date) -> dict[str, float]:
        # 筛选日期范围内的 bars
        filtered = {
            d: bars
            for d, bars in all_bars.items()
            if start <= d <= end
        }

        if len(filtered) < 5:
            return {"return": 0.0, "sharpe": 0.0, "max_drawdown": 0.0}

        strategy = create_strategy_from_config({
            "name": strategy_name,
            "params": strategy_params,
        })

        engine = BacktestEngine(
            config=config,
            strategy=strategy,
            fill_model=NextBarOpenFillModel(),
            slippage=FixedBpsSlippageModel(bps=5),
            fee_model=FeeModelCNEquity(
                commission=CommissionModel(rate=0.0001, min_fee=5.0),
                stamp_tax=StampTaxModel(sell_rate=0.0005),
                transfer_fee=TransferFeeModel(rate=0.00001),
            ),
            rules=MarketRulesCNEquity(),
        )

        result = engine.run(filtered)

        # 从 BacktestResult 提取指标
        perf = compute_performance_metrics(result)
        dd = compute_drawdown_analysis(result)

        return {
            "return": perf.total_return,
            "sharpe": perf.sharpe_ratio,
            "max_drawdown": dd.max_drawdown_pct,
        }

    return run_fn


# ============================================================================
# 测试类
# ============================================================================


class TestWalkForwardEngineIntegration:
    """WF + BacktestEngine 集成测试"""

    @pytest.fixture(scope="class")
    def long_bars(self) -> dict[date, dict[str, Bar]]:
        """500 个交易日的合成数据（约 2 年）"""
        return _generate_long_equity_bars(n_stocks=10, n_days=500, seed=123)

    @pytest.fixture(scope="class")
    def bar_date_range(self, long_bars) -> tuple[date, date]:
        sorted_dates = sorted(long_bars.keys())
        return sorted_dates[0], sorted_dates[-1]

    def test_walk_forward_with_real_engine_basic(
        self, long_bars, bar_date_range,
    ):
        """基础 WF：2 fold，TimeSeriesSplitter + BacktestEngine"""
        start, end = bar_date_range
        run_fn = _make_engine_run_fn(long_bars)

        report = walk_forward_validate(
            run_fn=run_fn,
            start_date=start,
            end_date=end,
            train_days=180,
            test_days=60,
            n_splits=2,
            strategy_name="multifactor_enhance_wf",
        )

        assert isinstance(report, ValidationReport)
        assert len(report.split_results) == 2
        assert report.strategy_name == "multifactor_enhance_wf"

        # 每个 fold 都应有实际指标（非全零）
        for sr in report.split_results:
            # 至少训练期应有收益（正或负）
            assert sr.train_sharpe != 0.0 or sr.train_return != 0.0, (
                f"Split {sr.split_id}: 训练期指标全零，引擎可能未正常运行"
            )

        # 报告汇总统计应已计算
        assert report.test_win_rate >= 0.0
        assert report.decay_consistency >= 0.0

    def test_purged_walk_forward_with_real_engine(
        self, long_bars, bar_date_range,
    ):
        """Purged WF：2 fold，含 purge/embargo + BacktestEngine"""
        start, end = bar_date_range
        run_fn = _make_engine_run_fn(long_bars)

        report = purged_walk_forward_validate(
            run_fn=run_fn,
            start_date=start,
            end_date=end,
            train_days=180,
            test_days=60,
            n_splits=2,
            purge_days=5,
            embargo_days=2,
            strategy_name="multifactor_purged_wf",
        )

        assert isinstance(report, ValidationReport)
        assert len(report.split_results) >= 1
        assert report.strategy_name == "multifactor_purged_wf"

        # 验证 purge 生效：训练期应比标准 WF 短
        for sr in report.split_results:
            train_days = (sr.period.train_end - sr.period.train_start).days
            # 180 天训练期 - 5 天 purge = 175 天
            assert train_days < 180, (
                f"Purge 应缩减训练期，实际 {train_days} 天"
            )

    def test_judge_walk_forward_result(
        self, long_bars, bar_date_range,
    ):
        """judge_walk_forward 能对真实引擎报告做出判定"""
        start, end = bar_date_range
        run_fn = _make_engine_run_fn(long_bars)

        report = walk_forward_validate(
            run_fn=run_fn,
            start_date=start,
            end_date=end,
            train_days=180,
            test_days=60,
            n_splits=2,
            strategy_name="judge_test",
        )

        verdict = judge_walk_forward(report)
        assert verdict in ("PASS", "FAIL", "NO_DATA")

        # 用宽松门槛
        verdict_loose = judge_walk_forward(
            report,
            decay_threshold=0.95,
            win_rate_threshold=0.0,
            sharpe_threshold=-10.0,
        )
        # 宽松门槛应更容易 PASS
        assert verdict_loose in ("PASS", "FAIL")

    def test_compute_wf_score_on_real_report(
        self, long_bars, bar_date_range,
    ):
        """compute_wf_score 对真实引擎报告返回合理评分"""
        start, end = bar_date_range
        run_fn = _make_engine_run_fn(long_bars)

        report = walk_forward_validate(
            run_fn=run_fn,
            start_date=start,
            end_date=end,
            train_days=180,
            test_days=60,
            n_splits=2,
            strategy_name="score_test",
        )

        score = compute_wf_score(report)
        assert 0.0 <= score <= 10.0
        assert isinstance(score, float)

    def test_wf_report_summary_and_to_dict(
        self, long_bars, bar_date_range,
    ):
        """ValidationReport.summary() 和 to_dict() 可用"""
        start, end = bar_date_range
        run_fn = _make_engine_run_fn(long_bars)

        report = walk_forward_validate(
            run_fn=run_fn,
            start_date=start,
            end_date=end,
            train_days=180,
            test_days=60,
            n_splits=2,
            strategy_name="summary_test",
        )

        summary = report.summary()
        assert "summary_test" in summary
        assert "训练集表现" in summary
        assert "测试集表现" in summary

        d = report.to_dict()
        assert d["strategy_name"] == "summary_test"
        assert d["n_splits"] == 2
        assert len(d["splits"]) == 2
        for s in d["splits"]:
            assert "train_return" in s
            assert "test_sharpe" in s

    def test_wf_with_thresholds_config(
        self, long_bars, bar_date_range,
    ):
        """WalkForwardThresholds 配置对象可驱动 judge"""
        start, end = bar_date_range
        run_fn = _make_engine_run_fn(long_bars)

        report = walk_forward_validate(
            run_fn=run_fn,
            start_date=start,
            end_date=end,
            train_days=180,
            test_days=60,
            n_splits=2,
            strategy_name="thresholds_test",
        )

        # 用严格门槛
        strict = WalkForwardThresholds(
            decay_threshold=0.10,
            win_rate_threshold=0.90,
            sharpe_threshold=1.0,
        )
        verdict_strict = judge_walk_forward(report, thresholds=strict)
        assert verdict_strict in ("PASS", "FAIL")

        # 用宽松门槛
        loose = WalkForwardThresholds(
            decay_threshold=0.99,
            win_rate_threshold=0.0,
            sharpe_threshold=-100.0,
        )
        verdict_loose = judge_walk_forward(report, thresholds=loose)
        assert verdict_loose in ("PASS", "FAIL")

    def test_auto_n_splits_utility(self, bar_date_range):
        """auto_n_splits 根据数据长度自动计算最大分折数"""
        start, end = bar_date_range
        total_days = (end - start).days

        n = auto_n_splits(total_days, train_days=180, test_days=60)
        assert n >= 1
        # 500 天数据约 700 日历天 → (700-180)/60 = ~8
        assert n >= 3, f"500 交易日应至少支持 3 fold，实际 {n}"

    def test_dual_ma_strategy_wf(
        self, long_bars, bar_date_range,
    ):
        """dual_ma 策略也能通过 WF 框架运行"""
        start, end = bar_date_range
        run_fn = _make_engine_run_fn(
            long_bars,
            strategy_name="demo_dual_ma",
            strategy_params={
                "short_window": 5,
                "long_window": 20,
                "max_positions": 5,
            },
        )

        report = walk_forward_validate(
            run_fn=run_fn,
            start_date=start,
            end_date=end,
            train_days=180,
            test_days=60,
            n_splits=2,
            strategy_name="dual_ma_wf",
        )

        assert len(report.split_results) == 2
        verdict = judge_walk_forward(report)
        assert verdict in ("PASS", "FAIL")

    def test_momentum_strategy_wf(
        self, long_bars, bar_date_range,
    ):
        """momentum 策略也能通过 WF 框架运行"""
        start, end = bar_date_range
        run_fn = _make_engine_run_fn(
            long_bars,
            strategy_name="demo_momentum",
            strategy_params={
                "top_k": 5,
                "rebalance_days": 10,
                "lookback_days": 20,
            },
        )

        report = walk_forward_validate(
            run_fn=run_fn,
            start_date=start,
            end_date=end,
            train_days=180,
            test_days=60,
            n_splits=2,
            strategy_name="momentum_wf",
        )

        assert len(report.split_results) == 2
        assert report.avg_train_return is not None
        assert report.avg_test_return is not None

    # ------------------------------------------------------------------
    # D-2: Walk-Forward 覆盖更多 A 股策略
    # ------------------------------------------------------------------

    def test_pead_event_strategy_wf(self, long_bars, bar_date_range):
        """pead_event 策略 Walk-Forward 验证"""
        start, end = bar_date_range
        run_fn = _make_engine_run_fn(
            long_bars,
            strategy_name="pead_event",
            strategy_params={"top_k": 5, "rebalance_days": 10},
        )
        report = walk_forward_validate(
            run_fn=run_fn,
            start_date=start,
            end_date=end,
            train_days=180,
            test_days=60,
            n_splits=2,
            strategy_name="pead_event_wf",
        )
        assert len(report.split_results) == 2
        verdict = judge_walk_forward(report)
        assert verdict in ("PASS", "FAIL")
        score = compute_wf_score(report)
        assert 0.0 <= score <= 10.0

    def test_smallcap_rotation_strategy_wf(self, long_bars, bar_date_range):
        """smallcap_rotation 策略 Walk-Forward 验证"""
        start, end = bar_date_range
        run_fn = _make_engine_run_fn(
            long_bars,
            strategy_name="smallcap_rotation",
            strategy_params={"top_k": 5, "rebalance_days": 10},
        )
        report = walk_forward_validate(
            run_fn=run_fn,
            start_date=start,
            end_date=end,
            train_days=180,
            test_days=60,
            n_splits=2,
            strategy_name="smallcap_rotation_wf",
        )
        assert len(report.split_results) == 2
        verdict = judge_walk_forward(report)
        assert verdict in ("PASS", "FAIL")
        score = compute_wf_score(report)
        assert 0.0 <= score <= 10.0

    def test_value_quality_strategy_wf(self, long_bars, bar_date_range):
        """value_quality 策略 Walk-Forward 验证"""
        start, end = bar_date_range
        run_fn = _make_engine_run_fn(
            long_bars,
            strategy_name="value_quality",
            strategy_params={"top_k": 5, "rebalance_days": 10},
        )
        report = walk_forward_validate(
            run_fn=run_fn,
            start_date=start,
            end_date=end,
            train_days=180,
            test_days=60,
            n_splits=2,
            strategy_name="value_quality_wf",
        )
        assert len(report.split_results) == 2
        verdict = judge_walk_forward(report)
        assert verdict in ("PASS", "FAIL")
        score = compute_wf_score(report)
        assert 0.0 <= score <= 10.0

    def test_sector_momentum_strategy_wf(self, long_bars, bar_date_range):
        """sector_momentum 策略 Walk-Forward 验证"""
        start, end = bar_date_range
        run_fn = _make_engine_run_fn(
            long_bars,
            strategy_name="sector_momentum",
            strategy_params={"top_k": 5, "rebalance_days": 10},
        )
        report = walk_forward_validate(
            run_fn=run_fn,
            start_date=start,
            end_date=end,
            train_days=180,
            test_days=60,
            n_splits=2,
            strategy_name="sector_momentum_wf",
        )
        assert len(report.split_results) == 2
        verdict = judge_walk_forward(report)
        assert verdict in ("PASS", "FAIL")

    def test_purged_wf_pead_event(self, long_bars, bar_date_range):
        """pead_event Purged Walk-Forward：验证 purge/embargo 对非默认策略也有效"""
        start, end = bar_date_range
        run_fn = _make_engine_run_fn(
            long_bars,
            strategy_name="pead_event",
            strategy_params={"top_k": 5, "rebalance_days": 10},
        )
        report = purged_walk_forward_validate(
            run_fn=run_fn,
            start_date=start,
            end_date=end,
            train_days=180,
            test_days=60,
            n_splits=2,
            purge_days=5,
            embargo_days=2,
            strategy_name="pead_purged_wf",
        )
        assert len(report.split_results) >= 1
        for sr in report.split_results:
            train_days = (sr.period.train_end - sr.period.train_start).days
            assert train_days < 180

    def test_dividend_defensive_strategy_wf(self, long_bars, bar_date_range):
        """dividend_defensive 策略 Walk-Forward 验证"""
        start, end = bar_date_range
        run_fn = _make_engine_run_fn(
            long_bars,
            strategy_name="dividend_defensive",
            strategy_params={
                "top_k": 5,
                "rebalance_days": 10,
                "min_dividend_yield": 0.5,
            },
        )
        report = walk_forward_validate(
            run_fn=run_fn,
            start_date=start,
            end_date=end,
            train_days=180,
            test_days=60,
            n_splits=2,
            strategy_name="dividend_defensive_wf",
        )
        assert len(report.split_results) == 2
        verdict = judge_walk_forward(report)
        assert verdict in ("PASS", "FAIL")
        score = compute_wf_score(report)
        assert 0.0 <= score <= 10.0
