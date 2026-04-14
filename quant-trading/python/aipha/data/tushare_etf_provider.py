"""TuShare ETF 数据提供者

提供 ETF 基本信息、日线行情加载，支持本地 Parquet 缓存。
通过 TuShareGateway 全局单例访问 API，遵循限流/重试规则。

主要接口：
- load_etf_basic(): 获取全部场内 ETF 基本信息
- load_fund_daily(): 获取 ETF 日线行情（含成交量/金额）
"""

from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


class TuShareETFProvider:
    """TuShare ETF 数据提供者

    Args:
        token: TuShare Pro API Token（None 则从环境变量获取）
        cache_dir: 本地缓存目录
    """

    def __init__(
        self,
        token: str | None = None,
        cache_dir: str = "data/cache/etf_tushare",
    ) -> None:
        from aipha.data.tushare_gateway import get_tushare_gateway

        self._gateway = get_tushare_gateway(token=token)
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def load_etf_basic(self, use_cache: bool = True) -> pd.DataFrame:
        """加载场内 ETF 基本信息

        Returns:
            DataFrame with columns:
            ts_code, name, management, fund_type, list_date,
            delist_date, status (D=摘牌, L=上市)
        """
        cache_path = self._cache_dir / "etf_basic.parquet"

        if use_cache and cache_path.exists():
            try:
                df = pd.read_parquet(cache_path)
                if not df.empty:
                    logger.info(
                        f"ETF basic 缓存命中: {len(df)} 只"
                    )
                    return df
            except Exception as e:
                logger.warning(f"ETF basic 缓存读取失败: {e}")

        logger.info("从 TuShare 加载 ETF basic...")
        df = self._gateway.call(
            "fund_basic",
            market="E",  # E=场内
            status="L",  # L=上市中
        )
        if df is None or df.empty:
            logger.warning("ETF basic 返回空数据")
            return pd.DataFrame()

        # 筛选 ETF（排除 LOF/分级基金等）
        if "fund_type" in df.columns:
            df = df[df["fund_type"] == "ETF"].copy()

        try:
            df.to_parquet(cache_path, index=False)
            logger.info(f"ETF basic 已缓存: {len(df)} 只")
        except Exception as e:
            logger.warning(f"ETF basic 缓存写入失败: {e}")

        return df

    def load_fund_daily(
        self,
        ts_code: str,
        start_date: str,
        end_date: str,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """加载单只 ETF 的日线行情

        Args:
            ts_code: ETF 代码（如 510300.SH）
            start_date: 起始日期 YYYYMMDD
            end_date: 结束日期 YYYYMMDD

        Returns:
            DataFrame with: ts_code, trade_date, open, high,
            low, close, pre_close, vol, amount
        """
        cache_path = (
            self._cache_dir
            / f"fund_daily_{ts_code}_{start_date}_{end_date}.parquet"
        )

        if use_cache and cache_path.exists():
            try:
                df = pd.read_parquet(cache_path)
                if not df.empty:
                    return df
            except Exception:
                pass

        df = self._gateway.call(
            "fund_daily",
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
        )
        if df is None or df.empty:
            return pd.DataFrame()

        df = df.sort_values("trade_date").reset_index(drop=True)

        try:
            df.to_parquet(cache_path, index=False)
        except Exception as e:
            logger.warning(f"fund_daily 缓存写入失败 {ts_code}: {e}")

        return df
