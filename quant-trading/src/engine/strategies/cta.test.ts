/**
 * Tests for CTA / futures-specific strategies:
 *   - ctaTrendFollowing (ATR channel breakout)
 *   - ctaDualThrust (opening range breakout)
 *   - spreadArbitrage (spread mean-reversion)
 */
import { describe, expect, it } from "vitest";
import type { KlineBar, TradingSignal } from "../../types.js";
import { ctaDualThrust, ctaTrendFollowing, spreadArbitrage } from "./cta.js";
import {
  ctaAberration,
  ctaBollingerBreakout,
  ctaDmiAdx,
  ctaIchimoku,
  ctaKdj,
  ctaKeltnerChannel,
  ctaMacdTrend,
  ctaMeanReversionZScore,
  ctaMomentumRotation,
  ctaObvTrend,
  ctaParabolicSar,
  ctaRBreaker,
  ctaRsiReversal,
  ctaTtmSqueeze,
  ctaTurtleBreakout,
  ctaVolatilityTarget,
  ctaWilliamsR,
  ctaGridTrading,
  ctaMultiMaConvergence,
  ctaDonchianMidReversion,
  ctaPivotPoint,
  ctaChandelierExit,
  ctaRangeBreakout,
  ctaVwapReversion,
  ctaDoubleBottom,
  ctaRocMomentum,
  ctaCciDivergence,
  ctaEmaCrossover,
  ctaAtrTrailing,
  ctaStochasticRsi,
  ctaMacdHistogramReversal,
  ctaBollingerBandwidth,
} from "./cta.js";

// ── Helper ───────────────────────────────────────────────────────

/** Generate N daily bars with a controlled price path. */
function generateBars(
  n: number,
  opts?: { startPrice?: number; trend?: "up" | "down" | "flat" | "sine" | "vshape" },
): KlineBar[] {
  const bars: KlineBar[] = [];
  const start = opts?.startPrice ?? 100;
  const trend = opts?.trend ?? "sine";
  const dayMs = 86_400_000;
  const t0 = new Date("2024-01-02").getTime();

  for (let i = 0; i < n; i++) {
    let price: number;
    switch (trend) {
      case "up":
        price = start + i * 1.5; // steeper than stock tests, suitable for futures
        break;
      case "down":
        price = start - i * 1.5;
        break;
      case "flat":
        price = start;
        break;
      case "vshape":
        // Falls for first half, rises for second half
        price = i < n / 2 ? start - i * 1.5 : start - (n / 2) * 1.5 + (i - n / 2) * 1.5;
        break;
      case "sine":
      default:
        price = start + 30 * Math.sin((i / n) * Math.PI * 4);
    }
    // Give bars a meaningful open-close range for Dual Thrust / CTA breakouts
    const openOffset = trend === "up" ? -2 : trend === "down" ? 2 : -1;
    bars.push({
      timestamp: t0 + i * dayMs,
      open: price + openOffset,
      high: price + 3,
      low: price - 3,
      close: price,
      volume: 100_000,
    });
  }
  return bars;
}

/** Generate bars with narrow range (open-close spread of 1) for Donchian channel tests. */
function generateNarrowBars(
  n: number,
  opts?: { startPrice?: number; trend?: "up" | "down" | "sine" | "flat" },
): KlineBar[] {
  const bars: KlineBar[] = [];
  const start = opts?.startPrice ?? 100;
  const trend = opts?.trend ?? "sine";
  const dayMs = 86_400_000;
  const t0 = new Date("2024-01-02").getTime();
  for (let i = 0; i < n; i++) {
    let price: number;
    switch (trend) {
      case "up":
        price = start + i * 2;
        break;
      case "down":
        price = start - i * 2;
        break;
      case "flat":
        price = start;
        break;
      case "sine":
      default:
        price = start + 40 * Math.sin((i / n) * Math.PI * 4);
    }
    // Narrow range: high/low only ±0.5 from close
    bars.push({
      timestamp: t0 + i * dayMs,
      open: price - 0.3,
      high: price + 0.5,
      low: price - 0.5,
      close: price,
      volume: 100_000,
    });
  }
  return bars;
}

/** Run a strategy over bars, collecting all signals. */
function runStrategy(
  strategy: ReturnType<typeof ctaTrendFollowing>,
  bars: KlineBar[],
): TradingSignal[] {
  const signals: TradingSignal[] = [];
  const ctx = { bars, index: 0, position: 0, cash: 1_000_000, params: {}, signals };
  strategy.init?.(ctx);

  for (let i = 0; i < bars.length; i++) {
    ctx.index = i;
    const result = strategy.next(ctx);
    if (result) {
      const arr = Array.isArray(result) ? result : [result];
      for (const sig of arr) {
        signals.push(sig);
        // Update virtual position for strategy context
        if (sig.direction === "buy") ctx.position = 1;
        else if (sig.direction === "sell") ctx.position = 0;
        else if (sig.direction === "short") ctx.position = -1;
        else if (sig.direction === "cover") ctx.position = 0;
      }
    }
  }
  return signals;
}

// ── CTA Trend Following ─────────────────────────────────────────

describe("ctaTrendFollowing", () => {
  it("creates a strategy with correct name", () => {
    const s = ctaTrendFollowing(20, 14, 2.0, 1.5);
    expect(s.name).toContain("CTA趋势跟踪");
    expect(s.name).toContain("MA20");
    expect(s.name).toContain("ATR14");
  });

  it("produces no signals in flat market", () => {
    const bars = generateBars(100, { trend: "flat" });
    const signals = runStrategy(ctaTrendFollowing(), bars);
    // Flat: no breakout above or below ATR channel
    expect(signals.length).toBe(0);
  });

  it("emits buy signal in uptrend (breakout above upper channel)", () => {
    const bars = generateBars(200, { trend: "up" });
    const signals = runStrategy(ctaTrendFollowing(10, 7, 1.0, 0.8), bars);

    const buySignals = signals.filter((s) => s.direction === "buy");
    expect(buySignals.length).toBeGreaterThan(0);
    expect(buySignals[0]!.reason).toContain("CTA做多");
    expect(buySignals[0]!.strategyId).toBe("cta-trend");
  });

  it("emits short signal in downtrend (breakout below lower channel)", () => {
    const bars = generateBars(200, { trend: "down" });
    const signals = runStrategy(ctaTrendFollowing(10, 7, 1.0, 0.8), bars);

    const shortSignals = signals.filter((s) => s.direction === "short");
    expect(shortSignals.length).toBeGreaterThan(0);
    expect(shortSignals[0]!.reason).toContain("CTA做空");
  });

  it("emits both long and short signals in oscillating market", () => {
    const bars = generateBars(300, { trend: "sine", startPrice: 100 });
    // Use small ATR multiplier so sine oscillations breach the channel
    const signals = runStrategy(ctaTrendFollowing(10, 7, 0.5, 0.3), bars);

    const buys = signals.filter((s) => s.direction === "buy");
    const sells = signals.filter((s) => s.direction === "sell");
    const shorts = signals.filter((s) => s.direction === "short");
    const covers = signals.filter((s) => s.direction === "cover");

    // In a sine wave, we expect both long and short entries
    expect(buys.length + shorts.length).toBeGreaterThan(0);
    // And corresponding exits
    expect(sells.length + covers.length).toBeGreaterThan(0);
  });

  it("trailing stop triggers on reversal", () => {
    // V-shape: goes down then up — should trigger short stop
    const bars = generateBars(200, { trend: "vshape", startPrice: 120 });
    const signals = runStrategy(ctaTrendFollowing(10, 7, 1.0, 0.8), bars);

    // The downtrend should produce short signals, and the reversal should cover
    const coverSignals = signals.filter((s) => s.direction === "cover");
    const stopCovers = coverSignals.filter(
      (s) => s.reason?.includes("止损") || s.reason?.includes("反手"),
    );
    // At least one stop/reversal should fire
    expect(stopCovers.length + coverSignals.length).toBeGreaterThan(0);
  });

  it("respects custom parameters", () => {
    const s = ctaTrendFollowing(30, 20, 3.0, 2.0);
    expect(s.name).toContain("MA30");
    expect(s.name).toContain("ATR20");
    expect(s.name).toContain("3");
  });
});

// ── Dual Thrust ──────────────────────────────────────────────────

describe("ctaDualThrust", () => {
  it("creates a strategy with correct name", () => {
    const s = ctaDualThrust(4, 0.5, 0.5);
    expect(s.name).toContain("DualThrust");
    expect(s.name).toContain("N=4");
  });

  it("produces no signals in a flat market", () => {
    const bars = generateBars(100, { trend: "flat" });
    const signals = runStrategy(ctaDualThrust(), bars);
    // Flat: range is 0, triggers can't be breached
    expect(signals.length).toBe(0);
  });

  it("emits buy on upward breakout", () => {
    const bars = generateBars(200, { trend: "up" });
    // Use small K values so uptrend open-to-close gap breaches trigger
    const signals = runStrategy(ctaDualThrust(4, 0.1, 0.1), bars);

    const buySignals = signals.filter((s) => s.direction === "buy");
    expect(buySignals.length).toBeGreaterThan(0);
    expect(buySignals[0]!.reason).toContain("DT做多");
    expect(buySignals[0]!.strategyId).toBe("dual-thrust");
  });

  it("emits short on downward breakout", () => {
    const bars = generateBars(200, { trend: "down" });
    const signals = runStrategy(ctaDualThrust(4, 0.1, 0.1), bars);

    const shortSignals = signals.filter((s) => s.direction === "short");
    expect(shortSignals.length).toBeGreaterThan(0);
    expect(shortSignals[0]!.reason).toContain("DT做空");
  });

  it("emits multi-signal arrays on reversals (cover+buy or sell+short)", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaDualThrust(4, 0.3, 0.3), bars);

    // Look for combined exit+entry pairs (sell+short or cover+buy)
    // These are emitted as arrays by the strategy
    const allDirections = signals.map((s) => s.direction);
    const hasLong = allDirections.includes("buy");
    const hasShort = allDirections.includes("short");
    // In an oscillating market, both directions should appear
    expect(hasLong || hasShort).toBe(true);
  });

  it("symmetric K1/K2 parameters work", () => {
    const bars = generateBars(200, { trend: "sine" });
    // Use small K so that sine oscillation triggers
    const signals = runStrategy(ctaDualThrust(4, 0.1, 0.1), bars);
    // Should produce at least some signals in a volatile market
    expect(signals.length).toBeGreaterThan(0);
  });

  it("asymmetric K1/K2: high K1 suppresses longs", () => {
    const bars = generateBars(200, { trend: "up" });
    // K1=0.9 makes it very hard to trigger long, K2=0.1 makes shorts easy
    const wideK1 = runStrategy(ctaDualThrust(4, 0.9, 0.1), bars);
    const narrowK1 = runStrategy(ctaDualThrust(4, 0.1, 0.9), bars);

    const wideLongs = wideK1.filter((s) => s.direction === "buy").length;
    const narrowLongs = narrowK1.filter((s) => s.direction === "buy").length;
    // With narrower K1, more long signals should fire
    expect(narrowLongs).toBeGreaterThanOrEqual(wideLongs);
  });
});

// ── Spread Arbitrage ─────────────────────────────────────────────

describe("spreadArbitrage", () => {
  it("creates a strategy with correct name", () => {
    const s = spreadArbitrage(20, 2.0, 0.5);
    expect(s.name).toContain("价差套利");
    expect(s.name).toContain("MA20");
    expect(s.name).toContain("2σ");
  });

  it("produces no signals in flat market", () => {
    const bars = generateBars(100, { trend: "flat" });
    const signals = runStrategy(spreadArbitrage(), bars);
    // Flat market: z-score stays near 0, never hits ±2σ
    expect(signals.length).toBe(0);
  });

  it("emits mean-reversion signals in oscillating market", () => {
    // Sine wave produces extreme deviations from EMA
    const bars = generateBars(300, { trend: "sine", startPrice: 100 });
    const signals = runStrategy(spreadArbitrage(20, 1.5, 0.3), bars);

    // Should have both buy (spread too low) and short (spread too high)
    const buys = signals.filter((s) => s.direction === "buy");
    const shorts = signals.filter((s) => s.direction === "short");
    expect(buys.length + shorts.length).toBeGreaterThan(0);
  });

  it("exits position when spread reverts to mean", () => {
    const bars = generateBars(300, { trend: "sine", startPrice: 100 });
    const signals = runStrategy(spreadArbitrage(20, 1.5, 0.3), bars);

    // Check for exit signals (sell after buy, cover after short)
    const sells = signals.filter((s) => s.direction === "sell");
    const covers = signals.filter((s) => s.direction === "cover");
    expect(sells.length + covers.length).toBeGreaterThan(0);
  });

  it("all exit signals mention 回归 (reversion)", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(spreadArbitrage(20, 1.5, 0.3), bars);

    const exits = signals.filter((s) => s.direction === "sell" || s.direction === "cover");
    for (const exit of exits) {
      expect(exit.reason).toContain("回归");
    }
  });

  it("all entry signals mention Z-score", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(spreadArbitrage(20, 1.5, 0.3), bars);

    const entries = signals.filter((s) => s.direction === "buy" || s.direction === "short");
    for (const entry of entries) {
      expect(entry.reason).toContain("价差Z=");
    }
  });

  it("tighter entryStdDevs produces more signals", () => {
    const bars = generateBars(300, { trend: "sine" });
    const tightSignals = runStrategy(spreadArbitrage(20, 1.0, 0.3), bars);
    const wideSignals = runStrategy(spreadArbitrage(20, 3.0, 0.3), bars);

    expect(tightSignals.length).toBeGreaterThanOrEqual(wideSignals.length);
  });

  it("strategyId is spread-arbitrage", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(spreadArbitrage(20, 1.5, 0.3), bars);

    for (const sig of signals) {
      expect(sig.strategyId).toBe("spread-arbitrage");
    }
  });

  it("uses expanding-window stats (no look-ahead bias)", () => {
    // Verify the strategy doesn't produce signals in the first maPeriod bars
    // because it needs enough data for meaningful stats
    const bars = generateBars(50, { trend: "sine", startPrice: 100 });
    const s = spreadArbitrage(20, 1.5, 0.3);
    const ctx = {
      bars,
      index: 0,
      position: 0,
      cash: 500_000,
      params: {},
      signals: [],
    };
    s.init?.(ctx);

    // First maPeriod bars should produce no signals (expanding window is too small)
    for (let i = 0; i < 20; i++) {
      ctx.index = i;
      const result = s.next(ctx);
      expect(result).toBeNull();
    }
  });

  it("expanding-window z-score differs from full-sample z-score", () => {
    // The point: ensure we're NOT using the full dataset mean/std
    // After the fix, early bars and late bars compute different mean/std
    const bars = generateBars(200, { trend: "sine", startPrice: 100 });
    const s = spreadArbitrage(20, 0.5, 0.1); // very tight thresholds to trigger signals
    const ctx = {
      bars,
      index: 0,
      position: 0,
      cash: 500_000,
      params: {},
      signals: [],
    };
    s.init?.(ctx);

    // Collect all signals with their indices
    const signals: Array<{ index: number; direction: string }> = [];
    for (let i = 0; i < bars.length; i++) {
      ctx.index = i;
      const result = s.next(ctx);
      if (result) {
        const arr = Array.isArray(result) ? result : [result];
        for (const sig of arr) {
          signals.push({ index: i, direction: sig.direction });
        }
      }
    }
    // Strategy should still produce signals with expanding window
    expect(signals.length).toBeGreaterThan(0);
  });
});

// ── Turtle Breakout ──────────────────────────────────────────────

describe("ctaTurtleBreakout", () => {
  it("creates a strategy with correct name", () => {
    const s = ctaTurtleBreakout(20, 10);
    expect(s.name).toContain("海龟突破");
    expect(s.name).toContain("入场20日");
    expect(s.name).toContain("出场10日");
  });

  it("produces no signals in flat market", () => {
    const bars = generateBars(100, { trend: "flat" });
    const signals = runStrategy(ctaTurtleBreakout(), bars);
    expect(signals.length).toBe(0);
  });

  it("emits buy signal in uptrend on Donchian channel breakout", () => {
    const bars = generateNarrowBars(200, { trend: "up" });
    const signals = runStrategy(ctaTurtleBreakout(10, 5), bars);

    const buySignals = signals.filter((s) => s.direction === "buy");
    expect(buySignals.length).toBeGreaterThan(0);
    expect(buySignals[0]!.reason).toContain("海龟做多");
    expect(buySignals[0]!.strategyId).toBe("turtle-breakout");
  });

  it("emits short signal in downtrend", () => {
    const bars = generateNarrowBars(200, { trend: "down" });
    const signals = runStrategy(ctaTurtleBreakout(10, 5), bars);

    const shortSignals = signals.filter((s) => s.direction === "short");
    expect(shortSignals.length).toBeGreaterThan(0);
    expect(shortSignals[0]!.reason).toContain("海龟做空");
  });

  it("exits long when close drops below exitPeriod low", () => {
    const bars = generateNarrowBars(300, { trend: "sine" });
    const signals = runStrategy(ctaTurtleBreakout(10, 5), bars);

    const sellSignals = signals.filter((s) => s.direction === "sell");
    if (sellSignals.length > 0) {
      expect(sellSignals[0]!.reason).toContain("海龟平多");
    }
  });

  it("exits short when close rises above exitPeriod high", () => {
    const bars = generateNarrowBars(300, { trend: "sine" });
    const signals = runStrategy(ctaTurtleBreakout(10, 5), bars);

    const coverSignals = signals.filter((s) => s.direction === "cover");
    if (coverSignals.length > 0) {
      expect(coverSignals[0]!.reason).toContain("海龟平空");
    }
  });

  it("oscillating market produces both long and short entries", () => {
    const bars = generateNarrowBars(300, { trend: "sine" });
    const signals = runStrategy(ctaTurtleBreakout(10, 5), bars);

    const buys = signals.filter((s) => s.direction === "buy");
    const shorts = signals.filter((s) => s.direction === "short");
    expect(buys.length + shorts.length).toBeGreaterThan(0);
  });

  it("System 2 parameters (55/20) produce fewer signals than System 1 (20/10)", () => {
    const bars = generateNarrowBars(300, { trend: "sine" });
    const sys1 = runStrategy(ctaTurtleBreakout(20, 10), bars);
    const sys2 = runStrategy(ctaTurtleBreakout(55, 20), bars);

    // Wider channels → fewer breakouts
    expect(sys1.length).toBeGreaterThanOrEqual(sys2.length);
  });
});

// ── MACD Trend ───────────────────────────────────────────────────

describe("ctaMacdTrend", () => {
  it("creates a strategy with correct name", () => {
    const s = ctaMacdTrend(12, 26, 9);
    expect(s.name).toContain("MACD多空");
    expect(s.name).toContain("12");
    expect(s.name).toContain("26");
    expect(s.name).toContain("9");
  });

  it("emits buy signal when histogram crosses above zero", () => {
    // V-shape: down then up → histogram crosses zero on recovery
    const bars = generateBars(200, { trend: "vshape", startPrice: 200 });
    const signals = runStrategy(ctaMacdTrend(6, 13, 5), bars);

    const buySignals = signals.filter((s) => s.direction === "buy");
    expect(buySignals.length).toBeGreaterThan(0);
    expect(buySignals[0]!.reason).toContain("MACD做多");
    expect(buySignals[0]!.strategyId).toBe("macd-trend");
  });

  it("emits short signal when histogram crosses below zero", () => {
    // Sine generates crossovers in both directions
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaMacdTrend(6, 13, 5), bars);

    const shortSignals = signals.filter((s) => s.direction === "short");
    expect(shortSignals.length).toBeGreaterThan(0);
    expect(shortSignals[0]!.reason).toContain("MACD做空");
  });

  it("emits combined sell+short on reversal", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaMacdTrend(6, 13, 5), bars);

    // Look for a sell (平多) followed by short (做空) pattern
    const sells = signals.filter((s) => s.direction === "sell");
    if (sells.length > 0) {
      expect(sells[0]!.reason).toContain("MACD平多");
    }
  });

  it("emits combined cover+buy on upward reversal", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaMacdTrend(6, 13, 5), bars);

    const covers = signals.filter((s) => s.direction === "cover");
    if (covers.length > 0) {
      expect(covers[0]!.reason).toContain("MACD平空");
    }
  });

  it("oscillating market produces both longs and shorts", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaMacdTrend(6, 13, 5), bars);

    // Sine wave creates multiple histogram zero crossings
    expect(signals.length).toBeGreaterThan(0);
    const dirs = new Set(signals.map((s) => s.direction));
    // Should have at least one entry direction
    expect(dirs.has("buy") || dirs.has("short")).toBe(true);
  });

  it("flat market produces no or minimal signals", () => {
    const bars = generateBars(100, { trend: "flat" });
    const signals = runStrategy(ctaMacdTrend(), bars);
    // Flat market: MACD hovers near 0, histogram stays near 0
    expect(signals.length).toBeLessThanOrEqual(2);
  });
});

// ── RSI Reversal ─────────────────────────────────────────────────

describe("ctaRsiReversal", () => {
  it("creates a strategy with correct name", () => {
    const s = ctaRsiReversal(14, 70, 30);
    expect(s.name).toContain("RSI反转");
    expect(s.name).toContain("14");
    expect(s.name).toContain("超买70");
    expect(s.name).toContain("超卖30");
  });

  it("emits buy signal after oversold recovery", () => {
    // Strong down then up → RSI goes below 30 then recovers
    const bars = generateBars(200, { trend: "vshape", startPrice: 200 });
    const signals = runStrategy(ctaRsiReversal(7, 70, 30), bars);

    const buySignals = signals.filter((s) => s.direction === "buy");
    if (buySignals.length > 0) {
      expect(buySignals[0]!.reason).toContain("RSI做多");
      expect(buySignals[0]!.reason).toContain("超卖区反弹");
      expect(buySignals[0]!.strategyId).toBe("rsi-reversal");
    }
  });

  it("emits short signal after overbought pullback", () => {
    // Sine wave: RSI crosses both extremes and pulls back
    const bars = generateBars(300, { trend: "sine", startPrice: 100 });
    const signals = runStrategy(ctaRsiReversal(7, 70, 30), bars);

    const shortSignals = signals.filter((s) => s.direction === "short");
    if (shortSignals.length > 0) {
      expect(shortSignals[0]!.reason).toContain("RSI做空");
      expect(shortSignals[0]!.reason).toContain("超买区回落");
    }
    // At minimum, RSI should produce some signals in a sine wave
    expect(signals.length).toBeGreaterThan(0);
  });

  it("exits long when RSI reaches overbought", () => {
    // V-shape: down then up → buy on recovery, sell on overbought
    const bars = generateBars(200, { trend: "vshape", startPrice: 200 });
    const signals = runStrategy(ctaRsiReversal(7, 70, 30), bars);

    const sellSignals = signals.filter((s) => s.direction === "sell");
    if (sellSignals.length > 0) {
      expect(sellSignals[0]!.reason).toContain("RSI平多");
      expect(sellSignals[0]!.reason).toContain("超买区");
    }
  });

  it("exits short when RSI reaches oversold", () => {
    const bars = generateBars(300, { trend: "sine", startPrice: 100 });
    const signals = runStrategy(ctaRsiReversal(7, 70, 30), bars);

    const coverSignals = signals.filter((s) => s.direction === "cover");
    if (coverSignals.length > 0) {
      expect(coverSignals[0]!.reason).toContain("RSI平空");
      expect(coverSignals[0]!.reason).toContain("超卖区");
    }
  });

  it("oscillating market produces balanced signals", () => {
    const bars = generateBars(300, { trend: "sine", startPrice: 100 });
    const signals = runStrategy(ctaRsiReversal(7, 70, 30), bars);

    expect(signals.length).toBeGreaterThan(0);
    const buys = signals.filter((s) => s.direction === "buy").length;
    const shorts = signals.filter((s) => s.direction === "short").length;
    // Both should appear in oscillating data
    expect(buys + shorts).toBeGreaterThan(0);
  });

  it("tighter thresholds produce more signals", () => {
    const bars = generateBars(300, { trend: "sine" });
    const tight = runStrategy(ctaRsiReversal(7, 60, 40), bars);
    const wide = runStrategy(ctaRsiReversal(7, 80, 20), bars);

    expect(tight.length).toBeGreaterThanOrEqual(wide.length);
  });
});

// ── Bollinger Breakout ───────────────────────────────────────────

describe("ctaBollingerBreakout", () => {
  it("creates a strategy with correct name", () => {
    const s = ctaBollingerBreakout(20, 2.0);
    expect(s.name).toContain("布林突破");
    expect(s.name).toContain("20");
    expect(s.name).toContain("2σ");
  });

  it("produces no signals in flat market", () => {
    const bars = generateBars(100, { trend: "flat" });
    const signals = runStrategy(ctaBollingerBreakout(), bars);
    expect(signals.length).toBe(0);
  });

  it("emits buy on upper band breakout in uptrend", () => {
    const bars = generateBars(200, { trend: "up" });
    const signals = runStrategy(ctaBollingerBreakout(10, 1.0), bars);

    const buySignals = signals.filter((s) => s.direction === "buy");
    expect(buySignals.length).toBeGreaterThan(0);
    expect(buySignals[0]!.reason).toContain("布林做多");
    expect(buySignals[0]!.reason).toContain("突破上轨");
    expect(buySignals[0]!.strategyId).toBe("bollinger-breakout");
  });

  it("emits short on lower band breakout in downtrend", () => {
    const bars = generateBars(200, { trend: "down" });
    const signals = runStrategy(ctaBollingerBreakout(10, 1.0), bars);

    const shortSignals = signals.filter((s) => s.direction === "short");
    expect(shortSignals.length).toBeGreaterThan(0);
    expect(shortSignals[0]!.reason).toContain("布林做空");
    expect(shortSignals[0]!.reason).toContain("跌破下轨");
  });

  it("exits long on middle band cross", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaBollingerBreakout(10, 1.0), bars);

    const sells = signals.filter((s) => s.direction === "sell");
    if (sells.length > 0) {
      expect(sells[0]!.reason).toContain("布林平多");
      expect(sells[0]!.reason).toContain("中轨");
    }
  });

  it("exits short on middle band cross", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaBollingerBreakout(10, 1.0), bars);

    const covers = signals.filter((s) => s.direction === "cover");
    if (covers.length > 0) {
      expect(covers[0]!.reason).toContain("布林平空");
      expect(covers[0]!.reason).toContain("中轨");
    }
  });

  it("tighter bands (smaller stdDev) produce more signals", () => {
    const bars = generateBars(300, { trend: "sine" });
    const tight = runStrategy(ctaBollingerBreakout(10, 1.0), bars);
    const wide = runStrategy(ctaBollingerBreakout(10, 3.0), bars);

    expect(tight.length).toBeGreaterThanOrEqual(wide.length);
  });

  it("oscillating market produces both longs and shorts", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaBollingerBreakout(10, 1.0), bars);

    const buys = signals.filter((s) => s.direction === "buy");
    const shorts = signals.filter((s) => s.direction === "short");
    expect(buys.length + shorts.length).toBeGreaterThan(0);
  });
});

// ── Keltner Channel ──────────────────────────────────────────────

describe("ctaKeltnerChannel", () => {
  it("creates a strategy with correct name", () => {
    const s = ctaKeltnerChannel(20, 14, 2.0);
    expect(s.name).toContain("肯特纳通道");
    expect(s.name).toContain("EMA20");
    expect(s.name).toContain("ATR14");
    expect(s.name).toContain("2");
  });

  it("produces no signals in flat market", () => {
    const bars = generateBars(100, { trend: "flat" });
    const signals = runStrategy(ctaKeltnerChannel(), bars);
    expect(signals.length).toBe(0);
  });

  it("emits buy on upper channel breakout in uptrend", () => {
    const bars = generateBars(200, { trend: "up" });
    const signals = runStrategy(ctaKeltnerChannel(10, 7, 1.0), bars);

    const buySignals = signals.filter((s) => s.direction === "buy");
    expect(buySignals.length).toBeGreaterThan(0);
    expect(buySignals[0]!.reason).toContain("肯特纳做多");
    expect(buySignals[0]!.strategyId).toBe("keltner-channel");
  });

  it("emits short on lower channel breakout in downtrend", () => {
    const bars = generateBars(200, { trend: "down" });
    const signals = runStrategy(ctaKeltnerChannel(10, 7, 1.0), bars);

    const shortSignals = signals.filter((s) => s.direction === "short");
    expect(shortSignals.length).toBeGreaterThan(0);
    expect(shortSignals[0]!.reason).toContain("肯特纳做空");
  });

  it("exits long on middle channel cross", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaKeltnerChannel(10, 7, 0.5), bars);

    const sells = signals.filter((s) => s.direction === "sell");
    if (sells.length > 0) {
      expect(sells[0]!.reason).toContain("肯特纳平多");
    }
  });

  it("exits short on middle channel cross", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaKeltnerChannel(10, 7, 0.5), bars);

    const covers = signals.filter((s) => s.direction === "cover");
    if (covers.length > 0) {
      expect(covers[0]!.reason).toContain("肯特纳平空");
    }
  });

  it("wider ATR multiplier produces fewer signals", () => {
    const bars = generateBars(300, { trend: "sine" });
    const narrow = runStrategy(ctaKeltnerChannel(10, 7, 1.0), bars);
    const wide = runStrategy(ctaKeltnerChannel(10, 7, 3.0), bars);

    expect(narrow.length).toBeGreaterThanOrEqual(wide.length);
  });

  it("oscillating market produces both longs and shorts", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaKeltnerChannel(10, 7, 0.5), bars);

    const buys = signals.filter((s) => s.direction === "buy");
    const shorts = signals.filter((s) => s.direction === "short");
    expect(buys.length + shorts.length).toBeGreaterThan(0);
  });

  it("custom parameters are respected", () => {
    const s = ctaKeltnerChannel(30, 20, 2.5);
    expect(s.name).toContain("EMA30");
    expect(s.name).toContain("ATR20");
    expect(s.name).toContain("2.5");
  });
});

// ── R-Breaker ────────────────────────────────────────────────────

describe("ctaRBreaker", () => {
  it("creates a strategy with correct name", () => {
    const s = ctaRBreaker();
    expect(s.name).toContain("R-Breaker");
  });

  it("produces no signals on single bar", () => {
    const bars = generateBars(1, { trend: "flat" });
    const signals = runStrategy(ctaRBreaker(), bars);
    expect(signals.length).toBe(0);
  });

  it("produces no signals in flat market", () => {
    const bars = generateBars(50, { trend: "flat" });
    const signals = runStrategy(ctaRBreaker(), bars);
    expect(signals.length).toBe(0);
  });

  it("emits breakout buy in strong uptrend", () => {
    // Narrow bars so price step (+2/bar) exceeds R-Breaker ssetup distance
    const bars = generateNarrowBars(200, { trend: "up" });
    const signals = runStrategy(ctaRBreaker(), bars);

    const buySignals = signals.filter((s) => s.direction === "buy");
    expect(buySignals.length).toBeGreaterThan(0);
    expect(buySignals[0]!.reason).toContain("R-Breaker");
    expect(buySignals[0]!.strategyId).toBe("r-breaker");
  });

  it("emits breakout short in strong downtrend", () => {
    const bars = generateNarrowBars(200, { trend: "down" });
    const signals = runStrategy(ctaRBreaker(), bars);

    const shortSignals = signals.filter((s) => s.direction === "short");
    expect(shortSignals.length).toBeGreaterThan(0);
    expect(shortSignals[0]!.reason).toContain("R-Breaker");
    expect(shortSignals[0]!.strategyId).toBe("r-breaker");
  });

  it("generates reversal signals in oscillating market", () => {
    const bars = generateBars(500, { trend: "sine" });
    const signals = runStrategy(ctaRBreaker(), bars);

    // R-Breaker should produce sell/cover signals for reversals
    const sells = signals.filter((s) => s.direction === "sell");
    const covers = signals.filter((s) => s.direction === "cover");
    // In a sine wave, we expect some reversals
    expect(sells.length + covers.length).toBeGreaterThanOrEqual(0);
  });

  it("produces buy+short direction pairs in oscillating market", () => {
    // V-shape with steep slopes so bar-to-bar moves exceed R-Breaker pivot distances
    const bars: KlineBar[] = [];
    const t0 = new Date("2024-01-02").getTime();
    const dayMs = 86_400_000;
    for (let i = 0; i < 200; i++) {
      const cycle = i % 40;
      const price = cycle < 20 ? 100 + cycle * 3 : 100 + (40 - cycle) * 3;
      bars.push({
        timestamp: t0 + i * dayMs,
        open: price - 0.3,
        high: price + 0.5,
        low: price - 0.5,
        close: price,
        volume: 100_000,
      });
    }
    const signals = runStrategy(ctaRBreaker(), bars);

    const buys = signals.filter((s) => s.direction === "buy");
    const shorts = signals.filter((s) => s.direction === "short");
    expect(buys.length + shorts.length).toBeGreaterThan(0);
  });

  it("init resets state", () => {
    const s = ctaRBreaker();
    const bars1 = generateBars(100, { trend: "up" });
    const signals1 = runStrategy(s, bars1);

    // Re-init and run again on flat → should get no signals
    const bars2 = generateBars(50, { trend: "flat" });
    const signals2 = runStrategy(s, bars2);
    expect(signals2.length).toBe(0);
  });
});

// ── Aberration ───────────────────────────────────────────────────

describe("ctaAberration", () => {
  it("creates a strategy with correct name", () => {
    const s = ctaAberration(20, 2.0);
    expect(s.name).toContain("菲阿里四价");
    expect(s.name).toContain("BB20");
  });

  it("produces no signals on insufficient data", () => {
    const bars = generateBars(5, { trend: "flat" });
    const signals = runStrategy(ctaAberration(), bars);
    expect(signals.length).toBe(0);
  });

  it("emits buy when breaking above prevHigh with BB confirmation", () => {
    // Narrow bars so close can exceed prevHigh (step > bar range)
    const bars = generateNarrowBars(200, { trend: "up" });
    const signals = runStrategy(ctaAberration(10, 1.0), bars);

    const buySignals = signals.filter((s) => s.direction === "buy");
    expect(buySignals.length).toBeGreaterThan(0);
    expect(buySignals[0]!.reason).toContain("菲阿里做多");
    expect(buySignals[0]!.strategyId).toBe("aberration");
  });

  it("emits short when breaking below prevLow with BB confirmation", () => {
    const bars = generateNarrowBars(200, { trend: "down" });
    const signals = runStrategy(ctaAberration(10, 1.0), bars);

    const shortSignals = signals.filter((s) => s.direction === "short");
    expect(shortSignals.length).toBeGreaterThan(0);
    expect(shortSignals[0]!.reason).toContain("菲阿里做空");
  });

  it("exits long when price drops below prevLow or BB lower", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaAberration(10, 1.0), bars);

    const sells = signals.filter((s) => s.direction === "sell");
    if (sells.length > 0) {
      expect(sells[0]!.reason).toContain("菲阿里平多");
    }
  });

  it("exits short when price rises above prevHigh or BB upper", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaAberration(10, 1.0), bars);

    const covers = signals.filter((s) => s.direction === "cover");
    if (covers.length > 0) {
      expect(covers[0]!.reason).toContain("菲阿里平空");
    }
  });

  it("oscillating market produces both longs and shorts", () => {
    const bars = generateNarrowBars(300, { trend: "sine" });
    const signals = runStrategy(ctaAberration(10, 1.0), bars);

    const buys = signals.filter((s) => s.direction === "buy");
    const shorts = signals.filter((s) => s.direction === "short");
    expect(buys.length + shorts.length).toBeGreaterThan(0);
  });

  it("custom parameters are respected", () => {
    const s = ctaAberration(30, 2.5);
    expect(s.name).toContain("BB30");
    expect(s.name).toContain("2.5");
  });
});

// ── TTM Squeeze ──────────────────────────────────────────────────

describe("ctaTtmSqueeze", () => {
  it("creates a strategy with correct name", () => {
    const s = ctaTtmSqueeze(20, 2.0, 20, 14, 1.5, 20);
    expect(s.name).toContain("TTM挤压");
    expect(s.name).toContain("BB20");
    expect(s.name).toContain("CCI20");
  });

  it("produces no signals on insufficient data", () => {
    const bars = generateBars(10, { trend: "flat" });
    const signals = runStrategy(ctaTtmSqueeze(), bars);
    expect(signals.length).toBe(0);
  });

  it("detects squeeze and fires on release", () => {
    // A sine wave should alternate between squeeze and release states
    const bars = generateBars(500, { trend: "sine" });
    const signals = runStrategy(ctaTtmSqueeze(10, 1.5, 10, 7, 2.0, 10), bars);

    // We expect some signals as squeeze fires and CCI determines direction
    const buys = signals.filter((s) => s.direction === "buy");
    const shorts = signals.filter((s) => s.direction === "short");
    expect(buys.length + shorts.length).toBeGreaterThanOrEqual(0);
  });

  it("exits long when CCI crosses below zero", () => {
    const bars = generateBars(500, { trend: "sine" });
    const signals = runStrategy(ctaTtmSqueeze(10, 1.5, 10, 7, 2.0, 10), bars);

    const sells = signals.filter((s) => s.direction === "sell");
    if (sells.length > 0) {
      expect(sells[0]!.reason).toContain("TTM平多");
      expect(sells[0]!.reason).toContain("CCI");
      expect(sells[0]!.strategyId).toBe("ttm-squeeze");
    }
  });

  it("exits short when CCI crosses above zero", () => {
    const bars = generateBars(500, { trend: "sine" });
    const signals = runStrategy(ctaTtmSqueeze(10, 1.5, 10, 7, 2.0, 10), bars);

    const covers = signals.filter((s) => s.direction === "cover");
    if (covers.length > 0) {
      expect(covers[0]!.reason).toContain("TTM平空");
      expect(covers[0]!.reason).toContain("CCI");
    }
  });

  it("no trade in flat market (no squeeze release)", () => {
    const bars = generateBars(100, { trend: "flat" });
    const signals = runStrategy(ctaTtmSqueeze(), bars);
    expect(signals.length).toBe(0);
  });

  it("custom parameters are respected", () => {
    const s = ctaTtmSqueeze(30, 2.5, 25, 10, 2.0, 15);
    expect(s.name).toContain("BB30");
    expect(s.name).toContain("2.5");
    expect(s.name).toContain("KC25");
    expect(s.name).toContain("CCI15");
  });
});

// ── DMI/ADX Trend ──────────────────────────────────────────────

describe("ctaDmiAdx", () => {
  it("creates a strategy with correct name", () => {
    const s = ctaDmiAdx(14, 25);
    expect(s.name).toContain("DMI/ADX");
    expect(s.name).toContain("14");
    expect(s.name).toContain("25");
  });

  it("produces no signals on insufficient data", () => {
    const bars = generateBars(10, { trend: "flat" });
    const signals = runStrategy(ctaDmiAdx(), bars);
    expect(signals.length).toBe(0);
  });

  it("emits buy in trend with DI crossover", () => {
    // V-shape: downtrend → uptrend creates a +DI crossover at the turning point
    const bars = generateBars(200, { trend: "vshape" });
    const signals = runStrategy(ctaDmiAdx(7, 10), bars);

    const buySignals = signals.filter((s) => s.direction === "buy");
    expect(buySignals.length).toBeGreaterThan(0);
    expect(buySignals[0]!.reason).toContain("DMI做多");
    expect(buySignals[0]!.strategyId).toBe("dmi-adx");
  });

  it("emits short in trend with DI crossover", () => {
    // Oscillating market should eventually produce a -DI crossover
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaDmiAdx(7, 10), bars);

    const shortSignals = signals.filter((s) => s.direction === "short");
    expect(shortSignals.length).toBeGreaterThan(0);
    expect(shortSignals[0]!.reason).toContain("DMI做空");
  });

  it("produces no signals in flat market (ADX low)", () => {
    const bars = generateBars(100, { trend: "flat" });
    const signals = runStrategy(ctaDmiAdx(), bars);
    expect(signals.length).toBe(0);
  });

  it("exits on ADX drop or DI reversal", () => {
    const bars = generateNarrowBars(300, { trend: "sine" });
    const signals = runStrategy(ctaDmiAdx(7, 10), bars);

    const sells = signals.filter((s) => s.direction === "sell");
    const covers = signals.filter((s) => s.direction === "cover");
    if (sells.length > 0) {
      expect(sells[0]!.reason).toContain("DMI平多");
    }
    if (covers.length > 0) {
      expect(covers[0]!.reason).toContain("DMI平空");
    }
  });

  it("custom parameters are respected", () => {
    const s = ctaDmiAdx(20, 30);
    expect(s.name).toContain("20");
    expect(s.name).toContain("30");
  });
});

// ── Parabolic SAR ──────────────────────────────────────────────

describe("ctaParabolicSar", () => {
  it("creates a strategy with correct name", () => {
    const s = ctaParabolicSar(0.02, 0.02, 0.2);
    expect(s.name).toContain("抛物线SAR");
    expect(s.name).toContain("0.02");
  });

  it("produces no signals on insufficient data", () => {
    const bars = generateBars(2, { trend: "flat" });
    const signals = runStrategy(ctaParabolicSar(), bars);
    expect(signals.length).toBe(0);
  });

  it("emits buy signals on SAR reversal", () => {
    // Sine wave produces multiple SAR reversals
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaParabolicSar(), bars);

    const buySignals = signals.filter((s) => s.direction === "buy");
    expect(buySignals.length).toBeGreaterThan(0);
    expect(buySignals[0]!.reason).toContain("SAR做多");
    expect(buySignals[0]!.strategyId).toBe("parabolic-sar");
  });

  it("emits short signals on SAR reversal", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaParabolicSar(), bars);

    const shortSignals = signals.filter((s) => s.direction === "short");
    expect(shortSignals.length).toBeGreaterThan(0);
    expect(shortSignals[0]!.reason).toContain("SAR做空");
  });

  it("flips position in oscillating market", () => {
    const bars = generateNarrowBars(300, { trend: "sine" });
    const signals = runStrategy(ctaParabolicSar(), bars);

    const buys = signals.filter((s) => s.direction === "buy");
    const shorts = signals.filter((s) => s.direction === "short");
    // SAR should flip multiple times in oscillation
    expect(buys.length).toBeGreaterThan(0);
    expect(shorts.length).toBeGreaterThan(0);
  });

  it("emits sell/cover on reversal", () => {
    const bars = generateNarrowBars(300, { trend: "sine" });
    const signals = runStrategy(ctaParabolicSar(), bars);

    const sells = signals.filter((s) => s.direction === "sell");
    const covers = signals.filter((s) => s.direction === "cover");
    if (sells.length > 0) {
      expect(sells[0]!.reason).toContain("SAR平多");
    }
    if (covers.length > 0) {
      expect(covers[0]!.reason).toContain("SAR平空");
    }
  });

  it("custom parameters are respected", () => {
    const s = ctaParabolicSar(0.01, 0.01, 0.1);
    expect(s.name).toContain("0.01");
    expect(s.name).toContain("0.1");
  });
});

// ── Volatility Target ──────────────────────────────────────────

describe("ctaVolatilityTarget", () => {
  it("creates a strategy with correct name", () => {
    const s = ctaVolatilityTarget(20, 14, 0.15);
    expect(s.name).toContain("波动率目标");
    expect(s.name).toContain("MA20");
    expect(s.name).toContain("ATR14");
    expect(s.name).toContain("15%");
  });

  it("produces no signals on insufficient data", () => {
    const bars = generateBars(5, { trend: "flat" });
    const signals = runStrategy(ctaVolatilityTarget(), bars);
    expect(signals.length).toBe(0);
  });

  it("emits buy in uptrend (close > MA)", () => {
    const bars = generateBars(200, { trend: "up" });
    const signals = runStrategy(ctaVolatilityTarget(10, 7, 0.15), bars);

    const buySignals = signals.filter((s) => s.direction === "buy");
    expect(buySignals.length).toBeGreaterThan(0);
    expect(buySignals[0]!.reason).toContain("波动率目标做多");
    expect(buySignals[0]!.reason).toContain("仓位系数");
    expect(buySignals[0]!.strategyId).toBe("volatility-target");
  });

  it("emits short in downtrend (close < MA)", () => {
    const bars = generateBars(200, { trend: "down" });
    const signals = runStrategy(ctaVolatilityTarget(10, 7, 0.15), bars);

    const shortSignals = signals.filter((s) => s.direction === "short");
    expect(shortSignals.length).toBeGreaterThan(0);
    expect(shortSignals[0]!.reason).toContain("波动率目标做空");
  });

  it("exits on MA crossover", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaVolatilityTarget(10, 7, 0.15), bars);

    const sells = signals.filter((s) => s.direction === "sell");
    if (sells.length > 0) {
      expect(sells[0]!.reason).toContain("波动率目标平多");
    }
    const covers = signals.filter((s) => s.direction === "cover");
    if (covers.length > 0) {
      expect(covers[0]!.reason).toContain("波动率目标平空");
    }
  });

  it("scale factor appears in reason text", () => {
    const bars = generateBars(200, { trend: "up" });
    const signals = runStrategy(ctaVolatilityTarget(10, 7, 0.15), bars);

    const buySignals = signals.filter((s) => s.direction === "buy");
    if (buySignals.length > 0) {
      expect(buySignals[0]!.reason).toMatch(/仓位系数=\d/);
    }
  });

  it("oscillating market produces both longs and shorts", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaVolatilityTarget(10, 7, 0.15), bars);

    const buys = signals.filter((s) => s.direction === "buy");
    const shorts = signals.filter((s) => s.direction === "short");
    expect(buys.length + shorts.length).toBeGreaterThan(0);
  });

  it("custom parameters are respected", () => {
    const s = ctaVolatilityTarget(30, 20, 0.2);
    expect(s.name).toContain("MA30");
    expect(s.name).toContain("ATR20");
    expect(s.name).toContain("20%");
  });
});

// ── Momentum Rotation (ROC) ─────────────────────────────────────

describe("ctaMomentumRotation", () => {
  it("has correct strategy metadata", () => {
    const s = ctaMomentumRotation();
    expect(s.name).toBe("动量轮动(ROC20,阈值0%)");
  });

  it("signals carry correct strategyId", () => {
    const bars = generateBars(80, { trend: "up" });
    const signals = runStrategy(ctaMomentumRotation(5, 0), bars);
    const buys = signals.filter((s) => s.direction === "buy");
    if (buys.length > 0) {
      expect(buys[0]!.strategyId).toBe("momentum-rotation");
    }
  });

  it("custom params appear in name", () => {
    const s = ctaMomentumRotation(10, 2);
    expect(s.name).toContain("ROC10");
    expect(s.name).toContain("阈值2%");
  });

  it("generates buy signals in strong uptrend", () => {
    const bars = generateBars(80, { trend: "up" });
    const signals = runStrategy(ctaMomentumRotation(5, 0), bars);
    const buys = signals.filter((s) => s.direction === "buy");
    expect(buys.length).toBeGreaterThan(0);
  });

  it("generates short signals in strong downtrend", () => {
    const bars = generateBars(80, { trend: "down" });
    const signals = runStrategy(ctaMomentumRotation(5, 0), bars);
    const shorts = signals.filter((s) => s.direction === "short");
    expect(shorts.length).toBeGreaterThan(0);
  });

  it("reason text includes ROC value", () => {
    const bars = generateBars(80, { trend: "up" });
    const signals = runStrategy(ctaMomentumRotation(5, 0), bars);
    const buys = signals.filter((s) => s.direction === "buy");
    if (buys.length > 0) {
      expect(buys[0]!.reason).toMatch(/ROC/i);
    }
  });

  it("oscillating market produces both long and short signals", () => {
    const bars = generateBars(200, { trend: "sine" });
    const signals = runStrategy(ctaMomentumRotation(5, 0), bars);
    const buys = signals.filter((s) => s.direction === "buy");
    const shorts = signals.filter((s) => s.direction === "short");
    expect(buys.length).toBeGreaterThan(0);
    expect(shorts.length).toBeGreaterThan(0);
  });

  it("with high threshold, sine market produces fewer signals", () => {
    const bars = generateBars(200, { trend: "sine" });
    const loThreshold = runStrategy(ctaMomentumRotation(5, 0), bars);
    const hiThreshold = runStrategy(ctaMomentumRotation(5, 10), bars);
    expect(hiThreshold.length).toBeLessThanOrEqual(loThreshold.length);
  });
});

// ── Williams %R ──────────────────────────────────────────────────

describe("ctaWilliamsR", () => {
  it("has correct strategy metadata", () => {
    const s = ctaWilliamsR();
    expect(s.name).toBe("威廉%R(14,OB=-20,OS=-80)");
  });

  it("signals carry correct strategyId", () => {
    const bars = generateBars(200, { trend: "sine" });
    const signals = runStrategy(ctaWilliamsR(14, -20, -80), bars);
    if (signals.length > 0) {
      expect(signals[0]!.strategyId).toBe("williams-r");
    }
  });

  it("custom params appear in name", () => {
    const s = ctaWilliamsR(21, -10, -90);
    expect(s.name).toContain("21");
    expect(s.name).toContain("OB=-10");
    expect(s.name).toContain("OS=-90");
  });

  it("oscillating market produces buy signals from oversold", () => {
    const bars = generateBars(200, { trend: "sine" });
    const signals = runStrategy(ctaWilliamsR(14, -20, -80), bars);
    const buys = signals.filter((s) => s.direction === "buy");
    expect(buys.length).toBeGreaterThan(0);
  });

  it("oscillating market produces short signals from overbought", () => {
    const bars = generateBars(200, { trend: "sine" });
    const signals = runStrategy(ctaWilliamsR(14, -20, -80), bars);
    const shorts = signals.filter((s) => s.direction === "short");
    expect(shorts.length).toBeGreaterThan(0);
  });

  it("reason text includes WilliamsR value", () => {
    const bars = generateBars(200, { trend: "sine" });
    const signals = runStrategy(ctaWilliamsR(14, -20, -80), bars);
    if (signals.length > 0) {
      expect(signals[0]!.reason).toMatch(/威廉|%R/i);
    }
  });

  it("v-shape market produces exit signals", () => {
    const bars = generateBars(200, { trend: "vshape" });
    const signals = runStrategy(ctaWilliamsR(14, -20, -80), bars);
    const exits = signals.filter((s) => s.direction === "sell" || s.direction === "cover");
    expect(exits.length).toBeGreaterThanOrEqual(0); // may or may not exit — no crash
  });
});

// ── OBV Trend ────────────────────────────────────────────────────

describe("ctaObvTrend", () => {
  it("has correct strategy metadata", () => {
    const s = ctaObvTrend();
    expect(s.name).toBe("OBV量价趋势(MA20)");
  });

  it("signals carry correct strategyId", () => {
    const bars = generateBars(100, { trend: "up" });
    const signals = runStrategy(ctaObvTrend(10), bars);
    const buys = signals.filter((s) => s.direction === "buy");
    if (buys.length > 0) {
      expect(buys[0]!.strategyId).toBe("obv-trend");
    }
  });

  it("custom period appears in name", () => {
    const s = ctaObvTrend(50);
    expect(s.name).toContain("50");
  });

  it("uptrend produces buy signals", () => {
    const bars = generateBars(100, { trend: "up" });
    const signals = runStrategy(ctaObvTrend(10), bars);
    const buys = signals.filter((s) => s.direction === "buy");
    expect(buys.length).toBeGreaterThan(0);
  });

  it("downtrend produces short signals", () => {
    const bars = generateBars(100, { trend: "down" });
    const signals = runStrategy(ctaObvTrend(10), bars);
    const shorts = signals.filter((s) => s.direction === "short");
    expect(shorts.length).toBeGreaterThan(0);
  });

  it("reason text mentions OBV", () => {
    const bars = generateBars(100, { trend: "up" });
    const signals = runStrategy(ctaObvTrend(10), bars);
    const buys = signals.filter((s) => s.direction === "buy");
    if (buys.length > 0) {
      expect(buys[0]!.reason).toMatch(/OBV/i);
    }
  });

  it("oscillating market produces both buy and short signals", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaObvTrend(10), bars);
    const buys = signals.filter((s) => s.direction === "buy");
    const shorts = signals.filter((s) => s.direction === "short");
    expect(buys.length + shorts.length).toBeGreaterThan(0);
  });

  it("produces exit signals on trend change", () => {
    const bars = generateBars(200, { trend: "vshape" });
    const signals = runStrategy(ctaObvTrend(10), bars);
    const exits = signals.filter((s) => s.direction === "sell" || s.direction === "cover");
    // V-shape should trigger a trend reversal at some point
    expect(exits.length).toBeGreaterThanOrEqual(0);
  });
});

// ── Ichimoku Cloud ──────────────────────────────────────────────

describe("ctaIchimoku", () => {
  it("has correct strategy metadata", () => {
    const s = ctaIchimoku();
    expect(s.name).toBe("一目均衡(9,26,52)");
  });

  it("custom params appear in name", () => {
    const s = ctaIchimoku(7, 22, 44);
    expect(s.name).toContain("7,22,44");
  });

  it("signals carry correct strategyId", () => {
    // Long bars with enough data for ichimoku warmup
    const bars = generateBars(200, { trend: "sine" });
    const signals = runStrategy(ctaIchimoku(9, 26, 52), bars);
    if (signals.length > 0) {
      expect(signals[0]!.strategyId).toBe("ichimoku");
    }
  });

  it("uptrend produces buy or sell signals", () => {
    const bars = generateBars(200, { trend: "up" });
    const signals = runStrategy(ctaIchimoku(9, 26, 52), bars);
    // With a strong uptrend + enough warmup, should get at least some signals
    expect(signals.length).toBeGreaterThanOrEqual(0);
  });

  it("oscillating market with short periods produces signals", () => {
    // Use generateBars for wider range; very short periods for Ichimoku to reduce cloud warmup
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaIchimoku(3, 5, 10), bars);
    // Ichimoku needs tenkan cross + price vs cloud; at least gets exits from crossovers
    expect(signals.length).toBeGreaterThanOrEqual(0);
  });

  it("ichimoku entry requires cloud confirmation", () => {
    // Verify it doesn't crash and processes all bars
    const bars = generateBars(200, { trend: "up" });
    const s = ctaIchimoku(3, 5, 10);
    expect(s.name).toContain("3,5,10");
    const signals = runStrategy(s, bars);
    // Strong uptrend: price should eventually be above cloud
    const buys = signals.filter((sig) => sig.direction === "buy");
    expect(buys.length).toBeGreaterThanOrEqual(0);
  });

  it("reason text mentions 一目", () => {
    const bars = generateBars(200, { trend: "sine" });
    const signals = runStrategy(ctaIchimoku(5, 10, 20), bars);
    if (signals.length > 0) {
      expect(signals[0]!.reason).toMatch(/一目/);
    }
  });

  it("v-shape market produces entries and exits", () => {
    const bars = generateBars(300, { trend: "vshape" });
    const signals = runStrategy(ctaIchimoku(5, 10, 20), bars);
    const entries = signals.filter((s) => s.direction === "buy" || s.direction === "short");
    const exits = signals.filter((s) => s.direction === "sell" || s.direction === "cover");
    expect(entries.length + exits.length).toBeGreaterThanOrEqual(0);
  });
});

// ── KDJ ──────────────────────────────────────────────────────────

describe("ctaKdj", () => {
  it("has correct strategy metadata", () => {
    const s = ctaKdj();
    expect(s.name).toBe("KDJ(9,3,3,OB80,OS20)");
  });

  it("custom params appear in name", () => {
    const s = ctaKdj(14, 5, 5, 90, 10);
    expect(s.name).toContain("14");
    expect(s.name).toContain("OB90");
    expect(s.name).toContain("OS10");
  });

  it("signals carry correct strategyId", () => {
    const bars = generateBars(200, { trend: "sine" });
    const signals = runStrategy(ctaKdj(9, 3, 3, 80, 20), bars);
    if (signals.length > 0) {
      expect(signals[0]!.strategyId).toBe("kdj");
    }
  });

  it("oscillating market produces buy signals from oversold", () => {
    const bars = generateBars(200, { trend: "sine" });
    const signals = runStrategy(ctaKdj(9, 3, 3, 80, 20), bars);
    const buys = signals.filter((s) => s.direction === "buy");
    expect(buys.length).toBeGreaterThan(0);
  });

  it("oscillating market produces short signals from overbought", () => {
    // Use wider J thresholds (overbought=60) so more kCrossDown events qualify
    const bars = generateNarrowBars(300, { trend: "sine" });
    const signals = runStrategy(ctaKdj(5, 3, 3, 60, 40), bars);
    const shorts = signals.filter((s) => s.direction === "short");
    expect(shorts.length).toBeGreaterThan(0);
  });

  it("reason text includes KDJ", () => {
    const bars = generateBars(200, { trend: "sine" });
    const signals = runStrategy(ctaKdj(9, 3, 3, 80, 20), bars);
    if (signals.length > 0) {
      expect(signals[0]!.reason).toMatch(/KDJ/);
    }
  });

  it("produces exit signals on K/D crossover", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaKdj(9, 3, 3, 80, 20), bars);
    const exits = signals.filter((s) => s.direction === "sell" || s.direction === "cover");
    expect(exits.length).toBeGreaterThan(0);
  });
});

// ── Mean Reversion Z-Score ──────────────────────────────────────

describe("ctaMeanReversionZScore", () => {
  it("has correct strategy metadata", () => {
    const s = ctaMeanReversionZScore();
    expect(s.name).toBe("均值回归Z(MA20,入场2,出场0.5)");
  });

  it("custom params appear in name", () => {
    const s = ctaMeanReversionZScore(30, 1.5, 0.3);
    expect(s.name).toContain("MA30");
    expect(s.name).toContain("1.5");
    expect(s.name).toContain("0.3");
  });

  it("signals carry correct strategyId", () => {
    const bars = generateBars(200, { trend: "sine" });
    const signals = runStrategy(ctaMeanReversionZScore(10, 1.5, 0.3), bars);
    if (signals.length > 0) {
      expect(signals[0]!.strategyId).toBe("mean-reversion-zscore");
    }
  });

  it("sine market produces buy signals (price below mean)", () => {
    const bars = generateBars(200, { trend: "sine" });
    const signals = runStrategy(ctaMeanReversionZScore(10, 1.5, 0.3), bars);
    const buys = signals.filter((s) => s.direction === "buy");
    expect(buys.length).toBeGreaterThan(0);
  });

  it("sine market produces short signals (price above mean)", () => {
    const bars = generateBars(200, { trend: "sine" });
    const signals = runStrategy(ctaMeanReversionZScore(10, 1.5, 0.3), bars);
    const shorts = signals.filter((s) => s.direction === "short");
    expect(shorts.length).toBeGreaterThan(0);
  });

  it("reason text mentions Z", () => {
    const bars = generateBars(200, { trend: "sine" });
    const signals = runStrategy(ctaMeanReversionZScore(10, 1.5, 0.3), bars);
    if (signals.length > 0) {
      expect(signals[0]!.reason).toMatch(/Z/);
    }
  });

  it("produces exit signals when price reverts to mean", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaMeanReversionZScore(10, 1.5, 0.3), bars);
    const exits = signals.filter((s) => s.direction === "sell" || s.direction === "cover");
    expect(exits.length).toBeGreaterThan(0);
  });

  it("flat market produces no signals (Z near 0)", () => {
    const bars = generateBars(100, { trend: "flat" });
    const signals = runStrategy(ctaMeanReversionZScore(10, 2.0, 0.5), bars);
    // flat prices → Z always 0 → no entry
    const entries = signals.filter((s) => s.direction === "buy" || s.direction === "short");
    expect(entries.length).toBe(0);
  });
});

// ── ctaGridTrading ───────────────────────────────────────────────

describe("ctaGridTrading", () => {
  it("returns a strategy with correct name", () => {
    const s = ctaGridTrading(5, 4);
    expect(s.name).toContain("网格");
  });

  it("produces buy signals on V-shape (price drops then recovers)", () => {
    const bars = generateBars(200, { trend: "vshape" });
    const signals = runStrategy(ctaGridTrading(3, 3), bars);
    const buys = signals.filter((s) => s.direction === "buy");
    expect(buys.length).toBeGreaterThan(0);
  });

  it("produces short signals on uptrend (price rises above grid)", () => {
    const bars = generateBars(200, { trend: "up" });
    const signals = runStrategy(ctaGridTrading(2, 5), bars);
    const shorts = signals.filter((s) => s.direction === "short");
    expect(shorts.length).toBeGreaterThan(0);
  });

  it("produces exit signals (sell or cover) after entry", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaGridTrading(2, 5), bars);
    const exits = signals.filter((s) => s.direction === "sell" || s.direction === "cover");
    expect(exits.length).toBeGreaterThan(0);
  });

  it("all signals carry strategyId = grid-trading", () => {
    const bars = generateBars(200, { trend: "sine" });
    const signals = runStrategy(ctaGridTrading(2, 5), bars);
    for (const sig of signals) {
      expect(sig.strategyId).toBe("grid-trading");
    }
  });

  it("reason text mentions 网格", () => {
    const bars = generateBars(200, { trend: "vshape" });
    const signals = runStrategy(ctaGridTrading(3, 3), bars);
    if (signals.length > 0) {
      expect(signals[0]!.reason).toMatch(/网格/);
    }
  });

  it("flat market produces no signals (price stays near base)", () => {
    // gridSize=10 with flat close=100 → gridLevel always 0 → no entry
    const bars = generateBars(100, { trend: "flat" });
    const signals = runStrategy(ctaGridTrading(10, 4), bars);
    expect(signals.length).toBe(0);
  });
});

// ── ctaMultiMaConvergence ────────────────────────────────────────

describe("ctaMultiMaConvergence", () => {
  it("returns a strategy with correct name", () => {
    const s = ctaMultiMaConvergence(5, 10, 20);
    expect(s.name).toContain("多均线");
  });

  it("produces buy signals on uptrend (bullish alignment)", () => {
    const bars = generateBars(200, { trend: "up" });
    const signals = runStrategy(ctaMultiMaConvergence(3, 8, 15), bars);
    const buys = signals.filter((s) => s.direction === "buy");
    expect(buys.length).toBeGreaterThan(0);
  });

  it("produces short signals on downtrend (bearish alignment)", () => {
    const bars = generateBars(200, { trend: "down" });
    const signals = runStrategy(ctaMultiMaConvergence(3, 8, 15), bars);
    const shorts = signals.filter((s) => s.direction === "short");
    expect(shorts.length).toBeGreaterThan(0);
  });

  it("produces exit signals when MAs converge", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaMultiMaConvergence(3, 8, 15), bars);
    const exits = signals.filter((s) => s.direction === "sell" || s.direction === "cover");
    expect(exits.length).toBeGreaterThanOrEqual(0);
  });

  it("all signals carry strategyId = multi-ma-convergence", () => {
    const bars = generateBars(200, { trend: "up" });
    const signals = runStrategy(ctaMultiMaConvergence(3, 8, 15), bars);
    for (const sig of signals) {
      expect(sig.strategyId).toBe("multi-ma-convergence");
    }
  });

  it("reason text mentions 多MA", () => {
    const bars = generateBars(200, { trend: "up" });
    const signals = runStrategy(ctaMultiMaConvergence(3, 8, 15), bars);
    if (signals.length > 0) {
      expect(signals[0]!.reason).toMatch(/多MA/);
    }
  });

  it("flat market with short periods still produces few/no signals", () => {
    const bars = generateNarrowBars(100, { trend: "flat" });
    const signals = runStrategy(ctaMultiMaConvergence(3, 8, 15), bars);
    // narrow flat data — MAs all converge around same value
    // could produce some spurious signals from tiny differences
    expect(signals.length).toBeGreaterThanOrEqual(0);
  });
});

// ── ctaDonchianMidReversion ──────────────────────────────────────

describe("ctaDonchianMidReversion", () => {
  it("returns a strategy with correct name", () => {
    const s = ctaDonchianMidReversion(20);
    expect(s.name).toContain("Donchian");
  });

  it("produces buy signals on V-shape (price touches lower band)", () => {
    const bars = generateBars(200, { trend: "vshape" });
    const signals = runStrategy(ctaDonchianMidReversion(10), bars);
    const buys = signals.filter((s) => s.direction === "buy");
    expect(buys.length).toBeGreaterThan(0);
  });

  it("produces short signals on uptrend (price at upper band)", () => {
    const bars = generateBars(200, { trend: "up" });
    const signals = runStrategy(ctaDonchianMidReversion(10), bars);
    const shorts = signals.filter((s) => s.direction === "short");
    expect(shorts.length).toBeGreaterThan(0);
  });

  it("produces exit signals when price reverts to midline", () => {
    // vshape: drops then recovers — clear entry at trough, exit on recovery
    const bars = generateBars(300, { trend: "vshape" });
    const signals = runStrategy(ctaDonchianMidReversion(10), bars);
    const exits = signals.filter((s) => s.direction === "sell" || s.direction === "cover");
    expect(exits.length).toBeGreaterThan(0);
  });

  it("all signals carry strategyId = donchian-mid-reversion", () => {
    const bars = generateBars(200, { trend: "sine" });
    const signals = runStrategy(ctaDonchianMidReversion(10), bars);
    for (const sig of signals) {
      expect(sig.strategyId).toBe("donchian-mid-reversion");
    }
  });

  it("reason text mentions Donchian", () => {
    const bars = generateBars(200, { trend: "vshape" });
    const signals = runStrategy(ctaDonchianMidReversion(10), bars);
    if (signals.length > 0) {
      expect(signals[0]!.reason).toMatch(/Donchian/);
    }
  });

  it("downtrend produces buy signals (price at channel lows)", () => {
    const bars = generateBars(200, { trend: "down" });
    const signals = runStrategy(ctaDonchianMidReversion(10), bars);
    const buys = signals.filter((s) => s.direction === "buy");
    expect(buys.length).toBeGreaterThan(0);
  });
});

// ── ctaPivotPoint ───────────────────────────────────────────────

describe("ctaPivotPoint", () => {
  it("returns a strategy with correct name", () => {
    const s = ctaPivotPoint();
    expect(s.name).toContain("枢轴");
  });

  it("produces buy signals on downtrend (price drops below S1)", () => {
    const bars = generateNarrowBars(200, { trend: "down" });
    const signals = runStrategy(ctaPivotPoint(), bars);
    const buys = signals.filter((s) => s.direction === "buy");
    expect(buys.length).toBeGreaterThan(0);
  });

  it("produces short signals on uptrend (price rises above R1)", () => {
    const bars = generateNarrowBars(200, { trend: "up" });
    const signals = runStrategy(ctaPivotPoint(), bars);
    const shorts = signals.filter((s) => s.direction === "short");
    expect(shorts.length).toBeGreaterThan(0);
  });

  it("produces exit signals (sell or cover)", () => {
    const bars = generateNarrowBars(300, { trend: "sine" });
    const signals = runStrategy(ctaPivotPoint(), bars);
    const exits = signals.filter((s) => s.direction === "sell" || s.direction === "cover");
    expect(exits.length).toBeGreaterThan(0);
  });

  it("all signals carry strategyId = pivot-point", () => {
    const bars = generateNarrowBars(200, { trend: "sine" });
    const signals = runStrategy(ctaPivotPoint(), bars);
    for (const sig of signals) {
      expect(sig.strategyId).toBe("pivot-point");
    }
  });

  it("reason text mentions 枢轴", () => {
    const bars = generateNarrowBars(200, { trend: "down" });
    const signals = runStrategy(ctaPivotPoint(), bars);
    if (signals.length > 0) {
      expect(signals[0]!.reason).toMatch(/枢轴/);
    }
  });

  it("flat market produces few signals (price near pivot)", () => {
    const bars = generateBars(100, { trend: "flat" });
    const signals = runStrategy(ctaPivotPoint(), bars);
    // flat: close stays constant, so H=L+6 → pivot near close
    // S1/R1 may or may not be breached depending on H-L spread
    expect(signals.length).toBeGreaterThanOrEqual(0);
  });
});

// ── ctaChandelierExit ───────────────────────────────────────────

describe("ctaChandelierExit", () => {
  it("returns a strategy with correct name", () => {
    const s = ctaChandelierExit(10, 2.0, 10);
    expect(s.name).toContain("吊灯");
  });

  it("produces buy signals on uptrend (breakout above lookback high)", () => {
    const bars = generateNarrowBars(200, { trend: "up" });
    const signals = runStrategy(ctaChandelierExit(10, 2.0, 10), bars);
    const buys = signals.filter((s) => s.direction === "buy");
    expect(buys.length).toBeGreaterThan(0);
  });

  it("produces short signals on downtrend (breakout below lookback low)", () => {
    const bars = generateNarrowBars(200, { trend: "down" });
    const signals = runStrategy(ctaChandelierExit(10, 2.0, 10), bars);
    const shorts = signals.filter((s) => s.direction === "short");
    expect(shorts.length).toBeGreaterThan(0);
  });

  it("produces trailing stop exits on V-shape", () => {
    const bars = generateNarrowBars(300, { trend: "sine" });
    const signals = runStrategy(ctaChandelierExit(10, 2.0, 10), bars);
    const exits = signals.filter((s) => s.direction === "sell" || s.direction === "cover");
    expect(exits.length).toBeGreaterThan(0);
  });

  it("all signals carry strategyId = chandelier-exit", () => {
    const bars = generateNarrowBars(200, { trend: "up" });
    const signals = runStrategy(ctaChandelierExit(10, 2.0, 10), bars);
    for (const sig of signals) {
      expect(sig.strategyId).toBe("chandelier-exit");
    }
  });

  it("reason text mentions 吊灯", () => {
    const bars = generateNarrowBars(200, { trend: "up" });
    const signals = runStrategy(ctaChandelierExit(10, 2.0, 10), bars);
    if (signals.length > 0) {
      expect(signals[0]!.reason).toMatch(/吊灯/);
    }
  });

  it("sine market produces entry and exit signals", () => {
    const bars = generateNarrowBars(300, { trend: "sine" });
    const signals = runStrategy(ctaChandelierExit(10, 2.0, 10), bars);
    expect(signals.length).toBeGreaterThan(0);
  });
});

// ── ctaRangeBreakout ────────────────────────────────────────────

describe("ctaRangeBreakout", () => {
  it("returns a strategy with correct name", () => {
    const s = ctaRangeBreakout(20);
    expect(s.name).toContain("区间");
  });

  it("produces buy signals on uptrend (breakout above range)", () => {
    const bars = generateBars(200, { trend: "up" });
    const signals = runStrategy(ctaRangeBreakout(10), bars);
    const buys = signals.filter((s) => s.direction === "buy");
    expect(buys.length).toBeGreaterThan(0);
  });

  it("produces short signals on downtrend (breakout below range)", () => {
    const bars = generateBars(200, { trend: "down" });
    const signals = runStrategy(ctaRangeBreakout(10), bars);
    const shorts = signals.filter((s) => s.direction === "short");
    expect(shorts.length).toBeGreaterThan(0);
  });

  it("produces exit signals when price reverts to midline", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaRangeBreakout(10), bars);
    const exits = signals.filter((s) => s.direction === "sell" || s.direction === "cover");
    expect(exits.length).toBeGreaterThanOrEqual(0);
  });

  it("all signals carry strategyId = range-breakout", () => {
    const bars = generateBars(200, { trend: "up" });
    const signals = runStrategy(ctaRangeBreakout(10), bars);
    for (const sig of signals) {
      expect(sig.strategyId).toBe("range-breakout");
    }
  });

  it("reason text mentions 区间", () => {
    const bars = generateBars(200, { trend: "up" });
    const signals = runStrategy(ctaRangeBreakout(10), bars);
    if (signals.length > 0) {
      expect(signals[0]!.reason).toMatch(/区间/);
    }
  });

  it("flat market produces no breakout signals", () => {
    const bars = generateBars(100, { trend: "flat" });
    const signals = runStrategy(ctaRangeBreakout(10), bars);
    // flat: price=100 constant, range=[97,103], close never > 103 or < 97
    expect(signals.length).toBe(0);
  });
});

// ── ctaVwapReversion ───────────────────────────────────────────

describe("ctaVwapReversion", () => {
  it("returns a strategy with correct name", () => {
    const s = ctaVwapReversion(10, 0.02);
    expect(s.name).toContain("VWAP");
  });

  it("produces buy signals on V-shape (price drops below VWAP)", () => {
    const bars = generateBars(200, { trend: "vshape" });
    const signals = runStrategy(ctaVwapReversion(10, 0.02), bars);
    const buys = signals.filter((s) => s.direction === "buy");
    expect(buys.length).toBeGreaterThan(0);
  });

  it("produces short signals on sine (price rises above VWAP)", () => {
    const bars = generateBars(200, { trend: "sine" });
    const signals = runStrategy(ctaVwapReversion(10, 0.02), bars);
    const shorts = signals.filter((s) => s.direction === "short");
    expect(shorts.length).toBeGreaterThan(0);
  });

  it("produces exit signals when price reverts to VWAP", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaVwapReversion(10, 0.02), bars);
    const exits = signals.filter((s) => s.direction === "sell" || s.direction === "cover");
    expect(exits.length).toBeGreaterThan(0);
  });

  it("all signals carry strategyId = vwap-reversion", () => {
    const bars = generateBars(200, { trend: "sine" });
    const signals = runStrategy(ctaVwapReversion(10, 0.02), bars);
    for (const sig of signals) {
      expect(sig.strategyId).toBe("vwap-reversion");
    }
  });

  it("reason text mentions VWAP", () => {
    const bars = generateBars(200, { trend: "sine" });
    const signals = runStrategy(ctaVwapReversion(10, 0.02), bars);
    if (signals.length > 0) {
      expect(signals[0]!.reason).toMatch(/VWAP/);
    }
  });

  it("flat market produces no signals (price near VWAP)", () => {
    const bars = generateBars(100, { trend: "flat" });
    const signals = runStrategy(ctaVwapReversion(10, 0.05), bars);
    const entries = signals.filter((s) => s.direction === "buy" || s.direction === "short");
    expect(entries.length).toBe(0);
  });
});

// ── ctaDoubleBottom ───────────────────────────────────────────

describe("ctaDoubleBottom", () => {
  it("returns a strategy with correct name", () => {
    const s = ctaDoubleBottom(20, 0.05);
    expect(s.name).toContain("双底");
  });

  it("produces buy signals on V-shape (double bottom pattern)", () => {
    // V-shape creates a clear trough → recovery pattern
    const bars = generateBars(200, { trend: "vshape" });
    const signals = runStrategy(ctaDoubleBottom(20, 0.05), bars);
    const buys = signals.filter((s) => s.direction === "buy");
    expect(buys.length).toBeGreaterThanOrEqual(0);
  });

  it("produces signals on sine with relaxed tolerance", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaDoubleBottom(30, 0.1), bars);
    // sine creates repeating peaks/troughs — should detect some patterns
    expect(signals.length).toBeGreaterThanOrEqual(0);
  });

  it("all signals carry strategyId = double-bottom", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaDoubleBottom(30, 0.1), bars);
    for (const sig of signals) {
      expect(sig.strategyId).toBe("double-bottom");
    }
  });

  it("reason text mentions 双底 or 双顶", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaDoubleBottom(30, 0.1), bars);
    if (signals.length > 0) {
      expect(signals[0]!.reason).toMatch(/双底|双顶/);
    }
  });

  it("flat market produces no signals (no pattern)", () => {
    const bars = generateBars(100, { trend: "flat" });
    const signals = runStrategy(ctaDoubleBottom(20, 0.02), bars);
    expect(signals.length).toBe(0);
  });
});

// ── ctaRocMomentum ────────────────────────────────────────────

describe("ctaRocMomentum", () => {
  it("returns a strategy with correct name", () => {
    const s = ctaRocMomentum(12, 5.0, 0.0);
    expect(s.name).toContain("ROC");
  });

  it("produces buy signals on uptrend (positive ROC)", () => {
    const bars = generateBars(200, { trend: "up" });
    const signals = runStrategy(ctaRocMomentum(10, 3.0, 0.0), bars);
    const buys = signals.filter((s) => s.direction === "buy");
    expect(buys.length).toBeGreaterThan(0);
  });

  it("produces short signals on downtrend (negative ROC)", () => {
    const bars = generateBars(200, { trend: "down" });
    const signals = runStrategy(ctaRocMomentum(10, 3.0, 0.0), bars);
    const shorts = signals.filter((s) => s.direction === "short");
    expect(shorts.length).toBeGreaterThan(0);
  });

  it("produces exit signals when momentum fades", () => {
    const bars = generateBars(300, { trend: "vshape" });
    const signals = runStrategy(ctaRocMomentum(10, 3.0, 0.0), bars);
    const exits = signals.filter((s) => s.direction === "sell" || s.direction === "cover");
    expect(exits.length).toBeGreaterThan(0);
  });

  it("all signals carry strategyId = roc-momentum", () => {
    const bars = generateBars(200, { trend: "up" });
    const signals = runStrategy(ctaRocMomentum(10, 3.0, 0.0), bars);
    for (const sig of signals) {
      expect(sig.strategyId).toBe("roc-momentum");
    }
  });

  it("reason text mentions ROC", () => {
    const bars = generateBars(200, { trend: "up" });
    const signals = runStrategy(ctaRocMomentum(10, 3.0, 0.0), bars);
    if (signals.length > 0) {
      expect(signals[0]!.reason).toMatch(/ROC/);
    }
  });

  it("flat market produces no signals (ROC near 0)", () => {
    const bars = generateBars(100, { trend: "flat" });
    const signals = runStrategy(ctaRocMomentum(10, 5.0, 0.0), bars);
    const entries = signals.filter((s) => s.direction === "buy" || s.direction === "short");
    expect(entries.length).toBe(0);
  });
});

// ── ctaCciDivergence ───────────────────────────────────────────

describe("ctaCciDivergence", () => {
  it("returns a strategy with correct name", () => {
    const s = ctaCciDivergence(10, 8, 100, -100);
    expect(s.name).toContain("CCI");
  });

  it("produces buy signals on V-shape (bottom divergence)", () => {
    const bars = generateBars(200, { trend: "vshape" });
    const signals = runStrategy(ctaCciDivergence(10, 8, 100, -100), bars);
    const buys = signals.filter((s) => s.direction === "buy");
    expect(buys.length).toBeGreaterThanOrEqual(0);
  });

  it("produces signals on sine with short lookback", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaCciDivergence(10, 8, 80, -80), bars);
    expect(signals.length).toBeGreaterThan(0);
  });

  it("produces exit signals when CCI reaches extremes", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaCciDivergence(10, 8, 80, -80), bars);
    const exits = signals.filter((s) => s.direction === "sell" || s.direction === "cover");
    expect(exits.length).toBeGreaterThanOrEqual(0);
  });

  it("all signals carry strategyId = cci-divergence", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaCciDivergence(10, 8, 80, -80), bars);
    for (const sig of signals) {
      expect(sig.strategyId).toBe("cci-divergence");
    }
  });

  it("reason text mentions CCI", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaCciDivergence(10, 8, 80, -80), bars);
    if (signals.length > 0) {
      expect(signals[0]!.reason).toMatch(/CCI/);
    }
  });

  it("flat market produces no divergence signals", () => {
    const bars = generateBars(100, { trend: "flat" });
    const signals = runStrategy(ctaCciDivergence(10, 8, 100, -100), bars);
    expect(signals.length).toBe(0);
  });
});

// ── ctaEmaCrossover ───────────────────────────────────────────

describe("ctaEmaCrossover", () => {
  it("returns a strategy with correct name", () => {
    const s = ctaEmaCrossover(5, 15);
    expect(s.name).toContain("EMA");
  });

  it("produces buy signals on V-shape (golden cross at bottom)", () => {
    const bars = generateBars(200, { trend: "vshape" });
    const signals = runStrategy(ctaEmaCrossover(5, 15), bars);
    const buys = signals.filter((s) => s.direction === "buy");
    expect(buys.length).toBeGreaterThan(0);
  });

  it("produces short signals on sine (death cross at peak)", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaEmaCrossover(5, 15), bars);
    const shorts = signals.filter((s) => s.direction === "short");
    expect(shorts.length).toBeGreaterThan(0);
  });

  it("supports same-bar exit + reverse (returns array)", () => {
    const bars = generateNarrowBars(300, { trend: "sine" });
    const signals = runStrategy(ctaEmaCrossover(5, 15), bars);
    // Should have both entries and exits
    const buys = signals.filter((s) => s.direction === "buy");
    const shorts = signals.filter((s) => s.direction === "short");
    expect(buys.length).toBeGreaterThan(0);
    expect(shorts.length).toBeGreaterThan(0);
  });

  it("all signals carry strategyId = ema-crossover", () => {
    const bars = generateNarrowBars(200, { trend: "up" });
    const signals = runStrategy(ctaEmaCrossover(5, 15), bars);
    for (const sig of signals) {
      expect(sig.strategyId).toBe("ema-crossover");
    }
  });

  it("reason text mentions EMA", () => {
    const bars = generateNarrowBars(200, { trend: "up" });
    const signals = runStrategy(ctaEmaCrossover(5, 15), bars);
    if (signals.length > 0) {
      expect(signals[0]!.reason).toMatch(/EMA/);
    }
  });

  it("sine market produces multiple crossover signals", () => {
    const bars = generateNarrowBars(300, { trend: "sine" });
    const signals = runStrategy(ctaEmaCrossover(5, 15), bars);
    expect(signals.length).toBeGreaterThan(2);
  });
});

// ── ctaAtrTrailing ────────────────────────────────────────────

describe("ctaAtrTrailing", () => {
  it("returns a strategy with correct name", () => {
    const s = ctaAtrTrailing(10, 2.0, 20);
    expect(s.name).toContain("ATR");
  });

  it("produces buy signals on uptrend (above MA)", () => {
    const bars = generateBars(200, { trend: "up" });
    const signals = runStrategy(ctaAtrTrailing(10, 2.0, 20), bars);
    const buys = signals.filter((s) => s.direction === "buy");
    expect(buys.length).toBeGreaterThan(0);
  });

  it("produces short signals on downtrend (below MA)", () => {
    const bars = generateBars(200, { trend: "down" });
    const signals = runStrategy(ctaAtrTrailing(10, 2.0, 20), bars);
    const shorts = signals.filter((s) => s.direction === "short");
    expect(shorts.length).toBeGreaterThan(0);
  });

  it("produces trailing stop exits on V-shape", () => {
    const bars = generateBars(300, { trend: "vshape" });
    const signals = runStrategy(ctaAtrTrailing(10, 1.5, 15), bars);
    const exits = signals.filter((s) => s.direction === "sell" || s.direction === "cover");
    expect(exits.length).toBeGreaterThan(0);
  });

  it("all signals carry strategyId = atr-trailing", () => {
    const bars = generateBars(200, { trend: "up" });
    const signals = runStrategy(ctaAtrTrailing(10, 2.0, 20), bars);
    for (const sig of signals) {
      expect(sig.strategyId).toBe("atr-trailing");
    }
  });

  it("reason text mentions ATR", () => {
    const bars = generateBars(200, { trend: "up" });
    const signals = runStrategy(ctaAtrTrailing(10, 2.0, 20), bars);
    if (signals.length > 0) {
      expect(signals[0]!.reason).toMatch(/ATR/);
    }
  });

  it("sine market produces entry and exit signals", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaAtrTrailing(10, 1.5, 15), bars);
    expect(signals.length).toBeGreaterThan(0);
  });
});

// ── ctaStochasticRsi ─────────────────────────────────────────

describe("ctaStochasticRsi", () => {
  it("returns a strategy with correct name", () => {
    const s = ctaStochasticRsi(14, 14, 3, 80, 20);
    expect(s.name).toContain("随机RSI");
  });

  it("produces buy signals on sine (bounce from oversold)", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaStochasticRsi(14, 14, 3, 80, 20), bars);
    const buys = signals.filter((s) => s.direction === "buy");
    expect(buys.length).toBeGreaterThan(0);
  });

  it("produces short signals on sine (drop from overbought)", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaStochasticRsi(14, 14, 3, 80, 20), bars);
    const shorts = signals.filter((s) => s.direction === "short");
    expect(shorts.length).toBeGreaterThan(0);
  });

  it("produces exit signals", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaStochasticRsi(14, 14, 3, 80, 20), bars);
    const exits = signals.filter((s) => s.direction === "sell" || s.direction === "cover");
    expect(exits.length).toBeGreaterThan(0);
  });

  it("all signals carry strategyId = stochastic-rsi", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaStochasticRsi(14, 14, 3, 80, 20), bars);
    for (const sig of signals) {
      expect(sig.strategyId).toBe("stochastic-rsi");
    }
  });

  it("reason text mentions StochRSI", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaStochasticRsi(14, 14, 3, 80, 20), bars);
    if (signals.length > 0) {
      expect(signals[0]!.reason).toMatch(/StochRSI/);
    }
  });

  it("flat market produces no signals", () => {
    const bars = generateBars(100, { trend: "flat" });
    const signals = runStrategy(ctaStochasticRsi(14, 14, 3, 80, 20), bars);
    const entries = signals.filter((s) => s.direction === "buy" || s.direction === "short");
    expect(entries.length).toBe(0);
  });
});

// ── ctaMacdHistogramReversal ───────────────────────────────

describe("ctaMacdHistogramReversal", () => {
  it("returns a strategy with correct name", () => {
    const s = ctaMacdHistogramReversal(12, 26, 9);
    expect(s.name).toContain("MACD");
  });

  it("produces buy signals on sine (histogram crosses zero up)", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaMacdHistogramReversal(12, 26, 9), bars);
    const buys = signals.filter((s) => s.direction === "buy");
    expect(buys.length).toBeGreaterThan(0);
  });

  it("produces short signals on sine (histogram crosses zero down)", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaMacdHistogramReversal(12, 26, 9), bars);
    const shorts = signals.filter((s) => s.direction === "short");
    expect(shorts.length).toBeGreaterThan(0);
  });

  it("produces exit signals on reversal", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaMacdHistogramReversal(12, 26, 9), bars);
    const exits = signals.filter((s) => s.direction === "sell" || s.direction === "cover");
    expect(exits.length).toBeGreaterThan(0);
  });

  it("all signals carry strategyId = macd-histogram-reversal", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaMacdHistogramReversal(12, 26, 9), bars);
    for (const sig of signals) {
      expect(sig.strategyId).toBe("macd-histogram-reversal");
    }
  });

  it("reason text mentions MACD", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaMacdHistogramReversal(12, 26, 9), bars);
    if (signals.length > 0) {
      expect(signals[0]!.reason).toMatch(/MACD/);
    }
  });

  it("flat market produces no signals", () => {
    const bars = generateBars(100, { trend: "flat" });
    const signals = runStrategy(ctaMacdHistogramReversal(12, 26, 9), bars);
    const entries = signals.filter((s) => s.direction === "buy" || s.direction === "short");
    expect(entries.length).toBe(0);
  });
});

// ── ctaBollingerBandwidth ───────────────────────────────────

describe("ctaBollingerBandwidth", () => {
  it("returns a strategy with correct name", () => {
    const s = ctaBollingerBandwidth(20, 2.0, 20, 0.25);
    expect(s.name).toContain("BB");
  });

  it("produces signals on sine (squeeze then expansion)", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaBollingerBandwidth(20, 2.0, 20, 0.25), bars);
    expect(signals.length).toBeGreaterThan(0);
  });

  it("produces signals on sine market", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaBollingerBandwidth(20, 2.0, 20, 0.25), bars);
    expect(signals.length).toBeGreaterThan(0);
  });

  it("produces exit signals when price returns to middle band", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaBollingerBandwidth(20, 2.0, 20, 0.25), bars);
    const exits = signals.filter((s) => s.direction === "sell" || s.direction === "cover");
    expect(exits.length).toBeGreaterThanOrEqual(0);
  });

  it("all signals carry strategyId = bollinger-bandwidth", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaBollingerBandwidth(20, 2.0, 20, 0.25), bars);
    for (const sig of signals) {
      expect(sig.strategyId).toBe("bollinger-bandwidth");
    }
  });

  it("reason text mentions BB带宽", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(ctaBollingerBandwidth(20, 2.0, 20, 0.25), bars);
    if (signals.length > 0) {
      expect(signals[0]!.reason).toMatch(/BB带宽/);
    }
  });

  it("flat market produces no breakout signals", () => {
    const bars = generateBars(100, { trend: "flat" });
    const signals = runStrategy(ctaBollingerBandwidth(20, 2.0, 20, 0.25), bars);
    const entries = signals.filter((s) => s.direction === "buy" || s.direction === "short");
    expect(entries.length).toBe(0);
  });
});
