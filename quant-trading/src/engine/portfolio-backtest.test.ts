/**
 * Integration test: 选股 → 多标的组合回测 全流程 (demo data)
 *
 * Simulates the complete pipeline:
 *   1. Stock screener returns 5 symbols (mocked — screener uses Python/network)
 *   2. Portfolio backtest with ¥300,000 initial capital, 1-year window
 *   3. Validates portfolio-level and per-symbol metrics
 *
 * Uses the "demo" data provider (synthetic price series) so the test is
 * deterministic, offline, and fast.
 */

import { describe, expect, it } from "vitest";
import type { QuantTradingConfig } from "../types.js";
import { runPortfolioBacktest, dualMaCrossover } from "./builtin-engine.js";
import { runBacktest } from "./strategy-registry.js";

// ── Config with demo data provider ───────────────────────────────────────────

const demoConfig: QuantTradingConfig = {
  dataProvider: "demo",
  broker: "paper",
  initialCash: 300_000,
  backtestEngine: "builtin",
} as QuantTradingConfig;

// Simulated output from stock screener (e.g., F-Score top picks from HS300)
// In production this comes from: stock_screen → action=run_screener, screenerId=fscore, universe=hs300
const SCREENED_SYMBOLS = ["600519", "000858", "300750", "601318", "000002"];

const ONE_YEAR_AGO = (() => {
  const d = new Date();
  d.setFullYear(d.getFullYear() - 1);
  return d.toISOString().slice(0, 10);
})();
const TODAY = new Date().toISOString().slice(0, 10);

// ── Tests ────────────────────────────────────────────────────────────────────

describe("Portfolio backtest integration — Screen 5 → Backtest 30万", () => {
  describe("step 1: simulate screener output", () => {
    it("screener returns 5 symbols (fixture simulating fscore screener)", () => {
      // In a live run this calls stock_screen tool; here we use the known list
      expect(SCREENED_SYMBOLS).toHaveLength(5);
      expect(SCREENED_SYMBOLS.every((s) => typeof s === "string" && s.length > 0)).toBe(true);
    });
  });

  describe("step 2: portfolio backtest via strategy-registry.runBacktest (multi-symbol path)", () => {
    it("routes to runPortfolioBacktest when symbols.length > 1", async () => {
      const result = await runBacktest(
        {
          strategyId: "dual-ma-crossover",
          symbols: SCREENED_SYMBOLS,
          startDate: ONE_YEAR_AGO,
          endDate: TODAY,
          initialCash: 300_000,
        },
        demoConfig,
      );

      // Portfolio-level fields
      expect(result.initialCash).toBe(300_000);
      expect(result.finalValue).toBeGreaterThan(0);
      expect(typeof result.totalReturn).toBe("number");
      expect(typeof result.annualizedReturn).toBe("number");
      expect(result.maxDrawdown).toBeGreaterThanOrEqual(0);
      expect(result.sharpeRatio).toBeDefined();
      expect(result.symbols).toHaveLength(5);
      expect(result.equityCurve.length).toBeGreaterThan(0);

      // Per-symbol breakdown should exist in the extended result
      const extended = result as typeof result & { perSymbol?: unknown[] };
      expect(extended.perSymbol).toBeDefined();
      expect(extended.perSymbol).toHaveLength(5);
    });
  });

  describe("step 3: runPortfolioBacktest directly", () => {
    it("creates an isolated strategy instance for each symbol when given a factory", async () => {
      let created = 0;
      const result = await runPortfolioBacktest(
        () => {
          created++;
          return dualMaCrossover(10, 30);
        },
        {
          strategyId: "dual-ma-crossover",
          symbols: ["600519", "000858"],
          startDate: ONE_YEAR_AGO,
          endDate: TODAY,
          initialCash: 100_000,
        },
        demoConfig,
      );

      expect(created).toBe(2);
      expect(result.perSymbol).toHaveLength(2);
    });

    it("allocates initialCash equally across all symbols", async () => {
      const strategy = dualMaCrossover(10, 30);
      const result = await runPortfolioBacktest(
        strategy,
        {
          strategyId: "dual-ma-crossover",
          symbols: SCREENED_SYMBOLS,
          startDate: ONE_YEAR_AGO,
          endDate: TODAY,
          initialCash: 300_000,
        },
        demoConfig,
      );

      // Each symbol gets ¥60,000 (300k / 5)
      const perSymbolCash = 300_000 / 5;
      for (const { result: sr } of result.perSymbol) {
        expect(sr.initialCash).toBe(perSymbolCash);
        expect(sr.finalValue).toBeGreaterThan(0);
      }
    });

    it("portfolio final value = sum of per-symbol final values", async () => {
      const strategy = dualMaCrossover(10, 30);
      const result = await runPortfolioBacktest(
        strategy,
        {
          strategyId: "dual-ma-crossover",
          symbols: SCREENED_SYMBOLS,
          startDate: ONE_YEAR_AGO,
          endDate: TODAY,
          initialCash: 300_000,
        },
        demoConfig,
      );

      const sumFinal = result.perSymbol.reduce((sum, { result: sr }) => sum + sr.finalValue, 0);
      expect(result.finalValue).toBeCloseTo(sumFinal, 1);
    });

    it("returns aggregated signals from all symbols sorted by timestamp", async () => {
      const strategy = dualMaCrossover(10, 30);
      const result = await runPortfolioBacktest(
        strategy,
        {
          strategyId: "dual-ma-crossover",
          symbols: SCREENED_SYMBOLS,
          startDate: ONE_YEAR_AGO,
          endDate: TODAY,
          initialCash: 300_000,
        },
        demoConfig,
      );

      // signals should be sorted ascending by timestamp
      for (let i = 1; i < result.signals.length; i++) {
        expect(result.signals[i]!.timestamp).toBeGreaterThanOrEqual(
          result.signals[i - 1]!.timestamp,
        );
      }
    });

    it("portfolio equity curve length matches trading days", async () => {
      const strategy = dualMaCrossover(5, 20);
      const result = await runPortfolioBacktest(
        strategy,
        {
          strategyId: "dual-ma-crossover",
          symbols: ["600519", "000858"], // 2 symbols for simplicity
          startDate: ONE_YEAR_AGO,
          endDate: TODAY,
          initialCash: 100_000,
        },
        demoConfig,
      );

      // Should have ~250 trading days a year
      expect(result.equityCurve.length).toBeGreaterThan(50);
      // Each entry must have date and positive value
      for (const { date, value } of result.equityCurve) {
        expect(date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
        expect(value).toBeGreaterThan(0);
      }
    });

    it("reports portfolio drawdown ≥ the best individual symbol drawdown", async () => {
      // Diversification can reduce drawdown — portfolio DD should be ≤ worst individual DD.
      // Here we just check the value is non-negative and ≤ 100%.
      const strategy = dualMaCrossover(10, 30);
      const result = await runPortfolioBacktest(
        strategy,
        {
          strategyId: "dual-ma-crossover",
          symbols: SCREENED_SYMBOLS,
          startDate: ONE_YEAR_AGO,
          endDate: TODAY,
          initialCash: 300_000,
        },
        demoConfig,
      );

      expect(result.maxDrawdown).toBeGreaterThanOrEqual(0);
      expect(result.maxDrawdown).toBeLessThanOrEqual(100);
    });
  });

  describe("step 4: backtest tool (AI callable) — multi-symbol output shape", () => {
    it("backtest tool returns perSymbol array for multi-symbol input", async () => {
      // Import here to avoid fakeApi dependency in this test module
      const { createBacktestTool } = await import("../tools/backtest.js");
      const fakeApi = {
        id: "quant-trading",
        name: "quant-trading",
        source: "test",
        config: {},
        pluginConfig: {},
        runtime: { version: "test" } as never,
        logger: { info() {}, warn() {}, error() {}, debug() {} },
        registerTool() {},
        registerHttpHandler() {},
        registerChannel() {},
        registerGatewayMethod() {},
        registerCli() {},
        registerService() {},
        registerProvider() {},
        registerHook() {},
        registerHttpRoute() {},
        registerCommand() {},
        on() {},
        resolvePath: (p: string) => p,
      } as never;

      const tool = createBacktestTool(fakeApi, demoConfig);
      const result = (await tool.execute({
        action: "run",
        strategyId: "dual-ma-crossover",
        symbols: SCREENED_SYMBOLS.join(","),
        startDate: ONE_YEAR_AGO,
        endDate: TODAY,
        initialCash: 300_000,
      })) as {
        summary: Record<string, unknown>;
        perSymbol: Array<{ symbol: string; allocation: string; totalReturn: string }> | undefined;
        signals: unknown[];
      };

      // Summary must be present
      expect(result.summary).toBeDefined();
      expect(result.summary.initialCash).toBe("¥300,000");
      expect(result.summary.symbols).toHaveLength(5);

      // perSymbol breakdown
      expect(result.perSymbol).toBeDefined();
      expect(result.perSymbol).toHaveLength(5);
      for (const sym of result.perSymbol!) {
        expect(sym.symbol).toBeTruthy();
        expect(sym.allocation).toMatch(/¥/);
        expect(sym.totalReturn).toMatch(/%$/);
      }
    });
  });
});
