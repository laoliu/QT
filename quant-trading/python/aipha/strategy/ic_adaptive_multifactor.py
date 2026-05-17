"""A 股 IC 自适应多因子策略（简化版）

与已有 adaptive_multifactor 的核心差异：
- **5 因子精简版**（vs 8 因子+温度计），减少过拟合风险
- **无市场择时/温度计** — 纯选股 alpha，避免择时失效拖累
- **IC 半衰期衰减加权** — 近期 IC 权重更高，老 IC 指数衰减
- **等权兜底** — IC 计算不足 60 日时退化为等权

核心机制：
1. 记录每个因子的滚动 Rank IC（因子排名 vs 次日收益排名的相关系数）
2. 对 IC 序列做半衰期加权平均 → 得到每因子的动态权重
3. IC > 0 的因子按权重打分，IC ≤ 0 的因子权重置零
4. 所有因子 IC ≤ 0 时退化为等权

Alpha 来源（5 因子）：
1. **质量 ROE** — IC 约 4-6%，全周期稳定
2. **价值 EP** — IC 约 3-5%，熊市强
3. **动量 60d** — IC 约 2-3%，牛市强
4. **低波动 60d** — IC 约 5-7%，熊市强
5. **规模 (-logMV)** — IC 约 3-5%，小盘溢价

预期表现：AR 12-18%, MDD 22-30%, Sharpe 0.5-0.9
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_momentum_factor,
    compute_quality_factor,
    compute_size_factor,
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
    pass

logger = logging.getLogger(__name__)

# IC 记录条目
_FACTOR_NAMES = ("quality", "value", "momentum", "volatility", "size")


@dataclass
class ICAdaptiveConfig(EquityRotationConfig):
    """IC 自适应多因子配置。"""

    top_k: int = 30
    rebalance_days: int = 15
    min_price: float = 3.0
    max_price: float = 300.0
    min_turnover: float = 800.0
    min_listed_days: int = 120
    stop_loss_pct: float = 0.15
    trailing_stop_pct: float = 0.12
    buffer_k: int = 10
    max_single_pct: float = 0.05
    min_mv: float = 300_000.0
    max_mv: float = 5_000_000.0
    max_industry_pct: float = 0.20

    # IC 加权参数
    ic_lookback: int = 60            # IC 滚动窗口（天）
    ic_halflife: int = 20            # 半衰期（天），近期 IC 权重更高
    ic_min_periods: int = 30         # IC 数据不足此天数时退化为等权

    # 等权基础权重（IC 退化时使用）
    base_weight_quality: float = 0.25
    base_weight_value: float = 0.20
    base_weight_momentum: float = 0.20
    base_weight_volatility: float = 0.20
    base_weight_size: float = 0.15

    # 因子参数
    momentum_lookback: int = 60
    momentum_skip_recent: int = 5
    volatility_lookback: int = 60

    # 宽松过滤
    max_pe: float = 100.0


@StrategyRegistry().register("ic_adaptive_multifactor")
class ICAdaptiveMultifactorStrategy(EquityRotationStrategy):
    """IC 自适应多因子策略（简化版）。

    5 因子 IC 加权，无温度计/择时，纯选股 alpha。
    """

    name = "ic_adaptive_multifactor"

    def __init__(
        self, config: ICAdaptiveConfig | None = None, **kwargs,
    ):
        super().__init__()
        self._config = config or ICAdaptiveConfig(**kwargs)
        # 因子得分历史（用于 IC 计算）
        self._factor_score_history: dict[str, deque[dict[str, float]]] = {
            name: deque(maxlen=self._config.ic_lookback)
            for name in _FACTOR_NAMES
        }
        # 次日收益历史
        self._return_history: deque[dict[str, float]] = deque(
            maxlen=self._config.ic_lookback,
        )
        self._prev_prices: dict[str, float] = {}
        self._ic_weights: dict[str, float] = {}

    @property
    def config(self) -> ICAdaptiveConfig:
        return self._config

    def _on_bar_update_state(self, bars: dict, context: StrategyContext) -> None:
        """每 bar 更新收益历史和 IC 权重。"""
        # 计算昨日到今日的收益
        current_returns: dict[str, float] = {}
        for sym, bar in bars.items():
            prev = self._prev_prices.get(sym)
            if prev and prev > 0 and bar.close > 0:
                current_returns[sym] = (bar.close - prev) / prev
            self._prev_prices[sym] = bar.close

        if current_returns:
            self._return_history.append(current_returns)

        # 定期更新 IC 权重
        if len(self._return_history) >= self._config.ic_min_periods:
            self._update_ic_weights()

    def _update_ic_weights(self) -> None:
        """基于滚动 Rank IC 计算动态因子权重。"""
        cfg = self._config
        n = len(self._return_history)
        if n < cfg.ic_min_periods:
            return

        # 半衰期指数衰减权重
        decay_weights = []
        for i in range(n):
            age = n - 1 - i
            w = math.pow(0.5, age / cfg.ic_halflife)
            decay_weights.append(w)

        weighted_ic: dict[str, float] = {}
        for fname in _FACTOR_NAMES:
            hist = self._factor_score_history[fname]
            if len(hist) < cfg.ic_min_periods:
                weighted_ic[fname] = 0.0
                continue

            # 计算每期 Rank IC (Spearman rank correlation proxy)
            ic_sum = 0.0
            w_sum = 0.0
            for i in range(min(n, len(hist))):
                f_scores = hist[i] if i < len(hist) else {}
                r_scores = self._return_history[i] if i < n else {}
                common = set(f_scores.keys()) & set(r_scores.keys())
                if len(common) < 10:
                    continue
                # 简化 Rank IC: 排名相关
                f_vals = [f_scores[s] for s in common]
                r_vals = [r_scores[s] for s in common]
                ic = self._rank_correlation(f_vals, r_vals)
                if math.isfinite(ic):
                    w = decay_weights[i] if i < len(decay_weights) else 1.0
                    ic_sum += ic * w
                    w_sum += w

            weighted_ic[fname] = ic_sum / w_sum if w_sum > 0 else 0.0

        # IC > 0 的因子分配权重（正比于 IC），IC ≤ 0 置零
        positive = {k: v for k, v in weighted_ic.items() if v > 0}
        if positive:
            total = sum(positive.values())
            self._ic_weights = {k: v / total for k, v in positive.items()}
            for k in _FACTOR_NAMES:
                if k not in self._ic_weights:
                    self._ic_weights[k] = 0.0
        else:
            # 全部退化为等权
            self._ic_weights = {}

    @staticmethod
    def _rank_correlation(x: list[float], y: list[float]) -> float:
        """简化 Spearman Rank Correlation。"""
        n = len(x)
        if n < 5:
            return 0.0
        # 转为排名
        rx = [0.0] * n
        ry = [0.0] * n
        sx = sorted(range(n), key=lambda i: x[i])
        sy = sorted(range(n), key=lambda i: y[i])
        for rank, idx in enumerate(sx):
            rx[idx] = float(rank)
        for rank, idx in enumerate(sy):
            ry[idx] = float(rank)
        # Pearson on ranks
        mx = sum(rx) / n
        my = sum(ry) / n
        cov = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
        sx2 = sum((rx[i] - mx) ** 2 for i in range(n))
        sy2 = sum((ry[i] - my) ** 2 for i in range(n))
        denom = math.sqrt(sx2 * sy2)
        return cov / denom if denom > 0 else 0.0

    def _get_weights(self) -> dict[str, float]:
        """获取当前因子权重（IC加权 or 等权兜底）。"""
        cfg = self._config
        if self._ic_weights:
            return self._ic_weights
        return {
            "quality": cfg.base_weight_quality,
            "value": cfg.base_weight_value,
            "momentum": cfg.base_weight_momentum,
            "volatility": cfg.base_weight_volatility,
            "size": cfg.base_weight_size,
        }

    def _extra_filter(
        self,
        candidate: EquityCandidateInfo,
        context: StrategyContext,
    ) -> bool:
        pe = candidate.metadata.get("pe_ttm")
        if pe is not None and (pe <= 0 or pe > self._config.max_pe):
            return False
        return True

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """IC 加权 5 因子打分。"""
        cfg = self._config
        if not candidates:
            return []

        weights = self._get_weights()
        scores: dict[str, float] = {}

        # 计算各因子原始得分（同时记录到历史）
        factor_raw: dict[str, dict[str, float]] = {}

        # 质量
        f_q = compute_quality_factor(candidates, metric="roe")
        factor_raw["quality"] = f_q

        # 价值
        f_v = compute_value_factor(candidates, metric="pe_ttm")
        factor_raw["value"] = f_v

        # 动量
        f_m = compute_momentum_factor(
            self._price_history, candidates,
            lookback=cfg.momentum_lookback,
            skip_recent=cfg.momentum_skip_recent,
        )
        factor_raw["momentum"] = f_m

        # 低波动
        f_vol = compute_volatility_factor(
            self._price_history, candidates,
            lookback=cfg.volatility_lookback,
        )
        factor_raw["volatility"] = f_vol

        # 规模
        f_s = compute_size_factor(candidates)
        factor_raw["size"] = f_s

        # 记录因子得分历史（用于下次 IC 计算）
        for fname, fval in factor_raw.items():
            self._factor_score_history[fname].append(fval)

        # 加权打分
        for fname, fval in factor_raw.items():
            w = weights.get(fname, 0.0)
            if w > 0:
                self.rank_normalize(candidates, fval, w, scores)

        return self.rank_sort(scores)
