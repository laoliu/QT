/**
 * LiveBacktestCompare — compares live/paper trading performance against
 * backtest results for the same strategy.
 *
 * Detects drift between what the backtest promised and what actually
 * happened in live trading. Provides:
 *   - Return comparison (backtest vs live)
 *   - Equity curve overlay data
 *   - Drawdown comparison
 *   - Execution quality metrics (slippage, fill rate)
 *   - Signal fidelity (how many backtest signals were actually executed)
 *   - Drift score (composite measure of live-vs-backtest divergence)
 */

import type { BacktestResult, TradingSignal, TradeRecord } from "../types.js";
import type { EquitySnapshot } from "./equity-history.js";

// ── Types ────────────────────────────────────────────────────

export interface LiveBacktestCompareRequest {
  /** Strategy ID to compare */
  strategyId: string;
  /** Date range for comparison (YYYY-MM-DD) */
  startDate: string;
  endDate: string;
  /** Backtest result to compare against */
  backtestResult: BacktestResult;
  /** Live equity snapshots from EquityHistory */
  liveEquity: EquitySnapshot[];
  /** Live trade records */
  liveTrades: TradeRecord[];
  /** Live signals emitted */
  liveSignals: TradingSignal[];
}

export interface ReturnComparison {
  /** Backtest total return (%) */
  backtestReturn: number;
  /** Live total return (%) */
  liveReturn: number;
  /** Difference: live - backtest (%) */
  returnDiff: number;
  /** Backtest annualized return (%) */
  backtestAnnualizedReturn: number;
  /** Live annualized return (%) — estimated from snapshot period */
  liveAnnualizedReturn: number;
}

export interface DrawdownComparison {
  /** Backtest max drawdown (%) */
  backtestMaxDrawdown: number;
  /** Live max drawdown (%) */
  liveMaxDrawdown: number;
  /** Difference: live - backtest (%). Positive means live had worse drawdown. */
  drawdownDiff: number;
}

export interface RiskComparison {
  /** Backtest Sharpe ratio */
  backtestSharpe: number;
  /** Live Sharpe ratio (estimated from daily returns) */
  liveSharpe: number;
  /** Backtest win rate */
  backtestWinRate: number;
  /** Live win rate */
  liveWinRate: number;
}

export interface ExecutionQuality {
  /** Number of signals generated in backtest */
  backtestSignalCount: number;
  /** Number of signals actually executed live */
  liveExecutedCount: number;
  /** Fill rate: liveExecutedCount / backtestSignalCount (0-1) */
  fillRate: number;
  /** Average slippage (%) between signal price and fill price */
  avgSlippagePct: number;
  /** Total trades in backtest vs live */
  backtestTrades: number;
  liveTrades: number;
}

export interface EquityCurveOverlay {
  /** Aligned data points for charting: date + backtest value + live value */
  points: Array<{
    date: string;
    backtestValue: number | null;
    liveValue: number | null;
  }>;
}

export type DriftSeverity = "low" | "moderate" | "high" | "critical";

export interface DriftAnalysis {
  /** Composite drift score (0-100). Higher = more divergence. */
  score: number;
  severity: DriftSeverity;
  /** Individual component scores */
  components: {
    returnDrift: number;
    drawdownDrift: number;
    executionDrift: number;
    riskDrift: number;
  };
  /** Actionable observations */
  observations: string[];
}

export interface LiveBacktestCompareResult {
  strategyId: string;
  period: { start: string; end: string };
  returnComparison: ReturnComparison;
  drawdownComparison: DrawdownComparison;
  riskComparison: RiskComparison;
  executionQuality: ExecutionQuality;
  equityCurveOverlay: EquityCurveOverlay;
  drift: DriftAnalysis;
}

// ── Implementation ──────────────────────────────────────────

/**
 * Compare live trading performance against a backtest result.
 */
export function compareLiveVsBacktest(
  req: LiveBacktestCompareRequest,
): LiveBacktestCompareResult {
  const { strategyId, startDate, endDate, backtestResult, liveEquity, liveTrades, liveSignals } =
    req;

  const returnComp = computeReturnComparison(backtestResult, liveEquity);
  const drawdownComp = computeDrawdownComparison(backtestResult, liveEquity);
  const riskComp = computeRiskComparison(backtestResult, liveEquity, liveTrades);
  const execQuality = computeExecutionQuality(backtestResult, liveTrades, liveSignals);
  const overlay = buildEquityCurveOverlay(backtestResult, liveEquity);
  const drift = computeDrift(returnComp, drawdownComp, riskComp, execQuality);

  return {
    strategyId,
    period: { start: startDate, end: endDate },
    returnComparison: returnComp,
    drawdownComparison: drawdownComp,
    riskComparison: riskComp,
    executionQuality: execQuality,
    equityCurveOverlay: overlay,
    drift,
  };
}

function computeReturnComparison(
  bt: BacktestResult,
  liveEquity: EquitySnapshot[],
): ReturnComparison {
  const sorted = [...liveEquity].sort((a, b) => a.date.localeCompare(b.date));
  let liveReturn = 0;
  let liveAnnualizedReturn = 0;

  if (sorted.length >= 2) {
    const first = sorted[0]!;
    const last = sorted[sorted.length - 1]!;
    if (first.equity > 0) {
      liveReturn = ((last.equity - first.equity) / first.equity) * 100;
      const days = Math.max(
        1,
        (new Date(last.date).getTime() - new Date(first.date).getTime()) / 86_400_000,
      );
      liveAnnualizedReturn = (Math.pow(1 + liveReturn / 100, 365 / days) - 1) * 100;
    }
  }

  return {
    backtestReturn: bt.totalReturn,
    liveReturn,
    returnDiff: liveReturn - bt.totalReturn,
    backtestAnnualizedReturn: bt.annualizedReturn,
    liveAnnualizedReturn,
  };
}

function computeDrawdownComparison(
  bt: BacktestResult,
  liveEquity: EquitySnapshot[],
): DrawdownComparison {
  const sorted = [...liveEquity].sort((a, b) => a.date.localeCompare(b.date));
  let liveMaxDrawdown = 0;

  if (sorted.length > 0) {
    let peak = -Infinity;
    for (const snap of sorted) {
      if (snap.equity > peak) peak = snap.equity;
      const dd = peak > 0 ? ((peak - snap.equity) / peak) * 100 : 0;
      if (dd > liveMaxDrawdown) liveMaxDrawdown = dd;
    }
  }

  return {
    backtestMaxDrawdown: bt.maxDrawdown,
    liveMaxDrawdown,
    drawdownDiff: liveMaxDrawdown - bt.maxDrawdown,
  };
}

function computeRiskComparison(
  bt: BacktestResult,
  liveEquity: EquitySnapshot[],
  liveTrades: TradeRecord[],
): RiskComparison {
  // Estimate live Sharpe from daily PnL
  const sorted = [...liveEquity].sort((a, b) => a.date.localeCompare(b.date));
  let liveSharpe = 0;

  if (sorted.length >= 5) {
    const dailyReturns: number[] = [];
    for (let i = 1; i < sorted.length; i++) {
      const prev = sorted[i - 1]!;
      if (prev.equity > 0) {
        dailyReturns.push((sorted[i]!.equity - prev.equity) / prev.equity);
      }
    }
    if (dailyReturns.length > 1) {
      const mean = dailyReturns.reduce((a, b) => a + b, 0) / dailyReturns.length;
      const variance =
        dailyReturns.reduce((a, r) => a + (r - mean) ** 2, 0) / (dailyReturns.length - 1);
      const std = Math.sqrt(variance);
      if (std > 0) {
        liveSharpe = (mean / std) * Math.sqrt(252);
      }
    }
  }

  // Live win rate from closed trades
  const closedTrades = liveTrades.filter((t) => t.exitPrice != null);
  const wins = closedTrades.filter((t) => t.netPnl > 0).length;
  const liveWinRate = closedTrades.length > 0 ? (wins / closedTrades.length) * 100 : 0;

  return {
    backtestSharpe: bt.sharpeRatio,
    liveSharpe,
    backtestWinRate: bt.winRate,
    liveWinRate,
  };
}

function computeExecutionQuality(
  bt: BacktestResult,
  liveTrades: TradeRecord[],
  liveSignals: TradingSignal[],
): ExecutionQuality {
  const backtestSignalCount = bt.signals.length;
  const liveExecutedCount = liveTrades.length;

  // Compute average slippage: compare signal price with actual fill price
  let totalSlippage = 0;
  let slippageCount = 0;

  for (const trade of liveTrades) {
    // Try to find the matching signal
    const matchingSignal = liveSignals.find(
      (s) =>
        s.symbol === trade.symbol &&
        s.strategyId === trade.strategyId &&
        Math.abs(s.timestamp - new Date(trade.entryTime).getTime()) < 5 * 60 * 1000,
    );
    if (matchingSignal && matchingSignal.price > 0) {
      const slippage =
        Math.abs(trade.entryPrice - matchingSignal.price) / matchingSignal.price;
      totalSlippage += slippage * 100;
      slippageCount++;
    }
  }

  return {
    backtestSignalCount,
    liveExecutedCount,
    fillRate: backtestSignalCount > 0 ? liveExecutedCount / backtestSignalCount : 0,
    avgSlippagePct: slippageCount > 0 ? totalSlippage / slippageCount : 0,
    backtestTrades: bt.totalTrades,
    liveTrades: liveTrades.length,
  };
}

function buildEquityCurveOverlay(
  bt: BacktestResult,
  liveEquity: EquitySnapshot[],
): EquityCurveOverlay {
  // Build a map of all dates
  const dateMap = new Map<
    string,
    { backtestValue: number | null; liveValue: number | null }
  >();

  // Normalize backtest equity to percentage of initial
  for (const point of bt.equityCurve) {
    dateMap.set(point.date, {
      backtestValue: bt.initialCash > 0 ? (point.value / bt.initialCash) * 100 : point.value,
      liveValue: null,
    });
  }

  // Normalize live equity to percentage of initial
  const sortedLive = [...liveEquity].sort((a, b) => a.date.localeCompare(b.date));
  const liveInitial = sortedLive.length > 0 ? sortedLive[0]!.equity : 1;

  for (const snap of sortedLive) {
    const existing = dateMap.get(snap.date);
    const liveValue = liveInitial > 0 ? (snap.equity / liveInitial) * 100 : 0;
    if (existing) {
      existing.liveValue = liveValue;
    } else {
      dateMap.set(snap.date, { backtestValue: null, liveValue });
    }
  }

  // Sort by date and return
  const points = [...dateMap.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, values]) => ({
      date,
      backtestValue: values.backtestValue,
      liveValue: values.liveValue,
    }));

  return { points };
}

function computeDrift(
  returnComp: ReturnComparison,
  drawdownComp: DrawdownComparison,
  riskComp: RiskComparison,
  execQuality: ExecutionQuality,
): DriftAnalysis {
  // Return drift: how much live return diverges from backtest
  const returnDrift = Math.min(
    100,
    Math.abs(returnComp.returnDiff) * 2,
  );

  // Drawdown drift: live drawdown worse than backtest
  const drawdownDrift = Math.min(
    100,
    drawdownComp.drawdownDiff > 0 ? drawdownComp.drawdownDiff * 3 : 0,
  );

  // Execution drift: based on fill rate and slippage
  const fillRatePenalty = (1 - execQuality.fillRate) * 50;
  const slippagePenalty = Math.min(50, execQuality.avgSlippagePct * 20);
  const executionDrift = Math.min(100, fillRatePenalty + slippagePenalty);

  // Risk drift: Sharpe ratio and win rate divergence
  const sharpeDiff = Math.abs(riskComp.backtestSharpe - riskComp.liveSharpe);
  const winRateDiff = Math.abs(riskComp.backtestWinRate - riskComp.liveWinRate);
  const riskDrift = Math.min(100, sharpeDiff * 20 + winRateDiff);

  // Composite score (weighted average)
  const score = Math.round(
    returnDrift * 0.35 + drawdownDrift * 0.25 + executionDrift * 0.25 + riskDrift * 0.15,
  );

  // Severity classification
  let severity: DriftSeverity;
  if (score <= 15) severity = "low";
  else if (score <= 35) severity = "moderate";
  else if (score <= 60) severity = "high";
  else severity = "critical";

  // Generate observations
  const observations: string[] = [];

  if (returnComp.returnDiff < -5) {
    observations.push(
      `实盘收益 (${returnComp.liveReturn.toFixed(1)}%) 显著低于回测 (${returnComp.backtestReturn.toFixed(1)}%)，差距 ${Math.abs(returnComp.returnDiff).toFixed(1)}%`,
    );
  } else if (returnComp.returnDiff > 5) {
    observations.push(
      `实盘收益 (${returnComp.liveReturn.toFixed(1)}%) 高于回测 (${returnComp.backtestReturn.toFixed(1)}%)，可能存在幸存者偏差`,
    );
  } else {
    observations.push(
      `收益偏差在可接受范围内 (差距 ${Math.abs(returnComp.returnDiff).toFixed(1)}%)`,
    );
  }

  if (drawdownComp.drawdownDiff > 5) {
    observations.push(
      `实盘最大回撤 (${drawdownComp.liveMaxDrawdown.toFixed(1)}%) 明显超过回测 (${drawdownComp.backtestMaxDrawdown.toFixed(1)}%)，注意风控`,
    );
  }

  if (execQuality.fillRate < 0.7) {
    observations.push(
      `信号执行率较低 (${(execQuality.fillRate * 100).toFixed(0)}%)，可能因流动性或价格偏移未成交`,
    );
  }

  if (execQuality.avgSlippagePct > 0.3) {
    observations.push(
      `平均滑点 ${execQuality.avgSlippagePct.toFixed(2)}% 偏高，建议优化下单时机`,
    );
  }

  if (sharpeDiff > 0.5) {
    observations.push(
      `Sharpe差异明显 (回测: ${riskComp.backtestSharpe.toFixed(2)}, 实盘: ${riskComp.liveSharpe.toFixed(2)})`,
    );
  }

  if (winRateDiff > 10) {
    observations.push(
      `胜率偏差 ${winRateDiff.toFixed(1)}% (回测: ${riskComp.backtestWinRate.toFixed(1)}%, 实盘: ${riskComp.liveWinRate.toFixed(1)}%)`,
    );
  }

  if (observations.length === 1 && severity === "low") {
    observations.push("策略实盘表现与回测基本一致，运行状态良好");
  }

  return {
    score,
    severity,
    components: {
      returnDrift: Math.round(returnDrift),
      drawdownDrift: Math.round(drawdownDrift),
      executionDrift: Math.round(executionDrift),
      riskDrift: Math.round(riskDrift),
    },
    observations,
  };
}
