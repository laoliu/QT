"""统一仓位缩放器（UnifiedPositionSizer）

Backtest / Paper / Live 三路径共享的仓位管理实现（§4.3.1）：
- VolTargetScaler（§13.2 唯一实现，来自 ml_risk_controls）: 波动率目标 → 当波动率上升时自动减仓
- DrawdownPositionReducer: 回撤控制 → 回撤超阈值时降低仓位
- ValuationPositionSizer: 估值择时 → 根据市场整体估值水位调整仓位
- UnifiedPositionSizer: 组合：同时应用波动率目标 + 回撤控制 + 估值择时 + MDD 梯度

默认参数对齐 CPS 生产验证值 (vol_target=0.20, lookback=60, max_scale=1.5)。

这些 overlay 是"后处理"层，作用在策略输出的 OrderIntent 之上，
通过调整持仓规模来控制组合波动和回撤，而非改变策略选股逻辑。

设计原则（对齐 copilot-instructions.md 第 7.1 条）：
- 可插拔：通过配置开关启用/禁用
- 与策略层解耦：策略只输出目标持仓，overlay 调整规模
- 参数化：所有阈值通过配置注入，不硬编码

使用方式：
    sizer = UnifiedPositionSizer(
        vol_target=0.20,         # 年化波动率目标 20%
        drawdown_threshold=0.05, # 回撤 >5% 时减仓
        drawdown_reduction=0.5,  # 减仓至 50%
    )
    # 每日调用
    scale = sizer.compute_scale(daily_nav_series)
    adjusted_capital = total_capital * scale

PositionSizingOverlay 是 UnifiedPositionSizer 的向后兼容别名（Phase D 删除）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR

logger = logging.getLogger(__name__)


@dataclass
class VolatilityTargetConfig:
    """波动率目标配置

    默认参数对齐 CPS 生产验证值 (§4.3.1 共享路径):
    - target_vol=0.20, lookback=60, max_scale=1.5
    """

    target_vol: float = 0.20       # 目标年化波动率 (对齐 VolTargetScaler)
    lookback_days: int = 60        # 波动率估算窗口（交易日，对齐 VolTargetScaler）
    min_scale: float = 0.2         # 最低仓位比例
    max_scale: float = 1.5         # 最高仓位比例 (对齐 VolTargetScaler)
    trading_days_per_year: int = TRADING_DAYS_PER_YEAR
    enabled: bool = True


@dataclass
class DrawdownControlConfig:
    """回撤控制配置

    支持两种模式:
    1. 简单模式: threshold → max_threshold 间线性插值 (兼容旧配置)
    2. 渐进模式: progressive_thresholds 定义多级阈值与对应仓位比例

    渐进模式示例 (R71 推荐):
        progressive_thresholds: [(0.10, 0.70), (0.15, 0.50), (0.20, 0.30)]
        含义: 回撤 10% 时仓位降至 70%, 15% 降至 50%, 20% 降至 30%

    peak_reset_after_flat_days: 当 drawdown 持续恢复且 NAV 在 peak 的 95%
        以上超过 N 天时，重置 peak 为当前 NAV。防止死亡螺旋中 peak
        永远锁在历史高位导致 scale 无法恢复。0 = 禁用（默认）。
    """

    threshold: float = 0.05        # 回撤超此值开始减仓 (简单模式)
    max_threshold: float = 0.10    # 回撤超此值减至最低 (简单模式)
    min_scale: float = 0.3         # 最大回撤时的仓位下限 (简单模式)
    enabled: bool = True
    peak_reset_after_flat_days: int = 0  # >0 时启用 peak reset 机制

    # 渐进模式: list of (drawdown_pct, target_scale) 升序排列
    # 非空时覆盖简单模式
    progressive_thresholds: list[tuple[float, float]] = field(
        default_factory=list,
    )


class DrawdownPositionReducer:
    """回撤仓位控制

    原理：
    - 检测当前回撤 = 1 - current_nav / peak_nav
    - 回撤 < threshold → scale = 1.0（满仓）
    - threshold <= 回撤 <= max_threshold → 线性插值减仓
    - 回撤 > max_threshold → scale = min_scale
    """

    def __init__(self, config: DrawdownControlConfig | None = None):
        self._config = config or DrawdownControlConfig()
        self._peak_nav: float = 0.0
        self._flat_days: int = 0  # peak reset 计数器

    def reset(self) -> None:
        """重置峰值（新回测开始时调用）。"""
        self._peak_nav = 0.0
        self._flat_days = 0

    def compute_scale(self, current_nav: float) -> float:
        """根据当前回撤计算仓位比例。

        支持两种模式:
        - 简单模式: threshold → max_threshold 间线性插值
        - 渐进模式: progressive_thresholds 多级分段插值

        包含 peak reset 机制: 当 NAV 恢复到 peak 的 95% 以上
        持续 peak_reset_after_flat_days 天后，重置 peak 为当前 NAV，
        防止死亡螺旋中 peak 永远锁在历史高位（R161 §4.1 修复）。

        Args:
            current_nav: 当前净值

        Returns:
            仓位比例 [min_scale, 1.0]
        """
        cfg = self._config
        if not cfg.enabled:
            return 1.0

        if current_nav > self._peak_nav:
            self._peak_nav = current_nav
            self._flat_days = 0
        elif cfg.peak_reset_after_flat_days > 0 and self._peak_nav > 0:
            # peak reset 机制: NAV 恢复到 peak 的 95% 以上时开始计数
            recovery_ratio = current_nav / self._peak_nav
            if recovery_ratio >= 0.95:
                self._flat_days += 1
                if self._flat_days >= cfg.peak_reset_after_flat_days:
                    logger.info(
                        "DrawdownPositionReducer: peak reset "
                        "%.2f → %.2f (flat %d days)",
                        self._peak_nav, current_nav, self._flat_days,
                    )
                    self._peak_nav = current_nav
                    self._flat_days = 0
            else:
                self._flat_days = 0

        if self._peak_nav <= 0:
            return 1.0

        drawdown = 1.0 - current_nav / self._peak_nav

        # 渐进模式
        if cfg.progressive_thresholds:
            return self._compute_progressive_scale(drawdown)

        # 简单模式 (向后兼容)
        if drawdown <= cfg.threshold:
            return 1.0
        elif drawdown >= cfg.max_threshold:
            return cfg.min_scale
        else:
            ratio = (drawdown - cfg.threshold) / (
                cfg.max_threshold - cfg.threshold
            )
            scale = 1.0 - ratio * (1.0 - cfg.min_scale)
            return max(cfg.min_scale, scale)

    def _compute_progressive_scale(self, drawdown: float) -> float:
        """分段渐进减仓。

        在相邻阈值之间线性插值:
        - 回撤 < 第一个阈值 → scale = 1.0
        - 回撤在两个阈值之间 → 线性插值
        - 回撤 > 最后一个阈值 → scale = 最后一个目标值
        """
        pts = sorted(self._config.progressive_thresholds, key=lambda x: x[0])
        if not pts:
            return 1.0

        if drawdown <= pts[0][0]:
            return 1.0

        if drawdown >= pts[-1][0]:
            return pts[-1][1]

        # 在阈值段间做线性插值
        prev_dd, prev_scale = 0.0, 1.0
        for i, (dd_thr, tgt_scale) in enumerate(pts):
            if i == 0:
                prev_dd, prev_scale = 0.0, 1.0
            if drawdown <= dd_thr:
                ratio = (drawdown - prev_dd) / (dd_thr - prev_dd)
                return prev_scale - ratio * (prev_scale - tgt_scale)
            prev_dd, prev_scale = dd_thr, tgt_scale

        return pts[-1][1]


class UnifiedPositionSizer:
    """统一仓位缩放器：Backtest / Paper / Live 三路径共享 (§4.3.1)。

    同时应用波动率目标 + 回撤控制 + 估值择时 + MDD 梯度，
    最终 scale = min(vol_scale, dd_scale, val_scale, mdd_gradient_scale)，
    即取所有组件中最保守的。

    默认参数对齐 CPS 生产验证值 (vol_target=0.20, lookback=60, max_scale=1.5)。

    R94 新增：MDD 梯度减仓（阶梯式），从 ``aipha.risk.ml_risk_controls``
    导入 ``MddGradientDeleverager``，遵循 §4.3.1 共享路径规则。

    使用方式::

        sizer = UnifiedPositionSizer(
            vol_config=VolatilityTargetConfig(target_vol=0.20),
            dd_config=DrawdownControlConfig(threshold=0.05),
            val_config=ValuationConfig(enabled=True, high_threshold=0.80),
            mdd_gradient_levels=[(0.10, 1.0), (0.15, 0.80), (0.20, 0.55)],
        )

        # 在回测循环中
        sizer.reset()
        for day in trading_days:
            # ... 策略生成信号 ...
            daily_returns.append(today_return)
            scale = sizer.compute_scale(
                daily_returns, current_nav, market_values=today_premiums,
            )
            effective_capital = total_capital * scale
    """

    def __init__(
        self,
        vol_config: VolatilityTargetConfig | None = None,
        dd_config: DrawdownControlConfig | None = None,
        val_config: ValuationConfig | None = None,
        mdd_gradient_levels: list[tuple[float, float]] | None = None,
        *,
        reduce_threshold: float = 0.0,
        reduce_cooldown_days: int = 5,
        scale_floor: float = 0.3,
    ):
        # §13.2: 使用 VolTargetScaler 作为唯一波动率目标实现
        from aipha.risk.ml_risk_controls import VolTargetScaler
        vc = vol_config or VolatilityTargetConfig()
        self._vol_config = vc
        self._vol_scaler = VolTargetScaler(
            vol_target=vc.target_vol,
            lookback=vc.lookback_days,
            min_scale=vc.min_scale,
            max_scale=vc.max_scale,
        )
        self._dd_reducer = DrawdownPositionReducer(dd_config)
        self._val_sizer = ValuationPositionSizer(val_config)
        # R161 §4.1: 引擎主动减持参数（默认禁用）
        self.reduce_threshold = reduce_threshold  # 0.0 = 禁用主动减持
        self.reduce_cooldown_days = reduce_cooldown_days
        self.scale_floor = scale_floor
        # R94: MDD 梯度减仓（阶梯式，从共享模块导入）
        self._mdd_gradient = None
        self._mdd_gradient_peak: float = 0.0
        if mdd_gradient_levels:
            from aipha.risk.ml_risk_controls import MddGradientDeleverager
            self._mdd_gradient = MddGradientDeleverager(
                levels=list(mdd_gradient_levels),
            )

    def reset(self) -> None:
        """重置状态（新回测开始时调用）。"""
        self._vol_scaler.reset()
        self._dd_reducer.reset()
        self._val_sizer.reset()
        self._mdd_gradient_peak = 0.0

    # 向后兼容属性（供 Paper 测试和诊断使用）
    @property
    def vol_scaler(self) -> "VolTargetScaler":
        """底层波动率缩放器（§13.2 唯一实现）。"""
        return self._vol_scaler

    @property
    def mdd_deleverager(self) -> "MddGradientDeleverager | None":
        """底层 MDD 梯度减仓器。"""
        return self._mdd_gradient

    def _compute_vol_scale(self, daily_returns: Sequence[float]) -> float:
        """使用 VolTargetScaler 计算波动率目标缩放因子。

        将传入的日收益率序列通过增量 API 喂给 VolTargetScaler。
        由于 PositionSizingOverlay 在回测循环中被逐日调用，
        每次传入的 daily_returns 是累积序列，只需处理最新增量。
        """
        if not self._vol_config.enabled:
            return 1.0
        n_returns = len(daily_returns)
        n_fed = len(self._vol_scaler._returns)
        if n_returns > n_fed:
            # 喂入新增的日收益率
            for r in daily_returns[n_fed:]:
                self._vol_scaler.update(r)
        return self._vol_scaler.get_current_scale()

    def compute_scale(
        self,
        daily_returns: Sequence[float],
        current_nav: float,
        market_values: Sequence[float] | None = None,
    ) -> float:
        """计算综合仓位比例。

        Args:
            daily_returns: 日收益率序列
            current_nav: 当前组合净值
            market_values: 当日全市场估值指标（可选）

        Returns:
            综合仓位比例 [min_scale, 1.0]
        """
        vol_scale = self._compute_vol_scale(daily_returns)
        dd_scale = self._dd_reducer.compute_scale(current_nav)
        val_scale = (
            self._val_sizer.compute_scale(market_values)
            if market_values is not None
            else 1.0
        )

        # R94: MDD 梯度减仓（阶梯式）
        mdd_gradient_scale = 1.0
        if self._mdd_gradient is not None:
            if current_nav > self._mdd_gradient_peak:
                self._mdd_gradient_peak = current_nav
            if self._mdd_gradient_peak > 0:
                current_mdd = (
                    1.0 - current_nav / self._mdd_gradient_peak
                )
                mdd_gradient_scale = self._mdd_gradient.get_scale(
                    current_mdd,
                )

        return min(vol_scale, dd_scale, val_scale, mdd_gradient_scale)

    def compute_scale_detail(
        self,
        daily_returns: Sequence[float],
        current_nav: float,
        market_values: Sequence[float] | None = None,
    ) -> dict[str, object]:
        """计算综合仓位比例并返回各层明细（Paper/Live 诊断用）。

        返回 dict 含各层缩放因子，供 Paper/Live 日报和诊断使用。

        Returns:
            dict 含 mdd, mdd_scale, vol_scale, dd_scale, val_scale,
            final_scale, mdd_triggered, vol_triggered
        """
        vol_scale = self._compute_vol_scale(daily_returns)
        dd_scale = self._dd_reducer.compute_scale(current_nav)
        val_scale = (
            self._val_sizer.compute_scale(market_values)
            if market_values is not None
            else 1.0
        )

        current_mdd = 0.0
        mdd_gradient_scale = 1.0
        if self._mdd_gradient is not None:
            if current_nav > self._mdd_gradient_peak:
                self._mdd_gradient_peak = current_nav
            if self._mdd_gradient_peak > 0:
                current_mdd = 1.0 - current_nav / self._mdd_gradient_peak
                mdd_gradient_scale = self._mdd_gradient.get_scale(current_mdd)

        final_scale = min(vol_scale, dd_scale, val_scale, mdd_gradient_scale)
        return {
            "mdd": current_mdd,
            "mdd_scale": mdd_gradient_scale,
            "vol_scale": vol_scale,
            "dd_scale": dd_scale,
            "val_scale": val_scale,
            "final_scale": final_scale,
            "mdd_triggered": mdd_gradient_scale < 1.0,
            "vol_triggered": vol_scale < 0.95,
        }

    @classmethod
    def from_paper_config(
        cls,
        vol_target: float = 0.20,
        vol_lookback: int = 60,
        mdd_levels: list[tuple[float, float]] | None = None,
    ) -> "UnifiedPositionSizer":
        """从 Paper/Live 扁平配置创建实例（向后兼容旧 CPS 接口）。

        Args:
            vol_target: 波动率目标（默认 0.20）
            vol_lookback: 波动率窗口（默认 60）
            mdd_levels: MDD 梯度级别，None 使用 DEFAULT_MDD_LEVELS
        """
        from aipha.risk.ml_risk_controls import DEFAULT_MDD_LEVELS

        vol_cfg = VolatilityTargetConfig(
            target_vol=vol_target,
            lookback_days=vol_lookback,
            enabled=True,
        )
        dd_cfg = DrawdownControlConfig(enabled=False)
        levels = list(mdd_levels) if mdd_levels else list(DEFAULT_MDD_LEVELS)
        return cls(
            vol_config=vol_cfg,
            dd_config=dd_cfg,
            mdd_gradient_levels=levels,
        )


# ============================================================================
# 市场估值仓位控制
# ============================================================================


@dataclass
class ValuationConfig:
    """市场估值仓位控制配置

    根据可转债/A股市场整体估值水位动态调整仓位。

    原理：当市场整体估值偏高（转股溢价率百分位高、中位数价格高）时，
    系统性风险增大，应降低仓位；估值偏低时满仓或加仓。

    Attributes:
        enabled: 是否启用估值仓位控制
        metric: 估值指标类型
            - "premium_percentile": 转股溢价率历史百分位（CB 专用）
            - "price_percentile": 中位数价格的历史百分位（通用）
            - "pe_percentile": 市场 PE 历史百分位（A 股专用）
        lookback_days: 计算历史百分位的回看天数
        high_threshold: 估值偏高阈值（百分位，0~1）
        low_threshold: 估值偏低阈值（百分位，0~1）
        high_scale: 估值偏高时的仓位比例
        low_scale: 估值偏低时的仓位比例
        mid_scale: 中性区间仓位比例
        smooth_days: 平滑天数（避免频繁切换）
    """

    enabled: bool = False
    metric: str = "premium_percentile"
    lookback_days: int = 500
    high_threshold: float = 0.80
    low_threshold: float = 0.20
    high_scale: float = 0.50
    low_scale: float = 1.00
    mid_scale: float = 0.80
    smooth_days: int = 5


class ValuationPositionSizer:
    """基于市场整体估值水平的仓位动态调整器。

    工作流程：
    1. 接收当天全市场标的的估值指标值（如溢价率、PE 等）
    2. 计算当天市场中位数
    3. 与历史滚动窗口比较，得到当前百分位
    4. 根据百分位所在区间（高/中/低）输出仓位缩放比例

    示例::

        sizer = ValuationPositionSizer(ValuationConfig(
            enabled=True,
            metric="premium_percentile",
            high_threshold=0.80,
            low_threshold=0.20,
            high_scale=0.50,   # 估值高位 → 半仓
            low_scale=1.00,    # 估值低位 → 满仓
        ))

        # 每个交易日
        scale = sizer.compute_scale(today_premium_values)
        effective_capital = total_capital * scale
    """

    def __init__(self, config: ValuationConfig | None = None):
        self._config = config or ValuationConfig()
        self._history: list[float] = []  # 每日市场中位数历史
        self._scale_history: list[float] = []  # 输出 scale 平滑用

    def reset(self) -> None:
        """重置历史数据（新回测开始时调用）。"""
        self._history.clear()
        self._scale_history.clear()

    def compute_scale(self, market_values: Sequence[float]) -> float:
        """计算当日仓位缩放比例。

        Args:
            market_values: 当日全市场标的的估值指标值
                - 对于 CB: 所有可转债的转股溢价率
                - 对于 A 股: 所有股票的 PE 值

        Returns:
            仓位缩放比例 [high_scale, low_scale]
        """
        if not self._config.enabled:
            return 1.0

        if not market_values or len(market_values) < 3:
            return self._config.mid_scale

        # 计算当天市场中位数
        median_val = float(np.median(market_values))
        self._history.append(median_val)

        # 回看窗口不足时返回中性仓位
        lookback = self._config.lookback_days
        if len(self._history) < min(20, lookback):
            return self._config.mid_scale

        # 计算当前中位数在历史中的百分位
        window = self._history[-lookback:]
        percentile = sum(1 for v in window if v <= median_val) / len(window)

        # 根据百分位确定原始 scale
        cfg = self._config
        if percentile >= cfg.high_threshold:
            # 线性插值: high_threshold→1.0 映射到 mid_scale→high_scale
            t = (percentile - cfg.high_threshold) / (1.0 - cfg.high_threshold)
            raw_scale = cfg.mid_scale + t * (cfg.high_scale - cfg.mid_scale)
        elif percentile <= cfg.low_threshold:
            # 线性插值: 0.0→low_threshold 映射到 low_scale→mid_scale
            t = percentile / cfg.low_threshold if cfg.low_threshold > 0 else 0.0
            raw_scale = cfg.low_scale + t * (cfg.mid_scale - cfg.low_scale)
        else:
            raw_scale = cfg.mid_scale

        raw_scale = max(min(raw_scale, 1.0), 0.1)

        # EMA 平滑
        self._scale_history.append(raw_scale)
        if cfg.smooth_days > 1 and len(self._scale_history) > 1:
            alpha = 2.0 / (cfg.smooth_days + 1)
            smoothed = self._scale_history[-1]
            for i in range(len(self._scale_history) - 2, max(-1, len(self._scale_history) - cfg.smooth_days - 1), -1):
                smoothed = alpha * self._scale_history[i] + (1 - alpha) * smoothed
            return max(min(smoothed, 1.0), 0.1)

        return raw_scale

    @property
    def current_percentile(self) -> float | None:
        """返回最近一次计算的百分位（用于报告/调试）。"""
        if len(self._history) < 2:
            return None
        window = self._history[-self._config.lookback_days:]
        current = self._history[-1]
        return sum(1 for v in window if v <= current) / len(window)


# 向后兼容别名 — Phase D 时删除
PositionSizingOverlay = UnifiedPositionSizer
