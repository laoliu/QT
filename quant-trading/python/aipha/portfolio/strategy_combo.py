"""策略组合模块 — 将多个策略的收益曲线合成为组合级别的净值曲线。

支持:
- 等权组合 (equal weight)
- 风险平价组合 (risk parity, 1/σ 加权)
- 滑窗风险平价 (rolling risk parity)
- 动量倾斜 (momentum tilt)
- Regime 自适应配权 (regime_adaptive)

典型用法:
    from aipha.portfolio.strategy_combo import StrategyCombo, load_strategy_navs
    navs = load_strategy_navs(["reports/strat_a/daily_snapshots.csv", ...])
    combo = StrategyCombo(navs, method="risk_parity", lookback=60)
    result = combo.compute()
    result.summary()
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

from aipha.utils.metrics import compute_sharpe_ratio
from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class ComboResult:
    """策略组合计算结果。"""

    method: str
    strategy_names: list[str]
    weights_history: list[dict[str, float]]
    combo_nav: np.ndarray
    strategy_navs: dict[str, np.ndarray]
    dates: list[str]
    # 汇总指标
    total_return: float = 0.0
    annualized_return: float = 0.0
    volatility: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    calmar_ratio: float = 0.0

    def summary(self) -> str:
        """打印可读的组合汇总。"""
        lines = [
            f"=== Strategy Combo ({self.method}) ===",
            f"  Strategies: {', '.join(self.strategy_names)}",
            f"  Period: {self.dates[0]} ~ {self.dates[-1]} ({len(self.dates)} days)",
            f"  Total Return:     {self.total_return:.2%}",
            f"  Annualized Return:{self.annualized_return:.2%}",
            f"  Volatility:       {self.volatility:.2%}",
            f"  Sharpe Ratio:     {self.sharpe_ratio:.3f}",
            f"  Max Drawdown:     {self.max_drawdown:.2%}",
            f"  Calmar Ratio:     {self.calmar_ratio:.3f}",
        ]
        # 最终权重
        if self.weights_history:
            last_w = self.weights_history[-1]
            lines.append("  Final Weights:")
            for name, w in sorted(last_w.items(), key=lambda x: -x[1]):
                lines.append(f"    {name:30s} {w:.2%}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def load_strategy_navs(
    paths: list[str | Path],
    nav_column: str = "nav",
    date_column: str = "date",
) -> dict[str, dict[str, float]]:
    """从 daily_snapshots.csv 加载多个策略的 NAV 序列。

    Args:
        paths: CSV 文件路径列表
        nav_column: NAV 列名
        date_column: 日期列名

    Returns:
        {strategy_name: {date_str: nav_value}}
    """
    import csv

    result: dict[str, dict[str, float]] = {}
    for p in paths:
        p = Path(p)
        name = p.parent.name  # 用目录名作为策略名
        navs: dict[str, float] = {}
        with open(p, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                date = row.get(date_column, "")
                nav_str = row.get(nav_column, "")
                if date and nav_str:
                    try:
                        navs[date] = float(nav_str)
                    except (ValueError, TypeError):
                        continue
        if navs:
            result[name] = navs
            logger.info("Loaded %s: %d days, NAV range [%.2f, %.2f]",
                        name, len(navs), min(navs.values()), max(navs.values()))
        else:
            logger.warning("No NAV data loaded from %s", p)
    return result


def _align_navs(
    raw_navs: dict[str, dict[str, float]],
) -> tuple[list[str], dict[str, np.ndarray]]:
    """对齐多个策略的 NAV 到共同日期集。

    Returns:
        (sorted_dates, {name: nav_array})，长度一致。
    """
    if not raw_navs:
        return [], {}
    # 取所有策略的共同日期
    date_sets = [set(navs.keys()) for navs in raw_navs.values()]
    common_dates = sorted(set.intersection(*date_sets))
    if not common_dates:
        logger.warning("No common dates across strategies")
        return [], {}

    aligned: dict[str, np.ndarray] = {}
    for name, navs in raw_navs.items():
        aligned[name] = np.array([navs[d] for d in common_dates])
    return common_dates, aligned


def _compute_returns(nav: np.ndarray) -> np.ndarray:
    """NAV → 日收益率序列 (长度 = len(nav) - 1)。"""
    return np.diff(nav) / nav[:-1]


# ---------------------------------------------------------------------------
# 核心类
# ---------------------------------------------------------------------------

class StrategyCombo:
    """策略组合构造器。

    Args:
        raw_navs: {strategy_name: {date: nav}} 从 load_strategy_navs 获取
        method: "equal_weight" | "risk_parity" | "rolling_risk_parity" | "momentum_tilt"
        lookback: 风险平价/动量计算的回看窗口（天）
        rebalance_freq: 权重再平衡频率（天）
        risk_free_rate: 年化无风险利率,用于夏普计算
        tilt_strength: momentum_tilt 模式下的倾斜强度（0~1, 默认 0.3）
    """

    METHODS = (
        "equal_weight", "risk_parity", "rolling_risk_parity",
        "momentum_tilt", "regime_adaptive", "vol_targeted_regime",
        "performance_weighted", "regime_momentum",
    )

    def __init__(
        self,
        raw_navs: dict[str, dict[str, float]],
        method: str = "equal_weight",
        lookback: int = 60,
        rebalance_freq: int = 20,
        risk_free_rate: float = 0.02,
        tilt_strength: float = 0.3,
        external_index: dict[str, float] | None = None,
        vol_target: float = 0.15,
        cb_market_data: dict[str, dict[str, float]] | None = None,
    ) -> None:
        if method not in self.METHODS:
            raise ValueError(f"Unknown method {method}, choose from {self.METHODS}")
        self.method = method
        self.lookback = lookback
        self.rebalance_freq = rebalance_freq
        self.risk_free_rate = risk_free_rate
        self.tilt_strength = tilt_strength
        self.external_index = external_index  # {date_str: close}
        self.vol_target = vol_target
        # cb_market_data: {"index_close": {date:v}, "total_amount": {date:v},
        #                  "premium_median": {date:v}} — 来自 CBMarketAggregator
        self.cb_market_data = cb_market_data

        self.dates, self.aligned_navs = _align_navs(raw_navs)
        self.names = sorted(self.aligned_navs.keys())
        if len(self.dates) < 2:
            raise ValueError("Need at least 2 common dates for combo")
        logger.info(
            "StrategyCombo: %d strategies, %d common dates, method=%s",
            len(self.names), len(self.dates), method,
        )

    def compute(self) -> ComboResult:
        """执行组合计算，返回 ComboResult。"""
        n_days = len(self.dates)
        n_strats = len(self.names)

        # 将各策略 NAV 归一化到起始 = 1.0
        norm_navs: dict[str, np.ndarray] = {}
        for name in self.names:
            nav = self.aligned_navs[name]
            norm_navs[name] = nav / nav[0]

        # 计算各策略日收益率
        returns_dict: dict[str, np.ndarray] = {}
        for name in self.names:
            returns_dict[name] = _compute_returns(norm_navs[name])

        # 构建权重历史
        weights_history: list[dict[str, float]] = []
        combo_nav = np.ones(n_days)

        if self.method == "equal_weight":
            w = {name: 1.0 / n_strats for name in self.names}
            for t in range(n_days):
                weights_history.append(w.copy())
                if t == 0:
                    continue
                day_ret = sum(
                    w[name] * returns_dict[name][t - 1] for name in self.names
                )
                combo_nav[t] = combo_nav[t - 1] * (1 + day_ret)

        elif self.method == "risk_parity":
            # 全局风险平价：用全部历史算波动率
            all_rets = np.column_stack(
                [returns_dict[name] for name in self.names]
            )
            vols = np.std(all_rets, axis=0, ddof=1)
            vols = np.maximum(vols, 1e-10)
            inv_vols = 1.0 / vols
            w_arr = inv_vols / inv_vols.sum()
            w = {name: float(w_arr[i]) for i, name in enumerate(self.names)}
            for t in range(n_days):
                weights_history.append(w.copy())
                if t == 0:
                    continue
                day_ret = sum(
                    w[name] * returns_dict[name][t - 1] for name in self.names
                )
                combo_nav[t] = combo_nav[t - 1] * (1 + day_ret)

        elif self.method == "rolling_risk_parity":
            # 滑窗风险平价：每 rebalance_freq 天重新计算
            current_w = {name: 1.0 / n_strats for name in self.names}
            for t in range(n_days):
                # 重新计算权重
                if t > 0 and t % self.rebalance_freq == 0:
                    start_idx = max(0, t - 1 - self.lookback)
                    end_idx = t - 1
                    if end_idx > start_idx:
                        window_rets = np.column_stack([
                            returns_dict[name][start_idx:end_idx]
                            for name in self.names
                        ])
                        vols = np.std(window_rets, axis=0, ddof=1)
                        vols = np.maximum(vols, 1e-10)
                        inv_vols = 1.0 / vols
                        w_arr = inv_vols / inv_vols.sum()
                        current_w = {
                            name: float(w_arr[i])
                            for i, name in enumerate(self.names)
                        }
                weights_history.append(current_w.copy())
                if t == 0:
                    continue
                day_ret = sum(
                    current_w[name] * returns_dict[name][t - 1]
                    for name in self.names
                )
                combo_nav[t] = combo_nav[t - 1] * (1 + day_ret)

        elif self.method == "momentum_tilt":
            # Momentum Tilt: 在等权基础上按近期动量倾斜权重
            #
            # 算法 (Asness et al. "Value and Momentum Everywhere", 2013):
            # 1. 基础权重 = 1/N
            # 2. 每 rebalance_freq 天计算 lookback 窗口内的累计收益
            # 3. 对累计收益做 z-score 标准化
            # 4. w_i = base_w + tilt_strength * z_i / N
            # 5. Clip 到 [0, 2/N] 防止做空或过度集中，再归一化
            #
            # 经济学逻辑：时序动量（Moskowitz, Ooi, Pedersen 2012）
            # 近期表现好的策略在短期内有持续性。
            base_w = 1.0 / n_strats
            current_w = {name: base_w for name in self.names}
            tilt = self.tilt_strength

            for t in range(n_days):
                # 在 rebalance 点重新计算权重
                if t > self.lookback and t % self.rebalance_freq == 0:
                    # 计算各策略 lookback 期间累计收益
                    mom_scores = {}
                    for name in self.names:
                        nav_now = norm_navs[name][t]
                        nav_lb = norm_navs[name][t - self.lookback]
                        mom_scores[name] = nav_now / nav_lb - 1.0

                    # Z-score 标准化
                    scores = np.array([mom_scores[name] for name in self.names])
                    mu = scores.mean()
                    sigma = scores.std(ddof=0)
                    if sigma > 1e-10:
                        z = (scores - mu) / sigma
                    else:
                        z = np.zeros(n_strats)

                    # 倾斜
                    raw_w = np.array([base_w + tilt * z[i] / n_strats
                                     for i in range(n_strats)])
                    # Clip 防止做空和过度集中
                    raw_w = np.clip(raw_w, 0.0, 2.0 / n_strats)
                    raw_w /= raw_w.sum()  # 归一化
                    current_w = {
                        name: float(raw_w[i]) for i, name in enumerate(self.names)
                    }

                weights_history.append(current_w.copy())
                if t == 0:
                    continue
                day_ret = sum(
                    current_w[name] * returns_dict[name][t - 1]
                    for name in self.names
                )
                combo_nav[t] = combo_nav[t - 1] * (1 + day_ret)

        elif self.method == "regime_adaptive":
            # Regime 自适应配权：用 RegimeDetector 检测状态，
            # 通过 RegimeAdaptiveAllocator 调整权重
            # 增强：使用 CB 市场聚合数据 (index/volume/premium)
            from aipha.portfolio.regime_detector import (
                DetectorMode,
                RegimeDetector,
                RegimeDetectorConfig,
            )
            from aipha.portfolio.regime_weight import (
                RegimeAdaptiveAllocator,
                RegimeWeightConfig,
                StrategyStyle,
            )
            detector = RegimeDetector(RegimeDetectorConfig(
                mode=DetectorMode.RULE, use_premium_signal=True,
            ))
            allocator = RegimeAdaptiveAllocator(
                config=RegimeWeightConfig(transition_speed=1.0),
            )
            # 自动注册短策略名 → 风格映射
            _SHORT_STYLE = {
                "sq_v4b": StrategyStyle.AGGRESSIVE,
                "cb_stock_quality": StrategyStyle.AGGRESSIVE,
                "triple_low_v3": StrategyStyle.DEFENSIVE,
                "triple_low": StrategyStyle.DEFENSIVE,
                "small_size_v2": StrategyStyle.BALANCED,
                "adaptive_factor": StrategyStyle.BALANCED,
                "cb_adaptive_factor": StrategyStyle.BALANCED,
            }
            for name in self.names:
                if name in _SHORT_STYLE:
                    allocator.register_strategy(name, _SHORT_STYLE[name])
            base_w = {name: 1.0 / n_strats for name in self.names}
            current_w = dict(base_w)
            ext_idx = self._align_external_index()
            # 对齐 CB 市场数据
            cb_vol = self._align_cb_series("total_amount")
            cb_prem = self._align_cb_series("premium_median")
            cb_idx = self._align_cb_series("index_close")

            for t in range(n_days):
                if t > 0:
                    # 优先用 CB 市场聚合指数 > 外部指数 > combo NAV
                    if cb_idx is not None:
                        idx_close = cb_idx[t - 1]
                    elif ext_idx is not None:
                        idx_close = ext_idx[t - 1]
                    else:
                        idx_close = float(combo_nav[t - 1]) * 1000.0
                    vol_val = float(cb_vol[t - 1]) if cb_vol is not None else 0.0
                    prem_val = float(cb_prem[t - 1]) if cb_prem is not None else 0.0
                    detector.update(idx_close, volume=vol_val, premium_median=prem_val)
                if t > 0 and t % self.rebalance_freq == 0:
                    regime = detector.detect()
                    is_hv = detector.is_high_volatility()
                    current_w = allocator.compute_weights(
                        base_weights=base_w, regime=regime, is_high_vol=is_hv,
                    )
                weights_history.append(current_w.copy())
                if t == 0:
                    continue
                day_ret = sum(
                    current_w[name] * returns_dict[name][t - 1]
                    for name in self.names
                )
                combo_nav[t] = combo_nav[t - 1] * (1 + day_ret)

        elif self.method == "vol_targeted_regime":
            # Regime 自适应 + 波动率目标控制
            # 增强：使用真实 CB 市场数据
            from aipha.portfolio.regime_detector import (
                DetectorMode,
                RegimeDetector,
                RegimeDetectorConfig,
            )
            from aipha.portfolio.regime_weight import (
                RegimeAdaptiveAllocator,
                RegimeWeightConfig,
                StrategyStyle,
            )
            from aipha.risk.ml_risk_controls import VolTargetScaler
            detector = RegimeDetector(RegimeDetectorConfig(
                mode=DetectorMode.RULE, use_premium_signal=True,
            ))
            allocator = RegimeAdaptiveAllocator(
                config=RegimeWeightConfig(transition_speed=1.0),
            )
            # 自动注册策略风格
            _SHORT_STYLE = {
                "sq_v4b": StrategyStyle.AGGRESSIVE,
                "cb_stock_quality": StrategyStyle.AGGRESSIVE,
                "triple_low_v3": StrategyStyle.DEFENSIVE,
                "triple_low": StrategyStyle.DEFENSIVE,
                "small_size_v2": StrategyStyle.BALANCED,
                "adaptive_factor": StrategyStyle.BALANCED,
                "cb_adaptive_factor": StrategyStyle.BALANCED,
            }
            for name in self.names:
                if name in _SHORT_STYLE:
                    allocator.register_strategy(name, _SHORT_STYLE[name])
            vol_targeter = VolTargetScaler(
                vol_target=self.vol_target,
                lookback=20,
                min_scale=0.3,
                max_scale=1.5,
            )
            base_w = {name: 1.0 / n_strats for name in self.names}
            current_w = dict(base_w)
            ext_idx = self._align_external_index()
            cb_vol = self._align_cb_series("total_amount")
            cb_prem = self._align_cb_series("premium_median")
            cb_idx = self._align_cb_series("index_close")

            for t in range(n_days):
                if t > 0:
                    if cb_idx is not None:
                        idx_close = cb_idx[t - 1]
                    elif ext_idx is not None:
                        idx_close = ext_idx[t - 1]
                    else:
                        idx_close = float(combo_nav[t - 1]) * 1000.0
                    vol_val = float(cb_vol[t - 1]) if cb_vol is not None else 0.0
                    prem_val = float(cb_prem[t - 1]) if cb_prem is not None else 0.0
                    detector.update(idx_close, volume=vol_val, premium_median=prem_val)
                # Regime-based rebalance
                if t > 0 and t % self.rebalance_freq == 0:
                    regime = detector.detect()
                    is_hv = detector.is_high_volatility()
                    current_w = allocator.compute_weights(
                        base_weights=base_w, regime=regime, is_high_vol=is_hv,
                    )
                weights_history.append(current_w.copy())
                if t == 0:
                    continue
                raw_ret = sum(
                    current_w[name] * returns_dict[name][t - 1]
                    for name in self.names
                )
                scale = vol_targeter.update(raw_ret)
                scaled_ret = raw_ret * scale
                combo_nav[t] = combo_nav[t - 1] * (1 + scaled_ret)

        elif self.method == "performance_weighted":
            # Performance-Weighted Allocation
            #
            # 核心思想: 滚动 Sharpe 驱动的动态权重分配
            # 1. 每 rebalance_freq 天评估各策略近 lookback 天的 Sharpe
            # 2. Sharpe > 0 的策略按 Sharpe 比例分配权重
            # 3. Sharpe <= 0 的策略降到 min_weight（不完全清零保留分散化）
            # 4. 叠加 vol targeting 缩放组合仓位
            #
            # 经济逻辑 (Moskowitz, Ooi, Pedersen 2012):
            # 时序动量 + 截面 relative value 的混合
            # 表现好的策略短期内有延续性 → 增配
            # 但保留 min_weight 避免过度集中并应对反转
            from aipha.risk.ml_risk_controls import VolTargetScaler
            vol_targeter = VolTargetScaler(
                vol_target=self.vol_target,
                lookback=20,
                min_scale=0.3,
                max_scale=1.5,
            )

            min_weight = 0.05  # 最低权重（保留分散化）
            current_w = {name: 1.0 / n_strats for name in self.names}

            for t in range(n_days):
                # Rebalance: 根据滚动 Sharpe 调整权重
                if t > self.lookback and t % self.rebalance_freq == 0:
                    sharpes: dict[str, float] = {}
                    for name in self.names:
                        rets_window = returns_dict[name][
                            max(0, t - 1 - self.lookback):t - 1
                        ]
                        if len(rets_window) > 5:
                            sharpes[name] = compute_sharpe_ratio(
                                rets_window,
                            )
                        else:
                            sharpes[name] = 0.0

                    # 正 Sharpe 策略按比例分配，负的给最低权重
                    pos_sharpes = {
                        n: max(0.0, s) for n, s in sharpes.items()
                    }
                    total_pos = sum(pos_sharpes.values())

                    raw_w: dict[str, float] = {}
                    if total_pos > 1e-10:
                        for name in self.names:
                            prop = pos_sharpes[name] / total_pos
                            raw_w[name] = max(
                                min_weight,
                                prop * (1.0 - min_weight * n_strats)
                                + min_weight,
                            )
                    else:
                        raw_w = {n: 1.0 / n_strats for n in self.names}

                    # 归一化
                    total_w = sum(raw_w.values())
                    current_w = {
                        n: w / total_w for n, w in raw_w.items()
                    }

                weights_history.append(current_w.copy())
                if t == 0:
                    continue
                raw_ret = sum(
                    current_w[name] * returns_dict[name][t - 1]
                    for name in self.names
                )
                # Vol targeting overlay
                scale = vol_targeter.update(raw_ret)
                scaled_ret = raw_ret * scale
                combo_nav[t] = combo_nav[t - 1] * (1 + scaled_ret)

        elif self.method == "regime_momentum":
            # 混合方法: Momentum Tilt 基础权重 + Regime 叠加
            #
            # 算法 (Asness et al. "Value and Momentum Everywhere" + Hamilton 1989):
            # 1. 先用 momentum_tilt 计算基础权重（截面动量）
            # 2. 再用 regime 信号调整：BULL 增配 aggressive，BEAR 增配 defensive
            # 3. EMA 平滑 + 归一化
            #
            # 优势：兼顾 time-series regime + cross-sectional momentum
            from aipha.portfolio.regime_detector import (
                DetectorMode, RegimeDetector, RegimeDetectorConfig,
            )
            from aipha.portfolio.regime_weight import (
                RegimeAdaptiveAllocator, RegimeWeightConfig, StrategyStyle,
            )
            detector = RegimeDetector(RegimeDetectorConfig(
                mode=DetectorMode.RULE, use_premium_signal=True,
            ))
            allocator = RegimeAdaptiveAllocator(
                config=RegimeWeightConfig(transition_speed=1.0),
            )
            _SHORT_STYLE = {
                "sq_v4b": StrategyStyle.AGGRESSIVE,
                "cb_stock_quality": StrategyStyle.AGGRESSIVE,
                "triple_low_v3": StrategyStyle.DEFENSIVE,
                "triple_low": StrategyStyle.DEFENSIVE,
                "small_size_v2": StrategyStyle.BALANCED,
                "adaptive_factor": StrategyStyle.BALANCED,
                "cb_adaptive_factor": StrategyStyle.BALANCED,
            }
            for name in self.names:
                if name in _SHORT_STYLE:
                    allocator.register_strategy(name, _SHORT_STYLE[name])

            base_w = 1.0 / n_strats
            tilt = self.tilt_strength
            current_w = {name: base_w for name in self.names}
            cb_vol = self._align_cb_series("total_amount")
            cb_prem = self._align_cb_series("premium_median")
            cb_idx = self._align_cb_series("index_close")

            for t in range(n_days):
                if t > 0:
                    idx_c = float(cb_idx[t - 1]) if cb_idx is not None else float(combo_nav[t - 1]) * 1000
                    vol_v = float(cb_vol[t - 1]) if cb_vol is not None else 0.0
                    prem_v = float(cb_prem[t - 1]) if cb_prem is not None else 0.0
                    detector.update(idx_c, volume=vol_v, premium_median=prem_v)

                if t > self.lookback and t % self.rebalance_freq == 0:
                    # Step 1: Momentum tilt 基础权重
                    mom_scores = {}
                    for name in self.names:
                        nav_now = norm_navs[name][t]
                        nav_lb = norm_navs[name][t - self.lookback]
                        mom_scores[name] = nav_now / nav_lb - 1.0
                    scores = np.array([mom_scores[n] for n in self.names])
                    mu, sigma = scores.mean(), scores.std(ddof=0)
                    z = (scores - mu) / sigma if sigma > 1e-10 else np.zeros(n_strats)
                    raw_w = np.array([base_w + tilt * z[i] / n_strats for i in range(n_strats)])
                    raw_w = np.clip(raw_w, 0.0, 2.0 / n_strats)
                    raw_w /= raw_w.sum()
                    mt_w = {n: float(raw_w[i]) for i, n in enumerate(self.names)}

                    # Step 2: Regime overlay
                    regime = detector.detect()
                    is_hv = detector.is_high_volatility()
                    current_w = allocator.compute_weights(
                        base_weights=mt_w, regime=regime, is_high_vol=is_hv,
                    )

                weights_history.append(current_w.copy())
                if t == 0:
                    continue
                day_ret = sum(
                    current_w[name] * returns_dict[name][t - 1]
                    for name in self.names
                )
                combo_nav[t] = combo_nav[t - 1] * (1 + day_ret)

        # 计算汇总指标
        result = ComboResult(
            method=self.method,
            strategy_names=self.names,
            weights_history=weights_history,
            combo_nav=combo_nav,
            strategy_navs=norm_navs,
            dates=self.dates,
        )
        self._compute_metrics(result, combo_nav)
        return result

    def _align_external_index(self) -> np.ndarray | None:
        """将外部指数对齐到 self.dates，返回 np.ndarray 或 None。"""
        if self.external_index is None:
            return None
        values: list[float] = []
        last_val = 1000.0  # fallback
        for d in self.dates:
            if d in self.external_index:
                last_val = self.external_index[d]
            values.append(last_val)
        return np.array(values)

    def _align_cb_series(self, key: str) -> np.ndarray | None:
        """从 cb_market_data 中提取指定 key 并对齐到 self.dates。

        Args:
            key: "index_close" | "total_amount" | "premium_median" 等

        Returns:
            对齐后的 np.ndarray 或 None（无数据时）
        """
        if self.cb_market_data is None or key not in self.cb_market_data:
            return None
        series = self.cb_market_data[key]
        if not series:
            return None
        values: list[float] = []
        last_val = 0.0
        for d in self.dates:
            if d in series:
                last_val = series[d]
            values.append(last_val)
        return np.array(values)

    def _compute_metrics(self, result: ComboResult, nav: np.ndarray) -> None:
        """填充 ComboResult 的汇总指标。"""
        rets = _compute_returns(nav)
        n_days = len(nav)
        trading_days = TRADING_DAYS_PER_YEAR

        result.total_return = float(nav[-1] / nav[0] - 1)

        years = n_days / trading_days
        if years > 0:
            result.annualized_return = float(
                (nav[-1] / nav[0]) ** (1 / years) - 1
            )
        result.volatility = float(np.std(rets, ddof=1) * math.sqrt(trading_days))

        rf_daily = (1 + self.risk_free_rate) ** (1 / trading_days) - 1
        excess = rets - rf_daily
        if np.std(excess, ddof=1) > 0:
            result.sharpe_ratio = float(
                np.mean(excess) / np.std(excess, ddof=1) * math.sqrt(trading_days)
            )

        # Max drawdown
        cummax = np.maximum.accumulate(nav)
        drawdowns = (cummax - nav) / cummax
        result.max_drawdown = float(np.max(drawdowns))

        if result.max_drawdown > 0:
            result.calmar_ratio = result.annualized_return / result.max_drawdown


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

def compare_combo_methods(
    raw_navs: dict[str, dict[str, float]],
    methods: list[str] | None = None,
    **kwargs,
) -> dict[str, ComboResult]:
    """比较多种组合方法的结果。

    Args:
        raw_navs: 策略 NAV 字典
        methods: 要比较的方法列表，默认全部三种
        **kwargs: 传给 StrategyCombo 的参数

    Returns:
        {method: ComboResult}
    """
    if methods is None:
        methods = list(StrategyCombo.METHODS)
    results = {}
    for m in methods:
        try:
            combo = StrategyCombo(raw_navs, method=m, **kwargs)
            results[m] = combo.compute()
        except Exception as e:
            logger.error("Failed to compute %s combo: %s", m, e)
    return results
