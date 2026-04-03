import { runBacktest, listStrategies } from "../engine/strategy-registry.js";
/**
 * CLI command: openclaw quant backtest
 */
import type { QuantTradingConfig } from "../types.js";

export async function runCliBacktest(
  strategy: string,
  opts: {
    start?: string;
    end?: string;
    cash?: string;
    engine?: string;
    symbols?: string;
  },
  config: QuantTradingConfig,
): Promise<void> {
  const strategies = listStrategies();
  const found = strategies.find((s) => s.id === strategy);
  if (!found) {
    console.log(`Unknown strategy: ${strategy}`);
    console.log("Available strategies:");
    for (const s of strategies) {
      console.log(`  ${s.id} — ${s.name}`);
    }
    return;
  }

  console.log(`Running backtest: ${found.name}`);
  console.log(`  Symbols: ${opts.symbols ?? "000300"}`);
  console.log(`  Period: ${opts.start ?? "1y ago"} ~ ${opts.end ?? "today"}`);
  console.log(`  Cash: ¥${opts.cash ?? "100000"}`);
  console.log(`  Engine: ${opts.engine ?? config.backtestEngine ?? "builtin"}`);
  console.log("");

  try {
    const result = await runBacktest(
      {
        strategyId: strategy,
        symbols: (opts.symbols ?? "000300").split(","),
        startDate: opts.start ?? getDefaultStartDate(),
        endDate: opts.end ?? new Date().toISOString().slice(0, 10),
        initialCash: Number(opts.cash ?? 100000),
        engine: (opts.engine as "builtin" | "backtrader") ?? undefined,
      },
      config,
    );

    console.log("===== Backtest Results =====");
    console.log(`  Total Return:       ${result.totalReturn.toFixed(2)}%`);
    console.log(`  Annualized Return:  ${result.annualizedReturn.toFixed(2)}%`);
    console.log(`  Max Drawdown:       ${result.maxDrawdown.toFixed(2)}%`);
    console.log(`  Sharpe Ratio:       ${result.sharpeRatio.toFixed(3)}`);
    console.log(`  Win Rate:           ${result.winRate.toFixed(1)}%`);
    console.log(`  Total Trades:       ${result.totalTrades}`);
    console.log(`  Final Value:        ¥${result.finalValue.toFixed(2)}`);
  } catch (err) {
    console.error(`Backtest failed: ${(err as Error).message}`);
  }
}

function getDefaultStartDate(): string {
  const d = new Date();
  d.setFullYear(d.getFullYear() - 1);
  return d.toISOString().slice(0, 10);
}
