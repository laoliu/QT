"""Delta 凸性可转债策略

选择平衡区（Delta 0.3-0.7）的可转债，最大化 Gamma 凸性收益。

核心理论（对齐 copilot-instructions.md §11.2 可转债部分）：
- **Gamma = dΔ/dS**，在 ATM 附近最大 → 正股每波动 1%，CB 涨幅 > 跌幅
- 持有高 Gamma CB = 做多波动率（期权卖方 pay Γ*σ²/2，买方 receive）
- 策略本质：**做多正股波动率 + 做多低溢价**

因子矩阵（4+1 因子）：
1. **Gamma 代理** (gamma_proxy): 越大越好 — ATM 区 Gamma 最大
2. **溢价率** (cb_over_rate): 低溢价 → 高弹性（权重 0.25）
3. **正股波动率** (stock_vol): 高波动 → Gamma 收益大（权重 0.25）
4. **价格因子** (close): 低价 → 债底保护（权重 0.15）
5. **发行规模** (issue_size): 弱因子（权重 ≤ 0.10）

选股逻辑：
1. 预过滤：仅保留 Delta 在 [delta_min, delta_max] 区间的 CB
2. 排名：gamma_proxy * vol → 取 top_k

与 cb_stock_quality 的互补性：
- cb_stock_quality: 选正股质量好（ROE+EP）的 CB → alpha from 正股价值
- cb_gamma_convexity: 选 Gamma 高 + 正股波动大的 CB → alpha from 凸性
- 组合使用可分散因子来源
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aipha.factors.cb_delta_zone import (
    DeltaZone,
    classify_delta_zone,
    compute_delta_proxy,
    compute_gamma_proxy,
)
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
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class GammaConvexityCBConfig(CBRotationConfig):
    """Delta 凸性策略配置。

    Attributes:
        delta_min: Delta 最小阈值（排除深度价外/债性区）
        delta_max: Delta 最大阈值（排除深度价内/强赎区）
        weight_gamma: Gamma 代理因子权重（越高越好）
        weight_premium: 溢价率因子权重（越低越好）
        weight_stock_vol: 正股波动率因子权重（越高越好）
        weight_price: 价格因子权重（越低越好）
        weight_issue_size: 发行规模因子权重（弱因子）
        vol_lookback: 波动率计算回看天数
        use_stock_bridge: 是否启用正股数据桥接
    """

    top_k: int = 15
    rebalance_days: int = 5
    max_price: float = 160.0          # 宽松价格限制（平衡区 CB 价格可较高）
    max_issue_size: float = float("inf")
    min_turnover: float = 20.0
    min_days_to_maturity: int = 60    # 到期太近的 CB 不适合做凸性
    stop_loss_pct: float = 0.20
    lot_size: int = 10

    # --- Delta 区间过滤 ---
    delta_min: float = 0.25           # 稍松于标准 0.3
    delta_max: float = 0.75           # 稍松于标准 0.7

    # --- 次新过滤 ---
    use_age_filter: bool = True
    min_age_days: int = 3

    # --- 溢价率过滤 ---
    max_premium: float = 60.0         # 高溢价的凸性不佳

    # --- 4+1 因子权重 ---
    weight_gamma: float = 0.25
    weight_premium: float = 0.25
    weight_stock_vol: float = 0.25
    weight_price: float = 0.15
    weight_issue_size: float = 0.10

    # --- 正股数据参数 ---
    vol_lookback: int = 60
    use_stock_bridge: bool = True


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------

@StrategyRegistry().register("cb_gamma_convexity")
class CBGammaConvexityStrategy(CBRotationStrategy):
    """Delta 凸性可转债策略。

    做多平衡区（高 Gamma）+ 高正股波动 + 低溢价的可转债。

    score = w_gamma   * rank(gamma_proxy, ↓)     # 越大越好
          + w_premium * rank(cb_over_rate, ↑)     # 越低越好
          + w_vol     * rank(stock_vol, ↓)        # 越高越好
          + w_price   * rank(close, ↑)            # 越低越好
          + w_size    * rank(issue_size, ↑)       # 越小越好

    关键过滤：仅保留 delta ∈ [delta_min, delta_max] 的 CB。
    """

    name = "cb_gamma_convexity"

    def __init__(
        self,
        config: GammaConvexityCBConfig | None = None,
        stock_bridge: Any | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self._config = config or GammaConvexityCBConfig(**kwargs)
        self._bridge = stock_bridge
        # 正股价格历史（通过 cb_value 代理） {stk_code: deque[float]}
        self._stk_price_history: dict[str, deque] = {}

    @property
    def config(self) -> GammaConvexityCBConfig:
        return self._config

    def _on_start_extra(self) -> None:
        self._stk_price_history.clear()
        if self._bridge is None and self._config.use_stock_bridge:
            try:
                from aipha.data.cb_stock_bridge import CBStockBridge
                self._bridge = CBStockBridge()
            except Exception as e:
                logger.warning(f"CBStockBridge 初始化失败: {e}")

    def _on_bar_update_state(
        self, bars: dict[str, Bar], context: StrategyContext,
    ) -> None:
        """更新正股价格历史。"""
        for symbol, bar in bars.items():
            meta = bar.metadata or {}
            stk_code = meta.get("stk_code", "")
            cb_value = meta.get("cb_value")
            if stk_code and cb_value and cb_value > 0:
                if stk_code not in self._stk_price_history:
                    self._stk_price_history[stk_code] = deque(
                        maxlen=self._config.vol_lookback + 5
                    )
                self._stk_price_history[stk_code].append(float(cb_value))

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        """Delta 区间 + 溢价率 + 次新过滤。"""
        cfg = self._config

        # Delta 区间过滤（核心！）
        delta = compute_delta_proxy(candidate.cb_value)
        if delta is None:
            return False
        if delta < cfg.delta_min or delta > cfg.delta_max:
            return False

        # 溢价率过滤
        if candidate.cb_over_rate is not None:
            if candidate.cb_over_rate > cfg.max_premium:
                return False

        # 次新过滤
        if cfg.use_age_filter and candidate.list_date:
            age = days_since_listed(candidate.list_date, context.current_time)
            if age < cfg.min_age_days:
                return False

        return True

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """4+1 因子排名加权评分。"""
        cfg = self._config
        if not candidates:
            return []
        n = len(candidates)
        if n == 1:
            return [candidates[0].symbol]

        # ----- 提取因子原始值 -----
        prices = [c.close for c in candidates]
        premiums = [
            c.cb_over_rate if c.cb_over_rate is not None else 50.0
            for c in candidates
        ]
        sizes = [c.issue_size for c in candidates]

        # Gamma 代理
        gammas: list[float] = []
        for c in candidates:
            gamma = compute_gamma_proxy(c.cb_value)
            gammas.append(gamma if gamma is not None else 0.0)

        # 正股波动率
        vols: list[float] = []
        for c in candidates:
            stk_code = (c.metadata or {}).get("stk_code", "")
            vol = self._compute_stk_vol(stk_code)
            vols.append(vol if vol is not None else 0.0)

        # ----- 排名归一化 -----
        rank_gamma = _rank_scores(gammas, ascending=False)       # 高 Gamma 好
        rank_premium = _rank_scores(premiums, ascending=True)    # 低溢价好
        rank_vol = _rank_scores(vols, ascending=False)           # 高波动好
        rank_price = _rank_scores(prices, ascending=True)        # 低价好
        rank_size = _rank_scores(sizes, ascending=True)          # 小规模好

        # ----- 权重归一化 -----
        total_w = (
            cfg.weight_gamma + cfg.weight_premium
            + cfg.weight_stock_vol + cfg.weight_price
            + cfg.weight_issue_size
        )
        if total_w <= 0:
            total_w = 1.0

        w_gm = cfg.weight_gamma / total_w
        w_pm = cfg.weight_premium / total_w
        w_vl = cfg.weight_stock_vol / total_w
        w_pr = cfg.weight_price / total_w
        w_sz = cfg.weight_issue_size / total_w

        # ----- 综合打分 -----
        scored: list[tuple[str, float]] = []
        for i, c in enumerate(candidates):
            score = (
                w_gm * rank_gamma[i]
                + w_pm * rank_premium[i]
                + w_vl * rank_vol[i]
                + w_pr * rank_price[i]
                + w_sz * rank_size[i]
            )
            scored.append((c.symbol, score))

        scored.sort(key=lambda x: x[1])
        return [s for s, _ in scored[: cfg.top_k]]

    # ----- 辅助方法 -----

    def _compute_stk_vol(self, stk_code: str) -> float | None:
        """计算正股波动率（年化）from 价格历史。"""
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
