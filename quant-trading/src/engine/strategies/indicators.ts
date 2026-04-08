/**
 * Strategy interface and shared indicators for the builtin backtest engine.
 */
import type { KlineBar, TradingSignal } from "../../types.js";

// ── Strategy Interface ───────────────────────────────────────────

export type BuiltinStrategyContext = {
  bars: KlineBar[];
  index: number;
  position: number; // current holding qty (0 = flat)
  cash: number;
  params: Record<string, unknown>;
  /** All signals emitted so far. */
  signals: TradingSignal[];
  /** Pre-computed close prices array (shared across strategies to avoid redundant .map()). */
  closes?: number[];
};

export type BuiltinStrategy = {
  name: string;
  /** Called once before the loop; use to compute indicators. */
  init?(ctx: BuiltinStrategyContext): void;
  /** Called on every bar. Return signals. */
  next(ctx: BuiltinStrategyContext): TradingSignal | TradingSignal[] | null;
};

/** Helper: get pre-computed closes or compute from bars (avoids redundant .map()). */
export function getCloses(ctx: BuiltinStrategyContext): number[] {
  if (!ctx.closes) ctx.closes = ctx.bars.map((b) => b.close);
  return ctx.closes;
}

// ── Indicators ───────────────────────────────────────────────────

/** Simple Moving Average. Returns NaN for indices before the period is ready. */
export function sma(data: number[], period: number): number[] {
  if (period <= 0) throw new Error(`sma: period must be > 0, got ${period}`);
  const n = data.length;
  const result: number[] = new Array(n);

  // Check if any value in the data is NaN (e.g. from upstream indicator warmup).
  // If so, fall back to a NaN-aware path; otherwise use the fast sliding window.
  let hasNaN = false;
  for (let i = 0; i < n; i++) {
    if (Number.isNaN(data[i]!)) {
      hasNaN = true;
      break;
    }
  }

  if (!hasNaN) {
    // Fast path: sliding window O(n)
    let windowSum = 0;
    for (let i = 0; i < n; i++) {
      windowSum += data[i]!;
      if (i >= period) windowSum -= data[i - period]!;
      result[i] = i < period - 1 ? NaN : windowSum / period;
    }
  } else {
    // NaN-aware path: sliding window that resets when NaN enters/leaves
    let windowSum = 0;
    let nanCount = 0;
    for (let i = 0; i < n; i++) {
      const entering = data[i]!;
      if (Number.isNaN(entering)) nanCount++;
      else windowSum += entering;

      if (i >= period) {
        const leaving = data[i - period]!;
        if (Number.isNaN(leaving)) nanCount--;
        else windowSum -= leaving;
      }
      result[i] = i < period - 1 || nanCount > 0 ? NaN : windowSum / period;
    }
  }

  return result;
}

/**
 * Rolling N-bar high/low arrays (excluding the current bar — no look-ahead).
 * Returns NaN for indices before the lookback period is ready.
 * Uses monotonic deques for O(n) computation instead of O(n×lookback).
 */
export function rollingHighLow(
  bars: KlineBar[],
  lookback: number,
): { highs: number[]; lows: number[] } {
  if (lookback <= 0) throw new Error(`rollingHighLow: lookback must be > 0, got ${lookback}`);
  const n = bars.length;
  const highs: number[] = new Array(n);
  const lows: number[] = new Array(n);
  // Monotonic deques storing indices into bars (window [i-lookback, i-1])
  const maxDeque: number[] = []; // decreasing highs
  const minDeque: number[] = []; // increasing lows

  for (let i = 0; i < n; i++) {
    if (i < lookback) {
      highs[i] = NaN;
      lows[i] = NaN;
    } else {
      highs[i] = bars[maxDeque[0]!]!.high;
      lows[i] = bars[minDeque[0]!]!.low;
    }
    // Add current bar to deques (it becomes part of future windows)
    while (maxDeque.length > 0 && bars[maxDeque[maxDeque.length - 1]!]!.high <= bars[i]!.high) {
      maxDeque.pop();
    }
    maxDeque.push(i);
    while (minDeque.length > 0 && bars[minDeque[minDeque.length - 1]!]!.low >= bars[i]!.low) {
      minDeque.pop();
    }
    minDeque.push(i);
    // Evict elements outside window [i+1-lookback, i] for next iteration's lookup
    if (maxDeque[0]! <= i - lookback) maxDeque.shift();
    if (minDeque[0]! <= i - lookback) minDeque.shift();
  }
  return { highs, lows };
}

/**
 * Bollinger Bands (middle + upper + lower).
 * Built on top of SMA. Uses rolling sum-of-squares for O(n) std dev computation.
 * Returns NaN for early indices.
 */
export function bollingerBands(
  closes: number[],
  period: number,
  stdDev: number,
): { middle: number[]; upper: number[]; lower: number[] } {
  if (period <= 0) throw new Error(`bollingerBands: period must be > 0, got ${period}`);
  if (stdDev < 0) throw new Error(`bollingerBands: stdDev must be >= 0, got ${stdDev}`);
  const middle = sma(closes, period);
  const n = closes.length;
  const upper: number[] = new Array(n);
  const lower: number[] = new Array(n);
  const divisor = period > 1 ? period - 1 : period;

  // Rolling window: maintain sum and sumSq for O(1) per bar variance
  let windowSum = 0;
  let windowSumSq = 0;
  for (let i = 0; i < n; i++) {
    const v = closes[i]!;
    windowSum += v;
    windowSumSq += v * v;
    if (i >= period) {
      const leaving = closes[i - period]!;
      windowSum -= leaving;
      windowSumSq -= leaving * leaving;
    }
    if (i < period - 1) {
      upper[i] = NaN;
      lower[i] = NaN;
    } else {
      const mean = middle[i]!;
      // Var = E[X²] - E[X]² (using rolling sums)
      const variance = windowSumSq / period - (windowSum / period) ** 2;
      // Adjust from population to sample variance: var_sample = var_pop * n/(n-1)
      const sd = Math.sqrt(Math.max(0, (variance * period) / divisor));
      upper[i] = mean + stdDev * sd;
      lower[i] = mean - stdDev * sd;
    }
  }
  return { middle, upper, lower };
}

/**
 * Exponential Moving Average.
 * Uses the standard smoothing factor k = 2/(period+1).
 * Returns NaN for indices before the first SMA seed is ready.
 */
export function ema(data: number[], period: number): number[] {
  if (period <= 0) throw new Error(`ema: period must be > 0, got ${period}`);
  const result: number[] = [];
  const k = 2 / (period + 1);
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) {
      result.push(NaN);
    } else if (i === period - 1) {
      // Seed with SMA
      let sum = 0;
      for (let j = 0; j < period; j++) sum += data[j]!;
      result.push(sum / period);
    } else {
      result.push(data[i]! * k + result[i - 1]! * (1 - k));
    }
  }
  return result;
}

/**
 * Average True Range (ATR) — volatility indicator for CTA strategies.
 *
 * TR = max(high-low, |high-prevClose|, |low-prevClose|)
 * ATR = EMA(TR, period) or SMA(TR, period).
 *
 * Returns NaN for indices before the indicator is ready.
 */
export function atr(bars: KlineBar[], period: number): number[] {
  if (period <= 0) throw new Error(`atr: period must be > 0, got ${period}`);
  const tr: number[] = [];
  for (let i = 0; i < bars.length; i++) {
    if (i === 0) {
      tr.push(bars[i]!.high - bars[i]!.low);
    } else {
      const prevClose = bars[i - 1]!.close;
      tr.push(
        Math.max(
          bars[i]!.high - bars[i]!.low,
          Math.abs(bars[i]!.high - prevClose),
          Math.abs(bars[i]!.low - prevClose),
        ),
      );
    }
  }
  // Use SMA for ATR (Wilder's original method)
  return sma(tr, period);
}

// ── RSI (Relative Strength Index) ────────────────────────────────

/**
 * Relative Strength Index.
 * Uses Wilder's smoothed moving average (equivalent to EMA with α=1/period).
 * Returns NaN for indices before the first `period` bars.
 */
export function rsi(closes: number[], period: number): number[] {
  if (period <= 0) throw new Error(`rsi: period must be > 0, got ${period}`);
  const result: number[] = new Array<number>(closes.length).fill(NaN);
  if (closes.length <= period) return result;

  // Seed averages from first `period` changes
  let avgGain = 0;
  let avgLoss = 0;
  for (let i = 1; i <= period; i++) {
    const delta = closes[i]! - closes[i - 1]!;
    if (delta > 0) avgGain += delta;
    else avgLoss -= delta; // make positive
  }
  avgGain /= period;
  avgLoss /= period;
  result[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);

  // Wilder smoothing for the rest
  for (let i = period + 1; i < closes.length; i++) {
    const delta = closes[i]! - closes[i - 1]!;
    const gain = delta > 0 ? delta : 0;
    const loss = delta < 0 ? -delta : 0;
    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;
    result[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  }
  return result;
}

// ── MACD (Moving Average Convergence Divergence) ─────────────────

/**
 * MACD indicator.
 * - macdLine  = EMA(fast) − EMA(slow)
 * - signalLine = EMA(macdLine, signalPeriod)
 * - histogram  = macdLine − signalLine
 *
 * Returns NaN for indices where not enough data is available.
 */
export function macd(
  closes: number[],
  fastPeriod = 12,
  slowPeriod = 26,
  signalPeriod = 9,
): { macdLine: number[]; signalLine: number[]; histogram: number[] } {
  const fastEma = ema(closes, fastPeriod);
  const slowEma = ema(closes, slowPeriod);

  const macdLine: number[] = [];
  for (let i = 0; i < closes.length; i++) {
    if (Number.isNaN(fastEma[i]!) || Number.isNaN(slowEma[i]!)) {
      macdLine.push(NaN);
    } else {
      macdLine.push(fastEma[i]! - slowEma[i]!);
    }
  }

  // Compute signal line EMA only on valid MACD values.
  // Standard ema() would seed on NaN values, so we roll our own here.
  const signalLine: number[] = new Array<number>(closes.length).fill(NaN);
  const k = 2 / (signalPeriod + 1);
  let validCount = 0;
  let seedSum = 0;
  let seeded = false;

  for (let i = 0; i < closes.length; i++) {
    if (Number.isNaN(macdLine[i]!)) continue;
    validCount++;
    if (!seeded) {
      seedSum += macdLine[i]!;
      if (validCount === signalPeriod) {
        // Seed signal line with SMA of first signalPeriod valid MACD values
        signalLine[i] = seedSum / signalPeriod;
        seeded = true;
      }
    } else {
      signalLine[i] = macdLine[i]! * k + signalLine[i - 1]! * (1 - k);
    }
  }

  // Handle case where signal line propagates NaN gaps (consecutive valid values)
  // Backfill from last valid signal value through any NaN gap in signalLine
  // Actually, since MACD becomes valid at slowPeriod-1 and stays valid after,
  // we just need the simple propagation above.

  const histogram: number[] = [];
  for (let i = 0; i < closes.length; i++) {
    if (Number.isNaN(macdLine[i]!) || Number.isNaN(signalLine[i]!)) {
      histogram.push(NaN);
    } else {
      histogram.push(macdLine[i]! - signalLine[i]!);
    }
  }

  return { macdLine, signalLine, histogram };
}

// ── CCI (Commodity Channel Index) ────────────────────────────────

/**
 * Commodity Channel Index.
 * CCI = (TP - SMA(TP)) / (0.015 × Mean Deviation)
 * where TP = (High + Low + Close) / 3.
 *
 * Returns NaN for indices before the first `period` bars.
 */
export function cci(bars: KlineBar[], period: number): number[] {
  if (period <= 0) throw new Error(`cci: period must be > 0, got ${period}`);
  const n = bars.length;
  const result: number[] = new Array<number>(n).fill(NaN);

  // Compute typical prices
  const tp: number[] = bars.map((b) => (b.high + b.low + b.close) / 3);

  // Rolling sum for SMA of TP (O(n) instead of O(n×period))
  let windowSum = 0;
  for (let i = 0; i < n; i++) {
    windowSum += tp[i]!;
    if (i >= period) windowSum -= tp[i - period]!;
    if (i < period - 1) continue;

    const tpMean = windowSum / period;

    // Mean deviation still requires per-window scan (depends on tpMean which changes each bar)
    let devSum = 0;
    for (let j = i - period + 1; j <= i; j++) devSum += Math.abs(tp[j]! - tpMean);
    const meanDev = devSum / period;

    // CCI formula (0.015 is Lambert's constant)
    result[i] = meanDev === 0 ? 0 : (tp[i]! - tpMean) / (0.015 * meanDev);
  }
  return result;
}

// ── DMI / ADX (Directional Movement Index) ───────────────────────

/**
 * Directional Movement Index + Average Directional Index.
 *
 * Computes +DI, -DI, and ADX arrays.
 * - +DM = max(high - prevHigh, 0) when > -DM, else 0
 * - -DM = max(prevLow - low, 0) when > +DM, else 0
 * - +DI = 100 × Smoothed(+DM) / Smoothed(TR)
 * - -DI = 100 × Smoothed(-DM) / Smoothed(TR)
 * - DX  = 100 × |+DI - -DI| / (+DI + -DI)
 * - ADX = Smoothed(DX)
 *
 * Uses Wilder's smoothing (α = 1/period).
 * Returns NaN for indices before 2×period bars (ADX needs DX warmup).
 */
export function dmi(
  bars: KlineBar[],
  period: number,
): { plusDI: number[]; minusDI: number[]; adx: number[] } {
  if (period <= 0) throw new Error(`dmi: period must be > 0, got ${period}`);
  const n = bars.length;
  const plusDI: number[] = new Array<number>(n).fill(NaN);
  const minusDI: number[] = new Array<number>(n).fill(NaN);
  const adx: number[] = new Array<number>(n).fill(NaN);

  if (n <= period) return { plusDI, minusDI, adx };

  // Raw +DM, -DM, TR
  const rawPlusDM: number[] = [0];
  const rawMinusDM: number[] = [0];
  const rawTR: number[] = [bars[0]!.high - bars[0]!.low];

  for (let i = 1; i < n; i++) {
    const upMove = bars[i]!.high - bars[i - 1]!.high;
    const downMove = bars[i - 1]!.low - bars[i]!.low;
    rawPlusDM.push(upMove > downMove && upMove > 0 ? upMove : 0);
    rawMinusDM.push(downMove > upMove && downMove > 0 ? downMove : 0);
    rawTR.push(
      Math.max(
        bars[i]!.high - bars[i]!.low,
        Math.abs(bars[i]!.high - bars[i - 1]!.close),
        Math.abs(bars[i]!.low - bars[i - 1]!.close),
      ),
    );
  }

  // Wilder-smoothed sums (seed with first `period` sum, then smooth)
  let smoothPlusDM = 0;
  let smoothMinusDM = 0;
  let smoothTR = 0;
  for (let i = 0; i < period; i++) {
    smoothPlusDM += rawPlusDM[i]!;
    smoothMinusDM += rawMinusDM[i]!;
    smoothTR += rawTR[i]!;
  }

  // DX values for ADX averaging
  const dxValues: number[] = [];

  for (let i = period; i < n; i++) {
    if (i === period) {
      // First smoothed value is just the sum
    } else {
      // Wilder smoothing: prev - prev/period + current
      smoothPlusDM = smoothPlusDM - smoothPlusDM / period + rawPlusDM[i]!;
      smoothMinusDM = smoothMinusDM - smoothMinusDM / period + rawMinusDM[i]!;
      smoothTR = smoothTR - smoothTR / period + rawTR[i]!;
    }

    const pdi = smoothTR === 0 ? 0 : (100 * smoothPlusDM) / smoothTR;
    const mdi = smoothTR === 0 ? 0 : (100 * smoothMinusDM) / smoothTR;
    plusDI[i] = pdi;
    minusDI[i] = mdi;

    const diSum = pdi + mdi;
    const dx = diSum === 0 ? 0 : (100 * Math.abs(pdi - mdi)) / diSum;
    dxValues.push(dx);

    // ADX = Wilder-smoothed DX (needs `period` DX values to seed)
    if (dxValues.length === period) {
      let dxSum = 0;
      for (const d of dxValues) dxSum += d;
      adx[i] = dxSum / period;
    } else if (dxValues.length > period) {
      adx[i] = (adx[i - 1]! * (period - 1) + dx) / period;
    }
  }

  return { plusDI, minusDI, adx };
}

// ── Parabolic SAR ────────────────────────────────────────────────

/**
 * Parabolic SAR (Stop And Reverse).
 *
 * Tracks trend direction with an acceleration factor that increases
 * each time a new extreme point is made.
 *
 * Returns an object with:
 *   - sar: number[] — the SAR values
 *   - trend: number[] — 1 for uptrend, -1 for downtrend
 *
 * NaN for the first bar (need at least 2 bars to initialize).
 */
export function parabolicSar(
  bars: KlineBar[],
  afStart = 0.02,
  afStep = 0.02,
  afMax = 0.2,
): { sar: number[]; trend: number[] } {
  const n = bars.length;
  const sar: number[] = new Array<number>(n).fill(NaN);
  const trend: number[] = new Array<number>(n).fill(0);

  if (n < 2) return { sar, trend };

  // Initialize: assume uptrend if bar 1 > bar 0
  let isUp = bars[1]!.close >= bars[0]!.close;
  let af = afStart;
  let ep = isUp ? bars[0]!.high : bars[0]!.low; // extreme point
  let sarVal = isUp ? bars[0]!.low : bars[0]!.high; // initial SAR

  sar[0] = NaN;
  trend[0] = 0;

  for (let i = 1; i < n; i++) {
    const bar = bars[i]!;
    const prev = bars[i - 1]!;

    // Compute SAR for current bar
    sarVal = sarVal + af * (ep - sarVal);

    // Adjust SAR to not penetrate prior bars
    if (isUp) {
      sarVal = Math.min(sarVal, prev.low, i >= 2 ? bars[i - 2]!.low : prev.low);
      if (bar.low < sarVal) {
        // Reversal → switch to downtrend
        isUp = false;
        sarVal = ep; // SAR = previous extreme point
        ep = bar.low;
        af = afStart;
      } else {
        if (bar.high > ep) {
          ep = bar.high;
          af = Math.min(af + afStep, afMax);
        }
      }
    } else {
      sarVal = Math.max(sarVal, prev.high, i >= 2 ? bars[i - 2]!.high : prev.high);
      if (bar.high > sarVal) {
        // Reversal → switch to uptrend
        isUp = true;
        sarVal = ep;
        ep = bar.high;
        af = afStart;
      } else {
        if (bar.low < ep) {
          ep = bar.low;
          af = Math.min(af + afStep, afMax);
        }
      }
    }

    sar[i] = sarVal;
    trend[i] = isUp ? 1 : -1;
  }

  return { sar, trend };
}

// ── OBV (On-Balance Volume) ──────────────────────────────────────

/**
 * On-Balance Volume — cumulative volume indicator.
 *
 * - If close > prevClose → OBV += volume
 * - If close < prevClose → OBV -= volume
 * - If close == prevClose → OBV unchanged
 *
 * Returns the raw OBV array (same length as input).
 */
export function obv(bars: KlineBar[]): number[] {
  const result: number[] = new Array<number>(bars.length);
  if (bars.length === 0) return result;

  result[0] = bars[0]!.volume;
  for (let i = 1; i < bars.length; i++) {
    const cur = bars[i]!;
    const prev = bars[i - 1]!;
    if (cur.close > prev.close) {
      result[i] = result[i - 1]! + cur.volume;
    } else if (cur.close < prev.close) {
      result[i] = result[i - 1]! - cur.volume;
    } else {
      result[i] = result[i - 1]!;
    }
  }
  return result;
}

// ── Williams %R ──────────────────────────────────────────────────

/**
 * Williams %R — momentum oscillator (range −100 to 0).
 *
 * %R = (Highest High − Close) / (Highest High − Lowest Low) × (−100)
 *
 * Readings > −20 = overbought, < −80 = oversold.
 * Returns NaN for indices before the lookback period is ready.
 */
export function williamsR(bars: KlineBar[], period: number): number[] {
  if (period <= 0) throw new Error(`williamsR: period must be > 0, got ${period}`);
  const n = bars.length;
  const result: number[] = new Array<number>(n).fill(NaN);

  // Monotonic deques for O(n) rolling max(high) / min(low)
  const maxDeque: number[] = []; // decreasing highs
  const minDeque: number[] = []; // increasing lows
  for (let i = 0; i < n; i++) {
    // Maintain decreasing deque for highs
    while (maxDeque.length > 0 && bars[maxDeque[maxDeque.length - 1]!]!.high <= bars[i]!.high)
      maxDeque.pop();
    maxDeque.push(i);
    // Maintain increasing deque for lows
    while (minDeque.length > 0 && bars[minDeque[minDeque.length - 1]!]!.low >= bars[i]!.low)
      minDeque.pop();
    minDeque.push(i);
    // Evict out-of-window elements
    if (maxDeque[0]! <= i - period) maxDeque.shift();
    if (minDeque[0]! <= i - period) minDeque.shift();

    if (i >= period - 1) {
      const hh = bars[maxDeque[0]!]!.high;
      const ll = bars[minDeque[0]!]!.low;
      const range = hh - ll;
      result[i] = range === 0 ? 0 : ((hh - bars[i]!.close) / range) * -100;
    }
  }
  return result;
}

// ── ROC (Rate of Change) ─────────────────────────────────────────

/**
 * Rate of Change — percentage change over `period` bars.
 *
 * ROC = (close − close[period ago]) / close[period ago] × 100
 *
 * Returns NaN for indices before the period is ready.
 */
export function roc(closes: number[], period: number): number[] {
  if (period <= 0) throw new Error(`roc: period must be > 0, got ${period}`);
  const result: number[] = new Array<number>(closes.length).fill(NaN);
  for (let i = period; i < closes.length; i++) {
    const prev = closes[i - period]!;
    result[i] = prev === 0 ? 0 : ((closes[i]! - prev) / prev) * 100;
  }
  return result;
}

// ── Ichimoku Cloud (一目均衡图) ──────────────────────────────────

export type IchimokuResult = {
  tenkan: number[]; // 转换线 (short-term midpoint)
  kijun: number[]; // 基准线 (medium-term midpoint)
  senkouA: number[]; // 先行上线 (leading span A)
  senkouB: number[]; // 先行下线 (leading span B)
  chikou: number[]; // 迟行线 (lagging span, shifted back)
};

/**
 * Ichimoku Kinko Hyo — 一目均衡图.
 *
 * - Tenkan (转换线): midpoint of highest high & lowest low over `tenkanPeriod`
 * - Kijun  (基准线): midpoint over `kijunPeriod`
 * - Senkou A (先行上线): (Tenkan + Kijun) / 2, plotted `displacement` bars ahead
 * - Senkou B (先行下线): midpoint over `senkouBPeriod`, plotted `displacement` bars ahead
 * - Chikou (迟行线): close plotted `displacement` bars behind
 *
 * Returns NaN where data is insufficient.
 */
export function ichimoku(
  bars: KlineBar[],
  tenkanPeriod = 9,
  kijunPeriod = 26,
  senkouBPeriod = 52,
  displacement = 26,
): IchimokuResult {
  const n = bars.length;
  const tenkan = new Array<number>(n).fill(NaN);
  const kijun = new Array<number>(n).fill(NaN);
  const senkouA = new Array<number>(n).fill(NaN);
  const senkouB = new Array<number>(n).fill(NaN);
  const chikou = new Array<number>(n).fill(NaN);

  // Helper: compute rolling midpoint (high+low)/2 over a window using monotonic deques — O(n)
  function rollingMidpoint(period: number): number[] {
    const result = new Array<number>(n).fill(NaN);
    const maxDq: number[] = []; // decreasing highs
    const minDq: number[] = []; // increasing lows
    for (let i = 0; i < n; i++) {
      while (maxDq.length > 0 && bars[maxDq[maxDq.length - 1]!]!.high <= bars[i]!.high) maxDq.pop();
      maxDq.push(i);
      while (minDq.length > 0 && bars[minDq[minDq.length - 1]!]!.low >= bars[i]!.low) minDq.pop();
      minDq.push(i);
      if (maxDq[0]! <= i - period) maxDq.shift();
      if (minDq[0]! <= i - period) minDq.shift();
      if (i >= period - 1) {
        result[i] = (bars[maxDq[0]!]!.high + bars[minDq[0]!]!.low) / 2;
      }
    }
    return result;
  }

  // Pre-compute rolling midpoints for each period in O(n) each
  const tenkanMid = rollingMidpoint(tenkanPeriod);
  const kijunMid = rollingMidpoint(kijunPeriod);
  const senkouBMid = rollingMidpoint(senkouBPeriod);

  for (let i = 0; i < n; i++) {
    tenkan[i] = tenkanMid[i]!;
    kijun[i] = kijunMid[i]!;

    // Senkou A/B: computed at i, plotted at i + displacement
    if (!Number.isNaN(tenkan[i]!) && !Number.isNaN(kijun[i]!)) {
      const destA = i + displacement;
      if (destA < n) senkouA[destA] = (tenkan[i]! + kijun[i]!) / 2;
    }
    const sbVal = senkouBMid[i]!;
    if (!Number.isNaN(sbVal)) {
      const destB = i + displacement;
      if (destB < n) senkouB[destB] = sbVal;
    }

    // Chikou: close plotted displacement bars behind
    const src = i;
    const dest = i - displacement;
    if (dest >= 0) chikou[dest] = bars[src]!.close;
  }

  return { tenkan, kijun, senkouA, senkouB, chikou };
}

// ── Stochastic / KDJ ────────────────────────────────────────────

export type StochasticResult = {
  k: number[]; // 快线
  d: number[]; // 慢线 (K 的 SMA)
  j: number[]; // J = 3K − 2D
};

/**
 * Stochastic oscillator (KDJ).
 *
 * K = SMA of raw %K over `kSmooth` periods
 * D = SMA of K over `dPeriod` periods
 * J = 3K − 2D
 *
 * Raw %K = (Close − Lowest Low) / (Highest High − Lowest Low) × 100
 *
 * Returns NaN where data is insufficient.
 */
export function stochastic(
  bars: KlineBar[],
  period = 9,
  kSmooth = 3,
  dPeriod = 3,
): StochasticResult {
  if (period <= 0) throw new Error(`stochastic: period must be > 0, got ${period}`);
  const n = bars.length;

  // Step 1: raw %K — O(n) via monotonic deques for rolling max/min
  const rawK = new Array<number>(n).fill(NaN);
  const maxDeque: number[] = []; // decreasing highs
  const minDeque: number[] = []; // increasing lows
  for (let i = 0; i < n; i++) {
    while (maxDeque.length > 0 && bars[maxDeque[maxDeque.length - 1]!]!.high <= bars[i]!.high)
      maxDeque.pop();
    maxDeque.push(i);
    while (minDeque.length > 0 && bars[minDeque[minDeque.length - 1]!]!.low >= bars[i]!.low)
      minDeque.pop();
    minDeque.push(i);
    if (maxDeque[0]! <= i - period) maxDeque.shift();
    if (minDeque[0]! <= i - period) minDeque.shift();

    if (i >= period - 1) {
      const hh = bars[maxDeque[0]!]!.high;
      const ll = bars[minDeque[0]!]!.low;
      rawK[i] = hh === ll ? 50 : ((bars[i]!.close - ll) / (hh - ll)) * 100;
    }
  }

  // Step 2: K = SMA(rawK, kSmooth)
  const k = sma(rawK, kSmooth);

  // Step 3: D = SMA(K, dPeriod)
  const d = sma(k, dPeriod);

  // Step 4: J = 3K − 2D
  const j = new Array<number>(n).fill(NaN);
  for (let i = 0; i < n; i++) {
    if (!Number.isNaN(k[i]!) && !Number.isNaN(d[i]!)) {
      j[i] = 3 * k[i]! - 2 * d[i]!;
    }
  }

  return { k, d, j };
}

// ── Extended Indicators ──────────────────────────────────────────

/** Weighted Moving Average. Recent values have higher weight. */
export function wma(data: number[], period: number): number[] {
  const n = data.length;
  const result = new Array<number>(n).fill(NaN);
  const denom = (period * (period + 1)) / 2;
  for (let i = period - 1; i < n; i++) {
    let sum = 0;
    for (let j = 0; j < period; j++) sum += data[i - period + 1 + j]! * (j + 1);
    result[i] = sum / denom;
  }
  return result;
}

/** Double EMA: 2×EMA(data) − EMA(EMA(data)). Less lag than EMA. */
export function dema(data: number[], period: number): number[] {
  const e1 = ema(data, period);
  const e2 = ema(e1, period);
  const n = data.length;
  const result = new Array<number>(n).fill(NaN);
  for (let i = 0; i < n; i++) {
    if (!Number.isNaN(e1[i]!) && !Number.isNaN(e2[i]!)) result[i] = 2 * e1[i]! - e2[i]!;
  }
  return result;
}

/** Hull Moving Average: WMA(2×WMA(n/2) − WMA(n), √n). Minimal lag. */
export function hullMa(data: number[], period: number): number[] {
  const halfPeriod = Math.max(2, Math.round(period / 2));
  const sqrtPeriod = Math.max(2, Math.round(Math.sqrt(period)));
  const wmaHalf = wma(data, halfPeriod);
  const wmaFull = wma(data, period);
  const n = data.length;
  const diff = new Array<number>(n).fill(NaN);
  for (let i = 0; i < n; i++) {
    if (!Number.isNaN(wmaHalf[i]!) && !Number.isNaN(wmaFull[i]!))
      diff[i] = 2 * wmaHalf[i]! - wmaFull[i]!;
  }
  return wma(diff, sqrtPeriod);
}

/** TRIX: 1-period ROC of triple-smoothed EMA. Returns % change. */
export function trix(data: number[], period: number): number[] {
  const e1 = ema(data, period);
  const e2 = ema(e1, period);
  const e3 = ema(e2, period);
  const n = data.length;
  const result = new Array<number>(n).fill(NaN);
  for (let i = 1; i < n; i++) {
    if (!Number.isNaN(e3[i]!) && !Number.isNaN(e3[i - 1]!) && e3[i - 1]! !== 0)
      result[i] = ((e3[i]! - e3[i - 1]!) / e3[i - 1]!) * 100;
  }
  return result;
}

/**
 * Money Flow Index — volume-weighted RSI.
 * Returns values 0–100 (NaN for warmup).
 */
export function mfi(bars: KlineBar[], period: number): number[] {
  const n = bars.length;
  const result = new Array<number>(n).fill(NaN);
  if (n < 2) return result;
  const posFlow = new Array<number>(n).fill(0);
  const negFlow = new Array<number>(n).fill(0);
  for (let i = 1; i < n; i++) {
    const tp = (bars[i]!.high + bars[i]!.low + bars[i]!.close) / 3;
    const prevTp = (bars[i - 1]!.high + bars[i - 1]!.low + bars[i - 1]!.close) / 3;
    const rawFlow = tp * bars[i]!.volume;
    if (tp > prevTp) posFlow[i] = rawFlow;
    else if (tp < prevTp) negFlow[i] = rawFlow;
  }
  for (let i = period; i < n; i++) {
    let pf = 0,
      nf = 0;
    for (let j = i - period + 1; j <= i; j++) {
      pf += posFlow[j]!;
      nf += negFlow[j]!;
    }
    result[i] = nf === 0 ? 100 : 100 - 100 / (1 + pf / nf);
  }
  return result;
}

/** Aroon Up / Down (0–100). */
export function aroon(
  bars: KlineBar[],
  period: number,
): { up: number[]; down: number[] } {
  const n = bars.length;
  const up = new Array<number>(n).fill(NaN);
  const down = new Array<number>(n).fill(NaN);
  for (let i = period; i < n; i++) {
    let highIdx = i,
      lowIdx = i;
    for (let j = i - period; j <= i; j++) {
      if (bars[j]!.high >= bars[highIdx]!.high) highIdx = j;
      if (bars[j]!.low <= bars[lowIdx]!.low) lowIdx = j;
    }
    up[i] = ((period - (i - highIdx)) / period) * 100;
    down[i] = ((period - (i - lowIdx)) / period) * 100;
  }
  return { up, down };
}

/** Chaikin Money Flow over N periods. Returns –1…+1 range. */
export function cmf(bars: KlineBar[], period: number): number[] {
  const n = bars.length;
  const result = new Array<number>(n).fill(NaN);
  for (let i = period - 1; i < n; i++) {
    let sumMfv = 0,
      sumVol = 0;
    for (let j = i - period + 1; j <= i; j++) {
      const b = bars[j]!;
      const range = b.high - b.low;
      const mfv = range === 0 ? 0 : ((b.close - b.low - (b.high - b.close)) / range) * b.volume;
      sumMfv += mfv;
      sumVol += b.volume;
    }
    result[i] = sumVol === 0 ? 0 : sumMfv / sumVol;
  }
  return result;
}

/**
 * Kaufman Adaptive Moving Average (KAMA).
 * Adapts smoothing speed to market efficiency.
 */
export function kama(data: number[], period: number, fastPeriod = 2, slowPeriod = 30): number[] {
  const n = data.length;
  const result = new Array<number>(n).fill(NaN);
  const fastSc = 2 / (fastPeriod + 1);
  const slowSc = 2 / (slowPeriod + 1);
  if (n <= period) return result;
  result[period - 1] = data[period - 1]!;
  for (let i = period; i < n; i++) {
    const direction = Math.abs(data[i]! - data[i - period]!);
    let volatility = 0;
    for (let j = i - period + 1; j <= i; j++) volatility += Math.abs(data[j]! - data[j - 1]!);
    const er = volatility === 0 ? 0 : direction / volatility;
    const sc = (er * (fastSc - slowSc) + slowSc) ** 2;
    result[i] = result[i - 1]! + sc * (data[i]! - result[i - 1]!);
  }
  return result;
}

/** DeMarker oscillator (0–1). Values < 0.3 oversold, > 0.7 overbought. */
export function demarker(bars: KlineBar[], period: number): number[] {
  const n = bars.length;
  const result = new Array<number>(n).fill(NaN);
  const deMax = new Array<number>(n).fill(0);
  const deMin = new Array<number>(n).fill(0);
  for (let i = 1; i < n; i++) {
    deMax[i] = bars[i]!.high > bars[i - 1]!.high ? bars[i]!.high - bars[i - 1]!.high : 0;
    deMin[i] = bars[i]!.low < bars[i - 1]!.low ? bars[i - 1]!.low - bars[i]!.low : 0;
  }
  for (let i = period; i < n; i++) {
    let sumMax = 0,
      sumMin = 0;
    for (let j = i - period + 1; j <= i; j++) {
      sumMax += deMax[j]!;
      sumMin += deMin[j]!;
    }
    const total = sumMax + sumMin;
    result[i] = total === 0 ? 0.5 : sumMax / total;
  }
  return result;
}

/**
 * Ultimate Oscillator — multi-period buying pressure.
 * Combines 3 timeframes (default 7/14/28).
 */
export function ultimateOscillator(
  bars: KlineBar[],
  period1 = 7,
  period2 = 14,
  period3 = 28,
): number[] {
  const n = bars.length;
  const result = new Array<number>(n).fill(NaN);
  const bp = new Array<number>(n).fill(0);
  const tr = new Array<number>(n).fill(0);
  for (let i = 1; i < n; i++) {
    const prevClose = bars[i - 1]!.close;
    const low = Math.min(bars[i]!.low, prevClose);
    const high = Math.max(bars[i]!.high, prevClose);
    bp[i] = bars[i]!.close - low;
    tr[i] = high - low;
  }
  const maxPeriod = Math.max(period1, period2, period3);
  for (let i = maxPeriod; i < n; i++) {
    const avg = (period: number) => {
      let sb = 0,
        st = 0;
      for (let j = i - period + 1; j <= i; j++) {
        sb += bp[j]!;
        st += tr[j]!;
      }
      return st === 0 ? 0 : sb / st;
    };
    result[i] = (100 * (4 * avg(period1) + 2 * avg(period2) + avg(period3))) / 7;
  }
  return result;
}
