"""组合健康检查 — 综合巡检策略组合状态"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class HealthCheckItem:
    name: str
    passed: bool
    level: str  # "ok" | "warning" | "critical"
    message: str
    value: float | None = None
    threshold: float | None = None


@dataclass
class HealthReport:
    timestamp: datetime
    profile_name: str
    items: list[HealthCheckItem] = field(default_factory=list)

    @property
    def has_critical(self) -> bool:
        return any(i.level == "critical" for i in self.items)

    @property
    def critical_items(self) -> list[HealthCheckItem]:
        return [i for i in self.items if i.level == "critical"]

    @property
    def warning_items(self) -> list[HealthCheckItem]:
        return [i for i in self.items if i.level == "warning"]

    def summary(self) -> str:
        total = len(self.items)
        ok = sum(1 for i in self.items if i.passed)
        warns = len(self.warning_items)
        crits = len(self.critical_items)
        return (
            f"[{self.profile_name}] {ok}/{total} OK | "
            f"{warns} warning | {crits} critical"
        )


class ComboHealthChecker:
    """策略组合健康检查器"""

    def __init__(self, config: dict[str, Any] | None = None, profile_name: str = ""):
        self._config = config or {}
        self._profile = profile_name

    @classmethod
    def from_config(cls, yaml_path: str | Path) -> ComboHealthChecker:
        import yaml
        path = Path(yaml_path)
        if not path.exists():
            return cls()
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(config=raw, profile_name=raw.get("profile_name", str(path.stem)))

    def run_check(
        self,
        nav_dict: dict[str, list[float]],
        weights: dict[str, float],
    ) -> HealthReport:
        """执行全套健康检查"""
        items: list[HealthCheckItem] = []
        items.append(self.check_risk_contribution(nav_dict, weights))
        # 生成组合净值
        combo_nav = self._combine_nav(nav_dict, weights)
        items.append(self.check_drawdown(combo_nav))
        items.append(self.check_alpha_decay(combo_nav))
        return HealthReport(
            timestamp=datetime.now(), profile_name=self._profile, items=items,
        )

    def check_risk_contribution(
        self, nav_dict: dict[str, list[float]], weights: dict[str, float],
    ) -> HealthCheckItem:
        """检查风险集中度 (最大风险贡献占比)"""
        thresh = self._config.get("max_risk_contrib", 0.5)
        if not nav_dict or not weights:
            return HealthCheckItem("risk_contribution", True, "ok", "无数据")
        # 简化: 用波动率加权
        vols: dict[str, float] = {}
        for name, nav in nav_dict.items():
            if len(nav) < 3:
                continue
            rets = [(nav[i] / nav[i - 1] - 1) for i in range(1, len(nav)) if nav[i - 1] > 0]
            vols[name] = float(np.std(rets)) if rets else 0.0

        total_risk = sum(weights.get(n, 0) * v for n, v in vols.items())
        max_contrib = 0.0
        max_name = ""
        for n, v in vols.items():
            contrib = weights.get(n, 0) * v / total_risk if total_risk > 0 else 0
            if contrib > max_contrib:
                max_contrib = contrib
                max_name = n

        passed = max_contrib <= thresh
        level = "ok" if passed else ("warning" if max_contrib < thresh * 1.5 else "critical")
        return HealthCheckItem(
            "risk_contribution", passed, level,
            f"最大风险贡献: {max_name} = {max_contrib:.1%}",
            value=max_contrib, threshold=thresh,
        )

    def check_drawdown(self, combo_nav: list[float]) -> HealthCheckItem:
        thresh = self._config.get("max_drawdown", 0.20)
        if len(combo_nav) < 2:
            return HealthCheckItem("max_drawdown", True, "ok", "数据不足")
        peak = combo_nav[0]
        max_dd = 0.0
        for v in combo_nav:
            peak = max(peak, v)
            dd = (peak - v) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)
        passed = max_dd <= thresh
        level = "ok" if passed else ("warning" if max_dd < thresh * 1.3 else "critical")
        return HealthCheckItem(
            "max_drawdown", passed, level,
            f"最大回撤 {max_dd:.2%}", value=max_dd, threshold=thresh,
        )

    def check_alpha_decay(self, combo_nav: list[float]) -> HealthCheckItem:
        """检查近期收益是否持续下滑"""
        window = self._config.get("alpha_decay_window", 30)
        if len(combo_nav) < window * 2:
            return HealthCheckItem("alpha_decay", True, "ok", "窗口不足")
        first_half = combo_nav[-window * 2:-window]
        second_half = combo_nav[-window:]
        ret_first = (first_half[-1] / first_half[0] - 1) if first_half[0] > 0 else 0
        ret_second = (second_half[-1] / second_half[0] - 1) if second_half[0] > 0 else 0
        decay = ret_first - ret_second
        thresh = self._config.get("alpha_decay_threshold", 0.05)
        passed = decay <= thresh
        level = "ok" if passed else "warning"
        return HealthCheckItem(
            "alpha_decay", passed, level,
            f"Alpha 衰减 {decay:.2%} (前半 {ret_first:.2%} → 后半 {ret_second:.2%})",
            value=decay, threshold=thresh,
        )

    @staticmethod
    def _combine_nav(
        nav_dict: dict[str, list[float]], weights: dict[str, float],
    ) -> list[float]:
        if not nav_dict or not weights:
            return []
        min_len = min(len(v) for v in nav_dict.values())
        combo = [0.0] * min_len
        for name, nav in nav_dict.items():
            w = weights.get(name, 0)
            for i in range(min_len):
                combo[i] += w * nav[i]
        return combo
