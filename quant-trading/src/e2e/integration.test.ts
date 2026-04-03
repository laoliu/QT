/**
 * E2E integration tests — security + monitoring + alerts flow.
 *
 * Tests the complete interaction between:
 *   - Auth middleware (Bearer token validation)
 *   - CORS middleware (cross-origin headers)
 *   - Rate limiter (per-IP throttling)
 *   - Alert Store (persistent storage, query, acknowledge)
 *   - Data Staleness Monitor (detection, alerting)
 *   - LiveEngine connection health (failure tracking, alerts)
 *   - Backtest Store (save, query, compare, performance)
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ── Security E2E ─────────────────────────────────────────────────

describe("E2E: security flow", () => {
  it("should reject requests without Bearer token when apiToken is configured", async () => {
    const { withAuth } = await import("../http/auth-middleware.js");
    const config = { apiToken: "secret-token-123" } as Record<string, unknown>;
    const handler = vi.fn(
      (
        _req: unknown,
        res: { writeHead: (...args: unknown[]) => void; end: (...args: unknown[]) => void },
      ) => {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ ok: true }));
      },
    );
    const secured = withAuth(config as never, handler as never);

    // No token → 401
    const res401 = { writeHead: vi.fn(), end: vi.fn() };
    const req401 = { headers: {}, url: "/quant/api/portfolio", method: "GET" };
    await secured(req401 as never, res401 as never);
    expect(res401.writeHead).toHaveBeenCalledWith(401, expect.any(Object));
    expect(handler).not.toHaveBeenCalled();

    // Wrong token → 401
    const resBad = { writeHead: vi.fn(), end: vi.fn() };
    const reqBad = {
      headers: { authorization: "Bearer wrong-token" },
      url: "/quant/api/portfolio",
      method: "GET",
    };
    await secured(reqBad as never, resBad as never);
    expect(resBad.writeHead).toHaveBeenCalledWith(401, expect.any(Object));

    // Correct token → handler called
    const resOk = { writeHead: vi.fn(), end: vi.fn() };
    const reqOk = {
      headers: { authorization: "Bearer secret-token-123" },
      url: "/quant/api/portfolio",
      method: "GET",
    };
    await secured(reqOk as never, resOk as never);
    expect(handler).toHaveBeenCalledOnce();
  });

  it("should allow public paths through auth middleware", async () => {
    const { withAuth } = await import("../http/auth-middleware.js");
    const config = { apiToken: "secret" } as Record<string, unknown>;
    const handler = vi.fn(
      (
        _req: unknown,
        res: { writeHead: (...args: unknown[]) => void; end: (...args: unknown[]) => void },
      ) => {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ ok: true }));
      },
    );
    const secured = withAuth(config as never, handler as never, {
      publicPaths: ["/quant/dashboard/ui"],
    });

    const res = { writeHead: vi.fn(), end: vi.fn() };
    const req = { headers: {}, url: "/quant/dashboard/ui", method: "GET" };
    await secured(req as never, res as never);
    expect(handler).toHaveBeenCalledOnce();
  });

  it("should compose full security stack: auth → CORS → rate-limit", async () => {
    const { withSecurity, RateLimiter } = await import("../http/auth-middleware.js");
    const config = {
      apiToken: "tok",
      corsOrigins: ["http://localhost:3000"],
      rateLimit: { maxRequests: 2, windowMs: 60000 },
    } as Record<string, unknown>;

    const handler = vi.fn(
      (
        _req: unknown,
        res: { writeHead: (...args: unknown[]) => void; end: (...args: unknown[]) => void },
      ) => {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ data: "ok" }));
      },
    );
    const limiter = new RateLimiter(2, 60000);
    const secured = withSecurity(config as never, handler as never, { rateLimiter: limiter });

    const baseReq = {
      headers: { authorization: "Bearer tok", origin: "http://localhost:3000" },
      url: "/quant/api/orders",
      method: "GET",
      socket: { remoteAddress: "10.0.0.1" },
    };

    // First request — passes all layers
    const res1 = { writeHead: vi.fn(), end: vi.fn(), setHeader: vi.fn() };
    await secured({ ...baseReq } as never, res1 as never);
    expect(handler).toHaveBeenCalledTimes(1);

    // Second request — still within rate limit
    const res2 = { writeHead: vi.fn(), end: vi.fn(), setHeader: vi.fn() };
    await secured({ ...baseReq } as never, res2 as never);
    expect(handler).toHaveBeenCalledTimes(2);

    // Third request — exceeds rate limit
    const res3 = { writeHead: vi.fn(), end: vi.fn(), setHeader: vi.fn() };
    await secured({ ...baseReq } as never, res3 as never);
    expect(res3.writeHead).toHaveBeenCalledWith(429, expect.any(Object));
    expect(handler).toHaveBeenCalledTimes(2); // Not called again
  });

  it("should handle CORS preflight OPTIONS request", async () => {
    const { withCors } = await import("../http/auth-middleware.js");
    const config = { corsOrigins: ["http://example.com"] } as Record<string, unknown>;
    const handler = vi.fn();
    const corsHandler = withCors(config as never, handler as never);

    const res = { writeHead: vi.fn(), end: vi.fn(), setHeader: vi.fn() };
    const req = {
      method: "OPTIONS",
      headers: { origin: "http://example.com" },
      url: "/quant/api/portfolio",
    };
    await corsHandler(req as never, res as never);
    expect(res.writeHead).toHaveBeenCalledWith(204);
    expect(res.setHeader).toHaveBeenCalledWith("Access-Control-Allow-Origin", "http://example.com");
    expect(handler).not.toHaveBeenCalled(); // OPTIONS short-circuits
  });
});

// ── Monitoring E2E ───────────────────────────────────────────────

describe("E2E: monitoring flow", () => {
  it("should detect data staleness and fire alert callback", async () => {
    vi.useFakeTimers();
    const { DataStalenessMonitor } = await import("../services/data-staleness-monitor.js");

    const alerts: Array<{ severity: string; message: string; symbol: string }> = [];
    const monitor = new DataStalenessMonitor(
      { warningThresholdMs: 1000, criticalThresholdMs: 3000, checkIntervalMs: 500 },
      { warn: vi.fn(), error: vi.fn(), info: vi.fn() },
      {},
      (severity, message, symbol) => {
        alerts.push({ severity, message, symbol });
      },
    );

    monitor.registerSymbols(["600519", "000001"]);
    monitor.start();

    // Record initial quotes
    monitor.recordQuote("600519");
    monitor.recordQuote("000001");

    // Advance past warning threshold (1s)
    await vi.advanceTimersByTimeAsync(1500);

    // Should have warning alerts for both symbols
    expect(alerts.length).toBeGreaterThanOrEqual(2);
    expect(alerts.some((a) => a.severity === "warning" && a.symbol === "600519")).toBe(true);
    expect(alerts.some((a) => a.severity === "warning" && a.symbol === "000001")).toBe(true);

    // Record a fresh quote for 600519 only
    monitor.recordQuote("600519");

    // Advance past critical threshold
    await vi.advanceTimersByTimeAsync(3500);

    // 000001 should now be critical, 600519 may get a new warning
    expect(alerts.some((a) => a.severity === "critical" && a.symbol === "000001")).toBe(true);

    monitor.stop();
    vi.useRealTimers();
  });

  it("should generate a staleness report", async () => {
    const { DataStalenessMonitor } = await import("../services/data-staleness-monitor.js");

    const monitor = new DataStalenessMonitor(
      { warningThresholdMs: 500, criticalThresholdMs: 2000 },
      { warn: vi.fn(), error: vi.fn(), info: vi.fn() },
    );

    monitor.registerSymbols(["600519"]);
    monitor.recordQuote("600519");

    const report = monitor.getReport();
    expect(report.totalSymbols).toBe(1);
    expect(report.symbols).toHaveLength(1);
    expect(report.symbols[0].symbol).toBe("600519");
    expect(report.symbols[0].status).toBe("fresh");
  });

  it("should track connection health alerts end-to-end", async () => {
    const { AlertStore } = await import("../services/alert-store.js");

    const alertStore = new AlertStore({ maxAlerts: 100 });

    // Simulate what LiveEngine does when connection fails
    const onConnectionAlert = (severity: string, message: string) => {
      alertStore.add(severity as never, "connection_health", message);
    };

    onConnectionAlert("warning", "Broker connection degraded: 3 consecutive failures");
    onConnectionAlert("critical", "Broker connection error: 5 consecutive failures, reconnecting");

    const summary = alertStore.getSummary();
    expect(summary.total).toBe(2);
    expect(summary.bySeverity.warning).toBe(1);
    expect(summary.bySeverity.critical).toBe(1);
    expect(summary.bySource.connection_health).toBe(2);

    // Query only critical alerts
    const criticals = alertStore.query({ severity: "critical" });
    expect(criticals).toHaveLength(1);
    expect(criticals[0].message).toContain("reconnecting");

    // Acknowledge all
    const count = alertStore.acknowledgeAll();
    expect(count).toBe(2);
    expect(alertStore.getSummary().unacknowledged).toBe(0);
  });

  it("should wire PnL alerts to alert store", async () => {
    const { AlertStore } = await import("../services/alert-store.js");

    const alertStore = new AlertStore({ maxAlerts: 100 });

    // Simulate the PnL → AlertStore callback from index.ts
    const pnlAlert = {
      severity: "warning" as const,
      trigger: "daily_loss",
      message: "Daily loss -3.2% exceeds warning threshold -2%",
      pnlPct: -3.2,
    };

    alertStore.add(pnlAlert.severity, "pnl_monitor", pnlAlert.message, {
      trigger: pnlAlert.trigger,
      pnlPct: pnlAlert.pnlPct,
    });

    const alerts = alertStore.query({ source: "pnl_monitor" });
    expect(alerts).toHaveLength(1);
    expect(alerts[0].metadata).toEqual({ trigger: "daily_loss", pnlPct: -3.2 });
  });
});

// ── Backtest Persistence E2E ─────────────────────────────────────

describe("E2E: backtest persistence flow", () => {
  let testDir: string;

  beforeEach(async () => {
    const { mkdirSync } = await import("node:fs");
    const { join } = await import("node:path");
    const { tmpdir } = await import("node:os");
    testDir = join(tmpdir(), `e2e-bt-${Date.now()}-${Math.random().toString(36).slice(2)}`);
    mkdirSync(testDir, { recursive: true });
  });

  afterEach(async () => {
    const { rmSync, existsSync } = await import("node:fs");
    try {
      if (existsSync(testDir)) rmSync(testDir, { recursive: true, force: true });
    } catch {
      // best-effort
    }
  });

  it("should save backtest → query history → compare → get summary", async () => {
    const { BacktestStore } = await import("../services/backtest-store.js");
    const store = new BacktestStore({ dataDir: testDir, maxResults: 100 });
    store.start();

    // Save 3 backtest runs with different results
    const id1 = await store.save(
      {
        strategyId: "dual-ma",
        symbols: ["600519"],
        startDate: "2024-01-01",
        endDate: "2024-06-30",
        initialCash: 100000,
        finalValue: 115000,
        totalReturn: 15.0,
        annualizedReturn: 30.0,
        maxDrawdown: 8.0,
        sharpeRatio: 1.8,
        winRate: 60,
        totalTrades: 25,
        profitFactor: 2.1,
        signals: [],
        equityCurve: [
          { date: "2024-01-01", value: 100000 },
          { date: "2024-06-30", value: 115000 },
        ],
      },
      { tags: ["good-run"], engine: "builtin" },
    );

    const id2 = await store.save(
      {
        strategyId: "dual-ma",
        symbols: ["600519"],
        startDate: "2024-07-01",
        endDate: "2024-12-31",
        initialCash: 100000,
        finalValue: 92000,
        totalReturn: -8.0,
        annualizedReturn: -16.0,
        maxDrawdown: 15.0,
        sharpeRatio: -0.5,
        winRate: 35,
        totalTrades: 20,
        profitFactor: 0.7,
        signals: [],
        equityCurve: [
          { date: "2024-07-01", value: 100000 },
          { date: "2024-12-31", value: 92000 },
        ],
      },
      { tags: ["bad-run"], engine: "builtin" },
    );

    const id3 = await store.save(
      {
        strategyId: "momentum",
        symbols: ["000001", "000858"],
        startDate: "2024-01-01",
        endDate: "2024-12-31",
        initialCash: 200000,
        finalValue: 250000,
        totalReturn: 25.0,
        annualizedReturn: 25.0,
        maxDrawdown: 10.0,
        sharpeRatio: 2.0,
        winRate: 55,
        totalTrades: 40,
        profitFactor: 1.9,
        signals: [],
        equityCurve: [
          { date: "2024-01-01", value: 200000 },
          { date: "2024-12-31", value: 250000 },
        ],
      },
      { tags: ["good-run", "multi-symbol"], engine: "backtrader" },
    );

    // Query all — should return 3 results
    expect(store.query()).toHaveLength(3);

    // Query by strategy
    expect(store.query({ strategyId: "dual-ma" })).toHaveLength(2);
    expect(store.query({ strategyId: "momentum" })).toHaveLength(1);

    // Query by tag
    expect(store.query({ tag: "good-run" })).toHaveLength(2);

    // Query sorted by return desc
    const sorted = store.query({ sortBy: "totalReturn", sortDir: "desc" });
    expect(sorted[0].totalReturn).toBe(25);
    expect(sorted[2].totalReturn).toBe(-8);

    // Compare id1 and id2 (same strategy, different periods)
    const cmp = store.compare([id1, id2])!;
    expect(cmp).not.toBeNull();
    expect(cmp.metrics.totalReturn.best.id).toBe(id1);
    expect(cmp.metrics.totalReturn.worst.id).toBe(id2);
    expect(cmp.metrics.maxDrawdown.best.id).toBe(id1); // 8% < 15%
    expect(cmp.metrics.sharpeRatio.best.id).toBe(id1);

    // Compare all 3
    const cmpAll = store.compare([id1, id2, id3])!;
    expect(cmpAll.metrics.totalReturn.best.id).toBe(id3); // 25%
    expect(cmpAll.metrics.totalReturn.avg).toBeCloseTo((15 - 8 + 25) / 3);

    // Performance summary
    const summary = store.getPerformanceSummary();
    expect(summary.totalRuns).toBe(3);
    expect(summary.profitableRuns).toBe(2);
    expect(summary.bestRun!.totalReturn).toBe(25);
    expect(summary.worstRun!.totalReturn).toBe(-8);
    expect(summary.byStrategy["dual-ma"].runs).toBe(2);
    expect(summary.byStrategy["momentum"].runs).toBe(1);

    // Performance summary filtered by strategy
    const maOnly = store.getPerformanceSummary("dual-ma");
    expect(maOnly.totalRuns).toBe(2);
    expect(maOnly.avgReturn).toBeCloseTo(3.5); // (15 + -8) / 2

    // Star and tag management
    await store.toggleStar(id1);
    expect(store.getEntry(id1)!.starred).toBe(true);
    await store.setNotes(id2, "Need to investigate drawdown");
    expect(store.getFullResult(id2)!.notes).toBe("Need to investigate drawdown");

    // Verify persistence — reload from disk
    const store2 = new BacktestStore({ dataDir: testDir, maxResults: 100 });
    store2.start();
    expect(store2.size).toBe(3);
    expect(store2.getEntry(id1)!.starred).toBe(true);
    expect(store2.getFullResult(id2)!.notes).toBe("Need to investigate drawdown");
  });

  it("should handle the full lifecycle: save → delete → verify pruned from disk", async () => {
    const { BacktestStore } = await import("../services/backtest-store.js");
    const { existsSync } = await import("node:fs");
    const { join } = await import("node:path");

    const store = new BacktestStore({ dataDir: testDir, maxResults: 100 });
    store.start();

    const id = await store.save({
      strategyId: "test",
      symbols: ["600519"],
      startDate: "2024-01-01",
      endDate: "2024-12-31",
      initialCash: 100000,
      finalValue: 110000,
      totalReturn: 10,
      annualizedReturn: 10,
      maxDrawdown: 5,
      sharpeRatio: 1.2,
      winRate: 50,
      totalTrades: 10,
      signals: [],
      equityCurve: [],
    });

    // Verify file exists
    expect(existsSync(join(testDir, `${id}.json`))).toBe(true);

    // Delete
    await store.delete(id);
    expect(store.size).toBe(0);
    expect(existsSync(join(testDir, `${id}.json`))).toBe(false);

    // Reload — should be empty
    const store2 = new BacktestStore({ dataDir: testDir, maxResults: 100 });
    store2.start();
    expect(store2.size).toBe(0);
  });
});

// ── Config Validation E2E ────────────────────────────────────────

describe("E2E: config validation flow", () => {
  it("should catch security misconfigurations at startup", async () => {
    const { validateConfig } = await import("../config-validator.js");

    const config = {
      apiToken: "ab", // too short
      corsOrigins: "*" as unknown as string[], // wrong type
      rateLimit: { maxRequests: -1, windowMs: 0 }, // invalid values
    };

    const issues = validateConfig(config as never);

    // Should have warnings/errors for all bad fields
    expect(issues.length).toBeGreaterThanOrEqual(3);
    expect(issues.some((i) => i.path === "apiToken")).toBe(true);
    expect(issues.some((i) => i.path === "corsOrigins")).toBe(true);
    expect(issues.some((i) => i.path?.startsWith("rateLimit"))).toBe(true);
  });

  it("should pass with valid security config", async () => {
    const { validateConfig } = await import("../config-validator.js");

    const config = {
      apiToken: "secure-long-token-value",
      corsOrigins: ["http://localhost:3000"],
      rateLimit: { maxRequests: 60, windowMs: 60000 },
    };

    const issues = validateConfig(config as never);
    const errors = issues.filter((i) => i.severity === "error");
    expect(errors).toHaveLength(0);
  });
});

// ── Multi-system Alert Integration E2E ───────────────────────────

describe("E2E: multi-source alert aggregation", () => {
  it("should aggregate alerts from PnL, staleness, and connection sources", async () => {
    const { AlertStore } = await import("../services/alert-store.js");

    const alertStore = new AlertStore({ maxAlerts: 1000 });

    // Simulate PnL alert
    alertStore.add("warning", "pnl_monitor", "Daily loss exceeds -2%", {
      trigger: "daily_loss",
      pnlPct: -2.5,
    });

    // Simulate staleness alert
    alertStore.add("warning", "data_staleness", "600519 数据超过 60 秒未更新", {
      symbol: "600519",
    });
    alertStore.add("critical", "data_staleness", "000001 数据超过 300 秒未更新", {
      symbol: "000001",
    });

    // Simulate connection alert
    alertStore.add("warning", "connection_health", "Broker degraded: 3 failures");

    // Simulate risk gate alert
    alertStore.add("critical", "risk_gate", "Position limit exceeded for 600519");

    // Summary should show all 5 alerts from 4 sources
    const summary = alertStore.getSummary();
    expect(summary.total).toBe(5);
    expect(summary.unacknowledged).toBe(5);
    expect(summary.bySeverity.warning).toBe(3);
    expect(summary.bySeverity.critical).toBe(2);
    expect(summary.bySource.pnl_monitor).toBe(1);
    expect(summary.bySource.data_staleness).toBe(2);
    expect(summary.bySource.connection_health).toBe(1);
    expect(summary.bySource.risk_gate).toBe(1);

    // Query critical only
    const criticals = alertStore.query({ severity: "critical" });
    expect(criticals).toHaveLength(2);

    // Query by source
    const stalenessAlerts = alertStore.query({ source: "data_staleness" });
    expect(stalenessAlerts).toHaveLength(2);

    // Acknowledge one, verify
    const firstId = criticals[0].id;
    alertStore.acknowledge(firstId);
    expect(alertStore.getSummary().unacknowledged).toBe(4);

    // Acknowledge all remaining
    alertStore.acknowledgeAll();
    expect(alertStore.getSummary().unacknowledged).toBe(0);
  });
});
