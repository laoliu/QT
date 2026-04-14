"""可转债领域模型测试"""
from datetime import date
from decimal import Decimal

import pytest

from aipha.domain.convertible_bond import (
    BondCorporateAction,
    BondStatus,
    ConversionMetrics,
    ConvertibleBond,
    ConvertibleBondBar,
    CorporateActionType,
    RedemptionCheck,
)


class TestConvertibleBond:
    """可转债基础信息测试"""

    def test_create_bond(self):
        """测试创建可转债对象"""
        bond = ConvertibleBond(
            ts_code="127075.SZ",
            bond_short_name="中矿转债",
            stk_code="002937.SZ",
            stk_short_name="兴瑞科技",
            conv_price=Decimal("30.00"),
            conv_start_date=date(2023, 1, 1),
            conv_end_date=date(2028, 12, 31),
            maturity_date=date(2029, 1, 1),
            issue_size=Decimal("5.0"),
        )

        assert bond.ts_code == "127075.SZ"
        assert bond.stk_code == "002937.SZ"
        assert bond.conv_price == Decimal("30.00")
        assert bond.par_value == Decimal("100")

    def test_conversion_value(self):
        """测试转股价值计算"""
        bond = ConvertibleBond(
            ts_code="127075.SZ",
            bond_short_name="测试转债",
            stk_code="002937.SZ",
            stk_short_name="测试正股",
            conv_price=Decimal("20.00"),
            conv_start_date=date(2023, 1, 1),
            conv_end_date=date(2028, 12, 31),
            maturity_date=date(2029, 1, 1),
            issue_size=Decimal("5.0"),
        )

        # 正股价格 25 元，转股价 20 元
        # 转股价值 = 25 × (100 / 20) = 125
        conv_value = bond.conversion_value(Decimal("25.00"))
        assert conv_value == Decimal("125")

    def test_conversion_premium(self):
        """测试转股溢价率计算"""
        bond = ConvertibleBond(
            ts_code="127075.SZ",
            bond_short_name="测试转债",
            stk_code="002937.SZ",
            stk_short_name="测试正股",
            conv_price=Decimal("20.00"),
            conv_start_date=date(2023, 1, 1),
            conv_end_date=date(2028, 12, 31),
            maturity_date=date(2029, 1, 1),
            issue_size=Decimal("5.0"),
        )

        # 转债价格 130 元，正股价格 25 元
        # 转股价值 = 25 × (100 / 20) = 125
        # 溢价率 = (130 - 125) / 125 × 100% = 4%
        premium = bond.conversion_premium(Decimal("130.00"), Decimal("25.00"))
        assert premium == Decimal("4")


class TestConversionMetrics:
    """转股指标测试"""

    def test_calculate_metrics(self):
        """测试转股指标计算"""
        metrics = ConversionMetrics.calculate(
            ts_code="127075.SZ",
            trade_date=date(2024, 1, 15),
            bond_price=Decimal("120.00"),
            stock_price=Decimal("25.00"),
            conv_price=Decimal("20.00"),
        )

        # 转股价值 = 25 × (100 / 20) = 125
        assert metrics.conv_value == Decimal("125")

        # 溢价率 = (120 - 125) / 125 × 100% = -4%
        assert metrics.premium_rate == Decimal("-4")

        # 双低指标 = 120 + (-4) = 116
        assert metrics.double_low == Decimal("116")

    def test_double_low_ranking(self):
        """测试双低排名"""
        metrics_list = [
            ConversionMetrics.calculate(
                ts_code="bond_a",
                trade_date=date(2024, 1, 15),
                bond_price=Decimal("110"),
                stock_price=Decimal("25"),
                conv_price=Decimal("20"),
            ),
            ConversionMetrics.calculate(
                ts_code="bond_b",
                trade_date=date(2024, 1, 15),
                bond_price=Decimal("100"),
                stock_price=Decimal("20"),
                conv_price=Decimal("20"),
            ),
            ConversionMetrics.calculate(
                ts_code="bond_c",
                trade_date=date(2024, 1, 15),
                bond_price=Decimal("130"),
                stock_price=Decimal("30"),
                conv_price=Decimal("20"),
            ),
        ]

        # 按双低指标排序
        sorted_metrics = sorted(metrics_list, key=lambda x: x.double_low)

        # bond_b: 价格 100，转股价值 100，溢价率 0%，双低 100
        # bond_a: 价格 110，转股价值 125，溢价率 -12%，双低 98
        # bond_c: 价格 130，转股价值 150，溢价率 -13.33%，双低 116.67
        assert sorted_metrics[0].ts_code == "bond_a"  # 双低最低


class TestRedemptionCheck:
    """强赎条件检查测试"""

    def test_trigger_redemption(self):
        """测试触发强赎条件"""
        # 转股价 10 元，触发价 = 10 × 130% = 13 元
        # 正股连续 15 天价格 >= 13 元
        stock_prices = [Decimal("14.0")] * 20

        result = RedemptionCheck.check(
            ts_code="127075.SZ",
            check_date=date(2024, 1, 15),
            conv_price=Decimal("10.00"),
            stock_prices=stock_prices,
        )

        assert result.is_triggered is True
        assert result.consecutive_days >= 15

    def test_not_trigger_redemption(self):
        """测试未触发强赎条件"""
        # 正股价格波动，连续天数不足
        stock_prices = [
            Decimal("14.0"),
            Decimal("14.0"),
            Decimal("12.0"),  # 低于触发价
            Decimal("14.0"),
            Decimal("14.0"),
        ]

        result = RedemptionCheck.check(
            ts_code="127075.SZ",
            check_date=date(2024, 1, 15),
            conv_price=Decimal("10.00"),
            stock_prices=stock_prices,
        )

        assert result.is_triggered is False
        assert result.consecutive_days == 2  # 只有最后 2 天连续


class TestConvertibleBondBar:
    """可转债行情测试"""

    def test_create_bar(self):
        """测试创建可转债行情"""
        from datetime import datetime

        bar = ConvertibleBondBar(
            symbol="127075.SZ",
            start_ts=datetime(2024, 1, 15, 9, 30),
            end_ts=datetime(2024, 1, 15, 15, 0),
            open=120.0,
            high=125.0,
            low=118.0,
            close=122.0,
            volume=100000,
            amount=12200000,
        )

        assert bar.symbol == "127075.SZ"
        assert bar.close == 122.0
        assert bar.is_limit_up is None  # 可转债无涨跌停


class TestBondCorporateAction:
    """公司行动测试"""

    def test_conversion_price_down(self):
        """测试转股价下修事件"""
        action = BondCorporateAction(
            ts_code="127075.SZ",
            action_type=CorporateActionType.CONVERSION_PRICE_DOWN,
            announce_date=date(2024, 1, 10),
            effective_date=date(2024, 1, 15),
            old_value=Decimal("30.00"),
            new_value=Decimal("25.00"),
            memo="触发下修条款",
        )

        assert action.action_type == CorporateActionType.CONVERSION_PRICE_DOWN
        assert action.old_value == Decimal("30.00")
        assert action.new_value == Decimal("25.00")
