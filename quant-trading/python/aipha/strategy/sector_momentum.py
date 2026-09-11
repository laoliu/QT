"""A 股行业轮动策略 — 板块动量 + 板块内精选

核心 Alpha：A 股行业轮动效应显著，行业 β 占个股收益方差 40-60%。
先识别"热门行业"（20 日平均动量最高的行业），再在热门行业内
按动量+低波动+价值+质量+小市值多因子精选个股。

与现有策略的本质区别：
- multifactor_enhance / pead_event / smallcap_rotation 均为"个股维度直接打分"
- 本策略引入"行业动量排名 → 行业内个股精选"两阶段架构
- 利用 metadata["industry"]（申万一级行业）做行业聚合

学术支撑：
- Moskowitz & Grinblatt (1999): Industry Momentum
- A 股行业主题热度持续 2-4 周，散户抱团加剧了行业动量

对齐 copilot-instructions.md：
- 策略层只输出 OrderIntent
- 因子计算无前视偏差
- 所有假设写入 configs/backtest/sector_momentum.yml
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_quality_factor,
    compute_size_factor,
    compute_value_factor,
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
class SectorMomentumConfig(EquityRotationConfig):
    """行业轮动策略配置。

    继承 EquityRotationConfig，添加行业轮动特有字段。
    """

    # 覆盖默认值
    # top_k=25 来自 dc5d7a6（标题即 "set top_k to 25 and adjust related tests"）。
    # be42091 把这段单行配置展开成多行时顺手写回了 30，提交信息只字未提 ——
    # 那次提交的主题是 ComboCPCV/质量门槛，改到这里属于误伤。测试一直按 25 断言。
    top_k: int = 25
    rebalance_days: int = 10       # 双周调仓以捕捉行业切换
    min_price: float = 5.0
    # be42091 在同一次"格式化"里把这行从 500.0 改成了 200.0，同样没在提交
    # 信息里提。但这一处**不回滚**，和上面的 top_k 不同：
    #   - top_k=25 有 dc5d7a6 的显式提交和一直按 25 断言的测试撑着，改回去是误伤；
    #   - max_price=500.0 只来自 dcb1308 的批量导入，没有任何提交或测试支持它。
    # 而 200.0 才是 A 股轮动线的通行值（相邻的 sector_momentum_plus.py:59 就是
    # 200.0，adaptive_multifactor / balanced_multifactor / pead_* 等一片都是）。
    # 用 500.0 的是 cash_cow_select / dividend_quality_select / stable_compounder
    # 这类"不限高价蓝筹"的红利策略 —— 行业轮动不是那个路子：一手 500 元股
    # 就是 5 万元，在 max_single_pct=0.05 下会直接顶穿小账户的单票上限。
    max_price: float = 200.0
    min_turnover: float = 800.0    # 万元
    min_listed_days: int = 120
    stop_loss_pct: float = 0.12    # 行业轮动换手高，止损更严
    buffer_k: int = 8
    max_single_pct: float = 0.05

    # 行业轮动特有参数
    top_industries: int = 6        # 选择 top N 个热门行业
    # 两阶段共用一个窗口：第一阶段的行业平均动量和第二阶段的行业内个股动量
    # 都走 calc_momentum(cfg.momentum_lookback)。原注释只写"行业动量回看窗口"，
    # 容易让人以为个股那一段另有窗口 —— 并没有。
    momentum_lookback: int = 20

    # 行业内个股因子权重
    weight_momentum: float = 0.30      # 个股动量
    weight_volatility: float = 0.25    # 低波动
    weight_value: float = 0.20         # 估值（低 PE）
    weight_quality: float = 0.15       # 质量（高 ROE）
    weight_size: float = 0.10          # 小市值偏好


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------


@StrategyRegistry().register("sector_momentum")
class SectorMomentumStrategy(EquityRotationStrategy):
    """A 股行业轮动策略

    两阶段选股：
    1. 行业排名：计算每个行业内候选股票的平均 20 日动量，选 top N 行业
    2. 行业内精选：在热门行业内用多因子打分选出 top_k 个股

    相比纯个股轮动策略，行业轮动能更好地捕捉 A 股的板块效应。
    """

    name = "sector_momentum"

    def __init__(self, config: SectorMomentumConfig | None = None, **kwargs):
        """初始化行业轮动策略。

        Args:
            config: 策略配置，None 时使用默认值或从 kwargs 构造。
        """
        super().__init__()
        self._config = config or SectorMomentumConfig(**kwargs)

    @property
    def config(self) -> SectorMomentumConfig:
        """返回策略配置。"""
        return self._config

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """两阶段打分：行业排名 → 行业内精选。

        Args:
            candidates: 经过基础过滤的候选列表。
            context: 策略上下文。

        Returns:
            按综合得分降序排列的 symbol 列表。
        """
        cfg = self._config
        if not candidates:
            return []

        # ---- 第一阶段：行业动量排名 ----
        industry_candidates: dict[str, list[EquityCandidateInfo]] = defaultdict(list)
        for c in candidates:
            industry = c.metadata.get("industry", "未知")
            industry_candidates[industry].append(c)

        # 计算每个行业的平均动量（要求行业内至少 3 只候选，避免少数标的噪声）
        _MIN_INDUSTRY_STOCKS = 3
        industry_momentum: dict[str, float] = {}
        for ind, cands in industry_candidates.items():
            if len(cands) < _MIN_INDUSTRY_STOCKS:
                # 标的数不足的行业给予最低动量，避免偶发性选中
                industry_momentum[ind] = float("-inf")
                continue
            moms = [self.calc_momentum(c.symbol) for c in cands]
            if moms:
                industry_momentum[ind] = sum(moms) / len(moms)
            else:
                industry_momentum[ind] = float("-inf")

        # 选 top N 行业
        sorted_industries = sorted(
            industry_momentum.items(), key=lambda x: x[1], reverse=True
        )
        top_ind_names = {
            ind for ind, _ in sorted_industries[:cfg.top_industries]
        }

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                f"热门行业 top-{cfg.top_industries}: "
                f"{[(ind, round(m, 4)) for ind, m in sorted_industries[:cfg.top_industries]]}"
            )

        # ---- 第二阶段：只在热门行业内打分 ----
        hot_candidates = [
            c for c in candidates
            if c.metadata.get("industry", "未知") in top_ind_names
        ]
        if not hot_candidates:
            # 降级：若热门行业无候选，回退到全部候选
            hot_candidates = candidates

        scores: dict[str, float] = {}

        # 个股动量
        f_mom = {c.symbol: self.calc_momentum(c.symbol) for c in hot_candidates}
        self.rank_normalize(hot_candidates, f_mom, cfg.weight_momentum, scores)

        # 低波动
        f_vol = {c.symbol: -self.calc_volatility(c.symbol) for c in hot_candidates}
        self.rank_normalize(hot_candidates, f_vol, cfg.weight_volatility, scores)

        # 价值（低 PE）—— 通过因子桥接层计算
        f_value = compute_value_factor(hot_candidates, metric="pe_ttm")
        self.rank_normalize(hot_candidates, f_value, cfg.weight_value, scores)

        # 质量（高 ROE）—— 通过因子桥接层计算
        f_quality = compute_quality_factor(hot_candidates, metric="roe")
        self.rank_normalize(hot_candidates, f_quality, cfg.weight_quality, scores)

        # 小市值 —— 通过因子桥接层计算
        f_size = compute_size_factor(hot_candidates)
        self.rank_normalize(hot_candidates, f_size, cfg.weight_size, scores)

        return self.rank_sort(scores)
