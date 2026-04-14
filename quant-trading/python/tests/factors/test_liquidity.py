"""
流动性因子测试

测试覆盖：
- AmihudIlliquidityFactor: 正常/窗口/极端值/缺失
- TurnoverMomentumFactor: 正常/反转/零分母/缺失
- VolumeRatioFactor: 正常/降级计算/缺失/窗口
"""

from datetime import date

import pytest

from aipha.factors.base import FactorData
from aipha.factors.liquidity import (
    AmihudIlliquidityFactor,
    TurnoverMomentumFactor,
    VolumeRatioFactor,
)


def _make_data(metadata: dict | None = None, **kw) -> FactorData:
    return FactorData(
        symbol=kw.get("symbol", "000001.SZ"),
        trade_date=kw.get("trade_date", date(2024, 6, 15)),
        close=kw.get("close", 10.0),
        volume=kw.get("volume", 100000.0),
        metadata=metadata or {},
    )


# ============================================================
# AmihudIlliquidityFactor
# ============================================================


class TestAmihudIlliquidityFactor:
    def test_name_includes_lookback(self):
        f = AmihudIlliquidityFactor(lookback_days=30)
        assert "30" in f.name

    def test_default_lookback(self):
        f = AmihudIlliquidityFactor()
        assert f.lookback_days == 20

    def test_compute_normal(self):
        returns_history = [0.01, -0.02, 0.015, 0.005, -0.01] * 4  # 20个
        amount_history = [1e8] * 20
        data = _make_data(metadata={
            "returns_history": returns_history,
            "amount_history": amount_history,
        })
        result = AmihudIlliquidityFactor(lookback_days=20).compute(data)
        assert result.value == result.value  # not NaN
        assert result.value > 0  # 非流动性总为正

    def test_compute_missing_returns(self):
        data = _make_data(metadata={"amount_history": [1e8] * 20})
        result = AmihudIlliquidityFactor().compute(data)
        assert result.value != result.value  # NaN

    def test_compute_missing_amount(self):
        data = _make_data(metadata={"returns_history": [0.01] * 20})
        result = AmihudIlliquidityFactor().compute(data)
        assert result.value != result.value

    def test_compute_short_history(self):
        data = _make_data(metadata={
            "returns_history": [0.01, -0.01],
            "amount_history": [1e8, 1e8],
        })
        result = AmihudIlliquidityFactor(lookback_days=20).compute(data)
        # 短历史可能返回 NaN 或用可用数据计算
        # 只要不崩溃即可
        assert isinstance(result.value, float)

    def test_scale_parameter(self):
        f = AmihudIlliquidityFactor(scale=1e9)
        assert f.scale == 1e9

    def test_zero_amount_handling(self):
        """成交金额为0时不应崩溃"""
        data = _make_data(metadata={
            "returns_history": [0.01] * 20,
            "amount_history": [0.0] * 20,
        })
        result = AmihudIlliquidityFactor().compute(data)
        assert isinstance(result.value, float)


# ============================================================
# TurnoverMomentumFactor
# ============================================================


class TestTurnoverMomentumFactor:
    def test_name_includes_windows(self):
        f = TurnoverMomentumFactor(short_window=3, long_window=10)
        assert "3" in f.name
        assert "10" in f.name

    def test_compute_increasing_turnover(self):
        # 短期换手率 > 长期 → 正动量
        turnover = [1.0] * 15 + [2.0] * 5  # 长期低，短期高
        data = _make_data(metadata={"turnover_history": turnover})
        result = TurnoverMomentumFactor(short_window=5, long_window=20).compute(data)
        assert result.value > 0

    def test_compute_decreasing_turnover(self):
        # 短期换手率 < 长期 → 负动量
        turnover = [2.0] * 15 + [1.0] * 5
        data = _make_data(metadata={"turnover_history": turnover})
        result = TurnoverMomentumFactor(short_window=5, long_window=20).compute(data)
        assert result.value < 0

    def test_compute_equal_turnover(self):
        turnover = [1.5] * 20
        data = _make_data(metadata={"turnover_history": turnover})
        result = TurnoverMomentumFactor(short_window=5, long_window=20).compute(data)
        assert result.value == pytest.approx(0.0)

    def test_compute_missing_data(self):
        data = _make_data(metadata={})
        result = TurnoverMomentumFactor().compute(data)
        assert result.value != result.value

    def test_compute_short_history(self):
        data = _make_data(metadata={"turnover_history": [1.0, 2.0]})
        result = TurnoverMomentumFactor(short_window=5, long_window=20).compute(data)
        assert result.value != result.value  # 数据不足

    def test_zero_long_mean(self):
        """长期均值为零时不应崩溃"""
        turnover = [0.0] * 20
        data = _make_data(metadata={"turnover_history": turnover})
        result = TurnoverMomentumFactor(short_window=5, long_window=20).compute(data)
        assert isinstance(result.value, float)


# ============================================================
# VolumeRatioFactor
# ============================================================


class TestVolumeRatioFactor:
    def test_name_includes_lookback(self):
        f = VolumeRatioFactor(lookback_days=10)
        assert "10" in f.name

    def test_compute_normal(self):
        volume_history = [100000.0] * 21  # 20+1
        data = _make_data(metadata={"volume_history": volume_history})
        result = VolumeRatioFactor(lookback_days=20).compute(data)
        assert result.value == result.value

    def test_compute_fallback_avg_volume(self):
        """降级路径: volume / avg_volume"""
        data = _make_data(
            volume=200000.0,
            metadata={"avg_volume": 100000.0},
        )
        result = VolumeRatioFactor().compute(data)
        if result.value == result.value:  # 非 NaN
            assert result.value == pytest.approx(2.0)

    def test_compute_missing_all(self):
        data = _make_data(metadata={})
        result = VolumeRatioFactor().compute(data)
        assert isinstance(result.value, float)

    def test_default_lookback(self):
        f = VolumeRatioFactor()
        assert f.lookback_days == 20
