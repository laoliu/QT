"""A 股均衡多因子轮动策略

策略逻辑：
1. **投资范围**：全 A 股中市值 30 亿~500 亿（中盘+中大盘），不限定指数成分
2. **核心因子**（四维度均衡）：
   - 价值因子 (25%) — 低 PE/PB，估值回归
   - 质量因子 (25%) — 高 ROE + 盈利增长，长期稳定超额
   - 动量因子 (25%) — 跳跃近期反转后的中期趋势延续
   - 低波动因子 (25%) — 低波标的风险调整后收益更优
3. **辅助因子**（可选启用）：
   - 股息率 — 高 div yield 作为安全垫
   - 低负债 — 降低财务风险
4. **调仓**：每 20 交易日，buffer_k=10 控制换手
5. **持仓**：top_k=30 等权分散，反波动率加权可选
6. **风控**：止损 15%、追踪止损 10%、行业偏离约束

Alpha 来源假说：
- A 股中盘股（30-500 亿）流动性充足但机构覆盖度低于大盘，存在定价错误空间
- Quality+Value 提供长期安全垫，Momentum+LowVol 提供趋势跟随与风险控制
- 四因子等权降低对单一风格的依赖，减少因子周期性衰退的冲击

继承 EquityRotationStrategy 基类，复用预过滤/止损/调仓/轮动指令生成逻辑，
仅实现 _score_candidates() 多因子打分排名。

对齐 copilot-instructions.md：
- 使用 MarketRulesCNEquity（T+1、涨跌停、停牌检测、手数=100）
- 使用 FeeModelCNEquity（万一佣金 + 印花税 + 过户费）
- 所有假设写入 configs/backtest/balanced_multifactor*.yml
- 因子计算无前视偏差（使用截面排名 + PIT 数据）
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
    compute_earnings_growth_factor,
    compute_earnings_stability_factor,
    compute_momentum_factor,
    compute_piotroski_factor,
    compute_quality_factor,
    compute_size_factor,
    compute_str_factor,
    compute_turnover_factor,
    compute_valuation_percentile_factor,
    compute_volatility_factor,
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
class BalancedMultiFactorConfig(EquityRotationConfig):
    """均衡多因子策略配置

    设计原则：
    - 四核心因子等权（各 25%），不偏向任何单一风格
    - 中盘偏大选股范围，兼顾容量与定价效率间的alpha空间
    - 足够分散（top_k=30），降低个股特异风险
    """

    # 覆盖默认值 — 中盘+中大盘宽范围
    top_k: int = 30
    rebalance_days: int = 20        # 月频调仓
    min_mv: float = 300_000.0       # 30 亿（万元）
    max_mv: float = 5_000_000.0     # 500 亿（万元）
    min_price: float = 5.0
    max_price: float = 200.0        # 不过滤高价优质股
    min_turnover: float = 1000.0    # 万元，确保充足流动性
    min_listed_days: int = 120      # 排除次新（4 个月）
    buffer_k: int = 10              # 大缓冲区降低换手

    # 止损
    stop_loss_pct: float = 0.15
    trailing_stop_pct: float = 0.10  # 追踪止损保护浮盈

    # 加权模式
    inverse_vol_weighting: bool = True  # 默认启用反波动率加权（风险平价）

    # 集中度控制
    max_single_pct: float = 0.06    # 单票最大 6%
    cash_reserve_pct: float = 0.03  # 现金缓冲 3%

    # --- 四核心因子权重（合计 = 1.0）---
    weight_value: float = 0.25      # 价值：低 PE
    weight_quality: float = 0.25    # 质量：高 ROE
    weight_momentum: float = 0.25   # 动量：中期趋势（skip 最近 5 天避免反转）
    weight_volatility: float = 0.25 # 低波动

    # --- 辅助因子权重（启用时从核心因子中扣除）---
    weight_dividend: float = 0.0    # 股息率（0=不启用，启用建议 0.10-0.15）
    weight_earnings_growth: float = 0.0  # 盈利增长（0=不启用，启用建议 0.05-0.10）
    weight_piotroski: float = 0.0   # Piotroski F-Score（0=不启用，建议 0.05-0.10）
    weight_earnings_stability: float = 0.0  # 盈利稳定性（0=不启用，建议 0.05）
    weight_valuation_percentile: float = 0.0  # 估值分位（0=不启用，建议 0.05-0.10）
    weight_accruals_quality: float = 0.0  # 应计盈余质量（0=不启用，建议 0.05）
    weight_debt: float = 0.0          # 低负债（0=不启用，启用建议 0.05-0.10）
    weight_turnover: float = 0.0      # 低换手（0=不启用，启用建议 0.05）
    weight_size: float = 0.0          # 小市值（0=不启用，启用建议 0.15-0.30）
    weight_str: float = 0.0           # 短期反转（0=不启用，启用建议 0.15-0.30）

    # --- 因子参数 ---
    momentum_lookback: int = 60     # 中期动量（60 天 ≈ 3 个月）
    momentum_skip_recent: int = 5   # 跳过最近 5 天（避免短期反转效应）
    momentum_multi_period: bool = True  # 多周期动量混合
    volatility_lookback: int = 60   # 波动率回看窗口
    value_metric: str = "pe_ttm"    # 价值指标
    quality_metric: str = "roe"     # 质量指标
    str_lookback: int = 5           # 短期反转回看天数

    # Regime 自适应开关
    regime_adaptive: bool = False
    # BULL：加动量，减低波
    regime_bull_delta_value: float = -0.03
    regime_bull_delta_quality: float = 0.0
    regime_bull_delta_momentum: float = 0.08
    regime_bull_delta_volatility: float = -0.05
    # BEAR：减动量，加低波+价值（防守）
    regime_bear_delta_value: float = 0.05
    regime_bear_delta_quality: float = 0.03
    regime_bear_delta_momentum: float = -0.08
    regime_bear_delta_volatility: float = 0.05


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------

@StrategyRegistry().register("balanced_multifactor")
class BalancedMultiFactorStrategy(EquityRotationStrategy):
    """A 股均衡多因子轮动策略

    核心思路：Quality + Value + Momentum + Low Volatility 四维度均衡，
    不偏向任何单一风格，追求穿越牛熊的稳健超额收益。

    与 SmallCapRotation 的根本区别：
    - 无小盘偏置 → 中盘选股规避容量/流动性风险
    - 四因子等权 → 抵抗单因子周期性衰退
    - 月频调仓 + 大缓冲 → 降低换手与交易成本侵蚀
    """

    name = "balanced_multifactor"

    def __init__(self, config: BalancedMultiFactorConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or BalancedMultiFactorConfig(**kwargs)

    @property
    def config(self) -> BalancedMultiFactorConfig:
        """策略配置。"""
        return self._config

    # ------------------------------------------------------------------
    # Regime 自适应权重
    # ------------------------------------------------------------------

    def _get_weights(self, context: StrategyContext) -> dict[str, float]:
        """获取因子权重，支持 regime 自适应。

        总权重归一化到 1.0。辅助因子（dividend/earnings_growth）按比例
        从核心因子权重中扣除。
        """
        cfg = self._config

        # 基础四因子权重
        base = {
            "value": cfg.weight_value,
            "quality": cfg.weight_quality,
            "momentum": cfg.weight_momentum,
            "volatility": cfg.weight_volatility,
            "dividend": cfg.weight_dividend,
            "earnings_growth": cfg.weight_earnings_growth,
            "piotroski": cfg.weight_piotroski,
            "earnings_stability": cfg.weight_earnings_stability,
            "valuation_percentile": cfg.weight_valuation_percentile,
            "accruals_quality": cfg.weight_accruals_quality,
            "debt": cfg.weight_debt,
            "turnover": cfg.weight_turnover,
            "size": cfg.weight_size,
            "str": cfg.weight_str,
        }

        # Regime 自适应
        if cfg.regime_adaptive:
            regime = (context.metadata or {}).get("market_regime", "NEUTRAL")
            if regime == "BULL":
                deltas = {
                    "value": cfg.regime_bull_delta_value,
                    "quality": cfg.regime_bull_delta_quality,
                    "momentum": cfg.regime_bull_delta_momentum,
                    "volatility": cfg.regime_bull_delta_volatility,
                }
            elif regime == "BEAR":
                deltas = {
                    "value": cfg.regime_bear_delta_value,
                    "quality": cfg.regime_bear_delta_quality,
                    "momentum": cfg.regime_bear_delta_momentum,
                    "volatility": cfg.regime_bear_delta_volatility,
                }
            else:
                deltas = {}

            for k, d in deltas.items():
                base[k] = max(0.0, base.get(k, 0.0) + d)

        # 归一化
        total = sum(base.values())
        if total > 0:
            base = {k: v / total for k, v in base.items()}
        return base

    # ------------------------------------------------------------------
    # 多因子排名
    # ------------------------------------------------------------------

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """六因子加权排名融合（warmup 友好）。

        核心因子（各 ~25%）：
        - 价值（低 PE）— 估值回归
        - 质量（高 ROE）— 盈利能力
        - 动量（中期趋势，跳过近 5 天）— 趋势延续
        - 低波动 — 风险调整后收益

        辅助因子（可选）：
        - 股息率 — 收益安全垫
        - 盈利增长 — 成长性

        Warmup 策略：在价格历史不足时（前 N 天），动量/波动率因子不可用，
        自动跳过并将权重重新分配到可用因子（value+quality），避免 NaN 污染。
        """
        cfg = self._config
        if not candidates:
            return []

        w = self._get_weights(context)

        # ------------------------------------------------------------------
        # 分两组：不依赖价格历史的"静态因子" vs 依赖价格历史的"动态因子"
        # 动态因子在 warmup 期间可能全部 NaN，此时只用静态因子
        # ------------------------------------------------------------------
        import math

        # 静态因子（只需 metadata）
        static_factors: list[tuple[str, dict[str, float]]] = []
        # 动态因子（需要 _price_history）
        dynamic_factors: list[tuple[str, dict[str, float]]] = []

        if w.get("value", 0) > 0.001:
            f_value = compute_value_factor(candidates, metric=cfg.value_metric)
            static_factors.append(("value", f_value))

        if w.get("quality", 0) > 0.001:
            f_quality = compute_quality_factor(
                candidates, metric=cfg.quality_metric,
            )
            static_factors.append(("quality", f_quality))

        if w.get("dividend", 0) > 0.001:
            f_div = compute_dividend_factor(candidates)
            static_factors.append(("dividend", f_div))

        if w.get("earnings_growth", 0) > 0.001:
            f_eg = compute_earnings_growth_factor(candidates)
            static_factors.append(("earnings_growth", f_eg))

        if w.get("piotroski", 0) > 0.001:
            f_pio = compute_piotroski_factor(candidates)
            static_factors.append(("piotroski", f_pio))

        if w.get("earnings_stability", 0) > 0.001:
            f_es = compute_earnings_stability_factor(candidates)
            static_factors.append(("earnings_stability", f_es))

        if w.get("valuation_percentile", 0) > 0.001:
            f_vp = compute_valuation_percentile_factor(candidates)
            static_factors.append(("valuation_percentile", f_vp))

        if w.get("accruals_quality", 0) > 0.001:
            f_aq = compute_accruals_quality_factor(candidates)
            static_factors.append(("accruals_quality", f_aq))

        if w.get("debt", 0) > 0.001:
            f_debt = compute_debt_factor(candidates)
            static_factors.append(("debt", f_debt))

        if w.get("turnover", 0) > 0.001:
            f_to = compute_turnover_factor(candidates)
            static_factors.append(("turnover", f_to))

        if w.get("size", 0) > 0.001:
            f_size = compute_size_factor(candidates)
            static_factors.append(("size", f_size))

        if w.get("momentum", 0) > 0.001:
            f_mom = compute_momentum_factor(
                self._price_history, candidates,
                lookback=cfg.momentum_lookback,
                skip_recent=cfg.momentum_skip_recent,
                multi_period=cfg.momentum_multi_period,
            )
            dynamic_factors.append(("momentum", f_mom))

        if w.get("volatility", 0) > 0.001:
            f_vol = compute_volatility_factor(
                self._price_history, candidates,
                lookback=cfg.volatility_lookback,
            )
            dynamic_factors.append(("volatility", f_vol))

        if w.get("str", 0) > 0.001:
            f_str = compute_str_factor(
                self._price_history, candidates,
                lookback=cfg.str_lookback,
            )
            dynamic_factors.append(("str", f_str))

        # 判断动态因子可用性：如果有效比例 < 30% 则视为不可用（warmup 中）
        _VALID_THRESHOLD = 0.30
        usable_dynamic: list[tuple[str, dict[str, float]]] = []
        for name, fv in dynamic_factors:
            n_valid = sum(1 for v in fv.values() if math.isfinite(v))
            if len(fv) > 0 and n_valid / len(fv) >= _VALID_THRESHOLD:
                usable_dynamic.append((name, fv))

        # 合并可用因子并重新分配权重
        all_usable = static_factors + usable_dynamic
        if not all_usable:
            return []  # 无可用因子

        usable_names = {name for name, _ in all_usable}
        raw_weights = {name: w.get(name, 0) for name in usable_names}
        w_total = sum(raw_weights.values())
        if w_total <= 0:
            return []
        norm_weights = {k: v / w_total for k, v in raw_weights.items()}

        # 执行排名归一化
        scores: dict[str, float] = {}
        for name, fv in all_usable:
            self.rank_normalize(candidates, fv, norm_weights[name], scores)

        return self.rank_sort(scores)
