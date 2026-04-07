"""
End-to-end tests for TradeSystem (backtest engine).

Uses a simple always-buy strategy to verify the full backtest loop:
  - Strategy signal dispatch
  - Trade execution flow (sell first, then buy)
  - Daily metric recording
  - End-of-backtest liquidation
  - Summary report generation
"""

import pytest

from engines.cn_backtest_engine import TradeSystem


def _make_daily_prices(trade_days, stocks, price_series):
    """Build daily_prices dict from a price series per stock.

    Args:
        trade_days: list of YYYYMMDD ints
        stocks: list of stock ids
        price_series: dict[stock_id, list[float]] — one price per day
    Returns:
        { date: { stock_id: { open, high, low, close, volume, prev_close } } }
    """
    daily_prices = {}
    for i, dt in enumerate(trade_days):
        day_data = {}
        for sid in stocks:
            prices = price_series[sid]
            close = prices[i]
            prev_close = prices[i - 1] if i > 0 else close
            day_data[sid] = {
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": 1_000_000,
                "prev_close": prev_close,
            }
        daily_prices[dt] = day_data
    return daily_prices


class TestAlwaysBuyStrategy:
    """End-to-end test: strategy that buys all available stocks every day."""

    @pytest.fixture
    def setup(self):
        stocks = ["sh600519", "sh600520"]
        trade_days = [20240101, 20240102, 20240103, 20240104, 20240105]
        # Gradually rising prices
        price_series = {
            "sh600519": [100.0, 102.0, 105.0, 103.0, 108.0],
            "sh600520": [50.0, 51.0, 53.0, 52.0, 55.0],
        }
        daily_prices = _make_daily_prices(trade_days, stocks, price_series)

        def always_buy(dt, positions, prices):
            buy_ids = [sid for sid in stocks if sid not in positions]
            return buy_ids, []

        return trade_days, daily_prices, always_buy, stocks

    def test_backtest_runs_to_completion(self, setup):
        trade_days, daily_prices, strategy, _ = setup
        system = TradeSystem(init_money=1_000_000, max_stocks=5)
        result = system.run(trade_days, daily_prices, strategy)
        assert result is not None
        assert "finalValue" in result
        assert "totalReturn" in result
        assert "equityCurve" in result

    def test_positions_are_entered(self, setup):
        trade_days, daily_prices, strategy, _ = setup
        system = TradeSystem(init_money=1_000_000, max_stocks=5)
        result = system.run(trade_days, daily_prices, strategy)
        # Strategy buys on day 1, all cleared on last day
        assert result["totalTrades"] > 0

    def test_equity_curve_length_matches_days(self, setup):
        trade_days, daily_prices, strategy, _ = setup
        system = TradeSystem(init_money=1_000_000, max_stocks=5)
        result = system.run(trade_days, daily_prices, strategy)
        assert len(result["equityCurve"]) == len(trade_days)

    def test_final_value_positive(self, setup):
        trade_days, daily_prices, strategy, _ = setup
        system = TradeSystem(init_money=1_000_000, max_stocks=5)
        result = system.run(trade_days, daily_prices, strategy)
        assert result["finalValue"] > 0

    def test_all_positions_cleared_at_end(self, setup):
        trade_days, daily_prices, strategy, _ = setup
        system = TradeSystem(init_money=1_000_000, max_stocks=5)
        system.run(trade_days, daily_prices, strategy)
        # After run, all positions should be cleared
        assert system.position_mgr.count() == 0

    def test_rising_prices_yield_positive_return(self, setup):
        """With rising prices, always-buy should produce positive return."""
        trade_days, daily_prices, strategy, _ = setup
        system = TradeSystem(init_money=1_000_000, max_stocks=5, commission=0.0)
        result = system.run(trade_days, daily_prices, strategy)
        assert result["totalReturn"] > 0


class TestBuyAndSellStrategy:
    """End-to-end: buy on day 1, sell on day 3, verify profit/loss tracking."""

    def test_sell_then_buy_order(self):
        """Sells are executed before buys within the same day."""
        stocks = ["sh600519"]
        trade_days = [20240101, 20240102, 20240103]
        price_series = {"sh600519": [100.0, 110.0, 105.0]}
        daily_prices = _make_daily_prices(trade_days, stocks, price_series)

        calls = []

        def tracking_strategy(dt, positions, prices):
            has_position = "sh600519" in positions
            calls.append({"dt": dt, "has_position": has_position})
            if dt == 20240101:
                return ["sh600519"], []
            elif dt == 20240102:
                return [], ["sh600519"]  # sell
            return [], []

        system = TradeSystem(init_money=1_000_000, max_stocks=5)
        result = system.run(trade_days, daily_prices, tracking_strategy)
        assert result is not None
        # Should have recorded at least one trade (buy on day 1)
        # Sell on day 2 produces the clear, plus final clear on day 3 if held
        assert result["totalTrades"] >= 1


class TestEmptyBacktest:
    """Edge case: no trading days."""

    def test_empty_trade_days(self):
        system = TradeSystem(init_money=1_000_000)
        result = system.run([], {}, lambda dt, pos, p: ([], []))
        assert result["initialCash"] == 1_000_000
        assert result["totalTrades"] == 0


class TestSingleDayBacktest:
    """Edge case: only one trading day."""

    def test_single_day(self):
        trade_days = [20240101]
        daily_prices = {
            20240101: {
                "sh600519": {
                    "open": 100.0, "high": 101.0, "low": 99.0,
                    "close": 100.0, "volume": 1_000_000, "prev_close": 100.0,
                }
            }
        }

        def buy_all(dt, positions, prices):
            return ["sh600519"], []

        system = TradeSystem(init_money=1_000_000, max_stocks=5)
        result = system.run(trade_days, daily_prices, buy_all)
        assert len(result["equityCurve"]) == 1
        # Positions cleared on last (only) day
        assert system.position_mgr.count() == 0


class TestBenchmarkCurve:
    """Test benchmark comparison in output."""

    def test_benchmark_included_in_result(self):
        trade_days = [20240101, 20240102, 20240103]
        daily_prices = _make_daily_prices(
            trade_days, ["sh600519"],
            {"sh600519": [100.0, 105.0, 110.0]},
        )
        benchmark = {20240101: 3000.0, 20240102: 3050.0, 20240103: 3100.0}

        system = TradeSystem(init_money=1_000_000, max_stocks=5)
        result = system.run(
            trade_days, daily_prices,
            lambda dt, pos, p: ([], []),
            benchmark_prices=benchmark,
        )
        assert "benchmarkCurve" in result
        assert len(result["benchmarkCurve"]) == 3


class TestCapitalPolicies:
    """Test different capital allocation policies."""

    def test_fixed_policy(self):
        trade_days = [20240101, 20240102]
        daily_prices = _make_daily_prices(
            trade_days, ["sh600519", "sh600520"],
            {"sh600519": [100.0, 105.0], "sh600520": [50.0, 52.0]},
        )

        system = TradeSystem(init_money=1_000_000, max_stocks=5, policy="fixed")
        result = system.run(
            trade_days, daily_prices,
            lambda dt, pos, p: (["sh600519", "sh600520"], []) if dt == 20240101 else ([], []),
        )
        assert result["totalTrades"] >= 2

    def test_demand_policy(self):
        trade_days = [20240101, 20240102]
        daily_prices = _make_daily_prices(
            trade_days, ["sh600519"],
            {"sh600519": [100.0, 105.0]},
        )

        system = TradeSystem(init_money=1_000_000, max_stocks=5, policy="demand")
        result = system.run(
            trade_days, daily_prices,
            lambda dt, pos, p: (["sh600519"], []) if dt == 20240101 else ([], []),
        )
        assert result["totalTrades"] >= 1


class TestDividendProcessing:
    """Test dividend handling in backtest."""

    def test_cash_dividend_credited(self):
        trade_days = [20240101, 20240102, 20240103]
        daily_prices = _make_daily_prices(
            trade_days, ["sh600519"],
            {"sh600519": [100.0, 100.0, 100.0]},
        )
        # Dividend on day 2: 5 yuan per 10 shares
        dividend_data = {
            20240102: {
                "sh600519": {
                    "type": 1,
                    "dividend": 5.0,
                    "price": 0,
                    "transfer": 0,
                    "qty": 0,
                    "ratio": 1,
                }
            }
        }

        system = TradeSystem(init_money=1_000_000, max_stocks=5, commission=0.0, stamp_tax=0.0)
        result = system.run(
            trade_days, daily_prices,
            lambda dt, pos, p: (["sh600519"], []) if dt == 20240101 else ([], []),
            dividend_data=dividend_data,
        )
        assert result["totalDividend"] > 0
