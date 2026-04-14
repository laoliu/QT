"""CSI300 ≥200B 分段 SR 监控

读取 CSI300 回测结果，计算滚动 SR 和分段绩效。
用于检测 alpha 衰减，触发预警。

用法:
    python scripts/csi300_segment_sr_monitor.py
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger(__name__)

# ---- 配置 ----
CONFIG_PATH = "configs/backtest/ml_csi300_ge200b_t1a.yml"
SEGMENT_BREAKPOINTS = {
    "pre_regulation": ("2018-01-16", "2022-07-31"),
    "post_regulation": ("2022-08-01", "2025-12-15"),
    "recent_2024_plus": ("2024-01-02", "2025-12-15"),
    "full_period": ("2018-01-16", "2025-12-15"),
}
ROLLING_WINDOWS = [63, 126, 252]  # 3M, 6M, 12M
DECAY_ALERT_THRESHOLD = 0.40  # 近期SR < 全量40% 触发告警


def run_single_backtest(config_path: str, start: str, end: str) -> dict[str, float]:
    """运行单段回测并返回绩效指标"""
    from aipha.backtest.config_runner import run_from_config

    run_result = run_from_config(
        config_path,
        param_overrides={
            "data.start_date": start,
            "data.end_date": end,
        },
        output_dir=f"output/_segment_monitor/csi300_{start}_{end}",
    )
    # 从report中提取指标
    rpt = run_result.report
    if rpt is None:
        return {"annualized_return": 0, "sharpe_ratio": 0, "max_drawdown": 0, "calmar_ratio": 0}
    return {
        "annualized_return": rpt.performance.annualized_return,
        "sharpe_ratio": rpt.performance.sharpe_ratio,
        "max_drawdown": rpt.drawdown.max_drawdown_pct,
        "calmar_ratio": rpt.performance.calmar_ratio,
    }


def compute_rolling_sr(daily_returns: pd.Series, window: int) -> pd.Series:
    """计算滚动夏普比率 (年化)"""
    rolling_mean = daily_returns.rolling(window).mean()
    rolling_std = daily_returns.rolling(window).std()
    # 避免除零
    sr = (rolling_mean / rolling_std.replace(0, np.nan)) * np.sqrt(252)
    return sr


def extract_daily_returns(result: dict) -> pd.Series:
    """从回测结果提取日收益率序列"""
    equity_curve = result.get("equity_curve", [])
    if not equity_curve:
        return pd.Series(dtype=float)
    navs = pd.Series(
        {rec["date"]: rec["nav"] for rec in equity_curve}
    ).sort_index()
    returns = navs.pct_change().dropna()
    return returns


def main() -> None:
    """主入口：运行分段回测 + 滚动SR计算"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=" * 70)
    print("CSI300 ≥200B 分段 SR 监控")
    print("=" * 70)

    # 1. 全量回测获取日收益率
    logger.info("运行全量回测获取日收益序列...")
    full_start, full_end = SEGMENT_BREAKPOINTS["full_period"]

    try:
        metrics = run_single_backtest(CONFIG_PATH, full_start, full_end)
    except Exception as e:
        logger.error("全量回测失败: %s", e)
        return

    print(f"\n全量期 ({full_start} ~ {full_end}):")
    print(f"  AR: {metrics.get('annualized_return', 0):.2%}")
    print(f"  SR: {metrics.get('sharpe_ratio', 0):.4f}")
    print(f"  MDD: {metrics.get('max_drawdown', 0):.2%}")
    print(f"  Calmar: {metrics.get('calmar_ratio', 0):.4f}")

    full_sr = metrics.get("sharpe_ratio", 0)

    # 2. 分段回测
    print("\n--- 分段绩效 ---")
    segment_results = {}
    for seg_name, (seg_start, seg_end) in SEGMENT_BREAKPOINTS.items():
        if seg_name == "full_period":
            continue
        try:
            seg_metrics = run_single_backtest(CONFIG_PATH, seg_start, seg_end)
            segment_results[seg_name] = seg_metrics
            seg_sr = seg_metrics.get("sharpe_ratio", 0)
            seg_ar = seg_metrics.get("annualized_return", 0)
            seg_mdd = seg_metrics.get("max_drawdown", 0)

            decay = 1 - seg_sr / full_sr if full_sr != 0 else float("nan")
            alert = " ⚠️ DECAY ALERT" if seg_sr < full_sr * DECAY_ALERT_THRESHOLD else ""

            print(f"\n{seg_name} ({seg_start} ~ {seg_end}):")
            print(f"  AR: {seg_ar:.2%}")
            print(f"  SR: {seg_sr:.4f}  (decay vs full: {decay:.1%}){alert}")
            print(f"  MDD: {seg_mdd:.2%}")
        except Exception as e:
            logger.error("分段 %s 回测失败: %s", seg_name, e)

    # 3. 衰减诊断
    print("\n--- Alpha 衰减诊断 ---")
    recent_sr = segment_results.get("recent_2024_plus", {}).get("sharpe_ratio", 0)
    if full_sr > 0:
        ratio = recent_sr / full_sr
        print(f"近期SR/全量SR = {recent_sr:.3f}/{full_sr:.3f} = {ratio:.1%}")
        if ratio < DECAY_ALERT_THRESHOLD:
            print("🔴 严重衰减: 近期SR不足全量期的40%")
        elif ratio < 0.60:
            print("🟡 中度衰减: 近期SR不足全量期的60%")
        else:
            print("🟢 衰减可控: 近期SR占全量期>60%")
    else:
        print("全量期SR≤0，无法计算衰减比")

    # 4. 输出summary
    summary = {
        "monitor_date": str(date.today()),
        "config": CONFIG_PATH,
        "full_period_sr": full_sr,
        "segments": segment_results,
        "decay_alert_threshold": DECAY_ALERT_THRESHOLD,
        "recent_vs_full_ratio": recent_sr / full_sr if full_sr else None,
    }
    out_path = Path("output/_segment_monitor/csi300_sr_summary.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"\n监控摘要已保存: {out_path}")


if __name__ == "__main__":
    main()
