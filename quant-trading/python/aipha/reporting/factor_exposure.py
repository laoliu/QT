"""因子暴露分析器

为回测持仓提供组合级别的风格因子暴露(exposure)时间序列分析。
可追踪每日持仓在 size / value / momentum / quality 等因子上的暴露度，
输出汇总统计供回测报告使用。

暴露计算公式：exposure_f = Σ_i (w_i × score_{i,f})
  其中 w_i = position_value_i / nav 为持仓权重，
  score_{i,f} 为标的 i 在因子 f 上的原始/标准化得分。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Sequence

import numpy as np


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class FactorExposureSnapshot:
    """单日组合因子暴露快照。"""

    trade_date: date
    exposures: dict[str, float]  # factor_name → exposure value
    num_holdings: int  # 当日持仓标的数


@dataclass(frozen=True, slots=True)
class FactorExposureSummary:
    """单因子在整个回测周期的暴露统计。"""

    factor_name: str
    mean_exposure: float
    std_exposure: float
    min_exposure: float
    max_exposure: float
    latest_exposure: float

    def to_dict(self) -> dict:
        """将单因子暴露统计序列化为字典，值保留 6 位小数。"""
        return {
            "factor": self.factor_name,
            "mean": round(self.mean_exposure, 6),
            "std": round(self.std_exposure, 6),
            "min": round(self.min_exposure, 6),
            "max": round(self.max_exposure, 6),
            "latest": round(self.latest_exposure, 6),
        }


# ---------------------------------------------------------------------------
# 分析器
# ---------------------------------------------------------------------------

class FactorExposureAnalyzer:
    """组合因子暴露分析器。

    用法::

        analyzer = FactorExposureAnalyzer(["size", "value", "momentum"])

        # 每个交易日调用
        analyzer.record(
            trade_date=date(2025, 1, 2),
            holdings_weights={"000001.SZ": 0.05, "600036.SH": 0.08, ...},
            factor_scores={
                "size":     {"000001.SZ": -0.3, "600036.SH": 0.5, ...},
                "value":    {"000001.SZ": 0.8,  "600036.SH": -0.2, ...},
                "momentum": {"000001.SZ": 0.1,  "600036.SH": 0.6, ...},
            },
        )

        # 最终获取汇总
        summary = analyzer.summary()
        report_dict = analyzer.to_dict()
    """

    def __init__(self, factor_names: Sequence[str]) -> None:
        if not factor_names:
            raise ValueError("factor_names must not be empty")
        self._factor_names: list[str] = list(factor_names)
        self._snapshots: list[FactorExposureSnapshot] = []

    # -- 属性 --

    @property
    def factor_names(self) -> list[str]:
        """返回已注册的因子名称列表副本。"""
        return list(self._factor_names)

    @property
    def snapshots(self) -> list[FactorExposureSnapshot]:
        """返回已记录的每日因子暴露快照列表副本。"""
        return list(self._snapshots)

    @property
    def num_snapshots(self) -> int:
        """返回已记录的快照总数。"""
        return len(self._snapshots)

    # -- 核心方法 --

    def record(
        self,
        trade_date: date,
        holdings_weights: dict[str, float],
        factor_scores: dict[str, dict[str, float]],
    ) -> FactorExposureSnapshot:
        """记录一日组合因子暴露。

        Parameters
        ----------
        trade_date : date
            交易日期
        holdings_weights : dict[str, float]
            持仓权重 {symbol: weight}，weight = value / nav
        factor_scores : dict[str, dict[str, float]]
            因子分数 {factor_name: {symbol: score}}

        Returns
        -------
        FactorExposureSnapshot
        """
        exposures: dict[str, float] = {}
        for factor_name in self._factor_names:
            scores = factor_scores.get(factor_name, {})
            exp = sum(
                weight * scores.get(symbol, 0.0)
                for symbol, weight in holdings_weights.items()
            )
            exposures[factor_name] = exp
        snapshot = FactorExposureSnapshot(
            trade_date=trade_date,
            exposures=exposures,
            num_holdings=len(holdings_weights),
        )
        self._snapshots.append(snapshot)
        return snapshot

    def record_from_position_values(
        self,
        trade_date: date,
        position_values: dict[str, float],
        nav: float,
        factor_scores: dict[str, dict[str, float]],
    ) -> FactorExposureSnapshot:
        """从持仓市值 + NAV 自动计算权重后记录暴露。

        Parameters
        ----------
        trade_date : date
        position_values : dict[str, float]
            {symbol: market_value}
        nav : float
            组合净值（总资产）
        factor_scores : dict[str, dict[str, float]]
            {factor_name: {symbol: score}}
        """
        if nav <= 0:
            weights: dict[str, float] = {}
        else:
            weights = {s: v / nav for s, v in position_values.items() if v > 0}
        return self.record(trade_date, weights, factor_scores)

    def summary(self) -> list[FactorExposureSummary]:
        """汇总因子暴露统计。"""
        if not self._snapshots:
            return []
        results: list[FactorExposureSummary] = []
        for factor_name in self._factor_names:
            values = [s.exposures.get(factor_name, 0.0) for s in self._snapshots]
            arr = np.array(values, dtype=np.float64)
            results.append(
                FactorExposureSummary(
                    factor_name=factor_name,
                    mean_exposure=float(np.mean(arr)),
                    std_exposure=float(np.std(arr, ddof=0)),
                    min_exposure=float(np.min(arr)),
                    max_exposure=float(np.max(arr)),
                    latest_exposure=values[-1],
                )
            )
        return results

    def to_dict(self) -> dict:
        """导出完整结果供报告使用。"""
        return {
            "factor_names": self._factor_names,
            "num_snapshots": self.num_snapshots,
            "snapshots": [
                {
                    "date": s.trade_date.isoformat(),
                    "num_holdings": s.num_holdings,
                    **s.exposures,
                }
                for s in self._snapshots
            ],
            "summary": [s.to_dict() for s in self.summary()],
        }

    def format_summary(self) -> str:
        """返回可读的因子暴露汇总文本。"""
        summaries = self.summary()
        if not summaries:
            return "Factor Exposure: No data recorded."
        lines = [
            "Factor Exposure Summary",
            "=" * 60,
            f"{'Factor':<14} {'Mean':>10} {'Std':>10} {'Min':>10} {'Max':>10} {'Latest':>10}",
            "-" * 60,
        ]
        for s in summaries:
            lines.append(
                f"{s.factor_name:<14} {s.mean_exposure:>10.4f} "
                f"{s.std_exposure:>10.4f} {s.min_exposure:>10.4f} "
                f"{s.max_exposure:>10.4f} {s.latest_exposure:>10.4f}"
            )
        lines.append("=" * 60)
        return "\n".join(lines)
