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
import tempfile
import time
from datetime import date
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 模块级单例 — 所有消费方通过 get_etf_provider() 获取共享实例
# ---------------------------------------------------------------------------
_etf_provider_instance: "TuShareETFProvider | None" = None


def get_etf_provider(
    token: str | None = None,
    cache_dir: str = "data/cache/etf_tushare",
) -> "TuShareETFProvider":
    """获取 TuShareETFProvider 全局单例。"""
    global _etf_provider_instance
    if _etf_provider_instance is None:
        _etf_provider_instance = TuShareETFProvider(
            token=token, cache_dir=cache_dir,
        )
    return _etf_provider_instance


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

    # ------------------------------------------------------------------
    # 日线快照 — 全市场 ETF 单日数据（Paper/Live 统一入口）
    # ------------------------------------------------------------------
    _SNAPSHOT_FIELDS = (
        "ts_code,trade_date,open,high,low,close,pre_close,vol,amount"
    )

    def get_daily_snapshot(
        self,
        trade_date: str,
        *,
        retry_interval: float = 3.0,
        log_interval: float = 600.0,
        alert_after: float = 1800.0,
        max_wait: float | None = None,
    ) -> pd.DataFrame:
        """获取 ETF 全市场日线快照（缓存优先，默认无限重试）。

        行为与 CB/Equity 的 get_daily_snapshot() 完全对齐（含 ``max_wait``
        语义：默认 None = 不设上限；给了值且超时抛 ``TimeoutError``）。
        """
        cache_path = self._cache_dir / f"fund_daily_{trade_date}.parquet"

        if cache_path.exists():
            df = pd.read_parquet(cache_path)
            if not df.empty:
                return df

        start_ts = time.monotonic()
        last_log_ts = 0.0
        alerted = False
        attempt = 0

        while True:
            # 等待上限（默认 None = 不设上限）。检查放在打接口之前，故
            # max_wait=0 等价于「只用缓存」。
            elapsed = time.monotonic() - start_ts
            if max_wait is not None and elapsed >= max_wait:
                raise TimeoutError(
                    f"fund_daily {trade_date} 未取到数据: 已等待 "
                    f"{elapsed:.1f}s (上限 {max_wait:.1f}s, {attempt} 次尝试)"
                )

            attempt += 1
            try:
                df = self._gateway.call(
                    "fund_daily",
                    trade_date=trade_date,
                    fields=self._SNAPSHOT_FIELDS,
                )
                if df is not None and not df.empty:
                    df = df[df["close"] > 0].copy()
                    if not df.empty:
                        self._atomic_write_parquet(df, cache_path)
                        elapsed = time.monotonic() - start_ts
                        if elapsed > 10:
                            logger.info(
                                "fund_daily %s 获取成功: %d 行 (%.0fs, %d 次)",
                                trade_date, len(df), elapsed, attempt,
                            )
                        return df
            except Exception:
                pass

            elapsed = time.monotonic() - start_ts
            if elapsed - last_log_ts >= log_interval:
                logger.warning(
                    "fund_daily %s 仍在重试: 已 %.0f 分钟, %d 次尝试",
                    trade_date, elapsed / 60, attempt,
                )
                last_log_ts = elapsed

            if not alerted and elapsed >= alert_after:
                self._send_snapshot_alert(trade_date, elapsed, attempt)
                alerted = True

            time.sleep(retry_interval)

    @staticmethod
    def _atomic_write_parquet(df: pd.DataFrame, path: Path) -> None:
        """原子写入 Parquet（先写 .tmp 再 rename）。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            suffix=".parquet.tmp", dir=str(path.parent),
        )
        try:
            os.close(fd)
            df.to_parquet(tmp, index=False)
            os.replace(tmp, str(path))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @staticmethod
    def _send_snapshot_alert(
        trade_date: str, elapsed: float, attempt: int,
    ) -> None:
        """发送数据获取超时飞书告警（仅一次）。"""
        try:
            from aipha.monitor.feishu_alert import send_feishu_alert
            send_feishu_alert(
                f"ETF 日线数据获取超时: {trade_date}",
                f"已重试 {attempt} 次 ({elapsed / 60:.0f} 分钟) "
                f"仍未获取到 fund_daily 数据。\n"
                f"请检查 TuShare API 状态和网络连通性。",
                level="ERROR",
            )
        except Exception:
            logger.error(
                "ETF daily 快照告警推送失败 (trade_date=%s)",
                trade_date,
            )

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
            self._atomic_write_parquet(df, cache_path)
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
            self._atomic_write_parquet(df, cache_path)
        except Exception as e:
            logger.warning(f"fund_daily 缓存写入失败 {ts_code}: {e}")

        return df
