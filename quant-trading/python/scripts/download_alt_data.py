"""批量下载替代数据 (moneyflow/margin/cyq/analyst) 到本地缓存。

支持断点续传: 已存在的 parquet 文件会自动跳过。
按日拉取，逐日缓存到 data/cache/alternative/ 目录。

Usage:
    python scripts/download_alt_data.py                    # 下载全部 4 类
    python scripts/download_alt_data.py --types moneyflow  # 只下载资金流
    python scripts/download_alt_data.py --start 20220801   # 指定起始日期
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_START = "20150105"
DEFAULT_END = "20260312"
CACHE_DIR = "data/cache/alternative"

ALL_TYPES = ["moneyflow", "margin", "cyq", "analyst"]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Download alt data to local cache")
    p.add_argument("--start", default=DEFAULT_START, help="Start date YYYYMMDD")
    p.add_argument("--end", default=DEFAULT_END, help="End date YYYYMMDD")
    p.add_argument("--cache-dir", default=CACHE_DIR)
    p.add_argument(
        "--types", nargs="*", default=ALL_TYPES,
        choices=ALL_TYPES, help="Data types to download",
    )
    return p.parse_args()


def get_trading_days(start: str, end: str) -> list[date]:
    """获取交易日列表。"""
    from aipha.data.calendar import CNStockCalendar
    cal = CNStockCalendar()
    sd = date(int(start[:4]), int(start[4:6]), int(start[6:8]))
    ed = date(int(end[:4]), int(end[4:6]), int(end[6:8]))
    return cal.get_trading_days(sd, ed)


def download_moneyflow(
    provider, dates: list[date], cache_dir: Path,
) -> dict[str, int]:
    """下载资金流数据，返回统计。"""
    total = len(dates)
    skipped = 0
    downloaded = 0
    failed = 0
    for i, d in enumerate(dates):
        ds = d.strftime("%Y%m%d")
        cache_path = cache_dir / f"moneyflow_{ds}.parquet"
        if cache_path.exists() and cache_path.stat().st_size > 100:
            skipped += 1
            continue
        try:
            df = provider.load_moneyflow(ds)
            if len(df) > 0:
                downloaded += 1
            else:
                failed += 1
        except Exception as e:
            logger.warning("moneyflow %s failed: %s", ds, e)
            failed += 1
        if (i + 1) % 100 == 0 or i == total - 1:
            logger.info(
                "moneyflow progress: %d/%d (skip=%d, dl=%d, fail=%d)",
                i + 1, total, skipped, downloaded, failed,
            )
    return {"total": total, "skipped": skipped, "downloaded": downloaded, "failed": failed}


def download_margin(
    provider, dates: list[date], cache_dir: Path,
) -> dict[str, int]:
    """下载融资融券数据，返回统计。"""
    total = len(dates)
    skipped = 0
    downloaded = 0
    failed = 0
    for i, d in enumerate(dates):
        ds = d.strftime("%Y%m%d")
        cache_path = cache_dir / f"margin_{ds}.parquet"
        if cache_path.exists() and cache_path.stat().st_size > 100:
            skipped += 1
            continue
        try:
            df = provider.load_margin_detail(ds)
            if len(df) > 0:
                downloaded += 1
            else:
                failed += 1
        except Exception as e:
            logger.warning("margin %s failed: %s", ds, e)
            failed += 1
        if (i + 1) % 100 == 0 or i == total - 1:
            logger.info(
                "margin progress: %d/%d (skip=%d, dl=%d, fail=%d)",
                i + 1, total, skipped, downloaded, failed,
            )
    return {"total": total, "skipped": skipped, "downloaded": downloaded, "failed": failed}


def download_cyq(
    provider, dates: list[date], cache_dir: Path,
) -> dict[str, int]:
    """下载筹码分布数据，返回统计。"""
    total = len(dates)
    skipped = 0
    downloaded = 0
    failed = 0
    for i, d in enumerate(dates):
        ds = d.strftime("%Y%m%d")
        cache_path = cache_dir / f"chips_daily_{ds}.parquet"
        if cache_path.exists() and cache_path.stat().st_size > 100:
            skipped += 1
            continue
        try:
            df = provider.load_chips_bulk(ds)
            if len(df) > 0:
                downloaded += 1
            else:
                failed += 1
        except Exception as e:
            logger.warning("cyq %s failed: %s", ds, e)
            failed += 1
        if (i + 1) % 100 == 0 or i == total - 1:
            logger.info(
                "cyq progress: %d/%d (skip=%d, dl=%d, fail=%d)",
                i + 1, total, skipped, downloaded, failed,
            )
    return {"total": total, "skipped": skipped, "downloaded": downloaded, "failed": failed}


def download_analyst(
    provider, start: str, end: str, cache_dir: Path,
) -> dict[str, int]:
    """下载分析师预测数据 (月频)，返回统计。

    report_rc 端点物理限制：2次/分钟（与积分无关，服务端硬限）。
    处理方案：
      - max_retries=1：禁止网关在同一60s窗口内重试耗尽配额
      - 捕获限速错误后 sleep 35s，再重试当前月（不跳过）
      - 日配额超限：等待到次日 00:05 后重试
    """
    import pandas as pd
    start_dt = pd.Timestamp(start)
    end_dt = pd.Timestamp(end)
    current = start_dt.to_period("M")
    end_p = end_dt.to_period("M")
    total = 0
    skipped = 0
    downloaded = 0
    failed = 0
    while current <= end_p:
        total += 1
        ym = current.strftime("%Y%m")
        cache_path = cache_dir / f"analyst_{ym}.parquet"
        if cache_path.exists() and cache_path.stat().st_size > 100:
            skipped += 1
            current += 1
            continue
        try:
            m_start = current.start_time.strftime("%Y%m%d")
            m_end = current.end_time.strftime("%Y%m%d")
            # max_retries=1：网关只尝试1次，不在60s内重试消耗配额
            df = provider._gw.call(
                "report_rc", start_date=m_start, end_date=m_end,
                max_retries=1,
            )
            if df is not None and not df.empty:
                df.to_parquet(cache_path, index=False)
                downloaded += 1
                logger.info("analyst %s: %d rows", ym, len(df))
            else:
                failed += 1
                logger.warning("analyst %s: empty response", ym)
            current += 1
        except Exception as e:
            err_msg = str(e)
            if "每天" in err_msg or "daily" in err_msg.lower():
                import datetime as _dt
                now = _dt.datetime.now()
                tomorrow = (now + _dt.timedelta(days=1)).replace(
                    hour=0, minute=5, second=0, microsecond=0,
                )
                wait_secs = (tomorrow - now).total_seconds()
                logger.warning(
                    "analyst %s: daily limit, waiting %.0fs until %s",
                    ym, wait_secs, tomorrow.strftime("%Y-%m-%d %H:%M"),
                )
                time.sleep(wait_secs)
                # 重试本月，不推进
            elif "每小时" in err_msg:
                # 小时配额（10次/小时）：等待65分钟让小时窗口充分重置
                logger.warning("analyst %s: hourly limit, waiting 65 min ...", ym)
                time.sleep(65 * 60)
                # 重试本月，不推进
            elif any(kw in err_msg for kw in ("每分钟", "rate", "limit")):
                # 分钟限速（2次/分钟）：等待35s让分钟窗口重置后重试
                logger.warning("analyst %s: rate limited, retry in 35s ...", ym)
                time.sleep(35)
                # 重试本月，不推进
            else:
                failed += 1
                logger.warning("analyst %s failed: %s", ym, e)
                current += 1
    logger.info(
        "analyst done: total=%d, skip=%d, dl=%d, fail=%d",
        total, skipped, downloaded, failed,
    )
    return {
        "total": total, "skipped": skipped,
        "downloaded": downloaded, "failed": failed,
    }


def main() -> None:
    """主入口。"""
    args = parse_args()
    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    from aipha.data.tushare_alternative_provider import TuShareAlternativeProvider
    provider = TuShareAlternativeProvider(cache_dir=str(cache_dir))
    logger.info("Provider initialized, cache=%s", cache_dir)

    dates = get_trading_days(args.start, args.end)
    logger.info("Trading days: %d (%s ~ %s)", len(dates), dates[0], dates[-1])

    summary: dict[str, dict] = {}
    t0 = time.time()

    if "moneyflow" in args.types:
        logger.info("=" * 50 + " moneyflow")
        summary["moneyflow"] = download_moneyflow(provider, dates, cache_dir)

    if "margin" in args.types:
        logger.info("=" * 50 + " margin")
        summary["margin"] = download_margin(provider, dates, cache_dir)

    if "cyq" in args.types:
        logger.info("=" * 50 + " cyq")
        summary["cyq"] = download_cyq(provider, dates, cache_dir)

    if "analyst" in args.types:
        logger.info("=" * 50 + " analyst")
        summary["analyst"] = download_analyst(
            provider, args.start, args.end, cache_dir,
        )

    elapsed = time.time() - t0
    print("\n" + "=" * 60)
    print(f"Download complete in {elapsed:.0f}s")
    print("=" * 60)
    for dtype, stats in summary.items():
        print(
            f"  {dtype:12s}  total={stats['total']:5d}  "
            f"skip={stats['skipped']:5d}  dl={stats['downloaded']:5d}  "
            f"fail={stats['failed']:5d}"
        )

    # 统计缓存文件
    n_files = len([f for f in cache_dir.iterdir() if f.is_file()])
    total_mb = sum(f.stat().st_size for f in cache_dir.iterdir() if f.is_file()) / 1024 / 1024
    print(f"\nCache: {n_files} files, {total_mb:.1f} MB in {cache_dir}")


if __name__ == "__main__":
    main()
