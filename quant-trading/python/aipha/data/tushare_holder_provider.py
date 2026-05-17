"""TuShare 股东数据 Provider — 股东户数 + 股东增减持.

Phase C / C-2a + C-2b: A股基本面因子数据源.

API 覆盖:
- stk_holdernumber: 股东户数 (600积分, 单次3000)
  输出: ts_code, ann_date, end_date, holder_num
- stk_holdertrade: 股东增减持 (2000积分, 单次3000)
  输出: ts_code, ann_date, holder_name, holder_type, in_de,
        change_vol, change_ratio, after_share, after_ratio,
        avg_price, total_share, begin_date, close_date

缓存: data/cache/holder/ 按股票+年份分片 Parquet.
PIT: 因子侧必须以 ann_date 做 PIT 对齐.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import pandas as pd

from aipha.data.cache_utils import is_cache_fresh, safe_read_parquet

logger = logging.getLogger(__name__)

TTL_HISTORICAL = 0
TTL_RECENT = 86400  # 近期数据 1 天过期


class TuShareHolderProvider:
    """TuShare 股东数据 Provider (股东户数 + 增减持)."""

    def __init__(
        self,
        token: Optional[str] = None,
        cache_dir: str = "data/cache/holder",
    ):
        from aipha.data.tushare_gateway import get_tushare_gateway
        self._gw = get_tushare_gateway(token=token)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def load_holdernumber(
        self,
        ts_code: str,
        start_date: str = "",
        end_date: str = "",
    ) -> pd.DataFrame:
        """加载股东户数数据 (stk_holdernumber).

        Returns:
            DataFrame[ts_code, ann_date, end_date, holder_num].
        """
        cache_key = ts_code.replace(".", "_")
        cache = self._cache_path("holdernumber", cache_key)
        cached = safe_read_parquet(cache)
        if cached is not None and is_cache_fresh(cache, TTL_RECENT):
            df = cached
            if start_date:
                df = df[df["ann_date"] >= start_date]
            if end_date:
                df = df[df["ann_date"] <= end_date]
            return df.reset_index(drop=True)
        try:
            kwargs: dict = {"ts_code": ts_code}
            if start_date:
                kwargs["start_date"] = start_date
            if end_date:
                kwargs["end_date"] = end_date
            df = self._gw.call("stk_holdernumber", **kwargs)
            if df is not None and not df.empty:
                df = df.sort_values("ann_date").reset_index(drop=True)
                cache.parent.mkdir(parents=True, exist_ok=True)
                df.to_parquet(cache, index=False)
                logger.info("股东户数 %s: %d 条", ts_code, len(df))
            return df if df is not None else pd.DataFrame()
        except Exception as e:
            logger.warning("股东户数 %s 获取失败: %s", ts_code, e)
            return pd.DataFrame()

    def load_holdernumber_by_date(
        self, ann_date: str,
    ) -> pd.DataFrame:
        """按公告日期加载全市场股东户数."""
        cache = self._cache_path("holdernumber_date", ann_date)
        cached = safe_read_parquet(cache)
        if cached is not None and is_cache_fresh(cache, TTL_HISTORICAL):
            return cached
        try:
            df = self._gw.call("stk_holdernumber", ann_date=ann_date)
            if df is not None and not df.empty:
                cache.parent.mkdir(parents=True, exist_ok=True)
                df.to_parquet(cache, index=False)
                logger.info("股东户数(日期) %s: %d 条", ann_date, len(df))
            return df if df is not None else pd.DataFrame()
        except Exception as e:
            logger.warning("股东户数(日期) %s 获取失败: %s", ann_date, e)
            return pd.DataFrame()

    def load_holdertrade(
        self,
        ts_code: str = "",
        start_date: str = "",
        end_date: str = "",
        trade_type: str = "",
        holder_type: str = "",
    ) -> pd.DataFrame:
        """加载股东增减持数据 (stk_holdertrade).

        Args:
            ts_code: 股票代码 (空=全市场).
            start_date: 公告开始日期.
            end_date: 公告结束日期.
            trade_type: IN增持 / DE减持 (空=全部).
            holder_type: C公司 / P个人 / G高管 (空=全部).

        Returns:
            DataFrame[ts_code, ann_date, holder_name, holder_type,
                      in_de, change_vol, change_ratio, after_share,
                      after_ratio, avg_price, total_share,
                      begin_date, close_date].
        """
        cache_key = ts_code.replace(".", "_") if ts_code else "all"
        if start_date:
            cache_key += f"_{start_date}"
        if end_date:
            cache_key += f"_{end_date}"
        cache = self._cache_path("holdertrade", cache_key)
        cached = safe_read_parquet(cache)
        if cached is not None and is_cache_fresh(cache, TTL_RECENT):
            return cached
        try:
            kwargs: dict = {}
            if ts_code:
                kwargs["ts_code"] = ts_code
            if start_date:
                kwargs["start_date"] = start_date
            if end_date:
                kwargs["end_date"] = end_date
            if trade_type:
                kwargs["trade_type"] = trade_type
            if holder_type:
                kwargs["holder_type"] = holder_type
            df = self._gw.call("stk_holdertrade", **kwargs)
            if df is not None and not df.empty:
                df = df.sort_values("ann_date").reset_index(drop=True)
                cache.parent.mkdir(parents=True, exist_ok=True)
                df.to_parquet(cache, index=False)
                logger.info("增减持 %s: %d 条", ts_code or "all", len(df))
            return df if df is not None else pd.DataFrame()
        except Exception as e:
            logger.warning("增减持 %s 获取失败: %s", ts_code or "all", e)
            return pd.DataFrame()

    def batch_download_holdertrade(
        self,
        start_date: str = "20180101",
        end_date: str = "",
        output_dir: str = "data/cache/holder",
    ) -> dict[str, int]:
        """按月批量下载全市场增减持, 保存为 holdertrade_YYYYMM.parquet。

        stk_holdertrade API 支持全市场查询(不传 ts_code),
        按月分片调用以避免单次返回过多。

        Returns:
            {YYYYMM: record_count}
        """
        import time
        from datetime import date as dt_date

        if not end_date:
            end_date = dt_date.today().strftime("%Y%m%d")

        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        stats: dict[str, int] = {}
        months = self._generate_months(start_date, end_date)

        for ym in months:
            out_path = out / f"holdertrade_{ym}.parquet"
            if out_path.exists():
                try:
                    existing = pd.read_parquet(out_path)
                    if not existing.empty:
                        stats[ym] = len(existing)
                        continue
                except Exception:
                    pass

            m_start = f"{ym}01"
            y, m = int(ym[:4]), int(ym[4:6])
            if m == 12:
                m_end = f"{y + 1}0101"
            else:
                m_end = f"{y}{m + 1:02d}01"

            try:
                df = self._gw.call(
                    "stk_holdertrade",
                    start_date=m_start,
                    end_date=m_end,
                )
                if df is not None and not df.empty:
                    df.to_parquet(out_path, index=False)
                    stats[ym] = len(df)
                    logger.info("holdertrade %s: %d 条", ym, len(df))
                else:
                    stats[ym] = 0
            except Exception as e:
                logger.warning("holdertrade %s 下载失败: %s", ym, e)
                stats[ym] = -1
            time.sleep(0.6)

        return stats

    def batch_download_holdernumber(
        self,
        stock_list: list[str],
        start_date: str = "20180101",
        end_date: str = "",
        output_dir: str = "data/cache/reference/stk_holdernumber",
    ) -> dict[str, int]:
        """逐只下载股东户数, 保存为 stk_holdernumber_{code}.parquet。

        与 analyst_factors.load_holder_data() 期望的文件格式一致。

        Returns:
            {ts_code: record_count}
        """
        import time
        from datetime import date as dt_date

        if not end_date:
            end_date = dt_date.today().strftime("%Y%m%d")

        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        stats: dict[str, int] = {}
        total = len(stock_list)

        for i, code in enumerate(stock_list):
            if (i + 1) % 200 == 0:
                logger.info(
                    "holdernumber 进度: %d/%d (%.0f%%)",
                    i + 1, total, (i + 1) / total * 100,
                )
            safe_code = code.replace(".", "_")
            out_path = out / f"stk_holdernumber_{safe_code}.parquet"
            if out_path.exists():
                try:
                    existing = pd.read_parquet(out_path)
                    if not existing.empty:
                        stats[code] = len(existing)
                        continue
                except Exception:
                    pass

            try:
                df = self.load_holdernumber(code, start_date, end_date)
                if not df.empty:
                    df.to_parquet(out_path, index=False)
                    stats[code] = len(df)
                else:
                    stats[code] = 0
            except Exception as e:
                logger.warning("holdernumber %s 失败: %s", code, e)
                stats[code] = -1
            time.sleep(0.05)

        return stats

    def _cache_path(self, api: str, key: str) -> Path:
        return self.cache_dir / f"{api}_{key}.parquet"

    @staticmethod
    def _generate_months(start_date: str, end_date: str) -> list[str]:
        """生成 YYYYMM 月份列表。"""
        sy, sm = int(start_date[:4]), int(start_date[4:6])
        ey, em = int(end_date[:4]), int(end_date[4:6])
        months: list[str] = []
        y, m = sy, sm
        while (y, m) <= (ey, em):
            months.append(f"{y:04d}{m:02d}")
            m += 1
            if m > 12:
                m = 1
                y += 1
        return months
