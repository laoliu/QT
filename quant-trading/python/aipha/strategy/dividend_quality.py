"""A 股红利质量策略 (O7)

差异化定位：
  - 聚焦 **红利+现金流+盈利稳定性** 因子，与已有 EP+ROE+MOM+IVOL 组合正交
  - 目标：低波动、高分红、现金流充裕的优质公司
  - 作为组合分散化来源，与 ML 策略低相关

五因子权重：
  1. DV_RATIO  股息率                 (weight=0.25)
  2. FCFY      自由现金流收益率       (weight=0.25)
  3. ROE       股东权益收益率         (weight=0.20)
  4. VOL       低波动率               (weight=0.15)
  5. EP        盈利收益率             (weight=0.15)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_dividend_factor,
    compute_ep_factor,
    compute_fcfy_factor,
    compute_quality_factor,
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


@dataclass
class DividendQualityConfig(EquityRotationConfig):
    """红利质量策略配置."""

    # 因子权重
    weight_dividend: float = 0.25
    weight_fcfy: float = 0.25
    weight_roe: float = 0.20
    weight_vol: float = 0.15
    weight_ep: float = 0.15

    # 指标字段
    dividend_field: str = "dv_ratio"
    roe_metric: str = "roe"
    ep_metric: str = "pe_ttm"
    vol_lookback: int = 60

    # 硬性门槛
    min_dividend: float = 0.5   # 最低股息率 0.5%
    min_roe: float = 5.0        # 最低 ROE 5%

    def __post_init__(self) -> None:
        """同步 volatility lookback 到基类."""
        self.volatility_lookback = max(self.vol_lookback, self.volatility_lookback)


@StrategyRegistry().register("dividend_quality")
class DividendQualityStrategy(EquityRotationStrategy):
    """A 股红利质量策略."""

    def __init__(self, config: DividendQualityConfig | None = None, **kwargs) -> None:
        if config is None and kwargs:
            valid = {
                k: v for k, v in kwargs.items()
                if k in DividendQualityConfig.__dataclass_fields__
            }
            config = DividendQualityConfig(**valid)
        super().__init__()
        self._cfg = config or DividendQualityConfig()

    @property
    def config(self) -> DividendQualityConfig:
        return self._cfg

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """五因子打分：红利+FCFY+ROE+低波+EP."""
        if not candidates:
            return []
        cfg = self._cfg

        # 硬性门槛过滤
        filtered = self._apply_hard_filters(candidates)
        if not filtered:
            logger.warning("dividend_quality: 无标的通过硬性门槛")
            return []

        scores: dict[str, float] = {c.symbol: 0.0 for c in filtered}

        # 因子 1: 股息率
        if cfg.weight_dividend > 0:
            dv = compute_dividend_factor(filtered, field=cfg.dividend_field)
            self.rank_normalize(filtered, dv, cfg.weight_dividend, scores)

        # 因子 2: 自由现金流收益率
        if cfg.weight_fcfy > 0:
            fcfy = compute_fcfy_factor(filtered)
            self.rank_normalize(filtered, fcfy, cfg.weight_fcfy, scores)

        # 因子 3: ROE 质量
        if cfg.weight_roe > 0:
            roe = compute_quality_factor(filtered, metric=cfg.roe_metric)
            self.rank_normalize(filtered, roe, cfg.weight_roe, scores)

        # 因子 4: 低波动
        if cfg.weight_vol > 0:
            vol = compute_volatility_factor(
                self._price_history, filtered, lookback=cfg.vol_lookback,
            )
            self.rank_normalize(filtered, vol, cfg.weight_vol, scores)

        # 因子 5: EP 盈利收益率
        if cfg.weight_ep > 0:
            ep = compute_ep_factor(filtered, metric=cfg.ep_metric)
            self.rank_normalize(filtered, ep, cfg.weight_ep, scores)

        return self.rank_sort(scores)

    def _apply_hard_filters(
        self, candidates: list[EquityCandidateInfo],
    ) -> list[EquityCandidateInfo]:
        """硬性门槛：最低股息率 + 最低 ROE."""
        import math
        cfg = self._cfg
        result = []
        for c in candidates:
            dv = c.metadata.get(cfg.dividend_field)
            if dv is not None and math.isfinite(dv) and dv >= cfg.min_dividend:
                roe = c.metadata.get(cfg.roe_metric)
                if roe is not None and math.isfinite(roe) and roe >= cfg.min_roe:
                    result.append(c)
        if len(result) < cfg.top_k:
            logger.info(
                "dividend_quality: 硬性门槛后 %d 只 < top_k=%d, 放宽门槛",
                len(result), cfg.top_k,
            )
            # 放宽：只要求股息率 > 0
            result = [
                c for c in candidates
                if (c.metadata.get(cfg.dividend_field) or 0) > 0
            ]
        return result
