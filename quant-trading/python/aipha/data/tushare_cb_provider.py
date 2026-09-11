"""TuShare Pro 可转债数据提供者

专为可转债策略设计的 Tushare Pro 数据接口，支持：
- 可转债基础信息（cb_basic）
- 可转债日行情（cb_daily）含溢价率
- 本地 Parquet 缓存
- 数据质量检查

使用示例：
    provider = TuShareCBProvider(cache_dir="data/cache/cb_tushare")
    basic = provider.load_cb_basic()
    daily = provider.load_cb_daily(start_date="20240101", end_date="20241231")
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from dotenv import load_dotenv

from aipha.monitoring.downward_revision_tracker import DownwardRevisionTracker

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 模块级单例 — 所有消费方通过 get_cb_provider() 获取共享实例
# ---------------------------------------------------------------------------
_cb_provider_instance: Optional["TuShareCBProvider"] = None


def get_cb_provider(
    token: Optional[str] = None,
    cache_dir: str = "data/cache/cb_tushare",
) -> "TuShareCBProvider":
    """获取 TuShareCBProvider 全局单例。

    所有 paper/live/preheat 消费方应通过此函数获取 Provider，
    确保共享同一份 Parquet 缓存，避免重复 API 调用。
    """
    global _cb_provider_instance
    if _cb_provider_instance is None:
        _cb_provider_instance = TuShareCBProvider(
            token=token, cache_dir=cache_dir,
        )
    return _cb_provider_instance


class TuShareCBProvider:
    """TuShare Pro 可转债数据提供者

    Args:
        token: TuShare Pro API Token（可选，优先从环境变量读取）
        cache_dir: 本地缓存目录
    """

    def __init__(
        self,
        token: Optional[str] = None,
        cache_dir: str = "data/cache/cb_tushare",
    ):
        from aipha.data.tushare_gateway import get_tushare_gateway

        load_dotenv()
        self._gateway = get_tushare_gateway(token=token)
        self.token = self._gateway._token  # 向后兼容

        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # 通过网关获取 API 对象（向后兼容需要 self.api 的调用方）
        self.api = self._gateway.api

        logger.info(f"TuShareCBProvider 初始化完成，缓存目录: {self.cache_dir}")

    # ------------------------------------------------------------------
    # 评级数据补充（TuShare cb_basic 不返回 rating 列）
    # ------------------------------------------------------------------

    def _supplement_rating(self, df: pd.DataFrame) -> pd.DataFrame:
        """为 cb_basic DataFrame 补充评级数据。

        TuShare ``cb_basic`` 声称有 ``rating`` 字段但实际不返回，
        此方法从以下来源**按优先级**尝试补充：

        1. 本地映射文件 ``{cache_dir}/cb_rating_map.parquet``
           （由 ``scripts/build_cb_rating_map.py`` 生成）
        2. 如已安装 AKShare，从 ``bond_cb_jsl``（集思录）获取当前评级快照

        Rating 仅在缺失时才填充（已有的不覆盖）。

        Args:
            df: 含 ``ts_code`` 列的 cb_basic DataFrame

        Returns:
            同一 DataFrame，``rating`` 列已尽量填充
        """
        if df is None or df.empty:
            return df

        # 确保 rating 列存在
        if "rating" not in df.columns:
            df["rating"] = ""
        else:
            df["rating"] = df["rating"].fillna("").astype(str)

        missing_mask = df["rating"].isin(["", "nan", "None", "NaN"])
        n_missing = missing_mask.sum()
        if n_missing == 0:
            return df

        # --- 来源 1：本地映射文件 ---
        map_path = self.cache_dir / "cb_rating_map.parquet"
        map_loaded = False
        if map_path.exists():
            try:
                rating_map = pd.read_parquet(map_path)
                if "ts_code" in rating_map.columns and "rating" in rating_map.columns:
                    lookup = dict(zip(rating_map["ts_code"], rating_map["rating"]))
                    filled = 0
                    for idx in df.index[missing_mask]:
                        ts = df.at[idx, "ts_code"]
                        r = lookup.get(ts, "")
                        if r and r not in ("", "nan", "None"):
                            df.at[idx, "rating"] = r
                            filled += 1
                    if filled:
                        logger.info(f"从本地评级映射补充 {filled}/{n_missing} 只转债评级")
                    missing_mask = df["rating"].isin(["", "nan", "None", "NaN"])
                    n_missing = missing_mask.sum()
                    map_loaded = True
            except Exception as e:
                logger.warning(f"加载本地评级映射失败: {e}")

        if n_missing == 0:
            return df

        # --- 来源 2：AKShare 集思录（实时快照） ---
        # 若本地映射文件已存在（即使不完整），跳过网络请求，避免阻塞回测
        if map_loaded:
            logger.debug(
                f"本地评级映射已加载但仍有 {n_missing} 只转债评级缺失，"
                "跳过 AKShare 实时拉取（避免阻塞回测）。"
                "如需更新评级，请运行: python scripts/build_cb_rating_map.py"
            )
            return df

        try:
            import concurrent.futures
            import akshare as ak

            def _fetch_jsl():
                return ak.bond_cb_jsl()

            executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future = executor.submit(_fetch_jsl)
            jsl_df = None
            try:
                jsl_df = future.result(timeout=15)  # 最多等待 15 秒
            except concurrent.futures.TimeoutError:
                logger.warning("AKShare 集思录请求超时 (15s)，跳过评级补充。")
                executor.shutdown(wait=False)  # 不等待卡住的线程
                return df
            finally:
                executor.shutdown(wait=False)  # 无论如何不阻塞等待

            if jsl_df is not None and not jsl_df.empty:
                # 集思录代码格式: 纯数字，需映射到 ts_code
                jsl_map: dict[str, str] = {}
                for _, row in jsl_df.iterrows():
                    code = str(row.get("代码", ""))
                    rating_val = str(row.get("债券评级", ""))
                    if code and rating_val and rating_val not in ("", "nan", "None"):
                        # 映射: 11xxxx/132xxx -> .SH, 12xxxx/145xxx -> .SZ
                        if code.startswith("11") or code.startswith("132"):
                            ts_code = code + ".SH"
                        else:
                            ts_code = code + ".SZ"
                        jsl_map[ts_code] = rating_val

                filled = 0
                for idx in df.index[missing_mask]:
                    ts = df.at[idx, "ts_code"]
                    r = jsl_map.get(ts, "")
                    if r:
                        df.at[idx, "rating"] = r
                        filled += 1
                if filled:
                    logger.info(f"从 AKShare 集思录补充 {filled}/{n_missing} 只转债评级")

                    # 保存评级映射到本地缓存，供后续使用
                    all_ratings = []
                    for _, row in df.iterrows():
                        ts = row["ts_code"]
                        r = row.get("rating", "")
                        if r and r not in ("", "nan", "None"):
                            all_ratings.append({"ts_code": ts, "rating": r})
                    if all_ratings:
                        self._atomic_write_parquet(
                            pd.DataFrame(all_ratings), map_path,
                        )
                        logger.info(f"评级映射已缓存: {map_path}")

        except ImportError:
            logger.debug("AKShare 未安装，跳过集思录评级补充")
        except Exception as e:
            logger.warning(f"AKShare 集思录评级获取失败: {e}")

        return df

    def load_cb_basic(self, use_cache: bool = True) -> pd.DataFrame:
        """获取可转债基础信息列表

        Returns:
            DataFrame with columns:
                - ts_code: 转债代码
                - bond_short_name: 转债简称
                - stk_code: 正股代码
                - stk_short_name: 正股简称
                - list_date: 上市日期
                - delist_date: 摘牌日期
                - conv_price: 最新转股价
                - remain_size: 剩余规模
                - maturity_date: 到期日期
        """
        cache_path = self.cache_dir / "cb_basic.parquet"

        if use_cache and cache_path.exists():
            df = pd.read_parquet(cache_path)
            logger.info(f"从缓存加载可转债基础信息: {len(df)} 条")
            df = self._supplement_rating(df)
            return df

        logger.info("从 Tushare 获取可转债基础信息...")
        df = self._gateway.call(
            "cb_basic",
            fields="ts_code,bond_short_name,stk_code,stk_short_name,"
            "list_date,delist_date,conv_price,remain_size,maturity_date,"
            "put_clause,call_clause,issue_size,rating"
        )

        if df is not None and len(df) > 0:
            df = self._supplement_rating(df)
            self._atomic_write_parquet(df, cache_path)
            logger.info(f"获取并缓存可转债基础信息: {len(df)} 条")

        return df

    def load_cb_daily(
        self,
        ts_code: Optional[str] = None,
        trade_date: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """获取可转债日行情（含溢价率）

        Args:
            ts_code: 转债代码（可选）
            trade_date: 交易日期 YYYYMMDD（可选）
            start_date: 开始日期 YYYYMMDD（可选）
            end_date: 结束日期 YYYYMMDD（可选）

        Returns:
            DataFrame with columns:
                - ts_code: 转债代码
                - trade_date: 交易日期
                - open, high, low, close: OHLC
                - vol: 成交量
                - amount: 成交金额
                - cb_value: 转股价值
                - cb_over_rate: 转股溢价率（%）
        """
        # 构建缓存 key
        cache_key = f"cb_daily"
        if ts_code:
            cache_key += f"_{ts_code.replace('.', '_')}"
        if trade_date:
            cache_key += f"_{trade_date}"
        elif start_date and end_date:
            cache_key += f"_{start_date}_{end_date}"

        cache_path = self.cache_dir / f"{cache_key}.parquet"

        if use_cache and cache_path.exists():
            df = pd.read_parquet(cache_path)
            logger.info(f"从缓存加载可转债行情: {len(df)} 条")
            return df

        logger.info(f"从 Tushare 获取可转债行情: {ts_code or trade_date or f'{start_date}-{end_date}'}")

        # Tushare 单次最多2000条，需要分批获取
        all_data = []

        if ts_code:
            # 按单个转债获取
            df = self._gateway.call(
                "cb_daily",
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                fields="ts_code,trade_date,open,high,low,close,vol,amount,cb_value,cb_over_rate"
            )
            if df is not None and not df.empty:
                all_data.append(df)
        elif trade_date:
            # 按单日获取全部转债
            df = self._gateway.call(
                "cb_daily",
                trade_date=trade_date,
                fields="ts_code,trade_date,open,high,low,close,vol,amount,cb_value,cb_over_rate"
            )
            if df is not None and not df.empty:
                all_data.append(df)
        elif start_date and end_date:
            # 按日期范围获取，逐日迭代并优先复用逐日缓存
            from datetime import datetime, timedelta

            current = datetime.strptime(start_date, "%Y%m%d")
            end = datetime.strptime(end_date, "%Y%m%d")
            cached_count = 0

            while current <= end:
                trade_dt = current.strftime("%Y%m%d")

                # 优先检查逐日缓存文件
                day_cache_path = self.cache_dir / f"cb_daily_{trade_dt}.parquet"
                if use_cache and day_cache_path.exists():
                    df = pd.read_parquet(day_cache_path)
                    if len(df) > 0:
                        all_data.append(df)
                        cached_count += 1
                else:
                    try:
                        df = self._gateway.call(
                            "cb_daily",
                            trade_date=trade_dt,
                            fields="ts_code,trade_date,open,high,low,close,vol,amount,cb_value,cb_over_rate"
                        )
                        if df is not None and len(df) > 0:
                            all_data.append(df)
                            # 同时保存逐日缓存，供后续复用
                            self._atomic_write_parquet(df, day_cache_path)
                    except Exception as e:
                        logger.warning(f"获取 {trade_dt} 数据失败: {e}")

                if len(all_data) % 100 == 0 and len(all_data) > 0:
                    logger.info(
                        f"已加载 {len(all_data)} 个交易日数据"
                        f"（缓存 {cached_count}，API {len(all_data) - cached_count}）..."
                    )

                current += timedelta(days=1)

        if all_data:
            result = pd.concat(all_data, ignore_index=True)
            # 过滤无效数据（close=0 或 NaN）
            result = result[result["close"] > 0].copy()
            # NOTE: 不再 dropna(subset=["cb_value","cb_over_rate"])
            # 新上市/停牌/数据暂缺时 cb_value/cb_over_rate 可能为 NaN，
            # 但 OHLCV 有效；下游 cb_data_adapter._build_metadata() 已安全处理 NaN。
            # 移除 dropna 以统一批量/逐日缓存行为（修复 F-D3）。

            if len(result) > 0:
                self._atomic_write_parquet(result, cache_path)
                logger.info(f"获取并缓存可转债行情: {len(result)} 条")

            return result

        return pd.DataFrame()

    # ------------------------------------------------------------------
    # 单日全量快照 — Paper/Live/Preheat 的唯一数据入口
    # ------------------------------------------------------------------

    # get_daily_snapshot 所需的字段（覆盖 close/open/pre_close/vol/amount/cb_value/cb_over_rate）
    _SNAPSHOT_FIELDS = (
        "ts_code,trade_date,open,high,low,close,pre_close,"
        "vol,amount,cb_value,cb_over_rate"
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
        """获取指定交易日的全量 CB 日线快照（缓存优先）。

        这是 Paper/Live/Preheat 路径获取 CB 日线数据的 **唯一入口**。
        所有消费方（_fetch_prices_once / fetch_open_prices / scorer 等）
        应通过此方法获取数据，不应直接调用 gw.call("cb_daily")。

        行为：
        1. 缓存命中 → 直接返回 DataFrame
        2. 缓存未命中 → 调 gw.call("cb_daily") → 原子写缓存 → 返回
        3. API 失败 → 每 3s 重试，默认永不放弃（见 max_wait）
        4. 日志：每次失败不输出，仅每 10 分钟输出一次 "仍在重试"
        5. 30 分钟未成功 → 飞书告警（仅一次）

        Args:
            trade_date: 交易日期 YYYYMMDD
            retry_interval: 重试间隔（秒），默认 3s
            log_interval: 日志输出间隔（秒），默认 600s (10min)
            alert_after: 超过此秒数发飞书告警，默认 1800s (30min)
            max_wait: 最长等待秒数，超过则抛 ``TimeoutError``。
                默认 ``None`` = 不设上限，保持 Paper/Live 的「永不放弃」语义
                （盘后接口抽风时宁可等，也不要拿半份数据去调仓）。
                预算在**打接口之前**检查，所以 ``0`` 表示「只用缓存，一次
                API 都不打」—— 用于「有缓存就用、没有就让调用方走自己的
                降级路径」的场景（如
                ``cb_data_enricher.fetch_cb_daily_enriched``）。
                注意单次 ``gw.call()`` 自身还有内部重试（默认 1s 间隔 ×
                max_retries，实测可达一分钟以上），所以 max_wait 只能约束
                「还要不要再起一次调用」，不能把已在途的调用打断。

        Returns:
            DataFrame，至少含 ts_code/close/open/pre_close/vol 等列。

        Raises:
            TimeoutError: 仅当传了 ``max_wait`` 且已超时。

        注意：**本方法不会返回空 DataFrame**。上游返回空（含非交易日）与
        API 报错走同一条重试路径 —— 无法区分「今天确实没数据」和「接口这一
        刻挂了」，而后者返回空会让调仓误判成全市场停牌。需要判非交易日的
        调用方请先查交易日历，不要靠这里的返回值。
        """
        cache_path = self.cache_dir / f"cb_daily_{trade_date}.parquet"

        # --- 缓存命中 ---
        if cache_path.exists():
            df = pd.read_parquet(cache_path)
            if not df.empty:
                return df

        # --- 缓存未命中：重试直到成功 ---
        start_ts = time.monotonic()
        last_log_ts = 0.0
        alerted = False
        attempt = 0

        while True:
            # --- 等待上限：默认 None = 不设上限，行为与历史一致 ---
            # 放在打接口之前：预算用光后不再起新调用，且 max_wait=0 等价于
            # 「只用缓存」。
            elapsed = time.monotonic() - start_ts
            if max_wait is not None and elapsed >= max_wait:
                raise TimeoutError(
                    f"cb_daily {trade_date} 未取到数据: 已等待 {elapsed:.1f}s "
                    f"(上限 {max_wait:.1f}s, {attempt} 次尝试)"
                )

            attempt += 1
            try:
                df = self._gateway.call(
                    "cb_daily",
                    trade_date=trade_date,
                    fields=self._SNAPSHOT_FIELDS,
                )
                if df is not None and not df.empty:
                    # 过滤 close<=0 的无效行
                    df = df[df["close"] > 0].copy()
                    if not df.empty:
                        self._atomic_write_parquet(df, cache_path)
                        elapsed = time.monotonic() - start_ts
                        if elapsed > 10:
                            logger.info(
                                "cb_daily %s 获取成功: %d 行 (耗时 %.0fs, %d 次尝试)",
                                trade_date, len(df), elapsed, attempt,
                            )
                        return df
            except Exception:
                pass  # 静默——由下方日志抑制逻辑统一处理

            # --- 日志抑制：仅每 log_interval 秒输出一次 ---
            elapsed = time.monotonic() - start_ts
            if elapsed - last_log_ts >= log_interval:
                logger.warning(
                    "cb_daily %s 仍在重试: 已 %.0f 分钟, %d 次尝试",
                    trade_date, elapsed / 60, attempt,
                )
                last_log_ts = elapsed

            # --- 飞书告警（仅一次） ---
            if not alerted and elapsed >= alert_after:
                self._send_snapshot_alert(trade_date, elapsed, attempt)
                alerted = True

            time.sleep(retry_interval)

    @staticmethod
    def _atomic_write_parquet(df: pd.DataFrame, path: Path) -> None:
        """原子写入 Parquet（先写 .tmp 再 rename，避免并发读到半成品）。"""
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
    def _send_snapshot_alert(trade_date: str, elapsed: float, attempt: int) -> None:
        """发送数据获取超时飞书告警（仅一次）。"""
        try:
            from aipha.monitor.feishu_alert import send_feishu_alert
            send_feishu_alert(
                f"CB 日线数据获取超时: {trade_date}",
                f"已重试 {attempt} 次 ({elapsed / 60:.0f} 分钟) 仍未获取到 cb_daily 数据。\n"
                f"请检查 TuShare API 状态或网络连接。",
                level="ERROR",
            )
        except Exception:
            logger.error("飞书告警发送失败", exc_info=True)

    def load_trading_cb_list(self, trade_date: str) -> pd.DataFrame:
        """获取指定日期的可交易可转债列表

        Args:
            trade_date: 交易日期 YYYYMMDD

        Returns:
            DataFrame 包含当日可交易的可转债（含行情和基础信息）
        """
        # 获取基础信息
        basic = self.load_cb_basic()

        # 过滤上市中的转债
        trade_dt = pd.to_datetime(trade_date, format="%Y%m%d")
        basic["list_date"] = pd.to_datetime(basic["list_date"], format="%Y%m%d")
        basic["delist_date"] = pd.to_datetime(basic["delist_date"], format="%Y%m%d", errors="coerce")

        # 上市且未摘牌
        mask = (basic["list_date"] <= trade_dt) & (
            basic["delist_date"].isna() | (basic["delist_date"] > trade_dt)
        )
        trading = basic[mask].copy()

        # 获取当日行情
        daily = self.load_cb_daily(trade_date=trade_date)

        if daily is None or len(daily) == 0:
            return pd.DataFrame()

        # 合并
        result = trading.merge(daily, on="ts_code", how="inner")

        return result

    def load_cb_call(self, use_cache: bool = True) -> pd.DataFrame:
        """获取可转债强赎/到赎公告数据（cb_call）

        TuShare cb_call 接口返回可转债赎回相关公告，包括：
        - call_type: '强赎' 或 '到赎'
        - is_call: 状态描述（已满足强赎条件 / 公告实施强赎 / 公告不强赎 / 公告提示强赎）
        - ann_date: 公告日期
        - call_date: 赎回日期（仅公告实施强赎时有值）

        Returns:
            DataFrame with columns:
                - ts_code: 转债代码
                - call_type: 赎回类型（强赎/到赎）
                - is_call: 赎回状态
                - ann_date: 公告日期 YYYYMMDD
                - call_date: 赎回日期 YYYYMMDD（可能为 None）
                - call_price: 赎回价格
                - call_price_tax: 赎回价格(税后)
                - call_vol: 赎回数量
                - call_amount: 赎回金额
                - payment_date: 兑付日
                - call_reg_date: 赎回登记日
        """
        cache_path = self.cache_dir / "cb_call.parquet"

        if use_cache and cache_path.exists():
            df = pd.read_parquet(cache_path)
            logger.info(f"从缓存加载可转债赎回公告: {len(df)} 条")
            return df

        logger.info("从 Tushare 获取可转债赎回公告...")

        # 分批获取：先获取强赎，再获取到赎
        all_data = []
        for call_type in ["强赎", "到赎"]:
            offset = 0
            while True:
                df_batch = self._gateway.call(
                    "cb_call",
                    call_type=call_type,
                    offset=offset,
                    limit=2000,
                    fields="ts_code,call_type,is_call,ann_date,call_date,"
                    "call_price,call_price_tax,call_vol,call_amount,"
                    "payment_date,call_reg_date",
                )
                if df_batch is None or len(df_batch) == 0:
                    break
                all_data.append(df_batch)
                if len(df_batch) < 2000:
                    break
                offset += 2000

        if all_data:
            result = pd.concat(all_data, ignore_index=True)
            # 去重（同一 ts_code + ann_date + is_call 可能重复）
            result = result.drop_duplicates(
                subset=["ts_code", "ann_date", "is_call"], keep="last"
            )
            result = result.sort_values(
                ["ts_code", "ann_date"], ascending=[True, True]
            ).reset_index(drop=True)

            self._atomic_write_parquet(result, cache_path)
            logger.info(f"获取并缓存可转债赎回公告: {len(result)} 条")
            return result

        logger.warning("未获取到可转债赎回公告数据")
        return pd.DataFrame()

    def load_cb_price_chg(self, use_cache: bool = True) -> pd.DataFrame:
        """获取可转债转股价变动历史数据（cb_price_chg）

        TuShare cb_price_chg 接口返回转股价变动记录，含下修/送股/配股等调整。
        注意：此接口需单独开权限（非积分相关），权限不足时返回空 DataFrame 并 warning。

        关键字段：
        - publish_date: 公告日期（PIT 信号时点）
        - change_date: 变动生效日期
        - convertprice_bef / convertprice_aft: 修正前后转股价

        Returns:
            DataFrame with columns:
                - ts_code: 转债代码
                - bond_short_name: 转债简称
                - publish_date: 公告日期 YYYYMMDD
                - change_date: 变动日期 YYYYMMDD
                - convert_price_initial: 初始转股价
                - convertprice_bef: 修正前转股价（可能为 None，表示初始记录）
                - convertprice_aft: 修正后转股价（可能为 None，表示初始记录）
        """
        cache_path = self.cache_dir / "cb_price_chg.parquet"

        if use_cache and cache_path.exists():
            df = pd.read_parquet(cache_path)
            logger.info(f"从缓存加载可转债转股价变动: {len(df)} 条")
            return df

        logger.info("从 Tushare 获取可转债转股价变动数据...")

        # 需要先获取所有转债代码列表
        cb_basic = self.load_cb_basic(use_cache=True)
        if cb_basic.empty:
            logger.warning("无法获取转债基础信息，跳过转股价变动数据获取")
            return pd.DataFrame()

        all_codes = cb_basic["ts_code"].unique().tolist()
        fields = (
            "ts_code,bond_short_name,publish_date,change_date,"
            "convert_price_initial,convertprice_bef,convertprice_aft"
        )

        all_data = []
        # 分批查询 — cb_price_chg 限频 5次/分钟，用大批次 + 显式延迟
        batch_size = 100
        import time as _time

        for i in range(0, len(all_codes), batch_size):
            batch_codes = all_codes[i : i + batch_size]
            ts_code_str = ",".join(batch_codes)
            try:
                df_batch = self._gateway.call(
                    "cb_price_chg",
                    ts_code=ts_code_str,
                    fields=fields,
                )
                if df_batch is not None and len(df_batch) > 0:
                    all_data.append(df_batch)
                logger.info(
                    "cb_price_chg batch %d/%d: %d 条",
                    i // batch_size + 1,
                    (len(all_codes) + batch_size - 1) // batch_size,
                    len(df_batch) if df_batch is not None else 0,
                )
            except Exception as e:
                err_msg = str(e)
                if "权限" in err_msg or "permission" in err_msg.lower():
                    logger.warning(
                        "cb_price_chg 接口权限不足，需在 TuShare 后台单独开通。"
                        "返回空 DataFrame。"
                    )
                    return pd.DataFrame()
                logger.warning("cb_price_chg batch %d 获取失败: %s", i, e)
            # 显式延迟避免触发 5次/分钟限制
            if i + batch_size < len(all_codes):
                _time.sleep(15)

        if all_data:
            result = pd.concat(all_data, ignore_index=True)
            result = result.drop_duplicates(
                subset=["ts_code", "change_date"], keep="last"
            )
            result = result.sort_values(
                ["ts_code", "change_date"], ascending=[True, True]
            ).reset_index(drop=True)

            self._atomic_write_parquet(result, cache_path)
            logger.info(f"获取并缓存可转债转股价变动: {len(result)} 条")
            return result

        logger.warning("未获取到可转债转股价变动数据")
        return pd.DataFrame()

    def build_downward_revision_events(
        self,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """从转股价变动数据中提取下修事件。

        筛选逻辑：
        - convertprice_bef 和 convertprice_aft 均非空
        - convertprice_aft < convertprice_bef（价格下调）
        - 下修幅度 > 1%（排除除权除息导致的微小调整）

        Returns:
            DataFrame with columns:
                - ts_code, bond_short_name, publish_date, change_date
                - convertprice_bef, convertprice_aft
                - revision_pct: 下修幅度（负值，如 -0.15 表示下修 15%）
        """
        price_chg = self.load_cb_price_chg(use_cache=use_cache)
        if price_chg.empty:
            return pd.DataFrame()

        # 过滤有效变动（排除初始记录）
        df = price_chg.dropna(
            subset=["convertprice_bef", "convertprice_aft"]
        ).copy()
        if df.empty:
            return pd.DataFrame()

        df["convertprice_bef"] = pd.to_numeric(df["convertprice_bef"], errors="coerce")
        df["convertprice_aft"] = pd.to_numeric(df["convertprice_aft"], errors="coerce")
        df = df.dropna(subset=["convertprice_bef", "convertprice_aft"])

        # 计算变动幅度
        df["revision_pct"] = (
            (df["convertprice_aft"] - df["convertprice_bef"]) / df["convertprice_bef"]
        )

        # 仅保留下修事件（价格下调且幅度 > 1%）
        events = df[df["revision_pct"] < -0.01].copy()
        events = events.sort_values(
            ["change_date", "ts_code"], ascending=[True, True]
        ).reset_index(drop=True)

        logger.info(
            f"提取下修事件: {len(events)} 次 "
            f"(占总变动 {len(df)} 次的 {len(events)/max(len(df),1)*100:.1f}%)"
        )
        return events

    def build_downward_revision_lookup(
        self,
        as_of_date: str | None = None,
        use_cache: bool = True,
    ) -> dict[str, dict]:
        """构建可转债下修事件查询表（PIT 安全）。"""
        events = self.build_downward_revision_events(use_cache=use_cache)
        if events.empty:
            return {}
        return DownwardRevisionTracker(events).build_lookup(as_of_date=as_of_date)

    def build_force_redeem_lookup(
        self,
        as_of_date: str | None = None,
        use_cache: bool = True,
    ) -> dict[str, dict]:
        """构建可转债强赎状态查询表（PIT 安全）

        基于 cb_call 公告数据，为每只转债构建截至 as_of_date 的最新强赎状态。
        严格遵循 PIT（Point-In-Time）原则：只使用 ann_date <= as_of_date 的公告。

        Args:
            as_of_date: 截止日期 YYYYMMDD（默认今日）
            use_cache: 是否使用本地缓存

        Returns:
            {ts_code: {
                "is_called": bool,           # 是否已公告实施强赎
                "force_redeem_status": str,   # 最新状态描述
                "ann_date": str,              # 最新公告日期
                "call_date": str | None,      # 赎回日期（如已公告实施）
            }}
        """
        cb_call = self.load_cb_call(use_cache=use_cache)
        if cb_call is None or cb_call.empty:
            return {}

        # 只取强赎类型
        force_redeem = cb_call[cb_call["call_type"] == "强赎"].copy()
        if force_redeem.empty:
            return {}

        # PIT 过滤：只使用 as_of_date 及之前的公告
        if as_of_date:
            force_redeem = force_redeem[
                force_redeem["ann_date"].fillna("") <= as_of_date
            ]
        if force_redeem.empty:
            return {}

        # 按 ts_code 分组，取最新公告
        force_redeem = force_redeem.sort_values("ann_date", ascending=True)
        lookup: dict[str, dict] = {}

        for ts_code, grp in force_redeem.groupby("ts_code"):
            latest = grp.iloc[-1]
            status = str(latest.get("is_call", "")).strip()

            # 映射 is_call 到 is_called 布尔值
            # "公告实施强赎" → True（确定要执行强赎）
            # "已满足强赎条件" → False（满足条件但未公告执行）
            # "公告不强赎" → False（明确不强赎）
            # "公告提示强赎" → False（提示性公告）
            is_called = status in ("公告实施强赎",)

            call_date = latest.get("call_date")
            if call_date is not None and not pd.isna(call_date):
                call_date = str(call_date)
            else:
                call_date = None

            ann_date = latest.get("ann_date")
            if ann_date is not None and not pd.isna(ann_date):
                ann_date = str(ann_date)
            else:
                ann_date = None

            lookup[str(ts_code)] = {
                "is_called": is_called,
                "force_redeem_status": status,
                "ann_date": ann_date,
                "call_date": call_date,
            }

        logger.info(
            f"强赎状态查询表构建完成: {len(lookup)} 只转债"
            f" (as_of={as_of_date or 'latest'})"
        )

        return lookup

    def load_cb_history_with_premium(
        self,
        ts_code: str,
        start_date: str,
        end_date: str,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """获取单个可转债的历史数据（含溢价率）

        Args:
            ts_code: 转债代码
            start_date: 开始日期 YYYYMMDD
            end_date: 结束日期 YYYYMMDD

        Returns:
            DataFrame with OHLCV + cb_value + cb_over_rate
        """
        cache_path = self.cache_dir / f"history/{ts_code.replace('.', '_')}_{start_date}_{end_date}.parquet"
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        if use_cache and cache_path.exists():
            df = pd.read_parquet(cache_path)
            logger.debug(f"从缓存加载 {ts_code} 历史: {len(df)} 条")
            return df

        logger.info(f"从 Tushare 获取 {ts_code} 历史数据...")
        df = self._gateway.call(
            "cb_daily",
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            fields="ts_code,trade_date,open,high,low,close,vol,amount,cb_value,cb_over_rate"
        )

        if df is not None and len(df) > 0:
            # 过滤无效数据
            df = df[df["close"] > 0].copy()
            df = df.sort_values("trade_date").reset_index(drop=True)

            if len(df) > 0:
                self._atomic_write_parquet(df, cache_path)
                logger.info(f"获取并缓存 {ts_code} 历史: {len(df)} 条")

            return df

        return pd.DataFrame()


def test_provider() -> None:
    """测试 TuShareCBProvider"""
    provider = TuShareCBProvider()

    # 测试基础信息
    print("\n=== 可转债基础信息 ===")
    basic = provider.load_cb_basic()
    print(f"总计: {len(basic)} 只可转债")
    print(basic[["ts_code", "bond_short_name", "stk_code", "list_date", "conv_price"]].head(5))

    # 测试某日行情
    print("\n=== 2024-01-02 行情 ===")
    daily = provider.load_cb_daily(trade_date="20240102")
    print(f"有效数据: {len(daily)} 条")
    print(daily[["ts_code", "close", "cb_value", "cb_over_rate"]].head(10))

    # 测试单个转债历史
    print("\n=== 113676.SH 历史 ===")
    history = provider.load_cb_history_with_premium(
        ts_code="113676.SH",
        start_date="20240101",
        end_date="20240131"
    )
    print(f"历史数据: {len(history)} 条")
    print(history.head())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test_provider()
