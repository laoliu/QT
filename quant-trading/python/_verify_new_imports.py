"""Batch import verification for all new aipha modules."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

modules = [
    # utils
    "aipha.utils",
    "aipha.utils.rank_utils",
    "aipha.utils.metrics",
    "aipha.utils.vectorized",
    # alpha158
    "aipha.factors.alpha158",
    # 9 category factors
    "aipha.factors.momentum",
    "aipha.factors.value",
    "aipha.factors.quality",
    "aipha.factors.volatility",
    "aipha.factors.size",
    "aipha.factors.liquidity",
    "aipha.factors.dividend",
    "aipha.factors.growth",
    "aipha.factors.technical",
    # factor registration
    "aipha.factors.register_all",
    # reporting
    "aipha.reporting",
    "aipha.reporting.brinson_attribution",
    "aipha.reporting.cost_attribution",
    "aipha.reporting.factor_exposure",
    # strategies
    "aipha.strategy.equity_rotation_base",
    "aipha.strategy.dividend_quality",
    "aipha.strategy.earnings_momentum",
    "aipha.strategy.garp_select",
    "aipha.strategy.value_quality",
    "aipha.strategy.small_cap_quality",
    "aipha.strategy.pead_event",
    "aipha.strategy.northbound_enhanced",
    "aipha.strategy.sector_rotation",
    "aipha.strategy.vol_managed_value",
    "aipha.strategy.momentum_acceleration",
    "aipha.strategy.turnaround_play",
    "aipha.strategy.stable_compounder",
    "aipha.strategy.index_enhance",
    "aipha.strategy.buyback_signal",
    "aipha.strategy.insider_buying",
    "aipha.strategy.high_delivery_expect",
    # strategy registration
    "aipha.strategy.register_all",
    # monitoring
    "aipha.monitoring",
    "aipha.monitoring.factor_decay_monitor",
    "aipha.monitoring.crowding_monitor",
    "aipha.monitoring.retrain_trigger",
    # universe
    "aipha.universe",
    "aipha.universe.exclusions",
    "aipha.universe.policy",
    # market supplement
    "aipha.market.halt_checker",
    # runner
    "aipha.runner",
    "aipha.runner.scheduler",
    # bridge engines
    "engines.aipha_screening_engine",
    "engines.aipha_monitor_engine",
]

ok = 0
fail = 0
for m in modules:
    try:
        __import__(m)
        print(f"  OK  {m}")
        ok += 1
    except Exception as e:
        print(f"FAIL  {m}: {e}")
        fail += 1

print(f"\n{'='*50}")
print(f"Total: {ok + fail} | OK: {ok} | FAIL: {fail}")
