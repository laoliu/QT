"""CB 数据降级告警测试

覆盖：
- 强赎数据加载失败时的降级日志
- 启发式判断时 force_redeem_status 标记
- 评级缺失汇总告警
- remain_size 告警降噪
- 强赎状态来源统计
"""

import logging
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from aipha.data.cb_data_adapter import CBDataAdapter
from aipha.domain.models import Bar


class TestForceRedeemDegradation:
    """强赎数据降级路径测试。"""

    def _make_adapter(self):
        adapter = CBDataAdapter.__new__(CBDataAdapter)
        adapter.provider = MagicMock()
        adapter._force_redeem_data = None
        adapter._force_redeem_index = {}
        return adapter

    def test_load_force_redeem_exception_logs_warning(self, caplog):
        """API 异常时应输出降级警告（含影响范围说明）。"""
        adapter = self._make_adapter()
        adapter.provider.load_cb_call.side_effect = RuntimeError("API error")

        with caplog.at_level(logging.WARNING):
            result = adapter._load_force_redeem_data()

        assert result.empty
        assert "cb_value≥130 启发式判断" in caplog.text
        assert "不可靠" in caplog.text

    def test_load_force_redeem_empty_logs_warning(self, caplog):
        """无强赎公告数据时应升级为 WARNING。"""
        adapter = self._make_adapter()
        adapter.provider.load_cb_call.return_value = pd.DataFrame()

        with caplog.at_level(logging.WARNING):
            result = adapter._load_force_redeem_data()

        assert result.empty
        assert "启发式判断" in caplog.text

    def test_heuristic_sets_force_redeem_status(self):
        """启发式降级时应设置 force_redeem_status 标记。"""
        adapter = self._make_adapter()
        adapter._force_redeem_data = pd.DataFrame()  # 空数据

        row = pd.Series({
            "ts_code": "113001.SH",
            "trade_date": "20240101",
            "open": 130.0,
            "high": 135.0,
            "low": 128.0,
            "close": 133.0,
            "vol": 1000.0,
            "amount": 100.0,
            "cb_value": 135.0,
            "cb_over_rate": 5.0,
        })

        meta = adapter._build_metadata(row, trade_date_str="20240101")

        assert meta["is_called"] is None
        assert "启发式" in meta.get("force_redeem_status", "")
        assert "cb_value=135.00" in meta.get("force_redeem_status", "")

    def test_heuristic_below_threshold(self):
        """cb_value < 130 时应设 is_called=False。"""
        adapter = self._make_adapter()
        adapter._force_redeem_data = pd.DataFrame()

        row = pd.Series({
            "ts_code": "113001.SH",
            "trade_date": "20240101",
            "open": 100.0,
            "high": 105.0,
            "low": 98.0,
            "close": 102.0,
            "vol": 1000.0,
            "amount": 50.0,
            "cb_value": 100.0,
            "cb_over_rate": -2.0,
        })

        meta = adapter._build_metadata(row, trade_date_str="20240101")

        assert meta["is_called"] is False

    def test_direct_inject_takes_priority(self):
        """行数据中已有 force_redeem_status 时不走启发式。"""
        adapter = self._make_adapter()
        adapter._force_redeem_data = pd.DataFrame()

        row = pd.Series({
            "ts_code": "113001.SH",
            "trade_date": "20240101",
            "open": 140.0,
            "high": 145.0,
            "low": 138.0,
            "close": 143.0,
            "vol": 1000.0,
            "amount": 100.0,
            "cb_value": 143.0,
            "cb_over_rate": 10.0,
            "force_redeem_status": "公告实施强赎",
        })

        meta = adapter._build_metadata(row, trade_date_str="20240101")

        assert meta["is_called"] is True
        assert meta["force_redeem_status"] == "公告实施强赎"


class TestRemainSizeWarningDowngrade:
    """remain_size 前视偏差告警降噪测试。"""

    def test_remain_size_uses_debug_level(self, caplog):
        """remain_size 告警应为 debug 级别而非 warning。"""
        adapter = CBDataAdapter.__new__(CBDataAdapter)
        adapter.provider = MagicMock()

        df = pd.DataFrame({
            "trade_date": ["20240101"],
            "ts_code": ["113001.SH"],
            "close": [100.0],
            "vol": [1000.0],
            "remain_size": [50000.0],
            "cb_value": [100.0],
            "cb_over_rate": [0.5],
        })

        # 使用 DEBUG 级别捕获
        with caplog.at_level(logging.DEBUG):
            result = adapter._quality_check_and_clean(df)

        # 应在 debug 中出现但不应在 warning 中
        debug_msgs = [
            r for r in caplog.records
            if r.levelno == logging.DEBUG and "remain_size" in r.message
        ]
        warning_msgs = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "remain_size" in r.message
        ]
        assert len(debug_msgs) >= 1
        assert len(warning_msgs) == 0


class TestForceRedeemSourceStatistics:
    """强赎状态来源统计测试。"""

    def test_source_count_log(self, caplog):
        """load() 结束时应输出强赎状态来源统计。"""
        adapter = CBDataAdapter.__new__(CBDataAdapter)
        adapter.provider = MagicMock()
        adapter._force_redeem_data = pd.DataFrame()  # 空 → 全部走启发式
        adapter._force_redeem_index = {}

        # Mock provider
        adapter.provider.load_cb_basic.return_value = pd.DataFrame({
            "ts_code": ["113001.SH"],
            "bond_short_name": ["测试转债"],
            "stk_short_name": ["测试股票"],
            "stk_code": ["600001.SH"],
            "remain_size": [50000.0],
            "issue_size": [100000.0],
            "list_date": ["20200101"],
            "delist_date": [""],
            "maturity_date": ["20260101"],
            "rating": ["AA"],
        })
        adapter.provider.load_cb_daily.return_value = pd.DataFrame({
            "ts_code": ["113001.SH"],
            "trade_date": ["20240101"],
            "open": [100.0],
            "high": [105.0],
            "low": [98.0],
            "close": [102.0],
            "vol": [100.0],
            "amount": [50.0],
            "cb_value": [135.0],  # >= 130 → 启发式
            "cb_over_rate": [5.0],
        })
        adapter.provider.load_cb_call.return_value = pd.DataFrame()

        with caplog.at_level(logging.INFO):
            result = adapter.load("20240101", "20240101")

        assert "强赎状态来源统计" in caplog.text
        assert "启发式=" in caplog.text


class TestGetForceRedeemStatusComment:
    """_get_force_redeem_status 空数据返回注释测试。"""

    def test_empty_force_redeem_returns_unknown(self):
        adapter = CBDataAdapter.__new__(CBDataAdapter)
        adapter.provider = MagicMock()
        adapter._force_redeem_data = pd.DataFrame()
        adapter._force_redeem_index = {}

        result = adapter._get_force_redeem_status("113001.SH", "20240101")
        assert result["is_called"] is None
        assert result["force_redeem_status"] == ""
