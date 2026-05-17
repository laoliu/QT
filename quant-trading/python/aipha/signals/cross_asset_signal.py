"""Cross-Asset Signal Engine — 跨资产交互信号生成与聚合。

将资金流/两融/宏观等原始数据层转化为标准化信号，供策略层和
组合层使用。信号输出为 [-1, +1] 区间的标准化值或离散制度标签。

集成点：
- RegimeAdvisor: macro_regime 信号 → 制度标签 → 权重调节
- CompositeRiskManager: risk_off 信号 → 仓位缩放
- Strategy: northbound_flow 信号 → 选股权重调节

学术依据：
- Baker & Wurgler (2006) 投资者情绪指标与横截面收益
- Lou (2012) 共同基金资金流与价格压力
- 北向资金净流入与 A 股收益：显著正相关（中金 2023 研报）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Enums & Data Models
# ------------------------------------------------------------------

class CrossAssetSignalType(str, Enum):
    """跨资产信号类型。"""
    NORTHBOUND_FLOW = "northbound_flow"       # 北向资金动量
    MARGIN_SENTIMENT = "margin_sentiment"     # 两融情绪
    MACRO_REGIME = "macro_regime"             # 宏观制度标签
    MARKET_BREADTH = "market_breadth"         # 市场宽度（涨跌比）


@dataclass
class CrossAssetSignal:
    """单条跨资产信号。"""
    signal_type: str               # CrossAssetSignalType.value
    trade_date: str                # YYYYMMDD
    value: float                   # 标准化信号 [-1, +1] 或 Z-score
    regime_label: str = ""         # 离散标签
    confidence: float = 1.0        # 信号置信度 [0, 1]
    raw_data: dict = field(default_factory=dict)


@dataclass
class CrossAssetConfig:
    """跨资产信号引擎配置。"""
    enabled: bool = True
    northbound_lookback: int = 20      # 北向资金动量窗口（交易日）
    margin_lookback: int = 10          # 两融情绪窗口
    breadth_threshold: float = 0.6     # 市场宽度阈值
    zscore_clip: float = 3.0           # Z-score 截断
    risk_off_threshold: float = -0.5   # risk_off 触发阈值


# ------------------------------------------------------------------
# Signal Engine
# ------------------------------------------------------------------

class CrossAssetSignalEngine:
    """跨资产信号引擎 — 数据层 → 标准化信号。"""

    def __init__(self, config: CrossAssetConfig | None = None) -> None:
        self._config = config or CrossAssetConfig()
        self._signal_cache: dict[str, list[CrossAssetSignal]] = {}

    def compute_signals(
        self, trade_date: str, market_data: dict[str, Any] | None = None,
    ) -> list[CrossAssetSignal]:
        """计算指定日期的所有跨资产信号。

        market_data keys:
            - "northbound_flow": pd.Series (日净流入, index=date)
            - "margin_balance": pd.Series (融资余额日变化, index=date)
            - "advance_decline": tuple[int, int] (涨/跌家数)
        """
        if not self._config.enabled:
            return []
        data = market_data or {}
        signals = [
            self.compute_northbound_signal(
                trade_date, data.get("northbound_flow"),
            ),
            self.compute_margin_signal(
                trade_date, data.get("margin_balance"),
            ),
            self.compute_breadth_signal(
                trade_date, data.get("advance_decline"),
            ),
        ]
        self._signal_cache[trade_date] = signals
        return signals

    def compute_northbound_signal(
        self, trade_date: str, flow_series: pd.Series | None = None,
    ) -> CrossAssetSignal:
        """北向资金动量信号。

        正值表示北向净流入加速（risk-on），负值表示净流出（risk-off）。
        使用滚动 Z-score 标准化。
        """
        if flow_series is None or flow_series.empty:
            return CrossAssetSignal(
                signal_type=CrossAssetSignalType.NORTHBOUND_FLOW.value,
                trade_date=trade_date,
                value=0.0,
                confidence=0.0,
            )
        z = self._zscore(
            flow_series, self._config.northbound_lookback, self._config.zscore_clip
        )
        # Normalize to [-1, 1]
        norm_val = float(np.clip(z / self._config.zscore_clip, -1.0, 1.0))
        label = "inflow" if norm_val > 0.2 else ("outflow" if norm_val < -0.2 else "neutral")
        return CrossAssetSignal(
            signal_type=CrossAssetSignalType.NORTHBOUND_FLOW.value,
            trade_date=trade_date,
            value=norm_val,
            regime_label=label,
            confidence=min(1.0, len(flow_series) / self._config.northbound_lookback),
            raw_data={"z_score": z, "latest_flow": float(flow_series.iloc[-1])},
        )

    def compute_margin_signal(
        self, trade_date: str, margin_series: pd.Series | None = None,
    ) -> CrossAssetSignal:
        """两融情绪信号。

        融资余额增长 → 看多情绪（正值）；融资余额下降 → 看空情绪（负值）。
        """
        if margin_series is None or margin_series.empty:
            return CrossAssetSignal(
                signal_type=CrossAssetSignalType.MARGIN_SENTIMENT.value,
                trade_date=trade_date,
                value=0.0,
                confidence=0.0,
            )
        z = self._zscore(
            margin_series, self._config.margin_lookback, self._config.zscore_clip
        )
        norm_val = float(np.clip(z / self._config.zscore_clip, -1.0, 1.0))
        label = "bullish" if norm_val > 0.3 else ("bearish" if norm_val < -0.3 else "neutral")
        return CrossAssetSignal(
            signal_type=CrossAssetSignalType.MARGIN_SENTIMENT.value,
            trade_date=trade_date,
            value=norm_val,
            regime_label=label,
            confidence=min(1.0, len(margin_series) / self._config.margin_lookback),
            raw_data={"z_score": z},
        )

    def compute_breadth_signal(
        self, trade_date: str, advance_decline: tuple[int, int] | None = None,
    ) -> CrossAssetSignal:
        """市场宽度信号。

        涨跌比 > threshold → 多头市场（正值）；< (1-threshold) → 空头市场（负值）。
        """
        if advance_decline is None:
            return CrossAssetSignal(
                signal_type=CrossAssetSignalType.MARKET_BREADTH.value,
                trade_date=trade_date,
                value=0.0,
                confidence=0.0,
            )
        advances, declines = advance_decline
        total = advances + declines
        if total == 0:
            return CrossAssetSignal(
                signal_type=CrossAssetSignalType.MARKET_BREADTH.value,
                trade_date=trade_date,
                value=0.0,
                confidence=0.0,
            )
        ratio = advances / total
        # Map ratio [0, 1] to signal [-1, 1]
        norm_val = float((ratio - 0.5) * 2.0)
        threshold = self._config.breadth_threshold
        if ratio > threshold:
            label = "broad_rally"
        elif ratio < (1.0 - threshold):
            label = "broad_decline"
        else:
            label = "mixed"
        return CrossAssetSignal(
            signal_type=CrossAssetSignalType.MARKET_BREADTH.value,
            trade_date=trade_date,
            value=norm_val,
            regime_label=label,
            confidence=1.0,
            raw_data={"advances": advances, "declines": declines, "ratio": ratio},
        )

    def get_position_scale(
        self, signals: list[CrossAssetSignal] | None = None,
    ) -> float:
        """基于当日跨资产信号返回全局仓位缩放因子 [0.0, 1.0]。

        等权聚合所有信号的 value，映射到仓位缩放：
        - avg_value > 0 → scale = 1.0 (full position)
        - avg_value < risk_off_threshold → scale 线性缩减到 0.5
        """
        if not signals:
            return 1.0
        # Only use signals with confidence > 0
        valid = [s for s in signals if s.confidence > 0]
        if not valid:
            return 1.0
        avg_val = sum(s.value * s.confidence for s in valid) / sum(
            s.confidence for s in valid
        )
        threshold = self._config.risk_off_threshold
        if avg_val >= 0:
            return 1.0
        elif avg_val <= threshold:
            return 0.5
        else:
            # Linear interpolation between 1.0 and 0.5
            return 1.0 + (avg_val / threshold) * 0.5

    @staticmethod
    def _zscore(
        series: pd.Series, lookback: int, clip: float = 3.0,
    ) -> float:
        """计算滚动 Z-score，截断到 [-clip, +clip]。"""
        if series is None or len(series) < 2:
            return 0.0
        recent = series.iloc[-lookback:] if len(series) >= lookback else series
        mean = recent.mean()
        std = recent.std()
        if std < 1e-12:
            return 0.0
        latest = float(series.iloc[-1])
        z = (latest - mean) / std
        return float(np.clip(z, -clip, clip))
