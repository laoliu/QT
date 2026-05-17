"""A 股红利质量精选策略

与 dividend_defensive 的核心差异：
- **更高股息门槛**(≥1.0%) + 应计盈余质量过滤 → 排除"假高股息"
- **大盘蓝筹定位**(100亿+) → 降低尾部风险和流动性冲击
- **盈利稳定性作为核心因子** → 避免周期股股息波动
- **更集中持仓**(25只) + 更严格止损 → 力图更高 alpha

Alpha 来源（4 因子，符合 3-5 因子反过拟合准则）：
1. **股息率** (30%) — 高 TTM 股息率优先
2. **质量复合** (30%) — ROE + 应计盈余质量（Sloan 1996）
3. **低波动** (25%) — 60日特异波动率倒数（Ang 2006）
4. **价值确认** (15%) — 低 PE 排除成长溢价过高标的

硬性门槛：
- 股息率 ≥ 1.0%（vs dividend_defensive 的 0.5%）
- ROE ≥ 8%
- PE_TTM ∈ (0, 40)
- 资产负债率 < 70%
- 流通市值 ≥ 100 亿

预期表现：AR 8-12%, MDD 12-18%, Sharpe 0.5-0.8
适用市况：熊市/震荡市防御底仓

对齐 copilot-instructions.md：
- 策略层只输出 OrderIntent
- 因子调用 equity_factors 统一模块
- 配置文件：configs/backtest/dividend_quality_select_12y.yml
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_accruals_quality_factor,
    compute_dividend_factor,
    compute_quality_factor,
    compute_value_factor,
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
    pass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


@dataclass
class DividendQualitySelectConfig(EquityRotationConfig):
    """红利质量精选策略配置。"""

    # --- 覆盖基类默认值 ---
    top_k: int = 25
    rebalance_days: int = 20          # 月度调仓
    min_price: float = 5.0
    max_price: float = 500.0          # 不限高价蓝筹
    min_turnover: float = 2000.0      # 2000万最低成交额（大盘股）
    min_listed_days: int = 250        # 满1年才纳入（股息历史需要）
    stop_loss_pct: float = 0.12       # 12% 止损
    trailing_stop_pct: float = 0.10   # 10% 追踪止损
    buffer_k: int = 8                 # 缓冲区降换手
    max_single_pct: float = 0.06      # 单票6%
    min_mv: float = 1_000_000.0       # 100亿以上（大盘蓝筹）
    max_mv: float = float("inf")
    inverse_vol_weighting: bool = True  # 反波动率加权
    max_industry_pct: float = 0.20    # 单行业20%

    # --- 因子权重（总和 = 1.0）---
    weight_dividend: float = 0.30
    weight_quality: float = 0.30      # 质量复合 = ROE + 应计质量
    weight_volatility: float = 0.25
    weight_value: float = 0.15

    # --- 质量复合内部权重 ---
    quality_roe_weight: float = 0.55
    quality_accruals_weight: float = 0.45

    # --- 硬性过滤门槛 ---
    min_dividend_yield: float = 1.0   # 股息率 ≥ 1.0%
    min_roe: float = 8.0              # ROE ≥ 8%
    max_pe: float = 40.0              # PE < 40
    max_debt_to_assets: float = 70.0  # 负债率 < 70%

    # --- 因子参数 ---
    dividend_field: str = "dv_ratio"
    volatility_lookback: int = 60
    momentum_lookback: int = 20       # 基类需要


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------


@StrategyRegistry().register("dividend_quality_select")
class DividendQualitySelectStrategy(EquityRotationStrategy):
    """A 股红利质量精选策略。

    在大盘蓝筹中筛选高股息率 + 高质量 + 低波动的标的，
    以应计盈余质量替代简单负债率，更精准排除"假高股息"。
    """

    name = "dividend_quality_select"

    def __init__(
        self,
        config: DividendQualitySelectConfig | None = None,
        **kwargs,
    ):
        super().__init__()
        self._config = config or DividendQualitySelectConfig(**kwargs)

    @property
    def config(self) -> DividendQualitySelectConfig:
        return self._config

    def _extra_filter(
        self,
        candidate: EquityCandidateInfo,
        context: StrategyContext,
    ) -> bool:
        """红利质量硬性过滤。"""
        cfg = self._config
        meta = candidate.metadata

        # 股息率下限（核心门槛）
        dv = meta.get(cfg.dividend_field)
        if dv is None or not math.isfinite(dv) or dv < cfg.min_dividend_yield:
            return False

        # ROE 下限
        roe = meta.get("roe")
        if roe is not None and roe < cfg.min_roe:
            return False

        # PE 范围：排除亏损和过高估值
        pe = meta.get("pe_ttm")
        if pe is not None:
            if pe <= 0 or pe > cfg.max_pe:
                return False

        # 负债率上限
        dta = meta.get("debt_to_assets")
        if dta is not None and dta > cfg.max_debt_to_assets:
            return False

        return True

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """4 因子复合打分：股息率 + 质量(ROE+应计) + 低波动 + 价值。"""
        cfg = self._config
        if not candidates:
            return []

        scores: dict[str, float] = {}

        # 1. 股息率因子
        f_div = compute_dividend_factor(
            candidates, field=cfg.dividend_field,
        )
        self.rank_normalize(
            candidates, f_div, cfg.weight_dividend, scores,
            nan_handling="median",
        )

        # 2. 质量复合因子 = ROE + 应计盈余质量
        f_roe = compute_quality_factor(candidates, metric="roe")
        f_acc = compute_accruals_quality_factor(candidates)

        quality_scores: dict[str, float] = {}
        self.rank_normalize(
            candidates, f_roe, cfg.quality_roe_weight, quality_scores,
            nan_handling="median",
        )
        self.rank_normalize(
            candidates, f_acc, cfg.quality_accruals_weight, quality_scores,
            nan_handling="median",
        )
        for sym, qs in quality_scores.items():
            scores[sym] = scores.get(sym, 0.0) + qs * cfg.weight_quality

        # 3. 低波动因子
        f_vol = compute_volatility_factor(
            self._price_history, candidates,
            lookback=cfg.volatility_lookback,
        )
        self.rank_normalize(
            candidates, f_vol, cfg.weight_volatility, scores,
            nan_handling="median",
        )

        # 4. 价值因子（低 PE 确认）
        f_value = compute_value_factor(candidates, metric="pe_ttm")
        self.rank_normalize(
            candidates, f_value, cfg.weight_value, scores,
            nan_handling="median",
        )

        return self.rank_sort(scores)
