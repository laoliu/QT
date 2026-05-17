"""
策略容量约束

保守估算策略的可行容量，识别容量瓶颈标的，
当总容量低于最低要求时标注为容量受限并发出阻塞警告。

容量估算公式（保守）：
    单标的容量（元） = 日均成交额（元） × max_participation_rate
    策略总容量     = Σ(单标的容量) 的加权/调和估计

参见：copilot-instructions.md §3.2 原则 D、R52 P15-A5。

使用示例：
    limit = CapacityLimit(max_participation=0.10, min_capacity_cny=500_000)
    estimate = limit.estimate_capacity(
        holdings={"000001.SZ": 50000.0},         # 持仓市值（元）
        avg_volumes={"000001.SZ": 1_000_000.0},  # 日均成交额（元）
    )
    result = limit.check(holdings, avg_volumes)
    bottlenecks = limit.get_bottleneck_assets(n=3)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AssetCapacityInfo:
    """单标的容量信息"""

    symbol: str
    position_value_cny: float     # 当前持仓市值（元）
    avg_daily_volume_cny: float   # 日均成交额（元）
    available_capacity_cny: float # 该标的可容纳的最大持仓（元）
    participation_rate: float     # 当前持仓占日成交额的比例
    is_bottleneck: bool = False

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "position_value_cny": round(self.position_value_cny, 2),
            "avg_daily_volume_cny": round(self.avg_daily_volume_cny, 2),
            "available_capacity_cny": round(self.available_capacity_cny, 2),
            "participation_rate": round(self.participation_rate, 4),
            "is_bottleneck": self.is_bottleneck,
        }


@dataclass
class CapacityEstimate:
    """策略容量估算结果"""

    total_capacity_cny: float         # 策略总可容纳规模（元）
    is_capacity_limited: bool         # 是否低于最低容量要求
    min_capacity_cny: float           # 配置的最低容量要求
    bottleneck_assets: list[AssetCapacityInfo]  # 容量最受限的标的
    asset_details: list[AssetCapacityInfo]      # 所有标的详情

    def to_dict(self) -> dict:
        return {
            "total_capacity_cny": round(self.total_capacity_cny, 2),
            "is_capacity_limited": self.is_capacity_limited,
            "min_capacity_cny": self.min_capacity_cny,
            "bottleneck_assets": [a.to_dict() for a in self.bottleneck_assets],
        }


@dataclass(frozen=True)
class CapacityCheckResult:
    """容量约束检查结果（用于质量门禁）"""

    passed: bool
    total_capacity_cny: float
    min_capacity_cny: float
    is_capacity_limited: bool
    bottleneck_symbols: list[str] = field(default_factory=list)
    message: str = ""


class CapacityLimit:
    """策略容量约束"""

    def __init__(
        self,
        max_participation: float = 0.10,
        min_capacity_cny: float = 500_000.0,
    ) -> None:
        if not (0 < max_participation <= 1.0):
            raise ValueError(f"max_participation must be in (0, 1], got {max_participation}")
        if min_capacity_cny < 0:
            raise ValueError(f"min_capacity_cny must be >= 0, got {min_capacity_cny}")
        self.max_participation = max_participation
        self.min_capacity_cny = min_capacity_cny

    def estimate_capacity(
        self,
        holdings: dict[str, float],
        avg_volumes: dict[str, float],
    ) -> CapacityEstimate:
        """估算策略容量

        Args:
            holdings: {symbol: position_value_cny} 当前持仓市值（元）
            avg_volumes: {symbol: daily_avg_volume_cny} 日均成交额（元）
        """
        if not holdings:
            return CapacityEstimate(
                total_capacity_cny=0.0,
                is_capacity_limited=True,
                min_capacity_cny=self.min_capacity_cny,
                bottleneck_assets=[],
                asset_details=[],
            )

        details: list[AssetCapacityInfo] = []
        for symbol, pos_value in holdings.items():
            avg_vol = avg_volumes.get(symbol, 0.0)
            available = avg_vol * self.max_participation
            participation = pos_value / avg_vol if avg_vol > 0 else float("inf")
            details.append(AssetCapacityInfo(
                symbol=symbol,
                position_value_cny=pos_value,
                avg_daily_volume_cny=avg_vol,
                available_capacity_cny=available,
                participation_rate=participation,
            ))

        # 总容量 = 各标的可容纳容量之和（等权持仓假设可叠加）
        total_capacity = sum(d.available_capacity_cny for d in details)
        is_limited = total_capacity < self.min_capacity_cny

        # 标记瓶颈（容量最小的 top-3）
        sorted_by_cap = sorted(details, key=lambda x: x.available_capacity_cny)
        bottleneck_n = min(3, len(sorted_by_cap))
        bottleneck_set = {a.symbol for a in sorted_by_cap[:bottleneck_n]}
        for d in details:
            d.is_bottleneck = d.symbol in bottleneck_set

        return CapacityEstimate(
            total_capacity_cny=total_capacity,
            is_capacity_limited=is_limited,
            min_capacity_cny=self.min_capacity_cny,
            bottleneck_assets=sorted_by_cap[:bottleneck_n],
            asset_details=details,
        )

    def check(
        self,
        holdings: dict[str, float],
        avg_volumes: dict[str, float],
    ) -> CapacityCheckResult:
        """返回可用于质量门禁的检查结果"""
        est = self.estimate_capacity(holdings, avg_volumes)
        bottleneck_symbols = [a.symbol for a in est.bottleneck_assets]
        msg = ""
        if est.is_capacity_limited:
            msg = (
                f"Strategy capacity {est.total_capacity_cny:,.0f} CNY "
                f"< minimum {self.min_capacity_cny:,.0f} CNY. "
                f"Bottlenecks: {bottleneck_symbols}"
            )
        return CapacityCheckResult(
            passed=not est.is_capacity_limited,
            total_capacity_cny=est.total_capacity_cny,
            min_capacity_cny=self.min_capacity_cny,
            is_capacity_limited=est.is_capacity_limited,
            bottleneck_symbols=bottleneck_symbols,
            message=msg,
        )

    def get_bottleneck_assets(
        self,
        holdings: dict[str, float],
        avg_volumes: dict[str, float],
        n: int = 3,
    ) -> list[AssetCapacityInfo]:
        """返回容量瓶颈 top-n 标的"""
        est = self.estimate_capacity(holdings, avg_volumes)
        sorted_all = sorted(est.asset_details, key=lambda x: x.available_capacity_cny)
        return sorted_all[:n]
