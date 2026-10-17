import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EquitySnapshot } from "../services/equity-history.js";
import type { TradeRecord } from "../types.js";
import { createTradingPerformanceReportTool } from "./trading-performance-report.js";

const fakeApi = { logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } } as never;

function makeTrade(overrides: Record<string, unknown> = {}): TradeRecord {
  return {
    tradeId: `t-${Math.random().toString(36).slice(2, 8)}`,
    symbol: "600519",
    name: "06",
    market: "cn_stock",
    side: "buy",
    entryPrice: 1800,
    exitPrice: 1850,
    quantity: 100,
    grossPnl: 5000,
    commission: 10,
    netPnl: 4990,
    holdingDays: 3,
    strategyId: "ma_cross",
    entryTime: "2025-01-10T09:30:00Z",
    exitTime: "2025-01-13T14:30:00Z",
    entryOrderId: "o1",
    exitOrderId: "o2",
    ...overrides,
  } as unknown as TradeRecord;
}

function makeSnapshot(overrides: Partial<EquitySnapshot> = {}): EquitySnapshot {
  return {
    date: "2025-01-10",
    equity: 1000000,
    cash: 500000,
    marketValue: 500000,
    dailyPnl: 5000,
    positionCount: 3,
    ...overrides,
  };
}

describe("trading_performance_report tool", () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "tpr-tool-"));
  });

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  function createTool(trades: TradeRecord[], snapshots: EquitySnapshot[] = []) {
    fs.writeFileSync(path.join(tmpDir, "trades.json"), JSON.stringify(trades));
    fs.writeFileSync(path.join(tmpDir, "equity-history.json"), JSON.stringify(snapshots));
    return createTradingPerformanceReportTool(fakeApi, { dataDir: tmpDir } as never);
  }

  // ── no data ──

  it("returns error when no data exists", async () => {
    const tool = createTool([], []);
    const res = (await tool.execute({ action: "full_report" })) as Record<string, unknown>;
    expect(res.error).toContain("暂无交易数据");
  });

  // ── full_report ──

  it("full_report returns comprehensive data", async () => {
    const trades = [
      makeTrade({ netPnl: 5000, holdingDays: 2 }),
      makeTrade({ netPnl: -2000, holdingDays: 8, exitTime: "2025-01-14T14:30:00Z" }),
      makeTrade({ netPnl: 3000, holdingDays: 25, exitTime: "2025-01-15T14:30:00Z" }),
    ];
    const snapshots = [
      makeSnapshot({ date: "2025-01-10", equity: 1000000, dailyPnl: 5000 }),
      makeSnapshot({ date: "2025-01-13", equity: 1005000, dailyPnl: -2000 }),
      makeSnapshot({ date: "2025-01-14", equity: 1003000, dailyPnl: 3000 }),
    ];
    const tool = createTool(trades, snapshots);
    const res = (await tool.execute({ action: "full_report" })) as Record<string, unknown>;

    expect(res.reportDate).toBeDefined();
    expect(res.equitySummary).toBeDefined();
    expect(res.tradeStats).toBeDefined();
    expect(res.streaks).toBeDefined();
    expect(res.capitalUtilization).toBeDefined();
    expect(res.holdingAnalysis).toBeDefined();
    expect(res.bestTrades).toBeDefined();
    expect(res.worstTrades).toBeDefined();
  });

  // ── equity_summary ──

  it("equity_summary returns equity curve stats", async () => {
    const snapshots = [
      makeSnapshot({ date: "2025-01-10", equity: 1000000, dailyPnl: 0 }),
      makeSnapshot({ date: "2025-01-11", equity: 1005000, dailyPnl: 5000 }),
      makeSnapshot({ date: "2025-01-12", equity: 1002000, dailyPnl: -3000 }),
    ];
    const tool = createTool([], snapshots);
    const res = (await tool.execute({ action: "equity_summary" })) as Record<string, unknown>;

    expect(res.totalDays).toBe(3);
    expect(res.winDays).toBe(1);
    expect(res.lossDays).toBe(1);
    expect(res.bestDay).toBeDefined();
    expect(res.worstDay).toBeDefined();
  });

  // ── streak_analysis ──

  it("streak_analysis computes win/loss streaks", async () => {
    const trades = [
      makeTrade({ netPnl: 1000, exitTime: "2025-01-10T14:30:00Z" }),
      makeTrade({ netPnl: 2000, exitTime: "2025-01-11T14:30:00Z" }),
      makeTrade({ netPnl: 500, exitTime: "2025-01-12T14:30:00Z" }),
      makeTrade({ netPnl: -1000, exitTime: "2025-01-13T14:30:00Z" }),
      makeTrade({ netPnl: -500, exitTime: "2025-01-14T14:30:00Z" }),
    ];
    const tool = createTool(trades);
    const res = (await tool.execute({ action: "streak_analysis" })) as Record<string, unknown>;

    expect(res.maxWinStreak).toBe(3);
    expect(res.maxLossStreak).toBe(2);
    expect(res.currentStreakType).toBe("连亏");
    expect(res.currentStreak).toBe(2);
  });

  // ── holding_analysis ──

  it("holding_analysis classifies by holding period", async () => {
    const trades = [
      makeTrade({ netPnl: 1000, holdingDays: 2 }),
      makeTrade({ netPnl: 2000, holdingDays: 4 }),
      makeTrade({ netPnl: -500, holdingDays: 10 }),
      makeTrade({ netPnl: 3000, holdingDays: 30 }),
    ];
    const tool = createTool(trades);
    const res = (await tool.execute({ action: "holding_analysis" })) as Record<string, unknown>;

    const shortTerm = res.shortTerm as Record<string, unknown>;
    const mediumTerm = res.mediumTerm as Record<string, unknown>;
    const longTerm = res.longTerm as Record<string, unknown>;

    expect(shortTerm.count).toBe(2);
    expect(mediumTerm.count).toBe(1);
    expect(longTerm.count).toBe(1);
  });

  // ── capital_utilization ──

  it("capital_utilization computes usage stats", async () => {
    const snapshots = [
      makeSnapshot({ equity: 1000000, marketValue: 850000 }),
      makeSnapshot({ date: "2025-01-11", equity: 1000000, marketValue: 100000 }),
      makeSnapshot({ date: "2025-01-12", equity: 1000000, marketValue: 900000 }),
    ];
    const tool = createTool([], snapshots);
    const res = (await tool.execute({ action: "capital_utilization" })) as Record<string, unknown>;

    expect(res.highUtilizationDays).toBe(2);
    expect(res.lowUtilizationDays).toBe(1);
  });

  // ── best_worst ──

  it("best_worst returns top and bottom trades", async () => {
    const trades = [
      makeTrade({ netPnl: 10000, symbol: "600519" }),
      makeTrade({ netPnl: -5000, symbol: "000858" }),
      makeTrade({ netPnl: 3000, symbol: "300750" }),
    ];
    const tool = createTool(trades);
    const res = (await tool.execute({ action: "best_worst", top_n: 2 })) as Record<
      string,
      unknown
    >;

    const best = res.bestTrades as Array<Record<string, unknown>>;
    const worst = res.worstTrades as Array<Record<string, unknown>>;

    expect(best.length).toBe(2);
    expect(worst.length).toBe(2);
    expect(best[0]!.symbol).toBe("600519");
  });

  // ── date filter ──

  it("full_report respects from/to filter", async () => {
    const trades = [
      makeTrade({ exitTime: "2025-01-05T14:30:00Z", netPnl: 1000 }),
      makeTrade({ exitTime: "2025-02-15T14:30:00Z", netPnl: 2000 }),
    ];
    const tool = createTool(trades);
    const res = (await tool.execute({
      action: "full_report",
      from: "2025-02-01",
      to: "2025-03-01",
    })) as Record<string, unknown>;
    const stats = res.tradeStats as Record<string, unknown>;
    expect(stats.totalTrades).toBe(1);
  });

  // ── unknown action ──

  it("returns error for unknown action", async () => {
    const tool = createTool([makeTrade()]);
    const res = (await tool.execute({ action: "unknown" })) as Record<string, unknown>;
    expect(res.error).toContain("ā04");
  });
});
