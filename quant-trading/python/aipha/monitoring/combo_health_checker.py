"""组合健康检查器 — R116 专家评审 A2/A3/C2/C4 监控实施。

定期检查组合健康状态：
- A2: 风险贡献监控（CSI2000 ≤ 50%）
- A3: Alpha 衰减触发器（滚动 12M SR < 0 连续 6 月 → 强制审查）
- C2: MDD 分级预警（-20% 减仓, -25% 冻结）
- C4: PBO 季度复检

Usage::

    checker = ComboHealthChecker.from_config("configs/paper/r116_combo.yml")
    report = checker.run_check(nav_dict)
    if report.has_critical:
        print("CRITICAL:", report.critical_items)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR

from aipha.utils.metrics import compute_sharpe_ratio

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class HealthCheckItem:
    """单项检查结果。"""
    name: str
    passed: bool
    level: str  # "info" | "warning" | "critical"
    message: str
    value: Optional[float] = None
    threshold: Optional[float] = None


@dataclass
class HealthReport:
    """组合健康检查报告。"""
    timestamp: str = ""
    profile_name: str = ""
    items: list[HealthCheckItem] = field(default_factory=list)

    @property
    def has_critical(self) -> bool:
        return any(not i.passed and i.level == "critical" for i in self.items)

    @property
    def critical_items(self) -> list[HealthCheckItem]:
        return [i for i in self.items if not i.passed and i.level == "critical"]

    @property
    def warning_items(self) -> list[HealthCheckItem]:
        return [i for i in self.items if not i.passed and i.level == "warning"]

    def summary(self) -> str:
        """返回可读摘要。"""
        lines = [f"=== ComboHealth: {self.profile_name} ({self.timestamp}) ==="]
        for item in self.items:
            status = "✅" if item.passed else ("❌" if item.level == "critical" else "⚠️")
            lines.append(f"  {status} {item.name}: {item.message}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主类（骨架）
# ---------------------------------------------------------------------------

class ComboHealthChecker:
    """组合健康检查器。"""

    def __init__(self, config: dict[str, Any], profile_name: str = ""):
        self._config = config
        self._profile_name = profile_name
        self._mon_cfg = config.get("monitoring", {})

    @classmethod
    def from_config(cls, yaml_path: str | Path) -> ComboHealthChecker:
        """从 YAML 配置文件加载。"""
        import yaml

        path = Path(yaml_path)
        with open(path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        return cls(config, profile_name=config.get("profile_name", path.stem))

    def run_check(
        self,
        nav_dict: dict[str, pd.Series],
        weights: dict[str, float],
    ) -> HealthReport:
        """执行全部健康检查。"""
        from datetime import datetime

        report = HealthReport(
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
            profile_name=self._profile_name,
        )

        # 合成组合 NAV
        aligned = pd.DataFrame(nav_dict).dropna()
        if aligned.empty:
            report.items.append(HealthCheckItem(
                "data", False, "critical", "NAV 数据对齐后为空",
            ))
            return report

        combo_ret = sum(
            aligned[k].pct_change() * weights.get(k, 0)
            for k in aligned.columns if k in weights
        )
        combo_nav = (1 + combo_ret).cumprod()
        combo_nav.iloc[0] = 1.0

        # A2: 风险贡献
        if self._mon_cfg.get("risk_contribution"):
            report.items.append(self.check_risk_contribution(nav_dict, weights))

        # A3: Alpha 衰减
        if self._mon_cfg.get("alpha_decay"):
            report.items.append(self.check_alpha_decay(combo_nav))

        # C2: MDD
        if self._mon_cfg.get("drawdown"):
            report.items.append(self.check_drawdown(combo_nav))

        return report

    def check_risk_contribution(
        self,
        nav_dict: dict[str, pd.Series],
        weights: dict[str, float],
    ) -> HealthCheckItem:
        """A2: 风险贡献监控 — CSI2000 风险贡献不得超过配置上限。"""
        cfg = self._mon_cfg.get("risk_contribution", {})
        max_rc = cfg.get("ml_csi2000_max", 0.50)
        warn_rc = cfg.get("ml_csi2000_warn", 0.45)

        aligned = pd.DataFrame(nav_dict).dropna()
        if aligned.shape[0] < 60:
            return HealthCheckItem(
                "A2_risk_contrib", True, "info",
                f"数据不足 ({aligned.shape[0]}<60 天), 跳过检查",
            )

        rets = aligned.pct_change().dropna()
        w_vec = np.array([weights.get(c, 0) for c in rets.columns])
        cov = rets.cov().values
        port_vol = np.sqrt(w_vec @ cov @ w_vec)

        if port_vol < 1e-10:
            return HealthCheckItem("A2_risk_contrib", True, "info", "组合波动率为零")

        marginal = cov @ w_vec
        rc = w_vec * marginal / (port_vol ** 2)

        csi2000_idx = list(rets.columns).index("ml_csi2000") if "ml_csi2000" in rets.columns else None
        if csi2000_idx is None:
            return HealthCheckItem("A2_risk_contrib", True, "info", "无 CSI2000 组件")

        rc_val = float(rc[csi2000_idx])
        passed = rc_val <= max_rc
        level = "critical" if rc_val > max_rc else ("warning" if rc_val > warn_rc else "info")
        msg = f"CSI2000 风险贡献 {rc_val:.1%} (上限 {max_rc:.0%})"

        return HealthCheckItem("A2_risk_contrib", passed, level, msg, rc_val, max_rc)

    def check_alpha_decay(
        self,
        combo_nav: pd.Series,
    ) -> HealthCheckItem:
        """A3: Alpha 衰减触发器 — 滚动 SR < 0 连续 N 月触发审查。"""
        cfg = self._mon_cfg.get("alpha_decay", {})
        window = cfg.get("rolling_sr_window", 252)
        sr_thresh = cfg.get("sr_threshold", 0.0)
        max_months = cfg.get("max_consecutive_months", 6)

        rets = combo_nav.pct_change().dropna()
        if len(rets) < window + 21:
            return HealthCheckItem(
                "A3_alpha_decay", True, "info",
                f"数据不足 ({len(rets)}<{window + 21} 天), 跳过",
            )

        # 计算月末滚动 SR（使用 compute_sharpe_ratio 风格的年化）
        ann_factor = TRADING_DAYS_PER_YEAR ** 0.5  # 年化因子（§13.8: sqrt(244)）
        rolling_mean = rets.rolling(window).mean()
        rolling_vol = rets.rolling(window).std()
        rolling_sr = (rolling_mean / rolling_vol.where(rolling_vol > 1e-12)) * ann_factor
        rolling_sr = rolling_sr.dropna()

        # 取每月末值
        monthly_sr = rolling_sr.resample("ME").last().dropna()

        # 统计尾部连续 SR < threshold 的月数
        consecutive = 0
        for sr_val in reversed(monthly_sr.values):
            if sr_val < sr_thresh:
                consecutive += 1
            else:
                break

        passed = consecutive < max_months
        current_sr = float(monthly_sr.iloc[-1]) if len(monthly_sr) > 0 else 0.0
        level = "critical" if not passed else ("warning" if consecutive >= 3 else "info")
        msg = (
            f"滚动 12M SR={current_sr:.2f}, "
            f"连续 {consecutive} 月 < {sr_thresh} (上限 {max_months} 月)"
        )
        return HealthCheckItem("A3_alpha_decay", passed, level, msg, current_sr, sr_thresh)

    def check_drawdown(
        self,
        combo_nav: pd.Series,
    ) -> HealthCheckItem:
        """C2: MDD 分级预警 — -20% 减仓, -25% 冻结。"""
        cfg = self._mon_cfg.get("drawdown", {})
        warn_level = cfg.get("warn_level", -0.20)
        halt_level = cfg.get("halt_level", -0.25)

        peak = combo_nav.cummax()
        dd = (combo_nav - peak) / peak
        current_dd = float(dd.iloc[-1])
        mdd = float(dd.min())

        if current_dd <= halt_level:
            return HealthCheckItem(
                "C2_drawdown", False, "critical",
                f"当前回撤 {current_dd:.1%} 触发冻结线 ({halt_level:.0%}), MDD={mdd:.1%}",
                current_dd, halt_level,
            )
        if current_dd <= warn_level:
            return HealthCheckItem(
                "C2_drawdown", False, "warning",
                f"当前回撤 {current_dd:.1%} 触发减仓线 ({warn_level:.0%}), MDD={mdd:.1%}",
                current_dd, warn_level,
            )
        return HealthCheckItem(
            "C2_drawdown", True, "info",
            f"当前回撤 {current_dd:.1%}, MDD={mdd:.1%} — 正常",
            current_dd, warn_level,
        )
