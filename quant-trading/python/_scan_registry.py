import os, importlib, inspect, sys
sys.path.insert(0, '.')
from aipha.strategy.base import Strategy
strategy_dir = 'aipha/strategy'
files = sorted(f[:-3] for f in os.listdir(strategy_dir) if f.endswith('.py') and not f.startswith('__'))
already = {
    'dividend_quality','earnings_momentum','garp_select','value_quality',
    'small_cap_quality','pead_event','northbound_enhanced','sector_rotation',
    'vol_managed_value','momentum_acceleration','turnaround_play','stable_compounder',
    'index_enhance','buyback_signal','insider_buying','high_delivery_expect',
    'cb_strategies','registry','register_all','base',
    'equity_rotation_base','cb_rotation_base','cb_intraday_base',
    'equity_factors','factor_strategy','param_space','portfolio',
    'diversification','cb_enhanced_factors','adaptive_weight','regime_gate',
    'cb_orthogonal_factors','cb_pricing_bias','cb_delta_zone',
}
new_entries = []
for f in files:
    if f in already:
        continue
    mod = importlib.import_module(f'aipha.strategy.{f}')
    classes = [
        (name, cls) for name, cls in inspect.getmembers(mod, inspect.isclass)
        if issubclass(cls, Strategy) and cls.__module__ == mod.__name__
    ]
    for clsname, cls in classes:
        new_entries.append((f, clsname))
for modname, clsname in new_entries:
    print(f'{modname}|{clsname}')
