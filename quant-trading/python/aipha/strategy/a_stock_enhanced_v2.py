"""A 股增强多因子策略 v2

在基线策略（EP + ROE + MOM + IVOL）基础上扩展：
  5. STR (短期反转)           — 5-20d 回报反转，IC 3-5%
  6. Analyst Revision (分析师修正) — EPS 一致预期修正率，IC 1-3%
  7. Holder Change (股东户数变化) — 户数减少=筹码集中，IC 1-2%
  8. Forecast Surprise (业绩预告) — 预增/扭亏信号，事件 alpha

回测配置：configs/backtest/a_stock_enhanced_v2_10y.yml
策略注册名：a_stock_enhanced_v2
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.analyst_factors import (
    compute_analyst_revision_factor,
    compute_forecast_surprise_factor,
    compute_holder_change_factor,
)
from aipha.factors.equity_factors import (
    compute_ep_factor,
    compute_ivol_factor,
    compute_momentum_factor,
    compute_quality_factor,
    compute_str_factor,
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


@dataclass
class AStockEnhancedV2Config(EquityRotationConfig):
    """A 股增强多因子 v2 配置。"""

    # ── 基线四因子权重 ──
    weight_ep: float = 0.15
    weight_roe: float = 0.15
    weight_mom: float = 0.10
    weight_ivol: float = 0.10

    # ── 新增因子权重 ──
    weight_str: float = 0.15
    weight_analyst: float = 0.15
    weight_holder: float = 0.10
    weight_forecast: float = 0.10

    # ── 动量参数 ──
    mom_lookback: int = 250
    mom_skip_recent: int = 20

    # ── IVOL / STR 参数 ──
    ivol_lookback: int = 60
    str_lookback: int = 10

    # ── 指标字段 ──
    ep_pe_metric: str = "pe_ttm"
    roe_metric: str = "roe"

    # ── 替代数据路径 ──
    analyst_cache_dir: str = "data/cache/alternative"
    holder_cache_dir: str = "data/cache/reference/stk_holdernumber"
    forecast_cache_dir: str = "data/cache/advanced"

    def __post_init__(self) -> None:
        """同步历史窗口参数。"""
        self.momentum_lookback = max(
            self.mom_lookback + self.mom_skip_recent,
            self.momentum_lookback,
        )
        self.volatility_lookback = max(
            self.ivol_lookback, self.volatility_lookback,
        )


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------

@StrategyRegistry().register("a_stock_enhanced_v2")
class AStockEnhancedV2Strategy(EquityRotationStrategy):
    """A 股增强多因子 v2 策略。

    8 因子加权打分：EP + ROE + MOM + IVOL + STR + Analyst + Holder + Forecast
    """

    def __init__(
        self, config: AStockEnhancedV2Config | None = None, **kwargs,
    ) -> None:
        if config is None and kwargs:
            valid = {
                k: v for k, v in kwargs.items()
                if k in AStockEnhancedV2Config.__dataclass_fields__
            }
            config = AStockEnhancedV2Config(**valid)
        super().__init__()
        self._cfg = config or AStockEnhancedV2Config()

    @property
    def config(self) -> AStockEnhancedV2Config:
        return self._cfg

    @classmethod
    def from_params(cls, **params) -> "AStockEnhancedV2Strategy":
        """从参数字典创建策略。"""
        cfg = AStockEnhancedV2Config(**{
            k: v for k, v in params.items()
            if k in AStockEnhancedV2Config.__dataclass_fields__
        })
        return cls(cfg)

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """8 因子打分选股。"""
        if not candidates:
            return []
        cfg = self._cfg
        scores: dict[str, float] = {c.symbol: 0.0 for c in candidates}
        td = ""
        if context.current_time:
            td = context.current_time.strftime("%Y%m%d")

        # ── F1: EP ──
        if cfg.weight_ep > 0:
            vals = compute_ep_factor(candidates, metric=cfg.ep_pe_metric)
            self.rank_normalize(
                candidates, vals, weight=cfg.weight_ep, scores=scores,
            )

        # ── F2: ROE ──
        if cfg.weight_roe > 0:
            vals = compute_quality_factor(candidates, metric=cfg.roe_metric)
            self.rank_normalize(
                candidates, vals, weight=cfg.weight_roe, scores=scores,
            )

        # ── F3: MOM_12_1 ──
        if cfg.weight_mom > 0:
            vals = compute_momentum_factor(
                self._price_history, candidates,
                lookback=cfg.mom_lookback,
                skip_recent=cfg.mom_skip_recent,
                invert=False,
            )
            self.rank_normalize(
                candidates, vals, weight=cfg.weight_mom, scores=scores,
            )

        # ── F4: IVOL ──
        if cfg.weight_ivol > 0:
            vals = compute_ivol_factor(
                self._price_history, candidates,
                market_returns=None,
                lookback=cfg.ivol_lookback,
            )
            self.rank_normalize(
                candidates, vals, weight=cfg.weight_ivol, scores=scores,
            )

        # ── F5: STR (短期反转) ──
        if cfg.weight_str > 0:
            vals = compute_str_factor(
                self._price_history, candidates,
                lookback=cfg.str_lookback,
            )
            self.rank_normalize(
                candidates, vals, weight=cfg.weight_str, scores=scores,
            )

        # ── F6: Analyst Revision ──
        if cfg.weight_analyst > 0 and td:
            vals = compute_analyst_revision_factor(
                candidates,
                cache_dir=cfg.analyst_cache_dir,
                trade_date=td,
            )
            if vals:
                self.rank_normalize(
                    candidates, vals,
                    weight=cfg.weight_analyst, scores=scores,
                )

        # ── F7: Holder Change ──
        if cfg.weight_holder > 0 and td:
            vals = compute_holder_change_factor(
                candidates,
                cache_dir=cfg.holder_cache_dir,
                trade_date=td,
            )
            if vals:
                self.rank_normalize(
                    candidates, vals,
                    weight=cfg.weight_holder, scores=scores,
                )

        # ── F8: Forecast Surprise ──
        if cfg.weight_forecast > 0 and td:
            vals = compute_forecast_surprise_factor(
                candidates,
                cache_dir=cfg.forecast_cache_dir,
                trade_date=td,
            )
            if vals:
                self.rank_normalize(
                    candidates, vals,
                    weight=cfg.weight_forecast, scores=scores,
                )

        return self.rank_sort(scores)
