"""P5 价值投资因子 — ML 训练管线兼容版本.

R96 价值投资因子系统性评估。灵感来自:
- 007南大: Lynch参数(g+d)/PE, DCF锚定, 股息率, 低杠杆, OCF质量
- 管我财: 低估逆向, ROE质量筛选, 应计质量

IC评估结果 (20d forward, CSI2000域):
  - reversal_60d: IC=6.7%, ICIR=0.447 (管我财-逆向)
  - composite_value: IC=5.4%, ICIR=0.341 (组合估值)
  - ep: IC=4.6%, ICIR=0.283 (007-E/P)
  - lynch_param: IC=2.4%, ICIR=0.259 (007-Lynch)
  - dv_yield: IC=2.8%, ICIR=0.251 (007-股息率)
  - garp_score: IC=2.8%, ICIR=0.195 (007-GARP)
  - piotroski_lite: IC=0.6%, ICIR=0.070

注意: 单因子回测显示排除2015后alpha极弱(AR<6%),
价值因子适合作为ML特征而非独立选股信号。
"""

from __future__ import annotations

import logging
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

__all__ = [
    "add_p5_value_factors",
    "P5_FACTOR_NAMES",
]

P5_FACTOR_NAMES: list[str] = [
    "v_ep", "cs_rank_v_ep",
    "v_lynch", "cs_rank_v_lynch",
    "v_dv_yield", "cs_rank_v_dv_yield",
    "v_ocf_quality", "cs_rank_v_ocf_quality",
    "v_low_leverage", "cs_rank_v_low_leverage",
    "v_reversal_60d", "cs_rank_v_reversal_60d",
    "v_composite", "cs_rank_v_composite",
    "v_garp", "cs_rank_v_garp",
]


def add_p5_value_factors(df: pd.DataFrame) -> int:
    """向面板 DataFrame 添加价值投资因子 (就地修改).

    需要: datetime, symbol, close, pe_ttm, pb, dv_ttm 等列。
    基本面列缺失时跳过对应因子。

    Returns:
        新增因子列数。
    """
    added = 0
    added += _add_ep(df)
    added += _add_lynch_param(df)
    added += _add_dv_yield(df)
    added += _add_ocf_quality(df)
    added += _add_low_leverage(df)
    added += _add_reversal_60d(df)
    added += _add_composite_value(df)
    added += _add_garp_score(df)
    logger.info("P5 价值因子: 新增 %d 列", added)
    return added


def _cs_rank(df: pd.DataFrame, col: str) -> pd.Series:
    """截面百分位排名 (per datetime)."""
    return df.groupby("datetime")[col].rank(pct=True)


def _add_ep(df: pd.DataFrame) -> int:
    """E/P = 1/PE_TTM. 高 E/P = 低估值."""
    if "pe_ttm" not in df.columns:
        return 0
    pe = df["pe_ttm"].replace(0, np.nan)
    df["v_ep"] = (1.0 / pe).clip(-1, 1)
    df["cs_rank_v_ep"] = _cs_rank(df, "v_ep")
    return 2


def _add_lynch_param(df: pd.DataFrame) -> int:
    """Lynch参数 = (g + d) / PE. g=净利润增长率, d=股息率."""
    need = ["pe_ttm", "dt_netprofit_yoy"]
    if not all(c in df.columns for c in need):
        return 0
    pe = df["pe_ttm"].clip(1, None)
    g = df["dt_netprofit_yoy"].clip(-100, 500)
    d = df.get("dv_ttm", pd.Series(0, index=df.index))
    df["v_lynch"] = ((g + d) / pe).clip(-5, 10)
    df["cs_rank_v_lynch"] = _cs_rank(df, "v_lynch")
    return 2


def _add_dv_yield(df: pd.DataFrame) -> int:
    """股息率因子 (dv_ttm, 已存在则直接排名)."""
    col = "dv_ttm" if "dv_ttm" in df.columns else "dv_ratio"
    if col not in df.columns:
        return 0
    df["v_dv_yield"] = df[col].fillna(0).clip(0, 30)
    df["cs_rank_v_dv_yield"] = _cs_rank(df, "v_dv_yield")
    return 2


def _add_ocf_quality(df: pd.DataFrame) -> int:
    """OCF/NI 经营现金流质量. 需要 fina 的 cfps/eps."""
    if "cfps" not in df.columns or "eps" not in df.columns:
        return 0
    eps_safe = df["eps"].abs().clip(0.01)
    df["v_ocf_quality"] = (df["cfps"] / eps_safe).clip(-5, 5)
    df["cs_rank_v_ocf_quality"] = _cs_rank(df, "v_ocf_quality")
    return 2


def _add_low_leverage(df: pd.DataFrame) -> int:
    """低杠杆 = 1 - debt_to_assets/100."""
    if "debt_to_assets" not in df.columns:
        return 0
    df["v_low_leverage"] = 1.0 - df["debt_to_assets"].clip(0, 100) / 100.0
    df["cs_rank_v_low_leverage"] = _cs_rank(df, "v_low_leverage")
    return 2


def _add_reversal_60d(df: pd.DataFrame) -> int:
    """60日反转 = -past_60d_return (管我财: 逆向投资)."""
    if "close" not in df.columns:
        return 0
    ret60 = df.groupby("symbol")["close"].pct_change(60)
    df["v_reversal_60d"] = -ret60.clip(-0.9, 5.0)
    df["cs_rank_v_reversal_60d"] = _cs_rank(df, "v_reversal_60d")
    return 2


def _add_composite_value(df: pd.DataFrame) -> int:
    """综合价值 = mean(rank(EP), rank(BP), rank(DV))."""
    parts = []
    if "pe_ttm" in df.columns:
        ep = (1.0 / df["pe_ttm"].replace(0, np.nan)).clip(-1, 1)
        parts.append(df.groupby("datetime")[ep.name if hasattr(ep, 'name') else 0].rank(pct=True)
                     if False else ep.groupby(df["datetime"]).rank(pct=True))
    if "pb" in df.columns:
        bp = (1.0 / df["pb"].replace(0, np.nan)).clip(-1, 5)
        parts.append(bp.groupby(df["datetime"]).rank(pct=True))
    if "dv_ttm" in df.columns:
        parts.append(df["dv_ttm"].fillna(0).groupby(df["datetime"]).rank(pct=True))
    if len(parts) < 2:
        return 0
    stacked = pd.concat(parts, axis=1)
    df["v_composite"] = stacked.mean(axis=1)
    df["cs_rank_v_composite"] = _cs_rank(df, "v_composite")
    return 2


def _add_garp_score(df: pd.DataFrame) -> int:
    """GARP = rank(growth) + rank(value). 007核心策略."""
    need = ["pe_ttm", "dt_netprofit_yoy"]
    if not all(c in df.columns for c in need):
        return 0
    ep = (1.0 / df["pe_ttm"].replace(0, np.nan)).clip(-1, 1)
    ep_rank = ep.groupby(df["datetime"]).rank(pct=True)
    g = df["dt_netprofit_yoy"].clip(-100, 500)
    g_rank = g.groupby(df["datetime"]).rank(pct=True)
    df["v_garp"] = (ep_rank + g_rank) / 2.0
    df["cs_rank_v_garp"] = _cs_rank(df, "v_garp")
    return 2
