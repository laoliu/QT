import { describe, it, expect } from "vitest";
import type { BacktestResult, TradeRecord, TradingSignal } from "../types.js";
import {
  alignTrades,
  alignTradesRaw,
  alignTradeRecords,
  computePnlDrift,
  exportAlignmentCsv,
  filterPairs,
  formatAlignmentReport,
  formatPnlDriftReport,
  formatTradeRecordAlignmentReport,
  A_SHARE_MORNING_SESSION,
  A_SHARE_AFTERNOON_SESSION,
} from "./trade-alignment.js";

// ── Helpers ──────────────────────────────────────────────────

function fakeLiveTrade(overrides: Partial<TradeRecord> = {}): TradeRecord {
  return {
    tradeId: "t-001",
    symbol: "600519",
    market: "cn_stock",
    side: "buy",
    entryTime: "2024-06-15T09:35:00.000Z",
    entryPrice: 1800,
    quantity: 100,
    grossPnl: 500,
    commission: 10,
    netPnl: 490,
    entryOrderId: "o-001",
    ...overrides,
  };
}

function fakeSignal(overrides: Partial<TradingSignal> = {}): TradingSignal {
  return {
    timestamp: new Date("2024-06-15T09:30:00.000Z").getTime(),
    symbol: "600519",
    direction: "buy",
    price: 1798,
    strategyId: "dual-ma",
    ...overrides,
  };
}

function fakeBacktestResult(signals: TradingSignal[]): BacktestResult {
  return {
    strategyId: "dual-ma",
    symbols: ["600519"],
    startDate: "2024-01-01",
    endDate: "2024-12-31",
    initialCash: 100_000,
    finalValue: 110_000,
    totalReturn: 0.1,
    annualizedReturn: 0.1,
    maxDrawdown: 0.05,
    sharpeRatio: 1.5,
    winRate: 0.6,
    totalTrades: 10,
    signals,
    equityCurve: [],
  };
}

// ── alignTrades ─────────────────────────────────────────────

describe("alignTrades", () => {
  it("matches a live trade with a close backtest signal as exact", () => {
    const live = [fakeLiveTrade()];
    const bt = fakeBacktestResult([fakeSignal()]);

    const result = alignTrades(live, bt);

    expect(result.pairs).toHaveLength(1);
    expect(result.pairs[0]!.matchType).toBe("exact");
    expect(result.pairs[0]!.liveTrade).toBeDefined();
    expect(result.pairs[0]!.backtestSignal).toBeDefined();
    expect(result.pairs[0]!.symbol).toBe("600519");
    expect(result.summary.exactMatches).toBe(1);
    expect(result.summary.liveOnlyTrades).toBe(0);
    expect(result.summary.backtestOnlySignals).toBe(0);
  });

  it("classifies time_shifted when times differ significantly", () => {
    const live = [
      fakeLiveTrade({
        entryTime: "2024-06-15T14:00:00.000Z", // 4.5 hours later than signal
      }),
    ];
    const bt = fakeBacktestResult([fakeSignal()]);

    // Use a tight tolerance so the 4.5h gap counts as time_shifted
    // Default tolerance = 1 day, exact threshold = tolerance/10 = 2.4h
    const result = alignTrades(live, bt);

    expect(result.pairs).toHaveLength(1);
    expect(result.pairs[0]!.matchType).toBe("time_shifted");
    expect(result.summary.timeShiftedMatches).toBe(1);
  });

  it("marks unmatched live trade as live_only", () => {
    // No backtest signals for this symbol
    const live = [fakeLiveTrade({ symbol: "000001" })];
    const bt = fakeBacktestResult([fakeSignal({ symbol: "600519" })]);

    const result = alignTrades(live, bt);

    const liveOnly = result.pairs.filter((p) => p.matchType === "live_only");
    const btOnly = result.pairs.filter((p) => p.matchType === "backtest_only");
    expect(liveOnly).toHaveLength(1);
    expect(liveOnly[0]!.symbol).toBe("000001");
    expect(btOnly).toHaveLength(1);
    expect(btOnly[0]!.symbol).toBe("600519");
  });

  it("marks unmatched backtest signal as backtest_only", () => {
    const live: TradeRecord[] = [];
    const bt = fakeBacktestResult([fakeSignal()]);

    const result = alignTrades(live, bt);

    expect(result.pairs).toHaveLength(1);
    expect(result.pairs[0]!.matchType).toBe("backtest_only");
    expect(result.summary.backtestOnlySignals).toBe(1);
    expect(result.summary.executionRate).toBe(0);
  });

  it("respects includeUnexecuted=false", () => {
    const live: TradeRecord[] = [];
    const bt = fakeBacktestResult([fakeSignal()]);

    const result = alignTrades(live, bt, { includeUnexecuted: false });

    expect(result.pairs).toHaveLength(0);
  });

  it("does not match when direction differs", () => {
    const live = [fakeLiveTrade({ side: "sell" })];
    const bt = fakeBacktestResult([fakeSignal({ direction: "buy" })]);

    const result = alignTrades(live, bt);

    const liveOnly = result.pairs.filter((p) => p.matchType === "live_only");
    const btOnly = result.pairs.filter((p) => p.matchType === "backtest_only");
    expect(liveOnly).toHaveLength(1);
    expect(btOnly).toHaveLength(1);
  });

  it("does not match when time exceeds tolerance", () => {
    const live = [
      fakeLiveTrade({
        entryTime: "2024-06-20T09:30:00.000Z", // 5 days later
      }),
    ];
    const bt = fakeBacktestResult([fakeSignal()]);

    const result = alignTrades(live, bt, { timeToleranceMs: 86_400_000 });

    const liveOnly = result.pairs.filter((p) => p.matchType === "live_only");
    const btOnly = result.pairs.filter((p) => p.matchType === "backtest_only");
    expect(liveOnly).toHaveLength(1);
    expect(btOnly).toHaveLength(1);
  });

  it("does not match when price exceeds tolerance", () => {
    const live = [fakeLiveTrade({ entryPrice: 2000 })]; // 11% above signal price 1798
    const bt = fakeBacktestResult([fakeSignal({ price: 1798 })]);

    const result = alignTrades(live, bt, { priceToleranceRatio: 0.05 });

    const liveOnly = result.pairs.filter((p) => p.matchType === "live_only");
    expect(liveOnly).toHaveLength(1);
  });

  it("handles multiple symbols correctly", () => {
    const live = [
      fakeLiveTrade({ tradeId: "t-1", symbol: "600519", entryPrice: 1800 }),
      fakeLiveTrade({ tradeId: "t-2", symbol: "000001", entryPrice: 15 }),
    ];
    const signals = [
      fakeSignal({ symbol: "600519", price: 1798 }),
      fakeSignal({ symbol: "000001", price: 14.8 }),
    ];
    const bt = fakeBacktestResult(signals);

    const result = alignTrades(live, bt);

    const matched = result.pairs.filter(
      (p) => p.matchType === "exact" || p.matchType === "time_shifted",
    );
    expect(matched).toHaveLength(2);
    expect(result.symbols).toContain("600519");
    expect(result.symbols).toContain("000001");
  });

  it("computes price slippage correctly", () => {
    const live = [fakeLiveTrade({ entryPrice: 1810 })];
    const bt = fakeBacktestResult([fakeSignal({ price: 1800 })]);

    const result = alignTrades(live, bt);

    expect(result.pairs[0]!.priceSlippage).toBeCloseTo(10 / 1800, 6);
  });

  it("computes execution rate", () => {
    const live = [fakeLiveTrade({ tradeId: "t-1" })];
    const signals = [
      fakeSignal({ symbol: "600519" }),
      fakeSignal({ symbol: "000858", price: 200 }), // unmatched
    ];
    const bt = fakeBacktestResult(signals);

    const result = alignTrades(live, bt);

    // 1 matched out of (1 matched + 1 backtest_only) = 0.5
    expect(result.summary.executionRate).toBeCloseTo(0.5, 5);
  });

  it("returns correct date range and strategy id", () => {
    const live = [fakeLiveTrade()];
    const bt = fakeBacktestResult([fakeSignal()]);

    const result = alignTrades(live, bt);

    expect(result.strategyId).toBe("dual-ma");
    expect(result.dateRange.from).toBe("2024-06-15");
    expect(result.dateRange.to).toBe("2024-06-15");
  });

  it("handles empty inputs", () => {
    const result = alignTrades([], fakeBacktestResult([]));

    expect(result.pairs).toHaveLength(0);
    expect(result.summary.totalPairs).toBe(0);
    expect(result.summary.executionRate).toBe(0);
  });
});

// ── alignTradesRaw ──────────────────────────────────────────

describe("alignTradesRaw", () => {
  it("aligns using raw signal array", () => {
    const live = [fakeLiveTrade()];
    const signals = [fakeSignal()];

    const result = alignTradesRaw(live, signals, { strategyId: "dual-ma" });

    expect(result.strategyId).toBe("dual-ma");
    expect(result.pairs).toHaveLength(1);
    expect(result.pairs[0]!.matchType).toBe("exact");
  });
});

// ── filterPairs ─────────────────────────────────────────────

describe("filterPairs", () => {
  it("filters by match type", () => {
    const live = [
      fakeLiveTrade({ tradeId: "t-1", symbol: "600519" }),
      fakeLiveTrade({ tradeId: "t-2", symbol: "999999" }),
    ];
    const bt = fakeBacktestResult([fakeSignal({ symbol: "600519" })]);

    const result = alignTrades(live, bt);
    const liveOnly = filterPairs(result.pairs, ["live_only"]);

    expect(liveOnly.length).toBeGreaterThanOrEqual(1);
    for (const p of liveOnly) {
      expect(p.matchType).toBe("live_only");
    }
  });
});

// ── formatAlignmentReport ───────────────────────────────────

describe("formatAlignmentReport", () => {
  it("formats a readable report", () => {
    const live = [fakeLiveTrade()];
    const bt = fakeBacktestResult([fakeSignal()]);
    const result = alignTrades(live, bt);

    const report = formatAlignmentReport(result);

    expect(report).toContain("实盘 vs 回测");
    expect(report).toContain("dual-ma");
    expect(report).toContain("600519");
    expect(report).toContain("执行率");
    expect(report).toContain("精确匹配");
  });
});

// ── alignTradeRecords ──────────────────────────────────────

function fakeBtTrade(overrides: Partial<TradeRecord> = {}): TradeRecord {
  return {
    tradeId: "bt-001",
    symbol: "600519",
    market: "cn_stock",
    side: "buy",
    entryTime: "2024-06-15T09:30:00.000Z",
    exitTime: "2024-06-18T14:30:00.000Z",
    entryPrice: 1798,
    exitPrice: 1850,
    quantity: 100,
    grossPnl: 5200,
    commission: 0,
    netPnl: 5200,
    holdingDays: 3,
    strategyId: "dual-ma",
    entryOrderId: "bt-o-001",
    exitOrderId: "bt-o-002",
    ...overrides,
  };
}

describe("alignTradeRecords", () => {
  it("matches trade records with close entry times as exact", () => {
    const live = [
      fakeLiveTrade({
        exitTime: "2024-06-18T14:35:00.000Z",
        exitPrice: 1845,
        holdingDays: 3,
      }),
    ];
    const bt = [fakeBtTrade()];

    const result = alignTradeRecords(live, bt, { strategyId: "dual-ma" });

    expect(result.pairs).toHaveLength(1);
    expect(result.pairs[0]!.matchType).toBe("exact");
    expect(result.pairs[0]!.liveTrade).toBeDefined();
    expect(result.pairs[0]!.backtestTrade).toBeDefined();
    expect(result.summary.exactMatches).toBe(1);
  });

  it("computes entry/exit slippage and pnl delta", () => {
    const live = [
      fakeLiveTrade({
        entryPrice: 1810,
        exitTime: "2024-06-18T14:30:00.000Z",
        exitPrice: 1845,
        netPnl: 3400,
        holdingDays: 3,
      }),
    ];
    const bt = [fakeBtTrade({ entryPrice: 1798, exitPrice: 1850, netPnl: 5200 })];

    const result = alignTradeRecords(live, bt, { strategyId: "dual-ma" });

    const p = result.pairs[0]!;
    expect(p.entryPriceSlippage).toBeCloseTo(12 / 1798, 5);
    expect(p.exitPriceSlippage).toBeCloseTo(-5 / 1850, 5);
    expect(p.pnlDelta).toBeCloseTo(3400 - 5200, 2);
  });

  it("marks unmatched as live_only and backtest_only", () => {
    const live = [fakeLiveTrade({ symbol: "000001" })];
    const bt = [fakeBtTrade({ symbol: "600519" })];

    const result = alignTradeRecords(live, bt, { strategyId: "dual-ma" });

    expect(result.pairs.filter((p) => p.matchType === "live_only")).toHaveLength(1);
    expect(result.pairs.filter((p) => p.matchType === "backtest_only")).toHaveLength(1);
  });

  it("provides per-symbol breakdown", () => {
    const live = [
      fakeLiveTrade({ tradeId: "t-1", symbol: "600519" }),
      fakeLiveTrade({ tradeId: "t-2", symbol: "000001", entryPrice: 15 }),
    ];
    const bt = [
      fakeBtTrade({ tradeId: "bt-1", symbol: "600519" }),
      fakeBtTrade({ tradeId: "bt-2", symbol: "000001", entryPrice: 14.8 }),
    ];

    const result = alignTradeRecords(live, bt, { strategyId: "dual-ma" });

    expect(result.symbolBreakdown).toBeDefined();
    expect(result.symbolBreakdown!["600519"]).toBeDefined();
    expect(result.symbolBreakdown!["000001"]).toBeDefined();
    expect(result.symbolBreakdown!["600519"]!.totalPairs).toBeGreaterThanOrEqual(1);
  });

  it("handles empty inputs", () => {
    const result = alignTradeRecords([], [], { strategyId: "dual-ma" });

    expect(result.pairs).toHaveLength(0);
    expect(result.summary.totalPairs).toBe(0);
  });

  it("computes pnl correlation for matched trades", () => {
    const live = [
      fakeLiveTrade({ tradeId: "t-1", netPnl: 500 }),
      fakeLiveTrade({ tradeId: "t-2", netPnl: -200, entryTime: "2024-06-16T09:35:00.000Z" }),
    ];
    const bt = [
      fakeBtTrade({ tradeId: "bt-1", netPnl: 520 }),
      fakeBtTrade({ tradeId: "bt-2", netPnl: -180, entryTime: "2024-06-16T09:30:00.000Z" }),
    ];

    const result = alignTradeRecords(live, bt, { strategyId: "dual-ma" });

    // Both have positive/negative PnL patterns, should correlate strongly
    expect(result.summary.pnlCorrelation).toBeGreaterThan(0.9);
  });
});

// ── formatTradeRecordAlignmentReport ───────────────────────

describe("formatTradeRecordAlignmentReport", () => {
  it("formats a readable trade-level report", () => {
    const live = [fakeLiveTrade({ exitTime: "2024-06-18T14:35:00.000Z", holdingDays: 3 })];
    const bt = [fakeBtTrade()];
    const result = alignTradeRecords(live, bt, { strategyId: "dual-ma" });

    const report = formatTradeRecordAlignmentReport(result);

    expect(report).toContain("实盘 vs 回测 交易记录对齐报告");
    expect(report).toContain("dual-ma");
    expect(report).toContain("600519");
    expect(report).toContain("执行率");
    expect(report).toContain("入场时间偏移");
  });
});

// ── quantitySlippage ──────────────────────────────────────────

describe("quantitySlippage", () => {
  it("computes quantity slippage for signal alignment", () => {
    const live = [fakeLiveTrade({ quantity: 200 })];
    const bt = fakeBacktestResult([fakeSignal({ quantity: 100 })]);

    const result = alignTrades(live, bt);

    expect(result.pairs[0]!.quantitySlippage).toBeCloseTo(1.0, 5); // (200-100)/100 = 1.0
  });

  it("returns undefined when backtest signal has no quantity", () => {
    const live = [fakeLiveTrade({ quantity: 200 })];
    const bt = fakeBacktestResult([fakeSignal()]);
    // fakeSignal has no quantity by default

    const result = alignTrades(live, bt);

    expect(result.pairs[0]!.quantitySlippage).toBeUndefined();
  });

  it("computes quantity slippage for trade record alignment", () => {
    const live = [
      fakeLiveTrade({ quantity: 300, exitTime: "2024-06-18T14:30:00.000Z", holdingDays: 3 }),
    ];
    const bt = [fakeBtTrade({ quantity: 200 })];

    const result = alignTradeRecords(live, bt, { strategyId: "dual-ma" });

    expect(result.pairs[0]!.quantitySlippage).toBeCloseTo(0.5, 5); // (300-200)/200
    expect(result.summary.avgQuantitySlippage).toBeCloseTo(0.5, 5);
  });
});

// ── tradingSession filtering ──────────────────────────────────

describe("trading session filtering", () => {
  it("only matches trades within morning session", () => {
    // Signal at 9:35 UTC+8 = 01:35 UTC
    const morningSignal = fakeSignal({
      timestamp: new Date("2024-06-15T01:35:00.000Z").getTime(),
    });
    // Trade at 9:40 UTC+8 = 01:40 UTC
    const morningTrade = fakeLiveTrade({
      entryTime: "2024-06-15T01:40:00.000Z",
    });
    // Trade at 14:00 UTC+8 = 06:00 UTC (afternoon)
    const afternoonTrade = fakeLiveTrade({
      tradeId: "t-afternoon",
      entryTime: "2024-06-15T06:00:00.000Z",
    });

    const bt = fakeBacktestResult([morningSignal]);
    const result = alignTrades([morningTrade, afternoonTrade], bt, {
      tradingSession: A_SHARE_MORNING_SESSION,
    });

    // Morning trade should match, afternoon trade should be excluded from session
    const matched = result.pairs.filter(
      (p) => p.matchType === "exact" || p.matchType === "time_shifted",
    );
    expect(matched).toHaveLength(1);
  });

  it("filters by afternoon session", () => {
    // Signal at 13:30 UTC+8 = 05:30 UTC
    const signal = fakeSignal({
      timestamp: new Date("2024-06-15T05:30:00.000Z").getTime(),
    });
    // Trade at 13:35 UTC+8 = 05:35 UTC
    const trade = fakeLiveTrade({
      entryTime: "2024-06-15T05:35:00.000Z",
    });

    const bt = fakeBacktestResult([signal]);
    const result = alignTrades([trade], bt, {
      tradingSession: A_SHARE_AFTERNOON_SESSION,
    });

    expect(result.pairs.filter((p) => p.matchType === "exact" || p.matchType === "time_shifted")).toHaveLength(1);
  });
});

// ── computePnlDrift ──────────────────────────────────────────

describe("computePnlDrift", () => {
  it("computes cumulative PnL drift for matched trade records", () => {
    const live = [
      fakeLiveTrade({
        tradeId: "t-1",
        netPnl: 500,
        exitTime: "2024-06-16T14:30:00.000Z",
        holdingDays: 1,
      }),
      fakeLiveTrade({
        tradeId: "t-2",
        netPnl: -200,
        entryTime: "2024-06-17T09:35:00.000Z",
        exitTime: "2024-06-18T14:30:00.000Z",
        holdingDays: 2,
      }),
    ];
    const bt = [
      fakeBtTrade({
        tradeId: "bt-1",
        netPnl: 520,
        exitTime: "2024-06-16T14:30:00.000Z",
        holdingDays: 1,
      }),
      fakeBtTrade({
        tradeId: "bt-2",
        netPnl: -180,
        entryTime: "2024-06-17T09:30:00.000Z",
        exitTime: "2024-06-18T14:30:00.000Z",
        holdingDays: 2,
      }),
    ];

    const alignment = alignTradeRecords(live, bt, { strategyId: "dual-ma" });
    const drift = computePnlDrift(alignment);

    expect(drift.strategyId).toBe("dual-ma");
    expect(drift.driftCurve.length).toBeGreaterThan(0);
    // Final drift should be (500-200) - (520-180) = 300 - 340 = -40
    expect(drift.finalDrift).toBeCloseTo(-40, 1);
  });

  it("returns empty drift for no pairs", () => {
    const alignment = alignTradeRecords([], [], { strategyId: "dual-ma" });
    const drift = computePnlDrift(alignment);

    expect(drift.driftCurve).toHaveLength(0);
    expect(drift.finalDrift).toBe(0);
    expect(drift.maxDrift).toBe(0);
  });
});

// ── formatPnlDriftReport ─────────────────────────────────────

describe("formatPnlDriftReport", () => {
  it("formats a readable drift report", () => {
    const live = [
      fakeLiveTrade({ tradeId: "t-1", netPnl: 500, exitTime: "2024-06-16T14:30:00.000Z", holdingDays: 1 }),
    ];
    const bt = [
      fakeBtTrade({ tradeId: "bt-1", netPnl: 520, exitTime: "2024-06-16T14:30:00.000Z", holdingDays: 1 }),
    ];

    const alignment = alignTradeRecords(live, bt, { strategyId: "dual-ma" });
    const drift = computePnlDrift(alignment);
    const report = formatPnlDriftReport(drift);

    expect(report).toContain("累计盈亏偏移报告");
    expect(report).toContain("dual-ma");
    expect(report).toContain("最大偏移");
    expect(report).toContain("最终偏移");
  });
});

// ── exportAlignmentCsv ──────────────────────────────────────

describe("exportAlignmentCsv", () => {
  it("exports matched pairs as CSV", () => {
    const live = [
      fakeLiveTrade({
        exitTime: "2024-06-18T14:35:00.000Z",
        exitPrice: 1845,
        holdingDays: 3,
      }),
    ];
    const bt = [fakeBtTrade()];

    const result = alignTradeRecords(live, bt, { strategyId: "dual-ma" });
    const csv = exportAlignmentCsv(result);

    // Header row + 1 data row
    const lines = csv.split("\n");
    expect(lines.length).toBe(2);
    expect(lines[0]).toContain("配对ID");
    expect(lines[0]).toContain("匹配类型");
    expect(lines[0]).toContain("实盘入场价");
    expect(lines[0]).toContain("数量偏差(%)");
    expect(lines[1]).toContain("600519");
  });

  it("handles empty result", () => {
    const result = alignTradeRecords([], [], { strategyId: "dual-ma" });
    const csv = exportAlignmentCsv(result);

    const lines = csv.split("\n");
    expect(lines.length).toBe(1); // Only header
  });

  it("escapes fields with commas", () => {
    const live = [
      fakeLiveTrade({
        tradeId: "t-with,comma",
        exitTime: "2024-06-18T14:35:00.000Z",
        holdingDays: 3,
      }),
    ];
    const bt = [fakeBtTrade()];

    const result = alignTradeRecords(live, bt, { strategyId: "dual-ma" });
    const csv = exportAlignmentCsv(result);

    // The comma in tradeId should not break CSV parsing
    expect(csv).toBeDefined();
    const lines = csv.split("\n");
    expect(lines.length).toBe(2);
  });
});

// ── open-position alignment ────────────────────────────────────

describe("open position alignment", () => {
  it("aligns live trades without exitTime (open positions)", () => {
    const live = [
      fakeLiveTrade({
        tradeId: "t-open",
        exitTime: undefined,
        exitPrice: undefined,
        holdingDays: undefined,
        netPnl: 0,
        grossPnl: 0,
      }),
    ];
    const bt = [fakeBtTrade()];

    const result = alignTradeRecords(live, bt, { strategyId: "dual-ma" });

    expect(result.pairs).toHaveLength(1);
    // Should still match on entry time + price + direction
    expect(result.pairs[0]!.matchType).toBe("exact");
    expect(result.pairs[0]!.liveTrade?.exitTime).toBeUndefined();
    // Exit slippage should be undefined for open positions
    expect(result.pairs[0]!.exitTimeDeltaMs).toBeUndefined();
    expect(result.pairs[0]!.exitPriceSlippage).toBeUndefined();
  });

  it("aligns signal with open live trade", () => {
    const live = [
      fakeLiveTrade({
        exitTime: undefined,
        exitPrice: undefined,
      }),
    ];
    const bt = fakeBacktestResult([fakeSignal()]);

    const result = alignTrades(live, bt);

    expect(result.pairs).toHaveLength(1);
    expect(result.pairs[0]!.matchType).toBe("exact");
    expect(result.pairs[0]!.liveTrade?.exitTime).toBeUndefined();
  });
});

// ── OrderSide normalization in matching ─────────────────────────

describe("OrderSide normalization", () => {
  it("matches sell_close live trade with sell backtest signal", () => {
    const live = [fakeLiveTrade({ side: "sell_close" })];
    const bt = fakeBacktestResult([fakeSignal({ direction: "sell" })]);

    const result = alignTrades(live, bt);

    const matched = result.pairs.filter(
      (p) => p.matchType === "exact" || p.matchType === "time_shifted",
    );
    expect(matched).toHaveLength(1);
  });

  it("matches buy_open live trade with buy backtest signal", () => {
    const live = [fakeLiveTrade({ side: "buy_open" })];
    const bt = fakeBacktestResult([fakeSignal({ direction: "buy" })]);

    const result = alignTrades(live, bt);

    const matched = result.pairs.filter(
      (p) => p.matchType === "exact" || p.matchType === "time_shifted",
    );
    expect(matched).toHaveLength(1);
  });

  it("matches cover trade with buy signal", () => {
    const live = [fakeLiveTrade({ side: "cover" })];
    const bt = fakeBacktestResult([fakeSignal({ direction: "buy" })]);

    const result = alignTrades(live, bt);

    const matched = result.pairs.filter(
      (p) => p.matchType === "exact" || p.matchType === "time_shifted",
    );
    expect(matched).toHaveLength(1);
  });
});

// ── formatAlignmentReport per-symbol breakdown ──────────────────

describe("formatAlignmentReport per-symbol breakdown", () => {
  it("includes per-symbol breakdown when multiple symbols", () => {
    const live = [
      fakeLiveTrade({ tradeId: "t-1", symbol: "600519", entryPrice: 1800 }),
      fakeLiveTrade({ tradeId: "t-2", symbol: "000001", entryPrice: 15 }),
    ];
    const signals = [
      fakeSignal({ symbol: "600519", price: 1798 }),
      fakeSignal({ symbol: "000001", price: 14.8 }),
    ];
    const bt = fakeBacktestResult(signals);

    const result = alignTrades(live, bt);
    const report = formatAlignmentReport(result);

    expect(report).toContain("标的明细");
    expect(report).toContain("600519");
    expect(report).toContain("000001");
    expect(report).toContain("执行率");
  });
});
