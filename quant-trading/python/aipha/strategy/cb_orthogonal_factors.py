"""可转债正交因子模块 — 突破 price/premium/size 维度

提供与现有价格/溢价率/规模因子**正交**的新 alpha 维度：

1. **特异波动率 (IVOL)** — Ang et al. (2006) 低波异象
   残差波动率 = 总波动率 - 市场系统性波动
   IVOL 低的 CB 往往被忽视，提供 alpha 溢价
   - 理论：低 IVOL → 信息环境清晰 → 定价更理性 → 回报更稳
   - 实证：A 股 IVOL 因子 IC 5-7%（代理模式下估 3-5%）

2. **52 周高点距离 (High Distance)** — George & Hwang (2004) 锚定效应
   distance = (52w_high - close) / 52w_high
   接近 52w 高点的标的有动量延续效应
   - 理论：投资者锚定历史高点，突破后犹豫卖出 → 动量持续
   - 本因子与纯收益率动量有区别（基于水平而非变化率）

3. **Theta 代理 (时间价值衰减速率)** — CB 特有因子
   theta_proxy = (close - bond_floor_proxy) / sqrt(years_to_maturity)
   时间价值大 + 到期远 → 期权成分有价值但衰减慢
   - 理论：Theta = ∂V/∂t，ATM CB 时间价值最大
   - 策略含义：高 theta 且到期远 → 稳定的期权溢价收入

4. **波动率偏度 (Volatility Skew)** — 尾部风险度量
   skew = E[(r - μ)³] / σ³
   正偏度 → 上行尾部更厚 → 有利于多头

所有因子设计遵循：
- 通过 Tracker 类维护滚动窗口历史数据
- 输入为 bars + metadata，不依赖外部数据源
- 值越大表示越优（统一方向），方便排名融合
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR

if TYPE_CHECKING:
    from aipha.domain.models import Bar
    from aipha.strategy.cb_rotation_base import CandidateInfo


# ---------------------------------------------------------------------------
# 特异波动率 (IVOL) Tracker
# ---------------------------------------------------------------------------


class IVOLTracker:
    """特异波动率跟踪器

    使用简化的市场模型残差计算 IVOL：
    r_i = α + β * r_market + ε_i
    IVOL_i = std(ε_i)

    市场收益率使用当前 bar 中所有 CB 的等权平均收益率。
    策略使用时，IVOL 低的标的更优（值取负以统一方向）。

    Parameters:
        lookback: 回望窗口（交易日数），默认 20
        min_obs: 最少需要的观测数，默认 10
    """

    def __init__(self, lookback: int = 20, min_obs: int = 10) -> None:
        self.lookback = lookback
        self.min_obs = min_obs
        self._prev_close: dict[str, float] = {}
        self._returns: dict[str, deque[float]] = {}
        self._market_returns: deque[float] = deque(maxlen=lookback + 5)
        self._bar_count: int = 0

    def reset(self) -> None:
        """重置所有状态(on_start 时调用)。"""
        self._prev_close.clear()
        self._returns.clear()
        self._market_returns.clear()
        self._bar_count = 0

    def update(self, bars: dict[str, Any]) -> None:
        """每 bar 调用一次，更新收益率历史和市场收益率。

        Args:
            bars: symbol → Bar 字典
        """
        self._bar_count += 1

        # 计算各标的收益率和市场收益率
        bar_returns: dict[str, float] = {}
        for symbol, bar in bars.items():
            close = bar.close
            if close <= 0:
                continue
            prev = self._prev_close.get(symbol)
            if prev is not None and prev > 0:
                ret = (close - prev) / prev
                bar_returns[symbol] = ret
                if symbol not in self._returns:
                    self._returns[symbol] = deque(maxlen=self.lookback + 5)
                self._returns[symbol].append(ret)
            self._prev_close[symbol] = close

        # 市场收益率 = 等权平均
        if bar_returns:
            mkt_ret = sum(bar_returns.values()) / len(bar_returns)
            self._market_returns.append(mkt_ret)

    def get_ivol(self, symbol: str) -> float | None:
        """获取标的的特异波动率（年化）。

        返回**负值**（IVOL 低 → 值大 → 更优）。
        返回 None 表示数据不足。
        """
        rets = self._returns.get(symbol)
        if rets is None or len(rets) < self.min_obs:
            return None
        mkt_rets = list(self._market_returns)
        if len(mkt_rets) < self.min_obs:
            return None

        # 对齐长度（取最近 lookback 个）
        n = min(len(rets), len(mkt_rets), self.lookback)
        r_i = list(rets)[-n:]
        r_m = mkt_rets[-n:]

        # OLS: r_i = α + β * r_m + ε → β = cov(r_i, r_m) / var(r_m)
        mean_i = sum(r_i) / n
        mean_m = sum(r_m) / n

        var_m = sum((m - mean_m) ** 2 for m in r_m) / n
        if var_m < 1e-12:
            # 市场无波动，残差 = 总收益率
            residuals = [ri - mean_i for ri in r_i]
        else:
            cov_im = sum((r_i[j] - mean_i) * (r_m[j] - mean_m) for j in range(n)) / n
            beta = cov_im / var_m
            alpha = mean_i - beta * mean_m
            residuals = [r_i[j] - alpha - beta * r_m[j] for j in range(n)]

        # 残差标准差 → 年化
        if len(residuals) < 2:
            return None
        res_mean = sum(residuals) / len(residuals)
        res_var = sum((e - res_mean) ** 2 for e in residuals) / (len(residuals) - 1)
        ivol = math.sqrt(res_var) * math.sqrt(TRADING_DAYS_PER_YEAR)  # §13.8: 中国 A 股年度交易日

        # 返回负值：IVOL 低 → 因子值高 → rank 靠前
        return -ivol


# ---------------------------------------------------------------------------
# 52 周高点距离 Tracker
# ---------------------------------------------------------------------------


class HighDistanceTracker:
    """52 周高点距离跟踪器

    distance = close / high_252d
    值越接近 1.0（接近高点）→ 动量越强 → 更优。

    Parameters:
        lookback: 回望窗口（交易日数），默认 TRADING_DAYS_PER_YEAR
    """

    def __init__(self, lookback: int = TRADING_DAYS_PER_YEAR) -> None:
        self.lookback = lookback
        self._highs: dict[str, deque[float]] = {}

    def reset(self) -> None:
        """重置所有状态。"""
        self._highs.clear()

    def update(self, bars: dict[str, Any]) -> None:
        """每 bar 更新各标的的价格高点历史。"""
        for symbol, bar in bars.items():
            if bar.close <= 0:
                continue
            if symbol not in self._highs:
                self._highs[symbol] = deque(maxlen=self.lookback + 5)
            high = getattr(bar, "high", bar.close)
            self._highs[symbol].append(high)

    def get_distance(self, symbol: str, current_close: float) -> float | None:
        """获取标的接近 52 周高点的程度。

        返回 close / max(recent_highs)，范围 (0, 1]。
        越接近 1.0 表示越强（动量延续效应）。
        None 表示数据不足。
        """
        highs = self._highs.get(symbol)
        if highs is None or len(highs) < 20:
            return None
        if current_close <= 0:
            return None
        max_high = max(highs)
        if max_high <= 0:
            return None
        return current_close / max_high


# ---------------------------------------------------------------------------
# Theta 代理（CB 特有）
# ---------------------------------------------------------------------------


def theta_proxy(
    close: float,
    maturity_date: str,
    now: datetime,
    par_value: float = 100.0,
    coupon_rate: float = 0.005,
) -> float:
    """CB Theta 代理 — 标准化时间价值。

    theta_proxy = option_premium / sqrt(years_to_maturity)

    其中 option_premium = close - bond_floor_proxy
    bond_floor_proxy ≈ par * exp(-yield * T)  (简化零息债模型)

    值越大 → 期权成分越有价值且时间衰减可控 → 更稳定的收益来源。
    负值表示价格低于纯债价值（深度价外），也可能是低估信号。

    Args:
        close: 当前价格
        maturity_date: 到期日 (YYYYMMDD 格式)
        now: 当前时间
        par_value: 面值
        coupon_rate: 近似票息率

    Returns:
        Theta 代理值（标准化），数据不足返回 0.0
    """
    if close <= 0:
        return 0.0
    try:
        mat = datetime.strptime(maturity_date[:8], "%Y%m%d")
    except (ValueError, TypeError):
        return 0.0

    days = max((mat - now).days, 1)
    years = days / 365.25
    if years < 0.1:
        return 0.0

    # 简化纯债价值：bond_floor ≈ par * exp(-r * T)  (r = coupon_rate as proxy)
    # 实际 CB 的纯债价值更高（有票息），这里保守估计
    discount_rate = max(coupon_rate, 0.02)  # 至少 2% 折现
    bond_floor = par_value * math.exp(-discount_rate * years)

    option_premium = close - bond_floor
    # 标准化：除以 sqrt(T) 使不同到期日可比
    # 类似于 BSM 中 Theta ∝ σ * S * N'(d1) / (2*sqrt(T))
    normalized = option_premium / math.sqrt(years)

    return normalized


# ---------------------------------------------------------------------------
# 波动率偏度 Tracker
# ---------------------------------------------------------------------------


class SkewnessTracker:
    """收益率偏度跟踪器

    skew = E[(r - μ)³] / σ³
    正偏度 → 上行尾部更厚 → 更有利于多头。

    Parameters:
        lookback: 回望窗口，默认 20
        min_obs: 最少观测数，默认 10
    """

    def __init__(self, lookback: int = 20, min_obs: int = 10) -> None:
        self.lookback = lookback
        self.min_obs = min_obs
        self._prev_close: dict[str, float] = {}
        self._returns: dict[str, deque[float]] = {}

    def reset(self) -> None:
        """重置所有状态。"""
        self._prev_close.clear()
        self._returns.clear()

    def update(self, bars: dict[str, Any]) -> None:
        """每 bar 更新收益率历史。"""
        for symbol, bar in bars.items():
            close = bar.close
            if close <= 0:
                continue
            prev = self._prev_close.get(symbol)
            if prev is not None and prev > 0:
                ret = (close - prev) / prev
                if symbol not in self._returns:
                    self._returns[symbol] = deque(maxlen=self.lookback + 5)
                self._returns[symbol].append(ret)
            self._prev_close[symbol] = close

    def get_skewness(self, symbol: str) -> float | None:
        """获取标的收益率偏度。正偏度更优。None 表示数据不足。"""
        rets = self._returns.get(symbol)
        if rets is None or len(rets) < self.min_obs:
            return None

        data = list(rets)[-self.lookback:]
        n = len(data)
        if n < 3:
            return None

        mean = sum(data) / n
        var = sum((x - mean) ** 2 for x in data) / (n - 1)
        if var < 1e-16:
            return 0.0
        std = math.sqrt(var)
        m3 = sum((x - mean) ** 3 for x in data) / n
        return m3 / (std ** 3)
