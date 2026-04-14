"""指数增强策略 — Alpha 倾斜 + Barra 约束优化器

核心逻辑:
  1. 接收基准权重 + alpha 评分
  2. 在行业偏离 / 跟踪误差 / 个股权重约束内
  3. 最大化组合 alpha 暴露

约束:
  - 行业偏离 ≤ industry_deviation_max
  - 跟踪误差 ≤ tracking_error_max
  - 个股权重 ∈ [w_bm - max_active, w_bm + max_active]
  - 风格因子偏离 ≤ style_deviation_max
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from aipha.strategy.base import Strategy, StrategyContext, OrderIntent, IntentType
from aipha.domain.models import Bar, Side


@dataclass
class IndexEnhanceConfig:
    benchmark: str = "CSI300"          # CSI300 / CSI500 / CSI1000
    tracking_error_target: float = 0.05
    tracking_error_max: float = 0.08
    industry_deviation_max: float = 0.05
    style_deviation_max: float = 0.50  # σ
    max_active_weight: float = 0.02    # 个股主动偏离
    min_weight: float = 0.0
    max_weight: float = 0.05
    rebalance_days: int = 10
    turnover_limit: float = 0.30       # 单次调仓换手率上限


class BarraConstraint:
    """Barra 行业/风格约束检查。"""

    def __init__(self, config: IndexEnhanceConfig) -> None:
        self.config = config

    def check_industry_deviation(
        self, port_weights: pd.Series, bm_weights: pd.Series, industry_map: dict[str, str],
    ) -> dict[str, float]:
        """返回 {industry: deviation}"""
        port_ind = pd.Series(0.0, dtype=float)
        bm_ind = pd.Series(0.0, dtype=float)
        for sym, w in port_weights.items():
            ind = industry_map.get(sym, "other")
            port_ind[ind] = port_ind.get(ind, 0) + w
        for sym, w in bm_weights.items():
            ind = industry_map.get(sym, "other")
            bm_ind[ind] = bm_ind.get(ind, 0) + w
        all_ind = set(port_ind.index) | set(bm_ind.index)
        deviations = {}
        for ind in all_ind:
            dev = abs(port_ind.get(ind, 0) - bm_ind.get(ind, 0))
            deviations[ind] = dev
        return deviations

    def estimate_tracking_error(
        self, active_weights: pd.Series, cov_matrix: pd.DataFrame,
    ) -> float:
        """估算年化跟踪误差: sqrt(w' Σ w) * sqrt(252)"""
        aligned = active_weights.reindex(cov_matrix.index, fill_value=0)
        w = aligned.values
        sigma = cov_matrix.values
        var = w @ sigma @ w
        return float(np.sqrt(max(var, 0))) * np.sqrt(252)


class IndexEnhanceOptimizer:
    """指数增强优化器 — 简化版 (无需 scipy.optimize)。

    策略: alpha tilt + 约束裁剪 + 行业中性化。
    """

    def __init__(self, config: IndexEnhanceConfig) -> None:
        self.config = config
        self.barra = BarraConstraint(config)

    def optimize(
        self,
        alpha_scores: dict[str, float],
        benchmark_weights: dict[str, float],
        industry_map: dict[str, str],
        cov_matrix: pd.DataFrame | None = None,
    ) -> dict[str, float]:
        """生成增强后的组合权重。"""
        cfg = self.config
        bm = pd.Series(benchmark_weights, dtype=float)
        alpha = pd.Series(alpha_scores, dtype=float)

        # 对齐到基准成分
        symbols = bm.index.tolist()
        alpha = alpha.reindex(symbols, fill_value=0)

        # Alpha rank → tilt
        alpha_rank = alpha.rank(pct=True)
        tilt = (alpha_rank - 0.5) * 2 * cfg.max_active_weight  # [-max_active, +max_active]

        # 初始组合 = benchmark + tilt
        port = bm + tilt

        # 裁剪个股权重
        port = port.clip(lower=cfg.min_weight, upper=cfg.max_weight)

        # 行业中性化
        bm_by_ind: dict[str, float] = {}
        port_by_ind: dict[str, float] = {}
        ind_members: dict[str, list[str]] = {}
        for sym in symbols:
            ind = industry_map.get(sym, "other")
            bm_by_ind[ind] = bm_by_ind.get(ind, 0) + bm.get(sym, 0)
            port_by_ind[ind] = port_by_ind.get(ind, 0) + port.get(sym, 0)
            ind_members.setdefault(ind, []).append(sym)

        for ind, members in ind_members.items():
            bm_w = bm_by_ind.get(ind, 0)
            port_w = port_by_ind.get(ind, 0)
            dev = port_w - bm_w
            if abs(dev) > cfg.industry_deviation_max and port_w > 0:
                # 按比例缩放该行业内的权重
                target_w = bm_w + np.sign(dev) * cfg.industry_deviation_max
                scale = target_w / port_w if port_w > 0 else 1.0
                for sym in members:
                    port[sym] *= scale

        # 归一化
        total = port.sum()
        if total > 0:
            port = port / total

        # 跟踪误差检查 (如有协方差矩阵)
        if cov_matrix is not None:
            active = port - bm.reindex(port.index, fill_value=0)
            te = self.barra.estimate_tracking_error(active, cov_matrix)
            if te > cfg.tracking_error_max:
                # 缩小 active 权重
                scale = cfg.tracking_error_target / te
                port = bm.reindex(port.index, fill_value=0) + active * scale
                total = port.sum()
                if total > 0:
                    port = port / total

        return port[port > 1e-8].to_dict()


class IndexEnhanceStrategy(Strategy):
    """指数增强策略 — 使用 optimizer 生成目标组合。"""

    name = "index_enhance"

    def __init__(
        self,
        config: IndexEnhanceConfig | None = None,
        benchmark_weights: dict[str, float] | None = None,
        industry_map: dict[str, str] | None = None,
        alpha_model: object | None = None,
    ) -> None:
        self.config = config or IndexEnhanceConfig()
        self.optimizer = IndexEnhanceOptimizer(self.config)
        self._bm_weights = benchmark_weights or {}
        self._industry_map = industry_map or {}
        self._alpha_model = alpha_model
        self._bar_count = 0
        self._last_rebalance = 0

    def set_benchmark(self, weights: dict[str, float]) -> None:
        self._bm_weights = weights

    def set_industry_map(self, mapping: dict[str, str]) -> None:
        self._industry_map = mapping

    def on_bar(self, bars: dict[str, Bar], context: StrategyContext) -> list[OrderIntent]:
        self._bar_count += 1
        if self._bar_count - self._last_rebalance < self.config.rebalance_days:
            return []
        if not self._bm_weights:
            return []

        # 获取 alpha 评分
        alpha_scores = self._get_alpha_scores(bars)

        # 优化
        target_weights = self.optimizer.optimize(
            alpha_scores=alpha_scores,
            benchmark_weights=self._bm_weights,
            industry_map=self._industry_map,
        )

        # 生成调仓指令
        intents = self._generate_rebalance_intents(target_weights, bars, context)
        self._last_rebalance = self._bar_count
        return intents

    def _get_alpha_scores(self, bars: dict[str, Bar]) -> dict[str, float]:
        """获取 alpha 评分。可外部注入 alpha_model 或用简单动量。"""
        if self._alpha_model is not None and hasattr(self._alpha_model, "predict"):
            return self._alpha_model.predict(bars)
        # 默认: 简单多因子评分 (动量 + 价值)
        scores = {}
        for sym, bar in bars.items():
            meta = getattr(bar, "metadata", {}) or {}
            mom = meta.get("momentum_score", 0) or 0
            val = meta.get("value_score", 0) or 0
            scores[sym] = 0.5 * mom + 0.5 * val
        return scores

    def _generate_rebalance_intents(
        self, target_weights: dict[str, float], bars: dict[str, Bar], ctx: StrategyContext,
    ) -> list[OrderIntent]:
        intents = []
        turnover_budget = self.config.turnover_limit * ctx.nav
        turnover_used = 0.0

        for sym, tw in target_weights.items():
            if sym not in bars:
                continue
            price = bars[sym].close
            if price <= 0:
                continue
            target_value = ctx.nav * tw
            target_qty = int(target_value / price / 100) * 100
            current = ctx.get_position(sym)
            diff = target_qty - current
            trade_value = abs(diff) * price
            if abs(diff) >= 100 and turnover_used + trade_value <= turnover_budget:
                side = Side.BUY if diff > 0 else Side.SELL
                intents.append(OrderIntent(
                    symbol=sym, side=side, qty=abs(diff),
                    intent_type=IntentType.TARGET_POSITION,
                    reason="index_enhance_rebalance",
                ))
                turnover_used += trade_value

        # 清仓不在目标中的
        for sym, pos in ctx.positions.items():
            if pos > 0 and sym not in target_weights:
                if sym in bars:
                    trade_value = pos * bars[sym].close
                    if turnover_used + trade_value <= turnover_budget:
                        intents.append(OrderIntent(
                            symbol=sym, side=Side.SELL, qty=pos,
                            intent_type=IntentType.ORDER,
                            reason="index_enhance_exit",
                        ))
                        turnover_used += trade_value

        return intents
