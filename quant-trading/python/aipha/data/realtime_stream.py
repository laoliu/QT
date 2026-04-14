"""
实时行情数据流

提供WebSocket连接管理和实时行情推送能力。
支持多数据源切换和重连机制。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Callable


logger = logging.getLogger(__name__)


class StreamStatus(str, Enum):
    """数据流状态"""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"


class QuoteType(str, Enum):
    """行情类型"""

    TICK = "tick"  # 逐笔
    LEVEL1 = "level1"  # 一档行情
    LEVEL2 = "level2"  # 五档行情
    TRADE = "trade"  # 成交明细
    ORDER = "order"  # 委托队列
    BAR_1M = "bar_1m"  # 1分钟K线
    BAR_5M = "bar_5m"  # 5分钟K线


@dataclass
class QuoteSnapshot:
    """行情快照"""

    symbol: str
    quote_type: QuoteType
    timestamp: datetime

    # 价格信息
    last_price: Decimal
    open_price: Decimal | None = None
    high_price: Decimal | None = None
    low_price: Decimal | None = None
    close_price: Decimal | None = None
    pre_close: Decimal | None = None

    # 成交信息
    volume: int = 0
    amount: Decimal = Decimal("0")
    trade_count: int = 0

    # 盘口
    bid_prices: list[Decimal] = field(default_factory=list)
    bid_volumes: list[int] = field(default_factory=list)
    ask_prices: list[Decimal] = field(default_factory=list)
    ask_volumes: list[int] = field(default_factory=list)

    # 状态
    status: str = "trading"  # trading / suspended / halt

    @property
    def spread(self) -> Decimal | None:
        """买卖价差"""
        if self.bid_prices and self.ask_prices:
            return self.ask_prices[0] - self.bid_prices[0]
        return None

    @property
    def mid_price(self) -> Decimal | None:
        """中间价"""
        if self.bid_prices and self.ask_prices:
            return (self.ask_prices[0] + self.bid_prices[0]) / 2
        return None


@dataclass
class BarData:
    """K线数据"""

    symbol: str
    interval: str  # 1m / 5m / 15m / 30m / 1h / 1d
    timestamp: datetime

    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    amount: Decimal

    vwap: Decimal | None = None
    trade_count: int | None = None


QuoteCallback = Callable[[QuoteSnapshot], None]
BarCallback = Callable[[BarData], None]
StatusCallback = Callable[[StreamStatus, str], None]


class RealtimeStreamConfig:
    """实时数据流配置"""

    def __init__(
        self,
        reconnect_enabled: bool = True,
        reconnect_max_attempts: int = 10,
        reconnect_interval: float = 5.0,
        reconnect_backoff: float = 1.5,
        heartbeat_interval: float = 30.0,
        connection_timeout: float = 10.0,
    ):
        """初始化实时数据流配置。

        Args:
            reconnect_enabled: 是否启用自动重连
            reconnect_max_attempts: 最大重连次数
            reconnect_interval: 重连间隔（秒）
            reconnect_backoff: 重连退避倍数
            heartbeat_interval: 心跳间隔（秒）
            connection_timeout: 连接超时（秒）
        """
        self.reconnect_enabled = reconnect_enabled
        self.reconnect_max_attempts = reconnect_max_attempts
        self.reconnect_interval = reconnect_interval
        self.reconnect_backoff = reconnect_backoff
        self.heartbeat_interval = heartbeat_interval
        self.connection_timeout = connection_timeout


class RealtimeStreamProvider(ABC):
    """实时行情数据源接口"""

    @abstractmethod
    async def connect(self) -> bool:
        """建立连接"""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """断开连接"""
        ...

    @abstractmethod
    async def subscribe(
        self,
        symbols: list[str],
        quote_types: list[QuoteType],
    ) -> bool:
        """订阅行情"""
        ...

    @abstractmethod
    async def unsubscribe(
        self,
        symbols: list[str],
        quote_types: list[QuoteType],
    ) -> bool:
        """取消订阅"""
        ...

    @abstractmethod
    def on_quote(self, callback: QuoteCallback) -> None:
        """设置行情回调"""
        ...

    @abstractmethod
    def on_bar(self, callback: BarCallback) -> None:
        """设置K线回调"""
        ...

    @abstractmethod
    def on_status(self, callback: StatusCallback) -> None:
        """设置状态回调"""
        ...

    @abstractmethod
    def get_status(self) -> StreamStatus:
        """获取当前状态"""
        ...


class StreamManager:
    """数据流管理器

    管理多个数据源连接，提供统一的行情推送。
    """

    def __init__(self, config: RealtimeStreamConfig | None = None):
        self.config = config or RealtimeStreamConfig()
        self._providers: dict[str, RealtimeStreamProvider] = {}
        self._primary_provider: str | None = None
        self._subscriptions: dict[str, set[QuoteType]] = {}

        self._quote_callbacks: list[QuoteCallback] = []
        self._bar_callbacks: list[BarCallback] = []
        self._status_callbacks: list[StatusCallback] = []

        self._status = StreamStatus.DISCONNECTED
        self._reconnect_task: asyncio.Task | None = None
        self._last_quote_time: datetime | None = None

    def register_provider(
        self,
        name: str,
        provider: RealtimeStreamProvider,
        is_primary: bool = False,
    ) -> None:
        """注册数据源"""
        self._providers[name] = provider
        if is_primary or self._primary_provider is None:
            self._primary_provider = name

        # 设置回调
        provider.on_quote(self._on_quote_received)
        provider.on_bar(self._on_bar_received)
        provider.on_status(self._on_status_changed)

        logger.info(f"Registered provider: {name}, primary={is_primary}")

    def get_primary_provider(self) -> RealtimeStreamProvider | None:
        """获取主数据源"""
        if self._primary_provider is None:
            return None
        return self._providers.get(self._primary_provider)

    async def start(self) -> bool:
        """启动数据流"""
        provider = self.get_primary_provider()
        if provider is None:
            logger.error("No provider registered")
            return False

        self._update_status(StreamStatus.CONNECTING)
        success = await provider.connect()

        if success:
            self._update_status(StreamStatus.CONNECTED)
            logger.info("Stream started successfully")
        else:
            self._update_status(StreamStatus.ERROR)
            logger.error("Failed to start stream")

        return success

    async def stop(self) -> None:
        """停止数据流"""
        # 取消重连任务
        if self._reconnect_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            self._reconnect_task = None

        # 断开所有连接
        for name, provider in self._providers.items():
            try:
                await provider.disconnect()
                logger.info(f"Disconnected provider: {name}")
            except Exception as e:
                logger.error(f"Error disconnecting {name}: {e}")

        self._update_status(StreamStatus.DISCONNECTED)

    async def subscribe(
        self,
        symbols: list[str],
        quote_types: list[QuoteType] | None = None,
    ) -> bool:
        """订阅行情"""
        if quote_types is None:
            quote_types = [QuoteType.LEVEL1]

        provider = self.get_primary_provider()
        if provider is None:
            return False

        success = await provider.subscribe(symbols, quote_types)

        if success:
            for symbol in symbols:
                if symbol not in self._subscriptions:
                    self._subscriptions[symbol] = set()
                self._subscriptions[symbol].update(quote_types)

        return success

    async def unsubscribe(
        self,
        symbols: list[str],
        quote_types: list[QuoteType] | None = None,
    ) -> bool:
        """取消订阅"""
        provider = self.get_primary_provider()
        if provider is None:
            return False

        if quote_types is None:
            # 取消所有类型
            quote_types = list(QuoteType)

        success = await provider.unsubscribe(symbols, quote_types)

        if success:
            for symbol in symbols:
                if symbol in self._subscriptions:
                    self._subscriptions[symbol] -= set(quote_types)
                    if not self._subscriptions[symbol]:
                        del self._subscriptions[symbol]

        return success

    def on_quote(self, callback: QuoteCallback) -> None:
        """注册行情回调"""
        self._quote_callbacks.append(callback)

    def on_bar(self, callback: BarCallback) -> None:
        """注册K线回调"""
        self._bar_callbacks.append(callback)

    def on_status(self, callback: StatusCallback) -> None:
        """注册状态回调"""
        self._status_callbacks.append(callback)

    def get_status(self) -> StreamStatus:
        """获取状态"""
        return self._status

    def get_subscriptions(self) -> dict[str, set[QuoteType]]:
        """获取当前订阅"""
        return self._subscriptions.copy()

    def _on_quote_received(self, quote: QuoteSnapshot) -> None:
        """处理接收到的行情"""
        self._last_quote_time = datetime.now()

        for callback in self._quote_callbacks:
            try:
                callback(quote)
            except Exception as e:
                logger.error(f"Error in quote callback: {e}")

    def _on_bar_received(self, bar: BarData) -> None:
        """处理接收到的K线"""
        for callback in self._bar_callbacks:
            try:
                callback(bar)
            except Exception as e:
                logger.error(f"Error in bar callback: {e}")

    def _on_status_changed(self, status: StreamStatus, message: str) -> None:
        """处理状态变化"""
        old_status = self._status
        self._update_status(status)

        # 触发重连
        if (
            self.config.reconnect_enabled
            and status == StreamStatus.DISCONNECTED
            and old_status == StreamStatus.CONNECTED
        ):
            self._schedule_reconnect()

    def _update_status(self, status: StreamStatus) -> None:
        """更新状态"""
        if self._status != status:
            self._status = status
            for callback in self._status_callbacks:
                try:
                    callback(status, "")
                except Exception as e:
                    logger.error(f"Error in status callback: {e}")

    def _schedule_reconnect(self) -> None:
        """调度重连"""
        if self._reconnect_task and not self._reconnect_task.done():
            return

        async def reconnect() -> None:
            attempts = 0
            interval = self.config.reconnect_interval

            while attempts < self.config.reconnect_max_attempts:
                attempts += 1
                self._update_status(StreamStatus.RECONNECTING)
                logger.info(
                    f"Reconnect attempt {attempts}/{self.config.reconnect_max_attempts}"
                )

                await asyncio.sleep(interval)

                provider = self.get_primary_provider()
                if provider and await provider.connect():
                    # 重新订阅
                    for symbol, quote_types in self._subscriptions.items():
                        await provider.subscribe([symbol], list(quote_types))

                    self._update_status(StreamStatus.CONNECTED)
                    logger.info("Reconnected successfully")
                    return

                interval *= self.config.reconnect_backoff

            logger.error("Max reconnect attempts reached")
            self._update_status(StreamStatus.ERROR)

        self._reconnect_task = asyncio.create_task(reconnect())


class SyntheticRealtimeProvider(RealtimeStreamProvider):
    """合成实时数据提供者（用于开发/测试/仿真）

    生成随机波动的模拟行情数据，实现 RealtimeStreamProvider 接口。
    不使用真实市场数据，适用于集成测试和纸上交易验证。
    """

    def __init__(self):
        self._connected = False
        self._subscribed: dict[str, set[QuoteType]] = {}

        self._quote_callback: QuoteCallback | None = None
        self._bar_callback: BarCallback | None = None
        self._status_callback: StatusCallback | None = None

        self._generate_task: asyncio.Task | None = None

    async def connect(self) -> bool:
        """建立模拟连接并通知状态回调。"""
        self._connected = True
        if self._status_callback:
            self._status_callback(StreamStatus.CONNECTED, "")
        return True

    async def disconnect(self) -> None:
        """断开模拟连接并停止数据生成任务。"""
        self._connected = False
        if self._generate_task:
            self._generate_task.cancel()
            try:
                await self._generate_task
            except asyncio.CancelledError:
                pass
        if self._status_callback:
            self._status_callback(StreamStatus.DISCONNECTED, "")

    async def subscribe(
        self,
        symbols: list[str],
        quote_types: list[QuoteType],
    ) -> bool:
        """订阅指定品种的模拟行情并启动数据生成。"""
        for symbol in symbols:
            if symbol not in self._subscribed:
                self._subscribed[symbol] = set()
            self._subscribed[symbol].update(quote_types)

        # 启动模拟数据生成
        if self._generate_task is None or self._generate_task.done():
            self._generate_task = asyncio.create_task(self._generate_quotes())

        return True

    async def unsubscribe(
        self,
        symbols: list[str],
        quote_types: list[QuoteType],
    ) -> bool:
        """取消指定品种的模拟行情订阅。"""
        for symbol in symbols:
            if symbol in self._subscribed:
                self._subscribed[symbol] -= set(quote_types)
                if not self._subscribed[symbol]:
                    del self._subscribed[symbol]
        return True

    def on_quote(self, callback: QuoteCallback) -> None:
        """注册行情快照回调函数。"""
        self._quote_callback = callback

    def on_bar(self, callback: BarCallback) -> None:
        """注册Bar数据回调函数。"""
        self._bar_callback = callback

    def on_status(self, callback: StatusCallback) -> None:
        """注册连接状态变更回调函数。"""
        self._status_callback = callback

    def get_status(self) -> StreamStatus:
        """返回当前流连接状态。"""
        return StreamStatus.CONNECTED if self._connected else StreamStatus.DISCONNECTED

    async def _generate_quotes(self) -> None:
        """生成模拟行情"""
        import random

        prices: dict[str, Decimal] = {}

        while self._connected and self._subscribed:
            for symbol, quote_types in list(self._subscribed.items()):
                if QuoteType.LEVEL1 not in quote_types:
                    continue

                # 初始化价格
                if symbol not in prices:
                    prices[symbol] = Decimal("10.00")

                # 随机波动
                change = Decimal(str(random.uniform(-0.02, 0.02)))
                prices[symbol] *= (1 + change)
                prices[symbol] = round(prices[symbol], 2)

                quote = QuoteSnapshot(
                    symbol=symbol,
                    quote_type=QuoteType.LEVEL1,
                    timestamp=datetime.now(),
                    last_price=prices[symbol],
                    bid_prices=[prices[symbol] - Decimal("0.01")],
                    bid_volumes=[random.randint(100, 1000)],
                    ask_prices=[prices[symbol] + Decimal("0.01")],
                    ask_volumes=[random.randint(100, 1000)],
                    volume=random.randint(1000, 10000),
                    amount=prices[symbol] * random.randint(10000, 100000),
                )

                if self._quote_callback:
                    self._quote_callback(quote)

            await asyncio.sleep(1.0)  # 每秒更新


class QuoteBuffer:
    """行情缓冲区

    缓存最近的行情数据，支持快速查询。
    """

    def __init__(self, max_size: int = 1000):
        self._max_size = max_size
        self._quotes: dict[str, list[QuoteSnapshot]] = {}
        self._latest: dict[str, QuoteSnapshot] = {}

    def add(self, quote: QuoteSnapshot) -> None:
        """添加行情"""
        symbol = quote.symbol

        # 更新最新
        self._latest[symbol] = quote

        # 添加到历史
        if symbol not in self._quotes:
            self._quotes[symbol] = []

        self._quotes[symbol].append(quote)

        # 限制大小
        if len(self._quotes[symbol]) > self._max_size:
            self._quotes[symbol] = self._quotes[symbol][-self._max_size:]

    def get_latest(self, symbol: str) -> QuoteSnapshot | None:
        """获取最新行情"""
        return self._latest.get(symbol)

    def get_history(
        self,
        symbol: str,
        count: int = 100,
    ) -> list[QuoteSnapshot]:
        """获取历史行情"""
        quotes = self._quotes.get(symbol, [])
        return quotes[-count:]

    def clear(self, symbol: str | None = None) -> None:
        """清除缓存"""
        if symbol:
            self._quotes.pop(symbol, None)
            self._latest.pop(symbol, None)
        else:
            self._quotes.clear()
            self._latest.clear()
