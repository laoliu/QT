"""ESG 因子实现 — G 因子（治理）+ 争议事件空头信号。

基于知识库 R 章节实现 ESG 因子体系：
- G 因子（治理质量）是最显著的 ESG 维度 (IC 3-6%)
- ESG 争议空头（负面事件）IC 3-7%
- 2024.04 沪深北三所《可持续发展报告指引》转折点

理论基础：
- 知识库 R1: 中国 ESG 政策演进
- 知识库 R2: G 因子为最有效维度
- Pedersen, Fitzgibbons, Pomorski (2021) — ESG Investing

与现有架构关系：
- 继承 Factor 基类，可注册到 FactorRegistry
- 计算使用 PIT 对齐的财务数据
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from aipha.factors.base import Factor, FactorData, FactorResult

logger = logging.getLogger(__name__)


@dataclass
class ESGConfig:
    """ESG 因子配置"""

    # G 因子权重
    independent_director_weight: float = 0.30
    audit_quality_weight: float = 0.25
    mgmt_ownership_weight: float = 0.20
    board_size_penalty_weight: float = 0.10
    dual_class_penalty: float = 0.15
    # 争议信号
    controversy_decay_days: int = 90
    controversy_severity_weights: dict = None

    def __post_init__(self):
        if self.controversy_severity_weights is None:
            self.controversy_severity_weights = {
                "critical": 1.0,
                "major": 0.6,
                "moderate": 0.3,
                "minor": 0.1,
            }


class GovernanceFactor(Factor):
    """G 因子（治理质量）

    综合评分：独董比例 + 审计质量 + 管理层持股 - 董事会过大惩罚

    知识库 R2: G 因子 IC 3-6%，最显著 ESG 维度
    """

    def __init__(self, config: Optional[ESGConfig] = None) -> None:
        self._config = config or ESGConfig()

    @property
    def name(self) -> str:
        return "governance_quality"

    def compute(self, data: FactorData) -> FactorResult:
        """计算治理质量因子

        使用 fundamentals 字典中的治理指标：
        - independent_ratio: 独董比例 (0-1)
        - audit_big4: 是否四大审计 (0/1)
        - mgmt_ownership: 管理层持股比例 (0-1)
        - board_size: 董事会人数
        """
        f = data.fundamentals or {}
        cfg = self._config

        # 独董比例 (越高越好)
        ind_ratio = float(f.get("independent_ratio", 0.33))
        ind_score = min(ind_ratio / 0.5, 1.0)  # 50% 满分

        # 审计质量 (四大 = 1.0)
        audit = float(f.get("audit_big4", 0.0))

        # 管理层持股 (适度最佳，过高过低扣分)
        mgmt = float(f.get("mgmt_ownership", 0.0))
        mgmt_score = 1.0 - abs(mgmt - 0.15) / 0.15 if mgmt <= 0.30 else 0.5

        # 董事会规模惩罚 (7-11 合理，过大扣分)
        board = int(f.get("board_size", 9))
        board_penalty = max(0, (board - 11)) * 0.1

        score = (
            cfg.independent_director_weight * ind_score
            + cfg.audit_quality_weight * audit
            + cfg.mgmt_ownership_weight * mgmt_score
            - cfg.board_size_penalty_weight * board_penalty
        )

        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=float(np.clip(score, -1, 1)),
        )


class ControversyFactor(Factor):
    """ESG 争议空头信号

    基于负面争议事件的空头信号，IC 3-7%。
    """

    def __init__(self, config: Optional[ESGConfig] = None) -> None:
        self._config = config or ESGConfig()

    @property
    def name(self) -> str:
        return "esg_controversy"

    def compute(self, data: FactorData) -> FactorResult:
        """计算争议空头信号

        使用 metadata 中的争议事件列表：
        - controversies: [{"severity": "major", "days_ago": 30}, ...]
        分越低越好（空头信号）
        """
        events = data.metadata.get("controversies", [])
        cfg = self._config
        score = 0.0
        for evt in events:
            severity = evt.get("severity", "minor")
            days_ago = evt.get("days_ago", 999)
            if days_ago > cfg.controversy_decay_days:
                continue
            weight = cfg.controversy_severity_weights.get(severity, 0.1)
            decay = 1.0 - (days_ago / cfg.controversy_decay_days)
            score += weight * decay
        # 反转：争议多 → 负值（空头信号）
        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=float(-score),
        )


class ESGCompositeFactor(Factor):
    """ESG 综合因子（G + 争议）"""

    def __init__(
        self,
        g_weight: float = 0.6,
        controversy_weight: float = 0.4,
        config: Optional[ESGConfig] = None,
    ) -> None:
        self._g_weight = g_weight
        self._c_weight = controversy_weight
        self._config = config or ESGConfig()
        self._g_factor = GovernanceFactor(self._config)
        self._c_factor = ControversyFactor(self._config)

    @property
    def name(self) -> str:
        return "esg_composite"

    def compute(self, data: FactorData) -> FactorResult:
        """计算 ESG 综合因子 = G_weight × G + C_weight × Controversy"""
        g_result = self._g_factor.compute(data)
        c_result = self._c_factor.compute(data)
        composite = (
            self._g_weight * g_result.value
            + self._c_weight * c_result.value
        )
        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=float(composite),
            metadata={
                "governance": g_result.value,
                "controversy": c_result.value,
            },
        )
