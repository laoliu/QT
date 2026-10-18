/** Signal rules ported from the retained Backtrader sources, without CTA shorts. */
import type { TradingSignal } from "../../types.js";
import { bollingerBands, ema, getCloses, rollingHighLow, sma,
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
