/**
 * Tests for strategy registry.
 */
import { describe, expect, it } from "vitest";
import { getStrategy, listStrategies, runBacktest } from "./strategy-registry.js";

describe("strategy-registry", () => {
  describe("listStrategies", () => {
    it("should return built-in strategies", () => {
      const strategies = listStrategies();
      expect(strategies.length).toBeGreaterThanOrEqual(3);
      expect(strategies.map((s) => s.id)).toContain("dual-ma-crossover");
      expect(strategies.map((s) => s.id)).toContain("momentum-breakout");
      expect(strategies.map((s) => s.id)).toContain("mean-reversion");
    });

    it("each strategy should have required fields", () => {
      const validEngines = ["builtin", "backtrader", "python"];
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
  });

  describe("CTA strategy definitions", () => {
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
    });

    it("spread-arbitrage definition should have futures market", () => {
      const s = getStrategy("spread-arbitrage");
      expect(s).toBeDefined();
      expect(s!.name).toContain("套利");
      expect(s!.markets).toContain("futures");
    });
  });
});
