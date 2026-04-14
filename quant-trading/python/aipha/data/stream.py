"""
实时数据流模拟模块

提供实时行情数据的模拟功能，用于策略仿真测试：
- 历史数据回放（支持倍速）
- 模拟盘口生成
- 回调机制
- 时钟同步

使用示例：
    simulator = DataStreamSimulator(historical_data)
    simulator.on_bar(my_strategy.handle_bar)
    simulator.start(speed=10)  # 10倍速回放
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from threading import Event, Thread
from typing import Any, Callable, Protocol


class BarFrequency(Enum):
    """K线频率"""

    MIN_1 = "1min"
    MIN_5 = "5min"
    MIN_15 = "15min"
    MIN_30 = "30min"
    MIN_60 = "60min"
    DAILY = "daily"


@dataclass
class Bar:
    """K线数据"""

    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float = 0.0
    frequency: BarFrequency = BarFrequency.MIN_1

    def __repr__(self) -> str:
        return f"Bar({self.symbol}, {self.timestamp}, O={self.open:.2f}, C={self.close:.2f}, V={self.volume:.0f})"


@dataclass
class Quote:
    """盘口数据"""

    symbol: str
    timestamp: datetime
    last_price: float
    bid_price: float
    ask_price: float
    bid_volume: float
    ask_volume: float


@dataclass
class MarketEvent:
    """市场事件（用于事件驱动架构）"""

    event_type: str  # "bar", "quote", "trade", "open", "close"
    timestamp: datetime
    data: Any


class DataStreamHandler(Protocol):
    """数据流处理器协议"""

    def on_bar(self, bar: Bar) -> None:
        """处理K线"""
        ...

    def on_quote(self, quote: Quote) -> None:
        """处理盘口"""
        ...

    def on_market_open(self) -> None:
        """开盘"""
        ...

    def on_market_close(self) -> None:
        """收盘"""
        ...


class DataStreamSimulator:
    """数据流模拟器

    将历史数据按时间顺序回放，模拟实时数据流。
    """

    def __init__(
        self,
        bars: list[Bar] | None = None,
        quotes: list[Quote] | None = None,
    ):
        """初始化模拟器

        Args:
            bars: K线数据列表
            quotes: 盘口数据列表
        """
        self._bars = sorted(bars or [], key=lambda x: x.timestamp)
        self._quotes = sorted(quotes or [], key=lambda x: x.timestamp)

        # 回调
        self._bar_callbacks: list[Callable[[Bar], None]] = []
        self._quote_callbacks: list[Callable[[Quote], None]] = []
        self._event_callbacks: list[Callable[[MarketEvent], None]] = []

        # 控制
        self._running = False
        self._paused = False
        self._stop_event = Event()
        self._speed = 1.0
        self._current_time: datetime | None = None

        # 统计
        self._bars_sent = 0
        self._quotes_sent = 0

    def on_bar(self, callback: Callable[[Bar], None]) -> None:
        """注册K线回调"""
        self._bar_callbacks.append(callback)

    def on_quote(self, callback: Callable[[Quote], None]) -> None:
        """注册盘口回调"""
        self._quote_callbacks.append(callback)

    def on_event(self, callback: Callable[[MarketEvent], None]) -> None:
        """注册事件回调"""
        self._event_callbacks.append(callback)

    def _emit_bar(self, bar: Bar) -> None:
        """发送K线"""
        for callback in self._bar_callbacks:
            try:
                callback(bar)
            except Exception as e:
                print(f"Bar callback error: {e}")
        self._bars_sent += 1

    def _emit_quote(self, quote: Quote) -> None:
        """发送盘口"""
        for callback in self._quote_callbacks:
            try:
                callback(quote)
            except Exception as e:
                print(f"Quote callback error: {e}")
        self._quotes_sent += 1

    def _emit_event(self, event: MarketEvent) -> None:
        """发送事件"""
        for callback in self._event_callbacks:
            try:
                callback(event)
            except Exception as e:
                print(f"Event callback error: {e}")

    def start(
        self,
        speed: float = 1.0,
        blocking: bool = True,
    ) -> None:
        """启动数据流

        Args:
            speed: 回放速度倍数（1.0=实时，10.0=10倍速）
            blocking: 是否阻塞
        """
        self._speed = speed
        self._running = True
        self._stop_event.clear()

        if blocking:
            self._run_loop()
        else:
            thread = Thread(target=self._run_loop, daemon=True)
            thread.start()

    def _run_loop(self) -> None:
        """运行主循环"""
        # 合并所有事件并排序
        events = []

        for bar in self._bars:
            events.append((bar.timestamp, "bar", bar))

        for quote in self._quotes:
            events.append((quote.timestamp, "quote", quote))

        events.sort(key=lambda x: x[0])

        if not events:
            self._running = False
            return

        # 通知开盘
        first_time = events[0][0]
        self._emit_event(MarketEvent("open", first_time, None))

        prev_time: datetime | None = None

        for timestamp, event_type, data in events:
            if self._stop_event.is_set():
                break

            while self._paused and not self._stop_event.is_set():
                time.sleep(0.1)

            # 模拟时间间隔
            if prev_time and self._speed > 0:
                interval = (timestamp - prev_time).total_seconds()
                sleep_time = interval / self._speed
                if sleep_time > 0:
                    time.sleep(min(sleep_time, 1.0))  # 最多等1秒

            self._current_time = timestamp

            if event_type == "bar":
                self._emit_bar(data)
            elif event_type == "quote":
                self._emit_quote(data)

            prev_time = timestamp

        # 通知收盘
        if events:
            last_time = events[-1][0]
            self._emit_event(MarketEvent("close", last_time, None))

        self._running = False

    def stop(self) -> None:
        """停止数据流"""
        self._stop_event.set()
        self._running = False

    def pause(self) -> None:
        """暂停"""
        self._paused = True

    def resume(self) -> None:
        """恢复"""
        self._paused = False

    def set_speed(self, speed: float) -> None:
        """设置速度"""
        self._speed = speed

    @property
    def is_running(self) -> bool:
        """是否正在运行"""
        return self._running

    @property
    def current_time(self) -> datetime | None:
        """当前模拟时间"""
        return self._current_time

    @property
    def stats(self) -> dict[str, int]:
        """统计信息"""
        return {
            "bars_sent": self._bars_sent,
            "quotes_sent": self._quotes_sent,
        }


class HistoricalDataPlayer:
    """历史数据播放器

    从历史数据生成模拟的实时数据流。
    """

    def __init__(
        self,
        data: dict[date, dict[str, dict[str, float]]],
        frequency: BarFrequency = BarFrequency.DAILY,
    ):
        """初始化播放器

        Args:
            data: 历史数据 {date: {symbol: {open, high, low, close, volume}}}
            frequency: 数据频率
        """
        self.data = data
        self.frequency = frequency

    def generate_bars(self) -> list[Bar]:
        """生成K线列表"""
        bars = []

        for d, symbols in sorted(self.data.items()):
            for symbol, ohlcv in symbols.items():
                if not all(k in ohlcv for k in ["open", "high", "low", "close", "volume"]):
                    continue

                bar = Bar(
                    symbol=symbol,
                    timestamp=datetime.combine(d, datetime.min.time().replace(hour=9, minute=30)),
                    open=ohlcv["open"],
                    high=ohlcv["high"],
                    low=ohlcv["low"],
                    close=ohlcv["close"],
                    volume=ohlcv["volume"],
                    amount=ohlcv.get("amount", ohlcv["volume"] * ohlcv["close"]),
                    frequency=self.frequency,
                )
                bars.append(bar)

        return sorted(bars, key=lambda x: (x.timestamp, x.symbol))

    def create_simulator(self) -> DataStreamSimulator:
        """创建模拟器"""
        bars = self.generate_bars()
        return DataStreamSimulator(bars=bars)


class QuoteGenerator:
    """盘口生成器

    从K线数据模拟盘口。
    """

    def __init__(
        self,
        spread_pct: float = 0.001,
        tick_size: float = 0.01,
    ):
        """初始化盘口生成器

        Args:
            spread_pct: 买卖价差百分比
            tick_size: 最小变动价位
        """
        self.spread_pct = spread_pct
        self.tick_size = tick_size

    def from_bar(self, bar: Bar) -> Quote:
        """从K线生成盘口"""
        spread = bar.close * self.spread_pct
        half_spread = spread / 2

        # 对齐到tick
        bid_price = round((bar.close - half_spread) / self.tick_size) * self.tick_size
        ask_price = round((bar.close + half_spread) / self.tick_size) * self.tick_size

        return Quote(
            symbol=bar.symbol,
            timestamp=bar.timestamp,
            last_price=bar.close,
            bid_price=bid_price,
            ask_price=ask_price,
            bid_volume=bar.volume * 0.3,  # 模拟
            ask_volume=bar.volume * 0.3,
        )


class AsyncDataStreamSimulator:
    """异步数据流模拟器

    支持asyncio的数据流模拟。
    """

    def __init__(
        self,
        bars: list[Bar] | None = None,
    ):
        """初始化模拟器"""
        self._bars = sorted(bars or [], key=lambda x: x.timestamp)
        self._bar_callbacks: list[Callable[[Bar], Any]] = []
        self._running = False
        self._speed = 1.0

    def on_bar(self, callback: Callable[[Bar], Any]) -> None:
        """注册K线回调（支持async函数）"""
        self._bar_callbacks.append(callback)

    async def start(self, speed: float = 1.0) -> None:
        """启动数据流（异步）"""
        self._speed = speed
        self._running = True

        prev_time: datetime | None = None

        for bar in self._bars:
            if not self._running:
                break

            # 模拟时间间隔
            if prev_time and self._speed > 0:
                interval = (bar.timestamp - prev_time).total_seconds()
                sleep_time = interval / self._speed
                if sleep_time > 0:
                    await asyncio.sleep(min(sleep_time, 1.0))

            # 发送K线
            for callback in self._bar_callbacks:
                try:
                    result = callback(bar)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    print(f"Async callback error: {e}")

            prev_time = bar.timestamp

        self._running = False

    def stop(self) -> None:
        """停止"""
        self._running = False


def create_simulator_from_dict(
    data: dict[date, dict[str, dict[str, float]]],
    speed: float = 1.0,
) -> DataStreamSimulator:
    """便捷函数：从字典数据创建模拟器

    Args:
        data: 历史数据
        speed: 回放速度

    Returns:
        数据流模拟器
    """
    player = HistoricalDataPlayer(data)
    simulator = player.create_simulator()
    simulator.set_speed(speed)
    return simulator
