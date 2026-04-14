"""统一数据加载器 — 将 QT 数据源适配为回测引擎所需的 bars_by_date 格式。"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from aipha.data.qt_adapter import create_provider
from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


def load_bars_by_date(
    symbols: list[str],
    start: date,
    end: date,
    source: str = "akshare",
    frequency: str = "1d",
) -> dict[date, dict[str, Bar]]:
    """加载数据并组织为 {date: {symbol: Bar}} 的格式。

    Args:
        symbols: 股票代码列表
        start: 开始日期
        end: 结束日期
        source: 数据源 ("akshare" / "tushare")
        frequency: K线频率

    Returns:
        {trade_date: {symbol: Bar}}
    """
    provider = create_provider(source)
    bars_by_date: dict[date, dict[str, Bar]] = {}

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
