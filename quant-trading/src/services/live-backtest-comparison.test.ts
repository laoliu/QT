import { describe, it, expect } from "vitest";
import type { BacktestResult, TradeRecord } from "../types.js";
import type { EquitySnapshot } from "./equity-history.js";
import { LiveBacktestComparison } from "./live-backtest-comparison.js";

// ── Helpers ──────────────────────────────────────────────────

function fakeEquityHistory(snapshots: EquitySnapshot[]) {
  return {
    getRange: (start: string, end: string) =>
      snapshots.filter((s) => s.date >= start && s.date <= end),
    getAll: () => [...snapshots],
    getSummary: () => ({
      totalDays: snapshots.length,
      startEquity: snapshots[0]?.equity ?? 0,
      endEquity: snapshots[snapshots.length - 1]?.equity ?? 0,
      totalReturn: 0,
      totalReturnPct: 0,
      maxDrawdown: 0,
      maxDrawdownPct: 0,
      bestDay: null,
      worstDay: null,
      avgDailyPnl: 0,
      winDays: 0,
      lossDays: 0,
      winRate: 0,
    }),
  } as any;
}

function fakeBacktestResult(overrides: Partial<BacktestResult> = {}): BacktestResult {
  return {
    strategyId: "dual-ma-crossover",
    symbols: ["600519"],
    startDate: "2025-01-01",
    endDate: "2025-03-31",
    initialCash: 100_000,
    finalValue: 115_000,
    totalReturn: 15,
    annualizedReturn: 60,
    maxDrawdown: 8,
    sharpeRatio: 2.0,
    winRate: 65,
    totalTrades: 20,
    signals: [],
    equityCurve: [
      { date: "2025-01-01", value: 100_000 },
      { date: "2025-02-01", value: 107_000 },
      { date: "2025-03-01", value: 112_000 },
      { date: "2025-03-31", value: 115_000 },
    ],
    ...overrides,
  };
}

function fakeTrade(overrides: Partial<TradeRecord> = {}): TradeRecord {
  return {
    tradeId: "t-1",
    symbol: "600519",
    market: "cn_stock",
    side: "buy",
    entryTime: "2025-01-15T10:00:00Z",
    exitTime: "2025-02-10T14:00:00Z",
    entryPrice: 1800,
    exitPrice: 1900,
    quantity: 100,
    grossPnl: 10_000,
    commission: 30,
    netPnl: 9_970,
    strategyId: "dual-ma-crossover",
    entryOrderId: "o-1",
    exitOrderId: "o-2",
    ...overrides,
  };
}

function fakeSnapshots(): EquitySnapshot[] {
  return [
    { date: "2025-01-01", equity: 100_000, cash: 100_000, marketValue: 0, dailyPnl: 0, positionCount: 0 },
    { date: "2025-01-15", equity: 100_500, cash: 500, marketValue: 100_000, dailyPnl: 500, positionCount: 1 },
    { date: "2025-02-01", equity: 105_000, cash: 5_000, marketValue: 100_000, dailyPnl: 1_000, positionCount: 1 },
    { date: "2025-02-15", equity: 103_000, cash: 3_000, marketValue: 100_000, dailyPnl: -2_000, positionCount: 1 },
    { date: "2025-03-01", equity: 108_000, cash: 8_000, marketValue: 100_000, dailyPnl: 2_000, positionCount: 1 },
    { date: "2025-03-31", equity: 110_000, cash: 110_000, marketValue: 0, dailyPnl: 500, positionCount: 0 },
  ];
}

// ── Tests ────────────────────────────────────────────────────

describe("LiveBacktestComparison", () => {
  it("compares live vs backtest and returns metrics", () => {
    const equityHistory = fakeEquityHistory(fakeSnapshots());
    const service = new LiveBacktestComparison(equityHistory, undefined);

    const result = service.compare(
      {
        strategyId: "dual-ma-crossover",
        symbols: ["600519"],
        startDate: "2025-01-01",
        endDate: "2025-03-31",
      },
      [
        fakeTrade(),
        fakeTrade({
          tradeId: "t-2",
          entryTime: "2025-02-20T10:00:00Z",
          exitTime: "2025-03-15T14:00:00Z",
          entryPrice: 1850,
          exitPrice: 1750,
          grossPnl: -10_000,
          netPnl: -10_030,
        }),
      ],
      fakeBacktestResult(),
    );

    expect(result.strategyId).toBe("dual-ma-crossover");
    expect(result.symbols).toEqual(["600519"]);
    expect(result.metrics.length).toBeGreaterThanOrEqual(4);

    // Should have metric deltas
    const returnMetric = result.metrics.find((m) => m.label === "总收益率");
    expect(returnMetric).toBeDefined();
    expect(returnMetric!.backtest).toBe(15); // from backtest result
    expect(typeof returnMetric!.live).toBe("number");
    expect(typeof returnMetric!.delta).toBe("number");
  });

  it("identifies gaps when live performance is worse", () => {
    const equityHistory = fakeEquityHistory(fakeSnapshots());
    const service = new LiveBacktestComparison(equityHistory, undefined);

    // Live has much worse win rate: 1 win, 3 losses
    const trades = [
      fakeTrade({ tradeId: "t-1", netPnl: 5000 }),
      fakeTrade({ tradeId: "t-2", netPnl: -3000, exitPrice: 1700 }),
      fakeTrade({ tradeId: "t-3", netPnl: -2000, exitPrice: 1750, entryTime: "2025-02-01T10:00:00Z" }),
      fakeTrade({ tradeId: "t-4", netPnl: -4000, exitPrice: 1600, entryTime: "2025-03-01T10:00:00Z" }),
    ];

    const result = service.compare(
      {
        strategyId: "dual-ma-crossover",
        symbols: ["600519"],
        startDate: "2025-01-01",
        endDate: "2025-03-31",
      },
      trades,
      fakeBacktestResult({ winRate: 65, totalTrades: 20 }),
    );

    // Should identify win rate gap
    const hasWinRateGap = result.gaps.some((g) => g.category === "fill_rate");
    expect(hasWinRateGap).toBe(true);
  });

  it("returns consistent verdict when performance matches", () => {
    // Equity snapshots that show ~10% return (matching the backtest)
    const matchingSnapshots: EquitySnapshot[] = [
      { date: "2025-01-01", equity: 100_000, cash: 100_000, marketValue: 0, dailyPnl: 0, positionCount: 0 },
      { date: "2025-02-01", equity: 103_000, cash: 3_000, marketValue: 100_000, dailyPnl: 1_000, positionCount: 1 },
      { date: "2025-03-01", equity: 107_000, cash: 7_000, marketValue: 100_000, dailyPnl: 1_500, positionCount: 1 },
      { date: "2025-03-31", equity: 110_000, cash: 110_000, marketValue: 0, dailyPnl: 500, positionCount: 0 },
    ];
    const equityHistory = fakeEquityHistory(matchingSnapshots);
    const service = new LiveBacktestComparison(equityHistory, undefined);

    // Good live trades: ~66% win rate, 15 trades — matching backtest expectations
    const trades = Array.from({ length: 15 }, (_, i) => {
      const isLoss = i % 3 === 0;
      return fakeTrade({
        tradeId: `t-${i}`,
        grossPnl: isLoss ? -470 : 1030,
        commission: 30,
        netPnl: isLoss ? -500 : 1000,
        exitPrice: isLoss ? 1750 : 1900,
        entryTime: `2025-0${1 + (i % 3)}-${String(10 + (i % 20)).padStart(2, "0")}T10:00:00Z`,
      });
    });

    const result = service.compare(
      {
        strategyId: "dual-ma-crossover",
        symbols: ["600519"],
        startDate: "2025-01-01",
        endDate: "2025-03-31",
      },
      trades,
      fakeBacktestResult({
        winRate: 65,
        totalTrades: 15,
        totalReturn: 10,
        maxDrawdown: 5,
      }),
    );

    expect(["consistent", "minor_drift"]).toContain(result.verdict);
  });

  it("builds equity curve overlay from both sources", () => {
    const equityHistory = fakeEquityHistory(fakeSnapshots());
    const service = new LiveBacktestComparison(equityHistory, undefined);

    const result = service.compare(
      {
        strategyId: "dual-ma-crossover",
        symbols: ["600519"],
        startDate: "2025-01-01",
        endDate: "2025-03-31",
      },
      [fakeTrade()],
      fakeBacktestResult(),
    );

    expect(result.equityCurve.length).toBeGreaterThan(0);
    // Should have dates from both live and backtest
    const hasLive = result.equityCurve.some((p) => p.liveValue !== null);
    const hasBacktest = result.equityCurve.some((p) => p.backtestValue !== null);
    expect(hasLive).toBe(true);
    expect(hasBacktest).toBe(true);
  });

  it("handles empty trades gracefully", () => {
    const equityHistory = fakeEquityHistory(fakeSnapshots());
    const service = new LiveBacktestComparison(equityHistory, undefined);

    const result = service.compare(
      {
        strategyId: "dual-ma-crossover",
        symbols: ["600519"],
        startDate: "2025-01-01",
        endDate: "2025-03-31",
      },
      [],
      fakeBacktestResult(),
    );

    expect(result.liveStats.totalTrades).toBe(0);
    expect(result.metrics.length).toBeGreaterThan(0);
    expect(result.verdict).toBeDefined();
  });

  it("handles missing backtest result", () => {
    const equityHistory = fakeEquityHistory(fakeSnapshots());
    const service = new LiveBacktestComparison(equityHistory, undefined);

    const result = service.compare(
      {
        strategyId: "dual-ma-crossover",
        symbols: ["600519"],
        startDate: "2025-01-01",
        endDate: "2025-03-31",
      },
      [fakeTrade()],
      // no backtest result
    );

    expect(result.backtestId).toBeNull();
    expect(result.backtestStats.totalTrades).toBe(0);
    expect(result.metrics.length).toBeGreaterThan(0);
  });

  it("detects critical divergence with severe gaps", () => {
    const snapshots: EquitySnapshot[] = [
      { date: "2025-01-01", equity: 100_000, cash: 100_000, marketValue: 0, dailyPnl: 0, positionCount: 0 },
      { date: "2025-02-01", equity: 85_000, cash: 85_000, marketValue: 0, dailyPnl: -5_000, positionCount: 0 },
      { date: "2025-03-31", equity: 75_000, cash: 75_000, marketValue: 0, dailyPnl: -3_000, positionCount: 0 },
    ];
    const equityHistory = fakeEquityHistory(snapshots);
    const service = new LiveBacktestComparison(equityHistory, undefined);

    // Very bad live performance
    const trades = Array.from({ length: 10 }, (_, i) =>
      fakeTrade({
        tradeId: `t-${i}`,
        netPnl: -2000,
        exitPrice: 1600,
        entryTime: `2025-0${1 + (i % 3)}-${10 + i}T10:00:00Z`,
      }),
    );

    const result = service.compare(
      {
        strategyId: "dual-ma-crossover",
        symbols: ["600519"],
        startDate: "2025-01-01",
        endDate: "2025-03-31",
      },
      trades,
      fakeBacktestResult({
        totalReturn: 30,
        winRate: 70,
        maxDrawdown: 5,
        totalTrades: 25,
      }),
    );

    // Should have critical or significant drift verdict
    expect(["significant_drift", "critical_divergence"]).toContain(result.verdict);
    expect(result.gaps.some((g) => g.severity === "critical")).toBe(true);
  });
});
