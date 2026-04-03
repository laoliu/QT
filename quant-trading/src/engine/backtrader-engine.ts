import { checkPythonModule, runPythonScript } from "../bridge/python-bridge.js";
/**
 * Backtrader engine bridge — delegates backtesting to Python's backtrader.
 *
 * This supports the strategy pattern from the screenshot:
 * 年化476%，回撤6.61% (annualized 476%, max drawdown 6.61%)
 *
 * Strategies are defined as Python files following the backtrader convention.
 */
import type { BacktestRequest, BacktestResult, QuantTradingConfig } from "../types.js";

/**
 * Run a backtest using the Python backtrader engine.
 */
export async function runBacktraderBacktest(
  request: BacktestRequest,
  config: QuantTradingConfig,
): Promise<BacktestResult> {
  // Verify backtrader is installed
  const hasBacktrader = await checkPythonModule("backtrader", config);
  if (!hasBacktrader) {
    throw new Error(
      "backtrader is not installed. Run: pip install backtrader\n" +
        "Or set backtestEngine to 'builtin' in the quant-trading plugin config.",
    );
  }

  const result = await runPythonScript<BacktestResult>(
    "engines/backtrader_engine.py",
    {
      strategyId: request.strategyId,
      symbols: request.symbols,
      startDate: request.startDate,
      endDate: request.endDate,
      initialCash: request.initialCash,
      commission: request.commission ?? 0.0003,
      slippage: request.slippage ?? 0.001, // 0.1% default — consistent with builtin engine
      parameters: request.parameters ?? {},
      benchmark: request.benchmark,
      market: detectMarketHint(request.symbols),
    },
    config,
  );

  if (!result.ok || !result.data) {
    throw new Error(`Backtrader backtest failed: ${result.error ?? "unknown error"}`);
  }

  return result.data;
}

/**
 * List available backtrader strategies (scans the strategies directory).
 */
export async function listBacktraderStrategies(
  config: QuantTradingConfig,
): Promise<Array<{ id: string; name: string; description: string }>> {
  const result = await runPythonScript<Array<{ id: string; name: string; description: string }>>(
    "engines/backtrader_engine.py",
    { action: "list_strategies" },
    config,
  );
  return result.ok ? (result.data ?? []) : [];
}

/**
 * Detect market type from symbols for data-source routing.
 * Matches the logic in python/data_providers/provider_factory.py.
 */
function detectMarketHint(symbols: string[]): string | undefined {
  if (!symbols.length) return undefined;
  const sym = symbols[0];

  // 6-digit numeric → A-share
  if (/^\d{6}$/.test(sym)) return "cn_stock";
  // .HK suffix → Hong Kong
  if (/\.HK$/i.test(sym)) return "hk_stock";
  // Purely alphabetic or has . separator (e.g. AAPL, BRK.B) → US stock
  if (/^[A-Za-z.]+$/.test(sym)) return "us_stock";

  return undefined;
}
