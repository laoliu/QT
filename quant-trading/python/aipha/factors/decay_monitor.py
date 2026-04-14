"""
因子衰减在线监控

实时跟踪因子的 IC/ICIR 变化趋势，当 ICIR 滑落至告警阈值以下时触发状态变更。
与 rolling_ic.py（离线批量分析）互补——本模块面向生产环境的增量更新场景。

使用示例：
    monitor = DecayMonitor("margin_buy_intensity", icir_threshold=0.3)
    result = monitor.update("2026-03-13", ic_value=-0.065)
    print(monitor.get_status())  # "NORMAL" / "WARNING" / "DEGRADED"
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Literal

import numpy as np


MonitorStatus = Literal["NORMAL", "WARNING", "DEGRADED"]

# 状态阈值倍数：ICIR 跌至 threshold * ratio 时进入对应状态
_WARNING_RATIO = 0.6    # ICIR < threshold * 0.6 → WARNING
_DEGRADED_RATIO = 0.3   # ICIR < threshold * 0.3 → DEGRADED


@dataclass
class DecayUpdateResult:
    """单次 update() 调用的返回值"""

    date: str
    ic_value: float
    rolling_icir: float
    status: MonitorStatus
    status_changed: bool
    window_size: int  # 当前窗口内实际点数


@dataclass
class DecayMonitorSnapshot:
    """完整状态快照，用于序列化/告警推送"""

    factor_name: str
    window: int
    icir_threshold: float
    current_status: MonitorStatus
    rolling_icir: float
    rolling_ic_mean: float
    rolling_ic_std: float
    n_observations: int
    latest_date: str | None
    warning_ratio: float = _WARNING_RATIO
    degraded_ratio: float = _DEGRADED_RATIO


class DecayMonitor:
    """因子衰减在线监控器

    维护一个滚动窗口，每日接收新的 IC 值，实时计算 ICIR 并判断状态。

    状态机：
        NORMAL → WARNING（ICIR < threshold * 0.6）
        WARNING → DEGRADED（ICIR < threshold * 0.3）
        DEGRADED → WARNING（ICIR 回升 ≥ threshold * 0.3）
        WARNING → NORMAL（ICIR 回升 ≥ threshold * 0.6）
    """

    def __init__(
        self,
        factor_name: str,
        window: int = 20,
        icir_threshold: float = 0.3,
    ) -> None:
        if window < 2:
            raise ValueError(f"window must be >= 2, got {window}")
        if icir_threshold <= 0:
            raise ValueError(f"icir_threshold must be > 0, got {icir_threshold}")
        self.factor_name = factor_name
        self.window = window
        self.icir_threshold = icir_threshold
        self._ic_buffer: Deque[float] = deque(maxlen=window)
        self._dates: Deque[str] = deque(maxlen=window)
        self._status: MonitorStatus = "NORMAL"
        self._n_total: int = 0  # 历史累计观测数

    def update(self, date: str, ic_value: float) -> DecayUpdateResult:
        """接收新 IC 值，更新窗口，返回当前状态"""
        self._ic_buffer.append(ic_value)
        self._dates.append(date)
        self._n_total += 1

        icir = self._compute_icir()
        prev_status = self._status
        self._status = self._resolve_status(icir)

        return DecayUpdateResult(
            date=date,
            ic_value=ic_value,
            rolling_icir=icir,
            status=self._status,
            status_changed=(self._status != prev_status),
            window_size=len(self._ic_buffer),
        )

    def get_status(self) -> MonitorStatus:
        """返回当前监控状态"""
        return self._status

    def get_rolling_icir(self) -> float:
        """返回当前窗口内的 ICIR（mean/std），窗口不足 2 点时返回 np.nan"""
        return self._compute_icir()

    def to_dict(self) -> dict:
        """序列化为字典，用于持久化或告警推送"""
        buf = list(self._ic_buffer)
        ic_mean = float(np.mean(buf)) if buf else float("nan")
        ic_std = float(np.std(buf, ddof=1)) if len(buf) >= 2 else float("nan")
        return {
            "factor_name": self.factor_name,
            "window": self.window,
            "icir_threshold": self.icir_threshold,
            "current_status": self._status,
            "rolling_icir": round(self.get_rolling_icir(), 4),
            "rolling_ic_mean": round(ic_mean, 4),
            "rolling_ic_std": round(ic_std, 4) if not np.isnan(ic_std) else None,
            "n_observations": self._n_total,
            "latest_date": self._dates[-1] if self._dates else None,
            "warning_ratio": _WARNING_RATIO,
            "degraded_ratio": _DEGRADED_RATIO,
        }

    def _compute_icir(self) -> float:
        """内部：计算当前窗口的 ICIR = mean(IC) / std(IC)"""
        buf = list(self._ic_buffer)
        if len(buf) < 2:
            return float("nan")
        mean = np.mean(buf)
        std = np.std(buf, ddof=1)
        if std == 0:
            return float("nan")
        return float(mean / std)

    def _resolve_status(self, icir: float) -> MonitorStatus:
        """内部：根据 |ICIR| 值与阈值比较决定状态"""
        if np.isnan(icir):
            return self._status  # 数据不足时维持现状
        abs_icir = abs(icir)
        thr = self.icir_threshold
        if abs_icir < thr * _DEGRADED_RATIO:
            return "DEGRADED"
        elif abs_icir < thr * _WARNING_RATIO:
            return "WARNING"
        else:
            return "NORMAL"
