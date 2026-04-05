import { describe, it, expect } from "vitest";
import type { BacktestResult, TradingSignal, TradeRecord } from "../types.js";
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

function fakeSignal(overrides: Partial<TradingSignal> = {}): TradingSignal {
  return {
    timestamp: new Date("2025-01-15T09:30:00Z").getTime(),
    symbol: "600519",
    direction: "buy",
    price: 1795,
    strategyId: "dual-ma-crossover",
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

  it("uses feeImpact for modeled slippage when available", () => {
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
      fakeBacktestResult({ feeImpact: 2.0, totalTrades: 20 }),
    );

    // modeledSlippage should be feeImpact / totalTrades = 2.0 / 20 = 0.1
    expect(result.backtestStats.modeledSlippage).toBe(0.1);
  });
});

// ── Trade matching tests ─────────────────────────────────────

describe("LiveBacktestComparison.matchTrades", () => {
  it("matches trades with exact same-day signals", () => {
    const equityHistory = fakeEquityHistory(fakeSnapshots());
    const service = new LiveBacktestComparison(equityHistory, undefined);

    const trades = [
      fakeTrade({ tradeId: "t-1", entryTime: "2025-01-15T10:00:00Z", entryPrice: 1800 }),
    ];

    const btResult = fakeBacktestResult({
      signals: [
        fakeSignal({ timestamp: new Date("2025-01-15T09:30:00Z").getTime(), price: 1795 }),
      ],
    });

    const result = service.matchTrades(
      { strategyId: "dual-ma-crossover", symbols: ["600519"], startDate: "2025-01-01", endDate: "2025-03-31" },
      trades,
      btResult,
    );

    expect(result.summary.exactMatches).toBe(1);
    expect(result.summary.unmatchedLiveTrades).toBe(0);
    expect(result.matches[0]!.matchType).toBe("exact");
    expect(result.matches[0]!.timingDeltaDays).toBe(0);
  });

  it("matches trades with approximate same-week signals", () => {
    const equityHistory = fakeEquityHistory(fakeSnapshots());
    const service = new LiveBacktestComparison(equityHistory, undefined);

    const trades = [
      fakeTrade({ tradeId: "t-1", entryTime: "2025-01-17T10:00:00Z", entryPrice: 1810 }),
    ];

    const btResult = fakeBacktestResult({
      signals: [
        fakeSignal({ timestamp: new Date("2025-01-15T09:30:00Z").getTime(), price: 1795 }),
      ],
    });

    const result = service.matchTrades(
      { strategyId: "dual-ma-crossover", symbols: ["600519"], startDate: "2025-01-01", endDate: "2025-03-31" },
      trades,
      btResult,
    );

    expect(result.summary.approximateMatches).toBe(1);
    expect(result.matches[0]!.matchType).toBe("approximate");
    expect(result.matches[0]!.timingDeltaDays).toBe(2);
  });

  it("identifies missed signals and unmatched trades", () => {
    const equityHistory = fakeEquityHistory(fakeSnapshots());
    const service = new LiveBacktestComparison(equityHistory, undefined);

    // Trade on a date with no nearby signal
    const trades = [
      fakeTrade({ tradeId: "t-1", entryTime: "2025-03-25T10:00:00Z" }),
    ];

    // Signal far from any trade
    const btResult = fakeBacktestResult({
      signals: [
        fakeSignal({ timestamp: new Date("2025-01-10T09:30:00Z").getTime() }),
      ],
    });

    const result = service.matchTrades(
      { strategyId: "dual-ma-crossover", symbols: ["600519"], startDate: "2025-01-01", endDate: "2025-03-31" },
      trades,
      btResult,
    );

    expect(result.summary.unmatchedLiveTrades).toBe(1);
    expect(result.summary.missedSignals).toBe(1);
    expect(result.missedSignals.length).toBe(1);
  });

  it("handles empty inputs gracefully", () => {
    const equityHistory = fakeEquityHistory(fakeSnapshots());
    const service = new LiveBacktestComparison(equityHistory, undefined);

    const result = service.matchTrades(
      { strategyId: "dual-ma-crossover", symbols: ["600519"], startDate: "2025-01-01", endDate: "2025-03-31" },
      [],
      fakeBacktestResult({ signals: [] }),
    );

    expect(result.summary.totalLiveTrades).toBe(0);
    expect(result.summary.totalBacktestSignals).toBe(0);
    expect(result.matches.length).toBe(0);
  });
});

// ── Rolling comparison tests ─────────────────────────────────

describe("LiveBacktestComparison.rollingComparison", () => {
  it("produces multiple rolling comparison points", () => {
    const equityHistory = fakeEquityHistory(fakeSnapshots());
    const service = new LiveBacktestComparison(equityHistory, undefined);

    const trades = Array.from({ length: 10 }, (_, i) =>
      fakeTrade({
        tradeId: `t-${i}`,
        entryTime: `2025-0${1 + (i % 3)}-${String(10 + i).padStart(2, "0")}T10:00:00Z`,
      }),
    );

    const result = service.rollingComparison(
      "dual-ma-crossover",
      ["600519"],
      "2025-03-31",
      trades,
      fakeBacktestResult(),
      30,
      14,
    );

    expect(result.rollingDays).toBe(30);
    expect(result.points.length).toBeGreaterThan(0);
    expect(["improving", "stable", "deteriorating"]).toContain(result.trend);
    expect(result.trendDescription).toBeTruthy();

    // Each point should have the expected fields
    for (const p of result.points) {
      expect(p.date).toBeTruthy();
      expect(p.windowStart).toBeTruthy();
      expect(typeof p.returnDelta).toBe("number");
      expect(["consistent", "minor_drift", "significant_drift", "critical_divergence"]).toContain(p.verdict);
    }
  });
});

// ── Batch comparison tests ───────────────────────────────────

describe("LiveBacktestComparison.batchCompare", () => {
  it("ranks multiple strategies by consistency", () => {
    const equityHistory = fakeEquityHistory(fakeSnapshots());
    const service = new LiveBacktestComparison(equityHistory, undefined);

    const goodTrades = Array.from({ length: 15 }, (_, i) =>
      fakeTrade({
        tradeId: `g-${i}`,
        strategyId: "good-strat",
        netPnl: i % 3 === 0 ? -500 : 1000,
        exitPrice: i % 3 === 0 ? 1750 : 1900,
        entryTime: `2025-0${1 + (i % 3)}-${String(10 + (i % 20)).padStart(2, "0")}T10:00:00Z`,
      }),
    );

    const badTrades = Array.from({ length: 10 }, (_, i) =>
      fakeTrade({
        tradeId: `b-${i}`,
        strategyId: "bad-strat",
        netPnl: -2000,
        exitPrice: 1600,
        entryTime: `2025-0${1 + (i % 3)}-${String(10 + i).padStart(2, "0")}T10:00:00Z`,
      }),
    );

    const result = service.batchCompare(
      [
        {
          strategyId: "good-strat",
          symbols: ["600519"],
          trades: goodTrades,
          backtestResult: fakeBacktestResult({ strategyId: "good-strat", winRate: 65, totalTrades: 15, totalReturn: 10, maxDrawdown: 5 }),
        },
        {
          strategyId: "bad-strat",
          symbols: ["600519"],
          trades: badTrades,
          backtestResult: fakeBacktestResult({ strategyId: "bad-strat", winRate: 70, totalTrades: 25, totalReturn: 30, maxDrawdown: 5 }),
        },
      ],
      "2025-01-01",
      "2025-03-31",
    );

    expect(result.entries.length).toBe(2);
    expect(result.ranking.length).toBe(2);
    // Good strategy should rank better (lower score)
    expect(result.ranking[0]!.strategyId).toBe("good-strat");
    expect(result.ranking[0]!.score).toBeLessThan(result.ranking[1]!.score);
  });
});

// ── Report generation tests ──────────────────────────────────

describe("LiveBacktestComparison.generateReport", () => {
  it("generates a Markdown report", () => {
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

    const report = service.generateReport(result);

    expect(report).toContain("# 实盘 vs 回测对比报告");
    expect(report).toContain("dual-ma-crossover");
    expect(report).toContain("600519");
    expect(report).toContain("综合评估");
    expect(report).toContain("关键指标对比");
    expect(report).toContain("| 指标 |");
    expect(report).toContain("差异诊断");
    expect(report).toContain("实盘统计");
    expect(report).toContain("回测统计");
  });
});
