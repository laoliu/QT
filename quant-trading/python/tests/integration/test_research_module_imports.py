import subprocess
import sys

from aipha.utils.paths import PROJECT_ROOT


def test_repaired_research_modules_import_in_clean_process():
    code = """
import importlib
modules = (
    'aipha.backtest.tick_engine', 'aipha.backtest.tick_data_loader',
    'aipha.strategy.tick_strategies', 'aipha.factors.register_all',
    'aipha.screeners.auction_sniper_screener',
    'aipha.screeners.sector_hotspot_screener',
    'aipha.tracker.collection_orchestrator',
)
for name in modules:
    importlib.import_module(name)
from aipha.factors.registry import FactorRegistry
registry = FactorRegistry()
assert len(registry.list_factors()) >= 30
assert registry.create('momentum').lookback_days == 20
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=60, cwd=PROJECT_ROOT)
    assert result.returncode == 0, result.stderr
