import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  StrategyHealthMonitor,
  adaptiveHealthConfig,
  computeRollingVolatility,
} from "./strategy-health.js";

describe("StrategyHealthMonitor", () => {
  let tmpDir: string;
  let monitor: StrategyHealthMonitor;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "strategy-health-"));
    monitor = new StrategyHealthMonitor(tmpDir);
  });

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  // ── Basic tracking ──

  it("tracks a trade and returns health status", () => {
    monitor.recordTrade("s1", 1000, new Date().toISOString());
    const status = monitor.checkStrategy("s1");
    expect(status).not.toBeNull();
    expect(status!.strategyId).toBe("s1");
    expect(status!.totalTrades).toBe(1);
    expect(status!.winRate).toBe(1);
    expect(status!.level).toBe("healthy");
  });

  it("returns null for unknown strategy", () => {
    expect(monitor.checkStrategy("unknown")).toBeNull();
  });

  it("lists tracked strategies", () => {
    monitor.recordTrade("s1", 100, "2025-01-10T10:00:00Z");
    monitor.recordTrade("s2", -50, "2025-01-10T10:00:00Z");
    expect(monitor.getTrackedStrategies()).toEqual(expect.arrayContaining(["s1", "s2"]));
  });

  // ── Win rate warning ──

  it("warns when win rate drops below threshold", () => {
    // Need ≥5 trades for win rate check
    for (let i = 0; i < 5; i++) {
      monitor.recordTrade("s1", i < 1 ? 100 : -100, `2025-01-${String(10 + i)}T10:00:00Z`);
    }
    const status = monitor.checkStrategy("s1")!;
    expect(status.winRate).toBe(0.2); // 1/5
    expect(status.level).not.toBe("healthy");
    expect(status.issues.length).toBeGreaterThan(0);
    expect(status.issues[0]).toContain("胜率过低");
  });

  it("stays healthy with good win rate", () => {
    for (let i = 0; i < 5; i++) {
      monitor.recordTrade("s1", i < 3 ? 100 : -100, `2025-01-${String(10 + i)}T10:00:00Z`);
    }
    const status = monitor.checkStrategy("s1")!;
    expect(status.winRate).toBe(0.6);
    // 0.6 > 0.3 threshold
    expect(status.issues.filter((i) => i.includes("胜率"))).toHaveLength(0);
  });

  // ── Consecutive losses ──

  it("escalates to critical on consecutive losses", () => {
    const monitor2 = new StrategyHealthMonitor(tmpDir, { maxConsecutiveLosses: 3 });
    monitor2.recordTrade("s1", -100, "2025-01-10T10:00:00Z");
    monitor2.recordTrade("s1", -100, "2025-01-11T10:00:00Z");
    monitor2.recordTrade("s1", -100, "2025-01-12T10:00:00Z");
    const status = monitor2.checkStrategy("s1")!;
    expect(status.consecutiveLosses).toBe(3);
    expect(status.level).toBe("critical");
    expect(status.issues.some((i) => i.includes("连续亏损"))).toBe(true);
  });

  it("resets consecutive losses after a win", () => {
    monitor.recordTrade("s1", -100, "2025-01-10T10:00:00Z");
    monitor.recordTrade("s1", -100, "2025-01-11T10:00:00Z");
    monitor.recordTrade("s1", 500, "2025-01-12T10:00:00Z"); // win
    const status = monitor.checkStrategy("s1")!;
    expect(status.consecutiveLosses).toBe(0);
  });

  // ── Quiet strategy ──

  it("warns when strategy has been quiet for too long", () => {
    const monitor2 = new StrategyHealthMonitor(tmpDir, { maxQuietDays: 3 });
    const oldDate = new Date(Date.now() - 5 * 24 * 60 * 60 * 1000).toISOString();
    monitor2.recordTrade("s1", 100, oldDate);
    const status = monitor2.checkStrategy("s1")!;
    expect(status.daysSinceLastSignal).toBeGreaterThanOrEqual(4);
    expect(status.issues.some((i) => i.includes("天无信号"))).toBe(true);
  });

  // ── Signal recording ──

  it("updates lastSignalTime on recordSignal", () => {
    monitor.recordSignal("s1", "2025-06-01T12:00:00Z");
    const status = monitor.checkStrategy("s1")!;
    expect(status.daysSinceLastSignal).not.toBeNull();
    expect(status.totalTrades).toBe(0);
  });

  // ── Rolling window ──

  it("keeps only windowSize recent results", () => {
    const monitor2 = new StrategyHealthMonitor(tmpDir, { windowSize: 5 });
    for (let i = 0; i < 10; i++) {
      monitor2.recordTrade("s1", i < 7 ? 100 : -100, `2025-01-${String(10 + i)}T10:00:00Z`);
    }
    const status = monitor2.checkStrategy("s1")!;
    expect(status.totalTrades).toBe(10);
    // Only last 5 trades in window: indices 5-9 → 2 wins (5,6) + 3 losses (7,8,9)
    expect(status.winRate).toBe(0.4); // 2/5
  });

  // ── checkAll ──

  it("returns all strategies sorted by severity", () => {
    const monitor2 = new StrategyHealthMonitor(tmpDir, { maxConsecutiveLosses: 2 });
    // s1: critical (2 consecutive losses)
    monitor2.recordTrade("s1", -100, "2025-01-10T10:00:00Z");
    monitor2.recordTrade("s1", -100, "2025-01-11T10:00:00Z");
    // s2: healthy (1 win)
    monitor2.recordTrade("s2", 1000, new Date().toISOString());

    const all = monitor2.checkAll();
    expect(all).toHaveLength(2);
    expect(all[0].strategyId).toBe("s1");
    expect(all[0].level).toBe("critical");
    expect(all[1].strategyId).toBe("s2");
    expect(all[1].level).toBe("healthy");
  });

  // ── Remove & Clear ──

  it("removes a strategy from tracking", () => {
    monitor.recordTrade("s1", 100, "2025-01-10T10:00:00Z");
    expect(monitor.remove("s1")).toBe(true);
    expect(monitor.checkStrategy("s1")).toBeNull();
    expect(monitor.remove("s1")).toBe(false);
  });

  it("clears all records", () => {
    monitor.recordTrade("s1", 100, "2025-01-10T10:00:00Z");
    monitor.recordTrade("s2", -50, "2025-01-10T10:00:00Z");
    monitor.clear();
    expect(monitor.getTrackedStrategies()).toHaveLength(0);
  });

  // ── Notification ──

  it("fires notification on unhealthy trade", () => {
    const notify = vi.fn();
    const monitor2 = new StrategyHealthMonitor(tmpDir, { maxConsecutiveLosses: 2 }, notify);
    monitor2.recordTrade("s1", -100, "2025-01-10T10:00:00Z");
    monitor2.recordTrade("s1", -100, "2025-01-11T10:00:00Z");
    expect(notify).toHaveBeenCalled();
    expect(notify.mock.calls[0][0]).toContain("策略健康预警");
  });

  // ── Persistence ──

  it("persists and reloads data", () => {
    monitor.recordTrade("s1", 1000, "2025-01-10T10:00:00Z");
    monitor.recordTrade("s1", -200, "2025-01-11T10:00:00Z");

    // Reload
    const monitor2 = new StrategyHealthMonitor(tmpDir);
    const status = monitor2.checkStrategy("s1");
    expect(status).not.toBeNull();
    expect(status!.totalTrades).toBe(2);
    expect(status!.winRate).toBe(0.5);
  });

  // ── recentPnl ──

  it("calculates recentPnl correctly", () => {
    monitor.recordTrade("s1", 1000, "2025-01-10T10:00:00Z");
    monitor.recordTrade("s1", -300, "2025-01-11T10:00:00Z");
    const status = monitor.checkStrategy("s1")!;
    expect(status.recentPnl).toBe(700);
  });
});

// ── Adaptive Health Config ───────────────────────────────────────

describe("adaptiveHealthConfig", () => {
  const baseConfig = {
    minWinRate: 0.3,
    maxConsecutiveLosses: 5,
    maxQuietDays: 7,
    windowSize: 20,
  };

  it("returns base config for normal volatility", () => {
    const config = adaptiveHealthConfig(0.2, 0.2, baseConfig);
    expect(config.minWinRate).toBe(0.3);
    expect(config.maxConsecutiveLosses).toBe(5);
    expect(config.maxQuietDays).toBe(7);
  });

  it("relaxes thresholds in high volatility", () => {
    // vol ratio = 0.4 / 0.2 = 2.0 (> 1.3)
    const config = adaptiveHealthConfig(0.4, 0.2, baseConfig);
    expect(config.minWinRate).toBeLessThan(baseConfig.minWinRate);
    expect(config.maxConsecutiveLosses).toBeGreaterThan(baseConfig.maxConsecutiveLosses);
    expect(config.maxQuietDays).toBeGreaterThan(baseConfig.maxQuietDays);
  });

  it("tightens thresholds in low volatility", () => {
    // vol ratio = 0.1 / 0.2 = 0.5 (< 0.7)
    const config = adaptiveHealthConfig(0.1, 0.2, baseConfig);
    expect(config.minWinRate).toBeGreaterThan(baseConfig.minWinRate);
    expect(config.maxConsecutiveLosses).toBeLessThan(baseConfig.maxConsecutiveLosses);
    expect(config.maxQuietDays).toBeLessThan(baseConfig.maxQuietDays);
  });

  it("handles zero historic volatility gracefully", () => {
    const config = adaptiveHealthConfig(0.1, 0, baseConfig);
    expect(config).toEqual(baseConfig);
  });

  it("clamps min win rate bounds", () => {
    // High vol: minWinRate should not go below 0.2
    const highConfig = adaptiveHealthConfig(1.0, 0.5, {
      ...baseConfig,
      minWinRate: 0.22,
    });
    expect(highConfig.minWinRate).toBeGreaterThanOrEqual(0.2);

    // Low vol: minWinRate should not exceed 0.45
    const lowConfig = adaptiveHealthConfig(0.05, 0.2, {
      ...baseConfig,
      minWinRate: 0.42,
    });
    expect(lowConfig.minWinRate).toBeLessThanOrEqual(0.45);
  });
});

// ── Rolling Volatility ───────────────────────────────────────────

describe("computeRollingVolatility", () => {
  it("returns zeros for insufficient data", () => {
    const result = computeRollingVolatility([100, 101, 102], 20);
    expect(result.recent).toBe(0);
    expect(result.historic).toBe(0);
  });

  it("computes positive volatility for varying prices", () => {
    // Generate 100 prices with some variance
    const closes: number[] = [];
    for (let i = 0; i < 100; i++) {
      closes.push(100 + 10 * Math.sin(i / 5));
    }
    const result = computeRollingVolatility(closes, 20);
    expect(result.recent).toBeGreaterThan(0);
    expect(result.historic).toBeGreaterThan(0);
  });

  it("returns zero volatility for flat prices", () => {
    const closes = Array.from({ length: 100 }, () => 100);
    const result = computeRollingVolatility(closes, 20);
    expect(result.recent).toBe(0);
    expect(result.historic).toBe(0);
  });
});
