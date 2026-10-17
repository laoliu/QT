"""CB 流动性月度跟踪

分析CB策略实际持仓的流动性分布，
监控可投标的数量与成交额变化趋势。

用法:
    python scripts/cb_liquidity_monitor.py
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aipha.utils.paths import project_path

logger = logging.getLogger(__name__)

# CB 流动性分析参数
MAX_PARTICIPATION_RATE = 0.05  # 最大参与率5% (CB策略)
OPENING_VOL_FRACTION = 0.15   # 开盘成交占比
LOT_SIZE = 10                 # CB最小单位


def load_cb_daily_data() -> pd.DataFrame:
    """加载CB日行情数据"""
    from aipha.data.tushare_cb_provider import TuShareCBProvider
    provider = TuShareCBProvider(cache_dir="data/cache/cb_tushare")
    daily = provider.load_cb_daily("20220801", "20260304")
    return daily


def compute_monthly_liquidity(daily: pd.DataFrame) -> pd.DataFrame:
    """计算月度流动性统计"""
    df = daily.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    df["yearmonth"] = df["trade_date"].dt.to_period("M")

    # 可投金额 = amount * participation_rate * opening_vol_fraction
    df["investable_amount"] = df["amount"] * MAX_PARTICIPATION_RATE * OPENING_VOL_FRACTION

    monthly = df.groupby("yearmonth").agg(
        n_bonds=("ts_code", "nunique"),
        median_amount=("amount", "median"),
        p10_amount=("amount", lambda x: np.percentile(x.dropna(), 10)),
        p25_amount=("amount", lambda x: np.percentile(x.dropna(), 25)),
        median_investable=("investable_amount", "median"),
        p10_investable=("investable_amount", lambda x: np.percentile(x.dropna(), 10)),
        total_volume=("vol", "sum"),
    ).reset_index()

    return monthly


def assess_capacity(monthly: pd.DataFrame) -> dict:
    """评估月度容量趋势"""
    recent = monthly.tail(6)
    trend = {
        "latest_month": str(monthly.iloc[-1]["yearmonth"]),
        "latest_n_bonds": int(monthly.iloc[-1]["n_bonds"]),
        "latest_median_amount_wan": float(monthly.iloc[-1]["median_amount"] / 10000),
        "latest_p10_investable_wan": float(monthly.iloc[-1]["p10_investable"] / 10000),
        "latest_median_investable_wan": float(monthly.iloc[-1]["median_investable"] / 10000),
        "recent_6m_avg_n_bonds": float(recent["n_bonds"].mean()),
        "recent_6m_avg_median_amount_wan": float(recent["median_amount"].mean() / 10000),
    }
    # 容量建议
    p10_investable = trend["latest_p10_investable_wan"]
    if p10_investable < 1.0:
        trend["capacity_rating"] = "CRITICAL"
        trend["max_recommended_wan"] = 20
    elif p10_investable < 5.0:
        trend["capacity_rating"] = "LIMITED"
        trend["max_recommended_wan"] = 50
    else:
        trend["capacity_rating"] = "ADEQUATE"
        trend["max_recommended_wan"] = 100
    return trend


def main() -> None:
    """主入口"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=" * 70)
    print("CB 流动性月度跟踪 (2022.08 新规后)")
    print("=" * 70)

    logger.info("加载CB日行情数据...")
    daily = load_cb_daily_data()
    logger.info("共 %d 条记录", len(daily))

    # 过滤有效数据
    daily = daily[daily["amount"] > 0].copy()
    logger.info("有效(amount>0)记录: %d", len(daily))

    monthly = compute_monthly_liquidity(daily)

    print("\n月度流动性统计 (近12月):")
    print(f"{'月份':<12} {'标的数':>8} {'中位成交(万)':>14} {'P10成交(万)':>14} "
          f"{'中位可投(万)':>14} {'P10可投(万)':>14}")
    print("-" * 78)

    for _, row in monthly.tail(12).iterrows():
        print(f"{str(row['yearmonth']):<12} "
              f"{row['n_bonds']:>8.0f} "
              f"{row['median_amount']/10000:>14.1f} "
              f"{row['p10_amount']/10000:>14.1f} "
              f"{row['median_investable']/10000:>14.1f} "
              f"{row['p10_investable']/10000:>14.1f}")

    # 容量评估
    assessment = assess_capacity(monthly)
    print(f"\n--- 容量评估 ---")
    print(f"最新月份: {assessment['latest_month']}")
    print(f"活跃标的: {assessment['latest_n_bonds']} 只")
    print(f"中位日成交额: {assessment['latest_median_amount_wan']:.1f} 万")
    print(f"P10日可投金额: {assessment['latest_p10_investable_wan']:.1f} 万")
    print(f"容量评级: {assessment['capacity_rating']}")
    print(f"建议最大配置: {assessment['max_recommended_wan']} 万")

    # 保存
    out_path = project_path("output/_liquidity_monitor/cb_monthly_liquidity.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(assessment, f, indent=2, default=str)
    print(f"\n监控结果已保存: {out_path}")


if __name__ == "__main__":
    main()
