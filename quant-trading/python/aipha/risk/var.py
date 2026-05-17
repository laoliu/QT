"""
VaR / CVaR 风险度量模块

提供：
- HistoricalVaR: 历史模拟法 VaR/CVaR
- ParametricVaR: 参数法（正态分布假设）VaR/CVaR
- CornishFisherVaR: Cornish-Fisher 展开（考虑偏度/峰度）
- PortfolioVaR: 组合级参数法 VaR/CVaR（协方差矩阵法 + Component VaR）
- StressTestScenario / StressTestRunner: 历史情景压力测试

使用示例:
    from aipha.risk.var import HistoricalVaR, ParametricVaR, StressTestRunner

    var = HistoricalVaR(confidence=0.95)
    result = var.compute(daily_returns)
    print(f"VaR(95%): {result.var:.4f}, CVaR: {result.cvar:.4f}")
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR


class VaRMethod(str, Enum):
    """VaR 计算方法"""
    HISTORICAL = "historical"
    PARAMETRIC = "parametric"
    CORNISH_FISHER = "cornish_fisher"


@dataclass(frozen=True)
class VaRResult:
    """VaR 计算结果

    Attributes:
        var: Value at Risk（正数表示损失）
        cvar: Conditional VaR / Expected Shortfall
        confidence: 置信度
        method: 计算方法
        sample_size: 样本数
        annualized_var: 年化 VaR（可选）
        annualized_cvar: 年化 CVaR（可选）
    """
    var: float
    cvar: float
    confidence: float
    method: VaRMethod
    sample_size: int
    annualized_var: Optional[float] = None
    annualized_cvar: Optional[float] = None

    def to_dict(self) -> dict:
        """转换为字典"""
        result = {
            "var": self.var,
            "cvar": self.cvar,
            "confidence": self.confidence,
            "method": self.method.value,
            "sample_size": self.sample_size,
        }
        if self.annualized_var is not None:
            result["annualized_var"] = self.annualized_var
        if self.annualized_cvar is not None:
            result["annualized_cvar"] = self.annualized_cvar
        return result


class HistoricalVaR:
    """历史模拟法 VaR/CVaR

    不假设收益率分布，直接从历史收益率排序取分位数。

    Args:
        confidence: 置信度，默认 0.95
        trading_days_per_year: 年化用交易日数
    """

    def __init__(
        self,
        confidence: float = 0.95,
        trading_days_per_year: int = TRADING_DAYS_PER_YEAR,
    ):
        if not 0 < confidence < 1:
            raise ValueError(f"confidence 必须在 (0, 1) 之间，得到 {confidence}")
        self.confidence = confidence
        self.trading_days_per_year = trading_days_per_year

    def compute(self, daily_returns: list[float]) -> VaRResult:
        """计算历史 VaR 和 CVaR

        Args:
            daily_returns: 日收益率序列

        Returns:
            VaRResult: VaR/CVaR 结果

        Raises:
            ValueError: 样本不足
        """
        n = len(daily_returns)
        if n < 2:
            raise ValueError(f"计算 VaR 至少需要 2 个样本，得到 {n}")

        sorted_rets = sorted(daily_returns)
        # VaR: 左尾分位数（损失为正）
        var_index = int(n * (1 - self.confidence))
        var_index = max(0, min(var_index, n - 1))
        var_daily = -sorted_rets[var_index]

        # CVaR: VaR 左侧的平均损失
        tail = sorted_rets[: max(var_index + 1, 1)]
        cvar_daily = -sum(tail) / len(tail)

        # 年化（假设独立同分布）
        sqrt_t = math.sqrt(self.trading_days_per_year)
        annualized_var = var_daily * sqrt_t
        annualized_cvar = cvar_daily * sqrt_t

        return VaRResult(
            var=var_daily,
            cvar=cvar_daily,
            confidence=self.confidence,
            method=VaRMethod.HISTORICAL,
            sample_size=n,
            annualized_var=annualized_var,
            annualized_cvar=annualized_cvar,
        )


class ParametricVaR:
    """参数法 VaR/CVaR（正态分布假设）

    Args:
        confidence: 置信度
        trading_days_per_year: 年化用交易日数
    """

    def __init__(
        self,
        confidence: float = 0.95,
        trading_days_per_year: int = TRADING_DAYS_PER_YEAR,
    ):
        if not 0 < confidence < 1:
            raise ValueError(f"confidence 必须在 (0, 1) 之间，得到 {confidence}")
        self.confidence = confidence
        self.trading_days_per_year = trading_days_per_year

    @staticmethod
    def _norm_ppf(p: float) -> float:
        """正态分布逆累积分布函数（近似）

        使用 Abramowitz & Stegun 近似公式 26.2.23，
        避免对 scipy 的硬依赖。
        """
        if p <= 0 or p >= 1:
            raise ValueError(f"p 必须在 (0,1)，得到 {p}")
        if p > 0.5:
            return -ParametricVaR._norm_ppf(1 - p)
        # Rational approximation
        t = math.sqrt(-2 * math.log(p))
        c0, c1, c2 = 2.515517, 0.802853, 0.010328
        d1, d2, d3 = 1.432788, 0.189269, 0.001308
        return -(t - (c0 + c1 * t + c2 * t * t) / (1 + d1 * t + d2 * t * t + d3 * t * t * t))

    @staticmethod
    def _norm_pdf(x: float) -> float:
        """标准正态 PDF"""
        return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)

    def compute(self, daily_returns: list[float]) -> VaRResult:
        """计算参数法 VaR/CVaR

        Args:
            daily_returns: 日收益率序列

        Returns:
            VaRResult
        """
        n = len(daily_returns)
        if n < 2:
            raise ValueError(f"计算 VaR 至少需要 2 个样本，得到 {n}")

        mean = sum(daily_returns) / n
        variance = sum((r - mean) ** 2 for r in daily_returns) / (n - 1)
        sigma = math.sqrt(variance)

        if sigma < 1e-15:
            return VaRResult(
                var=0.0, cvar=0.0, confidence=self.confidence,
                method=VaRMethod.PARAMETRIC, sample_size=n,
            )

        z = self._norm_ppf(self.confidence)
        var_daily = -(mean - z * sigma)  # 损失为正

        # CVaR for normal: mu + sigma * phi(z) / (1-alpha)
        phi_z = self._norm_pdf(z)
        cvar_daily = -(mean - sigma * phi_z / (1 - self.confidence))

        sqrt_t = math.sqrt(self.trading_days_per_year)
        return VaRResult(
            var=var_daily,
            cvar=cvar_daily,
            confidence=self.confidence,
            method=VaRMethod.PARAMETRIC,
            sample_size=n,
            annualized_var=var_daily * sqrt_t,
            annualized_cvar=cvar_daily * sqrt_t,
        )


class CornishFisherVaR:
    """Cornish-Fisher 展开 VaR（考虑偏度和峰度）

    修正正态分布的 z 值以反映实际分布的尾部特征，
    对金融时间序列（通常左偏、厚尾）更精确。

    Args:
        confidence: 置信度
        trading_days_per_year: 年化用交易日数
    """

    def __init__(
        self,
        confidence: float = 0.95,
        trading_days_per_year: int = TRADING_DAYS_PER_YEAR,
    ):
        if not 0 < confidence < 1:
            raise ValueError(f"confidence 必须在 (0,1)，得到 {confidence}")
        self.confidence = confidence
        self.trading_days_per_year = trading_days_per_year

    def compute(self, daily_returns: list[float]) -> VaRResult:
        """计算 Cornish-Fisher 调整的 VaR

        Args:
            daily_returns: 日收益率序列

        Returns:
            VaRResult
        """
        n = len(daily_returns)
        if n < 4:
            raise ValueError(f"Cornish-Fisher 至少需要 4 个样本，得到 {n}")

        mean = sum(daily_returns) / n
        m2 = sum((r - mean) ** 2 for r in daily_returns) / (n - 1)
        sigma = math.sqrt(m2)

        if sigma < 1e-15:
            return VaRResult(
                var=0.0, cvar=0.0, confidence=self.confidence,
                method=VaRMethod.CORNISH_FISHER, sample_size=n,
            )

        # 偏度和峰度
        m3 = sum((r - mean) ** 3 for r in daily_returns) / n
        m4 = sum((r - mean) ** 4 for r in daily_returns) / n
        skewness = m3 / (sigma ** 3)
        excess_kurtosis = m4 / (sigma ** 4) - 3.0

        # Cornish-Fisher 展开
        z = ParametricVaR._norm_ppf(self.confidence)
        z_cf = (
            z
            + (z ** 2 - 1) * skewness / 6
            + (z ** 3 - 3 * z) * excess_kurtosis / 24
            - (2 * z ** 3 - 5 * z) * skewness ** 2 / 36
        )

        var_daily = -(mean - z_cf * sigma)
        # CVaR 近似: 使用历史法计算尾部均值作为 CVaR
        sorted_rets = sorted(daily_returns)
        tail_count = max(int(n * (1 - self.confidence)), 1)
        tail = sorted_rets[:tail_count]
        cvar_daily = -sum(tail) / len(tail)

        sqrt_t = math.sqrt(self.trading_days_per_year)
        return VaRResult(
            var=var_daily,
            cvar=cvar_daily,
            confidence=self.confidence,
            method=VaRMethod.CORNISH_FISHER,
            sample_size=n,
            annualized_var=var_daily * sqrt_t,
            annualized_cvar=cvar_daily * sqrt_t,
        )


# ============================================================================
# 压力测试
# ============================================================================


@dataclass(frozen=True)
class StressTestScenario:
    """压力测试情景

    Attributes:
        name: 情景名称
        description: 情景描述
        start_date: 起始日期
        end_date: 结束日期
        benchmark_drawdown: 基准在该区间的最大回撤（可选，仅用于参考）
    """
    name: str
    description: str
    start_date: date
    end_date: date
    benchmark_drawdown: Optional[float] = None


# A 股典型历史压力情景
A_SHARE_STRESS_SCENARIOS = [
    StressTestScenario(
        name="2015_crash",
        description="2015年6月股灾（千股跌停）",
        start_date=date(2015, 6, 12),
        end_date=date(2015, 9, 15),
        benchmark_drawdown=0.45,
    ),
    StressTestScenario(
        name="2016_circuit_breaker",
        description="2016年1月熔断机制崩盘",
        start_date=date(2016, 1, 4),
        end_date=date(2016, 2, 29),
        benchmark_drawdown=0.22,
    ),
    StressTestScenario(
        name="2018_trade_war",
        description="2018年中美贸易战A股下跌",
        start_date=date(2018, 1, 29),
        end_date=date(2018, 12, 28),
        benchmark_drawdown=0.33,
    ),
    StressTestScenario(
        name="2020_covid",
        description="2020年初新冠疫情恐慌",
        start_date=date(2020, 1, 20),
        end_date=date(2020, 3, 23),
        benchmark_drawdown=0.16,
    ),
    StressTestScenario(
        name="2022_drawdown",
        description="2022年4月上海封控极端回撤",
        start_date=date(2022, 3, 1),
        end_date=date(2022, 4, 27),
        benchmark_drawdown=0.20,
    ),
    StressTestScenario(
        name="2024_dma_crisis",
        description="2024年2月DMA微盘股踩踏(CSI2000 -25%)",
        start_date=date(2024, 1, 15),
        end_date=date(2024, 2, 8),
        benchmark_drawdown=0.25,
    ),
]


@dataclass(frozen=True)
class StressTestResult:
    """单个情景的压力测试结果

    Attributes:
        scenario: 情景定义
        portfolio_return: 策略在该区间的收益率
        max_drawdown: 策略在该区间的最大回撤
        var_95: 区间内 95% VaR
        cvar_95: 区间内 95% CVaR
        worst_day: 区间内最差单日收益
        trading_days: 区间内交易日数
    """
    scenario: StressTestScenario
    portfolio_return: float
    max_drawdown: float
    var_95: float
    cvar_95: float
    worst_day: float
    trading_days: int

    def to_dict(self) -> dict:
        """转换为字典表示。"""
        return {
            "scenario": self.scenario.name,
            "description": self.scenario.description,
            "portfolio_return": self.portfolio_return,
            "max_drawdown": self.max_drawdown,
            "var_95": self.var_95,
            "cvar_95": self.cvar_95,
            "worst_day": self.worst_day,
            "trading_days": self.trading_days,
        }


class StressTestRunner:
    """压力测试运行器

    从 NAV 序列中提取对应历史区间，计算区间内的风险指标。

    Args:
        scenarios: 压力情景列表，默认使用 A 股典型情景
    """

    def __init__(
        self,
        scenarios: list[StressTestScenario] | None = None,
    ):
        self.scenarios = scenarios or A_SHARE_STRESS_SCENARIOS

    def run(
        self,
        nav_series: list[tuple[date, float]],
    ) -> list[StressTestResult]:
        """执行压力测试

        Args:
            nav_series: NAV 序列 (date, value)

        Returns:
            list[StressTestResult]。NAV 不覆盖的区间将被跳过。
        """
        nav_map = dict(nav_series)
        all_dates = sorted(nav_map.keys())
        if not all_dates:
            return []

        results: list[StressTestResult] = []
        for scenario in self.scenarios:
            # 筛选区间内日期
            period_dates = [
                d for d in all_dates
                if scenario.start_date <= d <= scenario.end_date
            ]
            if len(period_dates) < 2:
                continue

            navs = [nav_map[d] for d in period_dates]

            # 日收益率
            daily_rets = [
                (navs[i] - navs[i - 1]) / navs[i - 1]
                for i in range(1, len(navs))
            ]

            # 区间总收益
            total_ret = (navs[-1] - navs[0]) / navs[0]

            # 最大回撤
            max_dd = 0.0
            peak = navs[0]
            for nav in navs:
                if nav > peak:
                    peak = nav
                dd = (peak - nav) / peak
                if dd > max_dd:
                    max_dd = dd

            # VaR/CVaR（历史法）
            if len(daily_rets) >= 2:
                var_calc = HistoricalVaR(confidence=0.95)
                var_result = var_calc.compute(daily_rets)
                var_95 = var_result.var
                cvar_95 = var_result.cvar
            else:
                var_95 = 0.0
                cvar_95 = 0.0

            worst_day = min(daily_rets) if daily_rets else 0.0

            results.append(StressTestResult(
                scenario=scenario,
                portfolio_return=total_ret,
                max_drawdown=max_dd,
                var_95=var_95,
                cvar_95=cvar_95,
                worst_day=worst_day,
                trading_days=len(period_dates),
            ))

        return results

    def summary(self, results: list[StressTestResult]) -> str:
        """生成压力测试摘要报告"""
        if not results:
            return "无可用的压力测试结果（NAV 未覆盖任何压力情景区间）"

        lines = [
            "=" * 70,
            "压力测试报告",
            "=" * 70,
            "",
        ]
        for r in results:
            lines.extend([
                f"情景: {r.scenario.name} — {r.scenario.description}",
                f"  策略收益: {r.portfolio_return:+.2%}",
                f"  最大回撤: {r.max_drawdown:.2%}",
                f"  VaR(95%): {r.var_95:.4f}",
                f"  CVaR(95%): {r.cvar_95:.4f}",
                f"  最差单日: {r.worst_day:+.2%}",
                f"  交易日数: {r.trading_days}",
                "",
            ])
        return "\n".join(lines)


# ===========================================================================
# 组合级 VaR（协方差矩阵法）
# ===========================================================================


@dataclass(frozen=True)
class PortfolioVaRResult:
    """组合级协方差矩阵法 VaR 计算结果。

    Attributes:
        var: 组合日度 VaR（正数表示损失）
        cvar: 组合日度 CVaR / Expected Shortfall
        confidence: 置信度
        portfolio_vol: 组合日度波动率
        annualized_var: 年化 VaR
        annualized_cvar: 年化 CVaR
        component_var: 各资产 Component VaR（边际风险贡献）
        n_assets: 资产数量
        n_obs: 收益率观测数（行数）
    """

    var: float
    cvar: float
    confidence: float
    portfolio_vol: float
    annualized_var: float
    annualized_cvar: float
    component_var: dict[str, float]
    n_assets: int
    n_obs: int

    def to_dict(self) -> dict:
        """序列化为字典。"""
        return {
            "var": self.var,
            "cvar": self.cvar,
            "confidence": self.confidence,
            "portfolio_vol": self.portfolio_vol,
            "annualized_var": self.annualized_var,
            "annualized_cvar": self.annualized_cvar,
            "component_var": self.component_var,
            "n_assets": self.n_assets,
            "n_obs": self.n_obs,
        }


class PortfolioVaR:
    """组合级参数法 VaR/CVaR（协方差矩阵法）。

    通过**持仓权重向量**和**个股收益率矩阵**计算组合风险：

    $$\\text{VaR}_p = z_{\\alpha} \\cdot \\sqrt{\\mathbf{w}^T \\Sigma \\mathbf{w}} \\cdot \\sqrt{T}$$

    其中 $\\Sigma$ 是资产收益率协方差矩阵，$\\mathbf{w}$ 是持仓权重。

    相比整体组合收益序列的单资产 VaR 计算，该方法：
    1. 可输出 Component VaR（边际风险贡献），用于风险归因
    2. 自然支持子组合分析（只需调整权重向量）
    3. 在重新权衡场景下无需回测即可估算新组合风险

    Args:
        confidence: 置信水平（如 0.95 表示 95% VaR）
        trading_days_per_year: 年化用交易日数
    """

    def __init__(
        self,
        confidence: float = 0.95,
        trading_days_per_year: int = TRADING_DAYS_PER_YEAR,
    ):
        if not 0 < confidence < 1:
            raise ValueError(f"confidence 必须在 (0, 1) 之间，得到 {confidence}")
        self.confidence = confidence
        self.trading_days_per_year = trading_days_per_year

    def compute(
        self,
        weights: dict[str, float],
        returns_matrix: dict[str, list[float]],
    ) -> PortfolioVaRResult:
        """计算组合级协方差矩阵法 VaR/CVaR。

        Args:
            weights: 资产权重 {symbol: weight}，权重应合计为 1.0
                （不强制归一化，允许非满仓场景）。
            returns_matrix: 个股日收益率 {symbol: [r1, r2, ...]}，
                所有资产的收益率序列长度必须一致。

        Returns:
            PortfolioVaRResult

        Raises:
            ValueError: 权重或收益率数据不合法时。
        """
        # 验证输入
        symbols = list(weights.keys())
        n_assets = len(symbols)
        if n_assets == 0:
            raise ValueError("weights 不能为空")

        missing = [s for s in symbols if s not in returns_matrix]
        if missing:
            raise ValueError(f"以下资产缺少收益率数据: {missing}")

        # 构建对齐矩阵
        lengths = {len(returns_matrix[s]) for s in symbols}
        if len(lengths) != 1:
            raise ValueError(
                f"各资产收益率序列长度不一致: {dict(zip(symbols, [len(returns_matrix[s]) for s in symbols]))}"
            )

        n_obs = lengths.pop()
        if n_obs < 2:
            raise ValueError(f"计算协方差至少需要 2 期观测，得到 {n_obs}")

        # 权重向量
        w = [weights[s] for s in symbols]

        # 收益率矩阵 [n_obs × n_assets]
        R = [[returns_matrix[s][t] for s in symbols] for t in range(n_obs)]

        # 均值向量
        means = [sum(returns_matrix[s]) / n_obs for s in symbols]

        # 协方差矩阵 (无偏, n-1)
        cov = [[0.0] * n_assets for _ in range(n_assets)]
        for i in range(n_assets):
            for j in range(i, n_assets):
                c = sum(
                    (R[t][i] - means[i]) * (R[t][j] - means[j])
                    for t in range(n_obs)
                ) / (n_obs - 1)
                cov[i][j] = c
                cov[j][i] = c

        # 组合方差 = w^T Σ w
        port_var = 0.0
        for i in range(n_assets):
            for j in range(n_assets):
                port_var += w[i] * w[j] * cov[i][j]

        port_vol = math.sqrt(max(port_var, 0.0))

        # z_alpha
        z = ParametricVaR._norm_ppf(self.confidence)

        # 组合均值收益
        port_mean = sum(w[i] * means[i] for i in range(n_assets))

        # 日度 VaR = -(mu - z * sigma)（损失为正）
        var_daily = -(port_mean - z * port_vol)

        # 日度 CVaR（正态假设）
        phi_z = ParametricVaR._norm_pdf(z)
        alpha_tail = 1 - self.confidence
        cvar_daily = -(port_mean - port_vol * phi_z / alpha_tail) if alpha_tail > 0 else var_daily

        # 年化
        sqrt_t = math.sqrt(self.trading_days_per_year)
        ann_var = var_daily * sqrt_t
        ann_cvar = cvar_daily * sqrt_t

        # Component VaR：CVaR_i = w_i * (Σw)_i / sigma_p * VaR_p
        # 其中 (Σw)_i = sum_j cov[i][j] * w[j]
        component: dict[str, float] = {}
        for idx, s in enumerate(symbols):
            sigma_w_i = sum(cov[idx][j] * w[j] for j in range(n_assets))
            if port_vol > 1e-15:
                component[s] = w[idx] * sigma_w_i / port_vol * z
            else:
                component[s] = 0.0

        return PortfolioVaRResult(
            var=var_daily,
            cvar=cvar_daily,
            confidence=self.confidence,
            portfolio_vol=port_vol,
            annualized_var=ann_var,
            annualized_cvar=ann_cvar,
            component_var=component,
            n_assets=n_assets,
            n_obs=n_obs,
        )
