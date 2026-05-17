"""综合验证入口 — Alpha-Plus 策略组合端到端验证。

提供单一入口 ``run_comprehensive_validation()`` 编排：
1. Walk-Forward 质量门禁
2. DSR（Deflated Sharpe Ratio）校正
3. 蒙特卡洛排列检验
4. 分段评估（全量 / 新规前 / 新规后）
5. 压力测试（历史场景）
6. 容量评估

产出统一的 ``ValidationReport`` 数据结构，可序列化为 JSON。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR

from aipha.backtest.advanced_validation import (
    DSRResult,
    DeflatedSharpeRatio,
    MonteCarloPermutationTest,
    PermutationTestResult,
)

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# 数据结构
# ------------------------------------------------------------------

@dataclass
class SegmentMetrics:
    """某时间段的绩效指标。"""
    label: str
    start_date: str
    end_date: str
    annual_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    calmar_ratio: float = 0.0
    win_year_ratio: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class StressScenario:
    """压力测试场景。"""
    name: str
    start_date: str
    end_date: str
    drawdown: float = 0.0
    recovery_days: int = 0
    return_during: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class CapacityResult:
    """容量评估结果。"""
    capital_level: float    # 万元
    annual_return: float = 0.0
    sharpe_ratio: float = 0.0
    avg_participation: float = 0.0  # 平均市场参与率
    capacity_ok: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class ComprehensiveReport:
    """综合验证报告。"""
    strategy_name: str
    run_date: str = ""
    # 核心验证
    dsr: dict[str, Any] = field(default_factory=dict)
    permutation: dict[str, Any] = field(default_factory=dict)
    wf_gates: dict[str, Any] = field(default_factory=dict)
    # 分段
    segments: list[dict[str, Any]] = field(default_factory=list)
    # 压力测试
    stress_tests: list[dict[str, Any]] = field(default_factory=list)
    # 容量
    capacity: list[dict[str, Any]] = field(default_factory=list)
    # 总结
    verdict: str = "PENDING"
    issues: list[str] = field(default_factory=list)
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_name": self.strategy_name,
            "run_date": self.run_date,
            "dsr": self.dsr,
            "permutation": self.permutation,
            "wf_gates": self.wf_gates,
            "segments": self.segments,
            "stress_tests": self.stress_tests,
            "capacity": self.capacity,
            "verdict": self.verdict,
            "issues": self.issues,
            "score": self.score,
        }


# ------------------------------------------------------------------
# 默认压力场景
# ------------------------------------------------------------------

DEFAULT_STRESS_SCENARIOS = [
    {"name": "2018_trade_war", "start": "2018-06-15", "end": "2018-12-31"},
    {"name": "2020_covid", "start": "2020-01-20", "end": "2020-03-23"},
    {"name": "2022_cb_new_rule", "start": "2022-07-01", "end": "2022-09-30"},
    {"name": "2024_stimulus", "start": "2024-09-24", "end": "2024-10-31"},
]

# CB 新规分界
CB_NEW_RULE_DATE = "2022-08-01"


# ------------------------------------------------------------------
# 验证器
# ------------------------------------------------------------------

@dataclass
class ValidationConfig:
    """综合验证配置。"""
    n_trials: int = 45             # 策略/参数尝试总数（DSR用）
    dsr_significance: float = 0.05
    permutation_n: int = 500
    permutation_seed: int = 42
    # 硬门槛
    min_annual_return: float = 0.20
    max_drawdown: float = 0.30
    min_sharpe: float = 0.8
    # Post-2022 门槛
    post_rule_min_sr: float = 0.5
    post_rule_min_ar: float = 0.05


class ComprehensiveValidator:
    """综合验证器 — 编排所有验证步骤。

    用法示例::

        validator = ComprehensiveValidator()
        report = validator.validate(
            strategy_name="combo_ew3",
            daily_returns=[...],
            wf_report={...},
        )
    """

    def __init__(self, config: ValidationConfig | None = None) -> None:
        self._config = config or ValidationConfig()
        self._dsr = DeflatedSharpeRatio(self._config.dsr_significance)
        self._perm = MonteCarloPermutationTest(
            n_permutations=self._config.permutation_n,
            random_seed=self._config.permutation_seed,
        )

    @property
    def config(self) -> ValidationConfig:
        return self._config

    def validate(
        self,
        strategy_name: str,
        daily_returns: list[float],
        wf_report: dict[str, Any] | None = None,
        nav_dates: list[str] | None = None,
        stress_scenarios: list[dict] | None = None,
    ) -> ComprehensiveReport:
        """执行综合验证，返回 ComprehensiveReport。

        Args:
            strategy_name: 策略名称
            daily_returns: 日收益率序列
            wf_report: Walk-Forward 报告字典（可选）
            nav_dates: 与 daily_returns 对应的日期列表 (YYYY-MM-DD)
            stress_scenarios: 压力测试场景列表
        """
        from datetime import date as _date
        report = ComprehensiveReport(
            strategy_name=strategy_name,
            run_date=str(_date.today()),
        )
        issues: list[str] = []
        score = 10.0  # 满分10，逐项扣分

        # 1. DSR
        report.dsr = self._run_dsr(daily_returns)
        if not report.dsr.get("is_significant", False):
            issues.append("DSR not significant (p={:.3f})".format(
                report.dsr.get("p_value", 1.0)))
            score -= 2.0

        # 2. 排列检验
        report.permutation = self._run_permutation(daily_returns)
        if not report.permutation.get("is_significant", False):
            issues.append("Permutation test not significant")
            score -= 1.5

        # 3. WF 门禁
        if wf_report:
            report.wf_gates = self._run_wf_gates(wf_report)
            if not report.wf_gates.get("passed", False):
                issues.append("WF gates not passed")
                score -= 2.0

        # 4. 分段评估
        if nav_dates and len(nav_dates) == len(daily_returns):
            report.segments = self._run_segments(daily_returns, nav_dates)
            score = self._check_segments(report.segments, issues, score)

        # 5. 压力测试
        if nav_dates and len(nav_dates) == len(daily_returns):
            scenarios = stress_scenarios or DEFAULT_STRESS_SCENARIOS
            report.stress_tests = self._run_stress(
                daily_returns, nav_dates, scenarios)

        report.issues = issues
        report.score = max(score, 0.0)
        report.verdict = "PASS" if score >= 6.0 and not any(
            "DSR" in i for i in issues) else "FAIL"
        return report

    # -- 私有方法 --

    def _run_dsr(self, daily_returns: list[float]) -> dict[str, Any]:
        """运行 DSR 校正。"""
        cfg = self._config
        if len(daily_returns) < 30:
            return {"error": "insufficient data", "is_significant": False}
        n = len(daily_returns)
        mean = sum(daily_returns) / n
        var = sum((r - mean) ** 2 for r in daily_returns) / max(n - 1, 1)
        std = math.sqrt(var) if var > 0 else 0.01
        sharpe = (mean / std) * math.sqrt(TRADING_DAYS_PER_YEAR) if std > 1e-15 else 0.0
        result = self._dsr.compute(
            observed_sharpe=sharpe,
            n_trials=cfg.n_trials,
            trading_days=n,
            daily_returns=daily_returns,
        )
        return result.to_dict()

    def _run_permutation(self, daily_returns: list[float]) -> dict[str, Any]:
        """运行蒙特卡洛排列检验。"""
        if len(daily_returns) < 10:
            return {"error": "insufficient data", "is_significant": False}
        result = self._perm.test(daily_returns)
        return result.to_dict()

    def _run_wf_gates(self, wf_report: dict[str, Any]) -> dict[str, Any]:
        """运行 WF 质量门禁。"""
        from aipha.backtest.quality_gates import check_walk_forward_gates
        gate_report = check_walk_forward_gates(wf_report)
        return {
            "passed": gate_report.all_passed,
            "score": gate_report.wf_score,
            "verdict": gate_report.wf_verdict,
            "errors": [str(r) for r in gate_report.errors],
        }

    def _run_segments(
        self,
        daily_returns: list[float],
        nav_dates: list[str],
    ) -> list[dict[str, Any]]:
        """分段评估：全量 / 新规前 / 新规后。"""
        segments = []
        # 全量
        full = self._calc_segment("full", daily_returns, nav_dates)
        segments.append(full.to_dict())
        # 分段
        pre_idx = [i for i, d in enumerate(nav_dates) if d < CB_NEW_RULE_DATE]
        post_idx = [i for i, d in enumerate(nav_dates) if d >= CB_NEW_RULE_DATE]
        if pre_idx:
            pre_rets = [daily_returns[i] for i in pre_idx]
            pre_dates = [nav_dates[i] for i in pre_idx]
            segments.append(
                self._calc_segment("pre_2022_rule", pre_rets, pre_dates).to_dict())
        if post_idx:
            post_rets = [daily_returns[i] for i in post_idx]
            post_dates = [nav_dates[i] for i in post_idx]
            segments.append(
                self._calc_segment("post_2022_rule", post_rets, post_dates).to_dict())
        return segments

    @staticmethod
    def _calc_segment(
        label: str, rets: list[float], dates: list[str],
    ) -> SegmentMetrics:
        """计算某段的绩效指标。"""
        n = len(rets)
        if n < 5:
            return SegmentMetrics(label=label, start_date="", end_date="")
        mean = sum(rets) / n
        var = sum((r - mean) ** 2 for r in rets) / max(n - 1, 1)
        std = math.sqrt(var) if var > 0 else 0.01
        annual_return = mean * TRADING_DAYS_PER_YEAR
        sharpe = (mean / std) * math.sqrt(TRADING_DAYS_PER_YEAR) if std > 1e-15 else 0.0
        # 简化最大回撤
        nav = 1.0
        peak = 1.0
        mdd = 0.0
        for r in rets:
            nav *= (1 + r)
            peak = max(peak, nav)
            dd = (peak - nav) / peak
            mdd = max(mdd, dd)
        calmar = annual_return / mdd if mdd > 0.01 else 0.0
        return SegmentMetrics(
            label=label,
            start_date=dates[0] if dates else "",
            end_date=dates[-1] if dates else "",
            annual_return=annual_return,
            sharpe_ratio=sharpe,
            max_drawdown=mdd,
            calmar_ratio=calmar,
        )

    def _check_segments(
        self,
        segments: list[dict[str, Any]],
        issues: list[str],
        score: float,
    ) -> float:
        """检查分段是否达标。"""
        cfg = self._config
        for seg in segments:
            label = seg.get("label", "")
            if label == "post_2022_rule":
                sr = seg.get("sharpe_ratio", 0.0)
                ar = seg.get("annual_return", 0.0)
                if sr < cfg.post_rule_min_sr:
                    issues.append(f"Post-2022 SR={sr:.2f} < {cfg.post_rule_min_sr}")
                    score -= 1.5
                if ar < cfg.post_rule_min_ar:
                    issues.append(f"Post-2022 AR={ar:.1%} < {cfg.post_rule_min_ar:.0%}")
                    score -= 1.0
        return score

    @staticmethod
    def _run_stress(
        daily_returns: list[float],
        nav_dates: list[str],
        scenarios: list[dict],
    ) -> list[dict[str, Any]]:
        """压力测试：计算各历史场景的回撤。"""
        results = []
        date_idx = {d: i for i, d in enumerate(nav_dates)}
        for sc in scenarios:
            start = sc.get("start", "")
            end = sc.get("end", "")
            indices = [i for d, i in date_idx.items() if start <= d <= end]
            if not indices:
                results.append(StressScenario(
                    name=sc["name"], start_date=start, end_date=end,
                ).to_dict())
                continue
            rets = [daily_returns[i] for i in sorted(indices)]
            nav = 1.0
            peak = 1.0
            mdd = 0.0
            total_ret = 1.0
            for r in rets:
                nav *= (1 + r)
                total_ret *= (1 + r)
                peak = max(peak, nav)
                mdd = max(mdd, (peak - nav) / peak)
            results.append(StressScenario(
                name=sc["name"],
                start_date=start,
                end_date=end,
                drawdown=mdd,
                return_during=total_ret - 1,
            ).to_dict())
        return results
