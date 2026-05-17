"""可转债防守价值策略 —— 大盘低估值 + 到期催化

Alpha 来源：
1. 大发行规模 → 信用质量高（大公司违约概率低），与 small_size 天然负相关
2. 低价低溢价 → 债底安全垫 + 保留正股弹性（期权价值被低估）
3. 临近到期 → 下修预期 + 回售保护（隐性期权催化估值修复）

设计目标：
- 与 small_size/new_small 低相关（通过 min_issue_size 强制大盘）
- 在 2023-2024"小盘失灵"环境下保持正收益（大盘+信用优质）
- 参数稳健（宽松调仓周期、大分散持仓、多因子排名融合）

评分公式（排名融合法，对离群值鲁棒）：
    rank_score = w_ytm * ytm_rank + w_prem * premium_rank + w_mat * maturity_rank

- ytm_rank: 复利到期收益率 (100/close)^(1/years)-1 降序排名(越高越优先)
- premium_rank: 溢价率升序排名 (越低越优先，弹性越大)
- maturity_rank: 到期年限升序排名 (越近到期催化越强)

重要：使用 issue_size 做规模过滤，无 remain_size 前视偏差。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_enhanced_factors import enhanced_ytm
from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
    days_to_maturity,
)
from aipha.strategy.registry import StrategyRegistry
from aipha.utils.rank_utils import rank_normalize_list

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass
class DefensiveValueConfig(CBRotationConfig):
    """防守价值策略配置。

    设计原则：宽松参数 + 大范围覆盖 → 降低参数敏感性。
    """

    # --- 持仓与调仓 ---
    top_k: int = 25                   # 大分散，降低单券信用风险
    rebalance_days: int = 10          # 宽松周期，因子慢变，减少换手冲击
    buffer_k: int = 5                 # 缓冲区减少不必要换手

    # --- 规模筛选（关键：与 small_size 反向） ---
    min_issue_size: float = 30000.0   # 最小 3 亿发行规模 → 排除小盘
    max_issue_size: float = float("inf")

    # --- 价格与溢价 ---
    max_price: float = 115.0          # 低价区间，保留债底安全垫
    max_premium: float = 40.0         # 溢价率上限（%），保留正股弹性

    # --- 到期催化 ---
    max_years_to_maturity: float = 5.0   # 排除超长期（催化弱）
    min_years_to_maturity_val: float = 0.5  # 排除即将到期（不够交易）

    # --- 因子权重 ---
    ytm_weight: float = 0.40          # 到期收益率因子
    premium_weight: float = 0.35      # 低溢价因子
    maturity_weight: float = 0.25     # 到期催化因子

    # --- 风控 ---
    min_turnover: float = 30.0        # 成交额下限（万元），宽松一些
    min_days_to_maturity: int = 180   # 至少半年
    stop_loss_pct: float = 0.12       # 止损比一般策略略松
    min_positions: int = 10           # 持仓保护更高

    # --- 面值 ---
    face_value: float = 100.0


@StrategyRegistry().register("defensive_value")
class DefensiveValueStrategy(CBRotationStrategy):
    """防守价值策略 —— 大盘低估值 + 到期催化。

    选取大发行规模、低价低溢价、临近到期的优质转债，
    通过多因子排名融合评分，定期轮动持有。

    与 small_size/new_small 天然低相关（min_issue_size 强制大盘），
    在小盘杀跌期提供对冲保护。
    """

    name = "defensive_value"

    def __init__(self, config: DefensiveValueConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or DefensiveValueConfig(**kwargs)

    @property
    def config(self) -> DefensiveValueConfig:
        """策略配置。"""
        return self._config

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        """额外过滤：溢价率上限 + 到期年限区间。"""
        cfg = self._config

        # 溢价率上限
        prem = candidate.cb_over_rate
        if prem is not None and prem > cfg.max_premium:
            return False

        # 到期年限区间
        if not candidate.maturity_date:
            return False
        years = days_to_maturity(candidate.maturity_date, context.current_time) / 365.25
        if years < cfg.min_years_to_maturity_val or years > cfg.max_years_to_maturity:
            return False

        return True

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """多因子排名融合评分。

        排名法（rank-based）对离群值鲁棒，降低参数敏感性。
        """
        cfg = self._config

        if not candidates:
            return []

        # --- 计算各因子原始值 ---
        scored: list[dict] = []
        for c in candidates:
            years = days_to_maturity(c.maturity_date, context.current_time) / 365.25
            if years <= 0:
                continue

            # 使用统一的复利 YTM 公式 (100/P)^(1/T) - 1
            # 与 cb_enhanced_factors.enhanced_ytm 保持一致
            ytm = enhanced_ytm(c.close, c.maturity_date or "", context.current_time)

            prem = c.cb_over_rate if c.cb_over_rate is not None else 999.0

            scored.append({
                "symbol": c.symbol,
                "ytm": ytm,
                "premium": prem,
                "years": years,
            })

        if not scored:
            return []

        n = len(scored)

        # --- 排名 — 委托 rank_normalize_list (§13.2) ---
        # YTM: 降序（最高优先）→ rank 0 = 最优
        ytm_scores = rank_normalize_list(
            [s["ytm"] for s in scored], ascending=False, handle_ties=False,
        )
        for i, s in enumerate(scored):
            s["ytm_rank"] = ytm_scores[i]

        # 溢价率: 升序（最低优先）→ rank 0 = 最优
        prem_scores = rank_normalize_list(
            [s["premium"] for s in scored], ascending=True, handle_ties=False,
        )
        for i, s in enumerate(scored):
            s["premium_rank"] = prem_scores[i]

        # 到期年限: 升序（最近到期优先）→ rank 0 = 最优
        mat_scores = rank_normalize_list(
            [s["years"] for s in scored], ascending=True, handle_ties=False,
        )
        for i, s in enumerate(scored):
            s["maturity_rank"] = mat_scores[i]

        # --- 加权融合 ---
        for s in scored:
            s["total"] = (
                cfg.ytm_weight * s["ytm_rank"]
                + cfg.premium_weight * s["premium_rank"]
                + cfg.maturity_weight * s["maturity_rank"]
            )

        # 总分升序（最低最优）
        scored.sort(key=lambda x: x["total"])

        return [s["symbol"] for s in scored[: cfg.top_k]]
