"""CB 多维四因子模型策略

基于学术研究 (JacobZhangIV 2024) 的四层因子模型：

**因子维度**（与传统双低/三低正交的新信息源）：

1. **宏观-利率因子** (macro_rate):
   10Y 国债收益率变动代理 → 利率下行利好 CB（债底抬升 + 转股期权价值增加）
   实现：用正股指数（如沪深300）近期动量作为宏观景气代理

2. **微观-正股动量** (micro_momentum):
   正股 20 日动量 → 短期趋势延续（Jegadeesh & Titman 1993）
   CB 与正股高度联动 → 正股动量可传导至 CB

3. **流动性-换手率** (liquidity_turnover):
   CB 换手率 → 流动性溢价因子
   高换手率 → 关注度高 → 短期可能超额（注意力效应，Barber & Odean 2008）

4. **价值-低估** (value_underestimate):
   综合 CB 本体低估信号：低价 + 低溢价 + 到期收益（YTM proxy）
   多维度价值因子比单一双低更鲁棒

**与现有策略正交性分析**：
- triple_low: 本质是价值因子（价格+溢价率+YTM）→ 维度 4 有重叠
- sq_v4b: 正股基本面因子 → 维度 2 部分重叠（动量 vs ROE）
- credit_spread: 债性因子 → 维度 1 部分相关
- 独特维度：流动性换手率 + 宏观代理 = 新信息源
- 预期相关性：0.4-0.6（适度互补，非完全正交）
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
    days_since_listed,
)
from aipha.strategy.cb_ultra import _rank_scores
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    from datetime import date

    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class FourFactorCBConfig(CBRotationConfig):
    """CB 四因子模型策略配置。

    Attributes:
        weight_macro: 宏观-利率因子权重
        weight_momentum: 微观-正股动量因子权重
        weight_liquidity: 流动性-换手率因子权重
        weight_value: 价值-低估因子权重
        momentum_lookback: 动量回看天数
        volume_lookback: 换手率回看天数
        max_premium: 溢价率过滤上限 (%)
        use_age_filter: 是否启用次新过滤
        min_age_days: 最小上市天数
    """

    top_k: int = 20
    rebalance_days: int = 5
    max_price: float = 150.0
    max_issue_size: float = float("inf")
    min_turnover: float = 20.0
    min_days_to_maturity: int = 30
    stop_loss_pct: float = 0.25
    lot_size: int = 10

    # --- 过滤 ---
    use_age_filter: bool = True
    min_age_days: int = 3
    max_premium: float = 80.0

    # --- 4 因子权重 ---
    weight_macro: float = 0.15       # 宏观景气代理
    weight_momentum: float = 0.25    # 正股动量
    weight_liquidity: float = 0.20   # CB 换手率
    weight_value: float = 0.40       # CB 价值低估

    # --- 子因子参数 ---
    momentum_lookback: int = 20
    volume_lookback: int = 20

    # --- 价值子因子内部权重 ---
    value_price_w: float = 0.40      # 价格在价值因子中的占比
    value_premium_w: float = 0.40    # 溢价率在价值因子中的占比
    value_size_w: float = 0.20       # 规模在价值因子中的占比


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------

@StrategyRegistry().register("cb_four_factor")
class CBFourFactorStrategy(CBRotationStrategy):
    """CB 多维四因子模型策略。

    四因子组合评分：
      score = w_macro * rank(macro_proxy, ↓)
            + w_momentum * rank(stk_momentum_20d, ↓)
            + w_liquidity * rank(turnover_ratio, ↓)
            + w_value * composite_value_rank

    其中 composite_value_rank = sub_w_price * rank(price, ↑)
                               + sub_w_premium * rank(premium, ↑)
                               + sub_w_size * rank(issue_size, ↑)
    """

    name = "cb_four_factor"

    def __init__(
        self,
        config: FourFactorCBConfig | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self._config = config or FourFactorCBConfig(**kwargs)
        # 正股价格历史（用于计算动量和宏观代理）
        self._stk_price_history: dict[str, deque] = {}
        # CB 成交额历史（用于计算换手率趋势）
        self._cb_amount_history: dict[str, deque] = {}
        # 市场整体动量代理（所有 CB 价值的等权平均变动）
        self._market_values: deque = deque(maxlen=60)

    @property
    def config(self) -> FourFactorCBConfig:
        return self._config

    def _on_start_extra(self) -> None:
        self._stk_price_history.clear()
        self._cb_amount_history.clear()
        self._market_values.clear()

    def _on_bar_update_state(
        self, bars: dict[str, Bar], context: StrategyContext,
    ) -> None:
        """更新正股价格历史 + CB 成交额历史 + 市场代理。"""
        max_hist = max(
            self._config.momentum_lookback,
            self._config.volume_lookback,
        ) + 10

        market_sum = 0.0
        market_cnt = 0

        for symbol, bar in bars.items():
            meta = bar.metadata or {}

            # 正股转股价值 → 作为正股价格代理
            cb_value = meta.get("cb_value")
            stk_code = meta.get("stk_code", "")

            if cb_value and cb_value > 0 and stk_code:
                if stk_code not in self._stk_price_history:
                    self._stk_price_history[stk_code] = deque(maxlen=max_hist)
                self._stk_price_history[stk_code].append(float(cb_value))
                market_sum += float(cb_value)
                market_cnt += 1

            # CB 成交额
            amount = meta.get("amount")
            if amount and amount > 0:
                if symbol not in self._cb_amount_history:
                    self._cb_amount_history[symbol] = deque(maxlen=max_hist)
                self._cb_amount_history[symbol].append(float(amount))

        # 更新市场平均 CB 价值（宏观代理）
        if market_cnt > 0:
            self._market_values.append(market_sum / market_cnt)

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        cfg = self._config

        if candidate.cb_over_rate is not None:
            if candidate.cb_over_rate > cfg.max_premium:
                return False

        if cfg.use_age_filter and candidate.list_date:
            age = days_since_listed(candidate.list_date, context.current_time)
            if age < cfg.min_age_days:
                return False

        return True

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """四因子综合评分。"""
        cfg = self._config
        if not candidates:
            return []

        n = len(candidates)
        if n == 1:
            return [candidates[0].symbol]

        # ----- 因子 1：宏观景气代理 -----
        macro_scores = self._compute_macro_factor()

        # ----- 因子 2：正股动量 -----
        momentums: list[float] = []
        for c in candidates:
            stk_code = (c.metadata or {}).get("stk_code", "")
            mom = self._compute_momentum(stk_code, cfg.momentum_lookback)
            momentums.append(mom)

        # ----- 因子 3：流动性换手率 -----
        turnovers: list[float] = []
        for c in candidates:
            turnover = self._compute_turnover_score(c.symbol, c.amount)
            turnovers.append(turnover)

        # ----- 因子 4：价值低估 -----
        prices = [c.close for c in candidates]
        premiums = [
            c.cb_over_rate if c.cb_over_rate is not None else 50.0
            for c in candidates
        ]
        sizes = [c.issue_size if c.issue_size else 50000.0 for c in candidates]

        rank_price_v = _rank_scores(prices, ascending=True)
        rank_prem_v = _rank_scores(premiums, ascending=True)
        rank_size_v = _rank_scores(sizes, ascending=True)

        # 价值子因子合成
        total_sub = cfg.value_price_w + cfg.value_premium_w + cfg.value_size_w
        if total_sub <= 0:
            total_sub = 1.0
        sub_p = cfg.value_price_w / total_sub
        sub_m = cfg.value_premium_w / total_sub
        sub_s = cfg.value_size_w / total_sub

        value_composite = [
            sub_p * rank_price_v[i] + sub_m * rank_prem_v[i] + sub_s * rank_size_v[i]
            for i in range(n)
        ]

        # ----- 排名归一化各因子 -----
        rank_macro = [macro_scores] * n  # 宏观因子是市场级别，所有候选相同
        rank_momentum = _rank_scores(momentums, ascending=False)
        rank_liquidity = _rank_scores(turnovers, ascending=False)
        rank_value = _rank_scores(value_composite, ascending=True)  # 低复合值好

        # ----- 权重归一化 -----
        total_w = cfg.weight_macro + cfg.weight_momentum + cfg.weight_liquidity + cfg.weight_value
        if total_w <= 0:
            total_w = 1.0

        w_mac = cfg.weight_macro / total_w
        w_mom = cfg.weight_momentum / total_w
        w_liq = cfg.weight_liquidity / total_w
        w_val = cfg.weight_value / total_w

        # ----- 综合评分 -----
        scored: list[tuple[str, float]] = []
        for i, c in enumerate(candidates):
            # 宏观因子：相同值 → 不影响排序，但在多期比较意义大
            score = (
                w_mac * rank_macro[i]
                + w_mom * rank_momentum[i]
                + w_liq * rank_liquidity[i]
                + w_val * rank_value[i]
            )
            scored.append((c.symbol, score))

        scored.sort(key=lambda x: x[1])
        return [s for s, _ in scored[: cfg.top_k]]

    # -----------------------------------------------------------------------
    # 因子计算
    # -----------------------------------------------------------------------

    def _compute_macro_factor(self) -> float:
        """宏观景气代理：市场平均 CB 价值的动量。

        逻辑：市场整体转股价值上涨 → 宏观景气向好 → 利好 CB。
        返回标准化分数 [0, 1]，1 = 宏观良好。
        """
        if len(self._market_values) < 5:
            return 0.5  # 中性

        values = list(self._market_values)
        recent = values[-5:]
        older = values[:-5] if len(values) > 5 else values[:1]

        recent_avg = sum(recent) / len(recent)
        older_avg = sum(older) / len(older)

        if older_avg <= 0:
            return 0.5

        momentum = recent_avg / older_avg - 1.0

        # sigmoid 归一化到 [0, 1]
        score = 1.0 / (1.0 + math.exp(-momentum * 50))
        return score

    def _compute_momentum(self, stk_code: str, lookback: int) -> float:
        """正股动量：lookback 天价格变化率。"""
        if not stk_code:
            return 0.0
        hist = self._stk_price_history.get(stk_code)
        if hist is None or len(hist) < lookback:
            return 0.0

        prices = list(hist)
        if prices[-lookback] <= 0:
            return 0.0

        return prices[-1] / prices[-lookback] - 1.0

    def _compute_turnover_score(self, symbol: str, current_amount: float | None) -> float:
        """CB 换手率分数：近期成交额趋势。

        使用近 5 日平均 / 近 20 日平均的比值作为换手率加速度。
        """
        hist = self._cb_amount_history.get(symbol)
        if hist is None or len(hist) < 5:
            return current_amount if current_amount else 0.0

        amounts = list(hist)
        short_avg = sum(amounts[-5:]) / 5
        long_avg = sum(amounts) / len(amounts)

        if long_avg <= 0:
            return short_avg

        # 换手率加速度：短期 / 长期
        return short_avg / long_avg
