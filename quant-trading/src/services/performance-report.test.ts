import { describe, expect, it } from "vitest";
import type { TradeRecord } from "../types.js";
import {
  METRIC_GROUPS,
  PerformanceReportService,
  type PerformanceReportQuery,
} from "./performance-report.js";

function makeTrade(overrides: Partial<TradeRecord> = {}): TradeRecord {
  return {
    tradeId: `t-${Math.random().toString(36).slice(2, 8)}`,
    symbol: "600519",
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

// Helper: build a service that reads from an in-memory trade array
// by using a temp data dir. For unit testing we test computeMetrics
// via the public `generateReport` path, but override loadAndFilterTrades.
function createTestService(trades: TradeRecord[]): PerformanceReportService {
  const svc = new PerformanceReportService({ dataDir: "/tmp/nonexistent-qt-test" });
  // Override loadAndFilterTrades to return pre-filtered trades
  svc.loadAndFilterTrades = async (query: PerformanceReportQuery) => {
    let filtered = trades.filter((t) => t.exitTime != null);
    if (query.strategyIds?.length) {
      const ids = new Set(query.strategyIds);
      filtered = filtered.filter((t) => ids.has(t.strategyId ?? "unknown"));
    }
    if (query.symbols?.length) {
      const syms = new Set(query.symbols);
      filtered = filtered.filter((t) => syms.has(t.symbol));
    }
    if (query.from) {
      filtered = filtered.filter((t) => (t.exitTime ?? "") >= query.from!);
    }
    if (query.to) {
      filtered = filtered.filter((t) => (t.exitTime ?? "").slice(0, 10) <= query.to!);
    }
    return filtered;
  };
  return svc;
}

describe("PerformanceReportService", () => {
  // ── Basic report generation ──

  it("generates report for a single trade", async () => {
    const svc = createTestService([makeTrade()]);
    const report = await svc.generateReport();

    expect(report.totalTradesIncluded).toBe(1);
    expect(report.overallMetrics.totalNetPnl).toBe(9950);
    expect(report.overallMetrics.totalGrossPnl).toBe(10000);
    expect(report.overallMetrics.totalCommission).toBe(50);
    expect(report.overallMetrics.winRate).toBe(1);
    expect(report.overallMetrics.totalWins).toBe(1);
    expect(report.overallMetrics.totalLosses).toBe(0);
  });

  it("returns empty report for no trades", async () => {
    const svc = createTestService([]);
    const report = await svc.generateReport();

    expect(report.totalTradesIncluded).toBe(0);
    expect(report.overallMetrics.totalNetPnl).toBe(0);
    expect(report.overallMetrics.winRate).toBe(0);
    expect(report.periodSummaries).toEqual([]);
    expect(report.byStrategy).toEqual([]);
    expect(report.bySymbol).toEqual([]);
  });

  // ── Win/loss metrics ──

  it("computes win rate and profit factor with mixed results", async () => {
    const trades = [
      makeTrade({ netPnl: 1000, grossPnl: 1050 }),
      makeTrade({ netPnl: -500, grossPnl: -450 }),
      makeTrade({ netPnl: 2000, grossPnl: 2050 }),
    ];
    const svc = createTestService(trades);
    const report = await svc.generateReport();

    expect(report.overallMetrics.totalTrades).toBe(3);
    expect(report.overallMetrics.winRate).toBeCloseTo(2 / 3);
    expect(report.overallMetrics.lossRate).toBeCloseTo(1 / 3);
    expect(report.overallMetrics.avgWin).toBe(1500); // (1000+2000)/2
    expect(report.overallMetrics.avgLoss).toBe(500); // |(-500)|/1
    expect(report.overallMetrics.profitFactor).toBe(6); // 3000/500
  });

  it("returns Infinity profit factor when no losses", async () => {
    const svc = createTestService([makeTrade({ netPnl: 1000, grossPnl: 1050 })]);
    const report = await svc.generateReport();
    expect(report.overallMetrics.profitFactor).toBe(Infinity);
  });

  // ── Payoff ratio & expectancy ──

  it("computes payoff ratio and expectancy", async () => {
    const trades = [
      makeTrade({ netPnl: 2000, grossPnl: 2050 }),
      makeTrade({ netPnl: -1000, grossPnl: -950 }),
    ];
    const svc = createTestService(trades);
    const report = await svc.generateReport();

    expect(report.overallMetrics.payoffRatio).toBe(2); // 2000/1000
    // expectancy = 0.5 * 2000 - 0.5 * 1000 = 500
    expect(report.overallMetrics.expectancy).toBe(500);
  });

  // ── Streak analysis ──

  it("tracks consecutive win/loss streaks", async () => {
    const trades = [
      makeTrade({ netPnl: 100, exitTime: "2025-01-01T10:00:00Z" }),
      makeTrade({ netPnl: 200, exitTime: "2025-01-02T10:00:00Z" }),
      makeTrade({ netPnl: 300, exitTime: "2025-01-03T10:00:00Z" }),
      makeTrade({ netPnl: -100, exitTime: "2025-01-04T10:00:00Z" }),
      makeTrade({ netPnl: -200, exitTime: "2025-01-05T10:00:00Z" }),
    ];
    const svc = createTestService(trades);
    const report = await svc.generateReport();

    expect(report.overallMetrics.maxConsecutiveWins).toBe(3);
    expect(report.overallMetrics.maxConsecutiveLosses).toBe(2);
  });

  // ── Max drawdown ──

  it("computes max drawdown from cumulative PnL", async () => {
    const trades = [
      makeTrade({ netPnl: 1000, exitTime: "2025-01-01T10:00:00Z" }),
      makeTrade({ netPnl: 500, exitTime: "2025-01-02T10:00:00Z" }),
      makeTrade({ netPnl: -2000, exitTime: "2025-01-03T10:00:00Z" }),
      makeTrade({ netPnl: 300, exitTime: "2025-01-04T10:00:00Z" }),
    ];
    const svc = createTestService(trades);
    const report = await svc.generateReport();

    // Cumulative: 1000, 1500 (peak), -500, -200
    // Max DD = 1500 - (-500) = 2000
    expect(report.overallMetrics.maxDrawdown).toBe(2000);
  });

  // ── Return on capital ──

  it("computes return on capital when initialCapital is provided", async () => {
    const svc = createTestService([makeTrade({ netPnl: 5000 })]);
    const report = await svc.generateReport({ initialCapital: 100000 });

    expect(report.overallMetrics.returnOnCapital).toBeCloseTo(5); // 5%
  });

  it("returns NaN for returnOnCapital when no initialCapital", async () => {
    const svc = createTestService([makeTrade({ netPnl: 5000 })]);
    const report = await svc.generateReport();

    expect(report.overallMetrics.returnOnCapital).toBeNaN();
  });

  // ── Holding days ──

  it("computes average holding days by win/loss", async () => {
    const trades = [
      makeTrade({ netPnl: 1000, holdingDays: 3 }),
      makeTrade({ netPnl: 2000, holdingDays: 7 }),
      makeTrade({ netPnl: -500, holdingDays: 2 }),
    ];
    const svc = createTestService(trades);
    const report = await svc.generateReport();

    expect(report.overallMetrics.avgHoldingDays).toBe(4); // (3+7+2)/3
    expect(report.overallMetrics.avgWinHoldingDays).toBe(5); // (3+7)/2
    expect(report.overallMetrics.avgLossHoldingDays).toBe(2);
  });

  // ── Strategy filtering ──

  it("filters by strategy IDs", async () => {
    const trades = [
      makeTrade({ strategyId: "rsi", netPnl: 1000 }),
      makeTrade({ strategyId: "macd", netPnl: 2000 }),
      makeTrade({ strategyId: "rsi", netPnl: -500 }),
    ];
    const svc = createTestService(trades);
    const report = await svc.generateReport({ strategyIds: ["rsi"] });

    expect(report.totalTradesIncluded).toBe(2);
    expect(report.overallMetrics.totalNetPnl).toBe(500);
  });

  // ── Symbol filtering ──

  it("filters by symbols", async () => {
    const trades = [
      makeTrade({ symbol: "600519", netPnl: 1000 }),
      makeTrade({ symbol: "000001", netPnl: 2000 }),
      makeTrade({ symbol: "600519", netPnl: -500 }),
    ];
    const svc = createTestService(trades);
    const report = await svc.generateReport({ symbols: ["600519"] });

    expect(report.totalTradesIncluded).toBe(2);
    expect(report.overallMetrics.totalNetPnl).toBe(500);
  });

  // ── Date range filtering ──

  it("filters by date range", async () => {
    const trades = [
      makeTrade({ exitTime: "2025-01-05T10:00:00Z", netPnl: 100 }),
      makeTrade({ exitTime: "2025-01-15T10:00:00Z", netPnl: 200 }),
      makeTrade({ exitTime: "2025-01-25T10:00:00Z", netPnl: 300 }),
    ];
    const svc = createTestService(trades);
    const report = await svc.generateReport({ from: "2025-01-10", to: "2025-01-20" });

    expect(report.totalTradesIncluded).toBe(1);
    expect(report.overallMetrics.totalNetPnl).toBe(200);
  });

  // ── Period summaries ──

  it("aggregates into daily period summaries", async () => {
    const trades = [
      makeTrade({ exitTime: "2025-01-10T09:30:00Z", netPnl: 100, grossPnl: 150, commission: 50 }),
      makeTrade({
        exitTime: "2025-01-10T14:00:00Z",
        netPnl: -50,
        grossPnl: -30,
        commission: 20,
      }),
      makeTrade({ exitTime: "2025-01-11T10:00:00Z", netPnl: 200, grossPnl: 250, commission: 50 }),
    ];
    const svc = createTestService(trades);
    const report = await svc.generateReport({ aggregationPeriod: "daily" });

    expect(report.periodSummaries).toHaveLength(2);
    expect(report.periodSummaries[0].period).toBe("2025-01-10");
    expect(report.periodSummaries[0].trades).toBe(2);
    expect(report.periodSummaries[0].netPnl).toBe(50);
    expect(report.periodSummaries[0].cumNetPnl).toBe(50);
    expect(report.periodSummaries[1].period).toBe("2025-01-11");
    expect(report.periodSummaries[1].cumNetPnl).toBe(250);
  });

  it("aggregates into monthly period summaries", async () => {
    const trades = [
      makeTrade({ exitTime: "2025-01-10T10:00:00Z", netPnl: 100, grossPnl: 150, commission: 50 }),
      makeTrade({ exitTime: "2025-02-10T10:00:00Z", netPnl: 200, grossPnl: 250, commission: 50 }),
    ];
    const svc = createTestService(trades);
    const report = await svc.generateReport({ aggregationPeriod: "monthly" });

    expect(report.periodSummaries).toHaveLength(2);
    expect(report.periodSummaries[0].period).toBe("2025-01");
    expect(report.periodSummaries[1].period).toBe("2025-02");
  });

  // ── Strategy sections ──

  it("breaks down metrics by strategy", async () => {
    const trades = [
      makeTrade({ strategyId: "rsi", netPnl: 1000, grossPnl: 1050, commission: 50 }),
      makeTrade({ strategyId: "macd", netPnl: -500, grossPnl: -450, commission: 50 }),
      makeTrade({ strategyId: "rsi", netPnl: 500, grossPnl: 550, commission: 50 }),
    ];
    const svc = createTestService(trades);
    const report = await svc.generateReport();

    expect(report.byStrategy).toHaveLength(2);
    // Sorted by |netPnl| desc: rsi (1500) > macd (500)
    expect(report.byStrategy[0].strategyId).toBe("rsi");
    expect(report.byStrategy[0].tradeCount).toBe(2);
    expect(report.byStrategy[0].metrics.totalNetPnl).toBe(1500);
    expect(report.byStrategy[1].strategyId).toBe("macd");
  });

  // ── Symbol sections ──

  it("breaks down metrics by symbol", async () => {
    const trades = [
      makeTrade({ symbol: "600519", netPnl: 2000, grossPnl: 2050, commission: 50 }),
      makeTrade({ symbol: "000001", netPnl: -300, grossPnl: -250, commission: 50 }),
    ];
    const svc = createTestService(trades);
    const report = await svc.generateReport();

    expect(report.bySymbol).toHaveLength(2);
    expect(report.bySymbol[0].symbol).toBe("600519");
    expect(report.bySymbol[0].metrics.totalNetPnl).toBe(2000);
  });

  // ── Trades without exit are excluded ──

  it("excludes trades without exitTime", async () => {
    const trades = [
      makeTrade({ exitTime: "2025-01-15T14:30:00Z", netPnl: 100 }),
      makeTrade({ exitTime: undefined, netPnl: 200 }),
    ];
    const svc = createTestService(trades);
    const report = await svc.generateReport();

    expect(report.totalTradesIncluded).toBe(1);
  });

  // ── Date range in result ──

  it("reports correct date range", async () => {
    const trades = [
      makeTrade({ exitTime: "2025-01-05T10:00:00Z" }),
      makeTrade({ exitTime: "2025-03-20T10:00:00Z" }),
    ];
    const svc = createTestService(trades);
    const report = await svc.generateReport();

    expect(report.dateRange.from).toBe("2025-01-05");
    expect(report.dateRange.to).toBe("2025-03-20");
  });

  // ── METRIC_GROUPS covers all meaningful groups ──

  it("METRIC_GROUPS has expected groups", () => {
    expect(Object.keys(METRIC_GROUPS)).toContain("return");
    expect(Object.keys(METRIC_GROUPS)).toContain("winLoss");
    expect(Object.keys(METRIC_GROUPS)).toContain("trade");
    expect(Object.keys(METRIC_GROUPS)).toContain("risk");
    expect(Object.keys(METRIC_GROUPS)).toContain("holding");
  });
});
