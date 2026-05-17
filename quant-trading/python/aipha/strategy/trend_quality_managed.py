"""A 股趋势质量管理策略 (Trend Quality Managed)

核心逻辑：质量选股 + MA 趋势仓位管理。
- 基础选股：ROE + 盈利稳定 + 低波动（质量因子）
- 仓位叠加：根据沪深 300 短期 MA vs 长期 MA 调整仓位

学术基础：
- Moskowitz, Ooi, Pedersen (2012) — 时间序列动量
- 趋势跟随叠加在质量因子上可显著降低最大回撤

与 stable_compounder 的差异：
- stable_compounder：纯基本面选股，无择时
- **trend_quality_managed**：同样质量选股，但叠加 MA 趋势动态调仓

Alpha 来源（4 因子 + 1 仓位管理信号）：
1. **质量 ROE** (30%)
2. **盈利稳定** (30%)
3. **低波动** (20%)
4. **估值** (20%)
5. **仓位管理**：MA20 > MA60 → 满仓；MA20 < MA60 → 半仓

预期表现：AR 15-22%, MDD 15-25%, Sharpe 0.6-1.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_earnings_stability_factor,
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
    from aipha.backtest.engine import Bar

logger = logging.getLogger(__name__)


@dataclass
class TrendQualityManagedConfig(EquityRotationConfig):
    """趋势质量管理配置。"""

    top_k: int = 30
    rebalance_days: int = 15
    min_price: float = 5.0
    max_price: float = 200.0
    min_turnover: float = 500.0
    min_listed_days: int = 250
    stop_loss_pct: float = 0.15
    trailing_stop_pct: float = 0.12
    buffer_k: int = 8
    max_single_pct: float = 0.05
    min_mv: float = 500_000.0       # 50 亿
    max_mv: float = float("inf")
    max_industry_pct: float = 0.20

    # 因子权重
    weight_quality: float = 0.30
    weight_stability: float = 0.30
    weight_vol: float = 0.20
    weight_value: float = 0.20

    # MA 择时参数
    ma_short: int = 20
    ma_long: int = 60
    bear_scale: float = 0.50        # 空头排列时仓位缩放

    # 基本面
    min_roe: float = 5.0
    max_pe: float = 60.0

    # 内部状态
    _index_history: list[float] = field(default_factory=list)


@StrategyRegistry().register("trend_quality_managed")
class TrendQualityManagedStrategy(EquityRotationStrategy):
    """趋势质量管理：质量选股 + MA 趋势动态调仓。"""

    name = "trend_quality_managed"

    def __init__(
        self, config: TrendQualityManagedConfig | None = None, **kwargs,
    ):
        super().__init__()
        self._config = config or TrendQualityManagedConfig(**kwargs)

    @property
    def config(self) -> TrendQualityManagedConfig:
        return self._config

    def _on_bar_update_state(
        self, bars: dict[str, "Bar"], context: StrategyContext,
    ) -> None:
        """每 bar 更新基准指数历史 + 计算 MA 信号 → 设置 position_scale。"""
        cfg = self._config
        # 从 benchmark bar 获取指数价格
        bench_price = context.metadata.get("benchmark_close")
        if bench_price is None:
            # 降级：用持仓均价
            prices = [b.close for b in bars.values() if b.close > 0]
            bench_price = sum(prices) / len(prices) if prices else 0.0

        cfg._index_history.append(bench_price)
        max_len = cfg.ma_long + 10
        if len(cfg._index_history) > max_len:
            del cfg._index_history[:-max_len]

        # MA 信号
        if len(cfg._index_history) >= cfg.ma_long:
            ma_s = sum(cfg._index_history[-cfg.ma_short:]) / cfg.ma_short
            ma_l = sum(cfg._index_history[-cfg.ma_long:]) / cfg.ma_long
            if ma_l > 0 and ma_s < ma_l:
                context.metadata["position_scale"] = cfg.bear_scale
            else:
                context.metadata["position_scale"] = 1.0

    def _extra_filter(
        self, candidate: EquityCandidateInfo, context: StrategyContext,
    ) -> bool:
        cfg = self._config
        roe = candidate.metadata.get("roe")
        if roe is not None and roe < cfg.min_roe:
            return False
        pe = candidate.metadata.get("pe_ttm")
        if pe is not None and (pe <= 0 or pe > cfg.max_pe):
            return False
        return True

    def _score_candidates(
        self, candidates: list[EquityCandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """质量×稳定×低波×估值。"""
        cfg = self._config
        if not candidates:
            return []

        scores: dict[str, float] = {}

        f_roe = compute_quality_factor(candidates, metric="roe")
        self.rank_normalize(candidates, f_roe, cfg.weight_quality, scores)

        f_stab = compute_earnings_stability_factor(candidates)
        self.rank_normalize(candidates, f_stab, cfg.weight_stability, scores)

        f_vol = compute_volatility_factor(
            self._price_history, candidates,
            lookback=cfg.volatility_lookback,
        )
        self.rank_normalize(candidates, f_vol, cfg.weight_vol, scores)

        f_ep = compute_value_factor(candidates, metric="ep")
        self.rank_normalize(candidates, f_ep, cfg.weight_value, scores)

        return self.rank_sort(scores)
