"""TuShare 股权质押统计 Provider.

Phase C / C-2d: 质押统计数据源.

API: pledge_stat (TuShare)
- 输入: ts_code, end_date
- 输出: ts_code, end_date, pledge_count, unrest_pledge,
        rest_pledge, total_share, pledge_ratio
- 限量: 单次1000
- 积分: 2000+

缓存: data/cache/pledge/ 按股票 Parquet.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import pandas as pd

from aipha.data.cache_utils import is_cache_fresh, safe_read_parquet

logger = logging.getLogger(__name__)

TTL_HISTORICAL = 0
TTL_RECENT = 86400


class TuSharePledgeProvider:
    """TuShare 股权质押统计 Provider."""

    def __init__(
        self,
        token: Optional[str] = None,
        cache_dir: str = "data/cache/pledge",
    ):
        from aipha.data.tushare_gateway import get_tushare_gateway
        self._gw = get_tushare_gateway(token=token)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def load_pledge_stat(
        self,
        ts_code: str,
        end_date: str = "",
    ) -> pd.DataFrame:
        """加载单只股票质押统计数据.

        Returns:
            DataFrame[ts_code, end_date, pledge_count,
                      unrest_pledge, rest_pledge,
                      total_share, pledge_ratio].
        """
        cache_key = ts_code.replace(".", "_")
        cache = self._cache_path(cache_key)
        cached = safe_read_parquet(cache)
        if cached is not None and is_cache_fresh(cache, TTL_RECENT):
            df = cached
            if end_date:
                df = df[df["end_date"] <= end_date]
            return df.reset_index(drop=True)
        try:
            kwargs: dict = {"ts_code": ts_code}
            if end_date:
                kwargs["end_date"] = end_date
            df = self._gw.call("pledge_stat", **kwargs)
            if df is not None and not df.empty:
                df = df.sort_values("end_date").reset_index(drop=True)
                cache.parent.mkdir(parents=True, exist_ok=True)
                df.to_parquet(cache, index=False)
                logger.info("质押统计 %s: %d 条", ts_code, len(df))
            return df if df is not None else pd.DataFrame()
        except Exception as e:
            logger.warning("质押统计 %s 获取失败: %s", ts_code, e)
            return pd.DataFrame()

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"pledge_{key}.parquet"
