"""可转债负溢价/折价套利增强策略

Alpha 来源：当 cb_over_rate < 0 时，转债价格低于转股价值，
理论上存在套利空间（买入转债→转股→卖出正股）。虽然实际
套利存在摩擦（T+1 卖出正股、手续费等），但负溢价本身往往
是短期错误定价，有均值回归的倾向。

增强版特性（v2）：
1. **分层入场** — 根据折价深度分 3 档加仓
2. **成交量门槛** — 成交额须达到下限才入场（避免流动性陷阱）
3. **动态仓位** — 折价越深、成交量越大 → 仓位越大
4. **快速出场** — 溢价回归零轴即退出（T+0 特性允许当日进出）
5. **Delta 暴露** — 优先选择 Delta（cb_value/close）高的标的
6. **评级保护** — 可选过滤低评级转债（避免信用风险）

策略逻辑：
1. 筛选 cb_over_rate < entry_threshold 的转债
2. 按折价深度 × Delta 暴露 × 流动性 综合打分
3. 分档建仓：浅折价小仓位、深折价大仓位
4. 溢价回归至 exit_threshold 时即刻退出
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aipha.domain.models import Side
from aipha.strategy.base import OrderIntent, StrategyContext
from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
)
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


@dataclass
class NegativePremiumConfig(CBRotationConfig):
    """负溢价折价套利策略配置（增强版）"""

    top_k: int = 10
    rebalance_days: int = 1          # T+0 策略：每日检查
    entry_threshold: float = -2.0    # 入场阈值：溢价率 < -2%
    exit_threshold: float = 0.0      # 出场阈值：溢价率回归到 0%
    max_price: float = 200.0         # 负溢价转债可能价格较高
    stop_loss_pct: float = 0.10

    # 分层入场阈值（折价越深、仓位比例越大）
    tier1_threshold: float = -2.0    # 浅折价：-2% ~ -5%
    tier2_threshold: float = -5.0    # 中折价：-5% ~ -10%
    tier3_threshold: float = -10.0   # 深折价：< -10%

    # 分层仓位权重（不是百分比，是相对权重）
    tier1_weight: float = 1.0
    tier2_weight: float = 2.0
    tier3_weight: float = 3.0

    # 流动性下限（成交额，万元）
    min_arb_turnover: float = 500.0

    # Delta 暴露加分权重（cb_value/close 越高越好）
    delta_score_weight: float = 0.3


@StrategyRegistry().register("negative_premium")
class NegativePremiumStrategy(CBRotationStrategy):
    """负溢价折价套利增强策略（v2）

    买入溢价率为负（转债价格 < 转股价值）的转债，等待折价修复。
    增强版相比 v1 的改进：
    - 分层建仓：根据折价深度分配不同仓位权重
    - 流动性过滤：成交额不足的标的不入场
    - 综合打分：折价深度 × Delta 暴露 × 流动性
    - 每日检查（rebalance_days=1），捕捉 T+0 机会
    """

    name = "negative_premium"

    def __init__(self, config: NegativePremiumConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or NegativePremiumConfig(**kwargs)

    @property
    def config(self) -> NegativePremiumConfig:
        """策略配置。"""
        return self._config

    def _custom_exit_check(
        self, bars: dict[str, Bar], context: StrategyContext,
    ) -> list[OrderIntent]:
        """溢价回归至阈值时卖出（均值回归出场）。"""
        cfg = self._config
        intents: list[OrderIntent] = []
        for symbol, qty in list(context.positions.items()):
            if qty <= 0:
                continue
            bar = bars.get(symbol)
            if bar is None:
                continue
            meta = bar.metadata or {}
            premium = meta.get("cb_over_rate")
            if premium is not None and premium >= cfg.exit_threshold:
                intents.append(OrderIntent(
                    symbol=symbol, side=Side.SELL, qty=qty,
                    reason=f"溢价回归: {premium:.2f}% >= {cfg.exit_threshold}%",
                ))
        return intents

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        """仅保留负溢价 + 流动性达标的标的。"""
        cfg = self._config
        if candidate.cb_over_rate is None:
            return False
        if candidate.cb_over_rate >= cfg.entry_threshold:
            return False
        # 流动性门槛
        if candidate.amount < cfg.min_arb_turnover:
            return False
        return True

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """综合打分：折价深度 × Delta 暴露 × 流动性。

        打分公式：
        score = |cb_over_rate| + delta_weight * (cb_value/close - 1) * 100

        折价越深 + Delta 越高 → 分数越高 → 排名越前。
        """
        cfg = self._config
        scored: list[tuple[str, float]] = []

        for c in candidates:
            premium = c.cb_over_rate if c.cb_over_rate is not None else 0
            # 折价深度分（越负→绝对值越大→分越高）
            discount_score = abs(min(premium, 0))

            # Delta 暴露加分
            delta_bonus = 0.0
            if c.cb_value and c.close > 0:
                delta_ratio = c.cb_value / c.close
                delta_bonus = max(0, (delta_ratio - 1)) * 100 * cfg.delta_score_weight

            total = discount_score + delta_bonus
            scored.append((c.symbol, total))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [s for s, _ in scored[:cfg.top_k]]

    def _generate_rebalance(
        self,
        ranked_targets: list[str],
        context: StrategyContext,
        bars: dict[str, Bar],
    ) -> list[OrderIntent]:
        """分层建仓：卖出不在目标池的持仓 + 根据折价深度分配不同仓位权重。

        修复 P1-1: 原版覆写缺少卖出逻辑，持仓只增不减。
        现在先卖出不在 ranked_targets 的持仓，再按分层权重买入新标的。
        """
        cfg = self._config
        intents: list[OrderIntent] = []
        current_held = {s for s, q in context.positions.items() if q > 0}

        # ── 卖出：不在目标池的持仓（复用基类逻辑模式）──
        target_set = set(ranked_targets)
        to_sell = {s for s in current_held if s not in target_set}
        sell_value = 0.0
        for symbol in to_sell:
            qty = context.positions.get(symbol, 0)
            if qty > 0:
                bar = bars.get(symbol)
                if bar:
                    sell_value += qty * bar.close
                intents.append(OrderIntent(
                    symbol=symbol, side=Side.SELL, qty=qty,
                    reason="折价策略轮动卖出",
                ))

        # ── 买入：新进入目标池的标的 ──
        to_buy = [s for s in ranked_targets if s not in current_held]
        if not to_buy:
            return intents

        available = (context.cash + sell_value * 0.995) * 0.9

        # 计算各标的的分层仓位权重
        weights: list[tuple[str, float, float]] = []  # (symbol, weight, premium)
        for symbol in to_buy:
            bar = bars.get(symbol)
            if bar is None or bar.close <= 0:
                continue
            meta = bar.metadata or {}
            premium = meta.get("cb_over_rate", 0)

            if premium <= cfg.tier3_threshold:
                w = cfg.tier3_weight
            elif premium <= cfg.tier2_threshold:
                w = cfg.tier2_weight
            else:
                w = cfg.tier1_weight
            weights.append((symbol, w, premium))

        if not weights:
            return intents

        total_w = sum(w for _, w, _ in weights)

        for symbol, w, premium in weights:
            bar = bars[symbol]
            per_stock = available * (w / total_w)
            qty = int(per_stock / bar.close / cfg.lot_size) * cfg.lot_size
            if qty >= cfg.lot_size:
                tier_label = (
                    "深" if premium <= cfg.tier3_threshold
                    else "中" if premium <= cfg.tier2_threshold
                    else "浅"
                )
                intents.append(OrderIntent(
                    symbol=symbol, side=Side.BUY, qty=qty,
                    reason=f"折价套利({tier_label}): {premium:.2f}%",
                ))
        return intents
