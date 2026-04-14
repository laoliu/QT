"""因子正交化框架 — Gram-Schmidt / PCA / Löwdin 对称 / 残差回归。

消除因子间共线性，用于组合优化和因子构建的预处理步骤。
"""
from __future__ import annotations

import logging
import math
from enum import Enum
from typing import Sequence

logger = logging.getLogger(__name__)


class OrthMethod(str, Enum):
    GRAM_SCHMIDT = "gram_schmidt"
    PCA = "pca"
    SYMMETRIC = "symmetric"
    RESIDUAL = "residual"


# ── 向量工具 ──

def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))

def _norm(a: list[float]) -> float:
    return math.sqrt(_dot(a, a))

def _scale(a: list[float], s: float) -> list[float]:
    return [x * s for x in a]

def _subtract(a: list[float], b: list[float]) -> list[float]:
    return [x - y for x, y in zip(a, b)]

def _mean(a: list[float]) -> float:
    return sum(a) / len(a) if a else 0.0

def _std(a: list[float]) -> float:
    if len(a) < 2:
        return 0.0
    m = _mean(a)
    var = sum((x - m) ** 2 for x in a) / (len(a) - 1)
    return math.sqrt(var)

def _standardize(a: list[float]) -> list[float]:
    m = _mean(a)
    s = _std(a)
    return [(x - m) / s for x in a] if s > 1e-12 else [0.0] * len(a)


# ── 相关性 ──

def compute_factor_correlation(factors: dict[str, list[float]]) -> dict[str, dict[str, float]]:
    """计算因子间 Pearson 相关系数矩阵。"""
    names = sorted(factors.keys())
    if not names:
        return {}
    n = min(len(v) for v in factors.values())
    if n < 2:
        return {ni: {nj: 0.0 for nj in names} for ni in names}
    std = {name: _standardize(factors[name][:n]) for name in names}
    corr: dict[str, dict[str, float]] = {}
    for i in names:
        corr[i] = {}
        for j in names:
            c = _dot(std[i], std[j]) / (n - 1)
            corr[i][j] = max(-1.0, min(1.0, c))
    return corr


def compute_factor_ic(factor_values: list[float], forward_returns: list[float]) -> float:
    """计算因子 IC (Pearson 近似)。"""
    n = min(len(factor_values), len(forward_returns))
    if n < 3:
        return 0.0
    f = _standardize(factor_values[:n])
    r = _standardize(forward_returns[:n])
    return max(-1.0, min(1.0, _dot(f, r) / (n - 1))) if n > 1 else 0.0


# ── Gram-Schmidt ──

def gram_schmidt_orthogonalize(
    factors: dict[str, list[float]],
    priority_order: list[str] | None = None,
) -> dict[str, list[float]]:
    """Gram-Schmidt 正交化。高优先级因子保持不变。"""
    names = priority_order or sorted(factors.keys())
    n = min(len(v) for v in factors.values()) if factors else 0
    if n < 2:
        return dict(factors)

    data = {name: _standardize(factors[name][:n]) for name in names if name in factors}
    orthogonal: dict[str, list[float]] = {}
    basis: list[list[float]] = []

    for name in names:
        if name not in data:
            continue
        vec = list(data[name])
        for b in basis:
            coeff = _dot(vec, b) / (_dot(b, b) + 1e-15)
            vec = _subtract(vec, _scale(b, coeff))
        norm_val = _norm(vec)
        if norm_val > 1e-10:
            vec = _scale(vec, 1.0 / norm_val)
            basis.append(vec)
        orthogonal[name] = vec

    return orthogonal


# ── PCA ──

def pca_orthogonalize(
    factors: dict[str, list[float]],
    n_components: int | None = None,
) -> dict[str, list[float]]:
    """PCA 正交化 (通过 numpy 特征分解)。"""
    import numpy as np

    names = sorted(factors.keys())
    n = min(len(v) for v in factors.values())
    matrix = np.array([factors[name][:n] for name in names]).T  # (T, K)

    # 标准化
    matrix = (matrix - matrix.mean(axis=0)) / (matrix.std(axis=0, ddof=1) + 1e-15)

    # 协方差 → 特征分解
    cov = np.cov(matrix, rowvar=False)
    eigenvalues, eigenvectors = np.linalg.eigh(cov)
    idx = np.argsort(eigenvalues)[::-1]
    eigenvectors = eigenvectors[:, idx]

    k = n_components or len(names)
    k = min(k, len(names))

    # 投影到主成分空间
    projected = matrix @ eigenvectors[:, :k]  # (T, k)
    result: dict[str, list[float]] = {}
    for i in range(k):
        label = names[i] if i < len(names) else f"PC{i + 1}"
        result[label] = projected[:, i].tolist()
    return result


# ── Löwdin 对称正交化 ──

def symmetric_orthogonalize(factors: dict[str, list[float]]) -> dict[str, list[float]]:
    """Löwdin 对称正交化: F_orth = F · Σ^{-1/2}。保持因子经济含义。"""
    import numpy as np

    names = sorted(factors.keys())
    n = min(len(v) for v in factors.values())
    matrix = np.array([factors[name][:n] for name in names]).T  # (T, K)
    matrix = (matrix - matrix.mean(axis=0)) / (matrix.std(axis=0, ddof=1) + 1e-15)

    cov = np.cov(matrix, rowvar=False)
    eigenvalues, eigenvectors = np.linalg.eigh(cov)
    eigenvalues = np.maximum(eigenvalues, 1e-10)
    cov_inv_sqrt = eigenvectors @ np.diag(1.0 / np.sqrt(eigenvalues)) @ eigenvectors.T
    orth_matrix = matrix @ cov_inv_sqrt

    return {name: orth_matrix[:, i].tolist() for i, name in enumerate(names)}


# ── 残差正交化 ──

def residual_orthogonalize(
    factors: dict[str, list[float]],
    control_factors: list[str] | None = None,
) -> dict[str, list[float]]:
    """残差正交化：将每个因子对控制因子回归，取残差。"""
    import numpy as np

    names = sorted(factors.keys())
    n = min(len(v) for v in factors.values())
    controls = control_factors or names[:1]
    target_names = [name for name in names if name not in controls]

    if not target_names or not controls:
        return dict(factors)

    X = np.array([factors[c][:n] for c in controls if c in factors]).T  # (T, C)
    X = np.column_stack([np.ones(n), X])  # 加截距

    result: dict[str, list[float]] = {}
    for c in controls:
        result[c] = factors[c][:n]

    for name in target_names:
        y = np.array(factors[name][:n])
        try:
            beta = np.linalg.lstsq(X, y, rcond=None)[0]
            residual = y - X @ beta
        except np.linalg.LinAlgError:
            residual = y
        result[name] = residual.tolist()

    return result


# ── 统一入口 ──

def orthogonalize(
    factors: dict[str, list[float]],
    method: str | OrthMethod = OrthMethod.GRAM_SCHMIDT,
    **kwargs,
) -> dict[str, list[float]]:
    """统一正交化入口。"""
    method = OrthMethod(method) if isinstance(method, str) else method
    if method == OrthMethod.GRAM_SCHMIDT:
        return gram_schmidt_orthogonalize(factors, **kwargs)
    elif method == OrthMethod.PCA:
        return pca_orthogonalize(factors, **kwargs)
    elif method == OrthMethod.SYMMETRIC:
        return symmetric_orthogonalize(factors)
    elif method == OrthMethod.RESIDUAL:
        return residual_orthogonalize(factors, **kwargs)
    else:
        raise ValueError(f"Unknown method: {method}")
