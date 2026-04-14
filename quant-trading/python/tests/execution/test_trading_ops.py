"""Tests for aipha.execution.trading_ops — 统一交易操作层."""

from __future__ import annotations

import math
import pytest

from aipha.execution.trading_ops import (
    FeeParams,
    NavResult,
    RebalanceResult,
    TradeRecord,
    apply_slippage,
    calc_nav,
    calc_trade_cost,
    execute_rebalance,
    get_fee_params,
)

# =====================================================================
# Fixtures
# =====================================================================
@pytest.fixture
def equity_fee() -> FeeParams:
    return get_fee_params("cn_equity")

@pytest.fixture
def cb_fee() -> FeeParams:
    return get_fee_params("convertible_bond")

@pytest.fixture
def etf_fee() -> FeeParams:
    return get_fee_params("etf")


# =====================================================================
# 1. get_fee_params 工厂测试
# =====================================================================
class TestGetFeeParams:

    def test_cn_equity_defaults(self, equity_fee: FeeParams) -> None:
        assert equity_fee.commission_rate == pytest.approx(0.0000854)
        assert equity_fee.commission_min == 5.0
        assert equity_fee.stamp_tax_sell == pytest.approx(0.0005)
        assert equity_fee.transfer_fee == pytest.approx(0.00001)
        assert equity_fee.lot_size == 100
        assert equity_fee.t_plus_n == 1
        assert equity_fee.slippage_bps == 10.0

    def test_convertible_bond_defaults(self, cb_fee: FeeParams) -> None:
        assert cb_fee.stamp_tax_sell == 0.0  # CB 免印花税
        assert cb_fee.transfer_fee == 0.0    # CB 无过户费
        assert cb_fee.lot_size == 10
        assert cb_fee.t_plus_n == 0          # T+0
        assert cb_fee.slippage_bps == 5.0

    def test_etf_defaults(self, etf_fee: FeeParams) -> None:
        assert etf_fee.stamp_tax_sell == 0.0  # ETF 免印花税
        assert etf_fee.transfer_fee == 0.0
        assert etf_fee.lot_size == 100
        assert etf_fee.t_plus_n == 1

    def test_invalid_asset_type(self) -> None:
        with pytest.raises(ValueError, match="不支持"):
            get_fee_params("futures")


# =====================================================================
# 2. calc_trade_cost 测试
# =====================================================================
class TestCalcTradeCost:

    def test_buy_equity(self, equity_fee: FeeParams) -> None:
        """买入 A 股: 佣金+过户费, 无印花税."""
        amount = 100_000.0
        cost = calc_trade_cost(amount, "buy", equity_fee)
        comm = max(amount * 0.0000854, 5.0)
        transfer = amount * 0.00001
        assert cost == pytest.approx(comm + transfer)

    def test_sell_equity(self, equity_fee: FeeParams) -> None:
        """卖出 A 股: 佣金+印花税+过户费."""
        amount = 100_000.0
        cost = calc_trade_cost(amount, "sell", equity_fee)
        comm = max(amount * 0.0000854, 5.0)
        stamp = amount * 0.0005
        transfer = amount * 0.00001
        assert cost == pytest.approx(comm + stamp + transfer)

    def test_sell_cb_no_stamp(self, cb_fee: FeeParams) -> None:
        """卖出 CB: 佣金, 无印花税无过户费."""
        amount = 50_000.0
        cost = calc_trade_cost(amount, "sell", cb_fee)
        comm = max(amount * 0.0000854, 5.0)
        assert cost == pytest.approx(comm)

    def test_min_commission(self, equity_fee: FeeParams) -> None:
        """成交额很小时触发最低佣金 5 元."""
        amount = 1_000.0  # 佣金 = 1000*0.0000854 = 0.085 < 5
        cost = calc_trade_cost(amount, "buy", equity_fee)
        assert cost >= 5.0

    def test_zero_amount(self, equity_fee: FeeParams) -> None:
        cost = calc_trade_cost(0.0, "buy", equity_fee)
        assert cost == pytest.approx(5.0)  # min commission


# =====================================================================
# 3. apply_slippage 测试
# =====================================================================
class TestApplySlippage:

    def test_buy_slippage(self) -> None:
        """买入方向: 价格上浮."""
        price = apply_slippage(100.0, "buy", 10.0)
        assert price == pytest.approx(100.0 * (1 + 10.0 / 10000))

    def test_sell_slippage(self) -> None:
        """卖出方向: 价格下浮."""
        price = apply_slippage(100.0, "sell", 10.0)
        assert price == pytest.approx(100.0 * (1 - 10.0 / 10000))

    def test_zero_bps(self) -> None:
        price = apply_slippage(50.0, "buy", 0.0)
        assert price == pytest.approx(50.0)

    def test_negative_price_returns_negative(self) -> None:
        """负价格不报错, 计算结果为负."""
        price = apply_slippage(-10.0, "buy", 5.0)
        assert price < 0


# =====================================================================
# 4. calc_nav 测试
# =====================================================================
class TestCalcNav:

    def test_basic_nav(self) -> None:
        cash = 50_000.0
        positions = [
            {"code": "000001.SZ", "shares": 1000, "avg_cost": 9.0},
            {"code": "000002.SZ", "shares": 500, "avg_cost": 18.0},
        ]
        prices = {"000001.SZ": 10.0, "000002.SZ": 20.0}
        result = calc_nav(cash, positions, prices)
        expected = 50_000.0 + 1000 * 10.0 + 500 * 20.0
        assert result.nav == pytest.approx(expected)
        assert result.unpriced_codes == []

    def test_missing_price_uses_avg_cost(self) -> None:
        cash = 10_000.0
        positions = [{"code": "000001.SZ", "shares": 100, "avg_cost": 8.0}]
        prices: dict = {}
        result = calc_nav(cash, positions, prices, fallback="avg_cost")
        assert result.nav == pytest.approx(10_000.0 + 100 * 8.0)
        assert "000001.SZ" in result.unpriced_codes

    def test_empty_positions(self) -> None:
        result = calc_nav(100_000.0, [], {})
        assert result.nav == pytest.approx(100_000.0)
        assert result.unpriced_codes == []

    def test_completely_unpriced_no_cost(self) -> None:
        """无价格, fallback=avg_cost 但无 avg_cost 字段时按 0 计入."""
        result = calc_nav(5_000.0, [{"code": "X", "shares": 100}], {})
        assert result.nav == pytest.approx(5_000.0)
        assert "X" in result.unpriced_codes


# =====================================================================
# 5. execute_rebalance 测试
# =====================================================================
class TestExecuteRebalance:

    @pytest.fixture
    def fee(self) -> FeeParams:
        return FeeParams(
            commission_rate=0.0001, commission_min=5.0,
            stamp_tax_sell=0.0005, transfer_fee=0.00001,
            lot_size=100, t_plus_n=1, slippage_bps=0.0,
        )

    def test_sell_then_buy(self, fee: FeeParams) -> None:
        """持有 A 不在目标 → 卖 A; 目标 B 不在持仓 → 买 B."""
        positions = [
            {"code": "A", "shares": 100, "avg_cost": 10.0, "buy_date": "20260101"},
        ]
        prices = {"A": 12.0, "B": 20.0}
        result = execute_rebalance(
            cash=50_000.0, positions=positions,
            target_codes=["B"], prices=prices,
            per_stock_value=40_000.0, trade_date="20260411",
            fee_params=fee, slippage_bps=0.0,
        )
        assert len(result.sells) == 1
        assert result.sells[0].code == "A"
        assert len(result.buys) == 1
        assert result.buys[0].code == "B"
        assert result.new_cash >= 0

    def test_keep_held_in_target(self, fee: FeeParams) -> None:
        """持有 A 且 A 在目标 → 保留, 不卖不买."""
        positions = [
            {"code": "A", "shares": 200, "avg_cost": 10.0, "buy_date": "20260101"},
        ]
        prices = {"A": 11.0}
        result = execute_rebalance(
            cash=100_000.0, positions=positions,
            target_codes=["A"], prices=prices,
            per_stock_value=50_000.0, trade_date="20260411",
            fee_params=fee,
        )
        assert len(result.sells) == 0
        assert len(result.buys) == 0
        held = [p["code"] for p in result.new_positions]
        assert "A" in held

    def test_component_isolation(self, fee: FeeParams) -> None:
        """多组件时只操作本组件持仓."""
        positions = [
            {"code": "A", "shares": 100, "component": "comp1", "buy_date": "20260101"},
            {"code": "B", "shares": 200, "component": "comp2", "buy_date": "20260101"},
        ]
        prices = {"A": 10.0, "B": 20.0, "C": 15.0}
        result = execute_rebalance(
            cash=100_000.0, positions=positions,
            target_codes=["C"], prices=prices,
            per_stock_value=50_000.0, trade_date="20260411",
            fee_params=fee, component="comp1",
        )
        assert result.sells[0].code == "A"
        other_codes = [p["code"] for p in result.new_positions]
        assert "B" in other_codes  # comp2 不受影响


# =====================================================================
# 6. T+1 约束测试
# =====================================================================
class TestTPlusN:

    def test_t1_blocks_same_day_sell(self) -> None:
        """A 股 T+1: 当日买入的不可卖出."""
        fee = FeeParams(t_plus_n=1, slippage_bps=0.0)
        positions = [
            {"code": "A", "shares": 100, "avg_cost": 10.0, "buy_date": "20260411"},
        ]
        prices = {"A": 12.0}
        result = execute_rebalance(
            cash=50_000.0, positions=positions,
            target_codes=[], prices=prices,
            per_stock_value=0.0, trade_date="20260411",
            fee_params=fee,
        )
        assert len(result.sells) == 0
        skipped_codes = [s["code"] for s in result.skipped]
        assert "A" in skipped_codes

    def test_t1_allows_next_day_sell(self) -> None:
        """A 股 T+1: 隔日可卖."""
        fee = FeeParams(t_plus_n=1, slippage_bps=0.0)
        positions = [
            {"code": "A", "shares": 100, "avg_cost": 10.0, "buy_date": "20260410"},
        ]
        prices = {"A": 12.0}
        result = execute_rebalance(
            cash=50_000.0, positions=positions,
            target_codes=[], prices=prices,
            per_stock_value=0.0, trade_date="20260411",
            fee_params=fee,
        )
        assert len(result.sells) == 1
        assert result.sells[0].code == "A"

    def test_t0_allows_same_day_sell(self) -> None:
        """CB T+0: 当日买入可当日卖出."""
        fee = FeeParams(t_plus_n=0, slippage_bps=0.0)
        positions = [
            {"code": "CB001", "shares": 10, "avg_cost": 100.0, "buy_date": "20260411"},
        ]
        prices = {"CB001": 105.0}
        result = execute_rebalance(
            cash=10_000.0, positions=positions,
            target_codes=[], prices=prices,
            per_stock_value=0.0, trade_date="20260411",
            fee_params=fee,
        )
        assert len(result.sells) == 1


# =====================================================================
# 7. 涨跌停检查测试
# =====================================================================
class TestPriceLimits:

    def test_limit_up_blocks_buy(self) -> None:
        """涨停时无法买入."""
        fee = FeeParams(slippage_bps=0.0)
        prev_closes = {"A": 10.0}
        # 价格 >= 11.0 (涨停) 时不可买
        prices = {"A": 11.0}
        result = execute_rebalance(
            cash=200_000.0, positions=[],
            target_codes=["A"], prices=prices,
            per_stock_value=50_000.0, trade_date="20260411",
            fee_params=fee, check_price_limits=True,
            prev_closes=prev_closes,
        )
        assert len(result.buys) == 0
        skipped_codes = [s["code"] for s in result.skipped]
        assert "A" in skipped_codes

    def test_limit_down_blocks_sell(self) -> None:
        """跌停时无法卖出."""
        fee = FeeParams(slippage_bps=0.0)
        prev_closes = {"A": 10.0}
        prices = {"A": 9.0}  # <= 9.0 (跌停)
        positions = [
            {"code": "A", "shares": 100, "avg_cost": 10.0, "buy_date": "20260101"},
        ]
        result = execute_rebalance(
            cash=50_000.0, positions=positions,
            target_codes=[], prices=prices,
            per_stock_value=0.0, trade_date="20260411",
            fee_params=fee, check_price_limits=True,
            prev_closes=prev_closes,
        )
        assert len(result.sells) == 0

    def test_normal_price_allows_trade(self) -> None:
        """正常价格可以交易."""
        fee = FeeParams(slippage_bps=0.0)
        prev_closes = {"A": 10.0}
        prices = {"A": 10.5}
        result = execute_rebalance(
            cash=200_000.0, positions=[],
            target_codes=["A"], prices=prices,
            per_stock_value=50_000.0, trade_date="20260411",
            fee_params=fee, check_price_limits=True,
            prev_closes=prev_closes,
        )
        assert len(result.buys) == 1


# =====================================================================
# 8. 边缘情况
# =====================================================================
class TestEdgeCases:

    def test_empty_targets_sells_all(self) -> None:
        """目标为空 → 卖出所有持仓."""
        fee = FeeParams(slippage_bps=0.0)
        positions = [
            {"code": "A", "shares": 100, "avg_cost": 10.0, "buy_date": "20260101"},
        ]
        prices = {"A": 11.0}
        result = execute_rebalance(
            cash=50_000.0, positions=positions,
            target_codes=[], prices=prices,
            per_stock_value=0.0, trade_date="20260411",
            fee_params=fee,
        )
        assert len(result.sells) == 1
        my_codes = [p["code"] for p in result.new_positions]
        assert "A" not in my_codes

    def test_empty_positions_buys_targets(self) -> None:
        """空仓 → 直接买入目标."""
        fee = FeeParams(slippage_bps=0.0, lot_size=100)
        prices = {"X": 10.0}
        result = execute_rebalance(
            cash=200_000.0, positions=[],
            target_codes=["X"], prices=prices,
            per_stock_value=50_000.0, trade_date="20260411",
            fee_params=fee,
        )
        assert len(result.buys) == 1
        assert result.buys[0].code == "X"

    def test_no_price_skip(self) -> None:
        """无价格的标的被跳过."""
        fee = FeeParams(slippage_bps=0.0)
        result = execute_rebalance(
            cash=100_000.0, positions=[],
            target_codes=["NOPX"], prices={},
            per_stock_value=50_000.0, trade_date="20260411",
            fee_params=fee,
        )
        assert len(result.buys) == 0
        assert any(s["code"] == "NOPX" for s in result.skipped)

    def test_insufficient_cash_degrades(self) -> None:
        """资金不足时降级买较少股数."""
        fee = FeeParams(slippage_bps=0.0, lot_size=100)
        prices = {"A": 100.0}
        result = execute_rebalance(
            cash=5_500.0, positions=[],
            target_codes=["A"], prices=prices,
            per_stock_value=50_000.0, trade_date="20260411",
            fee_params=fee, retry_on_insufficient=True,
        )
        # 5500 现金只够买 ~50 股, 但 lot=100 取整后不够
        # 或者 95%*5500=5225, 5225/100=52.25, int(52.25/100)*100=0
        # 所以实际应该 skip
        if len(result.buys) > 0:
            assert result.buys[0].shares > 0
        else:
            assert any(s["code"] == "A" for s in result.skipped)

    def test_cash_accounting(self) -> None:
        """验证 sell 后现金增加, buy 后现金减少, 合计正确."""
        fee = FeeParams(
            commission_rate=0.0001, commission_min=0.0,
            stamp_tax_sell=0.0, transfer_fee=0.0,
            lot_size=100, slippage_bps=0.0, t_plus_n=0,
        )
        positions = [
            {"code": "A", "shares": 100, "avg_cost": 10.0, "buy_date": "20260101"},
        ]
        prices = {"A": 10.0, "B": 20.0}
        initial_cash = 100_000.0
        result = execute_rebalance(
            cash=initial_cash, positions=positions,
            target_codes=["B"], prices=prices,
            per_stock_value=50_000.0, trade_date="20260411",
            fee_params=fee,
        )
        sell_net = sum(s.net for s in result.sells)
        buy_net = sum(abs(b.net) for b in result.buys)
        expected = initial_cash + sell_net - buy_net
        assert result.new_cash == pytest.approx(expected, abs=1.0)
