"""Run V2b (6-model) ensemble backtest."""
import sys
import logging

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
print("Starting V2b backtest...", flush=True)

from aipha.backtest.config_runner import run_from_config

r = run_from_config("configs/backtest/ml_csi2000_ensemble_v2b_weighted.yml")
print("Backtest done", flush=True)

if isinstance(r, dict) and "summary" in r:
    s = r["summary"]
    ar = s.get("annualized_return", "?")
    sr = s.get("sharpe_ratio", "?")
    mdd = s.get("max_drawdown", "?")
    calmar = s.get("calmar_ratio", "?")
    print(f"AR={ar}, SR={sr}, MDD={mdd}, Calmar={calmar}")
else:
    print("Result type:", type(r))
