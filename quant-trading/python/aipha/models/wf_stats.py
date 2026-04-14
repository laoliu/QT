"""
aipha/models/wf_stats.py
——————————————————————————————
Walk-Forward 训练后统计验证辅助函数。

计算 DSR（Deflated Sharpe Ratio）、HLZ 校正 t 统计量、PBO（过拟合概率），
结果写入训练报告 JSON 与 result_store。

理论依据
--------
- DSR: Bailey & López de Prado (2014) "The Deflated Sharpe Ratio"
  校正公式（无需参数扫描）：
  SR* = SR_hat × √((T-1)/T) × [1 − (γ₃/6)·SR + (γ₄−3)/24·SR²]⁻¹
  其中 γ₃ = 样本偏度，γ₄ = 样本峰度，T = 观测数

  多重检验校正: DSR_pvalue 依据分布 SR* < SR_reference (default 0)
  实用简化: DSR = SR / √(1 + (−γ₃/6)·SR + (γ₄−3)/24·SR²) × √((T−1)/T)

- HLZ t 校正: Harvey-Liu-Zhu (2016)
  t_adj = t_obs − √(2·ln(N))  其中 N = 测试策略数

- PBO: 从 WF 折 OOS IC 序列构造多策略矩阵，传入 pbo.py 框架
  每折 OOS 视为一个独立策略，PBO < 0.5 为合格线
"""
from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 默认参照 Sharpe（用于 DSR 计算基准）
_DEFAULT_SR_REFERENCE: float = 0.0


def compute_dsr(
    ic_series: pd.Series,
    n_trials: int = 1,
    sr_reference: float = _DEFAULT_SR_REFERENCE,
) -> dict[str, float]:
    """
    计算 Deflated Sharpe Ratio（DSR）。

    Bailey & López de Prado (2014) 公式：
        SR = mean(IC) / std(IC)       ← 样本 SR（年化前）
        SR_deflated = SR × √((T−1)/T) × correction(skew, kurt)

    多重检验校正（简化版）：使用 N 次策略比较的 Bonferroni 校正等效
        t_obs = SR × √T
        t_critical(N) = √(2·ln(N))    ← HLZ 近似

    Parameters
    ----------
    ic_series : pd.Series
        每日 IC 值序列（样本内或 OOS 均可）。
    n_trials : int
        策略/参数组合搜索次数（用于多重检验校正）。
    sr_reference : float
        参照 Sharpe（默认 0，即判断是否显著优于零）。

    Returns
    -------
    dict with keys: dsr, raw_sr, skewness, kurtosis, n_obs, n_trials
    """
    ic_clean = ic_series.dropna()
    T = len(ic_clean)
    if T < 4:
        return {"dsr": float("nan"), "raw_sr": float("nan"),
                "skewness": 0.0, "kurtosis": 3.0, "n_obs": T, "n_trials": n_trials}

    mu = float(ic_clean.mean())
    sigma = float(ic_clean.std(ddof=1))
    if sigma < 1e-12:
        return {"dsr": 0.0, "raw_sr": 0.0,
                "skewness": 0.0, "kurtosis": 3.0, "n_obs": T, "n_trials": n_trials}

    raw_sr = (mu - sr_reference) / sigma

    # 样本偏度和超出峰度
    gamma3 = float(ic_clean.skew())
    gamma4 = float(ic_clean.kurtosis()) + 3.0  # pandas 返回超出峰度，加3得完整峰度

    # 方差校正分母（Bailey 式 7）
    # var_sr = (1/T) × (1 − γ₃·SR + (γ₄−1)/4·SR²)
    var_sr = (1.0 / T) * (
        1.0 - gamma3 * raw_sr + ((gamma4 - 1.0) / 4.0) * raw_sr ** 2
    )
    if var_sr <= 0.0:
        var_sr = 1.0 / T  # fallback

    # DSR = (SR − SR_ref) / √var_sr  — normalised
    dsr = raw_sr / math.sqrt(var_sr * T)

    # 多重检验折扣（适度保守：减去 √(2·ln(N))/√T）
    if n_trials > 1:
        hlz_penalty = math.sqrt(2.0 * math.log(n_trials)) / math.sqrt(T)
        dsr = dsr - hlz_penalty

    return {
        "dsr": round(dsr, 4),
        "raw_sr": round(raw_sr, 4),
        "skewness": round(gamma3, 4),
        "kurtosis": round(gamma4, 4),
        "n_obs": T,
        "n_trials": n_trials,
    }


def compute_hlz_adjusted_t(
    ic_series: pd.Series,
    n_trials: int,
) -> float:
    """
    HLZ 校正 t 统计量。

    t_obs = mean(IC) / (std(IC) / √T)
    t_adj = t_obs − √(2·ln(N))

    Parameters
    ----------
    ic_series : pd.Series
    n_trials : int
        测试策略总数 N。

    Returns
    -------
    float
        校正后 t 统计量；>2.0 视为显著。
    """
    ic_clean = ic_series.dropna()
    T = len(ic_clean)
    if T < 2 or n_trials < 1:
        return float("nan")

    sigma = float(ic_clean.std(ddof=1))
    if sigma < 1e-12:
        return 0.0

    t_obs = float(ic_clean.mean()) / sigma * math.sqrt(T)
    correction = math.sqrt(2.0 * math.log(max(n_trials, 1)))
    return round(t_obs - correction, 4)


def compute_pbo_from_wf_folds(
    oos_df: pd.DataFrame,
    fold_date_ranges: list[tuple[str, str]],
) -> float:
    """
    从 WF 折 OOS 日 IC 序列构造多策略矩阵，计算 PBO。

    每折的 OOS 日 IC 序列视为独立"策略"。若折数 < 4 则返回 nan。

    Parameters
    ----------
    oos_df : pd.DataFrame
        OOS 预测 DataFrame，含 datetime/pred_score/fwd_return 列。
    fold_date_ranges : list[tuple[str, str]]
        每折 (oos_start, oos_end) 日期字符串对。

    Returns
    -------
    float
        PBO 值；nan 表示数据不足。
    """
    from aipha.reporting.pbo import PBOConfig, compute_pbo

    if len(fold_date_ranges) < 4:
        logger.warning("WF 折数 < 4，无法计算 PBO，返回 nan")
        return float("nan")

    if "pred_score" not in oos_df.columns or "fwd_return" not in oos_df.columns:
        return float("nan")

    # 每折 OOS 日 IC 序列
    fold_ic_series: list[pd.Series] = []
    for oos_start, oos_end in fold_date_ranges:
        mask = (oos_df["datetime"] >= oos_start) & (oos_df["datetime"] <= oos_end)
        fold_df = oos_df.loc[mask]
        if len(fold_df) < 5:
            continue
        daily_ic = fold_df.groupby("datetime", group_keys=False).apply(
            lambda g: g["pred_score"].corr(g["fwd_return"], method="spearman"),
            include_groups=False,
        )
        fold_ic_series.append(daily_ic.dropna())

    if len(fold_ic_series) < 4:
        return float("nan")

    # 对齐长度（使用最短折）
    min_len = min(len(s) for s in fold_ic_series)
    if min_len < 4:
        return float("nan")

    matrix = np.column_stack([s.values[:min_len] for s in fold_ic_series])

    try:
        cfg = PBOConfig(num_subsamples=min(16, (min_len // 2) * 2))
        result = compute_pbo(matrix, cfg)
        return round(result.pbo, 4)
    except Exception as exc:
        logger.warning("PBO 计算失败: %s", exc)
        return float("nan")


def compute_wf_statistical_summary(
    oos_df: pd.DataFrame,
    wf_summary: dict[str, Any],
    n_trials: int = 1,
) -> dict[str, float]:
    """
    整合 DSR/HLZ/PBO 计算，返回统计摘要。

    Parameters
    ----------
    oos_df : pd.DataFrame
        OOS 预测 DataFrame。
    wf_summary : dict
        `selector.get_wf_summary()` 返回值（含 folds 列表）。
    n_trials : int
        等效测试配置数（用于多重检验校正）。

    Returns
    -------
    dict with keys: dsr, hlz_adjusted_t, pbo, n_obs, n_trials
    """
    # 1) 计算每日 IC 序列
    if "pred_score" not in oos_df.columns or "fwd_return" not in oos_df.columns:
        return {"dsr": float("nan"), "hlz_adjusted_t": float("nan"),
                "pbo": float("nan"), "n_obs": 0, "n_trials": n_trials}

    daily_ic: pd.Series = oos_df.groupby("datetime", group_keys=False).apply(
        lambda g: g["pred_score"].corr(g["fwd_return"], method="spearman"),
        include_groups=False,
    ).dropna()

    # 2) DSR
    dsr_result = compute_dsr(daily_ic, n_trials=n_trials)

    # 3) HLZ
    hlz_t = compute_hlz_adjusted_t(daily_ic, n_trials=n_trials)

    # 4) PBO（从折日期范围）
    folds = wf_summary.get("folds", [])
    fold_ranges: list[tuple[str, str]] = []
    for fold in folds:
        oos_start = fold.get("oos_start") or fold.get("test_start")
        oos_end = fold.get("oos_end") or fold.get("test_end")
        if oos_start and oos_end:
            fold_ranges.append((str(oos_start), str(oos_end)))

    pbo = compute_pbo_from_wf_folds(oos_df, fold_ranges)

    return {
        "dsr": dsr_result["dsr"],
        "hlz_adjusted_t": hlz_t,
        "pbo": pbo,
        "n_obs": dsr_result["n_obs"],
        "n_trials": n_trials,
    }
