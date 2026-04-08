import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TradeRecord } from "../types.js";
import { createPerformanceAttributionTool } from "./performance-attribution.js";

const fakeApi = { logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } } as never;

function makeTrade(overrides: Record<string, unknown> = {}): TradeRecord {
  return {
    id: `t-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
    symbol: "600519",
    direction: "long",
    entryPrice: 1800,
    exitPrice: 1850,
    quantity: 100,
    pnl: 5000,
    commission: 10,
    strategyId: "ma_cross",
    entryTime: "2025-01-10T09:30:00Z",
    exitTime: "2025-01-12T14:30:00Z",
    ...overrides,
  } as unknown as TradeRecord;
}

describe("performance_attribution tool", () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pa-tool-"));
  });

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  function createTool(trades: TradeRecord[]) {
    fs.writeFileSync(path.join(tmpDir, "trades.json"), JSON.stringify(trades));
    return createPerformanceAttributionTool(fakeApi, { dataDir: tmpDir } as never);
  }

  // ── no trades ──

  it("returns error when no trades exist", async () => {
    const tool = createTool([]);
    const res = (await tool.execute({ action: "full_report" })) as Record<string, unknown>;
    expect(res.error).toContain("暂无");
  });

  // ── full_report ──

  it("full_report returns comprehensive data", async () => {
    const trades = [
      makeTrade({ pnl: 5000, commission: 10, strategyId: "s1", symbol: "600519" }),
      makeTrade({ pnl: -2000, commission: 10, strategyId: "s1", symbol: "000858" }),
      makeTrade({ pnl: 3000, commission: 10, strategyId: "s2", symbol: "600519" }),
    ];
    const tool = createTool(trades);
    const res = (await tool.execute({ action: "full_report" })) as Record<string, unknown>;
    expect(res.totalTrades).toBe(3);
    expect(res.topStrategies).toBeDefined();
    expect(res.topSymbols).toBeDefined();
    expect(res.monthlyPnl).toBeDefined();
  });

  // ── by_strategy ──

  it("by_strategy breaks down by strategy", async () => {
    const trades = [
      makeTrade({ pnl: 5000, strategyId: "s1" }),
      makeTrade({ pnl: -1000, strategyId: "s2" }),
    ];
    const tool = createTool(trades);
    const res = (await tool.execute({ action: "by_strategy" })) as Record<string, unknown>;
    const strategies = res.strategies as Array<Record<string, unknown>>;
    expect(strategies.length).toBe(2);
    expect(strategies.some((s) => s.strategy === "s1")).toBe(true);
  });

  // ── by_symbol ──

  it("by_symbol breaks down by symbol", async () => {
    const trades = [
      makeTrade({ symbol: "600519", pnl: 5000 }),
      makeTrade({ symbol: "000858", pnl: 3000 }),
    ];
    const tool = createTool(trades);
    const res = (await tool.execute({ action: "by_symbol" })) as Record<string, unknown>;
    const symbols = res.symbols as Array<Record<string, unknown>>;
    expect(symbols.length).toBe(2);
  });

  // ── by_period ──

  it("by_period returns daily by default", async () => {
    const trades = [makeTrade({ exitTime: "2025-01-10T14:30:00Z", pnl: 1000 })];
    const tool = createTool(trades);
    const res = (await tool.execute({ action: "by_period" })) as Record<string, unknown>;
    expect(res.periodType).toBe("daily");
    expect((res.periods as unknown[]).length).toBeGreaterThan(0);
  });

  it("by_period supports weekly", async () => {
    const trades = [makeTrade({ exitTime: "2025-01-10T14:30:00Z", pnl: 1000 })];
    const tool = createTool(trades);
    const res = (await tool.execute({
      action: "by_period",
      period_type: "weekly",
    })) as Record<string, unknown>;
    expect(res.periodType).toBe("weekly");
  });

  it("by_period supports monthly", async () => {
    const trades = [makeTrade({ exitTime: "2025-01-10T14:30:00Z", pnl: 1000 })];
    const tool = createTool(trades);
    const res = (await tool.execute({
      action: "by_period",
      period_type: "monthly",
    })) as Record<string, unknown>;
    expect(res.periodType).toBe("monthly");
  });

  // ── date range filter ──

  it("full_report respects from/to filter", async () => {
    const trades = [
      makeTrade({ exitTime: "2025-01-05T14:30:00Z", pnl: 1000 }),
      makeTrade({ exitTime: "2025-02-15T14:30:00Z", pnl: 2000 }),
    ];
    const tool = createTool(trades);
    const res = (await tool.execute({
      action: "full_report",
      from: "2025-02-01",
      to: "2025-03-01",
    })) as Record<string, unknown>;
    expect(res.totalTrades).toBe(1);
  });

  // ── unknown action ──

  it("returns error for unknown action", async () => {
    const tool = createTool([makeTrade()]);
    const res = (await tool.execute({ action: "unknown" })) as Record<string, unknown>;
    expect(res.error).toContain("δ֪");
  });
});
