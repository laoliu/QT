"""AKShare 可转债数据提供者测试

测试集思录数据源的可转债数据获取功能。
"""

from __future__ import annotations

from decimal import Decimal
from datetime import date
from unittest.mock import Mock, patch

import pandas as pd
import pytest

from aipha.data.akshare_cb_provider import (
    AKShareCBProvider,
    JSLBondSnapshot,
    JSLRedeemInfo,
    JSLPriceAdjustment,
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def sample_jsl_data() -> pd.DataFrame:
    """模拟集思录可转债数据"""
    return pd.DataFrame([
        {
            "代码": "113676",
            "转债名称": "华钰转债",
            "现价": 105.5,
            "涨跌幅": 1.5,
            "正股代码": "601020",
            "正股名称": "华钰矿业",
            "正股价": 8.5,
            "正股涨跌": 2.1,
            "正股PB": 1.8,
            "转股价": 7.5,
            "转股价值": 113.33,
            "转股溢价率": -6.9,
            "债券评级": "AA",
            "回售触发价": 5.25,
            "强赎触发价": 9.75,
            "转债占比": 10.5,
            "到期时间": "2028-06-15",
            "剩余年限": 3.5,
            "剩余规模": 5.8,
            "成交额": 12000,
            "换手率": 8.5,
            "到期税前收益": 1.2,
            "双低": 98.6,
        },
        {
            "代码": "123456",
            "转债名称": "测试转债",
            "现价": 130.0,
            "涨跌幅": -0.5,
            "正股代码": "000001",
            "正股名称": "测试股票",
            "正股价": 15.0,
            "正股涨跌": -1.0,
            "正股PB": 2.5,
            "转股价": 12.0,
            "转股价值": 125.0,
            "转股溢价率": 4.0,
            "债券评级": "AA+",
            "回售触发价": 8.4,
            "强赎触发价": 15.6,
            "转债占比": 5.0,
            "到期时间": "2029-12-31",
            "剩余年限": 5.0,
            "剩余规模": 10.0,
            "成交额": 50000,
            "换手率": 15.0,
            "到期税前收益": 0.5,
            "双低": 134.0,
        },
    ])


@pytest.fixture
def sample_redeem_data() -> pd.DataFrame:
    """模拟强赎数据"""
    return pd.DataFrame([
        {
            "代码": "113676",
            "名称": "华钰转债",
            "现价": 155.5,
            "正股代码": "601020",
            "正股名称": "华钰矿业",
            "规模": 8.0,
            "剩余规模": 0.5,
            "转股起始日": "2022-01-15",
            "最后交易日": "2025-01-20",
            "到期日": "2028-06-15",
            "转股价": 7.5,
            "强赎触发比": 130.0,
            "强赎触发价": 9.75,
            "正股价": 12.0,
            "强赎价": 155.0,
            "强赎天计数": 15,
            "强赎条款": "连续30个交易日中有15个交易日收盘价≥130%",
            "强赎状态": "已触发",
        },
    ])


@pytest.fixture
def sample_adj_data() -> pd.DataFrame:
    """模拟转股价调整数据"""
    return pd.DataFrame([
        {
            "转债名称": "华钰转债",
            "股东大会日": "2024-05-15",
            "下修前转股价": 10.0,
            "下修后转股价": 7.5,
            "新转股价生效日期": "2024-06-01",
            "下修底价": 6.5,
        },
    ])


@pytest.fixture
def mock_akshare():
    """Mock akshare 模块"""
    with patch.dict("sys.modules", {"akshare": Mock()}):
        yield


# ============================================================
# JSLBondSnapshot 数据类测试
# ============================================================

class TestJSLBondSnapshot:
    """JSLBondSnapshot 数据类测试"""

    def test_ts_code_sh(self):
        """测试沪市代码转换"""
        bond = JSLBondSnapshot(
            code="113676",
            name="华钰转债",
            price=Decimal("105.5"),
            change_pct=1.5,
            stock_code="601020",
            stock_name="华钰矿业",
            stock_price=Decimal("8.5"),
            stock_change_pct=2.1,
            stock_pb=1.8,
            conversion_price=Decimal("7.5"),
            conversion_value=Decimal("113.33"),
            premium_rate=-6.9,
            rating="AA",
            put_trigger_price=Decimal("5.25"),
            call_trigger_price=Decimal("9.75"),
            cb_over_stock_pct=10.5,
            maturity_date=date(2028, 6, 15),
            years_to_maturity=3.5,
            remain_amount=Decimal("5.8"),
            turnover=Decimal("12000"),
            turnover_rate=8.5,
            ytm=1.2,
            double_low=98.6,
        )
        assert bond.ts_code == "113676.SH"

    def test_ts_code_sz(self):
        """测试深市代码转换"""
        bond = JSLBondSnapshot(
            code="123456",
            name="测试转债",
            price=Decimal("100"),
            change_pct=0,
            stock_code="000001",
            stock_name="测试",
            stock_price=Decimal("10"),
            stock_change_pct=0,
            stock_pb=1.0,
            conversion_price=Decimal("10"),
            conversion_value=Decimal("100"),
            premium_rate=0,
            rating="AA",
            put_trigger_price=None,
            call_trigger_price=None,
            cb_over_stock_pct=0,
            maturity_date=date(2030, 1, 1),
            years_to_maturity=5,
            remain_amount=Decimal("10"),
            turnover=Decimal("0"),
            turnover_rate=0,
            ytm=0,
            double_low=100,
        )
        assert bond.ts_code == "123456.SZ"

    def test_is_low_price(self):
        """测试低价判断"""
        bond_low = JSLBondSnapshot(
            code="1", name="", price=Decimal("105"), change_pct=0,
            stock_code="", stock_name="", stock_price=Decimal("0"),
            stock_change_pct=0, stock_pb=0, conversion_price=Decimal("0"),
            conversion_value=Decimal("0"), premium_rate=0, rating="",
            put_trigger_price=None, call_trigger_price=None, cb_over_stock_pct=0,
            maturity_date=date(2030, 1, 1), years_to_maturity=0,
            remain_amount=Decimal("0"), turnover=Decimal("0"), turnover_rate=0,
            ytm=0, double_low=100,
        )
        bond_high = JSLBondSnapshot(
            code="1", name="", price=Decimal("115"), change_pct=0,
            stock_code="", stock_name="", stock_price=Decimal("0"),
            stock_change_pct=0, stock_pb=0, conversion_price=Decimal("0"),
            conversion_value=Decimal("0"), premium_rate=0, rating="",
            put_trigger_price=None, call_trigger_price=None, cb_over_stock_pct=0,
            maturity_date=date(2030, 1, 1), years_to_maturity=0,
            remain_amount=Decimal("0"), turnover=Decimal("0"), turnover_rate=0,
            ytm=0, double_low=100,
        )
        assert bond_low.is_low_price is True
        assert bond_high.is_low_price is False

    def test_is_low_premium(self):
        """测试低溢价判断"""
        bond_low = JSLBondSnapshot(
            code="1", name="", price=Decimal("100"), change_pct=0,
            stock_code="", stock_name="", stock_price=Decimal("0"),
            stock_change_pct=0, stock_pb=0, conversion_price=Decimal("0"),
            conversion_value=Decimal("0"), premium_rate=15.0, rating="",
            put_trigger_price=None, call_trigger_price=None, cb_over_stock_pct=0,
            maturity_date=date(2030, 1, 1), years_to_maturity=0,
            remain_amount=Decimal("0"), turnover=Decimal("0"), turnover_rate=0,
            ytm=0, double_low=100,
        )
        bond_high = JSLBondSnapshot(
            code="1", name="", price=Decimal("100"), change_pct=0,
            stock_code="", stock_name="", stock_price=Decimal("0"),
            stock_change_pct=0, stock_pb=0, conversion_price=Decimal("0"),
            conversion_value=Decimal("0"), premium_rate=25.0, rating="",
            put_trigger_price=None, call_trigger_price=None, cb_over_stock_pct=0,
            maturity_date=date(2030, 1, 1), years_to_maturity=0,
            remain_amount=Decimal("0"), turnover=Decimal("0"), turnover_rate=0,
            ytm=0, double_low=100,
        )
        assert bond_low.is_low_premium is True
        assert bond_high.is_low_premium is False

    def test_is_double_low(self):
        """测试双低判断"""
        bond_low = JSLBondSnapshot(
            code="1", name="", price=Decimal("100"), change_pct=0,
            stock_code="", stock_name="", stock_price=Decimal("0"),
            stock_change_pct=0, stock_pb=0, conversion_price=Decimal("0"),
            conversion_value=Decimal("0"), premium_rate=0, rating="",
            put_trigger_price=None, call_trigger_price=None, cb_over_stock_pct=0,
            maturity_date=date(2030, 1, 1), years_to_maturity=0,
            remain_amount=Decimal("0"), turnover=Decimal("0"), turnover_rate=0,
            ytm=0, double_low=125.0,
        )
        bond_high = JSLBondSnapshot(
            code="1", name="", price=Decimal("100"), change_pct=0,
            stock_code="", stock_name="", stock_price=Decimal("0"),
            stock_change_pct=0, stock_pb=0, conversion_price=Decimal("0"),
            conversion_value=Decimal("0"), premium_rate=0, rating="",
            put_trigger_price=None, call_trigger_price=None, cb_over_stock_pct=0,
            maturity_date=date(2030, 1, 1), years_to_maturity=0,
            remain_amount=Decimal("0"), turnover=Decimal("0"), turnover_rate=0,
            ytm=0, double_low=135.0,
        )
        assert bond_low.is_double_low is True
        assert bond_high.is_double_low is False


# ============================================================
# AKShareCBProvider 测试
# ============================================================

class TestAKShareCBProvider:
    """AKShareCBProvider 测试"""

    def test_rating_score(self, mock_akshare):
        """测试评级分数转换"""
        provider = AKShareCBProvider()
        assert provider._rating_score("AAA") == 100
        assert provider._rating_score("AA+") == 90
        assert provider._rating_score("AA") == 80
        assert provider._rating_score("AA-") == 70
        assert provider._rating_score("A+") == 60
        assert provider._rating_score("BBB") == 20
        assert provider._rating_score("unknown") == 0

    def test_parse_date_valid(self, mock_akshare):
        """测试日期解析 - 有效日期"""
        provider = AKShareCBProvider()
        assert provider._parse_date("2024-06-15") == date(2024, 6, 15)
        assert provider._parse_date("2024/06/15") == date(2024, 6, 15)
        assert provider._parse_date("20240615") == date(2024, 6, 15)

    def test_parse_date_invalid(self, mock_akshare):
        """测试日期解析 - 无效日期"""
        provider = AKShareCBProvider()
        assert provider._parse_date(None) is None
        assert provider._parse_date("") is None
        assert provider._parse_date("-") is None
        assert provider._parse_date("invalid") is None

    def test_safe_decimal_valid(self, mock_akshare):
        """测试 Decimal 转换 - 有效值"""
        provider = AKShareCBProvider()
        assert provider._safe_decimal("123.45") == Decimal("123.45")
        assert provider._safe_decimal(100) == Decimal("100")
        assert provider._safe_decimal(99.99) == Decimal("99.99")

    def test_safe_decimal_invalid(self, mock_akshare):
        """测试 Decimal 转换 - 无效值"""
        provider = AKShareCBProvider()
        assert provider._safe_decimal(None) is None
        assert provider._safe_decimal("") is None
        assert provider._safe_decimal("-") is None

    def test_fingerprint(self, mock_akshare):
        """测试数据指纹"""
        provider = AKShareCBProvider()
        fp = provider.fingerprint()
        assert fp.provider == "akshare_jsl"
        assert fp.calendar == "cn_stock_v1"
        assert fp.schema == "jsl_cb_v1"

    def test_parse_jsl_bond(self, mock_akshare, sample_jsl_data):
        """测试解析集思录可转债数据"""
        provider = AKShareCBProvider()
        row = sample_jsl_data.iloc[0]
        bond = provider._parse_jsl_bond(row)

        assert bond.code == "113676"
        assert bond.name == "华钰转债"
        assert bond.price == Decimal("105.5")
        assert bond.double_low == 98.6
        assert bond.ts_code == "113676.SH"

    def test_parse_redeem_info(self, mock_akshare, sample_redeem_data):
        """测试解析强赎数据"""
        provider = AKShareCBProvider()
        row = sample_redeem_data.iloc[0]
        info = provider._parse_redeem_info(row)

        assert info.code == "113676"
        assert info.name == "华钰转债"
        assert info.call_status == "已触发"
        assert info.call_days_count == 15

    def test_parse_price_adjustment(self, mock_akshare, sample_adj_data):
        """测试解析转股价调整数据"""
        provider = AKShareCBProvider()
        row = sample_adj_data.iloc[0]
        adj = provider._parse_price_adjustment(row)

        assert adj.bond_name == "华钰转债"
        assert adj.old_conv_price == Decimal("10.0")
        assert adj.new_conv_price == Decimal("7.5")

    def test_load_bond_list_jsl(self, mock_akshare, sample_jsl_data):
        """测试加载可转债列表"""
        provider = AKShareCBProvider()
        provider._call_api_with_retry = Mock(return_value=sample_jsl_data)

        bonds = provider.load_bond_list_jsl()
        assert len(bonds) == 2
        assert bonds[0].code == "113676"
        assert bonds[1].code == "123456"

    def test_load_bond_list_with_filters(self, mock_akshare, sample_jsl_data):
        """测试带过滤条件加载可转债列表"""
        provider = AKShareCBProvider()
        provider._call_api_with_retry = Mock(return_value=sample_jsl_data)

        # 价格过滤
        bonds = provider.load_bond_list_jsl(max_price=120.0)
        assert len(bonds) == 1
        assert bonds[0].code == "113676"

    def test_load_double_low_bonds(self, mock_akshare, sample_jsl_data):
        """测试加载双低可转债"""
        provider = AKShareCBProvider()
        provider._call_api_with_retry = Mock(return_value=sample_jsl_data)

        bonds = provider.load_double_low_bonds(max_double_low=130.0, min_amount=0.0, min_rating="")
        assert len(bonds) == 1
        assert bonds[0].double_low == 98.6

    def test_load_double_low_bonds_sorted(self, mock_akshare, sample_jsl_data):
        """测试双低可转债按双低值排序"""
        # 添加更多测试数据
        df = sample_jsl_data.copy()
        df.loc[0, "双低"] = 120.0
        df.loc[1, "双低"] = 100.0

        provider = AKShareCBProvider()
        provider._call_api_with_retry = Mock(return_value=df)

        bonds = provider.load_double_low_bonds(max_double_low=130.0, top_n=2, min_amount=0.0, min_rating="")
        assert len(bonds) == 2
        # 应该按双低值升序排列
        assert bonds[0].double_low < bonds[1].double_low


# ============================================================
# 集成测试（需要网络，默认跳过）
# ============================================================

@pytest.mark.skip(reason="需要网络连接，CI 环境可能无法访问")
class TestAKShareCBProviderIntegration:
    """集成测试 - 需要网络"""

    def test_load_real_bond_list(self):
        """测试从真实 API 加载可转债列表"""
        provider = AKShareCBProvider()
        bonds = provider.load_bond_list_jsl()
        assert len(bonds) > 0
        assert all(b.code for b in bonds)

    def test_load_real_redeem_info(self):
        """测试从真实 API 加载强赎信息"""
        provider = AKShareCBProvider()
        redeems = provider.load_redeem_info()
        assert isinstance(redeems, list)

    def test_load_real_price_adjustments(self):
        """测试从真实 API 加载转股价调整记录"""
        provider = AKShareCBProvider()
        adjs = provider.load_price_adjustments()
        assert isinstance(adjs, list)
