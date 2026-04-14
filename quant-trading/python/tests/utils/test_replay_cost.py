"""replay_cost 共享模块单元测试

验证费用计算与 MarketRules 权威源、fees.py 一致。
"""

from __future__ import annotations

import math
from datetime import date

import pytest

from aipha.utils.replay_cost import (
    _parse_date,
    calc_buy_cost,
    calc_sell_cost,
    calc_slippage_impact,
)


class TestParseDate:
    def test_str_iso(self):
        assert _parse_date("2024-01-15") == date(2024, 1, 15)

    def test_str_compact(self):
        assert _parse_date("20240115") == date(2024, 1, 15)

    def test_date_passthrough(self):
        d = date(2024, 6, 1)
        assert _parse_date(d) is d


class TestCalcBuyCost:
    def test_cn_equity_basic(self):
        """A 股买入：佣金 = max(notional*0.0000854, 5)"""
        cost = calc_buy_cost(
            price=10.0, shares=1000, date="2024-06-01",
            asset_type="cn_equity",
        )
        notional = 10.0 * 1000
        expected_comm = max(notional * 0.0000854, 5.0)
        expected_tf = notional * 0.00001
        assert cost >= expected_comm + expected_tf

    def test_cb_no_stamp_tax_no_transfer(self):
        cost = calc_buy_cost(
            price=100.0, shares=10, date="2024-06-01",
            asset_type="convertible_bond",
        )
        notional = 100.0 * 10
        expected_comm = max(notional * 0.0000854, 5.0)  # CB 最低佣金5元
        assert abs(cost - expected_comm) < 0.01

    def test_min_commission_cn(self):
        """小额交易触发最低佣金 5 元"""
        cost = calc_buy_cost(
            price=1.0, shares=100, date="2024-06-01",
            asset_type="cn_equity",
        )
        assert cost >= 5.0  # 最低佣金


class TestCalcSellCost:
    def test_cn_equity_includes_stamp_tax(self):
        cost = calc_sell_cost(
            price=10.0, shares=1000, date="2024-06-01",
            asset_type="cn_equity",
        )
        notional = 10.0 * 1000
        comm = max(notional * 0.0000854, 5.0)
        stamp = notional * 0.0005
        tf = notional * 0.00001
        assert cost >= comm + stamp + tf - 0.01

    def test_stamp_tax_before_2023(self):
        """2023-08-28 前印花税为 0.001"""
        cost_old = calc_sell_cost(
            price=10.0, shares=1000, date="2023-01-01",
            asset_type="cn_equity",
        )
        cost_new = calc_sell_cost(
            price=10.0, shares=1000, date="2024-01-01",
            asset_type="cn_equity",
        )
        assert cost_old > cost_new  # 旧税率更高

    def test_cb_no_stamp_tax(self):
        cost = calc_sell_cost(
            price=100.0, shares=10, date="2024-06-01",
            asset_type="convertible_bond",
        )
        notional = 100.0 * 10
        expected_comm = max(notional * 0.0000854, 5.0)  # CB 最低佣金5元
        assert abs(cost - expected_comm) < 0.01


class TestSlippageImpact:
    def test_zero_volume(self):
        s = calc_slippage_impact(10.0, 100, 0)
        assert s > 0  # 回退到 spread_bps

    def test_sqrt_model(self):
        s = calc_slippage_impact(10.0, 100, 100000, 50.0, 3.0)
        participation = 100 / 100000
        expected_bps = 50.0 * math.sqrt(participation) + 1.5
        expected = 10.0 * 100 * expected_bps / 10000
        assert abs(s - expected) < 0.001
