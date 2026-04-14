"""CB 市场聚合特征计算器 — 为 Regime 检测提供市场级指标。

从 CB 个债日线数据聚合计算:
- 市场等权指数 (median close → 构造合成指数)
- 中位溢价率 (median premium rate)
- 总成交额 / 日均成交额
- 市场宽度 (上涨占比 / 活跃债券数)

理论基础:
- 中位溢价率反映 CB 市场整体"债性 vs 股性"偏向
- 成交额反映市场参与热度
- 市场宽度反映行情广度 vs 集中度

数据来源: data/cache/cb_tushare/cb_daily_*.parquet
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class CBMarketSnapshot:
    """单日 CB 市场聚合快照。"""
    date: str
    n_active: int                # 当日活跃债券数 (vol>0)
    median_close: float          # 中位收盘价
    mean_close: float            # 均值收盘价
    median_premium: float        # 中位溢价率 (%)
    total_amount: float          # 总成交额 (万元)
    advance_ratio: float         # 上涨占比 (0~1)
    index_value: float           # 合成等权指数值


class CBMarketAggregator:
    """从 CB 个债日线数据计算市场级聚合特征。

    Usage:
        agg = CBMarketAggregator()
        agg.load_from_cache("data/cache/cb_tushare")
        snapshots = agg.get_snapshots()
        # {date_str: CBMarketSnapshot}
    """

    def __init__(self) -> None:
        self._snapshots: dict[str, CBMarketSnapshot] = {}
        self._loaded = False

    @property
    def snapshots(self) -> dict[str, CBMarketSnapshot]:
        return self._snapshots

    def load_from_cache(
        self,
        cache_dir: str | Path,
        start_date: str = "20180101",
        end_date: str = "20261231",
    ) -> None:
        """从缓存 parquet 文件加载并聚合。"""
        self._load_impl(cache_dir, start_date, end_date)

    def _load_impl(
        self, cache_dir: str | Path, start_date: str, end_date: str
    ) -> None:
        """核心加载逻辑 — 分步实现。"""
        import pandas as pd

        cache_dir = Path(cache_dir)
        # 优先加载最大的范围缓存
        range_files = sorted(cache_dir.glob("cb_daily_2*_2*.parquet"))
        day_files = sorted(cache_dir.glob("cb_daily_2???????.parquet"))

        frames = []
        covered_dates: set[str] = set()

        # 加载范围缓存
        if range_files:
            best = max(range_files, key=lambda f: f.stat().st_size)
            logger.info("Loading range cache: %s", best.name)
            df = pd.read_parquet(best)
            df = df[(df["trade_date"] >= start_date) &
                    (df["trade_date"] <= end_date)]
            frames.append(df)
            covered_dates.update(df["trade_date"].unique())

        # 补充逐日缓存 (范围缓存之后的日期)
        for f in day_files:
            date_str = f.stem.replace("cb_daily_", "")
            if date_str in covered_dates:
                continue
            if date_str < start_date or date_str > end_date:
                continue
            try:
                df = pd.read_parquet(f)
                if not df.empty:
                    frames.append(df)
                    covered_dates.add(date_str)
            except Exception:
                continue

        if not frames:
            logger.warning("No CB data loaded from %s", cache_dir)
            return

        all_data = pd.concat(frames, ignore_index=True)
        self._aggregate(all_data)
        self._loaded = True
        logger.info(
            "CB market aggregated: %d trading days, %s ~ %s",
            len(self._snapshots),
            min(self._snapshots.keys()),
            max(self._snapshots.keys()),
        )

    def _aggregate(self, df: object) -> None:
        """按日聚合 CB 市场特征。"""
        import pandas as pd

        # 筛选活跃债券 (有成交 & 价格有效)
        active = df[(df["vol"] > 0) & (df["close"] > 0)].copy()

        # 计算日收益率 (需要前一日收盘价)
        active = active.sort_values(["ts_code", "trade_date"])
        active["prev_close"] = active.groupby("ts_code")["close"].shift(1)
        active["daily_ret"] = (
            (active["close"] - active["prev_close"]) / active["prev_close"]
        )

        # 构建合成等权指数 (以 median close 为基础)
        prev_index = 1000.0  # 初始指数值

        for trade_date, group in active.groupby("trade_date", sort=True):
            n_active = len(group)
            if n_active < 5:
                continue

            median_close = float(group["close"].median())
            mean_close = float(group["close"].mean())
            prem_valid = group["cb_over_rate"].dropna()
            median_premium = float(prem_valid.median()) if len(prem_valid) > 0 else 0.0
            total_amount = float(group["amount"].sum())

            # 上涨占比
            has_ret = group["daily_ret"].notna()
            if has_ret.any():
                up_count = (group.loc[has_ret, "daily_ret"] > 0).sum()
                advance_ratio = float(up_count / has_ret.sum())
            else:
                advance_ratio = 0.5

            # 合成指数: 用全市场中位日收益率更新
            valid_rets = group["daily_ret"].dropna()
            if len(valid_rets) > 5:
                market_ret = float(valid_rets.median())
                index_value = prev_index * (1 + market_ret)
            else:
                index_value = prev_index

            self._snapshots[str(trade_date)] = CBMarketSnapshot(
                date=str(trade_date),
                n_active=n_active,
                median_close=median_close,
                mean_close=mean_close,
                median_premium=median_premium,
                total_amount=total_amount,
                advance_ratio=advance_ratio,
                index_value=index_value,
            )
            prev_index = index_value

    def get_series(self) -> dict[str, dict[str, float]]:
        """返回各指标的时间序列字典。

        Returns:
            {
                "index_value": {date: value, ...},
                "median_premium": {date: value, ...},
                "total_amount": {date: value, ...},
                "advance_ratio": {date: value, ...},
            }
        """
        result: dict[str, dict[str, float]] = {
            "index_value": {},
            "median_premium": {},
            "total_amount": {},
            "advance_ratio": {},
            "n_active": {},
        }
        for d, snap in sorted(self._snapshots.items()):
            result["index_value"][d] = snap.index_value
            result["median_premium"][d] = snap.median_premium
            result["total_amount"][d] = snap.total_amount
            result["advance_ratio"][d] = snap.advance_ratio
            result["n_active"][d] = float(snap.n_active)
        return result

    def get_aligned_arrays(
        self, dates: list[str]
    ) -> dict[str, np.ndarray]:
        """按给定日期序列对齐返回 numpy 数组（前向填充缺失）。

        Args:
            dates: 目标日期列表 (YYYY-MM-DD 或 YYYYMMDD 格式)

        Returns:
            {indicator_name: np.ndarray}，长度与 dates 相同。
        """
        series = self.get_series()
        results: dict[str, np.ndarray] = {}

        for key, date_vals in series.items():
            arr = np.full(len(dates), np.nan)
            # 统一日期格式
            normalized = {}
            for d, v in date_vals.items():
                nd = d.replace("-", "")
                normalized[nd] = v

            last_val = np.nan
            for i, dt in enumerate(dates):
                nd = dt.replace("-", "")
                if nd in normalized:
                    last_val = normalized[nd]
                arr[i] = last_val
            results[key] = arr

        return results
