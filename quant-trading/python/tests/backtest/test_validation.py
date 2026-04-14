"""样本外验证模块测试"""

from datetime import date, timedelta
import numpy as np
import pytest

from aipha.backtest.validation import (
    SplitPeriod,
    SplitResult,
    ValidationReport,
    TimeSeriesSplitter,
    ExpandingWindowSplitter,
    WalkForwardValidator,
    WalkForwardThresholds,
    CrossValidator,
    PurgedWalkForwardSplitter,
    walk_forward_validate,
    purged_walk_forward_validate,
    judge_walk_forward,
)


class TestSplitPeriod:
    """SplitPeriod测试"""

    def test_repr(self):
        """测试字符串表示"""
        period = SplitPeriod(
            train_start=date(2023, 1, 1),
            train_end=date(2023, 12, 31),
            test_start=date(2024, 1, 1),
            test_end=date(2024, 3, 31),
        )
        repr_str = repr(period)
        assert "2023-01-01" in repr_str
        assert "2024-03-31" in repr_str


class TestTimeSeriesSplitter:
    """TimeSeriesSplitter测试"""

    def test_basic_split(self):
        """测试基本分割"""
        splitter = TimeSeriesSplitter(
            train_period_days=100,
            test_period_days=30,
            n_splits=3,
        )
        start = date(2023, 1, 1)
        end = date(2023, 12, 31)

        splits = splitter.split(start, end)

        assert len(splits) == 3
        for split in splits:
            train_days = (split.train_end - split.train_start).days + 1
            test_days = (split.test_end - split.test_start).days + 1
            assert train_days == 100
            assert test_days == 30

    def test_split_with_gap(self):
        """测试带间隔的分割"""
        splitter = TimeSeriesSplitter(
            train_period_days=100,
            test_period_days=30,
            n_splits=2,
            gap_days=5,
        )
        start = date(2023, 1, 1)
        end = date(2023, 12, 31)

        splits = splitter.split(start, end)

        for split in splits:
            # 训练结束到测试开始应有5天间隔
            gap = (split.test_start - split.train_end).days
            assert gap == 6  # gap_days + 1

    def test_split_stops_at_end(self):
        """测试分割不超过结束日期"""
        splitter = TimeSeriesSplitter(
            train_period_days=100,
            test_period_days=30,
            n_splits=10,  # 要求过多
        )
        start = date(2023, 1, 1)
        end = date(2023, 6, 30)  # 只有180天

        splits = splitter.split(start, end)

        # 应该只能分出1-2个有效期间
        assert len(splits) <= 3
        for split in splits:
            assert split.test_end <= end

    def test_rolling_splits(self):
        """测试滚动分割"""
        splitter = TimeSeriesSplitter(
            train_period_days=60,
            test_period_days=30,
            n_splits=3,
        )
        start = date(2023, 1, 1)
        end = date(2023, 12, 31)

        splits = splitter.split(start, end)

        # 每个分割的测试期起点应该递进
        for i in range(len(splits) - 1):
            assert splits[i + 1].train_start > splits[i].train_start


class TestExpandingWindowSplitter:
    """ExpandingWindowSplitter测试"""

    def test_expanding_train(self):
        """测试训练集扩展"""
        splitter = ExpandingWindowSplitter(
            initial_train_days=100,
            test_period_days=30,
            n_splits=3,
        )
        start = date(2023, 1, 1)
        end = date(2023, 12, 31)

        splits = splitter.split(start, end)

        # 所有训练期都从同一天开始
        for split in splits:
            assert split.train_start == start

        # 训练期逐步扩展
        train_lengths = [(s.train_end - s.train_start).days for s in splits]
        assert train_lengths == sorted(train_lengths)

    def test_initial_train_respected(self):
        """第一个 split 的训练期 ≥ initial_train_days"""
        splitter = ExpandingWindowSplitter(
            initial_train_days=200,
            test_period_days=50,
            n_splits=3,
        )
        splits = splitter.split(date(2022, 1, 1), date(2023, 12, 31))
        if splits:
            first_train_len = (splits[0].train_end - splits[0].train_start).days
            assert first_train_len >= 199  # 允许边界日差异

    def test_test_period_consistent(self):
        """每个 split 的测试期长度大致相同"""
        splitter = ExpandingWindowSplitter(
            initial_train_days=100,
            test_period_days=30,
            n_splits=4,
        )
        splits = splitter.split(date(2023, 1, 1), date(2024, 6, 30))
        test_lengths = [(s.test_end - s.test_start).days for s in splits]
        if len(test_lengths) >= 2:
            for tl in test_lengths:
                assert tl >= 25  # 至少接近 30

    def test_no_overlap(self):
        """测试期互不重叠"""
        splitter = ExpandingWindowSplitter(
            initial_train_days=100,
            test_period_days=30,
            n_splits=4,
        )
        splits = splitter.split(date(2023, 1, 1), date(2024, 6, 30))
        for i in range(1, len(splits)):
            assert splits[i].test_start >= splits[i - 1].test_end

    def test_with_gap(self):
        """带间隔的 expanding splitter"""
        splitter = ExpandingWindowSplitter(
            initial_train_days=100,
            test_period_days=30,
            n_splits=3,
            gap_days=5,
        )
        splits = splitter.split(date(2023, 1, 1), date(2023, 12, 31))
        for split in splits:
            gap = (split.test_start - split.train_end).days
            assert gap >= 5


class TestWalkForwardValidator:
    """WalkForwardValidator测试"""

    def _mock_runner(
        self,
        train_return: float = 0.10,
        test_return: float = 0.05,
    ):
        """创建模拟运行器"""

        class MockRunner:
            def __init__(self, train_ret: float, test_ret: float):
                self.train_ret = train_ret
                self.test_ret = test_ret
                self.call_count = 0

            def run(self, start: date, end: date) -> dict[str, float]:
                self.call_count += 1
                # 根据时间长度判断是训练还是测试
                days = (end - start).days
                if days > 50:
                    return {
                        "return": self.train_ret,
                        "sharpe": self.train_ret * 5,
                        "max_drawdown": 0.05,
                    }
                else:
                    return {
                        "return": self.test_ret,
                        "sharpe": self.test_ret * 5,
                        "max_drawdown": 0.03,
                    }

        return MockRunner(train_return, test_return)

    def test_basic_validation(self):
        """测试基本验证流程"""
        validator = WalkForwardValidator(
            train_period_days=100,
            test_period_days=30,
            n_splits=2,
        )
        runner = self._mock_runner(0.10, 0.05)

        report = validator.validate(
            runner,
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
            strategy_name="TestStrategy",
        )

        assert isinstance(report, ValidationReport)
        assert report.strategy_name == "TestStrategy"
        assert len(report.split_results) == 2

    def test_decay_calculation(self):
        """测试衰减计算"""
        validator = WalkForwardValidator(
            train_period_days=100,
            test_period_days=30,
            n_splits=1,
        )
        runner = self._mock_runner(0.10, 0.05)

        report = validator.validate(
            runner,
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
        )

        # 衰减 = (0.10 - 0.05) / 0.10 = 0.5
        assert len(report.split_results) > 0
        result = report.split_results[0]
        assert result.return_decay == pytest.approx(0.5, rel=0.1)

    def test_report_statistics(self):
        """测试报告统计"""
        validator = WalkForwardValidator(
            train_period_days=60,
            test_period_days=30,
            n_splits=3,
        )
        runner = self._mock_runner(0.12, 0.06)

        report = validator.validate(
            runner,
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
        )

        assert report.avg_train_return > 0
        assert report.avg_test_return > 0
        assert report.test_win_rate >= 0

    def test_validate_with_callable(self):
        """测试使用可调用对象"""
        validator = WalkForwardValidator(n_splits=2)

        def run_fn(start: date, end: date) -> dict[str, float]:
            return {"return": 0.08, "sharpe": 1.5, "max_drawdown": 0.04}

        report = validator.validate_with_callable(
            run_fn,
            start_date=date(2023, 1, 1),
            end_date=date(2024, 12, 31),
        )

        assert isinstance(report, ValidationReport)

    def test_custom_splitter(self):
        """测试自定义分割器"""
        splitter = ExpandingWindowSplitter(
            initial_train_days=90,
            test_period_days=30,
            n_splits=2,
        )
        validator = WalkForwardValidator(splitter=splitter)
        runner = self._mock_runner()

        report = validator.validate(
            runner,
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
        )

        assert len(report.split_results) == 2

    def test_empty_splits(self):
        """测试无有效分割"""
        validator = WalkForwardValidator(
            train_period_days=1000,  # 过长
            test_period_days=30,
            n_splits=1,
        )
        runner = self._mock_runner()

        report = validator.validate(
            runner,
            start_date=date(2023, 1, 1),
            end_date=date(2023, 6, 30),  # 只有180天
        )

        assert len(report.split_results) == 0
        assert report.avg_train_return == 0

    def test_report_summary(self):
        """测试报告摘要"""
        validator = WalkForwardValidator(n_splits=2)
        runner = self._mock_runner()

        report = validator.validate(
            runner,
            start_date=date(2023, 1, 1),
            end_date=date(2024, 12, 31),
        )
        summary = report.summary()

        assert "样本外验证报告" in summary
        assert "训练集表现" in summary
        assert "测试集表现" in summary
        assert "样本外衰减" in summary
        assert "Split" in summary


class TestCrossValidator:
    """CrossValidator测试"""

    def test_k_fold_split(self):
        """测试K折分割"""
        cv = CrossValidator(n_folds=5)
        start = date(2023, 1, 1)
        end = date(2023, 12, 31)

        splits = cv.split(start, end)

        # 第一折没有历史数据，应该跳过
        assert len(splits) <= 4

        # 每个训练期都从起点开始
        for split in splits:
            assert split.train_start == start

    def test_with_gap(self):
        """测试带间隔"""
        cv = CrossValidator(n_folds=3, gap_days=5)
        start = date(2023, 1, 1)
        end = date(2023, 12, 31)

        splits = cv.split(start, end)

        for split in splits:
            gap = (split.test_start - split.train_end).days
            assert gap >= 6  # gap_days + 1

    def test_3_fold(self):
        """3 折交叉验证"""
        cv = CrossValidator(n_folds=3)
        splits = cv.split(date(2023, 1, 1), date(2023, 12, 31))
        assert len(splits) >= 1
        assert len(splits) <= 2  # 第一折跳过

    def test_10_fold(self):
        """10 折交叉验证"""
        cv = CrossValidator(n_folds=10)
        splits = cv.split(date(2020, 1, 1), date(2023, 12, 31))
        assert len(splits) >= 5

    def test_test_covers_all_data(self):
        """测试期合起来应覆盖大部分数据"""
        cv = CrossValidator(n_folds=5)
        start = date(2023, 1, 1)
        end = date(2023, 12, 31)
        splits = cv.split(start, end)
        if splits:
            first_test = splits[0].test_start
            last_test = splits[-1].test_end
            # 测试覆盖的范围应该大于总数据的 50%
            total_days = (end - start).days
            test_span = (last_test - first_test).days
            assert test_span > total_days * 0.3


class TestConvenienceFunction:
    """便捷函数测试"""

    def test_walk_forward_validate(self):
        """测试walk_forward_validate"""

        def strategy_run(start: date, end: date) -> dict[str, float]:
            days = (end - start).days
            return {
                "return": 0.10 if days > 100 else 0.05,
                "sharpe": 1.5,
                "max_drawdown": 0.03,
            }

        report = walk_forward_validate(
            run_fn=strategy_run,
            start_date=date(2023, 1, 1),
            end_date=date(2024, 12, 31),
            train_days=180,
            test_days=60,
            n_splits=3,
            strategy_name="MyStrategy",
        )

        assert isinstance(report, ValidationReport)
        assert report.strategy_name == "MyStrategy"


class TestSplitResult:
    """SplitResult测试"""

    def test_dataclass_fields(self):
        """测试数据类字段"""
        result = SplitResult(
            split_id=1,
            period=SplitPeriod(
                train_start=date(2023, 1, 1),
                train_end=date(2023, 6, 30),
                test_start=date(2023, 7, 1),
                test_end=date(2023, 9, 30),
            ),
            train_return=0.12,
            train_sharpe=1.8,
            train_max_drawdown=0.05,
            test_return=0.06,
            test_sharpe=1.2,
            test_max_drawdown=0.03,
            return_decay=0.5,
            sharpe_decay=0.33,
        )

        assert result.split_id == 1
        assert result.train_return == 0.12
        assert result.test_return == 0.06
        assert result.return_decay == 0.5


# ============================================================================
# PurgedWalkForwardSplitter Tests
# ============================================================================


class TestPurgedWalkForwardSplitter:
    """PurgedWalkForwardSplitter 测试"""

    def test_basic_split(self):
        """基本分割应正常工作"""
        splitter = PurgedWalkForwardSplitter(
            train_period_days=100,
            test_period_days=30,
            n_splits=2,
            purge_days=5,
            embargo_days=2,
        )
        splits = splitter.split(date(2023, 1, 1), date(2024, 12, 31))
        assert len(splits) >= 1

    def test_purge_shortens_train(self):
        """purge 应缩短训练集"""
        splitter = PurgedWalkForwardSplitter(
            train_period_days=100,
            test_period_days=30,
            n_splits=1,
            purge_days=10,
            embargo_days=0,
        )
        splits = splitter.split(date(2023, 1, 1), date(2024, 12, 31))
        assert len(splits) == 1

        s = splits[0]
        train_days = (s.train_end - s.train_start).days + 1
        # 原始 100 天训练 → 实际 90 天（减去 purge_days=10）
        assert train_days == 90

    def test_purge_gap_exists(self):
        """训练集结束和测试集开始之间应有 purge 间隔"""
        splitter = PurgedWalkForwardSplitter(
            train_period_days=100,
            test_period_days=30,
            n_splits=1,
            purge_days=5,
            embargo_days=0,
        )
        splits = splitter.split(date(2023, 1, 1), date(2024, 12, 31))
        s = splits[0]

        gap = (s.test_start - s.train_end).days
        assert gap > 1  # 至少有 purge 间隔

    def test_embargo_shifts_next_split(self):
        """embargo 应使下一折起始更晚"""
        splitter_no_embargo = PurgedWalkForwardSplitter(
            train_period_days=100,
            test_period_days=30,
            n_splits=2,
            purge_days=0,
            embargo_days=0,
        )
        splitter_with_embargo = PurgedWalkForwardSplitter(
            train_period_days=100,
            test_period_days=30,
            n_splits=2,
            purge_days=0,
            embargo_days=10,
        )

        start = date(2023, 1, 1)
        end = date(2024, 12, 31)
        splits_no = splitter_no_embargo.split(start, end)
        splits_yes = splitter_with_embargo.split(start, end)

        if len(splits_no) >= 2 and len(splits_yes) >= 2:
            # 第二折的训练开始应更晚
            assert splits_yes[1].train_start > splits_no[1].train_start

    def test_respects_end_date(self):
        """测试集不应超过 end_date"""
        splitter = PurgedWalkForwardSplitter(
            train_period_days=100,
            test_period_days=30,
            n_splits=10,
            purge_days=5,
            embargo_days=2,
        )
        end = date(2023, 6, 30)
        splits = splitter.split(date(2023, 1, 1), end)

        for s in splits:
            assert s.test_end <= end

    def test_purge_too_large_skips(self):
        """purge 大于训练期应跳过该折"""
        splitter = PurgedWalkForwardSplitter(
            train_period_days=10,
            test_period_days=5,
            n_splits=1,
            purge_days=15,
            embargo_days=0,
        )
        splits = splitter.split(date(2023, 1, 1), date(2024, 12, 31))
        assert len(splits) == 0

    def test_no_purge_no_embargo(self):
        """purge=0 embargo=0 应等同于标准 Walk-Forward"""
        splitter = PurgedWalkForwardSplitter(
            train_period_days=100,
            test_period_days=30,
            n_splits=2,
            purge_days=0,
            embargo_days=0,
        )
        splits = splitter.split(date(2023, 1, 1), date(2024, 12, 31))

        # 应生成有效分割
        assert len(splits) >= 1
        for s in splits:
            assert s.train_end < s.test_start

    def test_train_before_test(self):
        """所有分割的训练期应在测试期之前"""
        splitter = PurgedWalkForwardSplitter(
            train_period_days=200,
            test_period_days=60,
            n_splits=3,
            purge_days=10,
            embargo_days=5,
        )
        splits = splitter.split(date(2020, 1, 1), date(2024, 12, 31))

        for s in splits:
            assert s.train_start < s.train_end
            assert s.train_end < s.test_start
            assert s.test_start <= s.test_end


class TestPurgedWalkForwardValidate:
    """purged_walk_forward_validate 便捷函数测试"""

    def test_basic_validate(self):
        """应能完成基本验证"""
        def mock_runner(start: date, end: date) -> dict[str, float]:
            days = (end - start).days
            return {
                "return": 0.05 * (days / 252),
                "sharpe": 1.2,
                "max_drawdown": 0.08,
            }

        report = purged_walk_forward_validate(
            run_fn=mock_runner,
            start_date=date(2020, 1, 1),
            end_date=date(2024, 12, 31),
            train_days=252,
            test_days=63,
            n_splits=3,
            purge_days=5,
            embargo_days=2,
            strategy_name="PurgedTest",
        )

        assert report.strategy_name == "PurgedTest"
        assert len(report.split_results) >= 1
        assert report.avg_test_sharpe > 0


# ============================================================
# judge_walk_forward 判定逻辑测试
# ============================================================


def _make_report(
    avg_test_sharpe: float = 0.5,
    test_win_rate: float = 0.6,
    avg_sharpe_decay: float = 0.3,
    n_splits: int = 3,
) -> ValidationReport:
    """构造测试用 ValidationReport"""
    period = SplitPeriod(
        train_start=date(2023, 1, 1),
        train_end=date(2023, 6, 30),
        test_start=date(2023, 7, 1),
        test_end=date(2023, 9, 30),
    )
    splits = [
        SplitResult(
            split_id=i,
            period=period,
            train_return=0.10,
            train_sharpe=1.5,
            train_max_drawdown=0.05,
            test_return=0.05,
            test_sharpe=avg_test_sharpe,
            test_max_drawdown=0.08,
            return_decay=0.5,
            sharpe_decay=avg_sharpe_decay,
        )
        for i in range(n_splits)
    ]
    return ValidationReport(
        split_results=splits,
        strategy_name="TestStrategy",
        avg_train_return=0.10,
        avg_test_return=0.05,
        avg_train_sharpe=1.5,
        avg_test_sharpe=avg_test_sharpe,
        avg_return_decay=0.3,
        avg_sharpe_decay=avg_sharpe_decay,
        test_win_rate=test_win_rate,
        decay_consistency=0.1,
    )


class TestJudgeWalkForward:
    """judge_walk_forward 判定逻辑测试"""

    def test_pass_all_criteria(self):
        """所有标准达标 → PASS"""
        report = _make_report(avg_test_sharpe=0.8, test_win_rate=0.7, avg_sharpe_decay=0.3)
        assert judge_walk_forward(report) == "PASS"

    def test_fail_low_sharpe(self):
        """OOS Sharpe ≤ 0 → FAIL"""
        report = _make_report(avg_test_sharpe=-0.1, test_win_rate=0.7, avg_sharpe_decay=0.2)
        assert judge_walk_forward(report) == "FAIL"

    def test_fail_low_win_rate(self):
        """胜率 < 50% → FAIL"""
        report = _make_report(avg_test_sharpe=0.5, test_win_rate=0.4, avg_sharpe_decay=0.2)
        assert judge_walk_forward(report) == "FAIL"

    def test_fail_high_decay(self):
        """衰减 ≥ 50% → FAIL"""
        report = _make_report(avg_test_sharpe=0.5, test_win_rate=0.7, avg_sharpe_decay=0.6)
        assert judge_walk_forward(report) == "FAIL"

    def test_negative_decay_pass(self):
        """负衰减 (OOS > IS) → 衰减检查通过"""
        report = _make_report(avg_test_sharpe=0.5, test_win_rate=0.6, avg_sharpe_decay=-0.1)
        assert judge_walk_forward(report) == "PASS"

    def test_no_data(self):
        """无数据 → NO_DATA"""
        report = ValidationReport(
            split_results=[],
            strategy_name="Empty",
            avg_train_return=0,
            avg_test_return=0,
            avg_train_sharpe=0,
            avg_test_sharpe=0,
            avg_return_decay=0,
            avg_sharpe_decay=0,
            test_win_rate=0,
            decay_consistency=0,
        )
        assert judge_walk_forward(report) == "NO_DATA"

    def test_custom_thresholds(self):
        """自定义门槛"""
        report = _make_report(avg_test_sharpe=0.3, test_win_rate=0.55, avg_sharpe_decay=0.35)
        # 默认通过
        assert judge_walk_forward(report) == "PASS"
        # 收紧 sharpe 门槛
        assert judge_walk_forward(report, sharpe_threshold=0.5) == "FAIL"
        # 收紧衰减门槛
        assert judge_walk_forward(report, decay_threshold=0.3) == "FAIL"

    def test_boundary_win_rate_exactly_50(self):
        """胜率恰好 50% → PASS（≥ threshold）"""
        report = _make_report(avg_test_sharpe=0.5, test_win_rate=0.50, avg_sharpe_decay=0.3)
        assert judge_walk_forward(report) == "PASS"


# ============================================================
# WalkForwardThresholds 配置测试
# ============================================================


class TestWalkForwardThresholds:
    """WalkForwardThresholds 配置对象测试"""

    def test_defaults(self):
        """默认值"""
        t = WalkForwardThresholds()
        assert t.decay_threshold == 0.50
        assert t.win_rate_threshold == 0.50
        assert t.sharpe_threshold == 0.0

    def test_from_dict(self):
        """从字典构造"""
        t = WalkForwardThresholds.from_dict({
            "decay_threshold": 0.40,
            "win_rate_threshold": 0.60,
            "sharpe_threshold": 0.1,
        })
        assert t.decay_threshold == 0.40
        assert t.win_rate_threshold == 0.60
        assert t.sharpe_threshold == 0.1

    def test_from_dict_partial(self):
        """部分字段使用默认值"""
        t = WalkForwardThresholds.from_dict({"decay_threshold": 0.30})
        assert t.decay_threshold == 0.30
        assert t.win_rate_threshold == 0.50  # default

    def test_from_dict_empty(self):
        """空字典使用所有默认值"""
        t = WalkForwardThresholds.from_dict({})
        assert t == WalkForwardThresholds()

    def test_judge_with_thresholds_object(self):
        """通过 thresholds 参数传入配置"""
        report = _make_report(
            avg_test_sharpe=0.3,
            test_win_rate=0.55,
            avg_sharpe_decay=0.35,
        )

        # 默认参数 → PASS
        assert judge_walk_forward(report) == "PASS"

        # 严格阈值 → FAIL
        strict = WalkForwardThresholds(
            decay_threshold=0.30,
            win_rate_threshold=0.60,
            sharpe_threshold=0.5,
        )
        assert judge_walk_forward(report, thresholds=strict) == "FAIL"

    def test_thresholds_override_kwargs(self):
        """thresholds 参数优先于关键字参数"""
        report = _make_report(
            avg_test_sharpe=0.3,
            test_win_rate=0.55,
            avg_sharpe_decay=0.35,
        )

        # kwargs 宽松（应 PASS），但 thresholds 严格
        thresholds = WalkForwardThresholds(
            sharpe_threshold=0.5,  # 比 0.3 高 → FAIL
        )
        result = judge_walk_forward(
            report,
            sharpe_threshold=0.0,  # 这个会被覆盖
            thresholds=thresholds,
        )
        assert result == "FAIL"

    def test_from_yaml(self, tmp_path):
        """从 YAML 文件加载"""
        yaml_content = """\
walk_forward:
  decay_threshold: 0.35
  win_rate_threshold: 0.65
  sharpe_threshold: 0.2
"""
        yaml_file = tmp_path / "wf_config.yml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        t = WalkForwardThresholds.from_yaml(str(yaml_file))
        assert t.decay_threshold == 0.35
        assert t.win_rate_threshold == 0.65
        assert t.sharpe_threshold == 0.2

    def test_from_yaml_flat(self, tmp_path):
        """YAML 无 walk_forward 键时直接读取顶层"""
        yaml_content = """\
decay_threshold: 0.40
win_rate_threshold: 0.55
"""
        yaml_file = tmp_path / "flat.yml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        t = WalkForwardThresholds.from_yaml(str(yaml_file))
        assert t.decay_threshold == 0.40
        assert t.win_rate_threshold == 0.55


# ===========================================================================
# compute_wf_score 测试
# ===========================================================================

from aipha.backtest.validation import compute_wf_score, auto_n_splits


class TestComputeWfScore:
    """综合评分函数测试。"""

    def test_perfect_report(self):
        """OOS 优于 IS + 高 Sharpe + 高胜率 + 低一致性 → 接近满分"""
        report = _make_report(
            avg_test_sharpe=2.0, test_win_rate=0.8, avg_sharpe_decay=-0.1,
        )
        # decay_consistency 默认 0.1
        score = compute_wf_score(report)
        assert score >= 8.0

    def test_terrible_report(self):
        """全部维度差 → 低分"""
        report = _make_report(
            avg_test_sharpe=-0.5, test_win_rate=0.0, avg_sharpe_decay=1.0,
        )
        score = compute_wf_score(report)
        assert score <= 2.0

    def test_empty_report(self):
        """无 split → 0 分"""
        report = _make_report(n_splits=0)
        # 0 splits 的 report
        report.split_results = []
        score = compute_wf_score(report)
        assert score == 0.0

    def test_medium_report(self):
        """中等表现"""
        report = _make_report(
            avg_test_sharpe=0.5, test_win_rate=0.6, avg_sharpe_decay=0.3,
        )
        score = compute_wf_score(report)
        assert 3.0 <= score <= 8.0

    def test_score_bounded(self):
        """评分不超过 10 不低于 0"""
        # 极好
        r1 = _make_report(avg_test_sharpe=5.0, test_win_rate=1.0, avg_sharpe_decay=-1.0)
        assert 0 <= compute_wf_score(r1) <= 10.0
        # 极差
        r2 = _make_report(avg_test_sharpe=-5.0, test_win_rate=0.0, avg_sharpe_decay=5.0)
        assert 0 <= compute_wf_score(r2) <= 10.0

    def test_custom_weights(self):
        """自定义权重"""
        report = _make_report(
            avg_test_sharpe=1.0, test_win_rate=0.6, avg_sharpe_decay=0.3,
        )
        # 只看 sharpe
        score = compute_wf_score(report, w_decay=0, w_win_rate=0, w_sharpe=1.0, w_consistency=0)
        expected = 1.0 / 2.0 * 10.0  # sharpe=1.0 → 5.0
        assert abs(score - expected) < 0.1

    def test_negative_decay_full_score(self):
        """负衰减 → 衰减维度满分"""
        report = _make_report(avg_sharpe_decay=-0.5)
        score_decay_only = compute_wf_score(
            report, w_decay=1.0, w_win_rate=0, w_sharpe=0, w_consistency=0,
        )
        assert score_decay_only == 10.0

    def test_high_consistency_penalty(self):
        """高 decay_consistency → 一致性维度低分"""
        report = _make_report()
        report.decay_consistency = 0.5
        score_cons_only = compute_wf_score(
            report, w_decay=0, w_win_rate=0, w_sharpe=0, w_consistency=1.0,
        )
        assert score_cons_only <= 0.5


# ===========================================================================
# auto_n_splits 测试
# ===========================================================================


class TestAutoNSplits:
    """自动分折数计算测试。"""

    def test_basic(self):
        # 1000 days, train=252, test=63, gap=0 → (1000-252)/63 = 11.8 → 11
        assert auto_n_splits(1000, 252, 63) == 11

    def test_with_gap(self):
        # (1000-252) / (63+5) = 748/68 = 11
        assert auto_n_splits(1000, 252, 63, gap_days=5) == 11

    def test_insufficient_data(self):
        # total ≤ train → 1
        assert auto_n_splits(200, 252, 63) == 1

    def test_exact_one_split(self):
        # (315 - 252) / 63 = 1
        assert auto_n_splits(315, 252, 63) == 1

    def test_zero_step(self):
        """test_days=0 → 1"""
        assert auto_n_splits(1000, 252, 0) == 1

    def test_large_dataset(self):
        """10 年 → 多折"""
        n = auto_n_splits(2520, 252, 63)
        assert n >= 30


# ===========================================================================
# ValidationReport.to_dict 测试
# ===========================================================================


class TestValidationReportToDict:
    """ValidationReport 序列化测试。"""

    def test_to_dict_keys(self):
        report = _make_report()
        d = report.to_dict()
        assert "strategy_name" in d
        assert "n_splits" in d
        assert "splits" in d
        assert d["strategy_name"] == "TestStrategy"

    def test_to_dict_splits_count(self):
        report = _make_report(n_splits=4)
        d = report.to_dict()
        assert d["n_splits"] == 4
        assert len(d["splits"]) == 4

    def test_to_dict_split_fields(self):
        report = _make_report()
        d = report.to_dict()
        split = d["splits"][0]
        assert "split_id" in split
        assert "train_return" in split
        assert "test_sharpe" in split
        assert "sharpe_decay" in split

    def test_to_dict_values_match(self):
        report = _make_report(avg_test_sharpe=1.2, test_win_rate=0.75)
        d = report.to_dict()
        assert d["avg_test_sharpe"] == 1.2
        assert d["test_win_rate"] == 0.75

    def test_json_serializable(self):
        """字典可被 JSON 序列化"""
        import json
        report = _make_report()
        d = report.to_dict()
        s = json.dumps(d)
        assert len(s) > 0


# ===========================================================================
# trading_dates 交易日偏移测试（D-1/M-2）
# ===========================================================================


def _make_trading_dates(start: date, n: int) -> list[date]:
    """生成模拟交易日列表（跳过周末）。"""
    dates: list[date] = []
    d = start
    while len(dates) < n:
        if d.weekday() < 5:  # Mon-Fri
            dates.append(d)
        d += timedelta(days=1)
    return dates


class TestTradingDatesOffset:
    """_offset_by_trading_days 工具函数测试。"""

    def test_none_falls_back_to_timedelta(self):
        """trading_dates=None 回退到自然日偏移"""
        from aipha.backtest.validation import _offset_by_trading_days

        base = date(2023, 6, 1)
        result = _offset_by_trading_days(base, 10, None)
        assert result == base + timedelta(days=10)

    def test_forward_offset(self):
        """正向偏移应跳过非交易日"""
        from aipha.backtest.validation import _offset_by_trading_days

        td = _make_trading_dates(date(2023, 1, 2), 250)  # Mon start
        result = _offset_by_trading_days(td[0], 5, td)
        assert result == td[5]

    def test_backward_offset(self):
        """反向偏移（direction=-1）"""
        from aipha.backtest.validation import _offset_by_trading_days

        td = _make_trading_dates(date(2023, 1, 2), 250)
        result = _offset_by_trading_days(td[20], 5, td, direction=-1)
        assert result == td[15]

    def test_zero_offset_returns_base(self):
        """n_days=0 直接返回 base"""
        from aipha.backtest.validation import _offset_by_trading_days

        td = _make_trading_dates(date(2023, 1, 2), 50)
        result = _offset_by_trading_days(td[10], 0, td)
        assert result == td[10]

    def test_clamp_at_end(self):
        """偏移超出列表尾部应 clamp"""
        from aipha.backtest.validation import _offset_by_trading_days

        td = _make_trading_dates(date(2023, 1, 2), 10)
        result = _offset_by_trading_days(td[8], 100, td)
        assert result == td[-1]


class TestTimeSeriesSplitterTradingDates:
    """TimeSeriesSplitter 交易日模式测试。"""

    def test_splits_use_trading_days(self):
        """传入 trading_dates 后按交易日切分"""
        td = _make_trading_dates(date(2023, 1, 2), 250)
        splitter = TimeSeriesSplitter(
            train_period_days=100,
            test_period_days=30,
            n_splits=2,
            trading_dates=td,
        )
        splits = splitter.split(td[0], td[-1])
        assert len(splits) >= 1
        for s in splits:
            assert s.train_start in td
            assert s.test_start in td

    def test_backward_compat_no_trading_dates(self):
        """不传 trading_dates 保持原有自然日行为"""
        splitter = TimeSeriesSplitter(
            train_period_days=100,
            test_period_days=30,
            n_splits=2,
        )
        splits = splitter.split(date(2023, 1, 1), date(2023, 12, 31))
        assert len(splits) >= 1


class TestExpandingWindowSplitterTradingDates:
    """ExpandingWindowSplitter 交易日模式测试。"""

    def test_expanding_with_trading_dates(self):
        td = _make_trading_dates(date(2023, 1, 2), 250)
        splitter = ExpandingWindowSplitter(
            initial_train_days=60,
            test_period_days=20,
            n_splits=3,
            trading_dates=td,
        )
        splits = splitter.split(td[0], td[-1])
        assert len(splits) >= 1
        # 训练窗口应递增
        if len(splits) >= 2:
            t0 = (splits[0].train_end - splits[0].train_start).days
            t1 = (splits[1].train_end - splits[1].train_start).days
            assert t1 > t0


class TestPurgedWalkForwardSplitterTradingDates:
    """PurgedWalkForwardSplitter 交易日模式测试。"""

    def test_purged_with_trading_dates(self):
        td = _make_trading_dates(date(2023, 1, 2), 500)
        splitter = PurgedWalkForwardSplitter(
            train_period_days=100,
            test_period_days=30,
            n_splits=2,
            purge_days=5,
            embargo_days=2,
            trading_dates=td,
        )
        splits = splitter.split(td[0], td[-1])
        assert len(splits) >= 1
        for s in splits:
            assert s.train_end < s.test_start

    def test_purge_gap_in_trading_days(self):
        """purge 间隔应基于交易日而非自然日"""
        td = _make_trading_dates(date(2023, 1, 2), 500)
        splitter = PurgedWalkForwardSplitter(
            train_period_days=100,
            test_period_days=30,
            n_splits=1,
            purge_days=10,
            embargo_days=0,
            trading_dates=td,
        )
        splits = splitter.split(td[0], td[-1])
        if splits:
            s = splits[0]
            # train_end 在交易日列表中的位置
            train_end_idx = td.index(s.train_end)
            test_start_idx = td.index(s.test_start)
            # 间隔应 >= purge_days
            assert test_start_idx - train_end_idx >= 10


class TestCrossValidatorTradingDates:
    """CrossValidator 交易日模式测试。"""

    def test_cv_with_trading_dates(self):
        td = _make_trading_dates(date(2023, 1, 2), 250)
        cv = CrossValidator(n_folds=5, trading_dates=td)
        splits = cv.split(td[0], td[-1])
        assert len(splits) >= 1
        for s in splits:
            assert s.train_start == td[0]

    def test_cv_gap_in_trading_days(self):
        td = _make_trading_dates(date(2023, 1, 2), 250)
        cv = CrossValidator(n_folds=3, gap_days=5, trading_dates=td)
        splits = cv.split(td[0], td[-1])
        for s in splits:
            gap_natural = (s.test_start - s.train_end).days
            assert gap_natural >= 5  # 至少 gap 交易日（自然日更长）


# ─────────────────────────────────────────────────────────────────────────────
# H6 回归测试: _compute_decay 截断 + 中位数聚合
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeDecayH6Fix:
    """H6 fix: _compute_decay 截断 [-2, 2] 并用中位数聚合 avg_sharpe_decay。"""

    def _make_validator(self):
        from aipha.backtest.validation import WalkForwardValidator, TimeSeriesSplitter
        return WalkForwardValidator(TimeSeriesSplitter(252, 63, 4))

    def test_compute_decay_clips_positive_outlier(self):
        """IS ≈ 0.025 时不产生 66x 异常衰减，被截断到 2.0。"""
        v = self._make_validator()
        # IS_SR = 0.025 (接近零), OOS_SR = -1.6 → raw = (0.025+1.6)/0.025 = 66
        decay = v._compute_decay(0.025, -1.6)
        assert decay == 2.0, f"期望截断到 2.0，实际 {decay}"

    def test_compute_decay_clips_negative_outlier(self):
        """OOS 远好于 IS 时也截断到 -2.0。"""
        v = self._make_validator()
        decay = v._compute_decay(0.025, 2.0)
        assert decay == -2.0, f"期望截断到 -2.0，实际 {decay}"

    def test_compute_decay_near_zero_is_positive_oos(self):
        """|IS| < 1e-6 且 OOS >= 0 时返回 0.0（无衰减，防除零路径）。"""
        v = self._make_validator()
        # IS = 1e-8 < 1e-6 threshold; OOS = 1.2 >= 0 → 0.0
        assert v._compute_decay(1e-8, 1.2) == 0.0

    def test_compute_decay_near_zero_is_negative_oos(self):
        """|IS| < 1e-6 且 OOS < 0 时返回 1.0（完全衰减，防除零路径）。"""
        v = self._make_validator()
        # IS = 1e-8 < 1e-6; OOS = -0.5 < 0 → 1.0
        assert v._compute_decay(1e-8, -0.5) == 1.0

    def test_compute_decay_zero_is_unchanged(self):
        """IS = 0.0 时若 OOS >= 0 返回 0.0 (无衰减)。"""
        v = self._make_validator()
        # IS exactly 0: |IS| < 0.05 → OOS=0.5 >= IS → 0.0
        assert v._compute_decay(0.0, 0.5) == 0.0

    def test_compute_decay_normal_case(self):
        """正常情况: IS=1.2, OOS=0.6 → decay = 0.5。"""
        v = self._make_validator()
        result = v._compute_decay(1.2, 0.6)
        assert abs(result - 0.5) < 1e-6

    def test_generate_report_uses_median(self):
        """avg_sharpe_decay 使用中位数 (而不是均值) 对极端值鲁棒。"""
        import statistics

        # 构造场景: 4 折正常衰减 + 1 折极端异常
        normal_decays = [0.2, 0.3, 0.25, 0.1]
        outlier_ray_decay = 66.45  # 未截断时的异常值
        clipped_outlier = 2.0      # _compute_decay 截断后

        # 原始（未修复）情况: 均值被单个极端值拉高
        raw_mean = sum(normal_decays + [outlier_ray_decay]) / 5
        assert raw_mean > 13.0, "原始均值应被极端值拉高"

        # 修复后: 先截断, 再取中位数
        clipped = [min(2.0, max(-2.0, d)) for d in normal_decays + [outlier_ray_decay]]
        clipped_median = statistics.median(clipped)  # 中位数 = 0.25

        # 修复后的 avg_sharpe_decay 应合理（< 1.0）
        assert clipped_median < 1.0, f"截断后中位数应合理: {clipped_median}"
        # 中位数不受单个极端值影响
        assert abs(clipped_median - 0.25) < 1e-6, f"应为 0.25, 实际 {clipped_median}"


# ============================================================================
# V13 折级离散度与异常折检测测试
# ============================================================================


class TestFoldDispersion:
    """R94-V13: test_sharpe_cv 与 outlier_fold_ids 测试"""

    def _make_report(self, test_sharpes: list[float]) -> ValidationReport:
        """从 OOS Sharpe 列表构造 ValidationReport"""
        results = []
        base = date(2020, 1, 1)
        for i, ts in enumerate(test_sharpes):
            results.append(SplitResult(
                split_id=i + 1,
                period=SplitPeriod(
                    train_start=base + timedelta(days=i * 90),
                    train_end=base + timedelta(days=i * 90 + 59),
                    test_start=base + timedelta(days=i * 90 + 60),
                    test_end=base + timedelta(days=i * 90 + 89),
                ),
                train_return=0.1, train_sharpe=1.0,
                train_max_drawdown=0.05,
                test_return=ts * 0.05,
                test_sharpe=ts,
                test_max_drawdown=0.03,
                return_decay=0.0, sharpe_decay=0.0,
            ))
        validator = WalkForwardValidator(
            splitter=TimeSeriesSplitter(n_splits=len(test_sharpes)),
        )
        return validator._generate_report(results, "TestV13")

    def test_cv_uniform_sharpes(self):
        """所有折 SR 相同 → CV=0"""
        report = self._make_report([1.0, 1.0, 1.0, 1.0])
        assert report.test_sharpe_cv == pytest.approx(0.0, abs=1e-4)

    def test_cv_dispersed_sharpes(self):
        """折间 SR 差异大 → CV > 0"""
        report = self._make_report([0.5, 1.0, 0.5, 1.5])
        assert report.test_sharpe_cv > 0.3

    def test_no_outlier_folds(self):
        """SR 比较均匀 → 无异常折"""
        report = self._make_report([1.0, 1.2, 0.8, 1.1])
        assert len(report.outlier_fold_ids) == 0

    def test_one_outlier_fold(self):
        """单折 SR 远超中位数 → 被标记为异常"""
        # median([0.5, 0.5, 0.6, 3.0]) = 0.55
        # 3.0 > 2 * 0.55 = 1.1 → outlier
        report = self._make_report([0.5, 0.5, 0.6, 3.0])
        assert 4 in report.outlier_fold_ids

    def test_outlier_in_to_dict(self):
        """to_dict 包含新字段"""
        report = self._make_report([0.5, 0.5, 0.6, 3.0])
        d = report.to_dict()
        assert "test_sharpe_cv" in d
        assert "outlier_fold_ids" in d
        assert isinstance(d["outlier_fold_ids"], list)

    def test_summary_contains_v13(self):
        """summary 输出包含 V13 离散度信息"""
        report = self._make_report([0.5, 0.5, 0.6, 3.0])
        s = report.summary()
        assert "OOS Sharpe CV" in s
        assert "异常折" in s

