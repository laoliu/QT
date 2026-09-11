"""AKShare 可转债数据适配器

与 CBDataAdapter (TuShare) 功能对等，输出格式完全相同的
``dict[date, dict[str, Bar]]``，但使用 AKShare 接口获取数据，
无 API 限频问题。

数据来源::

    - bond_zh_hs_cov_spot()          → 获取活跃可转债列表
    - bond_zh_hs_cov_daily(symbol)   → 单只 CB 历史 OHLCV
    - bond_zh_cov_value_analysis(symbol) → 转股价值 + 溢价率历史

典型用法::

    adapter = AKShareCBDataAdapter(cache_dir="data/cache/cb_akshare")
    bars_by_date = adapter.load("20220101", "20250905")

    engine = BacktestEngine(config, strategy, ...)
    result = engine.run(bars_by_date)
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from aipha.domain.models import Bar
from aipha.utils.paths import project_path

logger = logging.getLogger(__name__)


class AKShareCBDataAdapter:
    """AKShare 可转债数据适配器

    无 API 限频，使用 Parquet 本地缓存加速重复加载。

    Args:
        cache_dir: 本地缓存目录
        cb_basic_path: 可选，TuShare cb_basic.parquet 路径，
                       用于补充 remain_size/issue_size/rating/maturity_date 等静态字段
    """

    def __init__(
        self,
        cache_dir: str = "",
        cb_basic_path: Optional[str] = None,
    ):
        if not cache_dir:
            from aipha.config import get_cache_dir
            cache_dir = str(get_cache_dir() / "cb_akshare")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cb_basic_path = cb_basic_path
        self._basic_df: Optional[pd.DataFrame] = None

    def load(
        self,
        start_date: str,
        end_date: str,
        max_bonds: int = 0,
    ) -> dict[date, dict[str, Bar]]:
        """加载日期范围内的可转债数据。

        Args:
            start_date: 开始日期 YYYYMMDD
            end_date: 结束日期 YYYYMMDD
            max_bonds: 最大加载数量 (0=全部)

        Returns:
            date -> {ts_code -> Bar} 映射
        """
        import akshare as ak

        start_dt = self._parse_date(start_date)
        end_dt = self._parse_date(end_date)
        if not start_dt or not end_dt:
            logger.error(f"日期解析失败: {start_date}, {end_date}")
            return {}

        logger.info(f"AKShare 加载可转债数据: {start_date} ~ {end_date}")

        # 1. 加载 cb_basic (如有 TuShare 缓存)
        basic_map = self._load_basic_map()

        # 2. 获取可转债列表
        combined_cache = self.cache_dir / f"cb_combined_{start_date}_{end_date}.parquet"
        if combined_cache.exists():
            logger.info(f"使用缓存: {combined_cache}")
            all_daily = pd.read_parquet(combined_cache)
        else:
            all_daily = self._fetch_all_cb_data(
                ak, start_dt, end_dt, max_bonds=max_bonds
            )
            if all_daily is not None and len(all_daily) > 0:
                all_daily.to_parquet(combined_cache, index=False)
                logger.info(f"缓存已保存: {combined_cache} ({len(all_daily)} 行)")

        if all_daily is None or len(all_daily) == 0:
            logger.warning("无可转债行情数据")
            return {}

        # 3. 过滤日期范围
        all_daily["date_dt"] = pd.to_datetime(all_daily["date"])
        mask = (all_daily["date_dt"].dt.date >= start_dt) & (
            all_daily["date_dt"].dt.date <= end_dt
        )
        all_daily = all_daily[mask].copy()
        logger.info(f"日期范围过滤后: {len(all_daily)} 行")

        # 4. 转换为 dict[date, dict[str, Bar]]
        bars_by_date: dict[date, dict[str, Bar]] = {}

        for trade_date, group in all_daily.groupby(all_daily["date_dt"].dt.date):
            bars: dict[str, Bar] = {}
            dt_start = datetime.combine(trade_date, datetime.min.time().replace(hour=9, minute=30))
            dt_end = datetime.combine(trade_date, datetime.min.time().replace(hour=15))

            for row in group.itertuples(index=False):
                row_dict = row._asdict()
                ts_code = str(row_dict.get("ts_code", ""))
                close_val = self._safe_float(row_dict.get("close", 0))
                if close_val <= 0:
                    continue

                open_val = self._safe_float(row_dict.get("open", close_val))
                vol_val = self._safe_float(row_dict.get("volume", 0))

                is_suspended = (open_val <= 0 or vol_val <= 0) and close_val > 0

                # 构建 metadata
                metadata = self._build_metadata(row_dict, basic_map, trade_date)

                # 前收盘价
                prev_close_val = None
                raw_prev = row_dict.get("prev_close")
                if raw_prev is not None and not pd.isna(raw_prev):
                    prev_close_val = self._safe_float(raw_prev)
                    if prev_close_val <= 0:
                        prev_close_val = None

                bar = Bar(
                    symbol=ts_code,
                    start_ts=dt_start,
                    end_ts=dt_end,
                    open=open_val,
                    high=self._safe_float(row_dict.get("high", close_val)),
                    low=self._safe_float(row_dict.get("low", close_val)),
                    close=close_val,
                    volume=vol_val,
                    prev_close=prev_close_val,
                    is_suspended=is_suspended,
                    metadata=metadata,
                )
                bars[ts_code] = bar

            if bars:
                bars_by_date[trade_date] = bars

        total_bars = sum(len(v) for v in bars_by_date.values())
        logger.info(
            f"转换完成: {len(bars_by_date)} 个交易日, "
            f"{total_bars} 条有效 Bar 记录"
        )
        return bars_by_date

    # ------------------------------------------------------------------
    # 数据拉取
    # ------------------------------------------------------------------

    def _fetch_all_cb_data(
        self, ak, start_dt: date, end_dt: date, max_bonds: int = 0,
    ) -> Optional[pd.DataFrame]:
        """从 AKShare 拉取全部活跃 CB 的日线 + 价值分析数据。"""
        # 获取活跃 CB 列表
        try:
            spot = ak.bond_zh_hs_cov_spot()
        except Exception as e:
            logger.error(f"获取可转债列表失败: {e}")
            return None

        # 过滤有交易的 CB
        spot["trade_f"] = pd.to_numeric(spot["trade"], errors="coerce").fillna(0)
        active = spot[spot["trade_f"] > 0].copy()
        codes = active["code"].tolist()
        names = dict(zip(active["code"], active["name"]))

        if max_bonds > 0:
            codes = codes[:max_bonds]

        logger.info(f"活跃可转债: {len(codes)} 只")

        all_frames = []
        failed = []

        for i, code in enumerate(codes):
            # 确定 symbol 前缀: sh=上交所(11开头), sz=深交所(12开头)
            prefix = "sh" if code.startswith("11") else "sz"
            symbol_daily = f"{prefix}{code}"

            # 构建 ts_code (TuShare 格式, 保持兼容)
            exchange = "SH" if code.startswith("11") else "SZ"
            ts_code = f"{code}.{exchange}"

            # 单只缓存
            bond_cache = self.cache_dir / f"bond_{code}.parquet"
            if bond_cache.exists():
                df = pd.read_parquet(bond_cache)
                if len(df) > 0:
                    all_frames.append(df)
                    if (i + 1) % 50 == 0:
                        logger.info(f"  [{i+1}/{len(codes)}] {code} (缓存)")
                    continue

            try:
                # 拉取 OHLCV 日线
                df_daily = ak.bond_zh_hs_cov_daily(symbol=symbol_daily)
                if df_daily is None or len(df_daily) == 0:
                    failed.append(code)
                    continue

                df_daily = df_daily.rename(columns={"date": "date_str"})
                df_daily["date"] = pd.to_datetime(df_daily["date_str"])
                df_daily["ts_code"] = ts_code
                df_daily["bond_name"] = names.get(code, "")

                # 拉取价值分析 (转股价值 + 溢价率)
                try:
                    df_value = ak.bond_zh_cov_value_analysis(symbol=code)
                    if df_value is not None and len(df_value) > 0:
                        df_value = df_value.rename(columns={
                            df_value.columns[0]: "date",
                            df_value.columns[1]: "close_val",
                            df_value.columns[2]: "bond_value",  # 纯债价值
                            df_value.columns[3]: "cb_value",    # 转股价值
                            df_value.columns[4]: "bond_premium", # 纯债溢价率
                            df_value.columns[5]: "cb_over_rate", # 转股溢价率
                        })
                        df_value["date"] = pd.to_datetime(df_value["date"])
                        df_daily = df_daily.merge(
                            df_value[["date", "cb_value", "cb_over_rate"]],
                            on="date", how="left",
                        )
                except Exception as e:
                    logger.debug(f"  {code} 价值分析获取失败 (降级): {e}")

                # 计算 prev_close
                df_daily = df_daily.sort_values("date")
                df_daily["prev_close"] = df_daily["close"].shift(1)

                # 保存单只缓存
                df_daily.to_parquet(bond_cache, index=False)
                all_frames.append(df_daily)

                if (i + 1) % 10 == 0 or (i + 1) == len(codes):
                    logger.info(
                        f"  [{i+1}/{len(codes)}] {code} {names.get(code, '')}: "
                        f"{len(df_daily)} 天"
                    )

                # 礼貌间隔 (AKShare 不限频，但避免被封)
                if (i + 1) % 5 == 0:
                    time.sleep(0.3)

            except Exception as e:
                failed.append(code)
                logger.debug(f"  {code} 拉取失败: {e}")

        if failed:
            logger.warning(f"拉取失败: {len(failed)} 只 - {failed[:10]}")

        if not all_frames:
            return None

        combined = pd.concat(all_frames, ignore_index=True)
        logger.info(f"合并完成: {len(combined)} 行, {len(all_frames)} 只 CB")
        return combined

    # ------------------------------------------------------------------
    # cb_basic 补充静态字段
    # ------------------------------------------------------------------

    def _load_basic_map(self) -> dict[str, dict]:
        """加载 cb_basic 为 {ts_code: info} 映射。"""
        if self._basic_df is not None:
            return self._basic_to_map(self._basic_df)

        # 优先用指定路径
        if self._cb_basic_path and Path(self._cb_basic_path).exists():
            self._basic_df = pd.read_parquet(self._cb_basic_path)
            logger.info(f"cb_basic loaded from {self._cb_basic_path}: {len(self._basic_df)} rows")
            return self._basic_to_map(self._basic_df)

        # 尝试 TuShare 缓存的默认位置
        candidates = [
            self.cache_dir.parent / "cb_tushare" / "cb_basic.parquet",
            project_path("data", "cache", "cb_tushare", "cb_basic.parquet"),
        ]
        for p in candidates:
            if p.exists():
                self._basic_df = pd.read_parquet(p)
                logger.info(f"cb_basic loaded from {p}: {len(self._basic_df)} rows")
                return self._basic_to_map(self._basic_df)

        logger.warning("cb_basic 未找到，静态字段（remain_size, rating 等）将缺失")
        return {}

    @staticmethod
    def _basic_to_map(df: pd.DataFrame) -> dict[str, dict]:
        """将 cb_basic DataFrame 转为 {ts_code: dict} 映射。"""
        result = {}
        for row in df.itertuples(index=False):
            d = row._asdict()
            ts_code = str(d.get("ts_code", ""))
            if ts_code:
                result[ts_code] = d
        return result

    # ------------------------------------------------------------------
    # metadata 构建
    # ------------------------------------------------------------------

    def _build_metadata(
        self, row: dict, basic_map: dict[str, dict], trade_date: date,
    ) -> dict:
        """构建 Bar.metadata，保持与 CBDataAdapter 格式兼容。"""
        meta: dict = {}
        ts_code = str(row.get("ts_code", ""))

        # 转股价值 (来自 AKShare value analysis)
        cb_value = row.get("cb_value")
        if cb_value is not None and not pd.isna(cb_value):
            meta["cb_value"] = float(cb_value)

        # 转股溢价率
        cb_over_rate = row.get("cb_over_rate")
        if cb_over_rate is not None and not pd.isna(cb_over_rate):
            meta["cb_over_rate"] = float(cb_over_rate)

        # 转债简称
        bond_name = row.get("bond_name")
        if bond_name and not pd.isna(bond_name):
            meta["bond_short_name"] = str(bond_name)

        # 从 cb_basic 补充静态字段
        basic = basic_map.get(ts_code, {})
        if basic:
            # remain_size: 元 → 万元
            rs = basic.get("remain_size")
            if rs is not None and not pd.isna(rs):
                meta["remain_size"] = float(rs) / 10000.0

            is_sz = basic.get("issue_size")
            if is_sz is not None and not pd.isna(is_sz):
                meta["issue_size"] = float(is_sz) / 10000.0

            for field in ("maturity_date", "delist_date", "list_date"):
                val = basic.get(field)
                if val is not None and not pd.isna(val):
                    meta[field] = str(val).replace("-", "")[:8]

            stk_code = basic.get("stk_code")
            if stk_code and not pd.isna(stk_code):
                meta["stk_code"] = str(stk_code)

            stk_name = basic.get("stk_short_name")
            if stk_name and not pd.isna(stk_name):
                meta["stk_short_name"] = str(stk_name)

            rating = basic.get("rating")
            if rating and not pd.isna(rating):
                meta["rating"] = str(rating).strip()

            conv_price = basic.get("conv_price")
            if conv_price is not None and not pd.isna(conv_price):
                try:
                    meta["conv_price"] = float(conv_price)
                except (TypeError, ValueError):
                    pass

        # 成交额 (volume * close / 10000 → 万元)
        vol = row.get("volume") or row.get("vol")
        close_p = row.get("close")
        if vol is not None and close_p is not None:
            try:
                amount_val = float(vol) * float(close_p) / 10000.0
                if amount_val > 0:
                    meta["amount"] = amount_val
            except (TypeError, ValueError):
                pass

        # 简化的强赎判断 (基于 cb_value)
        cb_val = meta.get("cb_value")
        if cb_val is not None and cb_val >= 130.0:
            meta["is_called"] = None
            meta["force_redeem_status"] = f"启发式: cb_value={cb_val:.2f}≥130"
        else:
            meta["is_called"] = False

        return meta

    # ------------------------------------------------------------------
    # 工具函数
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_date(date_str: str) -> Optional[date]:
        """解析日期字符串。"""
        for fmt in ("%Y%m%d", "%Y-%m-%d"):
            try:
                return datetime.strptime(date_str, fmt).date()
            except ValueError:
                continue
        return None

    @staticmethod
    def _safe_float(val, default: float = 0.0) -> float:
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return default
        try:
            return float(val)
        except (TypeError, ValueError):
            return default
