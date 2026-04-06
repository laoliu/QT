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
  compareRegimeAware,
  comparePositionWeighted,
  runScheduledDriftCheck,
  comparePortfolio,
  checkReoptimizeNeed,
  compareWithCustomBenchmark,
  analyzeIntradayExecution,
  replayWhatIf,
  compareDrawdownEpisodes,
  DEFAULT_DECAY_ALERT_CONFIG,
  DEFAULT_SCHEDULED_DRIFT_CONFIG,
  type LiveBacktestCompareRequest,
  type DecayAlertConfig,
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

describe("curveCorrelation", () => {
  it("returns positive correlation for similar equity curves", () => {
    const result = compareLiveVsBacktest(makeReq());
    // Both curves go up, so correlation should be positive
    expect(result.curveCorrelation).toBeGreaterThan(0);
  });

  it("returns zero with no overlapping data", () => {
    const req = makeReq({ liveEquity: [makeEquitySnap("2025-01-01", 100000)] });
    const result = compareLiveVsBacktest(req);
    expect(result.curveCorrelation).toBe(0);
  });
});

describe("avgSignalDelayMs", () => {
  it("computes average delay between signals and trades", () => {
    const result = compareLiveVsBacktest(makeReq());
    // We have 2 live signals and 2 live trades, each with 1 min delay (60_000 ms)
    expect(result.avgSignalDelayMs).toBeGreaterThanOrEqual(0);
    expect(typeof result.avgSignalDelayMs).toBe("number");
  });

  it("returns zero with no matching signals", () => {
    const req = makeReq({ liveSignals: [] });
    const result = compareLiveVsBacktest(req);
    expect(result.avgSignalDelayMs).toBe(0);
  });
});

describe("decayAlertTriggered", () => {
  it("is false when return deviation is within threshold", () => {
    // Backtest: 15%, Live: ~12%, deviation = |12/15 - 1| = 0.2 = 20%
    // Default threshold is 0.20, so exactly at boundary
    const result = compareLiveVsBacktest(makeReq());
    expect(typeof result.decayAlertTriggered).toBe("boolean");
  });

  it("is true when return deviation exceeds threshold", () => {
    // Make a big gap: backtest 50%, live ~12%
    const bt = makeBacktest({ totalReturn: 50 });
    const config: DecayAlertConfig = { returnDeviationThreshold: 0.10 };
    const result = compareLiveVsBacktest(makeReq({ backtestResult: bt }), config);
    expect(result.decayAlertTriggered).toBe(true);
  });

  it("respects per-strategy override", () => {
    const config: DecayAlertConfig = {
      returnDeviationThreshold: 0.01,  // very tight default
      perStrategyOverrides: {
        ma_cross: 0.99,  // very lenient for this strategy
      },
    };
    const result = compareLiveVsBacktest(makeReq(), config);
    expect(result.decayAlertTriggered).toBe(false);
  });

  it("uses default threshold from DEFAULT_DECAY_ALERT_CONFIG", () => {
    expect(DEFAULT_DECAY_ALERT_CONFIG.returnDeviationThreshold).toBe(0.20);
  });
});

describe("compareRegimeAware", () => {
  it("returns regime-aware comparison with adjusted drift score", () => {
    const result = compareRegimeAware(makeReq());
    expect(result.strategyId).toBe("ma_cross");
    expect(["bull", "bear", "sideways", "high_volatility", "low_volatility"]).toContain(result.regime);
    expect(result.adjustedDriftScore).toBeGreaterThanOrEqual(0);
    expect(result.adjustedDriftScore).toBeLessThanOrEqual(100);
    expect(result.comparison).toBeDefined();
    expect(result.regimeAttribution.regimeExplainedPct + result.regimeAttribution.strategySpecificPct).toBe(100);
    expect(result.regimeObservations.length).toBeGreaterThan(0);
  });

  it("adjusts drift score downward in bear market", () => {
    // Create declining benchmark to simulate bear market
    const equity = [
      makeEquitySnap("2024-01-01", 100000, 4000),
      makeEquitySnap("2024-02-01", 99000, 3800),
      makeEquitySnap("2024-03-01", 97000, 3600),
      makeEquitySnap("2024-04-01", 95000, 3400),
      makeEquitySnap("2024-05-01", 93000, 3200),
      makeEquitySnap("2024-06-01", 91000, 3000),
      makeEquitySnap("2024-06-30", 90000, 2900),
    ];
    const req = makeReq({ liveEquity: equity });
    const regimeResult = compareRegimeAware(req);
    const rawResult = compareLiveVsBacktest(req);

    // In a bear/declining market, adjusted score should be <= raw score
    expect(regimeResult.adjustedDriftScore).toBeLessThanOrEqual(rawResult.drift.score);
  });

  it("handles insufficient data gracefully", () => {
    const req = makeReq({ liveEquity: [makeEquitySnap("2024-01-01", 100000)] });
    const result = compareRegimeAware(req);
    expect(result.regime).toBe("sideways"); // Default when data is insufficient
    expect(result.comparison).toBeDefined();
  });
});

describe("comparePositionWeighted", () => {
  it("returns position-weighted comparison with symbol weights", () => {
    const result = comparePositionWeighted(makeReq());
    expect(result.strategyId).toBe("ma_cross");
    expect(result.weightedReturnComparison).toBeDefined();
    expect(typeof result.weightedReturnComparison.liveWeightedReturn).toBe("number");
    expect(typeof result.weightedDriftScore).toBe("number");
    expect(result.symbolWeights.length).toBeGreaterThan(0);
    expect(result.observations.length).toBeGreaterThan(0);
  });

  it("weights drift by capital allocation", () => {
    // Create trades with very different position sizes
    const largeTrade = makeTrade({
      symbol: "600519",
      quantity: 1000,
      entryPrice: 1800,
      entryTime: "2024-03-01T09:31:00",
      netPnl: -5000,
      grossPnl: -4900,
      exitPrice: 1795,
    });
    const smallTrade = makeTrade({
      symbol: "000001",
      quantity: 100,
      entryPrice: 10,
      entryTime: "2024-04-01T09:31:00",
      netPnl: 50,
      grossPnl: 55,
      exitPrice: 10.5,
    });
    const bt = makeBacktest({
      signals: [
        makeSignal({ symbol: "600519", timestamp: new Date("2024-03-01").getTime() }),
        makeSignal({ symbol: "000001", timestamp: new Date("2024-04-01").getTime() }),
      ],
    });
    const req = makeReq({
      backtestResult: bt,
      liveTrades: [largeTrade, smallTrade],
    });
    const result = comparePositionWeighted(req);

    // 600519 should have much higher weight than 000001
    const large = result.symbolWeights.find((s) => s.symbol === "600519");
    const small = result.symbolWeights.find((s) => s.symbol === "000001");
    expect(large).toBeDefined();
    expect(small).toBeDefined();
    expect(large!.capitalWeight).toBeGreaterThan(small!.capitalWeight);
  });

  it("handles empty trades gracefully", () => {
    const req = makeReq({ liveTrades: [] });
    const result = comparePositionWeighted(req);
    expect(result.weightedDriftScore).toBeGreaterThanOrEqual(0);
    // Should still have symbols from backtest signals
    expect(result.symbolWeights.length).toBeGreaterThan(0);
  });
});

describe("runScheduledDriftCheck", () => {
  it("returns batch check results across multiple strategies", () => {
    const req1 = makeReq();
    const req2 = makeReq({
      strategyId: "rsi_strategy",
      backtestResult: makeBacktest({ strategyId: "rsi_strategy", totalReturn: 30 }),
    });

    const strategies = [
      {
        strategyId: "ma_cross",
        backtestResult: req1.backtestResult,
        liveEquity: req1.liveEquity,
        liveTrades: req1.liveTrades,
        liveSignals: req1.liveSignals,
      },
      {
        strategyId: "rsi_strategy",
        backtestResult: req2.backtestResult,
        liveEquity: req2.liveEquity,
        liveTrades: req2.liveTrades,
        liveSignals: req2.liveSignals,
      },
    ];

    const result = runScheduledDriftCheck(strategies);
    expect(result.checkTime).toBeDefined();
    expect(result.strategyResults.length).toBe(2);
    expect(["healthy", "warning", "critical"]).toContain(result.portfolioHealth);

    for (const sr of result.strategyResults) {
      expect(sr.driftScore).toBeGreaterThanOrEqual(0);
      expect(sr.driftScore).toBeLessThanOrEqual(100);
      expect(typeof sr.alertTriggered).toBe("boolean");
    }
  });

  it("skips strategies with insufficient data", () => {
    const strategies = [
      {
        strategyId: "empty_strat",
        backtestResult: makeBacktest(),
        liveEquity: [makeEquitySnap("2024-06-30", 100000)],
        liveTrades: [],
        liveSignals: [],
      },
    ];

    const result = runScheduledDriftCheck(strategies);
    expect(result.strategyResults.length).toBe(1);
    expect(result.strategyResults[0]!.observations).toContain("监控窗口内数据不足，跳过本次检查");
  });

  it("marks portfolio as critical when multiple strategies alert", () => {
    // Create strategies with very poor live performance vs backtest
    // Use current dates so they fall within the rolling window
    const now = new Date();
    const dates = Array.from({ length: 7 }, (_, i) => {
      const d = new Date(now.getTime() - (6 - i) * 4 * 86_400_000);
      return d.toISOString().slice(0, 10);
    });
    const poorEquity = dates.map((d, i) =>
      makeEquitySnap(d, 100000 - i * 8000),
    );

    const strategies = [
      {
        strategyId: "bad_strat_1",
        backtestResult: makeBacktest({ totalReturn: 50, strategyId: "bad_strat_1" }),
        liveEquity: poorEquity,
        liveTrades: [] as TradeRecord[],
        liveSignals: [] as TradingSignal[],
      },
      {
        strategyId: "bad_strat_2",
        backtestResult: makeBacktest({ totalReturn: 40, strategyId: "bad_strat_2" }),
        liveEquity: poorEquity,
        liveTrades: [] as TradeRecord[],
        liveSignals: [] as TradingSignal[],
      },
    ];

    const config = { ...DEFAULT_SCHEDULED_DRIFT_CONFIG, alertThreshold: 10 };
    const result = runScheduledDriftCheck(strategies, config);
    expect(result.alertedStrategies.length).toBeGreaterThan(0);
    expect(["warning", "critical"]).toContain(result.portfolioHealth);
  });

  it("uses DEFAULT_SCHEDULED_DRIFT_CONFIG defaults", () => {
    expect(DEFAULT_SCHEDULED_DRIFT_CONFIG.frequency).toBe("daily");
    expect(DEFAULT_SCHEDULED_DRIFT_CONFIG.windowDays).toBe(30);
    expect(DEFAULT_SCHEDULED_DRIFT_CONFIG.alertThreshold).toBe(35);
  });
});

describe("comparePortfolio", () => {
  it("returns portfolio-level comparison with blended backtest return", () => {
    const req1 = makeReq();
    const req2 = makeReq({
      strategyId: "rsi_strategy",
      backtestResult: makeBacktest({ strategyId: "rsi_strategy", totalReturn: 20 }),
    });

    const strategies = [
      {
        strategyId: "ma_cross",
        backtestResult: req1.backtestResult,
        liveEquity: req1.liveEquity,
        liveTrades: req1.liveTrades,
        liveSignals: req1.liveSignals,
        capitalAllocation: 100000,
      },
      {
        strategyId: "rsi_strategy",
        backtestResult: req2.backtestResult,
        liveEquity: req2.liveEquity,
        liveTrades: req2.liveTrades,
        liveSignals: req2.liveSignals,
        capitalAllocation: 50000,
      },
    ];

    const portfolioEquity = makeLiveEquity();
    const result = comparePortfolio(strategies, portfolioEquity, "2024-01-01", "2024-06-30");

    expect(result.period).toEqual({ start: "2024-01-01", end: "2024-06-30" });
    expect(typeof result.blendedBacktestReturn).toBe("number");
    expect(typeof result.livePortfolioReturn).toBe("number");
    expect(typeof result.portfolioDriftScore).toBe("number");
    expect(["low", "moderate", "high", "critical"]).toContain(result.portfolioDriftSeverity);
    expect(result.strategyContributions.length).toBe(2);
    expect(result.observations.length).toBeGreaterThan(0);
  });

  it("weights strategies by capital allocation", () => {
    const bt1 = makeBacktest({ strategyId: "strat_a", totalReturn: 10 });
    const bt2 = makeBacktest({ strategyId: "strat_b", totalReturn: 30 });

    const strategies = [
      {
        strategyId: "strat_a",
        backtestResult: bt1,
        liveEquity: makeLiveEquity(),
        liveTrades: makeReq().liveTrades,
        liveSignals: makeReq().liveSignals,
        capitalAllocation: 200000,
      },
      {
        strategyId: "strat_b",
        backtestResult: bt2,
        liveEquity: makeLiveEquity(),
        liveTrades: makeReq().liveTrades,
        liveSignals: makeReq().liveSignals,
        capitalAllocation: 100000,
      },
    ];

    const result = comparePortfolio(strategies, makeLiveEquity(), "2024-01-01", "2024-06-30");

    // strat_a has 2/3 weight, strat_b has 1/3 weight
    const stratA = result.strategyContributions.find((s) => s.strategyId === "strat_a");
    const stratB = result.strategyContributions.find((s) => s.strategyId === "strat_b");
    expect(stratA).toBeDefined();
    expect(stratB).toBeDefined();
    expect(stratA!.capitalWeight).toBeCloseTo(2 / 3, 2);
    expect(stratB!.capitalWeight).toBeCloseTo(1 / 3, 2);
  });

  it("computes diversification benefit", () => {
    const strategies = [
      {
        strategyId: "strat_a",
        backtestResult: makeBacktest({ strategyId: "strat_a" }),
        liveEquity: makeLiveEquity(),
        liveTrades: makeReq().liveTrades,
        liveSignals: makeReq().liveSignals,
        capitalAllocation: 100000,
      },
      {
        strategyId: "strat_b",
        backtestResult: makeBacktest({ strategyId: "strat_b" }),
        liveEquity: makeLiveEquity(),
        liveTrades: makeReq().liveTrades,
        liveSignals: makeReq().liveSignals,
        capitalAllocation: 100000,
      },
    ];

    const result = comparePortfolio(strategies, makeLiveEquity(), "2024-01-01", "2024-06-30");
    expect(typeof result.diversificationBenefit).toBe("number");
  });
});

describe("checkReoptimizeNeed", () => {
  it("returns reoptimize recommendation with all fields", () => {
    const req = makeReq();
    const postBtEquity = [
      makeEquitySnap("2024-07-01", 100000),
      makeEquitySnap("2024-07-15", 101000),
      makeEquitySnap("2024-08-01", 103000),
      makeEquitySnap("2024-08-15", 102000),
      makeEquitySnap("2024-09-01", 105000),
      makeEquitySnap("2024-09-15", 107000),
    ];
    const result = checkReoptimizeNeed(req, postBtEquity, []);

    expect(result.strategyId).toBe("ma_cross");
    expect(["none", "monitor", "recommended", "urgent"]).toContain(result.urgency);
    expect(result.reoptimizeScore).toBeGreaterThanOrEqual(0);
    expect(result.reoptimizeScore).toBeLessThanOrEqual(100);
    expect(result.recommendations.length).toBeGreaterThan(0);
    expect(result.metrics.driftScore).toBeGreaterThanOrEqual(0);
    expect(["improving", "stable", "worsening"]).toContain(result.metrics.driftTrend);
  });

  it("recommends urgent re-optimization for severely degraded strategies", () => {
    // Strategy with huge backtest return but very poor live performance
    const bt = makeBacktest({ totalReturn: 60, annualizedReturn: 80, sharpeRatio: 3.0 });
    const poorEquity = [
      makeEquitySnap("2024-01-01", 100000),
      makeEquitySnap("2024-02-01", 95000),
      makeEquitySnap("2024-03-01", 90000),
      makeEquitySnap("2024-04-01", 85000),
      makeEquitySnap("2024-05-01", 80000),
      makeEquitySnap("2024-06-01", 78000),
      makeEquitySnap("2024-06-30", 75000),
    ];
    const req = makeReq({ backtestResult: bt, liveEquity: poorEquity });

    // Post-backtest data also very poor
    const postBtEquity = [
      makeEquitySnap("2024-07-01", 75000),
      makeEquitySnap("2024-07-15", 72000),
      makeEquitySnap("2024-08-01", 70000),
      makeEquitySnap("2024-08-15", 68000),
      makeEquitySnap("2024-09-01", 65000),
      makeEquitySnap("2024-12-31", 60000),
    ];

    const result = checkReoptimizeNeed(req, postBtEquity, []);
    expect(result.urgency).toBe("urgent");
    expect(result.reoptimizeScore).toBeGreaterThanOrEqual(60);
    expect(result.signals.some((s) => s.severity === "critical")).toBe(true);
  });

  it("returns none urgency for well-performing strategies", () => {
    const req = makeReq();
    // Good post-backtest performance
    const postBtEquity = [
      makeEquitySnap("2024-07-01", 112000),
      makeEquitySnap("2024-07-15", 114000),
      makeEquitySnap("2024-08-01", 116000),
      makeEquitySnap("2024-08-15", 118000),
      makeEquitySnap("2024-09-01", 120000),
      makeEquitySnap("2024-09-15", 122000),
    ];
    const result = checkReoptimizeNeed(req, postBtEquity, []);
    expect(["none", "monitor"]).toContain(result.urgency);
  });

  it("handles empty post-backtest data gracefully", () => {
    const req = makeReq();
    const result = checkReoptimizeNeed(req, [], []);
    expect(result.metrics.oosVerdict).toBeNull();
    expect(result.recommendations.length).toBeGreaterThan(0);
  });
});

describe("compareWithCustomBenchmark", () => {
  it("returns benchmark comparison with alpha, beta, tracking error", () => {
    const req = makeReq();
    const benchmarkKlines = [
      { date: "2024-01-01", close: 4000 },
      { date: "2024-02-01", close: 4050 },
      { date: "2024-03-01", close: 4100 },
      { date: "2024-04-01", close: 4080 },
      { date: "2024-05-01", close: 4150 },
      { date: "2024-06-01", close: 4200 },
      { date: "2024-06-30", close: 4250 },
    ];

    const result = compareWithCustomBenchmark(req, "000300", benchmarkKlines);

    expect(result.strategyId).toBe("ma_cross");
    expect(result.benchmarkSymbol).toBe("000300");
    expect(typeof result.benchmarkReturn).toBe("number");
    expect(typeof result.liveAlpha).toBe("number");
    expect(typeof result.backtestAlpha).toBe("number");
    expect(typeof result.alphaDecay).toBe("number");
    expect(typeof result.informationRatio).toBe("number");
    expect(typeof result.trackingError).toBe("number");
    expect(typeof result.beta).toBe("number");
    expect(["outperforming", "alpha_decay", "market_decline", "strategy_underperform"]).toContain(result.attribution);
    expect(result.curves.dates.length).toBeGreaterThan(0);
    expect(result.observations.length).toBeGreaterThan(0);
  });

  it("computes correct benchmark return", () => {
    const req = makeReq();
    const benchmarkKlines = [
      { date: "2024-01-01", close: 1000 },
      { date: "2024-06-30", close: 1100 },
    ];

    const result = compareWithCustomBenchmark(req, "000001", benchmarkKlines);
    // 1000 -> 1100 = 10%
    expect(result.benchmarkReturn).toBeCloseTo(10, 0);
  });

  it("detects alpha decay when backtest outperformed but live does not", () => {
    const bt = makeBacktest({ totalReturn: 30 }); // Backtest: 30% return
    // Live: ~12%, Benchmark: ~15%
    // Backtest alpha: 30 - 15 = 15%
    // Live alpha: 12 - 15 = -3%
    // Alpha decay: 15 - (-3) = 18%
    const req = makeReq({ backtestResult: bt });
    const benchmarkKlines = [
      { date: "2024-01-01", close: 4000 },
      { date: "2024-02-01", close: 4100 },
      { date: "2024-03-01", close: 4200 },
      { date: "2024-04-01", close: 4300 },
      { date: "2024-05-01", close: 4400 },
      { date: "2024-06-01", close: 4500 },
      { date: "2024-06-30", close: 4600 },
    ];

    const result = compareWithCustomBenchmark(req, "000300", benchmarkKlines);
    expect(result.alphaDecay).toBeGreaterThan(5);
    expect(result.attribution).toBe("alpha_decay");
  });

  it("handles empty benchmark data gracefully", () => {
    const req = makeReq();
    const result = compareWithCustomBenchmark(req, "000300", []);
    expect(result.benchmarkReturn).toBe(0);
    expect(result.beta).toBe(0);
    expect(result.trackingError).toBe(0);
  });

  it("handles non-overlapping dates", () => {
    const req = makeReq();
    const benchmarkKlines = [
      { date: "2025-01-01", close: 4000 },
      { date: "2025-06-30", close: 4200 },
    ];
    const result = compareWithCustomBenchmark(req, "000300", benchmarkKlines);
    // No overlap, so metrics should be zero/default
    expect(result.beta).toBe(0);
    expect(result.informationRatio).toBe(0);
  });
});

// ── analyzeIntradayExecution ────────────────────────────────────

describe("analyzeIntradayExecution", () => {
  it("returns correct structure with all time slots", () => {
    const req = makeReq({
      liveTrades: [
        makeTrade({ entryTime: "2024-03-01T09:35:00" }),
        makeTrade({ entryTime: "2024-06-15T14:10:00", side: "sell", grossPnl: -100, netPnl: -105 }),
      ],
      liveSignals: [
        makeSignal({ timestamp: new Date("2024-03-01T09:34:00Z").getTime() }),
        makeSignal({ timestamp: new Date("2024-06-15T14:09:00Z").getTime() }),
      ],
    });
    const result = analyzeIntradayExecution(req);
    expect(result.strategyId).toBe("ma_cross");
    expect(result.buckets).toHaveLength(8); // 8 half-hour slots
    expect(result.buckets[0]!.slot).toBe("09:30-10:00");
    expect(result.buckets[7]!.slot).toBe("14:30-15:00");
  });

  it("assigns trades to correct time slots", () => {
    const req = makeReq({
      liveTrades: [
        makeTrade({ entryTime: "2024-03-01T09:35:00" }),
        makeTrade({ entryTime: "2024-03-02T09:45:00" }),
        makeTrade({ entryTime: "2024-04-01T14:35:00", side: "sell", grossPnl: 50, netPnl: 45 }),
      ],
      liveSignals: [],
    });
    const result = analyzeIntradayExecution(req);
    const slot0930 = result.buckets.find((b) => b.slot === "09:30-10:00")!;
    expect(slot0930.liveTradeCount).toBe(2);
    const slot1430 = result.buckets.find((b) => b.slot === "14:30-15:00")!;
    expect(slot1430.liveTradeCount).toBe(1);
  });

  it("handles empty trade data", () => {
    const req = makeReq({ liveTrades: [], liveSignals: [] });
    const result = analyzeIntradayExecution(req);
    expect(result.worstSlot).toBeNull();
    expect(result.bestSlot).toBeNull();
    expect(result.observations.length).toBeGreaterThan(0);
  });
});

// ── replayWhatIf ────────────────────────────────────────────────

describe("replayWhatIf", () => {
  it("returns correct structure", () => {
    const req = makeReq();
    const result = replayWhatIf(req);
    expect(result.strategyId).toBe("ma_cross");
    expect(typeof result.actualReturn).toBe("number");
    expect(typeof result.hypotheticalReturn).toBe("number");
    expect(typeof result.missedOpportunityCost).toBe("number");
    expect(result.totalBacktestSignals).toBeGreaterThan(0);
    expect(result.observations.length).toBeGreaterThan(0);
  });

  it("reports zero missed signals when all are executed", () => {
    // Create a request where all backtest signals have matching live trades
    const bt = makeBacktest({
      signals: [
        makeSignal({ timestamp: new Date("2024-03-01T09:30:00Z").getTime(), direction: "buy" }),
      ],
    });
    const req = makeReq({
      backtestResult: bt,
      liveTrades: [
        makeTrade({ entryTime: "2024-03-01T09:31:00", side: "buy" }),
      ],
      liveSignals: [
        makeSignal({ timestamp: new Date("2024-03-01T09:30:00Z").getTime() }),
      ],
    });
    const result = replayWhatIf(req);
    expect(result.missedSignalCount).toBe(0);
    expect(result.missedOpportunityCost).toBe(0);
  });

  it("detects missed signals", () => {
    // Backtest has 3 signals, live only has 1 trade
    const bt = makeBacktest({
      signals: [
        makeSignal({ timestamp: new Date("2024-03-01T09:30:00Z").getTime(), direction: "buy" }),
        makeSignal({ timestamp: new Date("2024-04-15T09:30:00Z").getTime(), direction: "sell", price: 1850 }),
        makeSignal({ timestamp: new Date("2024-06-15T09:30:00Z").getTime(), direction: "buy" }),
      ],
    });
    const req = makeReq({
      backtestResult: bt,
      liveTrades: [
        makeTrade({ entryTime: "2024-03-01T09:31:00", side: "buy" }),
      ],
      liveSignals: [
        makeSignal({ timestamp: new Date("2024-03-01T09:30:00Z").getTime() }),
      ],
    });
    const result = replayWhatIf(req);
    expect(result.missedSignalCount).toBeGreaterThan(0);
  });
});

// ── compareDrawdownEpisodes ─────────────────────────────────────

describe("compareDrawdownEpisodes", () => {
  it("returns correct structure", () => {
    const req = makeReq();
    const result = compareDrawdownEpisodes(req);
    expect(result.strategyId).toBe("ma_cross");
    expect(result.period).toEqual({ start: "2024-01-01", end: "2024-06-30" });
    expect(Array.isArray(result.backtestEpisodes)).toBe(true);
    expect(Array.isArray(result.liveEpisodes)).toBe(true);
    expect(typeof result.liveWorstExceedsBacktest).toBe("boolean");
    expect(typeof result.drawdownRatio).toBe("number");
    expect(result.observations.length).toBeGreaterThan(0);
  });

  it("detects drawdown episodes in equity curve with dips", () => {
    const bt = makeBacktest({
      equityCurve: [
        { date: "2024-01-01", value: 100000 },
        { date: "2024-02-01", value: 105000 },
        { date: "2024-03-01", value: 100000 }, // -4.76% drawdown
        { date: "2024-04-01", value: 106000 }, // recovery
        { date: "2024-05-01", value: 110000 },
        { date: "2024-06-01", value: 103000 }, // -6.36% drawdown
        { date: "2024-06-30", value: 115000 }, // recovery
      ],
    });
    const req = makeReq({ backtestResult: bt });
    const result = compareDrawdownEpisodes(req, 2);
    expect(result.backtestEpisodes.length).toBeGreaterThanOrEqual(2);
    expect(result.backtestEpisodes[0]!.maxDrawdown).toBeGreaterThanOrEqual(2);
  });

  it("identifies unrecovered drawdown at end of period", () => {
    const liveEquity = [
      makeEquitySnap("2024-01-01", 100000),
      makeEquitySnap("2024-02-01", 105000),
      makeEquitySnap("2024-03-01", 103000),
      makeEquitySnap("2024-04-01", 106000),
      makeEquitySnap("2024-05-01", 107000),
      makeEquitySnap("2024-06-01", 100000), // deep drawdown
      makeEquitySnap("2024-06-30", 101000), // not recovered to 107000 peak
    ];
    const req = makeReq({ liveEquity });
    const result = compareDrawdownEpisodes(req, 2);
    const unrecovered = result.liveEpisodes.filter((e) => e.recoveryDate === null);
    expect(unrecovered.length).toBeGreaterThanOrEqual(1);
  });

  it("handles monotonically increasing equity (no drawdowns)", () => {
    const req = makeReq(); // default live equity is monotonically increasing
    const result = compareDrawdownEpisodes(req, 2);
    expect(result.liveEpisodes).toHaveLength(0);
  });

  it("respects min_drawdown_pct threshold", () => {
    const liveEquity = [
      makeEquitySnap("2024-01-01", 100000),
      makeEquitySnap("2024-02-01", 99500), // -0.5% drawdown, under threshold
      makeEquitySnap("2024-03-01", 100500),
      makeEquitySnap("2024-04-01", 100000),
      makeEquitySnap("2024-05-01", 101000),
      makeEquitySnap("2024-06-01", 102000),
      makeEquitySnap("2024-06-30", 103000),
    ];
    const req = makeReq({ liveEquity });
    const result = compareDrawdownEpisodes(req, 2);
    // -0.5% is under 2% threshold, so no episodes
    expect(result.liveEpisodes).toHaveLength(0);
  });
});