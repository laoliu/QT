"""CB 陆股通因子桥接 (Phase C / C-1c).

将正股北向持仓数据映射到 CB，产出：
    hk_ratio         — 正股北向持股占流通比 (%)
    hk_delta_5d      — 5日持股比变动
    hk_delta_20d     — 20日持股比变动
    hk_momentum      — 方向一致性 (20日窗口内增仓占比)

数据路径：
    TuShareMoneyflowProvider.get_hk_hold_daily()
        → hk_hold (ts_code=正股, ratio)
        → 通过 cb_basic 的 stk_code 列映射到 CB ts_code

引用：
    - foreign_flow.py — ForeignFlowFactors (A 股直接使用)
    - cb_stock_bridge.py — CB→正股 映射
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def build_cb_northbound_factors(
    cb_basic: pd.DataFrame,
    hk_hold_panel: pd.DataFrame,
    lookback_short: int = 5,
    lookback_long: int = 20,
) -> pd.DataFrame:
    """将正股北向持仓映射到 CB 并计算变动因子。

    Args:
        cb_basic: CB 基础信息，须包含 ts_code(CB), stk_code(正股)。
        hk_hold_panel: 北向持仓面板，须包含 ts_code(正股), trade_date, ratio。
            可为多日拼接。
        lookback_short: 短期变动窗口 (交易日)。
        lookback_long: 长期变动窗口 / 方向一致性窗口。

    Returns:
        DataFrame[trade_date, cb_code, hk_ratio, hk_delta_5d,
                  hk_delta_20d, hk_momentum]
    """
    if cb_basic.empty or hk_hold_panel.empty:
        return _empty_result()

    # 1. CB → 正股映射
    mapping = _build_cb_stock_mapping(cb_basic)
    if not mapping:
        logger.warning("cb_basic 中无 stk_code 映射")
        return _empty_result()

    # 2. 过滤正股在北向持仓中存在的
    stk_codes = set(mapping.values())
    hk = hk_hold_panel[hk_hold_panel["ts_code"].isin(stk_codes)].copy()
    if hk.empty:
        return _empty_result()

    hk = hk.sort_values(["ts_code", "trade_date"])

    # 3. 计算因子 (按正股)
    grouped = hk.groupby("ts_code")["ratio"]
    hk["hk_ratio"] = hk["ratio"]
    hk["hk_delta_5d"] = grouped.diff(lookback_short)
    hk["hk_delta_20d"] = grouped.diff(lookback_long)

    hk["_daily_chg"] = grouped.diff(1)
    hk["hk_momentum"] = (
        hk.groupby("ts_code")["_daily_chg"]
        .transform(
            lambda s: s.rolling(lookback_long, min_periods=5)
            .apply(lambda x: (x > 0).sum() / len(x), raw=True)
        )
    )

    # 4. 反向映射：正股 → CB
    stk_to_cbs = _invert_mapping(mapping)
    records: list[dict] = []
    for _, row in hk.iterrows():
        stk = row["ts_code"]
        for cb_code in stk_to_cbs.get(stk, []):
            records.append({
                "trade_date": row["trade_date"],
                "cb_code": cb_code,
                "hk_ratio": row["hk_ratio"],
                "hk_delta_5d": row.get("hk_delta_5d"),
                "hk_delta_20d": row.get("hk_delta_20d"),
                "hk_momentum": row.get("hk_momentum"),
            })

    if not records:
        return _empty_result()
    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------
def _build_cb_stock_mapping(cb_basic: pd.DataFrame) -> dict[str, str]:
    """返回 {cb_ts_code: stk_code}。"""
    if "stk_code" not in cb_basic.columns or "ts_code" not in cb_basic.columns:
        return {}
    sub = cb_basic[["ts_code", "stk_code"]].dropna()
    return dict(zip(sub["ts_code"], sub["stk_code"]))


def _invert_mapping(mapping: dict[str, str]) -> dict[str, list[str]]:
    """反转 {cb→stk} 为 {stk→[cb, ...]}。"""
    inv: dict[str, list[str]] = {}
    for cb, stk in mapping.items():
        inv.setdefault(stk, []).append(cb)
    return inv


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame(
        columns=["trade_date", "cb_code", "hk_ratio",
                 "hk_delta_5d", "hk_delta_20d", "hk_momentum"]
    )
