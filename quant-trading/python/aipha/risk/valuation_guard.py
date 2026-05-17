"""跨资产估值过热风控层（Valuation Overheating Guard）

R28-A3 实施：基于 PE/PB 历史分位数的组合权重动态调整。

**四层风控机制**：
    - 预警(WARNING): 分位 > 80% → 标记黄灯，不调仓
    - 减仓(REDUCE):  分位 > 90% → 板块权重减半
    - 清仓(CLEAR):   分位 > 95% 且 PB > 3x中位 → 权重降至5%或清零
    - 回补(NORMAL):  回落至 < 70% → 恢复基准权重

**设计原则**：
    - 这是防御性风控，不是择时（不追求低估加仓的 alpha）
    - 可插拔：通过配置启用/禁用
    - 与 CrossAssetAllocator 解耦：guard 输出权重调整建议

理论依据：
    - 2021 量化踩踏：中证1000 PE 5年分位 > 95%
    - 2024 微盘股危机：CSI2000 PE > 80分位后流动性枯竭
    - Shiller CAPE 研究表明极端估值预示较差的未来收益
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Optional, Sequence

logger = logging.getLogger(__name__)


class ValuationAlertLevel(str, Enum):
    """估值预警级别"""
    NORMAL = "normal"
    WARNING = "warning"
    REDUCE = "reduce"
    CLEAR = "clear"


@dataclass(frozen=True)
class SectorValuationConfig:
    """单个板块/资产类别的估值监控配置"""
    sector_id: str
    metric: str = "pe_ttm"
    lookback_days: int = 1260
    warn_percentile: float = 0.80
    reduce_percentile: float = 0.90
    clear_percentile: float = 0.95
    clear_pb_multiplier: float = 3.0
    recover_percentile: float = 0.70
    reduce_weight_scale: float = 0.50
    clear_weight_floor: float = 0.05
    smooth_days: int = 5


@dataclass(frozen=True)
class ValuationAlert:
    """估值预警信号"""
    sector_id: str
    level: ValuationAlertLevel
    percentile: float
    metric_value: float
    pb_value: Optional[float]
    base_weight: float
    adjusted_weight: float
    message: str
    timestamp: datetime = field(default_factory=datetime.now)


class _SectorHistory:
    """单个板块的估值历史追踪.

    维护一个滚动窗口的估值指标值，计算当前值在历史中的百分位。
    """

    def __init__(self, config: SectorValuationConfig) -> None:
        self.config = config
        maxlen = config.lookback_days
        self._values: deque[float] = deque(maxlen=maxlen)
        self._pb_values: deque[float] = deque(maxlen=maxlen)
        self._current_level = ValuationAlertLevel.NORMAL
        self._scale_history: list[float] = []

    def update(
        self, metric_value: float, pb_value: float | None = None,
    ) -> None:
        """追加一天的估值数据."""
        self._values.append(metric_value)
        if pb_value is not None:
            self._pb_values.append(pb_value)

    @property
    def has_enough_data(self) -> bool:
        """至少需要 60 天数据才能计算百分位."""
        return len(self._values) >= 60

    @property
    def current_percentile(self) -> float | None:
        """当前值在历史窗口中的百分位 (0~1)."""
        if not self.has_enough_data:
            return None
        current = self._values[-1]
        window = list(self._values)
        return sum(1 for v in window if v <= current) / len(window)

    @property
    def pb_median(self) -> float | None:
        """PB 历史中位数."""
        if len(self._pb_values) < 60:
            return None
        sorted_pb = sorted(self._pb_values)
        mid = len(sorted_pb) // 2
        if len(sorted_pb) % 2 == 0:
            return (sorted_pb[mid - 1] + sorted_pb[mid]) / 2
        return sorted_pb[mid]

    def determine_level(self) -> ValuationAlertLevel:
        """根据当前百分位判定预警级别."""
        pct = self.current_percentile
        if pct is None:
            return ValuationAlertLevel.NORMAL

        cfg = self.config

        # 清仓条件：分位 > 95% 且 PB > 3x中位
        if pct >= cfg.clear_percentile:
            pb_med = self.pb_median
            current_pb = self._pb_values[-1] if self._pb_values else None
            if (
                current_pb is not None
                and pb_med is not None
                and current_pb > cfg.clear_pb_multiplier * pb_med
            ):
                self._current_level = ValuationAlertLevel.CLEAR
                return ValuationAlertLevel.CLEAR
            # PB 条件不满足时降为 REDUCE
            self._current_level = ValuationAlertLevel.REDUCE
            return ValuationAlertLevel.REDUCE

        # 减仓条件
        if pct >= cfg.reduce_percentile:
            self._current_level = ValuationAlertLevel.REDUCE
            return ValuationAlertLevel.REDUCE

        # 预警条件
        if pct >= cfg.warn_percentile:
            self._current_level = ValuationAlertLevel.WARNING
            return ValuationAlertLevel.WARNING

        # 回补逻辑：只有从高位回落才恢复
        if self._current_level != ValuationAlertLevel.NORMAL:
            if pct < cfg.recover_percentile:
                self._current_level = ValuationAlertLevel.NORMAL
            return self._current_level

        return ValuationAlertLevel.NORMAL

    def weight_scale(self) -> float:
        """当前级别对应的权重缩放因子."""
        level = self.determine_level()
        cfg = self.config
        if level == ValuationAlertLevel.CLEAR:
            return cfg.clear_weight_floor
        if level == ValuationAlertLevel.REDUCE:
            return cfg.reduce_weight_scale
        return 1.0

    def reset(self) -> None:
        """重置全部历史."""
        self._values.clear()
        self._pb_values.clear()
        self._current_level = ValuationAlertLevel.NORMAL
        self._scale_history.clear()


class ValuationGuard:
    """跨资产估值过热风控层（主类）.

    监控多个板块/资产类别的估值水位，当估值达到危险区间时
    自动调降对应板块的组合权重，释放出的权重按比例分配给
    仍处于安全区间的板块。

    Usage::

        guard = ValuationGuard([
            SectorValuationConfig(sector_id="csi1000", metric="pe_ttm"),
            SectorValuationConfig(sector_id="etf", metric="pe_ttm"),
        ])
        # 每日更新估值
        guard.update("csi1000", pe_value=35.2, pb_value=2.1)
        guard.update("etf", pe_value=12.8, pb_value=1.5)
        # 调整权重
        adj_w, alerts = guard.adjust_weights({"cb": 0.4, "csi1000": 0.3, "etf": 0.3})
    """

    def __init__(
        self,
        configs: Sequence[SectorValuationConfig],
        enabled: bool = True,
    ) -> None:
        self._enabled = enabled
        self._sectors: dict[str, _SectorHistory] = {}
        for cfg in configs:
            self._sectors[cfg.sector_id] = _SectorHistory(cfg)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def sector_ids(self) -> list[str]:
        return list(self._sectors.keys())

    def update(
        self,
        sector_id: str,
        metric_value: float,
        pb_value: float | None = None,
    ) -> ValuationAlert | None:
        """更新某板块的当日估值数据并返回预警信号。

        Args:
            sector_id: 板块标识
            metric_value: 估值指标值（如 PE-TTM）
            pb_value: PB 值（清仓判定用，可选）

        Returns:
            预警信号（如有），否则 None
        """
        if not self._enabled:
            return None
        hist = self._sectors.get(sector_id)
        if hist is None:
            logger.warning("ValuationGuard: unknown sector %s", sector_id)
            return None
        hist.update(metric_value, pb_value)
        level = hist.determine_level()
        if level == ValuationAlertLevel.NORMAL:
            return None
        # percentile 为 None 表示数据不足，使用 50% 中性值（避免 0% 虚假低估信号）
        pct = hist.current_percentile if hist.current_percentile is not None else 0.5
        return ValuationAlert(
            sector_id=sector_id,
            level=level,
            percentile=pct,
            metric_value=metric_value,
            pb_value=pb_value,
            base_weight=0.0,
            adjusted_weight=0.0,
            message=f"{sector_id} {level.value}: pct={pct:.1%}",
        )

    def get_sector_percentile(self, sector_id: str) -> float | None:
        """获取板块当前百分位."""
        hist = self._sectors.get(sector_id)
        if hist is None:
            return None
        return hist.current_percentile

    def get_sector_level(self, sector_id: str) -> ValuationAlertLevel:
        """获取板块当前预警级别."""
        hist = self._sectors.get(sector_id)
        if hist is None:
            return ValuationAlertLevel.NORMAL
        return hist.determine_level()

    def get_all_alerts(self) -> list[ValuationAlert]:
        """获取所有非 NORMAL 板块的预警信号."""
        alerts: list[ValuationAlert] = []
        for sid, hist in self._sectors.items():
            level = hist.determine_level()
            if level != ValuationAlertLevel.NORMAL:
                # percentile 为 None 表示数据不足，使用 50% 中性值
                pct = hist.current_percentile if hist.current_percentile is not None else 0.5
                val = hist._values[-1] if hist._values else 0.0
                pb = hist._pb_values[-1] if hist._pb_values else None
                alerts.append(ValuationAlert(
                    sector_id=sid, level=level, percentile=pct,
                    metric_value=val, pb_value=pb,
                    base_weight=0.0, adjusted_weight=0.0,
                    message=f"{sid} {level.value}: pct={pct:.1%}",
                ))
        return alerts

    def reset(self) -> None:
        """重置所有板块历史."""
        for hist in self._sectors.values():
            hist.reset()

    def adjust_weights(
        self,
        base_weights: dict[str, float],
    ) -> tuple[dict[str, float], list[ValuationAlert]]:
        """根据估值水位调整组合权重。

        被监控板块若触发 REDUCE/CLEAR，权重按比例缩减；
        释放的权重按比例分配给仍处 NORMAL 的板块。

        Args:
            base_weights: 基准权重 {"cb": 0.4, "csi1000": 0.3, ...}

        Returns:
            (调整后权重, 预警列表)
        """
        if not self._enabled:
            return dict(base_weights), []

        alerts: list[ValuationAlert] = []
        adjusted = dict(base_weights)
        released = 0.0

        # 第一轮：缩减过热板块
        for sid, hist in self._sectors.items():
            if sid not in adjusted:
                continue
            scale = hist.weight_scale()
            if scale >= 1.0:
                continue
            old_w = adjusted[sid]
            new_w = old_w * scale
            # 确保不低于 clear_weight_floor
            floor = hist.config.clear_weight_floor
            new_w = max(new_w, floor) if old_w > floor else new_w
            released += old_w - new_w
            adjusted[sid] = new_w

            # percentile 为 None 表示数据不足，使用 50% 中性值
            pct = hist.current_percentile if hist.current_percentile is not None else 0.5
            level = hist.determine_level()
            val = hist._values[-1] if hist._values else 0.0
            pb = hist._pb_values[-1] if hist._pb_values else None
            alerts.append(ValuationAlert(
                sector_id=sid, level=level, percentile=pct,
                metric_value=val, pb_value=pb,
                base_weight=old_w, adjusted_weight=new_w,
                message=(
                    f"{sid}: {level.value} pct={pct:.1%}, "
                    f"weight {old_w:.1%} → {new_w:.1%}"
                ),
            ))
            logger.info(
                "ValuationGuard: %s %s (pct=%.1f%%), "
                "weight %.1f%% → %.1f%%",
                sid, level.value, pct * 100,
                old_w * 100, new_w * 100,
            )

        # 第二轮：释放权重按比例分配给安全板块
        if released > 1e-8:
            safe_total = sum(
                adjusted[k] for k in adjusted
                if k not in self._sectors
                or self._sectors[k].determine_level()
                == ValuationAlertLevel.NORMAL
            )
            if safe_total > 1e-8:
                for k in adjusted:
                    in_guard = k in self._sectors
                    is_safe = (
                        not in_guard
                        or self._sectors[k].determine_level()
                        == ValuationAlertLevel.NORMAL
                    )
                    if is_safe:
                        share = adjusted[k] / safe_total
                        adjusted[k] += released * share
            else:
                # 所有板块都过热 → 剩余权重归现金
                logger.warning(
                    "ValuationGuard: all sectors overheated, "
                    "%.1f%% released to cash",
                    released * 100,
                )

        return adjusted, alerts

    def summary(self) -> dict[str, Any]:
        """汇总所有板块的当前状态."""
        result: dict[str, Any] = {"enabled": self._enabled, "sectors": {}}
        for sid, hist in self._sectors.items():
            result["sectors"][sid] = {
                "percentile": hist.current_percentile,
                "level": hist.determine_level().value,
                "data_points": len(hist._values),
                "has_enough_data": hist.has_enough_data,
            }
        return result
