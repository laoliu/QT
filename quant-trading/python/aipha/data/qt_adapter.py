"""QT 数据提供者适配器。

将 QT 现有的 data_providers（AKShare/TuShare/YFinance）包装为
aipha 的 BarProvider 协议，实现数据层复用。
"""
from __future__ import annotations

import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from aipha.data.provider_base import BarProvider, DataFingerprint
from aipha.domain.models import Bar

logger = logging.getLogger(__name__)

# 确保 QT 的 python/ 目录在 sys.path 中
_qt_python_dir = str(Path(__file__).parent.parent.parent)
if _qt_python_dir not in sys.path:
    sys.path.insert(0, _qt_python_dir)


class QTAKShareAdapter:
    """将 QT 的 akshare_provider 包装为 BarProvider。"""

    def __init__(self, adjust: str = "qfq"):
        self._adjust = adjust

    def fingerprint(self) -> DataFingerprint:
        return DataFingerprint(
            provider="qt_akshare",
            snapshot="latest",
            calendar="cn_stock_fallback",
            adjust=self._adjust,
            schema="v1",
        )

    def load_bars(self, *, symbol: str, start: date, end: date, frequency: str) -> list[Bar]:
        from data_providers.akshare_provider import get_stock_klines

        period_map = {"1d": "daily", "1w": "weekly", "1m": "1"}
        period = period_map.get(frequency, "daily")
        adjust_map = {"qfq": "qfq", "hfq": "hfq", "none": ""}
        adj = adjust_map.get(self._adjust, "qfq")

        raw = get_stock_klines(
            symbol=symbol,
            period=period,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust=adj,
        )

        if not raw or not isinstance(raw, list):
            return []

        bars: list[Bar] = []
        for item in raw:
            try:
                dt = _parse_datetime(item.get("date") or item.get("日期"))
                bars.append(Bar(
                    symbol=symbol,
                    start_ts=dt,
                    end_ts=dt,
                    open=float(item.get("open", item.get("开盘", 0))),
                    high=float(item.get("high", item.get("最高", 0))),
                    low=float(item.get("low", item.get("最低", 0))),
                    close=float(item.get("close", item.get("收盘", 0))),
                    volume=float(item.get("volume", item.get("成交量", 0))),
                    prev_close=_safe_float(item.get("prev_close")),
                ))
            except Exception as e:
                logger.debug("跳过无效 bar: %s", e)
                continue

        return sorted(bars, key=lambda b: b.start_ts)


class QTTuShareAdapter:
    """将 QT 的 tushare_provider 包装为 BarProvider。"""

    def __init__(self, adjust: str = "qfq"):
        self._adjust = adjust

    def fingerprint(self) -> DataFingerprint:
        return DataFingerprint(
            provider="qt_tushare",
            snapshot="latest",
            calendar="cn_stock_fallback",
            adjust=self._adjust,
            schema="v1",
        )

    def load_bars(self, *, symbol: str, start: date, end: date, frequency: str) -> list[Bar]:
        from data_providers.tushare_provider import get_stock_klines

        raw = get_stock_klines(
            symbol=symbol,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust=self._adjust,
        )

        if not raw or not isinstance(raw, list):
            return []

        bars: list[Bar] = []
        for item in raw:
            try:
                dt = _parse_datetime(item.get("trade_date") or item.get("date"))
                bars.append(Bar(
                    symbol=symbol,
                    start_ts=dt,
                    end_ts=dt,
                    open=float(item.get("open", 0)),
                    high=float(item.get("high", 0)),
                    low=float(item.get("low", 0)),
                    close=float(item.get("close", 0)),
                    volume=float(item.get("vol", item.get("volume", 0))),
                    prev_close=_safe_float(item.get("pre_close")),
                ))
            except Exception as e:
                logger.debug("跳过无效 bar: %s", e)
                continue

        return sorted(bars, key=lambda b: b.start_ts)


def create_provider(source: str = "akshare", adjust: str = "qfq") -> BarProvider:
    """工厂方法：根据 source 名称创建适配器。"""
    if source in ("tushare", "tushare_pro", "qt_tushare"):
        return QTTuShareAdapter(adjust=adjust)
    return QTAKShareAdapter(adjust=adjust)


def _parse_datetime(val: Any) -> datetime:
    if isinstance(val, datetime):
        return val
    if isinstance(val, date):
        return datetime(val.year, val.month, val.day)
    s = str(val).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"无法解析日期: {val!r}")


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
