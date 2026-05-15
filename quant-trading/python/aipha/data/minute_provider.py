"""分钟级行情数据 Provider 接口与内存缓存。

为 CB 日内 T+0 策略提供统一的分钟级数据获取协议：
- MinuteBarProvider: 抽象接口（Protocol）
- MinuteDataCache: 按日分片内存缓存
- 未来具体实现（QMT / 通达信 / 聚宽）继承此协议

数据频率：1min / 5min（BarFrequency 枚举）。
缓存策略：按 (symbol, date, freq) 三元组键存取，
    持久化为 Parquet 文件（日粒度分片）。
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np
import pandas as pd

from aipha.data.stream import Bar, BarFrequency

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# 配置
# ------------------------------------------------------------------

@dataclass
class MinuteProviderConfig:
    """分钟数据 Provider 配置。

    Attributes:
        cache_dir: Parquet 缓存目录
        freq: 默认K线频率
        market_open: 开盘时间
        market_close: 收盘时间
        lunch_start: 午休开始
        lunch_end: 午休结束
    """

    cache_dir: str = ""
    freq: BarFrequency = BarFrequency.MIN_1
    market_open: time = time(9, 30)
    market_close: time = time(15, 0)
    lunch_start: time = time(11, 30)
    lunch_end: time = time(13, 0)


# ------------------------------------------------------------------
# 抽象接口
# ------------------------------------------------------------------

@runtime_checkable
class MinuteBarProvider(Protocol):
    """分钟级行情数据获取协议。"""

    @abstractmethod
    def get_minute_bars(
        self,
        symbol: str,
        trade_date: date,
        freq: BarFrequency = BarFrequency.MIN_1,
    ) -> list[Bar]:
        """获取指定标的在某交易日的分钟级K线。

        Args:
            symbol: 标的代码（如 '123456.SZ'）
            trade_date: 交易日
            freq: K线频率

        Returns:
            按时间升序排列的 Bar 列表
        """
        ...

    @abstractmethod
    def get_minute_bars_range(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        freq: BarFrequency = BarFrequency.MIN_1,
    ) -> pd.DataFrame:
        """获取日期范围内的分钟K线 DataFrame。

        Returns:
            columns: [symbol, timestamp, open, high, low,
                      close, volume, amount, freq]
        """
        ...

    @abstractmethod
    def available_dates(self, symbol: str) -> list[date]:
        """返回该标的已有分钟数据的交易日列表。"""
        ...


# ------------------------------------------------------------------
# 内存缓存
# ------------------------------------------------------------------

class MinuteDataCache:
    """分钟级数据按日分片内存缓存。

    键格式: ``(symbol, date, freq)``。
    支持 Parquet 文件持久化（按日分片）。
    """

    def __init__(self, config: MinuteProviderConfig | None = None) -> None:
        self._config = config or MinuteProviderConfig()
        self._cache: dict[tuple[str, date, BarFrequency], list[Bar]] = {}
        cache_dir = self._config.cache_dir
        if not cache_dir:
            from aipha.config import get_cache_dir
            cache_dir = str(get_cache_dir() / "minute")
        self._cache_dir = Path(cache_dir)

    @property
    def size(self) -> int:
        """当前缓存数量。"""
        return len(self._cache)

    def get(
        self,
        symbol: str,
        trade_date: date,
        freq: BarFrequency = BarFrequency.MIN_1,
    ) -> list[Bar] | None:
        """查询缓存，未命中返回 None。"""
        return self._cache.get((symbol, trade_date, freq))

    def put(
        self,
        symbol: str,
        trade_date: date,
        bars: list[Bar],
        freq: BarFrequency = BarFrequency.MIN_1,
    ) -> None:
        """写入缓存。"""
        self._cache[(symbol, trade_date, freq)] = bars

    def clear(self) -> None:
        """清空缓存。"""
        self._cache.clear()

    # -- Parquet 持久化 --

    def _parquet_path(
        self, symbol: str, trade_date: date, freq: BarFrequency,
    ) -> Path:
        """生成 Parquet 文件路径: cache_dir/SYMBOL/YYYYMMDD_FREQ.parquet"""
        sym_dir = self._cache_dir / symbol.replace(".", "_")
        fname = f"{trade_date:%Y%m%d}_{freq.value}.parquet"
        return sym_dir / fname

    def save_to_parquet(
        self, symbol: str, trade_date: date, freq: BarFrequency,
    ) -> Path | None:
        """将缓存中某 (symbol, date, freq) 写入 Parquet。"""
        bars = self.get(symbol, trade_date, freq)
        if not bars:
            return None
        path = self._parquet_path(symbol, trade_date, freq)
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            {
                "symbol": b.symbol,
                "timestamp": b.timestamp,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
                "amount": b.amount,
            }
            for b in bars
        ]
        pd.DataFrame(rows).to_parquet(path, index=False)
        logger.debug("Saved %d bars → %s", len(bars), path)
        return path

    def load_from_parquet(
        self, symbol: str, trade_date: date, freq: BarFrequency,
    ) -> list[Bar] | None:
        """从 Parquet 加载到缓存，返回 Bar 列表或 None。"""
        path = self._parquet_path(symbol, trade_date, freq)
        if not path.exists():
            return None
        df = pd.read_parquet(path)
        bars = [
            Bar(
                symbol=row["symbol"],
                timestamp=pd.Timestamp(row["timestamp"]).to_pydatetime(),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                amount=float(row.get("amount", 0.0)),
                frequency=freq,
            )
            for _, row in df.iterrows()
        ]
        self.put(symbol, trade_date, bars, freq)
        return bars
