"""equity_data_validation.py 的单元测试"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# 确保项目根目录在 sys.path
project_root = Path(__file__).resolve().parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# 导入被测模块中的函数
sys.path.insert(0, str(project_root / "scripts"))
from equity_data_validation import (
    check_calendar_data,
    check_daily_basic_cache,
    check_ohlcv_cache,
    check_stock_basic_cache,
    check_strategy_config_alignment,
    run_validation,
)


class TestCheckCalendarData:
    """测试交易日历检查"""

    def test_calendar_loads_successfully(self):
        result = check_calendar_data()
        assert result["check"] == "calendar_data"
        assert result["status"] == "pass"
        assert result["details"]["trade_days_2024"] > 200

    def test_calendar_has_2023_data(self):
        result = check_calendar_data()
        assert result["details"]["trade_days_2023"] > 200


class TestCheckStockBasicCache:
    """测试 stock_basic 缓存检查"""

    def test_nonexistent_dir(self, tmp_path):
        result = check_stock_basic_cache(tmp_path / "nonexistent")
        assert result["status"] == "warning"

    def test_empty_dir(self, tmp_path):
        result = check_stock_basic_cache(tmp_path)
        assert result["status"] == "warning"

    def test_valid_cache(self, tmp_path):
        import pandas as pd

        df = pd.DataFrame({
            "ts_code": ["000001.SZ", "600000.SH"] * 100,
            "name": ["平安银行", "浦发银行"] * 100,
            "industry": ["银行", "银行"] * 100,
            "list_date": ["19910403", "19991110"] * 100,
            "list_status": ["L", "L"] * 100,
        })
        df.to_parquet(tmp_path / "stock_basic.parquet", index=False)

        result = check_stock_basic_cache(tmp_path)
        assert result["status"] == "pass"
        assert result["details"]["total_stocks"] == 200

    def test_missing_columns(self, tmp_path):
        import pandas as pd

        df = pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["平安"]})
        df.to_parquet(tmp_path / "stock_basic.parquet", index=False)

        result = check_stock_basic_cache(tmp_path)
        assert result["status"] == "error"
        assert len(result["details"]["missing_required_cols"]) > 0

    def test_too_few_stocks(self, tmp_path):
        import pandas as pd

        df = pd.DataFrame({
            "ts_code": ["000001.SZ"],
            "name": ["平安银行"],
            "industry": ["银行"],
            "list_date": ["19910403"],
            "list_status": ["L"],
        })
        df.to_parquet(tmp_path / "stock_basic.parquet", index=False)

        result = check_stock_basic_cache(tmp_path)
        assert result["status"] == "warning"


class TestCheckDailyBasicCache:
    """测试 daily_basic 缓存检查"""

    def test_empty_dir(self, tmp_path):
        result = check_daily_basic_cache(tmp_path)
        assert result["status"] == "warning"

    def test_valid_monthly_cache(self, tmp_path):
        import pandas as pd

        df = pd.DataFrame({
            "ts_code": ["000001.SZ"] * 20,
            "trade_date": ["20240101"] * 20,
            "circ_mv": [100000.0] * 20,
            "pe_ttm": [8.5] * 20,
            "pb": [0.9] * 20,
            "turnover_rate": [1.2] * 20,
            "total_mv": [200000.0] * 20,
        })
        df.to_parquet(tmp_path / "daily_basic_202401.parquet", index=False)

        result = check_daily_basic_cache(tmp_path)
        assert result["status"] == "pass"
        assert result["details"]["monthly_files"] == 1
        assert result["details"]["field_coverage"]["circ_mv"] == 1.0


class TestCheckOhlcvCache:
    """测试 OHLCV 缓存检查"""

    def test_empty_dir(self, tmp_path):
        result = check_ohlcv_cache(tmp_path)
        assert result["status"] == "warning"

    def test_excludes_daily_basic(self, tmp_path):
        """确保不会把 daily_basic 文件误认为 OHLCV"""
        import pandas as pd

        df = pd.DataFrame({"a": [1]})
        df.to_parquet(tmp_path / "daily_basic_202401.parquet", index=False)

        result = check_ohlcv_cache(tmp_path)
        assert result["status"] == "warning"  # 排除后无文件

    def test_valid_daily_cache(self, tmp_path):
        import pandas as pd

        df = pd.DataFrame({
            "ts_code": ["000001.SZ"],
            "trade_date": ["20240101"],
            "open": [10.0],
            "high": [10.5],
            "low": [9.8],
            "close": [10.2],
            "vol": [100000],
        })
        df.to_parquet(tmp_path / "daily_000001.SZ.parquet", index=False)

        result = check_ohlcv_cache(tmp_path)
        assert result["status"] == "pass"


class TestCheckStrategyConfigAlignment:
    """测试策略配置对齐检查"""

    def test_configs_exist(self):
        result = check_strategy_config_alignment()
        assert result["check"] == "strategy_config_alignment"
        # 至少 multifactor_enhance/smallcap_rotation/pead_event 应存在
        missing = result["details"]["missing_configs"]
        assert "multifactor_enhance" not in missing
        assert "smallcap_rotation" not in missing
        assert "pead_event" not in missing

    def test_has_strategy_section(self):
        result = check_strategy_config_alignment()
        # 不应有 "缺少 strategy 节" 的 issue
        for ci in result["details"]["config_issues"]:
            for issue in ci["issues"]:
                assert "缺少 strategy 节" not in issue


class TestRunValidation:
    """测试完整验证流程"""

    def test_cache_mode(self):
        report = run_validation(mode="cache")
        assert report["mode"] == "cache"
        assert "summary" in report
        assert report["summary"]["verdict"] in ("PASS", "FAIL")

    def test_output_structure(self):
        report = run_validation(mode="cache")
        assert isinstance(report["results"], list)
        assert len(report["results"]) > 0
        for r in report["results"]:
            assert "check" in r
            assert "status" in r
