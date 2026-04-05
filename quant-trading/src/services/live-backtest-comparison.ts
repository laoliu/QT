/**
 * LiveBacktestComparison — compares live trading performance against
 * backtested expectations for the same strategy/period.
 *
 * Use cases:
 *   - Detect strategy drift: live performance diverging from backtest
 *   - Validate backtest assumptions (slippage, fill rates)
 *   - Quantify the "backtest-to-live gap"
 *   - Generate actionable diagnostics (e.g. "slippage 3x higher than modeled")
 *
 * Data sources:
 *   - EquityHistory: actual live equity curve
 *   - BacktestStore: historical backtest results
 *   - TradeRecord[]: actual filled trades from live engine
 *   - Backtest engine: re-run backtest on the same period for fresh comparison
 */
import type { BacktestResult, TradeRecord } from "../types.js";
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
    const backtestStats = {
      totalTrades: btResult?.totalTrades ?? 0,
      winRate: btResult?.winRate ?? 0,
      modeledSlippage: 0.1, // default modeled slippage (0.1%)
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
}
