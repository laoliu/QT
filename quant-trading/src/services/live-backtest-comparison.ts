/**
 * LiveBacktestComparison — compares live trading performance against
 * backtested expectations for the same strategy/period.
 *
 * Use cases:
 *   - Detect strategy drift: live performance diverging from backtest
 *   - Validate backtest assumptions (slippage, fill rates)
 *   - Quantify the "backtest-to-live gap"
 *   - Generate actionable diagnostics (e.g. "slippage 3x higher than modeled")
 *   - Rolling window comparisons (30/60/90 day)
 *   - Trade-by-trade matching between live and backtest signals
 *   - Multi-strategy batch comparison
 *   - Markdown report export
 *
 * Data sources:
 *   - EquityHistory: actual live equity curve
 *   - BacktestStore: historical backtest results
 *   - TradeRecord[]: actual filled trades from live engine
 *   - Backtest engine: re-run backtest on the same period for fresh comparison
 */
import type { BacktestResult, TradingSignal, TradeRecord } from "../types.js";
import type { EquityHistory, EquitySnapshot } from "./equity-history.js";
import type { BacktestStore } from "./backtest-store.js";

// ── Types ────────────────────────────────────────────────────

export interface LiveVsBacktestRequest {
  /** Strategy ID used in both live and backtest. */
  strategyId: string;
  /** Symbols traded live (to match with backtest). */
  symbols: string[];
  /** Start date of comparison window (YYYY-MM-DD). */
  startDate: string;
  /** End date of comparison window (YYYY-MM-DD). */
  endDate: string;
  /** Backtest run ID from BacktestStore; if not provided, finds the best match. */
  backtestId?: string;
}

export interface MetricDelta {
  /** Metric name in Chinese for display. */
  label: string;
  /** Backtest value. */
  backtest: number;
  /** Live value. */
  live: number;
  /** Absolute difference (live - backtest). */
  delta: number;
  /** Percentage deviation. */
  deltaPct: number;
  /** Unit for display (e.g. "%", "x", "¥"). */
  unit: string;
  /** Whether the delta indicates degradation. */
  degraded: boolean;
}

export interface EquityCurvePoint {
  date: string;
  backtestValue: number | null;
  liveValue: number | null;
}

export interface TradingGap {
  /** Where the largest deviations occur. */
  category: "slippage" | "fill_rate" | "timing" | "frequency" | "drawdown" | "other";
  /** Human-readable description. */
  description: string;
  /** Severity: info, warning, critical. */
  severity: "info" | "warning" | "critical";
}

export interface LiveVsBacktestResult {
  strategyId: string;
  symbols: string[];
  period: string;
  backtestId: string | null;

  /** Key metrics comparison. */
  metrics: MetricDelta[];

  /** Overlaid equity curves for charting. */
  equityCurve: EquityCurvePoint[];

  /** Identified gaps between live and backtest. */
  gaps: TradingGap[];

  /** Overall health verdict. */
  verdict: "consistent" | "minor_drift" | "significant_drift" | "critical_divergence";
  verdictDescription: string;

  /** Live trading stats (from actual trades). */
  liveStats: {
    totalTrades: number;
    winRate: number;
    avgSlippage: number;
    totalReturn: number;
    maxDrawdown: number;
  };

  /** Backtest stats (from backtest result). */
  backtestStats: {
    totalTrades: number;
    winRate: number;
    modeledSlippage: number;
    totalReturn: number;
    maxDrawdown: number;
  };
}

// ── Trade-by-trade matching types ─────────────────────────────

export interface TradeMatch {
  /** Live trade that was matched. */
  liveTrade: TradeRecord;
  /** Backtest signal that was matched (null if unmatched). */
  backtestSignal: TradingSignal | null;
  /** Match quality. */
  matchType: "exact" | "approximate" | "unmatched";
  /** Days between live entry and backtest signal (0 = same day). */
  timingDeltaDays: number;
  /** Price difference between live entry and backtest signal price. */
  priceDeltaPct: number;
  /** Slippage impact of the timing/price difference. */
  slippageImpact: number;
}

export interface TradeMatchResult {
  strategyId: string;
  period: string;
  /** Trades matched between live and backtest. */
  matches: TradeMatch[];
  /** Backtest signals with no corresponding live trade. */
  missedSignals: TradingSignal[];
  /** Summary statistics. */
  summary: {
    totalLiveTrades: number;
    totalBacktestSignals: number;
    exactMatches: number;
    approximateMatches: number;
    unmatchedLiveTrades: number;
    missedSignals: number;
    avgTimingDeltaDays: number;
    avgPriceDeltaPct: number;
  };
}

// ── Rolling comparison types ─────────────────────────────────

export interface RollingComparisonPoint {
  /** Window end date. */
  date: string;
  /** Window start date. */
  windowStart: string;
  /** Verdict for this window. */
  verdict: LiveVsBacktestResult["verdict"];
  /** Key metric deltas for this window. */
  returnDelta: number;
  drawdownDelta: number;
  winRateDelta: number;
  /** Number of live trades in this window. */
  liveTrades: number;
}

export interface RollingComparisonResult {
  strategyId: string;
  symbols: string[];
  rollingDays: number;
  /** Time series of rolling comparison points. */
  points: RollingComparisonPoint[];
  /** Trend direction: improving, stable, deteriorating. */
  trend: "improving" | "stable" | "deteriorating";
  trendDescription: string;
}

// ── Multi-strategy batch comparison types ─────────────────────

export interface BatchComparisonEntry {
  strategyId: string;
  result: LiveVsBacktestResult;
}

export interface BatchComparisonResult {
  /** Individual strategy comparison results. */
  entries: BatchComparisonEntry[];
  /** Ranked by consistency (best verdict first). */
  ranking: Array<{
    strategyId: string;
    verdict: LiveVsBacktestResult["verdict"];
    returnDelta: number;
    drawdownDelta: number;
    score: number;
  }>;
}

// ── Helpers ──────────────────────────────────────────────────

function pct(a: number, b: number): number {
  if (b === 0) return a === 0 ? 0 : 100;
  return ((a - b) / Math.abs(b)) * 100;
}

function computeLiveStats(
  trades: TradeRecord[],
  equitySnapshots: EquitySnapshot[],
): {
  totalTrades: number;
  winRate: number;
  avgSlippage: number;
  totalReturn: number;
  maxDrawdown: number;
} {
  const closed = trades.filter((t) => t.exitPrice != null);
  const wins = closed.filter((t) => t.netPnl > 0).length;
  const winRate = closed.length > 0 ? (wins / closed.length) * 100 : 0;

  // Calculate return from equity snapshots
  let totalReturn = 0;
  let maxDrawdown = 0;
  if (equitySnapshots.length >= 2) {
    const first = equitySnapshots[0]!;
    const last = equitySnapshots[equitySnapshots.length - 1]!;
    totalReturn = first.equity > 0 ? ((last.equity - first.equity) / first.equity) * 100 : 0;

    let peak = -Infinity;
    for (const s of equitySnapshots) {
      if (s.equity > peak) peak = s.equity;
      const dd = peak > 0 ? ((peak - s.equity) / peak) * 100 : 0;
      if (dd > maxDrawdown) maxDrawdown = dd;
    }
  }

  // Estimate average slippage from filled trades
  // Slippage = difference between expected (signal) price and actual fill price
  // Since we don't always have signal price, use avgCost vs entryPrice as proxy
  const slippages = closed
    .filter((t) => t.entryPrice > 0 && t.exitPrice != null && t.exitPrice > 0)
    .map((t) => Math.abs(t.netPnl - t.grossPnl) / (t.entryPrice * t.quantity))
    .filter((s) => Number.isFinite(s));
  const avgSlippage =
    slippages.length > 0 ? slippages.reduce((a, b) => a + b, 0) / slippages.length : 0;

  return {
    totalTrades: closed.length,
    winRate,
    avgSlippage: avgSlippage * 100,
    totalReturn,
    maxDrawdown,
  };
}

function buildEquityCurve(
  liveSnapshots: EquitySnapshot[],
  backtestCurve: Array<{ date: string; value: number }>,
  startDate: string,
  endDate: string,
): EquityCurvePoint[] {
  // Collect all dates from both sources
  const dateSet = new Set<string>();
  for (const s of liveSnapshots) {
    if (s.date >= startDate && s.date <= endDate) dateSet.add(s.date);
  }
  for (const b of backtestCurve) {
    if (b.date >= startDate && b.date <= endDate) dateSet.add(b.date);
  }

  const dates = [...dateSet].sort();

  const liveMap = new Map(liveSnapshots.map((s) => [s.date, s.equity]));
  const btMap = new Map(backtestCurve.map((b) => [b.date, b.value]));

  return dates.map((date) => ({
    date,
    backtestValue: btMap.get(date) ?? null,
    liveValue: liveMap.get(date) ?? null,
  }));
}

function identifyGaps(
  liveStats: LiveVsBacktestResult["liveStats"],
  backtestStats: LiveVsBacktestResult["backtestStats"],
): TradingGap[] {
  const gaps: TradingGap[] = [];

  // Slippage gap
  if (backtestStats.modeledSlippage > 0) {
    const slippageRatio = liveStats.avgSlippage / backtestStats.modeledSlippage;
    if (slippageRatio > 3) {
      gaps.push({
        category: "slippage",
        description: `实际滑点(${liveStats.avgSlippage.toFixed(3)}%)是回测建模值(${backtestStats.modeledSlippage.toFixed(3)}%)的${slippageRatio.toFixed(1)}倍，建议调高回测滑点参数`,
        severity: "critical",
      });
    } else if (slippageRatio > 1.5) {
      gaps.push({
        category: "slippage",
        description: `实际滑点(${liveStats.avgSlippage.toFixed(3)}%)略高于回测建模值(${backtestStats.modeledSlippage.toFixed(3)}%)`,
        severity: "warning",
      });
    }
  }

  // Trade frequency gap
  if (backtestStats.totalTrades > 0) {
    const freqRatio = liveStats.totalTrades / backtestStats.totalTrades;
    if (freqRatio < 0.5) {
      gaps.push({
        category: "frequency",
        description: `实盘交易次数(${liveStats.totalTrades})远少于回测(${backtestStats.totalTrades})，可能存在信号被风控拦截或执行问题`,
        severity: "warning",
      });
    } else if (freqRatio > 2) {
      gaps.push({
        category: "frequency",
        description: `实盘交易次数(${liveStats.totalTrades})远多于回测(${backtestStats.totalTrades})，可能存在过度交易`,
        severity: "warning",
      });
    }
  }

  // Win rate gap
  const winRateDelta = liveStats.winRate - backtestStats.winRate;
  if (winRateDelta < -15) {
    gaps.push({
      category: "fill_rate",
      description: `实盘胜率(${liveStats.winRate.toFixed(1)}%)显著低于回测(${backtestStats.winRate.toFixed(1)}%)，差距${Math.abs(winRateDelta).toFixed(1)}个百分点`,
      severity: "critical",
    });
  } else if (winRateDelta < -5) {
    gaps.push({
      category: "fill_rate",
      description: `实盘胜率(${liveStats.winRate.toFixed(1)}%)略低于回测(${backtestStats.winRate.toFixed(1)}%)`,
      severity: "warning",
    });
  }

  // Drawdown gap
  const ddDelta = liveStats.maxDrawdown - backtestStats.maxDrawdown;
  if (ddDelta > 10) {
    gaps.push({
      category: "drawdown",
      description: `实盘最大回撤(${liveStats.maxDrawdown.toFixed(1)}%)超过回测(${backtestStats.maxDrawdown.toFixed(1)}%)达${ddDelta.toFixed(1)}个百分点，风险控制需加强`,
      severity: "critical",
    });
  } else if (ddDelta > 5) {
    gaps.push({
      category: "drawdown",
      description: `实盘最大回撤(${liveStats.maxDrawdown.toFixed(1)}%)高于回测(${backtestStats.maxDrawdown.toFixed(1)}%)`,
      severity: "warning",
    });
  }

  // Return gap
  const returnDelta = liveStats.totalReturn - backtestStats.totalReturn;
  if (returnDelta < -20) {
    gaps.push({
      category: "other",
      description: `实盘收益(${liveStats.totalReturn.toFixed(2)}%)大幅落后于回测(${backtestStats.totalReturn.toFixed(2)}%)，策略可能已失效`,
      severity: "critical",
    });
  } else if (returnDelta < -10) {
    gaps.push({
      category: "timing",
      description: `实盘收益(${liveStats.totalReturn.toFixed(2)}%)低于回测(${backtestStats.totalReturn.toFixed(2)}%)，可能存在执行时机偏差`,
      severity: "warning",
    });
  }

  if (gaps.length === 0) {
    gaps.push({
      category: "other",
      description: "实盘与回测表现基本一致，策略执行良好",
      severity: "info",
    });
  }

  return gaps;
}

function determineVerdict(
  gaps: TradingGap[],
): { verdict: LiveVsBacktestResult["verdict"]; description: string } {
  const criticalCount = gaps.filter((g) => g.severity === "critical").length;
  const warningCount = gaps.filter((g) => g.severity === "warning").length;

  if (criticalCount >= 2) {
    return {
      verdict: "critical_divergence",
      description:
        "实盘与回测存在严重偏离，建议暂停策略运行，重新评估策略有效性和参数设置",
    };
  }
  if (criticalCount >= 1) {
    return {
      verdict: "significant_drift",
      description:
        "实盘与回测存在显著差异，建议密切关注并考虑调整策略参数或风控设置",
    };
  }
  if (warningCount >= 2) {
    return {
      verdict: "minor_drift",
      description: "实盘与回测存在轻微偏差，属于正常范围但建议持续监控",
    };
  }
  return {
    verdict: "consistent",
    description: "实盘表现与回测预期基本一致，策略运行健康",
  };
}

// ── Service ─────────────────────────────────────────────────

function signalDate(s: TradingSignal): string {
  return new Date(s.timestamp).toISOString().slice(0, 10);
}

function daysBetween(a: string, b: string): number {
  const msPerDay = 86_400_000;
  return Math.round((new Date(a).getTime() - new Date(b).getTime()) / msPerDay);
}

function determineTrend(
  points: RollingComparisonPoint[],
): { trend: RollingComparisonResult["trend"]; trendDescription: string } {
  if (points.length < 3) {
    return { trend: "stable", trendDescription: "数据点不足，无法判断趋势" };
  }

  const verdictScores: Record<string, number> = {
    consistent: 0,
    minor_drift: 1,
    significant_drift: 2,
    critical_divergence: 3,
  };

  // Compare first half vs second half
  const mid = Math.floor(points.length / 2);
  const firstHalf = points.slice(0, mid);
  const secondHalf = points.slice(mid);

  const avgFirst =
    firstHalf.reduce((a, p) => a + (verdictScores[p.verdict] ?? 0), 0) / firstHalf.length;
  const avgSecond =
    secondHalf.reduce((a, p) => a + (verdictScores[p.verdict] ?? 0), 0) / secondHalf.length;

  const diff = avgSecond - avgFirst;

  if (diff < -0.3) {
    return { trend: "improving", trendDescription: "实盘与回测的一致性呈改善趋势" };
  }
  if (diff > 0.3) {
    return { trend: "deteriorating", trendDescription: "实盘与回测的偏离呈加剧趋势，需关注" };
  }
  return { trend: "stable", trendDescription: "实盘与回测的一致性保持稳定" };
}

export class LiveBacktestComparison {
  constructor(
    private equityHistory: EquityHistory,
    private backtestStore: BacktestStore | undefined,
  ) {}

  /**
   * Compare live trading performance against a matching backtest result.
   *
   * @param req - Comparison request parameters.
   * @param liveTrades - Actual trade records from the live engine.
   * @param backtestResult - A pre-fetched backtest result; if omitted, will look up from store.
   */
  compare(
    req: LiveVsBacktestRequest,
    liveTrades: TradeRecord[],
    backtestResult?: BacktestResult,
  ): LiveVsBacktestResult {
    // 1. Get backtest result
    let btResult = backtestResult ?? null;
    let backtestId = req.backtestId ?? null;

    if (!btResult && this.backtestStore && backtestId) {
      const record = this.backtestStore.getFullResult(backtestId);
      if (record) btResult = record.result;
    }

    // If no specific ID, try to find the best matching backtest
    if (!btResult && this.backtestStore) {
      const candidates = this.backtestStore.query({
        strategyId: req.strategyId,
        sortBy: "createdAt",
        sortDir: "desc",
        limit: 20,
      });
      // Find one that overlaps with the requested period and symbols
      for (const c of candidates) {
        const symbolOverlap = req.symbols.some((s) => c.symbols.includes(s));
        const dateOverlap = c.startDate <= req.endDate && c.endDate >= req.startDate;
        if (symbolOverlap && dateOverlap) {
          const record = this.backtestStore.getFullResult(c.id);
          if (record) {
            btResult = record.result;
            backtestId = c.id;
            break;
          }
        }
      }
    }

    // 2. Get live equity snapshots
    const liveSnapshots = this.equityHistory.getRange(req.startDate, req.endDate);

    // 3. Filter trades to the comparison period
    const periodTrades = liveTrades.filter((t) => {
      const tDate = t.entryTime.slice(0, 10);
      return tDate >= req.startDate && tDate <= req.endDate;
    });

    // Strategy-specific trades
    const stratTrades = req.strategyId
      ? periodTrades.filter((t) => t.strategyId === req.strategyId)
      : periodTrades;

    // 4. Compute live stats
    const liveStats = computeLiveStats(stratTrades, liveSnapshots);

    // 5. Compute backtest stats
    // Estimate modeled slippage from backtest fee impact, fallback to 0.1%
    const modeledSlippage = btResult?.feeImpact != null && btResult.feeImpact > 0
      ? btResult.feeImpact / (btResult.totalTrades || 1)
      : 0.1;
    const backtestStats = {
      totalTrades: btResult?.totalTrades ?? 0,
      winRate: btResult?.winRate ?? 0,
      modeledSlippage,
      totalReturn: btResult?.totalReturn ?? 0,
      maxDrawdown: Math.abs(btResult?.maxDrawdown ?? 0),
    };

    // 6. Build metric deltas
    const metrics: MetricDelta[] = [
      {
        label: "总收益率",
        backtest: backtestStats.totalReturn,
        live: liveStats.totalReturn,
        delta: liveStats.totalReturn - backtestStats.totalReturn,
        deltaPct: pct(liveStats.totalReturn, backtestStats.totalReturn),
        unit: "%",
        degraded: liveStats.totalReturn < backtestStats.totalReturn,
      },
      {
        label: "最大回撤",
        backtest: backtestStats.maxDrawdown,
        live: liveStats.maxDrawdown,
        delta: liveStats.maxDrawdown - backtestStats.maxDrawdown,
        deltaPct: pct(liveStats.maxDrawdown, backtestStats.maxDrawdown),
        unit: "%",
        degraded: liveStats.maxDrawdown > backtestStats.maxDrawdown,
      },
      {
        label: "胜率",
        backtest: backtestStats.winRate,
        live: liveStats.winRate,
        delta: liveStats.winRate - backtestStats.winRate,
        deltaPct: pct(liveStats.winRate, backtestStats.winRate),
        unit: "%",
        degraded: liveStats.winRate < backtestStats.winRate,
      },
      {
        label: "交易次数",
        backtest: backtestStats.totalTrades,
        live: liveStats.totalTrades,
        delta: liveStats.totalTrades - backtestStats.totalTrades,
        deltaPct: pct(liveStats.totalTrades, backtestStats.totalTrades),
        unit: "次",
        degraded: false,
      },
      {
        label: "平均滑点",
        backtest: backtestStats.modeledSlippage,
        live: liveStats.avgSlippage,
        delta: liveStats.avgSlippage - backtestStats.modeledSlippage,
        deltaPct: pct(liveStats.avgSlippage, backtestStats.modeledSlippage),
        unit: "%",
        degraded: liveStats.avgSlippage > backtestStats.modeledSlippage * 1.5,
      },
    ];

    // 7. Build equity curve overlay
    const equityCurve = buildEquityCurve(
      liveSnapshots,
      btResult?.equityCurve ?? [],
      req.startDate,
      req.endDate,
    );

    // 8. Identify gaps
    const gaps = identifyGaps(liveStats, backtestStats);

    // 9. Determine verdict
    const { verdict, description: verdictDescription } = determineVerdict(gaps);

    return {
      strategyId: req.strategyId,
      symbols: req.symbols,
      period: `${req.startDate} ~ ${req.endDate}`,
      backtestId,
      metrics,
      equityCurve,
      gaps,
      verdict,
      verdictDescription,
      liveStats,
      backtestStats,
    };
  }

  /**
   * Match individual live trades against backtest signals for the same strategy/period.
   */
  matchTrades(
    req: LiveVsBacktestRequest,
    liveTrades: TradeRecord[],
    backtestResult?: BacktestResult,
  ): TradeMatchResult {
    const signals = backtestResult?.signals ?? [];

    // Filter trades to period and strategy
    const periodTrades = liveTrades.filter((t) => {
      const tDate = t.entryTime.slice(0, 10);
      return (
        tDate >= req.startDate &&
        tDate <= req.endDate &&
        (!req.strategyId || t.strategyId === req.strategyId)
      );
    });

    // Filter signals to period and symbols
    const periodSignals = signals.filter((s) => {
      const sDate = signalDate(s);
      return sDate >= req.startDate && sDate <= req.endDate && req.symbols.includes(s.symbol);
    });

    const matchedSignalIndices = new Set<number>();
    const matches: TradeMatch[] = [];

    for (const trade of periodTrades) {
      const tradeDate = trade.entryTime.slice(0, 10);
      let bestMatch: { signal: TradingSignal; idx: number; daysDelta: number; priceDelta: number } | null = null;

      for (let i = 0; i < periodSignals.length; i++) {
        if (matchedSignalIndices.has(i)) continue;
        const sig = periodSignals[i]!;
        if (sig.symbol !== trade.symbol) continue;

        const daysDelta = Math.abs(daysBetween(tradeDate, signalDate(sig)));
        if (daysDelta > 5) continue; // max 5-day window for approximate match

        const priceDelta =
          sig.price > 0 ? Math.abs(trade.entryPrice - sig.price) / sig.price * 100 : 0;

        if (!bestMatch || daysDelta < bestMatch.daysDelta ||
          (daysDelta === bestMatch.daysDelta && priceDelta < bestMatch.priceDelta)) {
          bestMatch = { signal: sig, idx: i, daysDelta: daysDelta, priceDelta };
        }
      }

      if (bestMatch) {
        matchedSignalIndices.add(bestMatch.idx);
        const slippageImpact =
          trade.entryPrice > 0
            ? ((trade.entryPrice - bestMatch.signal.price) / trade.entryPrice) * 100
            : 0;
        matches.push({
          liveTrade: trade,
          backtestSignal: bestMatch.signal,
          matchType: bestMatch.daysDelta === 0 ? "exact" : "approximate",
          timingDeltaDays: bestMatch.daysDelta,
          priceDeltaPct: bestMatch.priceDelta,
          slippageImpact,
        });
      } else {
        matches.push({
          liveTrade: trade,
          backtestSignal: null,
          matchType: "unmatched",
          timingDeltaDays: 0,
          priceDeltaPct: 0,
          slippageImpact: 0,
        });
      }
    }

    const missedSignals = periodSignals.filter((_, i) => !matchedSignalIndices.has(i));

    const matched = matches.filter((m) => m.matchType !== "unmatched");
    const exactMatches = matches.filter((m) => m.matchType === "exact").length;
    const approximateMatches = matches.filter((m) => m.matchType === "approximate").length;
    const avgTimingDelta =
      matched.length > 0
        ? matched.reduce((a, m) => a + m.timingDeltaDays, 0) / matched.length
        : 0;
    const avgPriceDelta =
      matched.length > 0
        ? matched.reduce((a, m) => a + m.priceDeltaPct, 0) / matched.length
        : 0;

    return {
      strategyId: req.strategyId,
      period: `${req.startDate} ~ ${req.endDate}`,
      matches,
      missedSignals,
      summary: {
        totalLiveTrades: periodTrades.length,
        totalBacktestSignals: periodSignals.length,
        exactMatches,
        approximateMatches,
        unmatchedLiveTrades: matches.filter((m) => m.matchType === "unmatched").length,
        missedSignals: missedSignals.length,
        avgTimingDeltaDays: avgTimingDelta,
        avgPriceDeltaPct: avgPriceDelta,
      },
    };
  }

  /**
   * Compute rolling window comparisons over a date range.
   * Slides a window of `rollingDays` through the period, producing a time series of comparison verdicts.
   */
  rollingComparison(
    strategyId: string,
    symbols: string[],
    endDate: string,
    liveTrades: TradeRecord[],
    backtestResult?: BacktestResult,
    rollingDays: number = 30,
    stepDays: number = 7,
  ): RollingComparisonResult {
    const points: RollingComparisonPoint[] = [];
    const end = new Date(endDate);

    // Go back far enough to have multiple windows
    const maxLookback = rollingDays * 6;
    const earliest = new Date(end);
    earliest.setDate(earliest.getDate() - maxLookback);

    let cursor = new Date(earliest);
    cursor.setDate(cursor.getDate() + rollingDays);

    while (cursor <= end) {
      const windowEnd = cursor.toISOString().slice(0, 10);
      const windowStartDate = new Date(cursor);
      windowStartDate.setDate(windowStartDate.getDate() - rollingDays);
      const windowStart = windowStartDate.toISOString().slice(0, 10);

      const result = this.compare(
        { strategyId, symbols, startDate: windowStart, endDate: windowEnd },
        liveTrades,
        backtestResult,
      );

      const returnMetric = result.metrics.find((m) => m.label === "总收益率");
      const ddMetric = result.metrics.find((m) => m.label === "最大回撤");
      const wrMetric = result.metrics.find((m) => m.label === "胜率");

      points.push({
        date: windowEnd,
        windowStart,
        verdict: result.verdict,
        returnDelta: returnMetric?.delta ?? 0,
        drawdownDelta: ddMetric?.delta ?? 0,
        winRateDelta: wrMetric?.delta ?? 0,
        liveTrades: result.liveStats.totalTrades,
      });

      cursor.setDate(cursor.getDate() + stepDays);
    }

    // Determine trend from verdict progression
    const trend = determineTrend(points);

    return {
      strategyId,
      symbols,
      rollingDays,
      points,
      ...trend,
    };
  }

  /**
   * Compare multiple strategies simultaneously, ranking by consistency.
   */
  batchCompare(
    strategies: Array<{
      strategyId: string;
      symbols: string[];
      trades: TradeRecord[];
      backtestResult?: BacktestResult;
    }>,
    startDate: string,
    endDate: string,
  ): BatchComparisonResult {
    const entries: BatchComparisonEntry[] = [];

    for (const s of strategies) {
      const result = this.compare(
        { strategyId: s.strategyId, symbols: s.symbols, startDate, endDate },
        s.trades,
        s.backtestResult,
      );
      entries.push({ strategyId: s.strategyId, result });
    }

    // Score each strategy: lower is better (less drift)
    const verdictScores: Record<LiveVsBacktestResult["verdict"], number> = {
      consistent: 0,
      minor_drift: 1,
      significant_drift: 2,
      critical_divergence: 3,
    };

    const ranking = entries
      .map((e) => {
        const returnMetric = e.result.metrics.find((m) => m.label === "总收益率");
        const ddMetric = e.result.metrics.find((m) => m.label === "最大回撤");
        const verdictScore = verdictScores[e.result.verdict];
        const returnPenalty = Math.max(0, -(returnMetric?.delta ?? 0)) / 10;
        const ddPenalty = Math.max(0, ddMetric?.delta ?? 0) / 10;
        return {
          strategyId: e.strategyId,
          verdict: e.result.verdict,
          returnDelta: returnMetric?.delta ?? 0,
          drawdownDelta: ddMetric?.delta ?? 0,
          score: verdictScore + returnPenalty + ddPenalty,
        };
      })
      .sort((a, b) => a.score - b.score);

    return { entries, ranking };
  }

  /**
   * Generate a Markdown report of the comparison result.
   */
  generateReport(result: LiveVsBacktestResult): string {
    const lines: string[] = [];
    lines.push(`# 实盘 vs 回测对比报告`);
    lines.push("");
    lines.push(`**策略:** ${result.strategyId}`);
    lines.push(`**标的:** ${result.symbols.join(", ")}`);
    lines.push(`**期间:** ${result.period}`);
    lines.push(`**回测ID:** ${result.backtestId ?? "无匹配"}`);
    lines.push("");

    // Verdict
    const verdictEmoji: Record<string, string> = {
      consistent: "🟢",
      minor_drift: "🟡",
      significant_drift: "🟠",
      critical_divergence: "🔴",
    };
    lines.push(`## 综合评估: ${verdictEmoji[result.verdict] ?? ""} ${result.verdictDescription}`);
    lines.push("");

    // Metrics table
    lines.push("## 关键指标对比");
    lines.push("");
    lines.push("| 指标 | 回测 | 实盘 | 差异 | 偏离度 | 状态 |");
    lines.push("|------|------|------|------|--------|------|");
    for (const m of result.metrics) {
      const status = m.degraded ? "⚠️ 退化" : "✅ 正常";
      lines.push(
        `| ${m.label} | ${m.backtest.toFixed(2)}${m.unit} | ${m.live.toFixed(2)}${m.unit} | ${m.delta > 0 ? "+" : ""}${m.delta.toFixed(2)}${m.unit} | ${m.deltaPct > 0 ? "+" : ""}${m.deltaPct.toFixed(1)}% | ${status} |`,
      );
    }
    lines.push("");

    // Gaps
    lines.push("## 差异诊断");
    lines.push("");
    for (const g of result.gaps) {
      const icon = g.severity === "critical" ? "🔴" : g.severity === "warning" ? "🟡" : "🟢";
      lines.push(`- ${icon} **${g.category}**: ${g.description}`);
    }
    lines.push("");

    // Stats summary
    lines.push("## 实盘统计");
    lines.push("");
    lines.push(`- 交易次数: ${result.liveStats.totalTrades}`);
    lines.push(`- 胜率: ${result.liveStats.winRate.toFixed(1)}%`);
    lines.push(`- 总收益: ${result.liveStats.totalReturn.toFixed(2)}%`);
    lines.push(`- 最大回撤: ${result.liveStats.maxDrawdown.toFixed(2)}%`);
    lines.push(`- 平均滑点: ${result.liveStats.avgSlippage.toFixed(3)}%`);
    lines.push("");

    lines.push("## 回测统计");
    lines.push("");
    lines.push(`- 交易次数: ${result.backtestStats.totalTrades}`);
    lines.push(`- 胜率: ${result.backtestStats.winRate.toFixed(1)}%`);
    lines.push(`- 总收益: ${result.backtestStats.totalReturn.toFixed(2)}%`);
    lines.push(`- 最大回撤: ${result.backtestStats.maxDrawdown.toFixed(2)}%`);
    lines.push(`- 建模滑点: ${result.backtestStats.modeledSlippage.toFixed(3)}%`);

    return lines.join("\n");
  }
}
