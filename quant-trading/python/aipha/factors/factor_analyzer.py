"""因子分析流水线 — IC / ICIR / 分层回测 / 换手率分析。

知识库阈值: IC ≥ 0.04, ICIR ≥ 0.30 (D7)
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "FactorAnalyzer", "FactorReport",
    "compute_ic", "compute_icir", "compute_quantile_returns", "compute_turnover",
]

IC_THRESHOLD = 0.04
ICIR_THRESHOLD = 0.30
DEFAULT_QUANTILES = 5


@dataclass
class FactorReport:
    """单因子分析报告。"""
    factor_name: str = ""
    mean_ic: float = float("nan")
    std_ic: float = float("nan")
    icir: float = float("nan")
    ic_series: list[float] = field(default_factory=list)
    quantile_returns: dict[int, float] = field(default_factory=dict)
    long_short_return: float = float("nan")
    mean_turnover: float = float("nan")
    n_periods: int = 0
    valid_coverage: float = float("nan")

    @property
    def is_significant(self) -> bool:
        return abs(self.mean_ic) >= IC_THRESHOLD and abs(self.icir) >= ICIR_THRESHOLD


def _rank(values: list[float]) -> list[float]:
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
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    var_x = sum((xi - mx) ** 2 for xi in x)
    var_y = sum((yi - my) ** 2 for yi in y)
    denom = math.sqrt(var_x * var_y)
    return cov / denom if denom > 1e-15 else float("nan")


def compute_ic(
    factor_values: dict[str, float],
    forward_returns: dict[str, float],
    method: str = "rank",
) -> float:
    """计算单期 IC。method='rank' 为 Spearman Rank IC。"""
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
    return _pearson(f_vals, r_vals)


def compute_icir(ic_series: list[float]) -> float:
    """ICIR = mean(IC) / std(IC)"""
    valid = [v for v in ic_series if math.isfinite(v)]
    if len(valid) < 2:
        return float("nan")
    mean_ic = statistics.mean(valid)
    std_ic = statistics.stdev(valid)
    return mean_ic / std_ic if std_ic > 1e-15 else float("nan")


def compute_quantile_returns(
    factor_values: dict[str, float],
    forward_returns: dict[str, float],
    n_quantiles: int = DEFAULT_QUANTILES,
) -> dict[int, float]:
    """分层回测：按因子值分 n 分位组，计算各组平均收益。"""
    common = {
        s for s in factor_values
        if s in forward_returns
        and math.isfinite(factor_values[s])
        and math.isfinite(forward_returns[s])
    }
    if len(common) < n_quantiles:
        return {}
    sorted_symbols = sorted(common, key=lambda s: factor_values[s])
    group_size = len(sorted_symbols) / n_quantiles
    result: dict[int, float] = {}
    for q in range(n_quantiles):
        start = int(q * group_size)
        end = int((q + 1) * group_size) if q < n_quantiles - 1 else len(sorted_symbols)
        group = sorted_symbols[start:end]
        if group:
            result[q + 1] = statistics.mean(forward_returns[s] for s in group)
    return result


def compute_turnover(
    current_holdings: dict[str, float],
    previous_holdings: dict[str, float],
) -> float:
    """计算因子换手率 (双边)。"""
    all_symbols = set(current_holdings) | set(previous_holdings)
    if not all_symbols:
        return 0.0
    total_change = sum(
        abs(current_holdings.get(s, 0) - previous_holdings.get(s, 0))
        for s in all_symbols
    )
    total_value = sum(abs(v) for v in current_holdings.values()) + sum(abs(v) for v in previous_holdings.values())
    return total_change / total_value * 2.0 if total_value > 0 else 0.0


class FactorAnalyzer:
    """因子分析自动化流水线。

    用法::

        analyzer = FactorAnalyzer("momentum")
        for period in periods:
            analyzer.add_period(factor_values, forward_returns)
        report = analyzer.generate_report()
    """

    def __init__(self, factor_name: str, n_quantiles: int = DEFAULT_QUANTILES) -> None:
        self.factor_name = factor_name
        self.n_quantiles = n_quantiles
        self._ic_series: list[float] = []
        self._quantile_returns_agg: dict[int, list[float]] = {i: [] for i in range(1, n_quantiles + 1)}
        self._turnover_series: list[float] = []
        self._prev_holdings: dict[str, float] = {}
        self._n_periods = 0
        self._total_symbols = 0
        self._valid_symbols = 0

    def add_period(
        self,
        factor_values: dict[str, float],
        forward_returns: dict[str, float],
    ) -> None:
        """添加一个分析周期的数据。"""
        self._n_periods += 1
        self._total_symbols += len(factor_values)
        valid = {s for s in factor_values if math.isfinite(factor_values[s])}
        self._valid_symbols += len(valid)

        ic = compute_ic(factor_values, forward_returns)
        self._ic_series.append(ic)

        qr = compute_quantile_returns(factor_values, forward_returns, self.n_quantiles)
        for q, ret in qr.items():
            self._quantile_returns_agg.setdefault(q, []).append(ret)

        if self._prev_holdings:
            turnover = compute_turnover(factor_values, self._prev_holdings)
            self._turnover_series.append(turnover)
        self._prev_holdings = dict(factor_values)

    def generate_report(self) -> FactorReport:
        """生成因子分析报告。"""
        valid_ic = [v for v in self._ic_series if math.isfinite(v)]
        mean_ic = statistics.mean(valid_ic) if valid_ic else float("nan")
        std_ic = statistics.stdev(valid_ic) if len(valid_ic) > 1 else float("nan")
        icir = compute_icir(self._ic_series)

        quantile_avg = {}
        for q, rets in self._quantile_returns_agg.items():
            if rets:
                quantile_avg[q] = statistics.mean(rets)

        long_short = float("nan")
        if self.n_quantiles in quantile_avg and 1 in quantile_avg:
            long_short = quantile_avg[self.n_quantiles] - quantile_avg[1]

        mean_turnover = statistics.mean(self._turnover_series) if self._turnover_series else float("nan")
        coverage = self._valid_symbols / self._total_symbols if self._total_symbols > 0 else float("nan")

        return FactorReport(
            factor_name=self.factor_name,
            mean_ic=mean_ic,
            std_ic=std_ic,
            icir=icir,
            ic_series=list(self._ic_series),
            quantile_returns=quantile_avg,
            long_short_return=long_short,
            mean_turnover=mean_turnover,
            n_periods=self._n_periods,
            valid_coverage=coverage,
        )
