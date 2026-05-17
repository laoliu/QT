"""A 股高股息防御策略

核心 Alpha：高股息率 + 盈利质量过滤 + 低波动偏好，构建防御型组合。

Alpha 来源：
1. **股息率 (Dividend Yield)** — 高股息 TTM 优先
2. **质量锚** — ROE ≥ min_roe + 低负债，排除"假高股息"（盈利下滑导致的虚高股息率）
3. **低波动** — 高股息 + 低波动 = 防御型标配
4. **价值确认** — 低 PE 去除成长溢价过高标的
5. **动量确认** — 排除下跌趋势中的高股息（价值陷阱）

适用场景：
- 熊市/震荡市中的绝对收益
- 与动量/小盘/成长策略形成正交互补
- A 股红利风格长期有效（中证红利 vs 沪深300 长期超额 3-5%）

对齐 copilot-instructions.md：
- 策略层只输出 OrderIntent
- 所有假设写入 configs/backtest/dividend_defensive.yml
- 因子计算复用 equity_factors 统一模块
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_accruals_quality_factor,
    compute_debt_factor,
    compute_dividend_factor,
    compute_earnings_stability_factor,
    compute_momentum_factor,
    compute_piotroski_factor,
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
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


@dataclass
class DividendDefensiveConfig(EquityRotationConfig):
    """高股息防御策略配置。

    继承 EquityRotationConfig，添加股息相关字段。
    """

    # 覆盖基类默认值
    top_k: int = 30
    rebalance_days: int = 20     # 月度调仓
    min_price: float = 3.0
    max_price: float = 200.0
    min_turnover: float = 500.0
    min_listed_days: int = 120   # 排除次新股（股息不稳定）
    stop_loss_pct: float = 0.15
    buffer_k: int = 8
    max_single_pct: float = 0.05
    min_mv: float = 200_000.0   # 20 亿以上市值

    # ---------- 因子权重（总和应为 1.0） ----------
    weight_dividend: float = 0.35      # 主因子：股息率
    weight_quality: float = 0.25       # 质量复合（ROE + 低负债）
    weight_volatility: float = 0.20    # 低波动
    weight_value: float = 0.10         # 低 PE 确认
    weight_momentum: float = 0.10      # 动量确认

    # ---------- 质量复合内部权重 ----------
    quality_roe_weight: float = 0.60
    quality_debt_weight: float = 0.40

    # ---------- 硬性过滤门槛 ----------
    min_dividend_yield: float = 0.5    # 股息率 >= 0.5% 基本门槛
    min_roe: float = 5.0               # ROE >= 5%（高股息公司不需要太高成长性）
    max_pe: float = 60.0               # PE < 60（排除亏损和成长溢价过高）
    max_debt_to_assets: float = 75.0   # 负债率 < 75%（金融行业适当放宽）

    # ---------- 新增基本面因子权重（默认 0.0，向后兼容） ----------
    weight_earnings_stability: float = 0.0  # 盈利稳定性
    weight_piotroski: float = 0.0           # Piotroski F-Score
    weight_accruals: float = 0.0            # 应计盈余质量

    # 新增因子参数
    piotroski_min_dims: int = 5
    stability_min_periods: int = 3

    # ---------- 因子参数 ----------
    dividend_field: str = "dv_ratio"   # 股息率字段
    momentum_lookback: int = 20
    volatility_lookback: int = 60      # 低波动用较长窗口更稳定


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------


@StrategyRegistry().register("dividend_defensive")
class DividendDefensiveStrategy(EquityRotationStrategy):
    """A 股高股息防御策略

    在全市场筛选高股息率 + 高质量 + 低波动的标的，
    构建防御型红利组合，追求稳健绝对收益。
    """

    name = "dividend_defensive"

    def __init__(self, config: DividendDefensiveConfig | None = None, **kwargs):
        """初始化高股息防御策略。

        Args:
            config: 策略配置，None 时使用默认值或从 kwargs 构造。
        """
        super().__init__()
        self._config = config or DividendDefensiveConfig(**kwargs)

    @property
    def config(self) -> DividendDefensiveConfig:
        """返回策略配置。"""
        return self._config

    def _extra_filter(
        self,
        candidate: EquityCandidateInfo,
        context: StrategyContext,
    ) -> bool:
        """高股息硬性过滤：排除低股息、低 ROE、亏损和高杠杆标的。

        Args:
            candidate: 候选股票信息。
            context: 策略上下文。

        Returns:
            True 表示通过过滤，False 表示被剔除。
        """
        cfg = self._config
        meta = candidate.metadata

        # 股息率下限
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
        """高股息 + 质量 + 低波动 + 价值 + 动量复合打分。

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

        # ---------- 股息率因子（主因子） ----------
        f_div = compute_dividend_factor(
            candidates, field=cfg.dividend_field,
        )
        self.rank_normalize(candidates, f_div, cfg.weight_dividend, scores)

        # ---------- 质量复合因子 ----------
        f_roe = compute_quality_factor(candidates, metric="roe")
        f_debt = compute_debt_factor(candidates, metric="debt_to_assets")

        quality_scores: dict[str, float] = {}
        self.rank_normalize(
            candidates, f_roe, cfg.quality_roe_weight, quality_scores,
        )
        self.rank_normalize(
            candidates, f_debt, cfg.quality_debt_weight, quality_scores,
        )

        for sym, qs in quality_scores.items():
            scores[sym] = scores.get(sym, 0.0) + qs * cfg.weight_quality

        # ---------- 低波动因子 ----------
        f_vol = compute_volatility_factor(
            self._price_history, candidates,
            lookback=cfg.volatility_lookback,
        )
        self.rank_normalize(candidates, f_vol, cfg.weight_volatility, scores)

        # ---------- 价值因子（低 PE 确认） ----------
        f_value = compute_value_factor(candidates, metric="pe_ttm")
        self.rank_normalize(candidates, f_value, cfg.weight_value, scores)

        # ---------- 动量确认 ----------
        f_mom = compute_momentum_factor(
            self._price_history, candidates,
            lookback=cfg.momentum_lookback,
            skip_recent=cfg.momentum_skip_recent,
            multi_period=cfg.momentum_multi_period,
        )
        self.rank_normalize(candidates, f_mom, cfg.weight_momentum, scores)

        # ---------- 盈利稳定性（可选） ----------
        if cfg.weight_earnings_stability > 0:
            f_stab = compute_earnings_stability_factor(
                candidates, min_periods=cfg.stability_min_periods,
            )
            self.rank_normalize(
                candidates, f_stab, cfg.weight_earnings_stability, scores,
            )

        # ---------- Piotroski F-Score（可选） ----------
        if cfg.weight_piotroski > 0:
            f_pio = compute_piotroski_factor(
                candidates, min_dimensions=cfg.piotroski_min_dims,
            )
            self.rank_normalize(candidates, f_pio, cfg.weight_piotroski, scores)

        # ---------- 应计盈余质量（可选） ----------
        if cfg.weight_accruals > 0:
            f_acc = compute_accruals_quality_factor(candidates)
            self.rank_normalize(candidates, f_acc, cfg.weight_accruals, scores)

        return self.rank_sort(scores)
