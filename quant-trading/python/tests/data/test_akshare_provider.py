"""AKShare 数据提供者测试"""

from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from aipha.data.akshare_provider import AKShareBarProvider
from aipha.domain.models import Bar


class TestAKShareBarProviderInit:
    """初始化测试"""

    def test_init_without_cache(self) -> None:
        """无缓存初始化"""
        provider = AKShareBarProvider()
        assert provider.cache_dir is None
        assert provider.adjust == "qfq"

    def test_init_with_cache(self, tmp_path: Path) -> None:
        """有缓存初始化"""
        cache_dir = tmp_path / "cache"
        provider = AKShareBarProvider(cache_dir=str(cache_dir))
        assert provider.cache_dir == str(cache_dir)
        assert cache_dir.exists()

    def test_init_with_custom_adjust(self) -> None:
        """自定义复权方式"""
        provider = AKShareBarProvider(adjust="hfq")
        assert provider.adjust == "hfq"


class TestConvertToBar:
    """数据转换测试"""

    def test_convert_daily_data(self) -> None:
        """转换日线数据"""
        provider = AKShareBarProvider()
        row = pd.Series({
            "日期": "2024-01-15",
            "开盘": 10.5,
            "最高": 11.0,
            "最低": 10.2,
            "收盘": 10.8,
            "成交量": 1000000,
            "成交额": 10500000,
        })
        bar = provider._convert_to_bar(row, "000001.SZ")
        assert bar.symbol == "000001.SZ"
        assert bar.open == 10.5
        assert bar.high == 11.0
        assert bar.low == 10.2
        assert bar.close == 10.8
        assert bar.volume == 1000000

    def test_convert_minute_data(self) -> None:
        """转换分钟数据"""
        provider = AKShareBarProvider()
        row = pd.Series({
            "时间": "2024-01-15 10:30:00",
            "开盘": 10.5,
            "最高": 10.6,
            "最低": 10.4,
            "收盘": 10.55,
            "成交量": 50000,
        })
        bar = provider._convert_to_bar(row, "000001.SZ")
        assert bar.symbol == "000001.SZ"
        assert bar.close == 10.55
        assert bar.volume == 50000

    def test_convert_invalid_format(self) -> None:
        """无效格式抛出异常"""
        provider = AKShareBarProvider()
        row = pd.Series({
            "price": 10.5,
            "qty": 1000,
        })
        with pytest.raises(ValueError, match="无法识别的数据格式"):
            provider._convert_to_bar(row, "000001.SZ")


class TestFingerprint:
    """数据指纹测试"""

    def test_fingerprint(self) -> None:
        """生成数据指纹"""
        provider = AKShareBarProvider(adjust="qfq")
        fp = provider.fingerprint()
        assert fp.provider == "AKShare"
        assert fp.adjust == "qfq"
        assert fp.schema == "bar_v1"


class TestLoadBarsWithMock:
    """使用 Mock 的数据加载测试"""

    @patch("aipha.data.akshare_provider.AKShareBarProvider._fetch_from_api")
    def test_load_bars_success(self, mock_fetch: MagicMock) -> None:
        """成功加载数据"""
        # 模拟 API 返回数据
        mock_df = pd.DataFrame({
            "日期": ["2024-01-15", "2024-01-16"],
            "开盘": [10.0, 10.5],
            "最高": [10.5, 11.0],
            "最低": [9.8, 10.3],
            "收盘": [10.3, 10.8],
            "成交量": [1000000, 1200000],
        })
        mock_fetch.return_value = mock_df

        provider = AKShareBarProvider()
        bars = provider.load_bars(
            symbol="000001.SZ",
            start=date(2024, 1, 15),
            end=date(2024, 1, 16),
            frequency="daily",
        )

        assert len(bars) == 2
        assert bars[0].close == 10.3
        assert bars[1].close == 10.8

    @patch("aipha.data.akshare_provider.AKShareBarProvider._fetch_from_api")
    def test_load_bars_empty(self, mock_fetch: MagicMock) -> None:
        """空数据返回空列表"""
        mock_fetch.return_value = pd.DataFrame()

        provider = AKShareBarProvider()
        bars = provider.load_bars(
            symbol="000001.SZ",
            start=date(2024, 1, 15),
            end=date(2024, 1, 16),
            frequency="daily",
        )

        assert bars == []

    def test_symbol_normalization(self) -> None:
        """股票代码标准化"""
        provider = AKShareBarProvider()
        # 带后缀的代码应该被正确处理
        with patch.object(provider, "_fetch_from_api", return_value=pd.DataFrame()):
            provider.load_bars(
                symbol="000001.SZ",
                start=date(2024, 1, 1),
                end=date(2024, 1, 31),
            )


class TestCachePath:
    """缓存路径测试"""

    def test_cache_path_format(self, tmp_path: Path) -> None:
        """缓存路径格式"""
        provider = AKShareBarProvider(cache_dir=str(tmp_path), adjust="qfq")
        path = provider._cache_path(
            code="000001",
            start=date(2024, 1, 1),
            end=date(2024, 1, 31),
            frequency="daily",
        )
        assert "000001" in str(path)
        assert "2024-01-01" in str(path)
        assert "daily" in str(path)
        assert "qfq" in str(path)
        assert path.suffix == ".parquet"
