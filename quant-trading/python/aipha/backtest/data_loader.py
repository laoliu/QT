"""统一数据加载器 — 将 QT 数据源适配为回测引擎所需的 bars_by_date 格式。"""
from __future__ import annotations

import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from aipha.data.qt_adapter import create_provider
from aipha.domain.models import Bar

logger = logging.getLogger(__name__)

# 确保 data_providers 可导入
_qt_python_dir = str(Path(__file__).parent.parent.parent)
if _qt_python_dir not in sys.path:
    sys.path.insert(0, _qt_python_dir)


def _bars_from_raw(raw_bars: list[dict], symbol: str) -> list[Bar]:
    """将 get_klines_batch 返回的 raw dict 转为 Bar 对象。"""
    bars: list[Bar] = []
    for item in raw_bars:
        try:
            ts = item.get("timestamp")
            if ts is not None:
                dt = datetime.fromtimestamp(ts / 1000)
            else:
                continue
            bars.append(Bar(
                symbol=symbol,
                start_ts=dt,
                end_ts=dt,
                open=float(item.get("open", 0)),
                high=float(item.get("high", 0)),
                low=float(item.get("low", 0)),
                close=float(item.get("close", 0)),
                volume=float(item.get("volume", 0)),
                prev_close=None,
            ))
        except Exception:
            continue
    return bars


def load_bars_by_date(
    symbols: list[str],
    start: date,
    end: date,
    source: str = "akshare",
    frequency: str = "1d",
) -> dict[date, dict[str, Bar]]:
    """加载数据并组织为 {date: {symbol: Bar}} 的格式。

    优先使用批量接口 (get_klines_batch)，回退到逐只加载。
    """
    bars_by_date: dict[date, dict[str, Bar]] = {}

    # 尝试批量加载 (Parquet → SQLite → QMT batch → per-stock fallback)
    batch_loaded = False
    if frequency == "1d":
        try:
            from data_providers.akshare_provider import get_klines_batch
            start_str = start.strftime("%Y-%m-%d")
            end_str = end.strftime("%Y-%m-%d")
            logger.info("批量加载 %d 只股票 K线: %s ~ %s", len(symbols), start_str, end_str)
            raw_result = get_klines_batch(symbols=symbols, start=start_str, end=end_str)
            for sym, raw_bars in raw_result.items():
                for bar in _bars_from_raw(raw_bars, sym):
                    d = bar.start_ts.date()
                    if d not in bars_by_date:
                        bars_by_date[d] = {}
                    bars_by_date[d][sym] = bar
            batch_loaded = True
            logger.info("批量加载完成: %d 交易日, %d/%d 只股票", len(bars_by_date), len(raw_result), len(symbols))
        except Exception as e:
            logger.warning("批量加载失败，回退到逐只: %s", e)

    # 回退：逐只加载
    if not batch_loaded:
        provider = create_provider(source)
        for symbol in symbols:
            try:
                bars = provider.load_bars(symbol=symbol, start=start, end=end, frequency=frequency)
                for bar in bars:
                    d = bar.start_ts.date() if isinstance(bar.start_ts, datetime) else bar.start_ts
                    if d not in bars_by_date:
                        bars_by_date[d] = {}
                    bars_by_date[d][symbol] = bar
            except Exception as e:
                logger.warning(f"Failed to load {symbol}: {e}")
        logger.info(f"Loaded {len(bars_by_date)} trading days, {len(symbols)} symbols")

    return bars_by_date


def load_bars_dataframe(
    symbols: list[str],
    start: date,
    end: date,
    source: str = "akshare",
) -> Any:
    """加载数据并返回 pandas DataFrame（含 datetime, symbol, OHLCV）。

    用于 Alpha101 等需要面板 DataFrame 的因子计算。
    """
    import pandas as pd

    provider = create_provider(source)
    records = []

    for symbol in symbols:
        try:
            bars = provider.load_bars(symbol=symbol, start=start, end=end, frequency="1d")
            for bar in bars:
                d = bar.start_ts.date() if isinstance(bar.start_ts, datetime) else bar.start_ts
                records.append({
                    "datetime": d,
                    "symbol": symbol,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                })
        except Exception as e:
            logger.warning(f"Failed to load {symbol}: {e}")

    df = pd.DataFrame(records)
    if not df.empty:
        df.sort_values(["symbol", "datetime"], inplace=True)
        df.reset_index(drop=True, inplace=True)
    return df
