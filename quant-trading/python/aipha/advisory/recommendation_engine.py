"""荐股引擎 — 聚合多源信号生成推荐列表。

三档时间维度: SHORT(1-5d) / MEDIUM(1-3m) / LONG(3m+),
各维度使用不同权重（见 §4.3 信号聚合权重体系）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from aipha.advisory.aggregator import AggregatedSignal, SignalAggregator, SignalWeight
from aipha.advisory.models import (
    ActionType,
    Recommendation,
    ScreenerResult,
    TimeHorizon,
)

logger = logging.getLogger(__name__)

# 按 §4.3 短线/中线/长线权重差异表
HORIZON_WEIGHTS: dict[TimeHorizon, SignalWeight] = {
    TimeHorizon.SHORT: SignalWeight(quant=0.25, technical=0.25, intel=0.35, target_price=0.15),
    TimeHorizon.MEDIUM: SignalWeight(quant=0.35, technical=0.15, intel=0.30, target_price=0.20),
    TimeHorizon.LONG: SignalWeight(quant=0.40, technical=0.05, intel=0.20, target_price=0.35),
}

# 建议有效期 — 信号在多久内可用于开仓（不是持有期）
EXPIRY_DAYS: dict[TimeHorizon, int] = {
    TimeHorizon.SHORT: 3,
    TimeHorizon.MEDIUM: 30,
    TimeHorizon.LONG: 90,
}

# 计划持有期（交易日）— 对应 TimeHorizon 定义的 1-5d / 1-3m / 3m+
HOLDING_DAYS: dict[TimeHorizon, int] = {
    TimeHorizon.SHORT: 5,
    TimeHorizon.MEDIUM: 60,
    TimeHorizon.LONG: 180,
}

# 止损距离（相对信号价）。短线止损必须紧于单日跌停幅度，否则止损形同虚设
STOP_LOSS_PCT: dict[TimeHorizon, float] = {
    TimeHorizon.SHORT: 0.05,
    TimeHorizon.MEDIUM: 0.10,
    TimeHorizon.LONG: 0.15,
}

# 动作强弱阶梯 — regime 降级沿此顺序下调
ACTION_LADDER: list[ActionType] = [
    ActionType.STRONG_BUY,
    ActionType.BUY,
    ActionType.HOLD,
    ActionType.REDUCE,
    ActionType.SELL,
]


def normalize_regime(regime: Any) -> str | None:
    """把 regime 归一为大写字符串。

    兼容 ``portfolio.regime_weight.MarketRegime``（str 枚举）与裸字符串，
    因此 advisory 不必依赖 portfolio 层。
    """
    if regime is None:
        return None
    value = getattr(regime, "value", regime)
    text = str(value).strip().upper()
    return text or None


@dataclass
class RegimeGateConfig:
    """市场状态门控配置。

    个股信号的胜率随大盘状态系统性变化: 同一套因子在 BEAR 里的
    命中率显著低于 BULL。静态权重对牛熊一视同仁，是「买了就跌」
    的系统性来源之一。

    Attributes:
        confidence_multiplier: 各状态下的置信度乘数。压低后若跌破
            ``min_confidence``，该推荐直接被过滤掉。
        downgrade_steps: 沿 ACTION_LADDER 下调的档数
            （1 档 = STRONG_BUY→BUY / BUY→HOLD）
        chase_pct_multiplier: 追高上限乘数。下跌趋势里追高更危险，
            默认在 BEAR 收紧一半。
    """

    enabled: bool = True
    confidence_multiplier: dict[str, float] = field(
        default_factory=lambda: {"BULL": 1.0, "NEUTRAL": 0.9, "BEAR": 0.7},
    )
    downgrade_steps: dict[str, int] = field(
        default_factory=lambda: {"BULL": 0, "NEUTRAL": 0, "BEAR": 1},
    )
    chase_pct_multiplier: dict[str, float] = field(
        default_factory=lambda: {"BULL": 1.0, "NEUTRAL": 1.0, "BEAR": 0.5},
    )


@dataclass
class RecommendationConfig:
    top_n: int = 10
    min_confidence: float = 0.4
    min_source_count: int = 2
    horizons: list[TimeHorizon] = field(
        default_factory=lambda: [TimeHorizon.SHORT, TimeHorizon.MEDIUM],
    )
    # 追高上限: 现价高于信号价超过此比例即不再买入。
    # 与 backtest.engine 的 retry_max_chase_pct 同义，但作用于推荐/下单前，
    # 而非仅作用于未成交订单的重试决策。
    max_chase_pct: float = 0.03
    # 无外部目标价时，按此盈亏比从止损距离反推目标价
    reward_risk_ratio: float = 2.0
    stop_loss_pct: dict[TimeHorizon, float] = field(
        default_factory=lambda: dict(STOP_LOSS_PCT),
    )
    holding_days: dict[TimeHorizon, int] = field(
        default_factory=lambda: dict(HOLDING_DAYS),
    )
    # 盈亏比低于此值的买入建议降级为 HOLD（0=不检查）
    min_reward_risk: float = 0.0
    # 市场状态门控 — 仅在 generate(regime=...) 传入状态时生效
    regime_gate: RegimeGateConfig = field(default_factory=RegimeGateConfig)


class RecommendationEngine:
    """荐股引擎 — 核心入口。"""

    def __init__(self, config: RecommendationConfig | None = None) -> None:
        self._config = config or RecommendationConfig()
        self._aggregators: dict[TimeHorizon, SignalAggregator] = {
            h: SignalAggregator(weights=HORIZON_WEIGHTS.get(h))
            for h in TimeHorizon
        }

    def generate(
        self,
        screener_results: dict[str, list[ScreenerResult]],
        intel_scores: dict[str, float] | None = None,
        target_prices: dict[str, float] | None = None,
        current_prices: dict[str, float] | None = None,
        regime: Any = None,
    ) -> dict[TimeHorizon, list[Recommendation]]:
        """为每个时间维度生成推荐列表。

        Args:
            regime: 当前市场状态（``MarketRegime`` 或 "BULL"/"NEUTRAL"/"BEAR"）。
                传入时按 ``RegimeGateConfig`` 压低置信度、下调动作强度、
                收紧追高上限；不传则完全不介入（保持原有行为）。
        """
        regime_key = normalize_regime(regime)
        result: dict[TimeHorizon, list[Recommendation]] = {}
        for horizon in self._config.horizons:
            agg = self._aggregators[horizon]
            signals = agg.aggregate(
                screener_results, intel_scores, target_prices, current_prices,
            )
            recs = self._signals_to_recs(
                signals, horizon, current_prices, target_prices, regime_key,
            )
            result[horizon] = recs
            logger.info(
                "horizon=%s regime=%s generated %d recommendations",
                horizon.value, regime_key or "n/a", len(recs),
            )
        return result

    def revalidate(
        self,
        recs: list[Recommendation],
        live_prices: dict[str, float],
        now: datetime | None = None,
    ) -> list[Recommendation]:
        """用最新价复核推荐 —— 下单前的最后一道防追高闸门。

        推荐按信号日收盘价打分，但实际成交发生在之后（次日开盘或盘中）。
        这段间隔里价格可能已经跑掉，此时买入等于追在高点。
        触发追高 / 开仓前已跌破止损 / 信号过期的建议降级为 HOLD，
        并把原因写入 reasons 与 metadata["entry_blocked"]。

        原推荐对象不被修改，返回新列表。
        """
        now = now or datetime.now()
        out: list[Recommendation] = []
        for rec in recs:
            price = live_prices.get(rec.ts_code)
            if not rec.is_buy or price is None:
                out.append(rec)
                continue
            reason = rec.entry_block_reason(float(price), now=now)
            if reason is None:
                out.append(rec)
                continue
            logger.info(
                "推荐降级 %s: %s → hold — %s",
                rec.ts_code, rec.action.value, reason,
            )
            out.append(replace(
                rec,
                action=ActionType.HOLD,
                reasons=[*rec.reasons, f"entry_blocked: {reason}"],
                metadata={
                    **rec.metadata,
                    "entry_blocked": reason,
                    "original_action": rec.action.value,
                    "revalidated_price": float(price),
                },
            ))
        return out

    def _signals_to_recs(
        self,
        signals: list[AggregatedSignal],
        horizon: TimeHorizon,
        current_prices: dict[str, float] | None = None,
        target_prices: dict[str, float] | None = None,
        regime_key: str | None = None,
    ) -> list[Recommendation]:
        cfg = self._config
        current_prices = current_prices or {}
        target_prices = target_prices or {}
        now = datetime.now()
        expiry = now + timedelta(days=EXPIRY_DAYS.get(horizon, 7))
        gate_on = cfg.regime_gate.enabled and regime_key is not None
        recs: list[Recommendation] = []
        for sig in signals[: cfg.top_n]:
            if sig.source_count < cfg.min_source_count:
                continue
            action = self._score_to_action(sig.composite_score)
            conf_val = min(sig.composite_score / 100, 1.0)

            metadata: dict[str, Any] = {}
            if gate_on:
                raw_conf = conf_val
                conf_val *= cfg.regime_gate.confidence_multiplier.get(
                    regime_key, 1.0,  # type: ignore[arg-type]
                )
                action = self._downgrade_for_regime(action, regime_key)
                metadata = {
                    "regime": regime_key,
                    "raw_confidence": round(raw_conf, 4),
                }

            # 门控压低后再卡阈值 — BEAR 里的边缘信号就此出局
            if conf_val < cfg.min_confidence:
                continue

            signal_price = self._as_price(current_prices.get(sig.ts_code))
            max_entry, stop_loss, target = self._build_price_plan(
                horizon=horizon,
                signal_price=signal_price,
                target_price=self._as_price(target_prices.get(sig.ts_code)),
                regime_key=regime_key if gate_on else None,
            )
            rec = Recommendation(
                ts_code=sig.ts_code,
                name=sig.name,
                action=action,
                horizon=horizon,
                signal_price=signal_price,
                max_entry_price=max_entry,
                target_price=target,
                stop_loss=stop_loss,
                holding_days=cfg.holding_days.get(horizon),
                confidence=conf_val,
                reasons=list(sig.factors.keys()),
                signal_sources=dict(sig.source_scores),  # 复制，避免与信号共享可变字典
                created_at=now,
                expires_at=expiry,
                metadata=metadata,
            )
            recs.append(self._apply_reward_risk_floor(rec))
        return recs

    def _downgrade_for_regime(
        self,
        action: ActionType,
        regime_key: str | None,
    ) -> ActionType:
        """按市场状态沿 ACTION_LADDER 下调动作强度。

        只下调不上调 —— 门控是风险闸门，不该在牛市里放大信号。
        """
        steps = self._config.regime_gate.downgrade_steps.get(regime_key or "", 0)
        if steps <= 0 or action not in ACTION_LADDER:
            return action
        idx = min(ACTION_LADDER.index(action) + steps, ACTION_LADDER.index(ActionType.HOLD))
        return ACTION_LADDER[idx]

    def _build_price_plan(
        self,
        horizon: TimeHorizon,
        signal_price: float | None,
        target_price: float | None,
        regime_key: str | None = None,
    ) -> tuple[float | None, float | None, float | None]:
        """推算 (入场上限, 止损价, 目标价)。

        无有效信号价时无法给出价位计划，返回 (None, None, 原目标价)。
        外部目标价若不高于信号价则不可用（无上行空间），改用
        ``reward_risk_ratio`` 从止损距离反推。

        ``regime_key`` 非空时按 ``chase_pct_multiplier`` 收紧追高容忍度；
        止损与目标价不随状态变动 —— 那由 horizon 决定，与大盘无关。
        """
        cfg = self._config
        if signal_price is None:
            return None, None, target_price

        chase_pct = cfg.max_chase_pct
        if regime_key is not None:
            chase_pct *= cfg.regime_gate.chase_pct_multiplier.get(regime_key, 1.0)

        stop_pct = cfg.stop_loss_pct.get(horizon, 0.10)
        stop_loss = round(signal_price * (1 - stop_pct), 3)
        max_entry = round(signal_price * (1 + chase_pct), 3)

        if target_price is None or target_price <= signal_price:
            risk = signal_price - stop_loss
            target_price = round(
                signal_price + risk * cfg.reward_risk_ratio, 3,
            )
        return max_entry, stop_loss, target_price

    def _apply_reward_risk_floor(self, rec: Recommendation) -> Recommendation:
        """盈亏比不达标的买入建议降级为 HOLD。"""
        floor = self._config.min_reward_risk
        if floor <= 0 or not rec.is_buy:
            return rec
        rr = rec.reward_risk_ratio
        if rr is None or rr >= floor:
            return rec
        reason = f"盈亏比 {rr:.2f} < 下限 {floor:.2f}"
        logger.info("推荐降级 %s: %s → hold — %s", rec.ts_code, rec.action.value, reason)
        return replace(
            rec,
            action=ActionType.HOLD,
            reasons=[*rec.reasons, f"entry_blocked: {reason}"],
            metadata={
                **rec.metadata,
                "entry_blocked": reason,
                "original_action": rec.action.value,
            },
        )

    @staticmethod
    def _as_price(value: Any) -> float | None:
        """把外部传入的价格归一为正浮点数，无效值返回 None。"""
        if value is None:
            return None
        try:
            price = float(value)
        except (TypeError, ValueError):
            return None
        if price <= 0 or price != price:  # 非正 / NaN
            return None
        return price

    @staticmethod
    def _score_to_action(score: float) -> ActionType:
        if score >= 80:
            return ActionType.STRONG_BUY
        if score >= 60:
            return ActionType.BUY
        if score >= 40:
            return ActionType.HOLD
        if score >= 25:
            return ActionType.REDUCE
        return ActionType.SELL
