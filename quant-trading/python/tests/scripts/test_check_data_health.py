"""
数据健康检查脚本的单元测试
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

# 将 scripts 目录添加到路径
scripts_dir = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(scripts_dir))

from check_data_health import DataHealthChecker, HealthCheckResult


@pytest.fixture
def temp_cache_dir(tmp_path):
    """创建临时缓存目录"""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    return cache_dir


@pytest.fixture
def sample_good_data():
    """创建正常数据样本"""
    dates = pd.date_range("2024-01-02 09:30", periods=240, freq="1min")
    return pd.DataFrame(
        {
            "trade_time": dates,
            "open": 10.0 + pd.Series(range(240)) * 0.01,
            "high": 10.1 + pd.Series(range(240)) * 0.01,
            "low": 9.9 + pd.Series(range(240)) * 0.01,
            "close": 10.0 + pd.Series(range(240)) * 0.01,
            "volume": 1000,
            "halt": False,
            "limit_up": False,
            "limit_down": False,
        }
    )


@pytest.fixture
def sample_bad_data():
    """创建有问题的数据样本"""
    dates = pd.date_range("2024-01-02 09:30", periods=200, freq="1min")
    data = pd.DataFrame(
        {
            "trade_time": dates,
            "open": 10.0,
            "high": 10.1,
            "low": 9.9,
            "close": 10.0,
            "volume": 1000,
        }
    )
    # 添加价格跳变
    data.loc[100, "close"] = 15.0  # +50% jump
    return data


class TestDataHealthChecker:
    """DataHealthChecker 测试类"""

    def test_init(self, temp_cache_dir):
        """测试：初始化健康检查器"""
        checker = DataHealthChecker(
            cache_dir=str(temp_cache_dir),
            symbols=["000001.SZ", "600000.SH"],
            start_date="2024-01-01",
            end_date="2024-01-31",
        )

        assert checker.cache_dir == temp_cache_dir
        assert len(checker.symbols) == 2
        assert checker.start_date == "2024-01-01"
        assert checker.end_date == "2024-01-31"
        assert checker.missing_rate_threshold == 0.1
        assert checker.jump_threshold == 0.30

    def test_load_data_success(self, temp_cache_dir, sample_good_data):
        """测试：成功加载缓存数据"""
        # 保存样本数据
        cache_file = (
            temp_cache_dir / "000001.SZ_2024-01-01_2024-01-31_1min_qfq.parquet"
        )
        sample_good_data.to_parquet(cache_file)

        checker = DataHealthChecker(
            cache_dir=str(temp_cache_dir),
            symbols=["000001.SZ"],
            start_date="2024-01-01",
            end_date="2024-01-31",
        )

        df = checker.load_data("000001.SZ")
        assert df is not None
        assert len(df) == 240
        assert "trade_time" in df.columns

    def test_load_data_missing_file(self, temp_cache_dir):
        """测试：加载不存在的缓存文件"""
        checker = DataHealthChecker(
            cache_dir=str(temp_cache_dir),
            symbols=["000001.SZ"],
            start_date="2024-01-01",
            end_date="2024-01-31",
        )

        df = checker.load_data("000001.SZ")
        assert df is None

    def test_check_missing_rate_pass(self, temp_cache_dir, sample_good_data):
        """测试：缺失率检查通过"""
        cache_file = (
            temp_cache_dir / "000001.SZ_2024-01-02_2024-01-02_1min_qfq.parquet"
        )
        sample_good_data.to_parquet(cache_file)

        checker = DataHealthChecker(
            cache_dir=str(temp_cache_dir),
            symbols=["000001.SZ"],
            start_date="2024-01-02",
            end_date="2024-01-02",
            missing_rate_threshold=0.1,
        )

        result = checker.check_missing_rate()
        assert isinstance(result, HealthCheckResult)
        assert result.check_name == "missing_rate"
        # 单日240 bars，缺失率应该很低（或为0）
        assert result.status in ["pass", "warning"]

    def test_check_missing_rate_error(self, temp_cache_dir):
        """测试：缺失率检查发现错误（文件不存在）"""
        checker = DataHealthChecker(
            cache_dir=str(temp_cache_dir),
            symbols=["000001.SZ"],
            start_date="2024-01-01",
            end_date="2024-01-31",
        )

        result = checker.check_missing_rate()
        assert result.status == "error"
        assert len(result.issues) == 1
        assert result.issues[0]["symbol"] == "000001.SZ"
        assert "不存在" in result.issues[0]["issue"]

    def test_check_price_jumps_pass(self, temp_cache_dir, sample_good_data):
        """测试：价格跳变检查通过（无异常跳变）"""
        cache_file = (
            temp_cache_dir / "000001.SZ_2024-01-02_2024-01-02_1min_qfq.parquet"
        )
        sample_good_data.to_parquet(cache_file)

        checker = DataHealthChecker(
            cache_dir=str(temp_cache_dir),
            symbols=["000001.SZ"],
            start_date="2024-01-02",
            end_date="2024-01-02",
            jump_threshold=0.30,
        )

        result = checker.check_price_jumps()
        assert result.check_name == "price_jumps"
        assert result.status == "pass"
        assert len(result.issues) == 0

    def test_check_price_jumps_warning(self, temp_cache_dir, sample_bad_data):
        """测试：价格跳变检查发现警告"""
        cache_file = (
            temp_cache_dir / "000001.SZ_2024-01-02_2024-01-02_1min_qfq.parquet"
        )
        sample_bad_data.to_parquet(cache_file)

        checker = DataHealthChecker(
            cache_dir=str(temp_cache_dir),
            symbols=["000001.SZ"],
            start_date="2024-01-02",
            end_date="2024-01-02",
            jump_threshold=0.30,
        )

        result = checker.check_price_jumps()
        assert result.status == "warning"
        assert len(result.issues) > 0
        assert result.issues[0]["symbol"] == "000001.SZ"
        assert "跳变" in result.issues[0]["issue"]

    def test_check_halt_flags_pass(self, temp_cache_dir, sample_good_data):
        """测试：停牌标记检查通过"""
        cache_file = (
            temp_cache_dir / "000001.SZ_2024-01-02_2024-01-02_1min_qfq.parquet"
        )
        sample_good_data.to_parquet(cache_file)

        checker = DataHealthChecker(
            cache_dir=str(temp_cache_dir),
            symbols=["000001.SZ"],
            start_date="2024-01-02",
            end_date="2024-01-02",
        )

        result = checker.check_halt_flags()
        assert result.check_name == "halt_flags"
        assert result.status == "pass"

    def test_check_halt_flags_warning(self, temp_cache_dir, sample_bad_data):
        """测试：停牌标记检查发现缺失字段"""
        cache_file = (
            temp_cache_dir / "000001.SZ_2024-01-02_2024-01-02_1min_qfq.parquet"
        )
        sample_bad_data.to_parquet(cache_file)

        checker = DataHealthChecker(
            cache_dir=str(temp_cache_dir),
            symbols=["000001.SZ"],
            start_date="2024-01-02",
            end_date="2024-01-02",
        )

        result = checker.check_halt_flags()
        assert result.status == "warning"
        assert len(result.issues) == 1
        assert "缺少字段" in result.issues[0]["issue"]

    def test_check_duplicates_pass(self, temp_cache_dir, sample_good_data):
        """测试：重复检查通过（无重复）"""
        cache_file = (
            temp_cache_dir / "000001.SZ_2024-01-02_2024-01-02_1min_qfq.parquet"
        )
        sample_good_data.to_parquet(cache_file)

        checker = DataHealthChecker(
            cache_dir=str(temp_cache_dir),
            symbols=["000001.SZ"],
            start_date="2024-01-02",
            end_date="2024-01-02",
        )

        result = checker.check_duplicates()
        assert result.check_name == "duplicates"
        assert result.status == "pass"

    def test_check_duplicates_error(self, temp_cache_dir, sample_good_data):
        """测试：重复检查发现错误"""
        # 添加重复行
        dup_data = pd.concat([sample_good_data, sample_good_data.iloc[:5]])
        cache_file = (
            temp_cache_dir / "000001.SZ_2024-01-02_2024-01-02_1min_qfq.parquet"
        )
        dup_data.to_parquet(cache_file)

        checker = DataHealthChecker(
            cache_dir=str(temp_cache_dir),
            symbols=["000001.SZ"],
            start_date="2024-01-02",
            end_date="2024-01-02",
        )

        result = checker.check_duplicates()
        assert result.status == "error"
        assert len(result.issues) == 1
        assert "重复" in result.issues[0]["issue"]

    def test_check_order_pass(self, temp_cache_dir, sample_good_data):
        """测试：顺序检查通过"""
        cache_file = (
            temp_cache_dir / "000001.SZ_2024-01-02_2024-01-02_1min_qfq.parquet"
        )
        sample_good_data.to_parquet(cache_file)

        checker = DataHealthChecker(
            cache_dir=str(temp_cache_dir),
            symbols=["000001.SZ"],
            start_date="2024-01-02",
            end_date="2024-01-02",
        )

        result = checker.check_order()
        assert result.check_name == "order"
        assert result.status == "pass"

    def test_check_order_error(self, temp_cache_dir, sample_good_data):
        """测试：顺序检查发现乱序"""
        from unittest.mock import patch
        
        # 创建乱序数据：手动调换trade_time
        disorder_data = sample_good_data.copy()
        time_10 = disorder_data.loc[10, "trade_time"]
        time_20 = disorder_data.loc[20, "trade_time"]
        disorder_data.loc[10, "trade_time"] = time_20
        disorder_data.loc[20, "trade_time"] = time_10
        
        checker = DataHealthChecker(
            cache_dir=str(temp_cache_dir),
            symbols=["000001.SZ"],
            start_date="2024-01-02",
            end_date="2024-01-02",
        )

        # Mock load_data 返回乱序数据（绕过自动排序）
        with patch.object(checker, 'load_data', return_value=disorder_data):
            result = checker.check_order()
            assert result.status == "error"
            assert len(result.issues) == 1
            assert "乱序" in result.issues[0]["issue"]

    def test_run_all_checks(self, temp_cache_dir, sample_good_data, capsys):
        """测试：运行所有检查"""
        cache_file = (
            temp_cache_dir / "000001.SZ_2024-01-02_2024-01-02_1min_qfq.parquet"
        )
        sample_good_data.to_parquet(cache_file)

        checker = DataHealthChecker(
            cache_dir=str(temp_cache_dir),
            symbols=["000001.SZ"],
            start_date="2024-01-02",
            end_date="2024-01-02",
        )

        results = checker.run_all_checks()

        assert len(results) == 5  # 5 types of checks
        assert checker.summary["total_checks"] == 5
        assert "check_time" in checker.summary

        # 检查控制台输出
        captured = capsys.readouterr()
        assert "开始数据健康检查" in captured.out
        assert "检查完成" in captured.out

    def test_generate_html_report(self, temp_cache_dir, sample_good_data, tmp_path):
        """测试：生成HTML报告"""
        cache_file = (
            temp_cache_dir / "000001.SZ_2024-01-02_2024-01-02_1min_qfq.parquet"
        )
        sample_good_data.to_parquet(cache_file)

        checker = DataHealthChecker(
            cache_dir=str(temp_cache_dir),
            symbols=["000001.SZ"],
            start_date="2024-01-02",
            end_date="2024-01-02",
        )

        checker.run_all_checks()

        output_file = tmp_path / "test_report.html"
        checker.generate_html_report(str(output_file))

        assert output_file.exists()
        content = output_file.read_text(encoding="utf-8")
        assert "数据健康检查报告" in content
        assert "missing_rate" in content.lower()
        assert "<!DOCTYPE html>" in content


class TestHealthCheckResult:
    """HealthCheckResult 测试类"""

    def test_health_check_result_creation(self):
        """测试：创建健康检查结果"""
        result = HealthCheckResult(
            check_name="test_check",
            status="pass",
            details={"key": "value"},
            issues=[],
        )

        assert result.check_name == "test_check"
        assert result.status == "pass"
        assert result.details["key"] == "value"
        assert len(result.issues) == 0
