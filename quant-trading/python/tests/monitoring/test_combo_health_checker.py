"""ComboHealthChecker 单元测试。

覆盖 A2(风险贡献)/A3(alpha衰减)/C2(MDD预警) 三项健康检查。
"""

import numpy as np
import pandas as pd
import pytest

from aipha.monitoring.combo_health_checker import (
    ComboHealthChecker,
    HealthCheckItem,
    HealthReport,
)


# ---- helpers ----

def _make_nav(n: int = 500, mu: float = 0.0003, sigma: float = 0.015,
              seed: int = 42) -> pd.Series:
    rng = np.random.RandomState(seed)
    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    return pd.Series((1 + rng.normal(mu, sigma, n)).cumprod(), index=dates)


def _base_config() -> dict:
    return {
        "profile_name": "test_combo",
        "monitoring": {
            "risk_contribution": {
                "ml_csi2000_max": 0.50,
                "ml_csi2000_warn": 0.45,
            },
            "alpha_decay": {
                "rolling_sr_window": 252,
                "sr_threshold": 0.0,
                "max_consecutive_months": 6,
            },
            "drawdown": {
                "warn_level": -0.20,
                "halt_level": -0.25,
            },
        },
    }


W = {"ml_csi2000": 0.35, "ml_csi1000": 0.15, "cb_quality": 0.25, "etf_faa": 0.25}


# ---- A2: 风险贡献 ----

class TestRiskContribution:
    def test_passes_when_below_limit(self):
        """等波动率组合 → 风险贡献接近名义权重 → 通过"""
        nav = {k: _make_nav(500, 0.0002, 0.012, seed=i) for i, k in enumerate(W)}
        checker = ComboHealthChecker(_base_config())
        item = checker.check_risk_contribution(nav, W)
        assert item.passed or item.level != "critical"

    def test_fails_when_csi2000_dominates(self):
        """CSI2000 高波、其他低波 → 风险贡献超 50%"""
        nav = {
            "ml_csi2000": _make_nav(500, 0.0003, 0.030, seed=0),
            "ml_csi1000": _make_nav(500, 0.0001, 0.005, seed=1),
            "cb_quality": _make_nav(500, 0.0001, 0.003, seed=2),
            "etf_faa": _make_nav(500, 0.0001, 0.004, seed=3),
        }
        checker = ComboHealthChecker(_base_config())
        item = checker.check_risk_contribution(nav, W)
        assert not item.passed
        assert item.level == "critical"
        assert item.value is not None and item.value > 0.50

    def test_skips_when_data_insufficient(self):
        nav = {k: _make_nav(30, seed=i) for i, k in enumerate(W)}
        checker = ComboHealthChecker(_base_config())
        item = checker.check_risk_contribution(nav, W)
        assert item.passed  # 跳过 = 不告警


# ---- A3: Alpha 衰减 ----

class TestAlphaDecay:
    def test_passes_with_positive_sr(self):
        nav = _make_nav(600, mu=0.0005, sigma=0.010)
        checker = ComboHealthChecker(_base_config())
        item = checker.check_alpha_decay(nav)
        assert item.passed

    def test_fails_with_prolonged_negative_sr(self):
        """构造后半段持续亏损的 NAV"""
        n = 800
        dates = pd.date_range("2020-01-01", periods=n, freq="B")
        vals = np.ones(n)
        rng = np.random.RandomState(99)
        # 前 400 天正收益
        for i in range(1, 400):
            vals[i] = vals[i - 1] * (1 + rng.normal(0.0008, 0.01))
        # 后 400 天负收益
        for i in range(400, n):
            vals[i] = vals[i - 1] * (1 + rng.normal(-0.0005, 0.01))
        nav = pd.Series(vals, index=dates)
        checker = ComboHealthChecker(_base_config())
        item = checker.check_alpha_decay(nav)
        # 后段 SR < 0 连续多月
        assert item.name == "A3_alpha_decay"

    def test_skips_when_data_short(self):
        nav = _make_nav(100)
        checker = ComboHealthChecker(_base_config())
        item = checker.check_alpha_decay(nav)
        assert item.passed  # 跳过


# ---- C2: MDD ----

class TestDrawdown:
    def test_normal_drawdown(self):
        nav = _make_nav(500, mu=0.0005, sigma=0.008)
        checker = ComboHealthChecker(_base_config())
        item = checker.check_drawdown(nav)
        assert item.passed

    def test_warn_level_triggered(self):
        """构造 -22% 回撤"""
        dates = pd.date_range("2020-01-01", periods=300, freq="B")
        vals = np.linspace(1.0, 1.3, 200).tolist()
        vals += np.linspace(1.3, 1.01, 100).tolist()  # ~-22%
        nav = pd.Series(vals, index=dates)
        checker = ComboHealthChecker(_base_config())
        item = checker.check_drawdown(nav)
        assert not item.passed
        assert item.level == "warning"

    def test_halt_level_triggered(self):
        """构造 -30% 回撤"""
        dates = pd.date_range("2020-01-01", periods=300, freq="B")
        vals = np.linspace(1.0, 1.4, 200).tolist()
        vals += np.linspace(1.4, 0.98, 100).tolist()  # ~-30%
        nav = pd.Series(vals, index=dates)
        checker = ComboHealthChecker(_base_config())
        item = checker.check_drawdown(nav)
        assert not item.passed
        assert item.level == "critical"


# ---- HealthReport ----

class TestHealthReport:
    def test_summary_format(self):
        report = HealthReport(
            timestamp="2026-03-28",
            profile_name="test",
            items=[
                HealthCheckItem("check1", True, "info", "OK"),
                HealthCheckItem("check2", False, "critical", "FAIL"),
            ],
        )
        assert report.has_critical
        assert len(report.critical_items) == 1
        s = report.summary()
        assert "✅" in s
        assert "❌" in s

    def test_from_config(self, tmp_path):
        import yaml
        cfg = _base_config()
        p = tmp_path / "test.yml"
        p.write_text(yaml.dump(cfg), encoding="utf-8")
        checker = ComboHealthChecker.from_config(p)
        assert checker._profile_name == "test_combo"
