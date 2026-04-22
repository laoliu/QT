/**
 * Portfolio Combo Backtest Service
 *
 * Calls the Python multi_strategy_backtest module to run a combined backtest
 * of the top-N strategies from the fitness analysis.  Returns a rich result
 * with equity curves, sub-strategy details, correlation matrix, and more.
 */
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

// ── Result types ─────────────────────────────────────────────────

export interface ComboSubResult {
  name: string;
  weight: number;
  annual_return: number;
  max_drawdown: number;
  sharpe: number;
  total_trades: number;
  total_fees: number;
}

export interface ComboEquityPoint {
  date: string;
  value: number;
}

export interface ComboDrawdownPoint {
  date: string;
  drawdown: number;
}

export interface ComboMonthlyReturn {
  month: string;
  return: number;
}

export interface ComboContribution {
  strategy_id: string;
  contribution_pct: number;
  weighted_return?: number;
}

export interface PortfolioComboBacktestResult {
  ok: boolean;
  error?: string;
  allocation_mode: string;
  rebalance_freq: string;
  period_days: number;
  initial_capital: number;
  top_n: number;
  strategies_used: string[];
  combined_metrics: {
    total_return: number;
    annualized_return: number;
    sharpe_ratio: number;
    max_drawdown: number;
    win_rate: number;
    calmar_ratio: number;
  };
  equity_curve: ComboEquityPoint[];
  sub_results: ComboSubResult[];
  sub_equity_curves: Record<string, ComboEquityPoint[]>;
  correlation_matrix: Record<string, Record<string, number>> | null;
  monthly_returns: ComboMonthlyReturn[];
  drawdown_curve: ComboDrawdownPoint[];
  contributions: ComboContribution[];
  errors?: string[];
}

export interface ComboBacktestOptions {
  batchId?: string;
  topN?: number;
  allocationMode?: string;
  rebalanceFreq?: string;
  periodDays?: number;
  initialCapital?: number;
  customWeights?: number[];
}

export interface ComboCompareResult {
  ok: boolean;
  comparisons: Record<string, PortfolioComboBacktestResult>;
}

export interface ApplyComboToPaperOptions {
  allocations: Array<{
    strategy_name: string;
    weight: number;
    latest_signals?: unknown[];
  }>;
  dryRun?: boolean;
  executePct?: number;
  createConditionalOrders?: boolean;
  trailingStopPct?: number;
}

export interface ApplyComboToPaperResult {
  ok: boolean;
  error?: string;
  [key: string]: unknown;
}

// ── Service functions ────────────────────────────────────────────

/**
 * Run a combined backtest of the top-N strategies from the latest fitness batch.
 */
export async function runTop10PortfolioBacktest(
  options: ComboBacktestOptions,
  config: QuantTradingConfig,
): Promise<PortfolioComboBacktestResult> {
  const result = await runPythonScript<PortfolioComboBacktestResult>(
    "aipha/backtest/multi_strategy_backtest.py",
    {
      action: "run_top10",
      batch_id: options.batchId ?? "",
      top_n: options.topN ?? 10,
      allocation_mode: options.allocationMode ?? "equal",
      rebalance_freq: options.rebalanceFreq ?? "monthly",
      period_days: options.periodDays ?? 252,
      initial_capital: options.initialCapital ?? 1_000_000,
      custom_weights: options.customWeights ?? null,
    },
    config,
    { timeoutMs: 10 * 60_000 },
  );

  if (!result.ok) {
    return {
      ok: false,
      error: result.error ?? "Python 脚本执行失败",
    } as PortfolioComboBacktestResult;
  }

  return result.data as PortfolioComboBacktestResult;
}

/**
 * Compare multiple allocation modes side-by-side.
 */
export async function compareAllocationModes(
  options: ComboBacktestOptions & { modes?: string[] },
  config: QuantTradingConfig,
): Promise<ComboCompareResult> {
  const result = await runPythonScript<ComboCompareResult>(
    "aipha/backtest/multi_strategy_backtest.py",
    {
      action: "compare_allocations",
      batch_id: options.batchId ?? "",
      top_n: options.topN ?? 10,
      modes: options.modes ?? ["equal", "risk_parity", "max_sharpe", "min_variance"],
      rebalance_freq: options.rebalanceFreq ?? "monthly",
      period_days: options.periodDays ?? 252,
      initial_capital: options.initialCapital ?? 1_000_000,
    },
    config,
    { timeoutMs: 30 * 60_000 },
  );

  if (!result.ok) {
    return { ok: false, comparisons: {} };
  }

  return result.data as ComboCompareResult;
}

/**
 * Apply a combo backtest result to paper trading via the recommend-execute pipeline.
 */
export async function applyComboToPaper(
  options: ApplyComboToPaperOptions,
  config: QuantTradingConfig,
): Promise<ApplyComboToPaperResult> {
  if (!options.allocations?.length) {
    return { ok: false, error: "需要 allocations 数组" };
  }

  const result = await runPythonScript<ApplyComboToPaperResult>(
    "scripts/recommend_execute.py",
    {
      dry_run: options.dryRun ?? true,
      execute_pct: options.executePct ?? 100,
      create_conditional_orders: options.createConditionalOrders ?? false,
      trailing_stop_pct: options.trailingStopPct ?? 5,
      source: "combo-backtest",
      allocations: options.allocations,
    },
    config,
    { timeoutMs: 60_000 },
  );

  if (!result.ok) {
    return { ok: false, error: result.error ?? "执行失败" };
  }

  return result.data as ApplyComboToPaperResult;
}
