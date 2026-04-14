"""验证本轮新增模块是否全部可导入"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

modules = [
    # Backtest advanced
    "aipha.backtest.quality_gates",
    "aipha.backtest.benchmark",
    "aipha.backtest.benchmark_registry",
    "aipha.backtest.engine_builder",
    "aipha.backtest.parallel_runner",
    # Execution gateway
    "aipha.execution.gateway",
    "aipha.execution.paper_gateway",
    "aipha.execution.credentials",
    "aipha.execution.capacity",
    "aipha.execution.trading_ops",
    # Runner
    "aipha.runner.state_manager",
    "aipha.runner.bar_buffer",
    "aipha.runner.live_runner",
    "aipha.runner.multi_strategy",
    # Monitor
    "aipha.monitor",
    "aipha.monitor.alerts",
    "aipha.monitor.metrics_collector",
    "aipha.monitor.agent_audit",
    "aipha.monitor.force_redeem_watcher",
    "aipha.monitor.dashboard",
    # Signal
    "aipha.signal.evaluator",
    # Data
    "aipha.data.rate_limiter",
    "aipha.data.health_check",
    "aipha.data.fallback_provider",
    # Reporting
    "aipha.reporting.factor_ic",
    "aipha.reporting.comparator",
    # Portfolio
    "aipha.portfolio.position_manager",
    # Risk
    "aipha.risk.position_reconciler",
]

ok, fail = 0, 0
for mod in modules:
    try:
        __import__(mod)
        print(f"  OK  {mod}")
        ok += 1
    except Exception as e:
        print(f" FAIL {mod}: {e}")
        fail += 1

print(f"\n{'='*50}")
print(f"结果: {ok} OK / {fail} FAIL / {len(modules)} TOTAL")
