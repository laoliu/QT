/**
 * Tests for PerformanceReportService 66trading performance analysis.
 *
 * Covers:
 *  - FR-4.1: Core metrics (return, drawdown, Sharpe, Sortino, win rate, profit factor)
 *  - FR-4.2: Period returns (day/week/month)
 *  - FR-4.3: Attribution (sector/symbol/strategy)
 *  - FR-4.4: Trade behavior stats (avg holding days, max profit/loss)
 *  - FR-4.5: Benchmark comparison
 *  - AC-4: HTML report generation
 *  - NFR-2: Report generation <10s
 */
import { describe, expect, it } from "vitest";
import {
  PerformanceReportService,
  calcMaxDrawdown,
  calcSharpeRatio,
  calcSortinoRatio,
  type ReportDataSource,
} from "./performance-report.js";
import type { TradeRecord } from "../types.js";

// ── Mock data ───────────────────────────────────────────────

function generateEquityCurve(days: number, startEquity = 100000, dailyReturnMean = 0.001, dailyReturnStd = 0.02): { date: string; equity: number }[] {
  const curve: { date: string; equity: number }[] = [];
  let equity = startEquity;
  const start = new Date("2026-01-01");

  for (let i = 0; i < days; i++) {
    const d = new Date(start);
    d.setDate(d.getDate() + i);
    curve.push({ date: d.toISOString().slice(0, 10), equity });
    const dailyReturn = dailyReturnMean + dailyReturnStd * (Math.random() - 0.5) * 2;
    equity *= 1 + dailyReturn;
  }
  return curve;
}

const SAMPLE_TRADES: TradeRecord[] = [
  { tradeId: "T1", symbol: "600519", name: "06", market: "cn_stock", side: "buy", entryTime: "2026-01-05", exitTime: "2026-01-20", entryPrice: 1800, exitPrice: 1900, quantity: 100, grossPnl: 10000, commission: 95, netPnl: 9905, holdingDays: 15, strategyId: "macd", entryOrderId: "O1", exitOrderId: "O2" },
  { tradeId: "T2", symbol: "000858", name: "五粮", market: "cn_stock", side: "buy", entryTime: "2026-01-10", exitTime: "2026-01-25", entryPrice: 130, exitPrice: 125, quantity: 200, grossPnl: -1000, commission: 32.5, netPnl: -1032.5, holdingDays: 15, strategyId: "macd", entryOrderId: "O3", exitOrderId: "O4" },
  { tradeId: "T3", symbol: "601318", name: "1309", market: "cn_stock", side: "buy", entryTime: "2026-02-01", exitTime: "2026-02-15", entryPrice: 50, exitPrice: 55, quantity: 500, grossPnl: 2500, commission: 26.25, netPnl: 2473.75, holdingDays: 14, strategyId: "bollinger", entryOrderId: "O5", exitOrderId: "O6" },
  { tradeId: "T4", symbol: "600519", name: "06", market: "cn_stock", side: "buy", entryTime: "2026-02-20", exitTime: "2026-03-05", entryPrice: 1850, exitPrice: 1950, quantity: 100, grossPnl: 10000, commission: 97.5, netPnl: 9902.5, holdingDays: 13, strategyId: "macd", entryOrderId: "O7", exitOrderId: "O8" },
  { tradeId: "T5", symbol: "000001", name: "平安银行", market: "cn_stock", side: "buy", entryTime: "2026-03-01", exitTime: "2026-03-10", entryPrice: 11, exitPrice: 10.5, quantity: 1000, grossPnl: -500, commission: 5.25, netPnl: -505.25, holdingDays: 9, strategyId: "bollinger", entryOrderId: "O9", exitOrderId: "O10" },
];

const EQUITY_CURVE = generateEquityCurve(90);

function mockReportDataSource(overrides?: Partial<ReportDataSource>): ReportDataSource {
  return {
    getTrades: async () => SAMPLE_TRADES,
    getEquityCurve: async () => EQUITY_CURVE,
    getBenchmarkCurve: async (_benchmark, _from, _to) => {
      return generateEquityCurve(90, 100000, 0.0005, 0.015).map(p => ({ date: p.date, value: p.equity }));
    },
    getSectorForSymbol: (symbol) => {
      const map: Record<string, string> = {
        "600519" : "消费", "000858" : "消费", "601318": "金融", "000001": "金融",
      };
      return map[symbol] ?? "其他";
    },
    ...overrides,
  };
}

// ── Calculation helper tests ────────────────────────────────

describe("calcMaxDrawdown()", () => {
  it("returns 0 for empty curve", () => {
    expect(calcMaxDrawdown([]).maxDrawdown).toBe(0);
  });

  it("calculates correct drawdown for monotonically increasing", () => {
    const curve = [
      { date: "2026-01-01", equity: 100 },
      { date: "2026-01-02", equity: 110 },
      { date: "2026-01-03", equity: 120 },
    ];
    expect(calcMaxDrawdown(curve).maxDrawdown).toBe(0);
  });

  it("calculates correct drawdown for a dip", () => {
    const curve = [
      { date: "2026-01-01", equity: 100 },
      { date: "2026-01-02", equity: 120 },
      { date: "2026-01-03", equity: 90 },  // 25% drawdown from peak 120
      { date: "2026-01-04", equity: 110 },
    ];
    const { maxDrawdown } = calcMaxDrawdown(curve);
    expect(maxDrawdown).toBeCloseTo(0.25, 4); // (120-90)/120
  });
});

describe("calcSharpeRatio()", () => {
  it("returns 0 for insufficient data", () => {
    expect(calcSharpeRatio([])).toBe(0);
    expect(calcSharpeRatio([0.01])).toBe(0);
  });

  it("returns positive for consistently positive returns", () => {
    const returns = Array.from({ length: 252 }, () => 0.001);
    expect(calcSharpeRatio(returns)).toBeGreaterThan(0);
  });

  it("returns 0 for zero-variance returns", () => {
    const rf = 0.03 / 252; // daily risk-free
    const returns = Array.from({ length: 252 }, () => rf);
    expect(calcSharpeRatio(returns)).toBeCloseTo(0, 1);
  });
});

describe("calcSortinoRatio()", () => {
  it("returns 0 for insufficient data", () => {
    expect(calcSortinoRatio([])).toBe(0);
  });

  it("returns high value when no downside", () => {
    const returns = Array.from({ length: 100 }, () => 0.005); // all positive
    // Should be Infinity or very high
    const sortino = calcSortinoRatio(returns);
    expect(sortino).toBe(Infinity);
  });
});

// ── PerformanceReportService tests ──────────────────────────

describe("PerformanceReportService", () => {
  it("generates report with all core metrics", async () => {
    const svc = new PerformanceReportService(mockReportDataSource());
    const report = await svc.generate({
      dateFrom: "2026-01-01",
      dateTo: "2026-03-31",
    });

    // Meta
    expect(report.meta.dateFrom).toBe("2026-01-01");
    expect(report.meta.dateTo).toBe("2026-03-31");
    expect(report.meta.generatedAt).toBeDefined();

    // Summary metrics exist and are reasonable
    expect(report.summary.totalTrades).toBe(5);
    expect(report.summary.winRate).toBeCloseTo(3 / 5, 2); // 3 wins out of 5
    expect(report.summary.profitFactor).toBeGreaterThan(0);
    expect(report.summary.maxDrawdown).toBeGreaterThanOrEqual(0);
    expect(report.summary.totalCommission).toBeGreaterThan(0);
    expect(report.summary.avgHoldingDays).toBeGreaterThan(0);
  });

  it("calculates win rate correctly", async () => {
    const svc = new PerformanceReportService(mockReportDataSource());
    const report = await svc.generate({
      dateFrom: "2026-01-01",
      dateTo: "2026-03-31",
    });
    // 3 wins (T1, T3, T4), 2 losses (T2, T5)
    expect(report.summary.winRate).toBeCloseTo(0.6, 2);
  });

  it("computes symbol attribution correctly", async () => {
    const svc = new PerformanceReportService(mockReportDataSource());
    const report = await svc.generate({
      dateFrom: "2026-01-01",
      dateTo: "2026-03-31",
    });

    // 600519 should be the largest positive contributor
    const moutai = report.attribution.bySymbol.find((a) => a.symbol === "600519");
    expect(moutai).toBeDefined();
    expect(moutai!.trades).toBe(2);
    expect(moutai!.contribution).toBeGreaterThan(0);

    // Sum of all symbol contributions should be ~1 (or -1 if net loss)
    const totalContrib = report.attribution.bySymbol.reduce((s, a) => s + a.contribution, 0);
    expect(Math.abs(totalContrib)).toBeCloseTo(1, 1);
  });

  it("computes strategy attribution correctly", async () => {
    const svc = new PerformanceReportService(mockReportDataSource());
    const report = await svc.generate({
      dateFrom: "2026-01-01",
      dateTo: "2026-03-31",
    });

    const macd = report.attribution.byStrategy.find((a) => a.strategyId === "macd");
    const bollinger = report.attribution.byStrategy.find((a) => a.strategyId === "bollinger");
    expect(macd).toBeDefined();
    expect(bollinger).toBeDefined();
    expect(macd!.trades).toBe(3); // T1, T2, T4
    expect(bollinger!.trades).toBe(2); // T3, T5
  });

  it("computes sector attribution", async () => {
    const svc = new PerformanceReportService(mockReportDataSource());
    const report = await svc.generate({
      dateFrom: "2026-01-01",
      dateTo: "2026-03-31",
    });

    expect(report.attribution.bySector.length).toBeGreaterThan(0);
    const consumer = report.attribution.bySector.find((a) => a.sector === "消费");
    expect(consumer).toBeDefined();
  });

  it("generates period returns by month", async () => {
    const svc = new PerformanceReportService(mockReportDataSource());
    const report = await svc.generate({
      dateFrom: "2026-01-01",
      dateTo: "2026-03-31",
      groupBy: "month",
    });

    expect(report.periodReturns.length).toBeGreaterThan(0);
    const jan = report.periodReturns.find((p) => p.period === "2026-01");
    expect(jan).toBeDefined();
    expect(jan!.trades).toBeGreaterThan(0);
  });

  it("includes equity and drawdown curves", async () => {
    const svc = new PerformanceReportService(mockReportDataSource());
    const report = await svc.generate({
      dateFrom: "2026-01-01",
      dateTo: "2026-03-31",
    });

    expect(report.equityCurve.length).toBe(90);
    expect(report.drawdownCurve.length).toBe(90);
    // Drawdown should be between 0 and 1
    for (const dd of report.drawdownCurve) {
      expect(dd.drawdown).toBeGreaterThanOrEqual(0);
      expect(dd.drawdown).toBeLessThanOrEqual(1);
    }
  });

  it("includes benchmark curve when requested", async () => {
    const svc = new PerformanceReportService(mockReportDataSource());
    const report = await svc.generate({
      dateFrom: "2026-01-01",
      dateTo: "2026-03-31",
      benchmark: "000300.SH",
    });

    expect(report.benchmarkCurve).toBeDefined();
    expect(report.benchmarkCurve!.length).toBeGreaterThan(0);
  });

  it("handles empty trades gracefully", async () => {
    const svc = new PerformanceReportService(
      mockReportDataSource({ getTrades: async () => [] }),
    );
    const report = await svc.generate({
      dateFrom: "2026-01-01",
      dateTo: "2026-03-31",
    });

    expect(report.summary.totalTrades).toBe(0);
    expect(report.summary.winRate).toBe(0);
    expect(report.summary.profitFactor).toBe(0);
  });

  it("renders valid HTML report (AC-4)", async () => {
    const svc = new PerformanceReportService(mockReportDataSource());
    const report = await svc.generate({
      dateFrom: "2026-01-01",
      dateTo: "2026-03-31",
    });

    const html = svc.renderHtml(report);
    expect(html).toContain("<!DOCTYPE html>");
    expect(html).toContain("Performance Report");
    expect(html).toContain("Total Return");
    expect(html).toContain("Sharpe Ratio");
    expect(html).toContain("Win Rate");
    expect(html).toContain("600519"); // symbol in attribution
    expect(html).toContain("macd"); // strategy in attribution
  });

  it("generates report within performance budget (NFR-2: <10s)", async () => {
    // Generate 1 year of data
    const yearCurve = generateEquityCurve(365);
    const manyTrades: TradeRecord[] = Array.from({ length: 200 }, (_, i) => ({
      tradeId: `T-${i}`,
      symbol: `${600000 + (i % 50)}`,
      name: `07${i % 50}`,
      market: "cn_stock" as const,
      side: "buy" as const,
      entryTime: `2026-${String(Math.floor(i / 20) + 1).padStart(2, "0")}-${String((i % 28) + 1).padStart(2, "0")}`,
      exitTime: `2026-${String(Math.floor(i / 20) + 1).padStart(2, "0")}-${String(Math.min((i % 28) + 10, 28)).padStart(2, "0")}`,
      entryPrice: 10 + Math.random() * 90,
      exitPrice: 10 + Math.random() * 90,
      quantity: 100,
      grossPnl: Math.random() * 2000 - 1000,
      commission: 10,
      netPnl: Math.random() * 2000 - 1010,
      holdingDays: Math.floor(Math.random() * 30) + 1,
      strategyId: ["macd", "bollinger", "rsi"][i % 3],
      entryOrderId: `OE-${i}`,
      exitOrderId: `OX-${i}`,
    }));

    const svc = new PerformanceReportService(
      mockReportDataSource({
        getTrades: async () => manyTrades,
        getEquityCurve: async () => yearCurve,
      }),
    );

    const start = Date.now();
    const report = await svc.generate({
      dateFrom: "2026-01-01",
      dateTo: "2026-12-31",
    });
    const elapsed = Date.now() - start;

    expect(report.summary.totalTrades).toBe(200);
    expect(elapsed).toBeLessThan(10000); // NFR-2
  });
});
