"""
Comprehensive tests for the performance_metrics engine.

Covers:
  - Core logic for all metric calculations
  - Boundary conditions (single trade, zero values, extreme values)
  - Anomalous / exceptional inputs (empty, missing fields, inf/nan)
  - Input validation (no trades, no curve, malformed data)
  - Numerical precision verification
  - Integration tests for full_report action routing
"""

import math
import pytest

from engines.performance_metrics import (
    compute_performance_metrics,
    _compute_trade_stats,
    _compute_risk_metrics,
    _compute_holding_analysis,
    _compute_streaks,
    _compute_composite_score,
    _calc_returns,
    _annualized_return,
    _mean,
    _median,
    _std_dev,
    _sharpe_ratio,
    _sortino_ratio,
    _value_at_risk,
    _conditional_var,
    _monthly_returns,
    _calc_holding_days,
    _r2,
    _r4,
    _trade_summary,
    _max_drawdown_analysis,
)


# ── Fixtures / Helpers ─────────────────────────────────────────


def _make_trades(pnls, holding_days=None, symbols=None):
    """Create trade records from a list of PnLs."""
    trades = []
    for i, pnl in enumerate(pnls):
        hd = holding_days[i] if holding_days else 5
        sym = symbols[i] if symbols else f"00000{i % 5}"
        trades.append({
            "tradeId": f"t{i}",
            "symbol": sym,
            "entryTime": f"2024-01-{(i * 2 + 1):02d}",
            "exitTime": f"2024-01-{(i * 2 + 1 + hd):02d}",
            "entryPrice": 10.0,
            "exitPrice": 10.0 + pnl / 100,
            "quantity": 100,
            "grossPnl": pnl + 5,
            "commission": 5.0,
            "netPnl": pnl,
            "holdingDays": hd,
            "strategyId": "test",
        })
    return trades


def _make_equity_curve(values, start_date="2024-01-01"):
    """Create equity curve from value list."""
    curve = []
    for i, v in enumerate(values):
        day = i + 1
        month = 1 + (day - 1) // 28
        mday = ((day - 1) % 28) + 1
        curve.append({
            "date": f"2024-{month:02d}-{mday:02d}",
            "value": v,
        })
    return curve


# ════════════════════════════════════════════════════════════════
# Helper function unit tests
# ════════════════════════════════════════════════════════════════


class TestHelperCalcReturns:
    def test_basic_returns(self):
        values = [100, 110, 105, 120]
        returns = _calc_returns(values)
        assert len(returns) == 3
        assert abs(returns[0] - 0.10) < 1e-10
        assert abs(returns[1] - (-5 / 110)) < 1e-10
        assert abs(returns[2] - (15 / 105)) < 1e-10

    def test_single_value(self):
        assert _calc_returns([100]) == []

    def test_empty(self):
        assert _calc_returns([]) == []

    def test_zero_value_in_series(self):
        # When previous value is 0, return should be 0.0
        returns = _calc_returns([100, 0, 50])
        assert returns[0] == -1.0  # (0/100 - 1)
        assert returns[1] == 0.0   # previous is 0, fallback

    def test_constant_values(self):
        returns = _calc_returns([100, 100, 100])
        assert all(r == 0.0 for r in returns)


class TestAnnualizedReturn:
    def test_positive_return(self):
        # 10% over 1 year → 10%
        assert abs(_annualized_return(0.10, 1.0) - 0.10) < 1e-10

    def test_multi_year(self):
        # 21% over 2 years → ~10%
        result = _annualized_return(0.21, 2.0)
        assert abs(result - ((1.21) ** 0.5 - 1)) < 1e-10

    def test_sub_year(self):
        # 5% over 0.5 years
        result = _annualized_return(0.05, 0.5)
        assert result > 0.05  # annualized should be higher

    def test_zero_years(self):
        assert _annualized_return(0.10, 0) == 0.0

    def test_negative_years(self):
        assert _annualized_return(0.10, -1) == 0.0

    def test_total_loss(self):
        # Return of -100% → capped at -1.0
        assert _annualized_return(-1.0, 1.0) == -1.0

    def test_worse_than_total_loss(self):
        assert _annualized_return(-1.5, 1.0) == -1.0

    def test_zero_return(self):
        assert _annualized_return(0.0, 1.0) == 0.0


class TestMean:
    def test_basic(self):
        assert _mean([1, 2, 3, 4, 5]) == 3.0

    def test_single(self):
        assert _mean([42]) == 42.0

    def test_empty(self):
        assert _mean([]) == 0.0

    def test_negative(self):
        assert _mean([-10, 10]) == 0.0

    def test_floats(self):
        assert abs(_mean([0.1, 0.2, 0.3]) - 0.2) < 1e-10


class TestMedian:
    def test_odd_count(self):
        assert _median([3, 1, 2]) == 2.0

    def test_even_count(self):
        assert _median([1, 2, 3, 4]) == 2.5

    def test_single(self):
        assert _median([7]) == 7.0

    def test_empty(self):
        assert _median([]) == 0.0

    def test_already_sorted(self):
        assert _median([1, 2, 3, 4, 5]) == 3.0

    def test_duplicates(self):
        assert _median([5, 5, 5]) == 5.0


class TestStdDev:
    def test_basic(self):
        data = [2, 4, 4, 4, 5, 5, 7, 9]
        result = _std_dev(data)
        # Sample std dev
        m = sum(data) / len(data)
        expected = math.sqrt(sum((x - m) ** 2 for x in data) / (len(data) - 1))
        assert abs(result - expected) < 1e-10

    def test_constant_values(self):
        assert _std_dev([5, 5, 5, 5]) == 0.0

    def test_single_value(self):
        assert _std_dev([5]) == 0.0

    def test_empty(self):
        assert _std_dev([]) == 0.0

    def test_two_values(self):
        result = _std_dev([0, 10])
        expected = math.sqrt(50)  # variance = (25+25)/1 = 50
        assert abs(result - expected) < 1e-10


class TestRoundingHelpers:
    def test_r2_normal(self):
        assert _r2(3.14159) == 3.14

    def test_r2_inf(self):
        assert _r2(float("inf")) == 0.0

    def test_r2_neg_inf(self):
        assert _r2(float("-inf")) == 0.0

    def test_r2_nan(self):
        assert _r2(float("nan")) == 0.0

    def test_r4_normal(self):
        assert _r4(3.14159) == 3.1416

    def test_r4_inf(self):
        assert _r4(float("inf")) == 0.0

    def test_r4_nan(self):
        assert _r4(float("nan")) == 0.0

    def test_r2_integer(self):
        assert _r2(5) == 5

    def test_r4_zero(self):
        assert _r4(0.0) == 0.0


class TestCalcHoldingDays:
    def test_basic(self):
        assert _calc_holding_days("2024-01-01", "2024-01-15") == 14

    def test_same_day(self):
        assert _calc_holding_days("2024-01-01", "2024-01-01") == 0

    def test_with_timestamp(self):
        # Should parse only the date part
        assert _calc_holding_days("2024-01-01T09:30:00", "2024-01-10T15:00:00") == 9

    def test_invalid_date(self):
        assert _calc_holding_days("not-a-date", "2024-01-01") == 0

    def test_exit_before_entry(self):
        # Clamped to 0
        assert _calc_holding_days("2024-01-15", "2024-01-01") == 0

    def test_cross_month(self):
        assert _calc_holding_days("2024-01-25", "2024-02-05") == 11

    def test_cross_year(self):
        assert _calc_holding_days("2023-12-25", "2024-01-05") == 11


class TestTradeSummary:
    def test_full_trade(self):
        trade = {
            "symbol": "600001",
            "entryTime": "2024-01-01",
            "exitTime": "2024-01-10",
            "holdingDays": 9,
        }
        result = _trade_summary(trade)
        assert result["symbol"] == "600001"
        assert result["entryTime"] == "2024-01-01"
        assert result["exitTime"] == "2024-01-10"
        assert result["holdingDays"] == 9

    def test_missing_fields(self):
        result = _trade_summary({})
        assert result["symbol"] == ""
        assert result["entryTime"] == ""
        assert result["exitTime"] == ""
        assert result["holdingDays"] == 0


# ════════════════════════════════════════════════════════════════
# Trade Stats Tests
# ════════════════════════════════════════════════════════════════


class TestTradeStats:
    def test_empty_trades(self):
        result = _compute_trade_stats([])
        assert "error" in result

    def test_no_completed_trades(self):
        trades = [{"tradeId": "t1", "symbol": "000001", "entryTime": "2024-01-01"}]
        result = _compute_trade_stats(trades)
        assert "error" in result
        assert result["openTrades"] == 1

    def test_basic_win_loss(self):
        trades = _make_trades([100, -50, 200, -30, 50])
        result = _compute_trade_stats(trades)

        assert result["totalTrades"] == 5
        assert result["winCount"] == 3
        assert result["lossCount"] == 2
        assert result["flatCount"] == 0
        assert result["winRate"] == 0.6

    def test_profit_loss_ratio(self):
        trades = _make_trades([100, -50, 100, -50, 100])
        result = _compute_trade_stats(trades)
        assert result["avgWin"] == 100.0
        assert result["avgLoss"] == 50.0
        assert result["profitLossRatio"] == 2.0

    def test_profit_factor(self):
        trades = _make_trades([100, -50, 100, -50, 100])
        result = _compute_trade_stats(trades)
        # total_wins=300, total_losses=100, factor=3.0
        assert result["profitFactor"] == 3.0

    def test_best_worst_trade(self):
        trades = _make_trades([100, -200, 50, -30, 300])
        result = _compute_trade_stats(trades)
        assert result["bestTrade"]["pnl"] == 300.0
        assert result["worstTrade"]["pnl"] == -200.0
        assert result["bestTrade"]["symbol"] != ""
        assert result["worstTrade"]["symbol"] != ""

    def test_expectancy(self):
        trades = _make_trades([100, -50, 100, -50, 100])
        result = _compute_trade_stats(trades)
        # expectancy = 0.6 * 100 - 0.4 * 50 = 40
        assert result["expectancy"] == 40.0

    def test_all_wins(self):
        trades = _make_trades([100, 200, 50])
        result = _compute_trade_stats(trades)
        assert result["winRate"] == 1.0
        assert result["lossCount"] == 0
        assert result["avgLoss"] == 0.0
        # profit_loss_ratio with no losses → inf handled
        assert result["profitLossRatio"] == 0.0  # _r4(inf) → 0.0

    def test_all_losses(self):
        trades = _make_trades([-100, -200, -50])
        result = _compute_trade_stats(trades)
        assert result["winRate"] == 0.0
        assert result["winCount"] == 0
        assert result["avgWin"] == 0.0
        assert result["profitFactor"] == 0.0

    def test_single_trade_win(self):
        trades = _make_trades([500])
        result = _compute_trade_stats(trades)
        assert result["totalTrades"] == 1
        assert result["winRate"] == 1.0
        assert result["bestTrade"]["pnl"] == 500.0
        assert result["worstTrade"]["pnl"] == 500.0

    def test_single_trade_loss(self):
        trades = _make_trades([-300])
        result = _compute_trade_stats(trades)
        assert result["totalTrades"] == 1
        assert result["winRate"] == 0.0
        assert result["lossCount"] == 1

    def test_flat_trades(self):
        trades = _make_trades([0, 0, 0])
        result = _compute_trade_stats(trades)
        assert result["flatCount"] == 3
        assert result["winCount"] == 0
        assert result["lossCount"] == 0
        assert result["winRate"] == 0.0

    def test_mixed_with_flat(self):
        trades = _make_trades([100, 0, -50, 0, 200])
        result = _compute_trade_stats(trades)
        assert result["winCount"] == 2
        assert result["lossCount"] == 1
        assert result["flatCount"] == 2
        assert result["totalTrades"] == 5

    def test_commission_totals(self):
        trades = _make_trades([100, -50, 200])
        result = _compute_trade_stats(trades)
        assert result["totalCommission"] == 15.0  # 5 * 3
        assert result["totalNetPnl"] == 250.0  # 100 + (-50) + 200
        # grossPnl is pnl + 5 each → 105 + (-45) + 205 = 265
        assert result["totalGrossPnl"] == 265.0

    def test_return_pct_fields_present(self):
        trades = _make_trades([100, -50, 200])
        result = _compute_trade_stats(trades)
        assert "avgReturnPct" in result
        assert "medianReturnPct" in result
        assert "returnStdDev" in result

    def test_mixed_open_and_closed_trades(self):
        """Open trades (no exitTime) should be excluded from stats."""
        trades = _make_trades([100, -50])
        # Add an open trade
        trades.append({
            "tradeId": "open1",
            "symbol": "000099",
            "entryTime": "2024-01-20",
            "entryPrice": 10.0,
            "quantity": 100,
        })
        result = _compute_trade_stats(trades)
        assert result["totalTrades"] == 2  # only completed trades

    def test_zero_entry_price_skips_return_pct(self):
        """If entryPrice * quantity == 0, return pct is not computed for that trade."""
        trades = _make_trades([100])
        trades[0]["entryPrice"] = 0.0
        result = _compute_trade_stats(trades)
        # Should still work without return pct fields if no valid entry values
        assert result["totalTrades"] == 1

    def test_missing_netpnl_defaults_to_zero(self):
        """Trades missing netPnl should default to 0."""
        trades = [{"tradeId": "t1", "exitTime": "2024-01-10"}]
        result = _compute_trade_stats(trades)
        assert result["totalTrades"] == 1
        assert result["flatCount"] == 1


# ════════════════════════════════════════════════════════════════
# Streak Tests
# ════════════════════════════════════════════════════════════════


class TestStreaks:
    def test_basic_streaks(self):
        pnls = [100, 200, 50, -30, -20, 100]
        result = _compute_streaks(pnls)
        assert result["maxConsecutiveWins"] == 3
        assert result["maxConsecutiveLosses"] == 2
        assert result["currentStreak"] == 1
        assert result["currentStreakType"] == "win"

    def test_all_wins(self):
        pnls = [100, 200, 50, 300]
        result = _compute_streaks(pnls)
        assert result["maxConsecutiveWins"] == 4
        assert result["maxConsecutiveLosses"] == 0
        assert result["currentStreak"] == 4
        assert result["currentStreakType"] == "win"

    def test_all_losses(self):
        pnls = [-100, -200, -50]
        result = _compute_streaks(pnls)
        assert result["maxConsecutiveWins"] == 0
        assert result["maxConsecutiveLosses"] == 3
        assert result["currentStreak"] == 3
        assert result["currentStreakType"] == "loss"

    def test_alternating(self):
        pnls = [100, -50, 200, -30]
        result = _compute_streaks(pnls)
        assert result["maxConsecutiveWins"] == 1
        assert result["maxConsecutiveLosses"] == 1
        assert result["currentStreak"] == 1
        assert result["currentStreakType"] == "loss"

    def test_empty_pnls(self):
        result = _compute_streaks([])
        assert result["maxConsecutiveWins"] == 0
        assert result["maxConsecutiveLosses"] == 0
        assert result["currentStreak"] == 0
        assert result["currentStreakType"] == "flat"

    def test_single_win(self):
        result = _compute_streaks([100])
        assert result["maxConsecutiveWins"] == 1
        assert result["currentStreak"] == 1
        assert result["currentStreakType"] == "win"

    def test_single_loss(self):
        result = _compute_streaks([-50])
        assert result["maxConsecutiveLosses"] == 1
        assert result["currentStreak"] == 1
        assert result["currentStreakType"] == "loss"

    def test_flat_only(self):
        result = _compute_streaks([0, 0, 0])
        assert result["maxConsecutiveWins"] == 0
        assert result["maxConsecutiveLosses"] == 0
        assert result["currentStreak"] == 0
        assert result["currentStreakType"] == "flat"

    def test_flat_interspersed(self):
        """Flat trades should break streaks but not count as win/loss."""
        pnls = [100, 100, 0, 100]
        result = _compute_streaks(pnls)
        # The 0 breaks the win streak, so max consecutive wins = 2
        assert result["maxConsecutiveWins"] == 2
        assert result["currentStreak"] == 1
        assert result["currentStreakType"] == "win"

    def test_ends_with_flat(self):
        pnls = [100, 200, 0]
        result = _compute_streaks(pnls)
        # Current streak should look back past flats
        assert result["currentStreak"] == 2
        assert result["currentStreakType"] == "win"

    def test_multiple_streak_segments(self):
        pnls = [100, 100, -50, -50, -50, 100, 100, 100, 100]
        result = _compute_streaks(pnls)
        assert result["maxConsecutiveWins"] == 4
        assert result["maxConsecutiveLosses"] == 3
        assert result["currentStreak"] == 4
        assert result["currentStreakType"] == "win"


# ════════════════════════════════════════════════════════════════
# Risk Metrics Tests
# ════════════════════════════════════════════════════════════════


class TestRiskMetrics:
    def test_insufficient_data_single_point(self):
        result = _compute_risk_metrics(
            [{"date": "2024-01-01", "value": 100000}], 100000, 0.03
        )
        assert "error" in result

    def test_insufficient_data_empty(self):
        result = _compute_risk_metrics([], 100000, 0.03)
        assert "error" in result

    def test_basic_positive_return(self):
        values = [100000 * (1.0004 ** i) for i in range(100)]
        curve = _make_equity_curve(values)
        result = _compute_risk_metrics(curve, 100000, 0.03)

        assert result["totalReturn"] > 0
        assert result["annualizedReturn"] > 0
        assert result["sharpeRatio"] > 0
        assert result["tradingDays"] == 99

    def test_total_return_precision(self):
        """Verify total return = (last/first) - 1."""
        curve = _make_equity_curve([100000, 110000])
        result = _compute_risk_metrics(curve, 100000, 0.03)
        assert result["totalReturn"] == 0.1

    def test_drawdown(self):
        values = [100000, 110000, 120000, 100000, 95000, 105000]
        curve = _make_equity_curve(values)
        result = _compute_risk_metrics(curve, 100000, 0.03)
        expected_dd = (120000 - 95000) / 120000
        assert abs(result["maxDrawdown"] - round(expected_dd, 4)) < 0.001

    def test_drawdown_recovery(self):
        values = [100000, 110000, 120000, 100000, 95000, 110000, 125000]
        curve = _make_equity_curve(values)
        result = _compute_risk_metrics(curve, 100000, 0.03)
        assert result["maxDrawdownRecoveryDays"] > 0

    def test_no_recovery(self):
        values = [100000, 110000, 120000, 100000, 95000]
        curve = _make_equity_curve(values)
        result = _compute_risk_metrics(curve, 100000, 0.03)
        assert result["maxDrawdownRecoveryDays"] == -1

    def test_no_drawdown(self):
        """Monotonically increasing curve should have 0 drawdown."""
        values = [100000, 101000, 102000, 103000, 104000]
        curve = _make_equity_curve(values)
        result = _compute_risk_metrics(curve, 100000, 0.03)
        assert result["maxDrawdown"] == 0.0

    def test_constant_equity(self):
        """Flat equity curve → zero volatility, zero return."""
        values = [100000, 100000, 100000, 100000]
        curve = _make_equity_curve(values)
        result = _compute_risk_metrics(curve, 100000, 0.03)
        assert result["totalReturn"] == 0.0
        assert result["annualizedVolatility"] == 0.0
        assert result["sharpeRatio"] == 0.0

    def test_negative_total_return(self):
        values = [100000, 90000, 85000, 80000]
        curve = _make_equity_curve(values)
        result = _compute_risk_metrics(curve, 100000, 0.03)
        assert result["totalReturn"] < 0
        assert result["annualizedReturn"] < 0

    def test_volatility_positive(self):
        values = [100000, 105000, 95000, 110000, 90000]
        curve = _make_equity_curve(values)
        result = _compute_risk_metrics(curve, 100000, 0.03)
        assert result["annualizedVolatility"] > 0

    def test_win_loss_days(self):
        values = [100000, 101000, 99000, 102000, 100500]
        curve = _make_equity_curve(values)
        result = _compute_risk_metrics(curve, 100000, 0.03)
        assert result["winDays"] == 2   # up at index 1 and 3
        assert result["lossDays"] == 2  # down at index 2 and 4
        assert result["tradingDays"] == 4

    def test_monthly_returns(self):
        values = [100000 + i * 100 for i in range(60)]
        curve = _make_equity_curve(values)
        result = _compute_risk_metrics(curve, 100000, 0.03)
        assert len(result["monthlyReturns"]) >= 1

    def test_var_cvar_present(self):
        values = [100000 * (1 + 0.001 * ((-1) ** i)) for i in range(50)]
        curve = _make_equity_curve(values)
        result = _compute_risk_metrics(curve, 100000, 0.03)
        assert "var95" in result
        assert "cvar95" in result

    def test_best_worst_day(self):
        values = [100000, 110000, 90000, 100000]
        curve = _make_equity_curve(values)
        result = _compute_risk_metrics(curve, 100000, 0.03)
        assert result["bestDay"] > 0
        assert result["worstDay"] < 0

    def test_two_points_minimum(self):
        """Exactly 2 points should work."""
        curve = _make_equity_curve([100000, 105000])
        result = _compute_risk_metrics(curve, 100000, 0.03)
        assert result["totalReturn"] == 0.05
        assert result["tradingDays"] == 1

    def test_drawdown_dates(self):
        values = [100000, 110000, 120000, 100000, 95000, 105000]
        curve = _make_equity_curve(values)
        result = _compute_risk_metrics(curve, 100000, 0.03)
        assert result["maxDrawdownStart"] != ""
        assert result["maxDrawdownEnd"] != ""
        # Start should be the peak date, end the trough date
        assert result["maxDrawdownDuration"] > 0


# ════════════════════════════════════════════════════════════════
# Max Drawdown Analysis Tests
# ════════════════════════════════════════════════════════════════


class TestMaxDrawdownAnalysis:
    def test_empty_values(self):
        result = _max_drawdown_analysis([], [])
        assert result["maxDrawdown"] == 0
        assert result["maxDrawdownDuration"] == 0

    def test_single_value(self):
        result = _max_drawdown_analysis([100000], [{"date": "2024-01-01"}])
        assert result["maxDrawdown"] == 0

    def test_recovery_found(self):
        values = [100, 120, 100, 90, 100, 125]
        curve = [{"date": f"2024-01-{i+1:02d}"} for i in range(len(values))]
        result = _max_drawdown_analysis(values, curve)
        # Peak is 120 at index 1, trough is 90 at index 3
        assert abs(result["maxDrawdown"] - (120 - 90) / 120) < 1e-10
        # Recovery: need value >= 120, happens at index 5
        assert result["maxDrawdownRecoveryDays"] == 2  # from index 3 to 5

    def test_no_recovery(self):
        values = [100, 120, 90, 95]
        curve = [{"date": f"2024-01-{i+1:02d}"} for i in range(len(values))]
        result = _max_drawdown_analysis(values, curve)
        assert result["maxDrawdownRecoveryDays"] == -1


# ════════════════════════════════════════════════════════════════
# Sharpe / Sortino Ratio Tests
# ════════════════════════════════════════════════════════════════


class TestSharpeRatio:
    def test_insufficient_data(self):
        assert _sharpe_ratio([0.01], 0.03) == 0.0
        assert _sharpe_ratio([], 0.03) == 0.0

    def test_positive_consistent_returns(self):
        # Constant daily returns higher than rf → positive Sharpe
        returns = [0.001] * 100
        sharpe = _sharpe_ratio(returns, 0.03)
        assert sharpe > 0

    def test_zero_std(self):
        """Constant excess returns → std = 0 → Sharpe = 0."""
        rf_daily = 0.03 / 252
        returns = [rf_daily] * 50
        assert _sharpe_ratio(returns, 0.03) == 0.0

    def test_negative_sharpe(self):
        """Returns below risk-free rate → negative Sharpe."""
        returns = [-0.01] * 50
        assert _sharpe_ratio(returns, 0.03) < 0


class TestSortinoRatio:
    def test_insufficient_data(self):
        assert _sortino_ratio([0.01], 0.03) == 0.0
        assert _sortino_ratio([], 0.03) == 0.0

    def test_all_positive_excess(self):
        """All positive excess returns → downside deviation = 0 → Sortino = 0."""
        returns = [0.01] * 50
        assert _sortino_ratio(returns, 0.0) == 0.0

    def test_with_negative_returns(self):
        returns = [0.01, -0.02, 0.015, -0.005, 0.008] * 20
        sortino = _sortino_ratio(returns, 0.03)
        # Should be finite and non-zero
        assert sortino != 0.0


# ════════════════════════════════════════════════════════════════
# VaR / CVaR Tests
# ════════════════════════════════════════════════════════════════


class TestVaRCVaR:
    def test_var_empty(self):
        assert _value_at_risk([], 0.05) == 0.0

    def test_cvar_empty(self):
        assert _conditional_var([], 0.05) == 0.0

    def test_var_basic(self):
        returns = sorted([-0.05, -0.03, -0.01, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07])
        var = _value_at_risk(returns, 0.05)
        # 5% of 10 = idx 0, which is -0.05 → abs = 0.05
        assert var == 0.05

    def test_cvar_basic(self):
        returns = [-0.05, -0.03, -0.01, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07]
        cvar = _conditional_var(returns, 0.05)
        # idx = max(int(10*0.05), 1) = 1, tail = [-0.05]
        assert cvar == 0.05

    def test_var_positive_returns(self):
        """VaR should still return the worst tail even if all positive."""
        returns = [0.01, 0.02, 0.03, 0.04, 0.05]
        var = _value_at_risk(returns, 0.05)
        assert var == 0.01


# ════════════════════════════════════════════════════════════════
# Monthly Returns Tests
# ════════════════════════════════════════════════════════════════


class TestMonthlyReturns:
    def test_single_month(self):
        curve = [
            {"date": "2024-01-01", "value": 100000},
            {"date": "2024-01-15", "value": 105000},
        ]
        result = _monthly_returns(curve)
        assert len(result) == 1
        assert result[0]["month"] == "2024-01"
        assert result[0]["return"] == 0.05

    def test_cross_month(self):
        curve = [
            {"date": "2024-01-01", "value": 100000},
            {"date": "2024-01-31", "value": 110000},
            {"date": "2024-02-01", "value": 112000},
            {"date": "2024-02-28", "value": 115000},
        ]
        result = _monthly_returns(curve)
        assert len(result) == 2
        assert result[0]["month"] == "2024-01"
        assert result[1]["month"] == "2024-02"

    def test_too_few_points(self):
        assert _monthly_returns([]) == []
        assert _monthly_returns([{"date": "2024-01-01", "value": 100}]) == []


# ════════════════════════════════════════════════════════════════
# Holding Analysis Tests
# ════════════════════════════════════════════════════════════════


class TestHoldingAnalysis:
    def test_basic_holding(self):
        trades = _make_trades([100, -50, 200], holding_days=[3, 10, 25])
        result = _compute_holding_analysis(trades)

        assert result["avgHoldingDays"] == round((3 + 10 + 25) / 3, 2)
        assert result["shortTermTrades"] == 1
        assert result["mediumTermTrades"] == 1
        assert result["longTermTrades"] == 1

    def test_win_loss_holding_split(self):
        trades = _make_trades([100, -50, 200, -30], holding_days=[3, 15, 5, 20])
        result = _compute_holding_analysis(trades)
        assert result["avgWinHoldingDays"] == 4.0
        assert result["avgLossHoldingDays"] == 17.5

    def test_empty_trades(self):
        result = _compute_holding_analysis([])
        assert "error" in result

    def test_no_completed_trades(self):
        trades = [{"tradeId": "t1", "entryTime": "2024-01-01"}]
        result = _compute_holding_analysis(trades)
        assert "error" in result

    def test_all_short_term(self):
        trades = _make_trades([100, 200, -50], holding_days=[1, 3, 5])
        result = _compute_holding_analysis(trades)
        assert result["shortTermTrades"] == 3
        assert result["mediumTermTrades"] == 0
        assert result["longTermTrades"] == 0
        assert result["shortTermPct"] == 1.0

    def test_all_long_term(self):
        trades = _make_trades([100, 200], holding_days=[30, 60])
        result = _compute_holding_analysis(trades)
        assert result["longTermTrades"] == 2
        assert result["longTermPct"] == 1.0
        assert result["shortTermTrades"] == 0

    def test_boundary_5_days(self):
        """5 days = short term, 6 days = medium term."""
        trades = _make_trades([100, 200], holding_days=[5, 6])
        result = _compute_holding_analysis(trades)
        assert result["shortTermTrades"] == 1
        assert result["mediumTermTrades"] == 1

    def test_boundary_20_days(self):
        """20 days = medium term, 21 days = long term."""
        trades = _make_trades([100, 200], holding_days=[20, 21])
        result = _compute_holding_analysis(trades)
        assert result["mediumTermTrades"] == 1
        assert result["longTermTrades"] == 1

    def test_min_max_holding(self):
        trades = _make_trades([100, 200, -50], holding_days=[1, 50, 10])
        result = _compute_holding_analysis(trades)
        assert result["minHoldingDays"] == 1
        assert result["maxHoldingDays"] == 50

    def test_median_holding(self):
        trades = _make_trades([100, 200, -50], holding_days=[1, 50, 10])
        result = _compute_holding_analysis(trades)
        assert result["medianHoldingDays"] == 10.0

    def test_calc_holding_from_dates(self):
        """If holdingDays is None, should calculate from entry/exit times."""
        trades = [{
            "tradeId": "t1",
            "symbol": "000001",
            "entryTime": "2024-01-01",
            "exitTime": "2024-01-11",
            "entryPrice": 10.0,
            "exitPrice": 11.0,
            "quantity": 100,
            "netPnl": 100,
            "holdingDays": None,
        }]
        result = _compute_holding_analysis(trades)
        assert result["avgHoldingDays"] == 10.0

    def test_zero_holding_days(self):
        """Zero holding days (same-day trade) should be valid."""
        trades = _make_trades([100], holding_days=[0])
        result = _compute_holding_analysis(trades)
        assert result["avgHoldingDays"] == 0.0
        assert result["minHoldingDays"] == 0

    def test_win_holding_no_losses(self):
        trades = _make_trades([100, 200], holding_days=[5, 10])
        result = _compute_holding_analysis(trades)
        assert result["avgWinHoldingDays"] > 0
        assert result["avgLossHoldingDays"] == 0

    def test_loss_holding_no_wins(self):
        trades = _make_trades([-100, -200], holding_days=[5, 10])
        result = _compute_holding_analysis(trades)
        assert result["avgWinHoldingDays"] == 0
        assert result["avgLossHoldingDays"] > 0


# ════════════════════════════════════════════════════════════════
# Composite Score Tests
# ════════════════════════════════════════════════════════════════


class TestCompositeScore:
    def test_excellent_performance(self):
        trade_stats = {"winRate": 0.7, "profitLossRatio": 2.5}
        risk_metrics = {
            "annualizedReturn": 0.20,
            "sharpeRatio": 2.5,
            "maxDrawdown": 0.03,
        }
        result = _compute_composite_score(trade_stats, risk_metrics)
        assert result["composite"] > 80
        assert result["rating"] == "A"

    def test_poor_performance(self):
        trade_stats = {"winRate": 0.25, "profitLossRatio": 0.5}
        risk_metrics = {
            "annualizedReturn": -0.10,
            "sharpeRatio": -0.5,
            "maxDrawdown": 0.35,
        }
        result = _compute_composite_score(trade_stats, risk_metrics)
        assert result["composite"] < 20
        assert result["rating"] in ("D", "F")

    def test_moderate_performance(self):
        trade_stats = {"winRate": 0.5, "profitLossRatio": 1.5}
        risk_metrics = {
            "annualizedReturn": 0.08,
            "sharpeRatio": 1.0,
            "maxDrawdown": 0.15,
        }
        result = _compute_composite_score(trade_stats, risk_metrics)
        assert 30 < result["composite"] < 70

    def test_rating_boundaries(self):
        """Test all rating thresholds."""
        # Rating A: composite >= 80
        result = _compute_composite_score(
            {"winRate": 0.8, "profitLossRatio": 3.0},
            {"annualizedReturn": 0.20, "sharpeRatio": 2.5, "maxDrawdown": 0.03},
        )
        assert result["rating"] == "A"

        # Rating F: composite < 20
        result = _compute_composite_score(
            {"winRate": 0.1, "profitLossRatio": 0.1},
            {"annualizedReturn": -0.15, "sharpeRatio": -1.0, "maxDrawdown": 0.40},
        )
        assert result["rating"] == "F"

    def test_empty_inputs(self):
        """Missing keys should default to 0."""
        result = _compute_composite_score({}, {})
        assert "composite" in result
        assert "rating" in result
        assert result["composite"] >= 0

    def test_profitability_capping(self):
        """annualizedReturn > 15% caps profitability at 100."""
        result = _compute_composite_score(
            {"winRate": 0.5, "profitLossRatio": 1.0},
            {"annualizedReturn": 0.50, "sharpeRatio": 0.0, "maxDrawdown": 0.50},
        )
        assert result["profitability"] == 100.0

    def test_profitability_negative_capped(self):
        """Negative annualized return → profitability capped at 0."""
        result = _compute_composite_score(
            {"winRate": 0.5, "profitLossRatio": 1.0},
            {"annualizedReturn": -0.30, "sharpeRatio": 0.0, "maxDrawdown": 0.05},
        )
        assert result["profitability"] == 0.0

    def test_risk_control_boundary_5pct(self):
        """Max drawdown <= 5% → risk control = 100."""
        result = _compute_composite_score(
            {"winRate": 0.5, "profitLossRatio": 1.0},
            {"annualizedReturn": 0.0, "sharpeRatio": 0.0, "maxDrawdown": 0.05},
        )
        assert result["riskControl"] == 100.0

    def test_risk_control_boundary_30pct(self):
        """Max drawdown >= 30% → risk control = 0."""
        result = _compute_composite_score(
            {"winRate": 0.5, "profitLossRatio": 1.0},
            {"annualizedReturn": 0.0, "sharpeRatio": 0.0, "maxDrawdown": 0.30},
        )
        assert result["riskControl"] == 0.0

    def test_risk_control_between(self):
        """10% drawdown → should be between 0 and 100."""
        result = _compute_composite_score(
            {"winRate": 0.5, "profitLossRatio": 1.0},
            {"annualizedReturn": 0.0, "sharpeRatio": 0.0, "maxDrawdown": 0.10},
        )
        assert 0 < result["riskControl"] < 100

    def test_consistency_win_rate_boundary(self):
        """Win rate 30% → wr_score = 0; win rate 60% → wr_score = 50."""
        result_low = _compute_composite_score(
            {"winRate": 0.3, "profitLossRatio": 0.0},
            {"annualizedReturn": 0.0, "sharpeRatio": 0.0, "maxDrawdown": 0.50},
        )
        result_high = _compute_composite_score(
            {"winRate": 0.6, "profitLossRatio": 0.0},
            {"annualizedReturn": 0.0, "sharpeRatio": 0.0, "maxDrawdown": 0.50},
        )
        assert result_high["consistency"] > result_low["consistency"]

    def test_inf_profit_loss_ratio(self):
        """inf profitLossRatio should be capped at 3.0 in score calc."""
        result = _compute_composite_score(
            {"winRate": 0.5, "profitLossRatio": float("inf")},
            {"annualizedReturn": 0.0, "sharpeRatio": 0.0, "maxDrawdown": 0.10},
        )
        # Should not error
        assert "consistency" in result

    def test_composite_score_range(self):
        """Composite score should always be between 0 and 100."""
        test_cases = [
            ({"winRate": 1.0, "profitLossRatio": 10.0},
             {"annualizedReturn": 1.0, "sharpeRatio": 5.0, "maxDrawdown": 0.0}),
            ({"winRate": 0.0, "profitLossRatio": 0.0},
             {"annualizedReturn": -1.0, "sharpeRatio": -5.0, "maxDrawdown": 1.0}),
        ]
        for ts, rm in test_cases:
            result = _compute_composite_score(ts, rm)
            assert 0 <= result["composite"] <= 100


# ════════════════════════════════════════════════════════════════
# Full Report / Action Routing Integration Tests
# ════════════════════════════════════════════════════════════════


class TestFullReport:
    def test_full_report(self):
        trades = _make_trades(
            [100, -50, 200, -30, 50, 80, -20],
            holding_days=[3, 10, 25, 5, 7, 15, 2],
        )
        values = [100000 * (1.0003 ** i) for i in range(100)]
        curve = _make_equity_curve(values)

        result = compute_performance_metrics({
            "action": "full_report",
            "trades": trades,
            "equityCurve": curve,
            "initialCash": 100000,
            "riskFreeRate": 0.03,
        })

        assert "tradeStats" in result
        assert "riskMetrics" in result
        assert "holdingAnalysis" in result
        assert "compositeScore" in result
        assert result["tradeStats"]["totalTrades"] == 7
        assert result["holdingAnalysis"]["avgHoldingDays"] > 0
        assert result["riskMetrics"]["sharpeRatio"] != 0
        assert 0 <= result["compositeScore"]["composite"] <= 100

    def test_trades_only_action(self):
        trades = _make_trades([100, -50])
        result = compute_performance_metrics({
            "action": "trade_stats",
            "trades": trades,
        })
        assert "totalTrades" in result
        assert result["totalTrades"] == 2

    def test_risk_only_action(self):
        values = [100000, 101000, 102000, 101500, 103000]
        curve = _make_equity_curve(values)
        result = compute_performance_metrics({
            "action": "risk_metrics",
            "equityCurve": curve,
            "initialCash": 100000,
        })
        assert "totalReturn" in result
        assert "sharpeRatio" in result

    def test_holding_only_action(self):
        trades = _make_trades([100, -50, 200], holding_days=[3, 10, 25])
        result = compute_performance_metrics({
            "action": "holding_analysis",
            "trades": trades,
        })
        assert "avgHoldingDays" in result

    def test_default_action_is_full_report(self):
        trades = _make_trades([100])
        values = [100000, 105000, 110000]
        curve = _make_equity_curve(values)
        result = compute_performance_metrics({
            "trades": trades,
            "equityCurve": curve,
        })
        # Default should behave like full_report
        assert "tradeStats" in result

    def test_full_report_trades_only_no_curve(self):
        """Full report with trades but no equity curve."""
        trades = _make_trades([100, -50, 200])
        result = compute_performance_metrics({
            "action": "full_report",
            "trades": trades,
        })
        assert "tradeStats" in result
        assert "holdingAnalysis" in result
        assert "riskMetrics" not in result
        assert "compositeScore" in result  # computed from tradeStats only

    def test_full_report_curve_only_no_trades(self):
        """Full report with equity curve but no trades."""
        values = [100000, 105000, 110000]
        curve = _make_equity_curve(values)
        result = compute_performance_metrics({
            "action": "full_report",
            "equityCurve": curve,
        })
        assert "riskMetrics" in result
        assert "tradeStats" not in result
        assert "holdingAnalysis" not in result

    def test_full_report_empty(self):
        """Full report with no data at all."""
        result = compute_performance_metrics({"action": "full_report"})
        assert result == {}

    def test_defaults_for_missing_config(self):
        """Missing initialCash and riskFreeRate use defaults."""
        values = [100000, 105000, 110000]
        curve = _make_equity_curve(values)
        result = compute_performance_metrics({
            "action": "risk_metrics",
            "equityCurve": curve,
        })
        # Should not error — defaults applied
        assert "totalReturn" in result

    def test_unknown_action_falls_through_to_full_report(self):
        """An unknown action should be treated as full_report."""
        trades = _make_trades([100])
        result = compute_performance_metrics({
            "action": "unknown_action",
            "trades": trades,
        })
        assert "tradeStats" in result

    def test_single_point_curve_in_full_report(self):
        """Curve with < 2 points should be skipped in full_report."""
        trades = _make_trades([100])
        curve = _make_equity_curve([100000])
        result = compute_performance_metrics({
            "action": "full_report",
            "trades": trades,
            "equityCurve": curve,
        })
        assert "tradeStats" in result
        assert "riskMetrics" not in result


# ════════════════════════════════════════════════════════════════
# Edge Cases & Numerical Stability
# ════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_very_large_pnl(self):
        trades = _make_trades([1_000_000, -500_000])
        result = _compute_trade_stats(trades)
        assert result["totalNetPnl"] == 500_000.0

    def test_very_small_pnl(self):
        trades = _make_trades([0.001, -0.002])
        result = _compute_trade_stats(trades)
        assert result["totalTrades"] == 2

    def test_large_number_of_trades(self):
        pnls = [100 if i % 2 == 0 else -50 for i in range(1000)]
        trades = _make_trades(pnls)
        result = _compute_trade_stats(trades)
        assert result["totalTrades"] == 1000
        assert result["winCount"] == 500
        assert result["lossCount"] == 500

    def test_large_equity_curve(self):
        values = [100000 * (1.0001 ** i) for i in range(500)]
        curve = _make_equity_curve(values)
        result = _compute_risk_metrics(curve, 100000, 0.03)
        assert result["tradingDays"] == 499
        assert result["totalReturn"] > 0

    def test_zero_initial_value_in_curve(self):
        """If first value is 0, total return should be 0."""
        curve = _make_equity_curve([0, 100, 200])
        result = _compute_risk_metrics(curve, 0, 0.03)
        assert result["totalReturn"] == 0
