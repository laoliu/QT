"""ESG 因子 — 公司治理 / 争议事件 / ESG 综合"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

from aipha.factors.base import Factor, FactorData, FactorResult

logger = logging.getLogger(__name__)


@dataclass
class ESGConfig:
    independent_director_weight: float = 0.30
    audit_quality_weight: float = 0.25
    mgmt_ownership_weight: float = 0.20
    board_size_penalty_weight: float = 0.10
    dual_class_penalty: float = 0.15
    controversy_decay_days: int = 90
    controversy_severity_weights: dict[str, float] | None = None

    def __post_init__(self) -> None:
        if self.controversy_severity_weights is None:
            self.controversy_severity_weights = {
                "minor": 0.2, "moderate": 0.5, "major": 0.8, "critical": 1.0,
            }


class GovernanceFactor(Factor):
    """公司治理因子: 独董比例、审计质量、管理层持股、董事会规模"""

    def __init__(self, config: ESGConfig | None = None):
        self._config = config or ESGConfig()

    @property
    def name(self) -> str:
        return "GOVERNANCE"

    def compute(self, data: FactorData) -> FactorResult:
        fund = data.fundamentals or {}
        cfg = self._config
        score = 0.0
        total_weight = 0.0

        # 独董比例
        ind_dir = fund.get("independent_director_ratio")
        if ind_dir is not None:
            score += cfg.independent_director_weight * min(float(ind_dir) / 0.5, 1.0)
            total_weight += cfg.independent_director_weight

        # 审计质量 (四大 = 1.0)
        audit = fund.get("audit_quality")
        if audit is not None:
            score += cfg.audit_quality_weight * float(audit)
            total_weight += cfg.audit_quality_weight

        # 管理层持股比例
        mgmt = fund.get("mgmt_ownership_ratio")
        if mgmt is not None:
            score += cfg.mgmt_ownership_weight * min(float(mgmt) / 0.1, 1.0)
            total_weight += cfg.mgmt_ownership_weight

        # 董事会规模惩罚 (>15 人开始扣分)
        board_size = fund.get("board_size")
        if board_size is not None:
            penalty = max(0, (int(board_size) - 15) / 10)
            score -= cfg.board_size_penalty_weight * min(penalty, 1.0)
            total_weight += cfg.board_size_penalty_weight

        # 双重股权惩罚
        dual = fund.get("dual_class_shares", False)
        if dual:
            score -= cfg.dual_class_penalty
            total_weight += cfg.dual_class_penalty

        value = score / total_weight if total_weight > 0 else float("nan")
        return FactorResult(symbol=data.symbol, factor_name=self.name,
                            value=value, timestamp=data.timestamp)


class ControversyFactor(Factor):
    """争议事件因子: 按严重程度加权、时间衰减"""

    def __init__(self, config: ESGConfig | None = None):
        self._config = config or ESGConfig()

    @property
    def name(self) -> str:
        return "CONTROVERSY"

    def compute(self, data: FactorData) -> FactorResult:
        events = (data.fundamentals or {}).get("controversy_events", [])
        if not events:
            return FactorResult(symbol=data.symbol, factor_name=self.name,
                                value=0.0, timestamp=data.timestamp)
        cfg = self._config
        weights = cfg.controversy_severity_weights or {}
        total = 0.0
        for ev in events:
            sev = ev.get("severity", "minor")
            w = weights.get(sev, 0.2)
            days_ago = ev.get("days_ago", 0)
            decay = max(0, 1 - days_ago / cfg.controversy_decay_days)
            total += w * decay

        # 反转: 高争议 = 低分
        return FactorResult(symbol=data.symbol, factor_name=self.name,
                            value=-total, timestamp=data.timestamp)


class ESGCompositeFactor(Factor):
    """ESG 综合因子: 治理 + 争议"""

    def __init__(
        self,
        g_weight: float = 0.6,
        controversy_weight: float = 0.4,
        config: ESGConfig | None = None,
    ):
        self._g_weight = g_weight
        self._c_weight = controversy_weight
        self._config = config or ESGConfig()
        self._g_factor = GovernanceFactor(self._config)
        self._c_factor = ControversyFactor(self._config)

    @property
    def name(self) -> str:
        return "ESG_COMPOSITE"

    def compute(self, data: FactorData) -> FactorResult:
        g = self._g_factor.compute(data)
        c = self._c_factor.compute(data)
        g_val = g.value if np.isfinite(g.value) else 0.0
        c_val = c.value if np.isfinite(c.value) else 0.0
        value = self._g_weight * g_val + self._c_weight * c_val
        return FactorResult(symbol=data.symbol, factor_name=self.name,
                            value=value, timestamp=data.timestamp,
                            metadata={"governance": g_val, "controversy": c_val})
