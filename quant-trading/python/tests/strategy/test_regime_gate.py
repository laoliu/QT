"""Tests for RegimeStrategyGate — Regime 感知策略门控。

测试覆盖（≥10 用例）：
T01 默认配置参数
T02 CB 专用配置 neutral_scale=0.8
T03 Equity 专用配置 neutral_scale=0.7
T04 get_scale BULL 返回 bull_scale
T05 get_scale NEUTRAL 返回 neutral_scale
T06 get_scale BEAR 返回 bear_scale
T07 scale_scores 逐值缩放正确
T08 scale_scores 空字典不报错
T09 apply 返回 GateResult 且 is_gated=True（NEUTRAL != 1.0）
T10 apply BULL scale=1.0 时 is_gated=False
T11 min_scale / max_scale 限幅边界
T12 make_cb_gate 工厂 + 自定义 neutral_scale
T13 make_equity_gate 工厂
T14 get_regime_summary 包含三种 Regime
"""

from __future__ import annotations

import pytest

from aipha.portfolio.regime_weight import MarketRegime
from aipha.strategy.regime_gate import (
    CB_GATE_CONFIG,
    EQUITY_GATE_CONFIG,
    GateResult,
    RegimeGateConfig,
    RegimeStrategyGate,
    make_cb_gate,
    make_equity_gate,
)


# ---------------------------------------------------------------------------
# T01-T03 配置
# ---------------------------------------------------------------------------

class TestRegimeGateConfig:
    def test_defaults(self) -> None:  # T01
        cfg = RegimeGateConfig()
        assert cfg.bull_scale == 1.0
        assert cfg.neutral_scale == 0.7
        assert cfg.bear_scale == 0.5
        assert 0.0 < cfg.min_scale < 1.0
        assert cfg.max_scale > 1.0

    def test_cb_config(self) -> None:  # T02
        assert CB_GATE_CONFIG.neutral_scale == 0.8
        assert CB_GATE_CONFIG.bull_scale == 1.1
        assert CB_GATE_CONFIG.bear_scale == 0.6

    def test_equity_config(self) -> None:  # T03
        assert EQUITY_GATE_CONFIG.neutral_scale == 0.7
        assert EQUITY_GATE_CONFIG.bull_scale == 1.0
        assert EQUITY_GATE_CONFIG.bear_scale == 0.4


# ---------------------------------------------------------------------------
# T04-T11 RegimeStrategyGate 核心
# ---------------------------------------------------------------------------

class TestRegimeStrategyGate:
    @pytest.fixture
    def gate(self) -> RegimeStrategyGate:
        cfg = RegimeGateConfig(bull_scale=1.2, neutral_scale=0.8, bear_scale=0.5)
        return RegimeStrategyGate(cfg)

    def test_get_scale_bull(self, gate: RegimeStrategyGate) -> None:  # T04
        assert gate.get_scale(MarketRegime.BULL) == pytest.approx(1.2)

    def test_get_scale_neutral(self, gate: RegimeStrategyGate) -> None:  # T05
        assert gate.get_scale(MarketRegime.NEUTRAL) == pytest.approx(0.8)

    def test_get_scale_bear(self, gate: RegimeStrategyGate) -> None:  # T06
        assert gate.get_scale(MarketRegime.BEAR) == pytest.approx(0.5)

    def test_scale_scores_values(self, gate: RegimeStrategyGate) -> None:  # T07
        scores = {"A": 1.0, "B": -0.5, "C": 0.0}
        scaled = gate.scale_scores(scores, MarketRegime.NEUTRAL)
        assert scaled["A"] == pytest.approx(0.8)
        assert scaled["B"] == pytest.approx(-0.4)
        assert scaled["C"] == pytest.approx(0.0)

    def test_scale_scores_empty(self, gate: RegimeStrategyGate) -> None:  # T08
        result = gate.scale_scores({}, MarketRegime.NEUTRAL)
        assert result == {}

    def test_apply_returns_gate_result_is_gated(self, gate: RegimeStrategyGate) -> None:  # T09
        scores = {"X": 1.0}
        scaled, res = gate.apply(scores, MarketRegime.NEUTRAL)
        assert isinstance(res, GateResult)
        assert res.is_gated is True
        assert res.scale_applied == pytest.approx(0.8)
        assert res.n_candidates == 1
        assert res.regime == MarketRegime.NEUTRAL

    def test_apply_not_gated_when_scale_one(self) -> None:  # T10
        """bull_scale=1.0 → is_gated=False。"""
        gate = RegimeStrategyGate(RegimeGateConfig(bull_scale=1.0))
        scores = {"Y": 2.0}
        _, res = gate.apply(scores, MarketRegime.BULL)
        assert res.is_gated is False

    def test_min_max_scale_clamp(self) -> None:  # T11
        """配置边界外的系数被限幅到 [min_scale, max_scale]。"""
        cfg = RegimeGateConfig(
            bull_scale=5.0,    # 超过 max_scale=2.0
            bear_scale=0.0,    # 低于 min_scale=0.1
        )
        gate = RegimeStrategyGate(cfg)
        assert gate.get_scale(MarketRegime.BULL) == pytest.approx(cfg.max_scale)
        assert gate.get_scale(MarketRegime.BEAR) == pytest.approx(cfg.min_scale)


# ---------------------------------------------------------------------------
# T12-T14 工厂 + 摘要
# ---------------------------------------------------------------------------

class TestFactories:
    def test_make_cb_gate_default(self) -> None:  # T12
        gate = make_cb_gate()
        assert gate.get_scale(MarketRegime.NEUTRAL) == pytest.approx(0.8)
        assert gate.get_scale(MarketRegime.BULL) == pytest.approx(1.1)

    def test_make_cb_gate_custom(self) -> None:
        gate = make_cb_gate(neutral_scale=0.6)
        assert gate.get_scale(MarketRegime.NEUTRAL) == pytest.approx(0.6)

    def test_make_equity_gate(self) -> None:  # T13
        gate = make_equity_gate()
        assert gate.get_scale(MarketRegime.NEUTRAL) == pytest.approx(0.7)
        assert gate.get_scale(MarketRegime.BEAR) == pytest.approx(0.4)

    def test_get_regime_summary(self) -> None:  # T14
        gate = make_cb_gate()
        summary = gate.get_regime_summary()
        assert set(summary.keys()) == {"BULL", "NEUTRAL", "BEAR"}
        assert all(isinstance(v, float) for v in summary.values())
        assert summary["BULL"] >= summary["NEUTRAL"] >= summary["BEAR"]
