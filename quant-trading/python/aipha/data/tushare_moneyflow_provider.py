"""TuShare 北向资金 Provider (TS-1 Stage 3, R156)

提供个股级北向持股比例 (hk_hold) 和沪深港通资金流 (moneyflow_hsgt)。
用于构造外资信号因子（北向持仓变动、净买入累计等）。

缓存策略：
- hk_hold: 日级横截面 data/cache/tushare/hk_hold_{YYYYMMDD}.parquet
- moneyflow_hsgt: 月级 data/cache/tushare/hsgt_flow_{YYYYMM}.parquet
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from aipha.data.tushare_gateway import TuShareGateway, get_tushare_gateway

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = Path("data/cache/tushare")


class TuShareMoneyflowProvider:
    """北向资金数据 Provider（TuShare hk_hold / moneyflow_hsgt）。"""

    def __init__(
        self,
        gateway: TuShareGateway | None = None,
        cache_dir: Path | None = None,
    ) -> None:
        self._gw = gateway or get_tushare_gateway()
        self._cache_dir = cache_dir or _DEFAULT_CACHE_DIR
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def get_hk_hold_daily(self, trade_date: str) -> pd.DataFrame:
        """北向持股横截面（单日），含 ratio（持股比例%）。

        Args:
            trade_date: YYYYMMDD 格式交易日

        Returns:
            DataFrame with columns: ts_code, trade_date, ratio, vol, ...
        """
        cache_path = self._cache_dir / f"hk_hold_{trade_date}.parquet"
        if cache_path.exists():
            cached = pd.read_parquet(cache_path)
            if not cached.empty:
                return cached

        logger.info("从 TuShare API 下载 hk_hold: %s", trade_date)
        df = self._gw.call(
            "hk_hold",
            trade_date=trade_date,
            exchange="SH",
        )
        df2 = self._gw.call(
            "hk_hold",
            trade_date=trade_date,
            exchange="SZ",
        )
        if df is None:
            df = pd.DataFrame()
        if df2 is not None and not df2.empty:
            df = pd.concat([df, df2], ignore_index=True)

        if not df.empty:
            df.to_parquet(cache_path, index=False)
        return df

    def get_hsgt_moneyflow(
        self, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """沪深港通资金流（日级时序）。

        Returns:
            DataFrame with: trade_date, ggt_ss, ggt_sz,
            north_money (= ggt_ss + ggt_sz) 等。
        """
        # 按月缓存
        cache_path = (
            self._cache_dir / f"hsgt_flow_{start_date[:6]}_{end_date[:6]}.parquet"
        )
        if cache_path.exists():
            cached = pd.read_parquet(cache_path)
            if not cached.empty:
                return cached

        logger.info("从 TuShare API 下载 moneyflow_hsgt: %s~%s", start_date, end_date)
        df = self._gw.call(
            "moneyflow_hsgt",
            start_date=start_date,
            end_date=end_date,
        )
        if df is None:
            df = pd.DataFrame()
        if not df.empty:
            df.to_parquet(cache_path, index=False)
        return df

    def get_hk_hold_range(
        self,
        ts_codes: list[str],
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """指定标的的北向持股时间序列。

        Args:
            ts_codes: 股票代码列表
            start_date: YYYYMMDD
            end_date: YYYYMMDD

        Returns:
            DataFrame with: ts_code, trade_date, ratio, vol
        """
        frames: list[pd.DataFrame] = []
        for code in ts_codes:
            cache_path = (
                self._cache_dir / f"hk_hold_{code.replace('.', '_')}_{start_date}_{end_date}.parquet"
            )
            if cache_path.exists():
                cached = pd.read_parquet(cache_path)
                if not cached.empty:
                    frames.append(cached)
                    continue

            logger.info("从 TuShare API 下载 hk_hold: %s %s~%s", code, start_date, end_date)
            df = self._gw.call(
                "hk_hold",
                ts_code=code,
                start_date=start_date,
                end_date=end_date,
            )
            if df is not None and not df.empty:
                df.to_parquet(cache_path, index=False)
                frames.append(df)

        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)
