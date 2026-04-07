/**
 * PerformanceMetrics — 核心绩效指标计算引擎
 *
 * 从交易记录（TradeRecord[]）和权益快照（EquitySnapshot[]）计算完整的
 * 交易绩效指标，适用于实盘、模拟盘的绩效报告生成。
 *
 * 指标覆盖：
 *   - 收益：总收益率、年化收益率
 *   - 风险调整：夏普比率、索提诺比率、卡玛比率
 *   - 回撤：最大回撤、最大回撤持续天数、当前回撤
 *   - 交易质量：胜率、盈亏比、利润因子、期望值
 *   - 持仓：平均持仓周期、最长持仓、最短持仓
 *   - 连续性：最大连胜次数、最大连亏次数
 *   - 成本：总手续费、手续费占比
 */
import type { TradeRecord } from "../types.js";
import type { EquitySnapshot } from "./equity-history.js";

// ── Result types ────────────────────────────────────────────

export interface PerformanceMetricsResult {
  // ── 收益指标 ──
  /** 总收益（净值） */
  totalPnl: number;
  /** 总收益率 (%) */
  totalReturnPct: number;
  /** 年化收益率 (%) */
  annualizedReturnPct: number;

  // ── 风险调整指标 ──
  /** 夏普比率 (无风险利率默认 2%) */
  sharpeRatio: number;
  /** 索提诺比率 (仅计算下行波动) */
  sortinoRatio: number;
  /** 卡玛比率 (年化收益 / 最大回撤) */
  calmarRatio: number;

  // ── 回撤指标 ──
  /** 最大回撤 (%) */
  maxDrawdownPct: number;
  /** 最大回撤金额 */
  maxDrawdownAmount: number;
  /** 最大回撤持续天数 */
  maxDrawdownDays: number;
  /** 当前回撤 (%) */
  currentDrawdownPct: number;

  // ── 交易质量指标 ──
  /** 总交易次数 */
  totalTrades: number;
  /** 盈利交易次数 */
  winningTrades: number;
  /** 亏损交易次数 */
  losingTrades: number;
  /** 胜率 (%) */
  winRate: number;
  /** 盈亏比 (平均盈利 / 平均亏损) */
  payoffRatio: number;
  /** 利润因子 (总盈利 / 总亏损) */
  profitFactor: number;
  /** 期望值 (每笔交易的数学期望收益) */
  expectancy: number;
  /** 平均每笔交易收益 */
  avgTradeReturn: number;

  // ── 持仓周期指标 ──
  /** 平均持仓天数 */
  avgHoldingDays: number;
  /** 最长持仓天数 */
  maxHoldingDays: number;
  /** 最短持仓天数 */
  minHoldingDays: number;

  // ── 连续性指标 ──
  /** 最大连胜次数 */
  maxConsecutiveWins: number;
  /** 最大连亏次数 */
  maxConsecutiveLosses: number;

  // ── 成本指标 ──
  /** 总手续费 */
  totalCommission: number;
  /** 手续费占总盈利比例 (%) */
  commissionRatio: number;

  // ── 元数据 ──
  /** 交易天数跨度 */
  tradingDays: number;
  /** 计算时间 */
  calculatedAt: string;
}

export interface PerformanceMetricsOptions {
  /** 初始资金（用于计算收益率），默认 100000 */
  initialCapital?: number;
  /** 无风险年化利率（用于夏普/索提诺），默认 0.02 (2%) */
  riskFreeRate?: number;
  /** 年化交易日数，默认 252 */
  tradingDaysPerYear?: number;
}

const DEFAULTS: Required<PerformanceMetricsOptions> = {
  initialCapital: 100_000,
  riskFreeRate: 0.02,
  tradingDaysPerYear: 252,
};

// ── Core computation ────────────────────────────────────────

/**
 * 从交易记录计算完整的绩效指标。
 *
 * @param trades 已平仓的交易记录（必须有 exitTime）
 * @param equitySnapshots 可选的权益快照（用于更精确的回撤和夏普计算）
 * @param options 计算选项
 */
export function computePerformanceMetrics(
  trades: TradeRecord[],
  equitySnapshots?: EquitySnapshot[],
  options?: PerformanceMetricsOptions,
): PerformanceMetricsResult {
  const opts = { ...DEFAULTS, ...options };

  // Filter to closed trades only, sort by exit time
  const closed = trades
    .filter((t) => t.exitTime != null)
    .sort((a, b) => (a.exitTime ?? "").localeCompare(b.exitTime ?? ""));

  if (closed.length === 0) {
    return emptyResult(opts);
  }

  // ── 收益指标 ──
  const totalPnl = closed.reduce((s, t) => s + t.netPnl, 0);
  const totalGrossPnl = closed.reduce((s, t) => s + t.grossPnl, 0);
  const totalCommission = closed.reduce((s, t) => s + t.commission, 0);
  const totalReturnPct = (totalPnl / opts.initialCapital) * 100;

  // 交易跨度天数
  const firstDate = new Date(closed[0]!.entryTime);
  const lastDate = new Date(closed[closed.length - 1]!.exitTime!);
  const calendarDays = Math.max(1, (lastDate.getTime() - firstDate.getTime()) / 86_400_000);
  const tradingDays = Math.max(1, Math.round(calendarDays * (opts.tradingDaysPerYear / 365)));

  // 年化收益率
  const years = calendarDays / 365;
  const annualizedReturnPct =
    years >= 1 / 365
      ? ((1 + totalPnl / opts.initialCapital) ** (1 / years) - 1) * 100
      : totalReturnPct;

  // ── 胜率 / 盈亏分析 ──
  const winners = closed.filter((t) => t.netPnl > 0);
  const losers = closed.filter((t) => t.netPnl <= 0);
  const winRate = (winners.length / closed.length) * 100;

  const totalWinAmount = winners.reduce((s, t) => s + t.netPnl, 0);
  const totalLossAmount = Math.abs(losers.reduce((s, t) => s + t.netPnl, 0));

  const avgWin = winners.length > 0 ? totalWinAmount / winners.length : 0;
  const avgLoss = losers.length > 0 ? totalLossAmount / losers.length : 0;

  const payoffRatio = avgLoss > 0 ? avgWin / avgLoss : avgWin > 0 ? Infinity : 0;
  const profitFactor =
    totalLossAmount > 0
      ? totalWinAmount / totalLossAmount
      : totalWinAmount > 0
        ? Infinity
        : 0;

  // 期望值 = (胜率 × 平均盈利) - (败率 × 平均亏损)
  const winP = winners.length / closed.length;
  const loseP = losers.length / closed.length;
  const expectancy = winP * avgWin - loseP * avgLoss;

  const avgTradeReturn = totalPnl / closed.length;

  // ── 持仓周期 ──
  const holdingDaysList = closed
    .map((t) => t.holdingDays ?? computeHoldingDays(t.entryTime, t.exitTime!))
    .filter((d) => d > 0);
  const avgHoldingDays =
    holdingDaysList.length > 0
      ? holdingDaysList.reduce((s, d) => s + d, 0) / holdingDaysList.length
      : 0;
  const maxHoldingDays = holdingDaysList.length > 0 ? Math.max(...holdingDaysList) : 0;
  const minHoldingDays = holdingDaysList.length > 0 ? Math.min(...holdingDaysList) : 0;

  // ── 连续胜负 ──
  const { maxConsecutiveWins, maxConsecutiveLosses } = computeStreaks(closed);

  // ── 回撤指标 ──
  const drawdownResult = computeDrawdown(closed, equitySnapshots, opts);

  // ── 风险调整指标（夏普、索提诺、卡玛）──
  const riskAdjusted = computeRiskAdjustedMetrics(
    closed,
    equitySnapshots,
    opts,
    annualizedReturnPct,
    drawdownResult.maxDrawdownPct,
  );

  // ── 成本指标 ──
  const commissionRatio = totalGrossPnl > 0 ? (totalCommission / totalGrossPnl) * 100 : 0;

  return {
    totalPnl,
    totalReturnPct,
    annualizedReturnPct,

    sharpeRatio: riskAdjusted.sharpeRatio,
    sortinoRatio: riskAdjusted.sortinoRatio,
    calmarRatio: riskAdjusted.calmarRatio,

    maxDrawdownPct: drawdownResult.maxDrawdownPct,
    maxDrawdownAmount: drawdownResult.maxDrawdownAmount,
    maxDrawdownDays: drawdownResult.maxDrawdownDays,
    currentDrawdownPct: drawdownResult.currentDrawdownPct,

    totalTrades: closed.length,
    winningTrades: winners.length,
    losingTrades: losers.length,
    winRate,
    payoffRatio,
    profitFactor,
    expectancy,
    avgTradeReturn,

    avgHoldingDays,
    maxHoldingDays,
    minHoldingDays,

    maxConsecutiveWins,
    maxConsecutiveLosses,

    totalCommission,
    commissionRatio,

    tradingDays,
    calculatedAt: new Date().toISOString(),
  };
}

// ── Helpers ─────────────────────────────────────────────────

/** 从 entryTime / exitTime 计算持仓天数 */
function computeHoldingDays(entryTime: string, exitTime: string): number {
  const entry = new Date(entryTime);
  const exit = new Date(exitTime);
  return Math.max(1, Math.round((exit.getTime() - entry.getTime()) / 86_400_000));
}

/** 计算最大连胜 / 最大连亏 */
function computeStreaks(trades: TradeRecord[]): {
  maxConsecutiveWins: number;
  maxConsecutiveLosses: number;
} {
  let maxWins = 0;
  let maxLosses = 0;
  let currentWins = 0;
  let currentLosses = 0;

  for (const t of trades) {
    if (t.netPnl > 0) {
      currentWins++;
      currentLosses = 0;
      if (currentWins > maxWins) maxWins = currentWins;
    } else {
      currentLosses++;
      currentWins = 0;
      if (currentLosses > maxLosses) maxLosses = currentLosses;
    }
  }

  return { maxConsecutiveWins: maxWins, maxConsecutiveLosses: maxLosses };
}

/**
 * 计算回撤指标。
 * 优先使用权益快照（更精确），否则从交易记录构建简化权益曲线。
 */
function computeDrawdown(
  trades: TradeRecord[],
  equitySnapshots: EquitySnapshot[] | undefined,
  opts: Required<PerformanceMetricsOptions>,
): {
  maxDrawdownPct: number;
  maxDrawdownAmount: number;
  maxDrawdownDays: number;
  currentDrawdownPct: number;
} {
  // 构建权益序列
  let equitySeries: Array<{ date: string; equity: number }>;

  if (equitySnapshots && equitySnapshots.length > 0) {
    // 使用权益快照
    equitySeries = [...equitySnapshots]
      .sort((a, b) => a.date.localeCompare(b.date))
      .map((s) => ({ date: s.date, equity: s.equity }));
  } else {
    // 从交易记录构建累计权益曲线
    equitySeries = buildEquityCurveFromTrades(trades, opts.initialCapital);
  }

  if (equitySeries.length === 0) {
    return { maxDrawdownPct: 0, maxDrawdownAmount: 0, maxDrawdownDays: 0, currentDrawdownPct: 0 };
  }

  let peak = -Infinity;
  let maxDdPct = 0;
  let maxDdAmount = 0;
  let peakDate = equitySeries[0]!.date;
  let maxDdDays = 0;
  let currentDdStartDate = equitySeries[0]!.date;
  let inDrawdown = false;

  for (const point of equitySeries) {
    if (point.equity > peak) {
      peak = point.equity;
      peakDate = point.date;
      inDrawdown = false;
    }

    const ddAmount = peak - point.equity;
    const ddPct = peak > 0 ? (ddAmount / peak) * 100 : 0;

    if (ddAmount > 0 && !inDrawdown) {
      inDrawdown = true;
      currentDdStartDate = peakDate;
    }

    if (ddPct > maxDdPct) {
      maxDdPct = ddPct;
      maxDdAmount = ddAmount;
      const daysDiff = daysBetween(currentDdStartDate, point.date);
      if (daysDiff > maxDdDays) maxDdDays = daysDiff;
    }
  }

  // 当前回撤
  const lastEquity = equitySeries[equitySeries.length - 1]!.equity;
  const currentDdPct = peak > 0 ? ((peak - lastEquity) / peak) * 100 : 0;

  return {
    maxDrawdownPct: maxDdPct,
    maxDrawdownAmount: maxDdAmount,
    maxDrawdownDays: maxDdDays,
    currentDrawdownPct: currentDdPct,
  };
}

/** 从交易记录按退出日期构建累计权益曲线 */
function buildEquityCurveFromTrades(
  trades: TradeRecord[],
  initialCapital: number,
): Array<{ date: string; equity: number }> {
  if (trades.length === 0) return [];

  // 按退出日期分组累加 PnL
  const dailyPnl = new Map<string, number>();
  for (const t of trades) {
    const date = (t.exitTime ?? "").slice(0, 10);
    if (!date) continue;
    dailyPnl.set(date, (dailyPnl.get(date) ?? 0) + t.netPnl);
  }

  const sortedDates = [...dailyPnl.keys()].sort();
  let cumEquity = initialCapital;
  return sortedDates.map((date) => {
    cumEquity += dailyPnl.get(date)!;
    return { date, equity: cumEquity };
  });
}

/** 两个日期字符串之间的天数差 */
function daysBetween(d1: string, d2: string): number {
  const t1 = new Date(d1).getTime();
  const t2 = new Date(d2).getTime();
  return Math.max(0, Math.round(Math.abs(t2 - t1) / 86_400_000));
}

/**
 * 计算风险调整指标（夏普、索提诺、卡玛）。
 * 优先使用权益快照的日收益率，否则从交易序列构建。
 */
function computeRiskAdjustedMetrics(
  trades: TradeRecord[],
  equitySnapshots: EquitySnapshot[] | undefined,
  opts: Required<PerformanceMetricsOptions>,
  annualizedReturnPct: number,
  maxDrawdownPct: number,
): {
  sharpeRatio: number;
  sortinoRatio: number;
  calmarRatio: number;
} {
  // 获取日收益率序列
  let dailyReturns: number[];

  if (equitySnapshots && equitySnapshots.length >= 2) {
    // 从权益快照计算日收益率
    const sorted = [...equitySnapshots].sort((a, b) => a.date.localeCompare(b.date));
    dailyReturns = [];
    for (let i = 1; i < sorted.length; i++) {
      const prev = sorted[i - 1]!.equity;
      if (prev > 0) {
        dailyReturns.push((sorted[i]!.equity - prev) / prev);
      }
    }
  } else {
    // 从交易记录按日构建收益率
    dailyReturns = buildDailyReturnsFromTrades(trades, opts.initialCapital);
  }

  const n = dailyReturns.length;
  if (n < 2) {
    return { sharpeRatio: 0, sortinoRatio: 0, calmarRatio: 0 };
  }

  const dailyRiskFree = opts.riskFreeRate / opts.tradingDaysPerYear;
  const excessReturns = dailyReturns.map((r) => r - dailyRiskFree);

  // 平均日超额收益
  const avgExcess = excessReturns.reduce((s, r) => s + r, 0) / n;

  // ── 夏普比率 ──
  const variance = excessReturns.reduce((s, r) => s + (r - avgExcess) ** 2, 0) / (n - 1);
  const stdDev = Math.sqrt(variance);
  const sharpeRatio = stdDev > 0 ? (avgExcess / stdDev) * Math.sqrt(opts.tradingDaysPerYear) : 0;

  // ── 索提诺比率 ──（仅用下行偏差）
  const negativeExcess = excessReturns.filter((r) => r < 0);
  const downsideVariance =
    negativeExcess.length > 0
      ? negativeExcess.reduce((s, r) => s + r ** 2, 0) / negativeExcess.length
      : 0;
  const downsideDev = Math.sqrt(downsideVariance);
  const sortinoRatio =
    downsideDev > 0 ? (avgExcess / downsideDev) * Math.sqrt(opts.tradingDaysPerYear) : 0;

  // ── 卡玛比率 ──
  const calmarRatio =
    maxDrawdownPct > 0 ? annualizedReturnPct / maxDrawdownPct : annualizedReturnPct > 0 ? Infinity : 0;

  return { sharpeRatio, sortinoRatio, calmarRatio };
}

/** 从交易记录构建日收益率序列 */
function buildDailyReturnsFromTrades(trades: TradeRecord[], initialCapital: number): number[] {
  if (trades.length === 0) return [];

  const dailyPnl = new Map<string, number>();
  for (const t of trades) {
    const date = (t.exitTime ?? "").slice(0, 10);
    if (!date) continue;
    dailyPnl.set(date, (dailyPnl.get(date) ?? 0) + t.netPnl);
  }

  const sortedDates = [...dailyPnl.keys()].sort();
  const returns: number[] = [];
  let cumEquity = initialCapital;

  for (const date of sortedDates) {
    const pnl = dailyPnl.get(date)!;
    const dailyReturn = cumEquity > 0 ? pnl / cumEquity : 0;
    returns.push(dailyReturn);
    cumEquity += pnl;
  }

  return returns;
}

/** 返回空指标（无交易时） */
function emptyResult(opts: Required<PerformanceMetricsOptions>): PerformanceMetricsResult {
  return {
    totalPnl: 0,
    totalReturnPct: 0,
    annualizedReturnPct: 0,
    sharpeRatio: 0,
    sortinoRatio: 0,
    calmarRatio: 0,
    maxDrawdownPct: 0,
    maxDrawdownAmount: 0,
    maxDrawdownDays: 0,
    currentDrawdownPct: 0,
    totalTrades: 0,
    winningTrades: 0,
    losingTrades: 0,
    winRate: 0,
    payoffRatio: 0,
    profitFactor: 0,
    expectancy: 0,
    avgTradeReturn: 0,
    avgHoldingDays: 0,
    maxHoldingDays: 0,
    minHoldingDays: 0,
    maxConsecutiveWins: 0,
    maxConsecutiveLosses: 0,
    totalCommission: 0,
    commissionRatio: 0,
    tradingDays: 0,
    calculatedAt: new Date().toISOString(),
  };
}

// ── Formatting utility ──────────────────────────────────────

/**
 * 将绩效指标格式化为可读的报告文本。
 */
export function formatPerformanceReport(metrics: PerformanceMetricsResult): string {
  const lines: string[] = [];
  const hr = "─".repeat(50);

  lines.push("📊 交易绩效报告");
  lines.push(`计算时间: ${metrics.calculatedAt.slice(0, 19).replace("T", " ")}`);
  lines.push(hr);

  lines.push("");
  lines.push("【收益指标】");
  lines.push(`  总收益:           ¥${fmt(metrics.totalPnl)}`);
  lines.push(`  总收益率:         ${fmtPct(metrics.totalReturnPct)}`);
  lines.push(`  年化收益率:       ${fmtPct(metrics.annualizedReturnPct)}`);

  lines.push("");
  lines.push("【风险调整指标】");
  lines.push(`  夏普比率:         ${fmt(metrics.sharpeRatio, 3)}`);
  lines.push(`  索提诺比率:       ${fmt(metrics.sortinoRatio, 3)}`);
  lines.push(`  卡玛比率:         ${fmtRatio(metrics.calmarRatio)}`);

  lines.push("");
  lines.push("【回撤指标】");
  lines.push(`  最大回撤:         ${fmtPct(metrics.maxDrawdownPct)}`);
  lines.push(`  最大回撤金额:     ¥${fmt(metrics.maxDrawdownAmount)}`);
  lines.push(`  最大回撤持续:     ${metrics.maxDrawdownDays} 天`);
  lines.push(`  当前回撤:         ${fmtPct(metrics.currentDrawdownPct)}`);

  lines.push("");
  lines.push("【交易质量】");
  lines.push(
    `  总交易次数:       ${metrics.totalTrades} (盈 ${metrics.winningTrades} / 亏 ${metrics.losingTrades})`,
  );
  lines.push(`  胜率:             ${fmtPct(metrics.winRate)}`);
  lines.push(`  盈亏比:           ${fmtRatio(metrics.payoffRatio)}`);
  lines.push(`  利润因子:         ${fmtRatio(metrics.profitFactor)}`);
  lines.push(`  期望值:           ¥${fmt(metrics.expectancy)}`);
  lines.push(`  平均每笔收益:     ¥${fmt(metrics.avgTradeReturn)}`);

  lines.push("");
  lines.push("【持仓周期】");
  lines.push(`  平均持仓:         ${fmt(metrics.avgHoldingDays, 1)} 天`);
  lines.push(`  最长持仓:         ${metrics.maxHoldingDays} 天`);
  lines.push(`  最短持仓:         ${metrics.minHoldingDays} 天`);

  lines.push("");
  lines.push("【连续性】");
  lines.push(`  最大连胜:         ${metrics.maxConsecutiveWins} 次`);
  lines.push(`  最大连亏:         ${metrics.maxConsecutiveLosses} 次`);

  lines.push("");
  lines.push("【交易成本】");
  lines.push(`  总手续费:         ¥${fmt(metrics.totalCommission)}`);
  lines.push(`  手续费占比:       ${fmtPct(metrics.commissionRatio)}`);

  lines.push("");
  lines.push(`交易天数跨度: ${metrics.tradingDays} 天`);
  lines.push(hr);

  return lines.join("\n");
}

function fmt(value: number, decimals = 2): string {
  if (!Number.isFinite(value)) return "N/A";
  return value.toFixed(decimals);
}

function fmtPct(value: number): string {
  if (!Number.isFinite(value)) return "N/A";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function fmtRatio(value: number): string {
  if (value === Infinity) return "∞";
  if (!Number.isFinite(value)) return "N/A";
  return value.toFixed(3);
}
