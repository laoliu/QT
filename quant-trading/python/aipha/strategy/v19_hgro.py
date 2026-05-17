"""v19_hgro 微盘成长因子轮动策略 — 事件驱动引擎版

将 scripts/r103_v2_scan.py 中验证的 v19_hgro 因子体系
集成到 EquityRotationStrategy 事件驱动回测框架。

因子权重（来自 R103 v19_hgro 优化结果）：
- small_size: 0.40  (-log(circ_mv), 微盘溢价)
- mom_12_1:   0.25  (12月-1月动量, 跳过最近20天)
- growth:     0.20  (净利润YoY增速, PIT)
- ep:         0.10  (1/PE, 盈利收益率)
- bp:         0.05  (1/PB, 账面价值)

选股范围：市值 2-30 亿，持仓 15 只，月频调仓（20天）。
可选 vol_target 波动率缩放层（P1 安全网）。

对齐 copilot-instructions.md §4.3/§13.1/§13.2：
- 策略只输出 OrderIntent，不直接改资金/持仓
- 使用 MarketRulesCNEquity（T+1、涨跌停）
- 注册到 StrategyRegistry
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
    compute_momentum_factor,
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

# §13.8: 风控逻辑必须使用共享模块
from aipha.risk.ml_risk_controls import (
    MddGradientDeleverager,
    VolTargetScaler,
)

logger = logging.getLogger(__name__)


@dataclass
class V19HgroConfig(EquityRotationConfig):
    """v19_hgro 微盘成长策略配置"""
    # --- 持仓 ---
    top_k: int = 15
    rebalance_days: int = 20       # ~月频 (20交易日)
    buffer_k: int = 5              # 缓冲区

    # --- 选股范围 ---
    min_mv: float = 20_000.0      # 2亿（万元）
    max_mv: float = 300_000.0     # 30亿（万元）
    min_price: float = 2.0
    max_price: float = 100.0
    min_turnover: float = 200.0   # 万元
    min_listed_days: int = 120    # 排除次新
    max_pb: float = 5.0           # PB上限

    # --- 因子权重 (来自 R103 v19_hgro) ---
    weight_small_size: float = 0.40
    weight_momentum: float = 0.25
    weight_growth: float = 0.20
    weight_ep: float = 0.10
    weight_bp: float = 0.05

    # --- 动量参数 ---
    momentum_lookback: int = 240   # ~12个月
    momentum_skip_recent: int = 20 # 跳过最近1个月

    # --- 波动率缩放 (P1) ---
    vol_scaling_enabled: bool = False
    vol_target: float = 0.20      # 目标年化波动率
    vol_lookback: int = 120       # ~6个月回望

    # --- MDD 梯度减仓 —— 使用 MddGradientDeleverager 共享模块 (5级) ---
    mdd_gradient_enabled: bool = False     # 默认关闭（需在 YAML 中开启）

    # --- 风控 ---
    stop_loss_pct: float = 0.0    # 不启用固定止损(月频)
    trailing_stop_pct: float = 0.0
    max_industry_pct: float = 1.0  # 行业不限(与v19一致)


@StrategyRegistry().register("v19_hgro")
class V19HgroStrategy(EquityRotationStrategy):
    """v19_hgro 微盘成长因子轮动策略"""

    name = "v19_hgro"

    def __init__(self, config: V19HgroConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or V19HgroConfig(**kwargs)
        self._vol_scale: float = 1.0  # 当前波动率缩放因子
        self._nav_high: float = 0.0   # NAV 历史高点
        self._mdd_scale: float = 1.0  # MDD 梯度缩放因子

    @property
    def config(self) -> V19HgroConfig:
        return self._config

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """v19_hgro 五因子排名融合

        因子方向（越高越好）：
        - small_size: -log(circ_mv)，市值越小分越高
        - mom_12_1:   12月动量跳过近1月，趋势延续
        - growth:     净利润YoY增速因子，高增长
        - ep:         1/PE，高盈利收益率
        - bp:         1/PB，低估值
        """
        cfg = self._config
        if not candidates:
            return []

        scores: dict[str, float] = {}

        # 1) 小市值因子 (权重 0.40) — 核心因子
        f_size = compute_size_factor(candidates)
        self.rank_normalize(
            candidates, f_size, cfg.weight_small_size, scores,
            nan_handling="median",
        )

        # 2) 12-1 动量因子 (权重 0.25)
        f_mom = compute_momentum_factor(
            self._price_history, candidates,
            lookback=cfg.momentum_lookback,
            skip_recent=cfg.momentum_skip_recent,
            invert=False,
        )
        self.rank_normalize(
            candidates, f_mom, cfg.weight_momentum, scores,
            nan_handling="median",
        )

        # 3) 成长因子 (权重 0.20)
        f_growth = compute_earnings_growth_factor(candidates)
        self.rank_normalize(
            candidates, f_growth, cfg.weight_growth, scores,
            nan_handling="median",
        )

        # 4) EP 盈利收益率因子 (权重 0.10)
        f_ep = compute_ep_factor(candidates)
        self.rank_normalize(
            candidates, f_ep, cfg.weight_ep, scores,
            nan_handling="median",
        )

        # 5) BP 账面价值因子 (权重 0.05)
        f_bp = compute_bp_factor(candidates)
        self.rank_normalize(
            candidates, f_bp, cfg.weight_bp, scores,
            nan_handling="median",
        )

        return self.rank_sort(scores)

    def _extra_filter(
        self,
        candidate: EquityCandidateInfo,
        context: StrategyContext,
    ) -> bool:
        """额外过滤: PB上限 + PE>0

        返回 True 表示保留候选标的。
        """
        cfg = self._config
        md = candidate.metadata or {}
        # PB 上限 (排除高杠杆/重资产)
        pb = md.get("pb")
        if pb is not None and pb > cfg.max_pb:
            return False
        # PE > 0 (排除亏损股)
        pe = md.get("pe_ttm")
        if pe is not None and pe <= 0:
            return False
        return True

    def _on_bar_update_state(
        self, bars: dict[str, "Bar"], context: StrategyContext
    ) -> None:
        """更新 MDD 梯度减仓 + 波动率缩放因子。

        §13.8: 委托到共享风控模块 (MddGradientDeleverager + VolTargetScaler)。
        """
        import pandas as pd

        cfg = self._config

        # --- MDD 梯度减仓 (共享 5 级梯度) ---
        if cfg.mdd_gradient_enabled:
            nav = context.nav
            if nav > 0:
                nav_history = getattr(context, "nav_history", None)
                if nav_history and len(nav_history) >= 2:
                    nav_series = pd.Series(nav_history)
                    if not hasattr(self, "_mdd_deleverager"):
                        self._mdd_deleverager = MddGradientDeleverager()
                    mdd = self._mdd_deleverager.compute_mdd(nav_series)
                    self._mdd_scale = self._mdd_deleverager.get_scale(mdd)
                else:
                    self._mdd_scale = 1.0
        else:
            self._mdd_scale = 1.0

        # --- 波动率缩放 (共享 VolTargetScaler) ---
        if not cfg.vol_scaling_enabled:
            self._vol_scale = 1.0
        else:
            nav_history = getattr(context, "nav_history", None)
            if nav_history is None or len(nav_history) < 30:
                self._vol_scale = 1.0
            else:
                import pandas as pd
                nav_series = pd.Series(nav_history[-cfg.vol_lookback:])
                if not hasattr(self, "_vol_scaler"):
                    self._vol_scaler = VolTargetScaler(
                        vol_target=cfg.vol_target,
                        lookback=min(cfg.vol_lookback, 60),
                    )
                self._vol_scale = self._vol_scaler.get_scale(nav_series)

        # --- 合并 position_scale（取较小值）---
        final_scale = min(self._vol_scale, self._mdd_scale)
        context.metadata["position_scale"] = final_scale
