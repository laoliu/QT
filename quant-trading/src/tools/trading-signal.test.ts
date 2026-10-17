/**
 * Unit tests for the trading_signal AI tool.
 *
 * Tests load signal fixtures from a temp dir so no real filesystem state
 * is required or mutated outside the test run.
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { QuantTradingConfig, TradingSignal } from "../types.js";
import { createTradingSignalTool } from "./trading-signal.js";

// Minimal mock for QuantTradingRuntime — tool doesn't use it
const fakeApi = {} as never;

function makeConfig(dataDir: string): QuantTradingConfig {
  return {
    dataDir,
    dataProvider: "demo",
    broker: "paper",
    initialCash: 1_000_000,
    pythonPath: "python3",
    backtestEngine: "builtin",
  } as QuantTradingConfig;
}

function makeSignal(overrides?: Partial<TradingSignal>): TradingSignal {
  return {
    timestamp: Date.now(),
    symbol: "600519",
    direction: "buy",
    price: 1800,
    strategyId: "dual-ma-crossover",
    ...overrides,
  };
}

// ── Setup ────────────────────────────────────────────────────────────────────

let tmpDir: string;
let config: QuantTradingConfig;

beforeEach(async () => {
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "oc-quant-test-"));
  config = makeConfig(tmpDir);
});

afterEach(async () => {
  await fs.rm(tmpDir, { recursive: true, force: true });
});

async function writeSignals(signals: TradingSignal[]): Promise<void> {
  await fs.writeFile(path.join(tmpDir, "signals.json"), JSON.stringify(signals));
}

// ── Tests ────────────────────────────────────────────────────────────────────

describe("trading_signal tool", () => {
  describe("action=recent", () => {
    it("returns empty list when no signals file", async () => {
      const tool = createTradingSignalTool(fakeApi, config);
      const result = (await tool.execute({ action: "recent" })) as {
        count: number;
        signals: unknown[];
      };
      expect(result.count).toBe(0);
      expect(result.signals).toHaveLength(0);
    });

    it("returns last N signals ordered by insertion", async () => {
      const signals = Array.from({ length: 30 }, (_, i) =>
        makeSignal({ timestamp: 1_000_000 + i, symbol: `SYM${i}` }),
      );
      await writeSignals(signals);
      const tool = createTradingSignalTool(fakeApi, config);
      const result = (await tool.execute({ action: "recent", limit: 5 })) as {
        count: number;
        signals: { symbol: string }[];
      };
      expect(result.count).toBe(5);
      // Last 5 in the array
      expect(result.signals[0].symbol).toBe("SYM25");
      expect(result.signals[4].symbol).toBe("SYM29");
    });
  });

  describe("action=by_strategy", () => {
    it("filters signals by strategyId", async () => {
      await writeSignals([
        makeSignal({ strategyId: "strat-A", symbol: "000001" }),
        makeSignal({ strategyId: "strat-B", symbol: "000002" }),
        makeSignal({ strategyId: "strat-A", symbol: "000003" }),
      ]);
      const tool = createTradingSignalTool(fakeApi, config);
      const result = (await tool.execute({ action: "by_strategy", strategyId: "strat-A" })) as {
        count: number;
        signals: { symbol: string }[];
      };
      expect(result.count).toBe(2);
      expect(result.signals.map((s) => s.symbol)).toEqual(["000001", "000003"]);
    });

    it("returns empty list for unknown strategy", async () => {
      await writeSignals([makeSignal({ strategyId: "strat-A" })]);
      const tool = createTradingSignalTool(fakeApi, config);
      const result = (await tool.execute({ action: "by_strategy", strategyId: "strat-X" })) as {
        count: number;
      };
      expect(result.count).toBe(0);
    });
  });

  describe("action=by_symbol", () => {
    it("filters signals by symbol", async () => {
      await writeSignals([
        makeSignal({ symbol: "600519" }),
        makeSignal({ symbol: "000858" }),
        makeSignal({ symbol: "600519" }),
      ]);
      const tool = createTradingSignalTool(fakeApi, config);
      const result = (await tool.execute({ action: "by_symbol", symbol: "600519" })) as {
        count: number;
      };
      expect(result.count).toBe(2);
    });
  });

  describe("action=stats", () => {
    it("returns zeros when no signals", async () => {
      const tool = createTradingSignalTool(fakeApi, config);
      const result = (await tool.execute({ action: "stats" })) as {
        total: number;
        strategies: unknown[];
        symbols: unknown[];
      };
      expect(result.total).toBe(0);
      expect(result.strategies).toHaveLength(0);
      expect(result.symbols).toHaveLength(0);
    });

    it("aggregates buy/sell counts per strategy", async () => {
      await writeSignals([
        makeSignal({ strategyId: "strat-A", direction: "buy" }),
        makeSignal({ strategyId: "strat-A", direction: "sell" }),
        makeSignal({ strategyId: "strat-A", direction: "buy" }),
        makeSignal({ strategyId: "strat-B", direction: "sell" }),
      ]);
      const tool = createTradingSignalTool(fakeApi, config);
      const result = (await tool.execute({ action: "stats" })) as {
        total: number;
        strategies: { strategyId: string; count: number; buys: number; sells: number }[];
      };
      expect(result.total).toBe(4);
      const a = result.strategies.find((s) => s.strategyId === "strat-A")!;
      expect(a.count).toBe(3);
      expect(a.buys).toBe(2);
      expect(a.sells).toBe(1);
      const b = result.strategies.find((s) => s.strategyId === "strat-B")!;
      expect(b.count).toBe(1);
      expect(b.sells).toBe(1);
    });

    it("aggregates counts per symbol", async () => {
      await writeSignals([
        makeSignal({ symbol: "600519" }),
        makeSignal({ symbol: "600519" }),
        makeSignal({ symbol: "000858" }),
      ]);
      const tool = createTradingSignalTool(fakeApi, config);
      const result = (await tool.execute({ action: "stats" })) as {
        symbols: { symbol: string; count: number }[];
      };
      const sym = result.symbols.find((s) => s.symbol === "600519")!;
      expect(sym.count).toBe(2);
    });
  });

  describe("action=clear", () => {
    it("clears all signals and reports count", async () => {
      await writeSignals([makeSignal(), makeSignal(), makeSignal()]);
      const tool = createTradingSignalTool(fakeApi, config);
      const result = (await tool.execute({ action: "clear" })) as {
        success: boolean;
        message: string;
      };
      expect(result.success).toBe(true);
      expect(result.message).toContain("3");
    });

    it("signals file is empty after clear", async () => {
      await writeSignals([makeSignal()]);
      const tool = createTradingSignalTool(fakeApi, config);
      await tool.execute({ action: "clear" });
      const raw = await fs.readFile(path.join(tmpDir, "signals.json"), "utf-8");
      expect(JSON.parse(raw)).toHaveLength(0);
    });
  });

  describe("unknown action", () => {
    it("returns error for unknown action", async () => {
      const tool = createTradingSignalTool(fakeApi, config);
      const result = (await tool.execute({ action: "invalid" })) as { error: string };
      expect(result.error).toMatch(/未知操作/);
    });
  });
});
