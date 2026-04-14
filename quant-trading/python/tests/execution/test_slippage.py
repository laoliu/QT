"""Tests for slippage models.

Coverage targets:
- FixedBpsSlippageModel: apply bps slippage to fills
- BUY: price increases
- SELL: price decreases

Quality gates: 100% coverage.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from aipha.domain.models import Bar, Fill, Side
from aipha.execution.slippage import (
    FixedBpsSlippageModel,
    LinearImpactSlippageModel,
    SquareRootImpactSlippageModel,
    TieredImpactSlippageModel,
    CB_TIERED_SPREADS,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def buy_fill() -> Fill:
    """Sample BUY fill."""
    return Fill(
        order_id="ORD001",
        symbol="600000.SH",
        side=Side.BUY,
        qty=100,
        price=10.00,
        t_fill=datetime(2025, 1, 6, 9, 31),
        model="next_bar_open",
    )


@pytest.fixture
def sell_fill() -> Fill:
    """Sample SELL fill."""
    return Fill(
        order_id="ORD002",
        symbol="600000.SH",
        side=Side.SELL,
        qty=200,
        price=10.00,
        t_fill=datetime(2025, 1, 6, 9, 31),
        model="next_bar_open",
    )


# ============================================================================
# FixedBpsSlippageModel Tests
# ============================================================================


class TestFixedBpsSlippageModel:
    """Tests for FixedBpsSlippageModel."""

    def test_buy_slippage_increases_price(self, buy_fill: Fill):
        """BUY fill should have price increased by bps."""
        model = FixedBpsSlippageModel(bps=10.0)  # 10 bps = 0.1%

        slipped = model.apply(buy_fill)

        # 10.00 * (1 + 10/10000) = 10.00 * 1.001 = 10.01
        assert slipped.price == pytest.approx(10.01, rel=1e-9)
        assert slipped.side == Side.BUY
        assert slipped.qty == 100
        assert slipped.order_id == "ORD001"
        assert slipped.symbol == "600000.SH"
        assert slipped.t_fill == buy_fill.t_fill
        assert slipped.model == "next_bar_open"

    def test_sell_slippage_decreases_price(self, sell_fill: Fill):
        """SELL fill should have price decreased by bps."""
        model = FixedBpsSlippageModel(bps=10.0)  # 10 bps = 0.1%

        slipped = model.apply(sell_fill)

        # 10.00 * (1 - 10/10000) = 10.00 * 0.999 = 9.99
        assert slipped.price == pytest.approx(9.99, rel=1e-9)
        assert slipped.side == Side.SELL
        assert slipped.qty == 200

    def test_zero_bps_no_slippage(self, buy_fill: Fill):
        """Zero bps should not change price."""
        model = FixedBpsSlippageModel(bps=0.0)

        slipped = model.apply(buy_fill)

        assert slipped.price == 10.00

    def test_large_bps_slippage(self, buy_fill: Fill):
        """Large bps (e.g., 100 bps = 1%) should work correctly."""
        model = FixedBpsSlippageModel(bps=100.0)

        slipped = model.apply(buy_fill)

        # 10.00 * 1.01 = 10.10
        assert slipped.price == pytest.approx(10.10, rel=1e-9)

    def test_fractional_bps_slippage(self, sell_fill: Fill):
        """Fractional bps should work correctly."""
        model = FixedBpsSlippageModel(bps=5.5)  # 5.5 bps

        slipped = model.apply(sell_fill)

        # 10.00 * (1 - 5.5/10000) = 10.00 * 0.99945 = 9.9945
        assert slipped.price == pytest.approx(9.9945, rel=1e-9)

    def test_slippage_preserves_notional_calculation(self, buy_fill: Fill):
        """Slipped fill should calculate notional correctly."""
        model = FixedBpsSlippageModel(bps=10.0)

        slipped = model.apply(buy_fill)

        # notional = qty * price = 100 * 10.01 = 1001.00
        assert slipped.notional == pytest.approx(1001.00, rel=1e-9)

    def test_slippage_model_immutability(self, buy_fill: Fill):
        """Slippage model should not modify original fill."""
        model = FixedBpsSlippageModel(bps=10.0)
        original_price = buy_fill.price

        slipped = model.apply(buy_fill)

        assert buy_fill.price == original_price  # Original unchanged
        assert slipped.price != original_price  # New fill has different price

    def test_slippage_model_is_frozen(self):
        """FixedBpsSlippageModel should be immutable (frozen dataclass)."""
        model = FixedBpsSlippageModel(bps=10.0)

        with pytest.raises(Exception):  # FrozenInstanceError
            model.bps = 20.0

    def test_high_price_precision(self):
        """Slippage should maintain precision for high prices."""
        fill = Fill(
            order_id="ORD001",
            symbol="600519.SH",  # 贵州茅台
            side=Side.BUY,
            qty=100,
            price=1800.00,  # High price stock
            t_fill=datetime(2025, 1, 6, 9, 31),
            model="next_bar_open",
        )
        model = FixedBpsSlippageModel(bps=10.0)

        slipped = model.apply(fill)

        # 1800.00 * 1.001 = 1801.80
        assert slipped.price == pytest.approx(1801.80, rel=1e-9)

    def test_low_price_precision(self):
        """Slippage should work for low prices."""
        fill = Fill(
            order_id="ORD001",
            symbol="000001.SZ",
            side=Side.SELL,
            qty=1000,
            price=1.50,  # Low price stock
            t_fill=datetime(2025, 1, 6, 9, 31),
            model="next_bar_open",
        )
        model = FixedBpsSlippageModel(bps=10.0)

        slipped = model.apply(fill)

        # 1.50 * 0.999 = 1.4985
        assert slipped.price == pytest.approx(1.4985, rel=1e-9)


# ============================================================================
# LinearImpactSlippageModel Tests
# ============================================================================


@pytest.fixture
def sample_bar() -> Bar:
    """Sample bar with volume/amount data."""
    return Bar(
        symbol="128000.SZ",
        start_ts=datetime(2025, 1, 6, 9, 30),
        end_ts=datetime(2025, 1, 6, 15, 0),
        open=100.0,
        high=102.0,
        low=99.0,
        close=101.0,
        volume=100000.0,  # 10万张
        vwap=100.5,
        metadata={"amount": 10050000.0},  # 1005万元
    )


class TestLinearImpactSlippageModel:
    """Tests for LinearImpactSlippageModel."""

    def test_buy_with_bar_small_order(self, buy_fill: Fill, sample_bar: Bar):
        """Small order → small participation → small impact."""
        model = LinearImpactSlippageModel(base_bps=3.0, impact_coeff=50.0)
        slipped = model.apply(buy_fill, sample_bar)

        # order_notional = 100 * 10 = 1000
        # bar_amount = 10050000 (from metadata)
        # participation = 1000 / 10050000 ≈ 0.0000995
        # bps = 3.0 + 50 * 0.0000995 ≈ 3.005
        # price = 10.0 * (1 + 3.005/10000) ≈ 10.003005
        assert slipped.price > buy_fill.price
        assert slipped.price < buy_fill.price * 1.001  # Less than 10bps

    def test_sell_with_bar(self, sell_fill: Fill, sample_bar: Bar):
        """SELL should reduce price."""
        model = LinearImpactSlippageModel(base_bps=3.0, impact_coeff=50.0)
        slipped = model.apply(sell_fill, sample_bar)
        assert slipped.price < sell_fill.price

    def test_large_order_high_impact(self, sample_bar: Bar):
        """Large order → high participation → high impact."""
        big_fill = Fill(
            order_id="ORD_BIG",
            symbol="128000.SZ",
            side=Side.BUY,
            qty=50000,  # 5万张
            price=100.0,
            t_fill=datetime(2025, 1, 6, 9, 31),
            model="next_bar_open",
        )
        model = LinearImpactSlippageModel(base_bps=3.0, impact_coeff=50.0)
        slipped = model.apply(big_fill, sample_bar)

        # notional = 50000 * 100 = 5_000_000
        # participation = 5_000_000 / 10_050_000 ≈ 0.4975
        # bps = 3 + 50 * 0.4975 ≈ 27.87
        expected_bps = 3.0 + 50.0 * (5_000_000 / 10_050_000)
        expected_price = 100.0 * (1 + expected_bps / 10000.0)
        assert slipped.price == pytest.approx(expected_price, rel=1e-6)

    def test_no_bar_uses_fallback(self, buy_fill: Fill):
        """No bar → use fallback_bps."""
        model = LinearImpactSlippageModel(fallback_bps=15.0)
        slipped = model.apply(buy_fill, bar=None)

        expected = 10.0 * (1 + 15.0 / 10000.0)
        assert slipped.price == pytest.approx(expected, rel=1e-9)

    def test_zero_volume_bar_uses_fallback(self, buy_fill: Fill):
        """Zero-volume bar → fallback."""
        zero_bar = Bar(
            symbol="128000.SZ",
            start_ts=datetime(2025, 1, 6, 9, 30),
            end_ts=datetime(2025, 1, 6, 15, 0),
            open=100.0, high=100.0, low=100.0, close=100.0,
            volume=0.0,
        )
        model = LinearImpactSlippageModel(fallback_bps=15.0)
        slipped = model.apply(buy_fill, zero_bar)

        expected = 10.0 * (1 + 15.0 / 10000.0)
        assert slipped.price == pytest.approx(expected, rel=1e-9)

    def test_max_bps_cap(self, sample_bar: Bar):
        """Slippage should be capped at max_bps."""
        huge_fill = Fill(
            order_id="ORD_HUGE",
            symbol="128000.SZ",
            side=Side.BUY,
            qty=100000,
            price=200.0,
            t_fill=datetime(2025, 1, 6, 9, 31),
            model="next_bar_open",
        )
        model = LinearImpactSlippageModel(
            base_bps=3.0, impact_coeff=500.0, max_bps=50.0,
        )
        slipped = model.apply(huge_fill, sample_bar)

        # Without cap: very large bps. With cap: exactly 50 bps.
        expected = 200.0 * (1 + 50.0 / 10000.0)
        assert slipped.price == pytest.approx(expected, rel=1e-9)

    def test_amount_from_vwap_when_no_metadata(self, buy_fill: Fill):
        """When metadata has no amount, use volume*vwap."""
        bar_no_amount = Bar(
            symbol="128000.SZ",
            start_ts=datetime(2025, 1, 6, 9, 30),
            end_ts=datetime(2025, 1, 6, 15, 0),
            open=100.0, high=102.0, low=99.0, close=101.0,
            volume=100000.0,
            vwap=100.5,
            metadata={},  # no amount
        )
        model = LinearImpactSlippageModel(base_bps=3.0, impact_coeff=50.0)
        slipped = model.apply(buy_fill, bar_no_amount)

        # bar_amount = 100000 * 100.5 = 10_050_000 (same as sample_bar)
        assert slipped.price > buy_fill.price

    def test_backward_compat_no_bar_arg(self, buy_fill: Fill):
        """Calling apply without bar arg still works (backward compatibility)."""
        model = LinearImpactSlippageModel(fallback_bps=8.0)
        slipped = model.apply(buy_fill)
        expected = 10.0 * (1 + 8.0 / 10000.0)
        assert slipped.price == pytest.approx(expected, rel=1e-9)


# ============================================================================
# SquareRootImpactSlippageModel Tests
# ============================================================================


class TestSquareRootImpactSlippageModel:
    """Tests for SquareRootImpactSlippageModel (Almgren-Chriss simplified)."""

    def test_buy_with_bar(self, buy_fill: Fill, sample_bar: Bar):
        """Buy order gets price increased by sqrt impact."""
        model = SquareRootImpactSlippageModel(impact_coeff=0.5, sigma_daily=0.02)
        slipped = model.apply(buy_fill, sample_bar)
        assert slipped.price > buy_fill.price

    def test_sell_with_bar(self, sell_fill: Fill, sample_bar: Bar):
        """Sell order gets price decreased."""
        model = SquareRootImpactSlippageModel(impact_coeff=0.5, sigma_daily=0.02)
        slipped = model.apply(sell_fill, sample_bar)
        assert slipped.price < sell_fill.price

    def test_sqrt_scaling(self, sample_bar: Bar):
        """Impact should scale with sqrt of participation rate."""
        import math
        model = SquareRootImpactSlippageModel(
            impact_coeff=0.5, sigma_daily=0.02, use_bar_volatility=False,
        )

        fill_small = Fill("O1", "128000.SZ", Side.BUY, 100, 100.0,
                          datetime(2025, 1, 6, 9, 31), "test")
        fill_large = Fill("O2", "128000.SZ", Side.BUY, 10000, 100.0,
                          datetime(2025, 1, 6, 9, 31), "test")

        s1 = model.apply(fill_small, sample_bar)
        s2 = model.apply(fill_large, sample_bar)

        # Impact ratio should be sqrt(10000/100) = 10
        impact1 = s1.price - 100.0
        impact2 = s2.price - 100.0
        ratio = impact2 / impact1
        expected_ratio = math.sqrt(10000 / 100)
        assert ratio == pytest.approx(expected_ratio, rel=1e-6)

    def test_no_bar_uses_fallback(self, buy_fill: Fill):
        """No bar → fallback_bps."""
        model = SquareRootImpactSlippageModel(fallback_bps=12.0)
        slipped = model.apply(buy_fill, bar=None)
        expected = 10.0 * (1 + 12.0 / 10000.0)
        assert slipped.price == pytest.approx(expected, rel=1e-9)

    def test_uses_bar_volatility(self, buy_fill: Fill):
        """When use_bar_volatility=True, should use bar's intraday range."""
        # Bar with large range: (102-99)/101 ≈ 2.97% > sigma_daily=0.02
        wide_bar = Bar(
            symbol="128000.SZ",
            start_ts=datetime(2025, 1, 6, 9, 30),
            end_ts=datetime(2025, 1, 6, 15, 0),
            open=100.0, high=110.0, low=90.0, close=100.0,
            volume=100000.0, vwap=100.0,
            metadata={"amount": 10_000_000.0},
        )
        model_with_vol = SquareRootImpactSlippageModel(
            impact_coeff=0.5, sigma_daily=0.01, use_bar_volatility=True,
        )
        model_without_vol = SquareRootImpactSlippageModel(
            impact_coeff=0.5, sigma_daily=0.01, use_bar_volatility=False,
        )

        s_with = model_with_vol.apply(buy_fill, wide_bar)
        s_without = model_without_vol.apply(buy_fill, wide_bar)

        # With bar volatility should be higher (bar range 20% > sigma 1%)
        assert s_with.price > s_without.price

    def test_max_bps_cap(self, sample_bar: Bar):
        """Impact should be capped."""
        huge_fill = Fill("O1", "128000.SZ", Side.BUY, 100000, 100.0,
                         datetime(2025, 1, 6, 9, 31), "test")
        model = SquareRootImpactSlippageModel(
            impact_coeff=5.0, sigma_daily=0.05, max_bps=30.0,
            use_bar_volatility=False,
        )
        slipped = model.apply(huge_fill, sample_bar)
        expected = 100.0 * (1 + 30.0 / 10000.0)
        assert slipped.price == pytest.approx(expected, rel=1e-9)

    def test_backward_compat_no_bar_arg(self, buy_fill: Fill):
        """Calling apply without bar arg works (backward compat)."""
        model = SquareRootImpactSlippageModel(fallback_bps=7.0)
        slipped = model.apply(buy_fill)
        expected = 10.0 * (1 + 7.0 / 10000.0)
        assert slipped.price == pytest.approx(expected, rel=1e-9)

    def test_frozen(self):
        """Model should be immutable."""
        model = SquareRootImpactSlippageModel(impact_coeff=0.5)
        with pytest.raises(Exception):
            model.impact_coeff = 1.0


# ============================================================================
# FixedBpsSlippageModel backward-compat with bar param
# ============================================================================


class TestFixedBpsWithBarParam:
    """Ensure FixedBpsSlippageModel still works when bar is explicitly passed."""

    def test_bar_is_ignored(self, buy_fill: Fill, sample_bar: Bar):
        """FixedBps should return same result regardless of bar."""
        model = FixedBpsSlippageModel(bps=10.0)
        without_bar = model.apply(buy_fill)
        with_bar = model.apply(buy_fill, sample_bar)
        assert without_bar.price == with_bar.price


# ============================================================================
# Protocol 合规性测试
# ============================================================================


class TestSlippageModelProtocol:
    """验证所有实现都符合 SlippageModel Protocol"""

    def test_fixed_bps_is_slippage_model(self):
        from aipha.execution.slippage import SlippageModel
        assert isinstance(FixedBpsSlippageModel(bps=5.0), SlippageModel)

    def test_linear_impact_is_slippage_model(self):
        from aipha.execution.slippage import SlippageModel
        assert isinstance(LinearImpactSlippageModel(), SlippageModel)

    def test_square_root_is_slippage_model(self):
        from aipha.execution.slippage import SlippageModel
        assert isinstance(SquareRootImpactSlippageModel(), SlippageModel)


# ============================================================================
# 滑点工厂函数测试
# ============================================================================


class TestSlippageFactory:
    """测试 config_runner._create_slippage_model 工厂"""

    def test_default_creates_fixed(self):
        from aipha.backtest.config_runner import CostConfig, _create_slippage_model
        s = _create_slippage_model(CostConfig(slippage_bps=5.0))
        assert isinstance(s, FixedBpsSlippageModel)
        assert s.bps == 5.0

    def test_explicit_fixed(self):
        from aipha.backtest.config_runner import CostConfig, _create_slippage_model
        s = _create_slippage_model(CostConfig(slippage_model="fixed", slippage_bps=3.0))
        assert isinstance(s, FixedBpsSlippageModel)

    def test_linear_impact(self):
        from aipha.backtest.config_runner import CostConfig, _create_slippage_model
        s = _create_slippage_model(CostConfig(
            slippage_model="linear_impact",
            slippage_params={"base_bps": 2.0, "impact_coeff": 30.0},
        ))
        assert isinstance(s, LinearImpactSlippageModel)
        assert s.base_bps == 2.0

    def test_square_root_impact(self):
        from aipha.backtest.config_runner import CostConfig, _create_slippage_model
        s = _create_slippage_model(CostConfig(
            slippage_model="square_root_impact",
            slippage_params={"impact_coeff": 0.8, "sigma_daily": 0.03},
        ))
        assert isinstance(s, SquareRootImpactSlippageModel)
        assert s.impact_coeff == 0.8

    def test_unknown_model_raises(self):
        from aipha.backtest.config_runner import CostConfig, _create_slippage_model
        import pytest as _pt
        with _pt.raises(ValueError, match="不支持的滑点模型"):
            _create_slippage_model(CostConfig(slippage_model="unknown_model"))

    def test_case_insensitive(self):
        from aipha.backtest.config_runner import CostConfig, _create_slippage_model
        s = _create_slippage_model(CostConfig(slippage_model="Fixed", slippage_bps=1.0))
        assert isinstance(s, FixedBpsSlippageModel)


# ============================================================================
# TieredImpactSlippageModel tests (R94-V2 增强)
# ============================================================================


class TestTieredImpactSlippage:
    """分层买卖价差滑点模型测试。"""

    def _make_bar(self, amount: float, volume: float = 10000) -> Bar:
        return Bar(
            symbol="T001",
            open=100.0, high=102.0, low=98.0, close=101.0,
            volume=volume, vwap=100.5,
            start_ts=datetime(2024, 1, 2, 9, 30),
            end_ts=datetime(2024, 1, 2, 15, 0),
            metadata={"amount": amount},
        )

    def _make_fill(self, side: Side = Side.BUY, qty: float = 100) -> Fill:
        return Fill(
            order_id="test", symbol="T001", side=side,
            qty=qty, price=100.0,
            t_fill=datetime(2024, 1, 2, 9, 30), model="test",
        )

    def test_large_cb_low_bps(self):
        """大盘CB(>5000万)使用低base_bps。"""
        m = TieredImpactSlippageModel()
        bar = self._make_bar(amount=8000.0)
        fill = self._make_fill()
        result = m.apply(fill, bar)
        # base_bps = 5.0 for amount >= 5000
        assert result.price > fill.price  # BUY: price up
        spread_bps = (result.price - fill.price) / fill.price * 10000
        assert spread_bps >= 5.0  # at least base

    def test_small_cb_high_bps(self):
        """小盘CB(<500万)使用高base_bps。"""
        m = TieredImpactSlippageModel()
        bar = self._make_bar(amount=200.0)
        fill = self._make_fill()
        result = m.apply(fill, bar)
        spread_bps = (result.price - fill.price) / fill.price * 10000
        assert spread_bps >= 40.0  # base = 40 for <500

    def test_mid_cb_medium_bps(self):
        """中盘CB(500-2000万)使用中等base_bps。"""
        m = TieredImpactSlippageModel()
        bar = self._make_bar(amount=1000.0)
        fill = self._make_fill()
        result = m.apply(fill, bar)
        spread_bps = (result.price - fill.price) / fill.price * 10000
        assert spread_bps >= 20.0  # base = 20 for [500, 2000)

    def test_no_bar_uses_fallback(self):
        """无bar时使用fallback_bps。"""
        m = TieredImpactSlippageModel(fallback_bps=25.0)
        fill = self._make_fill()
        result = m.apply(fill, bar=None)
        spread_bps = (result.price - fill.price) / fill.price * 10000
        assert abs(spread_bps - 25.0) < 0.01

    def test_sell_price_decreases(self):
        """卖出时价格下调。"""
        m = TieredImpactSlippageModel()
        bar = self._make_bar(amount=3000.0)
        fill = self._make_fill(side=Side.SELL)
        result = m.apply(fill, bar)
        assert result.price < fill.price

    def test_max_bps_cap(self):
        """滑点不超过max_bps上限。"""
        m = TieredImpactSlippageModel(max_bps=50.0, impact_coeff=1000.0)
        bar = self._make_bar(amount=100.0, volume=1)
        fill = self._make_fill(qty=10000)
        result = m.apply(fill, bar)
        spread_bps = abs(result.price - fill.price) / fill.price * 10000
        assert spread_bps <= 50.1  # allow small fp error

    def test_custom_tiers(self):
        """自定义分层规则。"""
        custom = ((1000.0, 8.0), (0.0, 30.0))
        m = TieredImpactSlippageModel(tiers=custom, impact_coeff=0.0)
        # amount=1500 >= 1000, base=8.0
        bar = self._make_bar(amount=1500.0)
        fill = self._make_fill()
        result = m.apply(fill, bar)
        spread_bps = (result.price - fill.price) / fill.price * 10000
        assert abs(spread_bps - 8.0) < 0.01

    def test_engine_builder_tiered(self):
        """engine_builder 支持 tiered_impact 模型。"""
        from aipha.backtest.config_runner import CostConfig
        from aipha.backtest.engine_builder import _create_slippage_model
        cost = CostConfig(
            slippage_model="tiered_impact",
            slippage_params={"fallback_bps": 15.0, "max_bps": 120.0},
        )
        s = _create_slippage_model(cost)
        assert isinstance(s, TieredImpactSlippageModel)
        assert s.fallback_bps == 15.0
        assert s.max_bps == 120.0
