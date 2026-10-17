"""Quick backtest runner - runs a config and prints key metrics."""
import sys
import json
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aipha.backtest.config_runner import run_from_config


def run_and_report(config_path: str, output_dir: str):
    print(f"Running: {config_path}")
    result = run_from_config(config_path, output_dir=output_dir)

    # Find summary.json
    summary_path = None
    if result:
        summary_path = result.get("summary_path")
    
    if not summary_path:
        # Try to find it in output_dir
        for root, dirs, files in os.walk(output_dir):
            if "summary.json" in files:
                summary_path = os.path.join(root, "summary.json")
                break
    
    if summary_path and os.path.exists(summary_path):
        with open(summary_path) as f:
            d = json.load(f)
        m = d["metrics"]
        print(f"\n{'='*50}")
        print(f"Strategy: {d.get('strategy_name', 'unknown')}")
        print(f"Period: {d.get('start_date', '?')} ~ {d.get('end_date', '?')}")
        print(f"Annual Return: {m['annualized_return']:.2%}")
        print(f"Max Drawdown:  {m['max_drawdown_pct']:.2%}")
        print(f"Sharpe Ratio:  {m['sharpe_ratio']:.2f}")
        print(f"Calmar Ratio:  {m.get('calmar_ratio', 0):.2f}")
        print(f"Win Rate:      {m.get('win_rate', 0):.2%}")
        print(f"Avg Positions: {m.get('avg_positions', 0):.1f}")
        print(f"Total Trades:  {m.get('total_trades', 0):.0f}")
        print(f"Final NAV:     {d.get('final_nav', 0):,.2f}")
        print(f"{'='*50}")
    else:
        print(f"No summary found. Result: {result.to_dict() if hasattr(result, 'to_dict') else result}")


if __name__ == "__main__":
    config = sys.argv[1] if len(sys.argv) > 1 else "configs/cb_strategies/double_low_plus_opt_7y.yml"
    out = sys.argv[2] if len(sys.argv) > 2 else "output/" + os.path.splitext(os.path.basename(config))[0]
    run_and_report(config, out)
