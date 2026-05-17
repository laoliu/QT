"""TuShare 回购数据 Provider — 股票回购公告数据.

Phase F / F-7: A股基本面 L-4 P2 数据源.

API: repurchase (600积分, 单次2000)
  输出: ts_code, ann_date, end_date, proc, exp_date,
        vol, amount, high_limit, low_limit
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

TTL_RECENT = 86400  # 1 天


class TuShareRepurchaseProvider:
    """TuShare 股票回购数据 Provider."""

    def __init__(
        self,
        token: Optional[str] = None,
        cache_dir: str = "data/cache/governance",
    ):
        from aipha.data.tushare_gateway import get_tushare_gateway
        self._gw = get_tushare_gateway(token=token)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def load_repurchase(
        self,
        ann_date: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> pd.DataFrame:
        """加载回购数据 (repurchase API).

        按公告日范围查询。至少需要 ann_date 或 start_date+end_date。

        Returns:
            DataFrame[ts_code, ann_date, end_date, proc, exp_date,
                      vol, amount, high_limit, low_limit].
        """
        cache_key = ann_date or f"{start_date}_{end_date}"
        cache = self._cache_path("repurchase", cache_key)
        cached = safe_read_parquet(cache)
        if cached is not None and is_cache_fresh(cache, TTL_RECENT):
            return cached
        try:
            kwargs: dict = {}
            if ann_date:
                kwargs["ann_date"] = ann_date
            if start_date:
                kwargs["start_date"] = start_date
            if end_date:
                kwargs["end_date"] = end_date
            df = self._gw.call("repurchase", **kwargs)
            if df is not None and not df.empty:
                df = df.sort_values("ann_date").reset_index(drop=True)
                cache.parent.mkdir(parents=True, exist_ok=True)
                df.to_parquet(cache, index=False)
                logger.info("回购数据: %d 条", len(df))
            return df if df is not None else pd.DataFrame()
        except Exception as e:
            logger.warning("回购数据获取失败: %s", e)
            return pd.DataFrame()

    def _cache_path(self, api: str, key: str) -> Path:
        return self.cache_dir / f"{api}_{key}.parquet"
