#!/usr/bin/env python3
"""S4 跟踪误差检查 — 模拟盘 vs 回测一致性验证.

计算模拟盘实际收益与回测预期收益的跟踪误差(TE)，
作为 S4→S5 门禁检查项之一。

门禁标准:
  - 日度 TE (年化) < 5%
  - 日收益偏差 > 3% 天数 < 5%
  - 累计收益偏差 < 10%

用法:
    python scripts/s4_tracking_error.py \\
        --paper-state data/paper_state/tiered_combo_v3 \\
        --backtest-summary output/ml_csi2000_ensemble_v3b_mdd_controlled/summary.json
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

_PROJ = Path(__file__).resolve().parents[1]
if str(_PROJ / "src") not in sys.path:
    sys.path.insert(0, str(_PROJ / "src"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("s4_tracking_error")

# S4→S5 门禁阈值
TE_ANNUAL_LIMIT = 0.05       # 年化 TE < 5%
DAILY_DEVIATION_LIMIT = 0.03  # 单日偏差 > 3% 的天数占比 < 5%
DAILY_DEVIATION_RATIO = 0.05  # ≤5% 的交易日超标
CUM_RETURN_DEVIATION = 0.10   # 累计收益偏差 < 10%


def load_paper_nav(state_dir: Path) -> pd.Series:
    """加载模拟盘 NAV 序列."""
    state_file = state_dir / "paper_state.json"
    if not state_file.exists():
        raise FileNotFoundError(f"未找到模拟盘状态: {state_file}")
    with open(state_file, encoding="utf-8") as f:
        state = json.load(f)
    # 尝试从 nav_history 字段获取
    nav_hist = state.get("nav_history", state.get("equity_curve", []))
    if isinstance(nav_hist, dict):
        return pd.Series(nav_hist, dtype=float).sort_index()
    if isinstance(nav_hist, list) and nav_hist:
        if isinstance(nav_hist[0], dict):
            df = pd.DataFrame(nav_hist)
            date_col = "date" if "date" in df.columns else df.columns[0]
            nav_col = "nav" if "nav" in df.columns else df.columns[-1]
            return df.set_index(date_col)[nav_col].astype(float).sort_index()
    logger.warning("未找到有效 NAV 数据, 尝试日志文件")
    return pd.Series(dtype=float)


def load_backtest_nav(summary_path: Path) -> pd.Series:
    """加载回测 NAV 序列."""
    parent = summary_path.parent
    snap_file = parent / "daily_snapshots.csv"
    if snap_file.exists():
        df = pd.read_csv(snap_file)
        if "date" in df.columns and "nav" in df.columns:
            return df.set_index("date")["nav"].astype(float).sort_index()
        if "date" in df.columns and "equity" in df.columns:
            return df.set_index("date")["equity"].astype(float).sort_index()
    with open(summary_path, encoding="utf-8") as f:
        s = json.load(f)
    eq = s.get("equity_curve", {})
    if eq:
        return pd.Series(eq, dtype=float).sort_index()
    raise FileNotFoundError(f"未找到回测 NAV 数据: {parent}")


def compute_tracking_error(paper_nav: pd.Series, bt_nav: pd.Series) -> dict:
    """计算跟踪误差指标.

    Returns:
        dict with: te_annual, daily_deviation_pct, cum_return_deviation, n_days
    """
    # 对齐共同日期
    common = paper_nav.index.intersection(bt_nav.index)
    if len(common) < 5:
        return {
            "te_annual": float("inf"),
            "daily_deviation_pct": 1.0,
            "cum_return_deviation": float("inf"),
            "n_days": len(common),
            "msg": f"共同交易日仅 {len(common)} 天，不足以计算",
        }
    p = paper_nav[common].astype(float)
    b = bt_nav[common].astype(float)

    # 日收益率
    p_ret = p.pct_change().dropna()
    b_ret = b.pct_change().dropna()
    common_ret = p_ret.index.intersection(b_ret.index)
    p_ret = p_ret[common_ret]
    b_ret = b_ret[common_ret]

    diff = p_ret - b_ret
    te_daily = diff.std()
    te_annual = te_daily * math.sqrt(252)

    # 单日偏差 > 3% 的天数占比
    n_large = (diff.abs() > DAILY_DEVIATION_LIMIT).sum()
    deviation_ratio = n_large / len(diff) if len(diff) > 0 else 0.0

    # 累计收益偏差
    cum_p = (1 + p_ret).prod() - 1
    cum_b = (1 + b_ret).prod() - 1
    cum_dev = abs(cum_p - cum_b)

    return {
        "te_annual": float(te_annual),
        "daily_deviation_pct": float(deviation_ratio),
        "cum_return_deviation": float(cum_dev),
        "cum_paper": float(cum_p),
        "cum_backtest": float(cum_b),
        "n_days": len(common_ret),
    }


def check_s4_gate(metrics: dict) -> dict:
    """检查 S4→S5 门禁."""
    checks = []
    checks.append({
        "item": "年化跟踪误差",
        "value": metrics["te_annual"],
        "limit": TE_ANNUAL_LIMIT,
        "pass": metrics["te_annual"] <= TE_ANNUAL_LIMIT,
    })
    checks.append({
        "item": "大偏差天数占比",
        "value": metrics["daily_deviation_pct"],
        "limit": DAILY_DEVIATION_RATIO,
        "pass": metrics["daily_deviation_pct"] <= DAILY_DEVIATION_RATIO,
    })
    checks.append({
        "item": "累计收益偏差",
        "value": metrics["cum_return_deviation"],
        "limit": CUM_RETURN_DEVIATION,
        "pass": metrics["cum_return_deviation"] <= CUM_RETURN_DEVIATION,
    })
    all_pass = all(c["pass"] for c in checks)
    return {"gate_pass": all_pass, "checks": checks, "metrics": metrics}


def main() -> None:
    parser = argparse.ArgumentParser(description="S4 跟踪误差检查")
    parser.add_argument("--paper-state", required=True, help="模拟盘状态目录")
    parser.add_argument("--backtest-summary", required=True, help="回测 summary.json")
    parser.add_argument("--output", default="output/s4_tracking_error.json")
    args = parser.parse_args()

    paper_nav = load_paper_nav(Path(args.paper_state))
    bt_nav = load_backtest_nav(Path(args.backtest_summary))

    if paper_nav.empty:
        logger.error("模拟盘 NAV 为空，无法计算跟踪误差")
        sys.exit(1)

    metrics = compute_tracking_error(paper_nav, bt_nav)
    gate = check_s4_gate(metrics)

    print(f"\n=== S4→S5 跟踪误差门禁 ===")
    print(f"共同交易日: {metrics['n_days']} 天")
    for c in gate["checks"]:
        icon = "✅" if c["pass"] else "❌"
        print(f"  {icon} {c['item']}: {c['value']:.4f} "
              f"(限制: {c['limit']:.4f})")
    print(f"\n{'✅ 门禁通过' if gate['gate_pass'] else '❌ 门禁未通过'}")

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(gate, f, ensure_ascii=False, indent=2)
    logger.info("报告已保存至 %s", out)


if __name__ == "__main__":
    main()
