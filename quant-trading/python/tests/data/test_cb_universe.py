"""CBUniverseProvider 测试

测试覆盖：
- CBUniverseConfig 创建与 from_dict
- CBUniverseProvider 基本操作（fetch/refresh/needs_refresh）
- UniverseRefreshResult 变更检测
- 降级策略（数据源异常时返回上次结果）
- 黑名单过滤
- 手动覆盖
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from aipha.data.cb_universe import (
    CBUniverseConfig,
    CBUniverseProvider,
    UniversePolicy,
    UniverseRefreshResult,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def default_config() -> CBUniverseConfig:
    return CBUniverseConfig(
        enabled=True,
        max_price=150.0,
        min_amount=0.5,
        exclude_called=True,
        refresh_interval_sec=3600,
    )


@pytest.fixture
def provider(default_config: CBUniverseConfig) -> CBUniverseProvider:
    return CBUniverseProvider(default_config)


def _make_mock_bond(code: str, name: str = "", price: float = 105.0) -> MagicMock:
    """创建 Mock Bond 对象"""
    bond = MagicMock()
    bond.code = code
    bond.name = name or f"测试转债{code[-3:]}"
    bond.price = price
    bond.remain_amount = 2.0
    bond.rating = "AA"
    return bond


# ---------------------------------------------------------------------------
# CBUniverseConfig 测试
# ---------------------------------------------------------------------------


class TestCBUniverseConfig:
    def test_defaults(self):
        config = CBUniverseConfig()
        assert config.enabled is True
        assert config.max_price == 200.0
        assert config.min_amount == 0.5
        assert config.exclude_unrated is False
        assert config.exclude_called is True
        assert config.refresh_interval_sec == 3600

    def test_from_dict(self):
        data = {
            "enabled": True,
            "max_price": 130.0,
            "min_amount": 1.0,
            "min_rating": "AA+",
            "exclude_unrated": True,
            "exclude_codes": ["111111"],
            "max_symbols": 50,
            "refresh_interval_sec": 1800,
        }
        config = CBUniverseConfig.from_dict(data)
        assert config.max_price == 130.0
        assert config.min_amount == 1.0
        assert config.min_rating == "AA+"
        assert config.exclude_unrated is True
        assert config.exclude_codes == ["111111"]
        assert config.max_symbols == 50
        assert config.refresh_interval_sec == 1800

    def test_from_empty_dict(self):
        config = CBUniverseConfig.from_dict({})
        assert config.enabled is True
        assert config.max_price == 200.0


# ---------------------------------------------------------------------------
# CBUniverseProvider 测试
# ---------------------------------------------------------------------------


class TestCBUniverseProvider:
    def test_init(self, provider: CBUniverseProvider):
        assert provider.symbol_count == 0
        assert provider.current_symbols == []
        assert provider.last_refresh_time is None

    def test_needs_refresh_initial(self, provider: CBUniverseProvider):
        """首次应该需要刷新"""
        assert provider.needs_refresh() is True

    def test_needs_refresh_disabled(self):
        """禁用时不需要刷新"""
        config = CBUniverseConfig(enabled=False)
        provider = CBUniverseProvider(config)
        assert provider.needs_refresh() is False

    def test_needs_refresh_after_set(self, provider: CBUniverseProvider):
        """手动设置后不立即需要刷新"""
        provider.set_symbols_override(["127045", "128054"])
        assert provider.needs_refresh() is False

    def test_needs_refresh_after_interval(self, provider: CBUniverseProvider):
        """超过刷新间隔后需要刷新"""
        provider.set_symbols_override(["127045"])
        # 模拟时间过去
        provider._last_refresh = datetime.now() - timedelta(hours=2)
        assert provider.needs_refresh() is True

    @patch("aipha.data.akshare_cb_provider.AKShareCBProvider")
    def test_fetch_symbols(self, mock_cls, provider: CBUniverseProvider):
        """正常获取标的"""
        mock_provider = MagicMock()
        mock_cls.return_value = mock_provider

        bonds = [
            _make_mock_bond("127045"),
            _make_mock_bond("128054"),
            _make_mock_bond("113050"),
        ]
        mock_provider.load_bond_list_full.return_value = bonds

        symbols = provider.fetch_symbols()
        assert symbols == ["127045", "128054", "113050"]

    @patch("aipha.data.akshare_cb_provider.AKShareCBProvider")
    def test_fetch_symbols_min_rating_filter(self, mock_cls):
        config = CBUniverseConfig(enabled=True, min_rating="AA+")
        provider = CBUniverseProvider(config)

        mock_provider = MagicMock()
        mock_cls.return_value = mock_provider

        b1 = _make_mock_bond("127045")
        b1.rating = "AA"
        b2 = _make_mock_bond("128054")
        b2.rating = "AA+"
        b3 = _make_mock_bond("113050")
        b3.rating = "AAA"

        mock_provider.load_bond_list_full.return_value = [b1, b2, b3]

        symbols = provider.fetch_symbols()
        assert symbols == ["128054", "113050"]

    @patch("aipha.data.akshare_cb_provider.AKShareCBProvider")
    def test_fetch_symbols_exclude_unrated(self, mock_cls):
        config = CBUniverseConfig(enabled=True, exclude_unrated=True)
        provider = CBUniverseProvider(config)

        mock_provider = MagicMock()
        mock_cls.return_value = mock_provider

        b1 = _make_mock_bond("127045")
        b1.rating = ""
        b2 = _make_mock_bond("128054")
        b2.rating = "AA"

        mock_provider.load_bond_list_full.return_value = [b1, b2]

        symbols = provider.fetch_symbols()
        assert symbols == ["128054"]

    @patch("aipha.data.akshare_cb_provider.AKShareCBProvider")
    def test_fetch_symbols_with_blacklist(self, mock_cls):
        """黑名单过滤"""
        config = CBUniverseConfig(
            enabled=True,
            exclude_codes=["128054"],
        )
        provider = CBUniverseProvider(config)

        mock_provider = MagicMock()
        mock_cls.return_value = mock_provider
        bonds = [
            _make_mock_bond("127045"),
            _make_mock_bond("128054"),
            _make_mock_bond("113050"),
        ]
        mock_provider.load_bond_list_full.return_value = bonds

        symbols = provider.fetch_symbols()
        assert "128054" not in symbols
        assert len(symbols) == 2

    @patch("aipha.data.akshare_cb_provider.AKShareCBProvider")
    def test_fetch_excludes_called(self, mock_cls, provider: CBUniverseProvider):
        """排除已强赎"""
        mock_provider = MagicMock()
        mock_cls.return_value = mock_provider

        bonds = [
            _make_mock_bond("127045", name="测试转债"),
            _make_mock_bond("128054", name="某某强赎"),
            _make_mock_bond("113050", name="已赎回转债"),
        ]
        mock_provider.load_bond_list_full.return_value = bonds

        symbols = provider.fetch_symbols()
        assert "127045" in symbols
        assert "128054" not in symbols  # 包含"强赎"
        assert "113050" not in symbols  # 包含"已赎回"

    @patch("aipha.data.akshare_cb_provider.AKShareCBProvider")
    def test_fetch_max_symbols(self, mock_cls):
        """限制返回数量"""
        config = CBUniverseConfig(enabled=True, max_symbols=2)
        provider = CBUniverseProvider(config)

        mock_provider = MagicMock()
        mock_cls.return_value = mock_provider
        bonds = [_make_mock_bond(f"12{i:04d}") for i in range(10)]
        mock_provider.load_bond_list_full.return_value = bonds

        symbols = provider.fetch_symbols()
        assert len(symbols) == 2

    @patch("aipha.data.akshare_cb_provider.AKShareCBProvider")
    def test_fetch_error_returns_last(self, mock_cls, provider: CBUniverseProvider):
        """数据源异常时返回上次结果"""
        # 先设置初始值
        provider.set_symbols_override(["127045", "128054"])

        mock_provider = MagicMock()
        mock_cls.return_value = mock_provider
        mock_provider.load_bond_list_full.side_effect = Exception("网络异常")

        symbols = provider.fetch_symbols()
        assert symbols == ["127045", "128054"]  # 降级到上次结果

    @patch("aipha.data.akshare_cb_provider.AKShareCBProvider")
    def test_refresh_detects_changes(self, mock_cls, provider: CBUniverseProvider):
        """刷新检测到变更"""
        provider.set_symbols_override(["127045", "128054"])

        mock_provider = MagicMock()
        mock_cls.return_value = mock_provider
        bonds = [
            _make_mock_bond("127045"),
            _make_mock_bond("113050"),
        ]
        mock_provider.load_bond_list_full.return_value = bonds

        result = provider.refresh()
        assert isinstance(result, UniverseRefreshResult)
        assert "113050" in result.added
        assert "128054" in result.removed
        assert result.error is None
        assert result.total_after_filter == 2

    @patch("aipha.data.akshare_cb_provider.AKShareCBProvider")
    def test_refresh_no_changes(self, mock_cls, provider: CBUniverseProvider):
        """刷新无变更"""
        provider.set_symbols_override(["127045", "128054"])

        mock_provider = MagicMock()
        mock_cls.return_value = mock_provider
        bonds = [
            _make_mock_bond("127045"),
            _make_mock_bond("128054"),
        ]
        mock_provider.load_bond_list_full.return_value = bonds

        result = provider.refresh()
        assert result.added == []
        assert result.removed == []

    def test_set_symbols_override(self, provider: CBUniverseProvider):
        """手动覆盖"""
        provider.set_symbols_override(["A", "B", "C"])
        assert provider.symbol_count == 3
        assert provider.current_symbols == ["A", "B", "C"]
        assert provider.last_refresh_time is not None

    def test_get_status(self, provider: CBUniverseProvider):
        """状态查询"""
        status = provider.get_status()
        assert status["enabled"] is True
        assert status["symbol_count"] == 0
        assert status["refresh_count"] == 0

    def test_get_metadata_after_fetch(self, provider: CBUniverseProvider):
        """获取元数据"""
        # 初始为空
        meta = provider.get_metadata()
        assert meta == {}


# ---------------------------------------------------------------------------
# UniversePolicy ABC 测试
# ---------------------------------------------------------------------------


class TestUniversePolicy:
    def test_is_abstract(self):
        """UniversePolicy 是抽象类"""
        with pytest.raises(TypeError):
            UniversePolicy()  # type: ignore

    def test_concrete_implementation(self):
        """CBUniverseProvider 实现了 UniversePolicy"""
        provider = CBUniverseProvider()
        assert isinstance(provider, UniversePolicy)
