"""因子 IC (Information Coefficient) 分析

计算因子值与未来收益之间的 Rank IC（Spearman 秩相关），
以及 IC 均值、IC IR、IC 衰减/半衰期等统计量。

核心指标：
- IC: 单期 Spearman 秩相关（因子值 vs T+N 收益）
- IC Mean: 平均 IC（衡量因子预测方向性）
- IC Std: IC 标准差（衡量因子稳定性）
- IC IR (ICIR): IC Mean / IC Std（类 Sharpe，衡量风险调整后的预测力）
- IC Half-Life: IC 自相关衰减到 0.5 所需期数（衡量因子时效性）
- IC Decay: 不同持有期的 IC 趋势（因子在 T+1, T+5, T+20 的预测力）

典型用法::

    analyzer = FactorICAnalyzer(forward_periods=[1, 5, 10, 20])
    result = analyzer.compute(factor_df, return_df, group_col="datetime")
    print(result.summary())

参考：
- Qlib Alpha IC Analysis
- Barra Factor Model - IC / ICIR
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FactorICResult:
    """单因子 IC 分析结果。"""

    factor_name: str
    forward_period: int           # 前瞻收益期数（如 1, 5, 20）
    ic_series: list[float]        # 每期 IC 值
    dates: list[str]              # 对应的日期标签

    @property
    def ic_mean(self) -> float:
        """IC 均值。"""
        if not self.ic_series:
            return 0.0
        valid = [x for x in self.ic_series if math.isfinite(x)]
        return sum(valid) / len(valid) if valid else 0.0

    @property
    def ic_std(self) -> float:
        """IC 标准差。"""
        valid = [x for x in self.ic_series if math.isfinite(x)]
        if len(valid) < 2:
            return 0.0
        mean = sum(valid) / len(valid)
        var = sum((x - mean) ** 2 for x in valid) / (len(valid) - 1)
        return math.sqrt(var)

    @property
    def ic_ir(self) -> float:
        """IC Information Ratio (ICIR = IC_mean / IC_std)。"""
        std = self.ic_std
        if std < 1e-12:
            return 0.0
        return self.ic_mean / std

    @property
    def ic_positive_rate(self) -> float:
        """IC 为正的比率。"""
        valid = [x for x in self.ic_series if math.isfinite(x)]
        if not valid:
            return 0.0
        return sum(1 for x in valid if x > 0) / len(valid)

    @property
    def ic_abs_mean(self) -> float:
        """IC 绝对值均值。"""
        valid = [x for x in self.ic_series if math.isfinite(x)]
        return sum(abs(x) for x in valid) / len(valid) if valid else 0.0

    @property
    def n_periods(self) -> int:
        """有效期数。"""
        return len([x for x in self.ic_series if math.isfinite(x)])

    def to_dict(self) -> dict[str, Any]:
        """转换为字典（不含完整序列）。"""
        return {
            "factor_name": self.factor_name,
            "forward_period": self.forward_period,
            "ic_mean": round(self.ic_mean, 6),
            "ic_std": round(self.ic_std, 6),
            "ic_ir": round(self.ic_ir, 4),
            "ic_abs_mean": round(self.ic_abs_mean, 6),
            "ic_positive_rate": round(self.ic_positive_rate, 4),
            "n_periods": self.n_periods,
        }


@dataclass(frozen=True)
class ICDecayResult:
    """IC 衰减分析结果。"""

    factor_name: str
    periods: list[int]                 # 前瞻期列表 [1, 5, 10, 20]
    ic_means: list[float]              # 对应的 IC 均值
    ic_irs: list[float]                # 对应的 ICIR
    half_life: float | None            # IC 半衰期（期数），None 表示无法估计
    decay_slope: float                 # IC 均值随 period 变化的线性斜率

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        return {
            "factor_name": self.factor_name,
            "periods": self.periods,
            "ic_means": [round(x, 6) for x in self.ic_means],
            "ic_irs": [round(x, 4) for x in self.ic_irs],
            "half_life": round(self.half_life, 2) if self.half_life is not None else None,
            "decay_slope": round(self.decay_slope, 8),
        }


@dataclass
class FactorICAnalyzer:
    """因子 IC 分析器。

    通过纯 Python 实现（无 scipy 依赖），支持：
    - 单因子单期 IC 计算
    - 多期 IC 衰减分析
    - IC 半衰期估计

    Args:
        forward_periods: 前瞻收益计算周期列表
    """

    forward_periods: list[int] = field(
        default_factory=lambda: [1, 5, 10, 20]
    )

    def compute_ic(
        self,
        factor_values: list[float],
        forward_returns: list[float],
    ) -> float:
        """计算截面 Rank IC（Spearman 秩相关）。

        Args:
            factor_values: 因子值序列（对应 N 个标的）
            forward_returns: 未来收益序列（对应 N 个标的）

        Returns:
            Spearman 秩相关系数，NaN 无法计算。
        """
        if len(factor_values) != len(forward_returns):
            return float("nan")

        # 过滤 NaN / Inf
        pairs = [
            (f, r)
            for f, r in zip(factor_values, forward_returns)
            if math.isfinite(f) and math.isfinite(r)
        ]
        n = len(pairs)
        if n < 3:
            return float("nan")

        fv = [p[0] for p in pairs]
        rv = [p[1] for p in pairs]

        rank_f = _rank(fv)
        rank_r = _rank(rv)

        return _pearson(rank_f, rank_r)

    def compute_panel_ic(
        self,
        panel: list[dict[str, Any]],
        factor_col: str,
        return_col: str,
        date_col: str = "datetime",
    ) -> FactorICResult:
        """从面板数据计算逐期 IC。

        Args:
            panel: list of dict，每个 dict 代表一行数据，
                   需包含 date_col, factor_col, return_col 字段
            factor_col: 因子列名
            return_col: 收益列名（前瞻收益，已预计算）
            date_col: 日期列名

        Returns:
            FactorICResult 包含逐期 IC 序列
        """
        # 按日期分组
        groups: dict[str, list[tuple[float, float]]] = {}
        for row in panel:
            dt = str(row.get(date_col, ""))
            fv = row.get(factor_col)
            rv = row.get(return_col)
            if fv is None or rv is None:
                continue
            try:
                fv_f = float(fv)
                rv_f = float(rv)
            except (ValueError, TypeError):
                continue
            if not math.isfinite(fv_f) or not math.isfinite(rv_f):
                continue
            groups.setdefault(dt, []).append((fv_f, rv_f))

        dates = sorted(groups.keys())
        ic_values: list[float] = []
        valid_dates: list[str] = []

        for dt in dates:
            pairs = groups[dt]
            if len(pairs) < 3:
                continue
            fv_list = [p[0] for p in pairs]
            rv_list = [p[1] for p in pairs]
            ic = self.compute_ic(fv_list, rv_list)
            ic_values.append(ic)
            valid_dates.append(dt)

        return FactorICResult(
            factor_name=factor_col,
            forward_period=0,  # 由调用方指定实际期数
            ic_series=ic_values,
            dates=valid_dates,
        )

    def compute_ic_decay(
        self,
        panel: list[dict[str, Any]],
        factor_col: str,
        return_col_template: str = "fwd_ret_{period}",
        date_col: str = "datetime",
    ) -> ICDecayResult:
        """计算 IC 随前瞻期数的衰减。

        Args:
            panel: 面板数据
            factor_col: 因子列名
            return_col_template: 前瞻收益列名模板，{period} 将被替换为实际期数
            date_col: 日期列名

        Returns:
            ICDecayResult 包含各期 IC 和衰减斜率
        """
        ic_means: list[float] = []
        ic_irs: list[float] = []
        valid_periods: list[int] = []

        for period in self.forward_periods:
            ret_col = return_col_template.format(period=period)
            # 检查数据是否含有该列
            has_col = any(ret_col in row for row in panel[:1])
            if not has_col:
                continue

            result = self.compute_panel_ic(
                panel, factor_col, ret_col, date_col
            )
            result = FactorICResult(
                factor_name=result.factor_name,
                forward_period=period,
                ic_series=result.ic_series,
                dates=result.dates,
            )
            ic_means.append(result.ic_mean)
            ic_irs.append(result.ic_ir)
            valid_periods.append(period)

        # 衰减斜率
        slope = 0.0
        if len(valid_periods) >= 2:
            x = [float(p) for p in valid_periods]
            y = [abs(ic) for ic in ic_means]
            slope, _, _ = _simple_ols(x, y)

        # 半衰期估计
        half_life = _estimate_half_life(valid_periods, ic_means)

        return ICDecayResult(
            factor_name=factor_col,
            periods=valid_periods,
            ic_means=ic_means,
            ic_irs=ic_irs,
            half_life=half_life,
            decay_slope=slope,
        )

    def summary_table(
        self, results: list[FactorICResult]
    ) -> list[dict[str, Any]]:
        """生成因子 IC 汇总表。

        Args:
            results: 多个因子的 IC 分析结果

        Returns:
            list of dict，每个 dict 代表一行汇总
        """
        return [r.to_dict() for r in results]


# ========================================================================
# 内部工具函数（纯 Python，无外部依赖）
# ========================================================================


def _rank(values: list[float]) -> list[float]:
    """计算排名（平均值法处理并列）。"""
    n = len(values)
    indexed = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n

    i = 0
    while i < n:
        j = i
        while j < n - 1 and values[indexed[j]] == values[indexed[j + 1]]:
            j += 1
        rank_val = (i + j) / 2.0 + 1.0  # 1-based average rank
        for k in range(i, j + 1):
            ranks[indexed[k]] = rank_val
        i = j + 1

    return ranks


def _pearson(x: list[float], y: list[float]) -> float:
    """计算 Pearson 相关系数。"""
    n = len(x)
    if n < 2:
        return float("nan")

    mean_x = sum(x) / n
    mean_y = sum(y) / n

    cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    var_x = sum((xi - mean_x) ** 2 for xi in x)
    var_y = sum((yi - mean_y) ** 2 for yi in y)

    denom = math.sqrt(var_x * var_y)
    if denom < 1e-15:
        return 0.0

    return cov / denom


def _simple_ols(
    x: list[float], y: list[float]
) -> tuple[float, float, float]:
    """简单线性回归 y = intercept + slope * x。

    Returns: (slope, intercept, r_squared)
    """
    n = len(x)
    if n < 2:
        return 0.0, 0.0, 0.0

    sum_x = sum(x)
    sum_y = sum(y)
    sum_xy = sum(xi * yi for xi, yi in zip(x, y))
    sum_x2 = sum(xi ** 2 for xi in x)

    denom = n * sum_x2 - sum_x ** 2
    if abs(denom) < 1e-15:
        return 0.0, sum_y / n, 0.0

    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n

    mean_y = sum_y / n
    ss_total = sum((yi - mean_y) ** 2 for yi in y)
    ss_residual = sum(
        (yi - (intercept + slope * xi)) ** 2 for xi, yi in zip(x, y)
    )
    r_squared = 1 - ss_residual / ss_total if ss_total > 0 else 0.0

    return slope, intercept, r_squared


def _estimate_half_life(
    periods: list[int], ic_means: list[float]
) -> float | None:
    """估计 IC 半衰期。

    使用 |IC| 序列拟合指数衰减：|IC(t)| = a * exp(-lambda * t)
    半衰期 = ln(2) / lambda

    Returns:
        半衰期（以 period 为单位），None 表示无法估计
    """
    if len(periods) < 2:
        return None

    # 取绝对值
    abs_ic = [abs(ic) for ic in ic_means]
    if abs_ic[0] < 1e-10:
        return None

    # 对数变换: ln|IC| = ln(a) - lambda * t
    log_ic: list[float] = []
    valid_periods: list[float] = []
    for p, ic_abs in zip(periods, abs_ic):
        if ic_abs > 1e-10:
            log_ic.append(math.log(ic_abs))
            valid_periods.append(float(p))

    if len(valid_periods) < 2:
        return None

    slope, _, r2 = _simple_ols(valid_periods, log_ic)

    # slope = -lambda
    if slope >= 0:
        # IC 没有衰减
        return None

    lam = -slope
    half_life = math.log(2) / lam

    # 如果拟合太差，不报告
    if r2 < 0.1:
        return None

    return half_life
