import { checkPythonModule, runPythonScript } from "../bridge/python-bridge.js";
/**
 * VeighNa (vnpy) CTA backtest engine bridge — delegates backtesting to Python's vnpy engine.
 *
 * Supports vnpy-style CTA strategies:
 *   - vnpy-double-ma    — Double Moving Average crossover
 *   - vnpy-atr-rsi      — ATR-RSI combination
 *   - vnpy-boll-channel — Bollinger Channel breakout
 *   - vnpy-turtle        — Turtle Trading (Donchian)
 *   - vnpy-king-keltner  — King Keltner Channel
 *   - vnpy-multi-timeframe — Multi-timeframe trend following
 */
import type { BacktestRequest, BacktestResult, QuantTradingConfig } from "../types.js";

/**
 * Run a backtest using the vnpy CTA backtest engine.
 */
export async function runVnpyBacktest(
  request: BacktestRequest,
  config: QuantTradingConfig,
): Promise<BacktestResult> {
  const result = await runPythonScript<BacktestResult & { ok?: boolean; error?: string }>(
    "engines/vnpy_backtest_engine.py",
    {
      strategyId: request.strategyId,
      symbols: request.symbols,
      startDate: request.startDate,
      endDate: request.endDate,
      initialCash: request.initialCash,
      commission: request.commission ?? 0.0003,
      slippage: request.slippage ?? 0.001,
      parameters: request.parameters ?? {},
      benchmark: request.benchmark,
      market: detectMarketHint(request.symbols),
    },
    config,
  );

  if (!result.ok || !result.data) {
    throw new Error(`vnpy backtest failed: ${result.error ?? "unknown error"}`);
  }

  // The vnpy engine wraps results with ok/error at top level
  const data = result.data as BacktestResult & { ok?: boolean; error?: string };
  if (data.ok === false) {
    throw new Error(`vnpy backtest failed: ${data.error ?? "unknown error"}`);
  }

  return data;
}

/**
 * List available vnpy CTA strategies.
 */
export async function listVnpyStrategies(
  config: QuantTradingConfig,
): Promise<Array<{ id: string; name: string; description: string; category: string }>> {
  const result = await runPythonScript<
    Array<{ id: string; name: string; description: string; category: string }>
  >("engines/vnpy_backtest_engine.py", { action: "list_strategies" }, config);
  return result.ok ? (result.data ?? []) : [];
}

/**
 * Detect market type from symbols for data-source routing.
 */
function detectMarketHint(symbols: string[]): string | undefined {
  if (!symbols.length) return undefined;
  const sym = symbols[0];

  if (/^\d{6}$/.test(sym)) return "cn_stock";
  if (/\.HK$/i.test(sym)) return "hk_stock";
  if (/^[A-Za-z.]+$/.test(sym)) return "us_stock";

  return undefined;
}
