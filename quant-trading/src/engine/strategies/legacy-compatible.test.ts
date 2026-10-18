import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import type { KlineBar } from "../../types.js";
import { resolveBuiltinStrategy } from "../strategy-registry.js";
import type { BuiltinStrategyContext } from "./indicators.js";

type SignalRow = { index: number; direction: string; sizePct?: number };
const fixture = JSON.parse(readFileSync(new URL("./fixtures/legacy-signals.json", import.meta.url), "utf8")) as {
  bars: KlineBar[];
  cases: Array<{ legacyId: string; canonicalId: string; source: string; sourceSha256: string;
    parameters: Record<string, number>; expected: SignalRow[] }>;
};

function canonicalParams(id: string, p: Record<string, number>) {
  if (id === "ema-cross") return { fastPeriod: p.fast_period, slowPeriod: p.slow_period };
  if (id === "donchian-breakout") return { upperPeriod: p.upper_period, lowerPeriod: p.lower_period };
  if (id === "kdj") return { period: p.k_period, kSmooth: p.d_period, dPeriod: p.j_smooth,
    overbought: p.overbought, oversold: p.oversold };
  return { period: p.period, devfactor: p.devfactor, squeezePct: p.squeeze_pct };
}

function collect(id: string, params: Record<string, unknown>, bars = fixture.bars): SignalRow[] {
  const strategy = resolveBuiltinStrategy(id, params);
  const ctx: BuiltinStrategyContext = { bars, index: 0, position: 0,
    cash: 1_000_000, params, signals: [] };
  strategy.init?.(ctx);
  const result: SignalRow[] = [];
  for (let i = 0; i < ctx.bars.length; i++) {
    ctx.index = i;
    const emitted = strategy.next(ctx);
    for (const sig of !emitted ? [] : Array.isArray(emitted) ? emitted : [emitted]) {
      result.push({ index: i, direction: sig.direction,
        ...(sig.direction === "buy" ? { sizePct: sig.sizePct ?? 95 } : {}) });
      if (sig.direction === "buy") {
        let qty = Math.floor(ctx.cash * (sig.sizePct ?? 95) / 100 / sig.price);
        if (qty >= 100) qty = Math.floor(qty / 100) * 100;
        ctx.position += qty;
        ctx.cash -= qty * sig.price;
      } else if (sig.direction === "sell") {
        ctx.cash += ctx.position * sig.price;
        ctx.position = 0;
      }
    }
  }
  return result;
}

describe("retained Python strategy signal rules", () => {
  it("uses fixtures generated from the current retained Python sources", () => {
    for (const testCase of fixture.cases) {
      const source = readFileSync(new URL(`../../../../${testCase.source}`, import.meta.url), "utf8").replace(/\r\n/g, "\n");
      expect(createHash("sha256").update(source).digest("hex")).toBe(testCase.sourceSha256);
    }
  });
  for (const [i, testCase] of fixture.cases.entries()) {
    it(`${testCase.legacyId} matches Backtrader fixture ${i}`, () => {
      const actual = collect(testCase.canonicalId, canonicalParams(testCase.legacyId, testCase.parameters));
      expect(actual).toEqual(testCase.expected);
      expect(actual.every(sig => sig.direction === "buy" || sig.direction === "sell")).toBe(true);
    });
  }

  it("preserves the legacy entry's parameter mapping", () => {
    for (const testCase of fixture.cases) {
      expect(collect(testCase.legacyId, testCase.parameters)).toEqual(testCase.expected);
    }
  });

  it("does not substitute SMA for EMA or discard Donchian's exit period", () => {
    expect(collect("ema-cross", { fast_period: 3, slow_period: 10 }))
      .not.toEqual(collect("dual-ma-crossover", { fastPeriod: 3, slowPeriod: 10 }));
    expect(collect("donchian-breakout", { upper_period: 7, lower_period: 3 }))
      .not.toEqual(collect("donchian-breakout", { upper_period: 7, lower_period: 12 }));
  });

  it("retains KDJ's half-cash entries and Bollinger's squeeze filter", () => {
    expect(collect("kdj", {}).some(sig => sig.sizePct === 50)).toBe(true);
    expect(collect("bollinger", { period: 10, devfactor: 1.5, squeeze_pct: 0 }))
      .not.toEqual(collect("bollinger", { period: 10, devfactor: 1.5, squeeze_pct: 80 }));
  });

  it("does not change earlier signals when future bars are appended", () => {
    for (const testCase of fixture.cases) {
      const prefix = collect(testCase.legacyId, testCase.parameters, fixture.bars.slice(0, 130));
      expect(prefix).toEqual(testCase.expected.filter(sig => sig.index < 130));
    }
  });
});
