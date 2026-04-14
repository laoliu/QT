"""tests/scripts/test_evaluate_oos.py — evaluate_oos 单元测试（含 T+1 折扣）"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# scripts/ 不是包，需手动加入路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))
from train_ml_factor import evaluate_oos  # noqa: E402


# ---------------------------------------------------------------------------
# 辅助：构造简单的 OOS DataFrame
# ---------------------------------------------------------------------------

def _make_oos(n_dates: int = 30, n_stocks: int = 50, seed: int = 42) -> pd.DataFrame:
    """生成模拟 OOS 数据：pred_score 与 fwd_return 有弱正相关"""
    rng = np.random.default_rng(seed)
    rows = []
    for d in range(n_dates):
        date_str = f"2024-{(d // 20 + 1):02d}-{(d % 20 + 1):02d}"
        signal = rng.standard_normal(n_stocks)
        noise = rng.standard_normal(n_stocks) * 2
        rows.extend([
            {"datetime": date_str, "code": f"00{i:04d}.SZ",
             "pred_score": signal[i], "fwd_return": signal[i] * 0.2 + noise[i] * 0.01}
            for i in range(n_stocks)
        ])
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 基础字段
# ---------------------------------------------------------------------------

def test_evaluate_oos_returns_expected_keys():
    df = _make_oos()
    result = evaluate_oos(df)
    required = {"mean_ic", "std_ic", "ic_ir", "ic_ir_t1_adjusted",
                "t1_discount_factor", "ic_positive_ratio", "n_ic_days"}
    assert required.issubset(result.keys()), f"Missing: {required - result.keys()}"


def test_default_t1_discount_factor():
    df = _make_oos()
    result = evaluate_oos(df)
    assert result["t1_discount_factor"] == 0.90


def test_t1_adjusted_equals_ic_ir_times_factor():
    df = _make_oos()
    result = evaluate_oos(df, t1_discount_factor=0.85)
    expected = round(result["ic_ir"] * 0.85, 4)
    assert result["ic_ir_t1_adjusted"] == expected


def test_custom_t1_factor_respected():
    df = _make_oos()
    r1 = evaluate_oos(df, t1_discount_factor=1.0)
    r2 = evaluate_oos(df, t1_discount_factor=0.85)
    assert abs(r1["ic_ir_t1_adjusted"] - r1["ic_ir"]) < 1e-6   # factor=1 → no change
    assert r2["ic_ir_t1_adjusted"] < r2["ic_ir"]               # factor<1 → smaller


# ---------------------------------------------------------------------------
# 缺失列 / 容错
# ---------------------------------------------------------------------------

def test_missing_columns_returns_error():
    df = pd.DataFrame({"datetime": ["2024-01-01"], "code": ["000001.SZ"]})
    result = evaluate_oos(df)
    assert "error" in result


# ---------------------------------------------------------------------------
# T+1 折扣对高 ICIR 策略的影响验证
# ---------------------------------------------------------------------------

def test_t1_adjusted_reduces_icir():
    """任何 ic_ir > 0 情况下，t1_adjusted 必定小于原始 ic_ir"""
    df = _make_oos(seed=7)
    result = evaluate_oos(df, t1_discount_factor=0.90)
    if result["ic_ir"] > 0:
        assert result["ic_ir_t1_adjusted"] < result["ic_ir"]
    elif result["ic_ir"] < 0:
        assert result["ic_ir_t1_adjusted"] > result["ic_ir"]  # 负值折扣后绝对值更小
