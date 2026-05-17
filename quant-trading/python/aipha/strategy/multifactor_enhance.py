"""A 股多因子指数增强策略

目标：在沪深300或中证500基准上获取稳定超额收益（Alpha）。

Alpha 来源：
1. **价值因子** — 低 PE/PB 标的具有估值回归空间
2. **质量因子** — 高 ROE/盈利稳定的公司长期表现更优
3. **动量因子** — 短期趋势延续（20 日动量）
4. **低波动因子** — 低波动标的风险调整后收益更优
5. **小市值因子** — 指数成分内的相对小市值标的有超额收益

继承 EquityRotationStrategy 基类，复用预过滤/止损/调仓/轮动指令生成逻辑，
仅实现 _score_candidates() 多因子打分排名。

风控：
- 行业偏离约束（与基准行业分布偏离不超过阈值）
- 单票持仓上限
- 组合跟踪误差控制（通过调仓频率和仓位限制间接实现）

对齐 copilot-instructions.md：
- 使用 MarketRulesCNEquity + FeeModelCNEquity
- 所有假设写入 configs/backtest/multifactor_enhance.yml
- 因子计算无前视偏差
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_accruals_quality_factor,
    compute_momentum_factor,
    compute_piotroski_factor,
    compute_quality_factor,
    compute_size_factor,
    compute_valuation_percentile_factor,
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
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class MultiFactorEnhanceConfig(EquityRotationConfig):
    """多因子指数增强策略配置

    继承 EquityRotationConfig，添加指数增强特有字段。
    """

    # 覆盖默认值
    top_k: int = 50
    rebalance_days: int = 20     # 月度调仓
    min_price: float = 5.0
    max_price: float = 200.0
    min_turnover: float = 1000.0  # 万元
    min_listed_days: int = 120
    stop_loss_pct: float = 0.20
    max_single_pct: float = 0.05   # 单票最大5%
    buffer_k: int = 10

    # 指数增强特有
    benchmark: str = "000300.SH"  # 沪深300

    # 因子权重（合计=1.0）
    weight_value: float = 0.25     # 价值因子
    weight_quality: float = 0.20   # 质量因子
    weight_momentum: float = 0.20  # 动量因子
    weight_volatility: float = 0.20  # 低波动因子
    weight_size: float = 0.15     # 小市值偏好

    # 因子参数
    volatility_lookback: int = 60
    value_metric: str = "pe_ttm"  # pe_ttm 或 pb

    # ---------- 新增基本面因子权重（默认 0.0，向后兼容） ----------
    weight_piotroski: float = 0.0          # Piotroski F-Score
    weight_valuation_pct: float = 0.0      # 估值历史百分位
    weight_accruals: float = 0.0           # 应计盈余质量
    weight_dividend: float = 0.0           # 股息率

    # 新增因子参数
    piotroski_min_dims: int = 5
    valuation_min_history: int = 20


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------

@StrategyRegistry().register("multifactor_enhance")
class MultiFactorEnhanceStrategy(EquityRotationStrategy):
    """A 股多因子指数增强策略

    在指数成分股中通过多因子排名选股，构建高 Alpha 组合。
    因子组合：价值 + 质量 + 动量 + 低波动 + 小市值。
    """

    name = "multifactor_enhance"

    def __init__(self, config: MultiFactorEnhanceConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or MultiFactorEnhanceConfig(**kwargs)

    @property
    def config(self) -> MultiFactorEnhanceConfig:
        """策略配置。"""
        return self._config

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """多因子排名融合，返回 top_k 个 symbol。

        使用 aipha.factors.equity_factors 模块的标准化因子函数，
        避免内联重复计算，保持因子逻辑可复用与可测试。
        """
        cfg = self._config
        if not candidates:
            return []

        scores: dict[str, float] = {}

        # 价值因子 (低 PE/PB → 取负 → 越大越优)
        f_value = compute_value_factor(candidates, metric=cfg.value_metric)
        self.rank_normalize(candidates, f_value, cfg.weight_value, scores)

        # 质量因子 (高 ROE → 越大越优)
        f_quality = compute_quality_factor(candidates, metric="roe")
        self.rank_normalize(candidates, f_quality, cfg.weight_quality, scores)

        # 动量因子 — 使用 equity_factors 统一实现
        f_mom = compute_momentum_factor(
            self._price_history, candidates, lookback=cfg.momentum_lookback,
            skip_recent=cfg.momentum_skip_recent,
            multi_period=cfg.momentum_multi_period,
        )
        self.rank_normalize(candidates, f_mom, cfg.weight_momentum, scores)

        # 波动率因子 (低波动 → 取负) — 使用 equity_factors 统一实现
        f_vol = compute_volatility_factor(
            self._price_history, candidates,
            lookback=cfg.volatility_lookback,
        )
        self.rank_normalize(candidates, f_vol, cfg.weight_volatility, scores)

        # 小市值因子
        f_size = compute_size_factor(candidates)
        self.rank_normalize(candidates, f_size, cfg.weight_size, scores)

        # ---------- Piotroski F-Score（可选） ----------
        if cfg.weight_piotroski > 0:
            f_pio = compute_piotroski_factor(
                candidates, min_dimensions=cfg.piotroski_min_dims,
            )
            self.rank_normalize(candidates, f_pio, cfg.weight_piotroski, scores)

        # ---------- 估值历史百分位（可选） ----------
        if cfg.weight_valuation_pct > 0:
            f_vpct = compute_valuation_percentile_factor(
                candidates, metric=cfg.value_metric,
                min_history=cfg.valuation_min_history,
            )
            self.rank_normalize(candidates, f_vpct, cfg.weight_valuation_pct, scores)

        # ---------- 应计盈余质量（可选） ----------
        if cfg.weight_accruals > 0:
            f_acc = compute_accruals_quality_factor(candidates)
            self.rank_normalize(candidates, f_acc, cfg.weight_accruals, scores)

        # ---------- 股息率（可选） ----------
        if cfg.weight_dividend > 0:
            from aipha.factors.equity_factors import compute_dividend_factor
            f_div = compute_dividend_factor(candidates)
            self.rank_normalize(candidates, f_div, cfg.weight_dividend, scores)

        # 按综合分降序排列（NaN 分数标的被过滤）
        return self.rank_sort(scores)
