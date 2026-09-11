"""测试共享 fixtures"""

import pytest
from unittest.mock import MagicMock
import pandas as pd
from datetime import datetime


@pytest.fixture(autouse=True)
def reset_gateway():
    """每个测试前后重置 TuShare 网关单例，防止测试间状态泄漏"""
    from aipha.data.tushare_gateway import reset_tushare_gateway
    from aipha.data.rate_limiter import reset_tushare_limiter

    reset_tushare_gateway()
    reset_tushare_limiter()
    yield
    reset_tushare_gateway()
    reset_tushare_limiter()


@pytest.fixture
def mock_tushare_api(monkeypatch):
    """Mock TuShare Pro API（兼容 TuShareGateway 单例）

    同时 mock tushare.pro_api 和 tushare.set_token，
    确保 TuShareGateway 初始化时使用 mock 对象。

    用内置的 monkeypatch 而不是 pytest-mock 的 mocker：这两处 patch
    monkeypatch 原生就能做，多挂一个未安装的插件只会让整个文件报
    "fixture 'mocker' not found"。
    """
    mock_api = MagicMock()
    monkeypatch.setattr("tushare.pro_api", lambda *args, **kwargs: mock_api)
    monkeypatch.setattr("tushare.set_token", lambda *args, **kwargs: None)
    return mock_api


@pytest.fixture
def sample_tushare_data():
    """示例 TuShare 数据"""
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ"],
            "trade_time": [
                "2024-01-02 09:31",
                "2024-01-02 09:32",
                "2024-01-02 09:33",
            ],
            "open": [10.0, 10.1, 10.2],
            "high": [10.1, 10.2, 10.3],
            "low": [9.9, 10.0, 10.1],
            "close": [10.0, 10.1, 10.2],
            "vol": [1000.0, 1100.0, 1200.0],
            "amount": [10000.0, 11100.0, 12240.0],
        }
    )
