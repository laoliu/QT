#!/usr/bin/env python3
"""预取指数 历史成分并集 的全部 bar + fina_indicator 数据

用法:
    python scripts/prefetch_union.py --index 000905.SH --start 20160101 --end 20241231
    python scripts/prefetch_union.py --index 000852.SH --start 20160101 --end 20241231
    python scripts/prefetch_union.py --index 000905.SH,000852.SH --start 20160101 --end 20241231
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
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


def get_union_members(index_code: str, start_year: int, end_year: int) -> list[str]:
    """获取指数历史成分并集（利用缓存）"""
    from aipha.data.tushare_equity_provider import TuShareEquityProvider
    provider = TuShareEquityProvider(cache_dir="data/cache/equity_tushare")
    members = provider.load_all_index_members_union(
        index_code=index_code,
        start_year=start_year,
        end_year=end_year,
        use_cache=True,
    )
    return members


def check_bar_cache(symbols: list[str], cache_dir: Path, start: str, end: str,
                    frequency: str = "daily", adj: str = "qfq") -> tuple[list[str], list[str]]:
    """检查哪些股票缺少 bar 缓存"""
    from datetime import date as dt_date
    start_dt = dt_date(int(start[:4]), int(start[4:6]), int(start[6:8]))
    end_dt = dt_date(int(end[:4]), int(end[4:6]), int(end[6:8]))

    # 计算应有的月份
    months = []
    y, m = start_dt.year, start_dt.month
    while (y, m) <= (end_dt.year, end_dt.month):
        months.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1

    total_months = len(months)
    cached_symbols = []
    missing_symbols = []

    for sym in symbols:
        cached_count = sum(
            1 for y, m in months
            if (cache_dir / f"{sym}_{y:04d}{m:02d}_{frequency}_{adj}.parquet").exists()
        )
        if cached_count >= total_months:
            cached_symbols.append(sym)
        else:
            missing_symbols.append(sym)

    return cached_symbols, missing_symbols


def check_fina_cache(symbols: list[str], cache_dir: Path) -> tuple[list[str], list[str]]:
    """检查哪些股票缺少 fina_indicator 缓存"""
    cached = []
    missing = []
    for sym in symbols:
        fpath = cache_dir / f"fina_indicator_{sym.replace('.', '_')}.parquet"
        if fpath.exists() and fpath.stat().st_size > 0:
            cached.append(sym)
        else:
            missing.append(sym)
    return cached, missing


def prefetch_bars(symbols: list[str], start: str, end: str, cache_dir: str, adj: str = "qfq"):
    """调用 prefetch_bars.py 下载 bar 数据"""
    if not symbols:
        logger.info("Bar 数据: 全部已缓存")
        return
    sym_str = ",".join(symbols)
    cmd = [
        sys.executable, "scripts/prefetch_bars.py",
        "--symbols", sym_str,
        "--start", start,
        "--end", end,
        "--cache-dir", cache_dir,
        "--adj", adj,
    ]
    logger.info(f"开始下载 {len(symbols)} 只股票的 bar 数据...")
    result = subprocess.run(cmd, cwd=str(_REPO_ROOT))
    if result.returncode != 0:
        logger.error(f"prefetch_bars 失败, returncode={result.returncode}")


def prefetch_fina(symbols: list[str], cache_dir: str):
    """下载 fina_indicator 数据"""
    if not symbols:
        logger.info("Fina indicator: 全部已缓存")
        return

    from aipha.data.tushare_equity_provider import TuShareEquityProvider
    provider = TuShareEquityProvider(cache_dir=cache_dir)

    logger.info(f"开始下载 {len(symbols)} 只股票的 fina_indicator...")
    success = 0
    failed = 0
    t0 = time.time()

    for i, sym in enumerate(symbols):
        try:
            provider.load_fina_indicator(sym)
            success += 1
        except Exception as e:
            logger.warning(f"  {sym}: fina_indicator 失败 - {e}")
            failed += 1
            time.sleep(1)

        if (i + 1) % 50 == 0:
            elapsed = time.time() - t0
            logger.info(f"  fina_indicator: {i+1}/{len(symbols)} (成功={success}, 失败={failed}, 耗时={elapsed:.0f}s)")

    total_time = time.time() - t0
    logger.info(f"Fina indicator 完成: 成功={success}, 失败={failed}, 耗时={total_time/60:.1f}分钟")


def main():
    parser = argparse.ArgumentParser(description="指数历史成分并集数据预取")
    parser.add_argument("--index", required=True, help="指数代码（逗号分隔多个）")
    parser.add_argument("--start", default="20160101", help="起始日期")
    parser.add_argument("--end", default="20241231", help="结束日期")
    parser.add_argument("--bar-cache", default="data/cache/tushare", help="Bar 缓存目录")
    parser.add_argument("--fina-cache", default="data/cache/equity_tushare", help="Fina 缓存目录")
    parser.add_argument("--adj", default="qfq", help="复权方式")
    parser.add_argument("--skip-bar", action="store_true", help="跳过 bar 预取")
    parser.add_argument("--skip-fina", action="store_true", help="跳过 fina 预取")
    args = parser.parse_args()

    indices = [idx.strip() for idx in args.index.split(",")]
    start_year = int(args.start[:4])
    end_year = int(args.end[:4])

    bar_cache = Path(args.bar_cache)
    fina_cache = Path(args.fina_cache)

    all_symbols = set()
    for idx in indices:
        logger.info(f"\n{'='*60}")
        logger.info(f"指数: {idx}")
        members = get_union_members(idx, start_year, end_year)
        logger.info(f"历史成分并集: {len(members)} 只")
        all_symbols.update(members)

    all_symbols = sorted(all_symbols)
    logger.info(f"\n{'='*60}")
    logger.info(f"全部唯一股票: {len(all_symbols)} 只")

    # 检查 bar 缓存
    bar_cached, bar_missing = check_bar_cache(all_symbols, bar_cache, args.start, args.end, adj=args.adj)
    logger.info(f"Bar 数据: 已缓存={len(bar_cached)}, 缺失={len(bar_missing)}")

    # 检查 fina 缓存
    fina_cached, fina_missing = check_fina_cache(all_symbols, fina_cache)
    logger.info(f"Fina indicator: 已缓存={len(fina_cached)}, 缺失={len(fina_missing)}")

    # 预取 fina（较快）
    if not args.skip_fina and fina_missing:
        prefetch_fina(fina_missing, str(fina_cache))

    # 预取 bar（较慢）
    if not args.skip_bar and bar_missing:
        prefetch_bars(bar_missing, args.start, args.end, args.bar_cache, args.adj)

    logger.info(f"\n{'='*60}")
    logger.info("全部预取完成！")


if __name__ == "__main__":
    main()
