#!/usr/bin/env python3
"""
Tests for vol_models.py — SABR, SVI, Dupire local vol, IV interpolation, term structure.

Validates:
  - SABR Hagan formula ATM consistency
  - SABR calibration convergence to known parameters
  - SABR smile symmetry properties
  - SVI calibration & no-arbitrage constraints
  - Dupire local vol extraction with known flat surface
  - IV surface interpolation smoothness
  - Term structure classification
"""

import math
import sys
import os

import numpy as np
import pytest

# Add engines directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engines"))

from vol_models import (
    sabr_implied_vol,
    calibrate_sabr,
    sabr_smile,
    svi_total_variance,
    svi_implied_vol,
    calibrate_svi,
    dupire_local_vol,
    interpolate_iv_surface,
    vol_term_structure,
    run_vol_models_engine,
    bsm_price,
)


# ══════════════════════════════════════════════════════════════════════════════
# SABR Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestSABRImpliedVol:
    """Test Hagan's SABR approximation formula."""

    def test_atm_vol_approximation(self):
        """ATM implied vol should be close to α when β=1 (lognormal SABR)."""
        F, T = 100.0, 0.5
        alpha = 0.20
        beta = 1.0
        rho, nu = 0.0, 0.3

        vol = sabr_implied_vol(F, F, T, alpha, beta, rho, nu)
        # For β=1 and ATM, Hagan's formula gives σ ≈ α × (1 + correction × T)
        assert abs(vol - alpha) < 0.02, f"ATM vol {vol} should be close to α={alpha}"

    def test_negative_rho_creates_skew(self):
        """Negative ρ should produce higher OTM put vols (left skew)."""
        F, T = 100.0, 0.5
        alpha, beta, nu = 0.20, 0.5, 0.4
        rho = -0.5

        K_low = F * 0.85  # OTM put
        K_high = F * 1.15  # OTM call

        vol_low = sabr_implied_vol(F, K_low, T, alpha, beta, rho, nu)
        vol_high = sabr_implied_vol(F, K_high, T, alpha, beta, rho, nu)

        assert vol_low > vol_high, "Negative ρ should produce left skew (higher OTM put vols)"

    def test_positive_rho_creates_right_skew(self):
        """Positive ρ should produce higher OTM call vols."""
        F, T = 100.0, 0.5
        alpha, beta, nu = 0.20, 0.5, 0.4
        rho = 0.5

        K_low = F * 0.85
        K_high = F * 1.15

        vol_low = sabr_implied_vol(F, K_low, T, alpha, beta, rho, nu)
        vol_high = sabr_implied_vol(F, K_high, T, alpha, beta, rho, nu)

        assert vol_high > vol_low, "Positive ρ should produce right skew"

    def test_zero_nu_flat_smile(self):
        """When ν=0, the smile should be nearly flat (CEV model)."""
        F, T = 100.0, 0.5
        alpha, beta, rho = 0.20, 1.0, 0.0
        nu = 0.0

        vols = [sabr_implied_vol(F, F * m, T, alpha, beta, rho, nu) for m in [0.9, 0.95, 1.0, 1.05, 1.1]]
        vol_range = max(vols) - min(vols)

        assert vol_range < 0.005, f"ν=0 should produce nearly flat smile, range={vol_range}"

    def test_high_nu_convex_smile(self):
        """Higher ν should produce more pronounced smile curvature."""
        F, T = 100.0, 0.5
        alpha, beta, rho = 0.20, 0.5, 0.0

        # Low vol-of-vol
        smile_low = [sabr_implied_vol(F, F * m, T, alpha, beta, rho, 0.1) for m in [0.85, 1.0, 1.15]]
        curvature_low = smile_low[0] + smile_low[2] - 2 * smile_low[1]

        # High vol-of-vol
        smile_high = [sabr_implied_vol(F, F * m, T, alpha, beta, rho, 0.8) for m in [0.85, 1.0, 1.15]]
        curvature_high = smile_high[0] + smile_high[2] - 2 * smile_high[1]

        assert curvature_high > curvature_low, "Higher ν should create more smile curvature"

    def test_invalid_inputs(self):
        """Invalid inputs should return 0."""
        assert sabr_implied_vol(0, 100, 0.5, 0.2, 0.5, 0, 0.3) == 0.0
        assert sabr_implied_vol(100, 0, 0.5, 0.2, 0.5, 0, 0.3) == 0.0
        assert sabr_implied_vol(100, 100, 0, 0.2, 0.5, 0, 0.3) == 0.0


class TestSABRCalibration:
    """Test SABR calibration to market data."""

    def _generate_sabr_smile(self, F, T, alpha, beta, rho, nu, strikes):
        """Generate synthetic market data from known SABR parameters."""
        return [sabr_implied_vol(F, K, T, alpha, beta, rho, nu) for K in strikes]

    def test_recovery_of_known_parameters(self):
        """Calibration should recover parameters used to generate the smile."""
        F, T = 100.0, 0.5
        true_alpha, true_beta, true_rho, true_nu = 0.20, 0.5, -0.3, 0.4

        strikes = [F * m for m in [0.8, 0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15, 1.2]]
        market_vols = self._generate_sabr_smile(F, T, true_alpha, true_beta, true_rho, true_nu, strikes)

        result = calibrate_sabr(F, T, strikes, market_vols, beta=true_beta)
        assert result["ok"]

        data = result["data"]
        assert data["rmse"] < 0.001, f"RMSE {data['rmse']} too high"
        assert abs(data["alpha"] - true_alpha) < 0.02, f"α mismatch: {data['alpha']} vs {true_alpha}"
        assert abs(data["rho"] - true_rho) < 0.05, f"ρ mismatch: {data['rho']} vs {true_rho}"
        assert abs(data["nu"] - true_nu) < 0.05, f"ν mismatch: {data['nu']} vs {true_nu}"

    def test_calibration_fit_quality(self):
        """Calibrated smile should closely match market data."""
        F, T = 3500.0, 0.25  # S&P 500 index scale
        # Realistic equity smile
        strikes = [3150, 3250, 3350, 3400, 3450, 3500, 3550, 3600, 3650, 3750, 3850]
        market_vols = [0.25, 0.23, 0.21, 0.20, 0.195, 0.19, 0.188, 0.187, 0.19, 0.20, 0.22]

        result = calibrate_sabr(F, T, strikes, market_vols, beta=0.5)
        assert result["ok"]
        assert result["data"]["rmse"] < 0.01, f"RMSE {result['data']['rmse']} too high for realistic smile"

    def test_insufficient_data(self):
        """Should reject calibration with fewer than 3 points."""
        result = calibrate_sabr(100, 0.5, [95, 100], [0.2, 0.19], beta=0.5)
        assert not result["ok"]
        assert "3" in result["error"]


class TestSABRSmile:
    """Test SABR smile generation."""

    def test_smile_covers_range(self):
        """Generated smile should span the requested strike range."""
        result = sabr_smile(100, 0.5, 0.2, 0.5, -0.3, 0.4, n_points=20)
        strikes = [p["strike"] for p in result["smile"]]
        assert min(strikes) < 75
        assert max(strikes) > 125

    def test_custom_range(self):
        """Custom strike range should be respected."""
        result = sabr_smile(100, 0.5, 0.2, 0.5, -0.3, 0.4, strike_range=(90, 110), n_points=10)
        strikes = [p["strike"] for p in result["smile"]]
        assert strikes[0] >= 89.9
        assert strikes[-1] <= 110.1
        assert len(result["smile"]) == 10


# ══════════════════════════════════════════════════════════════════════════════
# SVI Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestSVIModel:
    """Test SVI parameterization and calibration."""

    def test_total_variance_minimum(self):
        """SVI total variance should have a minimum near k=m when ρ≈0."""
        a, b, rho, m, sigma = 0.04, 0.1, 0.0, 0.0, 0.1
        # Minimum should be at k ≈ m for ρ=0
        w_at_m = svi_total_variance(m, a, b, rho, m, sigma)
        w_away = svi_total_variance(m + 0.5, a, b, rho, m, sigma)
        assert w_at_m < w_away, "SVI minimum should be near k=m when ρ≈0"

    def test_positive_total_variance(self):
        """Total variance must be non-negative for no-arbitrage."""
        a, b, rho, m, sigma = 0.04, 0.1, -0.3, 0.0, 0.1
        for k in np.linspace(-1, 1, 100):
            w = svi_total_variance(k, a, b, rho, m, sigma)
            assert w >= -1e-10, f"Negative total variance w={w} at k={k}"

    def test_svi_calibration_recovery(self):
        """SVI calibration should fit synthetic data well."""
        F, T = 100.0, 0.5
        true_a, true_b, true_rho, true_m, true_sigma = 0.04, 0.08, -0.3, 0.0, 0.1

        strikes = [F * m for m in [0.75, 0.80, 0.85, 0.90, 0.95, 1.0, 1.05, 1.10, 1.15, 1.20, 1.25]]
        market_vols = []
        for K in strikes:
            k = math.log(K / F)
            w = svi_total_variance(k, true_a, true_b, true_rho, true_m, true_sigma)
            market_vols.append(math.sqrt(w / T))

        result = calibrate_svi(F, T, strikes, market_vols)
        assert result["ok"], f"SVI calibration failed: {result.get('error')}"
        assert result["data"]["rmse"] < 0.005, f"SVI RMSE {result['data']['rmse']} too high"

    def test_svi_insufficient_data(self):
        """Should reject calibration with fewer than 5 points."""
        result = calibrate_svi(100, 0.5, [90, 95, 100, 105], [0.25, 0.22, 0.20, 0.21])
        assert not result["ok"]


# ══════════════════════════════════════════════════════════════════════════════
# Dupire Local Volatility Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestDupireLocalVol:
    """Test Dupire local volatility extraction."""

    def test_flat_iv_gives_flat_local_vol(self):
        """If IV surface is flat, local vol should equal implied vol everywhere."""
        S = 100.0
        r, q = 0.02, 0.0
        flat_vol = 0.20

        strikes = [80, 90, 95, 100, 105, 110, 120]
        expiries = [0.1, 0.25, 0.5, 1.0]
        iv_matrix = [[flat_vol] * len(strikes) for _ in expiries]

        result = dupire_local_vol(S, r, q, strikes, expiries, iv_matrix)
        assert result["ok"], f"Dupire failed: {result.get('error')}"

        # Interior points (not boundary) should have local vol ≈ flat_vol
        interior = [
            p for p in result["data"]["surface"]
            if p["strike"] not in [strikes[0], strikes[-1]]
            and p["expiry"] not in [expiries[0], expiries[-1]]
        ]
        for point in interior:
            assert abs(point["localVol"] - flat_vol) < 0.05, (
                f"Local vol {point['localVol']} != flat vol {flat_vol} at K={point['strike']}, T={point['expiry']}"
            )

    def test_insufficient_data(self):
        """Should reject with too few expiries or strikes."""
        result = dupire_local_vol(100, 0.02, 0, [90, 100], [0.25], [[0.2, 0.2]])
        assert not result["ok"]

    def test_surface_has_correct_dimensions(self):
        """Surface should have n_expiries × n_strikes points."""
        S = 100.0
        strikes = [90, 95, 100, 105, 110]
        expiries = [0.25, 0.5, 1.0]
        iv_matrix = [[0.22, 0.21, 0.20, 0.21, 0.22]] * 3

        result = dupire_local_vol(S, 0.02, 0, strikes, expiries, iv_matrix)
        assert result["ok"]
        assert len(result["data"]["surface"]) == len(strikes) * len(expiries)


# ══════════════════════════════════════════════════════════════════════════════
# IV Surface Interpolation Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestIVInterpolation:
    """Test IV surface interpolation."""

    def test_interpolation_smoothness(self):
        """Interpolated surface should be smooth (no large vol jumps)."""
        strikes = [80, 90, 95, 100, 105, 110, 120]
        expiries = [0.1, 0.25, 0.5, 1.0]
        # Mild smile
        smile = [0.28, 0.24, 0.22, 0.20, 0.21, 0.23, 0.27]
        iv_matrix = [smile] * 4

        target_K = list(np.linspace(80, 120, 20))
        result = interpolate_iv_surface(strikes, expiries, iv_matrix, target_strikes=target_K)
        assert result["ok"]

        # Check no NaN or extremely large values
        for point in result["data"]["surface"]:
            assert 0 < point["impliedVol"] < 1.0, f"Unreasonable vol: {point['impliedVol']}"

    def test_interpolation_within_bounds(self):
        """Interpolated vols should be within reasonable range of input data."""
        strikes = [80, 90, 100, 110, 120]
        expiries = [0.25, 0.5, 1.0]
        iv_matrix = [
            [0.30, 0.25, 0.20, 0.22, 0.28],
            [0.28, 0.23, 0.19, 0.21, 0.26],
            [0.26, 0.22, 0.18, 0.20, 0.24],
        ]

        result = interpolate_iv_surface(strikes, expiries, iv_matrix)
        assert result["ok"]

        for point in result["data"]["surface"]:
            # Interpolated vol should be within ±50% of input range
            assert 0.05 < point["impliedVol"] < 0.50


# ══════════════════════════════════════════════════════════════════════════════
# Term Structure Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestTermStructure:
    """Test ATM volatility term structure analysis."""

    def test_contango_detection(self):
        """Upward-sloping term structure should be classified as contango."""
        F = 100.0
        expiries = [0.083, 0.25, 0.5, 1.0]
        atm_vols = [0.15, 0.18, 0.20, 0.22]  # increasing

        result = vol_term_structure(F, expiries, atm_vols)
        assert result["shape"] == "contango"

    def test_backwardation_detection(self):
        """Downward-sloping (inverted) term structure should be backwardation."""
        F = 100.0
        expiries = [0.083, 0.25, 0.5, 1.0]
        atm_vols = [0.30, 0.26, 0.22, 0.20]  # decreasing

        result = vol_term_structure(F, expiries, atm_vols)
        assert result["shape"] == "backwardation"

    def test_flat_detection(self):
        """Flat term structure should be classified as flat."""
        F = 100.0
        expiries = [0.083, 0.25, 0.5, 1.0]
        atm_vols = [0.20, 0.20, 0.20, 0.20]

        result = vol_term_structure(F, expiries, atm_vols)
        assert result["shape"] == "flat"

    def test_forward_vol_positive(self):
        """Forward volatility should be positive for non-arbitrage surface."""
        F = 100.0
        expiries = [0.1, 0.25, 0.5, 1.0]
        atm_vols = [0.18, 0.20, 0.21, 0.22]

        result = vol_term_structure(F, expiries, atm_vols)
        for ts in result["termStructure"]:
            if "forwardVol" in ts and ts["forwardVol"] is not None:
                assert ts["forwardVol"] > 0, "Forward vol must be positive"


# ══════════════════════════════════════════════════════════════════════════════
# Dispatcher Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestDispatcher:
    """Test run_vol_models_engine dispatcher."""

    def test_sabr_calibrate_dispatch(self):
        """SABR calibrate action should work through dispatcher."""
        F = 100.0
        strikes = [F * m for m in [0.85, 0.90, 0.95, 1.0, 1.05, 1.10, 1.15]]
        market_vols = [sabr_implied_vol(F, K, 0.5, 0.2, 0.5, -0.3, 0.4) for K in strikes]

        result = run_vol_models_engine({
            "action": "sabr_calibrate",
            "forwardPrice": F,
            "timeToExpiry": 0.5,
            "strikes": strikes,
            "marketVols": market_vols,
            "beta": 0.5,
        })
        assert result["ok"]
        assert result["data"]["rmse"] < 0.005

    def test_sabr_smile_dispatch(self):
        result = run_vol_models_engine({
            "action": "sabr_smile",
            "forwardPrice": 100,
            "timeToExpiry": 0.5,
            "alpha": 0.2,
            "beta": 0.5,
            "rho": -0.3,
            "nu": 0.4,
            "nPoints": 10,
        })
        assert result["ok"]
        assert len(result["data"]["smile"]) == 10

    def test_sabr_price_dispatch(self):
        result = run_vol_models_engine({
            "action": "sabr_price",
            "forwardPrice": 100,
            "strike": 105,
            "timeToExpiry": 0.5,
            "alpha": 0.2,
            "beta": 0.5,
            "rho": -0.3,
            "nu": 0.4,
            "riskFreeRate": 0.02,
            "optionType": "call",
        })
        assert result["ok"]
        assert result["data"]["sabrImpliedVol"] > 0
        assert result["data"]["bsmPrice"] > 0

    def test_svi_calibrate_dispatch(self):
        F, T = 100.0, 0.5
        strikes = [F * m for m in [0.80, 0.85, 0.90, 0.95, 1.0, 1.05, 1.10, 1.15, 1.20]]
        a, b, rho, m_p, sigma = 0.04, 0.08, -0.3, 0.0, 0.1
        market_vols = [math.sqrt(svi_total_variance(math.log(K / F), a, b, rho, m_p, sigma) / T) for K in strikes]

        result = run_vol_models_engine({
            "action": "svi_calibrate",
            "forwardPrice": F,
            "timeToExpiry": T,
            "strikes": strikes,
            "marketVols": market_vols,
        })
        assert result["ok"]

    def test_dupire_dispatch(self):
        result = run_vol_models_engine({
            "action": "dupire",
            "spotPrice": 100.0,
            "riskFreeRate": 0.02,
            "dividendYield": 0.0,
            "strikes": [90, 95, 100, 105, 110],
            "expiries": [0.25, 0.5, 1.0],
            "ivMatrix": [[0.22, 0.21, 0.20, 0.21, 0.22]] * 3,
        })
        assert result["ok"]
        assert len(result["data"]["surface"]) == 15

    def test_interpolate_dispatch(self):
        result = run_vol_models_engine({
            "action": "interpolate",
            "strikes": [80, 90, 100, 110, 120],
            "expiries": [0.25, 0.5, 1.0],
            "ivMatrix": [
                [0.28, 0.24, 0.20, 0.22, 0.26],
                [0.26, 0.22, 0.19, 0.21, 0.24],
                [0.24, 0.20, 0.18, 0.20, 0.22],
            ],
        })
        assert result["ok"]

    def test_term_structure_dispatch(self):
        result = run_vol_models_engine({
            "action": "term_structure",
            "forwardPrice": 100,
            "expiries": [0.1, 0.25, 0.5, 1.0],
            "atmVols": [0.18, 0.20, 0.21, 0.22],
        })
        assert result["ok"]
        assert result["data"]["shape"] in ("contango", "backwardation", "flat")

    def test_unknown_action(self):
        result = run_vol_models_engine({"action": "unknown_xyz"})
        assert not result["ok"]
        assert "unknown" in result["error"].lower()

    def test_missing_required_params(self):
        """Missing required params should return error, not crash."""
        result = run_vol_models_engine({
            "action": "sabr_calibrate",
            "forwardPrice": 0,
            "timeToExpiry": 0.5,
        })
        assert not result["ok"]
