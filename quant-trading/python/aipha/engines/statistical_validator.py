"""
Statistical Validation Engine — DSR, HLZ, PBO for backtest quality assurance.

Implements the three core statistical tests from the R113 evaluation framework:

1. DSR (Deflated Sharpe Ratio) — Bailey & Lopez de Prado (2014)
   Adjusts observed Sharpe for multiple testing bias. Key insight:
   If you tried N strategy variants, DSR accounts for the probability that
   the best SR arose by chance.

2. HLZ (Harvey-Liu-Zhu, 2016) — Multiple Testing Framework
   Penalizes t-statistic by √(2·ln(N)) where N is the number of independent trials.
   Requires t_adj > 2.0 for "significant" alpha.

3. PBO (Probability of Backtest Overfitting) — Bailey et al. (2015)
   Uses Combinatorially Symmetric Cross-Validation (CSCV) to estimate the
   probability that in-sample performance does not persist out-of-sample.

Usage:
    validator = StatisticalValidator(
        observed_sharpe=1.56,
        n_trials=8,
        n_years=5.87,
        daily_returns=[...],
    )

    dsr = validator.deflated_sharpe_ratio()
    hlz = validator.hlz_test()
    pbo = validator.pbo_test()
    report = validator.full_report()
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Optional

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR


# ── Mathematical Helpers ─────────────────────────────────────────


def _norm_cdf(x: float) -> float:
    """Cumulative distribution function of standard normal (approximation)."""
    # Abramowitz and Stegun approximation (|error| < 1.5e-7)
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p = 0.3275911
    sign = 1 if x >= 0 else -1
    x = abs(x)
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x / 2)
    return 0.5 * (1.0 + sign * y)


def _norm_ppf(p: float) -> float:
    """Inverse CDF (percent point function) of standard normal.
    Rational approximation by Peter Acklam."""
    if p <= 0:
        return -10.0
    if p >= 1:
        return 10.0
    if p == 0.5:
        return 0.0

    # Coefficients for rational approximation
    a = [-3.969683028665376e+01, 2.209460984245205e+02,
         -2.759285104469687e+02, 1.383577518672690e+02,
         -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02,
         -1.556989798598866e+02, 6.680131188771972e+01,
         -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01,
         -2.400758277161838e+00, -2.549732539343734e+00,
         4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01,
         2.445134137142996e+00, 3.754408661907416e+00]

    p_low = 0.02425
    p_high = 1 - p_low

    if p < p_low:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    elif p <= p_high:
        q = p - 0.5
        r = q * q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
               (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
    else:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)


def _euler_mascheroni() -> float:
    """Euler-Mascheroni constant γ ≈ 0.5772."""
    return 0.5772156649015329


# ── DSR: Deflated Sharpe Ratio ───────────────────────────────────


@dataclass
class DSRResult:
    """Result of Deflated Sharpe Ratio test."""
    observed_sr: float
    haircut_sr: float      # expected max SR under null hypothesis
    deflated_sr: float     # test statistic
    p_value: float
    n_trials: int
    n_observations: int
    skewness: float
    kurtosis: float
    significant: bool      # p < 0.05


def deflated_sharpe_ratio(
    observed_sr: float,
    sr_std: float,
    n_trials: int,
    n_observations: int,
    skewness: float = 0.0,
    excess_kurtosis: float = 0.0,
) -> DSRResult:
    """Compute the Deflated Sharpe Ratio.

    Bailey & Lopez de Prado (2014): "The Deflated Sharpe Ratio"

    The key insight: if you tried N strategy variants, the expected maximum
    SR under the null hypothesis (no skill) is E[max(SR)] which grows with N.
    DSR tests whether observed SR exceeds this expectation.

    Args:
        observed_sr: The observed Sharpe ratio of the strategy.
        sr_std: Standard deviation of the SR estimator ≈ sqrt((1 - skew*SR + (kurt-1)/4 * SR²) / (T-1)).
        n_trials: Number of independent strategy variants tested.
        n_observations: Number of return observations (trading days).
        skewness: Skewness of returns.
        excess_kurtosis: Excess kurtosis of returns (kurtosis - 3).

    Returns:
        DSRResult with p-value and significance.
    """
    if n_trials <= 0:
        n_trials = 1
    if n_observations < 2:
        return DSRResult(observed_sr, 0, 0, 1.0, n_trials, n_observations, skewness, excess_kurtosis, False)

    # Expected maximum SR under null: E[max(SR)] for N i.i.d. tries
    # Using approximation from Bailey & Lopez de Prado (2014):
    # E[max] ≈ sr_std * [(1 - γ) * Φ^{-1}(1 - 1/N) + γ * Φ^{-1}(1 - 1/(N*e))]
    # where γ is Euler-Mascheroni constant
    gamma = _euler_mascheroni()

    if n_trials == 1:
        haircut_sr = 0.0
    else:
        z1 = _norm_ppf(1 - 1.0 / n_trials)
        z2 = _norm_ppf(1 - 1.0 / (n_trials * math.e))
        haircut_sr = sr_std * ((1 - gamma) * z1 + gamma * z2)

    # SR standard error with non-normality correction
    # Var(SR) ≈ (1 - skew*SR + (kurt_excess)/4 * SR²) / (T - 1)
    sr_var = (1 - skewness * observed_sr + (excess_kurtosis / 4) * observed_sr ** 2) / max(n_observations - 1, 1)
    sr_se = math.sqrt(max(sr_var, 1e-10))

    # DSR test statistic
    if sr_se > 0:
        dsr_stat = (observed_sr - haircut_sr) / sr_se
    else:
        dsr_stat = 0.0

    p_value = 1.0 - _norm_cdf(dsr_stat)

    return DSRResult(
        observed_sr=round(observed_sr, 4),
        haircut_sr=round(haircut_sr, 4),
        deflated_sr=round(dsr_stat, 4),
        p_value=round(p_value, 4),
        n_trials=n_trials,
        n_observations=n_observations,
        skewness=round(skewness, 4),
        kurtosis=round(excess_kurtosis, 4),
        significant=p_value < 0.05,
    )


# ── HLZ: Harvey-Liu-Zhu Multiple Testing ────────────────────────


@dataclass
class HLZResult:
    """Result of Harvey-Liu-Zhu multiple testing adjustment."""
    t_observed: float
    penalty: float         # √(2·ln(N))
    t_adjusted: float
    n_trials: int
    significant: bool      # t_adj >= 2.0
    haircut_pct: float     # how much was "shaved off"


def hlz_test(
    observed_sr: float,
    n_years: float,
    n_trials: int,
) -> HLZResult:
    """Harvey-Liu-Zhu (2016) multiple testing framework.

    Key formula:
        t_obs = SR × √(n_years)
        penalty = √(2 × ln(N))
        t_adj = t_obs - penalty

    A strategy is "significant" when t_adj ≥ 2.0.

    Args:
        observed_sr: Annualized Sharpe ratio.
        n_years: Number of years of backtest data.
        n_trials: Number of independent strategy variants tried.

    Returns:
        HLZResult with adjusted t-statistic.
    """
    if n_years <= 0 or n_trials <= 0:
        return HLZResult(0, 0, 0, n_trials, False, 0)

    t_obs = observed_sr * math.sqrt(n_years)
    penalty = math.sqrt(2 * math.log(max(n_trials, 1)))
    t_adj = t_obs - penalty

    haircut = (penalty / t_obs * 100) if t_obs > 0 else 0

    return HLZResult(
        t_observed=round(t_obs, 4),
        penalty=round(penalty, 4),
        t_adjusted=round(t_adj, 4),
        n_trials=n_trials,
        significant=t_adj >= 2.0,
        haircut_pct=round(haircut, 2),
    )


# ── PBO: Probability of Backtest Overfitting ─────────────────────


@dataclass
class PBOResult:
    """Result of PBO (Probability of Backtest Overfitting) test."""
    pbo: float              # probability of overfitting [0, 1]
    logit_mean: float       # mean logit of performance degradation
    n_partitions: int       # number of CSCV partitions used
    significant: bool       # pbo < 0.50 means likely NOT overfitted
    is_overfitted: bool


def pbo_test(
    daily_returns: list[float],
    n_partitions: int = 10,
    seed: int = 42,
) -> PBOResult:
    """Probability of Backtest Overfitting via CSCV.

    Bailey, Borwein, Lopez de Prado, Zhu (2015):
    "Pseudomathematics and Financial Charlatanism"

    Uses Combinatorially Symmetric Cross-Validation:
    1. Split returns into S non-overlapping sub-periods
    2. For each combination of S/2 sub-periods as in-sample (IS):
       - Remaining S/2 are out-of-sample (OOS)
       - Compute IS Sharpe and OOS Sharpe
       - If IS optimization leads to OOS degradation → overfitting signal
    3. PBO = fraction of CSCV trials where OOS rank is below median

    For computational efficiency, we use a Monte Carlo approximation
    instead of enumerating all C(S, S/2) combinations.

    Args:
        daily_returns: Daily return series.
        n_partitions: Number of sub-periods to split data into (must be even).
        seed: Random seed for reproducibility.

    Returns:
        PBOResult with overfitting probability.
    """
    if len(daily_returns) < 60:
        return PBOResult(0.5, 0.0, 0, False, True)

    if n_partitions % 2 != 0:
        n_partitions += 1
    n_partitions = max(n_partitions, 4)

    # Split returns into n_partitions blocks
    block_size = len(daily_returns) // n_partitions
    if block_size < 5:
        n_partitions = max(len(daily_returns) // 5, 4)
        if n_partitions % 2 != 0:
            n_partitions -= 1
        block_size = len(daily_returns) // n_partitions

    blocks = []
    for i in range(n_partitions):
        start = i * block_size
        end = start + block_size if i < n_partitions - 1 else len(daily_returns)
        blocks.append(daily_returns[start:end])

    # Monte Carlo CSCV: randomly sample IS/OOS splits
    rng = random.Random(seed)
    n_mc = min(200, math.comb(n_partitions, n_partitions // 2))
    half = n_partitions // 2

    degradation_count = 0
    logit_values = []

    seen_splits = set()
    for _ in range(n_mc * 3):  # allow retries for unique splits
        if len(seen_splits) >= n_mc:
            break

        indices = list(range(n_partitions))
        rng.shuffle(indices)
        is_indices = tuple(sorted(indices[:half]))

        if is_indices in seen_splits:
            continue
        seen_splits.add(is_indices)

        oos_indices = [i for i in range(n_partitions) if i not in is_indices]

        # Compute IS and OOS Sharpe
        is_returns = []
        for idx in is_indices:
            is_returns.extend(blocks[idx])
        oos_returns = []
        for idx in oos_indices:
            oos_returns.extend(blocks[idx])

        is_sr = _sharpe_from_returns(is_returns)
        oos_sr = _sharpe_from_returns(oos_returns)

        # Degradation: OOS worse than IS
        if oos_sr < is_sr:
            degradation_count += 1

        # Logit of relative performance
        # λ = ln(oos_rank / is_rank) but we simplify to SR comparison
        if is_sr != 0:
            ratio = oos_sr / is_sr if is_sr > 0 else -oos_sr / abs(is_sr)
            # Bound ratio to avoid extreme logits
            ratio = max(0.01, min(ratio, 100))
            logit_values.append(math.log(ratio))

    n_actual = len(seen_splits)
    if n_actual == 0:
        return PBOResult(0.5, 0.0, n_partitions, False, True)

    pbo = degradation_count / n_actual
    logit_mean = sum(logit_values) / len(logit_values) if logit_values else 0

    return PBOResult(
        pbo=round(pbo, 4),
        logit_mean=round(logit_mean, 4),
        n_partitions=n_partitions,
        significant=pbo < 0.50,
        is_overfitted=pbo >= 0.50,
    )


def _sharpe_from_returns(returns: list[float], rf_annual: float = 0.02) -> float:
    """Compute annualized Sharpe from a return series."""
    if len(returns) < 2:
        return 0.0
    rf_daily = rf_annual / TRADING_DAYS_PER_YEAR
    excess = [r - rf_daily for r in returns]
    mean_e = sum(excess) / len(excess)
    var = sum((e - mean_e) ** 2 for e in excess) / (len(excess) - 1)
    std = math.sqrt(var) if var > 0 else 1e-8
    return (mean_e / std) * math.sqrt(TRADING_DAYS_PER_YEAR)


# ── Unified Validator ────────────────────────────────────────────


class StatisticalValidator:
    """Unified interface for DSR + HLZ + PBO statistical validation.

    Mirrors the R113 evaluation framework's Section P8 validation.
    """

    def __init__(
        self,
        daily_returns: list[float],
        observed_sharpe: Optional[float] = None,
        n_trials: int = 8,
        n_years: Optional[float] = None,
        risk_free_rate: float = 0.02,
    ):
        self.daily_returns = daily_returns
        self.n_trials = n_trials
        self.risk_free_rate = risk_free_rate

        # Compute SR from returns if not provided
        if observed_sharpe is not None:
            self.observed_sr = observed_sharpe
        else:
            self.observed_sr = _sharpe_from_returns(daily_returns, risk_free_rate)

        # Compute years from data if not provided
        if n_years is not None:
            self.n_years = n_years
        else:
            self.n_years = len(daily_returns) / float(TRADING_DAYS_PER_YEAR)

        # Pre-compute return statistics
        n = len(daily_returns)
        if n >= 3:
            mean = sum(daily_returns) / n
            m2 = sum((r - mean) ** 2 for r in daily_returns) / n
            m3 = sum((r - mean) ** 3 for r in daily_returns) / n
            m4 = sum((r - mean) ** 4 for r in daily_returns) / n
            std = math.sqrt(m2) if m2 > 0 else 1e-8

            self.skewness = m3 / (std ** 3) if std > 0 else 0
            self.excess_kurtosis = (m4 / (std ** 4)) - 3 if std > 0 else 0
        else:
            self.skewness = 0.0
            self.excess_kurtosis = 0.0

    def run_dsr(self) -> DSRResult:
        """Run Deflated Sharpe Ratio test."""
        n_obs = len(self.daily_returns)
        # SR standard deviation
        sr_var = (1 - self.skewness * self.observed_sr +
                  (self.excess_kurtosis / 4) * self.observed_sr ** 2) / max(n_obs - 1, 1)
        sr_std = math.sqrt(max(sr_var, 1e-10))

        return deflated_sharpe_ratio(
            observed_sr=self.observed_sr,
            sr_std=sr_std,
            n_trials=self.n_trials,
            n_observations=n_obs,
            skewness=self.skewness,
            excess_kurtosis=self.excess_kurtosis,
        )

    def run_hlz(self) -> HLZResult:
        """Run Harvey-Liu-Zhu multiple testing test."""
        return hlz_test(
            observed_sr=self.observed_sr,
            n_years=self.n_years,
            n_trials=self.n_trials,
        )

    def run_pbo(self, n_partitions: int = 10, seed: int = 42) -> PBOResult:
        """Run Probability of Backtest Overfitting test."""
        return pbo_test(
            daily_returns=self.daily_returns,
            n_partitions=n_partitions,
            seed=seed,
        )

    def full_report(self, n_partitions: int = 10) -> dict:
        """Generate complete statistical validation report (R113 P8 style)."""
        dsr = self.run_dsr()
        hlz = self.run_hlz()
        pbo = self.run_pbo(n_partitions)

        # Overall verdict
        dsr_pass = dsr.significant
        hlz_pass = hlz.significant
        pbo_pass = pbo.significant

        # Score: DSR is most important (R113 treated it as blocking)
        score = 0
        if dsr_pass:
            score += 4  # DSR is a "one-vote veto" item
        if hlz_pass:
            score += 3
        if pbo_pass:
            score += 3

        if score >= 7:
            verdict = "STRONG"
        elif score >= 4:
            verdict = "ACCEPTABLE"
        else:
            verdict = "WEAK"

        return {
            "summary": {
                "observed_sharpe": round(self.observed_sr, 4),
                "n_trials": self.n_trials,
                "n_years": round(self.n_years, 2),
                "n_observations": len(self.daily_returns),
                "skewness": round(self.skewness, 4),
                "excess_kurtosis": round(self.excess_kurtosis, 4),
                "verdict": verdict,
                "score": f"{score}/10",
            },
            "dsr": {
                "observed_sr": dsr.observed_sr,
                "haircut_sr": dsr.haircut_sr,
                "p_value": dsr.p_value,
                "significant": dsr.significant,
                "verdict": "✅ PASS" if dsr.significant else "❌ FAIL",
            },
            "hlz": {
                "t_observed": hlz.t_observed,
                "penalty": hlz.penalty,
                "t_adjusted": hlz.t_adjusted,
                "significant": hlz.significant,
                "verdict": "✅ PASS" if hlz.significant else "❌ FAIL (t_adj < 2.0)",
                "note": (
                    f"Need {math.ceil((2.0 + hlz.penalty) ** 2 / self.observed_sr ** 2)} years for significance"
                    if not hlz.significant and self.observed_sr > 0
                    else ""
                ),
            },
            "pbo": {
                "pbo": pbo.pbo,
                "logit_mean": pbo.logit_mean,
                "is_overfitted": pbo.is_overfitted,
                "verdict": "✅ PASS (not overfitted)" if pbo.significant else "❌ FAIL (likely overfitted)",
            },
        }
