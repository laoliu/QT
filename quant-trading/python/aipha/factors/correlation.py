"""因子相关性与冗余检测

提供：
- 因子间相关性矩阵计算（Spearman / Pearson）
- 冗余因子对检测（高相关 → 冗余候选）
- 因子换手率分析（Top/Bottom 组合持仓变动频率）

使用示例::

    # 相关性矩阵
    matrix = compute_correlation_matrix(factor_panels, method="spearman")

    # 冗余检测
    pairs = detect_redundant_factors(factor_panels, threshold=0.7)

    # 因子换手率
    turnover = compute_factor_turnover(factor_panels, quantile=0.2)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
from scipy import stats


# ============================================================================
# 数据结构
# ============================================================================


@dataclass(frozen=True, slots=True)
class CorrelationPair:
    """一对因子间的相关性

    Attributes:
        factor_a: 因子 A 名称
        factor_b: 因子 B 名称
        correlation: 相关系数
        p_value: 显著性 p 值
        n_observations: 观测数
    """

    factor_a: str
    factor_b: str
    correlation: float
    p_value: float
    n_observations: int


@dataclass
class CorrelationMatrix:
    """因子相关性矩阵结果

    Attributes:
        factor_names: 因子名称列表（行/列顺序）
        matrix: 相关系数矩阵（n×n numpy array）
        p_values: p 值矩阵（n×n）
        method: 使用的计算方法
        n_dates: 用于计算的日期数
        avg_n_symbols: 平均每日可用标的数
    """

    factor_names: list[str]
    matrix: np.ndarray  # shape (n, n)
    p_values: np.ndarray  # shape (n, n)
    method: str
    n_dates: int
    avg_n_symbols: float

    def get(self, factor_a: str, factor_b: str) -> float:
        """获取两个因子间的相关系数"""
        i = self.factor_names.index(factor_a)
        j = self.factor_names.index(factor_b)
        return float(self.matrix[i, j])

    def top_pairs(
        self, n: int = 10, *, absolute: bool = True
    ) -> list[CorrelationPair]:
        """获取相关性最高的 n 对因子（排除对角线）

        Args:
            n: 返回数量
            absolute: 是否按绝对值排序
        """
        pairs: list[CorrelationPair] = []
        k = len(self.factor_names)
        for i in range(k):
            for j in range(i + 1, k):
                pairs.append(
                    CorrelationPair(
                        factor_a=self.factor_names[i],
                        factor_b=self.factor_names[j],
                        correlation=float(self.matrix[i, j]),
                        p_value=float(self.p_values[i, j]),
                        n_observations=self.n_dates,
                    )
                )
        key = (lambda p: abs(p.correlation)) if absolute else (lambda p: p.correlation)
        pairs.sort(key=key, reverse=True)
        return pairs[:n]

    def to_dict(self) -> dict:
        """转为可序列化字典"""
        return {
            "factor_names": self.factor_names,
            "matrix": self.matrix.tolist(),
            "p_values": self.p_values.tolist(),
            "method": self.method,
            "n_dates": self.n_dates,
            "avg_n_symbols": self.avg_n_symbols,
        }


@dataclass(frozen=True, slots=True)
class FactorTurnoverResult:
    """因子换手率分析结果

    Attributes:
        factor_name: 因子名称
        avg_turnover: 平均换手率（0~1，Top 组的持仓变动比例）
        turnover_by_date: 每日换手率 {date: turnover}
        quantile: 选取分位（如 0.2 = Top 20%）
        n_dates: 日期数
        avg_group_size: 平均组内标的数
    """

    factor_name: str
    avg_turnover: float
    turnover_by_date: dict[date, float]
    quantile: float
    n_dates: int
    avg_group_size: float


# ============================================================================
# 因子相关性矩阵
# ============================================================================


FactorPanel = dict[date, dict[str, float]]
"""因子面板数据类型：{date: {symbol: value}}"""


def compute_correlation_matrix(
    factor_panels: dict[str, FactorPanel],
    *,
    method: str = "spearman",
    min_common_symbols: int = 10,
) -> CorrelationMatrix:
    """计算多因子间的截面相关性矩阵

    对每个交易日，取所有因子的公共标的，计算截面相关系数；
    最终取各日相关系数的均值。

    Args:
        factor_panels: {factor_name: {date: {symbol: value}}}
        method: ``"spearman"`` 或 ``"pearson"``
        min_common_symbols: 最少公共标的数（不足的日期跳过）

    Returns:
        CorrelationMatrix 结果

    Raises:
        ValueError: 因子数量 < 2
    """
    names = list(factor_panels.keys())
    n = len(names)
    if n < 2:
        raise ValueError("至少需要 2 个因子计算相关性矩阵")

    # 收集所有因子的公共日期
    all_dates: set[date] = set()
    for panel in factor_panels.values():
        all_dates |= panel.keys()
    common_dates = sorted(all_dates)

    # 每对因子的日度相关系数累积
    corr_accum = np.zeros((n, n), dtype=np.float64)
    pval_accum = np.zeros((n, n), dtype=np.float64)
    count_accum = np.zeros((n, n), dtype=np.int64)
    total_symbols = 0
    valid_dates = 0

    corr_fn = stats.spearmanr if method == "spearman" else stats.pearsonr

    for d in common_dates:
        # 取各因子在该日的数据
        day_data: dict[str, dict[str, float]] = {}
        for name in names:
            dd = factor_panels[name].get(d)
            if dd is not None:
                day_data[name] = dd

        if len(day_data) < 2:
            continue

        # 取所有因子的公共标的
        symbol_sets = [set(dd.keys()) for dd in day_data.values()]
        common_symbols = symbol_sets[0]
        for ss in symbol_sets[1:]:
            common_symbols &= ss
        common_symbols_sorted = sorted(common_symbols)

        if len(common_symbols_sorted) < min_common_symbols:
            continue

        valid_dates += 1
        total_symbols += len(common_symbols_sorted)

        # 构建值向量
        vectors: dict[str, np.ndarray] = {}
        for name in names:
            if name in day_data:
                vectors[name] = np.array(
                    [day_data[name][s] for s in common_symbols_sorted],
                    dtype=np.float64,
                )

        # 计算两两相关系数
        for i in range(n):
            for j in range(i, n):
                if names[i] not in vectors or names[j] not in vectors:
                    continue
                if i == j:
                    corr_accum[i, j] += 1.0
                    count_accum[i, j] += 1
                    continue
                vi, vj = vectors[names[i]], vectors[names[j]]
                # 检查常数向量
                if np.std(vi) == 0 or np.std(vj) == 0:
                    continue
                c, p = corr_fn(vi, vj)
                if not np.isnan(c):
                    corr_accum[i, j] += c
                    corr_accum[j, i] += c
                    pval_accum[i, j] += p
                    pval_accum[j, i] += p
                    count_accum[i, j] += 1
                    count_accum[j, i] += 1

    # 求均值
    matrix = np.zeros((n, n), dtype=np.float64)
    p_matrix = np.ones((n, n), dtype=np.float64)
    for i in range(n):
        for j in range(n):
            cnt = count_accum[i, j]
            if cnt > 0:
                matrix[i, j] = corr_accum[i, j] / cnt
                p_matrix[i, j] = pval_accum[i, j] / cnt

    avg_symbols = total_symbols / valid_dates if valid_dates > 0 else 0.0

    return CorrelationMatrix(
        factor_names=names,
        matrix=matrix,
        p_values=p_matrix,
        method=method,
        n_dates=valid_dates,
        avg_n_symbols=avg_symbols,
    )


# ============================================================================
# 冗余因子检测
# ============================================================================


def detect_redundant_factors(
    factor_panels: dict[str, FactorPanel],
    *,
    threshold: float = 0.7,
    method: str = "spearman",
    min_common_symbols: int = 10,
) -> list[CorrelationPair]:
    """检测冗余因子对

    返回相关系数绝对值 >= threshold 的因子对，按 |correlation| 降序排列。

    Args:
        factor_panels: 因子面板数据
        threshold: 冗余阈值（相关系数绝对值）
        method: 相关系数计算方法
        min_common_symbols: 最少公共标的数

    Returns:
        冗余因子对列表，按 |correlation| 降序

    Raises:
        ValueError: 因子数量 < 2 或 threshold 不在 (0, 1) 范围
    """
    if not 0.0 < threshold < 1.0:
        raise ValueError(f"阈值必须在 (0, 1) 范围内，当前: {threshold}")

    corr = compute_correlation_matrix(
        factor_panels, method=method, min_common_symbols=min_common_symbols,
    )

    n = len(corr.factor_names)
    pairs: list[CorrelationPair] = []
    for i in range(n):
        for j in range(i + 1, n):
            c = float(corr.matrix[i, j])
            if abs(c) >= threshold:
                pairs.append(
                    CorrelationPair(
                        factor_a=corr.factor_names[i],
                        factor_b=corr.factor_names[j],
                        correlation=c,
                        p_value=float(corr.p_values[i, j]),
                        n_observations=corr.n_dates,
                    )
                )
    pairs.sort(key=lambda p: abs(p.correlation), reverse=True)
    return pairs


# ============================================================================
# 因子换手率
# ============================================================================


def compute_factor_turnover(
    factor_panel: FactorPanel,
    *,
    factor_name: str = "factor",
    quantile: float = 0.2,
    top: bool = True,
) -> FactorTurnoverResult:
    """计算因子 Top/Bottom 组合的换手率

    对每个交易日，按因子值排序，取 Top (或 Bottom) quantile 的标的集合，
    与前一日对比计算换手率。

    Args:
        factor_panel: 因子面板 {date: {symbol: value}}
        factor_name: 因子名称
        quantile: 分位比例（0~1）。0.2 表示取 Top/Bottom 20%
        top: True=取因子值最大的一端，False=取最小的

    Returns:
        FactorTurnoverResult

    Raises:
        ValueError: quantile 不在 (0, 1) 范围
    """
    if not 0.0 < quantile < 1.0:
        raise ValueError(f"quantile 必须在 (0, 1) 范围内，当前: {quantile}")

    dates = sorted(factor_panel.keys())
    if len(dates) < 2:
        return FactorTurnoverResult(
            factor_name=factor_name,
            avg_turnover=0.0,
            turnover_by_date={},
            quantile=quantile,
            n_dates=len(dates),
            avg_group_size=0.0,
        )

    turnover_by_date: dict[date, float] = {}
    group_sizes: list[int] = []
    prev_group: set[str] | None = None

    for d in dates:
        day_data = factor_panel[d]
        if not day_data:
            continue

        # 排序并取 quantile
        sorted_symbols = sorted(
            day_data.keys(), key=lambda s: day_data[s], reverse=top,
        )
        n_select = max(1, int(len(sorted_symbols) * quantile))
        current_group = set(sorted_symbols[:n_select])
        group_sizes.append(len(current_group))

        if prev_group is not None and len(current_group) > 0:
            # 换手率 = 变动标的数 / 平均组大小
            union_size = len(prev_group | current_group)
            intersection_size = len(prev_group & current_group)
            changed = union_size - intersection_size
            avg_size = (len(prev_group) + len(current_group)) / 2
            turnover = changed / avg_size if avg_size > 0 else 0.0
            turnover_by_date[d] = turnover

        prev_group = current_group

    avg_turnover = (
        float(np.mean(list(turnover_by_date.values())))
        if turnover_by_date
        else 0.0
    )
    avg_group_size = float(np.mean(group_sizes)) if group_sizes else 0.0

    return FactorTurnoverResult(
        factor_name=factor_name,
        avg_turnover=avg_turnover,
        turnover_by_date=turnover_by_date,
        quantile=quantile,
        n_dates=len(dates),
        avg_group_size=avg_group_size,
    )
