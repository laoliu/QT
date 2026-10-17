#!/usr/bin/env python3
"""
TuShare 未缓存API批量下载脚本 (第二轮)
补全第一轮 prefetch_all_missing.py 未覆盖的30+ API数据

Usage:
    python scripts/prefetch_missing_apis.py --task quick     # 一次调用即可的静态数据
    python scripts/prefetch_missing_apis.py --task daily     # 按交易日拉取的数据
    python scripts/prefetch_missing_apis.py --task perstock  # 按股票拉取的数据
    python scripts/prefetch_missing_apis.py --task all       # 全部

Data stored under data/cache/ with sub-directories:
    reference/   - 参考数据 (dividend, suspend_d, share_float, repurchase, etc.)
    market_ref/  - 市场参考 (stk_limit, block_trade, hsgt, limit_list, etc.)
    index_ref/   - 指数参考 (index_daily, index_dailybasic, sw_daily, etc.)
    macro/       - 宏观数据 (shibor, daily_info, etc.)
"""

import argparse
import os
import sys
import time
import calendar as _cal
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(_PROJECT_ROOT / ".env")
sys.path.insert(0, str(_PROJECT_ROOT))

from aipha.data.tushare_gateway import get_tushare_gateway
from aipha.utils.paths import project_path

# === Global config ===
CACHE_ROOT = project_path('data/cache')
REF_DIR = CACHE_ROOT / 'reference'
MKT_DIR = CACHE_ROOT / 'market_ref'
IDX_DIR = CACHE_ROOT / 'index_ref'
MACRO_DIR = CACHE_ROOT / 'macro'

SLEEP = 0.55  # rate limit safety


def ensure_dirs():
    """Create cache directories."""
    for d in [REF_DIR, MKT_DIR, IDX_DIR, MACRO_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def get_trading_days(start: str, end: str) -> list[str]:
    """Get list of trading day strings."""
    gw = get_tushare_gateway()
    df = gw.call('trade_cal', exchange='SSE', start_date=start, end_date=end, is_open='1')
    return sorted(df['cal_date'].tolist())


def get_all_stocks() -> list[str]:
    """Get all stock ts_codes (including delisted)."""
    gw = get_tushare_gateway()
    frames = []
    for status in ['L', 'D', 'P']:
        df = gw.call('stock_basic', list_status=status,
                      fields='ts_code')
        if df is not None and not df.empty:
            frames.append(df)
        time.sleep(SLEEP)
    all_df = pd.concat(frames, ignore_index=True)
    return sorted(all_df['ts_code'].unique().tolist())


def save_parquet(df, path: Path):
    """Save DataFrame to parquet, skip if empty."""
    if df is not None and not df.empty:
        df.to_parquet(path, index=False)
        return len(df)
    return 0


# ============================================================
# TASK: quick — static/one-shot data (概念、指数列表等)
# ============================================================
def task_quick():
    """Download data that requires only 1-2 API calls."""
    gw = get_tushare_gateway()

    # 1. concept — 概念分类列表
    fp = REF_DIR / 'concept.parquet'
    if not fp.exists():
        df = gw.call('concept')
        n = save_parquet(df, fp)
        print(f'  concept: {n} rows')
        time.sleep(SLEEP)

    # 2. ths_index — 同花顺概念指数列表
    fp = IDX_DIR / 'ths_index.parquet'
    if not fp.exists():
        df = gw.call('ths_index')
        n = save_parquet(df, fp)
        print(f'  ths_index: {n} rows')
        time.sleep(SLEEP)

    # 3. margin_target — 融资融券标的列表
    fp = REF_DIR / 'margin_target.parquet'
    if not fp.exists():
        df = gw.call('margin_target')
        n = save_parquet(df, fp)
        print(f'  margin_target: {n} rows')
        time.sleep(SLEEP)

    # 4. hm_list — HM市场列表
    fp = REF_DIR / 'hm_list.parquet'
    if not fp.exists():
        df = gw.call('hm_list')
        n = save_parquet(df, fp)
        print(f'  hm_list: {n} rows')
        time.sleep(SLEEP)

    # 5. concept_detail — 概念成分(按概念ID)
    concept_fp = REF_DIR / 'concept.parquet'
    detail_dir = REF_DIR / 'concept_detail'
    detail_dir.mkdir(exist_ok=True)
    if concept_fp.exists():
        cdf = pd.read_parquet(concept_fp)
        total = len(cdf)
        done = 0
        for i, row in cdf.iterrows():
            cid = row.get('code', row.get('id', ''))
            fp = detail_dir / f'{cid}.parquet'
            if fp.exists():
                done += 1
                continue
            try:
                df = gw.call('concept_detail', id=cid)
                save_parquet(df, fp)
                done += 1
            except Exception as e:
                print(f'    concept_detail {cid}: {e}')
            time.sleep(SLEEP)
            if done % 50 == 0:
                print(f'    concept_detail: {done}/{total}')
        print(f'  concept_detail: {done}/{total} done')

    # 6. broker_recommend — 券商金股(按月)
    br_dir = MKT_DIR / 'broker_recommend'
    br_dir.mkdir(exist_ok=True)
    today = date.today()
    for y in range(2018, today.year + 1):
        for m in range(1, 13):
            if y == today.year and m > today.month:
                break
            ym = f'{y:04d}{m:02d}'
            fp = br_dir / f'broker_recommend_{ym}.parquet'
            if fp.exists():
                continue
            try:
                df = gw.call('broker_recommend', month=ym)
                save_parquet(df, fp)
            except Exception:
                pass
            time.sleep(SLEEP)
    print(f'  broker_recommend done')

    # 7. shibor — 利率(批量下载全部)
    fp = MACRO_DIR / 'shibor.parquet'
    if not fp.exists():
        frames = []
        for y in range(2006, today.year + 1):
            df = gw.call('shibor', start_date=f'{y}0101',
                          end_date=f'{y}1231')
            if df is not None and not df.empty:
                frames.append(df)
            time.sleep(SLEEP)
        if frames:
            combined = pd.concat(frames, ignore_index=True)
            save_parquet(combined, fp)
            print(f'  shibor: {len(combined)} rows')

    print('=== task_quick complete ===')


# ============================================================
# TASK: daily — per-trading-day data
# ============================================================
def task_daily():
    """Download per-trading-day data for all trading days."""
    gw = get_tushare_gateway()

    # Parse start/end from sys.argv implicitly via globals won't work;
    # we'll use command-line defaults stored in module-level after parse
    start = getattr(task_daily, '_start', '20150105')
    end = getattr(task_daily, '_end', date.today().strftime('%Y%m%d'))

    print(f'  Getting trading days {start} ~ {end} ...')
    trading_days = get_trading_days(start, end)
    total_days = len(trading_days)
    print(f'  Total trading days: {total_days}')

    # --- Daily API definitions ---
    # Each tuple: (api_name, cache_dir, file_prefix, extra_params)
    daily_apis = [
        ('stk_limit', MKT_DIR / 'stk_limit', 'stk_limit', {}),
        ('suspend_d', REF_DIR / 'suspend_d', 'suspend_d', {}),
        ('block_trade', MKT_DIR / 'block_trade', 'block_trade', {}),
        ('moneyflow_hsgt', MKT_DIR / 'hsgt', 'moneyflow_hsgt', {}),
        ('hsgt_top10', MKT_DIR / 'hsgt', 'hsgt_top10', {}),
        ('ggt_daily', MKT_DIR / 'hsgt', 'ggt_daily', {}),
        ('ggt_top10', MKT_DIR / 'hsgt', 'ggt_top10', {}),
        ('limit_list_d', MKT_DIR / 'limit_list', 'limit_list_d', {}),
        ('stk_auction', MKT_DIR / 'auction', 'stk_auction', {}),
        ('moneyflow_ind_dc', MKT_DIR / 'ind_moneyflow', 'moneyflow_ind_dc', {}),
        ('moneyflow_ind_ths', MKT_DIR / 'ind_moneyflow', 'moneyflow_ind_ths', {}),
        ('index_dailybasic', IDX_DIR / 'index_dailybasic', 'index_dailybasic', {}),
        ('daily_info', MACRO_DIR / 'daily_info', 'daily_info', {}),
        ('sz_daily_info', MACRO_DIR / 'daily_info', 'sz_daily_info', {}),
        ('margin', MKT_DIR / 'margin', 'margin_summary', {}),
        ('hk_hold', MKT_DIR / 'hk_hold', 'hk_hold', {}),
    ]

    # Create all sub-dirs
    for _, cache_dir, _, _ in daily_apis:
        cache_dir.mkdir(parents=True, exist_ok=True)

    # Download each API for each trading day
    for api_name, cache_dir, prefix, extra in daily_apis:
        cached = 0
        downloaded = 0
        errors = 0
        for i, td in enumerate(trading_days):
            fp = cache_dir / f'{prefix}_{td}.parquet'
            if fp.exists():
                cached += 1
                continue
            try:
                df = gw.call(api_name, trade_date=td, **extra)
                if df is not None and not df.empty:
                    df.to_parquet(fp, index=False)
                    downloaded += 1
                else:
                    # Save empty marker to skip next time
                    pd.DataFrame().to_parquet(fp, index=False)
                    downloaded += 1
            except Exception as e:
                errors += 1
                if errors <= 3:
                    print(f'    {api_name} {td}: {str(e)[:60]}')
            time.sleep(SLEEP)
            if (cached + downloaded) % 500 == 0 and downloaded > 0:
                print(f'    {api_name}: {cached+downloaded}/{total_days} '
                      f'(new={downloaded})')
        print(f'  {api_name}: done ({cached} cached, {downloaded} new, '
              f'{errors} errors)')

    # --- Index daily bars for major indices ---
    _download_index_daily_bars(gw, start, end)

    # --- Share float (解禁) by year ---
    _download_share_float(gw, start, end)

    # --- Repurchase (回购) by year ---
    _download_repurchase(gw, start, end)

    print('=== task_daily complete ===')


def _download_index_daily_bars(gw, start: str, end: str):
    """Download index daily bars for major indices."""
    idx_daily_dir = IDX_DIR / 'index_daily'
    idx_daily_dir.mkdir(exist_ok=True)
    indices = [
        '000001.SH', '000300.SH', '000905.SH', '000852.SH',
        '000016.SH', '399001.SZ', '399006.SZ', '399005.SZ',
        '000688.SH', '899050.BJ',
    ]
    for idx_code in indices:
        safe = idx_code.replace('.', '_')
        fp = idx_daily_dir / f'index_daily_{safe}.parquet'
        if fp.exists():
            continue
        try:
            df = gw.call('index_daily', ts_code=idx_code,
                          start_date=start, end_date=end)
            n = save_parquet(df, fp)
            print(f'    index_daily {idx_code}: {n} rows')
        except Exception as e:
            print(f'    index_daily {idx_code}: {e}')
        time.sleep(SLEEP)

    # index_global — 全球指数
    fp = IDX_DIR / 'index_global.parquet'
    if not fp.exists():
        frames = []
        for y in range(2015, date.today().year + 1):
            try:
                df = gw.call('index_global',
                              start_date=f'{y}0101',
                              end_date=f'{y}1231')
                if df is not None and not df.empty:
                    frames.append(df)
            except Exception:
                pass
            time.sleep(SLEEP)
        if frames:
            combined = pd.concat(frames, ignore_index=True)
            save_parquet(combined, fp)
            print(f'    index_global: {len(combined)} rows')

    # sw_daily — 申万行业日线
    sw_dir = IDX_DIR / 'sw_daily'
    sw_dir.mkdir(exist_ok=True)
    try:
        sw_df = gw.call('index_classify', level='L1', src='SW2021')
        if sw_df is not None and not sw_df.empty:
            for _, row in sw_df.iterrows():
                sw_code = row['index_code']
                safe = sw_code.replace('.', '_')
                fp = sw_dir / f'sw_daily_{safe}.parquet'
                if fp.exists():
                    continue
                try:
                    df = gw.call('sw_daily', ts_code=sw_code,
                                  start_date=start, end_date=end)
                    n = save_parquet(df, fp)
                except Exception:
                    n = 0
                time.sleep(SLEEP)
            print(f'    sw_daily: {len(sw_df)} industries')
    except Exception as e:
        print(f'    sw_daily classify: {e}')


def _download_share_float(gw, start: str, end: str):
    """Download share float (限售解禁) by year."""
    sf_dir = REF_DIR / 'share_float'
    sf_dir.mkdir(exist_ok=True)
    start_y = int(start[:4])
    end_y = int(end[:4])
    for y in range(start_y, end_y + 1):
        fp = sf_dir / f'share_float_{y}.parquet'
        if fp.exists():
            continue
        try:
            df = gw.call('share_float',
                          start_date=f'{y}0101',
                          end_date=f'{y}1231')
            n = save_parquet(df, fp)
            print(f'    share_float {y}: {n} rows')
        except Exception as e:
            print(f'    share_float {y}: {e}')
        time.sleep(SLEEP)


def _download_repurchase(gw, start: str, end: str):
    """Download stock repurchase (回购) by year."""
    rp_dir = REF_DIR / 'repurchase'
    rp_dir.mkdir(exist_ok=True)
    start_y = int(start[:4])
    end_y = int(end[:4])
    for y in range(start_y, end_y + 1):
        fp = rp_dir / f'repurchase_{y}.parquet'
        if fp.exists():
            continue
        try:
            df = gw.call('repurchase',
                          start_date=f'{y}0101',
                          end_date=f'{y}1231')
            n = save_parquet(df, fp)
            print(f'    repurchase {y}: {n} rows')
        except Exception as e:
            print(f'    repurchase {y}: {e}')
        time.sleep(SLEEP)


# ============================================================
# TASK: perstock — per-stock data
# ============================================================
def task_perstock():
    """Download per-stock data for all stocks."""
    gw = get_tushare_gateway()
    print('  Loading stock list...')
    stocks = get_all_stocks()
    total = len(stocks)
    print(f'  Total stocks: {total}')

    # --- Per-stock API definitions ---
    # (api_name, cache_dir, file_prefix, extra_params)
    perstock_apis = [
        ('dividend', REF_DIR / 'dividend', 'dividend', {}),
        ('fina_audit', REF_DIR / 'fina_audit', 'fina_audit', {}),
        ('fina_mainbz', REF_DIR / 'fina_mainbz', 'fina_mainbz',
         {'type': ''}),
        ('disclosure_date', REF_DIR / 'disclosure_date',
         'disclosure_date', {}),
        ('stk_holdernumber', REF_DIR / 'stk_holdernumber',
         'stk_holdernumber', {}),
        ('top10_holders', REF_DIR / 'top10_holders',
         'top10_holders', {}),
        ('top10_floatholders', REF_DIR / 'top10_floatholders',
         'top10_floatholders', {}),
        ('pledge_stat', REF_DIR / 'pledge_stat', 'pledge_stat', {}),
        ('stk_managers', REF_DIR / 'stk_managers',
         'stk_managers', {}),
        ('stk_rewards', REF_DIR / 'stk_rewards', 'stk_rewards', {}),
        ('stk_factor', MKT_DIR / 'stk_factor', 'stk_factor',
         {'start_date': '20150105',
          'end_date': date.today().strftime('%Y%m%d')}),
    ]

    # Create dirs
    for _, cache_dir, _, _ in perstock_apis:
        cache_dir.mkdir(parents=True, exist_ok=True)

    # Download each API
    for api_name, cache_dir, prefix, extra in perstock_apis:
        cached = 0
        downloaded = 0
        errors = 0
        for i, ts_code in enumerate(stocks):
            safe = ts_code.replace('.', '_')
            fp = cache_dir / f'{prefix}_{safe}.parquet'
            if fp.exists():
                cached += 1
                continue
            try:
                df = gw.call(api_name, ts_code=ts_code, **extra)
                if df is not None and not df.empty:
                    df.to_parquet(fp, index=False)
                else:
                    pd.DataFrame().to_parquet(fp, index=False)
                downloaded += 1
            except Exception as e:
                errors += 1
                if errors <= 3:
                    print(f'    {api_name} {ts_code}: '
                          f'{str(e)[:60]}')
            time.sleep(SLEEP)
            done = cached + downloaded
            if done % 500 == 0 and downloaded > 0:
                print(f'    {api_name}: {done}/{total} '
                      f'(new={downloaded})')
        print(f'  {api_name}: done ({cached} cached, '
              f'{downloaded} new, {errors} errors)')

    print('=== task_perstock complete ===')


def main():
    parser = argparse.ArgumentParser(description='Prefetch missing TuShare APIs')
    parser.add_argument('--task', choices=['quick', 'daily', 'perstock', 'all'],
                        default='all', help='Which task to run')
    parser.add_argument('--start', default='20150105', help='Start date for daily data')
    parser.add_argument('--end', default=None, help='End date (default: today)')
    args = parser.parse_args()

    if args.end is None:
        args.end = date.today().strftime('%Y%m%d')

    ensure_dirs()
    print(f"=== Prefetch Missing APIs: task={args.task} range={args.start}~{args.end} ===")

    # Pass date range to task_daily via function attributes
    task_daily._start = args.start
    task_daily._end = args.end

    if args.task in ('quick', 'all'):
        task_quick()
    if args.task in ('daily', 'all'):
        task_daily()
    if args.task in ('perstock', 'all'):
        task_perstock()

    print("\n=== All tasks complete ===")


if __name__ == '__main__':
    main()
