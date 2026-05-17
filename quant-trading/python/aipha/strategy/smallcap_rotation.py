"""A 股小盘因子轮动策略

策略逻辑：
1. **股票池**：全 A 股，排除 ST/*ST/退市整理/停牌/次新（上市 < 60 天）
2. **核心因子**：
   - 小市值因子（weight=0.40）：流通市值越小越好（A 股小盘效应）
   - 动量因子（weight=0.25）：过去 20 日收益率（短期动量/反转可配置）
   - 低换手因子（weight=0.15）：低换手率标的通常被低估
   - 低波动因子（weight=0.20）：低波动标的风险调整后收益更优
3. **调仓周期**：每 N 个交易日（默认 10 天）
4. **持仓数**：等权持有 top_k 只（默认 30）
5. **风控**：止损 15%、单票上限可配置

继承 EquityRotationStrategy 基类，复用预过滤/止损/调仓/轮动指令生成逻辑，
仅实现 _score_candidates() 因子打分排名。

对齐 copilot-instructions.md：
- 使用 MarketRulesCNEquity（T+1、涨跌停、停牌检测、手数=100）
- 使用 FeeModelCNEquity（万一佣金 + 印花税 + 过户费）
- 使用 circ_mv 作为规模代理，无前视偏差
- UniversePolicy 显式指定（不得默认"全 A 股都可交易"）
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_momentum_factor,
    compute_quality_factor,
    compute_size_factor,
    compute_turnover_factor,
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
class SmallCapConfig(EquityRotationConfig):
    """小盘因子轮动策略配置

    继承 EquityRotationConfig，添加小盘策略特有字段。
    """

    # 覆盖默认值
    top_k: int = 30
    rebalance_days: int = 15       # 15 天调仓（降低换手提升稳定性）
    min_mv: float = 100_000.0     # 10 亿（万元）
    max_mv: float = 2_000_000.0   # 200 亿（万元）
    min_price: float = 3.0
    max_price: float = 50.0       # 排除高价股
    min_turnover: float = 500.0   # 万元
    min_listed_days: int = 60
    buffer_k: int = 8              # 增大缓冲区减少不必要换手

    # 动量因子反转开关
    momentum_invert: bool = False  # False=趋势动量, True=反转

    # 因子权重（合计应为 1.0, 含质量/价值锚定因子）
    weight_small_cap: float = 0.30   # 降低纯小盘权重（稳定性高于极端小盘）
    weight_momentum: float = 0.15    # 降低动量权重（在波动市场中信噪比较低）
    weight_turnover: float = 0.10    # 低换手率
    weight_volatility: float = 0.15  # 低波动保护
    weight_quality: float = 0.15     # 质量因子（ROE），作为稳定锚
    weight_value: float = 0.15       # 价值因子（低PE），作为估值保护

    # Regime 自适应开关与偏移量
    regime_adaptive: bool = False
    # BULL 偏移：加大小盘+动量，减少质量+波动+价值
    regime_bull_delta_small_cap: float = 0.05
    regime_bull_delta_momentum: float = 0.05
    regime_bull_delta_quality: float = -0.03
    regime_bull_delta_volatility: float = -0.04
    regime_bull_delta_value: float = -0.03
    regime_bull_delta_turnover: float = 0.0
    # BEAR 偏移：减小盘+动量，加大质量+波动+价值
    regime_bear_delta_small_cap: float = -0.08
    regime_bear_delta_momentum: float = -0.05
    regime_bear_delta_quality: float = 0.05
    regime_bear_delta_volatility: float = 0.05
    regime_bear_delta_value: float = 0.05
    regime_bear_delta_turnover: float = -0.02


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------

@StrategyRegistry().register("smallcap_rotation")
class SmallCapRotationStrategy(EquityRotationStrategy):
    """A 股小盘因子轮动策略

    通过多因子排名融合选择小盘优质股票，定期轮动。

    因子组合：
    - 小市值 → 捕捉 A 股小盘溢价
    - 短期动量 → 趋势延续/反转信号
    - 低换手 → 被忽视的低估标的
    - 低波动 → 风险调整后收益更优
    """

    name = "smallcap_rotation"

    # 因子权重超过此值发出 beta 暴露警告
    _BETA_WARNING_THRESHOLD = 0.40

    def __init__(self, config: SmallCapConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or SmallCapConfig(**kwargs)
        self._check_factor_weights()

    def _check_factor_weights(self) -> None:
        """检查因子权重，对超阈值的单因子发出 beta 暴露警告。"""
        cfg = self._config
        weight_map = {
            "weight_small_cap": cfg.weight_small_cap,
            "weight_momentum": cfg.weight_momentum,
            "weight_turnover": cfg.weight_turnover,
            "weight_volatility": cfg.weight_volatility,
            "weight_quality": cfg.weight_quality,
            "weight_value": cfg.weight_value,
        }
        for name, w in weight_map.items():
            if w > self._BETA_WARNING_THRESHOLD:
                logger.warning(
                    "[SmallCapRotation] 因子 %s 权重 %.0f%% 超过 %.0f%% 阈值，"
                    "策略可能退化为纯 beta 暴露（非真正 alpha）。"
                    "建议降至 ≤ %.0f%%",
                    name, w * 100, self._BETA_WARNING_THRESHOLD * 100,
                    self._BETA_WARNING_THRESHOLD * 100,
                )

    @property
    def config(self) -> SmallCapConfig:
        """策略配置。"""
        return self._config

    # ------------------------------------------------------------------
    # Regime 自适应权重
    # ------------------------------------------------------------------

    def _get_regime_weights(self, context: StrategyContext) -> dict[str, float]:
        """根据 market_regime 计算调整后的因子权重。

        如果 regime_adaptive=False 或 regime 信息不可用，
        直接返回默认静态权重。

        权重调整规则：
        - BULL：加大小盘+动量，减少质量+波动+价值（进攻）
        - BEAR：减小盘+动量，加大质量+波动+价值（防守）
        - NEUTRAL / 未知：使用默认权重
        """
        cfg = self._config
        base = {
            "small_cap": cfg.weight_small_cap,
            "momentum": cfg.weight_momentum,
            "turnover": cfg.weight_turnover,
            "volatility": cfg.weight_volatility,
            "quality": cfg.weight_quality,
            "value": cfg.weight_value,
        }

        if not cfg.regime_adaptive:
            return base

        regime = (context.metadata or {}).get("market_regime", "NEUTRAL")
        if regime == "BULL":
            deltas = {
                "small_cap": cfg.regime_bull_delta_small_cap,
                "momentum": cfg.regime_bull_delta_momentum,
                "turnover": cfg.regime_bull_delta_turnover,
                "volatility": cfg.regime_bull_delta_volatility,
                "quality": cfg.regime_bull_delta_quality,
                "value": cfg.regime_bull_delta_value,
            }
        elif regime == "BEAR":
            deltas = {
                "small_cap": cfg.regime_bear_delta_small_cap,
                "momentum": cfg.regime_bear_delta_momentum,
                "turnover": cfg.regime_bear_delta_turnover,
                "volatility": cfg.regime_bear_delta_volatility,
                "quality": cfg.regime_bear_delta_quality,
                "value": cfg.regime_bear_delta_value,
            }
        else:
            return base

        # 应用 delta 并 clamp 到 >=0
        adjusted = {k: max(0.0, base[k] + deltas.get(k, 0.0)) for k in base}
        # 归一化到 sum=1.0
        total = sum(adjusted.values())
        if total > 0:
            adjusted = {k: v / total for k, v in adjusted.items()}
        else:
            adjusted = base
        return adjusted

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """多因子排名融合，返回 top_k 个 symbol。

        核心因子（捕捉收益）：
        - 小市值：A 股小盘溢价（权重最大但不过度集中）
        - 动量：短期趋势延续

        锚定因子（控制风险/稳定性）：
        - 质量（ROE）：过滤低质量小盘股
        - 价值（低PE）：估值保护，避免追高
        - 低换手率：被忽视的标的
        - 低波动：风险调整后收益
        """
        cfg = self._config
        if not candidates:
            return []

        # 获取 regime 自适应权重
        w = self._get_regime_weights(context)

        scores: dict[str, float] = {}

        # 小市值因子 — 使用 equity_factors 标准实现
        f_size = compute_size_factor(candidates)
        self.rank_normalize(
            candidates, f_size, w["small_cap"], scores,
            nan_handling="median",
        )

        # 动量因子 — 支持 invert（反转效应）
        # 动量因子 — 支持 invert（反转效应）
        f_mom = compute_momentum_factor(
            self._price_history, candidates,
            lookback=cfg.momentum_lookback, invert=cfg.momentum_invert,
            skip_recent=cfg.momentum_skip_recent,
            multi_period=cfg.momentum_multi_period,
        )
        self.rank_normalize(
            candidates, f_mom, w["momentum"], scores,
            nan_handling="median",
        )

        # 换手率因子（低换手优先）
        f_turnover = compute_turnover_factor(candidates)
        self.rank_normalize(
            candidates, f_turnover, w["turnover"], scores,
            nan_handling="median",
        )

        # 波动率因子（低波动优先）
        f_vol = compute_volatility_factor(
            self._price_history, candidates, lookback=cfg.volatility_lookback,
        )
        self.rank_normalize(
            candidates, f_vol, w["volatility"], scores,
            nan_handling="median",
        )

        # 质量因子（ROE 越高越优，过滤低质量小盘噪音）
        f_quality = compute_quality_factor(candidates, metric="roe")
        self.rank_normalize(
            candidates, f_quality, w["quality"], scores,
            nan_handling="median",
        )

        # 价值因子（低 PE，提供估值安全边际）
        f_value = compute_value_factor(candidates, metric="pe_ttm")
        self.rank_normalize(
            candidates, f_value, w["value"], scores,
            nan_handling="median",
        )

        # 按综合分降序排列
        return self.rank_sort(scores)
