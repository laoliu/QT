"""
高级验证工具

提供：
- DeflatedSharpeRatio: 校正多重检验偏差的 Sharpe 比率检验
  (Bailey & Lopez de Prado, 2014)
- CombinatorialPurgedCV: 组合型清洗交叉验证
  (de Prado, 2018 "Advances in Financial Machine Learning")
- MonteCarloPermutationTest: 蒙特卡洛排列检验

这些工具帮助评估策略是否真正具有 alpha，而不是数据挖掘的产物。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class DSRResult:
    """Deflated Sharpe Ratio 结果

    Attributes:
        observed_sharpe: 观察到的 Sharpe 比率
        deflated_sharpe: 校正后的 p-value（1 - pval）
        p_value: 在多重检验下，观察到的 Sharpe 偶然发生的概率
        haircut_sharpe: 经过 haircut 后的 Sharpe 估计
        n_trials: 独立/半独立尝试次数
        expected_max_sharpe: 在 n_trials 次随机尝试下的期望最大 Sharpe
        is_significant: 在给定显著性水平下是否显著
        significance_level: 使用的显著性水平
    """
    observed_sharpe: float
    deflated_sharpe: float
    p_value: float
    haircut_sharpe: float
    n_trials: int
    expected_max_sharpe: float
    is_significant: bool
    significance_level: float

    def to_dict(self) -> dict:
        """转换为字典表示。"""
        return {
            "observed_sharpe": self.observed_sharpe,
            "deflated_sharpe": self.deflated_sharpe,
            "p_value": self.p_value,
            "haircut_sharpe": self.haircut_sharpe,
            "n_trials": self.n_trials,
            "expected_max_sharpe": self.expected_max_sharpe,
            "is_significant": self.is_significant,
            "significance_level": self.significance_level,
        }


class DeflatedSharpeRatio:
    """Deflated Sharpe Ratio (DSR)

    修正因为多重检验（尝试多策略/参数后选最优）导致的 Sharpe 虚高。

    参考：Bailey & Lopez de Prado (2014)
    "The Deflated Sharpe Ratio: Correcting for Selection Bias,
    Backtest Overfitting, and Non-Normality"

    Args:
        significance_level: 显著性水平（默认 0.05）
    """

    def __init__(self, significance_level: float = 0.05):
        if not 0 < significance_level < 1:
            raise ValueError(f"significance_level 须在 (0,1)，得到 {significance_level}")
        self.significance_level = significance_level

    @staticmethod
    def _norm_cdf(x: float) -> float:
        """标准正态 CDF（近似）"""
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))

    @staticmethod
    def _norm_ppf(p: float) -> float:
        """标准正态逆 CDF（近似 Abramowitz & Stegun）"""
        if p <= 0 or p >= 1:
            raise ValueError(f"p 须在 (0,1)，得到 {p}")
        if p > 0.5:
            return -DeflatedSharpeRatio._norm_ppf(1 - p)
        t = math.sqrt(-2 * math.log(p))
        c0, c1, c2 = 2.515517, 0.802853, 0.010328
        d1, d2, d3 = 1.432788, 0.189269, 0.001308
        return -(t - (c0 + c1 * t + c2 * t * t) / (1 + d1 * t + d2 * t * t + d3 * t * t * t))

    @staticmethod
    def expected_max_sharpe(
        n_trials: int,
        trading_days: int,
        skewness: float = 0.0,
        excess_kurtosis: float = 0.0,
    ) -> float:
        """计算在 n_trials 次独立尝试下期望的最大 Sharpe

        基于 Euler-Mascheroni 常数近似：
        E[max(Z_1,...,Z_n)] ≈ (1 - γ) * Φ^{-1}(1 - 1/n) + γ * Φ^{-1}(1 - 1/(n*e))

        Args:
            n_trials: 尝试次数
            trading_days: 交易日数
            skewness: 偏度（默认 0）
            excess_kurtosis: 超额峰度（默认 0）

        Returns:
            期望最大 Sharpe（年化）
        """
        if n_trials <= 0:
            return 0.0
        if n_trials == 1:
            return 0.0

        euler_mascheroni = 0.5772156649

        z1 = DeflatedSharpeRatio._norm_ppf(1 - 1.0 / n_trials) if n_trials > 1 else 0
        z2 = DeflatedSharpeRatio._norm_ppf(1 - 1.0 / (n_trials * math.e)) if n_trials > 1 else 0

        e_max_z = (1 - euler_mascheroni) * z1 + euler_mascheroni * z2

        # 年化换算因子
        annual_factor = math.sqrt(252 / max(trading_days, 1))

        return e_max_z * annual_factor

    def compute(
        self,
        observed_sharpe: float,
        n_trials: int,
        trading_days: int,
        daily_returns: Optional[list[float]] = None,
        skewness: float = 0.0,
        excess_kurtosis: float = 0.0,
        returns_std: float = 0.01,
    ) -> DSRResult:
        """计算 Deflated Sharpe Ratio

        Args:
            observed_sharpe: 观察到的年化 Sharpe
            n_trials: 总共尝试的策略/参数数
            trading_days: 回测交易日数
            daily_returns: 日收益率序列（可选，用于计算偏度/峰度）
            skewness: 偏度（提供 daily_returns 时自动计算）
            excess_kurtosis: 超额峰度（同上）
            returns_std: 日收益率标准差（用于标准化 Sharpe）

        Returns:
            DSRResult
        """
        if n_trials < 1:
            n_trials = 1

        # 从日收益率计算实际统计量
        if daily_returns and len(daily_returns) >= 4:
            n = len(daily_returns)
            mean = sum(daily_returns) / n
            m2 = sum((r - mean) ** 2 for r in daily_returns) / (n - 1)
            returns_std = math.sqrt(m2) if m2 > 0 else 0.01
            if m2 > 1e-15:
                sigma3 = returns_std ** 3
                sigma4 = returns_std ** 4
                m3 = sum((r - mean) ** 3 for r in daily_returns) / n
                m4 = sum((r - mean) ** 4 for r in daily_returns) / n
                skewness = m3 / sigma3
                excess_kurtosis = m4 / sigma4 - 3.0
            trading_days = n

        # 期望最大 Sharpe E[max(SR)]
        e_max_sr = self.expected_max_sharpe(
            n_trials, trading_days, skewness, excess_kurtosis
        )

        # Sharpe 估计量的标准误
        # se(SR) ≈ sqrt( (1 - skew*SR + kurtosis*SR²/4) / T )
        sr_daily = observed_sharpe / math.sqrt(252)
        se_sr_sq = (
            1
            - skewness * sr_daily
            + (excess_kurtosis / 4) * sr_daily ** 2
        ) / max(trading_days, 1)
        se_sr = math.sqrt(max(se_sr_sq, 1e-15))

        # z-score: 超过期望最大 Sharpe 的程度
        sr_annual = observed_sharpe
        e_max_sr_annual = e_max_sr

        if se_sr > 1e-15:
            z = (sr_daily - e_max_sr / math.sqrt(252)) / se_sr
        else:
            z = 0.0

        # p-value
        p_value = 1 - self._norm_cdf(z)

        # Haircut: 减去期望最大值
        haircut_sharpe = max(observed_sharpe - e_max_sr, 0.0)

        # 显著性判定
        is_significant = p_value < self.significance_level

        return DSRResult(
            observed_sharpe=observed_sharpe,
            deflated_sharpe=1 - p_value,
            p_value=p_value,
            haircut_sharpe=haircut_sharpe,
            n_trials=n_trials,
            expected_max_sharpe=e_max_sr,
            is_significant=is_significant,
            significance_level=self.significance_level,
        )


@dataclass(frozen=True)
class PermutationTestResult:
    """排列检验结果

    Attributes:
        observed_metric: 观察到的指标值
        p_value: 排列检验的 p 值
        n_permutations: 排列次数
        metric_name: 指标名称
        is_significant: 是否显著
        percentile: 观察值在排列分布中的分位数
    """
    observed_metric: float
    p_value: float
    n_permutations: int
    metric_name: str
    is_significant: bool
    percentile: float

    def to_dict(self) -> dict:
        """转换为字典表示。"""
        return {
            "observed_metric": self.observed_metric,
            "p_value": self.p_value,
            "n_permutations": self.n_permutations,
            "metric_name": self.metric_name,
            "is_significant": self.is_significant,
            "percentile": self.percentile,
        }


class MonteCarloPermutationTest:
    """蒙特卡洛排列检验

    通过随机打乱收益率序列的时间顺序，检验策略的择时能力。
    如果策略真正具有择时 alpha，打乱后的 Sharpe 分布应低于实际值。

    Args:
        n_permutations: 排列次数（默认 1000）
        significance_level: 显著性水平（默认 0.05）
        random_seed: 随机种子
    """

    def __init__(
        self,
        n_permutations: int = 1000,
        significance_level: float = 0.05,
        random_seed: int = 42,
    ):
        self.n_permutations = n_permutations
        self.significance_level = significance_level
        self.random_seed = random_seed

    @staticmethod
    def _compute_sharpe(returns: list[float], trading_days: int = 252) -> float:
        """计算年化 Sharpe（路径敏感版本） — §13.2 委托共享模块。"""
        from aipha.utils.metrics import compute_sharpe_ratio_log
        import numpy as np
        return compute_sharpe_ratio_log(np.array(returns), trading_days=trading_days)

    def test(
        self,
        strategy_returns: list[float],
        metric_fn: Optional[object] = None,
        metric_name: str = "sharpe_ratio",
    ) -> PermutationTestResult:
        """执行排列检验

        Args:
            strategy_returns: 策略日收益率
            metric_fn: 自定义指标函数（接收 list[float] 返回 float），默认 Sharpe
            metric_name: 指标名称

        Returns:
            PermutationTestResult
        """
        import random

        if len(strategy_returns) < 5:
            return PermutationTestResult(
                observed_metric=0.0,
                p_value=1.0,
                n_permutations=0,
                metric_name=metric_name,
                is_significant=False,
                percentile=0.0,
            )

        compute = metric_fn if metric_fn else self._compute_sharpe
        observed = compute(strategy_returns)

        rng = random.Random(self.random_seed)
        count_better = 0
        shuffled = list(strategy_returns)

        for _ in range(self.n_permutations):
            rng.shuffle(shuffled)
            perm_metric = compute(list(shuffled))
            if perm_metric >= observed:
                count_better += 1

        p_value = (count_better + 1) / (self.n_permutations + 1)
        percentile = 1 - p_value

        return PermutationTestResult(
            observed_metric=observed,
            p_value=p_value,
            n_permutations=self.n_permutations,
            metric_name=metric_name,
            is_significant=p_value < self.significance_level,
            percentile=percentile,
        )
