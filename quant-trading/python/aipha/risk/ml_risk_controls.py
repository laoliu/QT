"""ML 策略公共风控模块 — 回测/模拟/实盘共享。

解决 R80 P0#1: risk_controlled_replay.py 与 PaperEngine 代码双轨问题。
将 MDD 梯度减仓、波动率动态仓位、行业中性约束抽取为可复用组件，
使回测脚本与模拟盘引擎共享同一套风控逻辑。

Usage:
    from aipha.risk.ml_risk_controls import (
        MddGradientDeleverager,
        LinearMddController,
        VolTargetScaler,
        IndustryConstraint,
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR


# ============================================================================
# Rolling Sharpe 降权触发器（B1: R88 工作项）
# ============================================================================


@dataclass
class SharpeDownweightTrigger:
    """Rolling SR 降权触发器。

    当 ML 策略的 rolling ``lookback`` 日 Sharpe Ratio 低于 ``sr_threshold``
    时，自动将 ML 权重从 ``normal_weight`` 降至 ``reduced_weight``。

    设计动机（R88 B1）：
      ML 策略（CSI2000-LGB）短期失效时应自动降低组合风险暴露，
      避免持续亏损放大组合 drawdown。

    用法::

        trigger = SharpeDownweightTrigger(
            lookback=60,
            sr_threshold=0.0,
            normal_weight=0.30,
            reduced_weight=0.20,
        )
        weight, info = trigger.evaluate(ml_nav_series)
    """

    lookback: int = 60
    sr_threshold: float = 0.0
    normal_weight: float = 0.30
    reduced_weight: float = 0.20
    annualization: int = TRADING_DAYS_PER_YEAR

    def compute_rolling_sr(self, nav_series: pd.Series) -> float:
        """计算 rolling Sharpe Ratio（最近 ``lookback`` 个交易日）。

        Returns:
            Sharpe Ratio 值；数据不足时返回 NaN。
        """
        if len(nav_series) < self.lookback + 1:
            return float("nan")
        rets = nav_series.pct_change().dropna().iloc[-self.lookback :]
        if len(rets) < 2:
            return float("nan")
        mean_ret = float(rets.mean())
        std_ret = float(rets.std())
        if std_ret <= 0:
            return 0.0
        return mean_ret / std_ret * np.sqrt(self.annualization)

    def evaluate(
        self, nav_series: pd.Series
    ) -> tuple[float, dict[str, Any]]:
        """评估当前是否触发降权。

        Args:
            nav_series: ML 策略的净值序列（日频）。

        Returns:
            (weight, diagnostic_info) 元组。
        """
        sr = self.compute_rolling_sr(nav_series)
        triggered = not np.isnan(sr) and sr < self.sr_threshold
        weight = self.reduced_weight if triggered else self.normal_weight
        return weight, {
            "rolling_sr_60d": round(sr, 4) if not np.isnan(sr) else None,
            "sr_threshold": self.sr_threshold,
            "triggered": triggered,
            "weight": weight,
            "normal_weight": self.normal_weight,
            "reduced_weight": self.reduced_weight,
        }


# ============================================================================
# MDD 梯度减仓
# ============================================================================

# V3 平衡版 5 级默认配置
DEFAULT_MDD_LEVELS: list[tuple[float, float]] = [
    (0.10, 1.0),   # MDD < 10%: 满仓
    (0.15, 0.80),  # MDD 10-15%: 80% 仓位
    (0.20, 0.55),  # MDD 15-20%: 55% 仓位
    (0.25, 0.30),  # MDD 20-25%: 30% 仓位
    (1.00, 0.10),  # MDD > 25%: 10% 仓位（不清仓）
]


@dataclass
class MddGradientDeleverager:
    """MDD 梯度减仓器。"""

    levels: list[tuple[float, float]] = field(
        default_factory=lambda: list(DEFAULT_MDD_LEVELS)
    )

    def get_scale(self, current_mdd: float) -> float:
        """根据当前 MDD 返回仓位缩放比例 (0~1)。"""
        for threshold, scale in self.levels:
            if current_mdd < threshold:
                return scale
        return 0.0

    def compute_mdd(self, nav_series: pd.Series) -> float:
        """计算当前 MDD。"""
        if len(nav_series) < 2:
            return 0.0
        peak = nav_series.cummax()
        return float((peak.iloc[-1] - nav_series.iloc[-1]) / peak.iloc[-1])


@dataclass
class LinearMddController:
    """线性 MDD 控制器（含冷却期状态机）。

    与 MddGradientDeleverager（阶梯式）互补，提供线性插值减仓。
    fusion_mdd_replay 等脚本的唯一 MDD 来源（§4.3.1）。

    状态机: normal → reducing → exited(cooldown) → normal
    """

    mdd_reduce: float = 0.12
    mdd_exit: float = 0.22
    cooldown_periods: int = 3

    _state: str = field(default="normal", init=False, repr=False)
    _cooldown_count: int = field(default=0, init=False, repr=False)
    _peak_nav: float = field(default=0.0, init=False, repr=False)

    def reset(self, initial_nav: float = 0.0) -> None:
        self._state = "normal"
        self._cooldown_count = 0
        self._peak_nav = initial_nav

    def update(self, nav: float) -> tuple[float, str]:
        """返回 (scale 0~1, state_name)。"""
        if self._peak_nav <= 0:
            self._peak_nav = nav
        self._peak_nav = max(self._peak_nav, nav)
        dd = ((self._peak_nav - nav) / self._peak_nav
              if self._peak_nav > 0 else 0.0)

        if self._state == "exited":
            self._cooldown_count += 1
            if self._cooldown_count >= self.cooldown_periods:
                self._peak_nav = nav
                self._state = "normal"
                self._cooldown_count = 0
                return 1.0, "recovered"
            return 0.0, "exited"

        if dd >= self.mdd_exit:
            self._state = "exited"
            self._cooldown_count = 0
            return 0.0, "exited"

        if dd >= self.mdd_reduce:
            self._state = "reducing"
            scale = (self.mdd_exit - dd) / (self.mdd_exit - self.mdd_reduce)
            return max(0.0, float(scale)), "reducing"

        self._state = "normal"
        return 1.0, "normal"

    @property
    def state(self) -> str:
        return self._state


# ============================================================================
# MDD 纯告警检查器（Paper / Live 共享）
# ============================================================================


@dataclass
class MddAlertChecker:
    """MDD 纯告警 + 安全阀（不降权）。

    Paper / Live 的 MDD 告警唯一实现（§4.3.1 / §13.8 扩展）。
    与 MddGradientDeleverager（回测降权）互补：
      - green : MDD < alert_threshold
      - alert : alert_threshold <= MDD < halt_threshold
      - halt  : MDD >= halt_threshold（暂停调仓）
    """

    alert_threshold: float = 0.15
    halt_threshold: float = 0.30

    def check(self, nav: float, peak_nav: float) -> str:
        """返回 'green' / 'alert' / 'halt'。"""
        if peak_nav <= 0:
            return "green"
        dd = 1.0 - nav / peak_nav
        if dd >= self.halt_threshold:
            return "halt"
        if dd >= self.alert_threshold:
            return "alert"
        return "green"

    def check_with_detail(
        self, nav: float, peak_nav: float,
    ) -> dict[str, Any]:
        """返回包含 MDD 数值和状态的详细字典。"""
        dd = (1.0 - nav / peak_nav) if peak_nav > 0 else 0.0
        status = self.check(nav, peak_nav)
        return {
            "mdd": dd,
            "status": status,
            "alert_threshold": self.alert_threshold,
            "halt_threshold": self.halt_threshold,
        }


# ============================================================================
# 波动率目标仓位
# ============================================================================


@dataclass
class VolTargetScaler:
    """波动率目标仓位缩放器。

    支持两种调用模式：
    - 批量式：``get_scale(nav_series)`` — 传入完整 NAV 序列
    - 增量式：``update(daily_return)`` + ``get_current_scale()`` — bar-by-bar 喂入
    """

    vol_target: float = 0.20
    lookback: int = 60
    min_scale: float = 0.2
    max_scale: float = 1.5
    annualization: int = TRADING_DAYS_PER_YEAR

    def __init__(
        self,
        vol_target: float = 0.20,
        lookback: int = 60,
        min_scale: float = 0.2,
        max_scale: float = 1.5,
    ) -> None:
        self.vol_target = vol_target
        self.lookback = lookback
        self.min_scale = min_scale
        self.max_scale = max_scale
        self.annualization = TRADING_DAYS_PER_YEAR
        # 增量式状态
        self._returns: list[float] = []
        self._current_scale: float = 1.0

    def get_scale(self, nav_series: pd.Series) -> float:
        """波动率目标仓位缩放: vol_target / realized_vol。"""
        if len(nav_series) < self.lookback:
            return 1.0
        rets = nav_series.pct_change().dropna().iloc[-self.lookback:]
        realized_vol = rets.std() * np.sqrt(self.annualization)
        if realized_vol <= 0:
            return 1.0
        scale = self.vol_target / realized_vol
        return float(np.clip(scale, self.min_scale, self.max_scale))

    # -- 增量式 API（兼容 VolatilityTargeter 接口）--------------------------

    def update(self, daily_return: float) -> float:
        """喂入日收益率，返回当前缩放因子。"""
        self._returns.append(daily_return)
        n = len(self._returns)
        min_obs = max(5, self.lookback // 2)
        if n < min_obs:
            self._current_scale = 1.0
            return 1.0
        recent = self._returns[-self.lookback:] if n >= self.lookback else self._returns
        daily_vol = float(np.std(recent, ddof=1))
        realized_vol = daily_vol * np.sqrt(self.annualization)
        if realized_vol < 1e-10:
            scale = self.max_scale
        else:
            scale = self.vol_target / realized_vol
        scale = float(np.clip(scale, self.min_scale, self.max_scale))
        self._current_scale = scale
        return scale

    def get_current_scale(self) -> float:
        """获取当前缩放因子（增量模式）。"""
        return self._current_scale

    def reset(self) -> None:
        """重置增量状态。"""
        self._returns.clear()
        self._current_scale = 1.0


# ============================================================================
# 行业中性约束
# ============================================================================


@dataclass
class IndustryConstraint:
    """行业中性约束：单行业不超 max_pct。"""

    max_pct: float = 0.15

    def apply(
        self,
        candidates: list[str],
        industry_map: dict[str, str],
        top_k: int,
    ) -> tuple[list[str], int]:
        """过滤候选标的，返回 (过滤后列表, 被阻断数量)。"""
        max_per_industry = max(1, int(top_k * self.max_pct))
        industry_count: dict[str, int] = {}
        filtered: list[str] = []
        blocked = 0
        for sym in candidates:
            ind = industry_map.get(sym, "未知")
            cnt = industry_count.get(ind, 0)
            if cnt >= max_per_industry:
                blocked += 1
                continue
            industry_count[ind] = cnt + 1
            filtered.append(sym)
            if len(filtered) >= top_k:
                break
        return filtered, blocked
