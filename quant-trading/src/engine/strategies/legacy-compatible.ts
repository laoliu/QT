/** Signal rules ported from the retained Backtrader sources, without CTA shorts. */
import type { TradingSignal } from "../../types.js";
import { additionalLegacyPorts } from "./legacy-ports.js";
import { bollingerBands, ema, getCloses, rollingHighLow, sma, rsi,
  type BuiltinStrategy, type BuiltinStrategyContext } from "./indicators.js";

function signal(ctx: BuiltinStrategyContext, id: string, direction: "buy" | "sell", reason: string, sizePct = 95): TradingSignal {
  const bar = ctx.bars[ctx.index]!;
  return { timestamp: bar.timestamp, symbol: "", direction, price: bar.close,
    strategyId: id, reason, ...(direction === "buy" ? { sizePct } : {}) };
}

/** Backtrader CrossOver remembers the last nonzero difference across equal bars. */
function crosses(fast: number[], slow: number[]): number[] {
  let previous = 0;
  return fast.map((value, i) => {
    const diff = value - slow[i]!;
    if (!Number.isFinite(diff)) return 0;
    const cross = diff > 0 && previous < 0 ? 1 : diff < 0 && previous > 0 ? -1 : 0;
    if (diff !== 0) previous = diff;
    return cross;
  });
}

export function longOnlyEmaCross(fastPeriod = 10, slowPeriod = 30): BuiltinStrategy {
  let cross: number[] = [];
  return {
    name: `EMA long only (${fastPeriod},${slowPeriod})`,
    init(ctx) { const closes = getCloses(ctx); cross = crosses(ema(closes, fastPeriod), ema(closes, slowPeriod)); },
    next(ctx) {
      if (ctx.index < Math.max(fastPeriod, slowPeriod)) return null;
      if (ctx.position === 0 && cross[ctx.index] === 1)
        return signal(ctx, "ema-cross-long-only", "buy", "EMA golden cross");
      if (ctx.position > 0 && cross[ctx.index] === -1)
        return signal(ctx, "ema-cross-long-only", "sell", "EMA death cross");
      return null;
    },
  };
}

export function longOnlyDonchian(upperPeriod = 20, lowerPeriod = 10): BuiltinStrategy {
  let highs: number[] = [], lows: number[] = [];
  return {
    name: `Donchian long only (${upperPeriod},${lowerPeriod})`,
    init(ctx) {
      highs = rollingHighLow(ctx.bars, upperPeriod).highs;
      lows = rollingHighLow(ctx.bars, lowerPeriod).lows;
    },
    next(ctx) {
      const i = ctx.index;
      if (i < Math.max(upperPeriod, lowerPeriod) - 1) return null;
      const close = ctx.bars[i]!.close;
      if (ctx.position === 0 && close > highs[i]!)
        return signal(ctx, "donchian-long-only", "buy", `Break above prior ${upperPeriod} highs`);
      if (ctx.position > 0 && close < lows[i]!)
        return signal(ctx, "donchian-long-only", "sell", `Break below prior ${lowerPeriod} lows`);
      return null;
    },
  };
}

export function longOnlyKdj(period = 9, kSmooth = 3, dPeriod = 3, overbought = 80, oversold = 20): BuiltinStrategy {
  let k: number[] = [], d: number[] = [], cross: number[] = [];
  return {
    name: `KDJ long only (${period},${kSmooth},${dPeriod})`,
    init(ctx) {
      const rsv = ctx.bars.map((bar, i) => {
        if (i < period - 1) return NaN;
        const window = ctx.bars.slice(i - period + 1, i + 1);
        const high = Math.max(...window.map(b => b.high));
        const low = Math.min(...window.map(b => b.low));
        return high > low ? (bar.close - low) / (high - low) * 100 : 50;
      });
      k = sma(rsv, kSmooth);
      d = sma(k, dPeriod);
      cross = crosses(k, d);
    },
    next(ctx) {
      const i = ctx.index;
      if (i < period + kSmooth + dPeriod - 2) return null;
      const j = 3 * k[i]! - 2 * d[i]!;
      if (ctx.position === 0) {
        if (cross[i] === 1 && k[i]! < oversold)
          return signal(ctx, "kdj-long-only", "buy", "K/D golden cross in oversold zone");
        if (j < 0) return signal(ctx, "kdj-long-only", "buy", "J below zero", 50);
      } else if (ctx.position > 0 && ((cross[i] === -1 && k[i]! > overbought) || j > 100)) {
        return signal(ctx, "kdj-long-only", "sell", "KDJ overbought exit");
      }
      return null;
    },
  };
}

export function bollingerSqueezeReversion(period = 20, devfactor = 2, squeezePct = 50): BuiltinStrategy {
  let bands: ReturnType<typeof bollingerBands>;
  let history: number[] = [];
  return {
    name: `Bollinger squeeze reversion (${period},${devfactor},${squeezePct})`,
    init(ctx) {
      // Backtrader uses population variance; the shared builtin helper uses sample variance.
      bands = bollingerBands(getCloses(ctx), period, devfactor * Math.sqrt((period - 1) / period));
      history = [];
    },
    next(ctx) {
      const i = ctx.index;
      if (i < period - 1) return null;
      const upper = bands.upper[i]!, lower = bands.lower[i]!, close = ctx.bars[i]!.close;
      const width = upper - lower;
      history.push(width);
      if (history.length > 4 * period) history.shift();
      const median = [...history].sort((a, b) => a - b)[Math.floor(history.length / 2)]!;
      const squeeze = history.length >= period && median > 0 && width < median * squeezePct / 100;
      if (ctx.position === 0 && close <= lower && !squeeze)
        return signal(ctx, "bollinger-squeeze-reversion", "buy", "Lower band without squeeze");
      const prevWidth = bands.upper[i - 1]! - bands.lower[i - 1]!;
      const prevPct = prevWidth === 0 ? 0.5 : (ctx.bars[i - 1]?.close! - bands.lower[i - 1]!) / prevWidth;
      if (ctx.position > 0 && (close >= upper || (close >= bands.middle[i]! && prevPct < 0.5)))
        return signal(ctx, "bollinger-squeeze-reversion", "sell", "Upper band or middle-band recovery");
      return null;
    },
  };
}

/** SMA-seeded smoothing which also handles leading indicator warmup NaNs. */
function smooth(data: number[], period: number, alpha = 2 / (period + 1)): number[] {
  const out = data.map(() => NaN);
  let count = 0, sum = 0, previous = NaN;
  for (let i = 0; i < data.length; i++) {
    const value = data[i]!;
    if (!Number.isFinite(value)) { count = 0; sum = 0; previous = NaN; continue; }
    if (count < period) {
      sum += value;
      count++;
      if (count === period) out[i] = previous = sum / period;
    } else out[i] = previous = previous * (1 - alpha) + value * alpha;
  }
  return out;
}

type Rules = { start: number; buy: (i: number) => boolean; sell: (i: number) => boolean };

function directional(ctx: BuiltinStrategyContext, period: number) {
  const positive = ctx.bars.map(() => NaN), negative = [...positive], tr = [...positive];
  for (let i = 1; i < ctx.bars.length; i++) {
    const bar = ctx.bars[i]!, prev = ctx.bars[i - 1]!;
    const up = bar.high - prev.high, down = prev.low - bar.low;
    positive[i] = up > down && up > 0 ? up : 0;
    negative[i] = down > up && down > 0 ? down : 0;
    tr[i] = Math.max(bar.high - bar.low, Math.abs(bar.high - prev.close), Math.abs(bar.low - prev.close));
  }
  const wilder = (values: number[]) => smooth(values, period, 1 / period);
  const atr = wilder(tr), pos = wilder(positive), neg = wilder(negative);
  const plus = pos.map((v, i) => v / atr[i]! * 100), minus = neg.map((v, i) => v / atr[i]! * 100);
  const dx = plus.map((v, i) => 100 * Math.abs(v - minus[i]!) / (v + minus[i]!));
  return { atr, plus, minus, adx: wilder(dx) };
}

export function legacyRuleStrategy(legacyId: string, params: Record<string, unknown> = {}): BuiltinStrategy {
  const port = additionalLegacyPorts.find(p => p.legacyId === legacyId);
  if (!port) throw new Error(`Unknown retained signal strategy: ${legacyId}`);
  const n = (key: string, defaultValue: number) => (params[key] as number | undefined) ?? defaultValue;
  let rules: Rules;
  return {
    name: `${legacyId} (long only)`,
    init(ctx) {
      const closes = getCloses(ctx), length = closes.length;
      const period = n("period", legacyId === "aroon" ? 25 : legacyId === "trix" ? 15 : legacyId === "roc" ? 12 : legacyId === "adaptive-ma" ? 10 : 14);
      const crossRules = (fast: number[], slow: number[], start: number): Rules => {
        const cross = crosses(fast, slow);
        return { start, buy: i => cross[i] === 1, sell: i => cross[i] === -1 };
      };
      if (legacyId === "macd") {
        const fast = n("fastPeriod", 12), slow = n("slowPeriod", 26), sig = n("signalPeriod", 9);
        const f = smooth(closes, fast), s = smooth(closes, slow);
        const line = f.map((v, i) => v - s[i]!);
        rules = crossRules(line, smooth(line, sig), Math.max(fast, slow) + sig - 1);
      } else if (legacyId === "dema-cross" || legacyId === "trix") {
        if (legacyId === "dema-cross") {
          const dema = (p: number) => { const one = smooth(closes, p), two = smooth(one, p); return one.map((v, i) => 2 * v - two[i]!); };
          const fast = n("fastPeriod", 10), slow = n("slowPeriod", 30);
          rules = crossRules(dema(fast), dema(slow), 2 * Math.max(fast, slow) - 1);
        } else {
          const triple = smooth(smooth(smooth(closes, period), period), period);
          const trix = triple.map((v, i) => i > 0 ? 100 * (v / triple[i - 1]! - 1) : NaN);
          const sig = n("signalPeriod", 9);
          rules = crossRules(trix, sma(trix, sig), 3 * period + sig - 2);
        }
      } else if (legacyId === "rsi") {
        const values = rsi(closes, period);
        rules = { start: period, buy: i => values[i]! < n("oversold", 30), sell: i => values[i]! > n("overbought", 70) };
      } else if (legacyId === "dmi" || legacyId === "adx-trend") {
        const { plus, minus, adx } = directional(ctx, period);
        const cross = crosses(plus, minus), threshold = n("adxThreshold", 25);
        rules = legacyId === "dmi" ? crossRules(plus, minus, period + 1)
          : { start: 2 * period - 1, buy: i => cross[i] === 1 && adx[i]! > threshold,
            sell: i => cross[i] === -1 || adx[i]! < threshold };
      } else if (legacyId === "aroon") {
        const up = closes.map(() => NaN), down = [...up];
        for (let i = period; i < length; i++) {
          let high = -Infinity, low = Infinity, sinceHigh = 0, sinceLow = 0;
          for (let j = 0; j <= period; j++) {
            const bar = ctx.bars[i - j]!;
            if (bar.high > high) { high = bar.high; sinceHigh = j; }
            if (bar.low < low) { low = bar.low; sinceLow = j; }
          }
          up[i] = (period - sinceHigh) / period * 100;
          down[i] = (period - sinceLow) / period * 100;
        }
        const upper = n("upperThreshold", 70), lower = n("lowerThreshold", 30);
        rules = { start: period + 1, buy: i => up[i - 1]! <= upper && up[i]! > upper && down[i]! < lower,
          sell: i => down[i - 1]! <= upper && down[i]! > upper && up[i]! < lower };
      } else if (legacyId === "envelope") {
        const p = n("maPeriod", 20), mid = sma(closes, p), factor = n("pct", 3) / 100;
        rules = { start: p - 1, buy: i => closes[i]! <= mid[i]! * (1 - factor),
          sell: i => closes[i]! >= mid[i]! * (1 + factor) || (closes[i]! >= mid[i]! && closes[i - 1]! < mid[i - 1]!) };
      } else if (legacyId === "price-channel-breakout") {
        const entry = n("breakoutPeriod", 20), exit = n("exitPeriod", 10);
        const highs = rollingHighLow(ctx.bars, entry).highs, lows = rollingHighLow(ctx.bars, exit).lows;
        const volume = ctx.bars.map(b => b.volume), average = sma(volume, 20);
        rules = { start: Math.max(entry, exit, 20) - 1,
          buy: i => closes[i]! > highs[i]! && (params.volConfirm === false || (average[i]! > 0 && volume[i]! > average[i]! * 1.5)),
          sell: i => closes[i]! < lows[i]! };
      } else if (legacyId === "mfi") {
        const typical = ctx.bars.map(b => (b.high + b.low + b.close) / 3), values = closes.map(() => NaN);
        for (let i = period; i < length; i++) {
          let positive = 0, negative = 0;
          for (let j = i - period + 1; j <= i; j++) {
            const flow = typical[j]! * ctx.bars[j]!.volume;
            if (typical[j]! > typical[j - 1]!) positive += flow; else negative += flow;
          }
          values[i] = negative === 0 ? 100 : 100 - 100 / (1 + positive / negative);
        }
        rules = { start: period, buy: i => values[i]! < n("oversold", 20), sell: i => values[i]! > n("overbought", 80) };
      } else if (legacyId === "roc") {
        const values = closes.map((v, i) => i >= period ? v / closes[i - period]! - 1 : NaN);
        rules = { start: period, buy: i => values[i]! > 0 && values[i - 1]! <= 0,
          sell: i => values[i]! < 0 && values[i - 1]! >= 0 };
      } else if (legacyId === "adaptive-ma") {
        const values = closes.map(() => NaN), fast = 2 / (n("fastPeriod", 2) + 1), slow = 2 / (n("slowPeriod", 30) + 1);
        // Dynamic smoothing's first valid alpha arrives one bar after the period.
        values[period] = closes.slice(1, period + 1).reduce((a, b) => a + b, 0) / period;
        for (let i = period + 1; i < length; i++) {
          let volatility = 0;
          for (let j = i - period + 1; j <= i; j++) volatility += Math.abs(closes[j]! - closes[j - 1]!);
          const efficiency = Math.abs(closes[i]! - closes[i - period]!) / volatility;
          const alpha = (efficiency * (fast - slow) + slow) ** 2;
          values[i] = values[i - 1]! * (1 - alpha) + closes[i]! * alpha;
        }
        rules = { start: period, buy: i => closes[i]! > values[i]! && values[i]! > values[i - 1]!, sell: i => closes[i]! < values[i]! };
      } else if (legacyId === "multi-tf-ma") {
        const a = n("shortPeriod", 5), b = n("mediumPeriod", 20), c = n("longPeriod", 60);
        const short = sma(closes, a), medium = sma(closes, b), long = sma(closes, c);
        const aligned = (i: number) => short[i]! > medium[i]! && medium[i]! > long[i]!;
        rules = { start: Math.max(a, b, c) - 1, buy: i => aligned(i) && !aligned(i - 1), sell: i => short[i]! < medium[i]! };
      } else {
        const p = n("atrPeriod", 10), mult = n("multiplier", 3), atr = directional(ctx, p).atr;
        const direction = closes.map(() => NaN);
        let prevUpper = NaN, prevLower = NaN;
        for (let i = p; i < length; i++) {
          const bar = ctx.bars[i]!, hl2 = (bar.high + bar.low) / 2;
          let upper = hl2 + mult * atr[i]!, lower = hl2 - mult * atr[i]!;
          if (i === p) direction[i] = 1;
          else {
            // Preserve the retained source's band update rules, including its reset conditions.
            if (!(lower > prevLower || closes[i - 1]! > prevLower)) lower = prevLower;
            if (!(upper < prevUpper || closes[i - 1]! < prevUpper)) upper = prevUpper;
            direction[i] = direction[i - 1] === 1 ? (closes[i]! < lower ? -1 : 1) : (closes[i]! > upper ? 1 : -1);
          }
          prevUpper = upper; prevLower = lower;
        }
        rules = { start: p, buy: i => direction[i] === 1 && direction[i - 1] === -1,
          sell: i => direction[i] === -1 && direction[i - 1] === 1 };
      }
    },
    next(ctx) {
      const i = ctx.index;
      if (i < rules.start) return null;
      if (ctx.position === 0 && rules.buy(i)) return signal(ctx, port.canonicalId, "buy", `${legacyId} entry`);
      if (ctx.position > 0 && rules.sell(i)) return signal(ctx, port.canonicalId, "sell", `${legacyId} exit`);
      return null;
    },
  };
}
