"""AKShare 数据提供者实现（免费数据源）

这是 BarProvider 接口的 AKShare 实现，支持：
- A 股日线/分钟线数据（完全免费，无积分限制）
- 本地缓存
- 自动重试

AKShare 官网：https://akshare.akfamily.xyz/

使用示例：
    provider = AKShareBarProvider(cache_dir="data/cache")
    bars = provider.load_bars(
        symbol="000001",  # 不需要后缀
        start=date(2024, 1, 1),
        end=date(2024, 1, 31),
        frequency="daily",
    )
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

import pandas as pd

from aipha.data.provider_base import DataFingerprint
from aipha.data.rate_limiter import RateLimiter, get_akshare_limiter, get_rate_limit_config
from aipha.domain.models import Bar

logger = logging.getLogger(__name__)

FrequencyType = Literal["1min", "5min", "15min", "30min", "60min", "daily"]


class AKShareBarProvider:
    """AKShare 数据提供者（免费）
    
    Args:
        cache_dir: 本地缓存目录（可选，不设置则不缓存）
        adjust: 复权方式（qfq前复权/hfq后复权/空字符串不复权）
        max_retries: 最大重试次数（从 .env 读取默认值）
        backoff_factor: 重试退避因子（从 .env 读取默认值）
        rate_limiter: 自定义速率限制器（默认使用全局共享限制器）
    """

    def __init__(
        self,
        cache_dir: str | None = None,
        adjust: str = "qfq",
        max_retries: int | None = None,
        backoff_factor: float | None = None,
        rate_limiter: RateLimiter | None = None,
    ):
        if cache_dir is None:
            from aipha.config import get_cache_dir
            cache_dir = str(get_cache_dir() / "akshare")
        self.cache_dir = cache_dir
        self.adjust = adjust

        # 从配置加载速率限制参数
        config = get_rate_limit_config("AKSHARE")
        self.max_retries = max_retries if max_retries is not None else config.max_retries
        self.backoff_factor = backoff_factor if backoff_factor is not None else config.backoff_factor

        # 速率限制器（默认使用全局共享限制器）
        self._rate_limiter = rate_limiter or get_akshare_limiter()

        # 创建缓存目录
        if cache_dir:
            Path(cache_dir).mkdir(parents=True, exist_ok=True)

        logger.debug(
            f"初始化 AKShareBarProvider: adjust={adjust}, "
            f"max_retries={self.max_retries}, backoff_factor={self.backoff_factor}"
        )

    def load_bars(
        self,
        *,
        symbol: str,
        start: date,
        end: date,
        frequency: FrequencyType = "daily",
    ) -> list[Bar]:
        """加载 bar 数据
        
        Args:
            symbol: 股票代码（如 000001 或 000001.SZ）
            start: 开始日期
            end: 结束日期（包含）
            frequency: 数据频率
        
        Returns:
            Bar 列表（按时间升序）
        """
        # 标准化股票代码（去掉后缀）
        code = symbol.split(".")[0]
        
        # 尝试从缓存加载
        if self.cache_dir:
            cache_path = self._cache_path(code, start, end, frequency)
            if cache_path.exists():
                return self._load_from_cache(cache_path, symbol)

        # 从 AKShare API 加载
        df = self._fetch_from_api(code, start, end, frequency)

        if df.empty:
            return []

        # 转换为 Bar 列表
        bars = [self._convert_to_bar(row, symbol) for _, row in df.iterrows()]

        # 保存到缓存
        if self.cache_dir:
            self._save_to_cache(df, cache_path)

        return bars

    def fingerprint(self) -> DataFingerprint:
        """生成数据指纹"""
        return DataFingerprint(
            provider="AKShare",
            snapshot="latest",
            calendar="cn_stock_v1",
            adjust=self.adjust,
            schema="bar_v1",
        )

    def _fetch_from_api(
        self,
        code: str,
        start: date,
        end: date,
        frequency: FrequencyType,
    ) -> pd.DataFrame:
        """从 AKShare API 获取数据（带速率限制）"""
        try:
            import akshare as ak
        except ImportError as err:
            raise ImportError(
                "请安装 akshare: pip install akshare"
            ) from err

        for attempt in range(self.max_retries):
            try:
                # 速率限制：阻塞直到可以发起请求
                self._rate_limiter.acquire()

                if frequency == "daily":
                    # 日线数据
                    df = ak.stock_zh_a_hist(
                        symbol=code,
                        period="daily",
                        start_date=start.strftime("%Y%m%d"),
                        end_date=end.strftime("%Y%m%d"),
                        adjust=self.adjust if self.adjust else "",
                    )
                else:
                    # 分钟数据
                    period_map = {
                        "1min": "1",
                        "5min": "5",
                        "15min": "15",
                        "30min": "30",
                        "60min": "60",
                    }
                    period = period_map.get(frequency, "1")
                    df = ak.stock_zh_a_hist_min_em(
                        symbol=code,
                        period=period,
                        start_date=start.strftime("%Y-%m-%d %H:%M:%S"),
                        end_date=end.strftime("%Y-%m-%d %H:%M:%S"),
                        adjust=self.adjust if self.adjust else "",
                    )

                logger.debug(f"AKShare API stock_zh_a_hist 调用成功: {code}")
                return df if df is not None else pd.DataFrame()

            except Exception as e:
                logger.warning(
                    f"AKShare API 调用失败 (尝试 {attempt+1}/{self.max_retries}): {e}"
                )
                if attempt < self.max_retries - 1:
                    wait_time = self.backoff_factor ** attempt
                    logger.debug(f"等待 {wait_time:.1f}s 后重试...")
                    time.sleep(wait_time)
                else:
                    raise RuntimeError(
                        f"AKShare API 调用失败（已重试 {self.max_retries} 次）: {e}"
                    ) from e

        return pd.DataFrame()

    def _convert_to_bar(self, row: pd.Series, symbol: str) -> Bar:
        """将 AKShare 数据转换为 Bar 对象"""
        # 日线数据的列名
        if "日期" in row.index:
            trade_date = pd.to_datetime(row["日期"])
            start_ts = trade_date.replace(hour=9, minute=30)
            end_ts = trade_date.replace(hour=15, minute=0)
            return Bar(
                symbol=symbol,
                start_ts=start_ts,
                end_ts=end_ts,
                open=float(row["开盘"]),
                high=float(row["最高"]),
                low=float(row["最低"]),
                close=float(row["收盘"]),
                volume=float(row["成交量"]),
            )
        # 分钟数据
        elif "时间" in row.index:
            trade_time = pd.to_datetime(row["时间"])
            end_ts = trade_time
            start_ts = end_ts - timedelta(minutes=1)
            return Bar(
                symbol=symbol,
                start_ts=start_ts,
                end_ts=end_ts,
                open=float(row["开盘"]),
                high=float(row["最高"]),
                low=float(row["最低"]),
                close=float(row["收盘"]),
                volume=float(row["成交量"]),
            )
        else:
            raise ValueError(f"无法识别的数据格式: {row.index.tolist()}")

    def _cache_path(
        self,
        code: str,
        start: date,
        end: date,
        frequency: FrequencyType,
    ) -> Path:
        """生成缓存文件路径"""
        filename = f"{code}_{start}_{end}_{frequency}_{self.adjust}.parquet"
        return Path(self.cache_dir) / "akshare" / filename

    def _load_from_cache(self, path: Path, symbol: str) -> list[Bar]:
        """从缓存加载数据"""
        df = pd.read_parquet(path)
        # 检查是否是已转换格式
        if "symbol" in df.columns:
            return [
                Bar(
                    symbol=row["symbol"],
                    start_ts=pd.to_datetime(row["start_ts"]),
                    end_ts=pd.to_datetime(row["end_ts"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
                for _, row in df.iterrows()
            ]
        # 原始 AKShare 格式（旧缓存）
        return [self._convert_to_bar(row, symbol) for _, row in df.iterrows()]

    def _save_to_cache(self, df: pd.DataFrame, path: Path) -> None:
        """保存数据到缓存"""
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, index=False)
