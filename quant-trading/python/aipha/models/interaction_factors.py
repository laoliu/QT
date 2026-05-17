"""Explicit interaction features for CB ML model (Phase 3 / D2).

Generates ≤10 economically-motivated interaction terms
to complement the LightGBM ranker's implicit nonlinearity.

Design rationale (expert consensus):
  - LGB already handles interactions, but explicit ones help with
    interpretability and can reduce tree depth needed.
  - Capped at ≤10 to prevent overfitting (PBO constraint).
  - Each interaction has economic motivation documented.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def add_interaction_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add explicit interaction features to a CB feature DataFrame.

    Modifies df in-place and returns it. Missing columns are skipped.

    Interaction terms (≤10):
        1. value_momentum: cb_value * delta — stock性 × 趋势
        2. cheap_liquid: (1/cb_over_rate) * liquidity_activity — 低溢价+高流动
        3. quality_value: roe * (1/cb_over_rate) — 优质正股+低溢价
        4. iv_gamma: iv * gamma_convexity — 低IV+高凸性=最优买点
        5. growth_delta: or_yoy * delta — 成长股+股性CB
        6. spread_maturity: credit_spread * maturity_convexity — 高利差+最优期限
        7. hk_value: hk_ratio * cb_value — 外资持仓+高转股价值
        8. vol_size: turnover_rate * (1/issue_size) — 高换手+小盘
        9. iv_prem_delta: iv_prem * delta — IV溢价+股性
       10. cashflow_cheap: ocf_to_or * (1/cb_over_rate) — 现金流好+低溢价
    """
    def _safe_div(a: pd.Series, b: pd.Series) -> pd.Series:
        return a / b.replace(0, np.nan)

    def _safe_col(name: str) -> pd.Series | None:
        return df[name] if name in df.columns else None

    # 1. value_momentum
    cb_val = _safe_col("cb_value")
    delta = _safe_col("delta")
    if cb_val is not None and delta is not None:
        df["x_value_momentum"] = cb_val * delta

    # 2. cheap_liquid
    over = _safe_col("cb_over_rate")
    liq = _safe_col("liquidity_activity")
    if over is not None and liq is not None:
        df["x_cheap_liquid"] = _safe_div(liq, over.abs() + 1)

    # 3. quality_value
    roe = _safe_col("roe")
    if roe is not None and over is not None:
        df["x_quality_value"] = _safe_div(roe, over.abs() + 1)

    # 4. iv_gamma
    iv = _safe_col("iv")
    gamma = _safe_col("gamma_convexity")
    if iv is not None and gamma is not None:
        df["x_iv_gamma"] = (1 - iv.clip(0, 2)) * gamma

    # 5. growth_delta
    or_yoy = _safe_col("or_yoy")
    if or_yoy is not None and delta is not None:
        df["x_growth_delta"] = or_yoy * delta

    # 6. spread_maturity
    spread = _safe_col("credit_spread")
    mat_conv = _safe_col("maturity_convexity")
    if spread is not None and mat_conv is not None:
        df["x_spread_maturity"] = spread * mat_conv

    # 7. hk_value
    hk = _safe_col("hk_ratio")
    if hk is not None and cb_val is not None:
        df["x_hk_value"] = hk * cb_val

    # 8. vol_size
    turnover = _safe_col("turnover_rate")
    size = _safe_col("issue_size")
    if turnover is not None and size is not None:
        df["x_vol_size"] = turnover * _safe_div(pd.Series(1, index=df.index), size)

    # 9. iv_prem_delta
    iv_prem = _safe_col("iv_prem")
    if iv_prem is not None and delta is not None:
        df["x_ivprem_delta"] = iv_prem * delta

    # 10. cashflow_cheap
    ocf = _safe_col("ocf_to_or")
    if ocf is not None and over is not None:
        df["x_cashflow_cheap"] = _safe_div(ocf, over.abs() + 1)

    return df


INTERACTION_COLS = [
    "x_value_momentum", "x_cheap_liquid", "x_quality_value",
    "x_iv_gamma", "x_growth_delta", "x_spread_maturity",
    "x_hk_value", "x_vol_size", "x_ivprem_delta", "x_cashflow_cheap",
]
