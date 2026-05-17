"""CB Yield-to-Maturity (YTM) 精确计算模块 (Phase C / C-1b).

基于转债现金流（票息 + 到期补偿 + 面值）的数值求解，替代 cb_factors.py
中的近似 YTM ((par/close)^(1/T) - 1) —— 后者忽略了票息。

核心函数：
    ytm_solve()       — 单只转债 YTM 数值求解 (Brent method)
    bond_floor_pv()   — 纯债价值 PV (与 cb_iv_greeks.bond_floor 一致)
    compute_ytm_row() — 用于 DataFrame.apply 的行级计算
    build_ytm_factors() — 批量计算 YTM 系列因子

衍生因子：
    ytm              — 到期收益率 (年化)
    bond_floor_ratio — 纯债价值 / 市场价, >1 表示债底保护充足
    credit_spread_precise — 精确 YTM - 无风险利率
    ytm_momentum_20d — 20 日 YTM 变动 (yield compression = 正信号)

参考：
    - docs/knowledge/china_market/convertible_bond_knowledge.md §2
    - cb_iv_greeks.py::bond_floor() (共享票息框架)
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
from scipy.optimize import brentq

# ---------------------------------------------------------------------------
# Constants (与 cb_iv_greeks.py 保持一致)
# ---------------------------------------------------------------------------
DEFAULT_COUPON_SCHEDULE: list[float] = [0.003, 0.005, 0.010, 0.015, 0.018, 0.020]
DEFAULT_COMPENSATION: float = 0.006
DEFAULT_RISK_FREE_RATE: float = 0.02
DEFAULT_PAR_VALUE: float = 100.0

_YTM_MIN: float = -0.20   # 允许负 YTM (高溢价债)
_YTM_MAX: float = 0.50    # 50% 年化上限
_YTM_TOL: float = 1e-8
_YTM_MAX_ITER: int = 100


# ---------------------------------------------------------------------------
# Core: 现金流 PV 计算
# ---------------------------------------------------------------------------
def _cashflow_pv(
    ytm: float,
    T: float,
    coupons: list[float],
    compensation: float,
    face: float,
) -> float:
    """计算给定折现率 ytm 下的现金流现值。"""
    if T <= 0:
        return face
    n_years = max(1, int(math.ceil(T)))
    pv = 0.0
    for i in range(n_years):
        yr = i + 1
        coupon_rate = coupons[min(i, len(coupons) - 1)]
        cf = face * coupon_rate
        if yr == n_years:
            cf += face * (1.0 + compensation)
        t_i = min(yr, T)
        denom = (1.0 + ytm) ** t_i
        if denom > 0:
            pv += cf / denom
    return pv


# ---------------------------------------------------------------------------
# Core: YTM 数值求解
# ---------------------------------------------------------------------------
def ytm_solve(
    market_price: float,
    T: float,
    coupons: list[float] | None = None,
    compensation: float = DEFAULT_COMPENSATION,
    face: float = DEFAULT_PAR_VALUE,
) -> float | None:
    """数值求解到期收益率 (YTM)。

    找到 y 使得 _cashflow_pv(y, T, ...) == market_price。

    Args:
        market_price: 转债市场价格。
        T: 剩余到期年限。
        coupons: 年度票息率列表。None 使用默认渐进票息。
        compensation: 到期补偿利率。
        face: 面值。

    Returns:
        年化 YTM，求解失败返回 None。
    """
    if market_price <= 0 or T <= 0.01:
        return None
    if coupons is None:
        coupons = DEFAULT_COUPON_SCHEDULE

    def f(y: float) -> float:
        return _cashflow_pv(y, T, coupons, compensation, face) - market_price

    try:
        return float(brentq(f, _YTM_MIN, _YTM_MAX, xtol=_YTM_TOL, maxiter=_YTM_MAX_ITER))
    except (ValueError, RuntimeError):
        return None


# ---------------------------------------------------------------------------
# bond_floor_pv — 纯债价值 (与 cb_iv_greeks.bond_floor 一致)
# ---------------------------------------------------------------------------
def bond_floor_pv(
    T: float,
    discount_rate: float = DEFAULT_RISK_FREE_RATE + 0.015,
    coupons: list[float] | None = None,
    compensation: float = DEFAULT_COMPENSATION,
    face: float = DEFAULT_PAR_VALUE,
) -> float:
    """计算纯债价值 (bond floor)。

    等价于 cb_iv_greeks.bond_floor()，使用同一现金流框架。
    """
    if coupons is None:
        coupons = DEFAULT_COUPON_SCHEDULE
    return _cashflow_pv(discount_rate, T, coupons, compensation, face)


# ---------------------------------------------------------------------------
# 行级计算 (用于 DataFrame.apply)
# ---------------------------------------------------------------------------
def compute_ytm_row(
    close: float,
    maturity_date_str: str | None,
    trade_date_str: str,
    coupons: list[float] | None = None,
    compensation: float = DEFAULT_COMPENSATION,
    face: float = DEFAULT_PAR_VALUE,
) -> dict[str, float | None]:
    """计算单行的 YTM + bond_floor_ratio + credit_spread。"""
    result: dict[str, float | None] = {
        "ytm": None,
        "bond_floor_ratio": None,
        "credit_spread_precise": None,
    }
    if not maturity_date_str or close <= 0:
        return result

    try:
        td = datetime.strptime(trade_date_str[:8], "%Y%m%d")
        md = datetime.strptime(maturity_date_str[:8], "%Y%m%d")
    except (ValueError, TypeError):
        return result

    T = (md - td).days / 365.25
    if T <= 0.05:
        return result

    ytm_val = ytm_solve(close, T, coupons, compensation, face)
    result["ytm"] = ytm_val

    bf = bond_floor_pv(T, coupons=coupons, compensation=compensation, face=face)
    result["bond_floor_ratio"] = bf / close if close > 0 else None

    if ytm_val is not None:
        result["credit_spread_precise"] = ytm_val - DEFAULT_RISK_FREE_RATE

    return result


# ---------------------------------------------------------------------------
# 批量因子构建
# ---------------------------------------------------------------------------
def build_ytm_factors(
    df: pd.DataFrame,
    close_col: str = "close",
    maturity_col: str = "maturity_date",
    date_col: str = "trade_date",
    momentum_window: int = 20,
) -> pd.DataFrame:
    """批量计算 YTM 系列因子，返回新列 DataFrame。

    输入 df 需包含 close_col, maturity_col, date_col 列。
    输出列: ytm, bond_floor_ratio, credit_spread_precise, ytm_momentum_{window}d

    Args:
        df: 含行情数据的 DataFrame (需含 ts_code 列用于分组)。
        close_col: 收盘价列名。
        maturity_col: 到期日列名 (YYYYMMDD 字符串)。
        date_col: 交易日列名 (YYYYMMDD 字符串)。
        momentum_window: YTM 动量窗口 (交易日)。

    Returns:
        仅含新因子列的 DataFrame，index 与输入对齐。
    """
    ytm_vals: list[float | None] = []
    bf_ratios: list[float | None] = []
    cs_vals: list[float | None] = []

    for _, row in df.iterrows():
        r = compute_ytm_row(
            close=float(row.get(close_col, 0)),
            maturity_date_str=str(row.get(maturity_col, "")),
            trade_date_str=str(row.get(date_col, "")),
        )
        ytm_vals.append(r["ytm"])
        bf_ratios.append(r["bond_floor_ratio"])
        cs_vals.append(r["credit_spread_precise"])

    out = pd.DataFrame(index=df.index)
    out["ytm"] = ytm_vals
    out["bond_floor_ratio"] = bf_ratios
    out["credit_spread_precise"] = cs_vals

    # YTM momentum: 按标的分组计算差值
    mom_col = f"ytm_momentum_{momentum_window}d"
    if "ts_code" in df.columns:
        ytm_s = pd.Series(ytm_vals, index=df.index, dtype=float)
        out[mom_col] = ytm_s.groupby(df["ts_code"]).diff(momentum_window)
    else:
        out[mom_col] = pd.Series(ytm_vals, index=df.index, dtype=float).diff(momentum_window)

    return out
