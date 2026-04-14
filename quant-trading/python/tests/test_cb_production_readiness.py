"""Tests for CB production readiness improvements.

Covers:
- PositionLimit/DrawdownLimit/TurnoverLimit lot_size parameterization
- ForceRedeemExposureLimit
- LiquidityConstraint
- CB circuit breaker (MarketRulesConvertibleBond)
- CBRotationStrategy forced redemption integration
- CB monitoring alerts
- OrderType extensions
- PaperTradingGateway.from_cb_mode()
- Config runner volume_constraint support
- CB data adapter is_called metadata
"""

from __future__ import annotations

import time
from datetime import datetime
from decimal import Decimal

import pytest

from aipha.domain.models import Bar, Order, OrderType, Side


# ============================================================================
# PositionLimit lot_size parameterization
# ============================================================================


class TestPositionLimitLotSize:
    """Test that PositionLimit uses parameterized lot_size."""

    def test_default_lot_size_is_100(self):
        from aipha.risk.constraints import PositionLimit

        pl = PositionLimit(max_weight=0.10)
        assert pl.lot_size == 100

    def test_cb_lot_size_10(self):
        from aipha.risk.constraints import PositionLimit

        pl = PositionLimit(max_weight=0.10, lot_size=10)
        assert pl.lot_size == 10

    def test_adjusted_qty_rounds_to_lot_size_10(self):
        from aipha.risk.constraints import PositionLimit

        pl = PositionLimit(max_weight=0.05, lot_size=10)
        # NAV=100000, max_weight=5%, price=100 → max_value=5000 → max_qty=50
        result = pl.check(
            symbol="123456.SH",
            qty=100,
            side="BUY",
            current_positions={},
            nav=100_000,
            prices={"123456.SH": 100.0},
        )
        assert not result.passed
        # adjusted_qty should be rounded to lot_size=10
        assert result.adjusted_qty is not None
        assert result.adjusted_qty % 10 == 0

    def test_adjusted_qty_rounds_to_lot_size_100(self):
        from aipha.risk.constraints import PositionLimit

        pl = PositionLimit(max_weight=0.05, lot_size=100)
        result = pl.check(
            symbol="000001.SZ",
            qty=1000,
            side="BUY",
            current_positions={},
            nav=100_000,
            prices={"000001.SZ": 15.0},
        )
        assert not result.passed
        assert result.adjusted_qty is not None
        assert result.adjusted_qty % 100 == 0

    def test_sell_always_passes(self):
        from aipha.risk.constraints import PositionLimit

        pl = PositionLimit(max_weight=0.05, lot_size=10)
        result = pl.check(
            symbol="123456.SH", qty=100, side="SELL",
            current_positions={"123456.SH": 100},
            nav=100_000, prices={"123456.SH": 100.0},
        )
        assert result.passed


# ============================================================================
# DrawdownLimit lot_size
# ============================================================================


class TestDrawdownLimitLotSize:
    """Test DrawdownLimit uses parameterized lot_size."""

    def test_default_lot_size(self):
        from aipha.risk.constraints import DrawdownLimit

        dl = DrawdownLimit()
        assert dl.lot_size == 100

    def test_cb_lot_size(self):
        from aipha.risk.constraints import DrawdownLimit

        dl = DrawdownLimit(lot_size=10)
        assert dl.lot_size == 10


# ============================================================================
# TurnoverLimit lot_size
# ============================================================================


class TestTurnoverLimitLotSize:
    """Test TurnoverLimit uses parameterized lot_size."""

    def test_default_lot_size(self):
        from aipha.risk.constraints import TurnoverLimit

        tl = TurnoverLimit()
        assert tl.lot_size == 100

    def test_cb_lot_size(self):
        from aipha.risk.constraints import TurnoverLimit

        tl = TurnoverLimit(lot_size=10)
        assert tl.lot_size == 10

    def test_single_trade_limit_rounds_to_lot_size_10(self):
        from aipha.risk.constraints import TurnoverLimit

        tl = TurnoverLimit(max_single_trade=0.05, lot_size=10)
        result = tl.check(
            symbol="123456.SH", qty=100, side="BUY",
            current_positions={}, nav=100_000,
            prices={"123456.SH": 100.0},
        )
        # 100 * 100 = 10000 > 5% * 100000 = 5000
        assert not result.passed
        assert result.adjusted_qty is not None
        assert result.adjusted_qty % 10 == 0


# ============================================================================
# ForceRedeemExposureLimit
# ============================================================================


class TestForceRedeemExposureLimit:
    """Test CB-specific forced redemption exposure constraint."""

    def test_within_limit(self):
        from aipha.risk.constraints import ForceRedeemExposureLimit

        c = ForceRedeemExposureLimit(max_weight=0.30, cb_value_threshold=125.0, lot_size=10)
        c.update_cb_values({"A.SH": 130.0})

        result = c.check(
            symbol="A.SH", qty=10, side="BUY",
            current_positions={}, nav=100_000,
            prices={"A.SH": 100.0},
        )
        # 10 * 100 = 1000 / 100000 = 1% << 30%
        assert result.passed

    def test_exceeds_limit(self):
        from aipha.risk.constraints import ForceRedeemExposureLimit

        c = ForceRedeemExposureLimit(max_weight=0.10, cb_value_threshold=125.0, lot_size=10)
        c.update_cb_values({"A.SH": 130.0, "B.SH": 128.0})

        result = c.check(
            symbol="A.SH", qty=200, side="BUY",
            current_positions={"B.SH": 100},
            nav=100_000,
            prices={"A.SH": 100.0, "B.SH": 100.0},
        )
        # B already at 100*100=10000 = 10%, adding more would exceed
        assert not result.passed
        assert result.violation_type.value == "FORCE_REDEEM_EXPOSURE"

    def test_sell_always_passes(self):
        from aipha.risk.constraints import ForceRedeemExposureLimit

        c = ForceRedeemExposureLimit()
        result = c.check(
            symbol="A.SH", qty=100, side="SELL",
            current_positions={}, nav=100_000, prices={},
        )
        assert result.passed

    def test_non_force_redeem_zone_passes(self):
        from aipha.risk.constraints import ForceRedeemExposureLimit

        c = ForceRedeemExposureLimit(max_weight=0.10, cb_value_threshold=125.0)
        c.update_cb_values({"A.SH": 80.0})  # Below threshold

        result = c.check(
            symbol="A.SH", qty=500, side="BUY",
            current_positions={}, nav=100_000,
            prices={"A.SH": 100.0},
        )
        assert result.passed


# ============================================================================
# LiquidityConstraint
# ============================================================================


class TestLiquidityConstraint:
    """Test CB liquidity constraint."""

    def test_within_volume_limit(self):
        from aipha.risk.constraints import LiquidityConstraint

        c = LiquidityConstraint(max_participation_rate=0.10, lot_size=10)
        c.update_volumes({"A.SH": 10_000})

        result = c.check(
            symbol="A.SH", qty=500, side="BUY",
            current_positions={}, nav=100_000,
            prices={"A.SH": 100.0},
        )
        # 500 <= 10000 * 0.10 = 1000
        assert result.passed

    def test_exceeds_volume_limit(self):
        from aipha.risk.constraints import LiquidityConstraint

        c = LiquidityConstraint(max_participation_rate=0.10, lot_size=10)
        c.update_volumes({"A.SH": 1_000})

        result = c.check(
            symbol="A.SH", qty=200, side="BUY",
            current_positions={}, nav=100_000,
            prices={"A.SH": 100.0},
        )
        # 200 > 1000 * 0.10 = 100
        assert not result.passed
        assert result.violation_type.value == "LIQUIDITY_LIMIT"
        assert result.adjusted_qty is not None
        assert result.adjusted_qty % 10 == 0

    def test_no_volume_data_blocks(self):
        """P1-2 修复: 无成交量数据 → 阻止买入。"""
        from aipha.risk.constraints import LiquidityConstraint

        c = LiquidityConstraint()
        result = c.check(
            symbol="A.SH", qty=100, side="BUY",
            current_positions={}, nav=100_000,
            prices={"A.SH": 100.0},
        )
        assert not result.passed
        assert result.violation_type.value == "LIQUIDITY_LIMIT"


# ============================================================================
# CB Circuit Breaker
# ============================================================================


class TestCBCircuitBreaker:
    """Test convertible bond first-day circuit breaker."""

    def _make_bar(self, open_p: float, high: float, low: float, close: float) -> Bar:
        now = datetime(2024, 1, 15, 10, 0, 0)
        return Bar(
            symbol="123456.SH", start_ts=now, end_ts=now,
            open=open_p, high=high, low=low, close=close,
            volume=10000,
        )

    def _make_order(self, side: Side = Side.BUY, qty: int = 10) -> Order:
        return Order(
            order_id="test_001", symbol="123456.SH", side=side,
            qty=qty, order_type=OrderType.MARKET,
            t_submit=datetime(2024, 1, 15),
        )

    def test_first_day_20pct_up_halt(self):
        from aipha.market.rules_convertible_bond import MarketRulesConvertibleBond

        rules = MarketRulesConvertibleBond()
        bar = self._make_bar(open_p=100, high=121, low=99, close=115)
        order = self._make_order()

        result = rules.precheck_order(
            order=order, bar=bar, is_first_day=True,
        )
        # high=121 vs open=100 → 21% > 20%
        assert not result.allowed

    def test_first_day_30pct_down_halt(self):
        from aipha.market.rules_convertible_bond import MarketRulesConvertibleBond

        rules = MarketRulesConvertibleBond()
        bar = self._make_bar(open_p=100, high=101, low=69, close=75)
        order = self._make_order()

        result = rules.precheck_order(
            order=order, bar=bar, is_first_day=True,
        )
        # low=69 → (100-69)/100 = 31% > 30%
        assert not result.allowed

    def test_first_day_normal_passes(self):
        from aipha.market.rules_convertible_bond import MarketRulesConvertibleBond

        rules = MarketRulesConvertibleBond()
        bar = self._make_bar(open_p=100, high=110, low=95, close=105)
        order = self._make_order()

        result = rules.precheck_order(
            order=order, bar=bar, is_first_day=True,
        )
        assert result.allowed

    def test_non_first_day_no_limit(self):
        from aipha.market.rules_convertible_bond import MarketRulesConvertibleBond

        rules = MarketRulesConvertibleBond()
        bar = self._make_bar(open_p=100, high=150, low=80, close=120)
        order = self._make_order()

        result = rules.precheck_order(
            order=order, bar=bar, is_first_day=False,
        )
        assert result.allowed


# ============================================================================
# ForceRedeem integration in CBRotationStrategy
# ============================================================================


class TestCBRotationForceRedeem:
    """Test forced redemption integration in cb_rotation_base."""

    def test_config_has_force_redeem_fields(self):
        from aipha.strategy.cb_rotation_base import CBRotationConfig

        cfg = CBRotationConfig()
        assert hasattr(cfg, "force_redeem_threshold")
        assert hasattr(cfg, "force_redeem_days")
        assert hasattr(cfg, "exclude_called")
        assert cfg.force_redeem_threshold == 130.0
        assert cfg.force_redeem_days == 8
        assert cfg.exclude_called is True

    def test_check_force_redeem_immediate_exit_on_called(self):
        """When is_called=True, strategy should immediately exit."""
        from aipha.strategy.cb_rotation_base import CBRotationConfig, CBRotationStrategy
        from aipha.strategy.base import StrategyContext

        # Create a concrete test strategy
        class TestStrategy(CBRotationStrategy):
            @property
            def config(self):
                return CBRotationConfig(force_redeem_days=3)

            @property
            def name(self):
                return "test_strategy"

            def _score_candidates(self, candidates, context):
                return []

        strategy = TestStrategy()
        strategy.on_start()
        strategy._entry_prices["CB001"] = 100.0

        bars = {
            "CB001": Bar(
                symbol="CB001",
                start_ts=datetime(2024, 1, 1),
                end_ts=datetime(2024, 1, 1),
                open=110, high=115, low=108, close=112,
                volume=10000,
                metadata={"is_called": True, "force_redeem_status": "已公告强赎"},
            ),
        }
        context = StrategyContext(
            positions={"CB001": 100},
            cash=100_000,
            nav=110_000,
            current_time=datetime(2024, 1, 1),
        )

        intents = strategy._check_force_redeem(bars, context)
        assert len(intents) == 1
        assert intents[0].side == Side.SELL
        assert intents[0].qty == 100

    def test_check_force_redeem_consecutive_days(self):
        """After N consecutive days of high cb_value, exit."""
        from aipha.strategy.cb_rotation_base import CBRotationConfig, CBRotationStrategy
        from aipha.strategy.base import StrategyContext

        class TestStrategy(CBRotationStrategy):
            @property
            def config(self):
                return CBRotationConfig(force_redeem_threshold=130, force_redeem_days=3)

            @property
            def name(self):
                return "test"

            def _score_candidates(self, candidates, context):
                return []

        strategy = TestStrategy()
        strategy.on_start()
        strategy._entry_prices["CB001"] = 100.0

        for day in range(1, 4):
            bars = {
                "CB001": Bar(
                    symbol="CB001",
                    start_ts=datetime(2024, 1, day),
                    end_ts=datetime(2024, 1, day),
                    open=135, high=136, low=134, close=135,
                    volume=5000,
                    metadata={"cb_value": 132.0, "is_called": False},
                ),
            }
            context = StrategyContext(
                positions={"CB001": 100},
                cash=50000, nav=63500,
                current_time=datetime(2024, 1, day),
            )
            intents = strategy._check_force_redeem(bars, context)

            if day < 3:
                assert len(intents) == 0
            else:
                # Day 3: counter reaches threshold
                assert len(intents) == 1
                assert intents[0].side == Side.SELL

    def test_filter_excludes_called(self):
        """When exclude_called=True, called CBs should be filtered out."""
        from aipha.strategy.cb_rotation_base import CBRotationConfig, CBRotationStrategy
        from aipha.strategy.base import StrategyContext

        class TestStrategy(CBRotationStrategy):
            @property
            def config(self):
                return CBRotationConfig(
                    exclude_called=True, max_price=200,
                    min_turnover=0, min_issue_size=0,
                )

            @property
            def name(self):
                return "test"

            def _score_candidates(self, candidates, context):
                return [c.symbol for c in candidates]

        strategy = TestStrategy()
        strategy.on_start()

        bars = {
            "CB001": Bar(
                symbol="CB001",
                start_ts=datetime(2024, 1, 1),
                end_ts=datetime(2024, 1, 1),
                open=100, high=105, low=98, close=102,
                volume=10000,
                metadata={"is_called": True, "amount": 100, "issue_size": 50000},
            ),
            "CB002": Bar(
                symbol="CB002",
                start_ts=datetime(2024, 1, 1),
                end_ts=datetime(2024, 1, 1),
                open=100, high=105, low=98, close=102,
                volume=10000,
                metadata={"is_called": False, "amount": 100, "issue_size": 50000},
            ),
        }
        context = StrategyContext(
            positions={}, cash=100_000, nav=100_000,
            current_time=datetime(2024, 1, 1),
        )

        candidates = strategy._filter_candidates(bars, context)
        symbols = [c.symbol for c in candidates]
        assert "CB001" not in symbols
        assert "CB002" in symbols

    def test_filter_uses_issue_size_not_remain_size(self):
        """Regression: size filter must use issue_size (no look-ahead)."""
        from aipha.strategy.cb_rotation_base import CBRotationConfig, CBRotationStrategy
        from aipha.strategy.base import StrategyContext

        class TestStrategy(CBRotationStrategy):
            @property
            def config(self):
                return CBRotationConfig(
                    max_price=200,
                    min_turnover=0,
                    min_issue_size=10_000,
                    max_issue_size=20_000,
                )

            @property
            def name(self):
                return "test"

            def _score_candidates(self, candidates, context):
                return [c.symbol for c in candidates]

        strategy = TestStrategy()
        strategy.on_start()

        bars = {
            # issue_size 在区间内，应通过；remain_size 再小也不应影响
            "CB_OK": Bar(
                symbol="CB_OK",
                start_ts=datetime(2024, 1, 1),
                end_ts=datetime(2024, 1, 1),
                open=100,
                high=105,
                low=98,
                close=102,
                volume=10000,
                metadata={
                    "amount": 100,
                    "issue_size": 15_000,
                    "remain_size": 0.1,
                },
            ),
            # issue_size 缺失但 remain_size 很合适：应被过滤（避免使用快照字段前视）
            "CB_NO_ISSUE": Bar(
                symbol="CB_NO_ISSUE",
                start_ts=datetime(2024, 1, 1),
                end_ts=datetime(2024, 1, 1),
                open=100,
                high=105,
                low=98,
                close=102,
                volume=10000,
                metadata={
                    "amount": 100,
                    "remain_size": 15_000,
                },
            ),
            # issue_size 低于下限但 remain_size 在区间内：也必须过滤
            "CB_TOO_SMALL": Bar(
                symbol="CB_TOO_SMALL",
                start_ts=datetime(2024, 1, 1),
                end_ts=datetime(2024, 1, 1),
                open=100,
                high=105,
                low=98,
                close=102,
                volume=10000,
                metadata={
                    "amount": 100,
                    "issue_size": 5_000,
                    "remain_size": 15_000,
                },
            ),
        }
        context = StrategyContext(
            positions={},
            cash=100_000,
            nav=100_000,
            current_time=datetime(2024, 1, 1),
        )

        candidates = strategy._filter_candidates(bars, context)
        symbols = {c.symbol for c in candidates}
        assert "CB_OK" in symbols
        assert "CB_NO_ISSUE" not in symbols
        assert "CB_TOO_SMALL" not in symbols


# ============================================================================
# CB Monitoring Alerts
# ============================================================================


class TestCBMonitoringAlerts:
    """Test CB-specific monitoring alert rules."""

    def test_force_redeem_exposure_alert_triggers(self):
        from aipha.monitor.alerts import ForceRedeemExposureAlert, PortfolioSnapshot

        alert_rule = ForceRedeemExposureAlert(
            cb_value_threshold=125.0,
            max_exposure_pct=0.20,
        )

        snapshot = PortfolioSnapshot(
            timestamp=datetime.now(),
            total_value=100_000,
            cash=20_000,
            positions={"CB001": 50_000, "CB002": 30_000},
            metadata={"cb_values": {"CB001": 130.0, "CB002": 128.0}},
        )

        alert = alert_rule.check(snapshot)
        assert alert is not None
        assert "强赎" in alert.title

    def test_force_redeem_exposure_alert_passes(self):
        from aipha.monitor.alerts import ForceRedeemExposureAlert, PortfolioSnapshot

        alert_rule = ForceRedeemExposureAlert(
            cb_value_threshold=125.0,
            max_exposure_pct=0.50,
        )

        snapshot = PortfolioSnapshot(
            timestamp=datetime.now(),
            total_value=100_000,
            cash=70_000,
            positions={"CB001": 10_000, "CB002": 20_000},
            metadata={"cb_values": {"CB001": 130.0, "CB002": 100.0}},
        )

        alert = alert_rule.check(snapshot)
        assert alert is None

    def test_liquidity_alert_triggers(self):
        from aipha.monitor.alerts import CBLiquidityAlert, PortfolioSnapshot

        alert_rule = CBLiquidityAlert(min_amount=100.0)

        snapshot = PortfolioSnapshot(
            timestamp=datetime.now(),
            total_value=100_000,
            cash=50_000,
            positions={"CB001": 30_000, "CB002": 20_000},
            metadata={"daily_amounts": {"CB001": 50.0, "CB002": 200.0}},
        )

        alert = alert_rule.check(snapshot)
        assert alert is not None
        assert "流动性" in alert.title

    def test_premium_distribution_alert_high(self):
        from aipha.monitor.alerts import PremiumDistributionAlert, PortfolioSnapshot

        alert_rule = PremiumDistributionAlert(
            max_avg_premium=30.0,
            min_avg_premium=-5.0,
        )

        snapshot = PortfolioSnapshot(
            timestamp=datetime.now(),
            total_value=100_000,
            cash=20_000,
            positions={"CB001": 40_000, "CB002": 40_000},
            metadata={"cb_premiums": {"CB001": 45.0, "CB002": 50.0}},
        )

        alert = alert_rule.check(snapshot)
        assert alert is not None
        assert "溢价率" in alert.title


# ============================================================================
# OrderType extensions
# ============================================================================


class TestOrderTypeExtensions:
    """Test new OrderType enum values."""

    def test_fok_exists(self):
        assert OrderType.FOK.value == "FOK"

    def test_ioc_exists(self):
        assert OrderType.IOC.value == "IOC"

    def test_best_five_exists(self):
        assert OrderType.BEST_FIVE.value == "BEST_FIVE"

    def test_best_five_then_limit_exists(self):
        assert OrderType.BEST_FIVE_THEN_LIMIT.value == "BEST_FIVE_THEN_LIMIT"

    def test_market_and_limit_still_work(self):
        assert OrderType.MARKET.value == "MARKET"
        assert OrderType.LIMIT.value == "LIMIT"


# ============================================================================
# PaperTradingGateway.from_cb_mode()
# ============================================================================


class TestPaperGatewayCBMode:
    """Test PaperTradingGateway.from_cb_mode() classmethod."""

    def test_from_cb_mode_creates_instance(self):
        from aipha.execution.paper_gateway import PaperTradingGateway

        gw = PaperTradingGateway.from_cb_mode()
        assert gw._lot_size == 10
        assert gw._t_plus_n == 0
        assert gw._stamp_tax_rate == Decimal("0")

    def test_from_cb_mode_custom_capital(self):
        from aipha.execution.paper_gateway import PaperTradingGateway

        gw = PaperTradingGateway.from_cb_mode(initial_capital=200_000)
        assert gw._initial_capital == Decimal("200000")

    def test_from_cb_mode_no_transfer_fee(self):
        from aipha.execution.paper_gateway import PaperTradingGateway

        gw = PaperTradingGateway.from_cb_mode()
        # Fee model should have zero transfer fee
        fee_model = gw._fee_model
        assert fee_model.transfer_fee.rate == 0.0


# ============================================================================
# Config runner volume_constraint
# ============================================================================


class TestConfigRunnerVolumeConstraint:
    """Test config_runner parses volume_constraint correctly."""

    def test_parse_config_with_volume_constraint(self):
        from aipha.backtest.config_runner import parse_config

        raw = {
            "name": "test",
            "data": {
                "source": "synthetic",
                "start_date": "2023-01-01",
                "end_date": "2023-12-31",
            },
            "strategy": {"name": "buy_and_hold"},
            "fill_model": {
                "name": "next_bar_open",
                "volume_constraint": {
                    "max_participation_rate": 0.10,
                    "lot_size": 10,
                },
            },
        }

        config = parse_config(raw)
        assert config.fill_model.volume_constraint is not None
        assert config.fill_model.volume_constraint["max_participation_rate"] == 0.10
        assert config.fill_model.volume_constraint["lot_size"] == 10

    def test_parse_config_without_volume_constraint(self):
        from aipha.backtest.config_runner import parse_config

        raw = {
            "name": "test",
            "data": {
                "source": "synthetic",
                "start_date": "2023-01-01",
                "end_date": "2023-12-31",
            },
            "strategy": {"name": "buy_and_hold"},
            "fill_model": {"name": "next_bar_open"},
        }

        config = parse_config(raw)
        assert config.fill_model.volume_constraint is None

    def test_config_to_dict_includes_volume_constraint(self):
        from aipha.backtest.config_runner import config_to_dict, parse_config

        raw = {
            "name": "test",
            "data": {
                "source": "synthetic",
                "start_date": "2023-01-01",
                "end_date": "2023-12-31",
            },
            "strategy": {"name": "buy_and_hold"},
            "fill_model": {
                "name": "next_bar_open",
                "volume_constraint": {"max_participation_rate": 0.10, "lot_size": 10},
            },
        }

        config = parse_config(raw)
        result = config_to_dict(config)
        assert "volume_constraint" in result["fill_model"]

    def test_config_to_dict_omits_null_volume_constraint(self):
        from aipha.backtest.config_runner import config_to_dict, parse_config

        raw = {
            "name": "test",
            "data": {
                "source": "synthetic",
                "start_date": "2023-01-01",
                "end_date": "2023-12-31",
            },
            "strategy": {"name": "buy_and_hold"},
        }

        config = parse_config(raw)
        result = config_to_dict(config)
        assert "volume_constraint" not in result["fill_model"]


# ============================================================================
# CB data adapter is_called metadata
# ============================================================================


class TestCBDataAdapterIsCalled:
    """Test is_called field in CB metadata."""

    def test_build_metadata_with_called_status(self):
        import pandas as pd

        from aipha.data.cb_data_adapter import CBDataAdapter

        adapter = CBDataAdapter.__new__(CBDataAdapter)
        adapter._force_redeem_index = {}

        row = pd.Series({
            "cb_value": 135.0,
            "cb_over_rate": 5.0,
            "amount": 100.0,
            "issue_size": 50000.0,
            "force_redeem_status": "已公告强赎",
        })

        meta = adapter._build_metadata(row)
        assert meta["is_called"] is True
        assert meta["force_redeem_status"] == "已公告强赎"

    def test_build_metadata_not_called(self):
        import pandas as pd

        from aipha.data.cb_data_adapter import CBDataAdapter

        adapter = CBDataAdapter.__new__(CBDataAdapter)
        adapter._force_redeem_index = {}

        row = pd.Series({
            "cb_value": 90.0,
            "cb_over_rate": 5.0,
            "amount": 100.0,
        })

        meta = adapter._build_metadata(row)
        assert meta["is_called"] is False

    def test_build_metadata_maybe_called_heuristic(self):
        """When no force_redeem_status but cb_value >= 130, is_called is None."""
        import pandas as pd

        from aipha.data.cb_data_adapter import CBDataAdapter

        adapter = CBDataAdapter.__new__(CBDataAdapter)
        adapter._force_redeem_index = {}

        row = pd.Series({
            "cb_value": 132.0,
            "amount": 100.0,
        })

        meta = adapter._build_metadata(row)
        assert meta["is_called"] is None  # Unknown, possibly triggered


# ============================================================================
# RiskViolationType enum extensions
# ============================================================================


class TestRiskViolationTypeExtensions:
    """Test new violation types exist."""

    def test_force_redeem_exposure_type(self):
        from aipha.risk.constraints import RiskViolationType

        assert RiskViolationType.FORCE_REDEEM_EXPOSURE.value == "FORCE_REDEEM_EXPOSURE"

    def test_liquidity_limit_type(self):
        from aipha.risk.constraints import RiskViolationType

        assert RiskViolationType.LIQUIDITY_LIMIT.value == "LIQUIDITY_LIMIT"
