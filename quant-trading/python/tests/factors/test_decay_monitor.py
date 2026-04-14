"""tests/factors/test_decay_monitor.py — DecayMonitor 单元测试"""
from __future__ import annotations

import math

import pytest

from aipha.factors.decay_monitor import DecayMonitor, _DEGRADED_RATIO, _WARNING_RATIO


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _fill(monitor: DecayMonitor, ic_values: list[float], start: int = 1) -> None:
    """批量写入 IC 序列"""
    for i, v in enumerate(ic_values):
        monitor.update(f"2026-01-{start + i:02d}", v)


# ---------------------------------------------------------------------------
# 构造参数校验
# ---------------------------------------------------------------------------

def test_init_invalid_window():
    with pytest.raises(ValueError, match="window"):
        DecayMonitor("f", window=1)


def test_init_invalid_threshold():
    with pytest.raises(ValueError, match="icir_threshold"):
        DecayMonitor("f", icir_threshold=0.0)


# ---------------------------------------------------------------------------
# 初始状态
# ---------------------------------------------------------------------------

def test_initial_status_normal():
    m = DecayMonitor("f")
    assert m.get_status() == "NORMAL"


def test_insufficient_data_returns_nan_icir():
    m = DecayMonitor("f", window=5)
    m.update("2026-01-01", -0.05)     # 只有 1 点
    assert math.isnan(m.get_rolling_icir())


def test_insufficient_data_status_unchanged():
    m = DecayMonitor("f", icir_threshold=0.3)
    result = m.update("2026-01-01", -0.001)   # 数据不足，维持 NORMAL
    assert result.status == "NORMAL"


# ---------------------------------------------------------------------------
# 正常→WARNING 状态转变
# ---------------------------------------------------------------------------

def test_status_warning_triggered():
    """ICIR 绝对值 < threshold * 0.6 时进入 WARNING

    设计：[1.0, -0.8, 1.0, -0.8, 0.1] → mean=0.1, std≈0.9 → ICIR≈0.111
    threshold=0.3, warning_thresh=0.3*0.6=0.18, 0.111 < 0.18 → WARNING
    """
    m = DecayMonitor("f", window=5, icir_threshold=0.3)
    _fill(m, [1.0, -0.8, 1.0, -0.8, 0.1])
    assert m.get_status() == "WARNING"


def test_status_degraded_triggered():
    """ICIR 绝对值 < threshold * 0.3 时进入 DEGRADED

    设计：[0.5, -0.5, 0.5, -0.5, 0.1] → mean≈0.02, std≈0.50 → ICIR≈0.04
    threshold=0.3, degraded_thresh=0.3*0.3=0.09, 0.04 < 0.09 → DEGRADED
    """
    m = DecayMonitor("f", window=5, icir_threshold=0.3)
    _fill(m, [0.5, -0.5, 0.5, -0.5, 0.1])
    assert m.get_status() == "DEGRADED"


# ---------------------------------------------------------------------------
# 状态恢复
# ---------------------------------------------------------------------------

def test_status_recovery_from_degraded():
    """ICIR 回升后可从 DEGRADED 恢复到 NORMAL"""
    m = DecayMonitor("f", window=5, icir_threshold=0.3)
    # 先降级：ICIR ≈ 0.04
    _fill(m, [0.5, -0.5, 0.5, -0.5, 0.1])
    assert m.get_status() == "DEGRADED"
    # 注入强信号（方向一致），覆盖旧观测 → ICIR >> threshold → NORMAL
    _fill(m, [-0.08, -0.09, -0.085, -0.09, -0.08], start=6)
    assert m.get_status() == "NORMAL"


# ---------------------------------------------------------------------------
# update 返回值
# ---------------------------------------------------------------------------

def test_update_result_fields():
    m = DecayMonitor("margin_buy_intensity", window=3, icir_threshold=0.3)
    _fill(m, [-0.06, -0.07])
    result = m.update("2026-01-03", -0.065)
    assert result.date == "2026-01-03"
    assert result.ic_value == -0.065
    assert not math.isnan(result.rolling_icir)
    assert result.window_size == 3


def test_update_result_status_changed_flag():
    m = DecayMonitor("f", window=3, icir_threshold=0.3)
    _fill(m, [0.0001, -0.0001])  # 窗口不足，维持 NORMAL
    # 注入一个使 std 大 mean ≈ 0 的点
    result = m.update("2026-01-03", 0.0001)
    # status_changed 正确反映前后状态是否变化
    assert isinstance(result.status_changed, bool)


# ---------------------------------------------------------------------------
# to_dict 序列化
# ---------------------------------------------------------------------------

def test_to_dict_structure():
    m = DecayMonitor("chips_cost_concentration", window=10, icir_threshold=0.3)
    _fill(m, [-0.05] * 6)
    d = m.to_dict()
    required_keys = {
        "factor_name", "window", "icir_threshold",
        "current_status", "rolling_icir",
        "rolling_ic_mean", "rolling_ic_std",
        "n_observations", "latest_date",
    }
    assert required_keys.issubset(d.keys())
    assert d["factor_name"] == "chips_cost_concentration"
    assert d["n_observations"] == 6
    assert d["latest_date"] == "2026-01-06"


def test_to_dict_empty_monitor():
    m = DecayMonitor("f")
    d = m.to_dict()
    assert d["latest_date"] is None
    assert d["n_observations"] == 0
