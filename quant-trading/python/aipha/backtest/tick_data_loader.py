"""Tick 数据加载器 — 将 QMT tick 数据适配为回测引擎所需格式。

支持两种数据源:
  1. Parquet 文件（预下载的历史 tick 数据）
  2. QMT bridge HTTP 接口（实时下载）

数据格式:
  {date: {symbol: [Tick, Tick, ...]}}  按交易日 → 按标的 → 按时间排序的 tick 列表
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

from aipha.domain.models import DepthLevel, Tick

logger = logging.getLogger(__name__)

# 默认 tick 缓存目录
_DEFAULT_TICK_CACHE_DIR = os.environ.get(
    "QT_TICK_CACHE_DIR",
    str(Path(os.environ.get("QT_DATA_ROOT", "G:/qtdata")) / "cache" / "tick"),
)


def _parse_tick_row(symbol: str, row: dict) -> Tick | None:
    """将单条 raw tick dict 转为 Tick 对象。"""
    try:
        ts = row.get("timestamp") or row.get("time") or row.get("datetime")
        if ts is None:
            return None

        if isinstance(ts, (int, float)):
            # 毫秒时间戳
            dt = datetime.fromtimestamp(ts / 1000 if ts > 1e12 else ts)
        elif isinstance(ts, str):
            # "20260424 09:30:01.500" 或 "2026-04-24 09:30:01"
            for fmt in ("%Y%m%d %H:%M:%S.%f", "%Y%m%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
                try:
                    dt = datetime.strptime(ts, fmt)
                    break
                except ValueError:
                    continue
            else:
                return None
        elif isinstance(ts, datetime):
            dt = ts
        else:
            return None

        price = float(row.get("price") or row.get("lastPrice") or 0)
        if price <= 0:
            return None

        # 解析盘口
        bid_levels: list[DepthLevel] = []
        ask_levels: list[DepthLevel] = []

        bid_prices = row.get("bidPrice", [])
        bid_vols = row.get("bidVol", [])
        ask_prices = row.get("askPrice", [])
        ask_vols = row.get("askVol", [])

        for i in range(min(5, len(bid_prices))):
            p = float(bid_prices[i]) if i < len(bid_prices) else 0
            v = int(bid_vols[i]) if i < len(bid_vols) else 0
            if p > 0:
                bid_levels.append(DepthLevel(price=p, volume=v))

        for i in range(min(5, len(ask_prices))):
            p = float(ask_prices[i]) if i < len(ask_prices) else 0
            v = int(ask_vols[i]) if i < len(ask_vols) else 0
            if p > 0:
                ask_levels.append(DepthLevel(price=p, volume=v))

        # 推断方向
        direction = str(row.get("direction", ""))
        if not direction and bid_levels and ask_levels:
            if price >= ask_levels[0].price:
                direction = "buy"
            elif price <= bid_levels[0].price:
                direction = "sell"
            else:
                direction = "neutral"

        return Tick(
            symbol=symbol,
            timestamp=dt,
            price=price,
            volume=int(row.get("volume") or row.get("vol") or 0),
            amount=float(row.get("amount") or 0),
            open=float(row.get("open") or 0),
            high=float(row.get("high") or 0),
            low=float(row.get("low") or 0),
            prev_close=float(row.get("lastClose") or row.get("prevClose") or row.get("prev_close") or 0),
            bid_levels=bid_levels,
            ask_levels=ask_levels,
            direction=direction,
            trade_count=int(row.get("transactionNum") or row.get("tradeCount") or row.get("trade_count") or 0),
        )
    except Exception as e:
        logger.debug("parse tick failed: %s", e)
        return None


def load_ticks_from_parquet(
    symbols: list[str],
    start: date,
    end: date,
    cache_dir: str | None = None,
) -> dict[date, dict[str, list[Tick]]]:
    """从 Parquet 文件加载历史 tick 数据。

    文件组织: {cache_dir}/{symbol}/{YYYYMMDD}.parquet
    每个 Parquet 文件包含一天的 tick 数据。
    """
    import pandas as pd

    tick_dir = Path(cache_dir or _DEFAULT_TICK_CACHE_DIR)
    result: dict[date, dict[str, list[Tick]]] = {}

    for symbol in symbols:
        sym_dir = tick_dir / symbol.replace(".", "_")
        if not sym_dir.exists():
            # 也尝试 6 位代码目录
            short_code = symbol.split(".")[0]
            sym_dir = tick_dir / short_code
            if not sym_dir.exists():
                logger.warning("tick 数据目录不存在: %s", sym_dir)
                continue

        current = start
        while current <= end:
            fname = f"{current.strftime('%Y%m%d')}.parquet"
            fpath = sym_dir / fname
            if fpath.exists():
                try:
                    df = pd.read_parquet(fpath)
                    ticks: list[Tick] = []
                    for _, row in df.iterrows():
                        tick = _parse_tick_row(symbol, row.to_dict())
                        if tick:
                            ticks.append(tick)
                    if ticks:
                        ticks.sort(key=lambda t: t.timestamp)
                        if current not in result:
                            result[current] = {}
                        result[current][symbol] = ticks
                except Exception as e:
                    logger.warning("读取 tick parquet 失败 %s: %s", fpath, e)

            # 下一天（简单递增，非交易日会跳过）
            from datetime import timedelta
            current += timedelta(days=1)

    logger.info("Parquet tick 加载完成: %d 交易日, %d 只股票", len(result), len(symbols))
    return result


def load_ticks_from_bridge(
    symbols: list[str],
    start: date,
    end: date,
    bridge_url: str = "http://127.0.0.1:58088",
    save_cache: bool = True,
    cache_dir: str | None = None,
) -> dict[date, dict[str, list[Tick]]]:
    """通过 QMT bridge 下载历史 tick 数据。

    调用 bridge 的 /tick_download 端点，按天下载并可选缓存为 Parquet。
    """
    import json
    import urllib.request

    tick_cache = Path(cache_dir or _DEFAULT_TICK_CACHE_DIR)
    result: dict[date, dict[str, list[Tick]]] = {}

    # 将 symbols 转为 QMT 格式
    qmt_codes = []
    code_map = {}  # qmt_code -> original symbol
    for sym in symbols:
        if "." in sym and sym.split(".")[-1] in ("SH", "SZ"):
            qmt_code = sym
        elif sym.startswith(("6", "5")):
            qmt_code = f"{sym}.SH"
        else:
            qmt_code = f"{sym}.SZ"
        qmt_codes.append(qmt_code)
        code_map[qmt_code] = sym

    from datetime import timedelta
    current = start
    while current <= end:
        date_str = current.strftime("%Y%m%d")

        # 先检查缓存
        all_cached = True
        for sym in symbols:
            sym_dir = tick_cache / sym.replace(".", "_")
            fpath = sym_dir / f"{date_str}.parquet"
            if not fpath.exists():
                all_cached = False
                break

        if all_cached:
            # 从缓存加载
            day_data = load_ticks_from_parquet(symbols, current, current, str(tick_cache))
            if current in day_data:
                result[current] = day_data[current]
            current += timedelta(days=1)
            continue

        # 通过 bridge 下载
        try:
            payload = json.dumps({
                "codes": qmt_codes,
                "date": date_str,
            }).encode("utf-8")

            req = urllib.request.Request(
                f"{bridge_url}/tick_download",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            if data.get("status") != "ok":
                logger.warning("bridge tick_download 失败 %s: %s", date_str, data.get("error"))
                current += timedelta(days=1)
                continue

            ticks_data = data.get("data", {})
            for qmt_code, raw_ticks in ticks_data.items():
                sym = code_map.get(qmt_code, qmt_code.split(".")[0])
                ticks: list[Tick] = []
                for row in raw_ticks:
                    tick = _parse_tick_row(sym, row)
                    if tick:
                        ticks.append(tick)
                if ticks:
                    ticks.sort(key=lambda t: t.timestamp)
                    if current not in result:
                        result[current] = {}
                    result[current][sym] = ticks

                    # 缓存到 Parquet
                    if save_cache:
                        _save_ticks_parquet(sym, current, raw_ticks, tick_cache)

        except Exception as e:
            logger.warning("bridge tick_download 异常 %s: %s", date_str, e)

        current += timedelta(days=1)

    logger.info("Bridge tick 加载完成: %d 交易日, %d 只股票", len(result), len(symbols))
    return result


def _save_ticks_parquet(
    symbol: str,
    trade_date: date,
    raw_ticks: list[dict],
    cache_dir: Path,
) -> None:
    """将 tick 数据缓存为 Parquet 文件。"""
    try:
        import pandas as pd

        sym_dir = cache_dir / symbol.replace(".", "_")
        sym_dir.mkdir(parents=True, exist_ok=True)
        fpath = sym_dir / f"{trade_date.strftime('%Y%m%d')}.parquet"

        df = pd.DataFrame(raw_ticks)
        df.to_parquet(fpath, index=False, compression="zstd")
        logger.debug("tick 缓存已保存: %s", fpath)
    except Exception as e:
        logger.warning("保存 tick parquet 失败: %s", e)


def load_ticks_by_date(
    symbols: list[str],
    start: date,
    end: date,
    source: str = "parquet",
    cache_dir: str | None = None,
    bridge_url: str = "http://127.0.0.1:58088",
) -> dict[date, dict[str, list[Tick]]]:
    """统一 tick 数据加载入口。

    Args:
        symbols: 股票代码列表
        start: 开始日期
        end: 结束日期
        source: 数据源 ("parquet", "bridge", "auto")
        cache_dir: tick 缓存目录
        bridge_url: QMT bridge 地址

    Returns:
        {date: {symbol: [Tick, ...]}} 按时间排序
    """
    if source == "parquet":
        return load_ticks_from_parquet(symbols, start, end, cache_dir)
    elif source == "bridge":
        return load_ticks_from_bridge(symbols, start, end, bridge_url, save_cache=True, cache_dir=cache_dir)
    elif source == "auto":
        # 先尝试 Parquet，不足的部分用 bridge 补充
        result = load_ticks_from_parquet(symbols, start, end, cache_dir)
        if not result:
            result = load_ticks_from_bridge(symbols, start, end, bridge_url, save_cache=True, cache_dir=cache_dir)
        return result
    else:
        raise ValueError(f"不支持的 tick 数据源: {source}")
