"""Tests for MarketCapFactor and TurnoverRateFactor."""
from datetime import date

import math
import pytest

from aipha.factors.base import FactorData
from aipha.factors.size import MarketCapFactor, TurnoverRateFactor


def _make_data(
    symbol: str = "000001.SZ",
    circ_mv: float | None = 500000.0,
    turnover_rate: float | None = 3.5,
    close: float = 10.0,
) -> FactorData:
    meta: dict = {}
    if circ_mv is not None:
        meta["circ_mv"] = circ_mv
    if turnover_rate is not None:
        meta["turnover_rate"] = turnover_rate
    return FactorData(
        symbol=symbol,
        trade_date=date(2024, 3, 15),
        close=close,
        metadata=meta,
    )


class TestMarketCapFactor:

    def test_name_inverted(self):
        f = MarketCapFactor()
        assert f.name == "SMALL_CAP"

    def test_name_normal(self):
        f = MarketCapFactor(invert=False)
        assert f.name == "MARKET_CAP"

    def test_compute_inverted(self):
        """小市值因子: invert=True → -log(mv)"""
        f = MarketCapFactor()
        data = _make_data(circ_mv=500000.0)
        result = f.compute(data)
        assert result.value == pytest.approx(-math.log(500000.0))

    def test_compute_normal(self):
        """原始方向: log(mv)"""
        f = MarketCapFactor(invert=False, use_log=True)
        data = _make_data(circ_mv=500000.0)
        result = f.compute(data)
        assert result.value == pytest.approx(math.log(500000.0))

    def test_compute_no_log(self):
        """不用对数"""
        f = MarketCapFactor(invert=True, use_log=False)
        data = _make_data(circ_mv=500000.0)
        result = f.compute(data)
        assert result.value == -500000.0

    def test_smaller_mv_higher_score(self):
        """小市值应该得分更高"""
        f = MarketCapFactor()
        small = f.compute(_make_data(circ_mv=100000.0))
        large = f.compute(_make_data(circ_mv=1000000.0))
        assert small.value > large.value

    def test_missing_mv_nan(self):
        f = MarketCapFactor()
        data = _make_data(circ_mv=None)
        result = f.compute(data)
        assert math.isnan(result.value)

    def test_zero_mv_nan(self):
        f = MarketCapFactor()
        data = _make_data(circ_mv=0.0)
        result = f.compute(data)
        assert math.isnan(result.value)

    def test_metadata_preserved(self):
        f = MarketCapFactor()
        data = _make_data(circ_mv=500000.0)
        result = f.compute(data)
        assert result.metadata["circ_mv"] == 500000.0

    def test_batch(self):
        f = MarketCapFactor()
        data_list = [
            _make_data("A.SZ", circ_mv=100000),
            _make_data("B.SZ", circ_mv=500000),
            _make_data("C.SZ", circ_mv=1000000),
        ]
        results = f.compute_batch(data_list)
        assert len(results) == 3
        # A (smallest) should have highest factor value
        assert results[0].value > results[1].value > results[2].value


class TestTurnoverRateFactor:

    def test_name_inverted(self):
        f = TurnoverRateFactor()
        assert f.name == "LOW_TURNOVER"

    def test_name_normal(self):
        f = TurnoverRateFactor(invert=False)
        assert f.name == "TURNOVER"

    def test_compute_inverted(self):
        f = TurnoverRateFactor()
        data = _make_data(turnover_rate=5.0)
        result = f.compute(data)
        assert result.value == -5.0

    def test_compute_normal(self):
        f = TurnoverRateFactor(invert=False)
        data = _make_data(turnover_rate=5.0)
        result = f.compute(data)
        assert result.value == 5.0

    def test_low_turnover_better(self):
        """低换手应该得分更高（invert=True）"""
        f = TurnoverRateFactor()
        low = f.compute(_make_data(turnover_rate=1.0))
        high = f.compute(_make_data(turnover_rate=10.0))
        assert low.value > high.value

    def test_missing_turnover(self):
        f = TurnoverRateFactor()
        data = _make_data(turnover_rate=None)
        result = f.compute(data)
        assert math.isnan(result.value)
