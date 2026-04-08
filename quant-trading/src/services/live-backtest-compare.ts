/**
 * LiveBacktestCompareService 66compare live strategy execution with backtest results.
 *
 * Aligns live and backtest equity curves on the same time period,
 * computes deviation metrics, and triggers decay alerts when live
 * performance diverges significantly from backtest expectations.
 */

// ── Types ────────────────────────────────────────────────────

export interface CompareRequest {
  strategyId: string;
  dateFrom: string;
  dateTo: string;
  accountId?: string;
  backtestId?: string;
}

export interface CurvePoint {
  date: string;
  equity: number;
}

export interface LiveMetrics {
  totalReturn: number;
  sharpeRatio: number;
  maxDrawdown: number;
  avgSlippage: number;
  avgSignalDelay: number;
  equityCurve: CurvePoint[];
}

export interface BacktestMetrics {
  totalReturn: number;
  sharpeRatio: number;
  maxDrawdown: number;
  equityCurve: CurvePoint[];
}

export interface DeviationMetrics {
  returnDeviation: number;
  curveCorrelation: number;
  slippageCost: number;
  decayAlertTriggered: boolean;
}

export interface CompareResult {
  strategyId: string;
  period: { from: string; to: string };
  live: LiveMetrics;
  backtest: BacktestMetrics;
  deviation: DeviationMetrics;
}

export interface DecayAlert {
  strategyId: string;
  detectedAt: string;
  returnDeviation: number;
  threshold: number;
  message: string;
}

export interface DecayAlertConfig {
  returnDeviationThreshold: number;
  perStrategyOverrides?: Record<string, number>;
}

// ── Data source interfaces ──────────────────────────────────

export interface CompareDataSource {
  getLiveEquityCurve(strategyId: string, accountId?: string, dateFrom?: string, dateTo?: string): Promise<CurvePoint[]>;
  getBacktestEquityCurve(strategyId: string, backtestId?: string, dateFrom?: string, dateTo?: string): Promise<CurvePoint[]>;
  getLiveSlippages(strategyId: string, dateFrom?: string, dateTo?: string): Promise<number[]>;
  getLiveSignalDelays(strategyId: string, dateFrom?: string, dateTo?: string): Promise<number[]>;
}

// ── Math helpers ────────────────────────────────────────────

export function calcCorrelation(xs: number[], ys: number[]): number {
  const n = Math.min(xs.length, ys.length);
  if (n < 2) return 0;

  const meanX = xs.slice(0, n).reduce((a, b) => a + b, 0) / n;
  const meanY = ys.slice(0, n).reduce((a, b) => a + b, 0) / n;

  let sumXY = 0;
  let sumX2 = 0;
  let sumY2 = 0;
  for (let i = 0; i < n; i++) {
    const dx = xs[i] - meanX;
    const dy = ys[i] - meanY;
    sumXY += dx * dy;
    sumX2 += dx * dx;
    sumY2 += dy * dy;
  }

  const denom = Math.sqrt(sumX2 * sumY2);
  return denom === 0 ? 0 : sumXY / denom;
}

function calcReturn(curve: CurvePoint[]): number {
  if (curve.length < 2) return 0;
  const initial = curve[0].equity;
  const final = curve[curve.length - 1].equity;
  return initial > 0 ? (final - initial) / initial : 0;
}

function calcDailyReturns(curve: CurvePoint[]): number[] {
  const returns: number[] = [];
  for (let i = 1; i < curve.length; i++) {
    returns.push((curve[i].equity - curve[i - 1].equity) / curve[i - 1].equity);
  }
  return returns;
}

function calcSharpe(returns: number[], rf = 0.03): number {
  if (returns.length < 2) return 0;
  const dailyRf = rf / 252;
  const excess = returns.map((r) => r - dailyRf);
  const mean = excess.reduce((a, b) => a + b, 0) / excess.length;
  const variance = excess.reduce((a, r) => a + (r - mean) ** 2, 0) / (excess.length - 1);
  const std = Math.sqrt(variance);
  return std === 0 ? 0 : (mean / std) * Math.sqrt(252);
}

function calcMaxDd(curve: CurvePoint[]): number {
  if (curve.length === 0) return 0;
  let peak = curve[0].equity;
  let maxDd = 0;
  for (const p of curve) {
    if (p.equity > peak) peak = p.equity;
    const dd = (peak - p.equity) / peak;
    if (dd > maxDd) maxDd = dd;
  }
  return maxDd;
}

/** Align two curves to their overlapping date range. */
export function alignCurves(
  live: CurvePoint[],
  backtest: CurvePoint[],
): { alignedLive: CurvePoint[]; alignedBacktest: CurvePoint[] } {
  const liveDates = new Set(live.map((p) => p.date));
  const backtestDates = new Set(backtest.map((p) => p.date));
  const commonDates = [...liveDates].filter((d) => backtestDates.has(d)).sort();

  const liveMap = new Map(live.map((p) => [p.date, p]));
  const btMap = new Map(backtest.map((p) => [p.date, p]));

  const alignedLive = commonDates.map((d) => liveMap.get(d)!);
  const alignedBacktest = commonDates.map((d) => btMap.get(d)!);

  return { alignedLive, alignedBacktest };
}

// ── Service ─────────────────────────────────────────────────

export class LiveBacktestCompareService {
  private dataSource: CompareDataSource;
  private alertConfig: DecayAlertConfig;
  private alerts: DecayAlert[] = [];

  constructor(dataSource: CompareDataSource, alertConfig?: Partial<DecayAlertConfig>) {
    this.dataSource = dataSource;
    this.alertConfig = {
      returnDeviationThreshold: alertConfig?.returnDeviationThreshold ?? 0.20,
      perStrategyOverrides: alertConfig?.perStrategyOverrides,
    };
  }

  async compare(req: CompareRequest): Promise<CompareResult> {
    const [rawLiveCurve, rawBtCurve, slippages, delays] = await Promise.all([
      this.dataSource.getLiveEquityCurve(req.strategyId, req.accountId, req.dateFrom, req.dateTo),
      this.dataSource.getBacktestEquityCurve(req.strategyId, req.backtestId, req.dateFrom, req.dateTo),
      this.dataSource.getLiveSlippages(req.strategyId, req.dateFrom, req.dateTo),
      this.dataSource.getLiveSignalDelays(req.strategyId, req.dateFrom, req.dateTo),
    ]);

    // Align curves to common date range
    const { alignedLive, alignedBacktest } = alignCurves(rawLiveCurve, rawBtCurve);

    const liveReturn = calcReturn(alignedLive);
    const btReturn = calcReturn(alignedBacktest);
    const liveDailyReturns = calcDailyReturns(alignedLive);
    const btDailyReturns = calcDailyReturns(alignedBacktest);

    const avgSlippage = slippages.length > 0
      ? slippages.reduce((a, b) => a + b, 0) / slippages.length
      : 0;
    const avgSignalDelay = delays.length > 0
      ? delays.reduce((a, b) => a + b, 0) / delays.length
      : 0;

    const returnDeviation = btReturn !== 0
      ? liveReturn / btReturn - 1
      : (liveReturn !== 0 ? Infinity : 0);
    const curveCorrelation = calcCorrelation(
      alignedLive.map((p) => p.equity),
      alignedBacktest.map((p) => p.equity),
    );
    // Slippage cost is proportional to the number of trades (slippages array),
    // not the number of days in the curve.
    const slippageCost = avgSlippage * (slippages.length || 1);

    // Check decay alert
    const threshold = this.alertConfig.perStrategyOverrides?.[req.strategyId]
      ?? this.alertConfig.returnDeviationThreshold;
    const decayAlertTriggered = Math.abs(returnDeviation) > threshold;

    if (decayAlertTriggered) {
      this.alerts.push({
        strategyId: req.strategyId,
        detectedAt: new Date().toISOString(),
        returnDeviation,
        threshold,
        message: `Strategy ${req.strategyId} live return deviates ${(returnDeviation * 100).toFixed(1)}% from backtest (threshold: ${(threshold * 100).toFixed(0)}%)`,
      });
    }

    return {
      strategyId: req.strategyId,
      period: { from: req.dateFrom, to: req.dateTo },
      live: {
        totalReturn: liveReturn,
        sharpeRatio: calcSharpe(liveDailyReturns),
        maxDrawdown: calcMaxDd(alignedLive),
        avgSlippage,
        avgSignalDelay,
        equityCurve: alignedLive,
      },
      backtest: {
        totalReturn: btReturn,
        sharpeRatio: calcSharpe(btDailyReturns),
        maxDrawdown: calcMaxDd(alignedBacktest),
        equityCurve: alignedBacktest,
      },
      deviation: {
        returnDeviation,
        curveCorrelation,
        slippageCost,
        decayAlertTriggered,
      },
    };
  }

  getDecayAlerts(): DecayAlert[] {
    return [...this.alerts];
  }

  clearAlerts(): void {
    this.alerts = [];
  }
}
