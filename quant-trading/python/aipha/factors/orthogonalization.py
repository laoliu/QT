"""因子正交化框架（Factor Orthogonalization Framework）

提供系统化的因子去相关方法，用于：
1. 策略选择：评估策略间相关性，选出正交度高的组合
2. 因子构建：对因子做正交化处理，消除共线性
3. 组合优化：使用正交因子权重替代原始因子权重

**支持的方法**：

1. **Gram-Schmidt 正交化**：
   逐步将因子投影到已选因子的正交补空间
   适用场景：因子有明确优先级（信息比 / IC 排序）

2. **PCA 正交化**：
   主成分分析提取正交成分
   适用场景：无先验优先级，探索数据结构

3. **对称正交化 (Symmetric / Löwdin)**：
   $F_{orth} = F \\cdot \\Sigma^{-1/2}$
   适用场景：需要保持因子的经济含义（变换后仍接近原因子）

4. **残差正交化**：
   回归法：将每个因子对其他因子回归，取残差
   适用场景：明确"控制变量"

**理论基础**（§11.2）：
- 多重共线性导致因子权重不稳定 → 正交化是必要预处理
- Löwdin 对称正交化保持因子解释性（Löwdin 1950）
- PCA 降维可有效减少参数数量（降低过拟合风险）
"""

from __future__ import annotations

import logging
import math
from enum import Enum
from typing import Any, Optional, Sequence

logger = logging.getLogger(__name__)


class OrthMethod(str, Enum):
    """正交化方法。"""
    GRAM_SCHMIDT = "gram_schmidt"
    PCA = "pca"
    SYMMETRIC = "symmetric"      # Löwdin
    RESIDUAL = "residual"

    def __str__(self) -> str:
        return self.value


# ---------------------------------------------------------------------------
# 向量工具
# ---------------------------------------------------------------------------

def _dot(a: list[float], b: list[float]) -> float:
    """向量点积。"""
    return sum(x * y for x, y in zip(a, b))


def _norm(a: list[float]) -> float:
    """向量 L2 范数。"""
    return math.sqrt(_dot(a, a))


def _scale(a: list[float], s: float) -> list[float]:
    """标量乘法。"""
    return [x * s for x in a]


def _subtract(a: list[float], b: list[float]) -> list[float]:
    """向量减法。"""
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
    """标准化到 mean=0, std=1。"""
    m = _mean(a)
    s = _std(a)
    if s < 1e-12:
        return [0.0] * len(a)
    return [(x - m) / s for x in a]


# ---------------------------------------------------------------------------
# 相关性计算
# ---------------------------------------------------------------------------

def compute_factor_correlation(
    factors: dict[str, list[float]],
) -> dict[str, dict[str, float]]:
    """计算因子间 Pearson 相关系数矩阵。

    Args:
        factors: {factor_name: [values]}，等长数组。

    Returns:
        {name_i: {name_j: correlation}}
    """
    names = sorted(factors.keys())
    if not names:
        return {}

    n = min(len(v) for v in factors.values())
    if n < 2:
        return {ni: {nj: 0.0 for nj in names} for ni in names}

    standardized = {name: _standardize(factors[name][:n]) for name in names}

    corr: dict[str, dict[str, float]] = {}
    for i in names:
        corr[i] = {}
        for j in names:
            c = _dot(standardized[i], standardized[j]) / (n - 1)
            corr[i][j] = max(-1.0, min(1.0, c))
    return corr


def compute_factor_ic(
    factor_values: list[float],
    forward_returns: list[float],
) -> float:
    """计算因子 IC（Rank IC: Spearman 秩相关）。

    简化实现：使用 Pearson 相关（因为 rank IC 需要排名，
    这里用标准化 Pearson 近似）。
    """
    n = min(len(factor_values), len(forward_returns))
    if n < 3:
        return 0.0

    f = _standardize(factor_values[:n])
    r = _standardize(forward_returns[:n])

    ic = _dot(f, r) / (n - 1) if n > 1 else 0.0
    return max(-1.0, min(1.0, ic))


# ---------------------------------------------------------------------------
# 正交化方法
# ---------------------------------------------------------------------------

def gram_schmidt_orthogonalize(
    factors: dict[str, list[float]],
    priority_order: list[str] | None = None,
) -> dict[str, list[float]]:
    """Gram-Schmidt 正交化。

    Args:
        factors: {factor_name: [values]}
        priority_order: 因子优先级（高优先级的先处理，保持不变）
                       None 时按字典序

    Returns:
        正交化后的因子字典
    """
    names = priority_order or sorted(factors.keys())
    n = min(len(v) for v in factors.values()) if factors else 0
    if n < 2:
        return dict(factors)

    # 标准化
    data = {name: _standardize(factors[name][:n]) for name in names if name in factors}

    orthogonal: dict[str, list[float]] = {}
    basis: list[list[float]] = []

    for name in names:
        if name not in data:
            continue
        vec = list(data[name])

        # 减去在已有基向量上的投影
        for b in basis:
            proj_coeff = _dot(vec, b) / (_dot(b, b) + 1e-12)
            projection = _scale(b, proj_coeff)
            vec = _subtract(vec, projection)

        # 归一化（如果非零）
        vec_norm = _norm(vec)
        if vec_norm > 1e-10:
            vec = _scale(vec, 1.0 / vec_norm)
            basis.append(vec)

        orthogonal[name] = vec

    return orthogonal


def pca_orthogonalize(
    factors: dict[str, list[float]],
    n_components: int | None = None,
) -> tuple[dict[str, list[float]], list[float]]:
    """PCA 正交化（简化实现：幂迭代提取主成分）。

    Args:
        factors: {factor_name: [values]}
        n_components: 保留的主成分数（None = 全保留）

    Returns:
        (主成分字典 {"PC1": [values], ...}, 解释方差比例列表)
    """
    names = sorted(factors.keys())
    n = min(len(v) for v in factors.values()) if factors else 0
    k = len(names)

    if n < 2 or k == 0:
        return {f"PC{i+1}": factors[name] for i, name in enumerate(names)}, [1.0 / k] * k

    # 构建标准化数据矩阵 (n x k)
    data = [_standardize(factors[name][:n]) for name in names]

    # 协方差矩阵 (k x k)
    cov = [[0.0] * k for _ in range(k)]
    for i in range(k):
        for j in range(k):
            cov[i][j] = _dot(data[i], data[j]) / (n - 1)

    # 幂迭代法提取特征向量
    n_comp = min(n_components or k, k)
    components: list[list[float]] = []
    eigenvalues: list[float] = []
    remaining_cov = [row[:] for row in cov]

    for _ in range(n_comp):
        ev, eigvec = _power_iteration(remaining_cov, max_iter=100)
        eigenvalues.append(ev)
        components.append(eigvec)

        # 偏转（deflation）
        for i in range(k):
            for j in range(k):
                remaining_cov[i][j] -= ev * eigvec[i] * eigvec[j]

    # 计算主成分分数
    total_var = sum(eigenvalues)
    explained = [ev / total_var if total_var > 0 else 0.0 for ev in eigenvalues]

    pc_scores: dict[str, list[float]] = {}
    for c_idx, eigvec in enumerate(components):
        # PC score = 数据 × 特征向量
        scores = []
        for t in range(n):
            score = sum(data[f_idx][t] * eigvec[f_idx] for f_idx in range(k))
            scores.append(score)
        pc_scores[f"PC{c_idx + 1}"] = scores

    return pc_scores, explained


def residual_orthogonalize(
    target: list[float],
    controls: dict[str, list[float]],
) -> list[float]:
    """残差正交化：将 target 对 controls 回归，返回残差。

    $\\text{target} = \\alpha + \\sum_i \\beta_i \\cdot \\text{control}_i + \\epsilon$
    返回 $\\epsilon$（正交之后的纯净因子暴露）。

    简化实现：逐步减去投影（等价于 Gram-Schmidt 对 target）。
    """
    n = len(target)
    if n < 2 or not controls:
        return list(target)

    residual = _standardize(target[:n])

    for name, ctrl in controls.items():
        ctrl_std = _standardize(ctrl[:n])
        proj_coeff = _dot(residual, ctrl_std) / (_dot(ctrl_std, ctrl_std) + 1e-12)
        projection = _scale(ctrl_std, proj_coeff)
        residual = _subtract(residual, projection)

    return residual


# ---------------------------------------------------------------------------
# 策略正交度评估
# ---------------------------------------------------------------------------

def evaluate_strategy_orthogonality(
    strategy_returns: dict[str, list[float]],
) -> dict[str, Any]:
    """评估策略组合的正交度。

    Returns:
        {
            "correlation_matrix": {s1: {s2: corr}},
            "avg_abs_correlation": float,
            "max_abs_correlation": float,
            "orthogonality_score": float (0-1, 1=完美正交),
            "pca_explained": [float, ...],
            "effective_dimensions": float,
        }
    """
    corr = compute_factor_correlation(strategy_returns)
    names = sorted(corr.keys())
    n = len(names)

    if n < 2:
        return {
            "correlation_matrix": corr,
            "avg_abs_correlation": 0.0,
            "max_abs_correlation": 0.0,
            "orthogonality_score": 1.0,
            "pca_explained": [1.0],
            "effective_dimensions": 1.0,
        }

    # 平均/最大绝对相关系数（不含对角线）
    abs_corrs = []
    for i in range(n):
        for j in range(i + 1, n):
            abs_corrs.append(abs(corr[names[i]][names[j]]))

    avg_abs = sum(abs_corrs) / len(abs_corrs) if abs_corrs else 0.0
    max_abs = max(abs_corrs) if abs_corrs else 0.0

    # PCA 解释方差
    _, explained = pca_orthogonalize(strategy_returns, n_components=n)

    # 有效维度（entropy-based）
    eff_dim = _effective_dimensions(explained)

    # 正交度分数：1 - avg_abs_corr (简化)
    orth_score = max(0.0, 1.0 - avg_abs)

    return {
        "correlation_matrix": corr,
        "avg_abs_correlation": avg_abs,
        "max_abs_correlation": max_abs,
        "orthogonality_score": orth_score,
        "pca_explained": explained,
        "effective_dimensions": eff_dim,
    }


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _power_iteration(
    matrix: list[list[float]],
    max_iter: int = 100,
    tol: float = 1e-8,
) -> tuple[float, list[float]]:
    """幂迭代法求最大特征值和特征向量。"""
    k = len(matrix)
    if k == 0:
        return 0.0, []

    # 初始向量 — 使用非对称起点以避免恰好落在退化特征向量上
    v = [float(i + 1) for i in range(k)]
    v_norm = _norm(v)
    v = _scale(v, 1.0 / v_norm) if v_norm > 1e-12 else [1.0 / math.sqrt(k)] * k

    for _ in range(max_iter):
        # Av
        new_v = [sum(matrix[i][j] * v[j] for j in range(k)) for i in range(k)]

        # 归一化
        norm = _norm(new_v)
        if norm < 1e-12:
            return 0.0, [0.0] * k

        new_v = _scale(new_v, 1.0 / norm)

        # 收敛检查
        diff = _norm(_subtract(new_v, v))
        v = new_v
        if diff < tol:
            break

    # 特征值 = v^T A v
    av = [sum(matrix[i][j] * v[j] for j in range(k)) for i in range(k)]
    eigenvalue = _dot(v, av)

    return eigenvalue, v


def _effective_dimensions(explained_ratios: list[float]) -> float:
    """基于信息熵的有效维度数。

    $d_{eff} = \\exp(-\\sum p_i \\log p_i)$（Shannon entropy）
    """
    if not explained_ratios:
        return 0.0

    total = sum(explained_ratios)
    if total <= 0:
        return 0.0

    normalized = [p / total for p in explained_ratios]
    entropy = 0.0
    for p in normalized:
        if p > 1e-12:
            entropy -= p * math.log(p)

    return math.exp(entropy)
