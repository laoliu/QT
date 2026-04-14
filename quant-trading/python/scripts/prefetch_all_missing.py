#!/usr/bin/env python3
"""全量数据补全脚本 — 下载所有 TuShare 可用但尚未缓存的数据

覆盖以下缺口:
1. 三大财务报表 (income / balancesheet / cashflow)
2. CB 日行情增量更新
3. ETF 基础信息 + 日行情
4. 分析师预测数据补全
5. 高级 API (forecast / express / stk_holdernumber / index_weight)
6. A股行情/指标增量更新到最新

用法:
    python scripts/prefetch_all_missing.py --task all
    python scripts/prefetch_all_missing.py --task financials
    python scripts/prefetch_all_missing.py --task cb_update
    python scripts/prefetch_all_missing.py --task etf
    python scripts/prefetch_all_missing.py --task analyst
    python scripts/prefetch_all_missing.py --task advanced
    python scripts/prefetch_all_missing.py --task incremental
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

EQUITY_CACHE = Path("data/cache/equity_tushare")
CB_CACHE = Path("data/cache/cb_tushare")
ALT_CACHE = Path("data/cache/alternative")
TUSHARE_CACHE = Path("data/cache/tushare")


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    p = argparse.ArgumentParser(description="全量数据补全")
    p.add_argument(
        "--task",
        default="all",
        choices=["all", "financials", "cb_update", "etf",
                 "analyst", "advanced", "incremental"],
    )
    p.add_argument("--max-stocks", type=int, default=0,
                   help="限制处理股票数（调试用，0=不限）")
    return p.parse_args()


def get_all_cached_symbols() -> list[str]:
    """从 fina_indicator 缓存获取已有股票列表。"""
    symbols = set()
    for f in EQUITY_CACHE.glob("fina_indicator_*.parquet"):
        sym = f.stem.replace("fina_indicator_", "").replace("_", ".")
        symbols.add(sym)
    return sorted(symbols)


def get_active_stock_list() -> list[str]:
    """获取当前上市股票列表（从 stock_basic 缓存）。"""
    import pandas as pd
    sb_path = EQUITY_CACHE / "stock_basic.parquet"
    if sb_path.exists():
        df = pd.read_parquet(sb_path)
        return df["ts_code"].tolist()
    # fallback: 从 TuShare 拉取
    from aipha.data.tushare_equity_provider import TuShareEquityProvider
    provider = TuShareEquityProvider(cache_dir=str(EQUITY_CACHE))
    df = provider.load_stock_basic()
    return df["ts_code"].tolist() if df is not None else []


def _get_provider():
    """创建 EquityProvider 实例。"""
    from aipha.data.tushare_equity_provider import TuShareEquityProvider
    return TuShareEquityProvider(cache_dir=str(EQUITY_CACHE))


# === Task functions ===

def task_financials(max_stocks: int = 0):
    """下载三大财务报表 (income/balancesheet/cashflow)。"""
    provider = _get_provider()
    # 使用 fina_indicator 已缓存的股票列表
    symbols = get_all_cached_symbols()
    if max_stocks > 0:
        symbols = symbols[:max_stocks]
    logger.info(f"三大报表下载: {len(symbols)} 只股票")

    report_types = [
        ("income", provider.load_income, "income_"),
        ("balancesheet", provider.load_balancesheet, "balancesheet_"),
        ("cashflow", provider.load_cashflow, "cashflow_"),
    ]

    for rtype, load_fn, prefix in report_types:
        need = []
        cached = 0
        for sym in symbols:
            fp = EQUITY_CACHE / f"{prefix}{sym.replace('.','_')}.parquet"
            if fp.exists() and fp.stat().st_size > 100:
                cached += 1
            else:
                need.append(sym)
        logger.info(f"  {rtype}: 已缓存 {cached}, 需下载 {len(need)}")
        if not need:
            continue

        ok = 0
        fail = 0
        t0 = time.time()
        for i, sym in enumerate(need):
            if (i + 1) % 50 == 0 or i == 0:
                elapsed = time.time() - t0
                eta = (len(need) - i) * (elapsed / max(i, 1))
                logger.info(
                    f"  [{i+1}/{len(need)}] {sym} "
                    f"ETA ~{eta/60:.1f}min"
                )
            try:
                df = load_fn(sym, use_cache=False)
                if df is not None and not df.empty:
                    ok += 1
                else:
                    logger.debug(f"  {sym}: 无 {rtype} 数据")
            except Exception as e:
                logger.warning(f"  {sym} {rtype} 失败: {e}")
                fail += 1
                time.sleep(1)
        cost = time.time() - t0
        logger.info(
            f"  {rtype} 完成: 成功={ok} 失败={fail} "
            f"耗时={cost/60:.1f}min"
        )


def task_cb_update():
    """CB 日行情增量更新到最新交易日。"""
    from aipha.data.tushare_cb_provider import TuShareCBProvider
    from aipha.data.calendar import CNStockCalendar

    provider = TuShareCBProvider(cache_dir=str(CB_CACHE))
    cal = CNStockCalendar()

    # 找到 cb_tushare 中最新的逐日缓存
    existing = sorted(CB_CACHE.glob("cb_daily_2???????.parquet"))
    if existing:
        last_date_str = existing[-1].stem.replace("cb_daily_", "")
        last_date = datetime.strptime(last_date_str, "%Y%m%d").date()
    else:
        last_date = date(2015, 1, 5)

    today = date.today()
    trade_days = cal.get_trading_days(last_date, today)
    # 跳过已有的
    need_days = [d for d in trade_days if d > last_date]

    logger.info(f"CB 增量更新: 最新缓存 {last_date}, 需补 {len(need_days)} 天")
    if not need_days:
        logger.info("CB 数据已是最新!")
        return

    ok = 0
    fail = 0
    for d in need_days:
        ds = d.strftime("%Y%m%d")
        cache_path = CB_CACHE / f"cb_daily_{ds}.parquet"
        if cache_path.exists():
            continue
        try:
            df = provider.load_cb_daily(trade_date=ds, use_cache=False)
            if df is not None and not df.empty:
                ok += 1
        except Exception as e:
            logger.warning(f"CB daily {ds} 失败: {e}")
            fail += 1
            time.sleep(1)
    logger.info(f"CB 更新完成: 成功={ok} 失败={fail}")


def task_etf():
    """下载 ETF 基础信息 + 日行情。"""
    import pandas as pd
    from aipha.data.tushare_etf_provider import TuShareETFProvider
    from aipha.data.calendar import CNStockCalendar

    etf_cache = Path("data/cache/etf_tushare")
    etf_cache.mkdir(parents=True, exist_ok=True)
    provider = TuShareETFProvider(cache_dir=str(etf_cache))

    # 1) ETF 基础信息 — 直接从 gateway 获取，不依赖 provider 的过滤
    logger.info("下载 ETF 基础信息 (fund_basic)...")
    from aipha.data.tushare_gateway import get_tushare_gateway
    gw = get_tushare_gateway()
    df_basic = gw.call("fund_basic", market="E", status="L")
    if df_basic is None or df_basic.empty:
        logger.warning("fund_basic 返回空")
        return
    # 用名称过滤 ETF（TuShare fund_type 不直接标注 ETF）
    if "name" in df_basic.columns:
        df_basic = df_basic[
            df_basic["name"].str.contains("ETF", case=False, na=False)
        ].copy()
    df_basic.to_parquet(etf_cache / "etf_basic.parquet", index=False)
    logger.info(f"  fund_basic: {len(df_basic)} 只 ETF")

    # 2) 挑选主要 ETF 下载日行情
    active = df_basic
    etf_codes = active["ts_code"].tolist() if not active.empty else []
    logger.info(f"  活跃 ETF: {len(etf_codes)} 只")

    if not etf_codes:
        return

    # 下载主要宽基 ETF 日行情 (前50只成交量大的)
    # 优先下载跟踪主要指数的 ETF
    major_etf_keywords = [
        "沪深300", "中证500", "中证1000", "创业板",
        "科创50", "上证50", "中证2000",
    ]
    major_codes = []
    if "name" in active.columns:
        for kw in major_etf_keywords:
            matched = active[active["name"].str.contains(kw, na=False)]
            major_codes.extend(matched["ts_code"].tolist()[:5])
    # 去重
    major_codes = list(dict.fromkeys(major_codes))[:50]
    if not major_codes:
        major_codes = etf_codes[:50]

    logger.info(f"  下载 {len(major_codes)} 只主要 ETF 日行情...")
    ok = 0
    fail = 0
    end_str = date.today().strftime("%Y%m%d")
    for i, code in enumerate(major_codes):
        try:
            # provider.load_fund_daily 内部会自动按(code, start, end)缓存
            df = provider.load_fund_daily(
                ts_code=code,
                start_date="20150101",
                end_date=end_str,
            )
            if df is not None and not df.empty:
                ok += 1
        except Exception as e:
            logger.warning(f"  ETF {code} 失败: {e}")
            fail += 1
            time.sleep(1)
    logger.info(f"ETF 日行情完成: 成功={ok} 失败={fail}")


def task_analyst():
    """补全分析师预测数据 (report_rc) — 按月拉取。"""
    from aipha.data.tushare_alternative_provider import (
        TuShareAlternativeProvider,
    )

    provider = TuShareAlternativeProvider(cache_dir=str(ALT_CACHE))

    # 检查已缓存的月份
    existing = sorted(ALT_CACHE.glob("analyst_*.parquet"))
    cached_months = {f.stem.replace("analyst_", "") for f in existing}
    logger.info(f"分析师数据: 已缓存 {len(cached_months)} 个月")

    # 需要覆盖 2015-01 ~ 当前
    all_months = []
    for y in range(2015, date.today().year + 1):
        for m in range(1, 13):
            ym = f"{y:04d}{m:02d}"
            if y == date.today().year and m > date.today().month:
                break
            all_months.append(ym)

    need = [m for m in all_months if m not in cached_months]
    logger.info(f"  需下载 {len(need)} 个月")

    if not need:
        return

    # 使用 load_analyst_forecasts(start, end) 逐月拉取
    ok = 0
    fail = 0
    for i, ym in enumerate(need):
        if (i + 1) % 10 == 0 or i == 0:
            logger.info(f"  [{i+1}/{len(need)}] analyst {ym}")
        try:
            import calendar as _cal
            y_int = int(ym[:4])
            m_int = int(ym[4:6])
            last_day = _cal.monthrange(y_int, m_int)[1]
            start_d = f"{ym}01"
            end_d = f"{ym}{last_day:02d}"
            # load_analyst_forecasts 内部会缓存到 analyst_{ym}.parquet
            df = provider.load_analyst_forecasts(start_d, end_d)
            if df is not None and not df.empty:
                ok += 1
        except Exception as e:
            logger.warning(f"  analyst {ym} 失败: {e}")
            fail += 1
            time.sleep(1)
    logger.info(f"分析师数据完成: 成功={ok} 失败={fail}")


def task_advanced():
    """下载高级 API 数据 (forecast/express/holdernumber/index_weight)。"""
    import pandas as pd
    from aipha.data.tushare_gateway import get_tushare_gateway

    gw = get_tushare_gateway()
    adv_cache = Path("data/cache/advanced")
    adv_cache.mkdir(parents=True, exist_ok=True)

    # 1) 业绩预告 forecast — 按月拉取(ann_date)
    logger.info("下载业绩预告 (forecast)...")
    # 按月缓存: forecast_YYYYMM.parquet
    forecast_months = []
    for y in range(2015, date.today().year + 1):
        for m in range(1, 13):
            ym = f"{y:04d}{m:02d}"
            if y == date.today().year and m > date.today().month:
                break
            forecast_months.append(ym)
    ok = 0
    for ym in forecast_months:
        fp = adv_cache / f"forecast_{ym}.parquet"
        if fp.exists():
            continue
        try:
            import calendar as _cal2
            y_int, m_int = int(ym[:4]), int(ym[4:6])
            last_day = _cal2.monthrange(y_int, m_int)[1]
            # 逐日拉取该月数据
            frames = []
            for day in range(1, last_day + 1):
                ann_d = f"{ym}{day:02d}"
                df = gw.call("forecast", ann_date=ann_d)
                if df is not None and not df.empty:
                    frames.append(df)
            if frames:
                combined = pd.concat(frames, ignore_index=True)
                combined = combined.drop_duplicates()
                combined.to_parquet(fp, index=False)
                ok += 1
                logger.info(f"  forecast {ym}: {len(combined)} rows")
        except Exception as e:
            logger.debug(f"  forecast {ym}: {e}")
    logger.info(f"  forecast 完成: 新增 {ok}")

    # 2) 业绩快报 express — 同理按月
    logger.info("下载业绩快报 (express)...")
    ok = 0
    for ym in forecast_months:
        fp = adv_cache / f"express_{ym}.parquet"
        if fp.exists():
            continue
        try:
            import calendar as _cal2
            y_int, m_int = int(ym[:4]), int(ym[4:6])
            last_day = _cal2.monthrange(y_int, m_int)[1]
            frames = []
            for day in range(1, last_day + 1):
                ann_d = f"{ym}{day:02d}"
                df = gw.call("express", ann_date=ann_d)
                if df is not None and not df.empty:
                    frames.append(df)
            if frames:
                combined = pd.concat(frames, ignore_index=True)
                combined = combined.drop_duplicates()
                combined.to_parquet(fp, index=False)
                ok += 1
                logger.info(f"  express {ym}: {len(combined)} rows")
        except Exception as e:
            logger.debug(f"  express {ym}: {e}")
    logger.info(f"  express 完成: 新增 {ok}")

    # 3) 指数权重 index_weight
    logger.info("下载指数权重 (index_weight)...")
    indices = ["000300.SH", "000905.SH", "000852.SH", "000016.SH"]
    ok = 0
    for idx_code in indices:
        fp = adv_cache / f"index_weight_{idx_code.replace('.','_')}.parquet"
        if fp.exists():
            continue
        try:
            frames = []
            for y in range(2015, date.today().year + 1):
                df = gw.call(
                    "index_weight",
                    index_code=idx_code,
                    start_date=f"{y}0101",
                    end_date=f"{y}1231",
                )
                if df is not None and not df.empty:
                    frames.append(df)
                time.sleep(0.5)
            if frames:
                combined = pd.concat(frames, ignore_index=True)
                combined.to_parquet(fp, index=False)
                ok += 1
                logger.info(f"  {idx_code}: {len(combined)} rows")
        except Exception as e:
            logger.warning(f"  index_weight {idx_code}: {e}")
    logger.info(f"  index_weight 完成: {ok} 指数")

    # 4) 股东人数 stk_holdernumber — 跳过（量大且非核心）
    logger.info("  stk_holdernumber 暂跳过（量大，按需下载）")


def task_incremental():
    """增量更新 A股行情/日度指标到最新交易日。"""
    from aipha.data.calendar import CNStockCalendar
    from aipha.data.tushare_equity_provider import TuShareEquityProvider

    cal = CNStockCalendar()
    provider = TuShareEquityProvider(cache_dir=str(EQUITY_CACHE))
    today = date.today()

    # 1) daily_basic 增量 — 逐日拉取, 自动按月缓存
    logger.info("更新 daily_basic 到最新...")
    # 找到最后一个缓存月的最新交易日
    import pandas as pd
    existing_db = sorted(EQUITY_CACHE.glob("daily_basic_*.parquet"))
    last_cached_date = None
    if existing_db:
        last_file = existing_db[-1]
        try:
            df_last = pd.read_parquet(last_file)
            if "trade_date" in df_last.columns and not df_last.empty:
                last_cached_date = df_last["trade_date"].max()
        except Exception:
            pass
    if last_cached_date:
        from datetime import datetime as _dt
        start_from = _dt.strptime(last_cached_date, "%Y%m%d").date()
    else:
        start_from = date(2026, 3, 1)

    trade_days = cal.get_trading_days(start_from, today)
    need_days = [d for d in trade_days if d > start_from]
    logger.info(f"  daily_basic 需补 {len(need_days)} 天")
    for d in need_days:
        ds = d.strftime("%Y%m%d")
        try:
            provider.load_daily_basic(ds, use_cache=False)
        except Exception as e:
            logger.warning(f"  daily_basic {ds}: {e}")

    # 2) adj_factor 增量 — 使用 load_adj_factor_bulk 按月
    logger.info("更新 adj_factor 到最新...")
    existing_af = sorted(EQUITY_CACHE.glob("adj_factor_*.parquet"))
    cur_ym = f"{today.year:04d}{today.month:02d}"
    if existing_af:
        last_af_ym = existing_af[-1].stem.replace("adj_factor_", "")
    else:
        last_af_ym = "202601"
    # 只更新最近的月份
    af_need = []
    y, m = int(last_af_ym[:4]), int(last_af_ym[4:6])
    while f"{y:04d}{m:02d}" <= cur_ym:
        ym = f"{y:04d}{m:02d}"
        af_need.append(ym)
        m += 1
        if m > 12:
            m = 1
            y += 1
    logger.info(f"  adj_factor 需更新: {len(af_need)} 个月")
    for ym in af_need:
        try:
            y_int, m_int = int(ym[:4]), int(ym[4:6])
            import calendar as _cal
            last_day = _cal.monthrange(y_int, m_int)[1]
            provider.load_adj_factor_bulk(
                start_date=f"{ym}01",
                end_date=f"{ym}{last_day:02d}",
                use_cache=False,
            )
        except Exception as e:
            logger.warning(f"  adj_factor {ym}: {e}")

    # 3) 另类数据增量 (moneyflow/margin/cyq)
    logger.info("更新另类数据到最新...")
    trade_days = cal.get_trading_days(date(2026, 3, 1), today)
    from aipha.data.tushare_alternative_provider import (
        TuShareAlternativeProvider,
    )
    alt_provider = TuShareAlternativeProvider(cache_dir=str(ALT_CACHE))

    for dtype in ["moneyflow", "margin", "cyq"]:
        need = []
        for d in trade_days:
            ds = d.strftime("%Y%m%d")
            if dtype == "cyq":
                fp = ALT_CACHE / f"chips_daily_{ds}.parquet"
            else:
                fp = ALT_CACHE / f"{dtype}_{ds}.parquet"
            if not fp.exists():
                need.append(ds)
        if not need:
            logger.info(f"  {dtype}: 已是最新")
            continue
        logger.info(f"  {dtype}: 需补 {len(need)} 天")
        for ds in need:
            try:
                if dtype == "moneyflow":
                    alt_provider.load_moneyflow(ds)
                elif dtype == "margin":
                    alt_provider.load_margin_detail(ds)
                elif dtype == "cyq":
                    alt_provider.load_chips_bulk(ds)
            except Exception as e:
                logger.debug(f"  {dtype} {ds}: {e}")

    logger.info("增量更新完成!")


def main():
    args = parse_args()
    logger.info(f"任务: {args.task}")

    task_map = {
        "financials": lambda: task_financials(args.max_stocks),
        "cb_update": task_cb_update,
        "etf": task_etf,
        "analyst": task_analyst,
        "advanced": task_advanced,
        "incremental": task_incremental,
    }

    if args.task == "all":
        for name, fn in task_map.items():
            logger.info(f"\n{'='*60}")
            logger.info(f"开始任务: {name}")
            logger.info(f"{'='*60}")
            fn()
    else:
        task_map[args.task]()

    logger.info("\n全部任务完成!")


if __name__ == "__main__":
    main()
