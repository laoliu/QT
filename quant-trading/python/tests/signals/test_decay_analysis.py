"""信号衰减分析专项测试

覆盖：DecayStats 计算、最优持有期选取、不同持有期收益趋势。
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from aipha.signals.evaluator import (
    DecayStats,
    Signal,
    SignalEvaluator,
    SignalType,
    evaluate_signals,
)

BASE_DATE = datetime(2024, 1, 1)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _rising_prices(symbol: str, n_days: int, base: float = 100.0, slope: float = 1.0) -> dict:
    """线性上升价格：price(d) = base + slope * d"""
    return {symbol: {BASE_DATE + timedelta(days=d): base + slope * d for d in range(n_days)}}


def _buy_at_day(symbol: str, day: int = 0, price: float = 100.0) -> Signal:
    return Signal(symbol=symbol, signal_type=SignalType.BUY, timestamp=BASE_DATE + timedelta(days=day), price=price)


# ---------------------------------------------------------------------------
# DecayStats 基本结构
# ---------------------------------------------------------------------------

class TestDecayStatsStructure:
    def test_default_holding_periods(self):
        """默认持有期为 [1, 3, 5, 10, 20]"""
        ev = SignalEvaluator([_buy_at_day("A")], _rising_prices("A", 25))
        report = ev.evaluate()
        assert report.decay_stats.holding_periods == [1, 3, 5, 10, 20]

    def test_custom_holding_periods_length_matches(self):
        """自定义持有期数量与 avg_returns/win_rates 长度一致"""
        periods = [2, 4, 6]
        ev = SignalEvaluator([_buy_at_day("A")], _rising_prices("A", 20), holding_periods=periods)
        report = ev.evaluate()
        assert len(report.decay_stats.avg_returns) == 3
        assert len(report.decay_stats.win_rates) == 3

    def test_empty_signals_decay_stats_zeros(self):
        """空信号的衰减统计全为零"""
        ev = SignalEvaluator([], {})
        report = ev.evaluate()
        ds: DecayStats = report.decay_stats
        assert all(r == 0.0 for r in ds.avg_returns)
        assert all(w == 0.0 for w in ds.win_rates)

    def test_holding_periods_and_returns_same_length(self):
        """holding_periods 与 avg_returns 长度始终一致"""
        periods = [1, 5, 10]
        ev = SignalEvaluator([_buy_at_day("A")], _rising_prices("A", 15), holding_periods=periods)
        report = ev.evaluate()
        ds = report.decay_stats
        assert len(ds.holding_periods) == len(ds.avg_returns) == len(ds.win_rates)


# ---------------------------------------------------------------------------
# 最优持有期选取
# ---------------------------------------------------------------------------

class TestOptimalPeriod:
    def test_longer_holding_better_for_rising_prices(self):
        """价格单调上涨时，持有越久收益越高 → 最优持有期应是最后一个"""
        prices = _rising_prices("A", n_days=25, slope=2.0)
        signals = [_buy_at_day("A", day=0, price=100.0)]
        periods = [1, 5, 10, 20]
        ev = SignalEvaluator(signals, prices, holding_periods=periods)
        report = ev.evaluate()
        # 单调上涨 → avg_returns 递增 → optimal = 20
        assert report.decay_stats.optimal_period == 20

    def test_falling_price_short_hold_better(self):
        """价格单调下跌时，持有越短亏损越少 → 最优持有期是最短的"""
        prices = {"A": {BASE_DATE + timedelta(days=d): 100.0 - d * 2 for d in range(25)}}
        signals = [_buy_at_day("A", day=0, price=100.0)]
        periods = [1, 5, 10]
        ev = SignalEvaluator(signals, prices, holding_periods=periods)
        report = ev.evaluate()
        # 下跌时最小亏损 → 最优持有期 = 1
        assert report.decay_stats.optimal_period == 1

    def test_optimal_period_from_evaluate_signals_consistent(self):
        """evaluate_signals 函数与 SignalEvaluator 的 optimal_period 一致"""
        prices = _rising_prices("A", 25, slope=1.5)
        signals = [_buy_at_day("A")]
        periods = [1, 3, 5, 10]
        r1 = evaluate_signals(signals, prices, holding_periods=periods)
        r2 = SignalEvaluator(signals, prices, holding_periods=periods).evaluate()
        assert r1.decay_stats.optimal_period == r2.decay_stats.optimal_period


# ---------------------------------------------------------------------------
# 收益随持有期单调性
# ---------------------------------------------------------------------------

class TestDecayReturnMonotonicity:
    def test_avg_returns_increase_with_holding_period_rising_market(self):
        """价格线性上涨时，avg_returns 随持有期单调递增"""
        prices = _rising_prices("A", n_days=25, slope=1.0)  # +1 元/天
        signals = [_buy_at_day("A", day=0, price=100.0)]
        periods = [1, 3, 5, 10]
        ev = SignalEvaluator(signals, prices, holding_periods=periods)
        report = ev.evaluate()
        avg_rets = report.decay_stats.avg_returns
        for i in range(len(avg_rets) - 1):
            assert avg_rets[i] <= avg_rets[i + 1] + 1e-9, (
                f"avg_return[{i}]={avg_rets[i]:.4f} > avg_return[{i+1}]={avg_rets[i+1]:.4f}"
            )

    def test_win_rate_all_ones_if_always_profitable(self):
        """价格必然上涨时，所有持有期的 win_rate = 1.0"""
        prices = _rising_prices("A", n_days=25, slope=2.0)
        signals = [_buy_at_day("A", day=0, price=100.0)]
        ev = SignalEvaluator(signals, prices, holding_periods=[1, 3, 5])
        report = ev.evaluate()
        for wr in report.decay_stats.win_rates:
            assert abs(wr - 1.0) < 1e-9

    def test_win_rate_zero_if_always_losing(self):
        """价格必然下跌时，所有持有期的 win_rate = 0.0"""
        prices = {"A": {BASE_DATE + timedelta(days=d): 100.0 - d * 2 for d in range(25)}}
        signals = [_buy_at_day("A", day=0, price=100.0)]
        ev = SignalEvaluator(signals, prices, holding_periods=[1, 3, 5])
        report = ev.evaluate()
        for wr in report.decay_stats.win_rates:
            assert abs(wr) < 1e-9


# ---------------------------------------------------------------------------
# 近似价格搜索
# ---------------------------------------------------------------------------

class TestFindNearestPrice:
    def test_exact_match_used_first(self):
        """精确时间匹配优先使用"""
        prices = {
            BASE_DATE: 100.0,
            BASE_DATE + timedelta(days=1): 200.0,  # 向后第 1 天
        }
        ev = SignalEvaluator([], {})
        result = ev._find_nearest_price(prices, BASE_DATE)
        assert result == 100.0

    def test_forward_search_within_max_days(self):
        """精确时间缺失时向后搜索 max_days 天"""
        prices = {
            BASE_DATE + timedelta(days=3): 105.0,
        }
        ev = SignalEvaluator([], {})
        # 搜索 BASE_DATE，精确缺失；向后 3 天找到 105.0
        result = ev._find_nearest_price(prices, BASE_DATE, max_days=5)
        assert result == 105.0

    def test_exceeds_max_days_returns_none(self):
        """超出 max_days 返回 None"""
        prices = {
            BASE_DATE + timedelta(days=10): 110.0,
        }
        ev = SignalEvaluator([], {})
        result = ev._find_nearest_price(prices, BASE_DATE, max_days=5)
        assert result is None
