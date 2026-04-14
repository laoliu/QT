"""回测引擎 × DegradationManager 集成测试

验证 DegradationManager 与 BacktestEngine 的协作：
- 信号上报：CircuitBreaker / MarketRegime → DegradationManager
- 级别检查：HALTED → 跳过交易
- 仓位缩减：WARNING → position_scale_warning
- 自动恢复：信号清除后恢复到 NORMAL
- 与 config_runner 的配置联动
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from aipha.backtest.engine import BacktestConfig, BacktestEngine
from aipha.domain.models import Bar, Side
from aipha.execution.fees import (
    CommissionModel,
    FeeModelCNEquity,
    StampTaxModel,
    TransferFeeModel,
)
from aipha.execution.fill_models import NextBarOpenFillModel
from aipha.execution.slippage import FixedBpsSlippageModel
from aipha.market.rules_cn_equity import MarketRulesCNEquity
from aipha.risk.degradation import (
    DegradationConfig,
    DegradationLevel,
    DegradationManager,
    DegradationSignal,
)
from aipha.strategy.base import IntentType, OrderIntent, Strategy, StrategyContext


# ============================================================================
# 辅助
# ============================================================================


def _bar(symbol: str, td: date, price: float = 10.0, volume: float = 1e6) -> Bar:
    ts = datetime.combine(td, datetime.min.time().replace(hour=15))
    return Bar(
        symbol=symbol,
        start_ts=ts.replace(hour=9, minute=30),
        end_ts=ts,
        open=price * 0.99,
        high=price * 1.01,
        low=price * 0.98,
        close=price,
        volume=volume,
    )


def _trade_dates(start: date, n: int) -> list[date]:
    """生成 n 个交易日（跳过周末）"""
    dates: list[date] = []
    d = start
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)
    return dates


def _bars_series(
    symbols: list[str],
    start: date,
    n: int = 5,
    base: float = 10.0,
) -> dict[date, dict[str, Bar]]:
    """生成多日多标的 bars"""
    out: dict[date, dict[str, Bar]] = {}
    for i, td in enumerate(_trade_dates(start, n)):
        out[td] = {s: _bar(s, td, base + i * 0.1) for s in symbols}
    return out


def _default_components() -> dict:
    cfg = BacktestConfig(initial_capital=100_000.0)
    fee = FeeModelCNEquity(
        commission=CommissionModel(rate=cfg.commission_rate, min_fee=cfg.commission_min),
        stamp_tax=StampTaxModel(sell_rate=cfg.stamp_tax_rate),
        transfer_fee=TransferFeeModel(rate=cfg.transfer_fee_rate),
    )
    return {
        "config": cfg,
        "fill_model": NextBarOpenFillModel(),
        "slippage": FixedBpsSlippageModel(bps=0.0),
        "fee_model": fee,
        "rules": MarketRulesCNEquity(),
    }


@dataclass
class AlwaysBuyStrategy(Strategy):
    """每个 bar 都尝试买入"""

    name: str = "AlwaysBuy"
    target: str = "A"
    qty: int = 100

    def on_bar(self, bars: dict[str, Bar], ctx: StrategyContext) -> list[OrderIntent]:
        if self.target in bars and ctx.positions.get(self.target, 0) == 0:
            return [
                OrderIntent(
                    symbol=self.target,
                    side=Side.BUY,
                    qty=self.qty,
                    intent_type=IntentType.ORDER,
                )
            ]
        return []


@dataclass
class CountCallsStrategy(Strategy):
    """记录 on_bar 被调用的次数"""

    name: str = "CountCalls"
    call_count: int = field(default=0, init=False)

    def on_bar(self, bars: dict[str, Bar], ctx: StrategyContext) -> list[OrderIntent]:
        self.call_count += 1
        return []


@dataclass
class RecordMetadataStrategy(Strategy):
    """记录每次收到的 metadata"""

    name: str = "RecordMeta"
    metadata_history: list[dict[str, Any]] = field(default_factory=list, init=False)

    def on_bar(self, bars: dict[str, Bar], ctx: StrategyContext) -> list[OrderIntent]:
        self.metadata_history.append(dict(ctx.metadata))
        return []


# ============================================================================
# 测试类
# ============================================================================


class TestDegradationEngineBasic:
    """DegradationManager 基本集成"""

    def test_engine_accepts_degradation_manager(self):
        """引擎构造函数接受 degradation_manager 参数"""
        dm = DegradationManager()
        strategy = CountCallsStrategy()
        engine = BacktestEngine(
            strategy=strategy,
            degradation_manager=dm,
            **_default_components(),
        )
        assert engine.degradation_manager is dm

    def test_engine_runs_without_degradation_manager(self):
        """不传 degradation_manager 也能正常运行"""
        strategy = CountCallsStrategy()
        engine = BacktestEngine(strategy=strategy, **_default_components())
        bars = _bars_series(["A"], date(2025, 1, 6), n=3)
        result = engine.run(bars)
        assert result.final_nav > 0
        assert strategy.call_count == 3

    def test_normal_level_no_impact(self):
        """NORMAL 级别不影响策略执行"""
        dm = DegradationManager()
        strategy = CountCallsStrategy()
        engine = BacktestEngine(
            strategy=strategy,
            degradation_manager=dm,
            **_default_components(),
        )
        bars = _bars_series(["A"], date(2025, 1, 6), n=5)
        result = engine.run(bars)
        assert strategy.call_count == 5
        assert dm.current_level == DegradationLevel.NORMAL


class TestDegradationHalted:
    """HALTED 级别跳过交易"""

    def test_halted_skips_strategy(self):
        """HALTED 级别应跳过策略调用"""
        dm = DegradationManager()
        # 预设 HALTED 信号
        dm.report_signal(DegradationSignal(
            source="external",
            severity=DegradationLevel.HALTED,
            message="test halt",
        ))
        assert dm.current_level == DegradationLevel.HALTED

        strategy = CountCallsStrategy()
        engine = BacktestEngine(
            strategy=strategy,
            degradation_manager=dm,
            **_default_components(),
        )
        bars = _bars_series(["A"], date(2025, 1, 6), n=3)
        result = engine.run(bars)

        # 策略不应被调用
        assert strategy.call_count == 0

    def test_halted_preserves_capital(self):
        """HALTED 下不产生任何交易"""
        dm = DegradationManager()
        dm.report_signal(DegradationSignal(
            source="external",
            severity=DegradationLevel.HALTED,
            message="halt",
        ))

        strategy = AlwaysBuyStrategy()
        engine = BacktestEngine(
            strategy=strategy,
            degradation_manager=dm,
            **_default_components(),
        )
        bars = _bars_series(["A"], date(2025, 1, 6), n=5)
        result = engine.run(bars)

        assert result.total_trades == 0
        assert result.final_nav == pytest.approx(100_000.0, rel=1e-6)


class TestDegradationWarningScale:
    """WARNING 级别仓位缩减"""

    def test_warning_applies_position_scale(self):
        """WARNING 级别应通过 position_scale 缩减买入数量"""
        dm = DegradationManager(config=DegradationConfig(
            position_scale_warning=0.5,
            recovery_cooldown_seconds=0,
        ))
        dm.report_signal(DegradationSignal(
            source="test",
            severity=DegradationLevel.WARNING,
            message="warning test",
        ))
        assert dm.position_scale == pytest.approx(0.5)

        strategy = RecordMetadataStrategy()
        engine = BacktestEngine(
            strategy=strategy,
            degradation_manager=dm,
            **_default_components(),
        )
        bars = _bars_series(["A"], date(2025, 1, 6), n=3)
        engine.run(bars)

        # 策略应收到 degradation_position_scale 元数据
        assert len(strategy.metadata_history) == 3
        for meta in strategy.metadata_history:
            assert "degradation_level" in meta
            assert meta["degradation_level"] == "WARNING"
            assert meta["degradation_position_scale"] == pytest.approx(0.5)


class TestDegradationDegraded:
    """DEGRADED 级别"""

    def test_degraded_zero_position_scale(self):
        """DEGRADED 级别默认 position_scale=0.0 → 不允许新开仓"""
        dm = DegradationManager(config=DegradationConfig(
            position_scale_degraded=0.0,
            recovery_cooldown_seconds=0,
        ))
        dm.report_signal(DegradationSignal(
            source="test",
            severity=DegradationLevel.DEGRADED,
            message="degraded test",
        ))

        strategy = RecordMetadataStrategy()
        engine = BacktestEngine(
            strategy=strategy,
            degradation_manager=dm,
            **_default_components(),
        )
        bars = _bars_series(["A"], date(2025, 1, 6), n=2)
        engine.run(bars)

        for meta in strategy.metadata_history:
            assert meta["degradation_level"] == "DEGRADED"
            assert meta["degradation_position_scale"] == pytest.approx(0.0)


class TestDegradationAutoRecover:
    """自动恢复"""

    def test_signal_clear_recovers_to_normal(self):
        """清除信号后应恢复到 NORMAL"""
        dm = DegradationManager(config=DegradationConfig(
            auto_recover=True,
            recovery_cooldown_seconds=0,  # 立即恢复
        ))
        dm.report_signal(DegradationSignal(
            source="test",
            severity=DegradationLevel.WARNING,
            message="temp warning",
        ))
        assert dm.current_level == DegradationLevel.WARNING

        dm.clear_signal("test")
        assert dm.current_level == DegradationLevel.NORMAL


class TestDegradationMetadata:
    """策略上下文中的降级元数据"""

    def test_metadata_includes_degradation_fields(self):
        """策略 metadata 应包含 degradation_level 和 degradation_position_scale"""
        dm = DegradationManager()
        strategy = RecordMetadataStrategy()
        engine = BacktestEngine(
            strategy=strategy,
            degradation_manager=dm,
            **_default_components(),
        )
        bars = _bars_series(["A"], date(2025, 1, 6), n=2)
        engine.run(bars)

        assert len(strategy.metadata_history) == 2
        for meta in strategy.metadata_history:
            assert meta["degradation_level"] == "NORMAL"
            assert meta["degradation_position_scale"] == pytest.approx(1.0)


class TestDegradationConfigRunner:
    """config_runner 降级配置解析"""

    def test_risk_config_degradation_defaults(self):
        """RiskConfig 降级字段默认值"""
        from aipha.backtest.config_runner import RiskConfig

        rc = RiskConfig()
        assert rc.degradation_enabled is False
        assert rc.degradation_auto_recover is True
        assert rc.degradation_recovery_cooldown == pytest.approx(300.0)
        assert rc.degradation_position_scale_warning == pytest.approx(0.5)
        assert rc.degradation_position_scale_degraded == pytest.approx(0.0)

    def test_risk_config_custom_degradation(self):
        """RiskConfig 自定义降级参数"""
        from aipha.backtest.config_runner import RiskConfig

        rc = RiskConfig(
            degradation_enabled=True,
            degradation_auto_recover=False,
            degradation_recovery_cooldown=600.0,
            degradation_position_scale_warning=0.7,
            degradation_position_scale_degraded=0.2,
        )
        assert rc.degradation_enabled is True
        assert rc.degradation_auto_recover is False
        assert rc.degradation_recovery_cooldown == pytest.approx(600.0)
        assert rc.degradation_position_scale_warning == pytest.approx(0.7)
        assert rc.degradation_position_scale_degraded == pytest.approx(0.2)

    def test_build_degradation_manager_disabled(self):
        """degradation_enabled=False 应返回 None"""
        from aipha.backtest.config_runner import (
            BacktestRunConfig,
            CostConfig,
            DataConfig,
            RiskConfig,
            StrategyConfig,
            UniverseConfig,
            _build_degradation_manager,
        )

        config = BacktestRunConfig(
            name="test",
            data=DataConfig(source="synthetic", start_date="2025-01-01", end_date="2025-03-01"),
            universe=UniverseConfig(),
            strategy=StrategyConfig(name="dual_ma"),
            cost=CostConfig(),
            risk=RiskConfig(degradation_enabled=False),
        )
        result = _build_degradation_manager(config)
        assert result is None

    def test_build_degradation_manager_enabled(self):
        """degradation_enabled=True 应返回 DegradationManager"""
        from aipha.backtest.config_runner import (
            BacktestRunConfig,
            CostConfig,
            DataConfig,
            RiskConfig,
            StrategyConfig,
            UniverseConfig,
            _build_degradation_manager,
        )

        config = BacktestRunConfig(
            name="test",
            data=DataConfig(source="synthetic", start_date="2025-01-01", end_date="2025-03-01"),
            universe=UniverseConfig(),
            strategy=StrategyConfig(name="dual_ma"),
            cost=CostConfig(),
            risk=RiskConfig(
                degradation_enabled=True,
                degradation_auto_recover=False,
                degradation_recovery_cooldown=120.0,
                degradation_position_scale_warning=0.6,
                degradation_position_scale_degraded=0.1,
            ),
        )
        dm = _build_degradation_manager(config)
        assert dm is not None
        assert isinstance(dm, DegradationManager)
        # 验证配置透传
        assert dm._config.auto_recover is False
        assert dm._config.recovery_cooldown_seconds == pytest.approx(120.0)
        assert dm._config.position_scale_warning == pytest.approx(0.6)
        assert dm._config.position_scale_degraded == pytest.approx(0.1)

    def test_build_degradation_manager_risk_disabled(self):
        """risk.enabled=False 应返回 None"""
        from aipha.backtest.config_runner import (
            BacktestRunConfig,
            CostConfig,
            DataConfig,
            RiskConfig,
            StrategyConfig,
            UniverseConfig,
            _build_degradation_manager,
        )

        config = BacktestRunConfig(
            name="test",
            data=DataConfig(source="synthetic", start_date="2025-01-01", end_date="2025-03-01"),
            universe=UniverseConfig(),
            strategy=StrategyConfig(name="dual_ma"),
            cost=CostConfig(),
            risk=RiskConfig(enabled=False, degradation_enabled=True),
        )
        result = _build_degradation_manager(config)
        assert result is None


class TestDegradationYamlParsing:
    """YAML 配置解析中的 degradation 字段"""

    def test_load_config_with_degradation(self, tmp_path):
        """YAML 中包含 degradation 配置时正确解析"""
        from aipha.backtest.config_runner import load_config

        yml = tmp_path / "test.yml"
        yml.write_text("""
name: degradation_test
data:
  source: synthetic
  start_date: "2025-01-01"
  end_date: "2025-06-01"
  symbols: ["000001.SZ"]
strategy:
  name: dual_ma
  params:
    short_period: 5
    long_period: 20
risk:
  degradation_enabled: true
  degradation_auto_recover: false
  degradation_recovery_cooldown: 120.0
  degradation_position_scale_warning: 0.6
  degradation_position_scale_degraded: 0.1
""", encoding="utf-8")

        config = load_config(str(yml))
        assert config.risk.degradation_enabled is True
        assert config.risk.degradation_auto_recover is False
        assert config.risk.degradation_recovery_cooldown == pytest.approx(120.0)
        assert config.risk.degradation_position_scale_warning == pytest.approx(0.6)
        assert config.risk.degradation_position_scale_degraded == pytest.approx(0.1)

    def test_load_config_without_degradation(self, tmp_path):
        """YAML 中不含 degradation 配置时使用默认值"""
        from aipha.backtest.config_runner import load_config

        yml = tmp_path / "test.yml"
        yml.write_text("""
name: no_degradation_test
data:
  source: synthetic
  start_date: "2025-01-01"
  end_date: "2025-06-01"
  symbols: ["000001.SZ"]
strategy:
  name: dual_ma
  params:
    short_period: 5
    long_period: 20
""", encoding="utf-8")

        config = load_config(str(yml))
        assert config.risk.degradation_enabled is False
        assert config.risk.degradation_auto_recover is True
