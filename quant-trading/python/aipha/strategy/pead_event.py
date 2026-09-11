"""A 股事件驱动策略 — 财报发布后漂移 (PEAD)

Post-Earnings Announcement Drift (PEAD) 策略：
- 核心 Alpha：财报发布后，超预期（正 SUE）的公司在 20-60 个交易日内
  存在显著正向价格漂移；低于预期（负 SUE）同理存在负向漂移。
- 做多：SUE 最高的 top_k 只；等权或信号强度加权。
- 持有期：到下一次财报季（约 60~90 天），或定期轮动裁剪。

因子依赖：
- SUE (Standardized Unexpected Earnings) → `factors/growth.py`
- 盈利成长 (EarningsGrowth) → 辅助确认
- 质量 (ROE) → 排除低质量超预期（盈利基数效应）

继承 EquityRotationStrategy 基类，复用预过滤/止损/轮动逻辑，
仅实现 _score_candidates() 的 PEAD 打分排名。

对齐 copilot-instructions.md：
- 策略层只输出 OrderIntent
- 财务类因子必须通过 PIT 对齐
- 避免前视偏差：使用 ann_date 控制数据可见性
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_earnings_growth_factor,
    compute_quality_factor,
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
class PEADConfig(EquityRotationConfig):
    """PEAD 事件驱动策略配置

    继承 EquityRotationConfig，添加 PEAD 特有字段。
    """

    # 覆盖默认值
    top_k: int = 20
    rebalance_days: int = 20     # 约月度调仓
    min_price: float = 5.0
    max_price: float = 200.0
    min_mv: float = 200_000.0    # 最小流通市值 20 亿（避免微盘噪声）
    min_turnover: float = 800.0  # 万元
    min_listed_days: int = 120
    stop_loss_pct: float = 0.15  # 与 configs/backtest/pead_event.yml 对齐
    buffer_k: int = 5

    # PEAD 特有参数
    weight_sue: float = 0.45         # SUE 因子权重
    weight_earnings_growth: float = 0.20  # 盈利增长辅助确认
    weight_quality: float = 0.15     # 质量因子（过滤低质量超预期）
    weight_momentum: float = 0.10    # 短期价格动量（确认漂移方向）
    weight_volatility: float = 0.10  # 低波动优选

    # SUE 计算参数
    min_sue: float = 0.0             # 最低 SUE 阈值（仅做多正 SUE）
    min_roe: float = 3.0             # 最低 ROE（排除低质量超预期）

    # 盈利成长参数
    earnings_growth_field: str = "dt_netprofit_yoy"  # 扣非净利润增长率


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------

@StrategyRegistry().register("pead_event")
class PEADStrategy(EquityRotationStrategy):
    """A 股 PEAD (Post-Earnings Announcement Drift) 事件驱动策略

    利用财报超预期后的价格漂移效应，选择 SUE 最高的标的做多。
    结合质量、盈利成长和动量因子进行多维确认。

    学术支撑：
    - Ball & Brown (1968): 首次发现 PEAD 效应
    - Bernard & Thomas (1989, 1990): 确认 SUE 可预测超额收益
    - A 股市场研究表明 PEAD 效应在超预期方向持续 40-60 交易日
    """

    name = "pead_event"

    def __init__(self, config: PEADConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or PEADConfig(**kwargs)

    @property
    def config(self) -> PEADConfig:
        """策略配置。"""
        return self._config

    def _extra_filter(
        self,
        candidate: EquityCandidateInfo,
        context: StrategyContext,
    ) -> bool:
        """PEAD 额外过滤：排除 ROE 过低和 SUE 为负的标的。"""
        cfg = self._config
        meta = candidate.metadata

        # ROE 下限：排除盈利基数极低导致的"虚假"超预期
        roe = meta.get("roe")
        if roe is not None and roe < cfg.min_roe:
            return False

        # SUE 必须可用且非负（只做多正向超预期）
        # 若 SUE 为 None（数据缺失），也应排除 — PEAD 策略核心依赖 SUE
        sue = meta.get("sue")
        if sue is None:
            return False
        if sue < cfg.min_sue:
            return False

        return True

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """PEAD 多因子打分排名。

        核心：SUE 因子（超预期程度）+ 辅助因子。
        """
        cfg = self._config
        if not candidates:
            return []

        # SUE 因子：值越大 = 超预期幅度越大 = 越优
        f_sue_all = self._compute_sue_values(candidates)

        # ---- SUE 缺失的候选直接出局，不进入打分 ----
        #
        # PEAD 的全部立论就是"盈利超预期后的漂移"。没有 SUE，剩下的
        # 动量/质量/低波动只是一组通用因子，推出来的票和 PEAD 没有关系。
        #
        # 为什么必须在这里显式剔除，而不是交给 rank_normalize 的 NaN 语义：
        # 1. ``_extra_filter`` 只拦 on_bar 路径，``_score_candidates``
        #    还会被组合器 / 回测器 / 测试直接调用，不能只靠上游；
        # 2. ``rank_normalize`` 的 ``nan_handling`` 实参默认是 "median"
        #    （不是它 docstring 里写的 "pollute"），NaN 会被当成中位分；
        # 3. 即便第一次显式传 "pollute" 把综合分污染成 NaN，后面任何一个
        #    走默认 median 的因子调用都会在 equity_rotation_base.py:1000
        #    把已有的 NaN 当 0 重新累加，污染当场被抹掉。
        scorable = [c for c in candidates if math.isfinite(f_sue_all[c.symbol])]
        n_dropped = len(candidates) - len(scorable)
        if n_dropped:
            logger.warning(
                "PEAD: %d/%d 候选无可用 SUE，已剔除（PEAD 的立论依据就是 SUE，"
                "缺 SUE 的票不该按动量/质量排进名单）",
                n_dropped, len(candidates),
            )
        if not scorable:
            return []

        scores: dict[str, float] = {}
        f_sue = {c.symbol: f_sue_all[c.symbol] for c in scorable}
        self.rank_normalize(scorable, f_sue, cfg.weight_sue, scores)

        # 盈利成长因子：统一调用 equity_factors 模块
        # NaN → -inf：辅助因子缺失不应排除候选（SUE 是核心因子）
        f_growth = compute_earnings_growth_factor(scorable, field=cfg.earnings_growth_field)
        f_growth = {k: (-math.inf if math.isnan(v) else v) for k, v in f_growth.items()}
        self.rank_normalize(scorable, f_growth, cfg.weight_earnings_growth, scores)

        # 质量因子：统一调用 equity_factors 模块
        f_quality = compute_quality_factor(scorable, metric="roe")
        f_quality = {k: (-math.inf if math.isnan(v) else v) for k, v in f_quality.items()}
        self.rank_normalize(scorable, f_quality, cfg.weight_quality, scores)

        # 动量因子：确认漂移方向
        f_mom = {c.symbol: self.calc_momentum(c.symbol) for c in scorable}
        self.rank_normalize(scorable, f_mom, cfg.weight_momentum, scores)

        # 低波动因子
        f_vol = {c.symbol: -self.calc_volatility(c.symbol) for c in scorable}
        self.rank_normalize(scorable, f_vol, cfg.weight_volatility, scores)

        # 因子覆盖率诊断（帮助排查 sensitivity=0 问题）
        # SUE 覆盖率不在这里报：缺 SUE 的已经被剔除，此处必然 100%，
        # 真正的覆盖率信息由上面 n_dropped 那条 warning 给出。
        n = len(scorable)
        if n > 0:
            # 用 isfinite 而不是 "!= -inf and not isinf"：后者会把 NaN 当成有效值。
            n_growth_valid = sum(1 for v in f_growth.values() if math.isfinite(v))
            n_quality_valid = sum(1 for v in f_quality.values() if math.isfinite(v))
            n_mom_valid = sum(1 for v in f_mom.values() if v != 0.0)
            logger.debug(
                "PEAD 因子覆盖率 [%d 可打分候选 / %d 输入]: "
                "growth=%.0f%% quality=%.0f%% momentum=%.0f%%",
                n, len(candidates),
                100.0 * n_growth_valid / n,
                100.0 * n_quality_valid / n,
                100.0 * n_mom_valid / n,
            )

        # 按综合分降序
        return self.rank_sort(scores)

    def _compute_sue_values(
        self,
        candidates: list[EquityCandidateInfo],
    ) -> dict[str, float]:
        """从 metadata 中提取 SUE 值。

        SUE 数据应由数据层预计算并注入 bar.metadata。
        支持两种数据路径：
        1. metadata["sue"] — 预计算的 SUE 值（推荐）
        2. metadata["eps"] + metadata["eps_yoy_prev"] + metadata["eps_std"] — 在线计算
        """
        result: dict[str, float] = {}
        for c in candidates:
            meta = c.metadata

            # 路径 1：预计算 SUE
            sue = meta.get("sue")
            if sue is not None:
                try:
                    result[c.symbol] = float(sue)
                    continue
                except (TypeError, ValueError):
                    pass

            # 路径 2：在线计算 SUE = (actual - expected) / std
            eps = meta.get("eps")
            eps_prev = meta.get("eps_yoy_prev")
            eps_std = meta.get("eps_std")

            if eps is not None and eps_prev is not None and eps_std is not None:
                try:
                    actual = float(eps)
                    expected = float(eps_prev)
                    std = float(eps_std)
                    if std > 0:
                        result[c.symbol] = (actual - expected) / std
                        continue
                except (TypeError, ValueError):
                    pass

            # 无法计算 → 标为缺失（NaN），由 rank_normalize 的 "pollute" 语义排除。
            #
            # 这里曾写 -inf。但在 rank_normalize 里 -inf 和 NaN 含义不同：
            # -inf = "有数据、排最后、保留"，NaN/缺键 = "无数据、污染综合分、剔除"。
            # 辅助因子（growth/quality）故意用 -inf，因为 SUE 才是核心因子、
            # 辅助因子缺失不该踢掉候选；但 SUE 自己缺失时用 -inf，就变成
            # "没有超预期数据的股票照样按动量/质量排进买入名单" ——
            # 策略的立论依据根本不存在，日志里那句 "SUE 有效覆盖率 0%"
            # 已经报了警，却仍然把票推了出去。
            result[c.symbol] = float("nan")

        return result
