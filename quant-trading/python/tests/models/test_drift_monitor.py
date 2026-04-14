"""tests/models/test_drift_monitor.py — DriftMonitor 单元测试"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from aipha.models.drift_monitor import DriftMonitor, PSI_WARNING


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _make_df(data: dict[str, list]) -> pd.DataFrame:
    return pd.DataFrame(data)


def _stable_baseline(n: int = 1000, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"f1": rng.normal(0, 1, n), "f2": rng.uniform(0, 1, n)})


def _drifted_current(n: int = 500, shift: float = 3.0, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"f1": rng.normal(shift, 1, n), "f2": rng.uniform(0, 1, n)})


# ---------------------------------------------------------------------------
# 构造参数校验
# ---------------------------------------------------------------------------

def test_empty_feature_names_raises():
    with pytest.raises(ValueError, match="feature_names"):
        DriftMonitor(feature_names=[])


def test_n_bins_too_small_raises():
    with pytest.raises(ValueError, match="n_bins"):
        DriftMonitor(feature_names=["f1"], n_bins=1)


# ---------------------------------------------------------------------------
# set_baseline & detect_drift
# ---------------------------------------------------------------------------

def test_detect_without_baseline_raises():
    m = DriftMonitor(["f1"])
    with pytest.raises(RuntimeError, match="Baseline"):
        m.detect_drift(_stable_baseline())


def test_stable_data_low_psi():
    """同分布数据的 PSI 应 < 0.1（稳定）"""
    baseline = _stable_baseline(n=2000, seed=0)
    current = _stable_baseline(n=500, seed=99)
    m = DriftMonitor(["f1", "f2"], n_bins=10)
    m.set_baseline(baseline)
    report = m.detect_drift(current)
    assert report.has_significant_drift is False
    assert report.overall_psi < PSI_WARNING


def test_drifted_data_high_psi():
    """显著偏移数据（均值差3σ）的 PSI 应 > 0.2"""
    baseline = _stable_baseline(n=2000, seed=0)
    current = _drifted_current(n=500, shift=3.0, seed=1)
    m = DriftMonitor(["f1", "f2"], n_bins=10)
    m.set_baseline(baseline)
    report = m.detect_drift(current)
    # f1 均值偏移3σ → PSI 超阈值
    assert "f1" in report.drifted_features


def test_report_structure():
    baseline = _stable_baseline()
    m = DriftMonitor(["f1", "f2"], n_bins=10)
    m.set_baseline(baseline)
    report = m.detect_drift(baseline)  # same data → minimal drift
    assert report.n_features_checked == 2
    assert isinstance(report.drifted_features, list)
    assert isinstance(report.warning_features, list)
    assert 0.0 <= report.overall_psi


# ---------------------------------------------------------------------------
# get_psi
# ---------------------------------------------------------------------------

def test_get_psi_after_detect():
    baseline = _stable_baseline()
    m = DriftMonitor(["f1", "f2"], n_bins=10)
    m.set_baseline(baseline)
    m.detect_drift(baseline)
    psi = m.get_psi("f1")
    assert isinstance(psi, float)
    assert psi >= 0.0


def test_get_psi_before_detect_raises():
    m = DriftMonitor(["f1"])
    with pytest.raises(KeyError):
        m.get_psi("f1")


# ---------------------------------------------------------------------------
# save / load baseline
# ---------------------------------------------------------------------------

def test_save_and_load_baseline(tmp_path: Path):
    baseline = _stable_baseline()
    m1 = DriftMonitor(["f1", "f2"], n_bins=10)
    m1.set_baseline(baseline)

    save_path = str(tmp_path / "baseline.json")
    m1.save_baseline(save_path)
    assert Path(save_path).exists()

    m2 = DriftMonitor(["f1", "f2"], n_bins=10)
    m2.load_baseline(save_path)
    # 加载后能正常检测
    report = m2.detect_drift(baseline)
    assert report.n_features_checked == 2


def test_loaded_baseline_json_valid(tmp_path: Path):
    baseline = _stable_baseline()
    m = DriftMonitor(["f1"], n_bins=5)
    m.set_baseline(baseline)
    path = str(tmp_path / "bl.json")
    m.save_baseline(path)
    with open(path) as f:
        data = json.load(f)
    assert "baseline" in data
    assert "f1" in data["baseline"]


# ---------------------------------------------------------------------------
# to_dict
# ---------------------------------------------------------------------------

def test_drift_report_to_dict():
    baseline = _stable_baseline()
    m = DriftMonitor(["f1", "f2"], n_bins=10)
    m.set_baseline(baseline)
    report = m.detect_drift(baseline)
    d = report.to_dict()
    assert set(d.keys()) >= {"has_significant_drift", "drifted_features",
                              "warning_features", "overall_psi", "n_features_checked"}
