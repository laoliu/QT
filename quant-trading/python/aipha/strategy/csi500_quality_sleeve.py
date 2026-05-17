"""CSI500 中盘质量 Sleeve 轮动策略

作为组合第二大容量源，与 CSI2000 ML 小盘 sleeve 形成天然分散化。
不复制微盘 alpha，而是在 30-100B 中盘域追求质量复利型超额。

策略逻辑：
1. **因子组成**（质量复利型）：
   - 质量因子 (30%) — 高 ROE + Piotroski F-Score
   - 价值因子 (25%) — 低 PE/PB，价值回归
   - 低波动因子 (20%) — 低波标的风险调整后收益更优
   - 低换手因子 (15%) — 稳定持仓降低交易成本
   - 盈利稳定性 (10%) — 盈利不大幅波动的公司更可持续
2. **投资范围**：CSI500 成分域（30-100B 流通市值）
3. **调仓**：每 20 交易日（月频），buffer_k=10 控制换手
4. **持仓**：top_k=30 等权分散

Alpha 来源假说：
- CSI500 中盘股机构覆盖度适中，定价效率介于大盘与小盘之间
- 质量 + 价值 + 低波动三因子在 A 股中盘有持续的风险溢价
- 低换手 + 盈利稳定性进一步筛选出"质量复利"型标的
- 与 CSI2000 ML sleeve 使用完全不同的 universe + 因子 → 低相关性
- 容量充足（CSI500 平均日成交额远大于 CSI2000）

对齐设计约束：
- §1.2 Tier L 适用（容量充足）
- §原则 D 容量验证：CSI500 日均成交额 > 1亿元，容量无瓶颈
- §原则 E 分散化：与 CSI2000 ρ ≈ 0（不同 universe）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_earnings_stability_factor,
    compute_piotroski_factor,
    compute_quality_factor,
    compute_turnover_factor,
    compute_value_factor,
    compute_volatility_factor,
)
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import (
    EquityCandidateInfo,
    EquityRotationConfig,
    EquityRotationStrategy,
    rank_normalize,
)
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class CSI500QualitySleeveConfig(EquityRotationConfig):
    """CSI500 中盘质量 Sleeve 配置"""

    # 持仓
    top_k: int = 30
    rebalance_days: int = 20  # 月频

    # 选股范围 — CSI500 中盘
    min_mv: float = 300_000.0       # 30 亿（万元）
    max_mv: float = 1_000_000.0     # 100 亿（万元）
    min_price: float = 5.0
    max_price: float = 200.0
    min_turnover: float = 1000.0    # 确保充足流动性
    min_listed_days: int = 180
    buffer_k: int = 10              # 大缓冲降低换手
    check_index_member: bool = False

    # 风控
    stop_loss_pct: float = 0.20
    trailing_stop_pct: float = 0.10
    max_single_pct: float = 0.05
    cash_reserve_pct: float = 0.03
    max_industry_pct: float = 0.20  # 行业分散

    # 加权
    inverse_vol_weighting: bool = True  # 反波动率加权

    # --- 因子权重 ---
    weight_quality: float = 0.30       # 质量 (ROE)
    weight_value: float = 0.25         # 价值 (PE/PB)
    weight_volatility: float = 0.20    # 低波动
    weight_low_turnover: float = 0.15  # 低换手
    weight_earnings_stab: float = 0.10 # 盈利稳定性

    # 辅助因子权重（可选启用）
    weight_piotroski: float = 0.0      # Piotroski F-Score

    # --- 因子参数 ---
    value_metric: str = "pe_ttm"
    quality_metric: str = "roe"
    volatility_lookback: int = 60      # 3 个月波动率
    momentum_lookback: int = 60        # 基类需要


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------

@StrategyRegistry().register("csi500_quality_sleeve")
class CSI500QualitySleeveStrategy(EquityRotationStrategy):
    """CSI500 中盘质量 Sleeve 轮动策略。

    聚焦质量 + 价值 + 低波动 + 低换手的复利型选股。
    """

    name = "csi500_quality_sleeve"

    def __init__(
        self, config: CSI500QualitySleeveConfig | None = None, **kwargs,
    ):
        super().__init__()
        self._config = config or CSI500QualitySleeveConfig(**kwargs)

    @property
    def config(self) -> CSI500QualitySleeveConfig:
        return self._config

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """质量复利型多因子加权排名。

        因子组成：
        - 质量 (ROE) — 持续盈利能力
        - 价值 (PE/PB) — 估值安全垫
        - 低波动 — 风险调整后收益更优
        - 低换手 — 交易冷门 → 定价偏差持续
        - 盈利稳定性 — 盈利波动小的公司更可持续

        使用排名归一化后加权融合。
        """
        import math

        cfg = self._config
        if not candidates:
            return []

        # 收集各因子得分
        factor_scores: list[tuple[str, dict[str, float], float]] = []

        # 质量因子
        if cfg.weight_quality > 0.001:
            f_q = compute_quality_factor(
                candidates, metric=cfg.quality_metric,
            )
            factor_scores.append(("quality", f_q, cfg.weight_quality))

        # 价值因子
        if cfg.weight_value > 0.001:
            f_v = compute_value_factor(candidates, metric=cfg.value_metric)
            factor_scores.append(("value", f_v, cfg.weight_value))

        # 低波动因子
        if cfg.weight_volatility > 0.001:
            f_vol = compute_volatility_factor(
                self._price_history, candidates,
                lookback=cfg.volatility_lookback,
            )
            factor_scores.append(("volatility", f_vol, cfg.weight_volatility))

        # 低换手因子
        if cfg.weight_low_turnover > 0.001:
            f_to = compute_turnover_factor(candidates)
            factor_scores.append(("turnover", f_to, cfg.weight_low_turnover))

        # 盈利稳定性
        if cfg.weight_earnings_stab > 0.001:
            f_es = compute_earnings_stability_factor(candidates)
            factor_scores.append(("earnings_stab", f_es, cfg.weight_earnings_stab))

        # Piotroski F-Score (可选)
        if cfg.weight_piotroski > 0.001:
            f_pio = compute_piotroski_factor(candidates)
            factor_scores.append(("piotroski", f_pio, cfg.weight_piotroski))

        if not factor_scores:
            return []

        # 排名归一化 + 加权融合
        symbols = [c.symbol for c in candidates]
        composite: dict[str, float] = {s: 0.0 for s in symbols}
        total_weight = sum(w for _, _, w in factor_scores)

        for name, fdict, weight in factor_scores:
            values = [fdict.get(s, float("nan")) for s in symbols]
            # 将有效值排名归一化到 [0, 1]
            valid_pairs = [
                (i, v) for i, v in enumerate(values) if math.isfinite(v)
            ]
            if not valid_pairs:
                continue
            valid_pairs.sort(key=lambda x: x[1])
            n_valid = len(valid_pairs)
            ranks = [0.5] * len(symbols)
            for rank_idx, (orig_idx, _) in enumerate(valid_pairs):
                ranks[orig_idx] = rank_idx / max(n_valid - 1, 1)

            norm_w = weight / total_weight if total_weight > 0 else 0
            for i, sym in enumerate(symbols):
                composite[sym] += norm_w * ranks[i]

        # 按分数降序排列
        ranked = sorted(
            composite.items(), key=lambda x: x[1], reverse=True,
        )
        return [sym for sym, _ in ranked[:cfg.top_k]]
