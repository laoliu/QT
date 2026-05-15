"""TuShare 替代数据 Provider — 筹码分布/资金流/分析师预测/融资融券。

通过 TuShare Gateway 统一网关访问以下 API：
- cyq_perf: 筹码分布成本百分位 (5/15/50/85/95%) + 获利比例
- moneyflow: 个股资金流向 (小/中/大/特大单)
- report_rc: 分析师盈利预测 (EPS/PE/ROE/目标价)
- margin_detail: 融资融券交易明细 (融资买入/融券卖出/余额)

所有数据本地 Parquet 缓存，按月分片存储。
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from aipha.data.cache_utils import is_cache_fresh, safe_read_parquet

logger = logging.getLogger(__name__)

# 缓存 TTL：历史数据永不过期，当月 1 天
TTL_HISTORICAL = 0
TTL_CURRENT_MONTH = 86400


class TuShareAlternativeProvider:
    """TuShare 替代数据 Provider（筹码/资金流/分析师）。

    Args:
        token: TuShare Pro API Token
        cache_dir: 缓存目录
    """

    def __init__(
        self,
        token: Optional[str] = None,
        cache_dir: str = "",
    ):
        from aipha.data.tushare_gateway import get_tushare_gateway

        if not cache_dir:
            from aipha.config import get_cache_dir
            cache_dir = str(get_cache_dir() / "alternative")

        self._gw = get_tushare_gateway(token=token)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        logger.info("AlternativeProvider 初始化, 缓存: %s", self.cache_dir)

    # ------------------------------------------------------------------
    # cyq_perf — 筹码成本分布
    # ------------------------------------------------------------------
    def load_chips_data(
        self, ts_code: str, start_date: str, end_date: str,
    ) -> pd.DataFrame:
        """加载单只股票筹码分布数据（cyq_perf API）。

        返回字段: ts_code, trade_date, his_low, his_high,
                  cost_5pct, cost_15pct, cost_50pct, cost_85pct, cost_95pct,
                  weight_avg, winner_rate
        """
        cache_path = (
            self.cache_dir / f"chips_{ts_code}_{start_date}_{end_date}.parquet"
        )
        cached = safe_read_parquet(cache_path)
        if cached is not None and is_cache_fresh(cache_path, TTL_HISTORICAL):
            return cached
        try:
            df = self._gw.call(
                "cyq_perf",
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
            )
            if df is not None and not df.empty:
                df.to_parquet(cache_path, index=False)
                logger.info("筹码数据 %s: %d 条", ts_code, len(df))
            return df if df is not None else pd.DataFrame()
        except Exception as e:
            logger.warning("筹码数据 %s 获取失败: %s", ts_code, e)
            return pd.DataFrame()

    def load_chips_bulk(self, trade_date: str) -> pd.DataFrame:
        """按日期批量加载全市场筹码数据。

        TuShare cyq_perf 支持按 trade_date 查询全市场。
        """
        cache_path = self.cache_dir / f"chips_daily_{trade_date}.parquet"
        cached = safe_read_parquet(cache_path)
        if cached is not None and is_cache_fresh(cache_path, TTL_HISTORICAL):
            return cached
        try:
            df = self._gw.call("cyq_perf", trade_date=trade_date)
            if df is not None and not df.empty:
                df.to_parquet(cache_path, index=False)
                logger.info("筹码日数据 %s: %d 条", trade_date, len(df))
            return df if df is not None else pd.DataFrame()
        except Exception as e:
            logger.warning("筹码日数据 %s 失败: %s", trade_date, e)
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # moneyflow — 个股资金流
    # ------------------------------------------------------------------
    def load_moneyflow(self, trade_date: str) -> pd.DataFrame:
        """按日期批量加载全市场资金流数据（moneyflow API）。

        返回字段: ts_code, trade_date, buy_sm_vol, sell_sm_vol,
                  buy_md_vol, sell_md_vol, buy_lg_vol, sell_lg_vol,
                  buy_elg_vol, sell_elg_vol, net_mf_vol, net_mf_amount
        """
        cache_path = self.cache_dir / f"moneyflow_{trade_date}.parquet"
        cached = safe_read_parquet(cache_path)
        if cached is not None and is_cache_fresh(cache_path, TTL_HISTORICAL):
            return cached
        try:
            df = self._gw.call("moneyflow", trade_date=trade_date)
            if df is not None and not df.empty:
                df.to_parquet(cache_path, index=False)
                logger.info("资金流 %s: %d 条", trade_date, len(df))
            return df if df is not None else pd.DataFrame()
        except Exception as e:
            logger.warning("资金流 %s 失败: %s", trade_date, e)
            return pd.DataFrame()

    def load_moneyflow_range(
        self, start_date: str, end_date: str,
    ) -> pd.DataFrame:
        """加载日期范围内资金流，逐日缓存合并。"""
        from datetime import date as _date

        from aipha.data.calendar import CNStockCalendar

        cal = CNStockCalendar()
        sd = _date(int(start_date[:4]), int(start_date[4:6]), int(start_date[6:8]))
        ed = _date(int(end_date[:4]), int(end_date[4:6]), int(end_date[6:8]))
        dates = cal.get_trading_days(sd, ed)
        parts: list[pd.DataFrame] = []
        for d in dates:
            ds = d.strftime("%Y%m%d")
            part = self.load_moneyflow(ds)
            if not part.empty:
                parts.append(part)
        if not parts:
            return pd.DataFrame()
        result = pd.concat(parts, ignore_index=True)
        logger.info("资金流范围 %s~%s: %d 条", start_date, end_date, len(result))
        return result

    # ------------------------------------------------------------------
    # report_rc — 分析师预测
    # ------------------------------------------------------------------
    def load_analyst_forecasts(
        self, start_date: str, end_date: str,
    ) -> pd.DataFrame:
        """加载分析师盈利预测数据（report_rc API）。

        返回字段: ts_code, report_date, eps_avg, eps_max, eps_min,
                  pe_avg, pe_max, pe_min, rating_avg, rating_buy,
                  roe_avg, target_price_avg, analyst_count
        逐月拉取并缓存以减少 API 调用量。
        """
        start_dt = pd.Timestamp(start_date)
        end_dt = pd.Timestamp(end_date)
        parts: list[pd.DataFrame] = []
        current = start_dt.to_period("M")
        end_p = end_dt.to_period("M")
        while current <= end_p:
            ym = current.strftime("%Y%m")
            cache_path = self.cache_dir / f"analyst_{ym}.parquet"
            cached = safe_read_parquet(cache_path)
            if cached is not None and is_cache_fresh(
                cache_path, TTL_HISTORICAL
            ):
                parts.append(cached)
            else:
                try:
                    m_start = current.start_time.strftime("%Y%m%d")
                    m_end = current.end_time.strftime("%Y%m%d")
                    df = self._gw.call(
                        "report_rc",
                        start_date=m_start,
                        end_date=m_end,
                    )
                    if df is not None and not df.empty:
                        df.to_parquet(cache_path, index=False)
                        parts.append(df)
                        logger.info("分析师预测 %s: %d 条", ym, len(df))
                except Exception as e:
                    logger.warning("分析师预测 %s 失败: %s", ym, e)
            current += 1
        if not parts:
            return pd.DataFrame()
        result = pd.concat(parts, ignore_index=True)
        logger.info(
            "分析师预测 %s~%s: %d 条", start_date, end_date, len(result)
        )
        return result

    # ------------------------------------------------------------------
    # margin_detail — 融资融券交易明细
    # ------------------------------------------------------------------
    def load_margin_detail(self, trade_date: str) -> pd.DataFrame:
        """按日期加载全市场融资融券明细（margin_detail API）。

        返回字段: ts_code, trade_date, rzye (融资余额),
                  rzmre (融资买入额), rzche (融资偿还额),
                  rqye (融券余额), rqmcl (融券卖出量),
                  rqchl (融券偿还量), rzrqye (融资融券余额)
        """
        cache_path = self.cache_dir / f"margin_{trade_date}.parquet"
        cached = safe_read_parquet(cache_path)
        if cached is not None and is_cache_fresh(
            cache_path, TTL_HISTORICAL
        ):
            return cached
        try:
            df = self._gw.call(
                "margin_detail", trade_date=trade_date,
            )
            if df is not None and not df.empty:
                df.to_parquet(cache_path, index=False)
                logger.info("融资融券 %s: %d 条", trade_date, len(df))
            return df if df is not None else pd.DataFrame()
        except Exception as e:
            logger.warning("融资融券 %s 失败: %s", trade_date, e)
            return pd.DataFrame()

    def load_margin_range(
        self, start_date: str, end_date: str,
    ) -> pd.DataFrame:
        """加载日期范围内融资融券数据，逐日缓存合并。"""
        from datetime import date as _date

        from aipha.data.calendar import CNStockCalendar

        cal = CNStockCalendar()
        sd = _date(
            int(start_date[:4]),
            int(start_date[4:6]),
            int(start_date[6:8]),
        )
        ed = _date(
            int(end_date[:4]),
            int(end_date[4:6]),
            int(end_date[6:8]),
        )
        dates = cal.get_trading_days(sd, ed)
        parts: list[pd.DataFrame] = []
        for d in dates:
            ds = d.strftime("%Y%m%d")
            part = self.load_margin_detail(ds)
            if not part.empty:
                parts.append(part)
        if not parts:
            return pd.DataFrame()
        result = pd.concat(parts, ignore_index=True)
        logger.info(
            "融资融券 %s~%s: %d 条",
            start_date, end_date, len(result),
        )
        return result
    # ------------------------------------------------------------------
    # hsgt_top10 — 沪深股通十大成交股（北向资金个股级别）
    # ------------------------------------------------------------------
    def load_northbound_top10(self, trade_date: str) -> pd.DataFrame:
        """加载沪深股通十大成交股数据（hsgt_top10 API）。

        返回字段: trade_date, ts_code, name, close, change, rank,
                  market_type, amount, net_amount, buy, sell
        """
        cache_path = (
            self.cache_dir / f"northbound_top10_{trade_date}.parquet"
        )
        cached = safe_read_parquet(cache_path)
        if cached is not None and is_cache_fresh(cache_path, TTL_DAILY):
            return cached
        try:
            df = self._gw.call(
                "hsgt_top10", trade_date=trade_date,
            )
            if df is not None and not df.empty:
                df.to_parquet(cache_path, index=False)
                logger.info("北向 top10 %s: %d 条", trade_date, len(df))
            return df if df is not None else pd.DataFrame()
        except Exception as e:
            logger.warning("北向 top10 %s 失败: %s", trade_date, e)
            return pd.DataFrame()

    def load_northbound_top10_range(
        self, start_date: str, end_date: str,
    ) -> pd.DataFrame:
        """加载日期范围内北向 top10 数据，逐日缓存合并。"""
        from datetime import date as _date

        from aipha.data.calendar import CNStockCalendar

        cal = CNStockCalendar()
        sd = _date(
            int(start_date[:4]),
            int(start_date[4:6]),
            int(start_date[6:8]),
        )
        ed = _date(
            int(end_date[:4]),
            int(end_date[4:6]),
            int(end_date[6:8]),
        )
        dates = cal.get_trading_days(sd, ed)
        parts: list[pd.DataFrame] = []
        for d in dates:
            ds = d.strftime("%Y%m%d")
            part = self.load_northbound_top10(ds)
            if not part.empty:
                parts.append(part)
        if not parts:
            return pd.DataFrame()
        result = pd.concat(parts, ignore_index=True)
        logger.info(
            "北向 top10 %s~%s: %d 条",
            start_date, end_date, len(result),
        )
        return result