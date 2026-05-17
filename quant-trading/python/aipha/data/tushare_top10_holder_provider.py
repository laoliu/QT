"""TuShare 前十大股东 Provider.

Phase F / F-7: A股基本面 L-4 P2 数据源.

API: top10_holders (2000积分, 单次3000)
  输入: ts_code(必填), period, ann_date, start_date, end_date
  输出: ts_code, ann_date, end_date, holder_name, hold_amount,
        hold_ratio, hold_float_ratio, hold_change, holder_type
缓存: data/cache/governance/ 按股票分片 Parquet.
PIT: 因子侧以 ann_date 做时点对齐.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import pandas as pd

from aipha.data.cache_utils import is_cache_fresh, safe_read_parquet

logger = logging.getLogger(__name__)

TTL_RECENT = 86400


class TuShareTop10HolderProvider:
    """TuShare 前十大股东数据 Provider."""

    def __init__(
        self,
        token: Optional[str] = None,
        cache_dir: str = "data/cache/governance",
    ):
        from aipha.data.tushare_gateway import get_tushare_gateway
        self._gw = get_tushare_gateway(token=token)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def load_top10_holders(
        self,
        ts_code: str,
        period: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> pd.DataFrame:
        """加载前十大股东数据 (top10_holders API).

        Args:
            ts_code: 股票代码 (必填).
            period: 报告期 (如 20231231).
            start_date: 公告开始日期.
            end_date: 公告结束日期.

        Returns:
            DataFrame[ts_code, ann_date, end_date, holder_name,
                      hold_amount, hold_ratio, hold_float_ratio,
                      hold_change, holder_type].
        """
        cache_key = ts_code.replace(".", "_")
        cache = self._cache_path("top10_holders", cache_key)
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
            if period:
                kwargs["period"] = period
            if start_date:
                kwargs["start_date"] = start_date
            if end_date:
                kwargs["end_date"] = end_date
            df = self._gw.call("top10_holders", **kwargs)
            if df is not None and not df.empty:
                df = df.sort_values("ann_date").reset_index(drop=True)
                cache.parent.mkdir(parents=True, exist_ok=True)
                df.to_parquet(cache, index=False)
                logger.info("前十大股东 %s: %d 条", ts_code, len(df))
            return df if df is not None else pd.DataFrame()
        except Exception as e:
            logger.warning("前十大股东 %s 获取失败: %s", ts_code, e)
            return pd.DataFrame()

    def _cache_path(self, api: str, key: str) -> Path:
        return self.cache_dir / f"{api}_{key}.parquet"
