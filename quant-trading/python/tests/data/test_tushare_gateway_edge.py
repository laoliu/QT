"""Edge tests for data/tushare_gateway.py — 边界与极端场景。

覆盖:
- _mask_token: 空字符串、恰好 8/9 字符
- stats() 返回副本不影响内部
- _record_stat 累积计数
- call() max_retries=0
- pro_bar max_retries=1 失败直接抛出
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from aipha.data.tushare_gateway import TuShareGateway


@pytest.fixture()
def gw():
    """创建 mock 网关（不触碰真实 TuShare API）。"""
    with patch("aipha.data.tushare_gateway.ts") as mock_ts, \
         patch("aipha.data.tushare_gateway.get_tushare_token", return_value="test_token_1234567890"), \
         patch("aipha.data.tushare_gateway.get_tushare_limiter") as mock_limiter:
        mock_ts.pro_api.return_value = MagicMock()
        mock_limiter.return_value = MagicMock()
        gateway = TuShareGateway(token="test_token_1234567890", max_retries=1)
        yield gateway


class TestMaskTokenEdge:
    """_mask_token 边界。"""

    def test_empty_string(self) -> None:
        assert TuShareGateway._mask_token("") == "****"

    def test_exactly_8_chars(self) -> None:
        assert TuShareGateway._mask_token("12345678") == "****"

    def test_exactly_9_chars(self) -> None:
        result = TuShareGateway._mask_token("123456789")
        assert result == "1234****6789"

    def test_long_token(self) -> None:
        token = "a" * 50
        result = TuShareGateway._mask_token(token)
        assert result.startswith("aaaa")
        assert result.endswith("aaaa")
        assert "****" in result


class TestStatsIsolation:
    """stats() 返回深拷贝。"""

    def test_stats_copy_isolation(self, gw) -> None:
        # 生成一条统计
        gw._record_stat("daily", "success")
        stats = gw.stats()
        stats["daily"]["success"] = 999
        # 内部状态不受影响
        assert gw.stats()["daily"]["success"] == 1

    def test_reset_stats(self, gw) -> None:
        gw._record_stat("daily", "success")
        assert len(gw.stats()) == 1
        gw.reset_stats()
        assert len(gw.stats()) == 0


class TestRecordStatAccumulate:
    """_record_stat 累积统计。"""

    def test_multiple_calls(self, gw) -> None:
        gw._record_stat("daily", "success")
        gw._record_stat("daily", "success")
        gw._record_stat("daily", "error")
        s = gw.stats()["daily"]
        assert s["success"] == 2
        assert s["error"] == 1
        assert s["total"] == 3

    def test_multiple_endpoints(self, gw) -> None:
        gw._record_stat("daily", "success")
        gw._record_stat("stock_basic", "success")
        assert len(gw.stats()) == 2


class TestCallEdge:
    """call() 边界。"""

    def test_call_max_retries_zero_raises(self, gw) -> None:
        """max_retries=0 → range(0) 不迭代 → 直接抛异。"""
        # 当 max_retries=0 时，for 循环不执行，最终抛 RuntimeError
        with pytest.raises(RuntimeError):
            gw.call("daily", max_retries=0, ts_code="000001.SZ")

    def test_call_success_records_stat(self, gw) -> None:
        mock_func = MagicMock(return_value=pd.DataFrame({"a": [1]}))
        gw._api.daily = mock_func
        result = gw.call("daily", ts_code="000001.SZ")
        assert not result.empty
        assert gw.stats()["daily"]["success"] == 1

    def test_call_none_result_returns_empty_df(self, gw) -> None:
        mock_func = MagicMock(return_value=None)
        gw._api.daily = mock_func
        result = gw.call("daily", ts_code="000001.SZ")
        assert isinstance(result, pd.DataFrame)
        assert result.empty
