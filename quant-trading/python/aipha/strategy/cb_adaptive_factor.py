"""CB自适应因子权重策略

核心创新：因子权重不再静态配置，而是基于滚动IC动态调整。

机制：
1. 维护每个因子的近N日因子值和前向收益率历史
2. 每 ic_update_interval 个调仓周期重算各因子近期IC
3. 用 abs(IC) 归一化为动态权重，与基线权重按 blend_ratio 混合
4. IC反转（变号）或失效（< floor）的因子自动降权

对比静态策略：
- cb_comprehensive: 12因子, 静态权重, full-period优化
- cb_adaptive_factor: 22因子(17 CB+5正股), IC驱动权重, 自适应新规后环境

经济逻辑：
- 2022-08 CB新规后市场微结构剧变, 小盘/次新因子失效
- 静态权重无法捕捉因子有效性的时变特性
- IC驱动权重本质上是对因子「近期预测力」的在线估计
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

import numpy as np
from scipy import stats

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR
from aipha.strategy.base import StrategyContext
from aipha.factors.cb_factors import (
    compute_credit_spread,
    compute_gamma_convexity,
    compute_iv_bias,
    compute_liquidity_activity,
    compute_maturity_convexity,
)
from aipha.strategy.cb_enhanced_factors import (
    delta_exposure,
    enhanced_ytm,
    turnover_activity,
)
from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
    days_since_listed,
)
from aipha.strategy.cb_ultra import _rank_scores
from aipha.strategy.double_low_plus import _get_rating_score
from aipha.strategy.registry import StrategyRegistry
from aipha.factors.rsrs_timing import (
    RSRSConfig,
    RSRSTimingFactor,
    MarketSignal,
    rsrs_to_position_scale,
)
from aipha.risk.ml_risk_controls import VolTargetScaler

if TYPE_CHECKING:
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 因子定义
# ---------------------------------------------------------------------------

# 因子名 → (提取函数签名, ascending排序方向)
# ascending=True: 值越小越好(如价格); False: 值越大越好(如YTM)
FACTOR_DEFS: dict[str, bool] = {
    "price": True,          # 低价优先
    "premium": True,        # 低溢价优先
    "size": True,           # 小规模优先
    "age": True,            # 次新优先
    "mean_reversion": False,  # BB偏离越大越好(超卖)
    "rating": False,        # 高评级优先
    "delta": False,         # 高delta优先
    "ytm": False,           # 高YTM优先
    "turnover": False,      # 高流动性优先
    "momentum": False,      # 正动量优先
    "premium_pct": False,   # 低百分位优先(反转因子)
    "putback": False,       # 高回售接近度优先
    # --- Phase 1 新因子 (from cb_factors.py) ---
    "iv_bias": True,        # 低IV=便宜期权(升序)
    "gamma_convexity": False,  # 高凸性=好(降序)
    "credit_spread": False,    # 高信用利差=高补偿(降序)
    "liquidity_activity": False, # 高活跃度=好(降序)
    "maturity_convexity": False,  # 接近甜蜜区间=好(降序)
    # --- Phase 2 正股质量因子 (via CBStockBridge) ---
    "stock_roe": False,        # 高ROE优先(正股质量核心)
    "stock_ep": False,         # 高EP优先(正股低估=上行空间)
    "stock_vol": False,        # 高正股波动=高期权价值(BSM)
    "stock_growth": False,     # 高营收增长优先(成长性)
    "stock_safety": True,      # 低资产负债率优先(安全性)
}


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class CBAdaptiveFactorConfig(CBRotationConfig):
    """自适应因子权重策略配置"""

    top_k: int = 15
    rebalance_days: int = 3
    max_price: float = 145.0
    max_issue_size: float = 30000.0
    min_issue_size: float = 1000.0
    min_turnover: float = 15.0
    min_days_to_maturity: int = 30
    exclude_st: bool = True
    stop_loss_pct: float = 0.20
    buffer_k: int = 3
    lot_size: int = 10

    # 次新过滤
    use_age_filter: bool = True
    min_age_days: int = 3
    max_age_days: int = 180
    fallback_max_age_days: int = 730
    max_premium: float = 70.0

    # 均值回归/动量参数
    lookback: int = 20
    bb_k: float = 2.0
    momentum_lookback: int = 10

    # 回售接近度参数
    putback_trigger_ratio: float = 0.85
    premium_pct_lookback: int = 40

    # --- 基线因子权重（作为IC驱动权重的锚点，22因子总和=1.0） ---
    # CB本体因子 (12因子, 合计 ~0.55)
    base_weight_price: float = 0.06
    base_weight_premium: float = 0.06
    base_weight_size: float = 0.04
    base_weight_age: float = 0.03
    base_weight_mean_reversion: float = 0.07
    base_weight_rating: float = 0.05
    base_weight_delta: float = 0.05
    base_weight_ytm: float = 0.05
    base_weight_turnover: float = 0.03
    base_weight_momentum: float = 0.04
    base_weight_premium_pct: float = 0.04
    base_weight_putback: float = 0.03

    # Phase 1 CB衍生因子 (5因子, 合计 ~0.20)
    base_weight_iv_bias: float = 0.04
    base_weight_gamma_convexity: float = 0.04
    base_weight_credit_spread: float = 0.04
    base_weight_liquidity_activity: float = 0.04
    base_weight_maturity_convexity: float = 0.04

    # Phase 2 正股质量因子 (5因子, 合计 ~0.25)
    base_weight_stock_roe: float = 0.07
    base_weight_stock_ep: float = 0.05
    base_weight_stock_vol: float = 0.04
    base_weight_stock_growth: float = 0.05
    base_weight_stock_safety: float = 0.04

    # 新因子参数
    risk_free_rate: float = 0.02  # §13.1 全局统一 RISK_FREE_RATE_DEFAULT
    maturity_sweet_spot: float = 3.0
    maturity_half_range: float = 3.0

    # --- 正股数据参数 ---
    use_stock_bridge: bool = True
    vol_lookback: int = 60
    default_roe: float = 0.0
    default_ep: float = 0.0
    default_growth: float = 0.0
    default_safety: float = 50.0  # 中性资产负债率(%)

    # --- IC自适应参数 ---
    ic_lookback: int = 60     # IC回看窗口(交易日)
    ic_update_interval: int = 5  # 每N个调仓周期重算IC权重
    ic_floor: float = 0.02   # IC低于此值的因子权重被抑制
    blend_ratio: float = 0.6  # IC权重占比(1=纯IC, 0=纯基线)
    min_ic_samples: int = 20   # 最少IC样本数(不足时用基线)
    ic_method: str = "spearman"  # IC计算方法

    # --- IC方向感知 & 衰减休眠 ---
    enable_ic_direction_flip: bool = True   # 允许IC持续为负时翻转排序方向
    ic_flip_threshold: float = 0.65  # 近期IC中负值占比>此值时翻转方向
    enable_ic_decay_dormancy: bool = True   # IC趋势强负时因子进入休眠
    ic_decay_slope_threshold: float = -0.003  # IC线性趋势斜率低于此值→休眠

    # --- RSRS 择时 overlay ---
    use_rsrs_timing: bool = False  # 启用 RSRS 择时仓位缩放
    rsrs_regression_window: int = 18  # β 回归窗口
    rsrs_zscore_window: int = 600     # Z-Score 标准化窗口
    rsrs_bull_threshold: float = 0.7
    rsrs_bear_threshold: float = -0.7
    rsrs_bull_scale: float = 1.0      # BULL 时满仓
    rsrs_bear_scale: float = 0.3      # BEAR 时 30% 仓位
    rsrs_neutral_scale: float = 0.7   # NEUTRAL 时 70% 仓位

    # --- 波动率目标 overlay ---
    use_vol_targeting: bool = False   # 启用波动率目标仓位缩放
    vol_target: float = 0.15          # 年化目标波动率
    vol_target_lookback: int = 20     # 已实现波动率回看窗口
    vol_scale_floor: float = 0.3      # 最小缩放因子
    vol_scale_cap: float = 1.5        # 最大缩放因子

    min_candidates: int = 5

    def get_base_weights(self) -> dict[str, float]:
        """返回基线因子权重字典（22因子）"""
        return {
            "price": self.base_weight_price,
            "premium": self.base_weight_premium,
            "size": self.base_weight_size,
            "age": self.base_weight_age,
            "mean_reversion": self.base_weight_mean_reversion,
            "rating": self.base_weight_rating,
            "delta": self.base_weight_delta,
            "ytm": self.base_weight_ytm,
            "turnover": self.base_weight_turnover,
            "momentum": self.base_weight_momentum,
            "premium_pct": self.base_weight_premium_pct,
            "putback": self.base_weight_putback,
            "iv_bias": self.base_weight_iv_bias,
            "gamma_convexity": self.base_weight_gamma_convexity,
            "credit_spread": self.base_weight_credit_spread,
            "liquidity_activity": self.base_weight_liquidity_activity,
            "maturity_convexity": self.base_weight_maturity_convexity,
            "stock_roe": self.base_weight_stock_roe,
            "stock_ep": self.base_weight_stock_ep,
            "stock_vol": self.base_weight_stock_vol,
            "stock_growth": self.base_weight_stock_growth,
            "stock_safety": self.base_weight_stock_safety,
        }


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------

@StrategyRegistry().register("cb_adaptive_factor")
class CBAdaptiveFactorStrategy(CBRotationStrategy):
    """IC驱动的自适应因子权重CB轮动策略

    融合 CB 本体因子(17个) + 正股质量因子(5个) = 22因子。
    正股数据通过 CBStockBridge 获取 (PIT 合规)。
    若 bridge 不可用，降级为纯 CB 因子模式(用默认值填充)。
    """

    def __init__(
        self,
        config: CBAdaptiveFactorConfig | None = None,
        stock_bridge: Any | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self._config = config or CBAdaptiveFactorConfig(**kwargs)
        self._bridge = stock_bridge
        # 因子值历史: {factor_name: deque of {symbol: value}}
        self._factor_history: dict[str, deque[dict[str, float]]] = {
            fn: deque(maxlen=self._config.ic_lookback + 10)
            for fn in FACTOR_DEFS
        }
        # 前向收益历史: deque of {symbol: return}
        self._return_history: deque[dict[str, float]] = deque(
            maxlen=self._config.ic_lookback + 10,
        )
        # 价格历史(布林带/动量)
        self._price_history: dict[str, deque[float]] = {}
        # 溢价率历史(百分位)
        self._premium_history: dict[str, deque[float]] = {}
        # 当前动态权重
        self._dynamic_weights: dict[str, float] = self._config.get_base_weights()
        self._ic_update_counter: int = 0
        # 上一次的持仓价格(计算收益率)
        self._prev_prices: dict[str, float] = {}
        # IC方向感知: True=ascending已翻转(与FACTOR_DEFS相反)
        self._factor_directions: dict[str, bool] = dict(FACTOR_DEFS)
        # --- Phase 2 正股数据状态 ---
        self._stk_price_history: dict[str, deque[float]] = {}
        self._current_fundamentals: dict[str, Any] = {}
        # --- RSRS 择时 overlay ---
        self._rsrs: RSRSTimingFactor | None = None
        if self._config.use_rsrs_timing:
            self._rsrs = RSRSTimingFactor(RSRSConfig(
                regression_window=self._config.rsrs_regression_window,
                zscore_window=self._config.rsrs_zscore_window,
                bull_threshold=self._config.rsrs_bull_threshold,
                bear_threshold=self._config.rsrs_bear_threshold,
            ))
        # --- 波动率目标 overlay ---
        self._vol_targeter: VolTargetScaler | None = None
        if self._config.use_vol_targeting:
            self._vol_targeter = VolTargetScaler(
                vol_target=self._config.vol_target,
                lookback=self._config.vol_target_lookback,
                min_scale=self._config.vol_scale_floor,
                max_scale=self._config.vol_scale_cap,
            )
        self._prev_nav: float = 0.0  # 上一bar组合净值(计算日收益)

    @property
    def config(self) -> CBAdaptiveFactorConfig:
        return self._config

    # ----- 状态钩子 -----

    def _on_start_extra(self) -> None:
        """清零所有有状态数据（WF 多折复用时必须清空）。"""
        self._factor_history = {
            fn: deque(maxlen=self._config.ic_lookback + 10)
            for fn in FACTOR_DEFS
        }
        self._return_history = deque(
            maxlen=self._config.ic_lookback + 10,
        )
        self._price_history.clear()
        self._premium_history.clear()
        self._prev_prices.clear()
        self._dynamic_weights = self._config.get_base_weights()
        self._ic_update_counter = 0
        self._effective_max_age = self._config.max_age_days
        self._factor_directions = dict(FACTOR_DEFS)
        # --- Phase 2 正股状态清空 + bridge 延迟初始化 ---
        self._stk_price_history.clear()
        self._current_fundamentals.clear()
        if self._bridge is None and self._config.use_stock_bridge:
            try:
                from aipha.data.cb_stock_bridge import CBStockBridge
                self._bridge = CBStockBridge()
                logger.info("CBStockBridge 延迟初始化成功")
            except Exception as e:
                logger.warning("CBStockBridge 初始化失败: %s，降级为纯CB因子模式", e)
        # --- RSRS + vol targeting 重置 ---
        if self._rsrs is not None:
            self._rsrs.reset()
        if self._vol_targeter is not None:
            self._vol_targeter.reset()
        self._prev_nav = 0.0

    def _on_bar_update_state(
        self, bars: dict[str, "Bar"], context: StrategyContext,
    ) -> None:
        """每根bar更新价格/溢价率历史 + 计算前向收益"""
        max_len = max(self._config.lookback, self._config.momentum_lookback) + 5
        pct_len = self._config.premium_pct_lookback + 5
        for symbol, bar in bars.items():
            close = bar.close
            if close <= 0:
                continue

            # 更新价格历史(布林带/动量)
            if symbol not in self._price_history:
                self._price_history[symbol] = deque(maxlen=max_len)
            self._price_history[symbol].append(close)

            # 更新溢价率历史
            meta = bar.metadata or {}
            prem = meta.get("cb_over_rate")
            if prem is not None and math.isfinite(prem):
                if symbol not in self._premium_history:
                    self._premium_history[symbol] = deque(maxlen=pct_len)
                self._premium_history[symbol].append(float(prem))

            # 计算前向收益(用于IC):
            # 当前bar的收益 = (close - prev_close) / prev_close
            if symbol in self._prev_prices and self._prev_prices[symbol] > 0:
                ret = (close - self._prev_prices[symbol]) / self._prev_prices[symbol]
                # 追加到最近一期的return_history
                if self._return_history:
                    self._return_history[-1][symbol] = ret
            self._prev_prices[symbol] = close

        # --- Phase 2: 正股数据更新 ---
        self._current_fundamentals.clear()
        vol_len = self._config.vol_lookback + 5
        for symbol, bar in bars.items():
            meta = bar.metadata or {}
            stk_code = meta.get("stk_code")
            if not stk_code:
                continue
            # 正股价格代理 (cb_value) → 用于波动率计算
            cb_value = meta.get("cb_value")
            if cb_value and cb_value > 0:
                if stk_code not in self._stk_price_history:
                    self._stk_price_history[stk_code] = deque(maxlen=vol_len)
                self._stk_price_history[stk_code].append(float(cb_value))
            # 查询正股基本面 (PIT)
            if self._bridge is not None and stk_code not in self._current_fundamentals:
                try:
                    qd = (context.current_time.date()
                          if hasattr(context.current_time, "date")
                          else context.current_time)
                    self._current_fundamentals[stk_code] = (
                        self._bridge.get_fundamentals(stk_code, qd)
                    )
                except Exception:
                    pass

        # --- RSRS 择时: 聚合全市场 High/Low → 更新 RSRS 因子 ---
        if self._rsrs is not None and bars:
            highs = [b.high for b in bars.values() if b.high > 0]
            lows = [b.low for b in bars.values() if b.low > 0]
            if highs and lows:
                self._rsrs.update(
                    float(np.mean(highs)), float(np.mean(lows)),
                )

        # --- Vol Targeting: 计算组合日收益率 → 更新波动率目标 ---
        if self._vol_targeter is not None:
            nav = context.cash
            for sym, qty in context.positions.items():
                if qty > 0:
                    bar = bars.get(sym)
                    if bar and bar.close > 0:
                        nav += qty * bar.close
            if self._prev_nav > 0 and nav > 0:
                daily_ret = (nav - self._prev_nav) / self._prev_nav
                self._vol_targeter.update(daily_ret)
            self._prev_nav = nav

    def _extra_filter(
        self, candidate: CandidateInfo, context: StrategyContext,
    ) -> bool:
        """次新 + 溢价率过滤。返回 True 保留。"""
        cfg = self._config
        # 溢价率过滤
        if candidate.cb_over_rate is not None and candidate.cb_over_rate > cfg.max_premium:
            return False
        # 次新过滤
        if cfg.use_age_filter and candidate.list_date:
            age = days_since_listed(candidate.list_date, context.current_time)
            if age < cfg.min_age_days:
                return False
            eff_max = (self._effective_max_age
                       if self._effective_max_age > 0
                       else cfg.max_age_days)
            if age > eff_max:
                return False
        return True

    # ----- 因子提取 -----

    def _extract_factors(
        self,
        candidates: list[CandidateInfo],
        context: StrategyContext,
    ) -> dict[str, list[float]]:
        """从候选池提取全部因子原始值"""
        cfg = self._config
        factors: dict[str, list[float]] = {}

        factors["price"] = [c.close for c in candidates]
        factors["premium"] = [
            c.cb_over_rate if c.cb_over_rate is not None else 50.0
            for c in candidates
        ]
        factors["size"] = [c.issue_size for c in candidates]
        factors["age"] = [
            float(days_since_listed(c.list_date, context.current_time))
            if c.list_date else 365.0
            for c in candidates
        ]
        factors["mean_reversion"] = [
            self._compute_bb_deviation(c.symbol, c.close) for c in candidates
        ]
        factors["rating"] = [float(_get_rating_score(c.rating))
                             for c in candidates]
        factors["delta"] = [delta_exposure(c) for c in candidates]
        factors["ytm"] = [
            enhanced_ytm(c.close, c.maturity_date, context.current_time)
            for c in candidates
        ]
        factors["turnover"] = [turnover_activity(c) for c in candidates]
        factors["momentum"] = [
            self._compute_momentum(c.symbol) for c in candidates
        ]
        factors["premium_pct"] = [
            self._compute_premium_pct(c.symbol, c.cb_over_rate)
            for c in candidates
        ]
        factors["putback"] = [
            self._compute_putback(c.cb_value) for c in candidates
        ]
        # --- Phase 1 新因子 (from cb_factors.py) ---
        now = context.current_time
        factors["iv_bias"] = [
            compute_iv_bias(c.close, c.cb_value, c.maturity_date, now)
            for c in candidates
        ]
        factors["gamma_convexity"] = [
            compute_gamma_convexity(c.cb_value, c.maturity_date, now)
            for c in candidates
        ]
        factors["credit_spread"] = [
            compute_credit_spread(
                c.close, c.maturity_date, now,
                self._config.risk_free_rate,
            )
            for c in candidates
        ]
        factors["liquidity_activity"] = [
            compute_liquidity_activity(c.amount, c.issue_size)
            for c in candidates
        ]
        factors["maturity_convexity"] = [
            compute_maturity_convexity(
                c.maturity_date, now,
                self._config.maturity_sweet_spot,
                self._config.maturity_half_range,
            )
            for c in candidates
        ]
        # --- Phase 2 正股质量因子 (via CBStockBridge) ---
        factors["stock_roe"] = [self._get_stock_roe(c) for c in candidates]
        factors["stock_ep"] = [self._get_stock_ep(c) for c in candidates]
        factors["stock_vol"] = [self._get_stock_vol(c) for c in candidates]
        factors["stock_growth"] = [
            self._get_stock_growth(c) for c in candidates
        ]
        factors["stock_safety"] = [
            self._get_stock_safety(c) for c in candidates
        ]
        return factors

    # ----- 辅助计算 -----

    def _compute_bb_deviation(self, symbol: str, close: float) -> float:
        """布林带偏离因子"""
        hist = self._price_history.get(symbol)
        if not hist or len(hist) < self._config.lookback:
            return 0.0
        arr = np.array(list(hist)[-self._config.lookback:])
        ma = float(np.mean(arr))
        std = float(np.std(arr, ddof=1))
        if std < 1e-10:
            return 0.0
        lower = ma - self._config.bb_k * std
        return (lower - close) / std  # 正=超卖(低于下轨)

    def _compute_momentum(self, symbol: str) -> float:
        """N日动量因子"""
        hist = self._price_history.get(symbol)
        n = self._config.momentum_lookback
        if not hist or len(hist) < n + 1:
            return 0.0
        return (hist[-1] - hist[-n - 1]) / hist[-n - 1] if hist[-n - 1] > 0 else 0.0

    def _compute_premium_pct(
        self, symbol: str, cur_premium: float | None,
    ) -> float:
        """溢价率历史百分位(越低=越便宜)"""
        hist = self._premium_history.get(symbol)
        if not hist or len(hist) < 10 or cur_premium is None:
            return 0.5
        arr = np.array(list(hist))
        return float(np.mean(arr <= cur_premium))

    def _compute_putback(self, cb_value: float | None) -> float:
        """回售接近度因子"""
        if cb_value is None or cb_value <= 0:
            return 0.0
        parity = cb_value / 100.0
        return 1.0 - parity / self._config.putback_trigger_ratio

    # ----- Phase 2 正股因子提取 -----

    def _get_stock_roe(self, c: CandidateInfo) -> float:
        """正股ROE (PIT合规)。缺失→默认值。"""
        stk_code = (c.metadata or {}).get("stk_code", "")
        fund = self._current_fundamentals.get(stk_code)
        if fund and getattr(fund, "roe", None) is not None:
            return float(fund.roe)
        return self._config.default_roe

    def _get_stock_ep(self, c: CandidateInfo) -> float:
        """正股EP = 1/PE_TTM。缺失→默认值。"""
        stk_code = (c.metadata or {}).get("stk_code", "")
        fund = self._current_fundamentals.get(stk_code)
        if fund and getattr(fund, "ep", None) is not None:
            return float(fund.ep)
        return self._config.default_ep

    def _get_stock_vol(self, c: CandidateInfo) -> float:
        """正股波动率 (cb_value代理, 年化)。数据不足→0.0。"""
        stk_code = (c.metadata or {}).get("stk_code", "")
        hist = self._stk_price_history.get(stk_code)
        if not hist or len(hist) < 10:
            return 0.0
        prices = list(hist)
        returns = [
            (prices[i] / prices[i - 1] - 1.0)
            for i in range(1, len(prices))
            if prices[i - 1] > 0
        ]
        if len(returns) < 5:
            return 0.0
        std = float(np.std(returns, ddof=1))
        return std * (TRADING_DAYS_PER_YEAR ** 0.5)  # 年化

    def _get_stock_growth(self, c: CandidateInfo) -> float:
        """正股营收同比增长率(%)。缺失→默认值。"""
        stk_code = (c.metadata or {}).get("stk_code", "")
        fund = self._current_fundamentals.get(stk_code)
        # StockFundamentals 可能没有 revenue_yoy 字段 → 用 getattr 安全取
        val = getattr(fund, "revenue_yoy", None) if fund else None
        return float(val) if val is not None else self._config.default_growth

    def _get_stock_safety(self, c: CandidateInfo) -> float:
        """正股资产负债率(%)。低好(ascending)。缺失→中性默认。"""
        stk_code = (c.metadata or {}).get("stk_code", "")
        fund = self._current_fundamentals.get(stk_code)
        val = getattr(fund, "debt_to_assets", None) if fund else None
        return float(val) if val is not None else self._config.default_safety

    # ----- IC 动态权重更新 -----

    def _update_factor_history(
        self,
        candidates: list[CandidateInfo],
        factors: dict[str, list[float]],
    ) -> None:
        """将当期因子值写入历史（供后续IC计算）"""
        # 新增一条空return记录（下一个bar fill）
        self._return_history.append({})
        for fn in FACTOR_DEFS:
            snapshot: dict[str, float] = {}
            vals = factors.get(fn, [])
            for i, c in enumerate(candidates):
                if i < len(vals):
                    snapshot[c.symbol] = vals[i]
            self._factor_history[fn].append(snapshot)

    def _recompute_ic_weights(self) -> None:
        """基于近期IC重算因子权重（含方向感知 + 衰减休眠）"""
        cfg = self._config
        n_samples = len(self._return_history)
        if n_samples < cfg.min_ic_samples:
            logger.debug("IC samples %d < %d, using base weights",
                         n_samples, cfg.min_ic_samples)
            self._dynamic_weights = cfg.get_base_weights()
            return

        base_w = cfg.get_base_weights()
        ic_values: dict[str, float] = {}
        ic_series: dict[str, list[float]] = {}  # 用于衰减检测

        for fn in FACTOR_DEFS:
            # 计算近期IC序列
            ics: list[float] = []
            f_hist = list(self._factor_history[fn])
            r_hist = list(self._return_history)
            # 对齐：factor[t] 对应 return[t+1]
            for t in range(min(len(f_hist), len(r_hist)) - 1):
                f_snap = f_hist[t]
                r_snap = r_hist[t + 1]
                common = set(f_snap.keys()) & set(r_snap.keys())
                if len(common) < 5:
                    continue
                fv = [f_snap[s] for s in common]
                rv = [r_snap[s] for s in common]
                if cfg.ic_method == "spearman":
                    corr, _ = stats.spearmanr(fv, rv)
                else:
                    corr, _ = stats.pearsonr(fv, rv)
                if not np.isnan(corr):
                    ics.append(float(corr))
            ic_values[fn] = float(np.mean(ics)) if ics else 0.0
            ic_series[fn] = ics

        # --- IC方向感知: IC持续负→翻转排序方向 ---
        if cfg.enable_ic_direction_flip:
            for fn, ics in ic_series.items():
                if len(ics) < max(5, cfg.min_ic_samples // 2):
                    continue
                neg_ratio = sum(1 for ic in ics if ic < 0) / len(ics)
                orig_asc = FACTOR_DEFS[fn]
                if neg_ratio > cfg.ic_flip_threshold:
                    # IC持续负 → 翻转方向
                    self._factor_directions[fn] = not orig_asc
                    logger.info("IC direction FLIP: %s neg_ratio=%.2f → %s",
                                fn, neg_ratio,
                                "ascending" if not orig_asc else "descending")
                else:
                    # 恢复原始方向
                    self._factor_directions[fn] = orig_asc

        # --- IC衰减休眠: IC趋势强负→权重=0 ---
        dormant_factors: set[str] = set()
        if cfg.enable_ic_decay_dormancy:
            for fn, ics in ic_series.items():
                if len(ics) < max(8, cfg.min_ic_samples // 2):
                    continue
                # 线性回归斜率
                x = np.arange(len(ics), dtype=float)
                y = np.array(ics)
                slope, _, _, _, _ = stats.linregress(x, y)
                if (slope < cfg.ic_decay_slope_threshold
                        and abs(ic_values[fn]) < cfg.ic_floor * 2):
                    dormant_factors.add(fn)
                    logger.info("IC dormancy: %s slope=%.4f meanIC=%.4f → dormant",
                                fn, slope, ic_values[fn])

        # abs(IC) 归一化, 抑制低IC因子, 休眠因子权重=0
        abs_ics: dict[str, float] = {}
        for fn, ic in ic_values.items():
            if fn in dormant_factors:
                abs_ics[fn] = 0.0
            else:
                abs_ics[fn] = max(0.0, abs(ic) - cfg.ic_floor)
        total_abs = sum(abs_ics.values())
        if total_abs > 1e-10:
            ic_w = {fn: v / total_abs for fn, v in abs_ics.items()}
        else:
            ic_w = {fn: 1.0 / len(FACTOR_DEFS) for fn in FACTOR_DEFS}

        # 混合: blend * ic_weight + (1 - blend) * base_weight
        blended: dict[str, float] = {}
        for fn in FACTOR_DEFS:
            w = (cfg.blend_ratio * ic_w.get(fn, 0.0)
                 + (1.0 - cfg.blend_ratio) * base_w.get(fn, 0.0))
            blended[fn] = w

        total = sum(blended.values())
        if total > 1e-10:
            blended = {fn: v / total for fn, v in blended.items()}

        self._dynamic_weights = blended
        logger.info("IC weights updated: %s",
                    {fn: f"{w:.3f}" for fn, w in blended.items()
                     if w > 0.01})

    # ----- 核心打分 -----

    def _score_candidates(
        self,
        candidates: list[CandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """IC驱动的自适应因子加权评分

        包含零方差因子自动抑制：如果某因子在当前候选池中
        所有值相同（无区分力），其权重重分配给其他因子。
        """
        cfg = self._config
        if not candidates:
            return []
        n = len(candidates)
        if n == 1:
            return [candidates[0].symbol]

        # 1. 提取各因子原始值
        factors = self._extract_factors(candidates, context)

        # 2. 更新因子历史(供IC计算)
        self._update_factor_history(candidates, factors)

        # 3. 定期更新IC权重
        self._ic_update_counter += 1
        if self._ic_update_counter >= cfg.ic_update_interval:
            self._ic_update_counter = 0
            self._recompute_ic_weights()

        # 4. 排名归一化（使用IC方向感知的方向）
        ranked: dict[str, list[float]] = {}
        for fn in FACTOR_DEFS:
            ascending = self._factor_directions.get(fn, FACTOR_DEFS[fn])
            vals = factors.get(fn, [0.0] * n)
            ranked[fn] = _rank_scores(vals, ascending=ascending)

        # 5. 零方差因子检测 + 权重重分配
        w = dict(self._dynamic_weights)
        zero_var_weight = 0.0
        active_factors: list[str] = []
        for fn in FACTOR_DEFS:
            vals = factors.get(fn, [])
            if vals and len(vals) > 1:
                mn, mx = min(vals), max(vals)
                if abs(mx - mn) < 1e-10:
                    # 所有值相同 → 无区分力
                    zero_var_weight += w.get(fn, 0.0)
                    w[fn] = 0.0
                    continue
            active_factors.append(fn)

        # 将零方差因子权重按比例重分配给活跃因子
        if zero_var_weight > 1e-10 and active_factors:
            active_total = sum(w.get(fn, 0.0) for fn in active_factors)
            if active_total > 1e-10:
                for fn in active_factors:
                    w[fn] += w[fn] / active_total * zero_var_weight

        # 6. 动态加权评分
        scored: list[tuple[str, float]] = []
        for i, c in enumerate(candidates):
            score = sum(
                w.get(fn, 0.0) * ranked[fn][i] for fn in FACTOR_DEFS
            )
            scored.append((c.symbol, score))

        scored.sort(key=lambda x: x[1])
        return [s for s, _ in scored[:cfg.top_k]]

    # ----- overlay 仓位缩放 -----

    def _get_overlay_scale(self) -> float:
        """计算 RSRS 择时 × 波动率目标的综合仓位缩放因子。

        返回 [0, 1.5] 范围的乘数，用于缩放可用资金。
        """
        scale = 1.0
        # RSRS timing overlay
        if self._rsrs is not None:
            signal = self._rsrs.get_signal()
            rsrs_scale = rsrs_to_position_scale(
                signal,
                bull_scale=self._config.rsrs_bull_scale,
                bear_scale=self._config.rsrs_bear_scale,
                neutral_scale=self._config.rsrs_neutral_scale,
            )
            scale *= rsrs_scale
            if signal != MarketSignal.BULL:
                logger.info("RSRS overlay: signal=%s scale=%.2f",
                            signal.value, rsrs_scale)
        # Vol targeting overlay
        if self._vol_targeter is not None:
            vol_scale = self._vol_targeter.get_current_scale()
            scale *= vol_scale
            if abs(vol_scale - 1.0) > 0.05:
                logger.info(
                    "Vol overlay: target=%.2f%% scale=%.2f",
                    self._config.vol_target * 100,
                    vol_scale,
                )
        return max(0.0, scale)

    def _generate_rebalance(
        self,
        ranked_targets: list[str],
        context: "StrategyContext",
        bars: dict[str, "Bar"],
    ) -> list:
        """重写父类以叠加 RSRS + vol targeting overlay。

        仅缩放买入侧资金，卖出逻辑不变。
        """
        from aipha.domain.models import Side
        from aipha.strategy.base import OrderIntent

        cfg = self.config
        intents: list[OrderIntent] = []
        current_held = {s for s, q in context.positions.items() if q > 0}

        if cfg.buffer_k > 0 and current_held:
            buffer_limit = cfg.top_k + cfg.buffer_k
            extended_set = set(ranked_targets[:buffer_limit])
            keep = current_held & extended_set
            to_sell = current_held - extended_set
            n_need = max(0, cfg.top_k - len(keep))
            to_buy_candidates = [
                s for s in ranked_targets[:cfg.top_k]
                if s not in current_held
            ]
            to_buy = to_buy_candidates[:n_need]
            target_set = keep | set(to_buy)
        else:
            target_set = set(ranked_targets)
            to_sell = {s for s in current_held if s not in target_set}
            to_buy = [s for s in ranked_targets if s not in current_held]

        # 卖出
        sell_value = 0.0
        for symbol in to_sell:
            qty = context.positions.get(symbol, 0)
            if qty > 0:
                bar = bars.get(symbol)
                if bar:
                    sell_value += qty * bar.close
                intents.append(OrderIntent(
                    symbol=symbol, side=Side.SELL, qty=qty,
                    reason=f"{self.name}轮动卖出",
                ))

        if not to_buy:
            return intents

        available = context.cash + sell_value * 0.995

        # --- 集中度硬门禁 ---
        total_target_count = len(target_set)
        min_to_trade = getattr(cfg, "min_candidates_to_trade", 0)
        if min_to_trade > 0 and total_target_count < min_to_trade:
            return intents

        # --- 集中度保护 ---
        min_pos = getattr(cfg, "min_positions", 0)
        if min_pos > 0 and total_target_count < min_pos:
            scale = total_target_count / min_pos
            available *= scale

        # ★★★ RSRS + Vol Targeting Overlay ★★★
        overlay_scale = self._get_overlay_scale()
        if overlay_scale < 0.99:
            available *= overlay_scale
            logger.info(
                "[%s] Overlay scale=%.2f → available=%.0f",
                self.name, overlay_scale, available,
            )

        per_stock = available / len(to_buy)

        for symbol in to_buy:
            bar = bars.get(symbol)
            if bar is None or bar.close <= 0:
                continue
            qty = int(per_stock / bar.close / cfg.lot_size) * cfg.lot_size
            if qty >= cfg.lot_size:
                intents.append(OrderIntent(
                    symbol=symbol, side=Side.BUY, qty=qty,
                    reason=f"{self.name}轮动买入",
                ))

        return intents
