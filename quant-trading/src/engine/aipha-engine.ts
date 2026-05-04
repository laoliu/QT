/**
 * AIPHA Backtest Engine Bridge — routes backtests to the Python aipha engine.
 */
import { runPythonScript } from "../bridge/python-bridge.js";
import type { BacktestRequest, BacktestResult, QuantTradingConfig } from "../types.js";

/**
 * Run a backtest using the Python AIPHA backtest engine.
 * Supports all strategies registered in the Python aipha/strategy/registry.
 */
export async function runAiphaBacktest(
  request: BacktestRequest,
  config: QuantTradingConfig,
): Promise<BacktestResult> {
  const result = await runPythonScript<{
    strategyId?: string;
    startDate?: string;
    endDate?: string;
    initialCapital?: number;
    finalValue?: number;
    totalReturn?: number;
    annualizedReturn?: number;
    totalTrades?: number;
    totalFees?: number;
    sharpeRatio?: number;
    maxDrawdown?: number;
    winRate?: number;
    profitFactor?: number;
    dailySnapshots?: Array<Record<string, unknown>>;
    trades?: Array<Record<string, unknown>>;
  }>(
    "engines/aipha_backtest_engine.py",
    {
      action: "run",
      strategyId: request.strategyId,
      symbols: request.symbols,
      startDate: request.startDate,
      endDate: request.endDate,
      initialCash: request.initialCash ?? 100000,
      fillModel: "next_bar_open",
      slippageBps: Math.round((request.slippage ?? 0.001) * 10000),
      parameters: request.parameters ?? {},
    },
    config,
    { timeoutMs: 300_000 },
  );

  if (!result.ok || !result.data) {
    throw new Error(result.error ?? `AIPHA 回测失败: ${request.strategyId}`);
  }

  const d = result.data;
  return {
    strategyId: request.strategyId,
    symbols: request.symbols,
    startDate: request.startDate,
    endDate: request.endDate ?? "",
    initialCash: d.initialCapital ?? request.initialCash ?? 100000,
    finalValue: d.finalValue ?? d.initialCapital ?? 0,
    totalReturn: d.totalReturn ?? 0,
    annualizedReturn: d.annualizedReturn ?? 0,
    maxDrawdown: d.maxDrawdown ?? 0,
    sharpeRatio: d.sharpeRatio ?? 0,
    winRate: d.winRate ?? 0,
    profitFactor: d.profitFactor ?? 0,
    totalTrades: d.totalTrades ?? 0,
    signals: [],
    equityCurve: (d.dailySnapshots ?? []).map((snap) => ({
      date: String(snap["date"] ?? ""),
      value: Number(snap["equity"] ?? snap["value"] ?? 0),
    })),
  };
}
