"""可转债正交因子追踪器（IVOL / 距离高点 / 偏度 / Theta代理）"""
from __future__ import annotations
import math
from collections import deque
from datetime import datetime
from typing import Any

class IVOLTracker:
    """追踪每个标的的隐含波动率代理"""
    def __init__(self, lookback: int = 20, min_obs: int = 10):
        self.lookback = lookback; self.min_obs = min_obs
        self._returns: dict[str, deque[float]] = {}; self._prev_close: dict[str, float] = {}
        self._bar_count = 0
    def reset(self) -> None:
        self._returns.clear(); self._prev_close.clear(); self._bar_count = 0
    def update(self, bars: dict[str, Any]) -> None:
        self._bar_count += 1
        for sym, bar in bars.items():
            close = getattr(bar, "close", None)
            if close is None: continue
            prev = self._prev_close.get(sym)
            if prev and prev > 0:
                if sym not in self._returns: self._returns[sym] = deque(maxlen=self.lookback + 2)
                self._returns[sym].append(math.log(close / prev))
            self._prev_close[sym] = close
    def get_ivol(self, symbol: str) -> float | None:
        rets = self._returns.get(symbol)
        if not rets or len(rets) < self.min_obs: return None
        recent = list(rets)[-self.lookback:]
        if len(recent) < 2: return None
        mu = sum(recent) / len(recent)
        var = sum((r - mu) ** 2 for r in recent) / (len(recent) - 1)
        return -math.sqrt(var * 252) if var > 0 else 0.0

class HighDistanceTracker:
    """追踪距离历史最高价的距离"""
    def __init__(self, lookback: int = 252):
        self.lookback = lookback; self._highs: dict[str, deque[float]] = {}
    def reset(self) -> None: self._highs.clear()
    def update(self, bars: dict[str, Any]) -> None:
        for sym, bar in bars.items():
            high = getattr(bar, "high", None)
            if high is None: continue
            if sym not in self._highs: self._highs[sym] = deque(maxlen=self.lookback + 2)
            self._highs[sym].append(high)
    def get_distance(self, symbol: str, current_close: float) -> float | None:
        if current_close <= 0:
            return None
        h = self._highs.get(symbol)
        if not h or len(h) < 20:
            return None
        peak = max(list(h)[-self.lookback:])
        return current_close / peak if peak > 0 else None

class SkewnessTracker:
    """追踪收益率偏度"""
    def __init__(self, lookback: int = 20, min_obs: int = 10):
        self.lookback = lookback; self.min_obs = min_obs
        self._returns: dict[str, deque[float]] = {}; self._prev_close: dict[str, float] = {}
    def reset(self) -> None:
        self._returns.clear(); self._prev_close.clear()
    def update(self, bars: dict[str, Any]) -> None:
        for sym, bar in bars.items():
            close = getattr(bar, "close", None)
            if close is None: continue
            prev = self._prev_close.get(sym)
            if prev and prev > 0:
                if sym not in self._returns: self._returns[sym] = deque(maxlen=self.lookback + 2)
                self._returns[sym].append(math.log(close / prev))
            self._prev_close[sym] = close
    def get_skewness(self, symbol: str) -> float | None:
        rets = self._returns.get(symbol)
        if not rets or len(rets) < self.min_obs: return None
        recent = list(rets)[-self.lookback:]
        n = len(recent); mu = sum(recent) / n
        m2 = sum((r - mu) ** 2 for r in recent) / n
        m3 = sum((r - mu) ** 3 for r in recent) / n
        return m3 / (m2 ** 1.5) if m2 > 0 else 0.0

def theta_proxy(close: float, maturity_date: str, now: datetime, par_value: float = 100.0, coupon_rate: float = 0.005) -> float:
    if close <= 0 or maturity_date is None:
        return 0.0
    try:
        if "-" in str(maturity_date):
            mat = datetime.strptime(str(maturity_date)[:10], "%Y-%m-%d")
        else:
            mat = datetime.strptime(str(maturity_date)[:8], "%Y%m%d")
        years = (mat - now).days / 365.25
    except Exception:
        return 0.0
    if years < 0.1:
        return 0.0
    bond_val = par_value * (1 + coupon_rate * years)
    time_val = close - bond_val
    return time_val / math.sqrt(years) if years > 0 else 0.0
