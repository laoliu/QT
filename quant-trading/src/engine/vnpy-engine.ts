import { checkPythonModule, runPythonScript } from "../bridge/python-bridge.js";
import type { BacktestRequest, BacktestResult, QuantTradingConfig } from "../types.js";

/**
 * VN.py engine bridge — delegates backtesting to Python's vnpy.
 */
export async function runVnpyBacktest(
  request: BacktestRequest,
  config: QuantTradingConfig,
): Promise<BacktestResult> {
  const hasVnpy = await checkPythonModule("vnpy", config);
  if (!hasVnpy) {
    throw new Error(
      "vnpy is not installed. Run: pip install vnpy\n" +
        "Or set backtestEngine to 'builtin' in the quant-trading plugin config.",
    );
  }

  const result = await runPythonScript<BacktestResult>(
    "engines/vnpy_engine.py",
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
    },
    config,
  );

  if (!result.ok || !result.data) {
    throw new Error(`VN.py backtest failed: ${result.error ?? "unknown error"}`);
  }

  return result.data;
}
