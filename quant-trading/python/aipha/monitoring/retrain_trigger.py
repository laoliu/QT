"""
自动重训练触发器 — 基于 PSI 漂移 + IC 衰减决策。

当以下任一条件满足时触发重训练建议：
1. DriftMonitor PSI > 0.20（显著漂移）
2. HalflifeDecayMonitor ICIR 进入 DEGRADED 状态
3. 滚动 12 月 SR 连续 < 0 超 6 月

使用示例::

    trigger = RetrainTrigger(
        psi_threshold=0.20,
        icir_degraded_ratio=0.30,
        sr_negative_months=6,
    )
    decision = trigger.evaluate(drift_report, decay_snapshot)
    if decision.should_retrain:
        # 执行重训练 ...

参考：
    - 知识库 T 章节 "Agent 化研发"
    - McLean & Pontiff 2016: PSI > 0.2 → 显著偏移
    - 五原则 B: alpha 衰减检验
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class RetrainReason(Enum):
    """重训练触发原因"""

    PSI_DRIFT = "psi_drift"
    ICIR_DEGRADED = "icir_degraded"
    ALPHA_DECAY = "alpha_decay"
    SR_NEGATIVE = "sr_negative"
    MANUAL = "manual"


@dataclass
class RetrainDecision:
    """重训练决策结果"""

    should_retrain: bool
    reasons: list[RetrainReason] = field(default_factory=list)
    urgency: str = "none"  # none / low / medium / high
    details: dict = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now().isoformat()
    )

    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "should_retrain": self.should_retrain,
            "reasons": [r.value for r in self.reasons],
            "urgency": self.urgency,
            "details": self.details,
            "timestamp": self.timestamp,
        }


class RetrainTrigger:
    """自动重训练触发器"""

    def __init__(
        self,
        psi_threshold: float = 0.20,
        psi_warning: float = 0.10,
        icir_degraded_ratio: float = 0.30,
        sr_negative_months: int = 6,
    ) -> None:
        self.psi_threshold = psi_threshold
        self.psi_warning = psi_warning
        self.icir_degraded_ratio = icir_degraded_ratio
        self.sr_negative_months = sr_negative_months

    def evaluate(
        self,
        drift_report: Optional[object] = None,
        decay_snapshot: Optional[object] = None,
        rolling_sr_history: Optional[list[float]] = None,
    ) -> RetrainDecision:
        """综合评估是否需要重训练"""
        reasons: list[RetrainReason] = []
        details: dict = {}

        # 1) PSI 漂移检查
        if drift_report is not None:
            triggered, msg, info = self._check_psi(drift_report)
            details["psi"] = info
            if triggered:
                reasons.append(RetrainReason.PSI_DRIFT)
                logger.warning("PSI drift detected: %s", msg)

        # 2) ICIR 衰减检查
        if decay_snapshot is not None:
            triggered, msg, info = self._check_icir(decay_snapshot)
            details["icir"] = info
            if triggered:
                reasons.append(RetrainReason.ICIR_DEGRADED)
                logger.warning("ICIR degradation: %s", msg)

        # 3) 滚动 SR 连续为负
        if rolling_sr_history is not None:
            triggered, msg, info = self._check_sr(rolling_sr_history)
            details["sr"] = info
            if triggered:
                reasons.append(RetrainReason.SR_NEGATIVE)
                logger.warning("SR negative streak: %s", msg)

        urgency = self._determine_urgency(reasons)
        return RetrainDecision(
            should_retrain=len(reasons) > 0,
            reasons=reasons,
            urgency=urgency,
            details=details,
        )

    def _check_psi(self, drift_report: object) -> tuple[bool, str, dict]:
        """检查 PSI 漂移（兼容 DriftReport 对象）"""
        report = drift_report
        overall = getattr(report, "overall_psi", 0.0)
        drifted = getattr(report, "drifted_features", [])
        has_drift = getattr(report, "has_significant_drift", False)
        info = {
            "overall_psi": round(overall, 4),
            "drifted_count": len(drifted),
            "threshold": self.psi_threshold,
        }
        if has_drift or overall > self.psi_threshold:
            msg = (
                f"overall_psi={overall:.4f} > {self.psi_threshold}, "
                f"drifted={drifted}"
            )
            return True, msg, info
        return False, "", info

    def _check_icir(
        self, decay_snapshot: object,
    ) -> tuple[bool, str, dict]:
        """检查 ICIR 衰减（兼容 HalflifeSnapshot）"""
        snap = decay_snapshot
        status = getattr(snap, "status", "NORMAL")
        recent_icir = getattr(snap, "recent_icir", None)
        full_icir = getattr(snap, "full_icir", None)
        info = {
            "status": status,
            "recent_icir": (
                round(recent_icir, 4) if recent_icir is not None else None
            ),
            "full_icir": (
                round(full_icir, 4) if full_icir is not None else None
            ),
        }
        if status == "DEGRADED":
            msg = f"status=DEGRADED, recent_icir={recent_icir}"
            return True, msg, info
        # 额外：近期 ICIR < 全量 × ratio → alpha decay
        if (
            recent_icir is not None
            and full_icir is not None
            and full_icir > 0
            and recent_icir / full_icir < self.icir_degraded_ratio
        ):
            info["alpha_decay_ratio"] = round(
                recent_icir / full_icir, 4
            )
            msg = (
                f"alpha decay: recent/full ICIR = "
                f"{recent_icir:.4f}/{full_icir:.4f}"
            )
            return True, msg, info
        return False, "", info

    def _check_sr(
        self, sr_history: list[float],
    ) -> tuple[bool, str, dict]:
        """检查滚动 SR 连续为负"""
        if not sr_history:
            return False, "", {"negative_streak": 0}
        # 从末尾计数连续 < 0 的月份
        streak = 0
        for val in reversed(sr_history):
            if val < 0:
                streak += 1
            else:
                break
        info = {
            "negative_streak": streak,
            "threshold_months": self.sr_negative_months,
        }
        if streak >= self.sr_negative_months:
            msg = f"SR < 0 for {streak} consecutive months"
            return True, msg, info
        return False, "", info

    def _determine_urgency(
        self, reasons: list[RetrainReason],
    ) -> str:
        """根据触发原因判定紧急程度"""
        if not reasons:
            return "none"
        # 多重触发 → 高优先级
        if len(reasons) >= 2:
            return "high"
        reason = reasons[0]
        if reason == RetrainReason.PSI_DRIFT:
            return "high"
        if reason == RetrainReason.ICIR_DEGRADED:
            return "medium"
        if reason == RetrainReason.SR_NEGATIVE:
            return "medium"
        return "low"
