"""因子 IC 分析 — Rank IC / ICIR / IC衰减 / 半衰期"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


def _rank(values: list[float]) -> list[float]:
    """计算排名 (平均秩)"""
    n = len(values)
    indexed = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j < n - 1 and values[indexed[j]] == values[indexed[j + 1]]:
            j += 1
        avg_rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[indexed[k]] = avg_rank
        i = j + 1
    return ranks


def _pearson(x: list[float], y: list[float]) -> float:
    n = len(x)
    if n < 2:
        return float("nan")
    mx, my = sum(x) / n, sum(y) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sx = math.sqrt(sum((a - mx) ** 2 for a in x))
    sy = math.sqrt(sum((b - my) ** 2 for b in y))
    if sx < 1e-12 or sy < 1e-12:
        return 0.0
    return cov / (sx * sy)


@dataclass(frozen=True)
class FactorICResult:
    factor_name: str
    forward_period: int
    ic_series: list[float]
    dates: list[str]

    @property
    def ic_mean(self) -> float:
        valid = [x for x in self.ic_series if math.isfinite(x)]
        return sum(valid) / len(valid) if valid else 0.0

    @property
    def ic_std(self) -> float:
        valid = [x for x in self.ic_series if math.isfinite(x)]
        if len(valid) < 2:
            return 0.0
        mean = sum(valid) / len(valid)
        return math.sqrt(sum((x - mean) ** 2 for x in valid) / (len(valid) - 1))

    @property
    def ic_ir(self) -> float:
        std = self.ic_std
        return self.ic_mean / std if std > 1e-12 else 0.0

    @property
    def ic_positive_rate(self) -> float:
        valid = [x for x in self.ic_series if math.isfinite(x)]
        return sum(1 for x in valid if x > 0) / len(valid) if valid else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "factor_name": self.factor_name, "forward_period": self.forward_period,
            "ic_mean": round(self.ic_mean, 6), "ic_std": round(self.ic_std, 6),
            "ic_ir": round(self.ic_ir, 4), "ic_positive_rate": round(self.ic_positive_rate, 4),
            "n_periods": len([x for x in self.ic_series if math.isfinite(x)]),
        }


@dataclass(frozen=True)
class ICDecayResult:
    factor_name: str
    periods: list[int]
    ic_means: list[float]
    ic_irs: list[float]
    half_life: float | None
    decay_slope: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "factor_name": self.factor_name, "periods": self.periods,
            "ic_means": [round(x, 6) for x in self.ic_means],
            "ic_irs": [round(x, 4) for x in self.ic_irs],
            "half_life": round(self.half_life, 2) if self.half_life else None,
            "decay_slope": round(self.decay_slope, 8),
        }


class FactorICAnalyzer:
    """因子 IC 分析器 — 纯 Python 实现"""

    def __init__(self, forward_periods: list[int] | None = None):
        self.forward_periods = forward_periods or [1, 5, 10, 20]

    def compute_ic(self, factor_values: list[float], forward_returns: list[float]) -> float:
        pairs = [(f, r) for f, r in zip(factor_values, forward_returns) if math.isfinite(f) and math.isfinite(r)]
        if len(pairs) < 3:
            return float("nan")
        fv = [p[0] for p in pairs]
        rv = [p[1] for p in pairs]
        return _pearson(_rank(fv), _rank(rv))

    def compute_panel_ic(
        self,
        panel: list[dict[str, Any]],
        factor_col: str,
        return_col: str,
        date_col: str = "datetime",
    ) -> FactorICResult:
        groups: dict[str, list[tuple[float, float]]] = {}
        for row in panel:
            dt = str(row.get(date_col, ""))
            fv, rv = row.get(factor_col), row.get(return_col)
            if fv is None or rv is None:
                continue
            try:
                fv_f, rv_f = float(fv), float(rv)
            except (ValueError, TypeError):
                continue
            if math.isfinite(fv_f) and math.isfinite(rv_f):
                groups.setdefault(dt, []).append((fv_f, rv_f))

        dates = sorted(groups.keys())
        ic_values, valid_dates = [], []
        for dt in dates:
            pairs = groups[dt]
            if len(pairs) < 3:
                continue
            ic = self.compute_ic([p[0] for p in pairs], [p[1] for p in pairs])
            ic_values.append(ic)
            valid_dates.append(dt)

        return FactorICResult(factor_col, 0, ic_values, valid_dates)

    def compute_ic_decay(
        self,
        panel: list[dict[str, Any]],
        factor_col: str,
        return_col_template: str = "fwd_ret_{period}",
        date_col: str = "datetime",
    ) -> ICDecayResult:
        ic_means, ic_irs = [], []
        for period in self.forward_periods:
            ret_col = return_col_template.format(period=period)
            result = self.compute_panel_ic(panel, factor_col, ret_col, date_col)
            ic_means.append(result.ic_mean)
            ic_irs.append(result.ic_ir)

        # 线性斜率
        if len(self.forward_periods) >= 2:
            x = self.forward_periods
            y = ic_means
            mx = sum(x) / len(x)
            my = sum(y) / len(y)
            num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
            den = sum((xi - mx) ** 2 for xi in x)
            slope = num / den if den > 0 else 0.0
        else:
            slope = 0.0

        # 半衰期估计
        half_life = None
        if len(ic_means) >= 2 and ic_means[0] != 0:
            for i, m in enumerate(ic_means):
                if abs(m) <= abs(ic_means[0]) * 0.5:
                    half_life = float(self.forward_periods[i])
                    break

        return ICDecayResult(factor_col, self.forward_periods, ic_means, ic_irs, half_life, slope)
