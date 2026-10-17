"""Quick runner for cb_adaptive_factor backtests."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aipha.backtest.config_runner import run_from_config

config_path = sys.argv[1] if len(sys.argv) > 1 else "configs/backtest/cb_adaptive_factor_post2022.yml"
print(f"Running: {config_path}")

result = run_from_config(config_path)

# Access metrics from saved summary.json
import json
from pathlib import Path
summary_path = Path(result.output_dir) / "summary.json"
d = json.load(open(summary_path))
m = d["metrics"]

ar = m.get("annual_return", 0)
mdd = m.get("max_drawdown_pct", 0)
sr = m.get("sharpe_ratio", 0)
cal = m.get("calmar_ratio", 0)
sortino = m.get("sortino_ratio", 0)
avg_pos = m.get("avg_positions", 0)

print(f"\n{'='*50}")
print(f"AR={ar:.2%}  MDD={mdd:.2%}  SR={sr:.2f}  Calmar={cal:.2f}")
print(f"Sortino={sortino:.2f}  AvgPos={avg_pos:.1f}")
print(f"Output: {result.output_dir}")

yr = m.get("yearly_returns", {})
if yr:
    print(f"\nYearly Returns:")
    for y, r in sorted(yr.items()):
        print(f"  {y}: {r:+.2%}")
