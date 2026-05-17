"""TuShare 事件数据 Provider — 涨跌停/停复牌/业绩预告/快报/解禁。

L-4 P0 数据维度扩容: 5 个高 alpha 潜力的事件类 API。

通过 TuShare Gateway 统一网关访问:
- stk_limit: 涨跌停价格
- suspend_d: 停复牌明细
- forecast: 业绩预告
- express: 业绩快报
- share_float: 限售解禁

所有数据本地 Parquet 缓存, 按月分片存储。
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from aipha.data.cache_utils import is_cache_fresh, safe_read_parquet

logger = logging.getLogger(__name__)

# 缓存 TTL
TTL_HISTORICAL = 0          # 历史数据永不过期
TTL_CURRENT_MONTH = 86400   # 当月数据 1 天过期


class TuShareEventProvider:
    """TuShare 事件数据 Provider (涨跌停/停复牌/预告/快报/解禁)。"""

    def __init__(
        self,
        token: Optional[str] = None,
        cache_dir: str = "data/cache/event",
    ):
        from aipha.data.tushare_gateway import get_tushare_gateway
        self._gw = get_tushare_gateway(token=token)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # -- stk_limit: 涨跌停价格 --
    def load_limit_prices(
        self, trade_date: str,
    ) -> pd.DataFrame:
        """加载指定日期全市场涨跌停价格。

        返回字段: ts_code, trade_date, pre_close, up_limit, down_limit
        """
        cache_path = self._cache_path("stk_limit", f"limit_{trade_date}")
        cached = safe_read_parquet(cache_path)
        if cached is not None and is_cache_fresh(
            cache_path,
            TTL_CURRENT_MONTH if self._is_current_month(trade_date)
            else TTL_HISTORICAL,
        ):
            return cached
        try:
            df = self._gw.call("stk_limit", trade_date=trade_date)
            if df is not None and not df.empty:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                df.to_parquet(cache_path, index=False)
                logger.info("涨跌停 %s: %d 条", trade_date, len(df))
            return df if df is not None else pd.DataFrame()
        except Exception as e:
            logger.warning("涨跌停 %s 获取失败: %s", trade_date, e)
            return pd.DataFrame()

    # -- suspend_d: 停复牌明细 --
    def load_suspend(
        self, ts_code: str = "", start_date: str = "", end_date: str = "",
    ) -> pd.DataFrame:
        """加载停复牌数据。

        返回字段: ts_code, trade_date, suspend_timing, suspend_type
        支持按股票或日期范围查询。
        """
        key = f"suspend_{ts_code or 'all'}_{start_date}_{end_date}"
        cache_path = self._cache_path("suspend_d", key)
        cached = safe_read_parquet(cache_path)
        if cached is not None and is_cache_fresh(cache_path, TTL_HISTORICAL):
            return cached
        try:
            kwargs: dict = {}
            if ts_code:
                kwargs["ts_code"] = ts_code
            if start_date:
                kwargs["start_date"] = start_date
            if end_date:
                kwargs["end_date"] = end_date
            df = self._gw.call("suspend_d", **kwargs)
            if df is not None and not df.empty:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                df.to_parquet(cache_path, index=False)
                logger.info("停复牌 %s: %d 条", ts_code or "all", len(df))
            return df if df is not None else pd.DataFrame()
        except Exception as e:
            logger.warning("停复牌数据获取失败: %s", e)
            return pd.DataFrame()

    # -- forecast: 业绩预告 --
    def load_forecast(
        self, ann_date: str = "", ts_code: str = "",
        start_date: str = "", end_date: str = "",
    ) -> pd.DataFrame:
        """加载业绩预告数据 (PIT: 以公告日为准)。

        返回字段: ts_code, ann_date, end_date, type, p_change_min,
                  p_change_max, net_profit_min, net_profit_max, summary
        """
        key = f"forecast_{ts_code or ann_date or 'range'}_{start_date}_{end_date}"
        cache_path = self._cache_path("forecast", key)
        cached = safe_read_parquet(cache_path)
        if cached is not None and is_cache_fresh(cache_path, TTL_HISTORICAL):
            return cached
        try:
            kwargs: dict = {}
            if ann_date:
                kwargs["ann_date"] = ann_date
            if ts_code:
                kwargs["ts_code"] = ts_code
            if start_date:
                kwargs["start_date"] = start_date
            if end_date:
                kwargs["end_date"] = end_date
            df = self._gw.call("forecast", **kwargs)
            if df is not None and not df.empty:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                df.to_parquet(cache_path, index=False)
                logger.info("业绩预告: %d 条", len(df))
            return df if df is not None else pd.DataFrame()
        except Exception as e:
            logger.warning("业绩预告获取失败: %s", e)
            return pd.DataFrame()

    # -- express: 业绩快报 --
    def load_express(
        self, ann_date: str = "", ts_code: str = "",
        start_date: str = "", end_date: str = "",
    ) -> pd.DataFrame:
        """加载业绩快报数据 (PIT: 以公告日为准)。

        返回字段: ts_code, ann_date, end_date, revenue, operate_profit,
                  total_profit, n_income, total_assets, total_hldr_eqy_exc_min_int,
                  diluted_eps, yoy_net_profit, bps
        """
        key = f"express_{ts_code or ann_date or 'range'}_{start_date}_{end_date}"
        cache_path = self._cache_path("express", key)
        cached = safe_read_parquet(cache_path)
        if cached is not None and is_cache_fresh(cache_path, TTL_HISTORICAL):
            return cached
        try:
            kwargs: dict = {}
            if ann_date:
                kwargs["ann_date"] = ann_date
            if ts_code:
                kwargs["ts_code"] = ts_code
            if start_date:
                kwargs["start_date"] = start_date
            if end_date:
                kwargs["end_date"] = end_date
            df = self._gw.call("express", **kwargs)
            if df is not None and not df.empty:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                df.to_parquet(cache_path, index=False)
                logger.info("业绩快报: %d 条", len(df))
            return df if df is not None else pd.DataFrame()
        except Exception as e:
            logger.warning("业绩快报获取失败: %s", e)
            return pd.DataFrame()

    # -- share_float: 限售解禁 --
    def load_share_float(
        self, ts_code: str = "", start_date: str = "", end_date: str = "",
    ) -> pd.DataFrame:
        """加载限售解禁计划数据。

        返回字段: ts_code, ann_date, float_date, float_share, float_ratio,
                  holder_name, share_type
        """
        key = f"share_float_{ts_code or 'all'}_{start_date}_{end_date}"
        cache_path = self._cache_path("share_float", key)
        cached = safe_read_parquet(cache_path)
        if cached is not None and is_cache_fresh(cache_path, TTL_HISTORICAL):
            return cached
        try:
            kwargs: dict = {}
            if ts_code:
                kwargs["ts_code"] = ts_code
            if start_date:
                kwargs["start_date"] = start_date
            if end_date:
                kwargs["end_date"] = end_date
            df = self._gw.call("share_float", **kwargs)
            if df is not None and not df.empty:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                df.to_parquet(cache_path, index=False)
                logger.info("限售解禁 %s: %d 条",
                            ts_code or "all", len(df))
            return df if df is not None else pd.DataFrame()
        except Exception as e:
            logger.warning("限售解禁数据获取失败: %s", e)
            return pd.DataFrame()

    # -- 批量下载: forecast --
    def batch_download_forecast(
        self,
        start_date: str = "20180101",
        end_date: str = "",
        output_dir: str = "data/cache/advanced",
    ) -> dict[str, int]:
        """按月批量下载业绩预告并保存为 forecast_YYYYMM.parquet。

        TuShare forecast API 支持 ann_date 范围查询(全市场)。
        按月分片: 每月一个文件, 便于因子模块增量读取。

        Args:
            start_date: 起始日期 YYYYMMDD
            end_date: 结束日期 YYYYMMDD (默认=今天)
            output_dir: 输出目录

        Returns:
            {YYYYMM: record_count}
        """
        import time

        if not end_date:
            end_date = date.today().strftime("%Y%m%d")
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        stats: dict[str, int] = {}
        months = self._generate_months(start_date, end_date)

        for ym in months:
            out_path = out / f"forecast_{ym}.parquet"
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
                    "forecast",
                    start_date=m_start,
                    end_date=m_end,
                )
                if df is not None and not df.empty:
                    df.to_parquet(out_path, index=False)
                    stats[ym] = len(df)
                    logger.info("forecast %s: %d 条", ym, len(df))
                else:
                    stats[ym] = 0
            except Exception as e:
                logger.warning("forecast %s 下载失败: %s", ym, e)
                stats[ym] = -1
            time.sleep(0.6)  # 限流

        return stats

    # -- 批量下载: express --
    def batch_download_express(
        self,
        start_date: str = "20180101",
        end_date: str = "",
        output_dir: str = "data/cache/advanced",
    ) -> dict[str, int]:
        """按月批量下载业绩快报并保存为 express_YYYYMM.parquet。"""
        import time

        if not end_date:
            end_date = date.today().strftime("%Y%m%d")
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        stats: dict[str, int] = {}
        months = self._generate_months(start_date, end_date)

        for ym in months:
            out_path = out / f"express_{ym}.parquet"
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
                    "express",
                    start_date=m_start,
                    end_date=m_end,
                )
                if df is not None and not df.empty:
                    df.to_parquet(out_path, index=False)
                    stats[ym] = len(df)
                    logger.info("express %s: %d 条", ym, len(df))
                else:
                    stats[ym] = 0
            except Exception as e:
                logger.warning("express %s 下载失败: %s", ym, e)
                stats[ym] = -1
            time.sleep(0.6)

        return stats

    # -- 辅助方法 --
    def _cache_path(self, api: str, key: str) -> Path:
        """构建缓存文件路径。"""
        return self.cache_dir / api / f"{key}.parquet"

    def _is_current_month(self, date_str: str) -> bool:
        """判断日期是否在当月。"""
        today = date.today()
        return date_str[:6] == today.strftime("%Y%m")

    @staticmethod
    def _generate_months(start_date: str, end_date: str) -> list[str]:
        """生成 start_date 到 end_date 的 YYYYMM 月份列表。"""
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
