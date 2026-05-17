"""TuShare 公募基金持仓 Provider.

Phase F / F-7: A股基本面 L-4 P2 数据源.

API: fund_portfolio (5000积分, 单次5000)
  输入: ts_code(基金代码), symbol(股票代码), ann_date, period,
        start_date, end_date (至少选一)
  输出: ts_code, ann_date, end_date, symbol, mkv, amount,
        stk_mkv_ratio, stk_float_ratio
缓存: data/cache/governance/ 按股票/基金分片 Parquet.
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


class TuShareFundPortfolioProvider:
    """TuShare 公募基金持仓数据 Provider."""

    def __init__(
        self,
        token: Optional[str] = None,
        cache_dir: str = "data/cache/governance",
    ):
        from aipha.data.tushare_gateway import get_tushare_gateway
        self._gw = get_tushare_gateway(token=token)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def load_fund_portfolio(
        self,
        ts_code: str = "",
        symbol: str = "",
        period: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> pd.DataFrame:
        """加载基金持仓数据 (fund_portfolio API).

        Args:
            ts_code: 基金代码 (如 000001.OF).
            symbol: 股票代码 (如 600000.SH, 按被持有股票查询).
            period: 报告期 (如 20231231).
            start_date: 公告开始日期.
            end_date: 公告结束日期.

        Returns:
            DataFrame[ts_code, ann_date, end_date, symbol, mkv,
                      amount, stk_mkv_ratio, stk_float_ratio].
        """
        cache_key = (ts_code or symbol or period or "all").replace(".", "_")
        if start_date:
            cache_key += f"_{start_date}"
        if end_date:
            cache_key += f"_{end_date}"
        cache = self._cache_path("fund_portfolio", cache_key)
        cached = safe_read_parquet(cache)
        if cached is not None and is_cache_fresh(cache, TTL_RECENT):
            return cached
        try:
            kwargs: dict = {}
            if ts_code:
                kwargs["ts_code"] = ts_code
            if symbol:
                kwargs["symbol"] = symbol
            if period:
                kwargs["period"] = period
            if start_date:
                kwargs["start_date"] = start_date
            if end_date:
                kwargs["end_date"] = end_date
            df = self._gw.call("fund_portfolio", **kwargs)
            if df is not None and not df.empty:
                df = df.sort_values("ann_date").reset_index(drop=True)
                cache.parent.mkdir(parents=True, exist_ok=True)
                df.to_parquet(cache, index=False)
                logger.info("基金持仓: %d 条", len(df))
            return df if df is not None else pd.DataFrame()
        except Exception as e:
            logger.warning("基金持仓获取失败: %s", e)
            return pd.DataFrame()

    def _cache_path(self, api: str, key: str) -> Path:
        return self.cache_dir / f"{api}_{key}.parquet"
