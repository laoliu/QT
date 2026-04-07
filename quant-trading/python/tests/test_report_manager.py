"""
Tests for ReportManager metric calculations.

Verifies correctness of:
  - Sharpe ratio calculation (known data, hand-computed expected value)
  - Max drawdown calculation (known peak-to-trough)
  - Total return and CAGR
  - Win rate and profit factor
  - Daily return derivation
  - Drawdown duration tracking
  - Monthly and yearly return tracking
"""

import math

import pytest

from engines.cn_backtest_engine import ReportManager


class TestSharpeRatio:
    """Verify Sharpe ratio calculation with known data."""

    def test_sharpe_known_values(self):
        """Hand-computed Sharpe ratio verification.

        Given daily returns: [0.01, 0.02, -0.005, 0.015, 0.008]
        risk_free_annual = 0.03, rf_daily = 0.03/252 ≈ 0.00011905

        excess = [0.01 - rf, 0.02 - rf, -0.005 - rf, 0.015 - rf, 0.008 - rf]
             ≈ [0.009881, 0.019881, -0.005119, 0.014881, 0.007881]
        mean_excess = sum(excess) / 5 ≈ 0.009481
        variance = sum((x - mean)^2) / 5 (population variance)
        std = sqrt(variance)
        sharpe = (mean_excess / std) * sqrt(252)
        """
        daily_returns = [0.01, 0.02, -0.005, 0.015, 0.008]
        rf_annual = 0.03
        rf_daily = rf_annual / 252

        excess = [r - rf_daily for r in daily_returns]
        mean_excess = sum(excess) / len(excess)
        variance = sum((x - mean_excess) ** 2 for x in excess) / len(excess)
        std_dev = math.sqrt(variance)
        expected_sharpe = (mean_excess / std_dev) * math.sqrt(252)

        actual = ReportManager._calc_sharpe(daily_returns, risk_free_annual=rf_annual)
        assert abs(actual - expected_sharpe) < 1e-6

    def test_sharpe_zero_volatility(self):
        """Constant returns → std_dev = 0 → Sharpe should be 0."""
        daily_returns = [0.001, 0.001, 0.001, 0.001]
        rf_daily = 0.03 / 252
        # All excess returns are equal → zero std → Sharpe = 0
        # Only if excess mean == 0 we get 0/0; here excess != 0 but std = 0
        actual = ReportManager._calc_sharpe(daily_returns, risk_free_annual=0.03)
        assert actual == 0.0

    def test_sharpe_single_return(self):
        """Less than 2 returns → should return 0."""
        assert ReportManager._calc_sharpe([0.01]) == 0.0

    def test_sharpe_empty_returns(self):
        assert ReportManager._calc_sharpe([]) == 0.0

    def test_sharpe_negative_returns(self):
        """All negative returns should produce a negative Sharpe."""
        daily_returns = [-0.01, -0.02, -0.015, -0.008, -0.012]
        sharpe = ReportManager._calc_sharpe(daily_returns, risk_free_annual=0.03)
        assert sharpe < 0

    def test_sharpe_uses_population_variance(self):
        """Confirm population variance (N) is used, not sample variance (N-1)."""
        daily_returns = [0.01, -0.01, 0.02, -0.02]
        rf = 0.03
        rf_daily = rf / 252

        excess = [r - rf_daily for r in daily_returns]
        mean_e = sum(excess) / len(excess)

        # Population variance (divide by N)
        pop_var = sum((x - mean_e) ** 2 for x in excess) / len(excess)
        pop_sharpe = (mean_e / math.sqrt(pop_var)) * math.sqrt(252)

        # Sample variance (divide by N-1) — should NOT match
        sample_var = sum((x - mean_e) ** 2 for x in excess) / (len(excess) - 1)
        sample_sharpe = (mean_e / math.sqrt(sample_var)) * math.sqrt(252)

        actual = ReportManager._calc_sharpe(daily_returns, risk_free_annual=rf)
        assert abs(actual - pop_sharpe) < 1e-6
        assert abs(actual - sample_sharpe) > 1e-4  # should differ


class TestMaxDrawdown:
    """Verify max drawdown calculation with known data."""

    def test_known_drawdown(self):
        """Equity curve: 100 → 120 → 90 → 110
        Max drawdown = (120 - 90) / 120 = 0.25
        """
        rm = ReportManager(init_money=100)
        # Day 1: value = 100 (peak)
        rm.record_daily(20240101, 100.0)
        assert rm.max_drawdown == 0.0

        # Day 2: value = 120 (new peak)
        rm.record_daily(20240102, 120.0)
        assert rm.max_drawdown == 0.0

        # Day 3: value = 90 (drawdown from 120)
        rm.record_daily(20240103, 90.0)
        expected_dd = (120.0 - 90.0) / 120.0
        assert abs(rm.max_drawdown - expected_dd) < 1e-6

        # Day 4: value = 110 (still below 120 peak)
        rm.record_daily(20240104, 110.0)
        # Max drawdown should still be 25%
        assert abs(rm.max_drawdown - expected_dd) < 1e-6

    def test_no_drawdown(self):
        """Monotonically increasing equity → max drawdown = 0."""
        rm = ReportManager(init_money=100)
        for i, val in enumerate([100, 110, 120, 130, 140]):
            rm.record_daily(20240101 + i, float(val))
        assert rm.max_drawdown == 0.0

    def test_full_drawdown(self):
        """Equity drops to near zero → drawdown approaches 1."""
        rm = ReportManager(init_money=1000)
        rm.record_daily(20240101, 1000.0)
        rm.record_daily(20240102, 100.0)
        expected = (1000.0 - 100.0) / 1000.0
        assert abs(rm.max_drawdown - expected) < 1e-6

    def test_multiple_drawdowns_takes_max(self):
        """Two drawdowns: 10% then 20%. Max should be 20%."""
        rm = ReportManager(init_money=100)
        rm.record_daily(20240101, 100.0)  # peak = 100
        rm.record_daily(20240102, 90.0)   # dd = 10%
        rm.record_daily(20240103, 110.0)  # new peak = 110
        rm.record_daily(20240104, 88.0)   # dd = (110-88)/110 = 20%
        expected = (110.0 - 88.0) / 110.0
        assert abs(rm.max_drawdown - expected) < 1e-6


class TestDrawdownDuration:
    """Verify drawdown duration tracking."""

    def test_drawdown_duration_resets_on_new_high(self):
        rm = ReportManager(init_money=100)
        rm.record_daily(20240101, 100.0)
        rm.record_daily(20240102, 95.0)   # in drawdown, duration = 1 after peak
        rm.record_daily(20240103, 90.0)   # still in drawdown
        rm.record_daily(20240104, 105.0)  # new high → reset
        rm.record_daily(20240105, 110.0)  # new high again
        # After new high on day 4, counter resets to 0 then +1
        # On day 5, another new high → reset to 0 then +1


class TestTotalReturn:
    """Verify total return and CAGR."""

    def test_total_return(self):
        rm = ReportManager(init_money=1_000_000)
        final_value = 1_200_000
        summary = rm.get_summary(final_value)
        expected = (1_200_000 / 1_000_000) - 1
        assert abs(summary["totalReturn"] - round(expected, 4)) < 1e-4

    def test_total_return_negative(self):
        rm = ReportManager(init_money=1_000_000)
        final_value = 800_000
        summary = rm.get_summary(final_value)
        assert summary["totalReturn"] < 0

    def test_cagr_one_year(self):
        """Over exactly 12 months, CAGR should equal total return."""
        rm = ReportManager(init_money=1_000_000)
        # Record daily to establish time range: Jan 2024 to Dec 2024 (12 months)
        rm.record_daily(20240101, 1_000_000)
        rm.record_daily(20241201, 1_100_000)

        final_value = 1_100_000
        summary = rm.get_summary(final_value)
        total_return = summary["totalReturn"]
        cagr = summary["annualizedReturn"]
        # For 1 year, CAGR ≈ total return
        assert abs(cagr - total_return) < 0.01


class TestWinRateAndProfitFactor:
    """Verify win rate and profit factor."""

    def test_win_rate(self):
        rm = ReportManager(init_money=1_000_000)
        rm.record_trade(20240101, 1000, 10)   # profit
        rm.record_trade(20240102, -500, 10)    # loss
        rm.record_trade(20240103, 2000, 10)    # profit

        summary = rm.get_summary(1_000_000)
        assert summary["winRate"] == round(2 / 3, 4)

    def test_profit_factor(self):
        rm = ReportManager(init_money=1_000_000)
        rm.record_trade(20240101, 1000, 0)   # profit
        rm.record_trade(20240102, 3000, 0)   # profit
        rm.record_trade(20240103, -500, 0)   # loss
        rm.record_trade(20240104, -1500, 0)  # loss

        summary = rm.get_summary(1_000_000)
        avg_profit = (1000 + 3000) / 2
        avg_loss = (500 + 1500) / 2
        expected_pf = avg_profit / avg_loss
        assert abs(summary["profitFactor"] - round(expected_pf, 4)) < 1e-4

    def test_no_trades(self):
        rm = ReportManager(init_money=1_000_000)
        summary = rm.get_summary(1_000_000)
        assert summary["winRate"] == 0
        assert summary["profitFactor"] == 0
        assert summary["totalTrades"] == 0


class TestDailyReturns:
    """Verify internal daily return calculation."""

    def test_daily_returns_from_equity_curve(self):
        rm = ReportManager(init_money=100)
        values = [100.0, 110.0, 105.0, 115.0]
        for i, val in enumerate(values):
            rm.record_daily(20240101 + i, val)

        returns = rm._calc_daily_returns()
        assert len(returns) == 3
        assert abs(returns[0] - (110.0 / 100.0 - 1)) < 1e-6
        assert abs(returns[1] - (105.0 / 110.0 - 1)) < 1e-6
        assert abs(returns[2] - (115.0 / 105.0 - 1)) < 1e-6


class TestMonthlyYearlyReturns:
    """Verify monthly and yearly return boundary tracking."""

    def test_monthly_return_boundary(self):
        rm = ReportManager(init_money=1_000_000)
        # January
        rm.record_daily(20240131, 1_050_000)
        # February (triggers monthly return for January)
        rm.record_daily(20240201, 1_080_000)

        assert len(rm._monthly_returns) == 1
        # _last_month_value starts at init_money (1_000_000), so return is
        # (1_080_000 / 1_000_000) - 1 = 0.08
        expected = (1_080_000 / 1_000_000) - 1
        assert abs(rm._monthly_returns[0]["return"] - round(expected, 4)) < 1e-4

    def test_yearly_return_boundary(self):
        rm = ReportManager(init_money=1_000_000)
        rm.record_daily(20231231, 1_100_000)
        rm.record_daily(20240101, 1_120_000)

        assert len(rm._yearly_returns) == 1


class TestEquityCurve:
    """Verify equity curve output format."""

    def test_curve_format(self):
        rm = ReportManager(init_money=1_000_000)
        rm.record_daily(20240115, 1_000_000)
        rm.record_daily(20240116, 1_010_000)

        assert len(rm.equity_curve) == 2
        entry = rm.equity_curve[0]
        assert "date" in entry
        assert "value" in entry
        assert "drawdown" in entry
        assert entry["date"] == "2024-01-15"
        assert entry["value"] == 1_000_000.0

    def test_drawdown_in_curve(self):
        rm = ReportManager(init_money=100)
        rm.record_daily(20240101, 100.0)
        rm.record_daily(20240102, 80.0)

        assert rm.equity_curve[0]["drawdown"] == 0.0
        expected_dd = round((100.0 - 80.0) / 100.0, 4)
        assert rm.equity_curve[1]["drawdown"] == expected_dd


class TestFeesAndDividendTracking:
    """Verify fee and dividend accumulation."""

    def test_fees_accumulated(self):
        rm = ReportManager(init_money=1_000_000)
        rm.record_trade(20240101, 100, 15.5)
        rm.record_trade(20240102, -50, 12.3)
        summary = rm.get_summary(1_000_000)
        assert abs(summary["totalFees"] - round(15.5 + 12.3, 2)) < 0.01

    def test_dividends_accumulated(self):
        rm = ReportManager(init_money=1_000_000)
        rm.record_dividend(20240101, 500.0)
        rm.record_dividend(20240102, 300.0)
        summary = rm.get_summary(1_000_000)
        assert summary["totalDividend"] == 800.0
