"""协方差矩阵估计 — Sample / Ledoit-Wolf 收缩 / 指数加权"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Literal

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class CovMethod(str, Enum):
    SAMPLE = "sample"
    SHRINKAGE = "shrinkage"      # Ledoit-Wolf
    EXPONENTIAL = "exponential"  # EWM


def sample_covariance(returns: pd.DataFrame) -> pd.DataFrame:
    """样本协方差矩阵"""
    return returns.cov()


def ledoit_wolf_shrinkage(returns: pd.DataFrame) -> pd.DataFrame:
    """Ledoit-Wolf 线性收缩协方差估计。

    收缩目标: 常数相关系数矩阵 (constant correlation)
    参考: Ledoit & Wolf (2004) "Honey, I shrunk the sample covariance matrix"
    """
    X = returns.values
    T, N = X.shape
    X_centered = X - X.mean(axis=0)
    S = (X_centered.T @ X_centered) / T  # 样本协方差

    # 收缩目标: F = diag(S) * r_bar  (常数相关 target)
    var = np.diag(S)
    std = np.sqrt(var)
    # 平均相关系数
    corr = S / np.outer(std, std)
    np.fill_diagonal(corr, 0)
    r_bar = corr.sum() / (N * (N - 1))
    F = r_bar * np.outer(std, std)
    np.fill_diagonal(F, var)

    # 最优收缩强度
    # 简化版：使用 Oracle Approximating Shrinkage (OAS)
    rho_num = 0.0
    rho_den = 0.0
    for t in range(T):
        x_t = X_centered[t:t + 1].T  # (N, 1)
        m_t = x_t @ x_t.T
        rho_num += np.sum((m_t - S) ** 2)
        rho_den += np.sum((m_t - F) ** 2)

    rho_num /= T ** 2
    rho_den /= T ** 2

    if rho_den < 1e-12:
        delta = 1.0
    else:
        delta = max(0.0, min(1.0, rho_num / rho_den))

    cov_shrunk = (1 - delta) * S + delta * F
    logger.info(f"Ledoit-Wolf shrinkage intensity: {delta:.4f}")
    return pd.DataFrame(cov_shrunk, index=returns.columns, columns=returns.columns)


def exponential_covariance(
    returns: pd.DataFrame,
    halflife: int = 63,
) -> pd.DataFrame:
    """指数加权协方差矩阵"""
    ewm = returns.ewm(halflife=halflife)
    # pandas ewm().cov() 返回 MultiIndex — 需要 reshape
    cov_vals = ewm.cov().iloc[-len(returns.columns):]
    # 提取最后一个截面
    n = len(returns.columns)
    last_cov = returns.ewm(halflife=halflife).cov()
    # 取最后 n 行（最后一个日期的所有资产 pair）
    tail = last_cov.tail(n)
    tail.index = tail.index.droplevel(0) if isinstance(tail.index, pd.MultiIndex) else tail.index
    return tail


def estimate_covariance(
    returns: pd.DataFrame,
    method: CovMethod | str = CovMethod.SHRINKAGE,
    **kwargs,
) -> pd.DataFrame:
    """统一协方差估计入口"""
    method = CovMethod(method) if isinstance(method, str) else method

    if method == CovMethod.SAMPLE:
        return sample_covariance(returns)
    elif method == CovMethod.SHRINKAGE:
        return ledoit_wolf_shrinkage(returns)
    elif method == CovMethod.EXPONENTIAL:
        return exponential_covariance(returns, **kwargs)
    else:
        raise ValueError(f"Unknown covariance method: {method}")


def nearest_positive_definite(cov: pd.DataFrame) -> pd.DataFrame:
    """修正非正定协方差矩阵（Higham 2002 算法简化版）"""
    A = cov.values.copy()
    B = (A + A.T) / 2
    eigvals, eigvecs = np.linalg.eigh(B)
    eigvals = np.maximum(eigvals, 1e-10)
    fixed = eigvecs @ np.diag(eigvals) @ eigvecs.T
    fixed = (fixed + fixed.T) / 2
    return pd.DataFrame(fixed, index=cov.index, columns=cov.columns)
