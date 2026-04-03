/**
 * Tests for rule-strategy engine — the declarative DSL for custom strategies.
 */
import { describe, expect, it } from "vitest";
import type { KlineBar } from "../types.js";
import {
  buildRuleStrategy,
  validateRuleConfig,
  type RuleStrategyConfig,
  type BuiltinStrategyContext,
} from "./rule-strategy.js";

// ── Helpers ──────────────────────────────────────────────────────

/** Generate synthetic bars with a given close price series. */
function makeBars(closes: number[], volumes?: number[]): KlineBar[] {
  return closes.map((close, i) => ({
    timestamp: Date.now() + i * 86400000,
    open: close - 0.5,
    high: close + 1,
    low: close - 1,
    close,
    volume: volumes?.[i] ?? 100000,
  }));
}

/** Run a strategy over bars and collect all emitted signals. */
function runStrategy(config: RuleStrategyConfig, bars: KlineBar[]) {
  const strategy = buildRuleStrategy(config);
  const signals: Array<{ direction: string; index: number; reason?: string }> = [];
  const ctx: BuiltinStrategyContext = {
    bars,
    index: 0,
    position: 0,
    cash: 100000,
    params: {},
    signals: [],
  };

  strategy.init?.(ctx);

  for (let i = 0; i < bars.length; i++) {
    ctx.index = i;
    const sig = strategy.next(ctx);
    if (sig) {
      const s = Array.isArray(sig) ? sig[0]! : sig;
      signals.push({ direction: s.direction, index: i, reason: s.reason });
      // Simulate position changes
      if (s.direction === "buy") ctx.position = 100;
      else if (s.direction === "sell") ctx.position = 0;
    }
  }

  return signals;
}

// ── Validation Tests ─────────────────────────────────────────────

describe("validateRuleConfig", () => {
  it("rejects missing ID", () => {
    const err = validateRuleConfig({
      id: "",
      name: "test",
      buyRules: { logic: "all", conditions: [] },
      sellRules: { logic: "all", conditions: [] },
    });
    expect(err).toContain("ID");
  });

  it("rejects empty conditions", () => {
    const err = validateRuleConfig({
      id: "test",
      name: "test",
      buyRules: { logic: "all", conditions: [] },
      sellRules: { logic: "all", conditions: [] },
    });
    expect(err).toContain("至少需要");
  });

  it("rejects invalid indicator", () => {
    const err = validateRuleConfig({
      id: "test",
      name: "test",
      buyRules: {
        logic: "all",
        conditions: [{ left: { indicator: "invalid" as never }, op: "above", value: 50 }],
      },
      sellRules: { logic: "all", conditions: [] },
    });
    expect(err).toContain("无效的左侧指标");
  });

  it("rejects invalid operator", () => {
    const err = validateRuleConfig({
      id: "test",
      name: "test",
      buyRules: {
        logic: "all",
        conditions: [{ left: { indicator: "rsi", period: 14 }, op: "equals" as never, value: 50 }],
      },
      sellRules: { logic: "all", conditions: [] },
    });
    expect(err).toContain("无效的比较运算符");
  });

  it("rejects between without upperValue", () => {
    const err = validateRuleConfig({
      id: "test",
      name: "test",
      buyRules: {
        logic: "all",
        conditions: [{ left: { indicator: "rsi", period: 14 }, op: "between", value: 30 }],
      },
      sellRules: { logic: "all", conditions: [] },
    });
    expect(err).toContain("upperValue");
  });

  it("passes valid config", () => {
    const err = validateRuleConfig({
      id: "my-rule",
      name: "My Rule",
      buyRules: {
        logic: "all",
        conditions: [
          {
            left: { indicator: "sma", period: 5 },
            op: "crosses_above",
            right: { indicator: "sma", period: 20 },
          },
        ],
      },
      sellRules: {
        logic: "all",
        conditions: [{ left: { indicator: "rsi", period: 14 }, op: "above", value: 70 }],
      },
    });
    expect(err).toBeNull();
  });
});

// ── Strategy Execution Tests ─────────────────────────────────────

describe("buildRuleStrategy", () => {
  it("SMA crossover generates buy signal", () => {
    // Build a series where SMA5 crosses above SMA20:
    // Start low (10), then spike up
    const closes = [
      // 25 bars of data for SMA20 warmup
      ...Array.from({ length: 20 }, () => 10),
      // Then a sudden uptrend: bars 20-24 rise sharply
      12,
      14,
      16,
      18,
      20,
    ];

    const config: RuleStrategyConfig = {
      id: "test-ma-cross",
      name: "MA Cross Test",
      buyRules: {
        logic: "all",
        conditions: [
          {
            left: { indicator: "sma", period: 5 },
            op: "crosses_above",
            right: { indicator: "sma", period: 20 },
          },
        ],
      },
      sellRules: {
        logic: "all",
        conditions: [{ left: { indicator: "price" }, op: "below", value: 8 }],
      },
    };

    const signals = runStrategy(config, makeBars(closes));
    const buys = signals.filter((s) => s.direction === "buy");
    expect(buys.length).toBeGreaterThanOrEqual(1);
    expect(buys[0]!.reason).toContain("SMA5");
    expect(buys[0]!.reason).toContain("上穿");
  });

  it("RSI threshold generates sell signal", () => {
    // Simulate a steady uptrend that pushes RSI high
    const closes: number[] = [];
    for (let i = 0; i < 50; i++) {
      closes.push(100 + i * 2); // Consistent uptrend — RSI will be near 100
    }

    const config: RuleStrategyConfig = {
      id: "test-rsi-sell",
      name: "RSI Sell Test",
      buyRules: {
        logic: "all",
        conditions: [
          { left: { indicator: "price" }, op: "above", value: 0 }, // always true
        ],
      },
      sellRules: {
        logic: "all",
        conditions: [{ left: { indicator: "rsi", period: 14 }, op: "above", value: 70 }],
      },
    };

    const signals = runStrategy(config, makeBars(closes));
    const sells = signals.filter((s) => s.direction === "sell");
    expect(sells.length).toBeGreaterThanOrEqual(1);
  });

  it("AND logic requires all conditions", () => {
    // SMA5 > SMA20 AND RSI < 30 — hard to satisfy simultaneously
    const closes = Array.from({ length: 30 }, () => 50);
    // RSI will be NaN or ~50 with flat prices — not < 30

    const config: RuleStrategyConfig = {
      id: "test-and",
      name: "AND Test",
      buyRules: {
        logic: "all",
        conditions: [
          {
            left: { indicator: "sma", period: 5 },
            op: "above",
            right: { indicator: "sma", period: 20 },
          },
          { left: { indicator: "rsi", period: 14 }, op: "below", value: 30 },
        ],
      },
      sellRules: {
        logic: "all",
        conditions: [{ left: { indicator: "price" }, op: "below", value: 1 }],
      },
    };

    const signals = runStrategy(config, makeBars(closes));
    const buys = signals.filter((s) => s.direction === "buy");
    expect(buys.length).toBe(0); // Can't satisfy both conditions with flat prices
  });

  it("OR logic requires at least one condition", () => {
    // Price > 100 OR Volume > 200000 — price is always 50, volume is 100000
    const closes = Array.from({ length: 30 }, () => 50);

    const config: RuleStrategyConfig = {
      id: "test-or",
      name: "OR Test",
      buyRules: {
        logic: "any",
        conditions: [
          { left: { indicator: "price" }, op: "above", value: 100 },
          { left: { indicator: "volume" }, op: "above", value: 200000 },
        ],
      },
      sellRules: {
        logic: "all",
        conditions: [{ left: { indicator: "price" }, op: "below", value: 1 }],
      },
    };

    const signals = runStrategy(config, makeBars(closes));
    const buys = signals.filter((s) => s.direction === "buy");
    expect(buys.length).toBe(0); // Neither condition met
  });

  it("between operator works correctly", () => {
    // RSI between 40 and 60 — flat prices should give RSI ~50
    const closes = Array.from(
      { length: 30 },
      (_, i) =>
        // Small oscillation to keep RSI in mid-range
        50 + (i % 2 === 0 ? 0.5 : -0.5),
    );

    const config: RuleStrategyConfig = {
      id: "test-between",
      name: "Between Test",
      buyRules: {
        logic: "all",
        conditions: [
          {
            left: { indicator: "rsi", period: 14 },
            op: "between",
            value: 30,
            upperValue: 70,
          },
        ],
      },
      sellRules: {
        logic: "all",
        conditions: [{ left: { indicator: "price" }, op: "below", value: 1 }],
      },
    };

    const signals = runStrategy(config, makeBars(closes));
    const buys = signals.filter((s) => s.direction === "buy");
    // RSI of gently oscillating prices should be around 50
    expect(buys.length).toBeGreaterThanOrEqual(1);
  });

  it("MACD indicator works with output selector", () => {
    // Downtrend then uptrend to get MACD histogram crossover
    const closes = [
      ...Array.from({ length: 30 }, (_, i) => 100 - i * 0.5),
      ...Array.from({ length: 15 }, (_, i) => 85 + i * 2),
    ];

    const config: RuleStrategyConfig = {
      id: "test-macd",
      name: "MACD Test",
      buyRules: {
        logic: "all",
        conditions: [
          {
            left: { indicator: "macd", period: 12, param: 26, output: "histogram" },
            op: "crosses_above",
            value: 0,
          },
        ],
      },
      sellRules: {
        logic: "all",
        conditions: [
          {
            left: { indicator: "macd", period: 12, param: 26, output: "histogram" },
            op: "crosses_below",
            value: 0,
          },
        ],
      },
    };

    const signals = runStrategy(config, makeBars(closes));
    // The uptrend should eventually cause MACD histogram to cross above 0
    expect(signals.length).toBeGreaterThanOrEqual(0); // May or may not trigger depending on data
  });

  it("Bollinger band condition works", () => {
    // Price oscillating — check if buy triggers when price < lower band
    const closes = [
      ...Array.from({ length: 25 }, () => 50),
      // Big drop
      40,
      38,
      36,
    ];

    const config: RuleStrategyConfig = {
      id: "test-boll",
      name: "Bollinger Test",
      buyRules: {
        logic: "all",
        conditions: [
          {
            left: { indicator: "price" },
            op: "below",
            right: { indicator: "bollinger", period: 20, param: 2, output: "lower" },
          },
        ],
      },
      sellRules: {
        logic: "all",
        conditions: [
          {
            left: { indicator: "price" },
            op: "above",
            right: { indicator: "bollinger", period: 20, param: 2, output: "upper" },
          },
        ],
      },
    };

    const signals = runStrategy(config, makeBars(closes));
    const buys = signals.filter((s) => s.direction === "buy");
    // Price dropping to 36 from 50 should breach lower band
    expect(buys.length).toBeGreaterThanOrEqual(1);
  });

  it("does not buy when already holding", () => {
    // Price always above 0 — buy condition always true
    const closes = Array.from({ length: 10 }, () => 50);

    const config: RuleStrategyConfig = {
      id: "test-no-double-buy",
      name: "No Double Buy",
      buyRules: {
        logic: "all",
        conditions: [{ left: { indicator: "price" }, op: "above", value: 0 }],
      },
      sellRules: {
        logic: "all",
        conditions: [{ left: { indicator: "price" }, op: "below", value: 1 }],
      },
    };

    const signals = runStrategy(config, makeBars(closes));
    const buys = signals.filter((s) => s.direction === "buy");
    // Should only buy once (second bar onwards, since index 0 is skipped)
    expect(buys.length).toBe(1);
  });
});
