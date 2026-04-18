/**
 * Worker thread for running builtin strategy backtests in parallel.
 *
 * Receives batches of { strategyId, symbol, bars, config } via parentPort,
 * runs the backtest, and sends results back.
 */
import { parentPort, workerData } from "node:worker_threads";
import { runBuiltinBacktest } from "./builtin-engine.js";
import { resolveBuiltinStrategy, hasStrategyFactory } from "./strategy-registry.js";
import type { KlineBar, QuantTradingConfig, BacktestResult } from "../types.js";

interface WorkerTask {
  strategyId: string;
  symbol: string;
  bars: KlineBar[];
  startDate: string;
  endDate: string;
  initialCash: number;
}

interface WorkerResult {
  strategyId: string;
  symbol: string;
  result: BacktestResult | null;
  error?: string;
}

// A no-op provider that serves pre-loaded bars from the main thread
class PreloadedProvider {
  name = "preloaded";
  private barsMap = new Map<string, KlineBar[]>();

  setBars(symbol: string, bars: KlineBar[]) {
    this.barsMap.set(symbol, bars);
  }

  async getQuote() { return null; }
  async getKlines(symbol: string): Promise<KlineBar[]> {
    return this.barsMap.get(symbol) ?? [];
  }
  async searchSymbols() { return []; }
}

const config = workerData.config as QuantTradingConfig;
const provider = new PreloadedProvider();

parentPort!.on("message", async (msg: { type: string; tasks?: WorkerTask[]; }) => {
  if (msg.type === "batch") {
    const results: WorkerResult[] = [];
    for (const task of msg.tasks ?? []) {
      try {
        if (!hasStrategyFactory(task.strategyId)) {
          results.push({ strategyId: task.strategyId, symbol: task.symbol, result: null, error: "未注册" });
          continue;
        }
        // Set bars for this symbol in the provider
        provider.setBars(task.symbol, task.bars);
        const strategyFn = resolveBuiltinStrategy(task.strategyId);
        const result = await runBuiltinBacktest(
          strategyFn,
          {
            strategyId: task.strategyId,
            symbols: [task.symbol],
            startDate: task.startDate,
            endDate: task.endDate,
            initialCash: task.initialCash,
            interval: "1d",
            market: "cn_stock",
          },
          config,
          provider as any,
        );
        results.push({ strategyId: task.strategyId, symbol: task.symbol, result });
      } catch (err) {
        results.push({
          strategyId: task.strategyId,
          symbol: task.symbol,
          result: null,
          error: err instanceof Error ? err.message : String(err),
        });
      }
    }
    parentPort!.postMessage({ type: "results", results });
  } else if (msg.type === "exit") {
    process.exit(0);
  }
});

// Signal ready
parentPort!.postMessage({ type: "ready" });
