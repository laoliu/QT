"""
Tests for the performance_metrics engine.
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
)


# ── Fixtures ────────────────────────────────────────────────────


def _make_trades(pnls, holding_days=None):
    """Create trade records from a list of PnLs."""
    trades = []
    for i, pnl in enumerate(pnls):
        hd = (holding_days[i] if holding_days else 5)
        trades.append({
            "tradeId": f"t{i}",
            "symbol": f"00000{i % 5}",
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


# ── Trade Stats Tests ───────────────────────────────────────────


class TestTradeStats:
    def test_empty_trades(self):
        result = _compute_trade_stats([])
        assert "error" in result

    def test_no_completed_trades(self):
        trades = [{"tradeId": "t1", "symbol": "000001", "entryTime": "2024-01-01"}]
        result = _compute_trade_stats(trades)
        assert "error" in result

    def test_basic_win_loss(self):
        trades = _make_trades([100, -50, 200, -30, 50])
        result = _compute_trade_stats(trades)

        assert result["totalTrades"] == 5
        assert result["winCount"] == 3
        assert result["lossCount"] == 2
        assert result["winRate"] == 0.6

    def test_profit_loss_ratio(self):
        # 3 wins of 100 each, 2 losses of -50 each
        trades = _make_trades([100, -50, 100, -50, 100])
        result = _compute_trade_stats(trades)

        # avg_win=100, avg_loss=50, ratio=2.0
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

    def test_expectancy(self):
        trades = _make_trades([100, -50, 100, -50, 100])
        result = _compute_trade_stats(trades)

        # expectancy = 0.6 * 100 - 0.4 * 50 = 60 - 20 = 40
        assert result["expectancy"] == 40.0

    def test_all_wins(self):
        trades = _make_trades([100, 200, 50])
        result = _compute_trade_stats(trades)

        assert result["winRate"] == 1.0
        assert result["lossCount"] == 0

    def test_all_losses(self):
        trades = _make_trades([-100, -200, -50])
        result = _compute_trade_stats(trades)

        assert result["winRate"] == 0.0
        assert result["winCount"] == 0


# ── Streak Tests ────────────────────────────────────────────────


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

    def test_all_losses(self):
        pnls = [-100, -200, -50]
        result = _compute_streaks(pnls)

        assert result["maxConsecutiveWins"] == 0
        assert result["maxConsecutiveLosses"] == 3

    def test_alternating(self):
        pnls = [100, -50, 200, -30]
        result = _compute_streaks(pnls)

        assert result["maxConsecutiveWins"] == 1
        assert result["maxConsecutiveLosses"] == 1


# ── Risk Metrics Tests ──────────────────────────────────────────


class TestRiskMetrics:
    def test_insufficient_data(self):
        result = _compute_risk_metrics([{"date": "2024-01-01", "value": 100000}], 100000, 0.03)
        assert "error" in result

    def test_basic_positive_return(self):
        # Steady growth: 100000 to ~110000 over 100 days
        values = [100000 * (1.0004 ** i) for i in range(100)]
        curve = _make_equity_curve(values)
        result = _compute_risk_metrics(curve, 100000, 0.03)

        assert result["totalReturn"] > 0
        assert result["annualizedReturn"] > 0
        assert result["sharpeRatio"] > 0
        assert result["tradingDays"] == 99

    def test_drawdown(self):
        # Up then down
        values = [100000, 110000, 120000, 100000, 95000, 105000]
        curve = _make_equity_curve(values)
        result = _compute_risk_metrics(curve, 100000, 0.03)

        # Max drawdown: from 120000 to 95000 = 20.83%
        expected_dd = (120000 - 95000) / 120000
        assert abs(result["maxDrawdown"] - round(expected_dd, 4)) < 0.001

    def test_drawdown_recovery(self):
        values = [100000, 110000, 120000, 100000, 95000, 110000, 125000]
        curve = _make_equity_curve(values)
        result = _compute_risk_metrics(curve, 100000, 0.03)

        # Recovery after reaching 120000 again at index 6
        assert result["maxDrawdownRecoveryDays"] > 0

    def test_no_recovery(self):
        values = [100000, 110000, 120000, 100000, 95000]
        curve = _make_equity_curve(values)
        result = _compute_risk_metrics(curve, 100000, 0.03)

        assert result["maxDrawdownRecoveryDays"] == -1

    def test_monthly_returns(self):
        # Generate 60 data points across ~2 months
        values = [100000 + i * 100 for i in range(60)]
        curve = _make_equity_curve(values)
        result = _compute_risk_metrics(curve, 100000, 0.03)

        assert len(result["monthlyReturns"]) >= 1


# ── Holding Analysis Tests ──────────────────────────────────────


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

        # Wins: 100 (3d), 200 (5d) → avg 4.0
        # Losses: -50 (15d), -30 (20d) → avg 17.5
        assert result["avgWinHoldingDays"] == 4.0
        assert result["avgLossHoldingDays"] == 17.5

    def test_empty_trades(self):
        result = _compute_holding_analysis([])
        assert "error" in result


# ── Composite Score Tests ───────────────────────────────────────


class TestCompositeScore:
    def test_excellent_performance(self):
        trade_stats = {
            "winRate": 0.7,
            "profitLossRatio": 2.5,
        }
        risk_metrics = {
            "annualizedReturn": 0.20,
            "sharpeRatio": 2.5,
            "maxDrawdown": 0.03,
        }
        result = _compute_composite_score(trade_stats, risk_metrics)

        assert result["composite"] > 80
        assert result["rating"] == "A"

    def test_poor_performance(self):
        trade_stats = {
            "winRate": 0.25,
            "profitLossRatio": 0.5,
        }
        risk_metrics = {
            "annualizedReturn": -0.10,
            "sharpeRatio": -0.5,
            "maxDrawdown": 0.35,
        }
        result = _compute_composite_score(trade_stats, risk_metrics)

        assert result["composite"] < 20
        assert result["rating"] in ("D", "F")

    def test_moderate_performance(self):
        trade_stats = {
            "winRate": 0.5,
            "profitLossRatio": 1.5,
        }
        risk_metrics = {
            "annualizedReturn": 0.08,
            "sharpeRatio": 1.0,
            "maxDrawdown": 0.15,
        }
        result = _compute_composite_score(trade_stats, risk_metrics)

        assert 30 < result["composite"] < 70


# ── Full Report Integration Test ────────────────────────────────


class TestFullReport:
    def test_full_report(self):
        trades = _make_trades([100, -50, 200, -30, 50, 80, -20], holding_days=[3, 10, 25, 5, 7, 15, 2])
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

    def test_trades_only(self):
        trades = _make_trades([100, -50])
        result = compute_performance_metrics({
            "action": "trade_stats",
            "trades": trades,
        })

        assert "totalTrades" in result
        assert result["totalTrades"] == 2

    def test_risk_only(self):
        values = [100000, 101000, 102000, 101500, 103000]
        curve = _make_equity_curve(values)
        result = compute_performance_metrics({
            "action": "risk_metrics",
            "equityCurve": curve,
            "initialCash": 100000,
        })

        assert "totalReturn" in result
        assert "sharpeRatio" in result

    def test_holding_only(self):
        trades = _make_trades([100, -50, 200], holding_days=[3, 10, 25])
        result = compute_performance_metrics({
            "action": "holding_analysis",
            "trades": trades,
        })

        assert "avgHoldingDays" in result
