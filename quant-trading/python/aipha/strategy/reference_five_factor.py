"""参考组合五因子策略 (Reference Five-Factor Strategy)

R76 Phase 1 产出 — 基于参考组合8年~7倍收益的核心设计哲学。

策略逻辑:
1. 5 核心因子等权: EP(盈利收益率) + BP(账面市值比) + ROE(净资产收益率)
   + DY(股息率) + LV(低波动)
2. 四象限框架: 按 Size(大/小) × Style(价值/成长) 分为 4 象限，
   每象限独立打分选股，确保风格分散
3. 行业分散化硬约束: 单行业占比 ≤ 12%，至少覆盖 6 个行业
4. 月频低换手: ~20 交易日调仓，buffer_k 缓冲减少不必要换手

Alpha 来源假说 (R76 E1/E4):
- 5 因子精简但完备: 覆盖价值(EP+BP)、质量(ROE)、收益(DY)、风险(LV)
- 因子数量少 → 过拟合风险低，参数稳定性高 (Occam's Razor)
- 四象限分散 → 降低对单一风格的依赖
- 行业硬约束 → 避免行业集中度踩踏
- 月频交易 → 低成本，适合大容量

继承 EquityRotationStrategy, 覆盖 _score_candidates。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_bp_factor,
    compute_dividend_factor,
    compute_ep_factor,
    compute_momentum_factor,
    compute_quality_factor,
    compute_size_factor,
    compute_volatility_factor,
)
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import (
    EquityCandidateInfo,
    EquityRotationConfig,
    EquityRotationStrategy,
)
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 四象限规格
# ---------------------------------------------------------------------------

@dataclass
class QuadrantSpec:
    """四象限中的单个象限规格。"""
    name: str = "large_value"
    mv_threshold: str = "large"   # "large" or "small"
    style: str = "value"          # "value" or "growth"
    top_k: int = 8
    weight_alloc: float = 0.25


# ---------------------------------------------------------------------------
# 策略配置
# ---------------------------------------------------------------------------

@dataclass
class ReferenceFiveFactorConfig(EquityRotationConfig):
    """参考组合五因子策略配置。"""

    # 覆盖基类默认 — 对标参考组合的保守设置
    top_k: int = 30
    rebalance_days: int = 21        # 月频 (~21 交易日)
    min_price: float = 3.0
    max_price: float = 500.0
    min_mv: float = 100_000.0       # 10 亿市值下限
    max_mv: float = float("inf")
    min_turnover: float = 2000.0    # 最低日成交额 (万元)
    min_listed_days: int = 120
    buffer_k: int = 10
    stop_loss_pct: float = 0.15
    trailing_stop_pct: float = 0.10
    max_single_pct: float = 0.05
    cash_reserve_pct: float = 0.03
    max_industry_pct: float = 0.12  # 单行业 ≤ 12%

    # --- 五因子权重 (等权 = 各 0.20) ---
    weight_ep: float = 0.20
    weight_bp: float = 0.20
    weight_roe: float = 0.20
    weight_dy: float = 0.20
    weight_lv: float = 0.20

    # --- 小市值因子 (默认 0 = 不启用) ---
    weight_size: float = 0.0

    # --- ROE 过滤模式 (代替排名因子) ---
    # 当 roe_as_filter=True 时，ROE 仅用作入池门槛 (min_roe)，
    # weight_roe 被忽略，不参与排名打分。
    # 经济学依据：A 股高 ROE 已被充分定价 (IC=-0.01)，
    # 用作筛选("有盈利能力"准入) 比排名("越高越好") 更有效。
    roe_as_filter: bool = False
    min_roe: float = 5.0  # ROE > 5% 才入池 (仅 roe_as_filter=True 时生效)

    # --- 可选第六因子: 动量 (默认 0 = 不启用) ---
    weight_momentum: float = 0.0
    momentum_multi_period: bool = True
    momentum_skip_recent: int = 5

    # --- 因子参数 ---
    ep_metric: str = "pe_ttm"
    bp_metric: str = "pb"
    roe_metric: str = "roe"
    dy_metric: str = "dv_ratio"
    lv_lookback: int = 60           # 低波动率回望期

    # --- 四象限参数 ---
    quadrant_enabled: bool = True
    mv_split_percentile: float = 50.0   # 市值分界百分位
    style_split_method: str = "bp"      # 用 BP 区分价值/成长

    # --- 行业分散 ---
    min_industries: int = 6             # 最少行业数

    # --- 动量/波动率基类参数 ---
    momentum_lookback: int = 65
    volatility_lookback: int = 65

    pass  # end of config


@StrategyRegistry().register("reference_five_factor")
class ReferenceFiveFactorStrategy(EquityRotationStrategy):
    """参考组合五因子策略。"""

    name = "reference_five_factor"

    def __init__(
        self,
        config: ReferenceFiveFactorConfig | None = None,
        **kwargs,
    ):
        super().__init__()
        self._config = config or ReferenceFiveFactorConfig(**kwargs)

    @property
    def config(self) -> ReferenceFiveFactorConfig:
        return self._config

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """五因子 + 四象限打分。"""
        if not candidates:
            return []

        cfg = self._config

        if cfg.quadrant_enabled:
            return self._score_with_quadrants(candidates, context)
        else:
            return self._score_flat(candidates, context)

    # ------------------------------------------------------------------
    # 扁平模式 (无象限，全市场统一打分)
    # ------------------------------------------------------------------

    def _score_flat(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """全市场统一五因子打分。"""
        cfg = self._config
        scores: dict[str, float] = {}

        # ROE 过滤模式：先剔除低 ROE 候选
        if cfg.roe_as_filter:
            before = len(candidates)
            candidates = [
                c for c in candidates
                if (c.metadata.get(cfg.roe_metric) or 0) >= cfg.min_roe
            ]
            logger.debug(
                f"[{self.name}] ROE filter: {before} → {len(candidates)} "
                f"(min_roe={cfg.min_roe}%)"
            )
            if not candidates:
                return []

        # 计算因子
        f_ep = compute_ep_factor(candidates, metric=cfg.ep_metric)
        f_bp = compute_bp_factor(candidates, metric=cfg.bp_metric)
        f_dy = compute_dividend_factor(candidates, field=cfg.dy_metric)
        f_lv = compute_volatility_factor(
            self._price_history, candidates,
            lookback=cfg.lv_lookback,
        )

        # 排名融合
        self.rank_normalize(candidates, f_ep, cfg.weight_ep, scores)
        self.rank_normalize(candidates, f_bp, cfg.weight_bp, scores)
        self.rank_normalize(candidates, f_dy, cfg.weight_dy, scores)
        self.rank_normalize(candidates, f_lv, cfg.weight_lv, scores)

        # ROE：仅当 roe_as_filter=False 时参与排名
        if not cfg.roe_as_filter and cfg.weight_roe > 0:
            f_roe = compute_quality_factor(candidates, metric=cfg.roe_metric)
            self.rank_normalize(candidates, f_roe, cfg.weight_roe, scores)

        # 小市值因子
        if cfg.weight_size > 0:
            f_size = compute_size_factor(candidates)
            self.rank_normalize(candidates, f_size, cfg.weight_size, scores)

        # 可选动量因子
        if cfg.weight_momentum > 0:
            f_mom = compute_momentum_factor(
                self._price_history, candidates,
                lookback=cfg.momentum_lookback,
                skip_recent=cfg.momentum_skip_recent,
                multi_period=cfg.momentum_multi_period,
            )
            self.rank_normalize(candidates, f_mom, cfg.weight_momentum, scores)

        return self.rank_sort(scores)

    # ------------------------------------------------------------------
    # 四象限模式
    # ------------------------------------------------------------------

    def _score_with_quadrants(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """四象限打分: Size(大/小) x Style(价值/成长)。"""
        cfg = self._config

        # ROE 过滤模式：先剔除低 ROE 候选
        if cfg.roe_as_filter:
            candidates = [
                c for c in candidates
                if (c.metadata.get(cfg.roe_metric) or 0) >= cfg.min_roe
            ]
            if not candidates:
                return []

        # 1. 计算市值分界点
        mv_values = [c.circ_mv for c in candidates if c.circ_mv > 0]
        if not mv_values:
            return self._score_flat(candidates, context)
        mv_median = sorted(mv_values)[
            int(len(mv_values) * cfg.mv_split_percentile / 100)
        ]

        # 2. 计算风格分界 (用 BP)
        bp_vals = {}
        for c in candidates:
            pb = c.metadata.get(cfg.bp_metric)
            if pb and math.isfinite(pb) and pb > 0:
                bp_vals[c.symbol] = 1.0 / pb
        if bp_vals:
            bp_sorted = sorted(bp_vals.values())
            bp_median = bp_sorted[len(bp_sorted) // 2]
        else:
            bp_median = 0.0

        # 3. 分配到四象限
        quadrants: dict[str, list[EquityCandidateInfo]] = {
            "large_value": [], "large_growth": [],
            "small_value": [], "small_growth": [],
        }

        for c in candidates:
            is_large = c.circ_mv >= mv_median
            bp_val = bp_vals.get(c.symbol, 0.0)
            is_value = bp_val >= bp_median

            if is_large and is_value:
                quadrants["large_value"].append(c)
            elif is_large and not is_value:
                quadrants["large_growth"].append(c)
            elif not is_large and is_value:
                quadrants["small_value"].append(c)
            else:
                quadrants["small_growth"].append(c)

        # 4. 各象限独立打分
        per_quad_k = max(3, cfg.top_k // 4)
        final_scores: dict[str, float] = {}

        quad_weights = {
            "large_value": 0.30,    # 大盘价值 — 稳定
            "large_growth": 0.20,   # 大盘成长 — 高质量
            "small_value": 0.30,    # 小盘价值 — 高 alpha
            "small_growth": 0.20,   # 小盘成长 — 动量
        }

        for q_name, q_cands in quadrants.items():
            if not q_cands:
                continue

            q_scores: dict[str, float] = {}
            self._compute_five_factors(q_cands, cfg, q_scores)

            ranked = self.rank_sort(q_scores)[:per_quad_k]
            n = len(ranked)
            w = quad_weights.get(q_name, 0.25)

            for i, sym in enumerate(ranked):
                rank_score = (n - i) / n if n > 0 else 0
                final_scores[sym] = w * rank_score

            logger.debug(
                f"[{self.name}] 象限 {q_name}: "
                f"{len(q_cands)} 候选 → {len(ranked)} 入选"
            )

        sorted_syms = sorted(
            final_scores, key=lambda s: final_scores[s], reverse=True,
        )
        return sorted_syms

    def _compute_five_factors(
        self,
        cands: list[EquityCandidateInfo],
        cfg: ReferenceFiveFactorConfig,
        scores: dict[str, float],
    ) -> None:
        """对一组候选计算因子并累加到 scores。

        支持 ROE 过滤模式和小市值因子。
        """
        f_ep = compute_ep_factor(cands, metric=cfg.ep_metric)
        f_bp = compute_bp_factor(cands, metric=cfg.bp_metric)
        f_dy = compute_dividend_factor(cands, field=cfg.dy_metric)
        f_lv = compute_volatility_factor(
            self._price_history, cands,
            lookback=cfg.lv_lookback,
        )

        self.rank_normalize(cands, f_ep, cfg.weight_ep, scores)
        self.rank_normalize(cands, f_bp, cfg.weight_bp, scores)
        self.rank_normalize(cands, f_dy, cfg.weight_dy, scores)
        self.rank_normalize(cands, f_lv, cfg.weight_lv, scores)

        # ROE：仅当 roe_as_filter=False 时参与排名
        if not cfg.roe_as_filter and cfg.weight_roe > 0:
            f_roe = compute_quality_factor(cands, metric=cfg.roe_metric)
            self.rank_normalize(cands, f_roe, cfg.weight_roe, scores)

        # 小市值因子
        if cfg.weight_size > 0:
            f_size = compute_size_factor(cands)
            self.rank_normalize(cands, f_size, cfg.weight_size, scores)

        if cfg.weight_momentum > 0:
            f_mom = compute_momentum_factor(
                self._price_history, cands,
                lookback=cfg.momentum_lookback,
                skip_recent=cfg.momentum_skip_recent,
                multi_period=cfg.momentum_multi_period,
            )
            self.rank_normalize(cands, f_mom, cfg.weight_momentum, scores)
