"""验证所有 aipha 模块是否可以正常导入"""
import sys, os
os.chdir(os.path.dirname(os.path.abspath(__file__)))

modules = [
    "aipha.domain.models",
    "aipha.data.provider_base",
    "aipha.data.calendar",
    "aipha.data.cache_utils",
    "aipha.config.config_models",
    "aipha.config.config_parser",
    "aipha.factors.base",
    "aipha.factors.registry",
    "aipha.factors.core_factors",
    "aipha.strategy.base",
    "aipha.strategy.registry",
    "aipha.backtest.engine",
    "aipha.execution.fill_models",
    "aipha.execution.slippage",
    "aipha.execution.fees",
    "aipha.market.rules",
    "aipha.risk.constraints",
    "aipha.risk.var",
    "aipha.risk.audit_log",
    "aipha.validation.stats",
]
ok = 0
fail = 0
for m in modules:
    try:
        __import__(m)
        print(f"  OK  {m}")
        ok += 1
    except Exception as e:
        print(f" FAIL {m}: {e}")
        fail += 1
print(f"\nResult: {ok} OK, {fail} FAIL")
