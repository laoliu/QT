"""QMT 回调处理器 — 线程安全的事件分发。

接收 xtquant XtQuantTraderCallback 的异步推送（成交/订单状态/断连），
转换为 AlphQ GatewayOrder/ExecutionReport，放入线程安全队列供上层消费。

依赖：xtquant（可选，仅 Windows + miniQMT 运行时可用）。
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import IntEnum
from typing import Any

from aipha.execution.gateway import (
    ExecutionReport,
    GatewayOrder,
    OrderStatus,
    OrderStatusChange,
)

logger = logging.getLogger(__name__)

try:
    from xtquant.xttrader import XtQuantTraderCallback  # type: ignore[import-untyped]

    HAS_XTQUANT = True
except ImportError:
    XtQuantTraderCallback = object  # type: ignore[assignment,misc]
    HAS_XTQUANT = False


# ---------------------------------------------------------------------------
# QMT 原始订单状态 → AlphQ OrderStatus 映射
# ---------------------------------------------------------------------------

class QMTOrderStatus(IntEnum):
    """xtquant 订单状态码。"""

    UNKNOWN = 255
    PENDING = 48  # 未报
    WAIT_REPORTING = 49
    SUBMITTED = 50  # 已报
    CANCELING = 51  # 已报待撤
    PARTIAL_CANCELING = 52
    PARTIAL_CANCELED = 53
    CANCELED = 54  # 已撤
    PARTIAL = 55  # 部成
    FILLED = 56  # 已成
    REJECTED = 57  # 废单
    EXPIRED = 99  # 过期（自定义）


_STATUS_MAP: dict[int, OrderStatus] = {
    QMTOrderStatus.PENDING: OrderStatus.PENDING,
    QMTOrderStatus.WAIT_REPORTING: OrderStatus.PENDING,
    QMTOrderStatus.SUBMITTED: OrderStatus.SUBMITTED,
    QMTOrderStatus.PARTIAL: OrderStatus.PARTIAL_FILLED,
    QMTOrderStatus.FILLED: OrderStatus.FILLED,
    QMTOrderStatus.CANCELING: OrderStatus.PENDING,
    QMTOrderStatus.PARTIAL_CANCELING: OrderStatus.PARTIAL_FILLED,
    QMTOrderStatus.PARTIAL_CANCELED: OrderStatus.CANCELED,
    QMTOrderStatus.CANCELED: OrderStatus.CANCELED,
    QMTOrderStatus.REJECTED: OrderStatus.REJECTED,
    QMTOrderStatus.EXPIRED: OrderStatus.EXPIRED,
}


def map_qmt_status(raw: int) -> OrderStatus:
    """将 QMT 原始状态码映射为 AlphQ OrderStatus。"""
    return _STATUS_MAP.get(raw, OrderStatus.PENDING)


# ---------------------------------------------------------------------------
# 事件数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class QMTFillEvent:
    """来自 QMT on_stock_trade 的成交推送。"""
    order_id: str
    symbol: str
    side: str  # "BUY" / "SELL"
    fill_qty: int
    fill_price: float
    fill_time: datetime
    commission: float = 0.0


@dataclass(frozen=True, slots=True)
class QMTOrderEvent:
    """来自 QMT on_stock_order / on_order_error 的订单状态推送。"""
    order_id: str
    symbol: str
    status: OrderStatus
    raw_status: int
    error_msg: str = ""
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True, slots=True)
class QMTTickEvent:
    """来自 xtquant subscribe_quote tick 推送。"""
    symbol: str
    price: float
    volume: int
    bid1: float = 0.0
    ask1: float = 0.0
    bid1_vol: int = 0
    ask1_vol: int = 0
    amount: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# Tick 字段提取辅助
# ---------------------------------------------------------------------------

def _last_val(fields: dict[str, Any], key: str, default: float = 0.0) -> float:
    """从 xtquant tick dict 的列表字段中取最后一个值。"""
    val = fields.get(key)
    if isinstance(val, (list, tuple)) and val:
        return float(val[-1])
    if isinstance(val, (int, float)):
        return float(val)
    return default


# ---------------------------------------------------------------------------
# QMT 回调实现
# ---------------------------------------------------------------------------

class QMTCallback(XtQuantTraderCallback):  # type: ignore[misc]
    """线程安全的 QMT 回调处理器。

    xtquant 在后台线程中调用 on_stock_trade / on_stock_order 等方法，
    本类将事件放入 queue.Queue 供主线程消费，避免竞态条件。
    """

    def __init__(self, maxsize: int = 10000) -> None:
        if HAS_XTQUANT:
            super().__init__()
        self._fill_queue: queue.Queue[QMTFillEvent] = queue.Queue(maxsize=maxsize)
        self._order_queue: queue.Queue[QMTOrderEvent] = queue.Queue(maxsize=maxsize)
        self._tick_queue: queue.Queue[QMTTickEvent] = queue.Queue(maxsize=maxsize)
        self._connected = threading.Event()
        self._connected.set()

    # -- xtquant callbacks --------------------------------------------------

    def on_disconnected(self) -> None:
        """QMT 连接断开。"""
        logger.warning("QMT 连接断开")
        self._connected.clear()

    def on_connected(self) -> None:
        """QMT 连接恢复（自定义扩展，非 xtquant 原生）。"""
        logger.info("QMT 连接恢复")
        self._connected.set()

    def on_stock_trade(self, trade: Any) -> None:
        """成交回报推送。"""
        try:
            evt = QMTFillEvent(
                order_id=str(getattr(trade, "order_id", "")),
                symbol=getattr(trade, "stock_code", ""),
                side="BUY" if getattr(trade, "order_type", 0) in (23, 33) else "SELL",
                fill_qty=int(getattr(trade, "traded_volume", 0)),
                fill_price=float(getattr(trade, "traded_price", 0.0)),
                fill_time=datetime.now(),
                commission=float(getattr(trade, "commission", 0.0)),
            )
            self._fill_queue.put_nowait(evt)
        except queue.Full:
            logger.error("成交事件队列已满，丢弃: %s", trade)
        except Exception:
            logger.exception("处理成交回报异常")

    def on_stock_order(self, order: Any) -> None:
        """订单状态推送。"""
        try:
            raw = int(getattr(order, "order_status", -1))
            evt = QMTOrderEvent(
                order_id=str(getattr(order, "order_id", "")),
                symbol=getattr(order, "stock_code", ""),
                status=map_qmt_status(raw),
                raw_status=raw,
            )
            self._order_queue.put_nowait(evt)
        except queue.Full:
            logger.error("订单事件队列已满，丢弃: %s", order)
        except Exception:
            logger.exception("处理订单状态异常")

    def on_order_error(self, order_error: Any) -> None:
        """订单错误（废单）推送。"""
        try:
            evt = QMTOrderEvent(
                order_id=str(getattr(order_error, "order_id", "")),
                symbol=getattr(order_error, "stock_code", ""),
                status=OrderStatus.REJECTED,
                raw_status=58,
                error_msg=getattr(order_error, "error_msg", "unknown"),
            )
            self._order_queue.put_nowait(evt)
        except Exception:
            logger.exception("处理订单错误异常")

    def on_stock_data(self, data: dict[str, Any]) -> None:
        """xtquant subscribe_quote tick 推送回调。

        xtdata.subscribe_quote 的 callback 签名为 callback(data)，
        data 是 dict: {symbol: {field: [values...]}} 。
        解析最新一条 tick 并放入 _tick_queue。
        """
        try:
            for symbol, fields in data.items():
                if not isinstance(fields, dict):
                    continue
                # xtquant 返回的每个字段是列表，取最后一条
                last_price = _last_val(fields, "lastPrice", 0.0)
                if last_price <= 0:
                    continue
                evt = QMTTickEvent(
                    symbol=symbol,
                    price=last_price,
                    volume=int(_last_val(fields, "volume", 0)),
                    bid1=_last_val(fields, "bidPrice", 0.0),
                    ask1=_last_val(fields, "askPrice", 0.0),
                    bid1_vol=int(_last_val(fields, "bidVol", 0)),
                    ask1_vol=int(_last_val(fields, "askVol", 0)),
                    amount=_last_val(fields, "amount", 0.0),
                )
                self._tick_queue.put_nowait(evt)
        except queue.Full:
            logger.warning("Tick 队列已满，丢弃部分 tick")
        except Exception:
            logger.exception("处理 tick 数据异常")

    # -- 队列消费接口 -------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()

    def drain_fills(self, max_items: int = 100) -> list[QMTFillEvent]:
        """批量取出成交事件（非阻塞）。"""
        events: list[QMTFillEvent] = []
        for _ in range(max_items):
            try:
                events.append(self._fill_queue.get_nowait())
            except queue.Empty:
                break
        return events

    def drain_orders(self, max_items: int = 100) -> list[QMTOrderEvent]:
        """批量取出订单状态事件（非阻塞）。"""
        events: list[QMTOrderEvent] = []
        for _ in range(max_items):
            try:
                events.append(self._order_queue.get_nowait())
            except queue.Empty:
                break
        return events

    def drain_ticks(self, max_items: int = 500) -> list[QMTTickEvent]:
        """批量取出 Tick 事件（非阻塞）。"""
        events: list[QMTTickEvent] = []
        for _ in range(max_items):
            try:
                events.append(self._tick_queue.get_nowait())
            except queue.Empty:
                break
        return events

    def wait_fill(self, timeout: float = 5.0) -> QMTFillEvent | None:
        """阻塞等待一个成交事件。"""
        try:
            return self._fill_queue.get(timeout=timeout)
        except queue.Empty:
            return None
