import { beforeEach, describe, expect, it } from "vitest";
import type { TradeRecord } from "../types.js";
import type { EquitySnapshot } from "./equity-history.js";
import {
  TradingPerformanceReportService,
} from "./trading-performance-report.js";

// ── Mock filesystem ─────────────────────────────────────────────

import { vi } from "vitest";
import fs from "node:fs/promises";

vi.mock("node:fs/promises", () => ({
  default: {
    readFile: vi.fn(),
    mkdir: vi.fn(),
    writeFile: vi.fn(),
  },
}));

const mockedFs = vi.mocked(fs);

// ── Test helpers ────────────────────────────────────────────────

function makeTrade(overrides: Partial<TradeRecord> = {}): TradeRecord {
  return {
    tradeId: `t-${Math.random().toString(36).slice(2, 8)}`,
    symbol: "600519",
    name: "贵州茅台",
    market: "cn_stock",
    side: "sell",
    entryTime: "2025-01-10T09:30:00Z",
    exitTime: "2025-01-15T14:30:00Z",
    entryPrice: 1800,
    exitPrice: 1900,
    quantity: 100,
    grossPnl: 10000,
    commission: 50,
    netPnl: 9950,
    holdingDays: 5,
    strategyId: "dual-ma",
    entryOrderId: "o-1",
    exitOrderId: "o-2",
    ...overrides,
  };
}

function makeEquitySnap(overrides: Partial<EquitySnapshot> = {}): EquitySnapshot {
  return {
    date: "2025-01-10",
    equity: 1000000,
    cash: 500000,
    marketValue: 500000,
    dailyPnl: 0,
    positionCount: 3,
    ...overrides,
  };
}

function setupMockData(trades: TradeRecord[], equity: EquitySnapshot[]) {
  mockedFs.readFile.mockImplementation((filePath: unknown) => {
    const p = String(filePath);
    if (p.includes("trades-")) {
      return Promise.resolve(JSON.stringify(trades));
    }
    if (p.includes("equity-history-")) {
      return Promise.resolve(JSON.stringify(equity));
    }
    return Promise.reject(new Error("not found"));
  });
}

// ── Tests ───────────────────────────────────────────────────────

describe("TradingPerformanceReportService", () => {
  const config = { dataDir: "/tmp/test-data", tradingAccount: { mode: "paper" as const } };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("generates empty report when no data exists", async () => {
    mockedFs.readFile.mockRejectedValue(new Error("ENOENT"));

    const svc = new TradingPerformanceReportService(config);
    const report = await svc.generateReport();

    expect(report.totalTradesAnalyzed).toBe(0);
    expect(report.totalEquitySnapshots).toBe(0);
    expect(report.metrics.length).toBeGreaterThan(0);
    expect(report.byStrategy).toHaveLength(0);
    expect(report.bySymbol).toHaveLength(0);
    expect(report.byMonth).toHaveLength(0);
  });

  it("computes trade-level metrics correctly", async () => {
    const trades = [
      makeTrade({ netPnl: 1000, grossPnl: 1050, commission: 50, strategyId: "s1" }),
      makeTrade({ netPnl: -500, grossPnl: -450, commission: 50, strategyId: "s1" }),
      makeTrade({ netPnl: 2000, grossPnl: 2050, commission: 50, strategyId: "s2" }),
    ];
    setupMockData(trades, []);

    const svc = new TradingPerformanceReportService(config);
    const report = await svc.generateReport();

    expect(report.totalTradesAnalyzed).toBe(3);

    const netPnl = report.metrics.find((m) => m.id === "net_pnl");
    expect(netPnl).toBeDefined();
    expect(netPnl!.value).toBe(2500);

    const winRate = report.metrics.find((m) => m.id === "win_rate");
    expect(winRate).toBeDefined();
    expect(winRate!.value).toBeCloseTo(66.67, 0);

    const totalTrades = report.metrics.find((m) => m.id === "total_trades");
    expect(totalTrades!.value).toBe(3);
  });

  it("aggregates by strategy", async () => {
    const trades = [
      makeTrade({ strategyId: "s1", netPnl: 1000, grossPnl: 1050, commission: 50 }),
      makeTrade({ strategyId: "s1", netPnl: -200, grossPnl: -150, commission: 50 }),
      makeTrade({ strategyId: "s2", netPnl: 500, grossPnl: 550, commission: 50 }),
    ];
    setupMockData(trades, []);

    const svc = new TradingPerformanceReportService(config);
    const report = await svc.generateReport();

    expect(report.byStrategy).toHaveLength(2);
    const s1 = report.byStrategy.find((s) => s.key === "s1")!;
    expect(s1.trades).toBe(2);
    expect(s1.netPnl).toBe(800);
    expect(s1.wins).toBe(1);
    expect(s1.losses).toBe(1);
  });

  it("aggregates by symbol", async () => {
    const trades = [
      makeTrade({ symbol: "600519", netPnl: 1000, grossPnl: 1050, commission: 50 }),
      makeTrade({ symbol: "000858", netPnl: 500, grossPnl: 550, commission: 50 }),
    ];
    setupMockData(trades, []);

    const svc = new TradingPerformanceReportService(config);
    const report = await svc.generateReport();

    expect(report.bySymbol).toHaveLength(2);
    expect(report.bySymbol[0]!.key).toBe("600519");
    expect(report.bySymbol[0]!.netPnl).toBe(1000);
  });

  it("aggregates by month", async () => {
    const trades = [
      makeTrade({ exitTime: "2025-01-15T10:00:00Z", netPnl: 1000, grossPnl: 1050, commission: 50 }),
      makeTrade({ exitTime: "2025-02-10T10:00:00Z", netPnl: -500, grossPnl: -450, commission: 50 }),
    ];
    setupMockData(trades, []);

    const svc = new TradingPerformanceReportService(config);
    const report = await svc.generateReport();

    expect(report.byMonth).toHaveLength(2);
    expect(report.byMonth[0]!.key).toBe("2025-01");
    expect(report.byMonth[1]!.key).toBe("2025-02");
  });

  it("filters by strategy IDs", async () => {
    const trades = [
      makeTrade({ strategyId: "s1", netPnl: 1000, grossPnl: 1050, commission: 50 }),
      makeTrade({ strategyId: "s2", netPnl: 500, grossPnl: 550, commission: 50 }),
    ];
    setupMockData(trades, []);

    const svc = new TradingPerformanceReportService(config);
    const report = await svc.generateReport({ strategyIds: ["s1"] });

    expect(report.totalTradesAnalyzed).toBe(1);
    expect(report.byStrategy).toHaveLength(1);
    expect(report.byStrategy[0]!.key).toBe("s1");
  });

  it("filters by symbols", async () => {
    const trades = [
      makeTrade({ symbol: "600519", netPnl: 1000, grossPnl: 1050, commission: 50 }),
      makeTrade({ symbol: "000858", netPnl: 500, grossPnl: 550, commission: 50 }),
    ];
    setupMockData(trades, []);

    const svc = new TradingPerformanceReportService(config);
    const report = await svc.generateReport({ symbols: ["600519"] });

    expect(report.totalTradesAnalyzed).toBe(1);
    expect(report.metrics.find((m) => m.id === "net_pnl")!.value).toBe(1000);
  });

  it("filters by date range", async () => {
    const trades = [
      makeTrade({ exitTime: "2025-01-05T10:00:00Z", netPnl: 100, grossPnl: 150, commission: 50 }),
      makeTrade({ exitTime: "2025-01-15T10:00:00Z", netPnl: 200, grossPnl: 250, commission: 50 }),
      makeTrade({ exitTime: "2025-02-10T10:00:00Z", netPnl: 300, grossPnl: 350, commission: 50 }),
    ];
    setupMockData(trades, []);

    const svc = new TradingPerformanceReportService(config);
    const report = await svc.generateReport({ startDate: "2025-01-10", endDate: "2025-01-31" });

    expect(report.totalTradesAnalyzed).toBe(1);
    expect(report.metrics.find((m) => m.id === "net_pnl")!.value).toBe(200);
  });

  it("computes equity-curve metrics from snapshots", async () => {
    const equity: EquitySnapshot[] = [
      makeEquitySnap({ date: "2025-01-01", equity: 1000000, dailyPnl: 0 }),
      makeEquitySnap({ date: "2025-01-02", equity: 1010000, dailyPnl: 10000 }),
      makeEquitySnap({ date: "2025-01-03", equity: 1005000, dailyPnl: -5000 }),
      makeEquitySnap({ date: "2025-01-06", equity: 1020000, dailyPnl: 15000 }),
      makeEquitySnap({ date: "2025-01-07", equity: 1015000, dailyPnl: -5000 }),
    ];
    setupMockData([], equity);

    const svc = new TradingPerformanceReportService(config);
    const report = await svc.generateReport();

    expect(report.totalEquitySnapshots).toBe(5);
    expect(report.equityCurve).toHaveLength(5);

    const totalReturn = report.metrics.find((m) => m.id === "total_return");
    expect(totalReturn).toBeDefined();
    expect(totalReturn!.value).toBeCloseTo(1.5, 0);

    const maxDdPct = report.metrics.find((m) => m.id === "max_drawdown_pct");
    expect(maxDdPct).toBeDefined();
    expect(maxDdPct!.value).toBeGreaterThan(0);
  });

  it("generates monthly returns from equity snapshots", async () => {
    const equity: EquitySnapshot[] = [
      makeEquitySnap({ date: "2025-01-02", equity: 1000000 }),
      makeEquitySnap({ date: "2025-01-31", equity: 1050000 }),
      makeEquitySnap({ date: "2025-02-03", equity: 1050000 }),
      makeEquitySnap({ date: "2025-02-28", equity: 1100000 }),
    ];
    setupMockData([], equity);

    const svc = new TradingPerformanceReportService(config);
    const report = await svc.generateReport();

    expect(report.monthlyReturns).toHaveLength(2);
    expect(report.monthlyReturns[0]!.month).toBe("2025-01");
    expect(report.monthlyReturns[0]!.returnPct).toBeCloseTo(5.0, 0);
  });

  it("formats metric values correctly", async () => {
    const trades = [makeTrade({ netPnl: 12345.67, grossPnl: 12400, commission: 54.33 })];
    setupMockData(trades, []);

    const svc = new TradingPerformanceReportService(config);
    const report = await svc.generateReport();

    const netPnl = report.metrics.find((m) => m.id === "net_pnl");
    expect(netPnl!.formatted).toBe("¥12345.67");
    expect(netPnl!.unit).toBe("currency");

    const winRate = report.metrics.find((m) => m.id === "win_rate");
    expect(winRate!.unit).toBe("pct");
    expect(winRate!.formatted).toContain("%");
  });

  it("getQuickSummary returns key metrics only", async () => {
    const trades = [makeTrade()];
    setupMockData(trades, []);

    const svc = new TradingPerformanceReportService(config);
    const summary = await svc.getQuickSummary();

    expect(summary.metrics.length).toBeLessThanOrEqual(8);
    const ids = summary.metrics.map((m) => m.id);
    expect(ids).toContain("net_pnl");
    expect(ids).toContain("win_rate");
    expect(ids).toContain("sharpe_ratio");
  });

  it("listStrategies returns unique sorted strategy IDs", async () => {
    const trades = [
      makeTrade({ strategyId: "b-strat" }),
      makeTrade({ strategyId: "a-strat" }),
      makeTrade({ strategyId: "b-strat" }),
    ];
    setupMockData(trades, []);

    const svc = new TradingPerformanceReportService(config);
    const strategies = await svc.listStrategies();

    expect(strategies).toEqual(["a-strat", "b-strat"]);
  });

  it("listSymbols returns unique sorted symbols", async () => {
    const trades = [
      makeTrade({ symbol: "000858" }),
      makeTrade({ symbol: "600519" }),
      makeTrade({ symbol: "000858" }),
    ];
    setupMockData(trades, []);

    const svc = new TradingPerformanceReportService(config);
    const symbols = await svc.listSymbols();

    expect(symbols).toEqual(["000858", "600519"]);
  });

  it("getAvailableDateRange returns correct range", async () => {
    const trades = [
      makeTrade({ exitTime: "2025-01-15T10:00:00Z" }),
      makeTrade({ exitTime: "2025-03-20T14:00:00Z" }),
    ];
    const equity = [
      makeEquitySnap({ date: "2025-01-10" }),
      makeEquitySnap({ date: "2025-04-01" }),
    ];
    setupMockData(trades, equity);

    const svc = new TradingPerformanceReportService(config);
    const range = await svc.getAvailableDateRange();

    expect(range).toEqual({ from: "2025-01-10", to: "2025-04-01" });
  });

  it("getAvailableDateRange returns null when no data", async () => {
    mockedFs.readFile.mockRejectedValue(new Error("ENOENT"));

    const svc = new TradingPerformanceReportService(config);
    const range = await svc.getAvailableDateRange();

    expect(range).toBeNull();
  });

  it("respects mode parameter (live vs paper)", async () => {
    setupMockData([], []);

    const svc = new TradingPerformanceReportService(config);
    await svc.generateReport({ mode: "live" });

    // Verify it tried to read live files
    const calls = mockedFs.readFile.mock.calls.map((c: unknown[]) => String(c[0]));
    expect(calls.some((p: string) => p.includes("trades-live.json"))).toBe(true);
    expect(calls.some((p: string) => p.includes("equity-history-live.json"))).toBe(true);
  });

  it("ignores open trades (no exitTime)", async () => {
    const trades = [
      makeTrade({ exitTime: "2025-01-15T10:00:00Z", netPnl: 1000, grossPnl: 1050, commission: 50 }),
      makeTrade({ exitTime: undefined, netPnl: 0, grossPnl: 0, commission: 0 }),
    ];
    setupMockData(trades, []);

    const svc = new TradingPerformanceReportService(config);
    const report = await svc.generateReport();

    expect(report.totalTradesAnalyzed).toBe(1);
  });

  it("filters metrics by query.metrics", async () => {
    setupMockData([makeTrade()], []);

    const svc = new TradingPerformanceReportService(config);
    const report = await svc.generateReport({ metrics: ["net_pnl", "win_rate"] });

    expect(report.metrics).toHaveLength(2);
    expect(report.metrics.map((m) => m.id)).toEqual(["net_pnl", "win_rate"]);
  });
});
