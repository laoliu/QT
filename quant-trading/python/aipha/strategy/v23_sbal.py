"""v23_sbal 风格均衡轮动策略 — 事件驱动引擎版

灵感来源：ZH1400599 Phase 1（纯股票期 AR>36%, MDD≈-15%, SR≈1.6）。
核心创新：2×2 Fama-French 风格矩阵 + 象限内等权，实现极致分散化。

设计核心：
1. **2×2 风格矩阵**（仿 ZH1400599 "大盘价值/大盘成长/小盘价值/小盘成长"）
   - 按流通市值中位数 → 大盘 vs 小盘
   - 按价值得分中位数 → 价值 vs 成长
   - 每象限各选 top 7-8 只，总持仓 ~30 只

2. **5 因子复合评分**（象限内排序用）
   - EP 盈利收益率 (0.25) — 价值因子
   - 营收增速 (0.25) — 成长因子
   - ROE 质量 (0.20) — Novy-Marx 2013
   - 低波动 EWMA (0.15) — Ang et al. 2006
   - 12-1 月动量 (0.15) — Jegadeesh-Titman 1993

3. **全市值覆盖** — 5-1000 亿，与 v19(2-30) 和 v22(5-150) 差异化
   天然降低与现有策略相关性

4. **行业强制分散** — 单行业 ≤ 15%、最少覆盖 8 行业

5. **MDD 梯度减仓**（Phase 2 启示）
   - MDD > 15% → 减仓至 70%
   - MDD > 20% → 减仓至 50%
   - MDD < 8% → 恢复满仓

理论基础：
- Fama-French (1993): 小盘+价值溢价
- Novy-Marx (2013): 质量溢价（高 ROE）
- ZH1400599 实证：风格均衡 > 单一风格暴露 控制 MDD
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_bp_factor,
    compute_earnings_growth_factor,
    compute_ep_factor,
    compute_ewma_volatility_factor,
    compute_momentum_factor,
    compute_quality_factor,
    compute_size_factor,
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


@dataclass
class V23SbalConfig(EquityRotationConfig):
    """v23_sbal 风格均衡策略配置"""

    # --- 持仓 ---
    top_k: int = 30
    rebalance_days: int = 20
    buffer_k: int = 10
    # --- 选股范围（全市值: 5-1000 亿）---
    min_mv: float = 50_000.0
    max_mv: float = 10_000_000.0
    min_price: float = 3.0
    max_price: float = 300.0
    min_turnover: float = 800.0
    min_listed_days: int = 180
    max_industry_pct: float = 0.15

    # --- 额外过滤 ---
    max_pb: float = 15.0            # 宽松 PB（覆盖成长股）
    min_roe: float = -5.0           # 允许微亏（成长股可能暂时亏损）

    # --- 象限配置 ---
    quadrant_stocks: int = 8        # 每象限选股数（4×8=32→top_k=30 裁剪）
    min_quadrant_stocks: int = 3    # 象限最少选股数（不足则跳过）

    # --- 因子权重（象限内排序用，总和 1.0）---
    weight_ep: float = 0.25         # EP 盈利收益率（价值）
    weight_growth: float = 0.25     # 营收增速（成长）
    weight_roe: float = 0.20        # ROE 质量
    weight_low_vol: float = 0.15    # EWMA 低波动
    weight_momentum: float = 0.15   # 12-1 月动量

    # --- 动量参数 ---
    momentum_lookback: int = 240
    momentum_skip_recent: int = 20
    volatility_lookback: int = 60

    # --- MDD 梯度减仓（Phase 2 启示）---
    drawdown_timing_enabled: bool = True   # 默认开启
    dd_level1_threshold: float = 0.15      # 15% → 减仓至 70%
    dd_level1_scale: float = 0.70
    dd_level2_threshold: float = 0.20      # 20% → 减仓至 50%
    dd_level2_scale: float = 0.50
    dd_restore_threshold: float = 0.08     # 8% 以内 → 恢复满仓

    # --- 风控 ---
    stop_loss_pct: float = 0.0
    trailing_stop_pct: float = 0.0
    cash_reserve_pct: float = 0.05


# 风格象限枚举
QUADRANT_NAMES = [
    "large_value",   # 大盘价值
    "large_growth",  # 大盘成长
    "small_value",   # 小盘价值
    "small_growth",  # 小盘成长
]


@StrategyRegistry().register("v23_sbal")
class V23SbalStrategy(EquityRotationStrategy):
    """v23_sbal 风格均衡轮动策略"""

    name = "v23_sbal"

    def __init__(self, config: V23SbalConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or V23SbalConfig(**kwargs)
        self._nav_high: float = 0.0
        self._timing_scale: float = 1.0

    @property
    def config(self) -> V23SbalConfig:
        return self._config

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """2×2 风格矩阵 + 象限内多因子排序。

        流程：
        1. 按市值 × 价值得分将候选分入 4 个象限
        2. 各象限内独立做 5 因子评分排序
        3. 从每象限取 top quadrant_stocks 只
        4. 交替拼接确保象限均衡（非简单拼接）
        """
        cfg = self._config
        if not candidates or len(candidates) < cfg.min_quadrant_stocks * 2:
            # 候选太少，退化为全局排序
            return self._score_within_quadrant(candidates, context)

        quadrants = self._classify_quadrants(candidates)

        # 从每象限取 top-N
        result: list[str] = []
        quadrant_picks: dict[str, list[str]] = {}
        for qname in QUADRANT_NAMES:
            q_cands = quadrants.get(qname, [])
            if len(q_cands) < cfg.min_quadrant_stocks:
                logger.info("[%s] 象限 %s 候选不足(%d), 跳过",
                            self.name, qname, len(q_cands))
                quadrant_picks[qname] = []
                continue
            ranked = self._score_within_quadrant(q_cands, context)
            quadrant_picks[qname] = ranked[:cfg.quadrant_stocks]

        # 交替拼接：round-robin 从各象限取 1 只
        max_len = max((len(v) for v in quadrant_picks.values()), default=0)
        for i in range(max_len):
            for qname in QUADRANT_NAMES:
                picks = quadrant_picks.get(qname, [])
                if i < len(picks) and picks[i] not in result:
                    result.append(picks[i])

        logger.info(
            "[%s] 风格矩阵: %s",
            self.name,
            {q: len(v) for q, v in quadrant_picks.items()},
        )
        return result

    def _classify_quadrants(
        self,
        candidates: list[EquityCandidateInfo],
    ) -> dict[str, list[EquityCandidateInfo]]:
        """按市值 × 价值得分将候选分入 4 个象限。

        市值切分：流通市值中位数
        价值切分：EP + BP 合成得分中位数
        """
        if not candidates:
            return {q: [] for q in QUADRANT_NAMES}

        # 计算每只候选的市值和价值得分
        mv_list: list[float] = []
        val_scores: dict[str, float] = {}

        for c in candidates:
            mv_list.append(c.circ_mv)
            md = c.metadata or {}
            # EP = 1/PE, BP = 1/PB
            pe = md.get("pe_ttm")
            pb = md.get("pb")
            ep = (1.0 / pe) if (pe and pe > 0) else 0.0
            bp = (1.0 / pb) if (pb and pb > 0) else 0.0
            val_scores[c.symbol] = ep + bp  # 合成价值得分

        # 中位数切分
        mv_sorted = sorted(mv_list)
        mv_median = mv_sorted[len(mv_sorted) // 2]
        val_vals = sorted(val_scores.values())
        val_median = val_vals[len(val_vals) // 2] if val_vals else 0.0

        quadrants: dict[str, list[EquityCandidateInfo]] = {
            q: [] for q in QUADRANT_NAMES
        }
        for c in candidates:
            is_large = c.circ_mv >= mv_median
            is_value = val_scores.get(c.symbol, 0) >= val_median
            if is_large and is_value:
                quadrants["large_value"].append(c)
            elif is_large and not is_value:
                quadrants["large_growth"].append(c)
            elif not is_large and is_value:
                quadrants["small_value"].append(c)
            else:
                quadrants["small_growth"].append(c)

        return quadrants

    def _score_within_quadrant(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """象限内 5 因子评分并排序。

        因子（总权重 1.0）：
        - EP 0.25: 盈利收益率
        - Growth 0.25: 营收增速
        - ROE 0.20: 盈利质量
        - LowVol 0.15: -EWMA 波动率
        - Momentum 0.15: 12-1 月动量
        """
        cfg = self._config
        if not candidates:
            return []

        scores: dict[str, float] = {}
        nh = "median"

        # EP 0.25
        f_ep = compute_ep_factor(candidates)
        self.rank_normalize(candidates, f_ep, cfg.weight_ep,
                            scores, nan_handling=nh)

        # Growth 0.25
        f_growth = compute_earnings_growth_factor(
            candidates, field="revenue_yoy",
        )
        self.rank_normalize(candidates, f_growth, cfg.weight_growth,
                            scores, nan_handling=nh)

        # ROE 0.20
        f_roe = compute_quality_factor(candidates, metric="roe")
        self.rank_normalize(candidates, f_roe, cfg.weight_roe,
                            scores, nan_handling=nh)

        # Low Vol 0.15
        f_vol = compute_ewma_volatility_factor(
            self._price_history, candidates,
            lookback=cfg.volatility_lookback, halflife=20,
        )
        self.rank_normalize(candidates, f_vol, cfg.weight_low_vol,
                            scores, nan_handling=nh)

        # Momentum 0.15
        f_mom = compute_momentum_factor(
            self._price_history, candidates,
            lookback=cfg.momentum_lookback,
            skip_recent=cfg.momentum_skip_recent,
            invert=False,
        )
        self.rank_normalize(candidates, f_mom, cfg.weight_momentum,
                            scores, nan_handling=nh)

        return self.rank_sort(scores)

    def _extra_filter(
        self,
        candidate: EquityCandidateInfo,
        context: StrategyContext,
    ) -> bool:
        """额外过滤: PB上限 + ROE下限"""
        cfg = self._config
        md = candidate.metadata or {}
        pb = md.get("pb")
        if pb is not None and pb > cfg.max_pb:
            return False
        roe = md.get("roe")
        if roe is not None and roe < cfg.min_roe:
            return False
        return True

    def _on_bar_update_state(
        self,
        bars: dict[str, "Bar"],
        context: StrategyContext,
    ) -> None:
        """MDD 梯度减仓（Phase 2 风控）。

        ZH1400599 Phase 2 实证：MDD 超阈值时主动减仓 → 加速恢复。
        本策略采用两级梯度：
          - MDD > 15% → scale = 0.70（减仓至 70%）
          - MDD > 20% → scale = 0.50（减仓至 50%）
          - MDD < 8%  → 恢复满仓
        中间区域保持当前状态（迟滞机制）。

        理论基础：Roncalli (2013) 风险预算动态调整。
        """
        cfg = self._config
        if not cfg.drawdown_timing_enabled:
            self._timing_scale = 1.0
            context.metadata["position_scale"] = 1.0
            return

        nav = context.nav
        if nav <= 0:
            return

        if nav > self._nav_high:
            self._nav_high = nav

        current_dd = 1.0 - nav / self._nav_high if self._nav_high > 0 else 0.0

        prev_scale = self._timing_scale
        if current_dd >= cfg.dd_level2_threshold:
            self._timing_scale = cfg.dd_level2_scale
        elif current_dd >= cfg.dd_level1_threshold:
            self._timing_scale = cfg.dd_level1_scale
        elif current_dd <= cfg.dd_restore_threshold:
            self._timing_scale = 1.0
        # else: 保持迟滞

        if self._timing_scale != prev_scale:
            logger.info(
                "[%s] MDD梯度减仓: MDD=%.1f%%, scale %.0f%% → %.0f%%",
                self.name, current_dd * 100,
                prev_scale * 100, self._timing_scale * 100,
            )

        context.metadata["position_scale"] = self._timing_scale
