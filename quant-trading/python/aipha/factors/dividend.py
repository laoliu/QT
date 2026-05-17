"""股息因子模块

提供正式的 Factor ABC 实现：
- DividendYieldFactor: 股息率因子（高股息优先）
- DividendGrowthFactor: 股息增长因子（连续增长年数 / 增长率）

数据依赖：
- FactorData.metadata["dv_ratio"] 或 ["dv_ttm"]（来自 daily_basic）
- FactorData.metadata["dv_history"]（多年股息率列表，用于增长因子）

设计说明：
- 因子方向统一：值越大 = 越优
- DividendYieldFactor 直接使用当期股息率
- DividendGrowthFactor 需要历史多期数据，支持两种计算：
  1. consecutive_growth_years: 连续增长年数
  2. cagr: 复合年增长率
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from aipha.factors.base import Factor, FactorData, FactorResult


@dataclass
class DividendYieldFactor(Factor):
    """股息率因子

    高股息率的股票通常提供更好的现金流回报。
    A 股中高股息策略长期有效，尤其在防御性行情中。

    Attributes:
        field: 使用的股息率字段（``dv_ratio`` 为当年已公告，``dv_ttm`` 为 TTM）
        min_yield: 最低股息率过滤（低于此值返回 NaN）
    """

    field: str = "dv_ratio"
    min_yield: float = 0.0

    @property
    def name(self) -> str:
        """因子名称，格式为 ``DIV_YIELD`` 或 ``DIV_YIELD_TTM``。"""
        suffix = "_TTM" if self.field == "dv_ttm" else ""
        return f"DIV_YIELD{suffix}"

    def compute(self, data: FactorData) -> FactorResult:
        """计算股息率因子值。

        优先从 metadata 中读取 ``dv_ratio`` / ``dv_ttm``。

        Args:
            data: 因子输入数据

        Returns:
            FactorResult，value 为股息率百分比；数据缺失时 value=NaN。
        """
        dv = data.metadata.get(self.field)

        if dv is None:
            return self._invalid(data, f"缺少 {self.field} 数据")

        try:
            dv_val = float(dv)
        except (TypeError, ValueError):
            return self._invalid(data, f"{self.field} 值无效: {dv}")

        if not math.isfinite(dv_val) or dv_val < 0:
            return self._invalid(data, f"{self.field} 异常: {dv_val}")

        if dv_val < self.min_yield:
            return self._invalid(
                data, f"股息率 {dv_val:.2f}% < 下限 {self.min_yield}%"
            )

        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=dv_val,
            metadata={"dv_ratio": dv_val},
        )

    def _invalid(self, data: FactorData, reason: str) -> FactorResult:
        """返回无效因子结果。"""
        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=float("nan"),
            metadata={"invalid": True, "reason": reason},
        )


@dataclass
class DividendGrowthFactor(Factor):
    """股息增长因子

    衡量公司派息的持续性和增长性。连续增长的股息是公司
    盈利稳定性和管理层信心的信号。

    计算方式根据 ``mode`` 属性：
    - ``consecutive``: 连续股息增长年数（越多越好）
    - ``cagr``: 股息率复合年增长率

    Attributes:
        mode: 计算模式（``consecutive`` 或 ``cagr``）
        min_years: 最少需要的历史年数
        history_field: metadata 中存放历史股息率列表的键名
    """

    mode: str = "consecutive"
    min_years: int = 3
    history_field: str = "dv_history"

    @property
    def name(self) -> str:
        """因子名称，格式为 ``DIV_GROWTH_CONSEC`` 或 ``DIV_GROWTH_CAGR``。"""
        suffix = "CONSEC" if self.mode == "consecutive" else "CAGR"
        return f"DIV_GROWTH_{suffix}"

    def compute(self, data: FactorData) -> FactorResult:
        """计算股息增长因子值。

        Args:
            data: 因子输入数据，需要 metadata 中包含 ``dv_history`` 列表
                  （按时间正序排列的年度股息率列表）

        Returns:
            FactorResult，value 为连续增长年数或 CAGR；数据不足时 value=NaN。
        """
        history = data.metadata.get(self.history_field)

        if not history or not isinstance(history, (list, tuple)):
            return self._invalid(data, f"缺少 {self.history_field} 数据")

        # 过滤无效值
        valid = []
        for v in history:
            try:
                fv = float(v)
                if math.isfinite(fv) and fv >= 0:
                    valid.append(fv)
            except (TypeError, ValueError):
                continue

        if len(valid) < self.min_years:
            return self._invalid(
                data,
                f"有效历史仅 {len(valid)} 年 < 最低要求 {self.min_years}",
            )

        if self.mode == "consecutive":
            value = self._consecutive_growth_years(valid)
        elif self.mode == "cagr":
            value = self._compute_cagr(valid)
        else:
            return self._invalid(data, f"未知计算模式: {self.mode}")

        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=value,
            metadata={
                "history_length": len(valid),
                "mode": self.mode,
            },
        )

    @staticmethod
    def _consecutive_growth_years(history: list[float]) -> float:
        """从最近一期向前回溯，计算连续增长年数。"""
        count = 0
        for i in range(len(history) - 1, 0, -1):
            if history[i] > history[i - 1] and history[i - 1] > 0:
                count += 1
            else:
                break
        return float(count)

    @staticmethod
    def _compute_cagr(history: list[float]) -> float:
        """计算股息率的复合年增长率。"""
        if len(history) < 2:
            return 0.0

        first = history[0]
        last = history[-1]

        if first <= 0 or last <= 0:
            return 0.0

        years = len(history) - 1
        cagr = (last / first) ** (1.0 / years) - 1.0
        return cagr

    def _invalid(self, data: FactorData, reason: str) -> FactorResult:
        """返回无效因子结果。"""
        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=float("nan"),
            metadata={"invalid": True, "reason": reason},
        )
