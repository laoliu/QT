"""CrossAssetSignal OrchestratorHook — 跨资产信号缩放挂载点。

将 CrossAssetSignalEngine 接入 ComboOrchestrator 的 hook 机制，
在 pre_rebalance 阶段计算跨资产信号并注入仓位缩放因子到 ctx。

集成路径：
    ComboOrchestrator(hooks=[CrossAssetSignalHook(config)])
    → on_pre_rebalance: 计算 northbound/margin/breadth 信号
    → ctx["cross_asset_scale"]: 0.5~1.0 仓位缩放因子
    → ctx["cross_asset_signals"]: 原始信号列表

使用方式：
    orchestrator 在执行调仓时可读取 ctx["cross_asset_scale"]
    对组件权重做风险调节。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CrossAssetHookConfig:
    """Hook 配置。"""
    enabled: bool = True
    northbound_lookback: int = 20
    margin_lookback: int = 10
    breadth_threshold: float = 0.6
    zscore_clip: float = 3.0
    risk_off_threshold: float = -0.5


class CrossAssetSignalHook:
    """OrchestratorHook 实现 — 跨资产信号仓位缩放。

    实现 OrchestratorHook Protocol 的 on_pre_rebalance 方法，
    在调仓前计算跨资产信号并将缩放因子注入 ctx。
    """

    def __init__(
        self,
        config: CrossAssetHookConfig | None = None,
        market_data_provider: Any = None,
    ) -> None:
        self._config = config or CrossAssetHookConfig()
        self._market_data_provider = market_data_provider
        self._engine: Any = None  # lazy init
        self._last_scale: float = 1.0

    def _ensure_engine(self) -> Any:
        """延迟初始化 CrossAssetSignalEngine。"""
        if self._engine is None:
            from aipha.signals.cross_asset_signal import (
                CrossAssetConfig,
                CrossAssetSignalEngine,
            )
            self._engine = CrossAssetSignalEngine(
                CrossAssetConfig(
                    enabled=self._config.enabled,
                    northbound_lookback=self._config.northbound_lookback,
                    margin_lookback=self._config.margin_lookback,
                    breadth_threshold=self._config.breadth_threshold,
                    zscore_clip=self._config.zscore_clip,
                    risk_off_threshold=self._config.risk_off_threshold,
                )
            )
        return self._engine

    def on_day_start(self, trade_date: str, ctx: dict[str, Any]) -> None:
        """日初回调 — 无操作。"""

    def on_pre_rebalance(self, trade_date: str, ctx: dict[str, Any]) -> None:
        """调仓前回调 — 计算跨资产信号并注入缩放因子。"""
        if not self._config.enabled:
            ctx["cross_asset_scale"] = 1.0
            return

        engine = self._ensure_engine()
        market_data = self._fetch_market_data(trade_date)
        signals = engine.compute_signals(trade_date, market_data)
        scale = engine.get_position_scale(signals)

        ctx["cross_asset_scale"] = scale
        ctx["cross_asset_signals"] = signals
        self._last_scale = scale

        if scale < 1.0:
            logger.info(
                "[CrossAssetHook] %s: position scale=%.2f (risk-off detected)",
                trade_date, scale,
            )

    def on_post_rebalance(
        self, trade_date: str, result: Any, ctx: dict[str, Any],
    ) -> None:
        """调仓后回调 — 无操作。"""

    def on_day_end(self, trade_date: str, ctx: dict[str, Any]) -> None:
        """日终回调 — 无操作。"""

    def on_error(
        self, trade_date: str, error: Exception, ctx: dict[str, Any],
    ) -> None:
        """异常回调 — 记录日志。"""
        logger.warning(
            "[CrossAssetHook] %s error: %s", trade_date, error,
        )

    @property
    def last_scale(self) -> float:
        """最近一次计算的仓位缩放因子。"""
        return self._last_scale

    def _fetch_market_data(self, trade_date: str) -> dict[str, Any]:
        """获取跨资产市场数据。

        优先使用外部提供的 market_data_provider，
        否则返回空 dict（信号降级为零置信度）。
        """
        if self._market_data_provider is not None:
            try:
                return self._market_data_provider(trade_date)
            except Exception:
                logger.warning(
                    "[CrossAssetHook] market_data_provider 调用失败: %s",
                    trade_date, exc_info=True,
                )
        return {}
