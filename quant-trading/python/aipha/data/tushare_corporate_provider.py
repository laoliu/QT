"""TuShare 公司治理数据 Provider (Phase C / C-2).

提供 A 股公司治理维度数据：
- stk_holdernumber: 股东户数变动 (C-2a)
- stk_holdertrade: 高管/股东增减持 (C-2b)
- dividend: 分红方案 (C-2c)
- pledge_stat: 股权质押统计 (C-2d)

所有数据本地 Parquet 缓存，按日期或按年分片。
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from aipha.data.tushare_gateway import TuShareGateway, get_tushare_gateway
from aipha.utils.paths import project_path

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = project_path("data", "cache", "corporate")


class TuShareCorporateProvider:
    """A 股公司治理数据 Provider。"""

    def __init__(
        self,
        gateway: TuShareGateway | None = None,
        cache_dir: Path | None = None,
    ) -> None:
        self._gw = gateway or get_tushare_gateway()
        self._cache_dir = cache_dir or _DEFAULT_CACHE_DIR
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    # ----------------------------------------------------------------
    # C-2a: stk_holdernumber — 股东户数
    # ----------------------------------------------------------------
    def get_holder_number(
        self, ts_code: str, start_date: str = "", end_date: str = "",
    ) -> pd.DataFrame:
        """获取单只股票股东户数变动。

        Returns:
            DataFrame[ann_date, end_date, holder_num, holder_num_change, ...]
        """
        cache_key = f"holdernumber_{ts_code}"
        cache_path = self._cache_dir / f"{cache_key}.parquet"
        if cache_path.exists():
            cached = pd.read_parquet(cache_path)
            if not cached.empty:
                return self._filter_date_range(
                    cached, start_date, end_date, date_col="end_date",
                )

        logger.info("下载 stk_holdernumber: %s", ts_code)
        try:
            df = self._gw.call("stk_holdernumber", ts_code=ts_code)
        except Exception as e:
            logger.warning("stk_holdernumber %s 失败: %s", ts_code, e)
            return pd.DataFrame()

        if df is not None and not df.empty:
            df.to_parquet(cache_path, index=False)
        else:
            df = pd.DataFrame()
        return self._filter_date_range(
            df, start_date, end_date, date_col="end_date",
        )

    # ----------------------------------------------------------------
    # C-2b: stk_holdertrade — 增减持明细
    # ----------------------------------------------------------------
    def get_holder_trade(
        self, ts_code: str = "", ann_date: str = "",
    ) -> pd.DataFrame:
        """获取增减持明细。

        可按个股或公告日查询。

        Returns:
            DataFrame[ts_code, ann_date, holder_name, holder_type,
                      in_de, change_vol, change_ratio, ...]
        """
        kwargs: dict = {}
        if ts_code:
            kwargs["ts_code"] = ts_code
        if ann_date:
            kwargs["ann_date"] = ann_date
        if not kwargs:
            raise ValueError("stk_holdertrade 需要 ts_code 或 ann_date")

        cache_key = f"holdertrade_{ts_code or ann_date}"
        cache_path = self._cache_dir / f"{cache_key}.parquet"
        if cache_path.exists():
            return pd.read_parquet(cache_path)

        logger.info("下载 stk_holdertrade: ts_code=%s ann_date=%s", ts_code, ann_date)
        try:
            df = self._gw.call("stk_holdertrade", **kwargs)
        except Exception as e:
            logger.warning("stk_holdertrade 失败: %s", e)
            return pd.DataFrame()

        if df is not None and not df.empty:
            df.to_parquet(cache_path, index=False)
        else:
            df = pd.DataFrame()
        return df

    # ----------------------------------------------------------------
    # C-2c: dividend — 分红送股
    # ----------------------------------------------------------------
    def get_dividend(
        self, ts_code: str, fields: str = "",
    ) -> pd.DataFrame:
        """获取分红送股方案。

        Returns:
            DataFrame[ts_code, end_date, ann_date, div_proc,
                      stk_div, cash_div, cash_div_tax, ...]
        """
        cache_path = self._cache_dir / f"dividend_{ts_code}.parquet"
        if cache_path.exists():
            return pd.read_parquet(cache_path)

        logger.info("下载 dividend: %s", ts_code)
        try:
            kwargs: dict = {"ts_code": ts_code}
            if fields:
                kwargs["fields"] = fields
            df = self._gw.call("dividend", **kwargs)
        except Exception as e:
            logger.warning("dividend %s 失败: %s", ts_code, e)
            return pd.DataFrame()

        if df is not None and not df.empty:
            df.to_parquet(cache_path, index=False)
        else:
            df = pd.DataFrame()
        return df

    # ----------------------------------------------------------------
    # C-2d: pledge_stat — 股权质押统计
    # ----------------------------------------------------------------
    def get_pledge_stat(
        self, ts_code: str,
    ) -> pd.DataFrame:
        """获取股权质押汇总统计。

        Returns:
            DataFrame[ts_code, end_date, pledge_count, unrest_pledge_ratio,
                      rest_pledge_ratio, total_share_ratio, ...]
        """
        cache_path = self._cache_dir / f"pledge_{ts_code}.parquet"
        if cache_path.exists():
            return pd.read_parquet(cache_path)

        logger.info("下载 pledge_stat: %s", ts_code)
        try:
            df = self._gw.call("pledge_stat", ts_code=ts_code)
        except Exception as e:
            logger.warning("pledge_stat %s 失败: %s", ts_code, e)
            return pd.DataFrame()

        if df is not None and not df.empty:
            df.to_parquet(cache_path, index=False)
        else:
            df = pd.DataFrame()
        return df

    # ----------------------------------------------------------------
    # 工具
    # ----------------------------------------------------------------
    @staticmethod
    def _filter_date_range(
        df: pd.DataFrame,
        start_date: str,
        end_date: str,
        date_col: str = "end_date",
    ) -> pd.DataFrame:
        if df.empty or date_col not in df.columns:
            return df
        if start_date:
            df = df[df[date_col] >= start_date]
        if end_date:
            df = df[df[date_col] <= end_date]
        return df
