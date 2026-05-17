"""
因子标准化和复合因子

包含：
- FactorNormalizer: 因子标准化（Z-Score, Rank, MinMax）
- WinsorizeNormalizer: 极值缩尾处理（行业标准预处理）
- MADNormalizer: 中位数绝对偏差标准化（对极端值更鲁棒）
- CompositeFactor: 复合因子（多因子加权组合）

学术参考：
- Winsorization: Dixon & Yuen (1974) "Trimming and Winsorization"
- MAD: Leys et al. (2013) "Detecting outliers: Do not use std when you can use MAD"
"""

import math
import statistics
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from aipha.factors.base import FactorResult


class FactorNormalizer(ABC):
    """因子标准化器基类"""

    @abstractmethod
    def normalize(
        self, results: list[FactorResult]
    ) -> list[FactorResult]:
        """
        标准化因子值

        Args:
            results: 原始因子结果列表

        Returns:
            标准化后的因子结果列表
        """
        raise NotImplementedError


@dataclass
class ZScoreNormalizer(FactorNormalizer):
    """Z-Score 标准化

    z = (x - mean) / std

    Attributes:
        clip: 是否裁剪极端值
        clip_range: 裁剪范围 (min, max)
        handle_nan: 如何处理 NaN 值 ('drop', 'mean', 'zero')
    """

    clip: bool = True
    clip_range: tuple[float, float] = (-3.0, 3.0)
    handle_nan: str = "drop"

    def normalize(
        self, results: list[FactorResult]
    ) -> list[FactorResult]:
        """Z-Score 标准化"""
        if not results:
            return []

        # 获取有效值
        valid_values = [r.value for r in results if not math.isnan(r.value)]

        if len(valid_values) < 2:
            # 无法计算标准差
            return self._handle_insufficient_data(results)

        # 计算均值和标准差
        mean = sum(valid_values) / len(valid_values)
        variance = sum((v - mean) ** 2 for v in valid_values) / (len(valid_values) - 1)
        std = math.sqrt(variance) if variance > 0 else 1.0

        # 标准化
        normalized = []
        for r in results:
            if math.isnan(r.value):
                z = self._handle_nan_value(mean)
            else:
                z = (r.value - mean) / std

            # 裁剪（仅对非 NaN 值）
            if self.clip and not math.isnan(z):
                z = max(self.clip_range[0], min(self.clip_range[1], z))

            normalized.append(
                FactorResult(
                    symbol=r.symbol,
                    trade_date=r.trade_date,
                    factor_name=r.factor_name,
                    value=r.value,
                    z_score=z,
                    rank=r.rank,
                    metadata={
                        **r.metadata,
                        "normalization": "z_score",
                        "mean": mean,
                        "std": std,
                    },
                )
            )

        return normalized

    def _handle_nan_value(self, mean: float) -> float:
        """处理 NaN 值"""
        if self.handle_nan == "drop":
            return float("nan")
        elif self.handle_nan == "mean":
            return 0.0  # Z-score of mean is 0
        elif self.handle_nan == "zero":
            return 0.0
        return float("nan")

    def _handle_insufficient_data(
        self, results: list[FactorResult]
    ) -> list[FactorResult]:
        """处理数据不足情况"""
        return [
            FactorResult(
                symbol=r.symbol,
                trade_date=r.trade_date,
                factor_name=r.factor_name,
                value=r.value,
                z_score=float("nan"),
                rank=r.rank,
                metadata={**r.metadata, "normalization": "z_score", "error": "insufficient_data"},
            )
            for r in results
        ]


@dataclass
class WinsorizeNormalizer(FactorNormalizer):
    """Winsorize 缩尾标准化

    将超出 [percentile_low, percentile_high] 百分位的值
    替换为边界值（缩尾），然后进行 Z-Score 标准化。

    相比简单的 clip，Winsorize 在统计计算前处理极端值，
    使得均值/方差更加稳健。

    学术参考：Dixon & Yuen (1974) "Trimming and Winsorization"

    Attributes:
        percentile_low: 下界百分位（默认 2.5%）
        percentile_high: 上界百分位（默认 97.5%）
        then_zscore: 缩尾后是否继续 Z-Score 标准化
    """

    percentile_low: float = 2.5
    percentile_high: float = 97.5
    then_zscore: bool = True

    def normalize(
        self, results: list[FactorResult]
    ) -> list[FactorResult]:
        """Winsorize + 可选 Z-Score 标准化"""
        if not results:
            return []

        valid_values = sorted(r.value for r in results if not math.isnan(r.value))
        n = len(valid_values)

        if n < 4:
            # 数据太少无法有效缩尾
            return [
                FactorResult(
                    symbol=r.symbol,
                    trade_date=r.trade_date,
                    factor_name=r.factor_name,
                    value=r.value,
                    z_score=0.0 if not math.isnan(r.value) else float("nan"),
                    metadata={**r.metadata, "normalization": "winsorize", "error": "insufficient_data"},
                )
                for r in results
            ]

        # 计算百分位边界
        low_idx = max(0, int(n * self.percentile_low / 100))
        high_idx = min(n - 1, int(n * self.percentile_high / 100))
        low_bound = valid_values[low_idx]
        high_bound = valid_values[high_idx]

        # 缩尾
        winsorized_values = []
        for r in results:
            if math.isnan(r.value):
                winsorized_values.append(float("nan"))
            else:
                winsorized_values.append(
                    max(low_bound, min(high_bound, r.value))
                )

        # 可选 Z-Score
        if self.then_zscore:
            valid_w = [v for v in winsorized_values if not math.isnan(v)]
            if len(valid_w) >= 2:
                mean = sum(valid_w) / len(valid_w)
                variance = sum((v - mean) ** 2 for v in valid_w) / (len(valid_w) - 1)
                std = math.sqrt(variance) if variance > 0 else 1.0
            else:
                mean, std = 0.0, 1.0
        else:
            mean, std = 0.0, 1.0

        normalized = []
        for r, w_val in zip(results, winsorized_values):
            if math.isnan(w_val):
                z = float("nan")
            elif self.then_zscore:
                z = (w_val - mean) / std
            else:
                z = w_val

            normalized.append(
                FactorResult(
                    symbol=r.symbol,
                    trade_date=r.trade_date,
                    factor_name=r.factor_name,
                    value=r.value,
                    z_score=z,
                    rank=r.rank,
                    metadata={
                        **r.metadata,
                        "normalization": "winsorize",
                        "winsorized_value": w_val if not math.isnan(w_val) else None,
                        "low_bound": low_bound,
                        "high_bound": high_bound,
                    },
                )
            )

        return normalized


@dataclass
class MADNormalizer(FactorNormalizer):
    """中位数绝对偏差 (MAD) 标准化

    使用中位数和 MAD 替代均值和标准差，对极端值更鲁棒。

    公式：z_mad = (x - median) / (1.4826 * MAD)
    其中 1.4826 是 MAD 到正态分布标准差的一致性校正因子。

    学术参考：
    - Leys et al. (2013) "Detecting outliers: Do not use standard deviation
      around the mean, use absolute deviation around the median"
    - Rousseeuw & Croux (1993) "Alternatives to the Median Absolute Deviation"

    Attributes:
        clip: 是否裁剪极端 z_mad 值
        clip_range: 裁剪范围
        consistency_factor: MAD 到 σ 的一致性校正
    """

    clip: bool = True
    clip_range: tuple[float, float] = (-3.0, 3.0)
    consistency_factor: float = 1.4826

    def normalize(
        self, results: list[FactorResult]
    ) -> list[FactorResult]:
        """MAD 标准化"""
        if not results:
            return []

        valid_values = [r.value for r in results if not math.isnan(r.value)]

        if len(valid_values) < 3:
            return [
                FactorResult(
                    symbol=r.symbol,
                    trade_date=r.trade_date,
                    factor_name=r.factor_name,
                    value=r.value,
                    z_score=0.0 if not math.isnan(r.value) else float("nan"),
                    metadata={**r.metadata, "normalization": "mad", "error": "insufficient_data"},
                )
                for r in results
            ]

        median_val = statistics.median(valid_values)
        mad = statistics.median(abs(v - median_val) for v in valid_values)
        mad_std = self.consistency_factor * mad if mad > 0 else 1.0

        normalized = []
        for r in results:
            if math.isnan(r.value):
                z = float("nan")
            else:
                z = (r.value - median_val) / mad_std
                if self.clip:
                    z = max(self.clip_range[0], min(self.clip_range[1], z))

            normalized.append(
                FactorResult(
                    symbol=r.symbol,
                    trade_date=r.trade_date,
                    factor_name=r.factor_name,
                    value=r.value,
                    z_score=z,
                    rank=r.rank,
                    metadata={
                        **r.metadata,
                        "normalization": "mad",
                        "median": median_val,
                        "mad": mad,
                        "mad_std": mad_std,
                    },
                )
            )

        return normalized


@dataclass
class RankNormalizer(FactorNormalizer):
    """排名标准化

    将因子值转换为 0-1 之间的排名分位数

    Attributes:
        ascending: True 表示值越小排名越靠前
        handle_nan: 如何处理 NaN 值 ('drop', 'worst', 'middle')
    """

    ascending: bool = True
    handle_nan: str = "drop"

    def normalize(
        self, results: list[FactorResult]
    ) -> list[FactorResult]:
        """排名标准化"""
        if not results:
            return []

        # 分离有效值和 NaN 值
        valid_results = [(i, r) for i, r in enumerate(results) if not math.isnan(r.value)]
        nan_results = [(i, r) for i, r in enumerate(results) if math.isnan(r.value)]

        n_valid = len(valid_results)
        if n_valid == 0:
            return self._all_nan_results(results)

        # 排序
        sorted_valid = sorted(
            valid_results,
            key=lambda x: x[1].value,
            reverse=not self.ascending,
        )

        # 分配排名
        rank_map: dict[int, float] = {}
        for rank_idx, (orig_idx, _) in enumerate(sorted_valid):
            # 排名归一化到 0-1，使用中点法 (rank + 0.5) / n，避免端点 0/1
            # 注: base.py 使用端点法 rank/(n-1)，两者设计意图不同
            rank_map[orig_idx] = (rank_idx + 0.5) / n_valid

        # 处理 NaN
        for orig_idx, _ in nan_results:
            if self.handle_nan == "worst":
                rank_map[orig_idx] = 1.0 if self.ascending else 0.0
            elif self.handle_nan == "middle":
                rank_map[orig_idx] = 0.5
            else:  # drop
                rank_map[orig_idx] = float("nan")

        # 生成结果
        normalized = []
        for i, r in enumerate(results):
            normalized.append(
                FactorResult(
                    symbol=r.symbol,
                    trade_date=r.trade_date,
                    factor_name=r.factor_name,
                    value=r.value,
                    rank=rank_map[i],
                    z_score=r.z_score,
                    metadata={
                        **r.metadata,
                        "normalization": "rank",
                        "n_valid": n_valid,
                    },
                )
            )

        return normalized

    def _all_nan_results(
        self, results: list[FactorResult]
    ) -> list[FactorResult]:
        """所有值都是 NaN"""
        return [
            FactorResult(
                symbol=r.symbol,
                trade_date=r.trade_date,
                factor_name=r.factor_name,
                value=r.value,
                rank=float("nan"),
                z_score=r.z_score,
                metadata={**r.metadata, "normalization": "rank", "error": "all_nan"},
            )
            for r in results
        ]


@dataclass
class MinMaxNormalizer(FactorNormalizer):
    """MinMax 标准化

    x_norm = (x - min) / (max - min)

    Attributes:
        feature_range: 目标范围 (min, max)
        handle_nan: 如何处理 NaN 值
    """

    feature_range: tuple[float, float] = (0.0, 1.0)
    handle_nan: str = "drop"

    def normalize(
        self, results: list[FactorResult]
    ) -> list[FactorResult]:
        """MinMax 标准化"""
        if not results:
            return []

        valid_values = [r.value for r in results if not math.isnan(r.value)]

        if len(valid_values) == 0:
            return self._all_nan_results(results)

        v_min = min(valid_values)
        v_max = max(valid_values)
        v_range = v_max - v_min

        if v_range == 0:
            # 所有值相同
            mid = (self.feature_range[0] + self.feature_range[1]) / 2
            return self._constant_results(results, mid, v_min, v_max)

        f_min, f_max = self.feature_range
        f_range = f_max - f_min

        normalized = []
        for r in results:
            if math.isnan(r.value):
                norm_value = self._handle_nan()
            else:
                norm_value = f_min + (r.value - v_min) / v_range * f_range

            normalized.append(
                FactorResult(
                    symbol=r.symbol,
                    trade_date=r.trade_date,
                    factor_name=r.factor_name,
                    value=norm_value,
                    rank=r.rank,
                    z_score=r.z_score,
                    metadata={
                        **r.metadata,
                        "normalization": "minmax",
                        "original_value": r.value,
                        "min": v_min,
                        "max": v_max,
                    },
                )
            )

        return normalized

    def _handle_nan(self) -> float:
        """处理 NaN 值"""
        if self.handle_nan == "drop":
            return float("nan")
        elif self.handle_nan == "middle":
            return (self.feature_range[0] + self.feature_range[1]) / 2
        return float("nan")

    def _all_nan_results(
        self, results: list[FactorResult]
    ) -> list[FactorResult]:
        """所有值都是 NaN"""
        return [
            FactorResult(
                symbol=r.symbol,
                trade_date=r.trade_date,
                factor_name=r.factor_name,
                value=float("nan"),
                metadata={**r.metadata, "normalization": "minmax", "error": "all_nan"},
            )
            for r in results
        ]

    def _constant_results(
        self,
        results: list[FactorResult],
        value: float,
        v_min: float,
        v_max: float,
    ) -> list[FactorResult]:
        """所有值相同"""
        return [
            FactorResult(
                symbol=r.symbol,
                trade_date=r.trade_date,
                factor_name=r.factor_name,
                value=value if not math.isnan(r.value) else float("nan"),
                metadata={
                    **r.metadata,
                    "normalization": "minmax",
                    "constant": True,
                    "min": v_min,
                    "max": v_max,
                },
            )
            for r in results
        ]


@dataclass
class CompositeFactor:
    """复合因子

    将多个因子加权组合成一个综合因子分数

    Attributes:
        weights: 因子权重 {factor_name: weight}
        normalize_method: 标准化方法 ('z_score', 'rank', 'minmax', 'none')
        weight_normalize: 是否归一化权重使其和为 1
    """

    weights: dict[str, float] = field(default_factory=dict)
    normalize_method: str = "z_score"
    weight_normalize: bool = True
    name: str = "COMPOSITE"

    def __post_init__(self):
        """初始化后处理"""
        if self.weight_normalize and self.weights:
            total = sum(abs(w) for w in self.weights.values())
            if total > 0:
                self.weights = {k: v / total for k, v in self.weights.items()}

    def combine(
        self, factor_results: dict[str, list[FactorResult]]
    ) -> list[FactorResult]:
        """
        组合多个因子

        Args:
            factor_results: {factor_name: [FactorResult, ...]}

        Returns:
            组合后的因子结果列表
        """
        if not factor_results:
            return []

        # 获取标准化器
        normalizer = self._get_normalizer()

        # 标准化各因子
        normalized_factors: dict[str, dict[str, float]] = {}
        for factor_name, results in factor_results.items():
            if normalizer:
                norm_results = normalizer.normalize(results)
            else:
                norm_results = results

            # 获取标准化后的值
            for r in norm_results:
                if r.symbol not in normalized_factors:
                    normalized_factors[r.symbol] = {}

                # 使用 z_score（如果有）或原始 value
                if self.normalize_method == "z_score" and r.z_score is not None:
                    normalized_factors[r.symbol][factor_name] = r.z_score
                elif self.normalize_method == "rank" and r.rank is not None:
                    normalized_factors[r.symbol][factor_name] = r.rank
                else:
                    normalized_factors[r.symbol][factor_name] = r.value

        # 计算加权组合分数
        combined = []
        sample_result = next(iter(next(iter(factor_results.values()))))

        for symbol, factor_values in normalized_factors.items():
            score = 0.0
            valid_weight = 0.0

            for factor_name, weight in self.weights.items():
                if factor_name in factor_values:
                    value = factor_values[factor_name]
                    if not math.isnan(value):
                        score += weight * value
                        valid_weight += abs(weight)

            # 重新归一化分数
            if valid_weight > 0:
                final_score = score / valid_weight if self.weight_normalize else score
            else:
                final_score = float("nan")

            combined.append(
                FactorResult(
                    symbol=symbol,
                    trade_date=sample_result.trade_date,
                    factor_name=self.name,
                    value=final_score,
                    metadata={
                        "component_factors": list(factor_values.keys()),
                        "weights": self.weights,
                        "normalize_method": self.normalize_method,
                    },
                )
            )

        return combined

    def _get_normalizer(self) -> Optional[FactorNormalizer]:
        """获取标准化器"""
        return get_normalizer(self.normalize_method)


# ============================================================================
# 配置驱动的标准化器工厂
# ============================================================================

# 标准化器注册表：名称 → 构造函数
_NORMALIZER_REGISTRY: dict[str, type[FactorNormalizer]] = {
    "z_score": ZScoreNormalizer,
    "zscore": ZScoreNormalizer,
    "rank": RankNormalizer,
    "minmax": MinMaxNormalizer,
    "winsorize": WinsorizeNormalizer,
    "mad": MADNormalizer,
}


def get_normalizer(
    name: str | None,
    **kwargs: Any,
) -> FactorNormalizer | None:
    """从名称创建标准化器实例（配置驱动）。

    支持的名称：
        - ``"z_score"`` / ``"zscore"``: Z-Score 标准化
        - ``"rank"``: 排名百分位
        - ``"minmax"``: Min-Max 缩放
        - ``"winsorize"``: 极值缩尾
        - ``"mad"``: 中位数绝对偏差

    Args:
        name: 标准化器名称（不区分大小写）。为 ``None`` 或空字符串时返回 ``None``。
        **kwargs: 传递给标准化器构造函数的参数。

    Returns:
        ``FactorNormalizer`` 实例，或当 *name* 为空时返回 ``None``。

    Raises:
        ValueError: 当名称未在注册表中找到时抛出。
    """
    if not name:
        return None
    key = name.strip().lower()
    if key == "none":
        return None
    cls = _NORMALIZER_REGISTRY.get(key)
    if cls is None:
        available = ", ".join(sorted(_NORMALIZER_REGISTRY.keys()))
        raise ValueError(
            f"未知的标准化器名称 '{name}'，可用: {available}"
        )
    return cls(**kwargs)
