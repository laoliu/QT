"""CB 转股价值分解因子 (Phase C / C-1a).

将转债价格分解为 bond floor + option value，提取：
    bond_premium_rate  — 纯债溢价率 = (price - bond_floor) / bond_floor
    option_value_ratio — 期权价值占比 = (price - bond_floor) / price
    delta_approx       — BSM Delta 近似 (正股敏感度)
    parity_premium     — 平价溢价率 = (price - conv_value) / conv_value
    conv_value_ratio   — 转股价值 / 价格 = 平价比

注意：cb_value / cb_over_rate 已由 TuShare 提供，此模块补充
bond-floor 侧分解 + Delta，完善双低因子精细化。

引用：
    - cb_iv_greeks.py — bond_floor, bsm_delta
    - cb_ytm.py — bond_floor_pv
"""
from __future__ import annotations

import math
from datetime import datetime

import numpy as np
import pandas as pd

from aipha.factors.cb_iv_greeks import (
    DEFAULT_CREDIT_SPREAD,
    DEFAULT_DIVIDEND_YIELD,
    DEFAULT_RISK_FREE_RATE,
    bond_floor,
    bsm_delta,
    historical_vol,
)
from aipha.factors.cb_ytm import DEFAULT_COUPON_SCHEDULE, DEFAULT_COMPENSATION


# ---------------------------------------------------------------------------
# 行级分解
# ---------------------------------------------------------------------------
def decompose_row(
    close: float,
    conv_price: float | None,
    stock_price: float | None,
    maturity_date_str: str | None,
    trade_date_str: str,
    stock_vol: float | None = None,
    r: float = DEFAULT_RISK_FREE_RATE,
    spread: float = DEFAULT_CREDIT_SPREAD,
) -> dict[str, float | None]:
    """单行转债价格分解。

    Returns dict with keys:
        bond_premium_rate, option_value_ratio, delta_approx,
        parity_premium, conv_value_ratio, bond_floor_value
    """
    result: dict[str, float | None] = {
        "bond_premium_rate": None,
        "option_value_ratio": None,
        "delta_approx": None,
        "parity_premium": None,
        "conv_value_ratio": None,
        "bond_floor_value": None,
    }
    if close <= 0 or not maturity_date_str:
        return result

    # 年限
    try:
        td = datetime.strptime(trade_date_str[:8], "%Y%m%d")
        md = datetime.strptime(maturity_date_str[:8], "%Y%m%d")
    except (ValueError, TypeError):
        return result
    T = (md - td).days / 365.25
    if T <= 0.05:
        return result

    # Bond floor
    bf = bond_floor(T, r, spread)
    result["bond_floor_value"] = bf
    if bf > 0:
        result["bond_premium_rate"] = (close - bf) / bf
        result["option_value_ratio"] = max(0.0, (close - bf) / close)

    # 转股价值 + 平价溢价率
    if conv_price and conv_price > 0 and stock_price and stock_price > 0:
        conv_value = 100.0 / conv_price * stock_price
        if conv_value > 0:
            result["parity_premium"] = (close - conv_value) / conv_value
            result["conv_value_ratio"] = conv_value / close

        # Delta 近似
        sigma = stock_vol if stock_vol and stock_vol > 0.01 else 0.30
        try:
            R = 100.0 / conv_price
            delta = R * bsm_delta(stock_price, conv_price, T, r, sigma)
            result["delta_approx"] = delta
        except Exception:
            pass

    return result


# ---------------------------------------------------------------------------
# 批量因子构建
# ---------------------------------------------------------------------------
def build_decomposition_factors(
    df: pd.DataFrame,
    close_col: str = "close",
    conv_price_col: str = "conv_price",
    stock_price_col: str = "stock_close",
    maturity_col: str = "maturity_date",
    date_col: str = "trade_date",
    stock_vol_col: str | None = "stock_vol_20d",
) -> pd.DataFrame:
    """批量计算转股价值分解因子。

    Args:
        df: CB daily DataFrame，需包含 close, conv_price, stock_close 等列。
        stock_vol_col: 正股波动率列名。None 则使用默认 30%。

    Returns:
        仅含新因子列的 DataFrame，index 与输入对齐。
    """
    records: list[dict[str, float | None]] = []
    for _, row in df.iterrows():
        sv = float(row.get(stock_vol_col, 0.0)) if stock_vol_col and stock_vol_col in df.columns else None
        r = decompose_row(
            close=float(row.get(close_col, 0)),
            conv_price=_safe_float(row.get(conv_price_col)),
            stock_price=_safe_float(row.get(stock_price_col)),
            maturity_date_str=str(row.get(maturity_col, "")),
            trade_date_str=str(row.get(date_col, "")),
            stock_vol=sv,
        )
        records.append(r)
    return pd.DataFrame(records, index=df.index)


def _safe_float(v) -> float | None:
    """安全转 float，None/NaN → None。"""
    if v is None:
        return None
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (ValueError, TypeError):
        return None
