"""Walk-Forward 统计诊断工具。

将每折 OOS 日收益序列汇总为一份轻量级统计验证摘要，用于识别：
- 多次试错后 Sharpe 是否仍显著（DSR）
- 多重检验惩罚后是否仍有统计优势（HLZ）
- OOS 在不同折上的稳定性与潜在过拟合风险（PBO proxy）

该模块刻意保持输入简单：只要求每折的 OOS 日收益序列。
这样既能复用于配置驱动回测，也能接在更复杂的训练/筛选流程后面。
"""
from __future__ import annotations

from typing import Any, Sequence

import numpy as np

from aipha.engines.statistical_validator import StatisticalValidator
from aipha.reporting.pbo import PBOConfig, compute_pbo
from aipha.utils.metrics import compute_sharpe_ratio


def summarize_wf_statistical_validation(
    fold_daily_returns: Sequence[Sequence[float]],
    *,
    n_trials: int = 1,
) -> dict[str, Any]:
    """对 Walk-Forward 的 OOS 日收益做统计诊断。

    Args:
        fold_daily_returns: 每折 OOS 日收益序列列表。
        n_trials: 等效试错次数。当前若未知，可先传入折数或人工估计值。

    Returns:
        适合直接序列化到 JSON 的摘要字典。
    """
    cleaned_folds = _clean_fold_returns(fold_daily_returns)
    summary = _empty_summary(n_trials=n_trials, n_folds=len(cleaned_folds))

    if not cleaned_folds:
        summary["reason"] = "no_valid_fold_returns"
        return summary

    flat_returns = np.concatenate(cleaned_folds)
    summary["n_obs"] = int(len(flat_returns))

    fold_sharpes = [float(compute_sharpe_ratio(arr)) for arr in cleaned_folds]
    summary["fold_sharpe_mean"] = round(float(np.mean(fold_sharpes)), 4)
    summary["fold_sharpe_std"] = round(float(np.std(fold_sharpes, ddof=1)), 4) if len(fold_sharpes) > 1 else 0.0
    summary["fold_sharpe_min"] = round(float(np.min(fold_sharpes)), 4)
    summary["negative_fold_ratio"] = round(
        float(sum(1 for sr in fold_sharpes if sr <= 0) / len(fold_sharpes)),
        4,
    )

    if len(flat_returns) < 20:
        summary["reason"] = "insufficient_oos_observations"
        return summary

    validator = StatisticalValidator(
        daily_returns=flat_returns.tolist(),
        n_trials=max(int(n_trials), 1),
    )
    dsr_result = validator.run_dsr()
    hlz_result = validator.run_hlz()

    summary["observed_sharpe"] = round(float(validator.observed_sr), 4)
    summary["dsr"] = float(dsr_result.deflated_sr)
    summary["dsr_p_value"] = float(dsr_result.p_value)
    summary["dsr_significant"] = bool(dsr_result.significant)
    summary["hlz_adjusted_t"] = float(hlz_result.t_adjusted)
    summary["hlz_significant"] = bool(hlz_result.significant)

    pbo_value, pbo_method = _compute_pbo_proxy(cleaned_folds, validator)
    summary["pbo"] = pbo_value
    summary["pbo_method"] = pbo_method
    summary["pbo_significant"] = pbo_value is not None and pbo_value < 0.50

    summary["passes_statistical_gate"] = bool(
        summary["dsr_significant"]
        and summary["hlz_significant"]
        and summary["pbo_significant"]
    )
    return summary


def _clean_fold_returns(
    fold_daily_returns: Sequence[Sequence[float]],
) -> list[np.ndarray]:
    """过滤空值/异常值，并转成 numpy 数组。"""
    cleaned: list[np.ndarray] = []
    for fold in fold_daily_returns:
        if not fold:
            continue
        arr = np.asarray(fold, dtype=float)
        arr = arr[np.isfinite(arr)]
        if len(arr) >= 2:
            cleaned.append(arr)
    return cleaned


def _compute_pbo_proxy(
    cleaned_folds: Sequence[np.ndarray],
    validator: StatisticalValidator,
) -> tuple[float | None, str | None]:
    """优先使用折矩阵 PBO；不足时退化为单序列 CSCV 近似。"""
    if len(cleaned_folds) >= 4:
        min_len = min(len(arr) for arr in cleaned_folds)
        if min_len >= 20:
            try:
                matrix = np.column_stack([arr[:min_len] for arr in cleaned_folds])
                num_subsamples = min(8, max(4, min_len // 5))
                if num_subsamples % 2 != 0:
                    num_subsamples -= 1
                num_subsamples = max(4, num_subsamples)
                pbo_result = compute_pbo(
                    matrix,
                    PBOConfig(
                        num_subsamples=num_subsamples,
                        performance_metric="sharpe",
                    ),
                )
                return round(float(pbo_result.pbo), 4), "fold_matrix"
            except Exception:
                pass

    fallback = validator.run_pbo()
    return float(fallback.pbo), "single_series"


def _empty_summary(
    *,
    n_trials: int,
    n_folds: int,
) -> dict[str, Any]:
    """生成空摘要，便于统一序列化。"""
    return {
        "observed_sharpe": None,
        "dsr": None,
        "dsr_p_value": None,
        "dsr_significant": False,
        "hlz_adjusted_t": None,
        "hlz_significant": False,
        "pbo": None,
        "pbo_method": None,
        "pbo_significant": False,
        "n_obs": 0,
        "n_trials": int(max(n_trials, 1)),
        "n_folds": int(max(n_folds, 0)),
        "fold_sharpe_mean": None,
        "fold_sharpe_std": None,
        "fold_sharpe_min": None,
        "negative_fold_ratio": None,
        "passes_statistical_gate": False,
        "reason": None,
    }
