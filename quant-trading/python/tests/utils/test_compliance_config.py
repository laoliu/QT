"""tests/utils/test_compliance_config.py — ComplianceConfig 单元测试"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from aipha.utils.compliance_config import ComplianceConfig, load_compliance_config


# ---------------------------------------------------------------------------
# 加载默认配置
# ---------------------------------------------------------------------------

def test_load_default_config():
    cfg = load_compliance_config()
    assert isinstance(cfg, ComplianceConfig)
    assert cfg.version == "2024-10"


def test_default_participation_rate():
    cfg = load_compliance_config()
    assert cfg.max_participation_rate == pytest.approx(0.05)


def test_default_cancel_rate():
    cfg = load_compliance_config()
    assert cfg.max_cancel_rate == pytest.approx(0.80)


def test_t1_discount_factor():
    cfg = load_compliance_config()
    assert cfg.t1_discount_factor == pytest.approx(0.90)


def test_cb_lot_size():
    cfg = load_compliance_config()
    assert cfg.cb_lot_size == 10


def test_cb_t_plus_n_zero():
    cfg = load_compliance_config()
    assert cfg.cb_t_plus_n == 0


def test_require_registration_true():
    cfg = load_compliance_config()
    assert cfg.require_registration is True


# ---------------------------------------------------------------------------
# raw() 返回完整字典
# ---------------------------------------------------------------------------

def test_raw_contains_audit_section():
    cfg = load_compliance_config()
    raw = cfg.raw()
    assert "audit" in raw


# ---------------------------------------------------------------------------
# 自定义配置文件（tmp_path）
# ---------------------------------------------------------------------------

def _minimal_yaml() -> dict:
    return {
        "version": "test-1.0",
        "reporting": {
            "require_registration": False,
            "registration_status": "exempt",
        },
        "order_limits": {
            "max_cancel_rate": 0.50,
            "max_order_rate_per_second": 3,
            "cancel_rate_alert_threshold": 0.40,
        },
        "participation": {
            "max_participation_rate": 0.05,
            "crowding_overlap_max_jaccard": 0.25,
            "crowding_high_participation_rate": 0.03,
        },
        "signal_quality": {
            "t1_discount_factor": 0.85,
            "flag_limit_hit_threshold": 0.03,
            "min_tradeable_universe_fraction": 0.75,
        },
        "convertible_bond": {
            "lot_size": 10,
            "t_plus_n": 0,
            "limit_up_pct_post_20220801": 0.20,
            "force_redeem_monitor": True,
            "force_redeem_max_exposure": 0.25,
        },
    }


def test_custom_config_file(tmp_path: Path):
    data = _minimal_yaml()
    path = tmp_path / "compliance.yml"
    with open(path, "w") as f:
        yaml.safe_dump(data, f)

    cfg = load_compliance_config(path)
    assert cfg.version == "test-1.0"
    assert cfg.max_participation_rate == pytest.approx(0.05)
    assert cfg.t1_discount_factor == pytest.approx(0.85)
    assert cfg.require_registration is False


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        load_compliance_config("/nonexistent/path/compliance.yml")


def test_missing_section_raises(tmp_path: Path):
    """缺少必要 section 时应抛 ValueError"""
    data = {"version": "bad"}
    path = tmp_path / "bad.yml"
    with open(path, "w") as f:
        yaml.safe_dump(data, f)
    with pytest.raises(ValueError, match="missing section"):
        load_compliance_config(path)


def test_config_is_frozen():
    """ComplianceConfig 是 frozen dataclass，不可修改"""
    cfg = load_compliance_config()
    with pytest.raises((AttributeError, TypeError)):
        cfg.max_cancel_rate = 0.99  # type: ignore[misc]
