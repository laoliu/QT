#!/usr/bin/env python3
"""Bar 数据批量预取脚本 — 大幅降低 20 年回测的数据下载时间

优化原理：
- 原始逻辑：per stock × per month → 300 stocks × 240 months = 72,000 API calls (~10h)
- 优化逻辑：per stock 一次性拉取全部日线 → 300 API calls (~3min)
- 结果按月拆分存入缓存，与 TuShareProBarProvider._load_bars_monthly 兼容

用法:
    python scripts/prefetch_bars.py --index 000300.SH --start 20050104 --end 20241231
    python scripts/prefetch_bars.py --symbols 000001.SZ,600000.SH --start 20100101 --end 20241231
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def _iter_months(start: date, end: date):
    """遍历覆盖的 (year, month) 对。"""
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m > 12:
            m = 1
            y += 1


def _count_cached_months(cache_dir: Path, symbol: str, start: date, end: date,
                         frequency: str = "daily", adj: str = "qfq") -> tuple[int, int]:
    """统计已缓存和总月份数。"""
    total = 0
    cached = 0
    for y, m in _iter_months(start, end):
        total += 1
        path = cache_dir / f"{symbol}_{y:04d}{m:02d}_{frequency}_{adj}.parquet"
        if path.exists():
            cached += 1
    return cached, total


def _get_index_members(index_code: str, trade_date: str | None = None) -> list[str]:
    """获取指数成分股列表。"""
    from aipha.data.tushare_equity_provider import TuShareEquityProvider
    provider = TuShareEquityProvider(cache_dir="data/cache/equity_tushare")
    df = provider.load_index_members(index_code, trade_date=trade_date)
    if df is not None and not df.empty:
        return df["con_code"].unique().tolist()
    return []


def _get_all_active_stocks() -> list[str]:
    """获取所有活跃股票。"""
    from aipha.data.tushare_equity_provider import TuShareEquityProvider
    provider = TuShareEquityProvider(cache_dir="data/cache/equity_tushare")
    df = provider.load_stock_basic()
    if df is not None and not df.empty:
        active = df[df["list_status"] == "L"]
        return active["ts_code"].tolist() if "ts_code" in active.columns else active.index.tolist()
    return []


def main():
    parser = argparse.ArgumentParser(description="Bar 数据批量预取")
    parser.add_argument("--index", default="000300.SH", help="指数代码")
    parser.add_argument("--symbols", type=str, default=None, help="逗号分隔的股票代码列表")
    parser.add_argument("--start", default="20050104", help="起始日期 YYYYMMDD")
    parser.add_argument("--end", default="20241231", help="结束日期 YYYYMMDD")
    parser.add_argument("--cache-dir", default="data/cache/tushare", help="Bar 缓存目录")
    parser.add_argument("--adj", default="qfq", help="复权方式: qfq/hfq/none")
    parser.add_argument("--frequency", default="daily", help="频率: daily")
    parser.add_argument("--skip-cached", action="store_true", default=True,
                        help="已完全缓存则跳过")
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    start_dt = datetime.strptime(args.start, "%Y%m%d").date()
    end_dt = datetime.strptime(args.end, "%Y%m%d").date()

    # 获取股票列表
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",")]
    else:
        logger.info(f"获取指数成分股: {args.index}")
        symbols = _get_index_members(args.index)
        if not symbols:
            logger.warning(f"未获取到 {args.index} 成分股，使用全市场")
            symbols = _get_all_active_stocks()

    total_months_needed = len(list(_iter_months(start_dt, end_dt)))
    logger.info(f"股票数: {len(symbols)}")
    logger.info(f"日期范围: {args.start} ~ {args.end} ({total_months_needed} 个月)")

    # 检查哪些股票需要下载
    need_download: list[tuple[str, int, int]] = []
    skip_count = 0
    for sym in symbols:
        cached, total = _count_cached_months(
            cache_dir, sym, start_dt, end_dt, args.frequency, args.adj,
        )
        if cached >= total:
            skip_count += 1
        else:
            need_download.append((sym, cached, total))

    logger.info(f"已完全缓存: {skip_count} 只, 需下载: {len(need_download)} 只")

    if not need_download:
        logger.info("所有 bar 数据已缓存，无需下载！")
        return

    # 按缺失月份数降序，优先下载数据最缺的
    need_download.sort(key=lambda x: x[1] - x[2])

    total_missing_months = sum(t - c for _, c, t in need_download)
    logger.info(f"总缺失月份: {total_missing_months}")
    est_seconds = len(need_download) * 0.6  # 每只股票 1 次 API 调用
    logger.info(f"预计耗时（每股一次全量拉取）: {est_seconds/60:.1f} 分钟")

    # 初始化 TuShare gateway
    from aipha.data.tushare_gateway import TuShareGateway
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        logger.error("TUSHARE_TOKEN 未配置")
        sys.exit(1)

    gateway = TuShareGateway(token=token)

    downloaded = 0
    failed = 0
    t0 = time.time()

    for i, (sym, cached, total) in enumerate(need_download):
        missing = total - cached
        if (i + 1) % 10 == 0 or i == 0 or i == len(need_download) - 1:
            elapsed = time.time() - t0
            remaining = (len(need_download) - i) * (elapsed / max(i, 1))
            logger.info(
                f"[{i+1}/{len(need_download)}] {sym}: {cached}/{total} 缓存, "
                f"缺 {missing} 月, 剩余 ~{remaining/60:.1f} 分"
            )

        try:
            # 全量拉取该股票的所有日线数据
            df = gateway.pro_bar(
                ts_code=sym,
                start_date=args.start,
                end_date=args.end,
                adj=args.adj,
                asset="E",
            )

            if df is None or df.empty:
                logger.warning(f"  {sym}: 无数据（可能退市或未上市）")
                continue

            # 按时间排序
            if "trade_date" in df.columns:
                df = df.sort_values("trade_date").reset_index(drop=True)

            # 按月拆分存入缓存
            df["_month_key"] = df["trade_date"].str[:6]
            for month_key, grp in df.groupby("_month_key"):
                y = int(month_key[:4])
                m = int(month_key[4:6])
                cache_path = cache_dir / f"{sym}_{y:04d}{m:02d}_{args.frequency}_{args.adj}.parquet"
                if not cache_path.exists():
                    grp_clean = grp.drop(columns=["_month_key"])
                    grp_clean.to_parquet(cache_path, index=False)

            downloaded += 1

        except Exception as e:
            logger.error(f"  {sym}: 失败 - {e}")
            failed += 1
            time.sleep(2)  # 失败后等待

    total_time = time.time() - t0
    logger.info(f"\n{'='*60}")
    logger.info(f"Bar 数据预取完成！")
    logger.info(f"  成功: {downloaded} 只")
    logger.info(f"  失败: {failed} 只")
    logger.info(f"  耗时: {total_time/60:.1f} 分钟")
    logger.info(f"{'='*60}")


if __name__ == "__main__":
    main()
