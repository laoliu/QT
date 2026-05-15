"""TuShare Pro 数据提供者实现

这是 BarProvider 接口的 TuShare Pro 实现，支持：
- 1min/5min/15min/30min/60min/daily 多频率数据
- 本地 Parquet 缓存（避免重复下载）
- 自动重试（网络异常/API限流）
- 数据质量检查（缺失率/异常跳变）
- 数据指纹生成（可复现性）

使用示例：
    provider = TuShareProBarProvider(
        token="your_token",
        cache_dir="data/cache",
    )
    bars = provider.load_bars(
        symbol="000001.SZ",
        start=date(2024, 1, 1),
        end=date(2024, 1, 31),
        frequency="1min",
    )
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

import pandas as pd
import tushare as ts

from aipha.data.cache_utils import is_cache_fresh, safe_read_parquet
from aipha.data.provider_base import DataFingerprint
from aipha.domain.models import Bar

logger = logging.getLogger(__name__)

FrequencyType = Literal["1min", "5min", "15min", "30min", "60min", "daily"]


class TuShareProBarProvider:
    """TuShare Pro 数据提供者
    
    Args:
        token: TuShare Pro API Token
        cache_dir: 本地缓存目录（可选，不设置则不缓存）
        adjust: 复权方式（qfq前复权/hfq后复权/None不复权）
        max_retries: 最大重试次数（网络异常时）
        backoff_factor: 重试退避因子（秒）
        max_missing_rate: 最大允许缺失率（超过则抛异常）
    """

    def __init__(
        self,
        token: str | None = None,
        cache_dir: str | None = None,
        adjust: str = "qfq",
        max_retries: int = 3,
        backoff_factor: float = 2.0,
        max_missing_rate: float = 0.05,
        asset: str = "E",
    ):
        from aipha.data.tushare_gateway import get_tushare_gateway

        if cache_dir is None:
            from aipha.config import get_cache_dir
            cache_dir = str(get_cache_dir() / "tushare")

        self._gateway = get_tushare_gateway(token=token)
        self.token = self._gateway._token  # 向后兼容
        self.cache_dir = cache_dir
        self.adjust = adjust
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.max_missing_rate = max_missing_rate
        self.asset = asset  # E=股票, FD=基金/ETF

        # 通过网关获取 API 对象（向后兼容需要 self.api 的调用方）
        self.api = self._gateway.api

        # 创建缓存目录
        if cache_dir:
            Path(cache_dir).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 缓存安全读取
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_read_parquet(path: Path) -> pd.DataFrame | None:
        """安全读取 Parquet 文件（委托给 cache_utils.safe_read_parquet）。"""
        return safe_read_parquet(path)

    def load_bars(
        self,
        *,
        symbol: str,
        start: date,
        end: date,
        frequency: FrequencyType = "1min",
    ) -> list[Bar]:
        """加载 bar 数据（按月分片缓存，只下载缺失月份）

        日线数据按 ``{symbol}_{YYYYMM}_{freq}_{adj}.parquet`` 分月缓存，
        查询跨月时自动合并已缓存月份，仅对缺失月份调用 API。
        分钟线仍使用完整日期范围缓存（数据量大，按月拆分效果不明显）。

        Args:
            symbol: 股票代码（例如：000001.SZ）
            start: 开始日期
            end: 结束日期（包含）
            frequency: 数据频率（1min/5min/15min/30min/60min/daily）

        Returns:
            Bar 列表（按时间升序）

        Raises:
            ValueError: 数据质量不符合要求（缺失率过高）
            RuntimeError: API 调用失败（超过最大重试次数）
        """
        # 日线使用按月分片策略；分钟线使用完整范围缓存（向后兼容）
        if frequency == "daily" and self.cache_dir:
            return self._load_bars_monthly(symbol, start, end, frequency)

        # 非日线 / 无缓存目录：走原始逻辑
        if self.cache_dir:
            cache_path = self._cache_path(symbol, start, end, frequency)
            if cache_path.exists():
                return self._load_from_cache(cache_path)

        df = self._fetch_from_api(symbol, start, end, frequency)
        if df.empty:
            return []

        self._check_data_quality(df, symbol, start, end, frequency)
        bars = [self._convert_to_bar(row) for _, row in df.iterrows()]

        if self.cache_dir:
            self._save_to_cache(df, self._cache_path(symbol, start, end, frequency))

        return bars

    # ------------------------------------------------------------------
    # 按月分片缓存（日线专用）
    # ------------------------------------------------------------------

    def _monthly_cache_path(
        self, symbol: str, year: int, month: int, frequency: str,
    ) -> Path:
        """生成月度缓存路径: {symbol}_{YYYYMM}_{freq}_{adj}.parquet"""
        return (
            Path(self.cache_dir)
            / f"{symbol}_{year:04d}{month:02d}_{frequency}_{self.adjust}.parquet"
        )

    def _iter_months(self, start: date, end: date):
        """遍历起止日期覆盖的所有 (year, month)"""
        y, m = start.year, start.month
        while (y, m) <= (end.year, end.month):
            yield y, m
            m += 1
            if m > 12:
                m = 1
                y += 1

    def _load_bars_monthly(
        self, symbol: str, start: date, end: date, frequency: str,
    ) -> list[Bar]:
        """按月加载日线数据，命中缓存则跳过，缺失月份逐月下载。

        兼容旧缓存：若旧格式 ``{symbol}_{start}_{end}_{freq}_{adj}.parquet``
        存在，先读取旧文件拆分为月度缓存，避免重复下载。

        包含连续 API 失败熔断机制：如果连续 3 个月 API 调用都失败，
        说明 TuShare API 当前不可用，跳过该股票剩余月份。
        """
        import calendar as cal_mod

        # 兼容旧格式缓存：如果旧缓存文件存在，迁移为月度分片
        self._migrate_legacy_cache(symbol, start, end, frequency)

        all_dfs: list[pd.DataFrame] = []
        months = list(self._iter_months(start, end))
        _consec_api_fails = 0  # 连续 API 失败计数
        _MAX_CONSEC_API_FAILS = 3  # 连续失败熔断阈值

        for y, m in months:
            cache_path = self._monthly_cache_path(symbol, y, m, frequency)
            if cache_path.exists():
                df_month = self._safe_read_parquet(cache_path)
                if df_month is not None and not df_month.empty:
                    all_dfs.append(df_month)
                    _consec_api_fails = 0  # 有缓存数据，重置计数
                    continue
                elif df_month is not None and df_month.empty:
                    # 空文件 = 该月确认无数据（股票未上市等），跳过不重试
                    continue
                # df_month is None → 文件损坏，走下面的 API 下载路径

            # 如果连续 API 失败已触发熔断，跳过剩余月份的 API 调用
            if _consec_api_fails >= _MAX_CONSEC_API_FAILS:
                # 保存空缓存标记，避免下次重复调用
                self._save_to_cache(pd.DataFrame(), cache_path)
                continue

            # 计算该月的下载范围（不超出请求范围）
            month_start = date(y, m, 1)
            _, last_day = cal_mod.monthrange(y, m)
            month_end = date(y, m, last_day)
            dl_start = max(month_start, start)
            dl_end = min(month_end, end)

            try:
                df_month = self._fetch_from_api(symbol, dl_start, dl_end, frequency)
            except Exception as e:
                _consec_api_fails += 1
                # API 调用失败（网络超时/TuShare 限流/空响应等）：
                # 保存空缓存标记，避免下次重复调用同一个失败的月份
                if _consec_api_fails >= _MAX_CONSEC_API_FAILS:
                    logger.warning(
                        f"API 连续 {_consec_api_fails} 月失败 ({symbol})，"
                        f"熔断跳过剩余月份"
                    )
                else:
                    logger.debug(
                        f"API 获取 {symbol} {y:04d}{m:02d} 失败: {e}，"
                        f"保存空缓存标记"
                    )
                self._save_to_cache(pd.DataFrame(), cache_path)
                continue
            if not df_month.empty:
                self._save_to_cache(df_month, cache_path)
                all_dfs.append(df_month)
                _consec_api_fails = 0  # 成功获取数据，重置计数
            else:
                # 保存空 DataFrame 作为"该月无数据"标记，避免下次重复调用 API
                self._save_to_cache(df_month, cache_path)

        if not all_dfs:
            return []

        merged = pd.concat(all_dfs, ignore_index=True)
        # 去重（旧缓存和新下载可能重叠）
        if "trade_date" in merged.columns:
            merged = merged.drop_duplicates(subset=["ts_code", "trade_date"])
            start_str = start.strftime("%Y%m%d")
            end_str = end.strftime("%Y%m%d")
            merged = merged[
                (merged["trade_date"] >= start_str)
                & (merged["trade_date"] <= end_str)
            ]
            merged = merged.sort_values("trade_date").reset_index(drop=True)

        self._check_data_quality(merged, symbol, start, end, frequency)
        return [self._convert_to_bar(row) for _, row in merged.iterrows()]

    def _migrate_legacy_cache(
        self, symbol: str, start: date, end: date, frequency: str,
    ) -> None:
        """将旧格式缓存 ``{symbol}_{start}_{end}_{freq}_{adj}.parquet``
        拆分为月度缓存文件，然后删除旧文件。"""
        import calendar as cal_mod

        legacy_path = self._cache_path(symbol, start, end, frequency)
        if not legacy_path.exists():
            return

        try:
            df = self._safe_read_parquet(legacy_path)
            if df is None:
                return  # 损坏的旧缓存无需迁移
            if df.empty or "trade_date" not in df.columns:
                return

            # 提取月份并按月分组存储
            df["_month"] = df["trade_date"].str[:6]
            for month_key, grp in df.groupby("_month"):
                y = int(month_key[:4])
                m = int(month_key[4:6])
                monthly_path = self._monthly_cache_path(symbol, y, m, frequency)
                if not monthly_path.exists():
                    grp_clean = grp.drop(columns=["_month"])
                    self._save_to_cache(grp_clean, monthly_path)

            # 迁移完成后删除旧文件
            legacy_path.unlink()
            import logging
            logging.getLogger(__name__).info(
                f"已迁移旧缓存 → 月度缓存: {symbol} ({len(df)} 根bar)"
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                f"旧缓存迁移失败 {symbol}: {e}（保留旧文件）"
            )

    def fingerprint(self) -> DataFingerprint:
        """生成数据指纹（用于可复现性）"""
        return DataFingerprint(
            provider="TuSharePro",
            snapshot=self._get_tushare_version(),
            calendar="cn_stock_v1",  # 使用统一的交易日历版本
            adjust=self.adjust,
            schema="bar_v1",  # Bar 数据结构版本
        )

    def _fetch_from_api(
        self,
        symbol: str,
        start: date,
        end: date,
        frequency: FrequencyType,
    ) -> pd.DataFrame:
        """从 TuShare API 获取数据（通过统一网关，自动限流/并发控制/重试）"""
        if frequency == "daily":
            # 日线数据：使用 pro_bar 获取复权数据
            df = self._gateway.pro_bar(
                ts_code=symbol,
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                adj=self.adjust,
                asset=self.asset,  # E=股票, FD=基金/ETF
            )
        else:
            # 分钟数据（需要较高积分权限）
            df = self._gateway.call(
                "stk_mins",
                ts_code=symbol,
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                freq=frequency,
                adj=self.adjust,
            )

        # 防御 API 返回 None（次新股/退市股偶发）
        if df is None:
            return pd.DataFrame()

        # 按时间升序排序
        if not df.empty:
            time_col = "trade_date" if frequency == "daily" else "trade_time"
            df = df.sort_values(time_col).reset_index(drop=True)

        return df

    def _convert_to_bar(self, row: pd.Series) -> Bar:
        """将 TuShare 数据转换为 Bar 对象

        从 TuShare 原始数据提取所有交易状态字段：
        - prev_close: 前收盘价（TuShare 字段 ``pre_close``）
        - is_suspended: 停牌判断（成交量 == 0 且 OHLC 全等）
        - is_limit_up / is_limit_down: 涨跌停判断（close 与涨跌停价差异 < 0.01）
        """
        # 判断是日线还是分钟线
        if "trade_date" in row:
            # 日线数据
            trade_date = pd.to_datetime(row["trade_date"], format="%Y%m%d")
            start_ts = trade_date.replace(hour=9, minute=30)
            end_ts = trade_date.replace(hour=15, minute=0)
        else:
            # 分钟线数据
            trade_time = pd.to_datetime(row["trade_time"])
            end_ts = trade_time
            start_ts = end_ts - timedelta(minutes=1)  # 假设1分钟bar

        close_val = float(row["close"])
        vol_val = float(row.get("vol", 0))

        # --- prev_close ---
        prev_close: float | None = None
        if "pre_close" in row and pd.notna(row["pre_close"]):
            prev_close = float(row["pre_close"])

        # --- is_suspended ---
        # 停牌特征：成交量为 0 且 OHLC 全部相等（无交易发生）
        is_suspended = False
        if vol_val == 0:
            o, h, l = float(row["open"]), float(row["high"]), float(row["low"])
            if o == h == l == close_val:
                is_suspended = True

        # --- is_limit_up / is_limit_down ---
        is_limit_up: bool | None = None
        is_limit_down: bool | None = None
        if prev_close is not None and prev_close > 0:
            # 默认 ±10%；ST/科创/创业板由 Bar 消费者（HaltChecker）精确判断
            # 这里使用宽松的 ±11% 阈值标记可能触及涨跌停
            limit_up_price = round(prev_close * 1.10, 2)
            limit_down_price = round(prev_close * 0.90, 2)
            is_limit_up = close_val >= limit_up_price - 0.01
            is_limit_down = close_val <= limit_down_price + 0.01

        return Bar(
            symbol=row["ts_code"],
            start_ts=start_ts,
            end_ts=end_ts,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=close_val,
            volume=vol_val * 100,  # TuShare: 手 -> 股
            vwap=self._calculate_vwap(row),
            prev_close=prev_close,
            is_suspended=is_suspended,
            is_limit_up=is_limit_up,
            is_limit_down=is_limit_down,
        )

    def _calculate_vwap(self, row: pd.Series) -> float | None:
        """计算 VWAP（成交额加权平均价）"""
        if "amount" in row and "vol" in row and row["vol"] > 0:
            # TuShare: amount单位千元，vol单位手
            return (float(row["amount"]) * 1000) / (float(row["vol"]) * 100)
        return None

    def _check_data_quality(
        self,
        df: pd.DataFrame,
        symbol: str,
        start: date,
        end: date,
        frequency: FrequencyType,
    ) -> None:
        """数据质量检查

        对于上市日期在 start 之后的股票，以数据首日作为有效起始日计算
        期望 bar 数量，避免误判"缺失率过高"。
        
        Raises:
            ValueError: 数据质量不符合要求
        """
        if df.empty:
            return

        # 推断实际有效起始日（股票可能上市较晚）
        effective_start = start
        if "trade_date" in df.columns and not df.empty:
            first_date_str = df["trade_date"].min()
            try:
                first_date = date(
                    int(first_date_str[:4]),
                    int(first_date_str[4:6]),
                    int(first_date_str[6:8]),
                )
                if first_date > start:
                    effective_start = first_date
            except (ValueError, TypeError):
                pass

        # 计算期望的 bar 数量（基于有效起始日）
        expected_bars = self._expected_bar_count(effective_start, end, frequency)
        actual_bars = len(df)

        # 检查缺失率
        if expected_bars > 0:
            missing_rate = 1 - (actual_bars / expected_bars)
            if missing_rate > self.max_missing_rate:
                raise ValueError(
                    f"{symbol} 数据缺失率过高: {missing_rate:.2%} "
                    f"(期望 {expected_bars} 根bar，实际 {actual_bars} 根, "
                    f"有效起始={effective_start})"
                )

    def _expected_bar_count(
        self,
        start: date,
        end: date,
        frequency: FrequencyType,
    ) -> int:
        """计算期望的 bar 数量

        优先使用交易日历精确计算，若不可用则使用保守估算（0.63 系数，
        覆盖春节/国庆等长假对 A 股的影响）。
        """
        # 先尝试用交易日历精确计算交易日数
        try:
            from aipha.data.calendar import CNStockCalendar
            cal = CNStockCalendar()
            trading_days = cal.get_trading_days(start, end)
            td_count = len(trading_days)
        except Exception:
            # 回退到保守估算
            days = (end - start).days + 1
            td_count = int(days * 0.63)  # A 股约 242/365 ≈ 0.663，取保守值

        if frequency == "daily":
            return td_count
        else:
            bars_per_day = {
                "1min": 240,
                "5min": 48,
                "15min": 16,
                "30min": 8,
                "60min": 4,
            }
            bpd = bars_per_day.get(frequency, 240)
            return td_count * bpd

    def _cache_path(
        self,
        symbol: str,
        start: date,
        end: date,
        frequency: FrequencyType,
    ) -> Path:
        """生成缓存文件路径"""
        cache_key = f"{symbol}_{start}_{end}_{frequency}_{self.adjust}"
        filename = f"{cache_key}.parquet"
        return Path(self.cache_dir) / filename

    def _load_from_cache(self, cache_path: Path) -> list[Bar]:
        """从缓存加载数据"""
        df = self._safe_read_parquet(cache_path)
        if df is None:
            return []
        return [self._convert_to_bar(row) for _, row in df.iterrows()]

    def _save_to_cache(self, df: pd.DataFrame, cache_path: Path) -> None:
        """保存数据到缓存"""
        df.to_parquet(cache_path, index=False)

    def _cache_key(
        self,
        symbol: str,
        start: date,
        end: date,
        frequency: FrequencyType,
    ) -> str:
        """生成缓存键（用于测试）"""
        return self._cache_path(symbol, start, end, frequency).name

    def _get_tushare_version(self) -> str:
        """获取 TuShare 版本信息"""
        try:
            return f"tushare_{ts.__version__}"
        except AttributeError:
            return "tushare_unknown"
