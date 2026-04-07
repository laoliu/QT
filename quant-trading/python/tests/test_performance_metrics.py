"""Tests for the performance metrics calculation engine."""

import math

import pytest

from engines.performance_metrics import (
    PerformanceMetricsResult,
    compute_performance_metrics,
    format_performance_report,
)


def _make_trade(**overrides):
    base = {
        "tradeId": f"t-{id(overrides)}",
        "symbol": "600519",
        "market": "cn_stock",
        "side": "sell",
        "entryTime": "2025-01-10T09:30:00Z",
        "exitTime": "2025-01-15T14:30:00Z",
        "entryPrice": 1800,
        "exitPrice": 1900,
        "quantity": 100,
        "grossPnl": 10000,
        "commission": 50,
        "netPnl": 9950,
        "holdingDays": 5,
        "strategyId": "dual-ma",
        "entryOrderId": "o-1",
        "exitOrderId": "o-2",
    }
    base.update(overrides)
    return base


def _make_snapshot(date, equity):
    return {
        "date": date,
        "equity": equity,
        "cash": equity * 0.3,
        "marketValue": equity * 0.7,
        "dailyPnl": 0,
    }


# ── Empty / edge cases ──


class TestEmptyAndEdgeCases:
    def test_empty_trades(self):
        result = compute_performance_metrics([])
        assert result.total_trades == 0
        assert result.total_pnl == 0
        assert result.win_rate == 0
        assert result.sharpe_ratio == 0
        assert result.max_drawdown_pct == 0

    def test_ignores_open_trades(self):
        trades = [
            _make_trade(exitTime="2025-01-15T14:30:00Z", netPnl=1000),
            _make_trade(exitTime=None, netPnl=500),
        ]
        result = compute_performance_metrics(trades)
        assert result.total_trades == 1
        assert result.total_pnl == 1000


# ── Returns ──


class TestReturns:
    def test_total_return(self):
        trades = [
            _make_trade(netPnl=5000, grossPnl=5200, commission=200),
            _make_trade(netPnl=-2000, grossPnl=-1800, commission=200),
        ]
        result = compute_performance_metrics(trades, initial_capital=100_000)
        assert result.total_pnl == 3000
        assert abs(result.total_return_pct - 3.0) < 0.01

    def test_custom_initial_capital(self):
        trades = [_make_trade(netPnl=5000)]
        result = compute_performance_metrics(trades, initial_capital=50_000)
        assert abs(result.total_return_pct - 10.0) < 0.01


# ── Win rate / payoff ratio ──


class TestTradeQuality:
    def test_win_rate_and_ratios(self):
        trades = [
            _make_trade(
                exitTime="2025-01-12T10:00:00Z",
                netPnl=3000, grossPnl=3100, commission=100,
            ),
            _make_trade(
                exitTime="2025-01-13T10:00:00Z",
                netPnl=1000, grossPnl=1100, commission=100,
            ),
            _make_trade(
                exitTime="2025-01-14T10:00:00Z",
                netPnl=-1000, grossPnl=-900, commission=100,
            ),
        ]
        result = compute_performance_metrics(trades)
        assert result.winning_trades == 2
        assert result.losing_trades == 1
        assert abs(result.win_rate - 66.67) < 0.1
        # avgWin=2000, avgLoss=1000 → payoff_ratio=2.0
        assert abs(result.payoff_ratio - 2.0) < 0.01
        # profit_factor = 4000/1000 = 4.0
        assert abs(result.profit_factor - 4.0) < 0.01

    def test_infinity_profit_factor_no_losses(self):
        trades = [
            _make_trade(netPnl=1000, grossPnl=1050, commission=50),
            _make_trade(netPnl=2000, grossPnl=2050, commission=50),
        ]
        result = compute_performance_metrics(trades)
        assert math.isinf(result.profit_factor)
        assert result.win_rate == 100


# ── Expectancy ──


class TestExpectancy:
    def test_expectancy(self):
        # 2 wins (3000, 1000), 1 loss (-1000)
        # winP=2/3, avgWin=2000, loseP=1/3, avgLoss=1000
        # expectancy = (2/3)*2000 - (1/3)*1000 = 1000
        trades = [
            _make_trade(exitTime="2025-01-12T10:00:00Z", netPnl=3000),
            _make_trade(exitTime="2025-01-13T10:00:00Z", netPnl=1000),
            _make_trade(exitTime="2025-01-14T10:00:00Z", netPnl=-1000),
        ]
        result = compute_performance_metrics(trades)
        assert abs(result.expectancy - 1000) < 1


# ── Holding period ──


class TestHoldingPeriod:
    def test_holding_period_stats(self):
        trades = [
            _make_trade(exitTime="2025-01-12T10:00:00Z", holdingDays=3),
            _make_trade(exitTime="2025-01-13T10:00:00Z", holdingDays=10),
            _make_trade(exitTime="2025-01-14T10:00:00Z", holdingDays=1),
        ]
        result = compute_performance_metrics(trades)
        assert abs(result.avg_holding_days - 4.67) < 0.1
        assert result.max_holding_days == 10
        assert result.min_holding_days == 1

    def test_computes_holding_days_from_timestamps(self):
        trades = [
            _make_trade(
                holdingDays=None,
                entryTime="2025-01-10T09:30:00Z",
                exitTime="2025-01-17T14:30:00Z",
            ),
        ]
        result = compute_performance_metrics(trades)
        assert result.avg_holding_days == 7


# ── Streaks ──


class TestStreaks:
    def test_consecutive_streaks(self):
        trades = [
            _make_trade(exitTime="2025-01-01T10:00:00Z", netPnl=100),
            _make_trade(exitTime="2025-01-02T10:00:00Z", netPnl=200),
            _make_trade(exitTime="2025-01-03T10:00:00Z", netPnl=300),
            _make_trade(exitTime="2025-01-04T10:00:00Z", netPnl=-100),
            _make_trade(exitTime="2025-01-05T10:00:00Z", netPnl=-200),
            _make_trade(exitTime="2025-01-06T10:00:00Z", netPnl=100),
        ]
        result = compute_performance_metrics(trades)
        assert result.max_consecutive_wins == 3
        assert result.max_consecutive_losses == 2


# ── Drawdown ──


class TestDrawdown:
    def test_drawdown_from_trades(self):
        trades = [
            _make_trade(exitTime="2025-01-01T10:00:00Z", netPnl=10000),
            _make_trade(exitTime="2025-01-02T10:00:00Z", netPnl=-5000),
            _make_trade(exitTime="2025-01-03T10:00:00Z", netPnl=-3000),
            _make_trade(exitTime="2025-01-04T10:00:00Z", netPnl=15000),
        ]
        result = compute_performance_metrics(trades, initial_capital=100_000)
        # Peak at 110000, drops to 102000, dd=8000/110000 ≈ 7.27%
        assert 7 < result.max_drawdown_pct < 8
        assert abs(result.max_drawdown_amount - 8000) < 1

    def test_drawdown_from_equity_snapshots(self):
        trades = [_make_trade(netPnl=5000)]
        snapshots = [
            _make_snapshot("2025-01-01", 100000),
            _make_snapshot("2025-01-02", 110000),
            _make_snapshot("2025-01-03", 95000),
            _make_snapshot("2025-01-04", 105000),
        ]
        result = compute_performance_metrics(trades, snapshots)
        # Max DD = (110000-95000)/110000 ≈ 13.64%
        assert abs(result.max_drawdown_pct - 13.64) < 0.1
        # Current DD = (110000-105000)/110000 ≈ 4.55%
        assert abs(result.current_drawdown_pct - 4.55) < 0.1


# ── Risk-adjusted metrics ──


class TestRiskAdjusted:
    def test_sharpe_sortino_from_snapshots(self):
        trades = [_make_trade(netPnl=5000)]
        snapshots = [
            _make_snapshot("2025-01-01", 100000),
            _make_snapshot("2025-01-02", 100500),
            _make_snapshot("2025-01-03", 101200),
            _make_snapshot("2025-01-06", 100800),
            _make_snapshot("2025-01-07", 101500),
            _make_snapshot("2025-01-08", 102000),
        ]
        result = compute_performance_metrics(trades, snapshots, risk_free_rate=0.02)
        assert result.sharpe_ratio != 0
        assert result.sortino_ratio != 0

    def test_calmar_ratio(self):
        trades = [
            _make_trade(exitTime="2025-01-01T10:00:00Z", netPnl=10000),
            _make_trade(exitTime="2025-01-02T10:00:00Z", netPnl=-5000),
        ]
        result = compute_performance_metrics(trades, initial_capital=100_000)
        if result.max_drawdown_pct > 0:
            expected_calmar = result.annualized_return_pct / result.max_drawdown_pct
            assert abs(result.calmar_ratio - expected_calmar) < 0.01


# ── Cost metrics ──


class TestCosts:
    def test_commission_ratio(self):
        trades = [
            _make_trade(
                exitTime="2025-01-12T10:00:00Z",
                grossPnl=10000, commission=500, netPnl=9500,
            ),
            _make_trade(
                exitTime="2025-01-13T10:00:00Z",
                grossPnl=5000, commission=300, netPnl=4700,
            ),
        ]
        result = compute_performance_metrics(trades)
        assert result.total_commission == 800
        # commissionRatio = 800/15000*100 ≈ 5.33%
        assert abs(result.commission_ratio - 5.33) < 0.1


# ── Single trade ──


class TestSingleTrade:
    def test_single_trade(self):
        result = compute_performance_metrics([
            _make_trade(netPnl=5000, holdingDays=3),
        ])
        assert result.total_trades == 1
        assert result.win_rate == 100
        assert result.max_consecutive_wins == 1
        assert result.max_consecutive_losses == 0
        assert result.avg_holding_days == 3


# ── Format report ──


class TestFormatReport:
    def test_report_contains_sections(self):
        trades = [
            _make_trade(
                exitTime="2025-01-12T10:00:00Z",
                netPnl=3000, grossPnl=3100, commission=100, holdingDays=5,
            ),
            _make_trade(
                exitTime="2025-01-13T10:00:00Z",
                netPnl=-1000, grossPnl=-900, commission=100, holdingDays=2,
            ),
            _make_trade(
                exitTime="2025-01-14T10:00:00Z",
                netPnl=2000, grossPnl=2100, commission=100, holdingDays=7,
            ),
        ]
        metrics = compute_performance_metrics(trades)
        report = format_performance_report(metrics)

        assert "Performance Report" in report
        assert "[Returns]" in report
        assert "[Risk-Adjusted]" in report
        assert "[Drawdown]" in report
        assert "[Trade Quality]" in report
        assert "[Holding Period]" in report
        assert "[Streaks]" in report
        assert "[Costs]" in report

    def test_empty_trades_report(self):
        metrics = compute_performance_metrics([])
        report = format_performance_report(metrics)
        assert "Total Trades:         0" in report

    def test_infinity_in_report(self):
        trades = [_make_trade(netPnl=5000, grossPnl=5050, commission=50)]
        metrics = compute_performance_metrics(trades)
        report = format_performance_report(metrics)
        assert "\u221e" in report


# ── to_dict serialization ──


class TestSerialization:
    def test_to_dict(self):
        trades = [
            _make_trade(netPnl=5000, holdingDays=3),
            _make_trade(exitTime="2025-01-16T14:30:00Z", netPnl=-1000, holdingDays=2),
        ]
        result = compute_performance_metrics(trades)
        d = result.to_dict()

        assert "totalPnl" in d
        assert "sharpeRatio" in d
        assert "winRate" in d
        assert "avgHoldingDays" in d
        assert "maxConsecutiveWins" in d
        assert "totalCommission" in d
        assert "calculatedAt" in d

    def test_infinity_serialization(self):
        trades = [_make_trade(netPnl=5000, grossPnl=5050, commission=50)]
        result = compute_performance_metrics(trades)
        d = result.to_dict()
        assert d["profitFactor"] == "Infinity"
