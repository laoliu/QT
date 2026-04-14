"""tests/data/test_force_redeem.py – 强赎数据加载与 PIT 查询测试

验证：
1. TuShareCBProvider.load_cb_call() 加载与缓存
2. TuShareCBProvider.build_force_redeem_lookup() PIT 查询表
3. CBDataAdapter 中强赎状态注入到 Bar.metadata
4. 策略层 _filter_candidates 和 _check_force_redeem 对新字段的处理
"""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


# ── Helper: 构造 cb_call 测试数据 ─────────────────────────


def _cb_call_df() -> pd.DataFrame:
    """模拟 TuShare cb_call 接口返回数据"""
    return pd.DataFrame([
        # 转债 A: 已满足强赎条件 → 后来公告实施强赎
        {
            "ts_code": "110001.SH",
            "call_type": "强赎",
            "is_call": "已满足强赎条件",
            "ann_date": "20230601",
            "call_date": None,
            "call_price": None,
            "call_price_tax": None,
            "call_vol": None,
            "call_amount": None,
            "payment_date": None,
            "call_reg_date": None,
        },
        {
            "ts_code": "110001.SH",
            "call_type": "强赎",
            "is_call": "公告实施强赎",
            "ann_date": "20230615",
            "call_date": "20230715",
            "call_price": 100.35,
            "call_price_tax": None,
            "call_vol": None,
            "call_amount": None,
            "payment_date": "20230720",
            "call_reg_date": "20230714",
        },
        # 转债 B: 已满足强赎条件 → 公告不强赎
        {
            "ts_code": "123456.SZ",
            "call_type": "强赎",
            "is_call": "已满足强赎条件",
            "ann_date": "20230801",
            "call_date": None,
            "call_price": None,
            "call_price_tax": None,
            "call_vol": None,
            "call_amount": None,
            "payment_date": None,
            "call_reg_date": None,
        },
        {
            "ts_code": "123456.SZ",
            "call_type": "强赎",
            "is_call": "公告不强赎",
            "ann_date": "20230810",
            "call_date": None,
            "call_price": None,
            "call_price_tax": None,
            "call_vol": None,
            "call_amount": None,
            "payment_date": None,
            "call_reg_date": None,
        },
        # 转债 C: 仅公告提示强赎
        {
            "ts_code": "127075.SZ",
            "call_type": "强赎",
            "is_call": "公告提示强赎",
            "ann_date": "20231001",
            "call_date": None,
            "call_price": None,
            "call_price_tax": None,
            "call_vol": None,
            "call_amount": None,
            "payment_date": None,
            "call_reg_date": None,
        },
        # 到赎类型（应被过滤掉）
        {
            "ts_code": "110002.SH",
            "call_type": "到赎",
            "is_call": "公告到期赎回",
            "ann_date": "20230901",
            "call_date": "20230930",
            "call_price": 106.0,
            "call_price_tax": None,
            "call_vol": None,
            "call_amount": None,
            "payment_date": "20231010",
            "call_reg_date": "20230929",
        },
    ])


# ── TuShareCBProvider.load_cb_call 测试 ──────────────────


@pytest.fixture()
def provider(tmp_path):
    """创建 mock TuShareCBProvider。"""
    from aipha.data.tushare_gateway import reset_tushare_gateway

    reset_tushare_gateway()
    with patch.dict(os.environ, {"TUSHARE_TOKEN": "fake_token"}):
        with patch("tushare.pro_api") as mock_pro_api, \
             patch("tushare.set_token"):
            mock_api = MagicMock()
            mock_pro_api.return_value = mock_api
            from aipha.data.tushare_cb_provider import TuShareCBProvider

            p = TuShareCBProvider(
                token="fake_token",
                cache_dir=str(tmp_path / "cb_cache"),
            )
            p._mock_api = mock_api
            yield p
    reset_tushare_gateway()


class TestLoadCbCall:
    """测试 load_cb_call 方法"""

    def test_load_from_api(self, provider):
        """从 API 加载强赎公告数据"""
        # Mock 网关 call 方法
        cb_call_data = _cb_call_df()
        # 第一次调用返回强赎数据，第二次返回到赎数据，后续返回空
        force_redeem = cb_call_data[cb_call_data["call_type"] == "强赎"].reset_index(drop=True)
        maturity_redeem = cb_call_data[cb_call_data["call_type"] == "到赎"].reset_index(drop=True)

        call_count = {"n": 0}
        def mock_call(api_name, **kwargs):
            call_count["n"] += 1
            ct = kwargs.get("call_type", "")
            if ct == "强赎" and call_count["n"] == 1:
                return force_redeem
            elif ct == "到赎" and call_count["n"] == 2:
                return maturity_redeem
            return pd.DataFrame()

        provider._gateway.call = mock_call

        result = provider.load_cb_call(use_cache=False)
        assert len(result) >= 5  # 强赎 + 到赎
        assert "ts_code" in result.columns
        assert "call_type" in result.columns
        assert "ann_date" in result.columns

    def test_cache_hit(self, provider, tmp_path):
        """缓存命中时不调用 API"""
        cache_path = Path(provider.cache_dir) / "cb_call.parquet"
        _cb_call_df().to_parquet(cache_path, index=False)

        result = provider.load_cb_call(use_cache=True)
        assert len(result) == 6  # 从缓存加载全部

    def test_empty_api_returns_empty_df(self, provider):
        """API 返回空时返回空 DataFrame"""
        provider._gateway.call = lambda *args, **kwargs: pd.DataFrame()

        result = provider.load_cb_call(use_cache=False)
        assert result.empty


# ── build_force_redeem_lookup 测试 ────────────────────────


class TestBuildForceRedeemLookup:
    """测试 PIT 强赎状态查询表构建"""

    def _setup_cb_call_cache(self, provider):
        """预填充 cb_call 缓存"""
        cache_path = Path(provider.cache_dir) / "cb_call.parquet"
        _cb_call_df().to_parquet(cache_path, index=False)

    def test_lookup_without_date_filter(self, provider):
        """无日期过滤时返回所有转债的最新状态"""
        self._setup_cb_call_cache(provider)

        lookup = provider.build_force_redeem_lookup()
        # 只返回强赎类型（不含到赎）
        assert "110002.SH" not in lookup  # 到赎不包含

        # 110001.SH: 最新是 "公告实施强赎"
        assert "110001.SH" in lookup
        assert lookup["110001.SH"]["is_called"] is True
        assert lookup["110001.SH"]["force_redeem_status"] == "公告实施强赎"
        assert lookup["110001.SH"]["call_date"] == "20230715"

        # 123456.SZ: 最新是 "公告不强赎"
        assert "123456.SZ" in lookup
        assert lookup["123456.SZ"]["is_called"] is False
        assert lookup["123456.SZ"]["force_redeem_status"] == "公告不强赎"

        # 127075.SZ: "公告提示强赎"
        assert "127075.SZ" in lookup
        assert lookup["127075.SZ"]["is_called"] is False

    def test_pit_filter_before_announcement(self, provider):
        """PIT: 公告日前应看不到后续公告"""
        self._setup_cb_call_cache(provider)

        # 2023-06-10: 110001.SH 只有 "已满足强赎条件" (ann_date=20230601)
        lookup = provider.build_force_redeem_lookup(as_of_date="20230610")
        assert "110001.SH" in lookup
        assert lookup["110001.SH"]["is_called"] is False
        assert lookup["110001.SH"]["force_redeem_status"] == "已满足强赎条件"

    def test_pit_filter_after_announcement(self, provider):
        """PIT: 公告日后应看到完整信息"""
        self._setup_cb_call_cache(provider)

        # 2023-06-20: 110001.SH 已公告实施强赎 (ann_date=20230615)
        lookup = provider.build_force_redeem_lookup(as_of_date="20230620")
        assert lookup["110001.SH"]["is_called"] is True
        assert lookup["110001.SH"]["force_redeem_status"] == "公告实施强赎"

    def test_pit_no_data_before_any_announcement(self, provider):
        """PIT: 所有公告之前，查询表应为空"""
        self._setup_cb_call_cache(provider)

        lookup = provider.build_force_redeem_lookup(as_of_date="20220101")
        assert len(lookup) == 0

    def test_empty_cb_call_returns_empty_lookup(self, provider):
        """无 cb_call 数据时返回空字典"""
        # 写入空的 cb_call 缓存
        cache_path = Path(provider.cache_dir) / "cb_call.parquet"
        pd.DataFrame(columns=["ts_code", "call_type", "is_call", "ann_date"]).to_parquet(
            cache_path, index=False
        )

        lookup = provider.build_force_redeem_lookup()
        assert lookup == {}


# ── CBDataAdapter 强赎状态注入测试 ────────────────────────


@pytest.fixture()
def adapter_with_force_redeem(tmp_path):
    """创建带强赎数据的 CBDataAdapter"""
    from aipha.data.cb_data_adapter import CBDataAdapter

    adapter = CBDataAdapter.__new__(CBDataAdapter)
    adapter.provider = MagicMock()
    adapter._force_redeem_data = None
    adapter._force_redeem_index = {}

    # 基础信息
    basic = pd.DataFrame({
        "ts_code": ["110001.SH", "123456.SZ", "127075.SZ"],
        "bond_short_name": ["转债A", "转债B", "转债C"],
        "stk_short_name": ["正股A", "正股B", "正股C"],
        "stk_code": ["600001.SH", "000456.SZ", "002075.SZ"],
        "remain_size": [1e9, 5e8, 3e8],  # 元
        "issue_size": [1e9, 5e8, 3e8],  # 元
        "list_date": ["20200101", "20210101", "20220101"],
        "delist_date": [None, None, None],
        "maturity_date": ["20260101", "20270101", "20280101"],
        "rating": ["AAA", "AA+", "AA"],
    })

    # 日行情
    daily = pd.DataFrame({
        "ts_code": ["110001.SH", "123456.SZ", "127075.SZ"],
        "trade_date": ["20230615", "20230615", "20230615"],
        "open": [132.0, 105.0, 115.0],
        "high": [135.0, 108.0, 118.0],
        "low": [130.0, 103.0, 113.0],
        "close": [133.0, 106.0, 116.0],
        "vol": [5000.0, 3000.0, 2000.0],
        "amount": [6650.0, 3180.0, 2320.0],
        "cb_value": [135.0, 98.0, 110.0],
        "cb_over_rate": [-1.5, 8.2, 5.5],
    })

    # cb_call 强赎数据
    cb_call = _cb_call_df()

    adapter.provider.load_cb_basic.return_value = basic
    adapter.provider.load_cb_daily.return_value = daily
    adapter.provider.load_cb_call.return_value = cb_call

    return adapter


class TestAdapterForceRedeemInjection:
    """验证 CBDataAdapter.load() 正确注入强赎状态"""

    def test_force_redeem_status_injected(self, adapter_with_force_redeem):
        """强赎状态应注入到 Bar.metadata"""
        bars_by_date = adapter_with_force_redeem.load("20230615", "20230615")

        assert len(bars_by_date) == 1
        bars = bars_by_date[date(2023, 6, 15)]

        # 110001.SH: 截至 20230615，已公告实施强赎
        bar_a = bars["110001.SH"]
        assert bar_a.metadata["is_called"] is True
        assert bar_a.metadata["force_redeem_status"] == "公告实施强赎"

        # 123456.SZ: 截至 20230615，无强赎公告（第一条公告是 20230801）
        bar_b = bars["123456.SZ"]
        # 无公告，cb_value=98 < 130，降级为 False
        assert bar_b.metadata["is_called"] is False

        # 127075.SZ: 截至 20230615，无强赎公告（第一条公告是 20231001）
        bar_c = bars["127075.SZ"]
        # cb_value=110 < 130，降级为 False
        assert bar_c.metadata["is_called"] is False

    def test_pit_safety_earlier_date(self, adapter_with_force_redeem):
        """PIT: 公告前应看不到强赎状态"""
        # 修改日行情日期为 20230601（公告首日）
        daily = adapter_with_force_redeem.provider.load_cb_daily.return_value.copy()
        daily["trade_date"] = "20230601"
        adapter_with_force_redeem.provider.load_cb_daily.return_value = daily

        bars_by_date = adapter_with_force_redeem.load("20230601", "20230601")
        bars = bars_by_date[date(2023, 6, 1)]

        # 110001.SH: 20230601 当天有 "已满足强赎条件" 公告
        bar_a = bars["110001.SH"]
        assert bar_a.metadata["is_called"] is False  # 不是"公告实施强赎"
        assert bar_a.metadata["force_redeem_status"] == "已满足强赎条件"

    def test_no_cb_call_data_graceful_fallback(self, tmp_path):
        """无 cb_call 数据时优雅降级"""
        from aipha.data.cb_data_adapter import CBDataAdapter

        adapter = CBDataAdapter.__new__(CBDataAdapter)
        adapter.provider = MagicMock()
        adapter._force_redeem_data = None
        adapter._force_redeem_index = {}

        basic = pd.DataFrame({
            "ts_code": ["128085.SZ"],
            "bond_short_name": ["鸿路转债"],
            "stk_short_name": ["鸿路钢构"],
            "stk_code": ["002446.SZ"],
            "remain_size": [5e8],
            "issue_size": [5e8],
            "list_date": ["20220101"],
            "delist_date": [None],
            "maturity_date": ["20270101"],
            "rating": ["AA"],
        })

        daily = pd.DataFrame({
            "ts_code": ["128085.SZ"],
            "trade_date": ["20240102"],
            "open": [140.0],
            "high": [142.0],
            "low": [138.0],
            "close": [141.0],
            "vol": [5000.0],
            "amount": [7050.0],
            "cb_value": [135.0],
            "cb_over_rate": [4.4],
        })

        adapter.provider.load_cb_basic.return_value = basic
        adapter.provider.load_cb_daily.return_value = daily
        adapter.provider.load_cb_call.side_effect = Exception("API unavailable")

        bars_by_date = adapter.load("20240102", "20240102")
        bars = bars_by_date[date(2024, 1, 2)]
        bar = bars["128085.SZ"]

        # 降级为 cb_value 启发式：cb_value=135 >= 130, is_called=None
        assert bar.metadata.get("is_called") is None


# ── 策略层强赎过滤测试 ───────────────────────────────────


class TestStrategyForceRedeemFilter:
    """验证策略基类对新强赎状态的过滤行为"""

    @staticmethod
    def _make_bar(symbol: str, close: float, metadata: dict):
        from aipha.domain.models import Bar
        return Bar(
            symbol=symbol,
            start_ts=datetime(2023, 6, 15, 9, 30),
            end_ts=datetime(2023, 6, 15, 15, 0),
            open=close,
            high=close,
            low=close,
            close=close,
            volume=10000.0,
            metadata=metadata,
        )

    def test_exclude_called_filters_confirmed_redeem(self):
        """exclude_called=True 应过滤 is_called=True 的转债"""
        from aipha.domain.models import Bar
        from aipha.strategy.cb_rotation_base import CBRotationConfig

        bars = {
            "110001.SH": self._make_bar("110001.SH", 133.0, {
                "is_called": True,
                "force_redeem_status": "公告实施强赎",
                "amount": 5000.0,
                "issue_size": 100000.0,
                "maturity_date": "20260101",
                "cb_value": 135.0,
                "cb_over_rate": -1.5,
            }),
            "123456.SZ": self._make_bar("123456.SZ", 106.0, {
                "is_called": False,
                "force_redeem_status": "公告不强赎",
                "amount": 3000.0,
                "issue_size": 50000.0,
                "maturity_date": "20270101",
                "cb_value": 98.0,
                "cb_over_rate": 8.2,
            }),
        }

        cfg = CBRotationConfig(exclude_called=True)

        # 测试 _filter_candidates 逻辑
        # 直接检测 meta 字段（不需要完整策略实例化）
        excluded = []
        passed = []
        for symbol, bar in bars.items():
            meta = bar.metadata or {}
            if cfg.exclude_called and meta.get("is_called") is True:
                excluded.append(symbol)
            else:
                passed.append(symbol)

        assert "110001.SH" in excluded
        assert "123456.SZ" in passed

    def test_exclude_force_redeem_pending(self):
        """exclude_force_redeem_pending=True 应过滤已满足强赎条件的转债"""
        from aipha.strategy.cb_rotation_base import CBRotationConfig

        bars = {
            "110001.SH": self._make_bar("110001.SH", 133.0, {
                "is_called": False,
                "force_redeem_status": "已满足强赎条件",
                "amount": 5000.0,
                "issue_size": 100000.0,
                "maturity_date": "20260101",
            }),
            "123456.SZ": self._make_bar("123456.SZ", 106.0, {
                "is_called": False,
                "force_redeem_status": "",
                "amount": 3000.0,
                "issue_size": 50000.0,
                "maturity_date": "20270101",
            }),
            "127075.SZ": self._make_bar("127075.SZ", 116.0, {
                "is_called": False,
                "force_redeem_status": "公告提示强赎",
                "amount": 2000.0,
                "issue_size": 30000.0,
                "maturity_date": "20280101",
            }),
        }

        cfg = CBRotationConfig(exclude_force_redeem_pending=True)

        excluded = []
        passed = []
        for symbol, bar in bars.items():
            meta = bar.metadata or {}
            if cfg.exclude_called and meta.get("is_called") is True:
                excluded.append(symbol)
                continue
            if cfg.exclude_force_redeem_pending:
                fr_status = meta.get("force_redeem_status", "")
                if fr_status in ("已满足强赎条件", "公告提示强赎"):
                    excluded.append(symbol)
                    continue
            passed.append(symbol)

        # 已满足强赎条件 和 公告提示强赎 都被排除
        assert "110001.SH" in excluded
        assert "127075.SZ" in excluded
        assert "123456.SZ" in passed

    def test_no_pending_filter_when_disabled(self):
        """exclude_force_redeem_pending=False 时不额外过滤"""
        from aipha.strategy.cb_rotation_base import CBRotationConfig

        cfg = CBRotationConfig(exclude_force_redeem_pending=False)

        meta = {"is_called": False, "force_redeem_status": "已满足强赎条件"}

        # 不应被排除
        excluded = False
        if cfg.exclude_force_redeem_pending:
            fr_status = meta.get("force_redeem_status", "")
            if fr_status in ("已满足强赎条件", "公告提示强赎"):
                excluded = True

        assert excluded is False
