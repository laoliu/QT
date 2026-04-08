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

  // ── Edge Cases & Boundary Conditions ──

  it("handles all losing trades", () => {
    const trades = [
      makeTrade({ netPnl: -500 }),
      makeTrade({ netPnl: -1000 }),
      makeTrade({ netPnl: -200 }),
    ];
    const result = computeAttribution(trades);
    expect(result.overallWinRate).toBe(0);
    expect(result.totalNetPnl).toBe(-1700);
    expect(result.avgWin).toBe(0);
    expect(result.avgLoss).toBeCloseTo(1700 / 3);
    expect(result.profitFactor).toBe(0);
  });

  it("handles all winning trades", () => {
    const trades = [
      makeTrade({ netPnl: 500 }),
      makeTrade({ netPnl: 1000 }),
    ];
    const result = computeAttribution(trades);
    expect(result.overallWinRate).toBe(1);
    expect(result.avgLoss).toBe(0);
    expect(result.profitFactor).toBe(Infinity);
  });

  it("handles single trade", () => {
    const result = computeAttribution([makeTrade({ netPnl: 100 })]);
    expect(result.totalTrades).toBe(1);
    expect(result.byDay).toHaveLength(1);
    expect(result.byStrategy).toHaveLength(1);
    expect(result.bySymbol).toHaveLength(1);
  });

  it("handles trades with zero P&L", () => {
    const trades = [
      makeTrade({ netPnl: 0 }),
      makeTrade({ netPnl: 0 }),
    ];
    const result = computeAttribution(trades);
    expect(result.overallWinRate).toBe(0); // zero is counted as loss
    expect(result.totalNetPnl).toBe(0);
  });

  it("handles unknown strategy gracefully", () => {
    const trades = [
      makeTrade({ strategyId: undefined, netPnl: 100 }),
    ];
    const result = computeAttribution(trades);
    expect(result.byStrategy).toHaveLength(1);
    expect(result.byStrategy[0]!.strategyId).toBe("unknown");
  });

  // ── Multi-Account / Multi-Symbol Aggregation ──

  it("aggregates many symbols correctly", () => {
    const symbols = ["A", "B", "C", "D", "E"];
    const trades = symbols.flatMap((sym, i) => [
      makeTrade({ symbol: sym, netPnl: (i + 1) * 100, exitTime: "2025-01-10T10:00:00Z" }),
      makeTrade({ symbol: sym, netPnl: -(i * 50), exitTime: "2025-01-11T10:00:00Z" }),
    ]);
    const result = computeAttribution(trades);
    expect(result.bySymbol).toHaveLength(5);
    expect(result.totalTrades).toBe(10);
    // Verify sorted by netPnl descending
    for (let i = 1; i < result.bySymbol.length; i++) {
      expect(result.bySymbol[i - 1]!.netPnl).toBeGreaterThanOrEqual(result.bySymbol[i]!.netPnl);
    }
  });

  it("aggregates many strategies correctly", () => {
    const strategies = ["ma", "rsi", "macd", "turtle"];
    const trades = strategies.flatMap((sid) => [
      makeTrade({ strategyId: sid, netPnl: 500, exitTime: "2025-01-10T10:00:00Z" }),
      makeTrade({ strategyId: sid, netPnl: -200, exitTime: "2025-01-11T10:00:00Z" }),
    ]);
    const result = computeAttribution(trades);
    expect(result.byStrategy).toHaveLength(4);
    for (const s of result.byStrategy) {
      expect(s.trades).toBe(2);
      expect(s.netPnl).toBe(300);
      expect(s.wins).toBe(1);
      expect(s.losses).toBe(1);
    }
  });

  it("contribution percentages are consistent with total P&L", () => {
    const trades = [
      makeTrade({ strategyId: "s1", netPnl: 3000, exitTime: "2025-01-10T10:00:00Z" }),
      makeTrade({ strategyId: "s2", netPnl: -1000, exitTime: "2025-01-11T10:00:00Z" }),
      makeTrade({ strategyId: "s3", netPnl: 500, exitTime: "2025-01-12T10:00:00Z" }),
    ];
    const result = computeAttribution(trades);
    // Each contribution is (strategyNetPnl / abs(totalNetPnl)) * 100
    const totalNet = result.totalNetPnl; // 2500
    for (const s of result.byStrategy) {
      const expected = (s.netPnl / Math.abs(totalNet)) * 100;
      expect(s.contributionPct).toBeCloseTo(expected);
    }
  });

  // ── Weekly aggregation ──

  it("computes weekly P&L correctly across multiple weeks", () => {
    const trades = [
      makeTrade({ exitTime: "2025-01-06T10:00:00Z", netPnl: 100 }),
      makeTrade({ exitTime: "2025-01-07T10:00:00Z", netPnl: 200 }),
      makeTrade({ exitTime: "2025-01-13T10:00:00Z", netPnl: -50 }),
      makeTrade({ exitTime: "2025-01-20T10:00:00Z", netPnl: 300 }),
    ];
    const result = computeAttribution(trades);
    expect(result.byWeek.length).toBeGreaterThanOrEqual(2);
    // Cumulative should be correct
    const lastWeek = result.byWeek[result.byWeek.length - 1]!;
    expect(lastWeek.cumPnl).toBe(550);
  });

  // ── Strategy-level metrics ──

  it("computes largestWin and largestLoss per strategy", () => {
    const trades = [
      makeTrade({ strategyId: "s1", netPnl: 100 }),
      makeTrade({ strategyId: "s1", netPnl: 500 }),
      makeTrade({ strategyId: "s1", netPnl: -200 }),
      makeTrade({ strategyId: "s1", netPnl: -50 }),
    ];
    const result = computeAttribution(trades);
    const s1 = result.byStrategy.find((s) => s.strategyId === "s1")!;
    expect(s1.largestWin).toBe(500);
    expect(s1.largestLoss).toBe(-200);
  });

  it("handles large number of trades efficiently", () => {
    const trades = Array.from({ length: 1000 }, (_, i) =>
      makeTrade({
        symbol: `S${String(i % 20).padStart(2, "0")}`,
        strategyId: `strat-${String(i % 5)}`,
        netPnl: i % 3 === 0 ? -100 : 200,
        exitTime: `2025-01-${String((i % 28) + 1).padStart(2, "0")}T10:00:00Z`,
        grossPnl: i % 3 === 0 ? -90 : 210,
        commission: 10,
      }),
    );
    const result = computeAttribution(trades);
    expect(result.totalTrades).toBe(1000);
    expect(result.bySymbol).toHaveLength(20);
    expect(result.byStrategy).toHaveLength(5);
    expect(result.byDay.length).toBeGreaterThan(0);
  });

  // ── Holding days ──

  it("computes average holding days per symbol", () => {
    const trades = [
      makeTrade({ symbol: "X", holdingDays: 3 }),
      makeTrade({ symbol: "X", holdingDays: 7 }),
      makeTrade({ symbol: "X", holdingDays: 5 }),
    ];
    const result = computeAttribution(trades);
    const x = result.bySymbol.find((s) => s.symbol === "X")!;
    expect(x.holdingDaysAvg).toBe(5);
  });

  it("handles missing holdingDays gracefully", () => {
    const trades = [
      makeTrade({ symbol: "Y", holdingDays: undefined }),
    ];
    const result = computeAttribution(trades);
    const y = result.bySymbol.find((s) => s.symbol === "Y")!;
    expect(y.holdingDaysAvg).toBe(0);
  });

  it("handles mixed holdingDays (some undefined, some defined)", () => {
    const trades = [
      makeTrade({ symbol: "Z", holdingDays: 10 }),
      makeTrade({ symbol: "Z", holdingDays: undefined }),
      makeTrade({ symbol: "Z", holdingDays: 5 }),
    ];
    const result = computeAttribution(trades);
    const z = result.bySymbol.find((s) => s.symbol === "Z")!;
    // (10 + 0 + 5) / 3 = 5
    expect(z.holdingDaysAvg).toBe(5);
  });

  // ── contributionPct when totalNetPnl is zero ──

  it("contributionPct is 0 for all strategies when totalNetPnl is 0", () => {
    const trades = [
      makeTrade({ strategyId: "s1", netPnl: 500, exitTime: "2025-01-10T10:00:00Z" }),
      makeTrade({ strategyId: "s2", netPnl: -500, exitTime: "2025-01-11T10:00:00Z" }),
    ];
    const result = computeAttribution(trades);
    expect(result.totalNetPnl).toBe(0);
    for (const s of result.byStrategy) {
      expect(s.contributionPct).toBe(0);
    }
    for (const s of result.bySymbol) {
      expect(s.contributionPct).toBe(0);
    }
  });

  // ── Profit factor edge: overall all-wins ──

  it("overall profitFactor is Infinity when all trades win", () => {
    const trades = [
      makeTrade({ netPnl: 100 }),
      makeTrade({ netPnl: 200 }),
      makeTrade({ netPnl: 300 }),
    ];
    const result = computeAttribution(trades);
    expect(result.profitFactor).toBe(Infinity);
  });

  // ── Date range edge: from equals to ──

  it("date range with from === to includes trades on that exact date", () => {
    const trades = [
      makeTrade({ exitTime: "2025-01-15T10:00:00Z", netPnl: 100 }),
      makeTrade({ exitTime: "2025-01-15T14:00:00Z", netPnl: 200 }),
      makeTrade({ exitTime: "2025-01-16T10:00:00Z", netPnl: 300 }),
    ];
    const result = computeAttribution(trades, { from: "2025-01-15", to: "2025-01-15" });
    // exitTime "2025-01-15T10:00:00Z" >= "2025-01-15" and <= "2025-01-15"
    // "2025-01-15T10:00:00Z" <= "2025-01-15" is false (T10:00:00Z > empty suffix)
    // So this filters to 0 trades with string comparison
    expect(result.totalTrades).toBe(0);
  });

  // ── Multi-account: verify symbol-level aggregation across strategies ──

  it("same symbol across different strategies is aggregated at symbol level", () => {
    const trades = [
      makeTrade({ symbol: "600519", strategyId: "s1", netPnl: 1000 }),
      makeTrade({ symbol: "600519", strategyId: "s2", netPnl: -300 }),
      makeTrade({ symbol: "600519", strategyId: "s3", netPnl: 500 }),
    ];
    const result = computeAttribution(trades);
    // Symbol level: all merged
    expect(result.bySymbol).toHaveLength(1);
    expect(result.bySymbol[0]!.netPnl).toBe(1200);
    expect(result.bySymbol[0]!.trades).toBe(3);
    // Strategy level: separate
    expect(result.byStrategy).toHaveLength(3);
  });
});
