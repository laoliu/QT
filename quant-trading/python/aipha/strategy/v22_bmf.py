"""v22_bmf 均衡多因子轮动策略 — 事件驱动引擎版

复刻"中国精选价值-30"组合的核心逻辑（ZH1400599, AR~36%, MDD~-15%）。
基于 Xueqiu 深度调研 + v19_hgro 因子分析重新设计：

设计核心：
1. **6 因子聚焦配权**（参考 v19 的 alpha 来源分析）
   - 规模: 小市值因子 (0.25) — A 股最强单因子
   - 动量: 12-1 月动量 (0.25)
   - 价值: EP (0.15)
   - 成长: 扣非利润增速 (0.15)
   - 质量: ROE (0.10)
   - 低波动: EWMA (0.10)

2. **中小盘聚焦** — 5-150 亿市值范围
   与 v19 的 2-30 亿微盘差异化，提供组合分散性

3. **回撤择时可选** — 默认关闭（实证显示拖累收益）

4. **行业分散** — 单行业最大 20%，持仓 20 只
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
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
class V22BmfConfig(EquityRotationConfig):
    """v22_bmf 中小盘多因子策略配置"""
    # --- 持仓 ---
    top_k: int = 20
    rebalance_days: int = 20
    buffer_k: int = 8
    # --- 选股范围（中小盘：5-150 亿）---
    min_mv: float = 50_000.0       # 5 亿（万元）
    max_mv: float = 1_500_000.0    # 150 亿（万元）
    min_price: float = 3.0
    max_price: float = 200.0
    min_turnover: float = 500.0    # 万元
    min_listed_days: int = 120
    max_industry_pct: float = 0.20  # 单行业 ≤ 20%
    # --- 额外过滤 ---
    max_pb: float = 8.0
    min_roe: float = 0.0           # ROE > 0 (排除亏损)

    # --- 因子权重（6 因子，总和 1.0）---
    weight_size: float = 0.25          # 小市值因子（核心 alpha）
    weight_momentum: float = 0.25      # 12-1 月动量
    weight_ep: float = 0.15            # 低 PE 价值
    weight_profit_growth: float = 0.15 # 扣非利润增速
    weight_roe: float = 0.10           # ROE 质量
    weight_low_vol: float = 0.10       # EWMA 低波动

    # --- 动量参数 ---
    momentum_lookback: int = 240   # ~12 个月
    momentum_skip_recent: int = 20 # 跳过最近 1 个月
    volatility_lookback: int = 60  # EWMA 波动率窗口

    # --- 回撤择时参数 ---
    drawdown_timing_enabled: bool = False  # 默认关闭
    dd_reduce_threshold: float = 0.20
    dd_restore_threshold: float = 0.10
    dd_min_scale: float = 0.50
    dd_min_scale: float = 0.50          # 减仓最低比例

    # --- 风控 ---
    stop_loss_pct: float = 0.0     # 不启用固定止损（依赖择时）
    trailing_stop_pct: float = 0.0
    cash_reserve_pct: float = 0.05


@StrategyRegistry().register("v22_bmf")
class V22BmfStrategy(EquityRotationStrategy):
    """v22_bmf 均衡多因子轮动策略"""

    name = "v22_bmf"

    def __init__(self, config: V22BmfConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or V22BmfConfig(**kwargs)
        # 回撤择时状态
        self._nav_high: float = 0.0
        self._timing_scale: float = 1.0
        pass

    @property
    def config(self) -> V22BmfConfig:
        return self._config

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """6 因子聚焦评分。

        因子配权（总 1.0）：
        - Size  0.25: -log(circ_mv)，中小市值溢价
        - Momentum 0.25: 12-1月动量
        - EP 0.15: 1/PE，盈利收益率
        - Profit Growth 0.15: 扣非利润增速
        - ROE 0.10: 盈利质量
        - Low Vol 0.10: -EWMA 波动率
        """
        cfg = self._config
        if not candidates:
            return []

        scores: dict[str, float] = {}
        nh = "median"

        # === Size 0.25 ===
        f_size = compute_size_factor(candidates)
        self.rank_normalize(candidates, f_size, cfg.weight_size,
                            scores, nan_handling=nh)

        # === Momentum 0.25 ===
        f_mom = compute_momentum_factor(
            self._price_history, candidates,
            lookback=cfg.momentum_lookback,
            skip_recent=cfg.momentum_skip_recent,
            invert=False,
        )
        self.rank_normalize(candidates, f_mom, cfg.weight_momentum,
                            scores, nan_handling=nh)

        # === EP 0.15 ===
        f_ep = compute_ep_factor(candidates)
        self.rank_normalize(candidates, f_ep, cfg.weight_ep,
                            scores, nan_handling=nh)

        # === Profit Growth 0.15 ===
        f_growth = compute_earnings_growth_factor(
            candidates, field="dt_netprofit_yoy",
        )
        self.rank_normalize(candidates, f_growth, cfg.weight_profit_growth,
                            scores, nan_handling=nh)

        # === ROE 0.10 ===
        f_roe = compute_quality_factor(candidates, metric="roe")
        self.rank_normalize(candidates, f_roe, cfg.weight_roe,
                            scores, nan_handling=nh)

        # === Low Vol 0.10 ===
        f_vol = compute_ewma_volatility_factor(
            self._price_history, candidates,
            lookback=cfg.volatility_lookback, halflife=20,
        )
        self.rank_normalize(candidates, f_vol, cfg.weight_low_vol,
                            scores, nan_handling=nh)

        return self.rank_sort(scores)

    def _extra_filter(
        self,
        candidate: EquityCandidateInfo,
        context: StrategyContext,
    ) -> bool:
        """额外过滤: PB上限 + PE>0 + ROE下限"""
        cfg = self._config
        md = candidate.metadata or {}
        # PB 上限（排除高杠杆/重资产异常值）
        pb = md.get("pb")
        if pb is not None and pb > cfg.max_pb:
            return False
        # PE > 0（排除亏损股）
        pe = md.get("pe_ttm")
        if pe is not None and pe <= 0:
            return False
        # ROE 下限（排除持续亏损）
        roe = md.get("roe")
        if roe is not None and roe < cfg.min_roe:
            return False
        return True

    def _on_bar_update_state(
        self, bars: dict[str, "Bar"], context: StrategyContext,
    ) -> None:
        """更新回撤择时状态。

        回撤择时逻辑（来自 ZH1400599 创建者）：
        - 跟踪组合净值最高水位（NAV high watermark）
        - 当 drawdown > dd_reduce_threshold (20%) → 减仓至 dd_min_scale (50%)
        - 当 drawdown < dd_restore_threshold (10%) → 恢复满仓
        - 中间区域保持上一次状态（迟滞机制避免频繁切换）
        - 通过 context.metadata["position_scale"] 注入基类的
          _generate_rebalance() 实现仓位缩放
        """
        cfg = self._config
        if not cfg.drawdown_timing_enabled:
            self._timing_scale = 1.0
            context.metadata["position_scale"] = 1.0
            return

        nav = context.nav
        if nav <= 0:
            return

        # 更新净值最高水位
        if nav > self._nav_high:
            self._nav_high = nav

        # 计算当前回撤
        if self._nav_high > 0:
            current_dd = 1.0 - nav / self._nav_high
        else:
            current_dd = 0.0

        # 迟滞择时：
        # - 回撤超过阈值 → 减仓
        # - 回撤恢复到低阈值 → 满仓
        # - 中间 → 保持当前状态
        prev_scale = self._timing_scale
        if current_dd >= cfg.dd_reduce_threshold:
            self._timing_scale = cfg.dd_min_scale
        elif current_dd <= cfg.dd_restore_threshold:
            self._timing_scale = 1.0
        # else: 保持 prev_scale（迟滞区）

        if self._timing_scale != prev_scale:
            logger.info(
                "[%s] 回撤择时切换: MDD=%.1f%%, scale %.0f%% → %.0f%%",
                self.name, current_dd * 100,
                prev_scale * 100, self._timing_scale * 100,
            )

        context.metadata["position_scale"] = self._timing_scale
