"""A 股事件驱动 PEAD 策略 — 基于 SUE + 分析师修正 + 管理层增持。

.. deprecated::
    此策略已于 2026-05-11 经 5 位专家评审证伪 (CF-053)。
    AR=-0.26%, SR=0.03, MDD=69.7%。根因: 稀疏事件信号不适合截面排名选股。
    事件因子已迁移至 ML 特征管线 (pead_ml_features.py --pead-factors)。
    计划物理删除日期: 2026-08-01。

学术基础:
    - Bernard & Thomas (1989): PEAD, IC ~ 3-5%
    - Jegadeesh & Kim (2010): Analyst revision, IC 1-3%
    - Jeng, Metrick, Zeckhauser (2003): Insider trading

因子组合:
    1. SUE (标准化意外盈利)      — 权重 30%, 核心 alpha 来源
    2. Analyst Revision (分析师修正) — 权重 25%, 信息扩散信号
    3. Forecast Surprise (业绩预告)  — 权重 20%, 公告前信号
    4. Insider Net Buy (管理层增持)   — 权重 15%, 催化信号
    5. EP (盈利收益率)             — 权重 10%, 价值锚定

回测配置: configs/backtest/a_stock_event_pead_10y.yml
策略注册名: a_stock_event_pead
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.analyst_factors import (
    compute_analyst_revision_factor,
    compute_forecast_surprise_factor,
)
from aipha.factors.equity_factors import compute_ep_factor
from aipha.factors.sue_factor import (
    compute_insider_net_buy_factor,
    compute_sue_factor,
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
class AStockEventPEADConfig(EquityRotationConfig):
    """事件驱动 PEAD 策略配置。"""

    # ── 因子权重 ──
    weight_sue: float = 0.30
    weight_analyst: float = 0.25
    weight_forecast: float = 0.20
    weight_insider: float = 0.15
    weight_ep: float = 0.10

    # ── SUE 参数 ──
    sue_lookback_months: int = 12
    sue_min_history: int = 2

    # ── 分析师参数 ──
    analyst_lookback_months: int = 3
    analyst_min_analysts: int = 2

    # ── 增持参数 ──
    insider_lookback_days: int = 90

    # ── 指标字段 ──
    ep_pe_metric: str = "pe_ttm"

    # ── 数据缓存路径 ──
    express_cache_dir: str = "data/cache/advanced"
    analyst_cache_dir: str = "data/cache/alternative"
    forecast_cache_dir: str = "data/cache/advanced"
    holdertrade_cache_dir: str = "data/cache/holder"

    def __post_init__(self) -> None:
        """同步历史窗口。"""
        self.momentum_lookback = max(20, self.momentum_lookback)


@StrategyRegistry().register("a_stock_event_pead")
class AStockEventPEADStrategy(EquityRotationStrategy):
    """A 股事件驱动 PEAD 策略。

    .. deprecated::
        此策略已证伪 (CF-053, 2026-05-11)。事件因子已迁移至 ML 特征管线。

    5 因子加权: SUE + Analyst + Forecast + Insider + EP
    """

    def __init__(
        self,
        config: AStockEventPEADConfig | None = None,
        **kwargs,
    ) -> None:
        warnings.warn(
            "AStockEventPEADStrategy 已证伪 (CF-053, 2026-05-11). "
            "事件因子已迁移至 ML 特征管线 (--pead-factors). "
            "计划物理删除: 2026-08-01.",
            DeprecationWarning,
            stacklevel=2,
        )
        if config is None and kwargs:
            valid = {
                k: v for k, v in kwargs.items()
                if k in AStockEventPEADConfig.__dataclass_fields__
            }
            config = AStockEventPEADConfig(**valid)
        super().__init__()
        self._cfg = config or AStockEventPEADConfig()

    @property
    def config(self) -> AStockEventPEADConfig:
        return self._cfg

    @classmethod
    def from_params(cls, **params) -> "AStockEventPEADStrategy":
        """从参数字典创建策略。"""
        cfg = AStockEventPEADConfig(**{
            k: v for k, v in params.items()
            if k in AStockEventPEADConfig.__dataclass_fields__
        })
        return cls(cfg)

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """5 因子打分选股。"""
        if not candidates:
            return []
        cfg = self._cfg
        scores: dict[str, float] = {c.symbol: 0.0 for c in candidates}
        td = ""
        if context.current_time:
            td = context.current_time.strftime("%Y%m%d")

        # ── F1: SUE ──
        if cfg.weight_sue > 0 and td:
            vals = compute_sue_factor(
                candidates,
                express_cache_dir=cfg.express_cache_dir,
                analyst_cache_dir=cfg.analyst_cache_dir,
                trade_date=td,
                lookback_months=cfg.sue_lookback_months,
                min_history=cfg.sue_min_history,
            )
            if vals:
                self.rank_normalize(
                    candidates, vals,
                    weight=cfg.weight_sue, scores=scores,
                )

        # ── F2: Analyst Revision ──
        if cfg.weight_analyst > 0 and td:
            vals = compute_analyst_revision_factor(
                candidates,
                cache_dir=cfg.analyst_cache_dir,
                trade_date=td,
                lookback_months=cfg.analyst_lookback_months,
                min_analysts=cfg.analyst_min_analysts,
            )
            if vals:
                self.rank_normalize(
                    candidates, vals,
                    weight=cfg.weight_analyst, scores=scores,
                )

        # ── F3: Forecast Surprise ──
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

        # ── F4: Insider Net Buy ──
        if cfg.weight_insider > 0 and td:
            vals = compute_insider_net_buy_factor(
                candidates,
                holdertrade_cache_dir=cfg.holdertrade_cache_dir,
                trade_date=td,
                lookback_days=cfg.insider_lookback_days,
            )
            if vals:
                self.rank_normalize(
                    candidates, vals,
                    weight=cfg.weight_insider, scores=scores,
                )

        # ── F5: EP ──
        if cfg.weight_ep > 0:
            vals = compute_ep_factor(
                candidates, metric=cfg.ep_pe_metric,
            )
            if vals:
                self.rank_normalize(
                    candidates, vals,
                    weight=cfg.weight_ep, scores=scores,
                )

        return self.rank_sort(scores)
