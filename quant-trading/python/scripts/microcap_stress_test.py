"""微盘股压力测试模块

模拟 CSI2000 极端下跌场景（参照 2024-02 DMA 踩踏），
评估 Tiered Combo V3.1 组合在微盘股冲击下的最大损失。

用法:
    python scripts/microcap_stress_test.py
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


@dataclass
class StressScenarioResult:
    """单个压力场景测试结果"""
    name: str
    description: str
    portfolio_loss_pct: float
    components: dict[str, float] = field(default_factory=dict)
    circuit_breaker_triggered: bool = False
    pass_threshold: bool = True


@dataclass
class MicrocapStressReport:
    """微盘股压力测试报告"""
    scenarios: list[StressScenarioResult] = field(default_factory=list)
    worst_loss_pct: float = 0.0
    all_pass: bool = True
    max_acceptable_loss: float = -15.0


# V3.1 组合权重
V31_WEIGHTS = {
    "csi2000": 0.35,
    "cb_stock_quality": 0.15,
    "csi300_ge200b": 0.15,
    "etf_faa": 0.35,
}

# V3.1 组件对微盘股的 beta（基于历史回归估计）
MICROCAP_BETAS = {
    "csi2000": 1.20,          # 高 beta，核心微盘敞口
    "cb_stock_quality": 0.30,  # CB 与微盘有间接相关（正股）
    "csi300_ge200b": -0.05,    # 大盘股与微盘负相关
    "etf_faa": 0.10,           # ETF 可能持有部分中小盘
}


def define_scenarios() -> list[dict[str, Any]]:
    """定义微盘股压力场景"""
    return [
        {
            "name": "2024-02 DMA 踩踏",
            "description": "CSI2000 单周 -20%（参照 2024 年 2 月量化踩踏）",
            "csi2000_shock": -0.20,
            "cb_market_shock": -0.05,
            "csi300_shock": 0.02,
            "etf_shock": -0.03,
        },
        {
            "name": "2024-02 极端延续",
            "description": "CSI2000 两周 -30%（极端尾部场景）",
            "csi2000_shock": -0.30,
            "cb_market_shock": -0.10,
            "csi300_shock": 0.00,
            "etf_shock": -0.05,
        },
        {
            "name": "流动性枯竭",
            "description": "微盘股日均成交额缩减 80%，滑点放大 4x",
            "csi2000_shock": -0.15,
            "cb_market_shock": -0.03,
            "csi300_shock": 0.01,
            "etf_shock": -0.02,
            "slippage_multiplier": 4.0,
        },
        {
            "name": "信用收缩+微盘下跌",
            "description": "利率骤升 100bp，信用收缩，微盘受冲击",
            "csi2000_shock": -0.18,
            "cb_market_shock": -0.08,
            "csi300_shock": -0.05,
            "etf_shock": -0.04,
        },
        {
            "name": "2015 股灾级别",
            "description": "全市场系统性暴跌",
            "csi2000_shock": -0.25,
            "cb_market_shock": -0.15,
            "csi300_shock": -0.15,
            "etf_shock": -0.10,
        },
    ]


def run_scenario(scenario: dict[str, Any]) -> StressScenarioResult:
    """执行单个压力场景"""
    component_losses = {
        "csi2000": scenario["csi2000_shock"],
        "cb_stock_quality": scenario["cb_market_shock"],
        "csi300_ge200b": scenario["csi300_shock"],
        "etf_faa": scenario["etf_shock"],
    }

    # 考虑滑点放大
    slippage_mult = scenario.get("slippage_multiplier", 1.0)
    if slippage_mult > 1.0:
        additional_cost = (slippage_mult - 1.0) * 0.001  # 基础滑点
        for k in component_losses:
            component_losses[k] -= additional_cost

    # 加权组合损失
    portfolio_loss = sum(
        V31_WEIGHTS[k] * component_losses[k]
        for k in V31_WEIGHTS
    )

    # 熔断检查：单日亏损 > 5% 触发线性减仓
    circuit_triggered = portfolio_loss < -0.05

    pass_ok = portfolio_loss >= -0.15  # 可接受最大损失 15%

    return StressScenarioResult(
        name=scenario["name"],
        description=scenario["description"],
        portfolio_loss_pct=portfolio_loss * 100,
        components={k: v * 100 for k, v in component_losses.items()},
        circuit_breaker_triggered=circuit_triggered,
        pass_threshold=pass_ok,
    )


def run_all_scenarios() -> MicrocapStressReport:
    """运行全部压力场景"""
    scenarios = define_scenarios()
    results: list[StressScenarioResult] = []

    for s in scenarios:
        result = run_scenario(s)
        results.append(result)
        logger.info(
            "场景 [%s]: 组合损失=%.1f%%, 熔断=%s, 通过=%s",
            result.name, result.portfolio_loss_pct,
            result.circuit_breaker_triggered, result.pass_threshold,
        )

    worst = min(r.portfolio_loss_pct for r in results)
    all_pass = all(r.pass_threshold for r in results)

    return MicrocapStressReport(
        scenarios=results,
        worst_loss_pct=worst,
        all_pass=all_pass,
    )


def print_report(report: MicrocapStressReport) -> None:
    """打印压力测试报告"""
    print("\n" + "=" * 75)
    print("微盘股压力测试报告 — Tiered Combo V3.1")
    print(f"组合权重: CSI2000={V31_WEIGHTS['csi2000']:.0%} | "
          f"CB={V31_WEIGHTS['cb_stock_quality']:.0%} | "
          f"CSI300={V31_WEIGHTS['csi300_ge200b']:.0%} | "
          f"ETF={V31_WEIGHTS['etf_faa']:.0%}")
    print("=" * 75)

    for r in report.scenarios:
        status = "PASS" if r.pass_threshold else "FAIL"
        cb = " [熔断]" if r.circuit_breaker_triggered else ""
        print(f"\n{'[' + status + ']':<8} {r.name}")
        print(f"  描述: {r.description}")
        print(f"  组合损失: {r.portfolio_loss_pct:+.1f}%{cb}")
        for comp, loss in r.components.items():
            w = V31_WEIGHTS[comp]
            print(f"    {comp:<20} {loss:+.1f}% (权重 {w:.0%})")

    print("\n" + "-" * 75)
    print(f"最大损失: {report.worst_loss_pct:+.1f}%")
    print(f"门槛: {report.max_acceptable_loss:.0f}%")
    result_str = "全部通过" if report.all_pass else "存在未通过场景"
    print(f"结论: {result_str}")
    print("=" * 75)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    report = run_all_scenarios()
    print_report(report)

    if not report.all_pass:
        logger.warning("压力测试存在未通过场景，需评估风控措施")
        sys.exit(1)


if __name__ == "__main__":
    main()
