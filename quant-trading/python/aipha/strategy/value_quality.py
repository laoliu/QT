"""A 股价值质量复合策略

核心 Alpha：低估值 + 高盈利质量 + 动量确认。
经典"Magic Formula"（Joel Greenblatt）的 A 股适配版。

Alpha 来源：
1. **价值复合** — 低 PE_TTM + 低 PB 双重估值锚
2. **质量复合** — 高 ROE + 高毛利率 + 低负债率三维质量锚
3. **动量确认** — 排除"下跌中的便宜股"（价值陷阱）
4. **低波动** — 风险调整后收益更优
5. **小市值** — 指数成分内的相对小市值标的有超额

额外安全机制：
- ROE >= 8% 硬性门槛（排除低盈利能力公司）
- PE_TTM > 0 且 < 80（排除亏损和过高估值）
- Debt-to-Assets < 70%（排除高杠杆风险）

对齐 copilot-instructions.md：
- 策略层只输出 OrderIntent
- 所有假设写入 configs/backtest/value_quality.yml
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_accruals_quality_factor,
    compute_debt_factor,
    compute_momentum_factor,
    compute_piotroski_factor,
    compute_quality_factor,
    compute_size_factor,
    compute_value_factor,
    compute_valuation_percentile_factor,
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
class ValueQualityConfig(EquityRotationConfig):
    """价值质量复合策略配置。

    继承 EquityRotationConfig，添加价值质量特有字段。
    """

    # 覆盖默认值
    top_k: int = 30
    rebalance_days: int = 20     # 月度调仓，降低换手
    min_price: float = 5.0
    max_price: float = 200.0
    min_turnover: float = 1000.0
    min_listed_days: int = 120
    stop_loss_pct: float = 0.18    # 价值股回撤容忍度稍高
    buffer_k: int = 10
    max_single_pct: float = 0.05
    min_mv: float = 200_000.0     # 20 亿以上

    # 复合因子权重
    weight_quality: float = 0.30       # 质量复合
    weight_value: float = 0.30         # 价值复合
    weight_momentum: float = 0.20      # 动量确认
    weight_volatility: float = 0.10    # 低波动
    weight_size: float = 0.10          # 小市值

    # 质量复合内部权重
    quality_roe_weight: float = 0.50
    quality_margin_weight: float = 0.30
    quality_debt_weight: float = 0.20

    # 价值复合内部权重
    value_pe_weight: float = 0.60
    value_pb_weight: float = 0.40

    # 硬性过滤门槛
    min_roe: float = 8.0          # ROE >= 8%
    max_pe: float = 80.0          # PE < 80
    max_debt_to_assets: float = 70.0  # 负债率 < 70%

    # ---------- 新增基本面因子权重（默认 0.0，向后兼容） ----------
    weight_piotroski: float = 0.0          # Piotroski F-Score
    weight_valuation_pct: float = 0.0      # 估值历史百分位
    weight_accruals: float = 0.0           # 应计盈余质量

    # 新增因子参数
    piotroski_min_dims: int = 5
    valuation_min_history: int = 20

    # 动量参数
    momentum_lookback: int = 20
    volatility_lookback: int = 60


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------


@StrategyRegistry().register("value_quality")
class ValueQualityStrategy(EquityRotationStrategy):
    """A 股价值质量复合策略

    在指数成分股中选择低估值 + 高质量的标的，
    辅以动量确认和低波动偏好，构建高 Alpha 组合。
    """

    name = "value_quality"

    def __init__(self, config: ValueQualityConfig | None = None, **kwargs):
        """初始化价值质量策略。

        Args:
            config: 策略配置，None 时使用默认值或从 kwargs 构造。
        """
        super().__init__()
        self._config = config or ValueQualityConfig(**kwargs)

    @property
    def config(self) -> ValueQualityConfig:
        """返回策略配置。"""
        return self._config

    def _extra_filter(
        self,
        candidate: EquityCandidateInfo,
        context: StrategyContext,
    ) -> bool:
        """价值质量硬性过滤：排除低 ROE、高估值、高杠杆标的。

        Args:
            candidate: 候选股票信息。
            context: 策略上下文。

        Returns:
            True 表示通过过滤，False 表示被剔除。
        """
        cfg = self._config
        meta = candidate.metadata

        # ROE 下限
        roe = meta.get("roe")
        if roe is not None and roe < cfg.min_roe:
            return False

        # PE 范围：排除亏损和严重高估
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
        """价值+质量复合打分。

        采用两个复合因子（ValueComposite + QualityComposite）
        加动量确认、低波动和小市值偏好。

        Args:
            candidates: 经过基础过滤和额外过滤的候选列表。
            context: 策略上下文。

        Returns:
            按综合得分降序排列的 symbol 列表。
        """
        cfg = self._config
        if not candidates:
            return []

        scores: dict[str, float] = {}

        # ---------- 价值复合因子 ----------
        # 分别计算 PE 和 PB 排名，再加权合并
        f_pe = compute_value_factor(candidates, metric="pe_ttm")
        f_pb = compute_value_factor(candidates, metric="pb")

        # 用价值复合权重打分
        value_scores: dict[str, float] = {}
        self.rank_normalize(candidates, f_pe, cfg.value_pe_weight, value_scores)
        self.rank_normalize(candidates, f_pb, cfg.value_pb_weight, value_scores)

        # 将价值复合分注入总分
        for sym, vs in value_scores.items():
            scores[sym] = scores.get(sym, 0.0) + vs * cfg.weight_value

        # ---------- 质量复合因子 ----------
        f_roe = compute_quality_factor(candidates, metric="roe")
        f_margin = compute_quality_factor(candidates, metric="grossprofit_margin")
        f_debt = compute_debt_factor(candidates, metric="debt_to_assets")

        quality_scores: dict[str, float] = {}
        self.rank_normalize(candidates, f_roe, cfg.quality_roe_weight, quality_scores)
        self.rank_normalize(candidates, f_margin, cfg.quality_margin_weight, quality_scores)
        self.rank_normalize(candidates, f_debt, cfg.quality_debt_weight, quality_scores)

        for sym, qs in quality_scores.items():
            scores[sym] = scores.get(sym, 0.0) + qs * cfg.weight_quality

        # ---------- 动量因子 ----------
        f_mom = compute_momentum_factor(
            self._price_history, candidates,
            lookback=cfg.momentum_lookback,
            skip_recent=cfg.momentum_skip_recent,
            multi_period=cfg.momentum_multi_period,
        )
        self.rank_normalize(candidates, f_mom, cfg.weight_momentum, scores)

        # ---------- 低波动因子 ----------
        f_vol = compute_volatility_factor(
            self._price_history, candidates,
            lookback=cfg.volatility_lookback,
        )
        self.rank_normalize(candidates, f_vol, cfg.weight_volatility, scores)

        # ---------- 小市值因子 ----------
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
                candidates, metric="pe_ttm",
                min_history=cfg.valuation_min_history,
            )
            self.rank_normalize(candidates, f_vpct, cfg.weight_valuation_pct, scores)

        # ---------- 应计盈余质量（可选） ----------
        if cfg.weight_accruals > 0:
            f_acc = compute_accruals_quality_factor(candidates)
            self.rank_normalize(candidates, f_acc, cfg.weight_accruals, scores)

        return self.rank_sort(scores)
