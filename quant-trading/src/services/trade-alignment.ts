/**
 * TradeAlignment — aligns live trading records with backtest signals.
 *
 * Use cases:
 *   - Compare actual execution vs backtest predictions
 *   - Measure slippage, timing drift, and execution quality
 *   - Identify missed signals (backtest_only) and unplanned trades (live_only)
 *   - Compute PnL correlation between live and backtest
 *
 * Matching algorithm:
 *   1. Group live trades and backtest signals by symbol
 *   2. For each symbol, sort both lists by time
 *   3. Greedy nearest-neighbor match within time + price tolerance
 *   4. Classify unmatched records as live_only / backtest_only
 */

import { randomUUID } from "node:crypto";
import type {
  AlignedTradePair,
  AlignedTradeRecordPair,
  AlignmentConfig,
  AlignmentMatchType,
  AlignmentResult,
  AlignmentSummary,
  BacktestResult,
  TradeRecord,
  TradeRecordAlignmentResult,
  TradingSignal,
} from "../types.js";

// ── Defaults ────────────────────────────────────────────────────

const DEFAULT_TIME_TOLERANCE_MS = 86_400_000; // 1 day
const DEFAULT_PRICE_TOLERANCE_RATIO = 0.05; // 5%

function resolveConfig(config?: AlignmentConfig): Required<AlignmentConfig> {
  return {
    timeToleranceMs: config?.timeToleranceMs ?? DEFAULT_TIME_TOLERANCE_MS,
    priceToleranceRatio: config?.priceToleranceRatio ?? DEFAULT_PRICE_TOLERANCE_RATIO,
    includeUnexecuted: config?.includeUnexecuted ?? true,
  };
}

// ── Helpers ─────────────────────────────────────────────────────

function toTimestamp(dateStr: string | number): number {
  if (typeof dateStr === "number") return dateStr;
  return new Date(dateStr).getTime();
}

/** Normalize direction from OrderSide to simple buy/sell. */
function normalizeSide(side: string): "buy" | "sell" {
  if (side === "buy" || side === "buy_open" || side === "buy_close" || side === "cover") {
    return "buy";
  }
  return "sell";
}

/** Group an array by a key function. */
function groupBy<T>(items: T[], keyFn: (item: T) => string): Map<string, T[]> {
  const map = new Map<string, T[]>();
  for (const item of items) {
    const key = keyFn(item);
    let arr = map.get(key);
    if (!arr) {
      arr = [];
      map.set(key, arr);
    }
    arr.push(item);
  }
  return map;
}

/** Pearson correlation coefficient for two equal-length number arrays. */
function pearsonCorrelation(xs: number[], ys: number[]): number {
  const n = xs.length;
  if (n < 2) return 0;

  let sumX = 0;
  let sumY = 0;
  let sumXY = 0;
  let sumX2 = 0;
  let sumY2 = 0;
  for (let i = 0; i < n; i++) {
    sumX += xs[i]!;
    sumY += ys[i]!;
    sumXY += xs[i]! * ys[i]!;
    sumX2 += xs[i]! * xs[i]!;
    sumY2 += ys[i]! * ys[i]!;
  }

  const denom = Math.sqrt((n * sumX2 - sumX * sumX) * (n * sumY2 - sumY * sumY));
  if (denom === 0) return 0;
  return (n * sumXY - sumX * sumY) / denom;
}

// ── Core alignment ──────────────────────────────────────────────

interface LiveTradeEntry {
  trade: TradeRecord;
  timestamp: number;
  matched: boolean;
}

interface BacktestSignalEntry {
  signal: TradingSignal;
  timestamp: number;
  matched: boolean;
}

/**
 * Align live trades against backtest signals for a single symbol.
 * Uses greedy nearest-neighbor matching sorted by time.
 */
function alignSymbol(
  liveTrades: TradeRecord[],
  backtestSignals: TradingSignal[],
  cfg: Required<AlignmentConfig>,
): AlignedTradePair[] {
  const pairs: AlignedTradePair[] = [];

  const liveEntries: LiveTradeEntry[] = liveTrades
    .map((trade) => ({
      trade,
      timestamp: toTimestamp(trade.entryTime),
      matched: false,
    }))
    .sort((a, b) => a.timestamp - b.timestamp);

  const btEntries: BacktestSignalEntry[] = backtestSignals
    .map((signal) => ({
      signal,
      timestamp: toTimestamp(signal.timestamp),
      matched: false,
    }))
    .sort((a, b) => a.timestamp - b.timestamp);

  // Greedy match: for each live trade, find closest unmatched backtest signal
  for (const le of liveEntries) {
    let bestIdx = -1;
    let bestDelta = Infinity;
    const liveSide = normalizeSide(le.trade.side);

    for (let j = 0; j < btEntries.length; j++) {
      const be = btEntries[j]!;
      if (be.matched) continue;

      // Direction must match
      const btSide = normalizeSide(be.signal.direction);
      if (liveSide !== btSide) continue;

      const timeDelta = Math.abs(le.timestamp - be.timestamp);
      if (timeDelta > cfg.timeToleranceMs) continue;

      // Price tolerance check
      if (le.trade.entryPrice > 0 && be.signal.price > 0) {
        const priceDeviation = Math.abs(le.trade.entryPrice - be.signal.price) / be.signal.price;
        if (priceDeviation > cfg.priceToleranceRatio) continue;
      }

      if (timeDelta < bestDelta) {
        bestDelta = timeDelta;
        bestIdx = j;
      }
    }

    if (bestIdx >= 0) {
      const be = btEntries[bestIdx]!;
      be.matched = true;
      le.matched = true;

      const timeDeltaMs = le.timestamp - be.timestamp;
      const priceSlippage =
        be.signal.price > 0
          ? (le.trade.entryPrice - be.signal.price) / be.signal.price
          : 0;

      // Determine match type
      const matchType: AlignmentMatchType =
        Math.abs(timeDeltaMs) <= cfg.timeToleranceMs / 10 ? "exact" : "time_shifted";

      pairs.push({
        id: randomUUID().slice(0, 12),
        matchType,
        symbol: le.trade.symbol,
        liveTrade: le.trade,
        backtestSignal: be.signal,
        backtestPrice: be.signal.price,
        backtestTimestamp: be.timestamp,
        timeDeltaMs,
        priceSlippage,
        pnlDelta: le.trade.netPnl,
      });
    }
  }

  // Collect unmatched live trades
  for (const le of liveEntries) {
    if (le.matched) continue;
    pairs.push({
      id: randomUUID().slice(0, 12),
      matchType: "live_only",
      symbol: le.trade.symbol,
      liveTrade: le.trade,
    });
  }

  // Collect unmatched backtest signals
  if (cfg.includeUnexecuted) {
    for (const be of btEntries) {
      if (be.matched) continue;
      pairs.push({
        id: randomUUID().slice(0, 12),
        matchType: "backtest_only",
        symbol: be.signal.symbol,
        backtestSignal: be.signal,
        backtestPrice: be.signal.price,
        backtestTimestamp: be.timestamp,
      });
    }
  }

  return pairs;
}

// ── Summary computation ─────────────────────────────────────────

function computeSummary(pairs: AlignedTradePair[]): AlignmentSummary {
  let exactMatches = 0;
  let timeShiftedMatches = 0;
  let liveOnlyTrades = 0;
  let backtestOnlySignals = 0;

  const matchedTimeDeltasAbs: number[] = [];
  const matchedSlippages: number[] = [];
  const matchedPnlDeltas: number[] = [];
  const livePnls: number[] = [];
  const btEstPnls: number[] = [];

  for (const p of pairs) {
    switch (p.matchType) {
      case "exact":
        exactMatches++;
        break;
      case "time_shifted":
        timeShiftedMatches++;
        break;
      case "live_only":
        liveOnlyTrades++;
        break;
      case "backtest_only":
        backtestOnlySignals++;
        break;
    }

    if (p.matchType === "exact" || p.matchType === "time_shifted") {
      if (p.timeDeltaMs != null) matchedTimeDeltasAbs.push(Math.abs(p.timeDeltaMs));
      if (p.priceSlippage != null) matchedSlippages.push(p.priceSlippage);
      if (p.pnlDelta != null) matchedPnlDeltas.push(p.pnlDelta);
      if (p.liveTrade) livePnls.push(p.liveTrade.netPnl);
      // For correlation, use 0 as backtest PnL estimate when unavailable
      btEstPnls.push(0);
    }
  }

  const matched = exactMatches + timeShiftedMatches;
  const avg = (arr: number[]) => (arr.length > 0 ? arr.reduce((a, b) => a + b, 0) / arr.length : 0);

  return {
    totalPairs: pairs.length,
    exactMatches,
    timeShiftedMatches,
    liveOnlyTrades,
    backtestOnlySignals,
    avgTimeDeltaMs: avg(matchedTimeDeltasAbs),
    avgPriceSlippage: avg(matchedSlippages),
    avgPnlDelta: avg(matchedPnlDeltas),
    executionRate: matched + backtestOnlySignals > 0
      ? matched / (matched + backtestOnlySignals)
      : 0,
    pnlCorrelation: pearsonCorrelation(livePnls, btEstPnls),
  };
}

// ── Public API ──────────────────────────────────────────────────

/**
 * Align live trade records with backtest signals.
 *
 * @param liveTrades - Actual trade records from live/paper trading
 * @param backtestResult - Backtest result containing signals
 * @param config - Optional alignment configuration
 */
export function alignTrades(
  liveTrades: TradeRecord[],
  backtestResult: BacktestResult,
  config?: AlignmentConfig,
): AlignmentResult {
  const cfg = resolveConfig(config);
  const backtestSignals = backtestResult.signals;

  // Determine date range
  const allTimestamps = [
    ...liveTrades.map((t) => toTimestamp(t.entryTime)),
    ...backtestSignals.map((s) => toTimestamp(s.timestamp)),
  ].filter((t) => t > 0);

  const from = allTimestamps.length > 0
    ? new Date(Math.min(...allTimestamps)).toISOString().slice(0, 10)
    : backtestResult.startDate;
  const to = allTimestamps.length > 0
    ? new Date(Math.max(...allTimestamps)).toISOString().slice(0, 10)
    : backtestResult.endDate;

  // Collect all symbols
  const symbolSet = new Set<string>();
  for (const t of liveTrades) symbolSet.add(t.symbol);
  for (const s of backtestSignals) symbolSet.add(s.symbol);
  const symbols = [...symbolSet].sort();

  // Group by symbol
  const liveBySymbol = groupBy(liveTrades, (t) => t.symbol);
  const btBySymbol = groupBy(backtestSignals, (s) => s.symbol);

  // Align per-symbol, then merge
  const allPairs: AlignedTradePair[] = [];
  const symbolBreakdown: Record<string, AlignmentSummary> = {};
  for (const sym of symbols) {
    const symLive = liveBySymbol.get(sym) ?? [];
    const symBt = btBySymbol.get(sym) ?? [];
    const symPairs = alignSymbol(symLive, symBt, cfg);
    allPairs.push(...symPairs);
    symbolBreakdown[sym] = computeSummary(symPairs);
  }

  // Sort final pairs by time
  allPairs.sort((a, b) => {
    const tA = a.liveTrade
      ? toTimestamp(a.liveTrade.entryTime)
      : (a.backtestTimestamp ?? 0);
    const tB = b.liveTrade
      ? toTimestamp(b.liveTrade.entryTime)
      : (b.backtestTimestamp ?? 0);
    return tA - tB;
  });

  return {
    strategyId: backtestResult.strategyId,
    symbols,
    dateRange: { from, to },
    pairs: allPairs,
    summary: computeSummary(allPairs),
    symbolBreakdown,
    config: cfg,
  };
}

/**
 * Align live trades with backtest signals using raw arrays
 * (when you don't have a full BacktestResult).
 */
export function alignTradesRaw(
  liveTrades: TradeRecord[],
  backtestSignals: TradingSignal[],
  opts: {
    strategyId: string;
    symbols?: string[];
    config?: AlignmentConfig;
  },
): AlignmentResult {
  const fakeResult: BacktestResult = {
    strategyId: opts.strategyId,
    symbols: opts.symbols ?? [],
    startDate: "",
    endDate: "",
    initialCash: 0,
    finalValue: 0,
    totalReturn: 0,
    annualizedReturn: 0,
    maxDrawdown: 0,
    sharpeRatio: 0,
    winRate: 0,
    totalTrades: 0,
    signals: backtestSignals,
    equityCurve: [],
  };
  return alignTrades(liveTrades, fakeResult, opts.config);
}

/**
 * Filter alignment pairs by match type.
 */
export function filterPairs(
  pairs: AlignedTradePair[],
  matchTypes: AlignmentMatchType[],
): AlignedTradePair[] {
  const typeSet = new Set(matchTypes);
  return pairs.filter((p) => typeSet.has(p.matchType));
}

/**
 * Format an alignment summary as a human-readable text report.
 */
export function formatAlignmentReport(result: AlignmentResult): string {
  const s = result.summary;
  const lines: string[] = [
    `=== 实盘 vs 回测 对齐报告 ===`,
    `策略: ${result.strategyId}`,
    `标的: ${result.symbols.join(", ")}`,
    `时间范围: ${result.dateRange.from} ~ ${result.dateRange.to}`,
    ``,
    `--- 匹配统计 ---`,
    `总配对数: ${s.totalPairs}`,
    `精确匹配: ${s.exactMatches}`,
    `时间偏移匹配: ${s.timeShiftedMatches}`,
    `仅实盘交易: ${s.liveOnlyTrades}`,
    `仅回测信号: ${s.backtestOnlySignals}`,
    ``,
    `--- 执行质量 ---`,
    `执行率: ${(s.executionRate * 100).toFixed(1)}%`,
    `平均时间偏移: ${(s.avgTimeDeltaMs / 3_600_000).toFixed(1)} 小时`,
    `平均价格滑点: ${(s.avgPriceSlippage * 100).toFixed(3)}%`,
    `平均盈亏差异: ¥${s.avgPnlDelta.toFixed(2)}`,
    `盈亏相关性: ${s.pnlCorrelation.toFixed(4)}`,
  ];

  return lines.join("\n");
}

// ── TradeRecord vs TradeRecord alignment ───────────────────────

interface TradeRecordEntry {
  trade: TradeRecord;
  entryTs: number;
  exitTs: number;
  matched: boolean;
}

/**
 * Align two sets of TradeRecord objects for a single symbol.
 * Matches by direction + entry time proximity + entry price tolerance.
 */
function alignTradeRecordsForSymbol(
  liveTrades: TradeRecord[],
  backtestTrades: TradeRecord[],
  cfg: Required<AlignmentConfig>,
): AlignedTradeRecordPair[] {
  const pairs: AlignedTradeRecordPair[] = [];

  const liveEntries: TradeRecordEntry[] = liveTrades
    .map((t) => ({
      trade: t,
      entryTs: toTimestamp(t.entryTime),
      exitTs: t.exitTime ? toTimestamp(t.exitTime) : 0,
      matched: false,
    }))
    .sort((a, b) => a.entryTs - b.entryTs);

  const btEntries: TradeRecordEntry[] = backtestTrades
    .map((t) => ({
      trade: t,
      entryTs: toTimestamp(t.entryTime),
      exitTs: t.exitTime ? toTimestamp(t.exitTime) : 0,
      matched: false,
    }))
    .sort((a, b) => a.entryTs - b.entryTs);

  // Greedy nearest-neighbor match
  for (const le of liveEntries) {
    let bestIdx = -1;
    let bestDelta = Infinity;
    const liveSide = normalizeSide(le.trade.side);

    for (let j = 0; j < btEntries.length; j++) {
      const be = btEntries[j]!;
      if (be.matched) continue;

      const btSide = normalizeSide(be.trade.side);
      if (liveSide !== btSide) continue;

      const timeDelta = Math.abs(le.entryTs - be.entryTs);
      if (timeDelta > cfg.timeToleranceMs) continue;

      if (le.trade.entryPrice > 0 && be.trade.entryPrice > 0) {
        const priceDeviation =
          Math.abs(le.trade.entryPrice - be.trade.entryPrice) / be.trade.entryPrice;
        if (priceDeviation > cfg.priceToleranceRatio) continue;
      }

      if (timeDelta < bestDelta) {
        bestDelta = timeDelta;
        bestIdx = j;
      }
    }

    if (bestIdx >= 0) {
      const be = btEntries[bestIdx]!;
      be.matched = true;
      le.matched = true;

      const entryTimeDeltaMs = le.entryTs - be.entryTs;
      const entryPriceSlippage =
        be.trade.entryPrice > 0
          ? (le.trade.entryPrice - be.trade.entryPrice) / be.trade.entryPrice
          : 0;

      let exitTimeDeltaMs: number | undefined;
      let exitPriceSlippage: number | undefined;
      if (le.exitTs > 0 && be.exitTs > 0) {
        exitTimeDeltaMs = le.exitTs - be.exitTs;
      }
      if (
        le.trade.exitPrice != null &&
        be.trade.exitPrice != null &&
        be.trade.exitPrice > 0
      ) {
        exitPriceSlippage =
          (le.trade.exitPrice - be.trade.exitPrice) / be.trade.exitPrice;
      }

      const pnlDelta = le.trade.netPnl - be.trade.netPnl;

      let holdingDaysDelta: number | undefined;
      if (le.trade.holdingDays != null && be.trade.holdingDays != null) {
        holdingDaysDelta = le.trade.holdingDays - be.trade.holdingDays;
      }

      const matchType: AlignmentMatchType =
        Math.abs(entryTimeDeltaMs) <= cfg.timeToleranceMs / 10 ? "exact" : "time_shifted";

      pairs.push({
        id: randomUUID().slice(0, 12),
        matchType,
        symbol: le.trade.symbol,
        liveTrade: le.trade,
        backtestTrade: be.trade,
        entryTimeDeltaMs,
        exitTimeDeltaMs,
        entryPriceSlippage,
        exitPriceSlippage,
        pnlDelta,
        holdingDaysDelta,
      });
    }
  }

  // Unmatched live trades
  for (const le of liveEntries) {
    if (le.matched) continue;
    pairs.push({
      id: randomUUID().slice(0, 12),
      matchType: "live_only",
      symbol: le.trade.symbol,
      liveTrade: le.trade,
    });
  }

  // Unmatched backtest trades
  if (cfg.includeUnexecuted) {
    for (const be of btEntries) {
      if (be.matched) continue;
      pairs.push({
        id: randomUUID().slice(0, 12),
        matchType: "backtest_only",
        symbol: be.trade.symbol,
        backtestTrade: be.trade,
      });
    }
  }

  return pairs;
}

/** Compute summary from TradeRecordPair array (reuses the same AlignmentSummary shape). */
function computeTradeRecordSummary(pairs: AlignedTradeRecordPair[]): AlignmentSummary {
  let exactMatches = 0;
  let timeShiftedMatches = 0;
  let liveOnlyTrades = 0;
  let backtestOnlySignals = 0;

  const matchedTimeDeltasAbs: number[] = [];
  const matchedSlippages: number[] = [];
  const matchedPnlDeltas: number[] = [];
  const livePnls: number[] = [];
  const btPnls: number[] = [];

  for (const p of pairs) {
    switch (p.matchType) {
      case "exact":
        exactMatches++;
        break;
      case "time_shifted":
        timeShiftedMatches++;
        break;
      case "live_only":
        liveOnlyTrades++;
        break;
      case "backtest_only":
        backtestOnlySignals++;
        break;
    }

    if (p.matchType === "exact" || p.matchType === "time_shifted") {
      if (p.entryTimeDeltaMs != null) matchedTimeDeltasAbs.push(Math.abs(p.entryTimeDeltaMs));
      if (p.entryPriceSlippage != null) matchedSlippages.push(p.entryPriceSlippage);
      if (p.pnlDelta != null) matchedPnlDeltas.push(p.pnlDelta);
      if (p.liveTrade) livePnls.push(p.liveTrade.netPnl);
      if (p.backtestTrade) btPnls.push(p.backtestTrade.netPnl);
    }
  }

  const matched = exactMatches + timeShiftedMatches;
  const avg = (arr: number[]) => (arr.length > 0 ? arr.reduce((a, b) => a + b, 0) / arr.length : 0);

  return {
    totalPairs: pairs.length,
    exactMatches,
    timeShiftedMatches,
    liveOnlyTrades,
    backtestOnlySignals,
    avgTimeDeltaMs: avg(matchedTimeDeltasAbs),
    avgPriceSlippage: avg(matchedSlippages),
    avgPnlDelta: avg(matchedPnlDeltas),
    executionRate:
      matched + backtestOnlySignals > 0 ? matched / (matched + backtestOnlySignals) : 0,
    pnlCorrelation: pearsonCorrelation(livePnls, btPnls),
  };
}

/**
 * Align live TradeRecords against backtest TradeRecords.
 *
 * This is the full trade-level alignment (entry+exit), as opposed to
 * `alignTrades()` which matches live trades against backtest entry signals.
 *
 * @param liveTrades - Trade records from live/paper trading
 * @param backtestTrades - Trade records from backtest execution
 * @param opts - Strategy ID, optional symbol filter, alignment config
 */
export function alignTradeRecords(
  liveTrades: TradeRecord[],
  backtestTrades: TradeRecord[],
  opts: {
    strategyId: string;
    symbols?: string[];
    config?: AlignmentConfig;
  },
): TradeRecordAlignmentResult {
  const cfg = resolveConfig(opts.config);

  // Collect all symbols
  const symbolSet = new Set<string>();
  for (const t of liveTrades) symbolSet.add(t.symbol);
  for (const t of backtestTrades) symbolSet.add(t.symbol);
  // Apply symbol filter if given
  const symbols = opts.symbols?.length
    ? [...symbolSet].filter((s) => opts.symbols!.includes(s)).sort()
    : [...symbolSet].sort();

  // Group by symbol
  const liveBySymbol = groupBy(liveTrades, (t) => t.symbol);
  const btBySymbol = groupBy(backtestTrades, (t) => t.symbol);

  // Align per-symbol, then merge
  const allPairs: AlignedTradeRecordPair[] = [];
  const symbolBreakdown: Record<string, AlignmentSummary> = {};

  for (const sym of symbols) {
    const symLive = liveBySymbol.get(sym) ?? [];
    const symBt = btBySymbol.get(sym) ?? [];
    const symPairs = alignTradeRecordsForSymbol(symLive, symBt, cfg);
    allPairs.push(...symPairs);
    symbolBreakdown[sym] = computeTradeRecordSummary(symPairs);
  }

  // Sort final pairs by time
  allPairs.sort((a, b) => {
    const tA = a.liveTrade
      ? toTimestamp(a.liveTrade.entryTime)
      : a.backtestTrade
        ? toTimestamp(a.backtestTrade.entryTime)
        : 0;
    const tB = b.liveTrade
      ? toTimestamp(b.liveTrade.entryTime)
      : b.backtestTrade
        ? toTimestamp(b.backtestTrade.entryTime)
        : 0;
    return tA - tB;
  });

  // Date range
  const allTimestamps = [
    ...liveTrades.map((t) => toTimestamp(t.entryTime)),
    ...backtestTrades.map((t) => toTimestamp(t.entryTime)),
  ].filter((t) => t > 0);

  const from =
    allTimestamps.length > 0
      ? new Date(Math.min(...allTimestamps)).toISOString().slice(0, 10)
      : "";
  const to =
    allTimestamps.length > 0
      ? new Date(Math.max(...allTimestamps)).toISOString().slice(0, 10)
      : "";

  return {
    strategyId: opts.strategyId,
    symbols,
    dateRange: { from, to },
    pairs: allPairs,
    summary: computeTradeRecordSummary(allPairs),
    symbolBreakdown,
    config: cfg,
  };
}

/**
 * Format a TradeRecordAlignmentResult as a human-readable text report.
 */
export function formatTradeRecordAlignmentReport(result: TradeRecordAlignmentResult): string {
  const s = result.summary;
  const lines: string[] = [
    `=== 实盘 vs 回测 交易记录对齐报告 ===`,
    `策略: ${result.strategyId}`,
    `标的: ${result.symbols.join(", ")}`,
    `时间范围: ${result.dateRange.from} ~ ${result.dateRange.to}`,
    ``,
    `--- 匹配统计 ---`,
    `总配对数: ${s.totalPairs}`,
    `精确匹配: ${s.exactMatches}`,
    `时间偏移匹配: ${s.timeShiftedMatches}`,
    `仅实盘交易: ${s.liveOnlyTrades}`,
    `仅回测交易: ${s.backtestOnlySignals}`,
    ``,
    `--- 执行质量 ---`,
    `执行率: ${(s.executionRate * 100).toFixed(1)}%`,
    `平均入场时间偏移: ${(s.avgTimeDeltaMs / 3_600_000).toFixed(1)} 小时`,
    `平均入场价格滑点: ${(s.avgPriceSlippage * 100).toFixed(3)}%`,
    `平均盈亏差异: ¥${s.avgPnlDelta.toFixed(2)}`,
    `盈亏相关性: ${s.pnlCorrelation.toFixed(4)}`,
  ];

  // Per-symbol breakdown
  if (result.symbolBreakdown && Object.keys(result.symbolBreakdown).length > 1) {
    lines.push(``, `--- 标的明细 ---`);
    for (const [sym, ss] of Object.entries(result.symbolBreakdown)) {
      const matched = ss.exactMatches + ss.timeShiftedMatches;
      lines.push(
        `${sym}: 匹配${matched} 仅实盘${ss.liveOnlyTrades} 仅回测${ss.backtestOnlySignals} ` +
          `执行率${(ss.executionRate * 100).toFixed(1)}% 滑点${(ss.avgPriceSlippage * 100).toFixed(3)}%`,
      );
    }
  }

  return lines.join("\n");
}
