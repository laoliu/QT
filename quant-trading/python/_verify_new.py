import importlib, sys
sys.path.insert(0, '.')
modules = [
    'aipha.backtest.slippage_stress',
    'aipha.backtest.multi_strategy_backtest',
    'aipha.backtest.report',
    'aipha.backtest.validation',
    'aipha.backtest.robustness',
    'aipha.backtest.sensitivity',
    'aipha.runner.paper_engine',
    'aipha.runner.paper_replay',
    'aipha.monitor.notifiers',
    'aipha.factors.neutralize',
    'aipha.factors.industry_relative',
    'aipha.factors.analyst_factors',
    'aipha.factors.residual_momentum',
    'aipha.factors.rsrs_timing',
    'aipha.factors.quality_advanced',
    'aipha.factors.equity_factors',
    'aipha.factors.esg_factors',
    'aipha.portfolio.account',
    'aipha.portfolio.convex_optimizer',
    'aipha.portfolio.cross_asset_allocator',
    'aipha.reporting.alpha_decay',
    'aipha.reporting.pbo',
    'aipha.risk.barra_style_monitor',
    'aipha.risk.valuation_guard',
    'aipha.risk.crowding_limit',
    'aipha.monitoring.combo_health_checker',
    'aipha.monitoring.psi_calculator',
    'aipha.utils.compliance_config',
    'aipha.utils.reproducibility',
]
ok = 0; fail = 0
for m in modules:
    try:
        importlib.import_module(m)
        ok += 1
        print(f'  OK  {m}')
    except Exception as e:
        fail += 1
        print(f'FAIL  {m}: {e}')
print(f'\n{ok}/{ok+fail} OK, {fail} FAIL')
