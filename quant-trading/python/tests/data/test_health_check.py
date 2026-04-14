"""
数据健康检查模块测试

测试覆盖:
- 缺失率检查
- 异常跳变检测
- 停牌/涨跌停标记完整性
- 重复/乱序交易日识别
- 数值范围检查
- DataHealthChecker 类
"""

from datetime import datetime, timedelta

import pandas as pd
import pytest

from aipha.data.health_check import (
    CheckResult,
    CheckStatus,
    DataHealthChecker,
    HealthIssue,
    HealthReport,
    IssueSeverity,
    check_daily_cache_health,
    check_duplicates,
    check_halt_flags,
    check_missing_rate,
    check_order,
    check_pit_data_completeness,
    check_price_jumps,
    check_value_range,
    format_report_text,
    quick_check_bars_by_date,
    run_quick_check,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def sample_data_clean():
    """干净的测试数据（无问题）"""
    dates = pd.date_range("2024-01-01 09:30", periods=480, freq="1min")
    df = pd.DataFrame(
        {
            "trade_time": dates,
            "open": [100.0] * 480,
            "high": [101.0] * 480,
            "low": [99.0] * 480,
            "close": [100.5] * 480,
            "volume": [1000] * 480,
            "halt": [False] * 480,
            "limit_up": [False] * 480,
            "limit_down": [False] * 480,
        }
    )
    return {"000001.SZ": df}


@pytest.fixture
def sample_data_with_issues():
    """有问题的测试数据"""
    dates = pd.date_range("2024-01-01 09:30", periods=100, freq="1min")
    
    # 包含异常跳变
    closes = [100.0] * 100
    closes[50] = 150.0  # 50% 跳变
    
    df = pd.DataFrame(
        {
            "trade_time": dates,
            "close": closes,
            "volume": [1000] * 100,
        }
    )
    return {"000002.SZ": df}


@pytest.fixture
def sample_data_duplicates():
    """包含重复时间的数据"""
    dates = list(pd.date_range("2024-01-01 09:30", periods=10, freq="1min"))
    # 添加重复
    dates.append(dates[5])
    dates.append(dates[5])
    
    df = pd.DataFrame(
        {
            "trade_time": dates,
            "close": [100.0] * len(dates),
        }
    )
    return {"000003.SZ": df}


@pytest.fixture
def sample_data_unordered():
    """时间乱序的数据"""
    dates = list(pd.date_range("2024-01-01 09:30", periods=10, freq="1min"))
    # 打乱顺序
    dates[3], dates[7] = dates[7], dates[3]
    
    df = pd.DataFrame(
        {
            "trade_time": dates,
            "close": [100.0] * len(dates),
        }
    )
    return {"000004.SZ": df}


# ============================================================================
# HealthIssue 测试
# ============================================================================


class TestHealthIssue:
    """HealthIssue 类测试"""

    def test_create_issue(self):
        """测试创建问题"""
        issue = HealthIssue(
            symbol="000001.SZ",
            check_name="test",
            message="测试消息",
            severity=IssueSeverity.WARNING,
        )
        assert issue.symbol == "000001.SZ"
        assert issue.severity == IssueSeverity.WARNING

    def test_to_dict(self):
        """测试转换为字典"""
        issue = HealthIssue(
            symbol="000001.SZ",
            check_name="test",
            message="测试",
            severity=IssueSeverity.ERROR,
            details={"key": "value"},
        )
        d = issue.to_dict()
        assert d["symbol"] == "000001.SZ"
        assert d["severity"] == "error"
        assert d["details"]["key"] == "value"


# ============================================================================
# CheckResult 测试
# ============================================================================


class TestCheckResult:
    """CheckResult 类测试"""

    def test_create_result(self):
        """测试创建结果"""
        result = CheckResult(
            check_name="test_check",
            status=CheckStatus.PASS,
        )
        assert result.check_name == "test_check"
        assert result.status == CheckStatus.PASS
        assert result.issues == []

    def test_to_dict(self):
        """测试转换为字典"""
        result = CheckResult(
            check_name="test",
            status=CheckStatus.WARNING,
            details={"threshold": 0.1},
            issues=[
                HealthIssue(
                    symbol="X",
                    check_name="test",
                    message="msg",
                    severity=IssueSeverity.WARNING,
                )
            ],
        )
        d = result.to_dict()
        assert d["status"] == "warning"
        assert len(d["issues"]) == 1


# ============================================================================
# HealthReport 测试
# ============================================================================


class TestHealthReport:
    """HealthReport 类测试"""

    def test_create_report(self):
        """测试创建报告"""
        report = HealthReport(
            check_time=datetime.now(),
            symbols=["A", "B"],
            date_range=("2024-01-01", "2024-01-31"),
            results=[
                CheckResult("check1", CheckStatus.PASS),
                CheckResult("check2", CheckStatus.WARNING),
            ],
        )
        assert report.summary["total_checks"] == 2
        assert report.summary["pass_count"] == 1
        assert report.summary["warning_count"] == 1

    def test_overall_status_pass(self):
        """测试整体状态 - 全通过"""
        report = HealthReport(
            check_time=datetime.now(),
            symbols=[],
            date_range=("2024-01-01", "2024-01-31"),
            results=[
                CheckResult("a", CheckStatus.PASS),
                CheckResult("b", CheckStatus.PASS),
            ],
        )
        assert report.overall_status == CheckStatus.PASS

    def test_overall_status_error(self):
        """测试整体状态 - 有错误"""
        report = HealthReport(
            check_time=datetime.now(),
            symbols=[],
            date_range=("2024-01-01", "2024-01-31"),
            results=[
                CheckResult("a", CheckStatus.PASS),
                CheckResult("b", CheckStatus.ERROR),
            ],
        )
        assert report.overall_status == CheckStatus.ERROR


# ============================================================================
# check_missing_rate 测试
# ============================================================================


class TestCheckMissingRate:
    """缺失率检查测试"""

    def test_no_missing(self, sample_data_clean):
        """测试无缺失数据"""
        result = check_missing_rate(sample_data_clean, expected_bars_per_day=240)
        assert result.status == CheckStatus.PASS
        assert len(result.issues) == 0

    def test_high_missing(self):
        """测试高缺失率"""
        # 只有10条数据，预期240条
        df = pd.DataFrame(
            {
                "trade_time": pd.date_range("2024-01-02 09:30", periods=10, freq="1min"),
                "close": [100.0] * 10,
            }
        )
        result = check_missing_rate(
            {"000001.SZ": df},
            expected_bars_per_day=240,
            trading_days=["2024-01-02"],
            threshold=0.1,
        )
        assert result.status in [CheckStatus.WARNING, CheckStatus.ERROR]
        assert len(result.issues) > 0
        assert result.details["missing_stats"]["000001.SZ"] > 0.9

    def test_empty_data(self):
        """测试空数据"""
        result = check_missing_rate({"EMPTY": pd.DataFrame()})
        assert result.status == CheckStatus.ERROR
        assert any("为空" in issue.message for issue in result.issues)

    def test_none_data(self):
        """测试 None 数据"""
        result = check_missing_rate({"NULL": None})
        assert result.status == CheckStatus.ERROR


# ============================================================================
# check_price_jumps 测试
# ============================================================================


class TestCheckPriceJumps:
    """价格跳变检查测试"""

    def test_no_jumps(self, sample_data_clean):
        """测试无跳变"""
        result = check_price_jumps(sample_data_clean, threshold=0.30)
        assert result.status == CheckStatus.PASS
        assert len(result.issues) == 0

    def test_has_jumps(self, sample_data_with_issues):
        """测试有跳变"""
        result = check_price_jumps(sample_data_with_issues, threshold=0.30)
        assert result.status == CheckStatus.WARNING
        assert len(result.issues) > 0
        assert result.details["jump_stats"]["000002.SZ"] > 0

    def test_custom_threshold(self):
        """测试自定义阈值"""
        df = pd.DataFrame(
            {
                "trade_time": pd.date_range("2024-01-01 09:30", periods=5, freq="1min"),
                "close": [100.0, 105.0, 110.0, 115.0, 120.0],  # 每步 ~5%
            }
        )
        # 阈值 10%，不应触发
        result = check_price_jumps({"X": df}, threshold=0.10)
        assert result.status == CheckStatus.PASS

        # 阈值 3%，应触发
        result = check_price_jumps({"X": df}, threshold=0.03)
        assert result.status == CheckStatus.WARNING

    def test_missing_price_column(self):
        """测试缺少价格列"""
        df = pd.DataFrame({"trade_time": [datetime.now()], "volume": [100]})
        result = check_price_jumps({"X": df}, price_column="close")
        assert any("缺少价格列" in issue.message for issue in result.issues)


# ============================================================================
# check_halt_flags 测试
# ============================================================================


class TestCheckHaltFlags:
    """停牌标记检查测试"""

    def test_all_flags_present(self, sample_data_clean):
        """测试所有标记都存在"""
        result = check_halt_flags(sample_data_clean)
        assert result.status == CheckStatus.PASS
        assert len(result.issues) == 0

    def test_missing_flags(self, sample_data_with_issues):
        """测试缺少标记"""
        result = check_halt_flags(sample_data_with_issues)
        assert result.status == CheckStatus.WARNING
        assert any("缺少字段" in issue.message for issue in result.issues)

    def test_custom_required_columns(self):
        """测试自定义必需列"""
        df = pd.DataFrame({"trade_time": [datetime.now()], "my_flag": [True]})
        result = check_halt_flags({"X": df}, required_columns=["my_flag"])
        assert result.status == CheckStatus.PASS

        result = check_halt_flags({"X": df}, required_columns=["other_flag"])
        assert result.status == CheckStatus.WARNING


# ============================================================================
# check_duplicates 测试
# ============================================================================


class TestCheckDuplicates:
    """重复数据检查测试"""

    def test_no_duplicates(self, sample_data_clean):
        """测试无重复"""
        result = check_duplicates(sample_data_clean)
        assert result.status == CheckStatus.PASS
        assert len(result.issues) == 0

    def test_has_duplicates(self, sample_data_duplicates):
        """测试有重复"""
        result = check_duplicates(sample_data_duplicates)
        assert result.status == CheckStatus.ERROR
        assert len(result.issues) > 0
        assert "重复记录" in result.issues[0].message

    def test_fallback_to_trade_date(self):
        """测试回退到 trade_date 列"""
        df = pd.DataFrame(
            {
                "trade_date": pd.to_datetime(["2024-01-01", "2024-01-01"]),
                "close": [100.0, 101.0],
            }
        )
        result = check_duplicates({"X": df}, time_column="trade_time")
        assert result.status == CheckStatus.ERROR


# ============================================================================
# check_order 测试
# ============================================================================


class TestCheckOrder:
    """时间顺序检查测试"""

    def test_in_order(self, sample_data_clean):
        """测试正确顺序"""
        result = check_order(sample_data_clean)
        assert result.status == CheckStatus.PASS
        assert len(result.issues) == 0

    def test_out_of_order(self, sample_data_unordered):
        """测试乱序"""
        result = check_order(sample_data_unordered)
        assert result.status == CheckStatus.ERROR
        assert any("乱序" in issue.message for issue in result.issues)


# ============================================================================
# check_value_range 测试
# ============================================================================


class TestCheckValueRange:
    """数值范围检查测试"""

    def test_within_range(self):
        """测试在范围内"""
        df = pd.DataFrame({"price": [50.0, 60.0, 70.0]})
        result = check_value_range({"X": df}, column="price", min_value=0, max_value=100)
        assert result.status == CheckStatus.PASS

    def test_below_min(self):
        """测试低于下界"""
        df = pd.DataFrame({"price": [-10.0, 50.0, 60.0]})
        result = check_value_range({"X": df}, column="price", min_value=0)
        assert result.status == CheckStatus.WARNING
        assert any("低于下界" in issue.message for issue in result.issues)

    def test_above_max(self):
        """测试超过上界"""
        df = pd.DataFrame({"price": [50.0, 60.0, 200.0]})
        result = check_value_range({"X": df}, column="price", max_value=100)
        assert result.status == CheckStatus.WARNING
        assert any("超过上界" in issue.message for issue in result.issues)

    def test_null_values(self):
        """测试空值"""
        df = pd.DataFrame({"price": [50.0, None, 60.0]})
        result = check_value_range({"X": df}, column="price", allow_null=False)
        assert result.status == CheckStatus.WARNING
        assert any("空值" in issue.message for issue in result.issues)

    def test_allow_null(self):
        """测试允许空值"""
        df = pd.DataFrame({"price": [50.0, None, 60.0]})
        result = check_value_range({"X": df}, column="price", allow_null=True)
        # 没有其他问题时应该通过
        assert not any("空值" in issue.message for issue in result.issues)

    def test_missing_column(self):
        """测试缺少列"""
        df = pd.DataFrame({"other": [1, 2, 3]})
        result = check_value_range({"X": df}, column="price")
        assert result.status == CheckStatus.WARNING
        assert any("缺少列" in issue.message for issue in result.issues)


# ============================================================================
# DataHealthChecker 测试
# ============================================================================


class TestDataHealthChecker:
    """DataHealthChecker 类测试"""

    def test_add_check(self):
        """测试添加检查"""
        checker = DataHealthChecker()
        checker.add_check(check_missing_rate, threshold=0.1)
        assert len(checker._checks) == 1

    def test_chain_calls(self):
        """测试链式调用"""
        checker = (
            DataHealthChecker()
            .add_check(check_missing_rate)
            .add_check(check_price_jumps)
        )
        assert len(checker._checks) == 2

    def test_add_default_checks(self):
        """测试添加默认检查"""
        checker = DataHealthChecker()
        checker.add_default_checks()
        assert len(checker._checks) == 5  # 5个默认检查

    def test_run_all_checks(self, sample_data_clean):
        """测试运行所有检查"""
        checker = DataHealthChecker()
        checker.add_default_checks()
        report = checker.run(sample_data_clean)

        assert isinstance(report, HealthReport)
        assert len(report.results) == 5
        assert report.overall_status == CheckStatus.PASS

    def test_run_with_issues(self, sample_data_with_issues):
        """测试运行有问题的数据"""
        checker = DataHealthChecker()
        checker.add_check(check_price_jumps, threshold=0.30)
        report = checker.run(sample_data_with_issues)

        assert report.overall_status == CheckStatus.WARNING

    def test_infer_date_range(self, sample_data_clean):
        """测试推断日期范围"""
        checker = DataHealthChecker()
        checker.add_check(check_missing_rate)
        report = checker.run(sample_data_clean)

        assert report.date_range[0] == "2024-01-01"


# ============================================================================
# run_quick_check 测试
# ============================================================================


class TestRunQuickCheck:
    """快速检查测试"""

    def test_quick_check(self, sample_data_clean):
        """测试快速检查"""
        report = run_quick_check(sample_data_clean)
        assert isinstance(report, HealthReport)
        assert report.summary["total_checks"] == 5

    def test_quick_check_with_thresholds(self, sample_data_with_issues):
        """测试带阈值的快速检查"""
        report = run_quick_check(
            sample_data_with_issues,
            missing_threshold=0.5,
            jump_threshold=0.1,
        )
        assert isinstance(report, HealthReport)


# ============================================================================
# format_report_text 测试
# ============================================================================


class TestFormatReportText:
    """报告格式化测试"""

    def test_format_pass_report(self, sample_data_clean):
        """测试格式化通过的报告"""
        report = run_quick_check(sample_data_clean)
        text = format_report_text(report)

        assert "数据健康检查报告" in text
        assert "✓" in text  # 通过标记
        assert "通过" in text

    def test_format_warning_report(self, sample_data_with_issues):
        """测试格式化有警告的报告"""
        report = run_quick_check(sample_data_with_issues)
        text = format_report_text(report)

        assert "⚠" in text or "警告" in text

    def test_format_error_report(self, sample_data_duplicates):
        """测试格式化有错误的报告"""
        report = run_quick_check(sample_data_duplicates)
        text = format_report_text(report)

        assert "✗" in text or "错误" in text


# ============================================================================
# 边界条件测试
# ============================================================================


class TestEdgeCases:
    """边界条件测试"""

    def test_empty_data_dict(self):
        """测试空数据字典"""
        checker = DataHealthChecker()
        checker.add_default_checks()
        report = checker.run({})

        assert report.summary["symbols_count"] == 0

    def test_single_row_data(self):
        """测试单行数据"""
        df = pd.DataFrame(
            {
                "trade_time": [datetime.now()],
                "close": [100.0],
            }
        )
        report = run_quick_check({"X": df})
        assert isinstance(report, HealthReport)

    def test_large_data(self):
        """测试大数据量"""
        dates = pd.date_range("2024-01-01", periods=10000, freq="1min")
        df = pd.DataFrame(
            {
                "trade_time": dates,
                "close": [100.0] * 10000,
                "halt": [False] * 10000,
                "limit_up": [False] * 10000,
                "limit_down": [False] * 10000,
            }
        )
        report = run_quick_check({"LARGE": df})
        assert isinstance(report, HealthReport)

    def test_multiple_symbols(self):
        """测试多个股票"""
        data = {}
        for i in range(10):
            symbol = f"{i:06d}.SZ"
            dates = pd.date_range("2024-01-01 09:30", periods=100, freq="1min")
            data[symbol] = pd.DataFrame(
                {
                    "trade_time": dates,
                    "close": [100.0] * 100,
                    "halt": [False] * 100,
                    "limit_up": [False] * 100,
                    "limit_down": [False] * 100,
                }
            )

        report = run_quick_check(data)
        assert report.summary["symbols_count"] == 10

    def test_check_exception_handling(self):
        """测试检查异常处理"""

        def bad_check(data):
            raise ValueError("Test error")

        checker = DataHealthChecker()
        checker.add_check(bad_check)
        report = checker.run({"X": pd.DataFrame()})

        assert report.overall_status == CheckStatus.ERROR
        assert any("检查失败" in issue.message for r in report.results for issue in r.issues)


# ============================================================================
# quick_check_bars_by_date 测试
# ============================================================================


class TestQuickCheckBarsByDate:
    """测试 bars_by_date 格式的快速健康检查"""

    def _make_bar(self, symbol: str, close: float = 100.0):
        """创建测试 Bar"""
        from aipha.domain.models import Bar

        return Bar(
            symbol=symbol,
            start_ts=datetime(2024, 1, 1, 9, 30),
            end_ts=datetime(2024, 1, 1, 15, 0),
            open=close,
            high=close + 1,
            low=close - 1,
            close=close,
            volume=10000,
        )

    def _make_bars_by_date(self, n_dates: int = 5, n_symbols: int = 3):
        """构造 n_dates 天、每天 n_symbols 只的标准测试数据"""
        from datetime import date, timedelta

        bars_by_date = {}
        base = date(2024, 1, 1)
        for i in range(n_dates):
            d = base + timedelta(days=i)
            bars_by_date[d] = {
                f"SYM{j:03d}": self._make_bar(f"SYM{j:03d}", 100.0 + i)
                for j in range(n_symbols)
            }
        return bars_by_date

    def test_empty_data(self):
        """空数据应返回 error"""
        result = quick_check_bars_by_date({})
        assert result["status"] == "error"

    def test_clean_data_pass(self):
        """干净数据应全部通过"""
        data = self._make_bars_by_date(10, 5)
        result = quick_check_bars_by_date(data)
        assert result["status"] == "pass"
        assert result["n_dates"] == 10
        assert result["total_bars"] == 50
        assert len(result["checks"]) >= 4

    def test_date_duplicates_detected(self):
        """测试日期重复检测（通过 dict 其实不会有重复 key，验证 pass 即可）"""
        data = self._make_bars_by_date(5, 3)
        result = quick_check_bars_by_date(data)
        dup_check = [c for c in result["checks"] if c["name"] == "date_duplicates"]
        assert len(dup_check) == 1
        assert dup_check[0]["status"] == "pass"

    def test_low_symbols_warning(self):
        """某天标的数低于阈值应报 warning"""
        from datetime import date

        data = self._make_bars_by_date(5, 10)
        # 第三天只有 1 只
        d = date(2024, 1, 3)
        data[d] = {"SYM000": self._make_bar("SYM000")}

        result = quick_check_bars_by_date(data, min_symbols_per_day=5)
        sym_check = [c for c in result["checks"] if c["name"] == "symbols_per_day"]
        assert sym_check[0]["status"] == "warning"

    def test_price_jump_detected(self):
        """跨日价格跳变应被检测"""
        from datetime import date, timedelta

        data = {}
        base = date(2024, 1, 1)
        # 第一天 close=100, 第二天 close=200 → 100% 跳变
        data[base] = {"SYM001": self._make_bar("SYM001", 100.0)}
        data[base + timedelta(days=1)] = {"SYM001": self._make_bar("SYM001", 200.0)}

        result = quick_check_bars_by_date(data, price_jump_threshold=0.30)
        jump_check = [c for c in result["checks"] if c["name"] == "price_jumps"]
        assert jump_check[0]["status"] == "warning"
        assert result["status"] == "warning"

    def test_no_price_jump_below_threshold(self):
        """正常价格变动不应触发跳变"""
        data = self._make_bars_by_date(5, 3)
        result = quick_check_bars_by_date(data, price_jump_threshold=0.30)
        jump_check = [c for c in result["checks"] if c["name"] == "price_jumps"]
        assert jump_check[0]["status"] == "pass"

    def test_date_range_in_result(self):
        """检查结果中包含日期范围"""
        data = self._make_bars_by_date(10, 3)
        result = quick_check_bars_by_date(data)
        assert "date_range" in result
        assert result["date_range"][0] == "2024-01-01"
        assert result["date_range"][1] == "2024-01-10"


# ============================================================================
# 日线月度分片缓存检查
# ============================================================================


class TestCheckDailyCacheHealth:
    """check_daily_cache_health 测试"""

    def test_nonexistent_dir(self, tmp_path):
        """不存在的目录返回 error"""
        r = check_daily_cache_health(str(tmp_path / "missing"))
        assert r["status"] == "error"

    def test_empty_dir(self, tmp_path):
        """空目录返回 warning"""
        r = check_daily_cache_health(str(tmp_path))
        assert r["status"] == "warning"

    def test_valid_cache(self, tmp_path):
        """有效缓存文件通过检查"""
        # 创建模拟 parquet 文件
        for ym in ["202401", "202402", "202403"]:
            df = pd.DataFrame({"close": [10.0] * 20})
            df.to_parquet(tmp_path / f"000001.SZ_{ym}_D_qfq.parquet")
        r = check_daily_cache_health(str(tmp_path))
        assert r["status"] == "pass"
        assert r["n_symbols"] == 1
        assert r["n_files"] == 3

    def test_month_gap_warning(self, tmp_path):
        """月份间断应产生 warning"""
        for ym in ["202401", "202403"]:  # 跳过 02
            df = pd.DataFrame({"close": [10.0] * 20})
            df.to_parquet(tmp_path / f"000001.SZ_{ym}_D_qfq.parquet")
        r = check_daily_cache_health(str(tmp_path))
        cont = [c for c in r["checks"] if c["name"] == "month_continuity"]
        assert cont[0]["status"] == "warning"

    def test_filter_by_symbols(self, tmp_path):
        """按 symbols 过滤"""
        for sym in ["000001.SZ", "000002.SZ"]:
            df = pd.DataFrame({"close": [10.0] * 20})
            df.to_parquet(tmp_path / f"{sym}_202401_D_qfq.parquet")
        r = check_daily_cache_health(str(tmp_path), symbols=["000001.SZ"])
        assert r["n_symbols"] == 1


# ============================================================================
# PIT 数据完整性检查
# ============================================================================


class TestCheckPitDataCompleteness:
    """check_pit_data_completeness 测试"""

    def test_none_pit_map(self):
        """空映射返回 warning"""
        r = check_pit_data_completeness(None)
        assert r["status"] == "warning"

    def test_empty_pit_map(self):
        """空字典返回 warning"""
        r = check_pit_data_completeness({})
        assert r["status"] == "warning"

    def test_valid_pit_data(self):
        """合法 PIT 数据通过"""
        from datetime import date
        pit = {
            "000001.SZ": [
                (date(2024, 4, 28), {"roe": 15.0}),
                (date(2024, 8, 30), {"roe": 16.0}),
            ],
        }
        r = check_pit_data_completeness(pit)
        assert r["status"] == "pass"
        assert r["n_symbols"] == 1

    def test_coverage_check(self):
        """expected_symbols 覆盖率检查"""
        from datetime import date
        pit = {
            "000001.SZ": [(date(2024, 4, 28), {"roe": 15.0})],
        }
        r = check_pit_data_completeness(
            pit,
            expected_symbols=["000001.SZ", "000002.SZ"],
        )
        cov = [c for c in r["checks"] if c["name"] == "pit_coverage"]
        assert cov[0]["status"] == "warning"  # 50% 覆盖率

    def test_unsorted_dates_error(self):
        """ann_date 未排序应返回 error"""
        from datetime import date
        pit = {
            "BAD": [
                (date(2024, 8, 30), {"roe": 16.0}),
                (date(2024, 4, 28), {"roe": 15.0}),  # 乱序
            ],
        }
        r = check_pit_data_completeness(pit)
        order = [c for c in r["checks"] if c["name"] == "pit_date_order"]
        assert order[0]["status"] == "error"

    def test_low_record_count_warning(self):
        """记录数不足应 warning"""
        from datetime import date
        pit = {
            "000001.SZ": [(date(2024, 4, 28), {"roe": 15.0})],
        }
        r = check_pit_data_completeness(pit, min_records_per_symbol=3)
        rc = [c for c in r["checks"] if c["name"] == "pit_record_count"]
        assert rc[0]["status"] == "warning"
