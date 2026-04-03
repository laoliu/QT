import { describe, expect, it } from "vitest";
import type { TradeRecord } from "../types.js";
import { computeAttribution } from "./performance-attribution.js";

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

describe("computeAttribution", () => {
  // ── Basic aggregation ──

  it("computes totals for a single trade", () => {
    const result = computeAttribution([makeTrade()]);
    expect(result.totalTrades).toBe(1);
    expect(result.totalNetPnl).toBe(9950);
    expect(result.totalGrossPnl).toBe(10000);
    expect(result.totalCommission).toBe(50);
    expect(result.overallWinRate).toBe(1);
  });

  it("handles empty trades array", () => {
    const result = computeAttribution([]);
    expect(result.totalTrades).toBe(0);
    expect(result.totalNetPnl).toBe(0);
    expect(result.overallWinRate).toBe(0);
  });

  it("calculates correct win rate with mixed results", () => {
    const trades = [
      makeTrade({ netPnl: 1000 }),
      makeTrade({ netPnl: -500 }),
      makeTrade({ netPnl: 2000 }),
    ];
    const result = computeAttribution(trades);
    expect(result.totalTrades).toBe(3);
    expect(result.overallWinRate).toBeCloseTo(2 / 3);
    expect(result.totalNetPnl).toBe(2500);
  });

  // ── avgWin / avgLoss ──

  it("calculates avgWin and avgLoss correctly", () => {
    const trades = [
      makeTrade({ netPnl: 1000 }),
      makeTrade({ netPnl: 3000 }),
      makeTrade({ netPnl: -500 }),
      makeTrade({ netPnl: -1500 }),
    ];
    const result = computeAttribution(trades);
    expect(result.avgWin).toBe(2000); // (1000+3000)/2
    expect(result.avgLoss).toBe(1000); // (500+1500)/2
  });

  // ── Profit factor ──

  it("calculates profit factor", () => {
    const trades = [makeTrade({ netPnl: 3000 }), makeTrade({ netPnl: -1000 })];
    const result = computeAttribution(trades);
    expect(result.profitFactor).toBe(3); // 3000/1000
  });

  it("returns Infinity when no losses", () => {
    const trades = [makeTrade({ netPnl: 1000 })];
    const result = computeAttribution(trades);
    expect(result.profitFactor).toBe(Infinity);
  });

  // ── By Strategy ──

  it("groups by strategy", () => {
    const trades = [
      makeTrade({ strategyId: "s1", netPnl: 1000 }),
      makeTrade({ strategyId: "s1", netPnl: -200 }),
      makeTrade({ strategyId: "s2", netPnl: 500 }),
    ];
    const result = computeAttribution(trades);
    expect(result.byStrategy).toHaveLength(2);

    const s1 = result.byStrategy.find((s) => s.strategyId === "s1")!;
    expect(s1.trades).toBe(2);
    expect(s1.wins).toBe(1);
    expect(s1.losses).toBe(1);
    expect(s1.netPnl).toBe(800);

    const s2 = result.byStrategy.find((s) => s.strategyId === "s2")!;
    expect(s2.trades).toBe(1);
    expect(s2.netPnl).toBe(500);
  });

  // ── By Symbol ──

  it("groups by symbol with correct metrics", () => {
    const trades = [
      makeTrade({ symbol: "A", netPnl: 1000, holdingDays: 3 }),
      makeTrade({ symbol: "A", netPnl: -200, holdingDays: 1 }),
      makeTrade({ symbol: "B", netPnl: 500, holdingDays: 5 }),
    ];
    const result = computeAttribution(trades);
    expect(result.bySymbol).toHaveLength(2);

    const a = result.bySymbol.find((s) => s.symbol === "A")!;
    expect(a.trades).toBe(2);
    expect(a.netPnl).toBe(800);
    expect(a.avgPnl).toBe(400);
    expect(a.winRate).toBe(0.5);
    expect(a.holdingDaysAvg).toBe(2);
  });

  // ── By Period ──

  it("computes daily P&L with cumulative sum", () => {
    const trades = [
      makeTrade({ exitTime: "2025-01-10T10:00:00Z", netPnl: 1000 }),
      makeTrade({ exitTime: "2025-01-10T14:00:00Z", netPnl: 500 }),
      makeTrade({ exitTime: "2025-01-11T10:00:00Z", netPnl: -300 }),
    ];
    const result = computeAttribution(trades);
    expect(result.byDay).toHaveLength(2);
    expect(result.byDay[0].period).toBe("2025-01-10");
    expect(result.byDay[0].netPnl).toBe(1500);
    expect(result.byDay[0].cumPnl).toBe(1500);
    expect(result.byDay[1].period).toBe("2025-01-11");
    expect(result.byDay[1].netPnl).toBe(-300);
    expect(result.byDay[1].cumPnl).toBe(1200);
  });

  it("computes monthly P&L", () => {
    const trades = [
      makeTrade({ exitTime: "2025-01-15T10:00:00Z", netPnl: 1000 }),
      makeTrade({ exitTime: "2025-02-10T10:00:00Z", netPnl: -500 }),
    ];
    const result = computeAttribution(trades);
    expect(result.byMonth).toHaveLength(2);
    expect(result.byMonth[0].period).toBe("2025-01");
    expect(result.byMonth[1].period).toBe("2025-02");
  });

  // ── Date range filtering ──

  it("filters by from/to date range", () => {
    const trades = [
      makeTrade({ exitTime: "2025-01-05T10:00:00Z", netPnl: 1000 }),
      makeTrade({ exitTime: "2025-01-15T10:00:00Z", netPnl: 2000 }),
      makeTrade({ exitTime: "2025-02-10T10:00:00Z", netPnl: 3000 }),
    ];
    const result = computeAttribution(trades, {
      from: "2025-01-10",
      to: "2025-01-31",
    });
    expect(result.totalTrades).toBe(1);
    expect(result.totalNetPnl).toBe(2000);
    expect(result.dateRange.from).toBe("2025-01-15");
  });

  // ── contribution pct ──

  it("calculates contribution percentages summing to ~100%", () => {
    const trades = [
      makeTrade({ strategyId: "s1", netPnl: 600 }),
      makeTrade({ strategyId: "s2", netPnl: 400 }),
    ];
    const result = computeAttribution(trades);
    const totalContrib = result.byStrategy.reduce((s, a) => s + a.contributionPct, 0);
    expect(totalContrib).toBeCloseTo(100);
  });

  // ── Filters out trades without exitTime ──

  it("ignores open trades (no exitTime)", () => {
    const trades = [
      makeTrade({ exitTime: "2025-01-10T10:00:00Z", netPnl: 1000 }),
      makeTrade({ exitTime: undefined, netPnl: 0 }),
    ];
    const result = computeAttribution(trades);
    expect(result.totalTrades).toBe(1);
  });
});
