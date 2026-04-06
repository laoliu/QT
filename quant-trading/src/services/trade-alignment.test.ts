import { describe, it, expect } from "vitest";
import type { BacktestResult, TradeRecord, TradingSignal } from "../types.js";
import {
  alignTrades,
  alignTradesRaw,
  filterPairs,
  formatAlignmentReport,
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
