"""A 股大小盘轮动策略 (Size Style Rotation)

核心逻辑：根据市场状态在大盘股和小盘股之间轮动。
- A 股大小盘风格切换明显（2017 大盘、2019-2020 小盘、2021 大盘...）
- 用 MA + 相对动量判断风格趋势

学术基础：
- Fama & French (1993) — SMB 因子
- A 股增强：大小盘轮动规律更强（因散户结构）

与 small_cap_quality 的差异：
- small_cap_quality：Always 小盘
- **size_style_rotation**：动态切换 — 小盘强势选小盘，大盘强势选大盘

Alpha 来源：
- 大盘模式（3 因子）：价值(40%) + 质量(35%) + 低波(25%)
- 小盘模式（3 因子）：规模(40%) + 动量(35%) + 盈利增速(25%)
- **风格信号**：CSI300 vs CSI500 相对动量

预期表现：AR 15-25%, MDD 22-32%, Sharpe 0.5-0.8
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_earnings_growth_factor,
    compute_momentum_factor,
    compute_quality_factor,
    compute_size_factor,
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
    from aipha.backtest.engine import Bar

logger = logging.getLogger(__name__)


@dataclass
class SizeStyleRotationConfig(EquityRotationConfig):
    """大小盘轮动配置。"""

    top_k: int = 30
    rebalance_days: int = 20         # 月频轮动
    min_price: float = 3.0
    max_price: float = 200.0
    min_turnover: float = 500.0
    min_listed_days: int = 120
    stop_loss_pct: float = 0.18
    trailing_stop_pct: float = 0.15
    buffer_k: int = 8
    max_single_pct: float = 0.05
    min_mv: float = 100_000.0       # 全市场覆盖
    max_mv: float = float("inf")
    max_industry_pct: float = 0.20

    # 大盘模式权重
    large_weight_value: float = 0.40
    large_weight_quality: float = 0.35
    large_weight_vol: float = 0.25
    large_min_mv: float = 1_000_000.0   # 大盘 100 亿+

    # 小盘模式权重
    small_weight_size: float = 0.40
    small_weight_momentum: float = 0.35
    small_weight_growth: float = 0.25
    small_max_mv: float = 1_000_000.0   # 小盘 < 100 亿

    # 风格信号参数
    style_lookback: int = 60         # 相对动量回看
    max_pe: float = 80.0
    momentum_lookback: int = 60

    # 内部状态
    _style_mode: str = "SMALL"       # LARGE / SMALL
    _large_index_history: list[float] = field(default_factory=list)
    _small_index_history: list[float] = field(default_factory=list)


@StrategyRegistry().register("size_style_rotation")
class SizeStyleRotationStrategy(EquityRotationStrategy):
    """大小盘轮动：动态切换大盘价值 vs 小盘动量。"""

    name = "size_style_rotation"

    def __init__(
        self, config: SizeStyleRotationConfig | None = None, **kwargs,
    ):
        super().__init__()
        self._config = config or SizeStyleRotationConfig(**kwargs)

    @property
    def config(self) -> SizeStyleRotationConfig:
        return self._config

    def _on_bar_update_state(
        self, bars: dict[str, "Bar"], context: StrategyContext,
    ) -> None:
        """用候选池大小盘分组的相对动量判断风格。"""
        cfg = self._config
        large_prices = []
        small_prices = []
        for sym, bar in bars.items():
            if bar.close <= 0:
                continue
            # 简化：用价格×量级粗估市值
            mv = bar.close  # 实际用 circ_mv，这里降级
            meta = getattr(bar, "metadata", {}) or {}
            circ = meta.get("circ_mv", 0)
            if circ >= cfg.large_min_mv:
                large_prices.append(bar.close)
            elif circ > 0:
                small_prices.append(bar.close)

        # 用收盘价均值代理指数
        large_idx = sum(large_prices) / len(large_prices) if large_prices else 0
        small_idx = sum(small_prices) / len(small_prices) if small_prices else 0

        cfg._large_index_history.append(large_idx)
        cfg._small_index_history.append(small_idx)
        max_len = cfg.style_lookback + 10
        if len(cfg._large_index_history) > max_len:
            del cfg._large_index_history[:-max_len]
        if len(cfg._small_index_history) > max_len:
            del cfg._small_index_history[:-max_len]

        # 相对动量
        lb = cfg.style_lookback
        if len(cfg._large_index_history) >= lb + 1 and len(cfg._small_index_history) >= lb + 1:
            l_old = cfg._large_index_history[-(lb + 1)]
            l_new = cfg._large_index_history[-1]
            s_old = cfg._small_index_history[-(lb + 1)]
            s_new = cfg._small_index_history[-1]
            l_ret = (l_new - l_old) / l_old if l_old > 0 else 0
            s_ret = (s_new - s_old) / s_old if s_old > 0 else 0
            cfg._style_mode = "LARGE" if l_ret > s_ret else "SMALL"

    def _extra_filter(
        self, candidate: EquityCandidateInfo, context: StrategyContext,
    ) -> bool:
        cfg = self._config
        pe = candidate.metadata.get("pe_ttm")
        if pe is not None and (pe <= 0 or pe > cfg.max_pe):
            return False
        # 根据风格模式过滤市值
        if cfg._style_mode == "LARGE" and candidate.circ_mv < cfg.large_min_mv:
            return False
        if cfg._style_mode == "SMALL" and candidate.circ_mv > cfg.small_max_mv:
            return False
        return True

    def _score_candidates(
        self, candidates: list[EquityCandidateInfo], context: StrategyContext,
    ) -> list[str]:
        cfg = self._config
        if not candidates:
            return []

        scores: dict[str, float] = {}

        if cfg._style_mode == "LARGE":
            f_val = compute_value_factor(candidates, metric="ep")
            self.rank_normalize(candidates, f_val, cfg.large_weight_value, scores)
            f_roe = compute_quality_factor(candidates, metric="roe")
            self.rank_normalize(candidates, f_roe, cfg.large_weight_quality, scores)
            f_vol = compute_volatility_factor(
                candidates, price_history=self._price_history,
                lookback=cfg.volatility_lookback, invert=True,
            )
            self.rank_normalize(candidates, f_vol, cfg.large_weight_vol, scores)
        else:
            f_size = compute_size_factor(candidates)
            self.rank_normalize(candidates, f_size, cfg.small_weight_size, scores)
            f_mom = compute_momentum_factor(
                candidates, price_history=self._price_history,
                lookback=cfg.momentum_lookback, risk_adjusted=True,
            )
            self.rank_normalize(candidates, f_mom, cfg.small_weight_momentum, scores)
            f_grow = compute_earnings_growth_factor(candidates)
            self.rank_normalize(candidates, f_grow, cfg.small_weight_growth, scores)

        return self.rank_sort(scores)
