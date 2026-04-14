"""TuShare Pro A 股数据提供者

提供 A 股基础信息（stock_basic）、每日指标（daily_basic）、日线 OHLCV、
行业分类、复权因子、指数成分股等全方位数据接口。

核心用途：
- 获取全部 A 股列表、上市状态、行业分类
- 获取每日流通市值（circ_mv）、总市值（total_mv）、估值指标（PE/PB/换手率）
- 获取日线 OHLCV 行情数据（前复权），用于因子计算和回测
- 获取行业分类映射（stock_basic.industry / 申万行业）
- 支持本地 Parquet 缓存，避免重复调用 API

数据口径（必须与 configs/ 一致）：
- 复权方式：前复权（qfq），通过 ts.pro_bar 获取
- 市值单位：万元（TuShare 原始口径）
- PE_TTM、PB：TuShare 原始口径
- 缓存粒度：stock_basic 整体缓存，daily/daily_basic 按月缓存

设计原则（对齐 copilot-instructions.md）：
- 作为可插拔 Provider，不绑定策略逻辑
- 数据口径在 configs/ 中显式声明
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from dotenv import load_dotenv

from aipha.data.cache_utils import (
    DEFAULT_TTL_POLICY,
    is_cache_fresh,
    safe_read_parquet,
)

logger = logging.getLogger(__name__)


class TuShareEquityProvider:
    """TuShare Pro A 股数据提供者

    Args:
        token: TuShare Pro API Token（优先从环境变量读取）
        cache_dir: 本地缓存目录
        max_retries: API 最大重试次数
    """

    def __init__(
        self,
        token: Optional[str] = None,
        cache_dir: str = "data/cache/equity_tushare",
        max_retries: int = 20,
    ):
        from aipha.data.tushare_gateway import get_tushare_gateway

        load_dotenv()
        self._gateway = get_tushare_gateway(token=token)
        self.token = self._gateway._token  # 向后兼容

        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_retries = max_retries

        # 通过网关获取 API 对象（向后兼容需要 self.api 的调用方）
        self.api = self._gateway.api
        logger.info(f"TuShareEquityProvider 初始化完成，缓存目录: {self.cache_dir}")

    @staticmethod
    def _to_date(d) -> date:
        """将字符串或 datetime 转为 date 对象"""
        if isinstance(d, date) and not isinstance(d, datetime):
            return d
        if isinstance(d, datetime):
            return d.date()
        if isinstance(d, str):
            s = d[:10].replace("-", "")
            return datetime.strptime(s[:8], "%Y%m%d").date()
        raise TypeError(f"无法转换为 date: {type(d)}")

    @staticmethod
    def _safe_read_parquet(path: Path) -> pd.DataFrame | None:
        """安全读取 Parquet 文件（委托给 cache_utils.safe_read_parquet）。"""
        return safe_read_parquet(path)

    # ------------------------------------------------------------------
    # stock_basic — 全 A 股列表
    # ------------------------------------------------------------------

    def load_stock_basic(self, use_cache: bool = True) -> pd.DataFrame:
        """获取全部 A 股基本信息

        字段：ts_code, symbol, name, area, industry, market, list_date,
              list_status, delist_date, is_hs

        Returns:
            DataFrame，以 ts_code 为唯一标识
        """
        cache_path = self.cache_dir / "stock_basic.parquet"

        if use_cache and cache_path.exists() and is_cache_fresh(cache_path, DEFAULT_TTL_POLICY.metadata):
            logger.info("从缓存加载 stock_basic")
            cached = self._safe_read_parquet(cache_path)
            if cached is not None:
                return cached

        logger.info("从 TuShare API 下载 stock_basic")
        fields = (
            "ts_code,symbol,name,area,industry,market,"
            "list_date,list_status,delist_date,is_hs"
        )

        df = self._retry_call(
            self.api.stock_basic,
            exchange="",
            list_status="L",
            fields=fields,
        )

        # 也获取退市股票（用于历史回测期间的可交易判断）
        df_d = self._retry_call(
            self.api.stock_basic,
            exchange="",
            list_status="D",
            fields=fields,
        )
        df_p = self._retry_call(
            self.api.stock_basic,
            exchange="",
            list_status="P",
            fields=fields,
        )

        df = pd.concat([df, df_d, df_p], ignore_index=True).drop_duplicates(
            subset=["ts_code"]
        )

        df.to_parquet(cache_path, index=False)
        logger.info(f"stock_basic 已缓存: {len(df)} 只股票")
        return df

    # ------------------------------------------------------------------
    # daily_basic — 每日指标（市值/PE/PB/换手率）
    # ------------------------------------------------------------------

    def load_daily_basic(
        self,
        trade_date: str,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """获取单个交易日的 daily_basic 数据

        字段：ts_code, trade_date, close, turnover_rate, pe_ttm, pb,
              total_mv, circ_mv, total_share, float_share

        Args:
            trade_date: 交易日期 YYYYMMDD
            use_cache: 是否使用缓存

        Returns:
            DataFrame
        """
        month = trade_date[:6]
        cache_path = self.cache_dir / f"daily_basic_{month}.parquet"

        if use_cache and cache_path.exists():
            cached = self._safe_read_parquet(cache_path)
            if cached is not None:
                day_data = cached[cached["trade_date"] == trade_date]
                if not day_data.empty:
                    return day_data

        # 下载单日数据
        logger.info(f"从 TuShare API 下载 daily_basic: {trade_date}")
        fields = (
            "ts_code,trade_date,close,turnover_rate,"
            "pe_ttm,pb,total_mv,circ_mv,"
            "total_share,float_share,"
            "dv_ratio,dv_ttm"
        )
        df = self._retry_call(
            self.api.daily_basic,
            trade_date=trade_date,
            fields=fields,
        )

        if df is None or df.empty:
            logger.warning(f"daily_basic 无数据: {trade_date}")
            return pd.DataFrame()

        # 追加到月缓存
        if cache_path.exists():
            existing = self._safe_read_parquet(cache_path)
            if existing is not None:
                df = pd.concat([existing, df], ignore_index=True).drop_duplicates(
                    subset=["ts_code", "trade_date"]
                )

        df.to_parquet(cache_path, index=False)
        return df[df["trade_date"] == trade_date]

    def load_daily_basic_range(
        self,
        start_date: str,
        end_date: str,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """获取日期范围内的 daily_basic 数据

        通过按日遍历+缓存来获取，TuShare daily_basic 每次只支持单日查询。

        Args:
            start_date: 起始日期 YYYYMMDD
            end_date: 结束日期 YYYYMMDD

        Returns:
            合并后的 DataFrame
        """
        from aipha.data.calendar import CNStockCalendar

        cal = CNStockCalendar()
        trade_dates = cal.get_trading_days(
            self._to_date(start_date), self._to_date(end_date),
        )

        dfs = []
        for td in trade_dates:
            td_str = td.strftime("%Y%m%d") if isinstance(td, (date, datetime)) else td
            day_df = self.load_daily_basic(td_str, use_cache=use_cache)
            if not day_df.empty:
                dfs.append(day_df)

        if not dfs:
            return pd.DataFrame()

        return pd.concat(dfs, ignore_index=True)

    # ------------------------------------------------------------------
    # 指数成分股
    # ------------------------------------------------------------------

    def load_index_members(
        self,
        index_code: str = "000852.SH",
        trade_date: str | None = None,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """获取指数成分股列表

        常用指数：
        - 000300.SH: 沪深300
        - 000905.SH: 中证500
        - 000852.SH: 中证1000（小盘代理）

        Args:
            index_code: 指数代码
            trade_date: 查询日期 YYYYMMDD（不填则获取最新）

        Returns:
            DataFrame with columns: index_code, con_code (成分股代码), in_date, out_date, is_new
        """
        # 缓存 key 包含 trade_date，避免不同日期交叉污染
        td_suffix = f"_{trade_date}" if trade_date else "_latest"
        cache_path = self.cache_dir / f"index_members_{index_code.replace('.', '_')}{td_suffix}.parquet"

        if use_cache and cache_path.exists():
            logger.info(f"从缓存加载指数成分: {index_code} (trade_date={trade_date or 'latest'})")
            cached = self._safe_read_parquet(cache_path)
            if cached is not None:
                return cached

        logger.info(f"从 TuShare API 下载指数成分: {index_code}")
        df = self._retry_call(
            self.api.index_weight,
            index_code=index_code,
            trade_date=trade_date,
        )

        if df is not None and not df.empty:
            df.to_parquet(cache_path, index=False)

        return df if df is not None else pd.DataFrame()

    def load_all_index_members_union(
        self,
        index_code: str = "000300.SH",
        start_year: int = 2016,
        end_year: int = 2025,
        use_cache: bool = True,
    ) -> list[str]:
        """获取指数历史成分股并集（跨所有可用日期）

        TuShare 的 index_weight 接口仅 2016 年起有数据，因此 2005-2015 时段
        无法获取精确历史成分。本方法下载所有可用的半年度成分快照，取并集作为
        股票池的"超集"近似。

        Args:
            index_code: 指数代码
            start_year: 开始查询年份（TuShare 最早 ~2016）
            end_year: 结束查询年份

        Returns:
            去重后的成分股代码列表
        """
        cache_path = self.cache_dir / f"index_members_union_{index_code.replace('.', '_')}_{start_year}_{end_year}.parquet"

        if use_cache and cache_path.exists():
            logger.info(f"从缓存加载指数成分并集: {index_code} ({start_year}-{end_year})")
            cached = self._safe_read_parquet(cache_path)
            if cached is not None and "con_code" in cached.columns:
                codes = cached["con_code"].unique().tolist()
                logger.info(f"  并集成分股: {len(codes)} 只")
                return codes

        logger.info(
            f"下载指数历史成分并集: {index_code} ({start_year}-{end_year})"
        )
        all_frames: list[pd.DataFrame] = []

        for year in range(start_year, end_year + 1):
            for month_end in ["0630", "1231"]:
                td = f"{year}{month_end}"
                try:
                    df = self._retry_call(
                        self.api.index_weight,
                        index_code=index_code,
                        trade_date=td,
                    )
                    if df is not None and not df.empty:
                        all_frames.append(df)
                        logger.info(f"  {td}: {len(df)} 条权重记录")
                    time.sleep(0.15)
                except Exception as e:
                    logger.warning(f"  {td}: 获取失败 - {e}")

        if not all_frames:
            logger.warning(f"未获取到{index_code}任何历史成分数据")
            return []

        merged = pd.concat(all_frames, ignore_index=True)
        merged.to_parquet(cache_path, index=False)

        codes = merged["con_code"].unique().tolist()
        logger.info(f"指数成分并集: {len(codes)} 只唯一股票（跨 {len(all_frames)} 个快照）")
        return codes

    # ------------------------------------------------------------------
    # 日线 OHLCV 行情数据（前复权）
    # ------------------------------------------------------------------

    def load_index_members_snapshots(
        self,
        index_code: str = "000300.SH",
        start_year: int = 2016,
        end_year: int = 2025,
        use_cache: bool = True,
    ) -> dict[str, list[str]]:
        """获取指数历史成分股的逐快照数据（半年度）。

        与 ``load_all_index_members_union`` 不同，本方法返回每个快照日期
        对应的成分股列表，而非并集。用于构建时间维度的成分股归属检查器，
        实现回测中成分股动态再平衡，避免前视偏差。

        Args:
            index_code: 指数代码
            start_year: 开始查询年份（TuShare 最早 ~2016）
            end_year: 结束查询年份
            use_cache: 是否使用缓存

        Returns:
            ``{snapshot_date_str: [con_code, ...]}`` 字典，
            snapshot_date_str 格式为 ``YYYYMMDD``。
        """
        cache_path = (
            self.cache_dir
            / f"index_snapshots_{index_code.replace('.', '_')}_{start_year}_{end_year}.parquet"
        )

        if use_cache and cache_path.exists():
            cached = self._safe_read_parquet(cache_path)
            if cached is not None and "con_code" in cached.columns and "snapshot_date" in cached.columns:
                result: dict[str, list[str]] = {}
                for sd, grp in cached.groupby("snapshot_date"):
                    result[str(sd)] = grp["con_code"].tolist()
                logger.info(
                    f"从缓存加载指数成分快照: {index_code} ({len(result)} 个快照)"
                )
                return result

        logger.info(
            f"下载指数成分快照: {index_code} ({start_year}-{end_year})"
        )
        all_frames: list[pd.DataFrame] = []
        for year in range(start_year, end_year + 1):
            for month_end in ["0630", "1231"]:
                # 如果目标日期非交易日，向前回溯最多 10 天寻找最近交易日
                from datetime import date as _date, timedelta as _td
                base = _date(year, int(month_end[:2]), int(month_end[2:]))
                fetched = False
                for offset in range(11):  # 0..10
                    td = (base - _td(days=offset)).strftime("%Y%m%d")
                    try:
                        df = self._retry_call(
                            self.api.index_weight,
                            index_code=index_code,
                            trade_date=td,
                        )
                        if df is not None and not df.empty:
                            df = df.copy()
                            # 使用标准化的半年末日期作为 snapshot_date
                            df["snapshot_date"] = f"{year}{month_end}"
                            all_frames.append(df)
                            fetched = True
                            logger.debug(f"  {year}{month_end}: 实际取自 {td}")
                            break
                        time.sleep(0.15)
                    except Exception as e:
                        logger.warning(f"  {td}: 获取失败 - {e}")
                        break
                if not fetched:
                    logger.warning(f"  {year}{month_end}: 未能获取成分股快照")

        if not all_frames:
            return {}

        merged = pd.concat(all_frames, ignore_index=True)
        merged.to_parquet(cache_path, index=False)

        result = {}
        for sd, grp in merged.groupby("snapshot_date"):
            result[str(sd)] = grp["con_code"].unique().tolist()
        logger.info(
            f"指数成分快照: {len(result)} 个快照, "
            f"总共 {merged['con_code'].nunique()} 只唯一股票"
        )
        return result

    def load_daily_bars(
        self,
        trade_date: str,
        use_cache: bool = True,
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        """获取单个交易日的日线 OHLCV 数据

        通过 TuShare pro.daily() 获取全市场日线数据。
        字段：ts_code, trade_date, open, high, low, close, vol, amount,
              pre_close, change, pct_chg

        Args:
            trade_date: 交易日期 YYYYMMDD
            use_cache: 是否使用缓存
            adjust: 复权方式（qfq/hfq/None）— 仅影响缓存文件名，
                    pro.daily 返回不复权数据，复权需要 adj_factor

        Returns:
            DataFrame，以 ts_code 为主键
        """
        month = trade_date[:6]
        cache_path = self.cache_dir / f"daily_{month}.parquet"

        if use_cache and cache_path.exists():
            cached = self._safe_read_parquet(cache_path)
            if cached is not None:
                day_data = cached[cached["trade_date"] == trade_date]
                if not day_data.empty:
                    return day_data

        logger.info(f"从 TuShare API 下载 daily: {trade_date}")
        fields = (
            "ts_code,trade_date,open,high,low,close,"
            "pre_close,change,pct_chg,vol,amount"
        )
        df = self._retry_call(
            self.api.daily,
            trade_date=trade_date,
            fields=fields,
        )

        if df is None or df.empty:
            logger.warning(f"daily 无数据: {trade_date}")
            return pd.DataFrame()

        # 追加到月缓存
        if cache_path.exists():
            existing = self._safe_read_parquet(cache_path)
            if existing is not None:
                df = pd.concat([existing, df], ignore_index=True).drop_duplicates(
                    subset=["ts_code", "trade_date"]
                )

        df.to_parquet(cache_path, index=False)
        return df[df["trade_date"] == trade_date]

    def load_daily_bars_range(
        self,
        start_date: str,
        end_date: str,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """获取日期范围内的日线 OHLCV 数据

        Args:
            start_date: 起始日期 YYYYMMDD
            end_date: 结束日期 YYYYMMDD

        Returns:
            合并后的 DataFrame
        """
        from aipha.data.calendar import CNStockCalendar

        cal = CNStockCalendar()
        trade_dates = cal.get_trading_days(
            self._to_date(start_date), self._to_date(end_date),
        )

        dfs = []
        for td in trade_dates:
            td_str = td.strftime("%Y%m%d") if isinstance(td, (date, datetime)) else td
            day_df = self.load_daily_bars(td_str, use_cache=use_cache)
            if not day_df.empty:
                dfs.append(day_df)

        if not dfs:
            return pd.DataFrame()

        return pd.concat(dfs, ignore_index=True)

    # ------------------------------------------------------------------
    # 复权因子
    # ------------------------------------------------------------------

    def load_adj_factor(
        self,
        ts_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """获取个股复权因子

        用途：验证前复权价格准确性，或自行计算后复权价格。
        字段：ts_code, trade_date, adj_factor

        Args:
            ts_code: 股票代码（如 000001.SZ）
            start_date: 起始日期 YYYYMMDD
            end_date: 结束日期 YYYYMMDD

        Returns:
            DataFrame with adj_factor
        """
        cache_path = self.cache_dir / f"adj_factor_{ts_code.replace('.', '_')}.parquet"

        if use_cache and cache_path.exists():
            cached = self._safe_read_parquet(cache_path)
            if cached is not None:
                mask = pd.Series(True, index=cached.index)
                if start_date:
                    mask &= cached["trade_date"] >= start_date
                if end_date:
                    mask &= cached["trade_date"] <= end_date
                filtered = cached[mask]
                if not filtered.empty:
                    return filtered

        logger.info(f"从 TuShare API 下载 adj_factor: {ts_code}")
        kwargs: dict = {"ts_code": ts_code}
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date

        df = self._retry_call(self.api.adj_factor, **kwargs)

        if df is None or df.empty:
            logger.warning(f"adj_factor 无数据: {ts_code}")
            return pd.DataFrame()

        # 合并缓存
        if cache_path.exists():
            existing = self._safe_read_parquet(cache_path)
            if existing is not None:
                df = pd.concat([existing, df], ignore_index=True).drop_duplicates(
                    subset=["ts_code", "trade_date"]
                )

        df.to_parquet(cache_path, index=False)
        mask = pd.Series(True, index=df.index)
        if start_date:
            mask &= df["trade_date"] >= start_date
        if end_date:
            mask &= df["trade_date"] <= end_date
        return df[mask]

    def load_adj_factor_bulk(
        self,
        start_date: str,
        end_date: str,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """批量获取全市场复权因子（按月缓存）。

        通过 TuShare adj_factor(trade_date=...) 逐日获取全市场复权因子，
        按月缓存为 adj_factor_YYYYMM.parquet。
        结合 bulk daily 行情可计算一致的前复权价格。

        Args:
            start_date: 起始日期 YYYYMMDD
            end_date: 结束日期 YYYYMMDD

        Returns:
            DataFrame with columns: ts_code, trade_date, adj_factor
        """
        from aipha.data.calendar import CNStockCalendar

        cal = CNStockCalendar()
        sd = self._to_date(start_date)
        ed = self._to_date(end_date)
        trade_dates = cal.get_trading_days(sd, ed)

        # 按月分组
        months: dict[str, list[str]] = {}
        for td in trade_dates:
            td_str = td.strftime("%Y%m%d") if isinstance(td, date) else td
            ym = td_str[:6]
            months.setdefault(ym, []).append(td_str)

        all_parts: list[pd.DataFrame] = []
        for ym in sorted(months.keys()):
            cache_path = self.cache_dir / f"adj_factor_{ym}.parquet"
            dates_in_month = months[ym]
            cached_df = None
            missing_dates = dates_in_month

            if use_cache and cache_path.exists():
                cached_df = self._safe_read_parquet(cache_path)
                if cached_df is not None and not cached_df.empty:
                    cached_dates = set(cached_df["trade_date"].unique())
                    missing_dates = [
                        d for d in dates_in_month if d not in cached_dates
                    ]

            if missing_dates:
                logger.info(
                    "下载 adj_factor %s: %d/%d 日缺失",
                    ym, len(missing_dates), len(dates_in_month),
                )
                new_parts: list[pd.DataFrame] = []
                for td_str in missing_dates:
                    df = self._retry_call(
                        self.api.adj_factor, trade_date=td_str,
                    )
                    if df is not None and not df.empty:
                        new_parts.append(df)

                if new_parts:
                    new_df = pd.concat(new_parts, ignore_index=True)
                    if cached_df is not None and not cached_df.empty:
                        combined = pd.concat(
                            [cached_df, new_df], ignore_index=True,
                        ).drop_duplicates(["ts_code", "trade_date"])
                    else:
                        combined = new_df
                    combined.to_parquet(cache_path, index=False)
                    cached_df = combined

            if cached_df is not None and not cached_df.empty:
                mask = cached_df["trade_date"].isin(set(dates_in_month))
                all_parts.append(cached_df[mask])

        if not all_parts:
            return pd.DataFrame(columns=["ts_code", "trade_date", "adj_factor"])
        result = pd.concat(all_parts, ignore_index=True)
        logger.info(
            "adj_factor 批量加载完成: %d 条, %d 只股票, %d 交易日",
            len(result), result["ts_code"].nunique(),
            result["trade_date"].nunique(),
        )
        return result

    # ------------------------------------------------------------------
    # 行业分类
    # ------------------------------------------------------------------

    def load_industry_mapping(
        self,
        use_cache: bool = True,
    ) -> dict[str, str]:
        """获取行业分类映射（stock_basic.industry 字段）

        返回 {ts_code: industry} 映射字典。
        使用 TuShare stock_basic 中的 industry 字段（证监会行业分类近似）。

        Returns:
            {ts_code: industry_name}，如 {"000001.SZ": "银行", ...}
        """
        df = self.load_stock_basic(use_cache=use_cache)
        mapping: dict[str, str] = {}
        for _, row in df.iterrows():
            ts_code = row.get("ts_code", "")
            industry = row.get("industry", "")
            if ts_code and industry:
                mapping[ts_code] = industry
        return mapping

    def load_sw_industry(
        self,
        level: str = "L1",
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """获取申万行业分类

        TuShare 接口：index_classify（申万行业分类列表）+ index_member（成分股）

        Args:
            level: 行业级别，"L1" = 一级（28 个）, "L2" = 二级, "L3" = 三级

        Returns:
            DataFrame with columns: ts_code (股票代码), index_code (行业指数),
            industry_name (行业名称), level
        """
        cache_path = self.cache_dir / f"sw_industry_{level}.parquet"

        if use_cache and cache_path.exists():
            logger.info(f"从缓存加载申万行业分类: {level}")
            cached = self._safe_read_parquet(cache_path)
            if cached is not None:
                return cached

        logger.info(f"从 TuShare API 下载申万行业分类: {level}")

        # 1. 获取申万行业分类列表
        index_df = self._retry_call(
            self.api.index_classify,
            level=level,
            src="SW2021",
        )

        if index_df is None or index_df.empty:
            logger.warning(f"申万行业分类无数据: {level}")
            return pd.DataFrame()

        # 2. 逐行业获取成分股
        results = []
        for _, row in index_df.iterrows():
            index_code = row["index_code"]
            industry_name = row["industry_name"]

            time.sleep(0.1)  # 额外冷却（网关限流器已统一控制并发与速率）
            members = self._retry_call(
                self.api.index_member,
                index_code=index_code,
            )

            if members is not None and not members.empty:
                members = members[["con_code"]].copy()
                members.rename(columns={"con_code": "ts_code"}, inplace=True)
                members["index_code"] = index_code
                members["industry_name"] = industry_name
                members["level"] = level
                results.append(members)

        if not results:
            return pd.DataFrame()

        df = pd.concat(results, ignore_index=True)
        df.to_parquet(cache_path, index=False)
        logger.info(f"申万行业分类已缓存: {len(df)} 条映射")
        return df

    def load_sw_industry_mapping(
        self,
        level: str = "L1",
        use_cache: bool = True,
    ) -> dict[str, str]:
        """获取申万行业映射字典

        Args:
            level: 行业级别

        Returns:
            {ts_code: industry_name}
        """
        df = self.load_sw_industry(level=level, use_cache=use_cache)
        if df.empty:
            # 降级到 stock_basic.industry
            logger.warning("申万行业数据为空，降级使用 stock_basic.industry")
            return self.load_industry_mapping(use_cache=use_cache)

        mapping: dict[str, str] = {}
        for _, row in df.iterrows():
            mapping[row["ts_code"]] = row["industry_name"]
        return mapping

    # ------------------------------------------------------------------
    # 财务指标（fina_indicator）
    # ------------------------------------------------------------------

    def load_fina_indicator(
        self,
        ts_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """获取个股财务指标数据

        核心字段：roe, roa, grossprofit_margin, netprofit_margin,
                  revenue_yoy, net_profit_yoy, dt_netprofit_yoy,
                  eps, bps, cfps, ann_date, end_date

        PIT 关键：ann_date（公告日期）用于 PIT 对齐。

        Args:
            ts_code: 股票代码
            start_date: 报告期起始 YYYYMMDD
            end_date: 报告期结束 YYYYMMDD

        Returns:
            DataFrame
        """
        cache_path = self.cache_dir / f"fina_indicator_{ts_code.replace('.', '_')}.parquet"

        if use_cache and cache_path.exists():
            cached = self._safe_read_parquet(cache_path)
            if cached is not None and not cached.empty:
                return cached

        logger.info(f"从 TuShare API 下载 fina_indicator: {ts_code}")
        fields = (
            "ts_code,ann_date,end_date,"
            "roe,roe_waa,roa,grossprofit_margin,netprofit_margin,"
            "profit_to_gr,revenue_yoy,net_profit_yoy,dt_netprofit_yoy,"
            "eps,bps,cfps,current_ratio,quick_ratio,"
            "debt_to_assets,op_yoy"
        )

        kwargs: dict = {"ts_code": ts_code, "fields": fields}
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date

        df = self._retry_call(self.api.fina_indicator, **kwargs)

        if df is None or df.empty:
            logger.warning(f"fina_indicator 无数据: {ts_code}")
            return pd.DataFrame()

        # 合并缓存
        if cache_path.exists():
            existing = self._safe_read_parquet(cache_path)
            if existing is not None:
                df = pd.concat([existing, df], ignore_index=True).drop_duplicates(
                    subset=["ts_code", "end_date", "ann_date"]
                )

        df.to_parquet(cache_path, index=False)
        return df

    def load_income(
        self,
        ts_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """获取利润表数据

        核心字段：revenue, operate_profit, n_income, n_income_attr_p,
                  total_cogs, ann_date, end_date

        Args:
            ts_code: 股票代码

        Returns:
            DataFrame
        """
        cache_path = self.cache_dir / f"income_{ts_code.replace('.', '_')}.parquet"

        if use_cache and cache_path.exists():
            cached = self._safe_read_parquet(cache_path)
            if cached is not None and not cached.empty:
                return cached

        logger.info(f"从 TuShare API 下载 income: {ts_code}")
        fields = (
            "ts_code,ann_date,end_date,report_type,"
            "revenue,operate_profit,total_profit,"
            "n_income,n_income_attr_p,total_cogs,"
            "oper_cost,sell_exp,admin_exp,fin_exp"
        )

        kwargs: dict = {"ts_code": ts_code, "fields": fields}
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date

        df = self._retry_call(self.api.income, **kwargs)

        if df is None or df.empty:
            return pd.DataFrame()

        if cache_path.exists():
            existing = self._safe_read_parquet(cache_path)
            if existing is not None:
                df = pd.concat([existing, df], ignore_index=True).drop_duplicates(
                    subset=["ts_code", "end_date", "report_type"]
                )

        df.to_parquet(cache_path, index=False)
        return df

    def load_balancesheet(
        self,
        ts_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """获取资产负债表数据

        核心字段：total_assets, total_liab, total_hldr_eqy_exc_min_int,
                  money_cap, ann_date, end_date

        Args:
            ts_code: 股票代码

        Returns:
            DataFrame
        """
        cache_path = self.cache_dir / f"balancesheet_{ts_code.replace('.', '_')}.parquet"

        if use_cache and cache_path.exists():
            cached = self._safe_read_parquet(cache_path)
            if cached is not None and not cached.empty:
                return cached

        logger.info(f"从 TuShare API 下载 balancesheet: {ts_code}")
        fields = (
            "ts_code,ann_date,end_date,report_type,"
            "total_assets,total_liab,"
            "total_hldr_eqy_exc_min_int,total_hldr_eqy_inc_min_int,"
            "money_cap,accounts_receiv,inventories,"
            "total_cur_assets,total_nca"
        )

        kwargs: dict = {"ts_code": ts_code, "fields": fields}
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date

        df = self._retry_call(self.api.balancesheet, **kwargs)

        if df is None or df.empty:
            return pd.DataFrame()

        if cache_path.exists():
            existing = self._safe_read_parquet(cache_path)
            if existing is not None:
                df = pd.concat([existing, df], ignore_index=True).drop_duplicates(
                    subset=["ts_code", "end_date", "report_type"]
                )

        df.to_parquet(cache_path, index=False)
        return df

    def load_cashflow(
        self,
        ts_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """获取现金流量表数据

        核心字段：n_cashflow_act, n_cashflow_inv_act, n_cash_flows_fnc_act,
                  ann_date, end_date

        Args:
            ts_code: 股票代码

        Returns:
            DataFrame
        """
        cache_path = self.cache_dir / f"cashflow_{ts_code.replace('.', '_')}.parquet"

        if use_cache and cache_path.exists():
            cached = self._safe_read_parquet(cache_path)
            if cached is not None and not cached.empty:
                return cached

        logger.info(f"从 TuShare API 下载 cashflow: {ts_code}")
        fields = (
            "ts_code,ann_date,end_date,report_type,"
            "n_cashflow_act,n_cashflow_inv_act,n_cash_flows_fnc_act,"
            "c_fr_sale_sg,c_paid_goods_s"
        )

        kwargs: dict = {"ts_code": ts_code, "fields": fields}
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date

        df = self._retry_call(self.api.cashflow, **kwargs)

        if df is None or df.empty:
            return pd.DataFrame()

        if cache_path.exists():
            existing = self._safe_read_parquet(cache_path)
            if existing is not None:
                df = pd.concat([existing, df], ignore_index=True).drop_duplicates(
                    subset=["ts_code", "end_date", "report_type"]
                )

        df.to_parquet(cache_path, index=False)
        return df

    # ------------------------------------------------------------------
    # namechange — 股票曾用名（历史 ST 标志来源）
    # ------------------------------------------------------------------

    def load_namechange(
        self,
        ts_code: str | None = None,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """获取股票曾用名记录，用于构建历史 ST 标志时间序列。

        TuShare namechange 接口返回字段：
            ts_code, name, start_date, end_date, ann_date, change_reason

        其中 start_date 表示该名称开始使用日期，end_date 表示结束日期。
        name 中含 'ST'/'*ST'/'退市' 的区间即为 ST 期间。

        Args:
            ts_code: 指定股票代码（如 '000001.SZ'）；为 None 则获取全部
            use_cache: 是否使用本地缓存

        Returns:
            DataFrame，含 ts_code/name/start_date/end_date 等字段
        """
        if ts_code:
            cache_path = self.cache_dir / f"namechange_{ts_code.replace('.', '_')}.parquet"
        else:
            cache_path = self.cache_dir / "namechange_all.parquet"

        if use_cache and cache_path.exists():
            logger.info(f"从缓存加载 namechange: {cache_path.name}")
            cached = self._safe_read_parquet(cache_path)
            if cached is not None:
                return cached

        logger.info(f"从 TuShare API 下载 namechange (ts_code={ts_code or 'ALL'})")
        fields = "ts_code,name,start_date,end_date,ann_date,change_reason"

        kwargs: dict = {"fields": fields}
        if ts_code:
            kwargs["ts_code"] = ts_code

        df = self._retry_call(self.api.namechange, **kwargs)

        if df is not None and not df.empty:
            # 标准化日期字段为 date 类型
            for col in ("start_date", "end_date", "ann_date"):
                if col in df.columns:
                    df[col] = pd.to_datetime(df[col], format="%Y%m%d", errors="coerce")
            df.to_parquet(cache_path, index=False)
            logger.info(f"namechange 已缓存: {len(df)} 条 → {cache_path}")
        else:
            df = pd.DataFrame(columns=["ts_code", "name", "start_date", "end_date", "ann_date", "change_reason"])

        return df

    def is_st_at_date(
        self,
        ts_code: str,
        trade_date: date,
        namechange_df: pd.DataFrame | None = None,
    ) -> bool:
        """判断指定股票在指定交易日是否处于 ST/*ST/退市整理期。

        通过 namechange 历史名称记录进行判断，消除仅用当前名称的前视偏差。

        Args:
            ts_code: 股票代码
            trade_date: 交易日期
            namechange_df: 预加载的 namechange DataFrame（避免重复 IO）

        Returns:
            True 如果该日处于 ST 期间
        """
        if namechange_df is None:
            namechange_df = self.load_namechange(ts_code=ts_code)

        if namechange_df.empty:
            return False

        stock_records = namechange_df[namechange_df["ts_code"] == ts_code]
        if stock_records.empty:
            return False

        td = pd.Timestamp(trade_date)
        for _, row in stock_records.iterrows():
            name = str(row.get("name", ""))
            start = row.get("start_date")
            end = row.get("end_date")

            # 判断名称是否含 ST 标志
            name_upper = name.upper()
            is_st_name = "ST" in name_upper or "退市" in name

            if not is_st_name:
                continue

            # 判断日期是否在有效区间内
            if pd.notna(start) and td < start:
                continue
            if pd.notna(end) and td > end:
                continue
            # 在 ST 名称的有效区间内
            return True

        return False

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _retry_call(self, func, **kwargs) -> pd.DataFrame:
        """带重试 + 限流的 API 调用（通过统一网关）

        固定 1 秒间隔重试，不使用指数退避。
        涉及真金白银的操作，不因临时故障轻易放弃。
        """
        func_name = getattr(func, '__name__', str(func))

        for attempt in range(self.max_retries):
            try:
                self._gateway._semaphore.acquire()
                try:
                    self._gateway._limiter.acquire()
                    result = func(**kwargs)
                    self._gateway._record_stat(func_name, "success")
                    return result if result is not None else pd.DataFrame()
                finally:
                    self._gateway._semaphore.release()
            except Exception as e:
                self._gateway._record_stat(func_name, "error")
                if attempt < self.max_retries - 1:
                    if attempt < 3 or attempt % 10 == 0:
                        logger.warning(
                            "API 调用失败 (尝试 %d/%d): %s, 1s 后重试",
                            attempt + 1, self.max_retries, e,
                        )
                    time.sleep(1)
                else:
                    logger.error(f"API 调用最终失败 ({self.max_retries}次): {e}")
                    raise
        return pd.DataFrame()
