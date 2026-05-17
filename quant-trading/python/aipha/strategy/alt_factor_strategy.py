"""另类因子规则策略（柱4）

基于筹码分布（chips）、资金流（moneyflow）、融资融券（margin）
三大另类数据源的规则策略，作为四柱组合中的第四柱。

因子来源：
- 筹码因子：chips_concentration, chips_upper_pct, chips_lower_pct, chips_asm, chips_cost_ratio
- 资金流因子：flow_buy_ratio, flow_large_net, flow_large_pct, flow_momentum_5d, flow_reversal
- 融资融券因子：margin_balance_ratio, margin_buy_intensity, margin_short_pressure, margin_net_momentum
- 分析师因子：analyst_coverage, analyst_buy_ratio, analyst_target_premium, analyst_est_growth

策略逻辑：
1. 对全截面股票计算 18 个另类因子
2. 每个因子做截面 rank 归一化到 [0,1]
3. 按权重加权合成 composite_score
4. 选 top_k 持仓，等权分配

理论基础：
- 筹码集中度与短期动量正相关（散户行为偏差，A 股特有）
- 机构资金流预测力在中国市场 IC>3%（Fan et al. 2020）
- 融资余额变化反映杠杆偏好，对中盘股有显著择时能力
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import (
    EquityCandidateInfo,
    EquityRotationConfig,
    EquityRotationStrategy,
)
from aipha.strategy.param_space import ParamSpace, ParamSpec, ParamType
from aipha.strategy.registry import StrategyRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 默认因子权重
# ---------------------------------------------------------------------------

_DEFAULT_WEIGHTS: dict[str, float] = {
    # 筹码因子
    "chips_concentration": 1.5,
    "chips_cost_ratio": 1.0,
    "chips_asm": 0.5,
    # 资金流因子
    "flow_buy_ratio": 1.2,
    "flow_large_pct": 1.0,
    "flow_momentum_5d": 0.8,
    # 融资融券因子
    "margin_buy_intensity": 1.0,
    "margin_net_momentum": 0.8,
    # 分析师因子
    "analyst_buy_ratio": 0.6,
    "analyst_est_growth": 0.5,
}


@dataclass
class AltFactorConfig(EquityRotationConfig):
    """另类因子规则策略配置。"""

    factor_weights: dict[str, float] = field(
        default_factory=lambda: dict(_DEFAULT_WEIGHTS),
    )
    rank_normalize: bool = True
    winsorize_pct: float = 0.02
    min_factor_coverage: float = 0.3


@StrategyRegistry().register("alt_factor")
class AltFactorStrategy(EquityRotationStrategy):
    """另类因子规则选股策略。"""

    name = "alt_factor"

    def __init__(
        self, config: AltFactorConfig | None = None, **kwargs: Any,
    ) -> None:
        super().__init__()
        if config is not None:
            self._config = config
        else:
            valid = {
                f.name
                for f in AltFactorConfig.__dataclass_fields__.values()
            }
            filtered = {k: v for k, v in kwargs.items() if k in valid}
            self._config = AltFactorConfig(**filtered)

    @property
    def config(self) -> AltFactorConfig:
        return self._config

    @classmethod
    def param_space(cls) -> ParamSpace:
        return ParamSpace(strategy_name="alt_factor", specs=[
            ParamSpec("top_k", ParamType.INT, default=30, low=20, high=80),
            ParamSpec("rebalance_days", ParamType.INT, default=10, low=5, high=20),
        ])

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        ctx: StrategyContext,
    ) -> list[tuple[str, float]]:
        """基于另类因子对候选股打分。

        每个 candidate.metadata 中应包含因子值（由引擎/数据层注入）。
        对可用因子做截面 rank 归一化后加权合成。
        """
        import numpy as np

        cfg = self._config
        weights = cfg.factor_weights
        if not candidates:
            return []

        # 收集每个因子在截面上的原始值
        factor_vals: dict[str, list[float | None]] = {
            k: [] for k in weights
        }
        symbols = [c.symbol for c in candidates]
        for c in candidates:
            md = c.metadata or {}
            for k in weights:
                factor_vals[k].append(md.get(k))

        # 截面 rank 归一化
        # §13.2 debt: 此处使用 numpy argsort + linspace + NaN 覆盖率过滤 + 缺失填 0.5
        # 的实现，与 rank_normalize_list 行为差异较大。
        # 待 rank_normalize_list 扩展支持 numpy/NaN 后重构为委托调用。
        n = len(symbols)
        rank_scores: dict[str, np.ndarray] = {}
        for k, vals in factor_vals.items():
            arr = np.array(vals, dtype=float)
            valid = ~np.isnan(arr)
            coverage = valid.sum() / max(n, 1)
            if coverage < cfg.min_factor_coverage:
                continue
            ranked = np.full(n, np.nan)
            order = np.argsort(arr[valid])
            ranked_vals = np.zeros(int(valid.sum()))
            ranked_vals[order] = np.linspace(0, 1, int(valid.sum()))
            ranked[valid] = ranked_vals
            # 缺失值填中位数 0.5
            ranked[np.isnan(ranked)] = 0.5
            rank_scores[k] = ranked

        if not rank_scores:
            return [(s, 0.0) for s in symbols]

        # 加权合成
        total_weight = sum(
            weights[k] for k in rank_scores if k in weights
        )
        if total_weight <= 0:
            return [(s, 0.0) for s in symbols]

        composite = np.zeros(n)
        for k, ranked in rank_scores.items():
            w = weights.get(k, 0.0)
            composite += w * ranked
        composite /= total_weight

        return list(zip(symbols, composite.tolist()))
