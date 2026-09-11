"""Tests for risk management integration in config_runner.

Validates that:
- RiskConfig parses correctly from YAML risk_constraints
- _build_risk_manager creates proper CompositeRiskManager with constraints
- _build_stop_loss_manager creates StopLossManager with rules
- _build_audit_logger creates AuditLogger with correct path
- _build_market_regime_detector creates detector when enabled
- run_from_config injects all risk components into BacktestEngine
- _normalize_config maps risk_constraints fields correctly
"""
from __future__ import annotations

import logging
import tempfile
from datetime import date
from pathlib import Path

import pytest
import yaml

from aipha.backtest.config_runner import (
    BacktestRunConfig,
    CostConfig,
    DataConfig,
    FillModelConfig,
    RiskConfig,
    StrategyConfig,
    UniverseConfig,
    _build_audit_logger,
    _build_market_regime_detector,
    _build_risk_manager,
    _build_stop_loss_manager,
    _normalize_config,
    config_to_dict,
    parse_config,
)
from aipha.risk.audit_log import AuditLogger
from aipha.risk.constraints import (
    CompositeRiskManager,
    DrawdownLimit,
    MarketRegimeDetector,
    PositionLimit,
    SectorLimit,
    TurnoverLimit,
)
from aipha.risk.stop_loss import (
    CircuitBreaker,
    FixedPercentStopLoss,
    StopLossManager,
    TrailingStopLoss,
)


# ============================================================================
# Helpers
# ============================================================================


def _make_config(**risk_overrides) -> BacktestRunConfig:
    """创建带风控配置的 BacktestRunConfig。"""
    risk = RiskConfig(**risk_overrides)
    return BacktestRunConfig(
        name="risk_test",
        data=DataConfig(
            source="synthetic",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        ),
        universe=UniverseConfig(),
        strategy=StrategyConfig(name="buy_and_hold"),
        cost=CostConfig(),
        risk=risk,
    )


# ============================================================================
# RiskConfig Defaults
# ============================================================================


class TestRiskConfigDefaults:
    """Test RiskConfig default values."""

    def test_defaults(self):
        """Should have sensible defaults."""
        rc = RiskConfig()
        assert rc.enabled is True
        assert rc.max_position_weight == 0.10
        # §4.3.2: 默认 1.0 = 渐进减仓**关闭**（不是 0.20）。
        # 0.20 曾经是默认值，但对集中持仓策略会形成反馈循环：
        # reduce_position_at=0.10 在正常波动里持续缩仓，CB new_small 案例
        # AR 从 40% 掉到 2.6%（docs/known_limitations.md §13）。
        # 想要回撤降杠杆必须在 YAML 里显式配 —— 见
        # TestDrawdownSafetyAutoInjection。
        assert rc.max_drawdown == 1.0
        assert rc.max_sector_weight is None
        assert rc.max_daily_turnover is None
        assert rc.daily_loss_limit is None
        assert rc.stop_loss_pct is None
        assert rc.trailing_stop_pct is None
        assert rc.circuit_breaker_enabled is False
        assert rc.circuit_breaker_drawdown == 0.10
        assert rc.enable_audit_log is True
        assert rc.market_regime_enabled is False
        assert rc.benchmark_symbol == ""

    def test_custom_values(self):
        """Should accept custom values."""
        rc = RiskConfig(
            max_position_weight=0.05,
            max_drawdown=0.15,
            max_sector_weight=0.25,
            stop_loss_pct=0.10,
        )
        assert rc.max_position_weight == 0.05
        assert rc.max_drawdown == 0.15
        assert rc.max_sector_weight == 0.25
        assert rc.stop_loss_pct == 0.10


class TestDrawdownSafetyAutoInjection:
    """§4.3.2: 一旦启用渐进减仓，配套防护参数会被自动补上。

    这是 ``max_drawdown`` 默认 1.0 的另一半：默认值只是「不启用」，
    而**启用时**必须带着三个防护参数一起，否则集中持仓策略会进入
    死亡螺旋（reduce_position_at 在正常波动里持续缩仓 →
    净值下不来 → 继续缩仓）。三个参数的作用：

    * ``drawdown_allow_rotation`` —— 有持仓时允许跳过区间缩减，让策略还能换股；
    * ``drawdown_scale_factor_floor`` —— 缩放比例地板，不至于缩到 0；
    * ``drawdown_peak_reset_days`` —— 空仓 N 天后重置 peak_nav，
      否则旧高点永远压着仓位。

    注入发生在 ``RiskConfig.validate_drawdown_safety()``，由
    ``parse_config()``（`config_parser.py:500`）以及实盘/组合路径调用。
    """

    def test_no_injection_when_disabled(self):
        """max_drawdown=1.0（默认）时不注入，字段保持原样"""
        rc = RiskConfig()
        rc.validate_drawdown_safety()
        assert rc.drawdown_allow_rotation is False
        assert rc.drawdown_scale_factor_floor == 0.0
        assert rc.drawdown_peak_reset_days == 0

    def test_injects_guards_when_enabled(self):
        """max_drawdown<1.0 且未配防护参数 → 三个参数被补上"""
        rc = RiskConfig(max_drawdown=0.20)
        rc.validate_drawdown_safety()
        assert rc.drawdown_allow_rotation is True
        assert rc.drawdown_scale_factor_floor == 0.5
        assert rc.drawdown_peak_reset_days == 60

    def test_does_not_weaken_explicit_guards(self):
        """比安全下限更严的显式配置不被覆盖"""
        rc = RiskConfig(
            max_drawdown=0.20,
            drawdown_scale_factor_floor=0.8,
            drawdown_peak_reset_days=90,
        )
        rc.validate_drawdown_safety()
        assert rc.drawdown_scale_factor_floor == 0.8
        assert rc.drawdown_peak_reset_days == 90

    def test_injection_via_parse_config(self):
        """走 parse_config 时同样注入（不只是直接调 validate 才生效）"""
        raw = {
            "name": "test",
            "data": {"source": "synthetic", "start_date": "2024-01-01",
                     "end_date": "2024-12-31"},
            "risk": {"max_drawdown": 0.20},
        }
        config = parse_config(raw)
        assert config.risk.max_drawdown == 0.20
        assert config.risk.drawdown_allow_rotation is True
        assert config.risk.drawdown_scale_factor_floor == 0.5
        assert config.risk.drawdown_peak_reset_days == 60


def _log_text(caplog) -> str:
    """把捕获到的日志拼成一整段文本。

    不能用 ``record.message % record.args``：告警文案里带 ``40%`` 这种
    字面百分号，二次格式化会 ValueError。``getMessage()`` 才是记录时
    实际生成的那一句。
    """
    return "\n".join(r.getMessage() for r in caplog.records)


class TestR163DeathSpiralWarning:
    """§4.3.2 规则 9 / R163：集中持仓 + 低 max_drawdown 必须告警。

    这条规则只在传了 ``top_k`` 时才判定。paper/live 路径原先全都没传
    （``build_risk_manager_from_dict`` 没有这个参数），所以规则虽然写着
    「Paper/Live 路径也必须执行」，实际从未在那条路上触发过。

    历史依据：CB new_small 案例里 top_k<=10 配 max_drawdown<0.40
    把 AR 从 40% 打到 2.6%（docs/known_limitations.md §13）。
    """

    def test_warns_when_concentrated_and_tight(self, caplog):
        rc = RiskConfig(max_drawdown=0.30)
        with caplog.at_level(logging.WARNING):
            rc.validate_drawdown_safety(top_k=8)
        assert "死亡螺旋高危" in _log_text(caplog)

    def test_silent_when_diversified(self, caplog):
        """持仓分散（top_k>10）时不告警 —— 告警太吵就没人看了"""
        rc = RiskConfig(max_drawdown=0.30)
        with caplog.at_level(logging.WARNING):
            rc.validate_drawdown_safety(top_k=50)
        assert "死亡螺旋高危" not in _log_text(caplog)
        # 但配套参数注入照常发生
        assert rc.drawdown_allow_rotation is True

    def test_silent_when_top_k_unknown(self, caplog):
        """推不出 top_k 时不判定：宁可不报，也不拿猜出来的数报警"""
        rc = RiskConfig(max_drawdown=0.30)
        with caplog.at_level(logging.WARNING):
            rc.validate_drawdown_safety(top_k=None)
        assert "死亡螺旋高危" not in _log_text(caplog)

    def test_build_risk_manager_from_dict_forwards_top_k(self, caplog):
        """paper/live 的统一入口必须把 top_k 透到校验里。

        回归防线：这个参数被去掉或忘记转发，R163 就会再次静默失效，
        而且不会有任何测试变红 —— 除了这一条。
        """
        from aipha.risk.risk_config_loader import build_risk_manager_from_dict

        with caplog.at_level(logging.WARNING):
            build_risk_manager_from_dict(
                {"enabled": True, "max_drawdown": 0.30}, top_k=8,
            )
        assert "死亡螺旋高危" in _log_text(caplog)


class TestInferPortfolioTopK:
    """组合层 top_k 推断 —— R163 判定的输入。"""

    def test_sums_component_top_k(self):
        """取和而非取小：3 个组件各 10 只 = 实际持有 30 只，并不集中"""
        from aipha.risk.risk_config_loader import infer_portfolio_top_k

        assert infer_portfolio_top_k(
            [{"top_k": 50}, {"top_k": 10}, {"top_k": 3}],
        ) == 63

    def test_reads_nested_params(self):
        """存量配置两种写法都有：组件顶层和 params: 子节"""
        from aipha.risk.risk_config_loader import infer_portfolio_top_k

        assert infer_portfolio_top_k(
            [{"params": {"top_k": 20}}, {"params": {"top_k": 5}}],
        ) == 25

    def test_returns_none_when_absent(self):
        from aipha.risk.risk_config_loader import infer_portfolio_top_k

        assert infer_portfolio_top_k([{"name": "x"}]) is None
        assert infer_portfolio_top_k([]) is None
        assert infer_portfolio_top_k(None) is None

    def test_ignores_unparsable_values(self):
        """脏值跳过，不让一个坏字段把整个推断带偏"""
        from aipha.risk.risk_config_loader import infer_portfolio_top_k

        assert infer_portfolio_top_k(
            [{"top_k": 10}, {"top_k": "auto"}, {"top_k": None}],
        ) == 10


# ============================================================================
# _normalize_config — risk_constraints mapping
# ============================================================================


class TestNormalizeRiskConstraints:
    """Test _normalize_config mapping of risk_constraints → risk."""

    def test_max_single_position_pct(self):
        """risk_constraints.max_single_position_pct → risk.max_position_weight"""
        raw = {
            "name": "test",
            "data": {"source": "synthetic", "start_date": "2024-01-01",
                     "end_date": "2024-12-31"},
            "risk_constraints": {"max_single_position_pct": 0.05},
        }
        out = _normalize_config(raw)
        assert "risk_constraints" not in out
        assert out["risk"]["max_position_weight"] == 0.05

    def test_max_drawdown_pct(self):
        """risk_constraints.max_drawdown_pct → risk.max_drawdown"""
        raw = {
            "name": "test",
            "data": {"source": "synthetic", "start_date": "2024-01-01",
                     "end_date": "2024-12-31"},
            "risk_constraints": {"max_drawdown_pct": 0.15},
        }
        out = _normalize_config(raw)
        assert out["risk"]["max_drawdown"] == 0.15

    def test_daily_loss_limit_enables_circuit_breaker(self):
        """daily_loss_limit_pct should also enable circuit breaker."""
        raw = {
            "name": "test",
            "data": {"source": "synthetic", "start_date": "2024-01-01",
                     "end_date": "2024-12-31"},
            "risk_constraints": {"daily_loss_limit_pct": 0.05},
        }
        out = _normalize_config(raw)
        assert out["risk"]["daily_loss_limit"] == 0.05
        assert out["risk"]["circuit_breaker_enabled"] is True

    def test_max_sector_pct(self):
        """risk_constraints.max_sector_pct → risk.max_sector_weight"""
        raw = {
            "name": "test",
            "data": {"source": "synthetic", "start_date": "2024-01-01",
                     "end_date": "2024-12-31"},
            "risk_constraints": {"max_sector_pct": 0.25},
        }
        out = _normalize_config(raw)
        assert out["risk"]["max_sector_weight"] == 0.25

    def test_all_fields_together(self):
        """All risk_constraints fields mapped correctly."""
        raw = {
            "name": "test",
            "data": {"source": "synthetic", "start_date": "2024-01-01",
                     "end_date": "2024-12-31"},
            "risk_constraints": {
                "max_single_position_pct": 0.10,
                "max_drawdown_pct": 0.20,
                "daily_loss_limit_pct": 0.05,
                "max_sector_pct": 0.30,
            },
        }
        out = _normalize_config(raw)
        risk = out["risk"]
        assert risk["max_position_weight"] == 0.10
        assert risk["max_drawdown"] == 0.20
        assert risk["daily_loss_limit"] == 0.05
        assert risk["max_sector_weight"] == 0.30

    def test_report_benchmark_to_risk(self):
        """report.benchmark → risk.benchmark_symbol"""
        raw = {
            "name": "test",
            "data": {"source": "synthetic", "start_date": "2024-01-01",
                     "end_date": "2024-12-31"},
            "report": {"benchmark": "000300.SH"},
        }
        out = _normalize_config(raw)
        assert out.get("risk", {}).get("benchmark_symbol") == "000300.SH"

    def test_strategy_stop_loss_to_risk(self):
        """strategy.params.stop_loss_pct → risk.stop_loss_pct"""
        raw = {
            "name": "test",
            "data": {"source": "synthetic", "start_date": "2024-01-01",
                     "end_date": "2024-12-31"},
            "strategy": {
                "name": "demo_momentum",
                "params": {"stop_loss_pct": 0.15},
            },
        }
        out = _normalize_config(raw)
        assert out.get("risk", {}).get("stop_loss_pct") == 0.15

    def test_no_risk_constraints_no_section(self):
        """Without risk_constraints, no risk section is created (unless from report/strategy)."""
        raw = {
            "name": "test",
            "data": {"source": "synthetic", "start_date": "2024-01-01",
                     "end_date": "2024-12-31"},
        }
        out = _normalize_config(raw)
        # risk section may or may not exist; should not error
        # parse_config will use defaults


# ============================================================================
# parse_config — risk section
# ============================================================================


class TestParseConfigRisk:
    """Test parse_config correctly parses risk field."""

    def test_parse_with_risk_constraints_yaml(self):
        """risk_constraints in YAML should result in populated RiskConfig."""
        raw = {
            "name": "test",
            "data": {"source": "synthetic", "start_date": "2024-01-01",
                     "end_date": "2024-12-31"},
            "risk_constraints": {
                "max_single_position_pct": 0.05,
                "max_drawdown_pct": 0.15,
            },
        }
        config = parse_config(raw)
        assert config.risk.max_position_weight == 0.05
        assert config.risk.max_drawdown == 0.15

    def test_parse_without_risk_uses_defaults(self):
        """Without risk section, defaults apply."""
        raw = {
            "name": "test",
            "data": {"source": "synthetic", "start_date": "2024-01-01",
                     "end_date": "2024-12-31"},
        }
        config = parse_config(raw)
        assert config.risk.enabled is True
        assert config.risk.max_position_weight == 0.10
        assert config.risk.max_drawdown == 1.0  # §4.3.2 渐进减仓默认关闭

    def test_parse_risk_section_direct(self):
        """Direct risk section in YAML."""
        raw = {
            "name": "test",
            "data": {"source": "synthetic", "start_date": "2024-01-01",
                     "end_date": "2024-12-31"},
            "risk": {
                "enabled": True,
                "max_position_weight": 0.08,
                "stop_loss_pct": 0.12,
                "circuit_breaker_enabled": True,
            },
        }
        config = parse_config(raw)
        assert config.risk.max_position_weight == 0.08
        assert config.risk.stop_loss_pct == 0.12
        assert config.risk.circuit_breaker_enabled is True

    def test_parse_risk_disabled(self):
        """risk.enabled=false should be preserved."""
        raw = {
            "name": "test",
            "data": {"source": "synthetic", "start_date": "2024-01-01",
                     "end_date": "2024-12-31"},
            "risk": {"enabled": False},
        }
        config = parse_config(raw)
        assert config.risk.enabled is False


# ============================================================================
# _build_risk_manager
# ============================================================================


class TestBuildRiskManager:
    """Test _build_risk_manager builder."""

    def test_basic_risk_manager(self):
        """默认配置只建 PositionLimit —— max_drawdown=1.0 是 no-op

        §4.3.2 把渐进减仓改成默认关闭后，DrawdownLimit 不再无条件构造。
        下面 test_basic_risk_manager_with_drawdown_enabled 是它的对照面。
        """
        config = _make_config()
        rm = _build_risk_manager(config)
        assert rm is not None
        assert isinstance(rm, CompositeRiskManager)
        types = [type(c).__name__ for c in rm.constraints]
        assert types == ["PositionLimit"]
        assert "DrawdownLimit" not in types

    def test_basic_risk_manager_with_drawdown_enabled(self):
        """显式配 max_drawdown < 1.0 时才有 DrawdownLimit"""
        config = _make_config(max_drawdown=0.20)
        rm = _build_risk_manager(config)
        types = [type(c).__name__ for c in rm.constraints]
        assert len(rm.constraints) == 2
        assert "PositionLimit" in types
        assert "DrawdownLimit" in types

    def test_risk_manager_position_limit_weight(self):
        """PositionLimit should use configured max_weight."""
        config = _make_config(max_position_weight=0.05)
        rm = _build_risk_manager(config)
        pos_limit = next(c for c in rm.constraints if isinstance(c, PositionLimit))
        assert pos_limit.max_weight == 0.05

    def test_risk_manager_drawdown_limit(self):
        """DrawdownLimit should use configured max_drawdown."""
        config = _make_config(max_drawdown=0.15)
        rm = _build_risk_manager(config)
        dd_limit = next(c for c in rm.constraints if isinstance(c, DrawdownLimit))
        assert dd_limit.max_drawdown == 0.15
        assert dd_limit.reduce_position_at == 0.075  # 50% of max

    def test_risk_manager_with_sector_limit(self):
        """SectorLimit should be added when max_sector_weight specified."""
        config = _make_config(max_sector_weight=0.25)
        rm = _build_risk_manager(config)
        # PositionLimit + SectorLimit（无 DrawdownLimit：max_drawdown 默认 1.0）
        assert len(rm.constraints) == 2
        sec_limit = next(c for c in rm.constraints if isinstance(c, SectorLimit))
        assert sec_limit.max_weight == 0.25

    def test_risk_manager_with_turnover_limit(self):
        """TurnoverLimit should be added when max_daily_turnover specified."""
        config = _make_config(max_daily_turnover=0.50)
        rm = _build_risk_manager(config)
        # PositionLimit + TurnoverLimit（无 DrawdownLimit：max_drawdown 默认 1.0）
        assert len(rm.constraints) == 2
        to_limit = next(c for c in rm.constraints if isinstance(c, TurnoverLimit))
        assert to_limit.max_daily_turnover == 0.50

    def test_risk_manager_all_constraints(self):
        """Should create all 4 constraints when all specified."""
        config = _make_config(
            max_position_weight=0.05,
            max_drawdown=0.15,
            max_sector_weight=0.25,
            max_daily_turnover=0.40,
        )
        rm = _build_risk_manager(config)
        assert len(rm.constraints) == 4

    def test_risk_manager_disabled(self):
        """Should return None when risk disabled."""
        config = _make_config(enabled=False)
        rm = _build_risk_manager(config)
        assert rm is None

    def test_risk_manager_lot_size_propagated(self):
        """lot_size from config should propagate to constraints."""
        config = _make_config()
        config.lot_size = 10  # CB lot size
        rm = _build_risk_manager(config)
        pos_limit = next(c for c in rm.constraints if isinstance(c, PositionLimit))
        assert pos_limit.lot_size == 10


# ============================================================================
# _build_stop_loss_manager
# ============================================================================


class TestBuildStopLossManager:
    """Test _build_stop_loss_manager builder."""

    def test_no_stop_loss(self):
        """Should return None when no stop loss configured."""
        config = _make_config()
        slm = _build_stop_loss_manager(config)
        assert slm is None

    def test_fixed_stop_loss(self):
        """Should create manager with FixedPercentStopLoss."""
        config = _make_config(stop_loss_pct=0.10)
        slm = _build_stop_loss_manager(config)
        assert slm is not None
        assert isinstance(slm, StopLossManager)
        assert len(slm._rules) == 1
        assert isinstance(slm._rules[0], FixedPercentStopLoss)

    def test_trailing_stop_loss(self):
        """Should create manager with TrailingStopLoss."""
        config = _make_config(trailing_stop_pct=0.08)
        slm = _build_stop_loss_manager(config)
        assert slm is not None
        assert len(slm._rules) == 1
        assert isinstance(slm._rules[0], TrailingStopLoss)

    def test_both_stop_losses(self):
        """Should create manager with both stop loss rules."""
        config = _make_config(stop_loss_pct=0.15, trailing_stop_pct=0.10)
        slm = _build_stop_loss_manager(config)
        assert slm is not None
        assert len(slm._rules) == 2

    def test_circuit_breaker_only(self):
        """Should create manager with circuit breaker only."""
        config = _make_config(
            circuit_breaker_enabled=True,
            circuit_breaker_drawdown=0.10,
            daily_loss_limit=0.05,
        )
        slm = _build_stop_loss_manager(config)
        assert slm is not None
        assert slm._circuit_breaker is not None
        assert isinstance(slm._circuit_breaker, CircuitBreaker)

    def test_circuit_breaker_params(self):
        """Circuit breaker should use configured thresholds."""
        config = _make_config(
            circuit_breaker_enabled=True,
            circuit_breaker_drawdown=0.12,
            daily_loss_limit=0.04,
        )
        slm = _build_stop_loss_manager(config)
        cb = slm._circuit_breaker
        assert cb.config.max_drawdown == 0.12
        assert cb.config.max_daily_loss == 0.04

    def test_stop_loss_disabled(self):
        """Should return None when risk disabled."""
        config = _make_config(enabled=False, stop_loss_pct=0.10)
        slm = _build_stop_loss_manager(config)
        assert slm is None

    def test_zero_stop_loss_pct_no_rule(self):
        """stop_loss_pct=0 should not create a rule."""
        config = _make_config(stop_loss_pct=0.0)
        slm = _build_stop_loss_manager(config)
        assert slm is None


# ============================================================================
# _build_audit_logger
# ============================================================================


class TestBuildAuditLogger:
    """Test _build_audit_logger builder."""

    def test_audit_logger_created(self):
        """Should create AuditLogger by default."""
        config = _make_config()
        with tempfile.TemporaryDirectory() as tmpdir:
            config.output_dir = tmpdir
            al = _build_audit_logger(config)
            assert al is not None
            assert isinstance(al, AuditLogger)
            # Close file handle to avoid Windows PermissionError
            if al._file_handle:
                al._file_handle.close()

    def test_audit_logger_log_dir(self):
        """AuditLogger should write to output_dir/audit/."""
        config = _make_config()
        with tempfile.TemporaryDirectory() as tmpdir:
            config.output_dir = tmpdir
            al = _build_audit_logger(config)
            expected_dir = Path(tmpdir) / "audit"
            assert al.config.log_dir == expected_dir
            # Close file handle to avoid Windows PermissionError
            if al._file_handle:
                al._file_handle.close()

    def test_audit_logger_disabled(self):
        """Should return None when enable_audit_log=False."""
        config = _make_config(enable_audit_log=False)
        al = _build_audit_logger(config)
        assert al is None

    def test_audit_logger_risk_disabled(self):
        """Should return None when risk disabled."""
        config = _make_config(enabled=False)
        al = _build_audit_logger(config)
        assert al is None


# ============================================================================
# _build_market_regime_detector
# ============================================================================


class TestBuildMarketRegimeDetector:
    """Test _build_market_regime_detector builder."""

    def test_detector_disabled_by_default(self):
        """Should return None by default (market_regime_enabled=False)."""
        config = _make_config()
        mrd = _build_market_regime_detector(config)
        assert mrd is None

    def test_detector_enabled(self):
        """Should create MarketRegimeDetector when enabled."""
        config = _make_config(market_regime_enabled=True)
        mrd = _build_market_regime_detector(config)
        assert mrd is not None
        assert isinstance(mrd, MarketRegimeDetector)

    def test_detector_risk_disabled(self):
        """Should return None when risk disabled."""
        config = _make_config(enabled=False, market_regime_enabled=True)
        mrd = _build_market_regime_detector(config)
        assert mrd is None


# ============================================================================
# config_to_dict round-trip
# ============================================================================


class TestRiskConfigSerialization:
    """Test config_to_dict includes risk section."""

    def test_config_to_dict_includes_risk(self):
        """config_to_dict should include risk configuration."""
        config = _make_config(
            max_position_weight=0.05,
            stop_loss_pct=0.10,
            circuit_breaker_enabled=True,
        )
        d = config_to_dict(config)
        assert "risk" in d
        assert d["risk"]["max_position_weight"] == 0.05
        assert d["risk"]["stop_loss_pct"] == 0.10
        assert d["risk"]["circuit_breaker_enabled"] is True

    def test_config_to_dict_risk_defaults(self):
        """Default RiskConfig should serialize correctly."""
        config = _make_config()
        d = config_to_dict(config)
        assert d["risk"]["enabled"] is True
        assert d["risk"]["max_position_weight"] == 0.10
        assert d["risk"]["max_drawdown"] == 1.0  # §4.3.2 渐进减仓默认关闭


# ============================================================================
# YAML round-trip via real config files
# ============================================================================


class TestRiskYAMLRoundTrip:
    """Test loading YAML configs with risk_constraints section."""

    def test_momentum_hs300_config(self):
        """configs/backtest/momentum_hs300.yml should parse risk correctly."""
        config_path = Path("configs/backtest/momentum_hs300.yml")
        if not config_path.exists():
            pytest.skip("momentum_hs300.yml not found")

        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        config = parse_config(raw)
        assert config.risk.max_position_weight == 0.10
        assert config.risk.max_drawdown == 0.20

    def test_multifactor_enhance_config(self):
        """configs/backtest/multifactor_enhance.yml risk and stop_loss."""
        config_path = Path("configs/backtest/multifactor_enhance.yml")
        if not config_path.exists():
            pytest.skip("multifactor_enhance.yml not found")

        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        config = parse_config(raw)
        assert config.risk.max_position_weight == 0.05
        assert config.risk.max_sector_weight == 0.25
        assert config.risk.max_drawdown == 0.20
        # stop_loss_pct from strategy params
        assert config.risk.stop_loss_pct == 0.20

    def test_pead_event_config(self):
        """configs/backtest/pead_event.yml risk fields."""
        config_path = Path("configs/backtest/pead_event.yml")
        if not config_path.exists():
            pytest.skip("pead_event.yml not found")

        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        config = parse_config(raw)
        assert config.risk.max_position_weight == 0.05
        assert config.risk.max_drawdown == 0.20
        assert config.risk.stop_loss_pct == 0.15

    def test_smallcap_rotation_config(self):
        """configs/backtest/smallcap_rotation.yml risk fields."""
        config_path = Path("configs/backtest/smallcap_rotation.yml")
        if not config_path.exists():
            pytest.skip("smallcap_rotation.yml not found")

        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        config = parse_config(raw)
        assert config.risk.max_position_weight == 0.05
        assert config.risk.max_drawdown == 0.20
        assert config.risk.stop_loss_pct == 0.15


class TestVolumeConstraintConfig:
    """Test volume_constraint is correctly parsed from YAML configs."""

    @pytest.mark.parametrize("config_name", [
        "multifactor_enhance_1y",
        "pead_event_1y",
        "smallcap_rotation_1y",
        "sector_momentum_5y",
        "earnings_momentum_5y",
        "value_quality_5y",
    ])
    def test_volume_constraint_present(self, config_name):
        """All A-share equity configs should have volume_constraint."""
        config_path = Path(f"configs/backtest/{config_name}.yml")
        if not config_path.exists():
            pytest.skip(f"{config_name}.yml not found")

        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        fm = raw.get("fill_model", {})
        assert "volume_constraint" in fm, (
            f"{config_name}: fill_model missing volume_constraint"
        )
        vc = fm["volume_constraint"]
        assert vc["max_participation_rate"] == 0.10
        assert vc["lot_size"] == 100

    def test_volume_constraint_parsed_by_config_runner(self):
        """FillModelConfig correctly parses volume_constraint dict."""
        from aipha.backtest.config_runner import FillModelConfig

        fm_cfg = FillModelConfig(
            name="next_bar_open",
            volume_constraint={
                "max_participation_rate": 0.10,
                "lot_size": 100,
            },
        )
        assert fm_cfg.volume_constraint is not None
        assert fm_cfg.volume_constraint["max_participation_rate"] == 0.10
        assert fm_cfg.volume_constraint["lot_size"] == 100


# ============================================================================
# run_from_config report integration (end-to-end)
# ============================================================================


class TestRunFromConfigReportIntegration:
    """Test that run_from_config generates reports and injects risk components."""

    def _make_config_file(self, tmp_path: Path, **extra) -> Path:
        """Create a synthetic backtest config with risk_constraints."""
        config_content = {
            "name": "risk_report_integration_test",
            "data": {
                "source": "synthetic",
                "start_date": "2024-01-01",
                "end_date": "2024-03-31",
                "symbols": ["600000.SH"],
            },
            "strategy": {
                "name": "demo_momentum",
                "params": {"lookback_days": 20, "top_k": 1},
            },
            "cost": {
                "commission_rate": 0.0003,
                "stamp_tax_rate": 0.001,
            },
            "initial_capital": 100000.0,
            "random_seed": 42,
            "output_dir": str(tmp_path / "output"),
            "risk_constraints": {
                "max_single_position_pct": 0.10,
                "max_drawdown_pct": 0.20,
            },
        }
        config_content.update(extra)
        config_path = tmp_path / "test_config.yml"
        with open(config_path, "w") as f:
            yaml.dump(config_content, f)
        return config_path

    def test_report_generated(self, tmp_path):
        """run_from_config should generate BacktestReport."""
        from aipha.backtest.config_runner import run_from_config

        path = self._make_config_file(tmp_path)
        result = run_from_config(path)
        assert result.report is not None
        assert result.report.strategy_name.lower() == "momentum"
        assert result.report.initial_capital == 100000.0

    def test_report_has_performance_metrics(self, tmp_path):
        """Report should have performance metrics."""
        from aipha.backtest.config_runner import run_from_config

        path = self._make_config_file(tmp_path)
        result = run_from_config(path)
        p = result.report.performance
        assert p.trading_days > 0
        assert isinstance(p.sharpe_ratio, float)

    def test_report_has_drawdown(self, tmp_path):
        """Report should have drawdown analysis."""
        from aipha.backtest.config_runner import run_from_config

        path = self._make_config_file(tmp_path)
        result = run_from_config(path)
        dd = result.report.drawdown
        assert isinstance(dd.max_drawdown_pct, float)

    def test_report_has_cost_attribution(self, tmp_path):
        """Report should have cost attribution."""
        from aipha.backtest.config_runner import run_from_config

        path = self._make_config_file(tmp_path)
        result = run_from_config(path)
        costs = result.report.costs
        assert costs.total_fees >= 0

    def test_html_report_generated(self, tmp_path):
        """run_from_config should generate HTML report file."""
        from aipha.backtest.config_runner import run_from_config

        path = self._make_config_file(tmp_path)
        result = run_from_config(path)
        assert result.html_report_path is not None
        assert result.html_report_path.exists()
        content = result.html_report_path.read_text(encoding="utf-8")
        assert "<html" in content.lower()

    def test_risk_manager_injected(self, tmp_path):
        """Risk constraints should be parsed into RiskConfig."""
        from aipha.backtest.config_runner import run_from_config

        path = self._make_config_file(tmp_path)
        result = run_from_config(path)
        assert result.config.risk.max_position_weight == 0.10
        assert result.config.risk.max_drawdown == 0.20

    def test_stop_loss_from_strategy_params(self, tmp_path):
        """stop_loss_pct in strategy params should populate RiskConfig via parse_config."""
        raw = {
            "name": "test",
            "data": {"source": "synthetic", "start_date": "2024-01-01",
                     "end_date": "2024-12-31"},
            "strategy": {
                "name": "demo_momentum",
                "params": {"lookback_days": 10, "top_k": 1, "stop_loss_pct": 0.15},
            },
        }
        config = parse_config(raw)
        assert config.risk.stop_loss_pct == 0.15


# ============================================================================
# Report edge case tests
# ============================================================================


class TestRunFromConfigReportEdgeCases:
    """Edge case tests for report generation in run_from_config."""

    def _make_config_file(self, tmp_path, **extra):
        """Create a synthetic backtest config."""
        config_content = {
            "name": "edge_case_test",
            "data": {
                "source": "synthetic",
                "start_date": "2024-01-01",
                "end_date": "2024-03-31",
                "symbols": ["600000.SH"],
            },
            "strategy": {
                "name": "demo_momentum",
                "params": {"lookback_days": 20, "top_k": 1},
            },
            "initial_capital": 100000.0,
            "random_seed": 42,
            "output_dir": str(tmp_path / "output"),
        }
        config_content.update(extra)
        config_path = tmp_path / "test_config.yml"
        with open(config_path, "w") as f:
            yaml.dump(config_content, f)
        return config_path

    def test_report_none_when_generation_fails(self, tmp_path, monkeypatch):
        """Report should be None if generate_report raises."""
        from aipha.backtest.config_runner import run_from_config
        import aipha.backtest.config_runner as cr_mod

        def _boom(*a, **kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(cr_mod, "generate_report", _boom)
        path = self._make_config_file(tmp_path)
        result = run_from_config(path)
        assert result.report is None
        assert result.html_report_path is None

    def test_html_report_none_when_html_gen_fails(self, tmp_path, monkeypatch):
        """HTML path should be None if generate_html_report raises."""
        from aipha.backtest.config_runner import run_from_config
        import aipha.backtest.config_runner as cr_mod

        def _html_boom(*a, **kw):
            raise RuntimeError("html boom")

        monkeypatch.setattr(cr_mod, "generate_html_report", _html_boom)
        path = self._make_config_file(tmp_path)
        result = run_from_config(path)
        assert result.report is not None
        assert result.html_report_path is None

    def test_report_summary_text(self, tmp_path):
        """Report summary should be a non-empty string."""
        from aipha.backtest.config_runner import run_from_config

        path = self._make_config_file(tmp_path)
        result = run_from_config(path)
        summary = result.report.summary()
        assert isinstance(summary, str)
        assert len(summary) > 50


# ============================================================================
# _build_position_sizing
# ============================================================================


class TestBuildPositionSizing:
    """_build_position_sizing 配置解析测试。"""

    def _make_config(self, risk_extra=None, risk_enabled=True):
        """构造最小 BacktestRunConfig 以测试 _build_position_sizing。"""
        return BacktestRunConfig(
            name="test_ps",
            data=DataConfig(source="synthetic", symbols=["A.SZ"],
                            start_date=date(2024, 1, 1), end_date=date(2024, 6, 1)),
            universe=UniverseConfig(),
            strategy=StrategyConfig(name="demo_dual_ma"),
            cost=CostConfig(),
            risk=RiskConfig(enabled=risk_enabled, extra=risk_extra or {}),
        )

    def test_returns_none_when_not_configured(self):
        """无 position_sizing 配置时返回 None。"""
        from aipha.backtest.config_runner import _build_position_sizing

        result = _build_position_sizing(self._make_config())
        assert result is None

    def test_returns_none_when_risk_disabled(self):
        """risk.enabled=False 时返回 None。"""
        from aipha.backtest.config_runner import _build_position_sizing

        result = _build_position_sizing(self._make_config(
            risk_enabled=False,
            risk_extra={"position_sizing": {"volatility_target": {"enabled": True}}},
        ))
        assert result is None

    def test_returns_none_when_both_disabled(self):
        """两个子模块都 enabled=false 时返回 None。"""
        from aipha.backtest.config_runner import _build_position_sizing

        result = _build_position_sizing(self._make_config(risk_extra={
            "position_sizing": {
                "volatility_target": {"enabled": False},
                "drawdown_control": {"enabled": False},
            }
        }))
        assert result is None

    def test_creates_overlay_with_vol_target(self):
        """仅配置 volatility_target 时创建 overlay。"""
        from aipha.backtest.config_runner import _build_position_sizing

        overlay = _build_position_sizing(self._make_config(risk_extra={
            "position_sizing": {
                "volatility_target": {
                    "enabled": True,
                    "target_vol": 0.15,
                    "lookback_days": 30,
                    "min_scale": 0.2,
                    "max_scale": 1.5,
                },
            }
        }))
        assert overlay is not None
        # §13.2: 波动率目标的唯一实现是 VolTargetScaler（`position_sizing.py:250`），
        # 早先的 `_vol_overlay` 子对象已不存在。配置留在 `_vol_config`，
        # 实际生效的参数在 `_vol_scaler` 上 —— 两边都断言，
        # 免得「配置存下来了但没接进 scaler」这种断线被漏掉。
        assert overlay._vol_config.target_vol == 0.15
        assert overlay._vol_config.lookback_days == 30
        assert overlay._vol_scaler.vol_target == 0.15
        assert overlay._vol_scaler.lookback == 30
        assert overlay._vol_scaler.min_scale == 0.2
        assert overlay._vol_scaler.max_scale == 1.5

    def test_creates_overlay_with_drawdown_control(self):
        """仅配置 drawdown_control 时创建 overlay。"""
        from aipha.backtest.config_runner import _build_position_sizing

        overlay = _build_position_sizing(self._make_config(risk_extra={
            "position_sizing": {
                "drawdown_control": {
                    "enabled": True,
                    "threshold": 0.08,
                    "max_threshold": 0.20,
                    "min_scale": 0.3,
                },
            }
        }))
        assert overlay is not None
        assert overlay._dd_reducer is not None
        assert overlay._dd_reducer._config.threshold == 0.08
        assert overlay._dd_reducer._config.max_threshold == 0.20
        assert overlay._dd_reducer._config.min_scale == 0.3

    def test_creates_overlay_with_both(self):
        """两者都配置时 overlay 包含两个子模块。"""
        from aipha.backtest.config_runner import _build_position_sizing

        overlay = _build_position_sizing(self._make_config(risk_extra={
            "position_sizing": {
                "volatility_target": {"enabled": True, "target_vol": 0.12},
                "drawdown_control": {"enabled": True, "threshold": 0.05},
            }
        }))
        assert overlay is not None
        # §13.2: 波动率目标走 _vol_scaler（VolTargetScaler），无 _vol_overlay
        assert overlay._vol_scaler.vol_target == 0.12
        assert overlay._dd_reducer is not None
        assert overlay._dd_reducer._config.threshold == 0.05

    def test_risk_config_extra_field_populated(self):
        """YAML 中非标准字段应落入 extra dict。"""
        config = RiskConfig(
            extra={"position_sizing": {"volatility_target": {"enabled": True}}, "custom_key": 42}
        )
        assert "position_sizing" in config.extra
        assert config.extra["custom_key"] == 42


# ---------------------------------------------------------------------------
# 数据健康检查阻断门禁 (C3 修复)
# ---------------------------------------------------------------------------

class TestDataHealthGate:
    """验证 _run_data_health_check 在 ERROR 级别时阻断回测"""

    def test_error_status_blocks_backtest(self, monkeypatch):
        """status=error 时应抛 RuntimeError"""
        from aipha.backtest.config_runner import _run_data_health_check

        mock_summary = {
            "status": "error",
            "checks": [
                {"name": "price_jump", "status": "error", "detail": "异常跳变 >50%"},
            ],
        }
        monkeypatch.setattr(
            "aipha.data.health_check.quick_check_bars_by_date",
            lambda bars: mock_summary,
        )
        with pytest.raises(RuntimeError, match="ERROR 级别问题"):
            _run_data_health_check({})

    def test_warning_status_does_not_block(self, monkeypatch):
        """status=warning 时不应阻断"""
        from aipha.backtest.config_runner import _run_data_health_check

        mock_summary = {
            "status": "warning",
            "checks": [
                {"name": "missing_rate", "status": "warning", "detail": "缺失率 5%"},
            ],
        }
        monkeypatch.setattr(
            "aipha.data.health_check.quick_check_bars_by_date",
            lambda bars: mock_summary,
        )
        result = _run_data_health_check({})
        assert result["status"] == "warning"

    def test_pass_status_returns_summary(self, monkeypatch):
        """status=pass 正常返回"""
        from aipha.backtest.config_runner import _run_data_health_check

        monkeypatch.setattr(
            "aipha.data.health_check.quick_check_bars_by_date",
            lambda bars: {"status": "pass", "checks": []},
        )
        result = _run_data_health_check({})
        assert result["status"] == "pass"

    def test_block_on_error_false_does_not_raise(self, monkeypatch):
        """block_on_error=False 时 error 也不阻断"""
        from aipha.backtest.config_runner import _run_data_health_check

        monkeypatch.setattr(
            "aipha.data.health_check.quick_check_bars_by_date",
            lambda bars: {"status": "error", "checks": [{"name": "x", "status": "error", "detail": "y"}]},
        )
        result = _run_data_health_check({}, block_on_error=False)
        assert result["status"] == "error"