"""Tests for ConcentrationLimit and LiquidityConstraint integration in config_runner.

验证 _build_risk_manager 正确解析并构建新增风控约束。
"""

from __future__ import annotations

import pytest
from datetime import date
from dataclasses import dataclass, field

from aipha.backtest.config_runner import (
    RiskConfig,
    BacktestRunConfig,
    DataConfig,
    UniverseConfig,
    StrategyConfig,
    CostConfig,
    _build_risk_manager,
)
from aipha.risk.constraints import (
    ConcentrationLimit,
    ForceRedeemExposureLimit,
    LiquidityConstraint,
    StyleExposureLimit,
)


def _make_config(**risk_overrides) -> BacktestRunConfig:
    """创建最小 BacktestRunConfig 用于测试 _build_risk_manager。"""
    risk_cfg = RiskConfig(**risk_overrides)
    return BacktestRunConfig(
        name="test",
        data=DataConfig(
            source="tushare",
            symbols=["000001.SZ"],
            start_date=date(2024, 1, 1),
            end_date=date(2024, 3, 1),
        ),
        universe=UniverseConfig(),
        strategy=StrategyConfig(name="dual_ma"),
        cost=CostConfig(),
        risk=risk_cfg,
    )


# === ConcentrationLimit 集成 ===


class TestConcentrationLimitIntegration:
    """集中度约束集成测试"""

    def test_disabled_by_default(self):
        """默认不启用集中度约束"""
        config = _make_config()
        rm = _build_risk_manager(config)
        assert rm is not None
        concentration_constraints = [
            c for c in rm.constraints if isinstance(c, ConcentrationLimit)
        ]
        assert len(concentration_constraints) == 0

    def test_enabled_creates_constraint(self):
        """启用后正确创建 ConcentrationLimit"""
        config = _make_config(concentration_enabled=True)
        rm = _build_risk_manager(config)
        concentration_constraints = [
            c for c in rm.constraints if isinstance(c, ConcentrationLimit)
        ]
        assert len(concentration_constraints) == 1
        cl = concentration_constraints[0]
        assert cl.max_top_n_weight == 0.50
        assert cl.top_n == 5
        assert cl.min_positions == 10

    def test_custom_params(self):
        """自定义参数正确传递"""
        config = _make_config(
            concentration_enabled=True,
            concentration_max_top_n_weight=0.40,
            concentration_top_n=3,
            concentration_min_positions=5,
        )
        rm = _build_risk_manager(config)
        cl = [c for c in rm.constraints if isinstance(c, ConcentrationLimit)][0]
        assert cl.max_top_n_weight == 0.40
        assert cl.top_n == 3
        assert cl.min_positions == 5

    def test_constraint_count_with_concentration(self):
        """启用集中度后约束总数增加"""
        config_off = _make_config()
        config_on = _make_config(concentration_enabled=True)
        rm_off = _build_risk_manager(config_off)
        rm_on = _build_risk_manager(config_on)
        assert len(rm_on.constraints) == len(rm_off.constraints) + 1


# === LiquidityConstraint 集成 ===


class TestLiquidityConstraintIntegration:
    """流动性约束集成测试"""

    def test_disabled_by_default(self):
        """默认不启用流动性约束"""
        config = _make_config()
        rm = _build_risk_manager(config)
        liquidity_constraints = [
            c for c in rm.constraints if isinstance(c, LiquidityConstraint)
        ]
        assert len(liquidity_constraints) == 0

    def test_enabled_creates_constraint(self):
        """启用后正确创建 LiquidityConstraint"""
        config = _make_config(liquidity_enabled=True)
        rm = _build_risk_manager(config)
        liquidity_constraints = [
            c for c in rm.constraints if isinstance(c, LiquidityConstraint)
        ]
        assert len(liquidity_constraints) == 1
        lc = liquidity_constraints[0]
        assert lc.max_participation_rate == 0.10
        assert lc.lot_size == 100  # from config.lot_size

    def test_custom_participation_rate(self):
        """自定义参与率正确传递"""
        config = _make_config(
            liquidity_enabled=True,
            liquidity_max_participation_rate=0.05,
        )
        rm = _build_risk_manager(config)
        lc = [c for c in rm.constraints if isinstance(c, LiquidityConstraint)][0]
        assert lc.max_participation_rate == 0.05

    def test_lot_size_inherited(self):
        """lot_size 从 BacktestRunConfig 继承"""
        config = _make_config(liquidity_enabled=True)
        config.lot_size = 200
        rm = _build_risk_manager(config)
        lc = [c for c in rm.constraints if isinstance(c, LiquidityConstraint)][0]
        assert lc.lot_size == 200


# === 组合测试 ===


class TestMultipleConstraintsCombination:
    """多约束组合测试"""

    def test_all_constraints_enabled(self):
        """所有可选约束同时启用"""
        config = _make_config(
            max_sector_weight=0.25,
            max_daily_turnover=0.50,
            industry_neutral_enabled=True,
            concentration_enabled=True,
            liquidity_enabled=True,
        )
        rm = _build_risk_manager(config)
        # 基础 2 (PositionLimit + DrawdownLimit) + 5 可选
        assert len(rm.constraints) == 7

    def test_risk_disabled_returns_none(self):
        """风控禁用时所有约束不生效"""
        config = _make_config(
            enabled=False,
            concentration_enabled=True,
            liquidity_enabled=True,
        )
        rm = _build_risk_manager(config)
        assert rm is None


# === RiskConfig 字段默认值 ===


class TestRiskConfigDefaults:
    """RiskConfig 新增字段默认值验证"""

    def test_concentration_defaults(self):
        rc = RiskConfig()
        assert rc.concentration_enabled is False
        assert rc.concentration_max_top_n_weight == 0.50
        assert rc.concentration_top_n == 5
        assert rc.concentration_min_positions == 10

    def test_liquidity_defaults(self):
        rc = RiskConfig()
        assert rc.liquidity_enabled is False
        assert rc.liquidity_max_participation_rate == 0.10

    def test_style_exposure_defaults(self):
        rc = RiskConfig()
        assert rc.style_exposure_enabled is False
        assert "size" in rc.style_exposure_limits
        assert "value" in rc.style_exposure_limits
        assert "momentum" in rc.style_exposure_limits


# === StyleExposureLimit 集成 ===


class TestStyleExposureLimitIntegration:
    """风格暴露约束集成测试"""

    def test_disabled_by_default(self):
        """默认不启用风格暴露约束"""
        config = _make_config()
        rm = _build_risk_manager(config)
        assert rm is not None
        style_constraints = [
            c for c in rm.constraints if isinstance(c, StyleExposureLimit)
        ]
        assert len(style_constraints) == 0

    def test_enabled_creates_constraint(self):
        """启用后创建 StyleExposureLimit"""
        config = _make_config(style_exposure_enabled=True)
        rm = _build_risk_manager(config)
        assert rm is not None
        style_constraints = [
            c for c in rm.constraints if isinstance(c, StyleExposureLimit)
        ]
        assert len(style_constraints) == 1

    def test_custom_limits(self):
        """自定义暴露限制"""
        limits = {"size": 0.20, "value": 0.15}
        config = _make_config(
            style_exposure_enabled=True,
            style_exposure_limits=limits,
        )
        rm = _build_risk_manager(config)
        style_constraints = [
            c for c in rm.constraints if isinstance(c, StyleExposureLimit)
        ]
        assert len(style_constraints) == 1
        sel = style_constraints[0]
        assert sel.max_exposure["size"] == 0.20
        assert sel.max_exposure["value"] == 0.15

    def test_total_constraint_count_with_style(self):
        """启用所有约束时的总数验证"""
        config = _make_config(
            max_sector_weight=0.20,
            max_daily_turnover=0.50,
            concentration_enabled=True,
            liquidity_enabled=True,
            style_exposure_enabled=True,
            industry_neutral_enabled=True,
        )
        rm = _build_risk_manager(config)
        assert rm is not None
        # PositionLimit + DrawdownLimit + SectorLimit + TurnoverLimit
        # + IndustryNeutral + ConcentrationLimit + LiquidityConstraint
        # + StyleExposureLimit = 8
        assert len(rm.constraints) == 8

    def test_style_exposure_defaults(self):
        """style_exposure_limits 默认值"""
        cfg = RiskConfig(style_exposure_enabled=True)
        assert "size" in cfg.style_exposure_limits
        assert "value" in cfg.style_exposure_limits
        assert "momentum" in cfg.style_exposure_limits


# ---------------------------------------------------------------------------
# ForceRedeemExposureLimit 集成测试
# ---------------------------------------------------------------------------


class TestForceRedeemExposure:
    def test_disabled_by_default(self):
        config = _make_config()
        rm = _build_risk_manager(config)
        fr_constraints = [
            c for c in rm.constraints
            if isinstance(c, ForceRedeemExposureLimit)
        ]
        assert len(fr_constraints) == 0

    def test_enabled_creates_constraint(self):
        config = _make_config(force_redeem_enabled=True)
        rm = _build_risk_manager(config)
        fr_constraints = [
            c for c in rm.constraints
            if isinstance(c, ForceRedeemExposureLimit)
        ]
        assert len(fr_constraints) == 1

    def test_default_params(self):
        config = _make_config(force_redeem_enabled=True)
        rm = _build_risk_manager(config)
        fr = [c for c in rm.constraints if isinstance(c, ForceRedeemExposureLimit)][0]
        assert fr.max_weight == 0.30
        assert fr.cb_value_threshold == 125.0

    def test_custom_params(self):
        config = _make_config(
            force_redeem_enabled=True,
            force_redeem_max_weight=0.20,
            force_redeem_cb_value_threshold=130.0,
        )
        rm = _build_risk_manager(config)
        fr = [c for c in rm.constraints if isinstance(c, ForceRedeemExposureLimit)][0]
        assert fr.max_weight == 0.20
        assert fr.cb_value_threshold == 130.0

    def test_total_count_with_force_redeem(self):
        """启用所有 9 种约束时的总数验证"""
        config = _make_config(
            max_sector_weight=0.20,
            max_daily_turnover=0.50,
            concentration_enabled=True,
            liquidity_enabled=True,
            style_exposure_enabled=True,
            industry_neutral_enabled=True,
            force_redeem_enabled=True,
        )
        rm = _build_risk_manager(config)
        assert rm is not None
        # PositionLimit + DrawdownLimit + SectorLimit + TurnoverLimit
        # + IndustryNeutral + ConcentrationLimit + LiquidityConstraint
        # + StyleExposureLimit + ForceRedeemExposureLimit = 9
        assert len(rm.constraints) == 9

    def test_risk_config_defaults(self):
        cfg = RiskConfig()
        assert cfg.force_redeem_enabled is False
        assert cfg.force_redeem_max_weight == 0.30
        assert cfg.force_redeem_cb_value_threshold == 125.0
