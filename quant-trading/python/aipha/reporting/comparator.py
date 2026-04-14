"""策略对比器 — 多策略绩效横向比较"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StrategyMetricsRow:
    name: str
    total_return: float = 0.0
    annual_return: float = 0.0
    volatility: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    calmar: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0


@dataclass
class ComparisonReport:
    strategies: list[StrategyMetricsRow]
    period: str = ""
    best_sharpe: str = ""
    best_return: str = ""
    lowest_drawdown: str = ""

    def summary(self) -> str:
        lines = ["=" * 70, "策略对比报告", "=" * 70, ""]
        header = f"{'策略':<20} {'年化':<10} {'波动':<10} {'Sharpe':<10} {'MDD':<10} {'胜率':<10}"
        lines.append(header)
        lines.append("-" * 70)
        for s in self.strategies:
            lines.append(
                f"{s.name:<20} {s.annual_return:>8.2%} {s.volatility:>8.2%} "
                f"{s.sharpe:>8.2f} {s.max_drawdown:>8.2%} {s.win_rate:>8.2%}"
            )
        lines.extend(["", f"最优Sharpe: {self.best_sharpe}", f"最高收益: {self.best_return}",
                       f"最低回撤: {self.lowest_drawdown}", "=" * 70])
        return "\n".join(lines)


class StrategyComparator:
    """多策略横向对比"""

    def __init__(self):
        self._strategies: list[StrategyMetricsRow] = []

    def add(self, row: StrategyMetricsRow):
        self._strategies.append(row)

    def add_from_dict(self, name: str, metrics: dict[str, Any]):
        self._strategies.append(StrategyMetricsRow(
            name=name,
            total_return=metrics.get("total_return", 0),
            annual_return=metrics.get("annual_return", 0),
            volatility=metrics.get("volatility", 0),
            sharpe=metrics.get("sharpe_ratio", metrics.get("sharpe", 0)),
            max_drawdown=metrics.get("max_drawdown", 0),
            calmar=metrics.get("calmar", 0),
            win_rate=metrics.get("win_rate", 0),
            profit_factor=metrics.get("profit_factor", 0),
            total_trades=metrics.get("total_trades", 0),
        ))

    def compare(self) -> ComparisonReport:
        if not self._strategies:
            return ComparisonReport([])
        best_sharpe = max(self._strategies, key=lambda s: s.sharpe).name
        best_return = max(self._strategies, key=lambda s: s.annual_return).name
        lowest_dd = min(self._strategies, key=lambda s: s.max_drawdown).name
        return ComparisonReport(
            strategies=self._strategies,
            best_sharpe=best_sharpe, best_return=best_return,
            lowest_drawdown=lowest_dd,
        )

    def rank_by(self, metric: str = "sharpe", ascending: bool = False) -> list[StrategyMetricsRow]:
        return sorted(self._strategies, key=lambda s: getattr(s, metric, 0), reverse=not ascending)
