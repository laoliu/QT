"""TuShare 沪深港通持股 Provider — 北向持仓数据获取与缓存.

Phase C / C-1c: 正股北向持仓数据, 供 cb_northbound.py 因子使用.

API: hk_hold (TuShare)
- 输入: trade_date / ts_code / exchange (SH/SZ)
- 输出: code, trade_date, ts_code, name, vol, ratio, exchange
- 限量: 单次 3800 条, 需循环调取
- 积分: 2000+

**重要限制 (2024-08-20 起)**:
    港交所停止发布日度北向持仓, 改为季度披露.
    2024-08-20 之前的历史日数据仍可正常拉取.
    2024-08-20 之后仅有季度数据, 因子需做降频处理.

缓存策略:
    按日缓存: data/cache/hk_hold/hk_hold_{trade_date}.parquet
    历史数据永不过期; 当月数据 1 天过期.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from aipha.data.cache_utils import is_cache_fresh, safe_read_parquet

logger = logging.getLogger(__name__)

TTL_HISTORICAL = 0
TTL_CURRENT_MONTH = 86400

# 港交所停止日度披露的分界日
HK_HOLD_DAILY_CUTOFF = "20240820"


class TuShareHkHoldProvider:
    """TuShare 沪深港通持股数据 Provider."""

    def __init__(
        self,
        token: Optional[str] = None,
        cache_dir: str = "data/cache/hk_hold",
    ):
        from aipha.data.tushare_gateway import get_tushare_gateway
        self._gw = get_tushare_gateway(token=token)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def load_daily(self, trade_date: str) -> pd.DataFrame:
        """加载指定日期北向持仓 (SH + SZ 合并).

        Args:
            trade_date: YYYYMMDD 格式交易日期.

        Returns:
            DataFrame[ts_code, trade_date, vol, ratio, exchange].
            数据不可用时返回空 DataFrame.
        """
        cache = self._cache_path(trade_date)
        cached = safe_read_parquet(cache)
        ttl = (
            TTL_CURRENT_MONTH if self._is_current_month(trade_date)
            else TTL_HISTORICAL
        )
        if cached is not None and is_cache_fresh(cache, ttl):
            return cached
        df = self._fetch_daily(trade_date)
        if df is not None and not df.empty:
            cache.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(cache, index=False)
        return df if df is not None else pd.DataFrame()

    def load_range(
        self, start_date: str, end_date: str,
    ) -> pd.DataFrame:
        """加载日期范围内的北向持仓面板数据.

        逐日拉取并拼接, 利用缓存避免重复请求.
        超过 HK_HOLD_DAILY_CUTOFF 的日期会 log warning 并跳过.

        Args:
            start_date: 起始日期 YYYYMMDD.
            end_date: 结束日期 YYYYMMDD.

        Returns:
            DataFrame[ts_code, trade_date, vol, ratio, exchange].
        """
        from datetime import datetime as dt
        from aipha.data.calendar import CNStockCalendar

        cal = CNStockCalendar()
        sd = dt.strptime(start_date, "%Y%m%d").date()
        ed = dt.strptime(end_date, "%Y%m%d").date()
        trade_days = cal.get_trading_days(sd, ed)
        frames: list[pd.DataFrame] = []
        for d in trade_days:
            ds = d.strftime("%Y%m%d")
            df = self.load_daily(ds)
            if not df.empty:
                frames.append(df)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    def load_stock_history(
        self, ts_code: str, start_date: str = "", end_date: str = "",
    ) -> pd.DataFrame:
        """加载单只股票的北向持仓历史.

        通过 hk_hold(ts_code=...) 直接查询, 独立缓存.

        Args:
            ts_code: TS 股票代码 (如 '600000.SH').
            start_date: 起始日期.
            end_date: 结束日期.

        Returns:
            DataFrame[trade_date, vol, ratio].
        """
        cache_key = ts_code.replace(".", "_")
        suffix = f"_{start_date}" if start_date else ""
        suffix += f"_{end_date}" if end_date else ""
        cache = self.cache_dir / f"stock_{cache_key}{suffix}.parquet"
        cached = safe_read_parquet(cache)
        if cached is not None and is_cache_fresh(cache, TTL_CURRENT_MONTH):
            return cached
        try:
            kwargs: dict = {"ts_code": ts_code}
            if start_date:
                kwargs["start_date"] = start_date
            if end_date:
                kwargs["end_date"] = end_date
            df = self._gw.call("hk_hold", **kwargs)
            if df is not None and not df.empty:
                df = df[df["exchange"].isin(["SH", "SZ"])]
                cols = ["trade_date", "vol", "ratio"]
                df = df[[c for c in cols if c in df.columns]]
                df = df.sort_values("trade_date").reset_index(drop=True)
                cache.parent.mkdir(parents=True, exist_ok=True)
                df.to_parquet(cache, index=False)
            return df if df is not None else pd.DataFrame()
        except Exception as e:
            logger.warning("北向持仓 %s 获取失败: %s", ts_code, e)
            return pd.DataFrame()

    def _fetch_daily(self, trade_date: str) -> pd.DataFrame:
        """从 TuShare 拉取单日数据 (SH + SZ 北向持仓).

        TuShare hk_hold 单次最多 3800 条, SH/SZ 分别拉取后合并.
        """
        if trade_date > HK_HOLD_DAILY_CUTOFF:
            logger.info(
                "北向日度数据 %s 超过截止日 %s (港交所已改季度披露)",
                trade_date, HK_HOLD_DAILY_CUTOFF,
            )
        frames: list[pd.DataFrame] = []
        for exch in ("SH", "SZ"):
            try:
                df = self._gw.call(
                    "hk_hold",
                    trade_date=trade_date,
                    exchange=exch,
                )
                if df is not None and not df.empty:
                    frames.append(df)
            except Exception as e:
                logger.warning(
                    "北向持仓 %s/%s 获取失败: %s", trade_date, exch, e,
                )
        if not frames:
            return pd.DataFrame()
        result = pd.concat(frames, ignore_index=True)
        keep = ["ts_code", "trade_date", "name", "vol", "ratio", "exchange"]
        result = result[[c for c in keep if c in result.columns]]
        logger.info("北向持仓 %s: %d 条", trade_date, len(result))
        return result

    def _cache_path(self, trade_date: str) -> Path:
        """缓存文件路径."""
        return self.cache_dir / f"hk_hold_{trade_date}.parquet"

    def _is_current_month(self, date_str: str) -> bool:
        """判断日期是否在当月."""
        today = date.today()
        return date_str[:6] == today.strftime("%Y%m")
