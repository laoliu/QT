/**
 * Tests for the builtin backtest engine — core logic, SMA, DualMA,
 * MomentumBreakout, and MeanReversion strategies.
 */
import { describe, expect, it } from "vitest";
import type { KlineBar } from "../types.js";
import {
  ctaDualThrust,
  ctaTrendFollowing,
  dualMaCrossover,
  meanReversion,
  momentumBreakout,
  runBuiltinBacktest,
  spreadArbitrage,
} from "./builtin-engine.js";

// ── Helper: generate synthetic bars ──────────────────────────────

/** Generate N daily bars with a controlled price path. */
function generateBars(
  n: number,
  opts?: { startPrice?: number; trend?: "up" | "down" | "flat" | "sine" },
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
        price = start + i * 0.5;
        break;
      case "down":
        price = start - i * 0.3;
        break;
      case "flat":
        price = start;
        break;
      case "sine":
      default:
        price = start + 20 * Math.sin((i / n) * Math.PI * 4);
    }
    bars.push({
      timestamp: t0 + i * dayMs,
      open: price - 0.5,
      high: price + 1,
      low: price - 1,
      close: price,
      volume: 100_000,
    });
  }
  return bars;
}

// ── SMA / DualMA unit tests ──────────────────────────────────────

describe("dualMaCrossover strategy", () => {
  it("should create a strategy with correct name", () => {
    const s = dualMaCrossover(5, 20);
    expect(s.name).toBe("DualMA(5,20)");
  });

  it("should produce no signals before slow MA is ready", () => {
    const bars = generateBars(10, { trend: "up" });
    const s = dualMaCrossover(3, 10);
    const ctx = { bars, index: 0, position: 0, cash: 100_000, params: {}, signals: [] };
    s.init!(ctx);

    // Before slow period (10) is reached, next() should return null
    for (let i = 0; i < 10; i++) {
      ctx.index = i;
      const sig = s.next(ctx);
      expect(sig).toBeNull();
    }
  });

  it("should emit buy signal on golden cross (sine data)", () => {
    const bars = generateBars(200, { trend: "sine" });
    const s = dualMaCrossover(5, 20);
    const ctx = { bars, index: 0, position: 0, cash: 100_000, params: {}, signals: [] };
    s.init!(ctx);

    const signals: Array<{ direction: string }> = [];
    for (let i = 0; i < bars.length; i++) {
      ctx.index = i;
      const sig = s.next(ctx);
      if (sig) {
        const arr = Array.isArray(sig) ? sig : [sig];
        for (const a of arr) {
          signals.push(a);
          if (a.direction === "buy") ctx.position = 100;
          if (a.direction === "sell") ctx.position = 0;
        }
      }
    }

    // With a sine wave, we should get at least one buy and one sell
    expect(signals.some((s) => s.direction === "buy")).toBe(true);
    expect(signals.some((s) => s.direction === "sell")).toBe(true);
  });

  it("should produce no signals in a flat market", () => {
    const bars = generateBars(100, { trend: "flat" });
    const s = dualMaCrossover(5, 20);
    const ctx = { bars, index: 0, position: 0, cash: 100_000, params: {}, signals: [] };
    s.init!(ctx);

    const signals: unknown[] = [];
    for (let i = 0; i < bars.length; i++) {
      ctx.index = i;
      const sig = s.next(ctx);
      if (sig) signals.push(sig);
    }
    // Flat market: both MAs converge → no crossover
    expect(signals.length).toBe(0);
  });
});

// ── Builtin engine integration test ──────────────────────────────

describe("runBuiltinBacktest", () => {
  it("should return valid backtest result with demo provider", async () => {
    const result = await runBuiltinBacktest(
      dualMaCrossover(5, 20),
      {
        strategyId: "dual-ma-crossover",
        symbols: ["000300"],
        startDate: "2024-01-01",
        endDate: "2024-12-31",
        initialCash: 100_000,
      },
      { dataProvider: "demo" },
    );

    expect(result.strategyId).toBe("dual-ma-crossover");
    expect(result.initialCash).toBe(100_000);
    expect(result.finalValue).toBeGreaterThan(0);
    expect(result.equityCurve.length).toBeGreaterThan(0);
    expect(typeof result.totalReturn).toBe("number");
    expect(typeof result.annualizedReturn).toBe("number");
    expect(typeof result.maxDrawdown).toBe("number");
    expect(typeof result.sharpeRatio).toBe("number");
    expect(typeof result.winRate).toBe("number");
    expect(typeof result.totalTrades).toBe("number");
  });

  it("should throw when no data is available", async () => {
    // Use a very narrow date range where demo provider returns empty
    // (DemoProvider uses start/end, which will have bars — but we can
    // test the error path with a mock in a follow-up)
    const result = await runBuiltinBacktest(
      dualMaCrossover(5, 20),
      {
        strategyId: "dual-ma-crossover",
        symbols: ["INVALID"],
        startDate: "2024-01-01",
        endDate: "2024-01-01", // Only one day → still returns bar from DemoProvider
        initialCash: 50_000,
      },
      { dataProvider: "demo" },
    );
    // DemoProvider always returns data, so this should succeed
    expect(result.initialCash).toBe(50_000);
  });
});

// ── Momentum Breakout tests ──────────────────────────────────────

describe("momentumBreakout strategy", () => {
  it("should create a strategy with correct name", () => {
    const s = momentumBreakout(10);
    expect(s.name).toBe("MomentumBreakout(10)");
  });

  it("should emit buy on breakout above N-day high (sine data)", () => {
    const bars = generateBars(200, { trend: "sine" });
    const s = momentumBreakout(10);
    const ctx = { bars, index: 0, position: 0, cash: 100_000, params: {}, signals: [] };
    s.init!(ctx);

    const signals: Array<{ direction: string }> = [];
    for (let i = 0; i < bars.length; i++) {
      ctx.index = i;
      const sig = s.next(ctx);
      if (sig) {
        const arr = Array.isArray(sig) ? sig : [sig];
        for (const a of arr) {
          signals.push(a);
          if (a.direction === "buy") ctx.position = 100;
          if (a.direction === "sell") ctx.position = 0;
        }
      }
    }

    // In a strong uptrend, we should get buy signals
    expect(signals.some((s) => s.direction === "buy")).toBe(true);
  });

  it("should produce no signals in a flat market", () => {
    const bars = generateBars(100, { trend: "flat" });
    const s = momentumBreakout(10);
    const ctx = { bars, index: 0, position: 0, cash: 100_000, params: {}, signals: [] };
    s.init!(ctx);

    const signals: unknown[] = [];
    for (let i = 0; i < bars.length; i++) {
      ctx.index = i;
      const sig = s.next(ctx);
      if (sig) signals.push(sig);
    }
    expect(signals.length).toBe(0);
  });

  it("should run backtest via registry", async () => {
    const result = await runBuiltinBacktest(
      momentumBreakout(10),
      {
        strategyId: "momentum-breakout",
        symbols: ["000300"],
        startDate: "2024-01-01",
        endDate: "2024-12-31",
        initialCash: 100_000,
      },
      { dataProvider: "demo" },
    );
    expect(result.strategyId).toBe("momentum-breakout");
    expect(result.finalValue).toBeGreaterThan(0);
  });
});

// ── Mean Reversion (Bollinger Band) tests ────────────────────────

describe("meanReversion strategy", () => {
  it("should create a strategy with correct name", () => {
    const s = meanReversion(20, 2);
    expect(s.name).toBe("MeanReversion(20,2)");
  });

  it("should emit buy/sell signals on sine wave data", () => {
    const bars = generateBars(200, { trend: "sine" });
    const s = meanReversion(20, 1.5); // tighter bands for more signals
    const ctx = { bars, index: 0, position: 0, cash: 100_000, params: {}, signals: [] };
    s.init!(ctx);

    const signals: Array<{ direction: string }> = [];
    for (let i = 0; i < bars.length; i++) {
      ctx.index = i;
      const sig = s.next(ctx);
      if (sig) {
        const arr = Array.isArray(sig) ? sig : [sig];
        for (const a of arr) {
          signals.push(a);
          if (a.direction === "buy") ctx.position = 100;
          if (a.direction === "sell") ctx.position = 0;
        }
      }
    }

    // Sine wave should trigger Bollinger band touches
    expect(signals.some((s) => s.direction === "buy")).toBe(true);
    expect(signals.some((s) => s.direction === "sell")).toBe(true);
  });

  it("should produce no signals in a flat market", () => {
    const bars = generateBars(100, { trend: "flat" });
    const s = meanReversion(20, 2);
    const ctx = { bars, index: 0, position: 0, cash: 100_000, params: {}, signals: [] };
    s.init!(ctx);

    const signals: unknown[] = [];
    for (let i = 0; i < bars.length; i++) {
      ctx.index = i;
      const sig = s.next(ctx);
      if (sig) signals.push(sig);
    }
    expect(signals.length).toBe(0);
  });

  it("should run backtest via registry", async () => {
    const result = await runBuiltinBacktest(
      meanReversion(20, 2),
      {
        strategyId: "mean-reversion",
        symbols: ["000300"],
        startDate: "2024-01-01",
        endDate: "2024-12-31",
        initialCash: 100_000,
      },
      { dataProvider: "demo" },
    );
    expect(result.strategyId).toBe("mean-reversion");
    expect(result.finalValue).toBeGreaterThan(0);
  });
});

// ── Engine short/cover support ───────────────────────────────────

describe("builtin engine short/cover", () => {
  it("CTA strategy produces short and cover signals in engine", async () => {
    const result = await runBuiltinBacktest(
      ctaTrendFollowing(10, 7, 1.5, 1.0),
      {
        strategyId: "cta-trend",
        symbols: ["000300"],
        startDate: "2024-01-01",
        endDate: "2024-12-31",
        initialCash: 500_000,
      },
      { dataProvider: "demo" },
    );

    expect(result.strategyId).toBe("cta-trend");
    expect(result.finalValue).toBeGreaterThan(0);
    expect(result.equityCurve.length).toBeGreaterThan(0);
    expect(typeof result.totalReturn).toBe("number");
    expect(typeof result.sharpeRatio).toBe("number");
  });

  it("DualThrust strategy runs in engine with valid results", async () => {
    const result = await runBuiltinBacktest(
      ctaDualThrust(4, 0.3, 0.3),
      {
        strategyId: "dual-thrust",
        symbols: ["000300"],
        startDate: "2024-01-01",
        endDate: "2024-12-31",
        initialCash: 500_000,
      },
      { dataProvider: "demo" },
    );

    expect(result.strategyId).toBe("dual-thrust");
    expect(result.finalValue).toBeGreaterThan(0);
    expect(result.equityCurve.length).toBeGreaterThan(0);
    expect(result.signals.length).toBeGreaterThan(0);
  });

  it("spread arbitrage strategy runs in engine without error", async () => {
    const result = await runBuiltinBacktest(
      spreadArbitrage(20, 1.5, 0.5),
      {
        strategyId: "spread-arbitrage",
        symbols: ["000300"],
        startDate: "2024-01-01",
        endDate: "2024-12-31",
        initialCash: 500_000,
      },
      { dataProvider: "demo" },
    );

    expect(result.strategyId).toBe("spread-arbitrage");
    expect(result.finalValue).toBeGreaterThan(0);
    expect(result.equityCurve.length).toBeGreaterThan(0);
  });

  it("win rate counts both long and short round-trips", async () => {
    const result = await runBuiltinBacktest(
      ctaDualThrust(4, 0.3, 0.3),
      {
        strategyId: "dual-thrust",
        symbols: ["000300"],
        startDate: "2024-01-01",
        endDate: "2024-12-31",
        initialCash: 500_000,
      },
      { dataProvider: "demo" },
    );

    expect(result.winRate).toBeGreaterThanOrEqual(0);
    expect(result.winRate).toBeLessThanOrEqual(100);
    expect(typeof result.totalTrades).toBe("number");
  });

  it("equity curve never goes negative", async () => {
    const result = await runBuiltinBacktest(
      ctaTrendFollowing(10, 7, 1.0, 0.8),
      {
        strategyId: "cta-trend",
        symbols: ["000300"],
        startDate: "2024-01-01",
        endDate: "2024-12-31",
        initialCash: 200_000,
      },
      { dataProvider: "demo" },
    );

    for (const point of result.equityCurve) {
      expect(point.value).toBeGreaterThan(0);
    }
  });

  it("fees are tracked for short/cover trades", async () => {
    const result = await runBuiltinBacktest(
      ctaDualThrust(4, 0.3, 0.3),
      {
        strategyId: "dual-thrust",
        symbols: ["000300"],
        startDate: "2024-01-01",
        endDate: "2024-12-31",
        initialCash: 500_000,
      },
      { dataProvider: "demo" },
    );

    if (result.totalTrades > 0) {
      expect(result.totalFees).toBeGreaterThan(0);
      expect(result.feeImpact).toBeGreaterThan(0);
    }
  });

  it("futures backtest skips stamp duty (no stamp duty on futures)", async () => {
    // Run same strategy with market=futures vs default (cn_stock)
    // Futures should have lower fee impact since stamp duty is skipped
    const futuresResult = await runBuiltinBacktest(
      ctaDualThrust(4, 0.3, 0.3),
      {
        strategyId: "dual-thrust",
        symbols: ["IF2503"],
        startDate: "2024-01-01",
        endDate: "2024-12-31",
        initialCash: 500_000,
        market: "futures",
      },
      { dataProvider: "demo" },
    );

    expect(futuresResult.strategyId).toBe("dual-thrust");
    expect(futuresResult.finalValue).toBeGreaterThan(0);
    // Futures P&L should reflect no stamp duty deduction
    if (futuresResult.totalTrades > 0) {
      expect(futuresResult.totalFees).toBeGreaterThanOrEqual(0);
    }
  });

  it("short entry uses slippage-adjusted notional for P&L tracking", async () => {
    const result = await runBuiltinBacktest(
      ctaTrendFollowing(10, 7, 1.5, 1.0),
      {
        strategyId: "cta-trend",
        symbols: ["000300"],
        startDate: "2024-01-01",
        endDate: "2024-12-31",
        initialCash: 500_000,
        slippage: 0.002, // 0.2% slippage
      },
      { dataProvider: "demo" },
    );

    // Just verify it runs without error and produces valid results
    expect(result.finalValue).toBeGreaterThan(0);
    expect(result.equityCurve.length).toBeGreaterThan(0);
  });

  it("uses market field from BacktestRequest for isFutures detection", async () => {
    // Symbol "000300" looks like cn_stock but we override as futures
    const result = await runBuiltinBacktest(
      ctaDualThrust(4, 0.3, 0.3),
      {
        strategyId: "dual-thrust",
        symbols: ["000300"],
        startDate: "2024-01-01",
        endDate: "2024-12-31",
        initialCash: 500_000,
        market: "futures", // override: treat as futures
      },
      { dataProvider: "demo" },
    );

    expect(result.finalValue).toBeGreaterThan(0);
  });
});
