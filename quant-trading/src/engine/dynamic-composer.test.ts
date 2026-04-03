/**
 * Unit tests for the Dynamic Strategy Composer.
 */
import { describe, expect, it } from "vitest";
import type { KlineBar, TradingSignal } from "../types.js";
import { composeStrategy, type CompositionConfig } from "./dynamic-composer.js";
import type { BuiltinStrategy, BuiltinStrategyContext } from "./strategies/index.js";

// ── Test Helpers ─────────────────────────────────────────────────

function makeBars(n: number): KlineBar[] {
  return Array.from({ length: n }, (_, i) => ({
    timestamp: Date.now() + i * 86400000,
    open: 100 + i,
    high: 102 + i,
    low: 99 + i,
    close: 101 + i,
    volume: 1000 + i * 10,
  }));
}

function makeCtx(bars: KlineBar[], index: number, position = 0): BuiltinStrategyContext {
  return {
    bars,
    index,
    position,
    cash: 100000,
    params: {},
    signals: [],
  };
}

/** A strategy that always emits a buy signal. */
function alwaysBuy(): BuiltinStrategy {
  return {
    name: "AlwaysBuy",
    next(ctx) {
      const bar = ctx.bars[ctx.index]!;
      return {
        timestamp: bar.timestamp,
        symbol: "",
        direction: "buy" as const,
        price: bar.close,
        reason: "always-buy",
        strategyId: "always-buy",
      };
    },
  };
}

/** A strategy that always emits a sell signal. */
function alwaysSell(): BuiltinStrategy {
  return {
    name: "AlwaysSell",
    next(ctx) {
      const bar = ctx.bars[ctx.index]!;
      return {
        timestamp: bar.timestamp,
        symbol: "",
        direction: "sell" as const,
        price: bar.close,
        reason: "always-sell",
        strategyId: "always-sell",
      };
    },
  };
}

/** A strategy that emits nothing. */
function noSignal(): BuiltinStrategy {
  return {
    name: "NoSignal",
    next() {
      return null;
    },
  };
}

function resolveSubs(strategies: BuiltinStrategy[], ids: string[]) {
  return strategies.map((s, i) => ({
    config: { strategyId: ids[i]!, weight: 1 },
    strategy: s,
  }));
}

// ── Tests ────────────────────────────────────────────────────────

describe("DynamicComposer", () => {
  const bars = makeBars(30);

  describe("voting method", () => {
    it("emits buy when votes >= minVotes", () => {
      const config: CompositionConfig = {
        id: "test-voting",
        name: "Test Voting",
        method: "voting",
        strategies: [{ strategyId: "a" }, { strategyId: "b" }, { strategyId: "c" }],
        minVotes: 2,
      };
      const subs = resolveSubs([alwaysBuy(), alwaysBuy(), noSignal()], ["a", "b", "c"]);
      const composed = composeStrategy(config, subs);
      const ctx = makeCtx(bars, 10, 0);
      const signal = composed.next(ctx);
      expect(signal).not.toBeNull();
      const sig = signal as TradingSignal;
      expect(sig.direction).toBe("buy");
      expect(sig.reason).toContain("组合投票买入");
    });

    it("no signal when votes < minVotes", () => {
      const config: CompositionConfig = {
        id: "test-voting-no",
        name: "Test Voting No",
        method: "voting",
        strategies: [{ strategyId: "a" }, { strategyId: "b" }, { strategyId: "c" }],
        minVotes: 2,
      };
      const subs = resolveSubs([alwaysBuy(), noSignal(), noSignal()], ["a", "b", "c"]);
      const composed = composeStrategy(config, subs);
      const ctx = makeCtx(bars, 10, 0);
      const signal = composed.next(ctx);
      expect(signal).toBeNull();
    });

    it("emits sell when sell votes meet threshold and has position", () => {
      const config: CompositionConfig = {
        id: "test-voting-sell",
        name: "Test Voting Sell",
        method: "voting",
        strategies: [{ strategyId: "a" }, { strategyId: "b" }],
        minVotes: 2,
      };
      const subs = resolveSubs([alwaysSell(), alwaysSell()], ["a", "b"]);
      const composed = composeStrategy(config, subs);
      const ctx = makeCtx(bars, 10, 100);
      const signal = composed.next(ctx);
      expect(signal).not.toBeNull();
      expect((signal as TradingSignal).direction).toBe("sell");
    });
  });

  describe("weighted-score method", () => {
    it("emits buy when weighted score >= threshold", () => {
      const config: CompositionConfig = {
        id: "test-weighted",
        name: "Test Weighted",
        method: "weighted-score",
        strategies: [
          { strategyId: "a", weight: 3 },
          { strategyId: "b", weight: 1 },
        ],
        scoreThreshold: 0.5,
      };
      const subs = [
        { config: { strategyId: "a", weight: 3 }, strategy: alwaysBuy() },
        { config: { strategyId: "b", weight: 1 }, strategy: noSignal() },
      ];
      const composed = composeStrategy(config, subs);
      const ctx = makeCtx(bars, 10, 0);
      const signal = composed.next(ctx);
      expect(signal).not.toBeNull();
      expect((signal as TradingSignal).direction).toBe("buy");
      expect((signal as TradingSignal).reason).toContain("加权买入");
    });

    it("no signal when score below threshold", () => {
      const config: CompositionConfig = {
        id: "test-weighted-no",
        name: "Test Weighted No",
        method: "weighted-score",
        strategies: [
          { strategyId: "a", weight: 1 },
          { strategyId: "b", weight: 3 },
        ],
        scoreThreshold: 0.5,
      };
      const subs = [
        { config: { strategyId: "a", weight: 1 }, strategy: alwaysBuy() },
        { config: { strategyId: "b", weight: 3 }, strategy: noSignal() },
      ];
      const composed = composeStrategy(config, subs);
      const ctx = makeCtx(bars, 10, 0);
      const signal = composed.next(ctx);
      // score = 1/4 = 0.25, below threshold 0.5
      expect(signal).toBeNull();
    });
  });

  describe("gated method", () => {
    it("emits buy when primary and all filters agree", () => {
      const config: CompositionConfig = {
        id: "test-gated",
        name: "Test Gated",
        method: "gated",
        strategies: [
          { strategyId: "a", role: "primary" },
          { strategyId: "b", role: "filter" },
        ],
      };
      const subs = [
        { config: { strategyId: "a", role: "primary" as const }, strategy: alwaysBuy() },
        { config: { strategyId: "b", role: "filter" as const }, strategy: alwaysBuy() },
      ];
      const composed = composeStrategy(config, subs);
      const ctx = makeCtx(bars, 10, 0);
      const signal = composed.next(ctx);
      expect(signal).not.toBeNull();
      expect((signal as TradingSignal).direction).toBe("buy");
      expect((signal as TradingSignal).reason).toContain("门控买入");
    });

    it("no signal when filter explicitly opposes", () => {
      const config: CompositionConfig = {
        id: "test-gated-no",
        name: "Test Gated No",
        method: "gated",
        strategies: [
          { strategyId: "a", role: "primary" },
          { strategyId: "b", role: "filter" },
        ],
      };
      const subs = [
        { config: { strategyId: "a", role: "primary" as const }, strategy: alwaysBuy() },
        { config: { strategyId: "b", role: "filter" as const }, strategy: alwaysSell() },
      ];
      const composed = composeStrategy(config, subs);
      const ctx = makeCtx(bars, 10, 0);
      const signal = composed.next(ctx);
      expect(signal).toBeNull();
    });

    it("allows buy when filter has no signal", () => {
      const config: CompositionConfig = {
        id: "test-gated-passive-filter",
        name: "Test Gated Passive Filter",
        method: "gated",
        strategies: [
          { strategyId: "a", role: "primary" },
          { strategyId: "b", role: "filter" },
        ],
      };
      const subs = [
        { config: { strategyId: "a", role: "primary" as const }, strategy: alwaysBuy() },
        { config: { strategyId: "b", role: "filter" as const }, strategy: noSignal() },
      ];
      const composed = composeStrategy(config, subs);
      const ctx = makeCtx(bars, 10, 0);
      const signal = composed.next(ctx);
      expect(signal).not.toBeNull();
      expect((signal as TradingSignal).direction).toBe("buy");
    });
  });

  describe("cascade method", () => {
    it("emits buy when all stages agree", () => {
      const config: CompositionConfig = {
        id: "test-cascade",
        name: "Test Cascade",
        method: "cascade",
        strategies: [{ strategyId: "a" }, { strategyId: "b" }, { strategyId: "c" }],
      };
      const subs = resolveSubs([alwaysBuy(), alwaysBuy(), alwaysBuy()], ["a", "b", "c"]);
      const composed = composeStrategy(config, subs);
      const ctx = makeCtx(bars, 10, 0);
      const signal = composed.next(ctx);
      expect(signal).not.toBeNull();
      expect((signal as TradingSignal).direction).toBe("buy");
      expect((signal as TradingSignal).reason).toContain("级联买入");
    });

    it("no signal when any later stage explicitly opposes", () => {
      const config: CompositionConfig = {
        id: "test-cascade-no",
        name: "Test Cascade No",
        method: "cascade",
        strategies: [{ strategyId: "a" }, { strategyId: "b" }],
      };
      const subs = resolveSubs([alwaysBuy(), alwaysSell()], ["a", "b"]);
      const composed = composeStrategy(config, subs);
      const ctx = makeCtx(bars, 10, 0);
      const signal = composed.next(ctx);
      expect(signal).toBeNull();
    });

    it("allows buy when later stage has no signal", () => {
      const config: CompositionConfig = {
        id: "test-cascade-passive-later-stage",
        name: "Test Cascade Passive Later Stage",
        method: "cascade",
        strategies: [{ strategyId: "a" }, { strategyId: "b" }],
      };
      const subs = resolveSubs([alwaysBuy(), noSignal()], ["a", "b"]);
      const composed = composeStrategy(config, subs);
      const ctx = makeCtx(bars, 10, 0);
      const signal = composed.next(ctx);
      expect(signal).not.toBeNull();
      expect((signal as TradingSignal).direction).toBe("buy");
    });

    it("no signal when first stage has no signal", () => {
      const config: CompositionConfig = {
        id: "test-cascade-no-first",
        name: "Test Cascade No First",
        method: "cascade",
        strategies: [{ strategyId: "a" }, { strategyId: "b" }],
      };
      const subs = resolveSubs([noSignal(), alwaysBuy()], ["a", "b"]);
      const composed = composeStrategy(config, subs);
      const ctx = makeCtx(bars, 10, 0);
      const signal = composed.next(ctx);
      expect(signal).toBeNull();
    });
  });

  describe("init propagation", () => {
    it("calls init on all sub-strategies", () => {
      let initCount = 0;
      const s: BuiltinStrategy = {
        name: "InitCounter",
        init() {
          initCount++;
        },
        next() {
          return null;
        },
      };

      const config: CompositionConfig = {
        id: "test-init",
        name: "Test Init",
        method: "voting",
        strategies: [{ strategyId: "a" }, { strategyId: "b" }],
      };
      const subs = [
        { config: { strategyId: "a" }, strategy: s },
        { config: { strategyId: "b" }, strategy: { ...s, name: "InitCounter2" } },
      ];
      const composed = composeStrategy(config, subs);
      composed.init?.(makeCtx(bars, 0));
      expect(initCount).toBe(2);
    });
  });
});
