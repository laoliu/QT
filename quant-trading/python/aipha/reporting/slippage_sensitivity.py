"""统一滑点灵敏度测试 API（D1-2）

提供可复用的 slippage sensitivity 测试接口，
替代分散在各脚本中的独立实现。

使用示例::

    from aipha.reporting.slippage_sensitivity import run_slippage_sensitivity

    result = run_slippage_sensitivity(
        config_path="configs/cb_strategies/my_strategy.yml",
        multipliers=[1.0, 2.0, 4.0],
    )
    print(result.pass_at_4x)  # SR > 0.8?
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SlippagePoint:
    """单个滑点倍数下的结果。"""

    multiplier: float
    slippage_bps: float
    annualized_return: float
    sharpe_ratio: float
    max_drawdown: float
    calmar_ratio: float


@dataclass
class SlippageSensitivityResult:
    """滑点灵敏度测试完整结果。"""

    config_path: str
    base_slippage_bps: float
    points: list[SlippagePoint] = field(default_factory=list)

    @property
    def pass_at_2x(self) -> bool:
        """2x 滑点下 SR > 0.8。"""
        for p in self.points:
            if abs(p.multiplier - 2.0) < 0.01:
                return p.sharpe_ratio > 0.8
        return False

    @property
    def pass_at_4x(self) -> bool:
        """4x 滑点下 SR > 0.8。"""
        for p in self.points:
            if abs(p.multiplier - 4.0) < 0.01:
                return p.sharpe_ratio > 0.8
        return False

    @property
    def sharpe_by_multiplier(self) -> dict[float, float]:
        """各倍数 → Sharpe。"""
        return {p.multiplier: p.sharpe_ratio for p in self.points}

    def to_dict(self) -> dict[str, Any]:
        """转为可序列化 dict。"""
        return {
            "config_path": self.config_path,
            "base_slippage_bps": self.base_slippage_bps,
            "pass_at_2x": self.pass_at_2x,
            "pass_at_4x": self.pass_at_4x,
            "points": [
                {
                    "multiplier": p.multiplier,
                    "slippage_bps": p.slippage_bps,
                    "annualized_return": p.annualized_return,
                    "sharpe_ratio": p.sharpe_ratio,
                    "max_drawdown": p.max_drawdown,
                    "calmar_ratio": p.calmar_ratio,
                }
                for p in self.points
            ],
        }


def run_slippage_sensitivity(
    config_path: str | Path,
    multipliers: list[float] | None = None,
    output_dir: str | Path | None = None,
) -> SlippageSensitivityResult:
    """运行滑点灵敏度测试。

    对给定配置文件，按倍数列表逐一运行回测，收集 SR/AR/MDD。

    Args:
        config_path: 策略回测 YAML 配置路径
        multipliers: 滑点倍数列表，默认 [1.0, 2.0, 4.0]
        output_dir: 输出目录（可选），不指定则使用临时目录

    Returns:
        SlippageSensitivityResult
    """
    from aipha.backtest.config_runner import run_from_config

    if multipliers is None:
        multipliers = [1.0, 2.0, 4.0]

    config_path = Path(config_path)

    # 读取基础滑点
    import yaml

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    base_bps = _extract_base_slippage(cfg)
    result = SlippageSensitivityResult(
        config_path=str(config_path),
        base_slippage_bps=base_bps,
    )

    for mult in sorted(multipliers):
        target_bps = base_bps * mult
        overrides: dict[str, Any] = {"slippage_bps": target_bps}

        # 构造输出目录
        run_output = None
        if output_dir:
            run_output = str(Path(output_dir) / f"slippage_{mult:.1f}x")

        try:
            report = run_from_config(
                str(config_path),
                param_overrides=overrides,
                output_dir=run_output,
            )
            metrics = report.performance
            point = SlippagePoint(
                multiplier=mult,
                slippage_bps=target_bps,
                annualized_return=metrics.annualized_return,
                sharpe_ratio=metrics.sharpe_ratio,
                max_drawdown=metrics.max_drawdown,
                calmar_ratio=metrics.calmar_ratio,
            )
        except Exception as e:
            logger.error(f"滑点 {mult}x ({target_bps}bps) 回测失败: {e}")
            point = SlippagePoint(
                multiplier=mult,
                slippage_bps=target_bps,
                annualized_return=0.0,
                sharpe_ratio=0.0,
                max_drawdown=1.0,
                calmar_ratio=0.0,
            )

        result.points.append(point)

    return result


def _extract_base_slippage(cfg: dict) -> float:
    """从 YAML 配置提取基础滑点 bps。"""
    # 尝试多个可能的配置路径
    if "slippage_bps" in cfg:
        return float(cfg["slippage_bps"])
    if "cost" in cfg and isinstance(cfg["cost"], dict):
        if "slippage_bps" in cfg["cost"]:
            return float(cfg["cost"]["slippage_bps"])
    if "execution" in cfg and isinstance(cfg["execution"], dict):
        if "slippage_bps" in cfg["execution"]:
            return float(cfg["execution"]["slippage_bps"])
    # 默认 5.0 bps（CB 默认值）
    return 5.0
