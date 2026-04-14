"""
因子分析模块

提供因子有效性评估功能：
- IC (Information Coefficient): 因子值与未来收益的相关系数
- IR (Information Ratio): IC的均值/标准差，衡量因子稳定性
- 因子收益分析: 分组回测
- 因子衰减分析: 不同持有期的IC变化

使用示例：
    analyzer = FactorAnalyzer(factor_data, return_data)
    ic_series = analyzer.compute_ic()
    report = analyzer.generate_report()
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
from scipy import stats


@dataclass
class ICAnalysisResult:
    """IC分析结果"""

    ic_series: dict[date, float]  # 每日IC
    ic_mean: float  # IC均值
    ic_std: float  # IC标准差
    ir: float  # 信息比率 = IC均值 / IC标准差
    ic_positive_rate: float  # IC为正的比例
    t_stat: float  # t统计量
    p_value: float  # p值
    ic_abs_mean: float  # |IC|均值


@dataclass
class FactorDecayResult:
    """因子衰减分析结果"""

    holding_periods: list[int]
    ic_by_period: dict[int, float]  # 不同持有期的IC
    ir_by_period: dict[int, float]  # 不同持有期的IR
    optimal_period: int  # 最优持有期（IR最高）


@dataclass
class QuantileReturnResult:
    """分位数收益分析结果"""

    num_quantiles: int
    quantile_returns: dict[int, float]  # 每个分位的平均收益
    long_short_return: float  # 多空收益 (Q1 - Q5 或 Qn)
    monotonicity: float  # 单调性指标


@dataclass
class FactorAnalysisReport:
    """因子分析完整报告"""

    factor_name: str
    ic_analysis: ICAnalysisResult
    decay_analysis: FactorDecayResult | None
    quantile_analysis: QuantileReturnResult | None
    analysis_period: tuple[date, date]
    sample_size: int

    def summary(self) -> str:
        """生成摘要报告"""
        lines = [
            "=" * 60,
            f"因子分析报告: {self.factor_name}",
            "=" * 60,
            "",
            f"分析期间: {self.analysis_period[0]} ~ {self.analysis_period[1]}",
            f"样本数量: {self.sample_size}",
            "",
            "【IC 分析】",
            f"  IC 均值: {self.ic_analysis.ic_mean:.4f}",
            f"  IC 标准差: {self.ic_analysis.ic_std:.4f}",
            f"  IR (信息比率): {self.ic_analysis.ir:.4f}",
            f"  IC > 0 比例: {self.ic_analysis.ic_positive_rate:.2%}",
            f"  |IC| 均值: {self.ic_analysis.ic_abs_mean:.4f}",
            f"  t 统计量: {self.ic_analysis.t_stat:.2f}",
            f"  p 值: {self.ic_analysis.p_value:.4f}",
        ]

        if self.decay_analysis:
            lines.extend([
                "",
                "【因子衰减分析】",
                f"  最优持有期: {self.decay_analysis.optimal_period} 天",
                "  各持有期 IC/IR:",
            ])
            for period in self.decay_analysis.holding_periods:
                ic = self.decay_analysis.ic_by_period.get(period, 0)
                ir = self.decay_analysis.ir_by_period.get(period, 0)
                lines.append(f"    {period}日: IC={ic:.4f}, IR={ir:.4f}")

        if self.quantile_analysis:
            lines.extend([
                "",
                "【分位数收益分析】",
                f"  分位数: {self.quantile_analysis.num_quantiles}",
                f"  多空收益: {self.quantile_analysis.long_short_return:.4f}",
                f"  单调性: {self.quantile_analysis.monotonicity:.4f}",
                "  各分位收益:",
            ])
            for q, ret in sorted(self.quantile_analysis.quantile_returns.items()):
                lines.append(f"    Q{q}: {ret:.4f}")

        lines.extend(["", "=" * 60])
        return "\n".join(lines)


class FactorAnalyzer:
    """因子分析器"""

    def __init__(
        self,
        factor_data: dict[date, dict[str, float]],
        return_data: dict[date, dict[str, float]],
        factor_name: str = "factor",
    ):
        """初始化因子分析器

        Args:
            factor_data: 因子数据 {date: {symbol: factor_value}}
            return_data: 收益数据 {date: {symbol: return_value}}
            factor_name: 因子名称
        """
        self.factor_data = factor_data
        self.return_data = return_data
        self.factor_name = factor_name

        # 对齐日期
        self.dates = sorted(set(factor_data.keys()) & set(return_data.keys()))

    def compute_ic(
        self,
        method: str = "spearman",
        forward_days: int = 1,
    ) -> ICAnalysisResult:
        """计算信息系数 (IC)

        Args:
            method: "spearman" (rank IC) 或 "pearson"
            forward_days: 前瞻收益天数

        Returns:
            IC分析结果
        """
        ic_series: dict[date, float] = {}

        for i, d in enumerate(self.dates[:-forward_days]):
            # 获取当日因子值
            factors = self.factor_data.get(d, {})

            # 获取未来收益
            future_date = self.dates[min(i + forward_days, len(self.dates) - 1)]
            returns = self.return_data.get(future_date, {})

            # 找到共同标的
            common_symbols = set(factors.keys()) & set(returns.keys())
            if len(common_symbols) < 5:  # 至少5个样本
                continue

            # 提取配对数据
            factor_vals = [factors[s] for s in common_symbols]
            return_vals = [returns[s] for s in common_symbols]

            # 计算相关系数
            if method == "spearman":
                corr, _ = stats.spearmanr(factor_vals, return_vals)
            else:
                corr, _ = stats.pearsonr(factor_vals, return_vals)

            if not np.isnan(corr):
                ic_series[d] = corr

        if not ic_series:
            return ICAnalysisResult(
                ic_series={},
                ic_mean=0.0,
                ic_std=0.0,
                ir=0.0,
                ic_positive_rate=0.0,
                t_stat=0.0,
                p_value=1.0,
                ic_abs_mean=0.0,
            )

        # 统计分析
        ic_values = np.array(list(ic_series.values()))
        ic_mean = float(np.mean(ic_values))
        ic_std = float(np.std(ic_values, ddof=1)) if len(ic_values) > 1 else 0.0
        ir = ic_mean / ic_std if ic_std > 0 else 0.0
        ic_positive_rate = np.mean(ic_values > 0)
        ic_abs_mean = float(np.mean(np.abs(ic_values)))

        # t检验
        n = len(ic_values)
        t_stat = ic_mean / (ic_std / np.sqrt(n)) if ic_std > 0 and n > 1 else 0.0
        p_value = 2 * (1 - stats.t.cdf(abs(t_stat), n - 1)) if n > 1 else 1.0

        return ICAnalysisResult(
            ic_series=ic_series,
            ic_mean=ic_mean,
            ic_std=ic_std,
            ir=ir,
            ic_positive_rate=ic_positive_rate,
            t_stat=t_stat,
            p_value=p_value,
            ic_abs_mean=ic_abs_mean,
        )

    def compute_decay(
        self,
        holding_periods: list[int] | None = None,
        method: str = "spearman",
    ) -> FactorDecayResult:
        """计算因子衰减

        Args:
            holding_periods: 持有期列表
            method: IC计算方法

        Returns:
            因子衰减分析结果
        """
        if holding_periods is None:
            holding_periods = [1, 3, 5, 10, 20]

        ic_by_period: dict[int, float] = {}
        ir_by_period: dict[int, float] = {}

        for period in holding_periods:
            result = self.compute_ic(method=method, forward_days=period)
            ic_by_period[period] = result.ic_mean
            ir_by_period[period] = result.ir

        # 找最优持有期（IR最高）
        optimal_period = max(ir_by_period, key=lambda p: ir_by_period[p])

        return FactorDecayResult(
            holding_periods=holding_periods,
            ic_by_period=ic_by_period,
            ir_by_period=ir_by_period,
            optimal_period=optimal_period,
        )

    def compute_quantile_returns(
        self,
        num_quantiles: int = 5,
        forward_days: int = 1,
    ) -> QuantileReturnResult:
        """计算分位数收益

        Args:
            num_quantiles: 分位数数量
            forward_days: 前瞻收益天数

        Returns:
            分位数收益分析结果
        """
        quantile_returns: dict[int, list[float]] = {q: [] for q in range(1, num_quantiles + 1)}

        for i, d in enumerate(self.dates[:-forward_days]):
            factors = self.factor_data.get(d, {})
            future_date = self.dates[min(i + forward_days, len(self.dates) - 1)]
            returns = self.return_data.get(future_date, {})

            common_symbols = set(factors.keys()) & set(returns.keys())
            if len(common_symbols) < num_quantiles:
                continue

            # 按因子值排序
            sorted_symbols = sorted(common_symbols, key=lambda s: factors[s], reverse=True)

            # 分组
            group_size = len(sorted_symbols) // num_quantiles
            for q in range(1, num_quantiles + 1):
                start_idx = (q - 1) * group_size
                end_idx = q * group_size if q < num_quantiles else len(sorted_symbols)
                group_symbols = sorted_symbols[start_idx:end_idx]

                if group_symbols:
                    avg_return = np.mean([returns[s] for s in group_symbols])
                    quantile_returns[q].append(avg_return)

        # 计算平均收益
        avg_quantile_returns = {
            q: float(np.mean(rets)) if rets else 0.0
            for q, rets in quantile_returns.items()
        }

        # 多空收益 (Q1 - Qn)
        long_short_return = avg_quantile_returns.get(1, 0) - avg_quantile_returns.get(num_quantiles, 0)

        # 单调性 (检查Q1 > Q2 > ... > Qn)
        returns_list = [avg_quantile_returns[q] for q in range(1, num_quantiles + 1)]
        if len(returns_list) >= 2:
            diffs = [returns_list[i] - returns_list[i + 1] for i in range(len(returns_list) - 1)]
            monotonicity = np.mean([1 if d > 0 else 0 for d in diffs])
        else:
            monotonicity = 0.0

        return QuantileReturnResult(
            num_quantiles=num_quantiles,
            quantile_returns=avg_quantile_returns,
            long_short_return=long_short_return,
            monotonicity=monotonicity,
        )

    def generate_report(
        self,
        include_decay: bool = True,
        include_quantile: bool = True,
        holding_periods: list[int] | None = None,
        num_quantiles: int = 5,
    ) -> FactorAnalysisReport:
        """生成完整分析报告

        Args:
            include_decay: 是否包含衰减分析
            include_quantile: 是否包含分位数分析
            holding_periods: 衰减分析持有期
            num_quantiles: 分位数数量

        Returns:
            因子分析报告
        """
        ic_analysis = self.compute_ic()

        decay_analysis = None
        if include_decay:
            decay_analysis = self.compute_decay(holding_periods)

        quantile_analysis = None
        if include_quantile:
            quantile_analysis = self.compute_quantile_returns(num_quantiles)

        return FactorAnalysisReport(
            factor_name=self.factor_name,
            ic_analysis=ic_analysis,
            decay_analysis=decay_analysis,
            quantile_analysis=quantile_analysis,
            analysis_period=(self.dates[0], self.dates[-1]) if self.dates else (date.today(), date.today()),
            sample_size=len(self.dates),
        )


def analyze_factor(
    factor_data: dict[date, dict[str, float]],
    return_data: dict[date, dict[str, float]],
    factor_name: str = "factor",
) -> FactorAnalysisReport:
    """便捷函数：分析因子

    Args:
        factor_data: 因子数据
        return_data: 收益数据
        factor_name: 因子名称

    Returns:
        因子分析报告
    """
    analyzer = FactorAnalyzer(factor_data, return_data, factor_name)
    return analyzer.generate_report()
