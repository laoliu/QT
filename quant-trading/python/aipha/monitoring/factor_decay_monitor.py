"""因子衰减半衰期监控 — HalflifeDecayMonitor。

在 DecayMonitor 基础上增加 AR(1) 半衰期估算与趋势预警：
- 半衰期（Half-life）：因子 IC 自相关的衰减速度
  公式：τ₁/₂ = -log(2) / log(|ρ₁|)
  其中 ρ₁ = AR(1) 自回归系数
- 近3年滚动 ICIR 不低于全量期 ICIR 40%（知识库原则B Alpha衰减检验）

理论基础：
- McLean & Pontiff (2016) — Alpha 衰减与市场竞争
- Hamilton (1994) — AR(1) 时间序列回归
- 知识库 Q1 衰减公式
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

from aipha.factors.decay_monitor import DecayMonitor, DecayUpdateResult, MonitorStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class HalflifeSnapshot:
    """半衰期估算快照。"""
    factor_name: str
    halflife_days: Optional[float]      # 半衰期（交易日），None=无法估算
    ar1_coeff: Optional[float]          # AR(1) 系数 ρ₁
    n_obs: int                          # 用于估算的观测数
    is_decaying: bool                   # True = 半衰期 < 252 天（年内衰减）
    decay_alert: bool                   # True = 近期 ICIR < 全量 ICIR * 0.40
    recent_icir: float                  # 近 N 期滚动 ICIR
    full_icir: float                    # 全量 ICIR（全窗口）


# ---------------------------------------------------------------------------
# 主类
# ---------------------------------------------------------------------------

class HalflifeDecayMonitor(DecayMonitor):
    """带半衰期估算的因子衰减监控器。

    继承 DecayMonitor 所有功能，新增：
    - ``estimate_halflife()`` — AR(1) 半衰期估算
    - ``get_halflife_snapshot()`` — 完整快照
    - ``is_alpha_decaying()`` — 知识库原则B Alpha衰减检验（近3年 vs 全量）

    Usage::

        monitor = HalflifeDecayMonitor("ep_ratio", window=60, icir_threshold=0.3)
        for date, ic in ic_series:
            result = monitor.update(date, ic)
        snapshot = monitor.get_halflife_snapshot(recent_n=20)
        print(f"Half-life: {snapshot.halflife_days:.1f} days")
    """

    def __init__(
        self,
        factor_name: str,
        window: int = 60,
        icir_threshold: float = 0.3,
        min_obs_for_halflife: int = 10,
    ) -> None:
        super().__init__(factor_name, window, icir_threshold)
        self._min_obs_for_halflife = min_obs_for_halflife
        # 全量 IC 历史（不受 window 限制，用于 Alpha 衰减检验）
        self._all_ic: list[float] = []

    def update(self, date: str, ic_value: float) -> DecayUpdateResult:
        """接收新 IC 值，同时更新全量历史。"""
        result = super().update(date, ic_value)
        self._all_ic.append(ic_value)
        return result

    def estimate_halflife(self) -> Optional[float]:
        """估算因子 IC 的半衰期（交易日）。

        使用 AR(1) 模型：IC_{t+1} = ρ₁ × IC_t + ε
        半衰期 τ₁/₂ = -log(2) / log(|ρ₁|)

        满足 |ρ₁| < 1 时才有意义（平稳过程）。

        Returns:
            半衰期（交易日），数据不足或非平稳时返回 None
        """
        rho = self.get_ar1_coeff()
        if rho is None:
            return None
        abs_rho = abs(rho)
        if abs_rho <= 0.0 or abs_rho >= 1.0:
            # abs_rho=0 → 立即衰减到0，abs_rho>=1 → 非平稳
            return None
        halflife = -np.log(2) / np.log(abs_rho)
        return float(halflife)

    def get_ar1_coeff(self) -> Optional[float]:
        """估算 AR(1) 自回归系数 ρ₁。

        使用 OLS：IC_t+1 = ρ₁ × IC_t + ε
        需要至少 min_obs_for_halflife + 1 个观测值。

        Returns:
            ρ₁ 估计值，不足时返回 None
        """
        ic_list = self._all_ic
        n = len(ic_list)
        if n < self._min_obs_for_halflife + 1:
            return None
        x = np.array(ic_list[:-1])  # IC_{t}
        y = np.array(ic_list[1:])   # IC_{t+1}
        # OLS: ρ₁ = cov(x, y) / var(x)
        x_mean = np.mean(x)
        y_mean = np.mean(y)
        cov_xy = np.mean((x - x_mean) * (y - y_mean))
        var_x = np.mean((x - x_mean) ** 2)
        if var_x < 1e-12:
            return None
        return float(cov_xy / var_x)

    def is_alpha_decaying(self, recent_n: int = 20) -> bool:
        """知识库原则B Alpha衰减检验。

        近 recent_n 期滚动 ICIR < 全量 ICIR × 0.40 则视为衰减。

        Returns:
            True = 衰减预警
        """
        all_ic = self._all_ic
        n_total = len(all_ic)
        if n_total < recent_n + 2:
            return False

        # 全量 ICIR
        full_icir = self._compute_icir_from(all_ic)
        if np.isnan(full_icir) or abs(full_icir) < 1e-9:
            return False

        # 近期 ICIR
        recent_ic = all_ic[-recent_n:]
        recent_icir = self._compute_icir_from(recent_ic)
        if np.isnan(recent_icir):
            return False

        # 知识库原则B：近3年滚动SR不低于全量SR的40%
        decay_ratio = abs(recent_icir) / abs(full_icir)
        return decay_ratio < 0.40

    def get_halflife_snapshot(self, recent_n: int = 20) -> HalflifeSnapshot:
        """获取完整半衰期快照。

        Args:
            recent_n: 近期 ICIR 的滚动窗口

        Returns:
            HalflifeSnapshot
        """
        halflife = self.estimate_halflife()
        ar1 = self.get_ar1_coeff()
        all_ic = self._all_ic

        # 全量 ICIR
        full_icir = self._compute_icir_from(all_ic)

        # 近期 ICIR
        recent_ic = all_ic[-recent_n:] if len(all_ic) >= recent_n else all_ic
        recent_icir = self._compute_icir_from(recent_ic) if len(recent_ic) >= 2 else float("nan")

        return HalflifeSnapshot(
            factor_name=self.factor_name,
            halflife_days=halflife,
            ar1_coeff=ar1,
            n_obs=len(all_ic),
            is_decaying=(halflife is not None and halflife < 252),
            decay_alert=self.is_alpha_decaying(recent_n),
            recent_icir=recent_icir,
            full_icir=full_icir,
        )

    # ---------------------------------------------------------------------------
    # 内部辅助
    # ---------------------------------------------------------------------------

    @staticmethod
    def _compute_icir_from(ic_list: list[float]) -> float:
        """从 IC 列表计算 ICIR，不受窗口限制。"""
        arr = np.asarray(ic_list, dtype=float)
        arr = arr[np.isfinite(arr)]
        if len(arr) < 2:
            return float("nan")
        std = np.std(arr, ddof=1)
        if std < 1e-12:
            return float("nan")
        return float(np.mean(arr) / std)
