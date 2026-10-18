/**
 * Tests for strategy registry.
 */
import { describe, expect, it } from "vitest";
import catalog from "../../strategies/catalog.json" with { type: "json" };
import { getStrategy, hasStrategyFactory, listStrategies, resolveBuiltinStrategy, runBacktest } from "./strategy-registry.js";
import { additionalLegacyPorts } from "./strategies/legacy-ports.js";

describe("strategy-registry", () => {
  describe("listStrategies", () => {
    it("should use a unique cross-language catalog with AIPHA as default research", () => {
      const ids = catalog.entries.map((entry) => entry.id);
      expect(new Set(ids).size).toBe(ids.length);
      expect(catalog.entries.find((entry) => entry.id === catalog.defaultResearchStrategy)?.engine).toBe("aipha");
      expect(catalog.defaultResearchStrategy).toBe("aipha-ml-ranking");
    });
    it("should account for every compatibility strategy", () => {
      const compatibility = catalog.entries.filter((entry) => entry.lifecycle === "compatibility");
      expect(compatibility.length).toBeGreaterThan(0);
      const builtinIds = new Set(listStrategies().map((strategy) => strategy.id));
      for (const entry of compatibility) {
        expect(entry.migration?.status).toMatch(/^(migrated_to_builtin|retired)$/);
        if (entry.migration?.status === "migrated_to_builtin") {
          expect(entry.migration.canonicalId).toBeTruthy();
          expect(builtinIds.has(entry.migration.canonicalId!)).toBe(true);
          expect(getStrategy(entry.id)?.canonicalStrategyId).toBe(entry.migration.canonicalId);
          expect(getStrategy(entry.id)?.availableEngines).toEqual(["builtin"]);
        }
      }
    });
    it("should return built-in strategies", () => {
      const strategies = listStrategies();
      expect(strategies.length).toBeGreaterThanOrEqual(3);
      expect(strategies.map((s) => s.id)).toContain("dual-ma-crossover");
      expect(strategies.map((s) => s.id)).toContain("momentum-breakout");
      expect(strategies.map((s) => s.id)).toContain("mean-reversion");
    });

    it("each strategy should have required fields", () => {
      const validEngines = ["builtin", "backtrader", "python", "vnpy", "aipha"];
      for (const s of listStrategies()) {
        expect(s.id).toBeTruthy();
        expect(s.name).toBeTruthy();
        expect(s.description).toBeTruthy();
        expect(validEngines).toContain(s.engine);
        expect(Array.isArray(s.parameters)).toBe(true);
      }
    });
  });

  describe("getStrategy", () => {
    it("should find dual-ma-crossover", () => {
      const s = getStrategy("dual-ma-crossover");
      expect(s).toBeDefined();
      expect(s!.name).toContain("MA");
    });

    it("should return undefined for unknown strategy", () => {
      expect(getStrategy("nonexistent")).toBeUndefined();
    });

    it("should expose canonical catalog metadata for TypeScript strategies", () => {
      const strategy = getStrategy("dual-ma-crossover");
      expect(strategy?.timeframes).toContain("1d");
      expect(strategy?.dataRequirements).toContain("ohlcv");
      expect(strategy?.signalFormat).toBe("trading_signal");
      expect(strategy?.lifecycle).toBe("primary");
      expect(strategy?.availableEngines).toContain("builtin");
    });

    it("should identify the AIPHA low-frequency research route", () => {
      const strategy = getStrategy("aipha-ml-ranking");
      expect(strategy?.engine).toBe("aipha");
      expect(strategy?.signalFormat).toBe("target_position");
      expect(strategy?.dataRequirements).toContain("fundamentals_pit");
    });
    it("should expose legacy IDs as builtin aliases", () => {
      const strategy = getStrategy("macd");
      expect(strategy?.engine).toBe("builtin");
      expect(strategy?.canonicalStrategyId).toBe("macd-long-only");
      expect(strategy?.migrationStatus).toBe("migrated_to_builtin");
      expect(strategy?.availableEngines).toEqual(["builtin"]);
      expect(getStrategy("sector-synergy")).toBeUndefined();
    });

    it("versions corrected legacy signal ports separately from the old approximate aliases", () => {
      for (const [legacy, canonical] of [
        ["ema-cross", "ema-cross-long-only"], ["donchian-breakout", "donchian-long-only"],
        ["kdj", "kdj-long-only"], ["bollinger", "bollinger-squeeze-reversion"],
        ...additionalLegacyPorts.map(port => [port.legacyId, port.canonicalId]),
      ]) {
        expect(getStrategy(legacy!)?.version).toBe("2.0.0");
        expect(getStrategy(legacy!)?.canonicalStrategyId).toBe(canonical);
        expect(getStrategy(canonical!)?.engine).toBe("builtin");
      }
    });

    it("keeps the distinct native Supertrend available under an explicit ID", () => {
      expect(getStrategy("supertrend-ts")?.engine).toBe("builtin");
      expect(getStrategy("supertrend")?.canonicalStrategyId).toBe("supertrend-python-long-only");
      expect(resolveBuiltinStrategy("supertrend-ts")).toBeDefined();
    });

    it.each(["dual-thrust", "ichimoku", "keltner-channel", "parabolic-sar", "pivot-point", "r-breaker", "swing-overlay", "williams-r"])("preserves native %s after retiring its same-name Python source", id => {
      expect(getStrategy(id)?.engine).toBe("builtin");
      expect(hasStrategyFactory(id)).toBe(true);
      expect(resolveBuiltinStrategy(id)).toBeDefined();
    });

    it.each(["turtle", "obv", "vnpy-double-ma", "vnpy-turtle"])("blocks retired %s in all builtin entry points", async id => {
      expect(getStrategy(id)).toBeUndefined();
      expect(hasStrategyFactory(id)).toBe(false);
      expect(listStrategies().some(strategy => strategy.id === id)).toBe(false);
      expect(() => resolveBuiltinStrategy(id)).toThrow("退役");
      await expect(runBacktest({ strategyId: id, symbols: ["600519"], startDate: "2024-01-01",
        endDate: "2024-12-31", initialCash: 100_000 }, { dataProvider: "demo" })).rejects.toThrow("退役");
    });
  });

  describe("runBacktest (builtin)", () => {
    it("should run dual-ma-crossover backtest with demo data", async () => {
      const result = await runBacktest(
        {
          strategyId: "dual-ma-crossover",
          symbols: ["000300"],
          startDate: "2024-01-01",
          endDate: "2024-12-31",
          initialCash: 100_000,
          engine: "builtin",
          parameters: { fastPeriod: 5, slowPeriod: 20 },
        },
        { dataProvider: "demo" },
      );

      expect(result.strategyId).toBe("dual-ma-crossover");
      expect(result.finalValue).toBeGreaterThan(0);
      expect(result.equityCurve.length).toBeGreaterThan(0);
      expect(result.runId).toBeTruthy();
      expect(result.strategyVersion).toBe("1.0.0");
      expect(result.dataAsOf).toBe(result.equityCurve.at(-1)?.date);
      expect(result.runEnvelope?.schemaVersion).toBe("1.0");
      expect(result.runEnvelope?.runId).toBe(result.runId);
    });

    it("should throw for unknown builtin strategy", async () => {
      await expect(
        runBacktest(
          {
            strategyId: "nonexistent-strategy",
            symbols: ["000300"],
            startDate: "2024-01-01",
            endDate: "2024-12-31",
            initialCash: 100_000,
            engine: "builtin",
          },
          { dataProvider: "demo" },
        ),
      ).rejects.toThrow("未知策略");
    });

    it("should run cta-trend backtest via registry", async () => {
      const result = await runBacktest(
        {
          strategyId: "cta-trend",
          symbols: ["000300"],
          startDate: "2024-01-01",
          endDate: "2024-12-31",
          initialCash: 500_000,
          engine: "builtin",
          parameters: { trendPeriod: 15, atrPeriod: 10 },
        },
        { dataProvider: "demo" },
      );
      expect(result.strategyId).toBe("cta-trend");
      expect(result.finalValue).toBeGreaterThan(0);
    });

    it("should run dual-thrust backtest via registry", async () => {
      const result = await runBacktest(
        {
          strategyId: "dual-thrust",
          symbols: ["000300"],
          startDate: "2024-01-01",
          endDate: "2024-12-31",
          initialCash: 500_000,
          engine: "builtin",
          parameters: { lookback: 4, k1: 0.4, k2: 0.4 },
        },
        { dataProvider: "demo" },
      );
      expect(result.strategyId).toBe("dual-thrust");
      expect(result.finalValue).toBeGreaterThan(0);
    });

    it("should run spread-arbitrage backtest via registry", async () => {
      const result = await runBacktest(
        {
          strategyId: "spread-arbitrage",
          symbols: ["000300"],
          startDate: "2024-01-01",
          endDate: "2024-12-31",
          initialCash: 500_000,
          engine: "builtin",
          parameters: { maPeriod: 20, entryStdDevs: 1.5 },
        },
        { dataProvider: "demo" },
      );
      expect(result.strategyId).toBe("spread-arbitrage");
      expect(result.finalValue).toBeGreaterThan(0);
    });

    it("should run a migrated legacy ID through builtin and reject the old engine", async () => {
      const result = await runBacktest(
        {
          strategyId: "macd",
          symbols: ["000300"],
          startDate: "2024-01-01",
          endDate: "2024-12-31",
          initialCash: 100_000,
          parameters: { fast_period: 5, slow_period: 20 },
        },
        { dataProvider: "demo" },
      );
      expect(result.engine).toBe("builtin");
      expect(result.runEnvelope?.engine).toBe("builtin");
      await expect(
        runBacktest(
          {
            strategyId: "macd",
            symbols: ["000300"],
            startDate: "2024-01-01",
            endDate: "2024-12-31",
            initialCash: 100_000,
            engine: "backtrader",
          },
          { dataProvider: "demo" },
        ),
      ).rejects.toThrow("旧引擎入口已关闭");
    });

    it("should execute every migrated catalog alias through its canonical builtin", async () => {
      const migrated = catalog.entries.filter((entry) => entry.migration?.status === "migrated_to_builtin");
      for (const entry of migrated) {
        const result = await runBacktest(
          {
            strategyId: entry.id,
            symbols: ["000300"],
            startDate: "2024-01-01",
            endDate: "2024-03-31",
            initialCash: 100_000,
          },
          { dataProvider: "demo" },
        );
        expect(result.engine, entry.id).toBe("builtin");
        expect(result.runEnvelope?.engine, entry.id).toBe("builtin");
        expect(result.runEnvelope?.strategyId, entry.id).toBe(entry.id);
      }
    }, 60_000);

    it("should reject a formally retired legacy strategy", async () => {
      const retired = catalog.entries
        .filter((entry) => entry.migration?.status === "retired")
        .map((entry) => entry.id);
      for (const strategyId of retired) {
        await expect(
          runBacktest(
            {
              strategyId,
              symbols: ["000300"],
              startDate: "2024-01-01",
              endDate: "2024-12-31",
              initialCash: 100_000,
            },
            { dataProvider: "demo" },
          ),
        ).rejects.toThrow("已正式退役");
      }
    });
  });

  describe("CTA strategy definitions", () => {
    it("should expose one canonical definition per strategy ID", () => {
      const ids = listStrategies().map((s) => s.id);
      expect(new Set(ids).size).toBe(ids.length);
    });

    it("should list cta-trend in registry", () => {
      const strategies = listStrategies();
      const ids = strategies.map((s) => s.id);
      expect(ids).toContain("cta-trend");
      expect(ids).toContain("dual-thrust");
      expect(ids).toContain("spread-arbitrage");
    });

    it("cta-trend definition should have futures market", () => {
      const s = getStrategy("cta-trend");
      expect(s).toBeDefined();
      expect(s!.name).toContain("CTA");
      expect(s!.markets).toContain("futures");
      expect(s!.parameters.some((p) => p.name === "atrPeriod")).toBe(true);
    });

    it("dual-thrust definition should have futures market", () => {
      const s = getStrategy("dual-thrust");
      expect(s).toBeDefined();
      expect(s!.name).toContain("Dual Thrust");
      expect(s!.markets).toContain("futures");
      expect(s!.parameters.some((p) => p.name === "k1")).toBe(true);
      expect(s!.engine).toBe("builtin");
    });

    it("spread-arbitrage definition should have futures market", () => {
      const s = getStrategy("spread-arbitrage");
      expect(s).toBeDefined();
      expect(s!.name).toContain("套利");
      expect(s!.markets).toContain("futures");
    });
  });
});
