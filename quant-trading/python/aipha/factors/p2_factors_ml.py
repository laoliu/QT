"""R127 P2 因子批次 — 基本面/质量维度因子.

避免 R126 IC 陷阱(CF-015): 所有因子均为基本面/质量维度,
不加载规模/流动性维度, 与 IVOL/Amihud 正交.

因子清单:
  - sue: 标准化意外盈利 (Standardized Unexpected Earnings)
  - accruals: 应计质量 (Accruals Quality, Sloan 1996)
  - cftp: 现金流收益率 (Cash Flow to Price, Lakonishok 1994)
  - roe_momentum: ROE 变化动量 (Quality Improvement)
  - turnover_stability: 换手稳定性 (机构持仓代理)
"""
from __future__ import annotations

import logging
from typing import Sequence

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

__all__ = [
    "add_p2_factors",
    "P2_FACTOR_NAMES",
]

P2_FACTOR_NAMES: list[str] = [
    "sue", "cs_rank_sue",
    "accruals", "cs_rank_accruals",
    "cftp", "cs_rank_cftp",
    "roe_momentum", "cs_rank_roe_momentum",
    "turnover_stability", "cs_rank_turnover_stability",
]


def add_p2_factors(df: pd.DataFrame) -> int:
    """向面板 DataFrame 添加 P2 因子列 (就地修改).

    Returns:
        新增因子列数.
    """
    n = 0
    n += _add_sue(df)
    n += _add_accruals(df)
    n += _add_cftp(df)
    n += _add_roe_momentum(df)
    n += _add_turnover_stability(df)
    logger.info("P2 基本面因子: 新增 %d 列", n)
    return n


def _add_sue(df: pd.DataFrame) -> int:
    """SUE: 标准化意外盈利 (Ball & Brown 1968).

    使用 dt_netprofit_yoy 作为盈利惊喜代理:
    高同比增长 → 正向盈利意外 → Post-Earnings Drift → 未来正收益.
    对 yoy 做截面标准化以消除量纲和截面分布差异.
    """
    if "dt_netprofit_yoy" not in df.columns:
        logger.warning("SUE: 缺少 dt_netprofit_yoy, 跳过")
        return 0

    added = 0
    yoy = df["dt_netprofit_yoy"].astype(np.float64)
    # Winsorize 极端值 (±200%)
    yoy = yoy.clip(-200, 200)

    # 截面标准化: (yoy - cs_mean) / cs_std
    cs_mean = df.groupby("datetime")["dt_netprofit_yoy"].transform("mean")
    cs_std = df.groupby("datetime")["dt_netprofit_yoy"].transform("std")
    cs_std = cs_std.replace(0, np.nan)
    df["sue"] = (yoy - cs_mean) / cs_std
    df["sue"] = df["sue"].clip(-3, 3)  # Z-score 截断
    added += 1

    df["cs_rank_sue"] = df.groupby("datetime")["sue"].rank(pct=True)
    added += 1
    return added


def _add_accruals(df: pd.DataFrame) -> int:
    """Accruals: 应计质量 (Sloan 1996).

    Accruals = (EPS - CFPS) / |EPS|.
    低应计(CFPS >> EPS) → 高质量 → 未来正收益.
    取负号使高质量 = 高值.
    """
    if "eps" not in df.columns or "cfps" not in df.columns:
        logger.warning("Accruals: 缺少 eps/cfps, 跳过")
        return 0

    added = 0
    eps = df["eps"].astype(np.float64)
    cfps = df["cfps"].astype(np.float64)

    # 避免除以零: 用 max(|eps|, 0.01) 标准化
    denom = eps.abs().clip(lower=0.01)
    # 负号: 低应计(现金流好) → 高因子值
    raw = -(eps - cfps) / denom
    df["accruals"] = raw.clip(-5, 5)
    added += 1

    df["cs_rank_accruals"] = df.groupby("datetime")["accruals"].rank(pct=True)
    added += 1
    return added


def _add_cftp(df: pd.DataFrame) -> int:
    """CFTP: 现金流收益率 (Lakonishok 1994).

    CFTP = CFPS / Close.
    高现金流收益率 → 价值型+高质量 → 未来正收益.
    A 股中 CFTP 比 PE 更稳健(不受非经常损益扭曲).
    """
    if "cfps" not in df.columns or "close" not in df.columns:
        logger.warning("CFTP: 缺少 cfps/close, 跳过")
        return 0

    added = 0
    cfps = df["cfps"].astype(np.float64)
    close = df["close"].astype(np.float64).clip(lower=0.1)

    raw = cfps / close
    # Winsorize at 1%/99%
    lo = raw.quantile(0.01)
    hi = raw.quantile(0.99)
    df["cftp"] = raw.clip(lo, hi)
    added += 1

    df["cs_rank_cftp"] = df.groupby("datetime")["cftp"].rank(pct=True)
    added += 1
    return added


def _add_roe_momentum(df: pd.DataFrame) -> int:
    """ROE Momentum: ROE 变化动量 (Haugen-Baker / Novy-Marx).

    ΔROE = ROE_current - ROE_lagged (约一季度前).
    ROE 改善 → 质量提升 → 未来正收益.
    使用每只股票的 ROE ffill 后做差分.
    """
    if "roe" not in df.columns:
        logger.warning("ROE Momentum: 缺少 roe, 跳过")
        return 0

    added = 0
    g = df.groupby("symbol")

    # ROE 是季频数据被 ffill 到日频, 差分捕获最近一次变化
    # 滞后约 63 个交易日 ≈ 1 个季度
    roe_lagged = g["roe"].shift(63)
    delta_roe = df["roe"].astype(np.float64) - roe_lagged
    df["roe_momentum"] = delta_roe.clip(-50, 50)
    added += 1

    df["cs_rank_roe_momentum"] = (
        df.groupby("datetime")["roe_momentum"].rank(pct=True)
    )
    added += 1
    return added


def _add_turnover_stability(df: pd.DataFrame) -> int:
    """Turnover Stability: 换手稳定性 (机构持仓代理).

    TurnStab = -CV(turnover_rate, 60d) = -std/mean.
    低换手波动 → 机构稳定持仓 → 未来正收益.
    取负号使稳定(低CV) = 高因子值.
    """
    if "turnover_rate" not in df.columns:
        logger.warning("TurnStab: 缺少 turnover_rate, 跳过")
        return 0

    added = 0
    g = df.groupby("symbol")
    window = 60

    roll_mean = g["turnover_rate"].transform(
        lambda x: x.rolling(window, min_periods=30).mean()
    )
    roll_std = g["turnover_rate"].transform(
        lambda x: x.rolling(window, min_periods=30).std()
    )
    # CV = std / mean; 取负号使低波动 = 高值
    cv = roll_std / roll_mean.replace(0, np.nan)
    df["turnover_stability"] = -cv.clip(-10, 10)
    added += 1

    df["cs_rank_turnover_stability"] = (
        df.groupby("datetime")["turnover_stability"].rank(pct=True)
    )
    added += 1
    return added
