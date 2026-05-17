"""R156 P6 因子批次 — 基本面时序衍生因子.

从已有 PIT 合并后的基本面列中, 通过时序变换提取增量信号,
补充 _add_deep_fundamental_factors 中的截面 rank.

因子清单:
  - roe_chg: ROE 季度变化 (Quality Improvement Momentum)
  - gpm_chg: 毛利率季度变化 (Margin Momentum)
  - npm_chg: 净利率季度变化 (Profitability Trend)
  - leverage_chg: 资产负债率变化 (Leverage Momentum)
  - roe_stability: ROE 滚动稳定性 (Quality Consistency)
  - growth_quality: 增长一致性 (Growth×ROE composite)
  - piotroski_lite: 简化 Piotroski F-Score (4 signals)
  - operating_efficiency: 经营效率变化 (ROA + Turnover)

依赖: 需要 _add_deep_fundamental_factors 已将 fina 列合并到 df.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

__all__ = [
    "add_p6_factors",
    "P6_FACTOR_NAMES",
    "check_p6_collinearity",
]

P6_FACTOR_NAMES: list[str] = [
    "roe_chg", "cs_rank_roe_chg",
    "gpm_chg", "cs_rank_gpm_chg",
    "npm_chg", "cs_rank_npm_chg",
    "leverage_chg", "cs_rank_leverage_chg",
    "roe_stability", "cs_rank_roe_stab",
    "growth_quality", "cs_rank_growth_q",
    "piotroski_lite", "cs_rank_piotroski",
    "operating_eff_chg", "cs_rank_op_eff_chg",
]


def add_p6_factors(df: pd.DataFrame) -> int:
    """添加 P6 基本面时序衍生因子 (inplace).

    Returns:
        新增列数.
    """
    # 前置条件: 需要 _add_deep_fundamental_factors 已将 fina 列合并到 df
    _REQUIRED_BASE = ["roe", "roa", "grossprofit_margin", "debt_to_assets"]
    present = [c for c in _REQUIRED_BASE if c in df.columns]
    if not present:
        logger.warning(
            "P6 跳过: 基础 fina 列缺失 (需先调用 _add_deep_fundamental_factors). "
            "已有列: %d, 需要: %s", len(df.columns), _REQUIRED_BASE,
        )
        return 0
    added = 0
    added += _add_roe_chg(df)
    added += _add_gpm_chg(df)
    added += _add_npm_chg(df)
    added += _add_leverage_chg(df)
    added += _add_roe_stability(df)
    added += _add_growth_quality(df)
    added += _add_piotroski_lite(df)
    added += _add_operating_eff_chg(df)
    logger.info("P6 基本面时序因子: 新增 %d 列", added)
    return added


def _add_roe_chg(df: pd.DataFrame) -> int:
    """ROE 季度变化 — Quality Improvement Momentum.

    PIT 合并后 ROE 按季更新, 取 60 日差分近似季度变化.
    """
    if "roe" not in df.columns:
        return 0
    df["roe_chg"] = df.groupby("symbol")["roe"].transform(
        lambda s: s.diff(60)
    ).clip(-50, 50)
    df["cs_rank_roe_chg"] = df.groupby("datetime")["roe_chg"].rank(pct=True)
    return 2


def _add_gpm_chg(df: pd.DataFrame) -> int:
    """毛利率季度变化 — Margin Momentum.

    60 日差分近似季度变化, clip 极值.
    """
    if "grossprofit_margin" not in df.columns:
        return 0
    df["gpm_chg"] = df.groupby("symbol")["grossprofit_margin"].transform(
        lambda s: s.diff(60)
    ).clip(-30, 30)
    df["cs_rank_gpm_chg"] = df.groupby("datetime")["gpm_chg"].rank(pct=True)
    return 2


def _add_npm_chg(df: pd.DataFrame) -> int:
    """净利率季度变化 — Profitability Trend.

    60 日差分, clip 极值.
    """
    if "netprofit_margin" not in df.columns:
        return 0
    df["npm_chg"] = df.groupby("symbol")["netprofit_margin"].transform(
        lambda s: s.diff(60)
    ).clip(-30, 30)
    df["cs_rank_npm_chg"] = df.groupby("datetime")["npm_chg"].rank(pct=True)
    return 2


def _add_leverage_chg(df: pd.DataFrame) -> int:
    """资产负债率变化 — Leverage Momentum.

    杠杆上升通常为负面信号, 60 日差分.
    """
    if "debt_to_assets" not in df.columns:
        return 0
    df["leverage_chg"] = df.groupby("symbol")["debt_to_assets"].transform(
        lambda s: s.diff(60)
    ).clip(-20, 20)
    df["cs_rank_leverage_chg"] = df.groupby("datetime")["leverage_chg"].rank(pct=True)
    return 2


def _add_roe_stability(df: pd.DataFrame) -> int:
    """ROE 滚动稳定性 — Quality Consistency.

    120 日滚动标准差, 低标准差 = 高稳定性 (负号翻转).
    """
    if "roe" not in df.columns:
        return 0
    roe_std = df.groupby("symbol")["roe"].transform(
        lambda s: s.rolling(120, min_periods=40).std()
    )
    df["roe_stability"] = -roe_std.clip(0, 30)  # 负号: 低波 = 高 rank
    df["cs_rank_roe_stab"] = df.groupby("datetime")["roe_stability"].rank(pct=True)
    return 2


def _add_growth_quality(df: pd.DataFrame) -> int:
    """增长一致性 — Growth×ROE composite.

    高增长 + 高 ROE 的交互项, 筛选质量成长股.
    依赖 dt_netprofit_yoy (净利增速) 和 roe.
    """
    if "dt_netprofit_yoy" not in df.columns or "roe" not in df.columns:
        return 0
    grow = df["dt_netprofit_yoy"].clip(-100, 300)
    roe = df["roe"].clip(-20, 60)
    # Z-score 归一化后相乘, 避免量纲影响
    g_z = df.groupby("datetime")["dt_netprofit_yoy"].transform(
        lambda s: (s - s.mean()) / (s.std() + 1e-8)
    ).clip(-3, 3)
    r_z = df.groupby("datetime")["roe"].transform(
        lambda s: (s - s.mean()) / (s.std() + 1e-8)
    ).clip(-3, 3)
    df["growth_quality"] = g_z * r_z
    df["cs_rank_growth_q"] = df.groupby("datetime")["growth_quality"].rank(pct=True)
    return 2


def _add_piotroski_lite(df: pd.DataFrame) -> int:
    """简化 Piotroski F-Score (4 signals).

    4 个二值信号求和:
      1) ROA > 0
      2) ROA 季度改善 (diff>0)
      3) 经营现金流/总资产 > 0 (用 cfps 代理)
      4) 杠杆下降 (debt_to_assets diff<0)
    """
    cols_needed = ["roa", "debt_to_assets"]
    if not all(c in df.columns for c in cols_needed):
        return 0
    score = pd.Series(0, index=df.index, dtype="int8")
    # 1) ROA > 0
    score += (df["roa"] > 0).astype("int8")
    # 2) ROA 改善
    roa_chg = df.groupby("symbol")["roa"].diff(60)
    score += (roa_chg > 0).astype("int8")
    # 3) 经营现金流正 (cfps 代理)
    if "cfps" in df.columns:
        score += (df["cfps"] > 0).astype("int8")
    # 4) 杠杆下降
    lev_chg = df.groupby("symbol")["debt_to_assets"].diff(60)
    score += (lev_chg < 0).astype("int8")
    df["piotroski_lite"] = score
    df["cs_rank_piotroski"] = df.groupby("datetime")["piotroski_lite"].rank(pct=True)
    return 2


def _add_operating_eff_chg(df: pd.DataFrame) -> int:
    """经营效率变化 — ROA + Turnover trend.

    ROA 60 日差分 + assets_turn 差分 (若存在) 的复合指标.
    """
    if "roa" not in df.columns:
        return 0
    roa_d = df.groupby("symbol")["roa"].transform(
        lambda s: s.diff(60)
    ).clip(-20, 20)
    if "assets_turn" in df.columns:
        at_d = df.groupby("symbol")["assets_turn"].transform(
            lambda s: s.diff(60)
        ).clip(-2, 2)
        # NOTE: 系数 5 为初始估计值 (turnover 变化量级 ~0.1 vs ROA ~1.0),
        # 进入正式评估前需做灵敏度测试 (×2/×10) 确认稳健性.
        _TURNOVER_AMPLIFIER = 5
        df["operating_eff_chg"] = roa_d + at_d * _TURNOVER_AMPLIFIER
    else:
        df["operating_eff_chg"] = roa_d
    df["operating_eff_chg"] = df["operating_eff_chg"].clip(-30, 30)
    df["cs_rank_op_eff_chg"] = df.groupby("datetime")["operating_eff_chg"].rank(pct=True)
    return 2


# =========================================================================
# 共线性诊断 (§原则 A: 反数据挖掘 / 因子独立性验证)
# =========================================================================

_RAW_P6_COLS: list[str] = [
    "roe_chg", "gpm_chg", "npm_chg", "leverage_chg",
    "roe_stability", "growth_quality", "piotroski_lite", "operating_eff_chg",
]

# 已知的高相关因子对 (结构性关系, 非数据挖掘)
_KNOWN_COLLINEAR_PAIRS: list[tuple[str, str, str]] = [
    ("gpm_chg", "npm_chg", "毛利率与净利率同源于收入端变化"),
    ("roe_chg", "growth_quality", "growth_quality 包含 ROE 作为输入"),
]


def check_p6_collinearity(
    df: pd.DataFrame,
    *,
    threshold: float = 0.70,
    method: str = "spearman",
) -> list[dict[str, object]]:
    """检测 P6 因子间的共线性.

    在训练/验证阶段调用, 对超过阈值的因子对发出警告.

    Args:
        df: 含 P6 因子列的 DataFrame (需先调用 add_p6_factors).
        threshold: 相关系数绝对值告警阈值 (默认 0.70).
        method: 相关性方法 ('spearman' / 'pearson').

    Returns:
        高相关因子对列表, 每项含 {col_a, col_b, corr, known_reason}.
    """
    present = [c for c in _RAW_P6_COLS if c in df.columns]
    if len(present) < 2:
        logger.info("P6 共线性检查: 可用因子 < 2, 跳过")
        return []

    corr = df[present].corr(method=method)
    alerts: list[dict[str, object]] = []

    for i, a in enumerate(present):
        for b in present[i + 1:]:
            r = corr.loc[a, b]
            if abs(r) < threshold:
                continue
            reason = ""
            for pa, pb, desc in _KNOWN_COLLINEAR_PAIRS:
                if {a, b} == {pa, pb}:
                    reason = desc
                    break
            alerts.append({"col_a": a, "col_b": b, "corr": round(r, 4),
                           "known_reason": reason})
            level = "WARNING" if not reason else "INFO"
            logger.log(
                logging.WARNING if not reason else logging.INFO,
                "P6 共线性 [%s]: %s ↔ %s ρ=%.3f%s",
                level, a, b, r,
                f" (已知: {reason})" if reason else " ⚠ 需审查",
            )

    if not alerts:
        logger.info("P6 共线性检查通过: %d 因子, 阈值 %.2f, 无高相关对",
                    len(present), threshold)
    return alerts
