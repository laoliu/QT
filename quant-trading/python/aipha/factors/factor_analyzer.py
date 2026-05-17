"""因子分析自动化流水线（C-3）

知识库 G1 + O3：面向 A 股和 CB 的单因子评估工具，对齐 alphalens 设计理念。

主要功能：
  - IC / Rank IC 时序计算（Pearson & Spearman）
  - ICIR（IC均值 / IC标准差）
  - 分层回测（5分位多空展开）
  - 因子换手率分析（Fama-MacBeth 截面换手）
  - 自动生成因子评估报告字典（可接入 HTML 报告）

知识库参考：
  - D7: IC ≥ 0.04, ICIR ≥ 0.30, 年化收益 ≥ 10%
  - O3: Fama-MacBeth 截面归因
  - G1: 因子分析流水线自动化
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "FactorAnalyzer",
    "FactorReport",
    "compute_ic",
    "compute_icir",
    "compute_quantile_returns",
    "compute_turnover",
]

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

IC_THRESHOLD = 0.04       # 知识库 D7 IC 显著性门槛
ICIR_THRESHOLD = 0.30     # 知识库 D7 ICIR 显著性门槛
DEFAULT_QUANTILES = 5     # 默认分位数


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class FactorReport:
    """单因子分析报告（不可变结果快照）。"""

    factor_name: str = ""
    mean_ic: float = float("nan")
    std_ic: float = float("nan")
    icir: float = float("nan")
    ic_series: list[float] = field(default_factory=list)
    quantile_returns: dict[int, float] = field(default_factory=dict)
    long_short_return: float = float("nan")
    mean_turnover: float = float("nan")
    n_periods: int = 0
    valid_coverage: float = float("nan")  # 有效因子值覆盖率

    @property
    def is_significant(self) -> bool:
        """是否达到知识库 D7 显著性门槛。"""
        return (
            abs(self.mean_ic) >= IC_THRESHOLD
            and abs(self.icir) >= ICIR_THRESHOLD
        )


# ---------------------------------------------------------------------------
# 独立函数
# ---------------------------------------------------------------------------

def compute_ic(
    factor_values: dict[str, float],
    forward_returns: dict[str, float],
    method: str = "rank",
) -> float:
    """计算单期 IC（知识库 D7）。

    Args:
        factor_values: {symbol: factor_val}，NaN 值将被跳过
        forward_returns: {symbol: fwd_return}，NaN 值将被跳过
        method: "rank"（Spearman Rank IC）或 "pearson"

    Returns:
        IC 值，-1~1；若有效样本 < 5 则返回 NaN
    """
    # 取交集，过滤 NaN
    common = {
        s for s in factor_values
        if s in forward_returns
        and math.isfinite(factor_values[s])
        and math.isfinite(forward_returns[s])
    }
    if len(common) < 5:
        return float("nan")

    symbols = sorted(common)
    f_vals = [factor_values[s] for s in symbols]
    r_vals = [forward_returns[s] for s in symbols]

    if method == "rank":
        f_vals = _rank(f_vals)
        r_vals = _rank(r_vals)

    # Pearson 相关
    return _pearson(f_vals, r_vals)


def _rank(values: list[float]) -> list[float]:
    """将列表转换为从1开始的排名（均值处理同名次）。"""
    n = len(values)
    indexed = sorted(enumerate(values), key=lambda x: x[1])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j < n - 1 and indexed[j + 1][1] == indexed[j][1]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = avg_rank
        i = j + 1
    return ranks


def _pearson(x: list[float], y: list[float]) -> float:
    """计算 Pearson 相关系数。"""
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    var_x = sum((xi - mx) ** 2 for xi in x)
    var_y = sum((yi - my) ** 2 for yi in y)
    denom = math.sqrt(var_x * var_y)
    if denom < 1e-15:
        return float("nan")
    return cov / denom


def compute_icir(ic_series: list[float]) -> float:
    """计算 ICIR = mean(IC) / std(IC)。

    Args:
        ic_series: 各期 IC 列表（NaN 将被过滤）
    Returns:
        ICIR，若有效 IC < 2 或 std=0 则返回 NaN
    """
    valid = [v for v in ic_series if math.isfinite(v)]
    if len(valid) < 2:
        return float("nan")
    mean_ic = statistics.mean(valid)
    std_ic = statistics.stdev(valid)
    if std_ic < 1e-15:
        return float("nan")
    return mean_ic / std_ic


def compute_quantile_returns(
    factor_values: dict[str, float],
    forward_returns: dict[str, float],
    n_quantiles: int = DEFAULT_QUANTILES,
) -> dict[int, float]:
    """分层回测：按因子值分 n 分位组，计算各组平均收益（知识库 G1 分层回测）。

    Args:
        factor_values: {symbol: factor_val}
        forward_returns: {symbol: fwd_return}
        n_quantiles: 分位数（2-20，默认5）

    Returns:
        {1: avg_return_q1, ..., n: avg_return_qn}，分位1为空值最小的组
    """
    common = {
        s for s in factor_values
        if s in forward_returns
        and math.isfinite(factor_values[s])
        and math.isfinite(forward_returns[s])
    }
    if len(common) < n_quantiles:
        return {}

    symbols_sorted = sorted(common, key=lambda s: factor_values[s])
    n = len(symbols_sorted)
    result: dict[int, float] = {}
    for q in range(n_quantiles):
        lo = int(q * n / n_quantiles)
        hi = int((q + 1) * n / n_quantiles)
        group = symbols_sorted[lo:hi]
        if group:
            returns = [forward_returns[s] for s in group]
            result[q + 1] = statistics.mean(returns)
    return result


def compute_turnover(
    factor_prev: dict[str, float],
    factor_curr: dict[str, float],
    top_pct: float = 0.20,
) -> float:
    """因子换手率（知识库 O3）：相邻两期 top_pct 选股集合的 Jaccard 距离。

    Args:
        factor_prev: 上一期因子值
        factor_curr: 本期因子值
        top_pct: 选股比例（取前 top_pct 的股票作为选股集）

    Returns:
        换手率 0~1；1 = 完全换仓，0 = 完全不变
    """
    def _top_set(fvals: dict[str, float]) -> set[str]:
        valid = {s: v for s, v in fvals.items() if math.isfinite(v)}
        if not valid:
            return set()
        k = max(1, int(len(valid) * top_pct))
        sorted_s = sorted(valid, key=lambda s: valid[s], reverse=True)
        return set(sorted_s[:k])

    prev_top = _top_set(factor_prev)
    curr_top = _top_set(factor_curr)
    if not prev_top and not curr_top:
        return float("nan")
    union = prev_top | curr_top
    intersection = prev_top & curr_top
    if not union:
        return float("nan")
    return 1.0 - len(intersection) / len(union)


# ---------------------------------------------------------------------------
# 主分析类
# ---------------------------------------------------------------------------

class FactorAnalyzer:
    """单因子分析流水线。

    用法::

        fa = FactorAnalyzer("ep_factor")
        fa.add_period(factor_vals, forward_returns)
        report = fa.generate_report()
        print(report.icir)
    """

    def __init__(self, factor_name: str = "", n_quantiles: int = DEFAULT_QUANTILES) -> None:
        self.factor_name = factor_name
        self.n_quantiles = n_quantiles
        # 各期数据
        self._ic_series: list[float] = []
        self._quantile_returns_all: list[dict[int, float]] = []
        self._turnover_series: list[float] = []
        self._prev_factor: dict[str, float] | None = None
        self._total_symbols: int = 0
        self._valid_symbols: int = 0
        self._period_count: int = 0

    def add_period(
        self,
        factor_values: dict[str, float],
        forward_returns: dict[str, float],
    ) -> None:
        """添加一期数据。

        Args:
            factor_values: {symbol: factor_val} —— 当期截面因子值
            forward_returns: {symbol: fwd_return} —— 对应前瞻收益（无前视偏差）
        """
        self._period_count += 1
        self._total_symbols += len(factor_values)
        valid_count = sum(
            1 for s in factor_values
            if math.isfinite(factor_values.get(s, float("nan")))
        )
        self._valid_symbols += valid_count

        # IC
        ic = compute_ic(factor_values, forward_returns)
        self._ic_series.append(ic)

        # 分层回测
        q_rets = compute_quantile_returns(
            factor_values, forward_returns, n_quantiles=self.n_quantiles
        )
        self._quantile_returns_all.append(q_rets)

        # 换手率
        if self._prev_factor is not None:
            to = compute_turnover(self._prev_factor, factor_values)
            self._turnover_series.append(to)
        self._prev_factor = dict(factor_values)

    def generate_report(self) -> FactorReport:
        """汇总所有已添加期次，生成因子分析报告。"""
        valid_ic = [v for v in self._ic_series if math.isfinite(v)]
        mean_ic = statistics.mean(valid_ic) if valid_ic else float("nan")
        std_ic = statistics.stdev(valid_ic) if len(valid_ic) >= 2 else float("nan")
        icir = compute_icir(self._ic_series)

        # 各分位组收益均值（跨期平均）
        agg_q: dict[int, list[float]] = {q: [] for q in range(1, self.n_quantiles + 1)}
        for q_rets in self._quantile_returns_all:
            for q, r in q_rets.items():
                if q in agg_q and math.isfinite(r):
                    agg_q[q].append(r)
        quantile_avg = {
            q: statistics.mean(vals) if vals else float("nan")
            for q, vals in agg_q.items()
        }

        # 多空收益：最高分位组 - 最低分位组
        q_hi = quantile_avg.get(self.n_quantiles, float("nan"))
        q_lo = quantile_avg.get(1, float("nan"))
        ls_return = (
            q_hi - q_lo
            if math.isfinite(q_hi) and math.isfinite(q_lo)
            else float("nan")
        )

        # 换手率
        valid_to = [v for v in self._turnover_series if math.isfinite(v)]
        mean_turnover = statistics.mean(valid_to) if valid_to else float("nan")

        # 覆盖率
        coverage = (
            self._valid_symbols / self._total_symbols
            if self._total_symbols > 0
            else float("nan")
        )

        return FactorReport(
            factor_name=self.factor_name,
            mean_ic=mean_ic,
            std_ic=std_ic,
            icir=icir,
            ic_series=list(self._ic_series),
            quantile_returns=quantile_avg,
            long_short_return=ls_return,
            mean_turnover=mean_turnover,
            n_periods=self._period_count,
            valid_coverage=coverage,
        )

    def reset(self) -> None:
        """清空所有历史数据。"""
        self._ic_series.clear()
        self._quantile_returns_all.clear()
        self._turnover_series.clear()
        self._prev_factor = None
        self._total_symbols = 0
        self._valid_symbols = 0
        self._period_count = 0
