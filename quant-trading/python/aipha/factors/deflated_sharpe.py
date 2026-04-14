"""Deflated Sharpe Ratio (DSR) — 多重检验校正下的夏普比率.

基于 Bailey & López de Prado (2014):
  "The Deflated Sharpe Ratio: Correcting for Selection Bias,
   Backtest Overfitting and Non-Normality"

DSR 通过考虑:
  1. 回测试验次数 (N)
  2. 收益序列的偏度和峰度
  3. 观察期长度 (T)
来计算经校正后的 Sharpe Ratio 的统计显著性。

Usage::
    from aipha.factors.deflated_sharpe import deflated_sharpe_ratio
    p_value = deflated_sharpe_ratio(
        observed_sr=1.5, n_trials=130,
        sr_std=0.3, skewness=-0.5, kurtosis=4.0,
        t_observations=84,
    )
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
from scipy import stats as sp_stats


def expected_max_sr(
    n_trials: int,
    sr_std: float = 1.0,
) -> float:
    """E[max(SR)] — 预期最大夏普比率 (独立正态假设).

    Bailey & López de Prado (2014), Eq. (1):
    E[max] ≈ sr_std * ((1-γ)*Φ^{-1}(1-1/N) + γ*Φ^{-1}(1-1/(N*e)))
    其中 γ ≈ 0.5772 (Euler-Mascheroni 常数).
    """
    if n_trials <= 1:
        return 0.0
    gamma = 0.5772156649  # Euler-Mascheroni
    z1 = sp_stats.norm.ppf(1.0 - 1.0 / n_trials)
    z2 = sp_stats.norm.ppf(1.0 - 1.0 / (n_trials * math.e))
    return float(sr_std * ((1 - gamma) * z1 + gamma * z2))


def sr_std_corrected(
    observed_sr: float,
    skewness: float,
    kurtosis: float,
    t_obs: int,
) -> float:
    """SR 标准差 (考虑非正态性).

    Bailey & López de Prado (2014), Eq. (4):
    Var(SR) ≈ (1 - γ₁·SR + (γ₂-1)/4·SR²) / T
    其中 γ₁=偏度, γ₂=峰度 (非超额).
    """
    if t_obs <= 1:
        return 1.0
    sr2 = observed_sr ** 2
    # kurtosis 此处为超额峰度 (excess kurtosis)
    # 文献中用峰度 γ₂ (非超额) = excess_kurtosis + 3
    var = (1.0
           - skewness * observed_sr
           + (kurtosis - 1.0) / 4.0 * sr2) / t_obs
    return float(max(var, 1e-12) ** 0.5)


def deflated_sharpe_ratio(
    observed_sr: float,
    n_trials: int,
    sr_std: float | None = None,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
    t_observations: int = 60,
) -> dict:
    """计算 Deflated Sharpe Ratio 及 p-value.

    Args:
        observed_sr: 观测到的夏普比率
        n_trials: 总回测/策略试验次数 (N)
        sr_std: SR 横截面标准差 (如 None, 默认=1.0)
        skewness: 收益序列偏度 (默认 0=正态)
        kurtosis: 收益序列峰度 (默认 3=正态, 超额峰度=0)
        t_observations: 时间观测数 (如月频 IC 数量)

    Returns:
        dict with keys: dsr, p_value, e_max_sr,
        sr_std_hat, is_significant
    """
    if sr_std is None:
        sr_std = 1.0
    e_max = expected_max_sr(n_trials, sr_std)
    # SR 的标准差（考虑非正态性）
    excess_kurt = kurtosis - 3.0  # 转换为超额峰度
    sr_se = sr_std_corrected(
        observed_sr, skewness, excess_kurt + 1.0, t_observations)
    # DSR test statistic
    if sr_se < 1e-12:
        dsr_stat = 0.0
    else:
        dsr_stat = (observed_sr - e_max) / sr_se
    # p-value (单侧检验: H0: SR ≤ E[max(SR)])
    p_value = float(1.0 - sp_stats.norm.cdf(dsr_stat))
    return {
        "dsr": round(dsr_stat, 4),
        "p_value": round(p_value, 6),
        "e_max_sr": round(e_max, 4),
        "sr_std_hat": round(sr_se, 4),
        "observed_sr": round(observed_sr, 4),
        "n_trials": n_trials,
        "is_significant": p_value < 0.05,
    }


def compute_dsr_for_factor(
    ic_series: list[float],
    n_trials: int,
) -> dict:
    """从月频 IC 序列直接计算 DSR.

    IC 序列的均值/标准差类比 SR：
    SR_IC = mean(IC) / std(IC), 即 ICIR。
    """
    if len(ic_series) < 6 or n_trials < 2:
        return {
            "dsr": 0.0, "p_value": 1.0, "e_max_sr": 0.0,
            "sr_std_hat": 0.0, "observed_sr": 0.0,
            "n_trials": n_trials, "is_significant": False,
        }
    arr = np.array(ic_series, dtype=float)
    mu = float(np.mean(arr))
    sd = float(np.std(arr, ddof=1))
    icir = mu / sd if sd > 1e-12 else 0.0
    skew = float(sp_stats.skew(arr, bias=False))
    kurt = float(sp_stats.kurtosis(arr, bias=False, fisher=False))
    return deflated_sharpe_ratio(
        observed_sr=abs(icir),
        n_trials=n_trials,
        skewness=skew,
        kurtosis=kurt,
        t_observations=len(arr),
    )
