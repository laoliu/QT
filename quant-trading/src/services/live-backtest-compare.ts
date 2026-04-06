/**
 * LiveBacktestCompare — compares live trading results against backtest results.
 *
 * Aligns equity curves by date, computes metric deltas with significance levels,
 * and produces a structured comparison report suitable for chart rendering and
 * text-based summaries.
 */
import type { BacktestResult, TradeRecord } from "../types.js";
import type { EquitySnapshot } from "./equity-history.js";

// ── Types ────────────────────────────────────────────────────────

export interface MetricDelta {
  /** Metric name in Chinese */
  label: string;
  /** Metric key */
  key: string;
  /** Live value */
  live: number;
  /** Backtest value */
  backtest: number;
  /** Absolute difference (live - backtest) */
  diff: number;
  /** Percentage difference relative to backtest */
  diffPct: number;
  /** Higher is better for this metric (false for drawdown) */
  higherIsBetter: boolean;
  /** "better" | "worse" | "neutral" */
  verdict: "better" | "worse" | "neutral";
  /** Significance level based on deviation magnitude */
  significance: "low" | "medium" | "high";
}

export interface AlignedEquityPoint {
  date: string;
  liveEquity: number | null;
  backtestEquity: number | null;
  /** Absolute gap (live - backtest), null if either side missing */
  gap: number | null;
  /** Percentage gap relative to backtest */
  gapPct: number | null;
}

export interface LiveBacktestComparison {
  /** Aligned date range (intersection of both curves) */
  dateRange: { from: string; to: string };
  /** Aligned equity curves for overlay chart */
  alignedCurve: AlignedEquityPoint[];
  /** Per-metric delta analysis */
  metrics: MetricDelta[];
  /** Text summary suitable for reports */
  summary: string;
  /** Verdict: overall assessment */
  overallVerdict: "consistent" | "divergent" | "inconclusive";
  /** Tracking error (std dev of daily return differences) */
  trackingError: number;
  /** Maximum divergence point */
  maxDivergence: { date: string; gapPct: number } | null;
  /** Correlation of daily returns between live and backtest */
  returnCorrelation: number;
}

export interface LiveMetrics {
  totalReturn: number;
  annualizedReturn: number;
  maxDrawdown: number;
  maxDrawdownPct: number;
  sharpeRatio: number;
  winRate: number;
  totalTrades: number;
  avgDailyPnl: number;
}

// ── Metric definitions ──────────────────────────────────────────

const METRIC_DEFS: Array<{
  key: string;
  label: string;
  higherIsBetter: boolean;
  /** Threshold for "high" significance (absolute diff %) */
  highThreshold: number;
  /** Threshold for "medium" significance (absolute diff %) */
  medThreshold: number;
}> = [
  { key: "totalReturn", label: "总收益率", higherIsBetter: true, highThreshold: 20, medThreshold: 10 },
  { key: "annualizedReturn", label: "年化收益", higherIsBetter: true, highThreshold: 15, medThreshold: 8 },
  { key: "maxDrawdown", label: "最大回撤", higherIsBetter: false, highThreshold: 10, medThreshold: 5 },
  { key: "sharpeRatio", label: "夏普比率", higherIsBetter: true, highThreshold: 0.5, medThreshold: 0.2 },
  { key: "winRate", label: "胜率", higherIsBetter: true, highThreshold: 15, medThreshold: 8 },
  { key: "totalTrades", label: "交易次数", higherIsBetter: false, highThreshold: 50, medThreshold: 20 },
];

// ── Core comparison logic ───────────────────────────────────────

/**
 * Compute live performance metrics from equity snapshots and trade records.
 */
export function computeLiveMetrics(
  snapshots: EquitySnapshot[],
  trades: TradeRecord[],
): LiveMetrics {
  const sorted = [...snapshots].sort((a, b) => a.date.localeCompare(b.date));
  if (sorted.length === 0) {
    return {
      totalReturn: 0,
      annualizedReturn: 0,
      maxDrawdown: 0,
      maxDrawdownPct: 0,
      sharpeRatio: 0,
      winRate: 0,
      totalTrades: 0,
      avgDailyPnl: 0,
    };
  }

  const first = sorted[0]!;
  const last = sorted[sorted.length - 1]!;
  const totalReturn = first.equity > 0 ? ((last.equity - first.equity) / first.equity) * 100 : 0;

  // Annualized return
  const days = Math.max(1, sorted.length);
  const years = days / 252;
  const annualizedReturn =
    years > 0 && first.equity > 0
      ? (Math.pow(last.equity / first.equity, 1 / years) - 1) * 100
      : 0;

  // Max drawdown
  let peak = -Infinity;
  let maxDd = 0;
  let maxDdPct = 0;
  for (const s of sorted) {
    if (s.equity > peak) peak = s.equity;
    const dd = peak - s.equity;
    if (dd > maxDd) {
      maxDd = dd;
      maxDdPct = peak > 0 ? (dd / peak) * 100 : 0;
    }
  }

  // Daily returns for Sharpe
  const dailyReturns: number[] = [];
  for (let i = 1; i < sorted.length; i++) {
    const prev = sorted[i - 1]!.equity;
    const cur = sorted[i]!.equity;
    if (prev > 0) dailyReturns.push((cur - prev) / prev);
  }

  const avgReturn = dailyReturns.length > 0
    ? dailyReturns.reduce((a, b) => a + b, 0) / dailyReturns.length
    : 0;
  const stdDev = dailyReturns.length > 1
    ? Math.sqrt(
        dailyReturns.reduce((sum, r) => sum + (r - avgReturn) ** 2, 0) /
          (dailyReturns.length - 1),
      )
    : 0;
  const sharpeRatio = stdDev > 0 ? (avgReturn / stdDev) * Math.sqrt(252) : 0;

  // Win rate from trades
  const closedTrades = trades.filter((t) => t.exitTime != null);
  const wins = closedTrades.filter((t) => t.netPnl > 0).length;
  const winRate = closedTrades.length > 0 ? (wins / closedTrades.length) * 100 : 0;

  const totalPnl = sorted.reduce((sum, s) => sum + s.dailyPnl, 0);

  return {
    totalReturn,
    annualizedReturn,
    maxDrawdown: maxDdPct,
    maxDrawdownPct: maxDdPct,
    sharpeRatio,
    winRate,
    totalTrades: closedTrades.length,
    avgDailyPnl: sorted.length > 0 ? totalPnl / sorted.length : 0,
  };
}

/**
 * Align live equity snapshots with backtest equity curve by date.
 */
export function alignEquityCurves(
  liveSnapshots: EquitySnapshot[],
  backtestCurve: Array<{ date: string; value: number }>,
  initialCash: number,
): AlignedEquityPoint[] {
  const liveMap = new Map<string, number>();
  for (const s of liveSnapshots) liveMap.set(s.date, s.equity);

  const btMap = new Map<string, number>();
  for (const p of backtestCurve) btMap.set(p.date, p.value);

  // Union of all dates
  const allDates = new Set([...liveMap.keys(), ...btMap.keys()]);
  const sorted = [...allDates].sort();

  return sorted.map((date) => {
    const liveEquity = liveMap.get(date) ?? null;
    const backtestEquity = btMap.get(date) ?? null;

    let gap: number | null = null;
    let gapPct: number | null = null;
    if (liveEquity != null && backtestEquity != null) {
      gap = liveEquity - backtestEquity;
      gapPct = backtestEquity > 0 ? (gap / backtestEquity) * 100 : 0;
    }

    return { date, liveEquity, backtestEquity, gap, gapPct };
  });
}

/**
 * Compare live trading results against backtest results.
 */
export function compareLiveVsBacktest(
  liveSnapshots: EquitySnapshot[],
  trades: TradeRecord[],
  backtestResult: BacktestResult,
): LiveBacktestComparison {
  const liveMetrics = computeLiveMetrics(liveSnapshots, trades);

  const backtestMetricValues: Record<string, number> = {
    totalReturn: backtestResult.totalReturn,
    annualizedReturn: backtestResult.annualizedReturn,
    maxDrawdown: backtestResult.maxDrawdown,
    sharpeRatio: backtestResult.sharpeRatio,
    winRate: backtestResult.winRate,
    totalTrades: backtestResult.totalTrades,
  };

  const liveMetricValues: Record<string, number> = {
    totalReturn: liveMetrics.totalReturn,
    annualizedReturn: liveMetrics.annualizedReturn,
    maxDrawdown: liveMetrics.maxDrawdown,
    sharpeRatio: liveMetrics.sharpeRatio,
    winRate: liveMetrics.winRate,
    totalTrades: liveMetrics.totalTrades,
  };

  // Compute metric deltas
  const metrics: MetricDelta[] = METRIC_DEFS.map((def) => {
    const live = liveMetricValues[def.key] ?? 0;
    const backtest = backtestMetricValues[def.key] ?? 0;
    const diff = live - backtest;
    const diffPct = backtest !== 0 ? (diff / Math.abs(backtest)) * 100 : live !== 0 ? 100 : 0;
    const absDiff = Math.abs(def.key === "sharpeRatio" ? diff : diffPct);

    let verdict: "better" | "worse" | "neutral";
    if (Math.abs(diff) < 0.01) {
      verdict = "neutral";
    } else if (def.higherIsBetter) {
      verdict = diff > 0 ? "better" : "worse";
    } else {
      verdict = diff < 0 ? "better" : "worse";
    }

    let significance: "low" | "medium" | "high";
    const cmpVal = def.key === "sharpeRatio" ? Math.abs(diff) : absDiff;
    if (cmpVal >= def.highThreshold) significance = "high";
    else if (cmpVal >= def.medThreshold) significance = "medium";
    else significance = "low";

    return {
      label: def.label,
      key: def.key,
      live,
      backtest,
      diff,
      diffPct,
      higherIsBetter: def.higherIsBetter,
      verdict,
      significance,
    };
  });

  // Align equity curves
  const alignedCurve = alignEquityCurves(
    liveSnapshots,
    backtestResult.equityCurve,
    backtestResult.initialCash,
  );

  // Filter to overlapping dates only
  const overlapping = alignedCurve.filter(
    (p) => p.liveEquity != null && p.backtestEquity != null,
  );

  // Daily returns for both sides (overlapping only)
  const liveReturns: number[] = [];
  const btReturns: number[] = [];
  for (let i = 1; i < overlapping.length; i++) {
    const prevL = overlapping[i - 1]!.liveEquity!;
    const curL = overlapping[i]!.liveEquity!;
    const prevB = overlapping[i - 1]!.backtestEquity!;
    const curB = overlapping[i]!.backtestEquity!;
    if (prevL > 0 && prevB > 0) {
      liveReturns.push((curL - prevL) / prevL);
      btReturns.push((curB - prevB) / prevB);
    }
  }

  // Tracking error
  const returnDiffs = liveReturns.map((lr, i) => lr - btReturns[i]!);
  const avgDiff =
    returnDiffs.length > 0
      ? returnDiffs.reduce((a, b) => a + b, 0) / returnDiffs.length
      : 0;
  const trackingError =
    returnDiffs.length > 1
      ? Math.sqrt(
          returnDiffs.reduce((sum, d) => sum + (d - avgDiff) ** 2, 0) /
            (returnDiffs.length - 1),
        ) * Math.sqrt(252)
      : 0;

  // Return correlation
  let returnCorrelation = 0;
  if (liveReturns.length > 1) {
    const avgL = liveReturns.reduce((a, b) => a + b, 0) / liveReturns.length;
    const avgB = btReturns.reduce((a, b) => a + b, 0) / btReturns.length;
    let covSum = 0;
    let varL = 0;
    let varB = 0;
    for (let i = 0; i < liveReturns.length; i++) {
      const dl = liveReturns[i]! - avgL;
      const db = btReturns[i]! - avgB;
      covSum += dl * db;
      varL += dl * dl;
      varB += db * db;
    }
    const denom = Math.sqrt(varL * varB);
    returnCorrelation = denom > 0 ? covSum / denom : 0;
  }

  // Max divergence
  let maxDivergence: { date: string; gapPct: number } | null = null;
  for (const p of overlapping) {
    if (p.gapPct != null) {
      if (!maxDivergence || Math.abs(p.gapPct) > Math.abs(maxDivergence.gapPct)) {
        maxDivergence = { date: p.date, gapPct: p.gapPct };
      }
    }
  }

  // Date range
  const dateRange = overlapping.length > 0
    ? { from: overlapping[0]!.date, to: overlapping[overlapping.length - 1]!.date }
    : { from: "", to: "" };

  // Overall verdict
  const highSigCount = metrics.filter(
    (m) => m.significance === "high" && m.verdict === "worse",
  ).length;
  let overallVerdict: "consistent" | "divergent" | "inconclusive";
  if (overlapping.length < 5) {
    overallVerdict = "inconclusive";
  } else if (highSigCount >= 2 || trackingError > 0.3) {
    overallVerdict = "divergent";
  } else {
    overallVerdict = "consistent";
  }

  // Summary text
  const summary = buildSummary(metrics, trackingError, returnCorrelation, overallVerdict, dateRange);

  return {
    dateRange,
    alignedCurve,
    metrics,
    summary,
    overallVerdict,
    trackingError,
    maxDivergence,
    returnCorrelation,
  };
}

// ── Summary builder ─────────────────────────────────────────────

function buildSummary(
  metrics: MetricDelta[],
  trackingError: number,
  correlation: number,
  verdict: string,
  dateRange: { from: string; to: string },
): string {
  const lines: string[] = [];

  lines.push(`## 实盘 vs 回测对比报告`);
  lines.push(`**对比区间**: ${dateRange.from} ~ ${dateRange.to}`);
  lines.push("");

  // Verdict
  const verdictMap: Record<string, string> = {
    consistent: "✅ 实盘表现与回测基本一致",
    divergent: "⚠️ 实盘表现与回测存在显著偏差",
    inconclusive: "ℹ️ 数据量不足，暂无法做出结论",
  };
  lines.push(`**整体评估**: ${verdictMap[verdict] ?? verdict}`);
  lines.push(`**跟踪误差**: ${(trackingError * 100).toFixed(2)}%（年化）`);
  lines.push(`**收益相关性**: ${correlation.toFixed(3)}`);
  lines.push("");

  // Metrics table
  lines.push(`| 指标 | 实盘 | 回测 | 差异 | 评价 |`);
  lines.push(`|------|------|------|------|------|`);
  for (const m of metrics) {
    const fmtVal = (v: number) =>
      m.key === "sharpeRatio"
        ? v.toFixed(2)
        : m.key === "totalTrades"
          ? String(Math.round(v))
          : `${v.toFixed(2)}%`;
    const verdictIcon = m.verdict === "better" ? "🟢" : m.verdict === "worse" ? "🔴" : "⚪";
    const sig = m.significance === "high" ? "⚠️" : "";
    lines.push(
      `| ${m.label} | ${fmtVal(m.live)} | ${fmtVal(m.backtest)} | ${m.diff >= 0 ? "+" : ""}${fmtVal(m.diff)} | ${verdictIcon}${sig} |`,
    );
  }
  lines.push("");

  // Key findings
  const worseMetrics = metrics.filter((m) => m.verdict === "worse" && m.significance !== "low");
  const betterMetrics = metrics.filter((m) => m.verdict === "better" && m.significance !== "low");

  if (worseMetrics.length > 0) {
    lines.push(`### ⚠️ 实盘偏差较大的指标`);
    for (const m of worseMetrics) {
      lines.push(`- **${m.label}**: 实盘 ${m.live.toFixed(2)} vs 回测 ${m.backtest.toFixed(2)}（偏差 ${m.diffPct.toFixed(1)}%）`);
    }
    lines.push("");
  }

  if (betterMetrics.length > 0) {
    lines.push(`### ✅ 实盘优于回测的指标`);
    for (const m of betterMetrics) {
      lines.push(`- **${m.label}**: 实盘 ${m.live.toFixed(2)} vs 回测 ${m.backtest.toFixed(2)}（+${Math.abs(m.diffPct).toFixed(1)}%）`);
    }
    lines.push("");
  }

  return lines.join("\n");
}
