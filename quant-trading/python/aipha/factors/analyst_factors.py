"""分析师因子 — 盈利预测修正 / 业绩超预期 / 股东人数变化"""
from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Sequence

import pandas as pd

if TYPE_CHECKING:
    pass  # EquityCandidateInfo 等类型仅用于 type hint

logger = logging.getLogger(__name__)

_analyst_cache: dict[str, pd.DataFrame] = {}
_holder_cache: dict[str, pd.DataFrame] = {}


def _get_months_before(trade_date: str, n_months: int) -> list[str]:
    """返回 trade_date 之前 n 个自然月 (YYYYMM)"""
    if not trade_date or len(trade_date) < 6:
        return []
    year = int(trade_date[:4])
    month = int(trade_date[4:6])
    result = []
    for _ in range(n_months):
        month -= 1
        if month < 1:
            month = 12
            year -= 1
        result.append(f"{year}{month:02d}")
    return list(reversed(result))


def load_analyst_months(cache_dir: str | Path, months: list[str]) -> pd.DataFrame:
    """加载分析师评级 / 盈利预测月度数据"""
    frames = []
    for m in months:
        key = f"analyst_{m}"
        if key in _analyst_cache:
            frames.append(_analyst_cache[key])
            continue
        path = Path(cache_dir) / f"analyst_{m}.csv"
        if path.exists():
            df = pd.read_csv(path, dtype=str)
            _analyst_cache[key] = df
            frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def load_holder_data(cache_dir: str | Path, ts_code: str) -> pd.DataFrame:
    """加载某只股票的股东人数数据"""
    if ts_code in _holder_cache:
        return _holder_cache[ts_code]
    path = Path(cache_dir) / f"{ts_code}.csv"
    if path.exists():
        df = pd.read_csv(path)
        _holder_cache[ts_code] = df
        return df
    return pd.DataFrame()


def load_forecast_months(cache_dir: str | Path, months: list[str]) -> pd.DataFrame:
    """加载业绩预告数据"""
    frames = []
    for m in months:
        path = Path(cache_dir) / f"forecast_{m}.csv"
        if path.exists():
            frames.append(pd.read_csv(path, dtype=str))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def compute_analyst_revision_factor(
    candidates: Sequence[Any],
    cache_dir: str = "",
    trade_date: str = "",
    lookback_months: int = 3,
    min_analysts: int = 2,
) -> dict[str, float]:
    """分析师盈利预测修正因子

    正值 = 近期上调多于下调, 看多
    """
    if not cache_dir:
        from aipha.config import get_cache_dir
        cache_dir = str(get_cache_dir() / "alternative")
    months = _get_months_before(trade_date, lookback_months)
    if not months:
        return {}
    data = load_analyst_months(cache_dir, months)
    if data.empty:
        return {}

    codes = {getattr(c, "code", str(c)) for c in candidates}
    result: dict[str, float] = {}
    for code in codes:
        sub = data[data.iloc[:, 0] == code] if not data.empty else pd.DataFrame()
        if len(sub) < min_analysts:
            continue
        # 简化: 评级变化 = 上调次数 - 下调次数
        if "rating_change" in sub.columns:
            changes = pd.to_numeric(sub["rating_change"], errors="coerce").dropna()
            if len(changes) >= min_analysts:
                result[code] = float(changes.mean())
    return result


_TYPE_SCORES: dict[str, float] = {
    "预增": 1.0, "略增": 0.5, "续盈": 0.3, "扭亏": 0.8,
    "预减": -1.0, "略减": -0.5, "续亏": -0.8, "首亏": -1.0,
    "不确定": 0.0,
}


def compute_forecast_surprise_factor(
    candidates: Sequence[Any],
    cache_dir: str = "",
    trade_date: str = "",
    lookback_months: int = 6,
) -> dict[str, float]:
    """业绩预告超预期因子"""
    if not cache_dir:
        from aipha.config import get_cache_dir
        cache_dir = str(get_cache_dir() / "advanced")
    months = _get_months_before(trade_date, lookback_months)
    data = load_forecast_months(cache_dir, months)
    if data.empty:
        return {}

    codes = {getattr(c, "code", str(c)) for c in candidates}
    result: dict[str, float] = {}
    for code in codes:
        sub = data[data.iloc[:, 0] == code] if not data.empty else pd.DataFrame()
        if sub.empty:
            continue
        if "type" in sub.columns:
            scores = [_TYPE_SCORES.get(t, 0.0) for t in sub["type"]]
            if scores:
                result[code] = sum(scores) / len(scores)
    return result


def compute_holder_change_factor(
    candidates: Sequence[Any],
    cache_dir: str = "",
    trade_date: str = "",
) -> dict[str, float]:
    """股东人数变化因子

    负值 = 股东人数减少 = 筹码集中 = 看多
    """
    if not cache_dir:
        from aipha.config import get_cache_dir
        cache_dir = str(get_cache_dir() / "reference" / "stk_holdernumber")
    result: dict[str, float] = {}
    for c in candidates:
        code = getattr(c, "code", str(c))
        df = load_holder_data(cache_dir, code)
        if df.empty or len(df) < 2:
            continue
        if "holder_num" in df.columns:
            series = pd.to_numeric(df["holder_num"], errors="coerce").dropna()
            if len(series) >= 2:
                pct_change = (series.iloc[-1] - series.iloc[-2]) / series.iloc[-2]
                result[code] = float(-pct_change)  # 反转: 减少为正
    return result
