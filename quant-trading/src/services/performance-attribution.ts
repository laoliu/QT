/**
 * PerformanceAttribution — analyzes P&L sources.
 *
 * Breaks down returns by:
 *   - Strategy: which strategy contributed how much
 *   - Symbol: which symbols were most profitable / loss-making
 *   - Time period: daily / weekly / monthly P&L breakdown
 *   - Win/loss analysis: average win size vs average loss size
 *
 * Reads from the TradeRecord store (orders.json / trades.json).
 */
import type { TradeRecord } from "../types.js";

export interface StrategyAttribution {
  strategyId: string;
  trades: number;
  wins: number;
  losses: number;
  winRate: number;
  grossPnl: number;
  netPnl: number;
  avgWin: number;
  avgLoss: number;
  largestWin: number;
  largestLoss: number;
  profitFactor: number; // totalWins / totalLosses
  /** Percentage contribution to total portfolio return. */
  contributionPct: number;
}

export interface SymbolAttribution {
  symbol: string;
  trades: number;
  netPnl: number;
  avgPnl: number;
  winRate: number;
  holdingDaysAvg: number;
  contributionPct: number;
}

export interface PeriodPnl {
  period: string; // YYYY-MM-DD, YYYY-WW, or YYYY-MM
  trades: number;
  netPnl: number;
  cumPnl: number;
  wins: number;
  losses: number;
}

export interface AttributionResult {
  dateRange: { from: string; to: string };
  totalTrades: number;
  totalNetPnl: number;
  totalGrossPnl: number;
  totalCommission: number;
  overallWinRate: number;
  avgWin: number;
  avgLoss: number;
  profitFactor: number;
  maxConsecutiveWins: number;
  maxConsecutiveLosses: number;
  avgHoldingDays: number;
  largestWin: number;
  largestLoss: number;
  expectancy: number;
  byStrategy: StrategyAttribution[];
  bySymbol: SymbolAttribution[];
  byDay: PeriodPnl[];
  byWeek: PeriodPnl[];
  byMonth: PeriodPnl[];
  byYear: PeriodPnl[];
}

// ── Chart data structures ──

export interface EquityPoint {
  date: string;
  value: number;
  drawdown?: number;
}

export interface CumulativePnlPoint {
  date: string;
  pnl: number;
}

export interface ReturnBucket {
  bucketMin: number;
  bucketMax: number;
  count: number;
}

export interface MonthlyHeatmapPoint {
  year: number;
  month: number;
  netPnl: number;
}

export interface DrawdownPoint {
  date: string;
  drawdown: number;
}

export interface DayOfWeekPnl {
  dayOfWeek: string;
  dayIndex: number;
  avgPnl: number;
  totalPnl: number;
  trades: number;
}

export interface ChartData {
  equityCurve: EquityPoint[];
  cumulativePnl: CumulativePnlPoint[];
  dailyReturns: { date: string; return: number }[];
  returnDistribution: ReturnBucket[];
  monthlyHeatmap: MonthlyHeatmapPoint[];
  drawdownSeries: DrawdownPoint[];
  pnlByDayOfWeek: DayOfWeekPnl[];
}

// ── Risk metrics ──

export interface RiskMetrics {
  totalReturn: number;
  annualizedReturn: number;
  annualizedVolatility: number;
  sharpeRatio: number;
  sortinoRatio: number;
  calmarRatio: number;
  maxDrawdown: number;
  var95: number;
  tradingDays: number;
}

/** Get ISO week string YYYY-WNN */
function isoWeek(dateStr: string): string {
  const d = new Date(dateStr);
  const dayNum = d.getUTCDay() || 7;
  d.setUTCDate(d.getUTCDate() + 4 - dayNum);
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  const weekNo = Math.ceil(((d.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
  return `${String(d.getUTCFullYear())}-W${String(weekNo).padStart(2, "0")}`;
}

export function computeAttribution(
  trades: TradeRecord[],
  options?: { from?: string; to?: string },
): AttributionResult {
  // Filter by date range
  let filtered = trades.filter((t) => t.exitTime != null);
  if (options?.from) {
    filtered = filtered.filter((t) => (t.exitTime ?? "") >= options.from!);
  }
  if (options?.to) {
    filtered = filtered.filter((t) => (t.exitTime ?? "") <= options.to!);
  }

  // Sort by exit time
  filtered.sort((a, b) => (a.exitTime ?? "").localeCompare(b.exitTime ?? ""));

  const totalNetPnl = filtered.reduce((s, t) => s + t.netPnl, 0);
  const totalGrossPnl = filtered.reduce((s, t) => s + t.grossPnl, 0);
  const totalCommission = filtered.reduce((s, t) => s + t.commission, 0);

  const wins = filtered.filter((t) => t.netPnl > 0);
  const losses = filtered.filter((t) => t.netPnl <= 0);

  const totalWinAmount = wins.reduce((s, t) => s + t.netPnl, 0);
  const totalLossAmount = Math.abs(losses.reduce((s, t) => s + t.netPnl, 0));

  // ── By Strategy ──

  const stratMap = new Map<
    string,
    { trades: TradeRecord[]; wins: number; losses: number; grossPnl: number; netPnl: number }
  >();
  for (const t of filtered) {
    const sid = t.strategyId ?? "unknown";
    if (!stratMap.has(sid)) {
      stratMap.set(sid, { trades: [], wins: 0, losses: 0, grossPnl: 0, netPnl: 0 });
    }
    const s = stratMap.get(sid)!;
    s.trades.push(t);
    if (t.netPnl > 0) s.wins++;
    else s.losses++;
    s.grossPnl += t.grossPnl;
    s.netPnl += t.netPnl;
  }

  const byStrategy: StrategyAttribution[] = [...stratMap.entries()].map(([sid, s]) => {
    const sWins = s.trades.filter((t) => t.netPnl > 0);
    const sLosses = s.trades.filter((t) => t.netPnl <= 0);
    const sWinTotal = sWins.reduce((sum, t) => sum + t.netPnl, 0);
    const sLossTotal = Math.abs(sLosses.reduce((sum, t) => sum + t.netPnl, 0));
    return {
      strategyId: sid,
      trades: s.trades.length,
      wins: s.wins,
      losses: s.losses,
      winRate: s.trades.length > 0 ? s.wins / s.trades.length : 0,
      grossPnl: s.grossPnl,
      netPnl: s.netPnl,
      avgWin: sWins.length > 0 ? sWinTotal / sWins.length : 0,
      avgLoss: sLosses.length > 0 ? sLossTotal / sLosses.length : 0,
      largestWin: sWins.length > 0 ? Math.max(...sWins.map((t) => t.netPnl)) : 0,
      largestLoss: sLosses.length > 0 ? Math.min(...sLosses.map((t) => t.netPnl)) : 0,
      profitFactor: sLossTotal > 0 ? sWinTotal / sLossTotal : sWinTotal > 0 ? Infinity : 0,
      contributionPct: totalNetPnl !== 0 ? (s.netPnl / Math.abs(totalNetPnl)) * 100 : 0,
    };
  });

  // Sort by absolute PnL contribution desc
  byStrategy.sort((a, b) => Math.abs(b.netPnl) - Math.abs(a.netPnl));

  // ── By Symbol ──

  const symMap = new Map<
    string,
    { trades: TradeRecord[]; netPnl: number; wins: number; holdingDaysTotal: number }
  >();
  for (const t of filtered) {
    if (!symMap.has(t.symbol)) {
      symMap.set(t.symbol, { trades: [], netPnl: 0, wins: 0, holdingDaysTotal: 0 });
    }
    const s = symMap.get(t.symbol)!;
    s.trades.push(t);
    s.netPnl += t.netPnl;
    if (t.netPnl > 0) s.wins++;
    s.holdingDaysTotal += t.holdingDays ?? 0;
  }

  const bySymbol: SymbolAttribution[] = [...symMap.entries()].map(([sym, s]) => ({
    symbol: sym,
    trades: s.trades.length,
    netPnl: s.netPnl,
    avgPnl: s.trades.length > 0 ? s.netPnl / s.trades.length : 0,
    winRate: s.trades.length > 0 ? s.wins / s.trades.length : 0,
    holdingDaysAvg: s.trades.length > 0 ? s.holdingDaysTotal / s.trades.length : 0,
    contributionPct: totalNetPnl !== 0 ? (s.netPnl / Math.abs(totalNetPnl)) * 100 : 0,
  }));
  bySymbol.sort((a, b) => b.netPnl - a.netPnl);

  // ── By Period ──

  function buildPeriodPnl(keyFn: (t: TradeRecord) => string): PeriodPnl[] {
    const map = new Map<string, { trades: number; netPnl: number; wins: number; losses: number }>();
    for (const t of filtered) {
      const key = keyFn(t);
      if (!map.has(key)) map.set(key, { trades: 0, netPnl: 0, wins: 0, losses: 0 });
      const p = map.get(key)!;
      p.trades++;
      p.netPnl += t.netPnl;
      if (t.netPnl > 0) p.wins++;
      else p.losses++;
    }

    const sorted = [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]));
    let cum = 0;
    return sorted.map(([period, p]) => {
      cum += p.netPnl;
      return {
        period,
        trades: p.trades,
        netPnl: p.netPnl,
        cumPnl: cum,
        wins: p.wins,
        losses: p.losses,
      };
    });
  }

  const byDay = buildPeriodPnl((t) => (t.exitTime ?? "").slice(0, 10));
  const byWeek = buildPeriodPnl((t) => isoWeek(t.exitTime ?? ""));
  const byMonth = buildPeriodPnl((t) => (t.exitTime ?? "").slice(0, 7));
  const byYear = buildPeriodPnl((t) => (t.exitTime ?? "").slice(0, 4));

  // ── Consecutive wins/losses ──

  let maxConsecutiveWins = 0;
  let maxConsecutiveLosses = 0;
  let curWins = 0;
  let curLosses = 0;
  for (const t of filtered) {
    if (t.netPnl > 0) {
      curWins++;
      curLosses = 0;
      if (curWins > maxConsecutiveWins) maxConsecutiveWins = curWins;
    } else {
      curLosses++;
      curWins = 0;
      if (curLosses > maxConsecutiveLosses) maxConsecutiveLosses = curLosses;
    }
  }

  // ── Holding days & largest win/loss ──

  let holdingDaysTotal = 0;
  let holdingDaysCount = 0;
  let largestWin = 0;
  let largestLoss = 0;
  for (const t of filtered) {
    if (t.holdingDays != null) {
      holdingDaysTotal += t.holdingDays;
      holdingDaysCount++;
    }
    if (t.netPnl > largestWin) largestWin = t.netPnl;
    if (t.netPnl < largestLoss) largestLoss = t.netPnl;
  }
  const avgHoldingDays = holdingDaysCount > 0 ? holdingDaysTotal / holdingDaysCount : 0;

  // ── Expectancy ──

  const winRate = filtered.length > 0 ? wins.length / filtered.length : 0;
  const avgWin = wins.length > 0 ? totalWinAmount / wins.length : 0;
  const avgLoss = losses.length > 0 ? totalLossAmount / losses.length : 0;
  const expectancy = winRate * avgWin - (1 - winRate) * avgLoss;

  // ── Date range ──

  const dates = filtered.map((t) => t.exitTime ?? "").filter(Boolean);
  const from = dates[0]?.slice(0, 10) ?? "";
  const to = dates[dates.length - 1]?.slice(0, 10) ?? "";

  return {
    dateRange: { from, to },
    totalTrades: filtered.length,
    totalNetPnl,
    totalGrossPnl,
    totalCommission,
    overallWinRate: winRate,
    avgWin,
    avgLoss,
    profitFactor:
      totalLossAmount > 0 ? totalWinAmount / totalLossAmount : totalWinAmount > 0 ? Infinity : 0,
    maxConsecutiveWins,
    maxConsecutiveLosses,
    avgHoldingDays,
    largestWin,
    largestLoss,
    expectancy,
    byStrategy,
    bySymbol,
    byDay,
    byWeek,
    byMonth,
    byYear,
  };
}

// ── Chart data preparation ──

const DOW_NAMES = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

export function prepareChartData(
  trades: TradeRecord[],
  equityCurve?: EquityPoint[],
  initialCash: number = 1000000,
): ChartData {
  const filtered = trades
    .filter((t) => t.exitTime != null)
    .sort((a, b) => (a.exitTime ?? "").localeCompare(b.exitTime ?? ""));

  // 1. Equity curve (pass-through or synthesize)
  let eq: EquityPoint[];
  if (equityCurve && equityCurve.length > 0) {
    eq = equityCurve;
  } else {
    let cum = initialCash;
    eq = filtered.length > 0
      ? [{ date: (filtered[0]!.entryTime ?? "").slice(0, 10), value: initialCash }]
      : [];
    for (const t of filtered) {
      cum += t.netPnl;
      eq.push({ date: (t.exitTime ?? "").slice(0, 10), value: Math.round(cum * 100) / 100 });
    }
  }

  // 2. Cumulative P&L
  let cumPnl = 0;
  const cumulativePnl: CumulativePnlPoint[] = filtered.map((t) => {
    cumPnl += t.netPnl;
    return { date: (t.exitTime ?? "").slice(0, 10), pnl: Math.round(cumPnl * 100) / 100 };
  });

  // 3. Daily returns from equity curve
  const dailyReturns: { date: string; return: number }[] = [];
  for (let i = 1; i < eq.length; i++) {
    const prev = eq[i - 1]!.value;
    const cur = eq[i]!.value;
    const ret = prev > 0 ? cur / prev - 1 : 0;
    dailyReturns.push({ date: eq[i]!.date, return: Math.round(ret * 1e6) / 1e6 });
  }

  // 4. Return distribution histogram
  const tradeReturns = filtered.map((t) => t.netPnl);
  let returnDistribution: ReturnBucket[] = [];
  if (tradeReturns.length > 0) {
    const minR = Math.min(...tradeReturns);
    const maxR = Math.max(...tradeReturns);
    if (minR === maxR) {
      returnDistribution = [{ bucketMin: minR, bucketMax: maxR, count: tradeReturns.length }];
    } else {
      const nBuckets = Math.min(20, Math.max(5, Math.floor(tradeReturns.length / 5)));
      const bucketSize = (maxR - minR) / nBuckets;
      const buckets = new Array<number>(nBuckets).fill(0);
      for (const r of tradeReturns) {
        const idx = Math.min(Math.floor((r - minR) / bucketSize), nBuckets - 1);
        buckets[idx]!++;
      }
      returnDistribution = buckets.map((count, i) => ({
        bucketMin: Math.round((minR + i * bucketSize) * 100) / 100,
        bucketMax: Math.round((minR + (i + 1) * bucketSize) * 100) / 100,
        count,
      }));
    }
  }

  // 5. Monthly heatmap
  const monthlyMap = new Map<string, number>();
  for (const t of filtered) {
    const key = (t.exitTime ?? "").slice(0, 7);
    if (key) monthlyMap.set(key, (monthlyMap.get(key) ?? 0) + t.netPnl);
  }
  const monthlyHeatmap: MonthlyHeatmapPoint[] = [...monthlyMap.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([k, v]) => ({
      year: parseInt(k.slice(0, 4)),
      month: parseInt(k.slice(5, 7)),
      netPnl: Math.round(v * 100) / 100,
    }));

  // 6. Drawdown series
  const drawdownSeries: DrawdownPoint[] = [];
  if (eq.length >= 2) {
    let peak = eq[0]!.value;
    for (const point of eq) {
      if (point.value > peak) peak = point.value;
      const dd = peak > 0 ? (peak - point.value) / peak : 0;
      drawdownSeries.push({ date: point.date, drawdown: Math.round(dd * 1e6) / 1e6 });
    }
  }

  // 7. P&L by day of week
  const dowMap = new Map<number, number[]>();
  for (const t of filtered) {
    const exitTime = t.exitTime ?? "";
    if (exitTime.length >= 10) {
      const d = new Date(exitTime.slice(0, 10));
      if (!isNaN(d.getTime())) {
        const dow = d.getDay();
        if (!dowMap.has(dow)) dowMap.set(dow, []);
        dowMap.get(dow)!.push(t.netPnl);
      }
    }
  }
  const pnlByDayOfWeek: DayOfWeekPnl[] = [...dowMap.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([dow, pnls]) => ({
      dayOfWeek: DOW_NAMES[dow] ?? `Day${dow}`,
      dayIndex: dow,
      avgPnl: Math.round((pnls.reduce((s, v) => s + v, 0) / pnls.length) * 100) / 100,
      totalPnl: Math.round(pnls.reduce((s, v) => s + v, 0) * 100) / 100,
      trades: pnls.length,
    }));

  return {
    equityCurve: eq,
    cumulativePnl,
    dailyReturns,
    returnDistribution,
    monthlyHeatmap,
    drawdownSeries,
    pnlByDayOfWeek,
  };
}

// ── Risk metrics from equity curve ──

export function computeRiskMetrics(
  equityCurve: EquityPoint[],
  riskFreeRate: number = 0.03,
): RiskMetrics | null {
  if (equityCurve.length < 2) return null;

  const values = equityCurve.map((p) => p.value);
  const dailyReturns: number[] = [];
  for (let i = 1; i < values.length; i++) {
    if (values[i - 1]! > 0) dailyReturns.push(values[i]! / values[i - 1]! - 1);
  }
  if (dailyReturns.length === 0) return null;

  const tradingDays = dailyReturns.length;
  const years = tradingDays / 252;
  const totalReturn = values[0]! > 0 ? values[values.length - 1]! / values[0]! - 1 : 0;
  const annReturn =
    years > 0 && totalReturn > -1 ? Math.pow(1 + totalReturn, 1 / years) - 1 : 0;

  // Volatility
  const mean = dailyReturns.reduce((s, r) => s + r, 0) / dailyReturns.length;
  const variance =
    dailyReturns.length > 1
      ? dailyReturns.reduce((s, r) => s + (r - mean) ** 2, 0) / (dailyReturns.length - 1)
      : 0;
  const volatility = Math.sqrt(variance) * Math.sqrt(252);

  // Sharpe
  const rfDaily = riskFreeRate / 252;
  const excess = dailyReturns.map((r) => r - rfDaily);
  const meanExcess = excess.reduce((s, e) => s + e, 0) / excess.length;
  const excessVar = excess.reduce((s, e) => s + (e - meanExcess) ** 2, 0) / excess.length;
  const excessStd = Math.sqrt(excessVar);
  const sharpe = excessStd > 0 ? (meanExcess / excessStd) * Math.sqrt(252) : 0;

  // Sortino
  const downside = excess.map((e) => Math.min(e, 0) ** 2);
  const downsideDev = Math.sqrt(downside.reduce((s, d) => s + d, 0) / downside.length);
  const sortino = downsideDev > 0 ? (meanExcess / downsideDev) * Math.sqrt(252) : 0;

  // Max drawdown
  let peak = values[0]!;
  let maxDd = 0;
  for (const v of values) {
    if (v > peak) peak = v;
    const dd = peak > 0 ? (peak - v) / peak : 0;
    if (dd > maxDd) maxDd = dd;
  }

  const calmar = maxDd > 0 ? annReturn / maxDd : 0;

  // VaR (95%)
  const sorted = [...dailyReturns].sort((a, b) => a - b);
  const varIdx = Math.max(Math.floor(sorted.length * 0.05), 0);
  const var95 = Math.abs(sorted[varIdx] ?? 0);

  return {
    totalReturn: Math.round(totalReturn * 1e4) / 1e4,
    annualizedReturn: Math.round(annReturn * 1e4) / 1e4,
    annualizedVolatility: Math.round(volatility * 1e4) / 1e4,
    sharpeRatio: Math.round(sharpe * 1e4) / 1e4,
    sortinoRatio: Math.round(sortino * 1e4) / 1e4,
    calmarRatio: Math.round(calmar * 1e4) / 1e4,
    maxDrawdown: Math.round(maxDd * 1e4) / 1e4,
    var95: Math.round(var95 * 1e4) / 1e4,
    tradingDays,
  };
}

// ── Text summary generation ──

function formatCny(amount: number): string {
  const prefix = amount >= 0 ? "+" : "";
  return `${prefix}¥${amount.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatPct(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

export function generateTextSummary(
  result: AttributionResult,
  risk?: RiskMetrics | null,
): string {
  const lines: string[] = [];
  lines.push("=" .repeat(60));
  lines.push("            交易绩效报告");
  lines.push("=".repeat(60));
  if (result.dateRange.from) {
    lines.push(`报告期间: ${result.dateRange.from} ~ ${result.dateRange.to}`);
  }
  lines.push("");

  // Overall
  lines.push("── 整体表现 ──────────────────────────────");
  lines.push(`  总交易笔数:    ${result.totalTrades}`);
  lines.push(`  净盈亏:        ${formatCny(result.totalNetPnl)}`);
  lines.push(`  毛盈亏:        ${formatCny(result.totalGrossPnl)}`);
  lines.push(`  手续费合计:    ¥${result.totalCommission.toFixed(2)}`);
  lines.push(`  胜率:          ${formatPct(result.overallWinRate)}`);
  const pfStr = result.profitFactor === Infinity ? "∞" : result.profitFactor.toFixed(2);
  lines.push(`  盈亏比:        ${pfStr}`);
  lines.push(`  平均盈利:      ${formatCny(result.avgWin)}`);
  lines.push(`  平均亏损:      ¥${result.avgLoss.toFixed(2)}`);
  lines.push(`  最大单笔盈利:  ${formatCny(result.largestWin)}`);
  lines.push(`  最大单笔亏损:  ${formatCny(result.largestLoss)}`);
  lines.push(`  连续盈利最长:  ${result.maxConsecutiveWins} 笔`);
  lines.push(`  连续亏损最长:  ${result.maxConsecutiveLosses} 笔`);
  lines.push(`  平均持仓天数:  ${result.avgHoldingDays.toFixed(1)} 天`);
  lines.push(`  期望值:        ${formatCny(result.expectancy)}/笔`);
  lines.push("");

  // Risk metrics
  if (risk) {
    lines.push("── 风险指标 ──────────────────────────────");
    lines.push(`  总收益率:      ${formatPct(risk.totalReturn)}`);
    lines.push(`  年化收益率:    ${formatPct(risk.annualizedReturn)}`);
    lines.push(`  年化波动率:    ${formatPct(risk.annualizedVolatility)}`);
    lines.push(`  夏普比率:      ${risk.sharpeRatio.toFixed(4)}`);
    lines.push(`  索提诺比率:    ${risk.sortinoRatio.toFixed(4)}`);
    lines.push(`  卡尔马比率:    ${risk.calmarRatio.toFixed(4)}`);
    lines.push(`  最大回撤:      ${formatPct(risk.maxDrawdown)}`);
    lines.push(`  VaR(95%):      ${formatPct(risk.var95)}`);
    lines.push(`  交易天数:      ${risk.tradingDays}`);
    lines.push("");
  }

  // Strategy breakdown
  if (result.byStrategy.length > 0) {
    lines.push("── 策略分析 (按净盈亏排序) ────────────────");
    for (const s of result.byStrategy.slice(0, 5)) {
      const sPf = s.profitFactor === Infinity ? "∞" : s.profitFactor.toFixed(2);
      lines.push(`  ${s.strategyId}:`);
      lines.push(
        `    ${s.trades}笔  胜率${formatPct(s.winRate)}  净盈亏${formatCny(s.netPnl)}  盈亏比${sPf}  贡献${s.contributionPct.toFixed(1)}%`,
      );
    }
    lines.push("");
  }

  // Symbol breakdown (top 10)
  if (result.bySymbol.length > 0) {
    lines.push("── 标的分析 (按净盈亏排序) ────────────────");
    for (const s of result.bySymbol.slice(0, 10)) {
      lines.push(`  ${s.symbol}:`);
      lines.push(
        `    ${s.trades}笔  胜率${formatPct(s.winRate)}  净盈亏${formatCny(s.netPnl)}  均持${s.holdingDaysAvg.toFixed(1)}天  贡献${s.contributionPct.toFixed(1)}%`,
      );
    }
    lines.push("");
  }

  // Monthly P&L
  if (result.byMonth.length > 0) {
    lines.push("── 月度盈亏 ──────────────────────────────");
    lines.push(`  ${"月份".padEnd(10)} ${"笔数".padStart(5)} ${"净盈亏".padStart(12)} ${"累计".padStart(12)} ${"胜率".padStart(7)}`);
    lines.push("  " + "-".repeat(48));
    for (const m of result.byMonth) {
      const wr = m.trades > 0 ? formatPct(m.wins / m.trades) : "0.0%";
      lines.push(
        `  ${m.period.padEnd(10)} ${String(m.trades).padStart(5)} ${formatCny(m.netPnl).padStart(12)} ${formatCny(m.cumPnl).padStart(12)} ${wr.padStart(7)}`,
      );
    }
    lines.push("");
  }

  lines.push("=".repeat(60));
  return lines.join("\n");
}

// ── HTML summary generation ──

export function generateHtmlSummary(
  result: AttributionResult,
  risk?: RiskMetrics | null,
  chartData?: ChartData,
): string {
  function pnlColor(val: number): string {
    return val < 0 ? "color:#e74c3c" : "color:#27ae60";
  }
  function pnlHtml(val: number): string {
    return `<span style="${pnlColor(val)}">${formatCny(val)}</span>`;
  }

  const dateRange = result.dateRange.from
    ? `${result.dateRange.from} ~ ${result.dateRange.to}`
    : "";

  const pfStr = result.profitFactor === Infinity ? "∞" : result.profitFactor.toFixed(2);

  const parts: string[] = [];
  parts.push("<!DOCTYPE html>");
  parts.push('<html lang="zh-CN"><head><meta charset="UTF-8">');
  parts.push("<title>交易绩效报告</title>");
  parts.push("<style>");
  parts.push(`
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       max-width: 960px; margin: 0 auto; padding: 20px; background: #f8f9fa; color: #333; }
h1 { text-align: center; border-bottom: 2px solid #2c3e50; padding-bottom: 10px; }
h2 { color: #2c3e50; border-left: 4px solid #3498db; padding-left: 10px; margin-top: 30px; }
.date-range { text-align: center; color: #666; margin-bottom: 20px; }
.metrics-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 12px; margin: 16px 0; }
.metric-card { background: #fff; border-radius: 8px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
.metric-card .label { font-size: 12px; color: #888; text-transform: uppercase; }
.metric-card .value { font-size: 24px; font-weight: 700; margin-top: 4px; }
table { width: 100%; border-collapse: collapse; margin: 12px 0; background: #fff;
        border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
th { background: #2c3e50; color: #fff; padding: 10px 12px; text-align: left; font-size: 13px; }
td { padding: 8px 12px; border-bottom: 1px solid #eee; font-size: 13px; }
tr:hover { background: #f0f7ff; }
.positive { color: #27ae60; }
.negative { color: #e74c3c; }
.chart-placeholder { background: #fff; border-radius: 8px; padding: 20px; margin: 12px 0;
                     box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
.chart-placeholder pre { font-size: 11px; overflow-x: auto; }
`);
  parts.push("</style></head><body>");
  parts.push("<h1>交易绩效报告</h1>");
  if (dateRange) parts.push(`<div class="date-range">${dateRange}</div>`);

  // Overall metrics grid
  parts.push("<h2>整体表现</h2>");
  parts.push('<div class="metrics-grid">');
  const metrics: [string, string, string][] = [
    ["总交易", String(result.totalTrades), ""],
    ["净盈亏", formatCny(result.totalNetPnl), pnlColor(result.totalNetPnl)],
    ["胜率", formatPct(result.overallWinRate), ""],
    ["盈亏比", pfStr, ""],
    ["平均盈利", formatCny(result.avgWin), "color:#27ae60"],
    ["平均亏损", `¥${result.avgLoss.toFixed(2)}`, "color:#e74c3c"],
    ["最大单笔盈利", formatCny(result.largestWin), "color:#27ae60"],
    ["最大单笔亏损", formatCny(result.largestLoss), "color:#e74c3c"],
    ["连续盈利", `${result.maxConsecutiveWins}笔`, ""],
    ["连续亏损", `${result.maxConsecutiveLosses}笔`, ""],
    ["平均持仓", `${result.avgHoldingDays.toFixed(1)}天`, ""],
    ["期望值/笔", formatCny(result.expectancy), pnlColor(result.expectancy)],
  ];
  for (const [label, value, style] of metrics) {
    const styleAttr = style ? ` style="${style}"` : "";
    parts.push(
      `<div class="metric-card"><div class="label">${label}</div><div class="value"${styleAttr}>${value}</div></div>`,
    );
  }
  parts.push("</div>");

  // Risk metrics
  if (risk) {
    parts.push("<h2>风险指标</h2>");
    parts.push('<div class="metrics-grid">');
    const riskMetrics: [string, string, string][] = [
      ["总收益率", formatPct(risk.totalReturn), pnlColor(risk.totalReturn)],
      ["年化收益率", formatPct(risk.annualizedReturn), pnlColor(risk.annualizedReturn)],
      ["年化波动率", formatPct(risk.annualizedVolatility), ""],
      ["夏普比率", risk.sharpeRatio.toFixed(4), ""],
      ["索提诺比率", risk.sortinoRatio.toFixed(4), ""],
      ["卡尔马比率", risk.calmarRatio.toFixed(4), ""],
      ["最大回撤", formatPct(risk.maxDrawdown), "color:#e74c3c"],
      ["VaR(95%)", formatPct(risk.var95), ""],
    ];
    for (const [label, value, style] of riskMetrics) {
      const styleAttr = style ? ` style="${style}"` : "";
      parts.push(
        `<div class="metric-card"><div class="label">${label}</div><div class="value"${styleAttr}>${value}</div></div>`,
      );
    }
    parts.push("</div>");
  }

  // Strategy table
  if (result.byStrategy.length > 0) {
    parts.push("<h2>策略分析</h2>");
    parts.push("<table><thead><tr>");
    parts.push("<th>策略</th><th>笔数</th><th>胜率</th><th>净盈亏</th><th>盈亏比</th><th>贡献</th>");
    parts.push("</tr></thead><tbody>");
    for (const s of result.byStrategy) {
      const sPf = s.profitFactor === Infinity ? "∞" : s.profitFactor.toFixed(2);
      const cls = s.netPnl >= 0 ? "positive" : "negative";
      parts.push(`<tr><td>${s.strategyId}</td><td>${s.trades}</td>`);
      parts.push(`<td>${formatPct(s.winRate)}</td>`);
      parts.push(`<td class="${cls}">${formatCny(s.netPnl)}</td>`);
      parts.push(`<td>${sPf}</td><td>${s.contributionPct.toFixed(1)}%</td></tr>`);
    }
    parts.push("</tbody></table>");
  }

  // Symbol table
  if (result.bySymbol.length > 0) {
    parts.push("<h2>标的分析</h2>");
    parts.push("<table><thead><tr>");
    parts.push("<th>标的</th><th>笔数</th><th>胜率</th><th>净盈亏</th><th>均持天数</th><th>贡献</th>");
    parts.push("</tr></thead><tbody>");
    for (const s of result.bySymbol.slice(0, 20)) {
      const cls = s.netPnl >= 0 ? "positive" : "negative";
      parts.push(`<tr><td>${s.symbol}</td>`);
      parts.push(`<td>${s.trades}</td><td>${formatPct(s.winRate)}</td>`);
      parts.push(`<td class="${cls}">${formatCny(s.netPnl)}</td>`);
      parts.push(`<td>${s.holdingDaysAvg.toFixed(1)}</td><td>${s.contributionPct.toFixed(1)}%</td></tr>`);
    }
    parts.push("</tbody></table>");
  }

  // Monthly P&L table
  if (result.byMonth.length > 0) {
    parts.push("<h2>月度盈亏</h2>");
    parts.push("<table><thead><tr>");
    parts.push("<th>月份</th><th>笔数</th><th>净盈亏</th><th>累计</th><th>胜率</th>");
    parts.push("</tr></thead><tbody>");
    for (const m of result.byMonth) {
      const pnlCls = m.netPnl >= 0 ? "positive" : "negative";
      const cumCls = m.cumPnl >= 0 ? "positive" : "negative";
      const wr = m.trades > 0 ? formatPct(m.wins / m.trades) : "0.0%";
      parts.push(`<tr><td>${m.period}</td><td>${m.trades}</td>`);
      parts.push(`<td class="${pnlCls}">${formatCny(m.netPnl)}</td>`);
      parts.push(`<td class="${cumCls}">${formatCny(m.cumPnl)}</td>`);
      parts.push(`<td>${wr}</td></tr>`);
    }
    parts.push("</tbody></table>");
  }

  // Chart data as JSON
  if (chartData && chartData.equityCurve.length > 0) {
    parts.push("<h2>图表数据</h2>");
    parts.push('<div class="chart-placeholder">');
    parts.push("<p>以下 JSON 可供前端图表库（ECharts / Chart.js 等）直接使用：</p>");
    const chartJson = {
      equityCurve: chartData.equityCurve.slice(-30),
      monthlyHeatmap: chartData.monthlyHeatmap,
      pnlByDayOfWeek: chartData.pnlByDayOfWeek,
    };
    parts.push(`<pre>${JSON.stringify(chartJson, null, 2)}</pre>`);
    parts.push("</div>");
  }

  parts.push("</body></html>");
  return parts.join("\n");
}
