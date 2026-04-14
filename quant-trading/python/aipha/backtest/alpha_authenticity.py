"""Alpha 真实性验证模块。

检测策略收益是否来自真正的 Alpha（选股/择时能力），
还是仅仅是对某个风格因子（如 size）的 beta 暴露。

核心检测项：
1. **因子集中度检查** — 单因子权重 > 阈值时标记为 beta 暴露
2. **滑点鲁棒性检查** — 在不同滑点水平下收益是否仍为正
3. **起始日期敏感度** — 改变起始日期对结果的影响是否过大
4. **综合判定** — AlphaAuthenticityReport 整合所有检查

设计原则：
- 纯分析工具，不调用回测引擎，输入为指标/配置
- 与 robustness.py / sensitivity.py 互补
- 结果可序列化，便于报告输出

References:
- R12 专家评审发现：80% size 权重 = 纯 beta，15bps 滑点使 hs300_optimized 为负
- slippage.py 定义 A 股最低合理滑点 SLIPPAGE_BPS_MIN_REALISTIC = 5.0
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Sequence

logger = logging.getLogger(__name__)


# ============================================================================
# A 股因子权重参考阈值
# ============================================================================

# 单因子权重超过此值视为 beta 暴露（而非 alpha）
FACTOR_WEIGHT_BETA_THRESHOLD = 0.40

# 起始日期敏感度：年化收益标准差超过此值视为过度敏感（绝对值）
START_DATE_SENSITIVITY_THRESHOLD = 0.10  # 10ppt

# 滑点阈值：A 股不同市值段的最低合理滑点（bps）
SLIPPAGE_REALISTIC_MAP: dict[str, float] = {
    "largecap": 10.0,    # 沪深300
    "midcap": 15.0,      # 中证500
    "smallcap": 25.0,    # 中证1000+
    "microcap": 40.0,    # 微盘股
}


# ============================================================================
# 数据类
# ============================================================================


@dataclass(frozen=True, slots=True)
class FactorExposureCheck:
    """单因子暴露检查结果。

    Attributes:
        factor_name: 因子名称
        weight: 配置权重
        threshold: 判定阈值
        is_beta: 是否被认定为 beta 暴露
    """

    factor_name: str
    weight: float
    threshold: float

    @property
    def is_beta(self) -> bool:
        """权重超过阈值即视为 beta 暴露。"""
        return self.weight > self.threshold

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典，含因子名、权重、阈值及是否 beta 暴露。"""
        return {
            "factor_name": self.factor_name,
            "weight": self.weight,
            "threshold": self.threshold,
            "is_beta": self.is_beta,
        }


@dataclass(frozen=True, slots=True)
class SlippageRobustnessCheck:
    """滑点鲁棒性检查结果。

    Attributes:
        slippage_bps: 测试的滑点水平
        annualized_return: 对应年化收益
        sharpe_ratio: 对应夏普比
        is_positive: 收益是否为正
    """

    slippage_bps: float
    annualized_return: float
    sharpe_ratio: float

    @property
    def is_positive(self) -> bool:
        """滑点压力下年化收益是否仍为正。"""
        return self.annualized_return > 0.0

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典，含滑点、收益、夏普及是否仍正收益。"""
        return {
            "slippage_bps": self.slippage_bps,
            "annualized_return": self.annualized_return,
            "sharpe_ratio": self.sharpe_ratio,
            "is_positive": self.is_positive,
        }


@dataclass(frozen=True, slots=True)
class StartDateSensitivityCheck:
    """起始日期敏感度检查结果。

    Attributes:
        start_dates: 测试的起始日期列表
        annualized_returns: 每个起始日期对应的年化收益
        returns_std: 年化收益标准差
        returns_range: 年化收益极差（最大 - 最小）
        threshold: 判定阈值
    """

    start_dates: tuple[str, ...]
    annualized_returns: tuple[float, ...]
    returns_std: float
    returns_range: float
    threshold: float

    @property
    def is_sensitive(self) -> bool:
        """标准差超过阈值即视为过度敏感。"""
        return self.returns_std > self.threshold

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典，含起始日期列表、收益标准差、范围及敏感度判定。"""
        return {
            "start_dates": list(self.start_dates),
            "annualized_returns": list(self.annualized_returns),
            "returns_std": self.returns_std,
            "returns_range": self.returns_range,
            "threshold": self.threshold,
            "is_sensitive": self.is_sensitive,
        }


@dataclass
class AlphaAuthenticityReport:
    """Alpha 真实性综合报告。

    Attributes:
        config_name: 配置名称
        factor_checks: 因子暴露检查列表
        slippage_checks: 滑点鲁棒性检查列表（可选，需要多次回测）
        start_date_check: 起始日期敏感度检查（可选）
        score: 综合评分 0~10
        verdict: 判定结论
        warnings: 警告信息列表
    """

    config_name: str
    factor_checks: list[FactorExposureCheck] = field(default_factory=list)
    slippage_checks: list[SlippageRobustnessCheck] = field(default_factory=list)
    start_date_check: StartDateSensitivityCheck | None = None
    score: float = 0.0
    verdict: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典，含评分、判定结论、警告及各项检查结果。"""
        result: dict[str, Any] = {
            "config_name": self.config_name,
            "score": self.score,
            "verdict": self.verdict,
            "warnings": self.warnings,
            "factor_checks": [c.to_dict() for c in self.factor_checks],
        }
        if self.slippage_checks:
            result["slippage_checks"] = [c.to_dict() for c in self.slippage_checks]
        if self.start_date_check is not None:
            result["start_date_check"] = self.start_date_check.to_dict()
        return result


# ============================================================================
# 分析函数
# ============================================================================


def check_factor_exposure(
    factor_weights: dict[str, float],
    *,
    threshold: float = FACTOR_WEIGHT_BETA_THRESHOLD,
) -> list[FactorExposureCheck]:
    """检查因子权重是否存在过度集中（beta 暴露）。

    Args:
        factor_weights: 因子名 → 权重的映射
        threshold: 单因子权重阈值（默认 0.40）

    Returns:
        每个因子的检查结果
    """
    results: list[FactorExposureCheck] = []
    for name, weight in sorted(factor_weights.items()):
        results.append(FactorExposureCheck(
            factor_name=name,
            weight=weight,
            threshold=threshold,
        ))
    return results


def check_slippage_robustness(
    slippage_results: Sequence[tuple[float, float, float]],
) -> list[SlippageRobustnessCheck]:
    """包装滑点压力测试结果。

    Args:
        slippage_results: [(slippage_bps, annualized_return, sharpe_ratio), ...]

    Returns:
        检查结果列表
    """
    return [
        SlippageRobustnessCheck(
            slippage_bps=bps,
            annualized_return=ret,
            sharpe_ratio=sharpe,
        )
        for bps, ret, sharpe in slippage_results
    ]


def check_start_date_sensitivity(
    results: Sequence[tuple[str, float]],
    *,
    threshold: float = START_DATE_SENSITIVITY_THRESHOLD,
) -> StartDateSensitivityCheck:
    """检查起始日期敏感度。

    Args:
        results: [(start_date_str, annualized_return), ...]
        threshold: 标准差阈值

    Returns:
        敏感度检查结果
    """
    if not results:
        return StartDateSensitivityCheck(
            start_dates=(),
            annualized_returns=(),
            returns_std=0.0,
            returns_range=0.0,
            threshold=threshold,
        )

    dates = tuple(r[0] for r in results)
    returns = tuple(r[1] for r in results)

    n = len(returns)
    mean_ret = sum(returns) / n
    if n > 1:
        variance = sum((r - mean_ret) ** 2 for r in returns) / (n - 1)
        std = math.sqrt(variance)
    else:
        std = 0.0
    ret_range = max(returns) - min(returns)

    return StartDateSensitivityCheck(
        start_dates=dates,
        annualized_returns=returns,
        returns_std=std,
        returns_range=ret_range,
        threshold=threshold,
    )


def judge_alpha_authenticity(
    config_name: str,
    factor_weights: dict[str, float],
    *,
    slippage_results: Sequence[tuple[float, float, float]] | None = None,
    start_date_results: Sequence[tuple[str, float]] | None = None,
    factor_threshold: float = FACTOR_WEIGHT_BETA_THRESHOLD,
    start_date_threshold: float = START_DATE_SENSITIVITY_THRESHOLD,
    min_realistic_slippage_bps: float = 5.0,
) -> AlphaAuthenticityReport:
    """综合判定策略 Alpha 真实性。

    评分规则（总分 10）：
    - 因子集中度（4 分）：无 beta 暴露 → 4 分；有 → 按暴露因子数扣分
    - 滑点鲁棒性（3 分）：在最低合理滑点下收益为正 → 3 分
    - 起始日期稳定性（3 分）：标准差低于阈值 → 3 分

    Verdict:
    - score >= 7: "GENUINE_ALPHA"
    - score >= 4: "MIXED_ALPHA_BETA"
    - score < 4:  "LIKELY_BETA"

    Args:
        config_name: 配置名
        factor_weights: 因子权重
        slippage_results: 滑点测试结果（可选）
        start_date_results: 起始日期测试结果（可选）
        factor_threshold: 因子权重阈值
        start_date_threshold: 起始日期敏感度阈值
        min_realistic_slippage_bps: 最低合理滑点
    """
    warnings: list[str] = []
    score = 0.0

    # --- 1) 因子暴露检查（4 分）---
    factor_checks = check_factor_exposure(
        factor_weights, threshold=factor_threshold,
    )
    beta_factors = [c for c in factor_checks if c.is_beta]
    if not beta_factors:
        score += 4.0
    else:
        # 每个 beta 因子扣 2 分，最多扣 4 分
        deduction = min(len(beta_factors) * 2.0, 4.0)
        score += max(0.0, 4.0 - deduction)
        for bf in beta_factors:
            msg = (
                f"因子 '{bf.factor_name}' 权重 {bf.weight:.0%} "
                f"超过阈值 {bf.threshold:.0%}，疑似 beta 暴露"
            )
            warnings.append(msg)
            logger.warning("Alpha 真实性: %s — %s", config_name, msg)

    # --- 2) 滑点鲁棒性检查（3 分）---
    slippage_checks: list[SlippageRobustnessCheck] = []
    if slippage_results is not None:
        slippage_checks = check_slippage_robustness(slippage_results)
        # 找到 >= min_realistic_slippage_bps 的最小滑点结果
        realistic = [
            c for c in slippage_checks
            if c.slippage_bps >= min_realistic_slippage_bps
        ]
        if realistic:
            # 按 bps 从小到大排序
            realistic.sort(key=lambda c: c.slippage_bps)
            base_check = realistic[0]
            if base_check.is_positive and base_check.sharpe_ratio > 0:
                score += 3.0
            elif base_check.is_positive:
                score += 1.5
                warnings.append(
                    f"在 {base_check.slippage_bps:.0f}bps 滑点下收益为正"
                    f"但 Sharpe={base_check.sharpe_ratio:.2f} ≤ 0"
                )
            else:
                warnings.append(
                    f"在 {base_check.slippage_bps:.0f}bps 滑点下"
                    f"年化收益={base_check.annualized_return:.2%} 为负"
                )
        # 检查是否有不切实际的低滑点
        unrealistic = [
            c for c in slippage_checks
            if c.slippage_bps < min_realistic_slippage_bps and c.is_positive
        ]
        if unrealistic and not any(c.is_positive for c in realistic):
            warnings.append(
                "仅在不切实际的低滑点下收益为正，真实交易可能亏损"
            )
    else:
        # 未提供滑点数据时基于因子权重推测
        score += 1.5  # 给予中性分
        warnings.append("未提供滑点压力测试数据，无法验证滑点鲁棒性")

    # --- 3) 起始日期敏感度（3 分）---
    start_date_check: StartDateSensitivityCheck | None = None
    if start_date_results is not None:
        start_date_check = check_start_date_sensitivity(
            start_date_results, threshold=start_date_threshold,
        )
        if not start_date_check.is_sensitive:
            score += 3.0
        else:
            # 按敏感程度扣分
            ratio = start_date_check.returns_std / start_date_threshold
            deduction = min(ratio, 3.0)
            score += max(0.0, 3.0 - deduction)
            warnings.append(
                f"起始日期敏感: 年化收益标准差 "
                f"{start_date_check.returns_std:.2%} > "
                f"阈值 {start_date_threshold:.2%}"
            )
    else:
        score += 1.5
        warnings.append("未提供起始日期敏感度数据")

    # --- 综合判定 ---
    score = round(min(score, 10.0), 2)
    if score >= 7.0:
        verdict = "GENUINE_ALPHA"
    elif score >= 4.0:
        verdict = "MIXED_ALPHA_BETA"
    else:
        verdict = "LIKELY_BETA"

    return AlphaAuthenticityReport(
        config_name=config_name,
        factor_checks=factor_checks,
        slippage_checks=slippage_checks,
        start_date_check=start_date_check,
        score=score,
        verdict=verdict,
        warnings=warnings,
    )
