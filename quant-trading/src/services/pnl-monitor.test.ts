import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { OpenClawPluginServiceContext } from "openclaw/plugin-sdk/core";
/**
 * Unit tests for PnlMonitor — background PnL monitoring + alert severity.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import type { BrokerAccount, LivePosition, QuantTradingConfig } from "../types.js";
import { EquityHistory } from "./equity-history.js";
import { OrderManager } from "./order-manager.js";

// Mock isMarketOpen
vi.mock("../broker/paper-broker.js", () => ({
  isMarketOpen: vi.fn().mockReturnValue(true),
}));

import { isMarketOpen } from "../broker/paper-broker.js";
import { createPnlMonitorService } from "./pnl-monitor.js";

// ── Helpers ─────────────────────────────────────────────────────────────────

function makeAccount(overrides?: Partial<BrokerAccount>): BrokerAccount {
  return {
    accountId: "test",
    brokerType: "paper",
    cash: 1_000_000,
    frozenCash: 0,
    totalAssets: 1_000_000,
    marketValue: 0,
    profit: 0,
    profitPct: 0,
    todayProfit: 0,
    lastSyncAt: new Date().toISOString(),
    ...overrides,
  };
}

function makePosition(symbol: string, overrides?: Partial<LivePosition>): LivePosition {
  return {
    symbol,
    market: "cn_stock",
    quantity: 1000,
    availableQty: 1000,
    frozenQty: 0,
    todayBuyQty: 0,
    avgCost: 100,
    marketValue: 100_000,
    source: "local",
    lastSyncAt: new Date().toISOString(),
    ...overrides,
  };
}

type MockPluginApi = OpenClawPluginApi & { notify: ReturnType<typeof vi.fn> };

function makeMockApi(): MockPluginApi {
  return {
    logger: {
      info: vi.fn(),
      warn: vi.fn(),
      error: vi.fn(),
      debug: vi.fn(),
    },
    notify: vi.fn(),
  } as unknown as MockPluginApi;
}

function makeMockOrderManager(
  account: BrokerAccount,
  positions: LivePosition[] = [],
): OrderManager {
  return {
    broker: {
      queryAccount: vi.fn().mockResolvedValue(account),
      queryPositions: vi.fn().mockResolvedValue(positions),
    },
  } as unknown as OrderManager;
}

// ── Tests ───────────────────────────────────────────────────────────────────

describe("PnlMonitor", () => {
  const dummyCtx = {} as OpenClawPluginServiceContext;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.mocked(isMarketOpen).mockReturnValue(true);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("does not start when disabled", async () => {
    const api = makeMockApi();
    const config = { pnlMonitor: { enabled: false } } as QuantTradingConfig;
    const svc = createPnlMonitorService(api, config, () => null);
    await svc.start(dummyCtx);
    expect(api.logger.info).toHaveBeenCalledWith(expect.stringContaining("disabled"));
  });

  it("emits warning alert when daily PnL exceeds warning threshold", async () => {
    const api = makeMockApi();
    const config = {
      pnlMonitor: {
        pollIntervalSecs: 1,
        warningPct: -0.03,
        circuitBreakerPct: -0.05,
        enabled: true,
      },
    } as QuantTradingConfig;

    // Account has -4% daily loss (warning but not circuit breaker)
    const account = makeAccount({
      totalAssets: 1_000_000,
      todayProfit: -40_000, // -4%
    });
    const mgr = makeMockOrderManager(account);
    const svc = createPnlMonitorService(api, config, () => mgr);
    await svc.start(dummyCtx);

    // Let the first tick complete
    await vi.advanceTimersByTimeAsync(100);

    expect(api.notify).toHaveBeenCalledTimes(1);
    const notifyCall = api.notify.mock.calls[0]![0] as string;
    expect(notifyCall).toContain("⚠️");
    expect(notifyCall).toContain("预警");

    await svc.stop?.(dummyCtx);
  });

  it("emits critical alert and pauses engine at circuit-breaker threshold", async () => {
    const api = makeMockApi();
    const pauseEngine = vi.fn().mockResolvedValue(undefined);
    const config = {
      pnlMonitor: {
        pollIntervalSecs: 1,
        warningPct: -0.03,
        circuitBreakerPct: -0.05,
        enabled: true,
      },
    } as QuantTradingConfig;

    // Account has -6% daily loss (circuit breaker)
    const account = makeAccount({
      totalAssets: 1_000_000,
      todayProfit: -60_000, // -6%
    });
    const mgr = makeMockOrderManager(account);
    const svc = createPnlMonitorService(api, config, () => mgr, pauseEngine);
    await svc.start(dummyCtx);
    await vi.advanceTimersByTimeAsync(100);

    expect(api.notify).toHaveBeenCalledTimes(1);
    const notifyCall = api.notify.mock.calls[0]![0] as string;
    expect(notifyCall).toContain("🚨");
    expect(notifyCall).toContain("熔断");
    expect(pauseEngine).toHaveBeenCalledTimes(1);

    await svc.stop?.(dummyCtx);
  });

  it("emits position loss warning for individual holdings", async () => {
    const api = makeMockApi();
    const config = {
      pnlMonitor: {
        pollIntervalSecs: 1,
        positionWarningPct: -0.08,
        warningPct: -0.1,
        circuitBreakerPct: -0.2,
        enabled: true,
      },
    } as QuantTradingConfig;

    // Position lost 10% (avgCost=100, currentPrice=90)
    const account = makeAccount({ totalAssets: 1_000_000, todayProfit: 0 });
    const positions = [makePosition("600519", { avgCost: 100, currentPrice: 90, quantity: 1000 })];
    const mgr = makeMockOrderManager(account, positions);
    const svc = createPnlMonitorService(api, config, () => mgr);
    await svc.start(dummyCtx);
    await vi.advanceTimersByTimeAsync(100);

    expect(api.notify).toHaveBeenCalledTimes(1);
    const notifyCall = api.notify.mock.calls[0]![0] as string;
    expect(notifyCall).toContain("⚠️");
    expect(notifyCall).toContain("600519");
    expect(notifyCall).toContain("浮亏");

    await svc.stop?.(dummyCtx);
  });

  it("does not emit alerts when market is closed", async () => {
    vi.mocked(isMarketOpen).mockReturnValue(false);
    const api = makeMockApi();
    const config = {
      pnlMonitor: { pollIntervalSecs: 1, enabled: true },
    } as QuantTradingConfig;

    const account = makeAccount({ totalAssets: 1_000_000, todayProfit: -100_000 });
    const mgr = makeMockOrderManager(account);
    const svc = createPnlMonitorService(api, config, () => mgr);
    await svc.start(dummyCtx);
    await vi.advanceTimersByTimeAsync(2000);

    // No alerts because market is closed
    expect(api.notify).not.toHaveBeenCalled();
    await svc.stop?.(dummyCtx);
  });

  it("de-duplicates alerts within the same trading day", async () => {
    const api = makeMockApi();
    const config = {
      pnlMonitor: {
        pollIntervalSecs: 1,
        warningPct: -0.03,
        circuitBreakerPct: -0.2,
        enabled: true,
      },
    } as QuantTradingConfig;

    const account = makeAccount({ totalAssets: 1_000_000, todayProfit: -40_000 });
    const mgr = makeMockOrderManager(account);
    const svc = createPnlMonitorService(api, config, () => mgr);
    await svc.start(dummyCtx);
    await vi.advanceTimersByTimeAsync(100);

    // First tick fires warning
    expect(api.notify).toHaveBeenCalledTimes(1);

    // Second tick should NOT fire again (de-dup)
    await vi.advanceTimersByTimeAsync(1100);
    expect(api.notify).toHaveBeenCalledTimes(1);

    await svc.stop?.(dummyCtx);
  });

  it("emits warning alert when cumulative max drawdown exceeds warning threshold", async () => {
    const api = makeMockApi();
    const config = {
      pnlMonitor: {
        pollIntervalSecs: 1,
        warningPct: -0.2,
        circuitBreakerPct: -0.3,
        drawdownWarningPct: 0.10,
        drawdownCriticalPct: 0.15,
        enabled: true,
      },
    } as QuantTradingConfig;

    const account = makeAccount({ totalAssets: 900_000, todayProfit: 0 });
    const mgr = makeMockOrderManager(account);

    // Build equity history with a peak of 1M and current equity at 900k (10% drawdown)
    const eh = new EquityHistory({ dataDir: "/tmp/test-eh-dd" });
    eh.addSnapshot({
      date: "2026-04-01",
      equity: 1_000_000,
      cash: 500_000,
      marketValue: 500_000,
      dailyPnl: 0,
      positionCount: 2,
    });

    const svc = createPnlMonitorService(
      api, config, () => mgr, undefined, undefined, () => eh,
    );
    await svc.start(dummyCtx);
    await vi.advanceTimersByTimeAsync(100);

    expect(api.notify).toHaveBeenCalledTimes(1);
    const notifyCall = api.notify.mock.calls[0]![0] as string;
    expect(notifyCall).toContain("⚠️");
    expect(notifyCall).toContain("最大回撤");
    expect(notifyCall).toContain("预警线");

    await svc.stop?.(dummyCtx);
  });

  it("emits critical alert and pauses engine when drawdown exceeds stop-loss line", async () => {
    const api = makeMockApi();
    const pauseEngine = vi.fn().mockResolvedValue(undefined);
    const config = {
      pnlMonitor: {
        pollIntervalSecs: 1,
        warningPct: -0.2,
        circuitBreakerPct: -0.3,
        drawdownWarningPct: 0.10,
        drawdownCriticalPct: 0.15,
        enabled: true,
      },
    } as QuantTradingConfig;

    // 16% drawdown: peak 1M, current 840k
    const account = makeAccount({ totalAssets: 840_000, todayProfit: 0 });
    const mgr = makeMockOrderManager(account);

    const eh = new EquityHistory({ dataDir: "/tmp/test-eh-dd2" });
    eh.addSnapshot({
      date: "2026-04-01",
      equity: 1_000_000,
      cash: 500_000,
      marketValue: 500_000,
      dailyPnl: 0,
      positionCount: 2,
    });

    const svc = createPnlMonitorService(
      api, config, () => mgr, pauseEngine, undefined, () => eh,
    );
    await svc.start(dummyCtx);
    await vi.advanceTimersByTimeAsync(100);

    expect(api.notify).toHaveBeenCalled();
    const calls = api.notify.mock.calls.map((c: unknown[]) => c[0] as string);
    const criticalCall = calls.find((c: string) => c.includes("🚨"));
    expect(criticalCall).toBeDefined();
    expect(criticalCall).toContain("止损线");
    expect(pauseEngine).toHaveBeenCalledTimes(1);

    await svc.stop?.(dummyCtx);
  });

  it("emits warning alert when position concentration exceeds threshold", async () => {
    const api = makeMockApi();
    const config = {
      pnlMonitor: {
        pollIntervalSecs: 1,
        warningPct: -0.2,
        circuitBreakerPct: -0.3,
        concentrationWarningPct: 0.25,
        enabled: true,
      },
    } as QuantTradingConfig;

    // Position: 300k marketValue out of 1M total = 30% > 25% threshold
    const account = makeAccount({ totalAssets: 1_000_000, todayProfit: 0 });
    const positions = [
      makePosition("600519", {
        avgCost: 100,
        currentPrice: 100,
        quantity: 3000,
        marketValue: 300_000,
      }),
    ];
    const mgr = makeMockOrderManager(account, positions);

    const svc = createPnlMonitorService(api, config, () => mgr);
    await svc.start(dummyCtx);
    await vi.advanceTimersByTimeAsync(100);

    expect(api.notify).toHaveBeenCalledTimes(1);
    const notifyCall = api.notify.mock.calls[0]![0] as string;
    expect(notifyCall).toContain("⚠️");
    expect(notifyCall).toContain("600519");
    expect(notifyCall).toContain("集中度");

    await svc.stop?.(dummyCtx);
  });

  it("does not emit concentration alert when position is under threshold", async () => {
    const api = makeMockApi();
    const config = {
      pnlMonitor: {
        pollIntervalSecs: 1,
        warningPct: -0.2,
        circuitBreakerPct: -0.3,
        concentrationWarningPct: 0.25,
        enabled: true,
      },
    } as QuantTradingConfig;

    // Position: 200k marketValue out of 1M total = 20% < 25% threshold
    const account = makeAccount({ totalAssets: 1_000_000, todayProfit: 0 });
    const positions = [
      makePosition("600519", {
        avgCost: 100,
        currentPrice: 100,
        quantity: 2000,
        marketValue: 200_000,
      }),
    ];
    const mgr = makeMockOrderManager(account, positions);

    const svc = createPnlMonitorService(api, config, () => mgr);
    await svc.start(dummyCtx);
    await vi.advanceTimersByTimeAsync(100);

    expect(api.notify).not.toHaveBeenCalled();

    await svc.stop?.(dummyCtx);
  });
});
