"""滑点模型 (SlippageModel)

提供三种可插拔的滑点实现：

1. FixedBpsSlippageModel  — 固定基点滑点（最简单，V1 默认）
2. LinearImpactSlippageModel — 线性冲击模型：slippage_bps = base_bps + impact_coeff × participation_rate
3. SquareRootImpactSlippageModel — 平方根冲击（Almgren-Chriss 简化）：
   slippage_bps = sigma_daily × impact_coeff × sqrt(qty / daily_volume)

所有模型都通过 apply(fill, bar=None) 接口对 Fill 施加滑点。
bar 参数用于传入当日行情（volume/amount），供冲击模型估算参与率。
若 bar 为 None，冲击模型回退到 fallback_bps。

A 股滑点基准参考（含市场冲击 + 买卖价差，日频 next-bar-open）：
- 大盘（沪深300）: 10-15 bps
- 中盘（中证500）: 15-20 bps
- 小盘（中证1000+）: 25-50 bps
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from aipha.domain.models import Bar, Fill, Side


logger = logging.getLogger(__name__)


# ============================================================================
# A 股滑点参考常量（bps）
# ============================================================================

# 推荐最低滑点基准（保守估计，含隐性成本）
SLIPPAGE_BPS_LARGECAP = 10.0    # 沪深300成分股
SLIPPAGE_BPS_MIDCAP = 15.0      # 中证500成分股
SLIPPAGE_BPS_SMALLCAP = 25.0    # 中证1000 及更小
SLIPPAGE_BPS_MICROCAP = 40.0    # 微盘股 / 低流动性

# A 股策略的绝对最低合理值
SLIPPAGE_BPS_MIN_REALISTIC = 5.0


# ============================================================================
# Protocol 接口
# ============================================================================


@runtime_checkable
class SlippageModel(Protocol):
    """滑点模型协议。

    所有滑点实现必须符合此协议。
    """

    def apply(self, fill: Fill, bar: Bar | None = None) -> Fill:
        """对成交回报施加滑点。

        Args:
            fill: 原始成交回报
            bar:  当日行情（供冲击模型估算参与率），可选

        Returns:
            施加滑点后的成交回报
        """
        ...


# ============================================================================
# 固定基点滑点（V1 默认）
# ============================================================================


@dataclass(frozen=True, slots=True)
class FixedBpsSlippageModel:
    """Fixed bps slippage.

    BUY: price increases
    SELL: price decreases
    """

    bps: float

    def apply(self, fill: Fill, bar: Bar | None = None) -> Fill:
        """施加固定基点滑点。买入价格上调，卖出价格下调。"""
        m = 1.0 + (self.bps / 10000.0) if fill.side == Side.BUY else 1.0 - (self.bps / 10000.0)
        return Fill(
            order_id=fill.order_id,
            symbol=fill.symbol,
            side=fill.side,
            qty=fill.qty,
            price=fill.price * m,
            t_fill=fill.t_fill,
            model=fill.model,
        )


# ============================================================================
# 线性冲击滑点
# ============================================================================


@dataclass(frozen=True, slots=True)
class LinearImpactSlippageModel:
    """线性市场冲击模型。

    slippage_bps = base_bps + impact_coeff × participation_rate

    其中 participation_rate = order_notional / effective_bar_amount。
    当 opening_volume_fraction < 1.0 时，effective_bar_amount = bar_amount × opening_volume_fraction，
    以反映集合竞价/开盘成交的实际可用流动性。

    参数：
        base_bps:      固定基础滑点（基点）
        impact_coeff:  冲击系数（基点/参与率），即参与率=1时的额外滑点
        fallback_bps:  bar 缺失时回退的固定滑点
        max_bps:       单笔滑点上限（防止异常值）
        opening_volume_fraction: 开盘成交可用流动性占全天比例（0.0-1.0），
            默认 1.0（全天成交额）。当 fill_model 为 next_bar_open 时
            应设为 0.10-0.15 以反映集合竞价流动性。
    """

    base_bps: float = 3.0
    impact_coeff: float = 50.0
    fallback_bps: float = 10.0
    max_bps: float = 100.0
    opening_volume_fraction: float = 1.0

    def apply(self, fill: Fill, bar: Bar | None = None) -> Fill:
        """施加线性冲击滑点。

        根据订单参与率线性增加滑点，bar 缺失时回退到 fallback_bps。
        当 opening_volume_fraction < 1.0 时，bar 成交额按比例缩减。
        """
        if bar is None or bar.volume <= 0:
            bps = self.fallback_bps
        else:
            # 用成交额计算参与率；若 bar 无成交额则用 volume × vwap 估算
            bar_amount = _estimate_bar_amount(bar)
            if bar_amount <= 0:
                bps = self.fallback_bps
            else:
                # 按 opening_volume_fraction 缩减有效成交额
                effective_amount = bar_amount * self.opening_volume_fraction
                order_notional = fill.qty * fill.price
                participation = order_notional / effective_amount
                bps = self.base_bps + self.impact_coeff * participation

        bps = min(bps, self.max_bps)
        m = 1.0 + (bps / 10000.0) if fill.side == Side.BUY else 1.0 - (bps / 10000.0)
        return Fill(
            order_id=fill.order_id,
            symbol=fill.symbol,
            side=fill.side,
            qty=fill.qty,
            price=fill.price * m,
            t_fill=fill.t_fill,
            model=fill.model,
        )


# ============================================================================
# 平方根冲击滑点（Almgren-Chriss 简化）
# ============================================================================


@dataclass(frozen=True, slots=True)
class SquareRootImpactSlippageModel:
    """平方根市场冲击模型（Almgren-Chriss 简化）。

    slippage_bps = sigma_daily × impact_coeff × sqrt(qty / effective_volume) × 10000

    其中 sigma_daily 为日波动率（decimal），impact_coeff 为无量纲冲击系数。
    当 opening_volume_fraction < 1.0 时，effective_volume = volume × opening_volume_fraction。

    参数：
        impact_coeff:      无量纲冲击系数（经验值通常 0.1 ~ 1.0）
        sigma_daily:       默认日波动率（decimal 形式，如 0.02 = 2%）
        fallback_bps:      bar 缺失时回退的固定滑点
        max_bps:           单笔滑点上限
        use_bar_volatility: 是否尝试从 bar 的 (high-low)/close 估算当日波动率
        opening_volume_fraction: 开盘成交可用流动性占全天比例（0.0-1.0），
            默认 1.0。当 fill_model 为 next_bar_open 时应设为 0.10-0.15。
    """

    impact_coeff: float = 0.5
    sigma_daily: float = 0.02
    fallback_bps: float = 10.0
    max_bps: float = 200.0
    use_bar_volatility: bool = True
    opening_volume_fraction: float = 1.0

    def apply(self, fill: Fill, bar: Bar | None = None) -> Fill:
        """施加平方根冲击滑点。

        使用 Almgren-Chriss 简化公式，bar 缺失时回退到 fallback_bps。
        当 opening_volume_fraction < 1.0 时，成交量按比例缩减。
        """
        if bar is None or bar.volume <= 0:
            bps = self.fallback_bps
        else:
            # 波动率估算
            sigma = self.sigma_daily
            if self.use_bar_volatility and bar.close > 0:
                intraday_range = (bar.high - bar.low) / bar.close
                if intraday_range > 0:
                    sigma = max(sigma, intraday_range)

            # 参与率 = qty / effective_volume（按 opening_volume_fraction 调整）
            effective_volume = bar.volume * self.opening_volume_fraction
            participation = fill.qty / effective_volume if effective_volume > 0 else 1.0
            bps = sigma * self.impact_coeff * math.sqrt(participation) * 10000.0

        bps = min(bps, self.max_bps)
        m = 1.0 + (bps / 10000.0) if fill.side == Side.BUY else 1.0 - (bps / 10000.0)
        return Fill(
            order_id=fill.order_id,
            symbol=fill.symbol,
            side=fill.side,
            qty=fill.qty,
            price=fill.price * m,
            t_fill=fill.t_fill,
            model=fill.model,
        )


# ============================================================================
# 辅助函数
# ============================================================================


def _estimate_bar_amount(bar: Bar) -> float:
    """估算 bar 的成交额。

    优先使用 metadata 中的 amount 字段（CB 数据通常有）；
    否则用 volume × vwap（或 close）近似。
    """
    if bar.metadata and "amount" in bar.metadata:
        return float(bar.metadata["amount"])
    price_est = bar.vwap if bar.vwap and bar.vwap > 0 else bar.close
    return bar.volume * price_est if price_est > 0 else 0.0


# ============================================================================
# CB 分层买卖价差常量（R94 审计建议）
# ============================================================================

# 默认 CB 分层阈值：(日成交额万元下限, base_bps)
# 从高到低排列，第一个匹配的生效
CB_TIERED_SPREADS: list[tuple[float, float]] = [
    (5000.0, 5.0),    # >=5000万：大盘CB，价差窄
    (2000.0, 10.0),   # >=2000万：中盘CB
    (500.0, 20.0),    # >=500万：小盘CB
    (0.0, 40.0),      # <500万：微盘CB，价差宽
]


@dataclass(frozen=True, slots=True)
class TieredImpactSlippageModel:
    """分层买卖价差 + 线性冲击模型（R94-V2 增强）。

    根据标的日成交额自动选择 base_bps 层级，解决
    "所有 CB 统一 5 bps"严重低估小 CB 真实买卖价差的问题。

    slippage_bps = tiered_base_bps(amount) + impact_coeff × participation_rate

    参数：
        tiers:         分层规则 [(日成交额万元下限, base_bps), ...]，
                       降序排列。默认使用 CB_TIERED_SPREADS。
        impact_coeff:  冲击系数（bps/参与率）
        fallback_bps:  bar 缺失时的回退滑点
        max_bps:       单笔滑点上限
        opening_volume_fraction: 开盘流动性占全天比例
    """

    tiers: tuple[tuple[float, float], ...] = tuple(CB_TIERED_SPREADS)
    impact_coeff: float = 50.0
    fallback_bps: float = 20.0
    max_bps: float = 150.0
    opening_volume_fraction: float = 1.0

    def _get_base_bps(self, bar: Bar | None) -> float:
        """根据 bar 成交额选择对应的 base_bps 层级。"""
        if bar is None:
            return self.fallback_bps
        amount = _estimate_bar_amount(bar)
        if amount <= 0:
            return self.fallback_bps
        for threshold, bps in self.tiers:
            if amount >= threshold:
                return bps
        return self.fallback_bps

    def apply(self, fill: Fill, bar: Bar | None = None) -> Fill:
        """施加分层滑点。base_bps 根据成交额动态选择。"""
        base = self._get_base_bps(bar)
        if bar is None or bar.volume <= 0:
            bps = base
        else:
            bar_amount = _estimate_bar_amount(bar)
            effective = bar_amount * self.opening_volume_fraction
            if effective <= 0:
                bps = base
            else:
                notional = fill.qty * fill.price
                pr = notional / effective
                bps = base + self.impact_coeff * pr

        bps = min(bps, self.max_bps)
        m = (1.0 + bps / 10000.0) if fill.side == Side.BUY else (1.0 - bps / 10000.0)
        return Fill(
            order_id=fill.order_id,
            symbol=fill.symbol,
            side=fill.side,
            qty=fill.qty,
            price=fill.price * m,
            t_fill=fill.t_fill,
            model=fill.model,
        )


# 便于统一类型标注 — 保留向后兼容别名
# 推荐直接使用 SlippageModel Protocol
SlippageModelUnion = (
    FixedBpsSlippageModel
    | LinearImpactSlippageModel
    | SquareRootImpactSlippageModel
    | TieredImpactSlippageModel
)


_SLIPPAGE_MODELS: dict[str, type[SlippageModelUnion]] = {
    "linear_impact": LinearImpactSlippageModel,
    "square_root_impact": SquareRootImpactSlippageModel,
    "tiered_impact": TieredImpactSlippageModel,
}


def _warn_low_slippage(bps: float) -> None:
    if bps < SLIPPAGE_BPS_MIN_REALISTIC:
        logger.warning(
            "⚠️ 滑点 %.1f bps 低于最低合理值 %.1f bps",
            bps,
            SLIPPAGE_BPS_MIN_REALISTIC,
        )


def create_slippage_model(
    model_name: str = "fixed",
    *,
    slippage_bps: float = 0.0,
    slippage_params: dict | None = None,
    opening_volume_fraction: float = 1.0,
) -> SlippageModelUnion:
    """Create a slippage model from config-like primitives.

    This is the shared factory for backtest and paper/live paths.
    """
    name = model_name.lower().strip()
    params = dict(slippage_params or {})

    if name in ("fixed", "fixed_bps"):
        if params:
            logger.warning("fixed 滑点模型忽略额外参数: %s", params)
        _warn_low_slippage(slippage_bps)
        return FixedBpsSlippageModel(bps=slippage_bps)

    model_cls = _SLIPPAGE_MODELS.get(name)
    if model_cls is None:
        supported = ", ".join(["fixed", *sorted(_SLIPPAGE_MODELS.keys())])
        raise ValueError(f"不支持的滑点模型: '{model_name}'。支持: {supported}")

    if name == "linear_impact":
        params.setdefault("base_bps", slippage_bps)
    if opening_volume_fraction < 1.0 and "opening_volume_fraction" not in params:
        params["opening_volume_fraction"] = opening_volume_fraction

    base = float(params.get("base_bps", params.get("fallback_bps", slippage_bps)))
    _warn_low_slippage(base)
    logger.info("使用滑点模型: %s(%s)", name, params)
    return model_cls(**params)
