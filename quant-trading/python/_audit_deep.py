# -*- coding: utf-8 -*-
"""Cross-check all potentially missing modules."""
import sys

# 1. cb_factors
try:
    from aipha.factors.cb_factors import CBGreeks, CBValuation, CBMomentumFactor
    print("cb_factors: OK - CBGreeks, CBValuation, CBMomentumFactor")
except Exception as e:
    print(f"cb_factors: FAIL - {e}")

# 2. convertible_bond domain
try:
    from aipha.domain.convertible_bond import ConvertibleBond
    print("convertible_bond: OK")
except Exception as e:
    print(f"convertible_bond: FAIL - {e}")

# 3. strategy registry
try:
    from aipha.strategy.registry import StrategyRegistry
    sr = StrategyRegistry()
    print(f"StrategyRegistry: OK - {type(sr).__name__}")
except Exception as e:
    print(f"StrategyRegistry: FAIL - {e}")

# 4. All individual A-stock strategies
strats = [
    "dividend_quality", "earnings_momentum", "garp_select", "value_quality",
    "small_cap_quality", "pead_event", "northbound_enhanced", "sector_rotation",
    "vol_managed_value", "momentum_acceleration", "turnaround_play", "stable_compounder",
    "buyback_signal", "insider_buying", "high_delivery_expect", "index_enhance",
]
for s in strats:
    try:
        __import__(f"aipha.strategy.{s}")
        print(f"  strategy/{s}: OK")
    except Exception as e:
        print(f"  strategy/{s}: FAIL - {e}")

# 5. All models
models = [
    "lgbm_ranker", "gru_ranker", "gat_ranker", "master_model",
    "ensemble", "double_ensemble", "predictor", "retrain_pipeline",
    "drift_monitor", "feature_eng", "feature_groups", "ml_stock_selector",
]
for m in models:
    try:
        __import__(f"aipha.models.{m}")
        print(f"  models/{m}: OK")
    except Exception as e:
        print(f"  models/{m}: FAIL - {e}")

# 6. All risk modules
risks = [
    "constraints", "stop_loss", "degradation", "var",
    "euler_decomposition", "audit_log", "ml_risk_controls",
    "capacity_limit", "position_sizing",
]
for r in risks:
    try:
        __import__(f"aipha.risk.{r}")
        print(f"  risk/{r}: OK")
    except Exception as e:
        print(f"  risk/{r}: FAIL - {e}")

# 7. All portfolio modules
portfolios = [
    "optimizer", "covariance", "regime_detector",
    "vol_targeting", "strategy_combo", "compliance_engine",
]
for p in portfolios:
    try:
        __import__(f"aipha.portfolio.{p}")
        print(f"  portfolio/{p}: OK")
    except Exception as e:
        print(f"  portfolio/{p}: FAIL - {e}")

# 8. Check combined counts
import aipha.strategy.register_all
import aipha.factors.register_all
from aipha.strategy.registry import StrategyRegistry
from aipha.factors.registry import FactorRegistry
sr2 = StrategyRegistry()
fr2 = FactorRegistry()
print(f"\n=== TOTALS ===")
print(f"Registered strategies: {len(sr2.list_strategies())} -> {sr2.list_strategies()}")
print(f"Registered factors: {len(fr2.list_factors())} in {len(fr2.categories())} categories")

# 9. Check AlphQ-specific subsystems we planned
print("\n=== PLANNED SUBSYSTEMS ===")
subsystems = {
    "backtest": ["engine", "config_runner", "data_loader", "result_store", "html_report"],
    "config": ["config_models", "config_parser"],
    "data": ["provider_base", "qt_adapter", "cb_provider", "cache_utils", "calendar"],
    "domain": ["models", "convertible_bond"],
    "execution": ["fees", "fill_models", "slippage"],
    "factors": ["base", "registry", "register_all", "alpha101", "alpha158", "operators",
                "composite", "orthogonalization", "factor_analyzer", "rolling_ic", "core_factors", "cb_factors",
                "momentum", "value", "quality", "volatility", "size", "liquidity", "dividend", "growth", "technical"],
    "market": ["rules", "halt_checker"],
    "models": ["lgbm_ranker", "gru_ranker", "gat_ranker", "master_model", "ensemble",
               "double_ensemble", "predictor", "retrain_pipeline", "drift_monitor",
               "feature_eng", "feature_groups", "ml_stock_selector"],
    "monitoring": ["crowding_monitor", "factor_decay_monitor", "retrain_trigger"],
    "portfolio": ["optimizer", "covariance", "regime_detector", "vol_targeting",
                  "strategy_combo", "compliance_engine"],
    "reporting": ["brinson_attribution", "cost_attribution", "factor_exposure"],
    "risk": ["constraints", "stop_loss", "degradation", "var", "euler_decomposition",
             "audit_log", "ml_risk_controls", "capacity_limit", "position_sizing"],
    "runner": ["__init__", "scheduler"],
    "signal": ["aggregator", "signal_filter"],
    "strategy": ["base", "registry", "register_all", "equity_rotation_base", "cb_strategies",
                 "dividend_quality", "earnings_momentum", "garp_select", "value_quality",
                 "small_cap_quality", "pead_event", "northbound_enhanced", "sector_rotation",
                 "vol_managed_value", "momentum_acceleration", "turnaround_play", "stable_compounder",
                 "buyback_signal", "insider_buying", "high_delivery_expect", "index_enhance"],
    "tracker": ["signal_tracker", "position_tracker", "attribution_tracker"],
    "universe": ["policy", "exclusions"],
    "utils": ["metrics", "rank_utils", "vectorized"],
    "validation": ["stats", "walk_forward"],
}

import importlib
total_ok = 0
total_fail = 0
failures = []
for sub, modules in subsystems.items():
    for mod in modules:
        full = f"aipha.{sub}.{mod}"
        try:
            importlib.import_module(full)
            total_ok += 1
        except Exception as e:
            total_fail += 1
            failures.append(f"  {full}: {e}")

print(f"OK: {total_ok}, FAIL: {total_fail}")
if failures:
    print("FAILURES:")
    for f in failures:
        print(f)
else:
    print("ALL MODULES IMPORT SUCCESSFULLY!")
