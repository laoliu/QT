import { describe, expect, it } from "vitest";
import type { TradeRecord } from "../types.js";
import type { EquitySnapshot } from "./equity-history.js";
import { computePerformanceMetrics, formatPerformanceReport } from "./performance-metrics.js";

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

function makeSnapshot(date: string, equity: number): EquitySnapshot {
  return {
    date,
    equity,
    cash: equity * 0.3,
    marketValue: equity * 0.7,
    dailyPnl: 0,
    positionCount: 2,
  };
}

describe("computePerformanceMetrics", () => {
  // ── Empty / edge cases ──

  it("returns zero metrics for empty trades", () => {
    const result = computePerformanceMetrics([]);
    expect(result.totalTrades).toBe(0);
    expect(result.totalPnl).toBe(0);
    expect(result.winRate).toBe(0);
    expect(result.sharpeRatio).toBe(0);
    expect(result.maxDrawdownPct).toBe(0);
  });

  it("ignores open trades (no exitTime)", () => {
    const trades = [
      makeTrade({ exitTime: "2025-01-15T14:30:00Z", netPnl: 1000 }),
      makeTrade({ exitTime: undefined, netPnl: 500 }),
    ];
    const result = computePerformanceMetrics(trades);
    expect(result.totalTrades).toBe(1);
    expect(result.totalPnl).toBe(1000);
  });

  // ── 收益指标 ──

  it("calculates total return correctly", () => {
    const trades = [
      makeTrade({ netPnl: 5000, grossPnl: 5200, commission: 200 }),
      makeTrade({ netPnl: -2000, grossPnl: -1800, commission: 200 }),
    ];
    const result = computePerformanceMetrics(trades, undefined, { initialCapital: 100_000 });
    expect(result.totalPnl).toBe(3000);
    expect(result.totalReturnPct).toBeCloseTo(3.0);
  });

  it("respects custom initialCapital", () => {
    const trades = [makeTrade({ netPnl: 5000 })];
    const result = computePerformanceMetrics(trades, undefined, { initialCapital: 50_000 });
    expect(result.totalReturnPct).toBeCloseTo(10.0);
  });

  // ── 胜率 / 盈亏比 ──

  it("calculates win rate, payoff ratio, and profit factor", () => {
    const trades = [
      makeTrade({ netPnl: 3000, grossPnl: 3100, commission: 100 }),
      makeTrade({ netPnl: 1000, grossPnl: 1100, commission: 100 }),
      makeTrade({ netPnl: -1000, grossPnl: -900, commission: 100 }),
    ];
    const result = computePerformanceMetrics(trades);
    expect(result.winningTrades).toBe(2);
    expect(result.losingTrades).toBe(1);
    expect(result.winRate).toBeCloseTo(66.67, 1);
    // avgWin = 2000, avgLoss = 1000
    expect(result.payoffRatio).toBeCloseTo(2.0);
    // profitFactor = 4000 / 1000 = 4
    expect(result.profitFactor).toBeCloseTo(4.0);
  });

  it("returns Infinity profit factor when no losses", () => {
    const trades = [
      makeTrade({ netPnl: 1000, grossPnl: 1050, commission: 50 }),
      makeTrade({ netPnl: 2000, grossPnl: 2050, commission: 50 }),
    ];
    const result = computePerformanceMetrics(trades);
    expect(result.profitFactor).toBe(Infinity);
    expect(result.winRate).toBe(100);
  });

  // ── 期望值 ──

  it("calculates expectancy correctly", () => {
    // 2 wins of 3000 and 1000, 1 loss of -1000
    // winP = 2/3, avgWin = 2000, loseP = 1/3, avgLoss = 1000
    // expectancy = (2/3)*2000 - (1/3)*1000 = 1333.33 - 333.33 = 1000
    const trades = [
      makeTrade({ netPnl: 3000 }),
      makeTrade({ netPnl: 1000 }),
      makeTrade({ netPnl: -1000 }),
    ];
    const result = computePerformanceMetrics(trades);
    expect(result.expectancy).toBeCloseTo(1000, 0);
  });

  // ── 持仓周期 ──

  it("calculates holding period stats", () => {
    const trades = [
      makeTrade({ holdingDays: 3 }),
      makeTrade({ holdingDays: 10 }),
      makeTrade({ holdingDays: 1 }),
    ];
    const result = computePerformanceMetrics(trades);
    expect(result.avgHoldingDays).toBeCloseTo(4.67, 1);
    expect(result.maxHoldingDays).toBe(10);
    expect(result.minHoldingDays).toBe(1);
  });

  it("computes holding days from timestamps when holdingDays is missing", () => {
    const trades = [
      makeTrade({
        holdingDays: undefined,
        entryTime: "2025-01-10T09:30:00Z",
        exitTime: "2025-01-17T14:30:00Z",
      }),
    ];
    const result = computePerformanceMetrics(trades);
    expect(result.avgHoldingDays).toBe(7);
  });

  // ── 连续性 ──

  it("calculates consecutive win/loss streaks", () => {
    const trades = [
      makeTrade({ exitTime: "2025-01-01T10:00:00Z", netPnl: 100 }),
      makeTrade({ exitTime: "2025-01-02T10:00:00Z", netPnl: 200 }),
      makeTrade({ exitTime: "2025-01-03T10:00:00Z", netPnl: 300 }),
      makeTrade({ exitTime: "2025-01-04T10:00:00Z", netPnl: -100 }),
      makeTrade({ exitTime: "2025-01-05T10:00:00Z", netPnl: -200 }),
      makeTrade({ exitTime: "2025-01-06T10:00:00Z", netPnl: 100 }),
    ];
    const result = computePerformanceMetrics(trades);
    expect(result.maxConsecutiveWins).toBe(3);
    expect(result.maxConsecutiveLosses).toBe(2);
  });

  // ── 回撤 ──

  it("calculates drawdown from trades", () => {
    const trades = [
      makeTrade({ exitTime: "2025-01-01T10:00:00Z", netPnl: 10000 }),
      makeTrade({ exitTime: "2025-01-02T10:00:00Z", netPnl: -5000 }),
      makeTrade({ exitTime: "2025-01-03T10:00:00Z", netPnl: -3000 }),
      makeTrade({ exitTime: "2025-01-04T10:00:00Z", netPnl: 15000 }),
    ];
    const result = computePerformanceMetrics(trades, undefined, { initialCapital: 100_000 });
    // Peak at 110000, drops to 102000, dd = 8000/110000 ≈ 7.27%
    expect(result.maxDrawdownPct).toBeGreaterThan(7);
    expect(result.maxDrawdownPct).toBeLessThan(8);
    expect(result.maxDrawdownAmount).toBeCloseTo(8000, 0);
  });

  it("calculates drawdown from equity snapshots when provided", () => {
    const trades = [makeTrade({ netPnl: 5000 })];
    const snapshots: EquitySnapshot[] = [
      makeSnapshot("2025-01-01", 100000),
      makeSnapshot("2025-01-02", 110000),
      makeSnapshot("2025-01-03", 95000), // dd from 110000
      makeSnapshot("2025-01-04", 105000),
    ];
    const result = computePerformanceMetrics(trades, snapshots);
    // Max DD = (110000-95000)/110000 ≈ 13.64%
    expect(result.maxDrawdownPct).toBeCloseTo(13.64, 1);
    // Current DD = (110000-105000)/110000 ≈ 4.55%
    expect(result.currentDrawdownPct).toBeCloseTo(4.55, 1);
  });

  // ── 风险调整指标 ──

  it("computes Sharpe/Sortino from equity snapshots", () => {
    const trades = [makeTrade({ netPnl: 5000 })];
    const snapshots: EquitySnapshot[] = [
      makeSnapshot("2025-01-01", 100000),
      makeSnapshot("2025-01-02", 100500),
      makeSnapshot("2025-01-03", 101200),
      makeSnapshot("2025-01-06", 100800),
      makeSnapshot("2025-01-07", 101500),
      makeSnapshot("2025-01-08", 102000),
    ];
    const result = computePerformanceMetrics(trades, snapshots, { riskFreeRate: 0.02 });
    // Should produce non-zero Sharpe and Sortino
    expect(result.sharpeRatio).not.toBe(0);
    expect(result.sortinoRatio).not.toBe(0);
  });

  it("computes Calmar ratio correctly", () => {
    const trades = [
      makeTrade({
        exitTime: "2025-01-01T10:00:00Z",
        netPnl: 10000,
      }),
      makeTrade({
        exitTime: "2025-01-02T10:00:00Z",
        netPnl: -5000,
      }),
    ];
    const result = computePerformanceMetrics(trades, undefined, { initialCapital: 100_000 });
    // Calmar = annualized return / max drawdown
    if (result.maxDrawdownPct > 0) {
      expect(result.calmarRatio).toBeCloseTo(
        result.annualizedReturnPct / result.maxDrawdownPct,
        2,
      );
    }
  });

  // ── 成本指标 ──

  it("calculates commission ratio", () => {
    const trades = [
      makeTrade({ grossPnl: 10000, commission: 500, netPnl: 9500 }),
      makeTrade({ grossPnl: 5000, commission: 300, netPnl: 4700 }),
    ];
    const result = computePerformanceMetrics(trades);
    expect(result.totalCommission).toBe(800);
    // commissionRatio = 800 / 15000 * 100 ≈ 5.33%
    expect(result.commissionRatio).toBeCloseTo(5.33, 1);
  });

  // ── Single trade ──

  it("handles a single trade", () => {
    const result = computePerformanceMetrics([
      makeTrade({ netPnl: 5000, holdingDays: 3 }),
    ]);
    expect(result.totalTrades).toBe(1);
    expect(result.winRate).toBe(100);
    expect(result.maxConsecutiveWins).toBe(1);
    expect(result.maxConsecutiveLosses).toBe(0);
    expect(result.avgHoldingDays).toBe(3);
  });
});

describe("formatPerformanceReport", () => {
  it("produces readable output for normal metrics", () => {
    const trades = [
      makeTrade({ netPnl: 3000, grossPnl: 3100, commission: 100, holdingDays: 5 }),
      makeTrade({ netPnl: -1000, grossPnl: -900, commission: 100, holdingDays: 2 }),
      makeTrade({ netPnl: 2000, grossPnl: 2100, commission: 100, holdingDays: 7 }),
    ];
    const metrics = computePerformanceMetrics(trades);
    const report = formatPerformanceReport(metrics);

    expect(report).toContain("交易绩效报告");
    expect(report).toContain("收益指标");
    expect(report).toContain("风险调整指标");
    expect(report).toContain("回撤指标");
    expect(report).toContain("交易质量");
    expect(report).toContain("持仓周期");
    expect(report).toContain("连续性");
    expect(report).toContain("交易成本");
  });

  it("handles zero trades gracefully", () => {
    const metrics = computePerformanceMetrics([]);
    const report = formatPerformanceReport(metrics);
    expect(report).toContain("总交易次数:       0");
  });

  it("handles Infinity profit factor", () => {
    const trades = [makeTrade({ netPnl: 5000, grossPnl: 5050, commission: 50 })];
    const metrics = computePerformanceMetrics(trades);
    const report = formatPerformanceReport(metrics);
    expect(report).toContain("∞");
  });
});
