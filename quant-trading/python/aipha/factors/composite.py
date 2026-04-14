"""因子标准化与复合因子。

- ZScoreNormalizer / RankNormalizer / MinMaxNormalizer / WinsorizeNormalizer / MADNormalizer
- CompositeFactor: 多因子加权组合
"""
from __future__ import annotations

import math
import statistics
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from aipha.factors.base import FactorResult


class FactorNormalizer(ABC):
    """因子标准化器基类"""

    @abstractmethod
    def normalize(self, results: list[FactorResult]) -> list[FactorResult]:
        raise NotImplementedError


@dataclass
class ZScoreNormalizer(FactorNormalizer):
    """Z-Score 标准化"""
    clip: float = 3.0

    def normalize(self, results: list[FactorResult]) -> list[FactorResult]:
        values = [r.value for r in results if math.isfinite(r.value)]
        if len(values) < 2:
            return results
        mean = statistics.mean(values)
        std = statistics.stdev(values)
        if std < 1e-15:
            return results
        out = []
        for r in results:
            z = (r.value - mean) / std if math.isfinite(r.value) else float("nan")
            z = max(-self.clip, min(self.clip, z))
            out.append(FactorResult(
                symbol=r.symbol, trade_date=r.trade_date,
                factor_name=r.factor_name, value=r.value,
                z_score=z, rank=r.rank, metadata=r.metadata,
            ))
        return out


@dataclass
class RankNormalizer(FactorNormalizer):
    """排名百分位标准化 (0~1)"""

    def normalize(self, results: list[FactorResult]) -> list[FactorResult]:
        valid = [(i, r) for i, r in enumerate(results) if math.isfinite(r.value)]
        if not valid:
            return results
        sorted_items = sorted(valid, key=lambda x: x[1].value)
        n = len(sorted_items)
        rank_map: dict[int, float] = {}
        for rank_idx, (orig_idx, _) in enumerate(sorted_items):
            rank_map[orig_idx] = (rank_idx + 1) / n
        out = []
        for i, r in enumerate(results):
            out.append(FactorResult(
                symbol=r.symbol, trade_date=r.trade_date,
                factor_name=r.factor_name, value=r.value,
                rank=rank_map.get(i, float("nan")), z_score=r.z_score,
                metadata=r.metadata,
            ))
        return out


@dataclass
class MinMaxNormalizer(FactorNormalizer):
    """Min-Max 标准化到 [0, 1]"""

    def normalize(self, results: list[FactorResult]) -> list[FactorResult]:
        values = [r.value for r in results if math.isfinite(r.value)]
        if len(values) < 2:
            return results
        vmin, vmax = min(values), max(values)
        rng = vmax - vmin
        if rng < 1e-15:
            return results
        out = []
        for r in results:
            normed = (r.value - vmin) / rng if math.isfinite(r.value) else float("nan")
            out.append(FactorResult(
                symbol=r.symbol, trade_date=r.trade_date,
                factor_name=r.factor_name, value=normed,
                rank=r.rank, z_score=r.z_score, metadata=r.metadata,
            ))
        return out


@dataclass
class WinsorizeNormalizer(FactorNormalizer):
    """极值缩尾处理。Dixon & Yuen (1974)"""
    lower_pct: float = 0.025
    upper_pct: float = 0.975

    def normalize(self, results: list[FactorResult]) -> list[FactorResult]:
        values = sorted(r.value for r in results if math.isfinite(r.value))
        if len(values) < 4:
            return results
        lo_idx = max(0, int(len(values) * self.lower_pct))
        hi_idx = min(len(values) - 1, int(len(values) * self.upper_pct))
        lo_val, hi_val = values[lo_idx], values[hi_idx]
        out = []
        for r in results:
            v = r.value
            if math.isfinite(v):
                v = max(lo_val, min(hi_val, v))
            out.append(FactorResult(
                symbol=r.symbol, trade_date=r.trade_date,
                factor_name=r.factor_name, value=v,
                rank=r.rank, z_score=r.z_score, metadata=r.metadata,
            ))
        return out


@dataclass
class MADNormalizer(FactorNormalizer):
    """中位数绝对偏差标准化。Leys et al. (2013)"""
    clip_factor: float = 5.2

    def normalize(self, results: list[FactorResult]) -> list[FactorResult]:
        values = sorted(r.value for r in results if math.isfinite(r.value))
        if len(values) < 3:
            return results
        median = values[len(values) // 2]
        mad = statistics.median(abs(v - median) for v in values)
        if mad < 1e-15:
            return results
        out = []
        for r in results:
            v = r.value
            if math.isfinite(v):
                z = (v - median) / (1.4826 * mad)
                v = max(-self.clip_factor, min(self.clip_factor, z))
            out.append(FactorResult(
                symbol=r.symbol, trade_date=r.trade_date,
                factor_name=r.factor_name, value=v,
                rank=r.rank, z_score=r.z_score, metadata=r.metadata,
            ))
        return out


class CompositeFactor:
    """复合因子：多因子加权组合。

    用法::

        composite = CompositeFactor(
            weights={"pe": -0.2, "roe": 0.3, "momentum": 0.2},
            normalizer=ZScoreNormalizer(),
        )
        score = composite.compute({"pe": 15.2, "roe": 0.18, "momentum": 0.12})
    """

    def __init__(
        self,
        weights: dict[str, float],
        normalizer: FactorNormalizer | None = None,
    ) -> None:
        self.weights = weights
        self.normalizer = normalizer

    def compute(self, factor_values: dict[str, float]) -> float:
        """计算复合因子值。"""
        score = 0.0
        total_weight = 0.0
        for name, weight in self.weights.items():
            val = factor_values.get(name)
            if val is not None and math.isfinite(val):
                score += val * weight
                total_weight += abs(weight)
        return score / total_weight if total_weight > 0 else 0.0

    def compute_cross_section(
        self,
        factor_matrix: dict[str, dict[str, float]],
    ) -> dict[str, float]:
        """批量计算截面复合因子。

        Args:
            factor_matrix: {factor_name: {symbol: value}}

        Returns:
            {symbol: composite_score}
        """
        all_symbols: set[str] = set()
        for fv in factor_matrix.values():
            all_symbols.update(fv.keys())

        scores: dict[str, float] = {}
        for symbol in all_symbols:
            vals = {name: factor_matrix[name].get(symbol, float("nan"))
                    for name in self.weights if name in factor_matrix}
            scores[symbol] = self.compute(vals)
        return scores
