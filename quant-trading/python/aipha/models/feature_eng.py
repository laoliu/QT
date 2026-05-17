"""
ML特征工程

提供特征处理、转换和选择能力。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd


logger = logging.getLogger(__name__)


class ScalerType(str, Enum):
    """标准化类型"""

    STANDARD = "standard"  # Z-score
    MINMAX = "minmax"  # Min-Max
    ROBUST = "robust"  # Robust (中位数/IQR)
    RANK = "rank"  # 排名
    QUANTILE = "quantile"  # 分位数


@dataclass
class FeatureConfig:
    """特征配置"""

    name: str
    scaler: ScalerType = ScalerType.STANDARD
    fillna_method: str = "median"  # mean / median / zero / forward / drop
    winsorize: bool = True
    winsorize_percentile: tuple[float, float] = (0.01, 0.99)
    neutralize: bool = False  # 是否市值/行业中性化
    lag: int = 0  # 滞后期数


class FeatureTransformer(ABC):
    """特征转换器基类"""

    @abstractmethod
    def fit(self, X: pd.DataFrame) -> "FeatureTransformer":
        """拟合"""
        ...

    @abstractmethod
    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        """转换"""
        ...

    def fit_transform(self, X: pd.DataFrame) -> pd.DataFrame:
        """拟合并转换"""
        return self.fit(X).transform(X)


class StandardScaler(FeatureTransformer):
    """标准化转换器"""

    def __init__(self, columns: list[str] | None = None):
        self.columns = columns
        self._means: dict[str, float] = {}
        self._stds: dict[str, float] = {}

    def fit(self, X: pd.DataFrame) -> "StandardScaler":
        """计算各列均值和标准差用于后续标准化。"""
        cols = self.columns or list(X.select_dtypes(include=[np.number]).columns)
        for col in cols:
            if col in X.columns:
                self._means[col] = float(X[col].mean())
                self._stds[col] = float(X[col].std())
        return self

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        """对数据进行 Z-score 标准化转换。"""
        X = X.copy()
        for col, mean in self._means.items():
            if col in X.columns:
                std = self._stds[col]
                if std > 0:
                    X[col] = (X[col] - mean) / std
                else:
                    X[col] = X[col] - mean
        return X


class RankScaler(FeatureTransformer):
    """排名转换器"""

    def __init__(self, columns: list[str] | None = None):
        self.columns = columns

    def fit(self, X: pd.DataFrame) -> "RankScaler":
        """无需拟合，直接返回自身。"""
        return self

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        """将数值列转换为百分比排名。"""
        X = X.copy()
        cols = self.columns or list(X.select_dtypes(include=[np.number]).columns)
        for col in cols:
            if col in X.columns:
                X[col] = X[col].rank(pct=True)
        return X


class Winsorizer(FeatureTransformer):
    """缩尾处理器"""

    def __init__(
        self,
        columns: list[str] | None = None,
        lower: float = 0.01,
        upper: float = 0.99,
    ):
        self.columns = columns
        self.lower = lower
        self.upper = upper
        self._bounds: dict[str, tuple[float, float]] = {}

    def fit(self, X: pd.DataFrame) -> "Winsorizer":
        """计算各列缩尾边界分位数。"""
        cols = self.columns or list(X.select_dtypes(include=[np.number]).columns)
        for col in cols:
            if col in X.columns:
                lower_val = float(X[col].quantile(self.lower))
                upper_val = float(X[col].quantile(self.upper))
                self._bounds[col] = (lower_val, upper_val)
        return self

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        """对数据进行缩尾处理，将极端值裁剪到分位数边界内。"""
        X = X.copy()
        for col, (lower_val, upper_val) in self._bounds.items():
            if col in X.columns:
                X[col] = X[col].clip(lower=lower_val, upper=upper_val)
        return X


class MissingValueHandler(FeatureTransformer):
    """缺失值处理器"""

    def __init__(
        self,
        columns: list[str] | None = None,
        method: str = "median",  # mean / median / zero / forward / drop
    ):
        self.columns = columns
        self.method = method
        self._fill_values: dict[str, float] = {}

    def fit(self, X: pd.DataFrame) -> "MissingValueHandler":
        """计算缺失值填充所需的统计量（均值或中位数）。"""
        if self.method in ("mean", "median"):
            cols = self.columns or list(X.select_dtypes(include=[np.number]).columns)
            for col in cols:
                if col in X.columns:
                    if self.method == "mean":
                        self._fill_values[col] = float(X[col].mean())
                    else:
                        self._fill_values[col] = float(X[col].median())
        return self

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        """根据配置方法处理缺失值（填充、前向、丢弃等）。"""
        X = X.copy()

        if self.method == "drop":
            return X.dropna(subset=self.columns) if self.columns else X.dropna()

        if self.method == "forward":
            cols = self.columns or X.columns.tolist()
            for col in cols:
                if col in X.columns:
                    X[col] = X[col].ffill()
            return X

        if self.method == "zero":
            cols = self.columns or list(X.select_dtypes(include=[np.number]).columns)
            for col in cols:
                if col in X.columns:
                    X[col] = X[col].fillna(0)
            return X

        # mean / median
        for col, value in self._fill_values.items():
            if col in X.columns:
                X[col] = X[col].fillna(value)

        return X


class Neutralizer(FeatureTransformer):
    """中性化处理器

    对因子进行市值/行业中性化。
    """

    def __init__(
        self,
        feature_cols: list[str],
        market_cap_col: str = "market_cap",
        industry_col: str = "industry",
        neutralize_market: bool = True,
        neutralize_industry: bool = True,
    ):
        self.feature_cols = feature_cols
        self.market_cap_col = market_cap_col
        self.industry_col = industry_col
        self.neutralize_market = neutralize_market
        self.neutralize_industry = neutralize_industry

    def fit(self, X: pd.DataFrame) -> "Neutralizer":
        """无需拟合，直接返回自身（中性化在 transform 阶段完成）。"""
        return self

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        """对因子进行市值/行业回归中性化，返回残差。"""
        X = X.copy()

        for col in self.feature_cols:
            if col not in X.columns:
                continue

            y = X[col].values

            # 构建回归矩阵
            regressors = []

            if self.neutralize_market and self.market_cap_col in X.columns:
                # 使用对数市值
                log_cap = np.log(X[self.market_cap_col].values + 1)
                regressors.append(log_cap)

            if self.neutralize_industry and self.industry_col in X.columns:
                # 行业哑变量
                industry_dummies = pd.get_dummies(X[self.industry_col], prefix="ind")
                for dummy_col in industry_dummies.columns:
                    regressors.append(industry_dummies[dummy_col].values)

            if not regressors:
                continue

            # 回归
            X_reg = np.column_stack([np.ones(len(y)), *regressors])

            # 处理NaN
            mask = ~np.isnan(y) & ~np.any(np.isnan(X_reg), axis=1)
            if mask.sum() < 10:
                continue

            try:
                beta = np.linalg.lstsq(X_reg[mask], y[mask], rcond=None)[0]
                residuals = y - X_reg @ beta
                X[col] = residuals
            except Exception as e:
                logger.warning(f"Failed to neutralize {col}: {e}")

        return X


class LagFeatureGenerator(FeatureTransformer):
    """滞后特征生成器"""

    def __init__(
        self,
        columns: list[str],
        lags: list[int],
        date_col: str = "date",
        symbol_col: str = "symbol",
    ):
        self.columns = columns
        self.lags = lags
        self.date_col = date_col
        self.symbol_col = symbol_col

    def fit(self, X: pd.DataFrame) -> "LagFeatureGenerator":
        """无需拟合，直接返回自身。"""
        return self

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        """生成指定列的滞后特征（按股票分组或全局）。"""
        X = X.copy()

        if self.symbol_col in X.columns:
            # 按股票分组
            for col in self.columns:
                if col not in X.columns:
                    continue
                for lag in self.lags:
                    X[f"{col}_lag{lag}"] = X.groupby(self.symbol_col)[col].shift(lag)
        else:
            # 简单滞后
            for col in self.columns:
                if col not in X.columns:
                    continue
                for lag in self.lags:
                    X[f"{col}_lag{lag}"] = X[col].shift(lag)

        return X


class RollingFeatureGenerator(FeatureTransformer):
    """滚动特征生成器"""

    def __init__(
        self,
        columns: list[str],
        windows: list[int],
        functions: list[str],  # mean / std / min / max / sum / skew / kurt
        symbol_col: str = "symbol",
    ):
        self.columns = columns
        self.windows = windows
        self.functions = functions
        self.symbol_col = symbol_col

    def fit(self, X: pd.DataFrame) -> "RollingFeatureGenerator":
        """无需拟合，直接返回自身。"""
        return self

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        """生成滚动窗口统计特征（均值、标准差、偏度等）。"""
        X = X.copy()

        func_map = {
            "mean": lambda x: x.mean(),
            "std": lambda x: x.std(),
            "min": lambda x: x.min(),
            "max": lambda x: x.max(),
            "sum": lambda x: x.sum(),
            "skew": lambda x: x.skew(),
            "kurt": lambda x: x.kurt(),
        }

        for col in self.columns:
            if col not in X.columns:
                continue

            for window in self.windows:
                for func_name in self.functions:
                    if func_name not in func_map:
                        continue

                    new_col = f"{col}_roll{window}_{func_name}"

                    if self.symbol_col in X.columns:
                        X[new_col] = X.groupby(self.symbol_col)[col].transform(
                            lambda s: s.rolling(window, min_periods=1).apply(
                                func_map[func_name]
                            )
                        )
                    else:
                        X[new_col] = X[col].rolling(window, min_periods=1).apply(
                            func_map[func_name]
                        )

        return X


class FeaturePipeline:
    """特征处理管道"""

    def __init__(self):
        self._steps: list[tuple[str, FeatureTransformer]] = []

    def add_step(
        self,
        name: str,
        transformer: FeatureTransformer,
    ) -> "FeaturePipeline":
        """添加处理步骤"""
        self._steps.append((name, transformer))
        return self

    def fit(self, X: pd.DataFrame) -> "FeaturePipeline":
        """拟合管道"""
        df = X.copy()
        for name, transformer in self._steps:
            logger.debug(f"Fitting step: {name}")
            transformer.fit(df)
            df = transformer.transform(df)
        return self

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        """应用管道"""
        df = X.copy()
        for name, transformer in self._steps:
            logger.debug(f"Transforming step: {name}")
            df = transformer.transform(df)
        return df

    def fit_transform(self, X: pd.DataFrame) -> pd.DataFrame:
        """拟合并应用"""
        return self.fit(X).transform(X)

    def get_steps(self) -> list[str]:
        """获取步骤名列表"""
        return [name for name, _ in self._steps]


class FeatureSelector:
    """特征选择器"""

    def __init__(self, method: str = "importance"):
        self.method = method
        self._selected_features: list[str] = []

    def select_by_importance(
        self,
        importance: dict[str, float],
        top_n: int | None = None,
        threshold: float | None = None,
    ) -> list[str]:
        """根据重要性选择特征"""
        sorted_features = sorted(
            importance.items(),
            key=lambda x: x[1],
            reverse=True,
        )

        if top_n:
            selected = [f for f, _ in sorted_features[:top_n]]
        elif threshold:
            selected = [f for f, imp in sorted_features if imp >= threshold]
        else:
            selected = [f for f, _ in sorted_features]

        self._selected_features = selected
        return selected

    def select_by_correlation(
        self,
        X: pd.DataFrame,
        target: pd.Series,
        top_n: int = 50,
        method: str = "spearman",
    ) -> list[str]:
        """根据与目标的相关性选择特征"""
        correlations = {}

        for col in X.columns:
            if X[col].dtype in [np.float64, np.int64]:
                if method == "spearman":
                    corr = X[col].corr(target, method="spearman")
                else:
                    corr = X[col].corr(target)
                correlations[col] = abs(corr) if not np.isnan(corr) else 0

        sorted_features = sorted(
            correlations.items(),
            key=lambda x: x[1],
            reverse=True,
        )

        selected = [f for f, _ in sorted_features[:top_n]]
        self._selected_features = selected
        return selected

    def select_low_correlation_features(
        self,
        X: pd.DataFrame,
        threshold: float = 0.8,
    ) -> list[str]:
        """选择低相关性特征（去除高度相关的冗余特征）"""
        corr_matrix = X.corr().abs()
        upper_tri = corr_matrix.where(
            np.triu(np.ones(corr_matrix.shape), k=1).astype(bool)
        )

        # 找到高相关性的特征对
        to_drop = set()
        for col in upper_tri.columns:
            if any(upper_tri[col] > threshold):
                to_drop.add(col)

        selected = [col for col in X.columns if col not in to_drop]
        self._selected_features = selected
        return selected

    def get_selected_features(self) -> list[str]:
        """获取已选择的特征"""
        return self._selected_features.copy()


# ---------------------------------------------------------------------------
# 复合特征生成器 — 多周期动量融合 + 高阶派生特征
# ---------------------------------------------------------------------------


class CompositeFeatureGenerator(FeatureTransformer):
    """从已有因子派生复合特征（多周期融合、比率、交互项等）。

    典型使用场景：在 Alpha158 因子计算之后，对已有 roc_5/roc_10/roc_20/roc_60
    等单窗口因子做加权融合，生成更稳健的多周期动量信号。

    学术依据：
    - 广发/中金推荐多窗口动量融合降噪
    - Asness et al. (2013) Value and Momentum Everywhere — 多周期动量稳健性更高
    """

    #: 默认复合特征定义 {新特征名: (源列名列表, 权重列表)}
    DEFAULT_COMPOSITES: dict[str, tuple[list[str], list[float]]] = {
        # 多周期动量融合：短+中+长期加权
        "mom_composite_sml": (
            ["roc_5", "roc_20", "roc_60"],
            [0.3, 0.4, 0.3],
        ),
        # 12-1 类动量融合（跳过短期反转）
        "mom_composite_mid_long": (
            ["roc_10", "roc_30", "roc_60"],
            [0.2, 0.5, 0.3],
        ),
        # 短期反转信号融合
        "reversal_composite": (
            ["roc_5", "roc_10"],
            [-0.6, -0.4],  # 取反 = 反转
        ),
        # 波动率融合（多窗口）
        "vol_composite": (
            ["std_5", "std_20", "std_60"],
            [0.3, 0.4, 0.3],
        ),
    }

    def __init__(
        self,
        composites: dict[str, tuple[list[str], list[float]]] | None = None,
        *,
        add_cross_sectional_rank: bool = True,
    ) -> None:
        self.composites = composites or self.DEFAULT_COMPOSITES
        self.add_cs_rank = add_cross_sectional_rank

    def fit(self, X: pd.DataFrame) -> "CompositeFeatureGenerator":
        """无需拟合。"""
        return self

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        """生成加权融合的复合特征。"""
        X = X.copy()

        for new_name, (src_cols, weights) in self.composites.items():
            available = [c for c in src_cols if c in X.columns]
            if len(available) < 2:
                # 不足两个源列则跳过
                logger.debug(
                    "CompositeFeature %s: only %d/%d source cols available, skip",
                    new_name, len(available), len(src_cols),
                )
                continue

            # 按可用列重新归一化权重
            avail_weights = [
                weights[src_cols.index(c)] for c in available
            ]
            total_w = sum(abs(w) for w in avail_weights)
            if total_w == 0:
                continue
            norm_weights = [w / total_w for w in avail_weights]

            # 加权求和
            result = pd.Series(0.0, index=X.index)
            for col, w in zip(available, norm_weights):
                result = result + w * X[col].fillna(0.0)
            X[new_name] = result

            # 截面排名（如果有日期列）
            if self.add_cs_rank:
                date_col = None
                for dc in ("datetime", "trade_date", "date"):
                    if dc in X.columns:
                        date_col = dc
                        break
                if date_col is not None:
                    X[f"cs_rank_{new_name}"] = X.groupby(date_col)[
                        new_name
                    ].rank(method="average", pct=True)

        return X


# ------------------------------------------------------------------
# Fractional Differentiation (López de Prado 2018, Ch. 5)
# ------------------------------------------------------------------

class FractionalDiffTransformer(FeatureTransformer):
    """Fixed-width window fractional differentiation.

    Preserves memory (stationarity + information retention) by applying
    fractional order differencing d ∈ (0, 1) instead of integer d=1.

    The weights w_k = (-1)^k * Π_{i=0}^{k-1} (d - i) / (k!)
    are truncated at |w_k| < weight_threshold for computational efficiency.

    Reference:
        López de Prado (2018), Ch.5 — Fractionally Differentiated Features
        Hosking (1981) — Fractional Differencing

    Args:
        d: Fractional differencing order. Typical range 0.3-0.7.
           Lower d → more memory retained; higher d → more stationary.
        weight_threshold: Truncate weights below this absolute value.
        columns: Specific columns to transform. If None, applies to all numeric.
        suffix: Suffix for new column names.
    """

    def __init__(
        self,
        d: float = 0.5,
        weight_threshold: float = 1e-5,
        columns: list[str] | None = None,
        suffix: str = "_frac",
    ) -> None:
        super().__init__()
        if not 0 < d < 1:
            raise ValueError(f"d must be in (0, 1), got {d}")
        self.d = d
        self.weight_threshold = weight_threshold
        self.columns = columns
        self.suffix = suffix
        self._weights: np.ndarray | None = None

    def _compute_weights(self) -> np.ndarray:
        """Compute fractional diff weights until threshold."""
        weights = [1.0]
        k = 1
        while True:
            w_k = -weights[-1] * (self.d - k + 1) / k
            if abs(w_k) < self.weight_threshold:
                break
            weights.append(w_k)
            k += 1
            if k > 10000:  # safety guard
                break
        return np.array(weights[::-1], dtype=np.float64)

    def fit(self, X: pd.DataFrame, y: Any = None) -> "FractionalDiffTransformer":
        """Compute and cache the weight vector."""
        self._weights = self._compute_weights()
        return self

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        """Apply fractional differentiation to selected columns.

        Returns:
            DataFrame with new columns named {original}{suffix}.
            Rows where the convolution window is incomplete are set to NaN.
        """
        if self._weights is None:
            self.fit(X)
        assert self._weights is not None

        cols = self.columns or [
            c for c in X.columns if np.issubdtype(X[c].dtype, np.number)
        ]
        weights = self._weights
        window = len(weights)

        result = X.copy()
        for col in cols:
            if col not in X.columns:
                continue
            series = X[col].values.astype(np.float64)
            frac = np.full(len(series), np.nan)
            for t in range(window - 1, len(series)):
                frac[t] = np.dot(weights, series[t - window + 1 : t + 1])
            result[f"{col}{self.suffix}"] = frac

        return result

    @property
    def window_size(self) -> int:
        """Number of lookback periods required."""
        if self._weights is None:
            self._weights = self._compute_weights()
        return len(self._weights)
