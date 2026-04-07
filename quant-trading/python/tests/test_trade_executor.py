"""
Tests for TradeExecutor (simulated broker) order matching logic.

Covers:
  - Market order buy/sell execution
  - Limit-up detection (一字涨停 → buy rejected)
  - Limit-down detection (一字跌停 → sell rejected)
  - Max position limit enforcement
  - Fee and capital deduction correctness
  - Batch sell and clear-all operations
"""

import math

import pytest

from engines.cn_backtest_engine import (
    CapitalManager,
    PositionManager,
    ReportManager,
    TradeExecutor,
)


@pytest.fixture
def components():
    """Create a fresh set of capital/position/report managers and executor."""
    capital = CapitalManager(
        init_money=1_000_000,
        policy="fixed",
        commission=0.0003,
        stamp_tax=0.001,
        simulate_lots=True,
    )
    position = PositionManager()
    report = ReportManager(1_000_000)
    executor = TradeExecutor(capital, position, report, max_stocks=3)
    return capital, position, report, executor


class TestMarketOrderBuy:
    """Test market order buy execution."""

    def test_buy_basic(self, components):
        capital, position, report, executor = components
        result = executor.buy_one(
            "sh600519", 20240101,
            {"fixed_amount": 200_000},
            price=100.0,
        )
        assert result is not None
        assert result["id"] == "sh600519"
        assert result["price"] == 100.0
        assert result["number"] > 0
        assert result["number"] % 100 == 0  # lots of 100
        assert position.is_exist("sh600519")

    def test_buy_deducts_capital(self, components):
        capital, position, report, executor = components
        initial = capital.available_money
        executor.buy_one(
            "sh600519", 20240101,
            {"fixed_amount": 200_000},
            price=100.0,
        )
        # Capital should decrease by cost + commission
        assert capital.available_money < initial

    def test_buy_zero_price_rejected(self, components):
        _, _, _, executor = components
        result = executor.buy_one("sh600519", 20240101, {"fixed_amount": 200_000}, price=0)
        assert result is None

    def test_buy_negative_price_rejected(self, components):
        _, _, _, executor = components
        result = executor.buy_one("sh600519", 20240101, {"fixed_amount": 200_000}, price=-10)
        assert result is None

    def test_buy_respects_lot_size(self, components):
        """With simulate_lots=True, shares bought must be multiples of 100."""
        capital, position, _, executor = components
        result = executor.buy_one(
            "sh600519", 20240101,
            {"fixed_amount": 150},  # very small amount
            price=100.0,
        )
        # 150 / 100 = 1.5 → 1 share → < 100 → should fail
        assert result is None

    def test_buy_adds_to_existing_position(self, components):
        capital, position, _, executor = components
        executor.buy_one("sh600519", 20240101, {"fixed_amount": 200_000}, price=100.0)
        first_shares = position.get_position("sh600519").number
        executor.buy_one("sh600519", 20240102, {"fixed_amount": 200_000}, price=110.0)
        assert position.get_position("sh600519").number > first_shares


class TestMaxPositionLimit:
    """Test that max_stocks limit is enforced."""

    def test_cannot_exceed_max_positions(self, components):
        _, position, _, executor = components
        # max_stocks = 3, buy 3 different stocks
        for i, sid in enumerate(["sh600519", "sh600520", "sh600521"]):
            result = executor.buy_one(
                sid, 20240101, {"fixed_amount": 100_000}, price=50.0,
            )
            assert result is not None

        # 4th stock should be rejected
        result = executor.buy_one(
            "sh600522", 20240101, {"fixed_amount": 100_000}, price=50.0,
        )
        assert result is None
        assert position.count() == 3

    def test_adding_to_existing_does_not_count_as_new(self, components):
        """Buying more of an existing position should not be blocked."""
        _, position, _, executor = components
        for sid in ["sh600519", "sh600520", "sh600521"]:
            executor.buy_one(sid, 20240101, {"fixed_amount": 100_000}, price=50.0)
        # Adding to existing position should succeed
        result = executor.buy_one(
            "sh600519", 20240102, {"fixed_amount": 50_000}, price=55.0,
        )
        assert result is not None


class TestLimitUpDetection:
    """Test 一字涨停 (single-bar limit-up) buy rejection."""

    def test_limit_up_rejects_buy(self, components):
        """When close/prev_close >= 1.097 and OHLC all equal → reject."""
        _, _, _, executor = components
        bars = {
            "prev_close": 100.0,
            "open": 110.0,
            "high": 110.0,
            "low": 110.0,
            "close": 110.0,  # 110/100 = 1.10 >= 1.097
        }
        result = executor.buy_one(
            "sh600519", 20240101,
            {"fixed_amount": 200_000},
            price=110.0,
            bars=bars,
        )
        assert result is None

    def test_normal_up_allows_buy(self, components):
        """Price up but not a 一字涨停 (OHLC differ) → allow."""
        _, _, _, executor = components
        bars = {
            "prev_close": 100.0,
            "open": 105.0,
            "high": 110.0,
            "low": 104.0,
            "close": 110.0,  # 1.10 but OHLC differ
        }
        result = executor.buy_one(
            "sh600519", 20240101,
            {"fixed_amount": 200_000},
            price=110.0,
            bars=bars,
        )
        assert result is not None

    def test_no_bars_skips_limit_check(self, components):
        """Without bars data, limit-up check is skipped."""
        _, _, _, executor = components
        result = executor.buy_one(
            "sh600519", 20240101,
            {"fixed_amount": 200_000},
            price=110.0,
            bars=None,
        )
        assert result is not None


class TestMarketOrderSell:
    """Test sell execution."""

    def test_sell_basic(self, components):
        capital, position, _, executor = components
        executor.buy_one("sh600519", 20240101, {"fixed_amount": 500_000}, price=100.0)
        shares_before = position.get_position("sh600519").number

        result = executor.sell_one("sh600519", 20240102, price=110.0)
        assert result is not None
        assert result["number"] == shares_before
        assert not position.is_exist("sh600519")

    def test_sell_credits_capital(self, components):
        capital, _, _, executor = components
        executor.buy_one("sh600519", 20240101, {"fixed_amount": 500_000}, price=100.0)
        money_after_buy = capital.available_money

        executor.sell_one("sh600519", 20240102, price=110.0)
        assert capital.available_money > money_after_buy

    def test_sell_nonexistent_position(self, components):
        _, _, _, executor = components
        result = executor.sell_one("sh999999", 20240101, price=100.0)
        assert result is None

    def test_sell_zero_price_rejected(self, components):
        _, _, _, executor = components
        result = executor.sell_one("sh600519", 20240101, price=0)
        assert result is None

    def test_sell_records_profit(self, components):
        _, _, report, executor = components
        executor.buy_one("sh600519", 20240101, {"fixed_amount": 500_000}, price=100.0)
        executor.sell_one("sh600519", 20240102, price=110.0)
        assert report.profit_count == 1
        assert report.total_profit > 0

    def test_sell_records_loss(self, components):
        _, _, report, executor = components
        executor.buy_one("sh600519", 20240101, {"fixed_amount": 500_000}, price=100.0)
        executor.sell_one("sh600519", 20240102, price=90.0)
        assert report.loss_count == 1
        assert report.total_loss < 0


class TestLimitDownDetection:
    """Test 一字跌停 (single-bar limit-down) sell rejection."""

    def test_limit_down_rejects_sell(self, components):
        _, _, _, executor = components
        executor.buy_one("sh600519", 20240101, {"fixed_amount": 500_000}, price=100.0)

        bars = {
            "prev_close": 100.0,
            "open": 90.0,
            "high": 90.0,
            "low": 90.0,
            "close": 90.0,  # 90/100 = 0.90 <= 0.91, OHLC all equal
        }
        result = executor.sell_one("sh600519", 20240102, price=90.0, bars=bars)
        assert result is None

    def test_limit_down_bypassed_on_last_day(self, components):
        """On the last backtest day, limit-down check is bypassed."""
        _, _, _, executor = components
        executor.buy_one("sh600519", 20240101, {"fixed_amount": 500_000}, price=100.0)
        executor.set_last_day(20240102)

        bars = {
            "prev_close": 100.0,
            "open": 90.0,
            "high": 90.0,
            "low": 90.0,
            "close": 90.0,
        }
        result = executor.sell_one("sh600519", 20240102, price=90.0, bars=bars)
        assert result is not None

    def test_normal_down_allows_sell(self, components):
        """Price down but not 一字跌停 (OHLC differ) → allow."""
        _, _, _, executor = components
        executor.buy_one("sh600519", 20240101, {"fixed_amount": 500_000}, price=100.0)

        bars = {
            "prev_close": 100.0,
            "open": 95.0,
            "high": 96.0,
            "low": 90.0,
            "close": 90.0,  # OHLC differ
        }
        result = executor.sell_one("sh600519", 20240102, price=90.0, bars=bars)
        assert result is not None


class TestBatchAndClear:
    """Test batch sell and clear-all operations."""

    def test_sell_batch(self, components):
        _, position, _, executor = components
        for sid in ["sh600519", "sh600520"]:
            executor.buy_one(sid, 20240101, {"fixed_amount": 200_000}, price=100.0)

        executor.sell_batch(
            ["sh600519", "sh600520"], 20240102,
            {"sh600519": 110.0, "sh600520": 105.0},
        )
        assert position.count() == 0

    def test_clear_all(self, components):
        _, position, _, executor = components
        for sid in ["sh600519", "sh600520", "sh600521"]:
            executor.buy_one(sid, 20240101, {"fixed_amount": 100_000}, price=50.0)
        assert position.count() == 3

        executor.clear_all(
            20240102,
            {"sh600519": 55.0, "sh600520": 48.0, "sh600521": 52.0},
        )
        assert position.count() == 0


class TestFeeCalculation:
    """Verify commission and stamp tax are correctly applied."""

    def test_buy_commission(self, components):
        capital, _, _, executor = components
        initial = capital.available_money
        result = executor.buy_one(
            "sh600519", 20240101, {"fixed_amount": 500_000}, price=100.0,
        )
        shares = result["number"]
        cost = shares * 100.0
        expected_fee = cost * 0.0003  # commission rate
        # Capital should be reduced by cost + fee
        expected_remaining = initial - cost - expected_fee
        assert abs(capital.available_money - expected_remaining) < 0.01

    def test_sell_commission_and_stamp_tax(self, components):
        capital, _, report, executor = components
        executor.buy_one("sh600519", 20240101, {"fixed_amount": 500_000}, price=100.0)
        money_before_sell = capital.available_money

        result = executor.sell_one("sh600519", 20240102, price=100.0)
        shares_sold = result["number"]
        total_cap = shares_sold * 100.0
        expected_fee = total_cap * 0.0003 + total_cap * 0.001  # commission + stamp tax
        expected_credit = total_cap - expected_fee
        assert abs(capital.available_money - money_before_sell - expected_credit) < 0.01
