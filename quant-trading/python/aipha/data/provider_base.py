"""数据提供者基础协议

定义 ``BarProvider`` 接口与 ``DataFingerprint`` 数据结构，
确保所有数据提供者（TuShare / AkShare / 本地文件等）遵循统一的
加载与可复现性契约。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from aipha.domain.models import Bar


@dataclass(frozen=True, slots=True)
class DataFingerprint:
    """数据指纹：标识数据来源与口径，用于实验可复现性。

    Attributes:
        provider: 数据提供者名称（如 ``tushare_pro``）
        snapshot: 数据快照标识（如 ``latest`` 或日期）
        calendar: 交易日历版本
        adjust: 复权方式（如 ``qfq`` / ``hfq`` / ``none``）
        schema: 数据 schema 版本
    """

    provider: str
    snapshot: str
    calendar: str
    adjust: str
    schema: str


class BarProvider(Protocol):
    """K 线数据提供者协议。

    所有实现必须：
    - 给定 (symbol, start, end, frequency, adjust, snapshot) 后确定性返回结果
    - 提供 ``DataFingerprint`` 以支持可复现性

    当前实现：``TuShareProBarProvider``、``SyntheticBarProvider``。
    """

    def fingerprint(self) -> DataFingerprint:
        """返回当前提供者的数据指纹。"""
        ...

    def load_bars(self, *, symbol: str, start: date, end: date, frequency: str) -> list[Bar]:
        """加载指定标的在给定时间范围内的 K 线序列。

        Args:
            symbol: 标的代码（如 ``000001.SZ``）
            start: 开始日期（含）
            end: 结束日期（含）
            frequency: 数据频率（如 ``1d`` / ``1m``）

        Returns:
            按时间升序排列的 Bar 列表
        """
        ...
