"""Regime Advisor — hooks regime detection into orchestrator workflow.

Provides an OrchestratorHook implementation that:
1. Detects market regime on each trading day (on_day_start)
2. Adjusts component weights via RegimeCVaRERCOptimizer (on_pre_rebalance)
3. Logs regime state for audit trail

Usage:
    advisor = RegimeAdvisor(config)
    orchestrator = ComboOrchestrator(config, hooks=[advisor])
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from aipha.portfolio.regime_cvar_erc import RegimeCVaRERCConfig, RegimeCVaRERCOptimizer
from aipha.portfolio.regime_detector import DetectorMode, RegimeDetector, RegimeDetectorConfig
from aipha.portfolio.regime_weight import MarketRegime

logger = logging.getLogger(__name__)


@dataclass
class RegimeAdvisorConfig:
    """Configuration for regime advisor hook."""

    enabled: bool = True
    detector_mode: str = "rule"
    dynamic_allocation: bool = True
    log_dir: str = "data/paper_state/regime"

    # Detector params
    momentum_window: int = 60
    vol_window: int = 20

    # Optimizer params
    cvar_alpha: float = 0.95
    max_turnover: float = 0.30
    ema_alpha: float = 0.3

    # Returns lookback for covariance estimation
    returns_lookback: int = 120


class RegimeAdvisor:
    """OrchestratorHook for regime-aware dynamic allocation.

    Detects market regime and adjusts component weights before rebalancing.
    Compatible with ComboOrchestrator hook interface.
    """

    def __init__(self, config: RegimeAdvisorConfig | None = None) -> None:
        self._config = config or RegimeAdvisorConfig()
        self._detector = self._build_detector()
        self._optimizer = self._build_optimizer()
        self._current_regime: MarketRegime = MarketRegime.NEUTRAL
        self._regime_history: list[dict[str, Any]] = []
        self._prev_weights: dict[str, float] | None = None

    def _build_detector(self) -> RegimeDetector:
        """Build regime detector from config."""
        mode = DetectorMode(self._config.detector_mode)
        detector_cfg = RegimeDetectorConfig(
            mode=mode,
            momentum_window=self._config.momentum_window,
            vol_window=self._config.vol_window,
        )
        return RegimeDetector(detector_cfg)

    def _build_optimizer(self) -> RegimeCVaRERCOptimizer:
        """Build CVaR-ERC optimizer from config."""
        opt_cfg = RegimeCVaRERCConfig(
            cvar_alpha=self._config.cvar_alpha,
            max_turnover=self._config.max_turnover,
            ema_alpha=self._config.ema_alpha,
        )
        return RegimeCVaRERCOptimizer(opt_cfg)

    # ------------------------------------------------------------------
    # Hook interface methods
    # ------------------------------------------------------------------

    def on_day_start(self, trade_date: str, *, ctx: dict[str, Any] | None = None, **_extra: Any) -> None:
        """Detect regime at start of trading day."""
        if not self._config.enabled:
            return

        # Get market data for regime detection
        market_data = (ctx or {}).get("market_data")
        if market_data is not None:
            regime = self._detector.detect(market_data)
        else:
            # Fallback: use default neutral if no market data provided
            regime = MarketRegime.NEUTRAL

        self._current_regime = regime
        self._regime_history.append({
            "trade_date": trade_date,
            "regime": regime.value,
        })

        logger.info("Regime detected: %s for %s", regime.value, trade_date)

        # Persist to log
        self._save_regime_log(trade_date, regime)

    def on_pre_rebalance(self, trade_date: str, *, ctx: dict[str, Any] | None = None, **_extra: Any) -> None:
        """Adjust component weights based on detected regime."""
        if not self._config.enabled or not self._config.dynamic_allocation:
            return

        if ctx is None:
            return

        component_returns = ctx.get("component_returns")
        base_weights = ctx.get("base_weights")
        if component_returns is None or base_weights is None:
            logger.debug("No component returns/weights available for regime adjustment")
            return

        symbols = list(base_weights.keys())
        adjusted = self._optimizer.optimize(
            symbols=symbols,
            returns=component_returns,
            regime=self._current_regime,
            prev_weights=self._prev_weights,
        )

        self._prev_weights = adjusted

        # Store in context for orchestrator to use
        ctx["regime_adjusted_weights"] = adjusted
        ctx["current_regime"] = self._current_regime

        logger.info(
            "Regime-adjusted weights: %s (regime=%s)",
            {k: f"{v:.3f}" for k, v in adjusted.items()},
            self._current_regime.value,
        )

    def on_day_end(self, trade_date: str, *, ctx: dict[str, Any] | None = None, **_extra: Any) -> None:
        """Log regime state at end of day."""
        pass

    def on_error(self, component: str, error: Exception, *, ctx: dict[str, Any] | None = None, **_extra: Any) -> None:
        """Handle errors (no-op for regime advisor)."""
        pass

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def current_regime(self) -> MarketRegime:
        return self._current_regime

    @property
    def regime_history(self) -> list[dict[str, Any]]:
        return self._regime_history.copy()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_regime_log(self, trade_date: str, regime: MarketRegime) -> None:
        """Save regime detection result to disk."""
        log_dir = Path(self._config.log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"regime_{trade_date}.json"

        entry = {
            "trade_date": trade_date,
            "regime": regime.value,
            "detector_mode": self._config.detector_mode,
        }

        log_file.write_text(json.dumps(entry, indent=2, ensure_ascii=False))
