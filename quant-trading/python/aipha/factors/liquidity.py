"""流动性/微观结构因子模块

捕捉流动性溢价和微观结构特征的因子。

包含：
- AmihudIlliquidityFactor: Amihud 非流动性因子（经典流动性溢价）
- TurnoverMomentumFactor: 换手率动量（突变信号）
- VolumeRatioFactor: 相对成交量比率

数据依赖：
- OHLCV 日线数据（通过 FactorData.historical_close / returns / metadata）
- volume, amount 序列

设计决策：
- Amihud: |return| / amount，值越大 = 越不流动 = 流动性溢价越高
- 换手率动量: 短期换手率变化率，捕捉关注度突变
- VolumeRatio: 当前量 vs 历史均量比值
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from aipha.factors.base import Factor, FactorData, FactorResult


# ---------------------------------------------------------------------------
# Amihud 非流动性因子
# ---------------------------------------------------------------------------

@dataclass
class AmihudIlliquidityFactor(Factor):
    """Amihud 非流动性因子

    ILLIQ = mean(|daily_return| / daily_amount) × 10^6

    经典流动性溢价因子（Amihud 2002）。
    非流动性越高（ILLIQ 越大），预期收益溢价越高。

    Attributes:
        lookback_days: 回望期计算平均非流动性
        scale: 缩放因子（避免数值过小）
    """

    lookback_days: int = 20
    scale: float = 1e6

    @property
    def name(self) -> str:
        """因子名称: ``AMIHUD_{lookback_days}``。"""
        return f"AMIHUD_{self.lookback_days}"

    def compute(self, data: FactorData) -> FactorResult:
        """计算 Amihud 非流动性指标。

        Args:
            data: 因子输入数据（需 returns_history + amount_history）

        Returns:
            FactorResult，value 为 ILLIQ 值（已缩放）；数据不足时 value=NaN。
        """
        illiq = self._compute_illiq(data)

        if illiq is None:
            return self._invalid(data, "数据不足")

        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=illiq,
            metadata={"amihud_illiq": illiq, "lookback": self.lookback_days},
        )

    def _compute_illiq(self, data: FactorData) -> float | None:
        # 需要 returns 和 amount 序列
        returns = data.metadata.get("returns_history")
        amounts = data.metadata.get("amount_history")

        if returns is None or amounts is None:
            # 从 historical_close 计算 returns
            if data.historical_close:
                sorted_dates = sorted(data.historical_close.keys())
                if len(sorted_dates) < self.lookback_days + 1:
                    return None

                recent = sorted_dates[-(self.lookback_days + 1):]
                returns = []
                for i in range(1, len(recent)):
                    p0 = data.historical_close.get(recent[i - 1])
                    p1 = data.historical_close.get(recent[i])
                    if p0 and p0 > 0 and p1 is not None:
                        returns.append((p1 - p0) / p0)
                    else:
                        returns.append(0.0)

                # 如果 amount_history 不可用，无法计算
                if amounts is None:
                    return None

        if returns is None or amounts is None:
            return None

        # 取最近 lookback_days 个数据
        ret = returns[-self.lookback_days:]
        amt = amounts[-self.lookback_days:]

        if len(ret) < 2 or len(amt) < 2:
            return None

        # 确保长度一致
        n = min(len(ret), len(amt))
        ret = ret[-n:]
        amt = amt[-n:]

        illiq_values = []
        for r, a in zip(ret, amt):
            try:
                r_f = float(r)
                a_f = float(a)
                if a_f > 0:
                    illiq_values.append(abs(r_f) / a_f)
            except (TypeError, ValueError):
                continue

        if not illiq_values:
            return None

        return (sum(illiq_values) / len(illiq_values)) * self.scale

    def _invalid(self, data: FactorData, reason: str) -> FactorResult:
        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=float("nan"),
            metadata={"invalid": True, "reason": reason},
        )


# ---------------------------------------------------------------------------
# 换手率动量因子
# ---------------------------------------------------------------------------

@dataclass
class TurnoverMomentumFactor(Factor):
    """换手率动量因子

    衡量短期换手率变化率：
    turnover_momentum = mean(turnover_recent_N) / mean(turnover_long_M) - 1

    换手率突然放大可能预示关注度提升，是量价配合的信号。

    Attributes:
        short_window: 短期窗口（天）
        long_window: 长期窗口（天）
    """

    short_window: int = 5
    long_window: int = 20

    @property
    def name(self) -> str:
        """因子名称: ``TURNOVER_MOM_{short}_{long}``。"""
        return f"TURNOVER_MOM_{self.short_window}_{self.long_window}"

    def compute(self, data: FactorData) -> FactorResult:
        """计算换手率动量（短期均值 / 长期均值 - 1）。

        Args:
            data: 因子输入数据（需 metadata["turnover_history"]）

        Returns:
            FactorResult，正值=换手率放大，负值=缩小；数据不足时 value=NaN。
        """
        momentum = self._compute_momentum(data)

        if momentum is None:
            return self._invalid(data, "换手率数据不足")

        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=momentum,
            metadata={"turnover_momentum": momentum},
        )

    def _compute_momentum(self, data: FactorData) -> float | None:
        turnover_history = data.metadata.get("turnover_history")
        if turnover_history is None or len(turnover_history) < self.long_window:
            return None

        try:
            recent = [float(t) for t in turnover_history[-self.long_window:]]
        except (TypeError, ValueError):
            return None

        if len(recent) < self.long_window:
            return None

        short_mean = sum(recent[-self.short_window:]) / self.short_window
        long_mean = sum(recent) / self.long_window

        if long_mean <= 0:
            return None

        return short_mean / long_mean - 1.0

    def _invalid(self, data: FactorData, reason: str) -> FactorResult:
        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=float("nan"),
            metadata={"invalid": True, "reason": reason},
        )


# ---------------------------------------------------------------------------
# 相对成交量比率因子
# ---------------------------------------------------------------------------

@dataclass
class VolumeRatioFactor(Factor):
    """相对成交量比率因子

    VR = current_volume / mean(volume_past_N)

    成交量放大通常伴随趋势启动或结束。

    Attributes:
        lookback_days: 平均成交量回望期
    """

    lookback_days: int = 20

    @property
    def name(self) -> str:
        """因子名称: ``VOLUME_RATIO_{lookback_days}``。"""
        return f"VOLUME_RATIO_{self.lookback_days}"

    def compute(self, data: FactorData) -> FactorResult:
        """计算相对成交量比率 (VR)。

        Args:
            data: 因子输入数据（需 metadata["volume_history"]）

        Returns:
            FactorResult，value 为当前量/历史均量；数据不足时 value=NaN。
        """
        vr = self._compute_vr(data)

        if vr is None:
            return self._invalid(data, "成交量数据不足")

        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=vr,
            metadata={"volume_ratio": vr},
        )

    def _compute_vr(self, data: FactorData) -> float | None:
        volume_history = data.metadata.get("volume_history")

        if volume_history is None or len(volume_history) < self.lookback_days + 1:
            # 尝试从 FactorData.volume 和历史计算
            if data.volume is not None and data.volume > 0:
                avg_vol = data.metadata.get("avg_volume")
                if avg_vol is not None and float(avg_vol) > 0:
                    return data.volume / float(avg_vol)
            return None

        try:
            vols = [float(v) for v in volume_history]
        except (TypeError, ValueError):
            return None

        current_vol = vols[-1]
        past_vols = vols[-(self.lookback_days + 1):-1]

        if not past_vols or current_vol <= 0:
            return None

        avg_vol = sum(past_vols) / len(past_vols)
        if avg_vol <= 0:
            return None

        return current_vol / avg_vol

    def _invalid(self, data: FactorData, reason: str) -> FactorResult:
        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=float("nan"),
            metadata={"invalid": True, "reason": reason},
        )
