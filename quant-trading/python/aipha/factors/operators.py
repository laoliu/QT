"""Alpha101 因子库运算符集

WorldQuant 101 公式化 Alpha 因子所需的全部时序 / 截面运算符。
基于 pandas groupby 实现面板数据运算，支持多股票并行计算。

参考: Kakushadze (2016) "101 Formulaic Alphas"
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ═══════════════════════════════════════════════════════════
# 时序运算符 (ts_*)  —— 均在 groupby(symbol) 下操作
# ═══════════════════════════════════════════════════════════

def ts_delay(s: pd.Series, d: int, group: pd.Series) -> pd.Series:
    """延迟 d 期"""
    return s.groupby(group).shift(d)


def ts_delta(s: pd.Series, d: int, group: pd.Series) -> pd.Series:
    """d 期变化量"""
    return s - ts_delay(s, d, group)


def ts_sum(s: pd.Series, d: int, group: pd.Series) -> pd.Series:
    """d 期滚动求和"""
    return s.groupby(group).transform(lambda x: x.rolling(d, min_periods=1).sum())


def ts_mean(s: pd.Series, d: int, group: pd.Series) -> pd.Series:
    """d 期滚动均值"""
    return s.groupby(group).transform(lambda x: x.rolling(d, min_periods=1).mean())


def ts_std(s: pd.Series, d: int, group: pd.Series) -> pd.Series:
    """d 期滚动标准差"""
    return s.groupby(group).transform(lambda x: x.rolling(d, min_periods=2).std())


def ts_min(s: pd.Series, d: int, group: pd.Series) -> pd.Series:
    return s.groupby(group).transform(lambda x: x.rolling(d, min_periods=1).min())


def ts_max(s: pd.Series, d: int, group: pd.Series) -> pd.Series:
    return s.groupby(group).transform(lambda x: x.rolling(d, min_periods=1).max())


def ts_argmax(s: pd.Series, d: int, group: pd.Series) -> pd.Series:
    """滚动窗口内最大值位置 (0-based)"""
    return s.groupby(group).transform(
        lambda x: x.rolling(d, min_periods=1).apply(lambda w: np.argmax(w), raw=True)
    )


def ts_argmin(s: pd.Series, d: int, group: pd.Series) -> pd.Series:
    return s.groupby(group).transform(
        lambda x: x.rolling(d, min_periods=1).apply(lambda w: np.argmin(w), raw=True)
    )


def ts_rank(s: pd.Series, d: int, group: pd.Series) -> pd.Series:
    """滚动排名百分位"""
    return s.groupby(group).transform(
        lambda x: x.rolling(d, min_periods=1).apply(
            lambda w: pd.Series(w).rank(pct=True).iloc[-1], raw=False
        )
    )


def ts_product(s: pd.Series, d: int, group: pd.Series) -> pd.Series:
    return s.groupby(group).transform(
        lambda x: x.rolling(d, min_periods=1).apply(np.prod, raw=True)
    )


def ts_corr(x: pd.Series, y: pd.Series, d: int, group: pd.Series) -> pd.Series:
    """滚动相关系数"""
    def _corr(df):
        return df.iloc[:, 0].rolling(d, min_periods=max(3, d // 2)).corr(df.iloc[:, 1])
    tmp = pd.DataFrame({"x": x, "y": y, "g": group})
    return tmp.groupby("g").apply(lambda g: _corr(g[["x", "y"]])).droplevel(0).sort_index()


def ts_cov(x: pd.Series, y: pd.Series, d: int, group: pd.Series) -> pd.Series:
    """滚动协方差"""
    def _cov(df):
        return df.iloc[:, 0].rolling(d, min_periods=max(3, d // 2)).cov(df.iloc[:, 1])
    tmp = pd.DataFrame({"x": x, "y": y, "g": group})
    return tmp.groupby("g").apply(lambda g: _cov(g[["x", "y"]])).droplevel(0).sort_index()


def ts_decay_linear(s: pd.Series, d: int, group: pd.Series) -> pd.Series:
    """线性衰减加权均值 (权重 d, d-1, ..., 1)"""
    weights = np.arange(d, 0, -1, dtype=float)
    weights /= weights.sum()
    return s.groupby(group).transform(
        lambda x: x.rolling(d, min_periods=d).apply(lambda w: np.dot(w, weights), raw=True)
    )


def ts_log(s: pd.Series) -> pd.Series:
    return np.log(s.clip(lower=1e-10))


def ts_abs(s: pd.Series) -> pd.Series:
    return s.abs()


def ts_sign(s: pd.Series) -> pd.Series:
    return np.sign(s)


def ts_greater(a: pd.Series, b) -> pd.Series:
    return np.maximum(a, b)


def ts_less(a: pd.Series, b) -> pd.Series:
    return np.minimum(a, b)


# ═══════════════════════════════════════════════════════════
# 截面运算符 (cs_*)  —— 在每个 datetime 截面上操作
# ═══════════════════════════════════════════════════════════

def cs_rank(s: pd.Series, dt: pd.Series) -> pd.Series:
    """截面排名百分位"""
    tmp = pd.DataFrame({"val": s, "dt": dt})
    return tmp.groupby("dt")["val"].rank(pct=True)


def cs_scale(s: pd.Series, dt: pd.Series, a: float = 1.0) -> pd.Series:
    """截面缩放: x / sum(|x|) * a"""
    tmp = pd.DataFrame({"val": s, "dt": dt})
    abs_sum = tmp.groupby("dt")["val"].transform(lambda x: x.abs().sum())
    return s / abs_sum.replace(0, np.nan) * a


# ═══════════════════════════════════════════════════════════
# 辅助运算符
# ═══════════════════════════════════════════════════════════

def pow1(s: pd.Series, n: float) -> pd.Series:
    return s.pow(n)


def pow2(s: pd.Series) -> pd.Series:
    return s * s


def safe_div(a: pd.Series, b: pd.Series) -> pd.Series:
    return a / b.replace(0, np.nan)


def quesval(threshold, condition: pd.Series, true_val, false_val) -> pd.Series:
    """if condition < threshold: true_val else false_val"""
    if isinstance(true_val, (int, float)):
        true_series = pd.Series(true_val, index=condition.index)
    else:
        true_series = true_val
    if isinstance(false_val, (int, float)):
        false_series = pd.Series(false_val, index=condition.index)
    else:
        false_series = false_val
    return pd.Series(
        np.where(condition < threshold, true_series, false_series),
        index=condition.index,
    )


def quesval2(a: pd.Series, b: pd.Series, true_val, false_val) -> pd.Series:
    """if a < b: true_val else false_val"""
    if isinstance(true_val, (int, float)):
        true_val = pd.Series(true_val, index=a.index)
    if isinstance(false_val, (int, float)):
        false_val = pd.Series(false_val, index=a.index)
    return pd.Series(np.where(a < b, true_val, false_val), index=a.index)


# ═══════════════════════════════════════════════════════════
# Alpha158 补充运算符 — 回归 / 分位 / 频率统计
# ═══════════════════════════════════════════════════════════

def ts_slope(s: pd.Series, d: int, group: pd.Series) -> pd.Series:
    """滚动线性回归斜率"""
    def _slope(x):
        n = len(x)
        if n < 2:
            return np.nan
        t = np.arange(n, dtype=float)
        return np.polyfit(t, x, 1)[0]
    return s.groupby(group).transform(
        lambda x: x.rolling(d, min_periods=2).apply(_slope, raw=True)
    )


def ts_rsquare(s: pd.Series, d: int, group: pd.Series) -> pd.Series:
    """滚动 R² (线性拟合优度)"""
    def _rsq(x):
        n = len(x)
        if n < 2:
            return np.nan
        t = np.arange(n, dtype=float)
        c = np.corrcoef(t, x)[0, 1]
        return c * c if np.isfinite(c) else np.nan
    return s.groupby(group).transform(
        lambda x: x.rolling(d, min_periods=2).apply(_rsq, raw=True)
    )


def ts_resi(s: pd.Series, d: int, group: pd.Series) -> pd.Series:
    """滚动回归残差 (最后一个点的残差)"""
    def _resi(x):
        n = len(x)
        if n < 2:
            return np.nan
        t = np.arange(n, dtype=float)
        c = np.polyfit(t, x, 1)
        return x[-1] - (c[0] * (n - 1) + c[1])
    return s.groupby(group).transform(
        lambda x: x.rolling(d, min_periods=2).apply(_resi, raw=True)
    )


def ts_quantile(s: pd.Series, d: int, q: float, group: pd.Series) -> pd.Series:
    """滚动分位数"""
    return s.groupby(group).transform(
        lambda x: x.rolling(d, min_periods=1).quantile(q)
    )


def ts_count_positive(s: pd.Series, d: int, group: pd.Series) -> pd.Series:
    """滚动窗口内正值占比"""
    pos = (s > 0).astype(float)
    return pos.groupby(group).transform(
        lambda x: x.rolling(d, min_periods=1).mean()
    )


def ts_count_negative(s: pd.Series, d: int, group: pd.Series) -> pd.Series:
    """滚动窗口内负值占比"""
    neg = (s < 0).astype(float)
    return neg.groupby(group).transform(
        lambda x: x.rolling(d, min_periods=1).mean()
    )


def ts_sum_positive(s: pd.Series, d: int, group: pd.Series) -> pd.Series:
    """滚动窗口内正值之和"""
    pos = s.clip(lower=0)
    return pos.groupby(group).transform(
        lambda x: x.rolling(d, min_periods=1).sum()
    )


def ts_sum_negative(s: pd.Series, d: int, group: pd.Series) -> pd.Series:
    """滚动窗口内负值之和"""
    neg = s.clip(upper=0)
    return neg.groupby(group).transform(
        lambda x: x.rolling(d, min_periods=1).sum()
    )


def cs_zscore(s: pd.Series, dt: pd.Series) -> pd.Series:
    """截面 Z-Score 标准化"""
    tmp = pd.DataFrame({"val": s, "dt": dt})
    mean = tmp.groupby("dt")["val"].transform("mean")
    std = tmp.groupby("dt")["val"].transform("std")
    return (s - mean) / std.replace(0, np.nan)


def cs_demean(s: pd.Series, dt: pd.Series) -> pd.Series:
    """截面去均值"""
    tmp = pd.DataFrame({"val": s, "dt": dt})
    mean = tmp.groupby("dt")["val"].transform("mean")
    return s - mean
