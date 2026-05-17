"""
因子计算运算符引擎

提供时间序列 (ts_*) 和截面 (cs_*) 运算符，用于构建 Alpha158/Alpha101 等量化因子。
所有运算符基于 Pandas DataFrame 面板数据 (datetime, symbol, value) 操作。

参考：
- Qlib Alpha158 因子运算范式
- WorldQuant Alpha101 公式化 Alpha
- vnpy AlphaLab 表达式引擎
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats


# =============================================================================
# 时间序列运算符 (Time Series Operators)
# 所有 ts_* 函数接受 Series 或 DataFrame, 在 symbol 分组内沿时间轴滚动计算
# =============================================================================


def ts_delay(s: pd.Series, window: int, group: pd.Series | None = None) -> pd.Series:
    """延迟/滞后算子：返回 window 期之前的值。

    Args:
        s: 输入序列（已按 symbol+datetime 排列的面板数据列）
        window: 滞后期数（正=过去, 负=未来）
        group: 分组键（symbol），None 时不分组
    """
    if group is not None:
        return s.groupby(group).shift(window)
    return s.shift(window)


def ts_delta(s: pd.Series, window: int, group: pd.Series | None = None) -> pd.Series:
    """差分算子：当前值 - window 期前的值。"""
    return s - ts_delay(s, window, group)


def ts_sum(s: pd.Series, window: int, group: pd.Series | None = None) -> pd.Series:
    """滚动求和。"""
    if group is not None:
        return s.groupby(group).transform(lambda x: x.rolling(window, min_periods=1).sum())
    return s.rolling(window, min_periods=1).sum()


def ts_mean(s: pd.Series, window: int, group: pd.Series | None = None) -> pd.Series:
    """滚动均值。"""
    if group is not None:
        return s.groupby(group).transform(lambda x: x.rolling(window, min_periods=1).mean())
    return s.rolling(window, min_periods=1).mean()


def ts_std(s: pd.Series, window: int, group: pd.Series | None = None, ddof: int = 0) -> pd.Series:
    """滚动标准差。"""
    if group is not None:
        return s.groupby(group).transform(lambda x: x.rolling(window, min_periods=1).std(ddof=ddof))
    return s.rolling(window, min_periods=1).std(ddof=ddof)


def ts_var(s: pd.Series, window: int, group: pd.Series | None = None, ddof: int = 0) -> pd.Series:
    """滚动方差。"""
    if group is not None:
        return s.groupby(group).transform(lambda x: x.rolling(window, min_periods=1).var(ddof=ddof))
    return s.rolling(window, min_periods=1).var(ddof=ddof)


def ts_min(s: pd.Series, window: int, group: pd.Series | None = None) -> pd.Series:
    """滚动最小值。"""
    if group is not None:
        return s.groupby(group).transform(lambda x: x.rolling(window, min_periods=1).min())
    return s.rolling(window, min_periods=1).min()


def ts_max(s: pd.Series, window: int, group: pd.Series | None = None) -> pd.Series:
    """滚动最大值。"""
    if group is not None:
        return s.groupby(group).transform(lambda x: x.rolling(window, min_periods=1).max())
    return s.rolling(window, min_periods=1).max()


def ts_argmax(s: pd.Series, window: int, group: pd.Series | None = None) -> pd.Series:
    """滚动窗口内最大值的位置（1-based, 1=最近）。"""
    def _argmax(x: pd.Series) -> pd.Series:
        return x.rolling(window, min_periods=window).apply(
            lambda w: window - np.argmax(w), raw=True
        )
    if group is not None:
        return s.groupby(group).transform(_argmax)
    return _argmax(s)


def ts_argmin(s: pd.Series, window: int, group: pd.Series | None = None) -> pd.Series:
    """滚动窗口内最小值的位置（1-based, 1=最近）。"""
    def _argmin(x: pd.Series) -> pd.Series:
        return x.rolling(window, min_periods=window).apply(
            lambda w: window - np.argmin(w), raw=True
        )
    if group is not None:
        return s.groupby(group).transform(_argmin)
    return _argmin(s)


def ts_rank(s: pd.Series, window: int, group: pd.Series | None = None) -> pd.Series:
    """滚动窗口内当前值的百分位排名 [0, 1]。"""
    def _rank(x: pd.Series) -> pd.Series:
        return x.rolling(window, min_periods=window).apply(
            lambda w: stats.percentileofscore(w, w[-1]) / 100.0, raw=True
        )
    if group is not None:
        return s.groupby(group).transform(_rank)
    return _rank(s)


def ts_quantile(s: pd.Series, window: int, quantile: float,
                group: pd.Series | None = None) -> pd.Series:
    """滚动窗口分位数。"""
    def _quantile(x: pd.Series) -> pd.Series:
        return x.rolling(window, min_periods=window).quantile(quantile)
    if group is not None:
        return s.groupby(group).transform(_quantile)
    return _quantile(s)


def ts_corr(s1: pd.Series, s2: pd.Series, window: int,
            group: pd.Series | None = None) -> pd.Series:
    """滚动相关系数。"""
    if group is not None:
        # 需要对齐两个序列的 group
        df = pd.DataFrame({"a": s1, "b": s2, "g": group})
        result = df.groupby("g").apply(
            lambda g: g["a"].rolling(window, min_periods=1).corr(g["b"]),
            include_groups=False
        )
        # flatten MultiIndex
        if isinstance(result.index, pd.MultiIndex):
            result = result.droplevel(0)
        if isinstance(result, pd.DataFrame):
            result = result.iloc[:, 0]
        return result.reindex(s1.index).replace([np.inf, -np.inf], np.nan)
    return s1.rolling(window, min_periods=1).corr(s2).replace([np.inf, -np.inf], np.nan)


def ts_cov(s1: pd.Series, s2: pd.Series, window: int,
           group: pd.Series | None = None) -> pd.Series:
    """滚动协方差。"""
    if group is not None:
        df = pd.DataFrame({"a": s1, "b": s2, "g": group})
        result = df.groupby("g").apply(
            lambda g: g["a"].rolling(window, min_periods=1).cov(g["b"]),
            include_groups=False
        )
        if isinstance(result.index, pd.MultiIndex):
            result = result.droplevel(0)
        if isinstance(result, pd.DataFrame):
            result = result.iloc[:, 0]
        return result.reindex(s1.index)
    return s1.rolling(window, min_periods=1).cov(s2)


def ts_slope(s: pd.Series, window: int, group: pd.Series | None = None) -> pd.Series:
    """滚动线性回归斜率（x=时间索引 0..n-1, y=s）。

    使用 cumsum 恒等式向量化：sum_xy = n*cumsum(x) - rolling_sum(cumsum(x), n)，
    避免 Python 级别的 rolling.apply 回调，速度提升 50-100x。
    """
    n = window
    sum_x = n * (n - 1) / 2
    sum_x2 = (n - 1) * n * (2 * n - 1) / 6
    denom = n * sum_x2 - sum_x * sum_x

    def _slope(x: pd.Series) -> pd.Series:
        sum_y = x.rolling(window, min_periods=window).sum()
        # 向量化加权求和: sum_xy = n * cumsum(x) - rolling_sum(cumsum(x), n)
        csx = x.cumsum()
        rs_csx = csx.rolling(window, min_periods=window).sum()
        sum_xy = n * csx - rs_csx
        return (n * sum_xy - sum_x * sum_y) / denom

    if group is not None:
        return s.groupby(group).transform(_slope)
    return _slope(s)


def ts_rsquare(s: pd.Series, window: int, group: pd.Series | None = None) -> pd.Series:
    """滚动线性回归 R² 值。

    向量化实现：利用 cumsum 恒等式计算 cov(x,y)，利用 rolling.var 计算 var(y)。
    """
    n = window
    mean_x = (n - 1) / 2
    sum_x2 = (n - 1) * n * (2 * n - 1) / 6
    var_x = sum_x2 / n - mean_x ** 2

    def _rsq(x: pd.Series) -> pd.Series:
        mean_y = x.rolling(window, min_periods=window).mean()
        var_y = x.rolling(window, min_periods=window).var(ddof=0)
        # 向量化 cov(x, y) = E[xy] - E[x]*E[y]
        csx = x.cumsum()
        rs_csx = csx.rolling(window, min_periods=window).sum()
        sum_xy = n * csx - rs_csx
        mean_xy = sum_xy / n
        cov_xy = mean_xy - mean_x * mean_y
        result = cov_xy ** 2 / (var_x * var_y)
        return result.where(var_y > 1e-15, np.nan)

    if group is not None:
        return s.groupby(group).transform(_rsq)
    return _rsq(s)


def ts_resi(s: pd.Series, window: int, group: pd.Series | None = None) -> pd.Series:
    """滚动线性回归残差（最后一期的残差）。

    向量化实现：residual = x - (slope*(n-1) + intercept)。
    """
    n = window
    sum_x = n * (n - 1) / 2
    sum_x2 = (n - 1) * n * (2 * n - 1) / 6
    mean_x = (n - 1) / 2
    denom = n * sum_x2 - sum_x * sum_x

    def _resi(x: pd.Series) -> pd.Series:
        sum_y = x.rolling(window, min_periods=window).sum()
        mean_y = sum_y / n
        # 向量化 sum_xy
        csx = x.cumsum()
        rs_csx = csx.rolling(window, min_periods=window).sum()
        sum_xy = n * csx - rs_csx
        slope = (n * sum_xy - sum_x * sum_y) / denom
        intercept = mean_y - slope * mean_x
        predicted = slope * (n - 1) + intercept
        return x - predicted

    if group is not None:
        return s.groupby(group).transform(_resi)
    return _resi(s)


def ts_product(s: pd.Series, window: int, group: pd.Series | None = None) -> pd.Series:
    """滚动乘积。"""
    def _prod(x: pd.Series) -> pd.Series:
        return x.rolling(window, min_periods=window).apply(np.prod, raw=True)
    if group is not None:
        return s.groupby(group).transform(_prod)
    return _prod(s)


def ts_decay_linear(s: pd.Series, window: int, group: pd.Series | None = None) -> pd.Series:
    """线性衰减加权平均（权重: window, window-1, ..., 1，最近的权重最大）。

    向量化实现: decay = ((n+1)*rolling_sum - sum_xy) / total,
    其中 sum_xy 用 cumsum 恒等式避免 Python 回调。
    权重 w_k = n-k (k=0 最近, 权重最大=n; k=n-1 最远, 权重=1).
    """
    n = window
    total = n * (n + 1) / 2  # sum of 1..n

    def _decay(x: pd.Series) -> pd.Series:
        # sum_xy = sum_{k=0}^{n-1} k * x_{t-k}  (k=0 最近)
        # 用 cumsum 恒等式
        rs = x.rolling(window, min_periods=window).sum()
        csx = x.cumsum()
        rs_csx = csx.rolling(window, min_periods=window).sum()
        # sum_of_k_x_tk = n*cumsum - rolling_sum(cumsum) 这里的 k 是从最远端计数的
        # 需要仔细对应: 权重 [n, n-1, ..., 1] 对应 [x_t, x_{t-1}, ..., x_{t-n+1}]
        # 即 decay_sum = n*x_t + (n-1)*x_{t-1} + ... + 1*x_{t-n+1}
        # = (n+1) * rolling_sum - (0*x_t + 1*x_{t-1} + ... + (n-1)*x_{t-n+1})
        # 后半部分 = n*cumsum - rolling_sum(cumsum)
        sum_k_from_far = n * csx - rs_csx  # sum_{m=0}^{n-1} m * x_{t-n+1+m}
        # decay_sum = sum_{m=0}^{n-1} (m+1) * x_{t-n+1+m} = sum_k_from_far + rs
        # 但权重是 [n, n-1, ..., 1] 即 weight[m] = n-m
        # decay_sum = n*rs - sum_k_from_far
        decay_sum = n * rs - sum_k_from_far
        return decay_sum / total

    if group is not None:
        return s.groupby(group).transform(_decay)
    return _decay(s)


def ts_log(s: pd.Series) -> pd.Series:
    """自然对数（非正值替换为 NaN 避免 -inf）。"""
    result = np.log(s.where(s > 0, other=np.nan))
    return result


def ts_abs(s: pd.Series) -> pd.Series:
    """绝对值。"""
    return s.abs()


def ts_sign(s: pd.Series) -> pd.Series:
    """符号函数 (+1, 0, -1)。"""
    return np.sign(s)


def ts_greater(s1: pd.Series, s2: pd.Series | float) -> pd.Series:
    """逐元素取较大值 max(s1, s2)。"""
    if isinstance(s2, pd.Series):
        return pd.concat([s1, s2], axis=1).max(axis=1)
    return s1.clip(lower=s2)


def ts_less(s1: pd.Series, s2: pd.Series | float) -> pd.Series:
    """逐元素取较小值 min(s1, s2)。"""
    if isinstance(s2, pd.Series):
        return pd.concat([s1, s2], axis=1).min(axis=1)
    return s1.clip(upper=s2)


# =============================================================================
# 截面运算符 (Cross-Sectional Operators)
# 所有 cs_* 函数在同一时间截面（datetime 分组）内操作
# =============================================================================


def cs_rank(s: pd.Series, datetime_group: pd.Series) -> pd.Series:
    """截面排名（百分位 [0, 1]）。

    Args:
        s: 值序列
        datetime_group: 日期分组键
    """
    df = pd.DataFrame({"val": s, "dt": datetime_group})
    return df.groupby("dt")["val"].rank(pct=True)


def cs_scale(s: pd.Series, datetime_group: pd.Series) -> pd.Series:
    """截面缩放：值 / sum(|值|)，使截面绝对值之和为 1。"""
    df = pd.DataFrame({"val": s, "dt": datetime_group})
    abs_sum = df.groupby("dt")["val"].transform(lambda x: x.abs().sum())
    return s / abs_sum.replace(0, np.nan)


def cs_mean(s: pd.Series, datetime_group: pd.Series) -> pd.Series:
    """截面均值。"""
    df = pd.DataFrame({"val": s, "dt": datetime_group})
    return df.groupby("dt")["val"].transform("mean")


def cs_std(s: pd.Series, datetime_group: pd.Series) -> pd.Series:
    """截面标准差。"""
    df = pd.DataFrame({"val": s, "dt": datetime_group})
    return df.groupby("dt")["val"].transform("std")


def cs_zscore(s: pd.Series, datetime_group: pd.Series) -> pd.Series:
    """截面 Z-Score 标准化。"""
    mean = cs_mean(s, datetime_group)
    std = cs_std(s, datetime_group)
    return (s - mean) / std.replace(0, np.nan)


# =============================================================================
# 数学辅助函数
# =============================================================================


def safe_div(a: pd.Series, b: pd.Series, fill: float = 0.0) -> pd.Series:
    """安全除法，分母为零时返回 fill。"""
    result = a / b
    result = result.replace([np.inf, -np.inf], np.nan).fillna(fill)
    return result


def pow1(base: pd.Series, exp: float) -> pd.Series:
    """安全幂运算：负底数取绝对值后求幂再加符号。"""
    result = base.abs() ** exp
    result = result * np.sign(base)
    result = result.fillna(0)
    return result


def pow2(base: pd.Series, exp: pd.Series) -> pd.Series:
    """两个 Series 的幂运算 base^exp。"""
    positive = base > 0
    negative = base < 0
    int_exp = (exp.round() == exp)

    result = pd.Series(np.nan, index=base.index)
    result[positive] = base[positive] ** exp[positive]
    mask = negative & int_exp
    result[mask] = -(base[mask].abs() ** exp[mask])
    result = result.fillna(0)
    return result


def quesval(threshold: float, condition: pd.Series,
            true_val: pd.Series | float, false_val: pd.Series | float) -> pd.Series:
    """条件选择：if threshold < condition then true_val else false_val。"""
    mask = condition > threshold
    if isinstance(true_val, pd.Series) and isinstance(false_val, pd.Series):
        return pd.Series(np.where(mask, true_val, false_val), index=condition.index)
    elif isinstance(true_val, pd.Series):
        return pd.Series(np.where(mask, true_val, false_val), index=condition.index)
    elif isinstance(false_val, pd.Series):
        return pd.Series(np.where(mask, true_val, false_val), index=condition.index)
    else:
        return pd.Series(np.where(mask, true_val, false_val), index=condition.index)


def quesval2(threshold: pd.Series, condition: pd.Series,
             true_val: pd.Series | float | int,
             false_val: pd.Series | float | int) -> pd.Series:
    """条件选择（Series 比较版）：if condition < threshold then true_val else false_val。

    注意：遵循 vnpy 语义 — condition < threshold 时选 true_val。
    """
    mask = condition < threshold
    if isinstance(true_val, (int, float)):
        tv = true_val
    else:
        tv = true_val
    if isinstance(false_val, (int, float)):
        fv = false_val
    else:
        fv = false_val
    return pd.Series(np.where(mask, tv, fv), index=condition.index, dtype=float)
