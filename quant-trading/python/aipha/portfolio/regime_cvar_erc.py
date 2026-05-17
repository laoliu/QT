"""Regime-aware CVaR-ERC portfolio optimizer.

Adapts portfolio constraints and risk budgets based on market regime.
Combines Conditional VaR risk control with Equal Risk Contribution
objective, using regime-dependent parameters.

Design rationale:
- BULL: Favor aggressive sleeves (ML equity ↑), relaxed CVaR
- NEUTRAL: Balanced allocation, standard constraints
- BEAR: Favor defensive (CB/ETF ↑), tighter CVaR, optional deleveraging

依赖: cvxpy (required), scipy

学术依据:
- Maillard, Roncalli, Teïletche (2010) — ERC portfolios
- Rockafellar & Uryasev (2000) — CVaR optimization
- Ang & Bekaert (2002) — Regime switching & asset allocation
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from aipha.portfolio.regime_weight import MarketRegime

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------

@dataclass
class RegimeCVaRERCConfig:
    """Regime-aware CVaR-ERC optimizer configuration."""

    # CVaR confidence level
    cvar_alpha: float = 0.95

    # Regime-dependent limits
    cvar_limit_bull: float = 0.05
    cvar_limit_neutral: float = 0.03
    cvar_limit_bear: float = 0.02

    max_weight_bull: float = 0.45
    max_weight_neutral: float = 0.35
    max_weight_bear: float = 0.25

    min_weight: float = 0.05

    leverage_bull: float = 1.0
    leverage_neutral: float = 1.0
    leverage_bear: float = 0.70

    # ERC solver params
    erc_tol: float = 1e-6
    erc_max_iter: int = 200

    # Turnover constraint
    max_turnover: float = 0.30

    # Smoothing: EMA factor for weight transition
    ema_alpha: float = 0.3


# ------------------------------------------------------------------
# Core Optimizer
# ------------------------------------------------------------------

class RegimeCVaRERCOptimizer:
    """CVaR + ERC optimizer with regime-aware constraints.

    Solves:
        min Σ (RC_i - RC_target)^2
        s.t. sum(w) = leverage(regime)
             CVaR_α(w) ≤ cvar_limit(regime)
             w_min ≤ w_i ≤ w_max(regime)

    Usage:
        opt = RegimeCVaRERCOptimizer(config)
        weights = opt.optimize(
            symbols=["ml_csi2000", "cb_v4b", "csi300", "etf_regime"],
            returns=daily_returns_dict,
            regime=MarketRegime.BULL,
        )
    """

    def __init__(self, config: RegimeCVaRERCConfig | None = None) -> None:
        self._config = config or RegimeCVaRERCConfig()
        self._prev_weights: dict[str, float] | None = None

    def optimize(
        self,
        symbols: list[str],
        returns: dict[str, list[float] | np.ndarray],
        regime: MarketRegime,
        prev_weights: dict[str, float] | None = None,
    ) -> dict[str, float]:
        """Solve regime-aware CVaR-ERC optimization.

        Args:
            symbols: Component names (e.g., ['ml_csi2000', 'cb_v4b', ...]).
            returns: Dict of symbol → daily return series.
            regime: Current market regime.
            prev_weights: Previous period weights for turnover control.

        Returns:
            Dict of symbol → optimized weight.
        """
        cfg = self._config
        n = len(symbols)
        if n == 0:
            return {}
        if n == 1:
            return {symbols[0]: 1.0}

        cvar_limit, max_weight, leverage = self._get_regime_params(regime)

        # Build returns matrix (T x N)
        min_len = min(len(returns[s]) for s in symbols)
        ret_matrix = np.column_stack([
            np.asarray(returns[s])[-min_len:] for s in symbols
        ])

        # Covariance from returns
        cov_matrix = np.cov(ret_matrix, rowvar=False)
        # Ensure positive definiteness
        eigvals = np.linalg.eigvalsh(cov_matrix)
        if eigvals.min() < 1e-10:
            cov_matrix += np.eye(n) * 1e-8

        # Solve ERC with regime constraints
        raw_weights = self._solve_erc(
            cov_matrix, n, max_weight, cfg.min_weight, leverage,
        )

        # Check CVaR constraint
        cvar = self._compute_cvar(raw_weights, ret_matrix)
        if cvar > cvar_limit:
            # Scale down proportionally to meet CVaR limit
            scale = cvar_limit / cvar if cvar > 0 else 1.0
            raw_weights *= scale
            remainder = leverage - raw_weights.sum()
            if remainder > 0:
                # Distribute remainder equally
                raw_weights += remainder / n
            logger.warning(
                "CVaR %.4f exceeds limit %.4f for regime %s, scaled weights",
                cvar, cvar_limit, regime.value,
            )

        result = {sym: float(w) for sym, w in zip(symbols, raw_weights)}

        # Apply turnover constraint if previous weights available
        if prev_weights is not None:
            result = self._apply_turnover_constraint(result, prev_weights)

        self._prev_weights = result

        # Log risk contributions
        rc = self.get_risk_contributions(
            np.array([result[s] for s in symbols]), cov_matrix,
        )
        logger.info(
            "Regime %s: weights=%s, RC=%s, CVaR=%.4f",
            regime.value,
            {s: f"{result[s]:.3f}" for s in symbols},
            {s: f"{r:.4f}" for s, r in zip(symbols, rc)},
            cvar,
        )
        return result

    def _get_regime_params(
        self, regime: MarketRegime,
    ) -> tuple[float, float, float]:
        """Return (cvar_limit, max_weight, leverage) for regime."""
        cfg = self._config
        if regime == MarketRegime.BULL:
            return cfg.cvar_limit_bull, cfg.max_weight_bull, cfg.leverage_bull
        elif regime == MarketRegime.BEAR:
            return cfg.cvar_limit_bear, cfg.max_weight_bear, cfg.leverage_bear
        else:
            return cfg.cvar_limit_neutral, cfg.max_weight_neutral, cfg.leverage_neutral

    def _compute_cvar(
        self, weights: np.ndarray, returns_matrix: np.ndarray,
    ) -> float:
        """Compute CVaR at alpha confidence level (historical simulation)."""
        portfolio_returns = returns_matrix @ weights
        alpha = self._config.cvar_alpha
        var_threshold = np.percentile(portfolio_returns, (1 - alpha) * 100)
        tail = portfolio_returns[portfolio_returns <= var_threshold]
        if len(tail) == 0:
            return abs(var_threshold)
        return abs(float(np.mean(tail)))

    def _solve_erc(
        self,
        cov_matrix: np.ndarray,
        n: int,
        max_weight: float,
        min_weight: float,
        leverage: float,
    ) -> np.ndarray:
        """Solve ERC via Spinu (2013) iterative algorithm.

        Objective: minimize Σ_i Σ_j (w_i*(Σw)_i - w_j*(Σw)_j)^2
        """
        cfg = self._config
        # Initialize with equal weights
        w = np.full(n, leverage / n)

        for iteration in range(cfg.erc_max_iter):
            sigma_w = cov_matrix @ w
            rc = w * sigma_w  # risk contributions
            total_risk = np.sqrt(w @ sigma_w)

            if total_risk < 1e-12:
                break

            # Target: equal RC
            target_rc = total_risk**2 / n

            # Gradient: update weights proportional to RC deviation
            rc_normalized = rc / (total_risk**2) if total_risk > 0 else rc
            target = 1.0 / n
            gradient = rc_normalized - target

            # Update
            step = 0.5 / (iteration + 1)
            w_new = w * np.exp(-step * gradient * n)

            # Enforce constraints
            w_new = np.clip(w_new, min_weight, max_weight)
            w_new = w_new / w_new.sum() * leverage

            # Convergence check
            if np.max(np.abs(w_new - w)) < cfg.erc_tol:
                w = w_new
                break
            w = w_new

        return w

    def get_risk_contributions(
        self, weights: np.ndarray, cov_matrix: np.ndarray,
    ) -> np.ndarray:
        """Compute Euler risk contributions: RC_i = w_i * (Σw)_i / σ_p."""
        sigma_w = cov_matrix @ weights
        port_var = weights @ sigma_w
        if port_var < 1e-12:
            return np.zeros(len(weights))
        port_vol = np.sqrt(port_var)
        rc = weights * sigma_w / port_vol
        return rc

    def _apply_turnover_constraint(
        self,
        new_weights: dict[str, float],
        prev_weights: dict[str, float],
    ) -> dict[str, float]:
        """Limit turnover by blending with previous weights via EMA."""
        cfg = self._config
        all_symbols = set(new_weights) | set(prev_weights)

        # Compute raw turnover
        turnover = sum(
            abs(new_weights.get(s, 0) - prev_weights.get(s, 0))
            for s in all_symbols
        ) / 2

        if turnover <= cfg.max_turnover:
            return new_weights

        # Blend with EMA to reduce turnover
        alpha = cfg.ema_alpha
        blended = {}
        for s in all_symbols:
            old = prev_weights.get(s, 0)
            new = new_weights.get(s, 0)
            blended[s] = alpha * new + (1 - alpha) * old

        # Re-normalize
        total = sum(blended.values())
        if total > 0:
            blended = {s: v / total for s, v in blended.items()}

        logger.info(
            "Turnover %.3f exceeds limit %.3f, EMA-blended (alpha=%.2f)",
            turnover, cfg.max_turnover, alpha,
        )
        return blended
