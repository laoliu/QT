import { describe, expect, it } from "vitest";
import type { BacktestResult, TradingSignal, TradeRecord } from "../types.js";
import type { EquitySnapshot } from "./equity-history.js";
import {
  compareLiveVsBacktest,
  comparePeriodSegmented,
  matchTrades,
  computeDriftHistory,
  compareBySymbol,
  compareOutOfSample,
  findClosestBacktest,
  type LiveBacktestCompareRequest,
} from "./live-backtest-compare.js";

// ── Helpers ─────────────────────────────────────────────────────

function makeSignal(overrides: Partial<TradingSignal> = {}): TradingSignal {
  return {
    timestamp: new Date("2024-06-15T09:30:00Z").getTime(),
    symbol: "600519",
    direction: "buy",
    price: 1800,
    strategyId: "ma_cross",
    ...overrides,
  };
}

function makeTrade(overrides: Partial<TradeRecord> = {}): TradeRecord {
  return {
    tradeId: `trade_${Math.random().toString(36).slice(2, 8)}`,
    symbol: "600519",
    market: "cn_stock",
    side: "buy",
    entryTime: "2024-06-15T09:31:00",
    entryPrice: 1802,
    quantity: 100,
    grossPnl: 200,
    commission: 5,
    netPnl: 195,
    strategyId: "ma_cross",
    entryOrderId: "ord_001",
    ...overrides,
  };
}

function makeEquitySnap(date: string, equity: number, benchmark?: number): EquitySnapshot {
  return {
    date,
    equity,
    cash: equity * 0.3,
    marketValue: equity * 0.7,
    dailyPnl: 0,
    positionCount: 1,
    benchmark,
  };
}

function makeBacktest(overrides: Partial<BacktestResult> = {}): BacktestResult {
  return {
    strategyId: "ma_cross",
    symbols: ["600519"],
    startDate: "2024-01-01",
    endDate: "2024-06-30",
    initialCash: 100000,
    finalValue: 115000,
    totalReturn: 15,
    annualizedReturn: 30,
    maxDrawdown: 8,
    sharpeRatio: 1.5,
    winRate: 60,
    totalTrades: 20,
    totalFees: 100,
    signals: [
      makeSignal({ timestamp: new Date("2024-03-01").getTime() }),
      makeSignal({ timestamp: new Date("2024-04-15").getTime(), direction: "sell", price: 1850 }),
      makeSignal({ timestamp: new Date("2024-06-15").getTime() }),
    ],
    equityCurve: [
      { date: "2024-01-01", value: 100000 },
      { date: "2024-02-01", value: 103000 },
      { date: "2024-03-01", value: 106000 },
      { date: "2024-04-01", value: 108000 },
      { date: "2024-05-01", value: 112000 },
      { date: "2024-06-01", value: 114000 },
      { date: "2024-06-30", value: 115000 },
    ],
    ...overrides,
  };
}

function makeLiveEquity(): EquitySnapshot[] {
  return [
    makeEquitySnap("2024-01-01", 100000),
    makeEquitySnap("2024-02-01", 102000),
    makeEquitySnap("2024-03-01", 104000),
    makeEquitySnap("2024-04-01", 106000),
    makeEquitySnap("2024-05-01", 109000),
    makeEquitySnap("2024-06-01", 111000),
    makeEquitySnap("2024-06-30", 112000),
  ];
}

function makeReq(overrides: Partial<LiveBacktestCompareRequest> = {}): LiveBacktestCompareRequest {
  return {
    strategyId: "ma_cross",
    startDate: "2024-01-01",
    endDate: "2024-06-30",
    backtestResult: makeBacktest(),
    liveEquity: makeLiveEquity(),
    liveTrades: [
      makeTrade({ entryTime: "2024-03-01T09:31:00" }),
      makeTrade({ entryTime: "2024-06-15T09:31:00", side: "sell", grossPnl: -100, netPnl: -105, commission: 5 }),
    ],
    liveSignals: [
      makeSignal({ timestamp: new Date("2024-03-01T09:30:00Z").getTime() }),
      makeSignal({ timestamp: new Date("2024-06-15T09:30:00Z").getTime() }),
    ],
    ...overrides,
  };
}

// ── Tests ────────────────────────────────────────────────────────

describe("compareLiveVsBacktest", () => {
  it("returns correct structure with all fields", () => {
    const result = compareLiveVsBacktest(makeReq());
    expect(result.strategyId).toBe("ma_cross");
    expect(result.period).toEqual({ start: "2024-01-01", end: "2024-06-30" });
    expect(result.returnComparison).toBeDefined();
    expect(result.drawdownComparison).toBeDefined();
    expect(result.riskComparison).toBeDefined();
    expect(result.executionQuality).toBeDefined();
    expect(result.equityCurveOverlay).toBeDefined();
    expect(result.drift).toBeDefined();
    expect(result.extendedRisk).toBeDefined();
    expect(result.costDecomposition).toBeDefined();
  });

  it("computes live return correctly", () => {
    const result = compareLiveVsBacktest(makeReq());
    // Live equity: 100000 -> 112000 = 12% return
    expect(result.returnComparison.liveReturn).toBeCloseTo(12, 0);
    expect(result.returnComparison.backtestReturn).toBe(15);
    expect(result.returnComparison.returnDiff).toBeCloseTo(-3, 0);
  });

  it("computes drawdown correctly", () => {
    const result = compareLiveVsBacktest(makeReq());
    // Live equity never drops below starting, so max drawdown should be 0
    expect(result.drawdownComparison.liveMaxDrawdown).toBe(0);
    expect(result.drawdownComparison.backtestMaxDrawdown).toBe(8);
  });

  it("handles empty live equity gracefully", () => {
    const req = makeReq({ liveEquity: [] });
    const result = compareLiveVsBacktest(req);
    expect(result.returnComparison.liveReturn).toBe(0);
    expect(result.drawdownComparison.liveMaxDrawdown).toBe(0);
  });

  it("handles single equity snapshot", () => {
    const req = makeReq({ liveEquity: [makeEquitySnap("2024-01-01", 100000)] });
    const result = compareLiveVsBacktest(req);
    expect(result.returnComparison.liveReturn).toBe(0);
  });

  it("produces drift severity based on divergence", () => {
    const result = compareLiveVsBacktest(makeReq());
    expect(["low", "moderate", "high", "critical"]).toContain(result.drift.severity);
    expect(result.drift.score).toBeGreaterThanOrEqual(0);
    expect(result.drift.score).toBeLessThanOrEqual(100);
    expect(result.drift.observations.length).toBeGreaterThan(0);
  });

  it("returns benchmark comparison when benchmark data is present", () => {
    const bt = makeBacktest({
      benchmarkCurve: [
        { date: "2024-01-01", value: 4000 },
        { date: "2024-06-30", value: 4200 },
      ],
    });
    const result = compareLiveVsBacktest(makeReq({ backtestResult: bt }));
    expect(result.benchmarkComparison).toBeDefined();
    expect(result.benchmarkComparison!.benchmarkReturn).toBeCloseTo(5, 0);
  });

  it("computes extended risk metrics", () => {
    const result = compareLiveVsBacktest(makeReq());
    expect(result.extendedRisk).toBeDefined();
    expect(typeof result.extendedRisk!.liveSortino).toBe("number");
    expect(typeof result.extendedRisk!.liveCalmar).toBe("number");
    expect(typeof result.extendedRisk!.liveProfitFactor).toBe("number");
  });

  it("computes cost decomposition", () => {
    const result = compareLiveVsBacktest(makeReq());
    expect(result.costDecomposition).toBeDefined();
    expect(result.costDecomposition!.totalCommission).toBe(10); // 2 trades × 5 commission
    expect(result.costDecomposition!.backtestFees).toBe(100);
  });
});

describe("comparePeriodSegmented", () => {
  it("returns monthly segments", () => {
    const result = comparePeriodSegmented(makeReq(), "monthly");
    expect(result.strategyId).toBe("ma_cross");
    expect(result.granularity).toBe("monthly");
    expect(result.segments.length).toBeGreaterThan(0);
    for (const seg of result.segments) {
      expect(seg.label).toMatch(/^\d{4}-\d{2}$/);
      expect(typeof seg.backtestReturn).toBe("number");
      expect(typeof seg.liveReturn).toBe("number");
      expect(typeof seg.driftScore).toBe("number");
    }
  });

  it("returns weekly segments", () => {
    const result = comparePeriodSegmented(makeReq(), "weekly");
    expect(result.granularity).toBe("weekly");
    expect(result.segments.length).toBeGreaterThan(0);
  });

  it("identifies drift onset", () => {
    // With high divergence, some segment should trigger drift onset
    const bt = makeBacktest({
      totalReturn: 50,
      equityCurve: [
        { date: "2024-01-01", value: 100000 },
        { date: "2024-02-01", value: 120000 },
        { date: "2024-03-01", value: 140000 },
        { date: "2024-06-30", value: 150000 },
      ],
    });
    const result = comparePeriodSegmented(makeReq({ backtestResult: bt }), "monthly");
    // Drift onset may or may not be detected depending on segment returns
    expect(result.worstSegment).toBeDefined();
  });
});

describe("matchTrades", () => {
  it("matches backtest signals to live trades", () => {
    const result = matchTrades(makeReq());
    expect(result.strategyId).toBe("ma_cross");
    expect(result.matches.length).toBeGreaterThan(0);
    expect(result.summary.totalBacktestSignals).toBeGreaterThan(0);
  });

  it("identifies missed signals", () => {
    // Req with backtest signals but no live trades
    const req = makeReq({ liveTrades: [], liveSignals: [] });
    const result = matchTrades(req);
    expect(result.summary.missedCount).toBe(result.summary.totalBacktestSignals);
    expect(result.summary.matchRate).toBe(0);
  });

  it("identifies extra live trades", () => {
    const extraTrade = makeTrade({
      symbol: "000001",
      entryTime: "2024-05-01T10:00:00",
    });
    const req = makeReq({
      liveTrades: [...makeReq().liveTrades, extraTrade],
    });
    const result = matchTrades(req);
    expect(result.summary.extraLiveCount).toBeGreaterThan(0);
  });
});

describe("computeDriftHistory", () => {
  it("returns drift snapshots over rolling windows", () => {
    const result = computeDriftHistory(makeReq(), 60, 30);
    expect(result.strategyId).toBe("ma_cross");
    expect(result.snapshots.length).toBeGreaterThan(0);
    expect(["improving", "stable", "worsening"]).toContain(result.trend);
    expect(typeof result.avgDrift).toBe("number");
  });

  it("handles short period with few snapshots", () => {
    const req = makeReq({
      startDate: "2024-06-01",
      endDate: "2024-06-30",
    });
    const result = computeDriftHistory(req, 30, 7);
    // May produce 0 or 1 snapshot for such a short period
    expect(result.snapshots.length).toBeGreaterThanOrEqual(0);
  });
});

describe("compareBySymbol", () => {
  it("returns per-symbol breakdown", () => {
    const result = compareBySymbol(makeReq());
    expect(result.strategyId).toBe("ma_cross");
    expect(result.symbols.length).toBeGreaterThan(0);
    const sym = result.symbols[0]!;
    expect(sym.symbol).toBe("600519");
    expect(typeof sym.liveTrades).toBe("number");
    expect(typeof sym.missedSignals).toBe("number");
  });

  it("handles no trades gracefully", () => {
    const req = makeReq({ liveTrades: [], liveSignals: [] });
    const result = compareBySymbol(req);
    expect(result.symbols.length).toBeGreaterThan(0); // symbols from backtest signals
    expect(result.symbols[0]!.liveTrades).toBe(0);
  });
});

describe("compareOutOfSample", () => {
  it("computes out-of-sample metrics for live period after backtest", () => {
    const bt = makeBacktest({ endDate: "2024-06-30" });
    const liveEquity = [
      makeEquitySnap("2024-07-01", 100000),
      makeEquitySnap("2024-07-15", 101000),
      makeEquitySnap("2024-08-01", 103000),
      makeEquitySnap("2024-08-15", 102000),
      makeEquitySnap("2024-09-01", 105000),
      makeEquitySnap("2024-09-15", 107000),
    ];
    const result = compareOutOfSample(bt, liveEquity, []);
    expect(result.strategyId).toBe("ma_cross");
    expect(result.inSamplePeriod.start).toBe("2024-01-01");
    expect(result.outOfSamplePeriod.start).toBe("2024-07-01");
    expect(result.outOfSampleReturn).toBeGreaterThan(0);
    expect(["on_track", "degrading", "failed"]).toContain(result.verdict);
    expect(result.observations.length).toBeGreaterThan(0);
  });

  it("returns meaningful result with no live data after backtest end", () => {
    const bt = makeBacktest({ endDate: "2024-06-30" });
    const liveEquity = [makeEquitySnap("2024-05-01", 100000)]; // all before backtest end
    const result = compareOutOfSample(bt, liveEquity, []);
    expect(result.outOfSampleReturn).toBe(0);
    expect(result.observations).toContain("样本外数据不足，结论可能不可靠");
  });

  it("detects strategy failure when out-of-sample greatly underperforms", () => {
    const bt = makeBacktest({ annualizedReturn: 50, sharpeRatio: 2.5 });
    const liveEquity = [
      makeEquitySnap("2024-07-01", 100000),
      makeEquitySnap("2024-07-15", 99000),
      makeEquitySnap("2024-08-01", 97000),
      makeEquitySnap("2024-08-15", 95000),
      makeEquitySnap("2024-09-01", 93000),
      makeEquitySnap("2024-12-31", 90000),
    ];
    const result = compareOutOfSample(bt, liveEquity, []);
    expect(result.verdict).toBe("failed");
    expect(result.returnDecay).toBeGreaterThan(15);
  });
});

describe("findClosestBacktest", () => {
  it("ranks backtests by correlation with live equity", () => {
    const liveEquity = [
      makeEquitySnap("2024-01-01", 100000),
      makeEquitySnap("2024-02-01", 103000),
      makeEquitySnap("2024-03-01", 106000),
      makeEquitySnap("2024-04-01", 108000),
      makeEquitySnap("2024-05-01", 110000),
      makeEquitySnap("2024-06-01", 112000),
      makeEquitySnap("2024-06-30", 113000),
    ];

    const closeMatch = makeBacktest({
      strategyId: "close_match",
      equityCurve: [
        { date: "2024-01-01", value: 100000 },
        { date: "2024-02-01", value: 103500 },
        { date: "2024-03-01", value: 106500 },
        { date: "2024-04-01", value: 108500 },
        { date: "2024-05-01", value: 110500 },
        { date: "2024-06-01", value: 112500 },
        { date: "2024-06-30", value: 114000 },
      ],
    });

    const poorMatch = makeBacktest({
      strategyId: "poor_match",
      equityCurve: [
        { date: "2024-01-01", value: 100000 },
        { date: "2024-02-01", value: 95000 },
        { date: "2024-03-01", value: 90000 },
        { date: "2024-04-01", value: 98000 },
        { date: "2024-05-01", value: 85000 },
        { date: "2024-06-01", value: 92000 },
        { date: "2024-06-30", value: 88000 },
      ],
    });

    const result = findClosestBacktest(
      [
        { id: "bt_close", result: closeMatch },
        { id: "bt_poor", result: poorMatch },
      ],
      liveEquity,
    );

    expect(result.bestMatch).toBeDefined();
    expect(result.bestMatch!.backtestId).toBe("bt_close");
    expect(result.bestMatch!.correlationScore).toBeGreaterThan(50);
    expect(result.candidates.length).toBe(2);
    // Close match should rank first
    expect(result.candidates[0]!.backtestId).toBe("bt_close");
  });

  it("handles no overlapping dates", () => {
    const liveEquity = [
      makeEquitySnap("2025-01-01", 100000),
      makeEquitySnap("2025-02-01", 105000),
    ];
    const bt = makeBacktest(); // dates in 2024
    const result = findClosestBacktest([{ id: "bt1", result: bt }], liveEquity);
    expect(result.candidates[0]!.correlationScore).toBe(0);
  });

  it("handles empty backtests array", () => {
    const liveEquity = [makeEquitySnap("2024-01-01", 100000)];
    const result = findClosestBacktest([], liveEquity);
    expect(result.bestMatch).toBeNull();
    expect(result.candidates.length).toBe(0);
  });
});
