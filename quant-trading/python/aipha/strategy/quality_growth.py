"""A 股质量成长策略（两阶段选股）

用户需求："先筛选有基本面或者基础数据好的做为备选池子，再进一步筛选去量化"
目标年化 30%+。

Alpha 来源（两阶段）：
第一阶段 — 基本面质量池：
1. ROE ≥ 10%（高盈利能力）
2. 营收增长 > 0（非衰退企业）
3. 扣非利润增速 > 0（真实盈利增长）
4. PE_TTM ∈ (0, 50)（合理估值，排除亏损/泡沫）
5. 资产负债率 < 65%（财务健康）
6. 流通市值 ∈ [30亿, 500亿]（中小盘甜蜜区）

第二阶段 — 量化精选：
1. **盈利加速**（30%）— 扣非利润增速 rank
2. **动量确认**（25%）— 多周期(5d/20d/60d) 混合动量
3. **质量复合**（20%）— ROE + Piotroski F-Score
4. **低波动**（15%）— 60 日波动率倒数
5. **估值分位**（10%）— PE 历史百分位（低分位 = 便宜）

对齐 copilot-instructions.md：
- 策略层只输出 OrderIntent
- 所有假设写入 configs/backtest/quality_growth.yml
- 继承 EquityRotationStrategy 模板方法，不直接操作资金/持仓
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_earnings_growth_factor,
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
class QualityGrowthConfig(EquityRotationConfig):
    """质量成长策略配置。

    第一阶段：基本面硬性门槛
    第二阶段：多因子量化精选
    """

    # --- 覆盖基类默认值 ---
    top_k: int = 25
    rebalance_days: int = 15         # 双周调仓（平衡换手与响应速度）
    min_price: float = 5.0
    max_price: float = 200.0
    min_turnover: float = 1500.0     # 1500 万成交额下限（保流动性）
    min_listed_days: int = 120       # 排除次新股
    stop_loss_pct: float = 0.12      # 12% 止损（成长股波动大但不能死扛）
    trailing_stop_pct: float = 0.15  # 15% 追踪止损（保护利润）
    buffer_k: int = 8               # 缓冲区，降换手
    max_single_pct: float = 0.06    # 单票 6%
    min_mv: float = 300_000.0       # 30 亿下限
    max_mv: float = 5_000_000.0     # 500 亿上限
    inverse_vol_weighting: bool = True  # 反波动率加权
    max_industry_pct: float = 0.20  # 单行业最多 20%

    # --- 第一阶段：基本面硬性门槛 ---
    min_roe: float = 10.0           # ROE ≥ 10%
    min_revenue_growth: float = 0.0 # 营收同比 > 0%
    min_profit_growth: float = 0.0  # 扣非利润增速 > 0%
    max_pe: float = 50.0            # PE < 50
    max_debt_to_assets: float = 65.0  # 负债率 < 65%

    # --- 第二阶段：因子权重 ---
    weight_earnings_growth: float = 0.30  # 盈利加速
    weight_momentum: float = 0.25         # 动量确认
    weight_quality: float = 0.20          # 质量复合（ROE + Piotroski）
    weight_volatility: float = 0.15       # 低波动
    weight_valuation_pct: float = 0.10    # 估值（低 PE 优先）

    # --- 因子参数 ---
    momentum_lookback: int = 20
    momentum_skip_recent: int = 3    # 跳过最近3天（剔除短期反转）
    momentum_multi_period: bool = True  # 5d/20d/60d 混合
    volatility_lookback: int = 60
    piotroski_min_dims: int = 3      # 降至 3：fina_indicator 可提供的维度有限

    # --- 质量内部配置 ---
    quality_roe_weight: float = 0.50
    quality_piotroski_weight: float = 0.50


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------


@StrategyRegistry().register("quality_growth")
class QualityGrowthStrategy(EquityRotationStrategy):
    """A 股质量成长策略

    两阶段选股：
    1. 基本面质量池 — ROE/营收增长/PE/负债率硬性过滤
    2. 量化精选 — 盈利加速 + 动量确认 + 质量复合 + 低波动 + 估值分位
    """

    name = "quality_growth"

    def __init__(self, config: QualityGrowthConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or QualityGrowthConfig(**kwargs)

    @property
    def config(self) -> QualityGrowthConfig:
        return self._config

    # --- 第一阶段：基本面硬性过滤 ---

    def _extra_filter(
        self,
        candidate: EquityCandidateInfo,
        context: StrategyContext,
    ) -> bool:
        """基本面质量池筛选。

        只有同时满足所有硬性条件的标的才能进入第二阶段。
        """
        cfg = self._config
        meta = candidate.metadata

        # ROE 下限
        roe = meta.get("roe")
        if roe is not None and roe < cfg.min_roe:
            return False

        # PE 范围：排除亏损和高估值
        pe = meta.get("pe_ttm")
        if pe is not None:
            if pe <= 0 or pe > cfg.max_pe:
                return False

        # 负债率上限
        dta = meta.get("debt_to_assets")
        if dta is not None and dta > cfg.max_debt_to_assets:
            return False

        # 营收增长 > 阈值
        rev_growth = meta.get("revenue_yoy")
        if rev_growth is not None and rev_growth < cfg.min_revenue_growth:
            return False

        # 扣非利润增速 > 阈值
        profit_growth = meta.get("dt_netprofit_yoy")
        if profit_growth is not None and profit_growth < cfg.min_profit_growth:
            return False

        return True

    # --- 第二阶段：量化精选 ---

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """多因子量化精选打分。

        在通过基本面质量池筛选的候选中进行精细打分排名。
        """
        cfg = self._config
        if not candidates:
            return []

        scores: dict[str, float] = {}
        # nan_handling="skip": 缺失因子不参与评分（不污染其他因子的分数）
        # 多因子策略必须对部分因子缺失具备容错能力
        _nh = "skip"

        # -------- 盈利加速因子 --------
        f_eg = compute_earnings_growth_factor(candidates)
        self.rank_normalize(candidates, f_eg, cfg.weight_earnings_growth, scores,
                            nan_handling=_nh)

        # -------- 动量确认因子 --------
        f_mom = compute_momentum_factor(
            self._price_history,
            candidates,
            lookback=cfg.momentum_lookback,
            skip_recent=cfg.momentum_skip_recent,
            multi_period=cfg.momentum_multi_period,
        )
        self.rank_normalize(candidates, f_mom, cfg.weight_momentum, scores,
                            nan_handling=_nh)

        # -------- 质量复合因子（ROE + Piotroski）--------
        f_roe = compute_quality_factor(candidates, metric="roe")
        f_pio = compute_piotroski_factor(
            candidates, min_dimensions=cfg.piotroski_min_dims,
        )
        quality_scores: dict[str, float] = {}
        self.rank_normalize(candidates, f_roe, cfg.quality_roe_weight, quality_scores,
                            nan_handling=_nh)
        self.rank_normalize(candidates, f_pio, cfg.quality_piotroski_weight, quality_scores,
                            nan_handling=_nh)
        for sym, qs in quality_scores.items():
            if math.isfinite(qs):
                scores[sym] = scores.get(sym, 0.0) + qs * cfg.weight_quality

        # -------- 低波动因子 --------
        f_vol = compute_volatility_factor(
            self._price_history,
            candidates,
            lookback=cfg.volatility_lookback,
        )
        self.rank_normalize(candidates, f_vol, cfg.weight_volatility, scores,
                            nan_handling=_nh)

        # -------- 估值因子（低 PE 优先）--------
        # 使用 compute_value_factor（当日 PE 倒数），无需历史 PE 序列
        f_val = compute_value_factor(candidates, metric="pe_ttm")
        self.rank_normalize(candidates, f_val, cfg.weight_valuation_pct, scores,
                            nan_handling=_nh)

        return self.rank_sort(scores)
