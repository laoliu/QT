"""全三层融合可转债策略 (cb_multi_layer)

将 Layer 1 (CB 本体) + Layer 2 (正股传导) + Layer 3 (条款博弈) 全部因子
融合为一个策略，是三层因子架构的最终形态。

因子矩阵（8+1 因子，3 层架构）：

Layer 1 — CB 本体因子：
  1. **价格因子** (price):       低价 → 债底保护          (权重 0.15)
  2. **溢价率因子** (premium):    低溢价 → 股性弹性        (权重 0.12)

Layer 2 — 正股传导因子：
  3. **正股 ROE** (stock_roe):    高 ROE → 质量正股        (权重 0.15)
  4. **正股 EP**  (stock_ep):     高 EP → 正股低估          (权重 0.10)
  5. **正股波动率** (stock_vol):   高波动 → 期权价值大      (权重 0.08)
  6. **Gamma 代理** (gamma):      高 Gamma → 凸性强        (权重 0.08)

Layer 3 — 条款博弈因子：
  7. **回售接近度** (putback):     高接近度 → 下修概率高    (权重 0.15)
  8. **到期催化** (maturity_cat):  1-3 年高峰 → 催化事件多  (权重 0.10)

Weak：
  9. **发行规模** (issue_size):    小规模 → 定价偏差        (权重 0.07)

总权重: 0.15+0.12+0.15+0.10+0.08+0.08+0.15+0.10+0.07 = 1.00

设计原则：
- 所有因子均有先验经济学解释（§1.4 原则 A）
- 权重分配参考各因子 IC 和理论信息含量
- 正股数据缺失时因子值降级为中性，不影响其他因子
- 条款数据缺失时回售因子使用 cb_value 代理
- issue_size 权重 ≤10%（弱因子约束）

与子策略对比：
- cb_stock_quality:    L1+L2 (5+1 因子, 无条款/Gamma)
- cb_gamma_convexity:  L1+Delta (4+1 因子, 无 ROE/EP/条款)
- cb_clause_play:      L1+L3 (4+1 因子, 无正股)
- **cb_multi_layer**:  L1+L2+L3 (8+1 因子, 全覆盖) ← 本策略
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from aipha.factors.cb_delta_zone import compute_delta_proxy, compute_gamma_proxy
from aipha.factors.enhanced_putback import compute_putback_from_cb
from aipha.factors.maturity_catalyst import compute_maturity_catalyst
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
    days_since_listed,
    days_to_maturity,
)
from aipha.strategy.cb_ultra import _rank_scores
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class MultiLayerCBConfig(CBRotationConfig):
    """全三层融合可转债策略配置。

    8+1 因子权重（默认合计 1.0）：
      Layer 1: price(0.15) + premium(0.12)
      Layer 2: stock_roe(0.15) + stock_ep(0.10) + stock_vol(0.08) + gamma(0.08)
      Layer 3: putback(0.15) + maturity_catalyst(0.10)
      Weak:    issue_size(0.07)
    """

    top_k: int = 15
    rebalance_days: int = 5
    max_price: float = 140.0
    max_issue_size: float = float("inf")
    min_turnover: float = 20.0
    min_days_to_maturity: int = 30
    stop_loss_pct: float = 0.22
    lot_size: int = 10

    # --- 过滤参数 ---
    use_age_filter: bool = True
    min_age_days: int = 10
    max_premium: float = 70.0

    # --- Layer 1 权重 ---
    weight_price: float = 0.15
    weight_premium: float = 0.12

    # --- Layer 2 权重 ---
    weight_stock_roe: float = 0.15
    weight_stock_ep: float = 0.10
    weight_stock_vol: float = 0.08
    weight_gamma: float = 0.08

    # --- Layer 3 权重 ---
    weight_putback: float = 0.15
    weight_maturity: float = 0.10

    # --- Weak ---
    weight_issue_size: float = 0.07

    # --- 正股数据参数 ---
    vol_lookback: int = 60
    use_stock_bridge: bool = True
    default_roe: float = 0.0
    default_ep: float = 0.0

    # --- 条款参数 ---
    putback_trigger_ratio: float = 0.70


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------

@StrategyRegistry().register("cb_multi_layer")
class CBMultiLayerStrategy(CBRotationStrategy):
    """全三层融合可转债策略。

    8+1 因子排名加权选股，首次融合全部三个因子层。

    score = w_price      * rank(close, ↑)          # L1: 低价好
          + w_premium    * rank(cb_over_rate, ↑)    # L1: 低溢价好
          + w_roe        * rank(stock_roe, ↓)       # L2: 高 ROE 好
          + w_ep         * rank(stock_ep, ↓)         # L2: 高 EP 好
          + w_vol        * rank(stock_vol, ↓)        # L2: 高波动好
          + w_gamma      * rank(gamma_proxy, ↓)      # L2: 高 Gamma 好
          + w_putback    * rank(putback_prox, ↓)     # L3: 高接近度好
          + w_maturity   * rank(maturity_cat, ↓)     # L3: 高催化好
          + w_size       * rank(issue_size, ↑)       # Weak: 小规模好
    """

    name = "cb_multi_layer"

    def __init__(
        self,
        config: MultiLayerCBConfig | None = None,
        stock_bridge: Any | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self._config = config or MultiLayerCBConfig(**kwargs)
        self._bridge = stock_bridge
        self._stk_price_history: dict[str, deque] = {}
        self._current_fundamentals: dict[str, Any] = {}

    @property
    def config(self) -> MultiLayerCBConfig:
        return self._config

    # --- 生命周期钩子 ---

    def _on_start_extra(self) -> None:
        """重置状态缓存；延迟初始化 CBStockBridge。"""
        self._stk_price_history.clear()
        self._current_fundamentals.clear()
        if self._bridge is None and self._config.use_stock_bridge:
            try:
                from aipha.data.cb_stock_bridge import CBStockBridge
                self._bridge = CBStockBridge()
            except Exception as e:  # pragma: no cover
                logger.warning("CBStockBridge 初始化失败: %s，L2 因子降级", e)

    def _on_bar_update_state(
        self, bars: dict[str, Bar], context: StrategyContext,
    ) -> None:
        """更新正股价格历史 + 获取正股基本面。

        - 正股价格历史通过 cb_value 代理（后续可改为真实正股价）
        - 正股基本面通过 CBStockBridge PIT 合规查询
        """
        self._current_fundamentals.clear()
        query_date = (
            context.current_time.date()
            if hasattr(context.current_time, "date")
            else context.current_time
        )

        for _symbol, bar in bars.items():
            meta = bar.metadata or {}
            stk_code = meta.get("stk_code")
            if not stk_code:
                continue

            # 正股价格历史（通过 cb_value 代理）
            cb_value = meta.get("cb_value")
            if cb_value and cb_value > 0:
                if stk_code not in self._stk_price_history:
                    self._stk_price_history[stk_code] = deque(
                        maxlen=self._config.vol_lookback + 5,
                    )
                self._stk_price_history[stk_code].append(float(cb_value))

            # 正股基本面
            if (
                self._bridge is not None
                and stk_code not in self._current_fundamentals
            ):
                try:
                    fund = self._bridge.get_fundamentals(stk_code, query_date)
                    self._current_fundamentals[stk_code] = fund
                except Exception:
                    pass

    # --- 过滤 ---

    def _extra_filter(
        self, candidate: CandidateInfo, context: StrategyContext,
    ) -> bool:
        """溢价率 + 次新过滤。

        注意：不做 Delta 区间硬性过滤（那是 gamma_convexity 专用），
        multi_layer 通过 Gamma 因子权重柔性倾斜即可。
        """
        cfg = self._config

        # 溢价率上限
        if candidate.cb_over_rate is not None:
            if candidate.cb_over_rate > cfg.max_premium:
                return False

        # 次新排除
        if cfg.use_age_filter and candidate.list_date:
            age = days_since_listed(candidate.list_date, context.current_time)
            if age < cfg.min_age_days:
                return False

        return True

    # --- 评分（核心） ---

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """8+1 因子排名加权评分 — 三层全融合。

        Returns:
            按综合分升序排列的 top_k 标的 symbol 列表（最优在前）。
        """
        cfg = self._config
        if not candidates:
            return []

        n = len(candidates)
        if n == 1:
            return [candidates[0].symbol]

        now = datetime(
            context.current_time.year,
            context.current_time.month,
            context.current_time.day,
        )

        # ===== 提取 9 个因子原始值 =====

        # Layer 1: CB 本体
        prices = [c.close for c in candidates]
        premiums = [
            c.cb_over_rate if c.cb_over_rate is not None else 50.0
            for c in candidates
        ]

        # Layer 2: 正股传导
        roes: list[float] = []
        eps_vals: list[float] = []
        vols: list[float] = []
        gammas: list[float] = []

        for c in candidates:
            stk_code = (c.metadata or {}).get("stk_code", "")

            # ROE
            fund = self._current_fundamentals.get(stk_code)
            if fund is not None and fund.roe is not None:
                roes.append(fund.roe)
            else:
                roes.append(cfg.default_roe)

            # EP
            if fund is not None and fund.ep is not None:
                eps_vals.append(fund.ep)
            else:
                eps_vals.append(cfg.default_ep)

            # 正股波动率
            vol = self._compute_stk_vol(stk_code)
            vols.append(vol if vol is not None else 0.0)

            # Gamma 代理
            gamma = compute_gamma_proxy(c.cb_value)
            gammas.append(gamma if gamma is not None else 0.0)

        # Layer 3: 条款博弈
        putbacks: list[float] = []
        catalysts: list[float] = []

        for c in candidates:
            # 回售接近度
            pb = compute_putback_from_cb(
                c.cb_value, trigger_ratio=cfg.putback_trigger_ratio,
            )
            putbacks.append(pb if pb is not None else -1.0)

            # 到期催化分数
            dtm = (
                days_to_maturity(c.maturity_date, now)
                if c.maturity_date
                else 9999
            )
            catalysts.append(compute_maturity_catalyst(dtm))

        # Weak
        sizes = [
            c.issue_size if c.issue_size < float("inf") else 1e9
            for c in candidates
        ]

        # ===== 排名归一化 =====
        rank_price = _rank_scores(prices, ascending=True)         # 低价好
        rank_premium = _rank_scores(premiums, ascending=True)     # 低溢价好
        rank_roe = _rank_scores(roes, ascending=False)            # 高 ROE 好
        rank_ep = _rank_scores(eps_vals, ascending=False)         # 高 EP 好
        rank_vol = _rank_scores(vols, ascending=False)            # 高波动好
        rank_gamma = _rank_scores(gammas, ascending=False)        # 高 Gamma 好
        rank_putback = _rank_scores(putbacks, ascending=False)    # 高接近度好
        rank_catalyst = _rank_scores(catalysts, ascending=False)  # 高催化好
        rank_size = _rank_scores(sizes, ascending=True)           # 小规模好

        # ===== 权重归一化 =====
        total_w = (
            cfg.weight_price + cfg.weight_premium
            + cfg.weight_stock_roe + cfg.weight_stock_ep
            + cfg.weight_stock_vol + cfg.weight_gamma
            + cfg.weight_putback + cfg.weight_maturity
            + cfg.weight_issue_size
        )
        if total_w <= 0:
            total_w = 1.0

        w_pr = cfg.weight_price / total_w
        w_pm = cfg.weight_premium / total_w
        w_roe = cfg.weight_stock_roe / total_w
        w_ep = cfg.weight_stock_ep / total_w
        w_vol = cfg.weight_stock_vol / total_w
        w_gm = cfg.weight_gamma / total_w
        w_pb = cfg.weight_putback / total_w
        w_mc = cfg.weight_maturity / total_w
        w_sz = cfg.weight_issue_size / total_w

        # ===== 综合打分 =====
        scored: list[tuple[float, str]] = []
        for i, c in enumerate(candidates):
            score = (
                w_pr * rank_price[i]
                + w_pm * rank_premium[i]
                + w_roe * rank_roe[i]
                + w_ep * rank_ep[i]
                + w_vol * rank_vol[i]
                + w_gm * rank_gamma[i]
                + w_pb * rank_putback[i]
                + w_mc * rank_catalyst[i]
                + w_sz * rank_size[i]
            )
            scored.append((score, c.symbol))

        scored.sort(key=lambda x: x[0])

        best = scored[: cfg.top_k]
        if best:
            logger.debug(
                "multi_layer best=%s score=%.4f, worst=%s score=%.4f (n=%d)",
                best[0][1], best[0][0],
                best[-1][1], best[-1][0],
                len(scored),
            )

        return [sym for _, sym in best]

    # --- 辅助方法 ---

    def _compute_stk_vol(self, stk_code: str) -> float | None:
        """计算正股波动率（年化）from cb_value 价格历史代理。"""
        if not stk_code:
            return None
        hist = self._stk_price_history.get(stk_code)
        if hist is None or len(hist) < 10:
            return None

        prices = list(hist)
        from aipha.data.cb_stock_bridge import CBStockBridge

        returns = []
        for i in range(1, len(prices)):
            if prices[i - 1] > 0:
                returns.append(prices[i] / prices[i - 1] - 1.0)

        return CBStockBridge.compute_volatility(returns)
