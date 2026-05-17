"""A 股行为因子 Sleeve 轮动策略

独立的行为金融因子 sleeve，与 ML/LGB 主模型完全解耦，
避免 IC Trap（CF-015/020/022: 因子加入 LGB 提高 IC 但降低 AR）。

策略逻辑：
1. **因子组成**（四个行为偏差因子等权融合）：
   - ABT (异常换手, 25%) — 低注意力溢价，反向信号
   - 3 月反转 (25%) — 中期过度反应回归
   - IVOL (特异波动率, 25%) — 低波动异象
   - MAX (最大日收益, 25%) — 反彩票效应
2. **投资范围**：CSI2000 小盘域（与 ML 同域但不同 alpha 来源 → 低相关）
3. **调仓**：每 10 交易日，buffer_k=5 控制换手
4. **持仓**：top_k=30 等权分散

Alpha 来源假说：
- A 股散户占比高（~60%），行为偏差持续存在
- ABT + MAX + IVOL 捕捉散户注意力/彩票偏好/波动率定价偏差
- 3 月反转捕捉中期过度反应的均值回归
- 四因子正交性高，组合后信号更稳健
- 与 ML/LGB 模型使用完全不同的 alpha 来源 → 组合分散化

References:
    Barber & Odean (2008) — Attention-driven trading
    Bali, Cakici, Whitelaw (2011) — MAX effect
    Ang et al. (2006) — IVOL puzzle
    De Bondt & Thaler (1985) — Overreaction
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_abnormal_turnover_factor,
    compute_ivol_factor,
    compute_max_daily_return_factor,
    compute_overreaction_composite,
    compute_str_factor,
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
class BehavioralSleeveConfig(EquityRotationConfig):
    """行为因子 Sleeve 配置"""

    # 持仓
    top_k: int = 30
    rebalance_days: int = 10

    # 选股范围 — CSI2000 小盘
    min_mv: float = 50_000.0       # 5 亿（万元）
    max_mv: float = 300_000.0      # 30 亿（万元）
    min_price: float = 3.0
    max_price: float = 50.0
    min_turnover: float = 500.0
    min_listed_days: int = 120
    buffer_k: int = 5

    # 风控
    stop_loss_pct: float = 0.15
    trailing_stop_pct: float = 0.0
    max_single_pct: float = 0.06
    cash_reserve_pct: float = 0.03

    # --- 行为因子权重 ---
    weight_abt: float = 0.25       # 异常换手
    weight_reversal: float = 0.25  # 3 月反转
    weight_ivol: float = 0.25      # 特异波动率
    weight_max: float = 0.25       # 最大日收益

    # --- 因子参数 ---
    abt_short_window: int = 20     # ABT 短期窗口
    abt_long_window: int = 250     # ABT 长期窗口
    reversal_lookback: int = 60    # 3 月反转回看天数
    reversal_skip_recent: int = 5  # 跳过最近 5 天
    ivol_lookback: int = 20        # IVOL 计算窗口
    max_lookback: int = 20         # MAX 回看天数

    # --- 历史窗口 ---
    momentum_lookback: int = 60    # 基类需要
    volatility_lookback: int = 20  # 基类需要


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------

@StrategyRegistry().register("behavioral_sleeve")
class BehavioralSleeveStrategy(EquityRotationStrategy):
    """A 股行为因子 Sleeve 轮动策略。

    与 ML/LGB 主模型完全解耦，使用纯行为金融因子组合。
    """

    name = "behavioral_sleeve"

    def __init__(self, config: BehavioralSleeveConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or BehavioralSleeveConfig(**kwargs)
        self._volume_history: dict[str, list[float]] = {}

    @property
    def config(self) -> BehavioralSleeveConfig:
        return self._config

    def _on_start_extra(self) -> None:
        self._volume_history.clear()

    def _on_bar_update_state(self, bars: dict[str, Bar], context: StrategyContext) -> None:
        """维护成交量历史窗口，供 ABT 因子使用。"""
        cfg = self._config
        max_len = cfg.abt_long_window + 10  # +10 缓冲
        for sym, bar in bars.items():
            vhist = self._volume_history.setdefault(sym, [])
            vhist.append(bar.volume if bar.volume else 0.0)
            if len(vhist) > max_len:
                del vhist[:-max_len]

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """四行为因子加权排名融合。

        因子组成：
        - ABT (异常换手) — 低异常关注度优先
        - 3 月反转 — 中期过度反应回归
        - IVOL (特异波动率) — 低波动异象
        - MAX (最大日收益) — 反彩票效应

        使用 compute_overreaction_composite() 做排名融合，
        确保各因子量纲统一、互不干扰。
        """
        cfg = self._config
        if not candidates:
            return []

        subfactors: dict[str, dict[str, float]] = {}
        weights: dict[str, float] = {}

        # --- ABT 因子 ---
        if cfg.weight_abt > 0.001:
            f_abt = compute_abnormal_turnover_factor(
                self._volume_history,
                candidates,
                short_window=cfg.abt_short_window,
                long_window=cfg.abt_long_window,
            )
            subfactors["abt"] = f_abt
            weights["abt"] = cfg.weight_abt

        # --- 3 月反转因子（利用 STR 接口，lookback=60, skip_recent=5）---
        if cfg.weight_reversal > 0.001:
            f_rev = self._compute_reversal(candidates)
            subfactors["reversal"] = f_rev
            weights["reversal"] = cfg.weight_reversal

        # --- IVOL 因子 ---
        if cfg.weight_ivol > 0.001:
            f_ivol = compute_ivol_factor(
                self._price_history,
                candidates,
                lookback=cfg.ivol_lookback,
            )
            subfactors["ivol"] = f_ivol
            weights["ivol"] = cfg.weight_ivol

        # --- MAX 因子 ---
        if cfg.weight_max > 0.001:
            f_max = compute_max_daily_return_factor(
                self._price_history,
                candidates,
                lookback=cfg.max_lookback,
            )
            subfactors["max"] = f_max
            weights["max"] = cfg.weight_max

        if not subfactors:
            return []

        # 排名融合
        composite = compute_overreaction_composite(subfactors, weights)

        # 按分数降序排列，返回 top_k 的 symbol 列表
        ranked = sorted(
            composite.items(),
            key=lambda x: x[1],
            reverse=True,
        )
        return [sym for sym, _ in ranked[:cfg.top_k]]

    def _compute_reversal(
        self, candidates: list[EquityCandidateInfo],
    ) -> dict[str, float]:
        """3 月反转因子：lookback=60 天，跳过最近 5 天。

        计算 t-65 到 t-5 期间的累计收益率，取反（跌得多→分高）。
        """
        cfg = self._config
        lookback = cfg.reversal_lookback
        skip = cfg.reversal_skip_recent
        total_needed = lookback + skip + 1

        result: dict[str, float] = {}
        for c in candidates:
            hist = self._price_history.get(c.symbol, [])
            if len(hist) < total_needed:
                result[c.symbol] = float("nan")
                continue
            p_start = hist[-(lookback + skip + 1)]
            p_end = hist[-(skip + 1)]
            if p_start > 1e-8:
                ret = (p_end - p_start) / p_start
                result[c.symbol] = -ret  # 反转：跌 → 高分
            else:
                result[c.symbol] = float("nan")
        return result
