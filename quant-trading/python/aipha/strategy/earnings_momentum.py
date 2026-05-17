"""A 股盈利动量策略 — 盈利加速 + SUE 确认

核心 Alpha：关注盈利增长的加速度——营收增长 + 利润增长双双正增长的公司
处于业绩拐点，后续 1-2 季度继续超预期的概率极高。

与 pead_event 互补：
- PEAD 侧重"单次超预期后的漂移"（事件驱动）
- 本策略侧重"连续加速增长"的趋势（趋势跟踪型基本面策略）

Alpha 来源：
1. **盈利加速** — 扣非利润增长率越高 = 加速越明显
2. **营收增长** — 营收必须同步增长（排除纯降本增效）
3. **SUE 确认** — 超预期幅度进一步确认盈利质量
4. **动量确认** — 价格已对基本面改善做出反应
5. **质量锚** — ROE 排除基数太低的"虚假高增长"
6. **低波动** — 风险调整后收益控制

学术支撑：
- William O'Neil CAN SLIM 中的 E (Earnings) + S (Sales support)
- A 股分析师覆盖不足（中小盘），盈利加速信号定价效率低

对齐 copilot-instructions.md：
- 策略层只输出 OrderIntent
- 财务因子通过 PIT 对齐
- 所有假设写入 configs/backtest/earnings_momentum.yml
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_earnings_growth_factor,
    compute_momentum_factor,
    compute_quality_factor,
    compute_volatility_factor,
)
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import (
    EquityCandidateInfo,
    EquityRotationConfig,
    EquityRotationStrategy,
)
from aipha.strategy.param_space import ParamSpace, ParamSpec, ParamType
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


@dataclass
class EarningsMomentumConfig(EquityRotationConfig):
    """盈利动量策略配置。

    继承 EquityRotationConfig，添加盈利动量特有字段。
    """

    # 覆盖默认值
    top_k: int = 25
    rebalance_days: int = 15       # 略高换手以捕捉盈利拐点
    min_price: float = 5.0
    max_price: float = 100.0       # 排除超高价股
    min_turnover: float = 800.0
    min_listed_days: int = 120
    stop_loss_pct: float = 0.15
    buffer_k: int = 8
    max_single_pct: float = 0.05
    min_mv: float = 150_000.0     # 15 亿以上

    # 盈利动量因子权重
    weight_earnings_accel: float = 0.30   # 盈利加速（扣非利润增长率）
    weight_revenue_growth: float = 0.20   # 营收增长
    weight_sue: float = 0.20              # 超预期确认
    weight_momentum: float = 0.15         # 价格动量
    weight_quality: float = 0.10          # 质量锚
    weight_volatility: float = 0.05       # 低波动

    # 硬性过滤门槛
    min_earnings_growth: float = 10.0   # dt_netprofit_yoy >= 10%
    min_revenue_growth: float = 5.0     # revenue_yoy >= 5%
    min_roe: float = 5.0               # ROE >= 5%
    max_pe: float = 150.0              # PE < 150

    # 动量参数
    momentum_lookback: int = 20
    volatility_lookback: int = 60


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------


@StrategyRegistry().register("earnings_momentum")
class EarningsMomentumStrategy(EquityRotationStrategy):
    """A 股盈利动量策略

    选择盈利增长加速的标的做多，结合 SUE（超预期幅度）
    和价格动量进行多维确认，同时用 ROE 排除低质量的虚假高增长。
    """

    name = "earnings_momentum"

    def __init__(self, config: EarningsMomentumConfig | None = None, **kwargs):
        """初始化盈利动量策略。

        Args:
            config: 策略配置，None 时使用默认值或从 kwargs 构造。
        """
        super().__init__()
        self._config = config or EarningsMomentumConfig(**kwargs)

    @classmethod
    def param_space(cls) -> ParamSpace:
        """盈利动量策略参数空间"""
        return ParamSpace(
            strategy_name="earnings_momentum",
            specs=[
                ParamSpec(
                    name="top_k", param_type=ParamType.INT,
                    default=25, low=10, high=50, step=5,
                    description="选股数量", is_critical=True,
                ),
                ParamSpec(
                    name="rebalance_days", param_type=ParamType.INT,
                    default=15, low=5, high=30, step=5,
                    description="调仓周期（天）", is_critical=True,
                ),
                ParamSpec(
                    name="stop_loss_pct", param_type=ParamType.FLOAT,
                    default=0.15, low=0.05, high=0.30, step=0.05,
                    description="止损百分比", is_critical=True,
                ),
                ParamSpec(
                    name="weight_earnings_accel", param_type=ParamType.FLOAT,
                    default=0.30, low=0.10, high=0.50, step=0.05,
                    description="盈利加速因子权重",
                ),
                ParamSpec(
                    name="weight_revenue_growth", param_type=ParamType.FLOAT,
                    default=0.20, low=0.05, high=0.40, step=0.05,
                    description="营收增长因子权重",
                ),
                ParamSpec(
                    name="weight_sue", param_type=ParamType.FLOAT,
                    default=0.20, low=0.05, high=0.40, step=0.05,
                    description="超预期确认因子权重",
                ),
            ],
        )

    @property
    def config(self) -> EarningsMomentumConfig:
        """返回策略配置。"""
        return self._config

    def _extra_filter(
        self,
        candidate: EquityCandidateInfo,
        context: StrategyContext,
    ) -> bool:
        """盈利动量硬性过滤：排除盈利不增长、营收不增长、低质量标的。

        Args:
            candidate: 候选股票信息。
            context: 策略上下文。

        Returns:
            True 表示通过过滤，False 表示被剔除。
        """
        cfg = self._config
        meta = candidate.metadata

        # 扣非利润增长率 >= min_earnings_growth
        eg = meta.get("dt_netprofit_yoy")
        if eg is not None and eg < cfg.min_earnings_growth:
            return False

        # 营收增长率 >= min_revenue_growth
        rg = meta.get("revenue_yoy")
        if rg is not None and rg < cfg.min_revenue_growth:
            return False

        # ROE 下限
        roe = meta.get("roe")
        if roe is not None and roe < cfg.min_roe:
            return False

        # PE 范围
        pe = meta.get("pe_ttm")
        if pe is not None:
            if pe <= 0 or pe > cfg.max_pe:
                return False

        return True

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """盈利动量多因子打分。

        重点关注盈利增长幅度、营收增长、SUE 超预期，
        辅以价格动量确认和质量锚。

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

        # NaN 处理：财务因子频繁缺失（尤其SUE），使用中位填充避免全灭
        nan_mode = "median"

        # 盈利加速因子：扣非利润增长率
        f_earnings = compute_earnings_growth_factor(
            candidates, field="dt_netprofit_yoy",
        )
        self.rank_normalize(
            candidates, f_earnings, cfg.weight_earnings_accel, scores,
            nan_handling=nan_mode,
        )

        # 营收增长因子
        f_revenue = compute_earnings_growth_factor(
            candidates, field="revenue_yoy",
        )
        self.rank_normalize(
            candidates, f_revenue, cfg.weight_revenue_growth, scores,
            nan_handling=nan_mode,
        )

        # SUE 超预期因子
        f_sue = compute_earnings_growth_factor(
            candidates, field="sue",
        )
        self.rank_normalize(
            candidates, f_sue, cfg.weight_sue, scores,
            nan_handling=nan_mode,
        )

        # 价格动量
        f_mom = compute_momentum_factor(
            self._price_history, candidates,
            lookback=cfg.momentum_lookback,
            skip_recent=cfg.momentum_skip_recent,
            multi_period=cfg.momentum_multi_period,
        )
        self.rank_normalize(
            candidates, f_mom, cfg.weight_momentum, scores,
            nan_handling=nan_mode,
        )

        # 质量因子：ROE
        f_quality = compute_quality_factor(candidates, metric="roe")
        self.rank_normalize(
            candidates, f_quality, cfg.weight_quality, scores,
            nan_handling=nan_mode,
        )

        # 低波动
        f_vol = compute_volatility_factor(
            self._price_history, candidates,
            lookback=cfg.volatility_lookback,
        )
        self.rank_normalize(
            candidates, f_vol, cfg.weight_volatility, scores,
            nan_handling=nan_mode,
        )

        return self.rank_sort(scores)
