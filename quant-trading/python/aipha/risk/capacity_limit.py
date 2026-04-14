"""策略容量估算 — 基于流动性约束的最大可容纳资金"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class AssetCapacityInfo:
    symbol: str
    avg_daily_volume: float       # 日均成交额（元）
    avg_daily_shares: float       # 日均成交量（股）
    estimated_capacity: float     # 单票估算容量（元）
    participation_rate: float     # 实际参与率
    is_bottleneck: bool           # 是否为瓶颈


@dataclass
class CapacityEstimate:
    strategy_name: str
    total_capacity: float                    # 策略总容量（元）
    bottleneck_assets: list[AssetCapacityInfo]  # 瓶颈标的
    asset_details: list[AssetCapacityInfo]   # 所有标的详情
    utilization: float                       # 当前资金 / 总容量

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy_name,
            "total_capacity_yuan": round(self.total_capacity, 0),
            "total_capacity_wan": round(self.total_capacity / 1e4, 2),
            "bottleneck_count": len(self.bottleneck_assets),
            "bottlenecks": [a.symbol for a in self.bottleneck_assets],
            "utilization": round(self.utilization, 4),
        }


class CapacityLimit:
    """策略容量估算器。

    方法：
    1. 计算每只股票的日均成交额
    2. 假设最大参与率 (max_participation)，得到单票容量
    3. 按权重加权得到策略总容量
    4. 识别瓶颈标的（容量最小的 N 只）
    """

    def __init__(
        self,
        max_participation: float = 0.05,   # 最大参与率 5%
        slippage_bps_per_pct: float = 5.0, # 每 1% 参与率的冲击成本 (bps)
        bottleneck_percentile: float = 0.20,  # 最小20%视为瓶颈
    ) -> None:
        self._max_participation = max_participation
        self._slippage_bps_per_pct = slippage_bps_per_pct
        self._bottleneck_pct = bottleneck_percentile

    def estimate(
        self,
        strategy_name: str,
        weights: dict[str, float],
        volume_data: pd.DataFrame,
        current_capital: float = 0.0,
    ) -> CapacityEstimate:
        """估算策略容量。

        Args:
            strategy_name: 策略名称
            weights: {symbol: weight} 目标权重
            volume_data: DataFrame 列包含 'symbol', 'volume'(股数), 'amount'(金额)
            current_capital: 当前使用资金
        """
        asset_infos: list[AssetCapacityInfo] = []

        for symbol, w in weights.items():
            if abs(w) < 1e-8:
                continue
            sym_data = volume_data[volume_data["symbol"] == symbol]
            if sym_data.empty:
                logger.warning(f"No volume data for {symbol}")
                continue

            avg_amount = float(sym_data["amount"].mean()) if "amount" in sym_data.columns else 0.0
            avg_shares = float(sym_data["volume"].mean()) if "volume" in sym_data.columns else 0.0

            # 单票最大可交易金额 = 日均成交额 × 最大参与率
            max_trade_amount = avg_amount * self._max_participation
            # 策略中该票所需金额 = 总资金 × 权重
            # 反推：总资金上限 = max_trade_amount / |weight|
            if abs(w) > 1e-8:
                asset_capacity = max_trade_amount / abs(w)
            else:
                asset_capacity = float("inf")

            participation = (current_capital * abs(w) / avg_amount) if avg_amount > 0 else 0.0

            info = AssetCapacityInfo(
                symbol=symbol,
                avg_daily_volume=avg_amount,
                avg_daily_shares=avg_shares,
                estimated_capacity=asset_capacity,
                participation_rate=participation,
                is_bottleneck=False,
            )
            asset_infos.append(info)

        if not asset_infos:
            return CapacityEstimate(
                strategy_name=strategy_name,
                total_capacity=0.0,
                bottleneck_assets=[],
                asset_details=[],
                utilization=0.0,
            )

        # 策略总容量 = 所有标的容量的最小值（木桶原理）
        capacities = [a.estimated_capacity for a in asset_infos]
        total_capacity = min(capacities)

        # 识别瓶颈标的
        sorted_infos = sorted(asset_infos, key=lambda x: x.estimated_capacity)
        n_bottleneck = max(1, int(len(sorted_infos) * self._bottleneck_pct))
        for info in sorted_infos[:n_bottleneck]:
            info.is_bottleneck = True

        bottlenecks = [a for a in asset_infos if a.is_bottleneck]
        utilization = current_capital / total_capacity if total_capacity > 0 else 0.0

        return CapacityEstimate(
            strategy_name=strategy_name,
            total_capacity=total_capacity,
            bottleneck_assets=bottlenecks,
            asset_details=asset_infos,
            utilization=utilization,
        )

    def check_trade_feasibility(
        self,
        symbol: str,
        trade_amount: float,
        avg_daily_amount: float,
    ) -> tuple[bool, float]:
        """检查单笔交易是否可行。返回 (可行性, 预估冲击成本bps)。"""
        if avg_daily_amount <= 0:
            return False, 0.0

        participation = abs(trade_amount) / avg_daily_amount
        impact_bps = participation * 100 * self._slippage_bps_per_pct
        feasible = participation <= self._max_participation
        return feasible, impact_bps
