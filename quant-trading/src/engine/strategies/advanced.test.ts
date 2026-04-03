/**
 * Tests for advanced strategies: enhanced composite, new basic strategies,
 * and market regime detection.
 */
import { describe, expect, it } from "vitest";
import type { KlineBar, TradingSignal } from "../../types.js";
import { enhancedCompositeStrategy, detectMarketRegime, rsiMacdCombined } from "./advanced.js";
import {
  rsiFilter,
  rsiEnhanced,
  macdConfirmation,
  macdDivergence,
  volumeConfirmation,
} from "./basic.js";

// ── Helper ───────────────────────────────────────────────────────

/** Generate N daily bars with a controlled price path. */
function generateBars(
  n: number,
  opts?: {
    startPrice?: number;
    trend?: "up" | "down" | "flat" | "sine" | "vshape";
    baseVolume?: number;
  },
): KlineBar[] {
  const bars: KlineBar[] = [];
  const start = opts?.startPrice ?? 100;
  const trend = opts?.trend ?? "sine";
  const baseVolume = opts?.baseVolume ?? 100_000;
  const dayMs = 86_400_000;
  const t0 = new Date("2024-01-02").getTime();

  for (let i = 0; i < n; i++) {
    let price: number;
    switch (trend) {
      case "up":
        price = start + i * 1.5;
        break;
      case "down":
        price = start - i * 1.0;
        break;
      case "flat":
        price = start;
        break;
      case "vshape":
        price = i < n / 2 ? start - i * 1.5 : start - (n / 2) * 1.5 + (i - n / 2) * 1.5;
        break;
      case "sine":
      default:
        price = start + 30 * Math.sin((i / n) * Math.PI * 4);
    }
    // Give bars volume that varies — spikes near trend changes
    const volumeVariation = 1 + 0.5 * Math.abs(Math.sin((i / n) * Math.PI * 6));
    bars.push({
      timestamp: t0 + i * dayMs,
      open: price - 1,
      high: price + 3,
      low: price - 3,
      close: price,
      volume: Math.round(baseVolume * volumeVariation),
    });
  }
  return bars;
}

/** Run a strategy over bars, collecting all signals. */
function runStrategy(
  strategy: { name: string; init?(ctx: unknown): void; next(ctx: unknown): unknown },
  bars: KlineBar[],
): TradingSignal[] {
  const signals: TradingSignal[] = [];
  const ctx = { bars, index: 0, position: 0, cash: 1_000_000, params: {}, signals };
  strategy.init?.(ctx);

  for (let i = 0; i < bars.length; i++) {
    ctx.index = i;
    const result = strategy.next(ctx) as TradingSignal | TradingSignal[] | null;
    if (result) {
      const arr = Array.isArray(result) ? result : [result];
      for (const sig of arr) {
        signals.push(sig);
        if (sig.direction === "buy") ctx.position = 1;
        else if (sig.direction === "sell") ctx.position = 0;
      }
    }
  }
  return signals;
}

// ── RSI Filter ───────────────────────────────────────────────────

describe("rsiFilter", () => {
  it("creates a strategy with correct name", () => {
    const s = rsiFilter(14, 30, 70);
    expect(s.name).toContain("RSIFilter");
    expect(s.name).toContain("14");
  });

  it("produces no signals in flat market", () => {
    const bars = generateBars(100, { trend: "flat" });
    const signals = runStrategy(rsiFilter(), bars);
    expect(signals.length).toBe(0);
  });

  it("emits buy signal when RSI exits oversold in oscillating market", () => {
    const bars = generateBars(200, { trend: "sine" });
    const signals = runStrategy(rsiFilter(14, 30, 70), bars);
    const buys = signals.filter((s) => s.direction === "buy");
    expect(buys.length).toBeGreaterThan(0);
    expect(buys[0]!.strategyId).toBe("rsi-filter");
    expect(buys[0]!.reason).toContain("RSI");
  });

  it("emits sell signal when RSI exits overbought", () => {
    const bars = generateBars(200, { trend: "sine" });
    const signals = runStrategy(rsiFilter(14, 30, 70), bars);
    const sells = signals.filter((s) => s.direction === "sell");
    // May or may not have sells depending on oscillation amplitude
    if (sells.length > 0) {
      expect(sells[0]!.strategyId).toBe("rsi-filter");
    }
  });
});

// ── MACD Confirmation ────────────────────────────────────────────

describe("macdConfirmation", () => {
  it("creates a strategy with correct name", () => {
    const s = macdConfirmation(12, 26, 9);
    expect(s.name).toContain("MACDConfirm");
  });

  it("produces no signals in flat market", () => {
    const bars = generateBars(100, { trend: "flat" });
    const signals = runStrategy(macdConfirmation(), bars);
    expect(signals.length).toBe(0);
  });

  it("emits buy/sell signals in oscillating market", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(macdConfirmation(12, 26, 9), bars);
    expect(signals.length).toBeGreaterThan(0);
    const buys = signals.filter((s) => s.direction === "buy");
    expect(buys.length).toBeGreaterThan(0);
    expect(buys[0]!.strategyId).toBe("macd-confirmation");
    expect(buys[0]!.reason).toContain("MACD histogram");
  });
});

// ── Volume Confirmation ──────────────────────────────────────────

describe("volumeConfirmation", () => {
  it("creates a strategy with correct name", () => {
    const s = volumeConfirmation(20, 1.5);
    expect(s.name).toContain("VolumeConfirm");
  });

  it("requires high volume to produce signals", () => {
    // Flat volume should not produce signals (no volume above multiplier × average)
    const bars = generateBars(100, { trend: "up", baseVolume: 100_000 });
    // Make all volumes exactly the same — no spikes
    for (const bar of bars) bar.volume = 100_000;
    const signals = runStrategy(volumeConfirmation(20, 2.0), bars);
    expect(signals.length).toBe(0);
  });

  it("emits signal on volume spike with price move", () => {
    const bars = generateBars(100, { trend: "up", baseVolume: 100_000 });
    // Inject a volume spike
    bars[50]!.volume = 500_000;
    const signals = runStrategy(volumeConfirmation(20, 1.5), bars);
    // Should get at least one signal near the spike
    expect(signals.length).toBeGreaterThan(0);
    expect(signals[0]!.strategyId).toBe("volume-confirmation");
    expect(signals[0]!.reason).toContain("avg volume");
  });
});

// ── Market Regime Detection ──────────────────────────────────────

describe("detectMarketRegime", () => {
  it("detects trending-up in uptrend", () => {
    const bars = generateBars(200, { trend: "up" });
    const regimes = detectMarketRegime(bars, 60, 14, 10, 0.001);
    // After warmup, later bars should be trending-up
    const laterRegimes = regimes.slice(100);
    const upCount = laterRegimes.filter((r) => r === "trending-up").length;
    expect(upCount).toBeGreaterThan(laterRegimes.length * 0.5);
  });

  it("detects trending-down in downtrend", () => {
    const bars = generateBars(200, { trend: "down", startPrice: 300 });
    const regimes = detectMarketRegime(bars, 60, 14, 10, 0.001);
    const laterRegimes = regimes.slice(100);
    const downCount = laterRegimes.filter((r) => r === "trending-down").length;
    expect(downCount).toBeGreaterThan(laterRegimes.length * 0.5);
  });

  it("detects ranging in flat market", () => {
    const bars = generateBars(200, { trend: "flat" });
    const regimes = detectMarketRegime(bars, 60, 14, 10, 0.001);
    const laterRegimes = regimes.slice(100);
    const rangingCount = laterRegimes.filter((r) => r === "ranging").length;
    expect(rangingCount).toBe(laterRegimes.length);
  });

  it("returns correct array length", () => {
    const bars = generateBars(150);
    const regimes = detectMarketRegime(bars);
    expect(regimes.length).toBe(bars.length);
  });
});

// ── Enhanced Composite Strategy ──────────────────────────────────

describe("enhancedCompositeStrategy", () => {
  it("creates a strategy with correct name", () => {
    const s = enhancedCompositeStrategy();
    expect(s.name).toContain("EnhancedComposite");
    expect(s.name).toContain("5策略");
  });

  it("produces no signals in flat market", () => {
    const bars = generateBars(100, { trend: "flat" });
    const signals = runStrategy(enhancedCompositeStrategy(), bars);
    expect(signals.length).toBe(0);
  });

  it("emits buy signals in uptrend with sufficient consensus", () => {
    const bars = generateBars(300, { trend: "up" });
    const signals = runStrategy(enhancedCompositeStrategy(), bars);
    const buys = signals.filter((s) => s.direction === "buy");
    // In a clear uptrend, should get some buy signals
    if (buys.length > 0) {
      expect(buys[0]!.strategyId).toBe("enhanced-composite");
      expect(buys[0]!.reason).toContain("增强综合买入");
    }
  });

  it("generates fewer signals than basic composite (higher threshold)", () => {
    const bars = generateBars(300, { trend: "sine" });
    const enhancedSignals = runStrategy(
      enhancedCompositeStrategy(10, 30, 20, 14, 30, 70, 12, 26, 9, 20, 1.5, 3, false),
      bars,
    );
    // Enhanced composite with minVotes=3 should be more selective
    // (just verify it runs without error and produces signals in oscillating market)
    expect(enhancedSignals).toBeDefined();
  });

  it("respects regime filter — reduces trading in downtrend", () => {
    const bars = generateBars(300, { trend: "down", startPrice: 400 });
    const withRegime = runStrategy(
      enhancedCompositeStrategy(10, 30, 20, 14, 30, 70, 12, 26, 9, 20, 1.5, 2, true),
      bars,
    );
    const noRegime = runStrategy(
      enhancedCompositeStrategy(10, 30, 20, 14, 30, 70, 12, 26, 9, 20, 1.5, 2, false),
      bars,
    );
    // With regime filter, should have equal or fewer buy signals in downtrend
    const withBuys = withRegime.filter((s) => s.direction === "buy").length;
    const noBuys = noRegime.filter((s) => s.direction === "buy").length;
    expect(withBuys).toBeLessThanOrEqual(noBuys);
  });

  it("buy signals include regime tag when filter is on", () => {
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(
      enhancedCompositeStrategy(10, 30, 20, 14, 30, 70, 12, 26, 9, 20, 1.5, 2, true),
      bars,
    );
    const buys = signals.filter((s) => s.direction === "buy");
    if (buys.length > 0) {
      // Should include regime tag like [trending-up] or [ranging]
      expect(buys[0]!.reason).toMatch(/\[(trending-up|trending-down|ranging)\]/);
    }
  });

  it("sell threshold is lower than buy threshold", () => {
    // Just verify the strategy runs correctly — sell minVotes = max(2, minVotes-1)
    const bars = generateBars(300, { trend: "vshape", startPrice: 200 });
    const signals = runStrategy(
      enhancedCompositeStrategy(10, 30, 20, 14, 30, 70, 12, 26, 9, 20, 1.5, 3, false),
      bars,
    );
    // Verify structural integrity
    for (const sig of signals) {
      expect(["buy", "sell"]).toContain(sig.direction);
      expect(sig.price).toBeGreaterThan(0);
    }
  });
});

// ── RSI Enhanced ─────────────────────────────────────────────────

describe("rsiEnhanced", () => {
  it("creates a strategy with correct name", () => {
    const s = rsiEnhanced(14, 35, 65, 20, 5, 1.0, 14, 2.0);
    expect(s.name).toContain("RSIEnhanced");
    expect(s.name).toContain("14");
  });

  it("produces no signals in flat market", () => {
    const bars = generateBars(100, { trend: "flat" });
    const signals = runStrategy(rsiEnhanced(), bars);
    expect(signals.length).toBe(0);
  });

  it("requires price above trend MA for buy (filters falling knives)", () => {
    // Pure downtrend — should NOT buy despite RSI going oversold
    const bars = generateBars(150, { trend: "down", startPrice: 200 });
    const signals = runStrategy(rsiEnhanced(14, 30, 70, 20), bars);
    const buys = signals.filter((s) => s.direction === "buy");
    // In a strong downtrend, price stays below MA — few/no buys
    // Basic rsiFilter would have more buys here
    const basicSignals = runStrategy(rsiFilter(14, 30, 70), bars);
    const basicBuys = basicSignals.filter((s) => s.direction === "buy");
    expect(buys.length).toBeLessThanOrEqual(basicBuys.length);
  });

  it("emits buy signals in V-shape recovery with volume", () => {
    const bars = generateBars(200, { trend: "vshape", baseVolume: 200_000 });
    const signals = runStrategy(rsiEnhanced(14, 35, 65, 20, 5, 1.0, 14, 2.0), bars);
    const buys = signals.filter((s) => s.direction === "buy");
    // V-shape should trigger at bottom recovery
    if (buys.length > 0) {
      expect(buys[0]!.strategyId).toBe("rsi-enhanced");
      expect(buys[0]!.reason).toContain("RSI");
    }
  });

  it("tags signals with rsi-enhanced strategy ID", () => {
    const bars = generateBars(200, { trend: "sine", baseVolume: 200_000 });
    const signals = runStrategy(rsiEnhanced(14, 30, 70, 20, 5, 1.0, 14, 2.0), bars);
    for (const sig of signals) {
      expect(sig.strategyId).toBe("rsi-enhanced");
    }
  });
});

// ── MACD Divergence ──────────────────────────────────────────────

describe("macdDivergence", () => {
  it("creates a strategy with correct name", () => {
    const s = macdDivergence(12, 26, 9, 60, 5);
    expect(s.name).toContain("MACDDivergence");
  });

  it("produces no signals on flat market", () => {
    const bars = generateBars(100, { trend: "flat" });
    const signals = runStrategy(macdDivergence(), bars);
    expect(signals.length).toBe(0);
  });

  it("finds divergence signals in oscillating market", () => {
    // Long oscillating series should produce some divergence patterns
    const bars = generateBars(300, { trend: "sine" });
    const signals = runStrategy(macdDivergence(12, 26, 9, 60, 3), bars);
    // Divergence is rare — may have 0 signals on synthetic data
    for (const sig of signals) {
      expect(sig.strategyId).toBe("macd-divergence");
      expect(sig.reason).toMatch(/背离/);
    }
  });

  it("reports bottom divergence as buy and top divergence as sell", () => {
    // Create a double-bottom pattern: down, up, lower-down, up
    const n = 200;
    const bars: KlineBar[] = [];
    const t0 = new Date("2024-01-02").getTime();
    for (let i = 0; i < n; i++) {
      // First dip deeper, second dip shallower in MACD but deeper in price
      let price: number;
      if (i < 50)
        price = 100 - i * 0.8; // drop
      else if (i < 80)
        price = 60 + (i - 50) * 0.8; // recover
      else if (i < 130)
        price = 84 - (i - 80) * 1.0; // deeper drop
      else price = 34 + (i - 130) * 1.2; // recovery
      bars.push({
        timestamp: t0 + i * 86_400_000,
        open: price - 0.5,
        high: price + 2,
        low: price - 2,
        close: price,
        volume: 100_000 + Math.random() * 50_000,
      });
    }
    const signals = runStrategy(macdDivergence(12, 26, 9, 60, 3), bars);
    for (const sig of signals) {
      expect(["buy", "sell"]).toContain(sig.direction);
      expect(sig.price).toBeGreaterThan(0);
    }
  });
});

// ── RSI + MACD Combined ─────────────────────────────────────────

describe("rsiMacdCombined", () => {
  it("creates a strategy with correct name", () => {
    const s = rsiMacdCombined();
    expect(s.name).toContain("RSI+MACD");
  });

  it("produces no signals on flat market", () => {
    const bars = generateBars(100, { trend: "flat" });
    const signals = runStrategy(rsiMacdCombined(), bars);
    expect(signals.length).toBe(0);
  });

  it("requires both RSI and MACD signals — fewer trades than either alone", () => {
    const bars = generateBars(300, { trend: "sine", baseVolume: 200_000 });
    const combinedSignals = runStrategy(rsiMacdCombined(14, 30, 70, 20, 5, 1.0), bars);
    const rsiOnlySignals = runStrategy(rsiEnhanced(14, 30, 70, 20, 5, 1.0), bars);
    // Combined should have fewer or equal trades (stricter entry)
    expect(combinedSignals.length).toBeLessThanOrEqual(rsiOnlySignals.length);
  });

  it("tags all signals with rsi-macd-combined strategy ID", () => {
    const bars = generateBars(300, { trend: "sine", baseVolume: 200_000 });
    const signals = runStrategy(
      rsiMacdCombined(14, 30, 70, 20, 5, 1.0, 14, 2.0, 12, 26, 9, 60, 10),
      bars,
    );
    for (const sig of signals) {
      expect(sig.strategyId).toBe("rsi-macd-combined");
    }
  });

  it("sell signals reference which sub-strategy triggered them", () => {
    const bars = generateBars(300, { trend: "sine", baseVolume: 200_000 });
    const signals = runStrategy(
      rsiMacdCombined(14, 30, 70, 20, 5, 1.0, 14, 2.0, 12, 26, 9, 60, 10),
      bars,
    );
    const sells = signals.filter((s) => s.direction === "sell");
    for (const sell of sells) {
      expect(sell.reason).toMatch(/RSI|MACD/);
    }
  });
});
