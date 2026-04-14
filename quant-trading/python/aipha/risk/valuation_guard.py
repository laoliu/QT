"""估值防护 — 板块估值分位监控 & 权重自动调整"""
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
    NORMAL = "normal"
    WARNING = "warning"
    REDUCE = "reduce"
    CLEAR = "clear"


@dataclass(frozen=True)
class SectorValuationConfig:
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
    sector_id: str
    level: ValuationAlertLevel
    percentile: float
    metric_value: float
    pb_value: float | None
    base_weight: float
    adjusted_weight: float
    message: str
    timestamp: datetime = field(default_factory=datetime.now)


class _SectorHistory:
    def __init__(self, config: SectorValuationConfig):
        self.config = config
        self._values: deque[float] = deque(maxlen=config.lookback_days)
        self._pb_values: deque[float] = deque(maxlen=config.lookback_days)
        self._level = ValuationAlertLevel.NORMAL

    def update(self, metric_value: float, pb_value: float | None = None) -> None:
        self._values.append(metric_value)
        if pb_value is not None:
            self._pb_values.append(pb_value)

    @property
    def has_enough_data(self) -> bool:
        return len(self._values) >= self.config.smooth_days * 2

    @property
    def current_percentile(self) -> float:
        if not self._values:
            return 0.5
        vals = sorted(self._values)
        current = self._values[-1]
        rank = sum(1 for v in vals if v <= current) / len(vals)
        return rank

    @property
    def pb_median(self) -> float | None:
        if not self._pb_values:
            return None
        sorted_pb = sorted(self._pb_values)
        mid = len(sorted_pb) // 2
        return sorted_pb[mid]

    def determine_level(self) -> ValuationAlertLevel:
        pct = self.current_percentile
        cfg = self.config
        if pct >= cfg.clear_percentile:
            self._level = ValuationAlertLevel.CLEAR
        elif pct >= cfg.reduce_percentile:
            self._level = ValuationAlertLevel.REDUCE
        elif pct >= cfg.warn_percentile:
            self._level = ValuationAlertLevel.WARNING
        elif pct <= cfg.recover_percentile and self._level in (
            ValuationAlertLevel.WARNING, ValuationAlertLevel.REDUCE, ValuationAlertLevel.CLEAR,
        ):
            self._level = ValuationAlertLevel.NORMAL
        return self._level

    def weight_scale(self) -> float:
        if self._level == ValuationAlertLevel.CLEAR:
            return self.config.clear_weight_floor
        elif self._level == ValuationAlertLevel.REDUCE:
            return self.config.reduce_weight_scale
        return 1.0

    def reset(self) -> None:
        self._values.clear()
        self._pb_values.clear()
        self._level = ValuationAlertLevel.NORMAL


class ValuationGuard:
    """估值防护: 监控多个板块估值分位, 超阈值自动降权"""

    def __init__(
        self,
        configs: Sequence[SectorValuationConfig],
        enabled: bool = True,
    ):
        self._enabled = enabled
        self._histories: dict[str, _SectorHistory] = {}
        for cfg in configs:
            self._histories[cfg.sector_id] = _SectorHistory(cfg)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def sector_ids(self) -> list[str]:
        return list(self._histories.keys())

    def update(
        self, sector_id: str, metric_value: float, pb_value: float | None = None,
    ) -> ValuationAlert | None:
        hist = self._histories.get(sector_id)
        if hist is None:
            return None
        hist.update(metric_value, pb_value)
        if not hist.has_enough_data:
            return None
        level = hist.determine_level()
        if level == ValuationAlertLevel.NORMAL:
            return None
        return ValuationAlert(
            sector_id=sector_id, level=level, percentile=hist.current_percentile,
            metric_value=metric_value, pb_value=pb_value,
            base_weight=1.0, adjusted_weight=hist.weight_scale(),
            message=f"{sector_id} 估值分位 {hist.current_percentile:.0%} → {level.value}",
        )

    def get_sector_percentile(self, sector_id: str) -> float | None:
        h = self._histories.get(sector_id)
        return h.current_percentile if h and h.has_enough_data else None

    def get_sector_level(self, sector_id: str) -> ValuationAlertLevel | None:
        h = self._histories.get(sector_id)
        return h._level if h else None

    def get_all_alerts(self) -> list[ValuationAlert]:
        alerts = []
        for sid, h in self._histories.items():
            if h.has_enough_data and h._level != ValuationAlertLevel.NORMAL:
                alerts.append(ValuationAlert(
                    sector_id=sid, level=h._level, percentile=h.current_percentile,
                    metric_value=h._values[-1] if h._values else 0,
                    pb_value=h._pb_values[-1] if h._pb_values else None,
                    base_weight=1.0, adjusted_weight=h.weight_scale(),
                    message=f"{sid}: {h._level.value} @ {h.current_percentile:.0%}",
                ))
        return alerts

    def adjust_weights(
        self, base_weights: dict[str, float],
    ) -> tuple[dict[str, float], list[ValuationAlert]]:
        """批量调整权重, 返回 (调整后权重, 告警列表)"""
        if not self._enabled:
            return base_weights, []
        alerts = []
        adjusted = dict(base_weights)
        for sid, w in base_weights.items():
            h = self._histories.get(sid)
            if h is None or not h.has_enough_data:
                continue
            level = h.determine_level()
            scale = h.weight_scale()
            if scale < 1.0:
                adjusted[sid] = w * scale
                alerts.append(ValuationAlert(
                    sector_id=sid, level=level, percentile=h.current_percentile,
                    metric_value=h._values[-1] if h._values else 0,
                    pb_value=None, base_weight=w, adjusted_weight=adjusted[sid],
                    message=f"{sid} 权重 {w:.2%} → {adjusted[sid]:.2%}",
                ))
        # 再归一化
        total = sum(adjusted.values())
        if total > 0:
            adjusted = {k: v / total for k, v in adjusted.items()}
        return adjusted, alerts

    def reset(self) -> None:
        for h in self._histories.values():
            h.reset()

    def summary(self) -> dict[str, Any]:
        return {
            sid: {
                "level": h._level.value,
                "percentile": h.current_percentile if h.has_enough_data else None,
                "n_obs": len(h._values),
            }
            for sid, h in self._histories.items()
        }
