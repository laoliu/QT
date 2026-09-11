"""TuShare 分析师一致预期 Provider (TS-1 Stage 4, R156)

提供券商研报一致预期数据 (report_rc)，用于构造分析师情绪因子
（EPS 预测修正、目标价上行空间、分析师覆盖数等）。

缓存策略：
- data/cache/tushare/analyst_{ts_code}_{YYYYMM}.parquet
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from aipha.data.tushare_gateway import TuShareGateway, get_tushare_gateway
from aipha.utils.paths import project_path

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = project_path("data", "cache", "tushare")


class TuShareAnalystProvider:
    """分析师一致预期 Provider（TuShare report_rc）。"""

    def __init__(
        self,
        gateway: TuShareGateway | None = None,
        cache_dir: Path | None = None,
    ) -> None:
        self._gw = gateway or get_tushare_gateway()
        self._cache_dir = cache_dir or _DEFAULT_CACHE_DIR
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def get_report_rc(
        self,
        ts_code: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """券商研报一致预期 EPS / 目标价 / 评级。

        Args:
            ts_code: 股票代码 (e.g. '600000.SH')
            start_date: YYYYMMDD
            end_date: YYYYMMDD

        Returns:
            DataFrame with: ts_code, report_date, eps, target_price,
            rating, organ_name, ...
        """
        cache_path = (
            self._cache_dir
            / f"analyst_{ts_code.replace('.', '_')}_{start_date[:6]}_{end_date[:6]}.parquet"
        )
        if cache_path.exists():
            cached = pd.read_parquet(cache_path)
            if not cached.empty:
                return cached

        logger.info("从 TuShare API 下载 report_rc: %s %s~%s", ts_code, start_date, end_date)
        df = self._gw.call(
            "report_rc",
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
        )
        if df is None:
            df = pd.DataFrame()
        if not df.empty:
            df.to_parquet(cache_path, index=False)
        return df

    def get_forecast_revision(
        self,
        ts_codes: list[str],
        query_date: str,
        lookback_days: int = 30,
    ) -> pd.DataFrame:
        """EPS 预测修正幅度（lookback_days 内变化）。

        Args:
            ts_codes: 股票代码列表
            query_date: YYYYMMDD 基准日
            lookback_days: 回看天数

        Returns:
            DataFrame with: ts_code, eps_revision (当前 vs lookback 前)
        """
        from datetime import datetime, timedelta

        qd = datetime.strptime(query_date, "%Y%m%d")
        start = (qd - timedelta(days=lookback_days)).strftime("%Y%m%d")

        rows: list[dict] = []
        for code in ts_codes:
            df = self.get_report_rc(code, start, query_date)
            if df.empty or "eps" not in df.columns:
                continue
            df = df.sort_values("report_date") if "report_date" in df.columns else df
            eps_vals = df["eps"].dropna()
            if len(eps_vals) >= 2:
                revision = (eps_vals.iloc[-1] - eps_vals.iloc[0]) / abs(eps_vals.iloc[0]) if eps_vals.iloc[0] != 0 else 0.0
                rows.append({"ts_code": code, "eps_revision": float(revision)})
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["ts_code", "eps_revision"])

    def get_coverage_count(
        self,
        ts_codes: list[str],
        query_date: str,
    ) -> pd.DataFrame:
        """分析师覆盖数（最近 90 天内活跃机构数）。

        Args:
            ts_codes: 股票代码列表
            query_date: YYYYMMDD

        Returns:
            DataFrame with: ts_code, coverage_count
        """
        from datetime import datetime, timedelta

        qd = datetime.strptime(query_date, "%Y%m%d")
        start = (qd - timedelta(days=90)).strftime("%Y%m%d")

        rows: list[dict] = []
        for code in ts_codes:
            df = self.get_report_rc(code, start, query_date)
            if df.empty:
                rows.append({"ts_code": code, "coverage_count": 0})
                continue
            if "organ_name" in df.columns:
                cnt = df["organ_name"].nunique()
            else:
                cnt = len(df)
            rows.append({"ts_code": code, "coverage_count": cnt})
        return pd.DataFrame(rows)

    def batch_download_report_rc(
        self,
        stock_list: list[str],
        start_date: str = "20180101",
        end_date: str = "",
        output_dir: str | Path = "data/cache/alternative",
    ) -> dict[str, int]:
        """批量下载分析师一致预期并按月聚合保存。

        遍历 stock_list, 逐只下载 report_rc, 聚合为月度文件
        analyst_YYYYMM.parquet (与 analyst_factors 期望的格式一致)。

        Args:
            stock_list: A 股代码列表 (如 ['600000.SH', ...])
            start_date: 起始日期 YYYYMMDD
            end_date: 结束日期 YYYYMMDD (默认=今天)
            output_dir: 输出目录

        Returns:
            {YYYYMM: record_count}
        """
        import time
        from datetime import datetime

        if not end_date:
            end_date = datetime.now().strftime("%Y%m%d")

        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        all_data: list[pd.DataFrame] = []
        total = len(stock_list)

        for i, code in enumerate(stock_list):
            if (i + 1) % 100 == 0:
                logger.info(
                    "report_rc 下载进度: %d/%d (%.0f%%)",
                    i + 1, total, (i + 1) / total * 100,
                )
            try:
                df = self.get_report_rc(code, start_date, end_date)
                if not df.empty:
                    all_data.append(df)
            except Exception as e:
                logger.warning("report_rc %s 失败: %s", code, e)
            time.sleep(0.05)  # 底层 gateway 已有限流

        if not all_data:
            logger.warning("report_rc 批量下载: 无数据返回")
            return {}

        merged = pd.concat(all_data, ignore_index=True)
        logger.info("report_rc 汇总: %d 条记录", len(merged))

        # 按月分片保存
        if "report_date" in merged.columns:
            merged["_ym"] = merged["report_date"].astype(str).str[:6]
        elif "ann_date" in merged.columns:
            merged["_ym"] = merged["ann_date"].astype(str).str[:6]
        else:
            logger.warning("report_rc 缺少日期字段, 无法按月分片")
            return {}

        stats: dict[str, int] = {}
        for ym, group in merged.groupby("_ym"):
            ym_str = str(ym)
            out_path = out / f"analyst_{ym_str}.parquet"
            group_clean = group.drop(columns=["_ym"], errors="ignore")
            group_clean.to_parquet(out_path, index=False)
            stats[ym_str] = len(group_clean)

        logger.info("analyst 月度文件: %d 个月份", len(stats))
        return stats
