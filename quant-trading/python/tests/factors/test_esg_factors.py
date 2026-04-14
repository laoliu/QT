"""ESG 因子单元测试。"""

from __future__ import annotations

import pytest
from datetime import date

from aipha.factors.base import FactorData, FactorResult
from aipha.factors.esg_factors import (
    ESGConfig,
    GovernanceFactor,
    ControversyFactor,
    ESGCompositeFactor,
)


# ── 配置测试 ──


class TestESGConfig:
    def test_defaults(self):
        c = ESGConfig()
        assert c.independent_director_weight == 0.30
        assert "critical" in c.controversy_severity_weights

    def test_custom_weights(self):
        c = ESGConfig(independent_director_weight=0.50)
        assert c.independent_director_weight == 0.50


# ── G 因子测试 ──


class TestGovernanceFactor:
    def test_name(self):
        f = GovernanceFactor()
        assert f.name == "governance_quality"

    def test_high_governance(self):
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 1, 1),
            fundamentals={
                "independent_ratio": 0.45,
                "audit_big4": 1.0,
                "mgmt_ownership": 0.15,
                "board_size": 9,
            },
        )
        result = GovernanceFactor().compute(data)
        assert result.value > 0.3  # 高分
        assert result.factor_name == "governance_quality"

    def test_low_governance(self):
        data = FactorData(
            symbol="000002.SZ",
            trade_date=date(2025, 1, 1),
            fundamentals={
                "independent_ratio": 0.10,
                "audit_big4": 0.0,
                "mgmt_ownership": 0.0,
                "board_size": 20,
            },
        )
        result = GovernanceFactor().compute(data)
        assert result.value < 0.2  # 低分

    def test_missing_fundamentals(self):
        data = FactorData(
            symbol="000003.SZ",
            trade_date=date(2025, 1, 1),
        )
        result = GovernanceFactor().compute(data)
        assert isinstance(result.value, float)

    def test_value_bounded(self):
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 1, 1),
            fundamentals={"board_size": 50},
        )
        result = GovernanceFactor().compute(data)
        assert -1 <= result.value <= 1


# ── 争议因子测试 ──


class TestControversyFactor:
    def test_name(self):
        assert ControversyFactor().name == "esg_controversy"

    def test_no_controversies(self):
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 1, 1),
        )
        result = ControversyFactor().compute(data)
        assert result.value == 0.0  # 无争议 = 0

    def test_recent_critical(self):
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 1, 1),
            metadata={
                "controversies": [
                    {"severity": "critical", "days_ago": 10},
                ],
            },
        )
        result = ControversyFactor().compute(data)
        assert result.value < 0  # 空头信号

    def test_old_event_decayed(self):
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 1, 1),
            metadata={
                "controversies": [
                    {"severity": "major", "days_ago": 100},
                ],
            },
        )
        result = ControversyFactor().compute(data)
        assert result.value == 0.0  # 超过衰减窗口

    def test_multiple_events(self):
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 1, 1),
            metadata={
                "controversies": [
                    {"severity": "critical", "days_ago": 5},
                    {"severity": "moderate", "days_ago": 30},
                ],
            },
        )
        result = ControversyFactor().compute(data)
        assert result.value < -0.3  # 累积负值


# ── 综合因子测试 ──


class TestESGCompositeFactor:
    def test_name(self):
        assert ESGCompositeFactor().name == "esg_composite"

    def test_composite_calculation(self):
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 1, 1),
            fundamentals={
                "independent_ratio": 0.40,
                "audit_big4": 1.0,
                "mgmt_ownership": 0.15,
                "board_size": 9,
            },
            metadata={
                "controversies": [
                    {"severity": "minor", "days_ago": 20},
                ],
            },
        )
        result = ESGCompositeFactor().compute(data)
        assert "governance" in result.metadata
        assert "controversy" in result.metadata
        assert isinstance(result.value, float)

    def test_custom_weights(self):
        f1 = ESGCompositeFactor(g_weight=0.8, controversy_weight=0.2)
        f2 = ESGCompositeFactor(g_weight=0.2, controversy_weight=0.8)
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 1, 1),
            fundamentals={"audit_big4": 1.0},
        )
        r1 = f1.compute(data)
        r2 = f2.compute(data)
        # 不同权重应产生不同结果（或至少不报错）
        assert isinstance(r1.value, float)
        assert isinstance(r2.value, float)
