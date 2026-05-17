"""A 股自适应多因子策略（IC 加权 + 市场择时仓位管理）

策略逻辑：
1. **因子 IC 加权**：Rolling Rank IC（20 日）动态调整八因子权重，
   自动倾斜到近期有效因子，避免静态等权在因子周期性失效时的拖累。
2. **市场温度计仓位管理**：
   - 温度计 = 0.4×趋势信号 + 0.3×波动率信号 + 0.3×宽度信号
   - 映射到 [min_position_pct, 1.0] 的仓位比例
3. **熊市防御模式**：温度计低于阈值时，强制切换为低波+价值防御组合
4. **动态缓冲**：低温增大 buffer_k，减少换手

Alpha 来源假说：
- IC 加权捕获因子择时 alpha（短期有效因子获得更高权重）
- 温度计仓位管理在系统性下跌时降低暴露，保存利润
- 中盘股（30-500 亿）定价效率低于大盘，存在 alpha 空间
- 熊市防御模式在极端环境下切换风格，避免成长/动量因子崩溃

继承 EquityRotationStrategy 基类。
对齐 copilot-instructions.md：因子无前视偏差，PIT 数据，所有参数可配置。
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_debt_factor,
    compute_dividend_factor,
    compute_earnings_growth_factor,
    compute_momentum_factor,
    compute_piotroski_factor,
    compute_quality_factor,
    compute_value_factor,
    compute_volatility_factor,
)
from aipha.strategy.base import OrderIntent, StrategyContext
from aipha.strategy.equity_rotation_base import (
    EquityCandidateInfo,
    EquityRotationConfig,
    EquityRotationStrategy,
)
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


# ===========================================================================
# 配置
# ===========================================================================


@dataclass
class AdaptiveMultiFactorConfig(EquityRotationConfig):
    """自适应多因子策略配置。

    参数设计原则：
    - IC 加权使月度因子权重随市场环境自适应
    - 温度计 ∈ [0, 1]，映射到仓位 [min_position_pct, 1.0]
    - 防御阈值 < 进攻阈值，形成三段式：防御 / 中性 / 进攻
    """

    # 持仓
    top_k: int = 25
    rebalance_days: int = 15
    min_mv: float = 300_000.0       # 30 亿（万元）
    max_mv: float = 5_000_000.0     # 500 亿
    min_price: float = 5.0
    max_price: float = 200.0
    min_turnover: float = 2000.0    # 万元
    min_listed_days: int = 120
    buffer_k: int = 8
    buffer_k_defensive: int = 15    # 低温时更大缓冲

    # 止损
    stop_loss_pct: float = 0.12
    trailing_stop_pct: float = 0.08

    # 加权
    inverse_vol_weighting: bool = True
    max_single_pct: float = 0.06
    cash_reserve_pct: float = 0.03
    max_industry_pct: float = 0.20  # 行业上限 20%

    # --- 八因子基础权重（IC 加权为 0 时回退到此权重）---
    base_weight_value: float = 0.15
    base_weight_quality: float = 0.15
    base_weight_momentum: float = 0.15
    base_weight_volatility: float = 0.15
    base_weight_earnings_growth: float = 0.10
    base_weight_dividend: float = 0.10
    base_weight_piotroski: float = 0.10
    base_weight_debt: float = 0.10

    # --- IC 加权参数 ---
    ic_lookback: int = 20           # Rolling IC 回看天数
    ic_weight_alpha: float = 0.7    # IC 加权占比（0=纯静态，1=纯 IC）
    ic_min_abs: float = 0.02        # IC 绝对值低于此视为噪声，权重归零
    ic_decay_halflife: int = 10     # IC 指数衰减半衰期（天）

    # --- 市场温度计参数 ---
    temperature_enabled: bool = True
    temp_trend_weight: float = 0.4
    temp_vol_weight: float = 0.3
    temp_breadth_weight: float = 0.3
    temp_ma_short: int = 20         # 短均线
    temp_ma_mid: int = 60           # 中均线
    temp_ma_long: int = 120         # 长均线
    temp_vol_short: int = 20        # 短期波动率窗口
    temp_vol_long: int = 100        # 长期波动率窗口

    # --- 仓位管理 ---
    min_position_pct: float = 0.30  # 最低仓位 30%
    defensive_threshold: float = 0.30  # 温度计低于此进入防御模式
    offensive_threshold: float = 0.70  # 温度计高于此进入进攻模式

    # --- 防御模式因子权重 ---
    defensive_weight_volatility: float = 0.40
    defensive_weight_value: float = 0.25
    defensive_weight_dividend: float = 0.20
    defensive_weight_quality: float = 0.15

    # --- 因子计算参数 ---
    momentum_lookback: int = 60
    momentum_skip_recent: int = 5
    momentum_multi_period: bool = True
    volatility_lookback: int = 60
    value_metric: str = "pe_ttm"
    quality_metric: str = "roe"


# ===========================================================================
# 策略实现
# ===========================================================================


@StrategyRegistry().register("adaptive_multifactor")
class AdaptiveMultiFactorStrategy(EquityRotationStrategy):
    """A 股自适应多因子策略。

    在 balanced_multifactor 基础上的三大进化：
    1. IC 动态加权替代静态等权
    2. 市场温度计控制总仓位
    3. 熊市自动切换防御因子组合
    """

    name = "adaptive_multifactor"

    def __init__(
        self, config: AdaptiveMultiFactorConfig | None = None, **kwargs
    ):
        super().__init__()
        self._config = config or AdaptiveMultiFactorConfig(**kwargs)
        # IC 追踪状态
        self._factor_ic_history: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=self._config.ic_lookback)
        )
        # 上一期因子值快照（用于计算隔日 IC）
        self._prev_factor_scores: dict[str, dict[str, float]] = {}
        # 市场温度计
        self._temperature: float = 0.5  # 初始中性
        self._index_prices: deque[float] = deque(maxlen=max(
            self._config.temp_ma_long, self._config.temp_vol_long
        ) + 5)
        # 宽度指标：每日上涨标的占比
        self._breadth_history: deque[float] = deque(
            maxlen=self._config.temp_ma_mid
        )

    @property
    def config(self) -> AdaptiveMultiFactorConfig:
        return self._config

    # ------------------------------------------------------------------
    # 钩子
    # ------------------------------------------------------------------

    def _on_bar_update_state(
        self, bars: dict[str, Bar], context: StrategyContext
    ) -> None:
        """每根 bar 更新温度计 & 宽度指标。"""
        self._update_market_temperature(bars, context)

    # ------------------------------------------------------------------
    # 温度计计算
    # ------------------------------------------------------------------

    def _update_market_temperature(
        self, bars: dict[str, Bar], context: StrategyContext
    ) -> None:
        """更新市场温度计。

        综合趋势、波动率、宽度三个维度：
        - 趋势信号：基于均线排列（MA20>MA60>MA120）
        - 波动率信号：短期HV/长期HV比值越低越好
        - 宽度信号：上涨标的占比
        """
        cfg = self._config
        if not cfg.temperature_enabled:
            self._temperature = 0.5
            return

        # 收集当日市场指数价格（用所有标的均价近似）
        prices_today = []
        ups = 0
        total = 0
        for sym, bar in bars.items():
            if hasattr(bar, 'close') and bar.close and bar.close > 0:
                prices_today.append(bar.close)
                # 计算宽度：对比前一日
                hist = self._price_history.get(sym)
                if hist and len(hist) >= 2 and hist[-2] > 0:
                    total += 1
                    if bar.close > hist[-2]:
                        ups += 1

        if prices_today:
            # 用所有标的均价近似市场指数
            avg_price = sum(prices_today) / len(prices_today)
            self._index_prices.append(avg_price)

        # 宽度指标
        if total > 0:
            breadth = ups / total
        else:
            breadth = 0.5
        self._breadth_history.append(breadth)

        # 计算三个信号
        trend = self._calc_trend_signal()
        vol_sig = self._calc_volatility_signal()
        breadth_sig = self._calc_breadth_signal()

        # 加权合成温度计
        self._temperature = (
            cfg.temp_trend_weight * trend
            + cfg.temp_vol_weight * vol_sig
            + cfg.temp_breadth_weight * breadth_sig
        )
        self._temperature = max(0.0, min(1.0, self._temperature))

    def _calc_trend_signal(self) -> float:
        """趋势信号：MA20>MA60>MA120 递进评分 ∈ [0, 1]。

        - MA20 > MA60: +0.33
        - MA60 > MA120: +0.33
        - 当前价 > MA20: +0.34
        """
        prices = list(self._index_prices)
        cfg = self._config

        if len(prices) < cfg.temp_ma_short:
            return 0.5  # 数据不足，中性

        ma_short = sum(prices[-cfg.temp_ma_short:]) / cfg.temp_ma_short

        score = 0.0

        # 当前价 > MA短
        if prices[-1] > ma_short:
            score += 0.34

        # MA短 > MA中
        if len(prices) >= cfg.temp_ma_mid:
            ma_mid = sum(prices[-cfg.temp_ma_mid:]) / cfg.temp_ma_mid
            if ma_short > ma_mid:
                score += 0.33

            # MA中 > MA长
            if len(prices) >= cfg.temp_ma_long:
                ma_long = sum(prices[-cfg.temp_ma_long:]) / cfg.temp_ma_long
                if ma_mid > ma_long:
                    score += 0.33

        return score

    def _calc_volatility_signal(self) -> float:
        """波动率信号：短期 HV / 长期 HV 比值越低越好 ∈ [0, 1]。

        映射：ratio < 0.8 → 1.0（低波好）, ratio > 1.5 → 0.0（高波差）
        """
        prices = list(self._index_prices)
        cfg = self._config

        if len(prices) < cfg.temp_vol_long:
            return 0.5

        # 计算收益率的标准差
        def _hv(p_list: list[float]) -> float:
            if len(p_list) < 2:
                return 0.0
            rets = [
                math.log(p_list[i] / p_list[i - 1])
                for i in range(1, len(p_list))
                if p_list[i] > 0 and p_list[i - 1] > 0
            ]
            if len(rets) < 2:
                return 0.0
            mean_r = sum(rets) / len(rets)
            var_r = sum((r - mean_r) ** 2 for r in rets) / (len(rets) - 1)
            return math.sqrt(var_r)

        hv_short = _hv(prices[-cfg.temp_vol_short:])
        hv_long = _hv(prices[-cfg.temp_vol_long:])

        if hv_long <= 1e-12:
            return 0.5

        ratio = hv_short / hv_long

        # 映射：低比值=好（市场平稳）
        if ratio <= 0.8:
            return 1.0
        elif ratio >= 1.5:
            return 0.0
        else:
            return 1.0 - (ratio - 0.8) / (1.5 - 0.8)

    def _calc_breadth_signal(self) -> float:
        """宽度信号：上涨家数占比的移动平均 ∈ [0, 1]。"""
        if not self._breadth_history:
            return 0.5
        return sum(self._breadth_history) / len(self._breadth_history)

    # ------------------------------------------------------------------
    # IC 加权
    # ------------------------------------------------------------------

    def _update_factor_ic(
        self,
        factor_name: str,
        factor_values: dict[str, float],
        next_returns: dict[str, float],
    ) -> None:
        """更新单因子 Rank IC（Spearman 近似）。

        使用因子值 vs 下期收益率的 rank correlation，
        追加到 rolling deque 中供 _get_ic_weights 使用。
        """
        # 取交集
        common = set(factor_values) & set(next_returns)
        common = {
            s for s in common
            if math.isfinite(factor_values[s]) and math.isfinite(next_returns[s])
        }
        if len(common) < 10:
            return  # 样本不足，跳过

        symbols = sorted(common)
        fv = [factor_values[s] for s in symbols]
        rv = [next_returns[s] for s in symbols]

        # Rank correlation（Spearman）简易计算
        def _rank(arr: list[float]) -> list[float]:
            indexed = sorted(range(len(arr)), key=lambda i: arr[i])
            ranks = [0.0] * len(arr)
            for rank_pos, idx in enumerate(indexed):
                ranks[idx] = float(rank_pos)
            return ranks

        f_ranks = _rank(fv)
        r_ranks = _rank(rv)
        n = len(symbols)
        mean_f = sum(f_ranks) / n
        mean_r = sum(r_ranks) / n
        cov = sum((f_ranks[i] - mean_f) * (r_ranks[i] - mean_r) for i in range(n))
        std_f = max(1e-12, math.sqrt(sum((x - mean_f) ** 2 for x in f_ranks)))
        std_r = max(1e-12, math.sqrt(sum((x - mean_r) ** 2 for x in r_ranks)))
        ic = cov / (std_f * std_r)

        self._factor_ic_history[factor_name].append(ic)

    def _get_ic_weights(self) -> dict[str, float]:
        """基于 rolling IC 计算因子权重。

        使用指数衰减加权 IC 的绝对值，保留方向（IC < 0 翻转因子）。
        与基础权重按 ic_weight_alpha 混合。
        """
        cfg = self._config
        base_weights = {
            "value": cfg.base_weight_value,
            "quality": cfg.base_weight_quality,
            "momentum": cfg.base_weight_momentum,
            "volatility": cfg.base_weight_volatility,
            "earnings_growth": cfg.base_weight_earnings_growth,
            "dividend": cfg.base_weight_dividend,
            "piotroski": cfg.base_weight_piotroski,
            "debt": cfg.base_weight_debt,
        }

        # 如果 IC 历史不足，直接用基础权重
        has_ic = any(len(v) >= 5 for v in self._factor_ic_history.values())
        if not has_ic or cfg.ic_weight_alpha <= 0:
            total = sum(base_weights.values())
            if total > 0:
                return {k: v / total for k, v in base_weights.items()}
            return base_weights

        # 计算指数衰减加权 IC
        halflife = max(1, cfg.ic_decay_halflife)
        ic_weighted: dict[str, float] = {}
        ic_direction: dict[str, float] = {}  # +1 / -1

        for name in base_weights:
            history = self._factor_ic_history.get(name)
            if not history or len(history) < 3:
                ic_weighted[name] = 0.0
                ic_direction[name] = 1.0
                continue

            # 指数衰减：最近的权重最高
            decay_weights = []
            for i in range(len(history)):
                age = len(history) - 1 - i
                decay_weights.append(math.exp(-math.log(2) * age / halflife))

            w_total = sum(decay_weights)
            if w_total <= 0:
                ic_weighted[name] = 0.0
                ic_direction[name] = 1.0
                continue

            weighted_ic = sum(
                history[i] * decay_weights[i] / w_total
                for i in range(len(history))
            )

            ic_direction[name] = 1.0 if weighted_ic >= 0 else -1.0
            abs_ic = abs(weighted_ic)
            # 低于噪声阈值归零
            if abs_ic < cfg.ic_min_abs:
                abs_ic = 0.0
            ic_weighted[name] = abs_ic

        # IC 权重归一化
        ic_total = sum(ic_weighted.values())
        if ic_total > 0:
            ic_norm = {k: v / ic_total for k, v in ic_weighted.items()}
        else:
            ic_norm = {k: 1.0 / len(base_weights) for k in base_weights}

        # 混合：alpha * ic_weights + (1-alpha) * base_weights
        alpha = cfg.ic_weight_alpha
        base_total = sum(base_weights.values()) or 1.0
        base_norm = {k: v / base_total for k, v in base_weights.items()}

        final = {}
        for name in base_weights:
            blended = alpha * ic_norm.get(name, 0) + (1 - alpha) * base_norm.get(name, 0)
            final[name] = blended

        # 归一化
        f_total = sum(final.values())
        if f_total > 0:
            final = {k: v / f_total for k, v in final.items()}

        # 存储方向信息供打分使用
        self._ic_direction = ic_direction
        return final

    # ------------------------------------------------------------------
    # 仓位管理
    # ------------------------------------------------------------------

    def _get_position_scale(self) -> float:
        """基于温度计返回仓位比例 ∈ [min_position_pct, 1.0]。

        线性映射：temperature 0→min_pct, 1→1.0
        """
        cfg = self._config
        if not cfg.temperature_enabled:
            return 1.0
        t = max(0.0, min(1.0, self._temperature))
        min_pct = cfg.min_position_pct
        return min_pct + (1.0 - min_pct) * t

    def _is_defensive_mode(self) -> bool:
        """是否处于防御模式。"""
        return self._temperature < self._config.defensive_threshold

    # ------------------------------------------------------------------
    # 核心打分
    # ------------------------------------------------------------------

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """自适应多因子排名。

        流程：
        1. 计算八个因子
        2. 用上一期因子值 + 本期收益率更新 IC
        3. 获取 IC 加权权重（防御模式用固定防御权重）
        4. rank_normalize 融合
        5. 根据温度计设置 position_scale
        """
        cfg = self._config
        if not candidates:
            return []

        # ------------------------------------------------------------------
        # 1. 计算所有因子
        # ------------------------------------------------------------------
        factors: dict[str, dict[str, float]] = {}

        factors["value"] = compute_value_factor(
            candidates, metric=cfg.value_metric,
        )
        factors["quality"] = compute_quality_factor(
            candidates, metric=cfg.quality_metric,
        )
        factors["momentum"] = compute_momentum_factor(
            self._price_history, candidates,
            lookback=cfg.momentum_lookback,
            skip_recent=cfg.momentum_skip_recent,
            multi_period=cfg.momentum_multi_period,
        )
        factors["volatility"] = compute_volatility_factor(
            self._price_history, candidates,
            lookback=cfg.volatility_lookback,
        )
        factors["earnings_growth"] = compute_earnings_growth_factor(candidates)
        factors["dividend"] = compute_dividend_factor(candidates)
        factors["piotroski"] = compute_piotroski_factor(candidates)
        factors["debt"] = compute_debt_factor(candidates)

        # 诊断日志：输出各因子覆盖率
        if logger.isEnabledFor(logging.DEBUG):
            for fname, fv in factors.items():
                n_valid = sum(1 for v in fv.values() if math.isfinite(v))
                coverage = n_valid / len(fv) * 100 if fv else 0
                logger.debug(
                    "因子 %s: %d/%d 有效 (%.1f%%)",
                    fname, n_valid, len(fv), coverage,
                )

        # ------------------------------------------------------------------
        # 2. 用上一期因子 + 本期收益率更新 IC
        # ------------------------------------------------------------------
        if self._prev_factor_scores:
            # 计算本期收益率
            current_returns: dict[str, float] = {}
            for c in candidates:
                hist = self._price_history.get(c.symbol)
                if hist and len(hist) >= 2:
                    prev_price = hist[-2]
                    cur_price = hist[-1]
                    if prev_price > 0:
                        current_returns[c.symbol] = (cur_price - prev_price) / prev_price

            if len(current_returns) >= 10:
                for fname, fvals in self._prev_factor_scores.items():
                    self._update_factor_ic(fname, fvals, current_returns)

        # 保存当前因子值供下期 IC 计算
        self._prev_factor_scores = {k: dict(v) for k, v in factors.items()}

        # ------------------------------------------------------------------
        # 3. 获取权重
        # ------------------------------------------------------------------
        if self._is_defensive_mode():
            # 防御模式：固定防御权重
            weights = {
                "volatility": cfg.defensive_weight_volatility,
                "value": cfg.defensive_weight_value,
                "dividend": cfg.defensive_weight_dividend,
                "quality": cfg.defensive_weight_quality,
                "momentum": 0.0,
                "earnings_growth": 0.0,
                "piotroski": 0.0,
                "debt": 0.0,
            }
            logger.info(
                "防御模式激活（温度=%.2f < %.2f），使用防御因子权重",
                self._temperature, cfg.defensive_threshold,
            )
        else:
            weights = self._get_ic_weights()

        # ------------------------------------------------------------------
        # 4. 过滤不可用因子 + rank_normalize 融合
        # ------------------------------------------------------------------
        _VALID_THRESHOLD = 0.20
        _FALLBACK_THRESHOLD = 0.05  # 降级阈值：覆盖率 >= 5%
        usable: list[tuple[str, dict[str, float]]] = []
        for name, fv in factors.items():
            w = weights.get(name, 0.0)
            if w < 0.001:
                continue
            n_valid = sum(1 for v in fv.values() if math.isfinite(v))
            if len(fv) > 0 and n_valid / len(fv) >= _VALID_THRESHOLD:
                usable.append((name, fv))

        if not usable:
            # 回退策略 1: 降低覆盖率阈值重试
            logger.warning(
                "所有因子覆盖率 < %.0f%%，尝试降级阈值 %.0f%%",
                _VALID_THRESHOLD * 100, _FALLBACK_THRESHOLD * 100,
            )
            for name, fv in factors.items():
                w = weights.get(name, 0.0)
                if w < 0.001:
                    continue
                n_valid = sum(1 for v in fv.values() if math.isfinite(v))
                if len(fv) > 0 and n_valid / len(fv) >= _FALLBACK_THRESHOLD:
                    usable.append((name, fv))

        if not usable:
            # 回退策略 2: 仅使用价格导出因子（不依赖财务数据）
            _PRICE_FACTORS = {"momentum", "volatility"}
            logger.warning(
                "降级阈值下仍无可用因子，回退到价格导出因子 %s",
                _PRICE_FACTORS,
            )
            for name, fv in factors.items():
                if name not in _PRICE_FACTORS:
                    continue
                n_valid = sum(1 for v in fv.values() if math.isfinite(v))
                if n_valid >= 3:  # 至少 3 个有效值
                    usable.append((name, fv))
                    # 使用等权
                    weights[name] = 1.0 / len(_PRICE_FACTORS)

        if not usable:
            logger.warning("所有因子（含价格导出）均不可用，跳过本期选股")
            return []

        # 重新归一化可用因子的权重
        usable_names = {n for n, _ in usable}
        raw_w = {n: weights.get(n, 0.0) for n in usable_names}
        w_total = sum(raw_w.values())
        if w_total <= 0:
            return []
        norm_w = {k: v / w_total for k, v in raw_w.items()}

        # IC 方向翻转：如果因子 IC 为负，翻转分数
        ic_dir = getattr(self, "_ic_direction", {})

        scores: dict[str, float] = {}
        for name, fv in usable:
            direction = ic_dir.get(name, 1.0)
            if direction < 0:
                # IC 为负，翻转因子值
                fv = {s: -v if math.isfinite(v) else v for s, v in fv.items()}
            self.rank_normalize(
                candidates, fv, norm_w[name], scores,
                nan_handling="skip",
            )

        # ------------------------------------------------------------------
        # 5. 仓位控制（通过 context.metadata 传递给基类调仓逻辑）
        # ------------------------------------------------------------------
        scale = self._get_position_scale()
        context.metadata["position_scale"] = scale

        # 动态调整 buffer_k
        if self._is_defensive_mode():
            self._config.buffer_k = cfg.buffer_k_defensive
        else:
            self._config.buffer_k = 8  # 常规值

        return self.rank_sort(scores)
