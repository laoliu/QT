"""Data loading module for config-driven backtests.

Extracted from config_runner.py to reduce module size.
This module handles all data loading, caching, and metadata enrichment:
- TuShare A-stock data (daily bars, stock_basic, daily_basic, fina_indicator)
- CB data (via CBDataAdapter)
- Synthetic test data
- PIT (Point-in-Time) alignment for financial data
- ST/namechange historical identification
- Memory caching for Walk-Forward/parameter sweep scenarios

All functions accept BacktestRunConfig and return bars_by_date dicts.
"""
from __future__ import annotations

import bisect
import logging
from datetime import date
from pathlib import Path

from decimal import Decimal

from aipha.backtest.config_models import BacktestRunConfig
from aipha.market.halt_checker import HaltChecker, LimitStatus
from aipha.utils.paths import project_path

logger = logging.getLogger(__name__)

# 模块级单例，避免每个 Bar 都构造
_halt_checker = HaltChecker()


class IndexConstituentChecker:
    """基于半年度快照的指数成分股归属检查器。

    用于判断某只股票在某个交易日是否属于指数成分。
    采用"最近快照回溯"策略：对于快照间的日期，使用最近一个
    **早于等于**该日期的快照来判断成分归属。

    对于早于最早快照的日期，使用最早可用快照（保守近似）。

    Attributes:
        _sorted_dates: 排序后的快照日期列表
        _membership: ``{snapshot_date_str: set[str]}`` 成分股集合
    """

    def __init__(self, snapshots: dict[str, list[str]]) -> None:
        self._membership: dict[str, set[str]] = {
            sd: set(codes) for sd, codes in snapshots.items()
        }
        self._sorted_dates = sorted(self._membership.keys())

    @property
    def is_empty(self) -> bool:
        """是否无任何快照数据。"""
        return len(self._sorted_dates) == 0

    @property
    def all_symbols_union(self) -> list[str]:
        """所有快照的成分股并集。"""
        union: set[str] = set()
        for codes in self._membership.values():
            union.update(codes)
        return sorted(union)

    def is_member(self, symbol: str, trade_date: date) -> bool:
        """判断 symbol 在 trade_date 是否属于指数成分。

        策略：找到最近一个 ≤ trade_date 的快照，检查 symbol 是否在其中。
        如果 trade_date 早于所有快照，使用最早快照（保守近似）。
        """
        if not self._sorted_dates:
            return True  # 无快照数据时默认放行

        td_str = trade_date.strftime("%Y%m%d")
        idx = bisect.bisect_right(self._sorted_dates, td_str) - 1
        if idx < 0:
            # trade_date 早于最早快照 → 使用最早快照
            idx = 0
        snapshot_date = self._sorted_dates[idx]
        return symbol in self._membership[snapshot_date]

    def snapshot_count(self) -> int:
        """返回已加载的快照总数。"""
        return len(self._sorted_dates)

    def total_symbols(self) -> int:
        """返回所有快照的成分股去重后总数。"""
        return len(self.all_symbols_union)


def _load_data(config: BacktestRunConfig) -> dict[date, dict]:
    """根据 data.source 加载回测数据

    支持的数据源:
    - ``tushare_cb``: 可转债数据（通过 CBDataAdapter）
    - ``tushare``: A-stock 行情（通过 TuShareProBarProvider）
    - ``synthetic``: 合成测试数据

    Args:
        config: 回测运行配置

    Returns:
        bars_by_date: ``dict[date, dict[str, Bar]]``

    Raises:
        ValueError: 不支持的数据源
    """
    source = config.data.source

    if source == "tushare_cb":
        return _load_cb_data(config)
    elif source == "tushare_etf":
        return _load_etf_data(config)
    elif source in ("tushare", "tushare_pro"):
        return _load_tushare_data(config)
    elif source == "synthetic":
        return _generate_synthetic_data(config)
    else:
        raise ValueError(
            f"不支持的数据源: {source!r}。"
            f"可用: tushare_cb, tushare, tushare_pro, tushare_etf, synthetic"
        )


def _load_cb_data(config: BacktestRunConfig) -> dict[date, dict]:
    """通过 CBDataAdapter 加载可转债数据（支持内存缓存复用）"""
    from aipha.data.cb_data_adapter import CBDataAdapter

    # 内存缓存命中检查 — 与 A 股/ETF 对齐，避免 WF/Sweep 重复加载
    base_key = _cb_cache_base_key(config)
    if base_key in _cb_data_cache:
        cached_start, cached_end = _cb_data_cache_ranges[base_key]
        req_start = config.data.start_date
        req_end = config.data.end_date
        if cached_start <= req_start and cached_end >= req_end:
            cached = _cb_data_cache[base_key]
            filtered = {
                d: bars for d, bars in cached.items()
                if req_start <= d <= req_end
            }
            logger.info(
                f"CB 数据命中内存缓存 ({len(filtered)}/{len(cached)} 交易日)"
            )
            return filtered

    cache_dir = config.data.cache_dir or "data/cache/cb_tushare"
    adapter = CBDataAdapter(cache_dir=cache_dir)

    start_str = config.data.start_date.strftime("%Y%m%d")
    end_str = config.data.end_date.strftime("%Y%m%d")

    bars_by_date = adapter.load(start_str, end_str)
    logger.info(f"可转债数据加载完成: {len(bars_by_date)} 个交易日")

    # 写入内存缓存
    _cb_data_cache[base_key] = bars_by_date
    _cb_data_cache_ranges[base_key] = (
        config.data.start_date, config.data.end_date,
    )
    return bars_by_date


def _load_etf_data(config: BacktestRunConfig) -> dict[date, dict]:
    """加载 ETF 行情数据

    使用 TuShareProBarProvider(asset="FD") 获取场内基金日线，
    不需要 stock_basic / daily_basic 等 A 股元数据。
    symbols 直接从 config.data.symbols 读取。
    """
    import os
    from collections import defaultdict

    from aipha.data.tushare_provider import TuShareProBarProvider
    from aipha.domain.models import Bar

    token = os.environ.get("TUSHARE_TOKEN") or getattr(
        config.data, "tushare_token", None,
    )
    if not token:
        raise ValueError(
            "TUSHARE_TOKEN 未配置。data.source='tushare_etf' 需要有效 Token。"
        )

    # ETF 候选池中不同品种上市时间差异大（如 513500 自 2014 而 511130 自 2024），
    # 上市前自然无数据，应使用更宽松的缺失阈值。
    etf_missing_rate = (
        config.data.max_missing_rate
        if hasattr(config.data, "max_missing_rate") and config.data.max_missing_rate is not None
        else 0.25  # ETF 默认 25%（远大于股票的 5%）
    )

    provider = TuShareProBarProvider(
        token=token,
        cache_dir=config.data.cache_dir or "data/cache/tushare",
        adjust=config.data.adjust,
        asset="FD",
        max_missing_rate=etf_missing_rate,
    )

    symbols = config.data.symbols
    if not symbols:
        raise ValueError(
            "data.source='tushare_etf' 时必须在 data.symbols 中"
            "指定 ETF 代码列表，如 ['510300.SH', '510500.SH']"
        )

    bars_by_date: dict = defaultdict(dict)
    _FREQ_MAP = {"1d": "daily", "d": "daily", "day": "daily"}
    raw_freq = config.data.frequency or "daily"
    data_freq = _FREQ_MAP.get(raw_freq, raw_freq)

    for i, symbol in enumerate(symbols):
        logger.info(
            f"加载 ETF Bar: {i + 1}/{len(symbols)} ({symbol})..."
        )
        try:
            bars = provider.load_bars(
                symbol=symbol,
                start=config.data.start_date,
                end=config.data.end_date,
                frequency=data_freq,
            )
            for bar in bars:
                trade_date = bar.start_ts.date()
                meta = dict(bar.metadata) if bar.metadata else {}
                meta["asset_type"] = "etf"
                if "amount" not in meta and bar.volume and bar.vwap:
                    meta["amount"] = (bar.volume * bar.vwap) / 1000

                enriched = Bar(
                    symbol=bar.symbol,
                    start_ts=bar.start_ts,
                    end_ts=bar.end_ts,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                    vwap=bar.vwap,
                    prev_close=bar.prev_close,
                    is_suspended=bar.is_suspended,
                    is_st=False,
                    is_limit_up=False,
                    is_limit_down=False,
                    metadata=meta,
                )
                bars_by_date[trade_date][enriched.symbol] = enriched
        except Exception as e:
            logger.warning(f"加载 ETF {symbol} 失败: {e}")

    if not bars_by_date:
        raise RuntimeError(
            f"ETF 数据加载失败，0 个交易日。symbols={symbols}"
        )

    result = dict(bars_by_date)
    logger.info(
        f"ETF 数据加载完成: {len(result)} 个交易日, "
        f"{len(symbols)} 只 ETF"
    )

    # 内存缓存
    base_key = _tushare_cache_base_key(config)
    _tushare_data_cache[base_key] = result
    _tushare_data_cache_ranges[base_key] = (
        config.data.start_date, config.data.end_date,
    )
    return result


# TuShare 数据内存缓存，避免参数扫描/Walk-Forward 时重复加载
_tushare_data_cache: dict[str, dict] = {}
_tushare_data_cache_ranges: dict[str, tuple] = {}  # key → (start, end)

# CB 数据内存缓存（与 A 股/ETF 对齐）
_cb_data_cache: dict[str, dict] = {}
_cb_data_cache_ranges: dict[str, tuple] = {}  # key → (start, end)


def clear_tushare_data_cache() -> None:
    """清除 TuShare 数据内存缓存（含 CB）"""
    _tushare_data_cache.clear()
    _tushare_data_cache_ranges.clear()
    _cb_data_cache.clear()
    _cb_data_cache_ranges.clear()


def _cb_cache_base_key(config: BacktestRunConfig) -> str:
    """生成 CB 数据缓存的 base key（不含日期，用于超集匹配）"""
    cache_dir = config.data.cache_dir or "data/cache/cb_tushare"
    return f"cb|{cache_dir}"


def _tushare_cache_base_key(config: BacktestRunConfig) -> str:
    """生成不含日期的缓存 base key（用于 Walk-Forward 跨日期段复用）"""
    strat_params = config.strategy.params or {}
    benchmark = strat_params.get("benchmark") or strat_params.get("base_index") or ""
    return f"{config.data.source}|{config.strategy.name}|{benchmark}"


# 批量加载阈值：symbols 数量超过此值时使用批量路径
_BULK_LOAD_THRESHOLD = 200


def _load_bars_bulk(
    symbols: list[str],
    config: BacktestRunConfig,
    equity_provider,
    stock_basic_df,
    daily_basic_map: dict,
    fina_pit_map: dict,
    namechange_st_index: dict,
    constituent_checker: "IndexConstituentChecker | None",
) -> dict[date, dict]:
    """批量加载路径：读取月度全市场文件 + adj_factor 计算 qfq。

    通过 ``equity_tushare/daily_YYYYMM.parquet`` 批量文件（每月全市场）
    和 ``adj_factor_YYYYMM.parquet`` 复权因子，计算前复权价格并构建 Bar。

    相比逐股加载（230K 文件读取），仅需读取约 200 个文件，速度提升 50-100x。
    """
    import os
    from collections import defaultdict
    from datetime import datetime as _dt

    import numpy as np
    import pandas as pd

    from aipha.domain.models import Bar

    eq_cache = Path(config.data.cache_dir or "data/cache") / ".." / "equity_tushare"
    eq_cache = project_path("data", "cache", "equity_tushare")

    # 1) 确定月份范围
    start_ym = int(config.data.start_date.strftime("%Y%m"))
    end_ym = int(config.data.end_date.strftime("%Y%m"))
    sym_set = set(symbols)

    # 2) 读取月度 bulk 文件
    logger.info("批量加载: 读取月度行情文件 (symbols=%d)...", len(symbols))
    price_parts: list[pd.DataFrame] = []
    loaded_months: list[int] = []  # 实际加载到数据的 YYYYMM
    for f in sorted(eq_cache.glob("daily_2*.parquet")):
        stem = f.stem
        if "basic" in stem:
            continue
        ym_str = stem.replace("daily_", "")
        try:
            ym_int = int(ym_str)
        except ValueError:
            continue
        if ym_int < start_ym or ym_int > end_ym:
            continue
        try:
            df = pd.read_parquet(f)
            df = df[df["ts_code"].isin(sym_set)]
            if not df.empty:
                price_parts.append(df)
                loaded_months.append(ym_int)
        except Exception as e:
            logger.warning("跳过 %s: %s", f.name, e)

    if not price_parts:
        logger.warning("批量加载: 无行情数据文件，回退到逐股加载")
        return {}  # 空字典信号调用者应回退

    # ------------------------------------------------------------------
    # Coverage guard: 检测 bulk 数据是否充分覆盖请求的时间范围
    # 若首月数据远晚于请求起始月，说明早期 bulk 文件缺失，
    # 应回退到逐股路径（per-stock 缓存通常更完整）。
    # ------------------------------------------------------------------
    if loaded_months:
        first_loaded = min(loaded_months)
        # 若首个加载月份比请求起始月晚 6 个月以上，视为覆盖不足
        gap_months = (first_loaded // 100 - start_ym // 100) * 12 + (
            first_loaded % 100 - start_ym % 100
        )
        if gap_months > 6:
            logger.warning(
                "批量加载覆盖不足: 请求起始 %d, 首个可用月 %d "
                "(缺口 %d 月 > 6 月), 回退到逐股加载",
                start_ym, first_loaded, gap_months,
            )
            return {}

    prices = pd.concat(price_parts, ignore_index=True)
    # 过滤日期范围
    start_str = config.data.start_date.strftime("%Y%m%d")
    end_str = config.data.end_date.strftime("%Y%m%d")
    prices = prices[
        (prices["trade_date"] >= start_str) & (prices["trade_date"] <= end_str)
    ]
    logger.info(
        "  行情: %d 条, %d 只股票, %d 交易日",
        len(prices), prices["ts_code"].nunique(),
        prices["trade_date"].nunique(),
    )

    # 3) 加载 adj_factor
    adj_df = _load_adj_factor_for_bulk(equity_provider, start_str, end_str)
    if adj_df.empty:
        logger.warning("批量加载: 无 adj_factor 数据，回退到逐股加载")
        return {}

    # 4) 计算 qfq 价格
    prices = _apply_qfq_adjustment(prices, adj_df)

    # 5) 转换为 Bar 对象
    bars_by_date: dict = defaultdict(dict)
    _build_bars_from_bulk(
        prices, bars_by_date,
        stock_basic_df, daily_basic_map,
        fina_pit_map, namechange_st_index,
        constituent_checker,
    )

    logger.info(
        "批量加载完成: %d 交易日, %d 标的",
        len(bars_by_date),
        len(set().union(*(d.keys() for d in bars_by_date.values())))
        if bars_by_date else 0,
    )
    return dict(bars_by_date)


def _load_adj_factor_for_bulk(
    equity_provider, start_str: str, end_str: str,
):
    """加载批量复权因子，含最新日的因子用于归一化。"""
    import pandas as pd

    if equity_provider is None:
        return pd.DataFrame()

    try:
        af = equity_provider.load_adj_factor_bulk(start_str, end_str)
        if af.empty:
            return af
        # 还需要最新日期的 adj_factor 作为归一化基准
        # 取 af 中每个 ts_code 的最大 trade_date 对应的 adj_factor
        # 作为 ref_factor
        latest = af.sort_values("trade_date").groupby("ts_code").tail(1)
        latest = latest[["ts_code", "adj_factor"]].rename(
            columns={"adj_factor": "ref_factor"}
        )
        af = af.merge(latest, on="ts_code", how="left")
        return af
    except Exception as e:
        logger.error("adj_factor 批量加载失败（将回退到逐股加载）: %s", e, exc_info=True)
        return pd.DataFrame()


def _apply_qfq_adjustment(prices, adj_df):
    """将 adj_factor 应用到原始价格，计算前复权 OHLC。

    qfq_price = raw_price * adj_factor / ref_factor
    其中 ref_factor 是同一股票最新可用日期的 adj_factor。
    """
    import numpy as np

    # 合并 adj_factor 到 prices
    prices = prices.merge(
        adj_df[["ts_code", "trade_date", "adj_factor", "ref_factor"]],
        on=["ts_code", "trade_date"],
        how="left",
    )
    # 无 adj_factor 的记录（新股等），factor 设为 1
    prices["adj_factor"] = prices["adj_factor"].fillna(1.0)
    prices["ref_factor"] = prices["ref_factor"].fillna(1.0)

    # 计算归一化因子
    ratio = prices["adj_factor"] / prices["ref_factor"]

    # 应用到 OHLC
    for col in ["open", "high", "low", "close"]:
        prices[col] = np.round(prices[col] * ratio, 4)

    # 计算 qfq prev_close
    prices["prev_close"] = np.round(
        prices["pre_close"] * ratio, 4,
    )

    # 计算 vwap（amount 是千元, vol 是手*100=股）
    prices["vwap"] = np.where(
        prices["vol"] > 0,
        prices["amount"] * 1000 / (prices["vol"] * 100),
        prices["close"],
    )

    return prices


def _build_bars_from_bulk(
    prices,
    bars_by_date: dict,
    stock_basic_df,
    daily_basic_map: dict,
    fina_pit_map: dict,
    namechange_st_index: dict,
    constituent_checker,
) -> None:
    """将批量 DataFrame 转换为 Bar 对象并注入元数据（向量化预提取优化）。"""
    from datetime import datetime as _dt

    from aipha.domain.models import Bar

    if prices.empty:
        return

    n_rows = len(prices)

    # -- 向量化预提取：一次性取出 numpy 数组，避免逐行 Series 创建开销 --
    arr_symbol = prices["ts_code"].values
    arr_td = prices["trade_date"].values
    arr_open = prices["open"].fillna(0).values.astype(float)
    arr_high = prices["high"].fillna(0).values.astype(float)
    arr_low = prices["low"].fillna(0).values.astype(float)
    arr_close = prices["close"].fillna(0).values.astype(float)
    arr_vol = prices["vol"].fillna(0).values.astype(float)
    arr_amount = prices["amount"].values if "amount" in prices.columns else None
    arr_vwap = prices["vwap"].values if "vwap" in prices.columns else None
    arr_prev_close = prices["prev_close"].values if "prev_close" in prices.columns else None

    # 本地化频繁调用，减少属性查找
    _combine = _dt.combine
    _min_time = _dt.min.time()
    _date = date
    _float = float
    _Decimal = Decimal
    _get_board = _halt_checker.get_board_type
    _check_limit = _halt_checker.check_limit_status
    _LU = LimitStatus.LIMIT_UP
    _LD = LimitStatus.LIMIT_DOWN

    _log_interval = 1_000_000
    if n_rows > _log_interval:
        logger.info("_build_bars_from_bulk: 开始处理 %d 行...", n_rows)

    for i in range(n_rows):
        if i > 0 and i % _log_interval == 0:
            logger.info("  _build_bars_from_bulk: %d/%d (%.0f%%)", i, n_rows, i * 100.0 / n_rows)

        symbol = arr_symbol[i]
        td_str = str(arr_td[i])
        trade_date_obj = _date(int(td_str[:4]), int(td_str[4:6]), int(td_str[6:8]))
        ts = _combine(trade_date_obj, _min_time)

        vol = arr_vol[i]
        is_suspended = vol <= 0

        meta: dict = {}
        if arr_amount is not None:
            amt = arr_amount[i]
            if amt == amt and _float(amt) != 0.0:  # not NaN and not zero
                meta["amount"] = _float(amt)

        _enrich_bar_metadata(
            meta, symbol, trade_date_obj,
            stock_basic_df, daily_basic_map,
            fina_pit_map,
            namechange_st_index=namechange_st_index,
            constituent_checker=constituent_checker,
        )

        # -- 涨跌停标志：基于 prev_close + close 自动计算 (§13.3 禁止静默跳过) --
        close_f = arr_close[i]
        prev_close_f = 0.0
        if arr_prev_close is not None:
            pc_raw = arr_prev_close[i]
            if pc_raw == pc_raw:  # not NaN
                prev_close_f = _float(pc_raw)

        if prev_close_f <= 0 and not is_suspended:
            logger.debug(
                "prev_close 缺失或无效 (symbol=%s, date=%s) — "
                "涨跌停标志将为 None §13.3",
                symbol, trade_date_obj,
            )
        _is_limit_up: bool | None = None
        _is_limit_down: bool | None = None
        if prev_close_f > 0 and close_f > 0 and not is_suspended:
            _is_st = meta.get("is_st", False)
            _board = _get_board(symbol, is_st=_is_st)
            _lst = _check_limit(
                _Decimal(str(close_f)), _Decimal(str(prev_close_f)), _board,
            )
            _is_limit_up = (_lst == _LU)
            _is_limit_down = (_lst == _LD)

        vwap_val = None
        if arr_vwap is not None:
            vw = arr_vwap[i]
            if vw == vw and _float(vw) != 0.0:  # not NaN, not zero
                vwap_val = _float(vw)

        bar = Bar(
            symbol=symbol,
            start_ts=ts,
            end_ts=ts,
            open=arr_open[i],
            high=arr_high[i],
            low=arr_low[i],
            close=close_f,
            volume=vol * 100 if vol > 0 else 0,  # vol 是手, 转为股
            vwap=vwap_val,
            prev_close=prev_close_f,
            is_suspended=is_suspended,
            is_st=meta.get("is_st", False),
            is_limit_up=_is_limit_up,
            is_limit_down=_is_limit_down,
            metadata=meta,
        )
        bars_by_date[trade_date_obj][symbol] = bar

    if n_rows > _log_interval:
        logger.info("  _build_bars_from_bulk: 完成 %d 行", n_rows)


def _load_tushare_data(config: BacktestRunConfig) -> dict[date, dict]:
    """通过 TuShareProBarProvider + TuShareEquityProvider 加载 A-stock 数据

    当配置中包含 equity_data 节或策略属于 EquityRotation 系列时，
    自动加载 daily_basic（流通市值/PE/PB/换手率）和 stock_basic（上市日期/名称/ST）
    并注入到 Bar.metadata 中，使策略的 ``_filter_universe()`` 和
    ``_score_candidates()`` 能正常工作。

    支持内存缓存：相同数据源/策略/benchmark 组合只加载一次，
    参数扫描/Walk-Forward 等场景下显著提速。
    如果缓存中的日期范围覆盖请求范围，直接过滤返回子集。
    """
    from datetime import date as _date_cls

    # 内存缓存命中检查（支持日期范围超集匹配）
    base_key = _tushare_cache_base_key(config)
    if base_key in _tushare_data_cache:
        cached_start, cached_end = _tushare_data_cache_ranges[base_key]
        req_start = config.data.start_date
        req_end = config.data.end_date
        if cached_start <= req_start and cached_end >= req_end:
            cached = _tushare_data_cache[base_key]
            # 过滤到请求的日期范围
            filtered = {
                d: bars for d, bars in cached.items()
                if req_start <= d <= req_end
            }
            logger.info(
                f"TuShare 数据命中内存缓存 ({len(filtered)}/{len(cached)} 交易日)"
            )
            return filtered

    import os
    from collections import defaultdict

    from aipha.data.tushare_provider import TuShareProBarProvider
    from aipha.domain.models import Bar

    token = os.environ.get("TUSHARE_TOKEN") or getattr(
        config.data, "tushare_token", None
    )
    if not token:
        raise ValueError(
            "TUSHARE_TOKEN 未配置且 config.data.tushare_token 为空。"
            "数据源为 'tushare' 时必须提供有效 Token。"
            "请在 .env 文件中设置 TUSHARE_TOKEN 或在配置中指定 tushare_token。"
            "如需使用合成数据进行测试，请显式设置 data.source: synthetic"
        )

    provider = TuShareProBarProvider(
        token=token,
        cache_dir=config.data.cache_dir or "data/cache/tushare",
        adjust=config.data.adjust,
        max_missing_rate=config.data.max_missing_rate
        if hasattr(config.data, "max_missing_rate") and config.data.max_missing_rate is not None
        else 0.20,  # 长周期回测（10年+）允许 20% 缺失（停牌/上市不足等）
    )

    # ------------------------------------------------------------------
    # 判断是否需要加载 equity 元数据（daily_basic + stock_basic）
    # ------------------------------------------------------------------
    _EQUITY_ROTATION_STRATEGIES = {
        # --- Original ---
        "multifactor_enhance", "pead_event", "smallcap_rotation",
        "balanced_multifactor",
        "factor_based", "factor",
        "dividend_defensive", "sector_momentum", "value_quality",
        "earnings_momentum", "ml_predict", "ml_factor",
        "ml_factor_ensemble",
        "quality_growth", "comprehensive_alpha",
        "adaptive_multifactor", "micro_cap_value",
        # --- G1: Value/Quality ---
        "dividend_quality_select", "low_vol_value", "dividend_quality",
        "fscore_value_enhanced", "cash_cow_select", "stable_compounder",
        # --- G2: Growth/Momentum ---
        "quality_momentum_combo", "ic_adaptive_multifactor",
        "garp_select", "profitability_composite", "composite_rank_equal",
        # --- G3: Small-cap/Event ---
        "small_cap_quality", "momentum_acceleration",
        "earnings_surprise_alpha", "turnaround_play", "volume_price_breakout",
        # --- G4: Risk-managed ---
        "trend_quality_managed", "vol_managed_value",
        "regime_switch", "breadth_controlled", "max_drawdown_guard",
        # --- G5: Event-driven ---
        "pead_drift", "buyback_signal",
        "st_removal_play", "high_delivery_expect", "insider_buying",
        "a_stock_event_pead",
        # --- G6: Style/Sector rotation ---
        "sector_momentum_plus", "size_style_rotation",
        "value_growth_switch", "industry_ic_tilt", "northbound_enhanced",
        # --- R55: A-stock multifactor baseline ---
        "a_stock_multifactor",
        # --- A-stock enhanced v2 (8-factor) ---
        "a_stock_enhanced_v2",
        # --- R71: Bucket multi-factor (3-bucket by market cap) ---
        "bucket_multifactor",
        # --- R76: Reference five-factor (EP/BP/ROE/DY/LV + quadrant) ---
        "reference_five_factor",
        # --- R107: v19_hgro micro-cap growth rotation ---
        "v19_hgro",
        # --- Phase 2: v20_icw IC-weighted micro-cap rotation ---
        "v20_icw",
        # --- v21_vds: value-dividend-smallcap (replicate china_select_value_30) ---
        "v21_vds",
        # --- v22_bmf: balanced multi-factor (replicate ZH1400599) ---
        "v22_bmf",
        # --- v23_sbal: style-balanced rotation (FF 2×2 quadrant) ---
        "v23_sbal",
        # --- three_sleeve_equity: 三袖轮动 (micro/large/mid) ---
        "three_sleeve_equity",
        # --- overreaction: IVOL+MAX+ABT behavioral factor (De Bondt & Thaler) ---
        "overreaction",
        # --- CSI500 quality sleeve: ROE+PE+LowVol+LowTurnover+EarningsStab ---
        "csi500_quality_sleeve",
        # --- behavioral_sleeve: ABT+reversal+IVOL+MAX ---
        "behavioral_sleeve",
        # --- R178 M3: Quality+LowVol (CSI300 ROE/ROIC/leverage) ---
        "quality_lowvol",
    }
    need_equity_meta = (
        config.strategy.name in _EQUITY_ROTATION_STRATEGIES
        or getattr(config.data, "equity_data", None) is not None
    )

    equity_provider = None
    stock_basic_df = None
    daily_basic_map: dict = {}  # date -> DataFrame
    fina_pit_map: dict = {}     # symbol -> sorted list of (ann_date, row_dict)

    if need_equity_meta:
        try:
            from aipha.data.tushare_equity_provider import TuShareEquityProvider

            # equity_provider 使用独立缓存目录（与 bar provider 分离）
            # bar provider: data/cache/tushare（日线行情）
            # equity provider: data/cache/equity_tushare（stock_basic, daily_basic, fina_indicator, index_members）
            eq_cache = "data/cache/equity_tushare"
            equity_provider = TuShareEquityProvider(token=token, cache_dir=eq_cache)

            # 加载股票基本信息（名称、上市日期、行业）
            logger.info("加载 stock_basic 元数据...")
            stock_basic_df = equity_provider.load_stock_basic()
            if stock_basic_df is not None:
                stock_basic_df = stock_basic_df.set_index("ts_code")
            logger.info(
                f"  stock_basic: {len(stock_basic_df) if stock_basic_df is not None else 0} 只"
            )

            # 加载 daily_basic（市值/PE/PB/换手率）
            logger.info("加载 daily_basic 元数据...")
            try:
                start_str = config.data.start_date.strftime("%Y%m%d")
                end_str = config.data.end_date.strftime("%Y%m%d")
                db_range = equity_provider.load_daily_basic_range(
                    start_str, end_str,
                )
                if db_range is not None and not db_range.empty:
                    for td, grp in db_range.groupby("trade_date"):
                        daily_basic_map[td] = grp.set_index("ts_code")
                    logger.info(f"  daily_basic: {len(daily_basic_map)} 个交易日")
            except (IOError, KeyError, ValueError) as e:
                logger.warning(f"daily_basic_range 加载失败，尝试逐日加载: {e}")
        except ImportError as e:
            logger.warning(f"TuShareEquityProvider 模块不可用: {e}")
        except Exception as e:
            raise RuntimeError(
                f"TuShareEquityProvider 初始化或 stock_basic 加载失败: {e}\n"
                "请检查 TUSHARE_TOKEN 是否有效、网络是否连通。"
            ) from e

    # ------------------------------------------------------------------
    # 解析标的范围（symbols）——必须在加载 fina_indicator 之前完成
    # ------------------------------------------------------------------
    symbols = config.data.symbols or ["000001.SZ", "600000.SH"]

    # 从策略参数中提取 target_symbols（演示策略用法）
    strat_params = config.strategy.params or {}
    if not config.data.symbols and "target_symbols" in strat_params:
        ts = strat_params["target_symbols"]
        if isinstance(ts, list) and ts:
            symbols = ts
            logger.info(f"策略指定标的模式: {len(symbols)} 只股票")

    # 防御性检查：策略需要 equity 元数据但仍使用默认 2 只股票（P0 级静默失败风险）
    if (
        need_equity_meta
        and not config.data.symbols
        and "target_symbols" not in (config.strategy.params or {})
        and stock_basic_df is None
    ):
        raise RuntimeError(
            f"策略 '{config.strategy.name}' 需要全市场 A 股数据，"
            f"但 stock_basic 加载失败（equity_provider 不可用）。"
            f"这会导致仅加载 2 只默认大盘股并产生 0 笔交易。\n"
            f"请检查: 1) TUSHARE_TOKEN 是否有效 "
            f"2) data/cache/equity_tushare/stock_basic.parquet 是否存在 "
            f"3) 网络连接是否正常"
        )

    # 如果策略需要全市场数据且未指定 symbols，优先使用指数成分股
    constituent_checker: IndexConstituentChecker | None = None
    if (
        need_equity_meta
        and not config.data.symbols
        and stock_basic_df is not None
    ):
        # 尝试从配置中获取基准指数，用指数成分股限制范围
        index_code = None
        strat_params = config.strategy.params or {}
        index_code = strat_params.get("benchmark") or strat_params.get("base_index")

        if not index_code:
            # 从 universe 配置获取
            index_code = getattr(config.universe, "base_index", None)

        if index_code and equity_provider is not None:
            try:
                # -------------------------------------------------------
                # 动态成分股模式（修复前视偏差）：
                # 1) 加载所有可用的半年度成分快照
                # 2) 构建 IndexConstituentChecker（时间维度归属判断）
                # 3) 用并集作为数据加载范围（保证退市/调出股也有行情）
                # 4) 逐 bar 注入 is_index_member 标记，策略层据此过滤
                # -------------------------------------------------------
                end_year = config.data.end_date.year if config.data.end_date else 2025
                snapshots = equity_provider.load_index_members_snapshots(
                    index_code,
                    start_year=max(2016, config.data.start_date.year),
                    end_year=end_year,
                )

                if snapshots:
                    constituent_checker = IndexConstituentChecker(snapshots)
                    symbols = constituent_checker.all_symbols_union
                    logger.info(
                        f"动态成分模式({index_code}): "
                        f"{constituent_checker.snapshot_count()} 个快照, "
                        f"并集 {len(symbols)} 只股票"
                    )
                else:
                    # 无快照数据（极早期起始日或 API 不可用）
                    # 回退到起始日单快照
                    bt_start_str = config.data.start_date.strftime("%Y%m%d")
                    idx_df = equity_provider.load_index_members(
                        index_code, trade_date=bt_start_str
                    )
                    if idx_df is not None and not idx_df.empty:
                        symbols = idx_df["con_code"].unique().tolist()
                        logger.warning(
                            f"无半年度快照，回退到起始日成分({bt_start_str}): "
                            f"{len(symbols)} 只"
                        )
                    else:
                        active = stock_basic_df[stock_basic_df["list_status"] == "L"]
                        symbols = active.index.tolist()
                        logger.warning(
                            f"未获取到 {index_code} 任何成分数据，"
                            f"回退到全市场模式: {len(symbols)} 只活跃股票"
                        )
            except Exception as e:
                raise RuntimeError(
                    f"加载指数 {index_code} 成分股失败: {e}\n"
                    "配置了 index_code 但无法获取成分股数据，"
                    "为防止静默切换到全市场回测，终止执行。\n"
                    "如确需全市场模式，请移除策略参数中的 benchmark/base_index。"
                ) from e
        else:
            active = stock_basic_df[stock_basic_df["list_status"] == "L"]
            symbols = active.index.tolist()
            logger.info(f"全市场模式: 共 {len(symbols)} 只活跃股票")

    # ------------------------------------------------------------------
    # 加载 fina_indicator（财务指标）用于 PEAD / multifactor 策略
    # 遵循 PIT 原则：使用 ann_date 控制数据可见性
    # ------------------------------------------------------------------
    _FINA_STRATEGIES = {
        # --- Original ---
        "pead_event", "multifactor_enhance", "smallcap_rotation",
        "balanced_multifactor",
        "factor_based", "factor",
        "sector_momentum", "value_quality", "earnings_momentum",
        "dividend_defensive", "ml_predict", "ml_factor",
        "ml_factor_ensemble",
        "quality_growth", "comprehensive_alpha",
        "adaptive_multifactor", "micro_cap_value",
        # --- G1: Value/Quality ---
        "dividend_quality_select", "low_vol_value", "dividend_quality",
        "fscore_value_enhanced", "cash_cow_select", "stable_compounder",
        # --- G2: Growth/Momentum ---
        "quality_momentum_combo", "ic_adaptive_multifactor",
        "garp_select", "profitability_composite", "composite_rank_equal",
        # --- G3: Small-cap/Event ---
        "small_cap_quality", "momentum_acceleration",
        "earnings_surprise_alpha", "turnaround_play", "volume_price_breakout",
        # --- G4: Risk-managed ---
        "trend_quality_managed", "vol_managed_value",
        "regime_switch", "breadth_controlled", "max_drawdown_guard",
        # --- G5: Event-driven ---
        "pead_drift", "buyback_signal",
        "st_removal_play", "high_delivery_expect", "insider_buying",
        "a_stock_event_pead",
        # --- G6: Style/Sector rotation ---
        "sector_momentum_plus", "size_style_rotation",
        "value_growth_switch", "industry_ic_tilt", "northbound_enhanced",
        # --- R55: A-stock multifactor baseline ---
        "a_stock_multifactor",
        # --- A-stock enhanced v2 (8-factor) ---
        "a_stock_enhanced_v2",
        # --- R71: Bucket multi-factor (3-bucket by market cap) ---
        "bucket_multifactor",
        # --- R76: Reference five-factor ---
        "reference_five_factor",
        # --- R107: v19_hgro micro-cap growth rotation ---
        "v19_hgro",
        # --- Phase 2: v20_icw IC-weighted micro-cap rotation ---
        "v20_icw",
        # --- v21_vds: value-dividend-smallcap ---
        "v21_vds",
        # --- v22_bmf: balanced multi-factor (needs Piotroski/ROE/growth) ---
        "v22_bmf",
        # --- v23_sbal: style-balanced rotation (FF 2×2 quadrant, needs ROE/revenue_yoy) ---
        "v23_sbal",
        # --- three_sleeve_equity: 三袖轮动 (needs ROE/growth/EP) ---
        "three_sleeve_equity",
        # --- CSI500 quality sleeve: needs ROE/PE/Piotroski ---
        "csi500_quality_sleeve",
        # --- R178 M3: Quality+LowVol (needs ROE/ROIC/debt_to_assets) ---
        "quality_lowvol",
    }
    use_oos_replay = bool(
        strat_params.get("oos_predictions_path")
        or strat_params.get("oos_predictions_paths")
    )
    skip_fina_for_oos_replay = bool(
        strat_params.get("skip_fina_for_oos_replay", False)
    )
    if (
        config.strategy.name in _FINA_STRATEGIES
        and equity_provider is not None
        and not (skip_fina_for_oos_replay and use_oos_replay)
    ):
        fina_pit_map = _load_fina_indicators_pit(
            equity_provider, symbols, config,
        )
    elif skip_fina_for_oos_replay and use_oos_replay:
        logger.info(
            "跳过 fina_indicator 加载: %s 使用 OOS replay 专用配置",
            config.strategy.name,
        )

    # ------------------------------------------------------------------
    # 加载 namechange（曾用名）用于历史 ST 判断
    # ------------------------------------------------------------------
    namechange_st_index: dict[str, list[tuple]] = {}
    if equity_provider is not None:
        try:
            nc_df = equity_provider.load_namechange()
            if nc_df is not None and not nc_df.empty:
                namechange_st_index = _build_st_interval_index(nc_df)
                logger.info(f"namechange ST 索引: {len(namechange_st_index)} 只股票有 ST 区间")
        except Exception as e:
            logger.warning(f"加载 namechange 失败（将回退到 stock_basic.name）: {e}")

    # ------------------------------------------------------------------
    # 优先尝试批量加载路径（symbols > 阈值且有 bulk 文件时）
    # ------------------------------------------------------------------
    if len(symbols) >= _BULK_LOAD_THRESHOLD:
        bulk_result = _load_bars_bulk(
            symbols, config, equity_provider,
            stock_basic_df, daily_basic_map,
            fina_pit_map, namechange_st_index,
            constituent_checker,
        )
        if bulk_result:
            logger.info("使用批量加载路径完成 (%d 交易日)", len(bulk_result))
            # 注入基准指数行情
            benchmark_sym = config.risk.benchmark_symbol
            if benchmark_sym:
                from aipha.data.tushare_provider import TuShareProBarProvider
                import os
                token = os.environ.get("TUSHARE_TOKEN")
                _prov = TuShareProBarProvider(
                    token=token,
                    cache_dir=config.data.cache_dir or "data/cache/tushare",
                    adjust=config.data.adjust,
                )
                _inject_benchmark_bars(bulk_result, _prov, benchmark_sym, config)
            # 存入内存缓存
            base_key = _tushare_cache_base_key(config)
            _tushare_data_cache[base_key] = bulk_result
            _tushare_data_cache_ranges[base_key] = (
                config.data.start_date, config.data.end_date,
            )
            return bulk_result
        logger.info("批量加载未返回数据，回退到逐股加载")

    bars_by_date: dict = defaultdict(dict)

    # 频率映射：配置格式 → TuShareProBarProvider 格式
    _FREQ_MAP = {"1d": "daily", "d": "daily", "day": "daily"}
    raw_freq = config.data.frequency or "daily"
    data_freq = _FREQ_MAP.get(raw_freq, raw_freq)

    # ------------------------------------------------------------------
    # 预构建 list_date 索引：避免对上市前月份发起无效 API 调用
    # 例如 CSI1000 并集 2391 只股票中，很多在 2018+ 才上市，
    # 若回测 start_date=2016-01-04，对每只股票逐月检查 2016-2017
    # 会产生数万次空返回的 pro_bar() 调用。
    # ------------------------------------------------------------------
    _list_date_map: dict[str, date] = {}  # symbol → list_date
    if stock_basic_df is not None:
        for sym in symbols:
            if sym in stock_basic_df.index:
                ld_str = stock_basic_df.loc[sym].get("list_date", "")
                if ld_str:
                    try:
                        ld_str = str(ld_str)[:8]
                        _list_date_map[sym] = date(
                            int(ld_str[:4]), int(ld_str[4:6]), int(ld_str[6:8])
                        )
                    except (ValueError, TypeError, IndexError):
                        pass
        if _list_date_map:
            n_early = sum(
                1 for ld in _list_date_map.values()
                if ld > config.data.start_date
            )
            logger.info(
                f"list_date 优化: {n_early}/{len(symbols)} 只股票上市晚于回测起始日，"
                f"将跳过上市前月份的 API 调用"
            )

    for i, symbol in enumerate(symbols):
        if (i + 1) % 50 == 0 or i == 0 or i == len(symbols) - 1:
            logger.info(f"加载 Bar 数据: {i + 1}/{len(symbols)} ({symbol})...")
        try:
            # 根据上市日期收窄请求范围，避免对上市前月份发起 API 调用
            effective_start = config.data.start_date
            if symbol in _list_date_map:
                list_date = _list_date_map[symbol]
                if list_date > effective_start:
                    effective_start = list_date

            bars = provider.load_bars(
                symbol=symbol,
                start=effective_start,
                end=config.data.end_date,
                frequency=data_freq,
            )
            for bar in bars:
                trade_date = bar.start_ts.date()

                # 注入 equity 元数据
                meta = dict(bar.metadata) if bar.metadata else {}

                # OHLCV 原始 amount（千元）：provider 未存入 metadata，
                # 从 volume(股) * vwap(元/股) 推算
                if "amount" not in meta and bar.volume and bar.vwap:
                    meta["amount"] = (bar.volume * bar.vwap) / 1000

                _enrich_bar_metadata(
                    meta, symbol, trade_date,
                    stock_basic_df, daily_basic_map,
                    fina_pit_map,
                    namechange_st_index=namechange_st_index,
                    constituent_checker=constituent_checker,
                )

                # 创建带元数据的 Bar（Bar 是 frozen dataclass，需重建）
                enriched_bar = Bar(
                    symbol=bar.symbol,
                    start_ts=bar.start_ts,
                    end_ts=bar.end_ts,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                    vwap=bar.vwap,
                    prev_close=bar.prev_close,
                    is_suspended=bar.is_suspended,
                    is_st=meta.get("is_st", bar.is_st),
                    is_limit_up=bar.is_limit_up,
                    is_limit_down=bar.is_limit_down,
                    metadata=meta,
                )
                bars_by_date[trade_date][enriched_bar.symbol] = enriched_bar
            # 成功时重置连续失败计数
            _load_tushare_data._n_consec_fail = 0  # type: ignore[attr-defined]
        except Exception as e:
            n_fail = getattr(_load_tushare_data, "_n_fail", 0) + 1
            _load_tushare_data._n_fail = n_fail  # type: ignore[attr-defined]
            # 连续失败熔断：系统性故障（Token 过期/网络中断）不应逐只空跑
            _consec = getattr(_load_tushare_data, "_n_consec_fail", 0) + 1
            _load_tushare_data._n_consec_fail = _consec  # type: ignore[attr-defined]
            if _consec >= 10:
                raise RuntimeError(
                    f"连续 {_consec} 只股票加载失败，最后错误: {e}\n"
                    "疑似系统性故障（Token 过期/网络中断/积分不足），终止执行。"
                ) from e
            if n_fail <= 5 or (i + 1) % 50 == 0:
                logger.warning(f"加载 {symbol} 失败 ({n_fail}): {e}")
            elif n_fail == 6:
                logger.warning("后续加载失败仅每50只输出一次...")

    if not bars_by_date:
        raise RuntimeError(
            "TuShare 未加载到任何数据。请检查：\n"
            "  1) TUSHARE_TOKEN 是否有效且积分 ≥ 120\n"
            "  2) data.symbols 列表是否正确\n"
            "  3) data.start_date/end_date 范围是否包含交易日\n"
            "  4) 网络连接是否正常\n"
            "如需使用合成数据进行测试，请显式设置 data.source: synthetic"
        )

    result = dict(bars_by_date)
    logger.info(
        f"A 股数据加载完成: {len(result)} 个交易日, "
        f"{len(set().union(*(d.keys() for d in result.values())))} 只标的"
    )

    # --- 加载基准指数行情（用于 MarketRegimeDetector）---
    benchmark_sym = config.risk.benchmark_symbol
    if benchmark_sym:
        _inject_benchmark_bars(result, provider, benchmark_sym, config)

    # 存入内存缓存（记录日期范围，支持超集匹配）
    if base_key in _tushare_data_cache_ranges:
        old_start, old_end = _tushare_data_cache_ranges[base_key]
        # 合并为更宽的范围
        new_start = min(old_start, config.data.start_date)
        new_end = max(old_end, config.data.end_date)
        if new_start < old_start or new_end > old_end:
            # 需要合并旧缓存
            old_data = _tushare_data_cache[base_key]
            old_data.update(result)
            result = old_data
        _tushare_data_cache_ranges[base_key] = (new_start, new_end)
    else:
        _tushare_data_cache_ranges[base_key] = (
            config.data.start_date, config.data.end_date,
        )
    _tushare_data_cache[base_key] = result
    return result


def _inject_benchmark_bars(
    bars_by_date: dict[date, dict],
    provider,
    benchmark_sym: str,
    config: BacktestRunConfig,
) -> None:
    """加载基准指数日线行情并注入到 bars_by_date 中。

    MarketRegimeDetector 需要基准指数的 close 价格来判断市场状态。
    使用 TuShare ``index_daily`` 接口获取指数行情，转换为 Bar 后注入。
    结果缓存到 ``data/cache/tushare/{benchmark_sym}_index_daily.parquet``。

    Args:
        bars_by_date: 已加载的行情数据（会被原地修改）
        provider: TuShareProBarProvider 实例
        benchmark_sym: 基准指数代码（如 000300.SH）
        config: 回测配置
    """
    try:
        import pandas as pd

        from aipha.data.tushare_gateway import get_tushare_gateway
        from aipha.domain.models import Bar

        start_str = config.data.start_date.strftime("%Y%m%d")
        end_str = config.data.end_date.strftime("%Y%m%d")

        # --- 缓存逻辑：先读本地 parquet，仅缺失时调用 API ---
        cache_dir = Path(config.data.cache_dir or "data/cache/tushare")
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"{benchmark_sym}_index_daily.parquet"

        df: pd.DataFrame | None = None
        if cache_path.exists():
            try:
                cached_df = pd.read_parquet(cache_path)
                if cached_df is not None and not cached_df.empty:
                    # 检查缓存是否覆盖请求的日期范围
                    if "trade_date" in cached_df.columns:
                        cached_df = cached_df[
                            (cached_df["trade_date"] >= start_str)
                            & (cached_df["trade_date"] <= end_str)
                        ]
                        if not cached_df.empty:
                            df = cached_df
                            logger.info(
                                f"基准指数 {benchmark_sym} 行情命中缓存: "
                                f"{len(df)} 条记录"
                            )
            except Exception as e:
                logger.warning(f"基准指数缓存读取失败，将重新下载: {e}")

        if df is None:
            gw = get_tushare_gateway()
            df = gw.call(
                "index_daily",
                ts_code=benchmark_sym,
                start_date=start_str,
                end_date=end_str,
            )
            # 写入缓存
            if df is not None and not df.empty:
                try:
                    df.to_parquet(cache_path, index=False)
                    logger.info(
                        f"基准指数 {benchmark_sym} 行情已缓存: {cache_path.name}"
                    )
                except Exception as e:
                    logger.warning(f"基准指数缓存写入失败: {e}")

        if df is None or df.empty:
            logger.warning(f"基准指数 {benchmark_sym} 行情加载失败，市场状态检测将不可用")
            return

        n_injected = 0
        for _, row in df.iterrows():
            try:
                td = date(
                    int(str(row["trade_date"])[:4]),
                    int(str(row["trade_date"])[4:6]),
                    int(str(row["trade_date"])[6:8]),
                )
            except (ValueError, TypeError):
                continue

            if td not in bars_by_date:
                continue

            from datetime import datetime as _dt

            bar = Bar(
                symbol=benchmark_sym,
                start_ts=_dt(td.year, td.month, td.day, 9, 30),
                end_ts=_dt(td.year, td.month, td.day, 15, 0),
                open=float(row.get("open", 0)),
                high=float(row.get("high", 0)),
                low=float(row.get("low", 0)),
                close=float(row.get("close", 0)),
                volume=int(row.get("vol", 0)),
                metadata={"is_index": True},
            )
            bars_by_date[td][benchmark_sym] = bar
            n_injected += 1

        logger.info(
            f"基准指数 {benchmark_sym} 行情已注入: {n_injected}/{len(bars_by_date)} 交易日"
        )
    except Exception as e:
        logger.warning(f"基准指数行情加载失败: {e}")


def _compute_prev_fields(records: list[tuple]) -> None:
    """为 PIT 财务记录计算 _prev 同比/环比对照字段（就地修改）。

    Piotroski F-Score 等因子需要当期 vs 上期的变化方向。
    对每条记录，将上一条记录的指标值以 ``{field}_prev`` 形式注入。

    适用字段（存在即计算）：
        roa, debt_to_assets, current_ratio, grossprofit_margin, gross_margin,
        total_share, asset_turnover

    Args:
        records: [(ann_date, data_dict), ...] 按 ann_date 升序排列。
    """
    _PREV_FIELDS = [
        "roa", "debt_to_assets", "current_ratio",
        "grossprofit_margin", "gross_margin",
        "total_share", "asset_turnover",
    ]
    for i in range(1, len(records)):
        prev_data = records[i - 1][1]
        curr_data = records[i][1]
        for fld in _PREV_FIELDS:
            if fld in prev_data:
                curr_data.setdefault(f"{fld}_prev", prev_data[fld])


def _compute_sue_fields(records: list[tuple]) -> None:
    """为 PIT 财务记录计算 SUE 相关字段（就地修改）。

    对每条记录，基于同季度跨年 EPS 数据计算：
    - eps_yoy_prev: 去年同期 EPS
    - eps_history: 同季度历史 EPS 列表（最近 8 个季度）
    - sue: Standardized Unexpected Earnings = (eps - eps_yoy_prev) / std(surprises)
    - eps_std: 历史 EPS surprise 标准差

    Args:
        records: [(ann_date, data_dict), ...] 按 ann_date 升序排列，
                 data_dict 中需包含 ``eps`` 和 ``_end_date`` 字段。
    """
    import math
    import statistics

    # 按报告期季度分组：key = 月日部分（"0331","0630","0930","1231"）
    # value = [(year, eps, record_index), ...] 按年升序
    quarter_eps: dict[str, list[tuple]] = {}

    for idx, (_, data) in enumerate(records):
        end_date = data.get("_end_date")
        eps = data.get("eps")
        if end_date is None or eps is None:
            continue
        if len(end_date) < 8:
            continue
        year = end_date[:4]
        quarter_key = end_date[4:8]  # "0331", "0630", etc.
        if quarter_key not in quarter_eps:
            quarter_eps[quarter_key] = []
        quarter_eps[quarter_key].append((year, eps, idx))

    # 对每个季度组按年排序
    for qk in quarter_eps:
        quarter_eps[qk].sort(key=lambda x: x[0])

    # 构建同季度 EPS 历史并计算 SUE
    for qk, year_eps_list in quarter_eps.items():
        # 累积 surprise 序列用于计算滚动 std
        surprises: list[float] = []

        for i, (year, eps, rec_idx) in enumerate(year_eps_list):
            data = records[rec_idx][1]

            # eps_yoy_prev: 同季度上一年 EPS
            if i > 0:
                prev_year, prev_eps, _ = year_eps_list[i - 1]
                # 检查跳年：只接受相邻年份（差值 <= 2，允许跳 1 年）
                try:
                    year_gap = int(year) - int(prev_year)
                except (ValueError, TypeError):
                    year_gap = 999
                if year_gap > 2:
                    # 跳年太大，不计算同比 surprise
                    continue
                data["eps_yoy_prev"] = prev_eps
                surprise = eps - prev_eps
                surprises.append(surprise)

                # eps_std: 至少需要 2 个 surprise 才能计算 std
                if len(surprises) >= 2:
                    std_val = statistics.stdev(surprises)
                    data["eps_std"] = std_val
                    if std_val > 1e-9:
                        data["sue"] = surprise / std_val
                    else:
                        # std ≈ 0 说明 surprise 几乎恒定
                        # 正 surprise → 给高分，负 → 低分
                        # 使用统计学惯例值 3.0（约 99.7% 可信度的 z-score 上界）
                        _SUE_EXTREME_ZSCORE = 3.0
                        data["sue"] = math.copysign(_SUE_EXTREME_ZSCORE, surprise) if abs(surprise) > 1e-9 else 0.0
                elif len(surprises) == 1:
                    # 只有 1 个 surprise，无 std，用 |eps_yoy_prev| 归一化
                    if abs(prev_eps) > 1e-9:
                        data["sue"] = surprise / abs(prev_eps)
                    else:
                        data["sue"] = math.copysign(1.0, surprise) if abs(surprise) > 1e-9 else 0.0

            # eps_history: 同季度最近 8 期 EPS（含当期）
            start_hist = max(0, i - 7)
            data["eps_history"] = [ye[1] for ye in year_eps_list[start_hist:i + 1]]

    # 注意：不再清理 _end_date，策略层需要用它判断报告期以做 ROE 年化
    # 旧代码: for _, data in records: data.pop("_end_date", None)


def _load_fina_indicators_pit(
    equity_provider,
    symbols: list[str],
    config,
) -> dict[str, list[tuple]]:
    """批量加载财务指标并构建 PIT (Point-in-Time) 映射。

    PIT 原则：以 ann_date（公告日期）为可见时间点，
    对于任何 trade_date，只返回 ann_date <= trade_date 的最新报告。

    Returns:
        {symbol: [(ann_date, {roe, dt_netprofit_yoy, eps, ...}), ...]}
        每个 symbol 的列表按 ann_date 升序排列。
    """
    import math

    import pandas as pd

    fina_pit_map: dict[str, list[tuple]] = {}
    n_loaded = 0
    n_failed = 0
    n_total = len(symbols)

    logger.info(f"加载 fina_indicator 财务数据（PIT）: {n_total} 只标的...")

    for i, sym in enumerate(symbols):
        if (i + 1) % 100 == 0 or i == 0:
            logger.info(f"  fina_indicator: {i + 1}/{n_total} ({sym})...")
        try:
            df = equity_provider.load_fina_indicator(sym)
            if df is None or df.empty:
                continue

            # 确保 ann_date 是 date 对象
            if "ann_date" not in df.columns:
                continue

            records = []
            for _, row in df.iterrows():
                ann_str = row.get("ann_date")
                if ann_str is None or (isinstance(ann_str, float) and math.isnan(ann_str)):
                    continue
                try:
                    ann_date = pd.Timestamp(str(ann_str)).date()
                except (ValueError, TypeError):
                    continue

                # 提取 end_date（报告期截止日）用于 SUE 同季度跨年匹配
                end_date_str = row.get("end_date")
                end_date_val = None
                if end_date_str is not None:
                    try:
                        end_date_val = str(end_date_str)[:8]  # YYYYMMDD
                    except (TypeError, ValueError):
                        pass

                fina_row = {}
                for col in ["roe", "dt_netprofit_yoy", "eps", "bps",
                            "revenue_yoy", "net_profit_yoy", "op_yoy",
                            "grossprofit_margin", "netprofit_margin",
                            "roa", "current_ratio", "debt_to_assets",
                            "cfps", "total_share", "asset_turnover",
                            # TS-1 Stage 1 expansion (R156)
                            "or_yoy", "netprofit_yoy", "assets_yoy",
                            "ar_turn", "inv_turn", "roic", "ocf_to_or",
                            "basic_eps_yoy", "q_sales_yoy", "q_roe",
                            "ocfps", "assets_turn"]:
                    val = row.get(col)
                    if val is not None:
                        try:
                            f = float(val)
                            if not math.isnan(f):
                                fina_row[col] = f
                        except (TypeError, ValueError):
                            pass

                if end_date_val:
                    fina_row["_end_date"] = end_date_val

                if fina_row:
                    records.append((ann_date, fina_row))

            if records:
                # 按 ann_date 升序排列，供 PIT 二分查找
                records.sort(key=lambda x: x[0])
                # 计算 SUE 相关字段（eps_yoy_prev, eps_std, sue, eps_history）
                _compute_sue_fields(records)
                # 计算 _prev 字段（供 Piotroski F-Score 等因子使用）
                _compute_prev_fields(records)
                fina_pit_map[sym] = records
                n_loaded += 1

        except Exception as e:
            n_failed += 1
            # 连续失败熔断
            _consec_fina = getattr(_load_fina_indicators_pit, "_consec", 0) + 1
            _load_fina_indicators_pit._consec = _consec_fina  # type: ignore[attr-defined]
            if _consec_fina >= 10:
                raise RuntimeError(
                    f"连续 {_consec_fina} 只 fina_indicator 加载失败，最后错误: {e}\n"
                    "疑似系统性故障，终止执行。"
                ) from e
            if n_failed <= 5:
                logger.warning(f"  fina_indicator 加载失败 {sym}: {e}")
        else:
            # 成功时重置连续失败计数
            _load_fina_indicators_pit._consec = 0  # type: ignore[attr-defined]

    # 加载完成后检查失败比例
    if n_total > 0 and n_failed / n_total > 0.5:
        raise RuntimeError(
            f"fina_indicator 加载失败比例过高: {n_failed}/{n_total} ({n_failed/n_total:.0%})\n"
            "超过 50% 标的财务数据无法加载，回测结果不可信。"
        )

    logger.info(
        f"fina_indicator 加载完成: {n_loaded} 只有数据, {n_failed} 只失败"
    )
    return fina_pit_map


def _pit_lookup(
    records: list[tuple],
    trade_date,
) -> dict | None:
    """PIT 查找：返回 ann_date <= trade_date 的最新财务数据。

    使用线性扫描（记录数通常 < 20，性能影响忽略不计）。
    """
    if not records:
        return None

    # records 是 (ann_date, data_dict) 的列表，按 ann_date 升序。
    # 使用线性扫描（记录数通常 < 20），找到最后一个 ann_date <= trade_date 的行。
    result = None
    for ann_date, data in records:
        if ann_date <= trade_date:
            result = data
        else:
            break
    return result


def _build_st_interval_index(
    namechange_df,
) -> dict[str, list[tuple]]:
    """从 namechange DataFrame 构建 {ts_code: [(start, end), ...]} 的 ST 区间索引。

    仅保留名称含 'ST'/'*ST'/'退市' 的区间，供 _enrich_bar_metadata 快速查询。
    """
    import pandas as pd

    st_index: dict[str, list[tuple]] = {}
    if namechange_df is None or namechange_df.empty:
        return st_index

    for _, row in namechange_df.iterrows():
        name = str(row.get("name", ""))
        name_upper = name.upper()
        if "ST" not in name_upper and "退市" not in name:
            continue
        ts_code = row.get("ts_code", "")
        start = row.get("start_date")
        end = row.get("end_date")
        # 标准化为 date
        if pd.notna(start):
            start = pd.Timestamp(start).date()
        else:
            start = date(1990, 1, 1)
        if pd.notna(end):
            end = pd.Timestamp(end).date()
        else:
            end = date(2099, 12, 31)
        st_index.setdefault(ts_code, []).append((start, end))

    return st_index


def _is_st_by_namechange(
    symbol: str,
    trade_date: date,
    namechange_st_index: dict[str, list[tuple]],
) -> bool:
    """利用预构建的 ST 区间索引判断指定日期是否处于 ST 状态。"""
    intervals = namechange_st_index.get(symbol)
    if not intervals:
        return False
    for start, end in intervals:
        if start <= trade_date <= end:
            return True
    return False


def _enrich_bar_metadata(
    meta: dict,
    symbol: str,
    trade_date: date,
    stock_basic_df,
    daily_basic_map: dict,
    fina_pit_map: dict | None = None,
    namechange_st_index: dict | None = None,
    constituent_checker: IndexConstituentChecker | None = None,
) -> None:
    """将 stock_basic、daily_basic 和 fina_indicator 元数据注入到 Bar.metadata。

    就地修改 *meta* 字典。
    fina_indicator 使用 PIT 原则注入（仅可见 ann_date <= trade_date 的数据）。
    ST 判断优先使用 namechange 历史名称记录（消除前视偏差），
    若无 namechange 数据则回退到 stock_basic.name（当前名称）。
    is_index_member 标记由 IndexConstituentChecker 按交易日动态判断。
    """
    # 指数成分归属标记（动态按日判断）
    if constituent_checker is not None:
        meta["is_index_member"] = constituent_checker.is_member(symbol, trade_date)
    # stock_basic: 名称、上市日期、行业
    if stock_basic_df is not None and symbol in stock_basic_df.index:
        row = stock_basic_df.loc[symbol]
        meta.setdefault("name", row.get("name", ""))
        meta.setdefault("list_date", str(row.get("list_date", "")))
        meta.setdefault("industry", row.get("industry", ""))

        # ST 判断：优先 namechange 历史记录（PIT），回退 stock_basic.name
        if namechange_st_index:
            if _is_st_by_namechange(symbol, trade_date, namechange_st_index):
                meta["is_st"] = True
        else:
            # 回退：stock_basic.name 是当前名称，存在前视偏差
            stock_name = row.get("name", "")
            if "ST" in str(stock_name).upper() or "退市" in str(stock_name):
                meta["is_st"] = True

    # daily_basic: 市值、PE、PB、换手率、成交额
    # daily_basic_map key 可能是 str("YYYYMMDD") 也可能是 date 对象
    td_key = trade_date
    if td_key not in daily_basic_map:
        # 尝试字符串格式
        td_str = trade_date.strftime("%Y%m%d") if isinstance(trade_date, date) else str(trade_date)
        if td_str in daily_basic_map:
            td_key = td_str

    if td_key in daily_basic_map:
        db = daily_basic_map[td_key]
        if symbol in db.index:
            row = db.loc[symbol]
            meta.setdefault("circ_mv", _safe_float_or_none(row, "circ_mv"))
            meta.setdefault("total_mv", _safe_float_or_none(row, "total_mv"))
            meta.setdefault("pe_ttm", _safe_float_or_none(row, "pe_ttm"))
            meta.setdefault("pb", _safe_float_or_none(row, "pb"))
            meta.setdefault("turnover_rate", _safe_float(row, "turnover_rate"))
            meta.setdefault("dv_ratio", _safe_float_or_none(row, "dv_ratio"))
            meta.setdefault("dv_ttm", _safe_float_or_none(row, "dv_ttm"))
            # amount: daily_basic 可能无此字段，回退为 0
            if "amount" not in meta:
                meta["amount"] = _safe_float(row, "amount")

    # fina_indicator: 财务指标（PIT 对齐，使用 ann_date 控制可见性）
    if fina_pit_map and symbol in fina_pit_map:
        fina_row = _pit_lookup(fina_pit_map[symbol], trade_date)
        if fina_row:
            for k, v in fina_row.items():
                meta.setdefault(k, v)


def _safe_float(row, col: str, default: float = 0.0) -> float:
    """从 DataFrame 行中安全提取 float，NaN → default。"""
    import math

    val = row.get(col) if hasattr(row, "get") else getattr(row, col, None)
    if val is None:
        return default
    try:
        f = float(val)
        return default if math.isnan(f) else f
    except (ValueError, TypeError):
        return default


def _safe_float_or_none(row, col: str) -> float | None:
    """从 DataFrame 行中安全提取 float，NaN/缺失 → None。

    适用于 dv_ratio、dv_ttm 等需要区分"无数据"和"0"的字段。
    """
    import math

    val = row.get(col) if hasattr(row, "get") else getattr(row, col, None)
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) else f
    except (ValueError, TypeError):
        return None


def _generate_synthetic_data(config: BacktestRunConfig) -> dict[date, dict]:
    """生成合成测试数据"""
    from datetime import datetime, timedelta

    import numpy as np

    from aipha.domain.models import Bar

    symbols = config.data.symbols or ["600000.SH", "000001.SZ"]
    start_date = config.data.start_date
    end_date = config.data.end_date

    bars_by_date: dict = {}
    current_date = start_date
    prices = {symbol: 100.0 for symbol in symbols}
    np.random.seed(config.random_seed or 42)

    while current_date <= end_date:
        if current_date.weekday() >= 5:
            current_date += timedelta(days=1)
            continue

        day_bars: dict = {}
        for symbol in symbols:
            change = np.random.normal(0, 0.02)
            prices[symbol] *= 1 + change

            bar_start = datetime.combine(
                current_date,
                datetime.min.time().replace(hour=9, minute=30),
            )
            bar_end = datetime.combine(
                current_date,
                datetime.min.time().replace(hour=15, minute=0),
            )

            day_bars[symbol] = Bar(
                symbol=symbol,
                start_ts=bar_start,
                end_ts=bar_end,
                open=prices[symbol] * (1 + np.random.normal(0, 0.005)),
                high=prices[symbol] * (1 + abs(np.random.normal(0, 0.01))),
                low=prices[symbol] * (1 - abs(np.random.normal(0, 0.01))),
                close=prices[symbol],
                volume=int(np.random.uniform(1_000_000, 10_000_000)),
            )

        bars_by_date[current_date] = day_bars
        current_date += timedelta(days=1)

    logger.info(f"合成数据生成完成: {len(bars_by_date)} 个交易日, {len(symbols)} 只标的")
    return bars_by_date


