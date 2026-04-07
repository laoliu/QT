import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { OpenClawPluginServiceContext } from "openclaw/plugin-sdk/core";
/**
 * Unit tests for PnlMonitor — background PnL monitoring + alert severity.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import type { BrokerAccount, LivePosition, QuantTradingConfig, TradeRecord } from "../types.js";
import { OrderManager } from "./order-manager.js";

// Mock isMarketOpen
vi.mock("../broker/paper-broker.js", () => ({
  isMarketOpen: vi.fn().mockReturnValue(true),
}));

import { isMarketOpen } from "../broker/paper-broker.js";
import { computeDailySettlement, createPnlMonitorService } from "./pnl-monitor.js";

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
});

// ── computeDailySettlement tests ────────────────────────────────────────

describe("computeDailySettlement", () => {
  function makeTradeRecord(overrides: Partial<TradeRecord>): TradeRecord {
    return {
      tradeId: "t-001",
      symbol: "600519",
      market: "cn_stock",
      side: "sell",
      entryTime: "2026-04-07T09:30:00Z",
      entryPrice: 1800,
      exitPrice: 1850,
      quantity: 100,
      grossPnl: 5000,
      commission: 50,
      netPnl: 4950,
      entryOrderId: "o-001",
      exitOrderId: "o-002",
      ...overrides,
    };
  }

  it("computes settlement with winning and losing trades", () => {
    const account = makeAccount({ totalAssets: 500000, todayProfit: 3000 });
    const positions = [
      makePosition("300750", { unrealizedPnl: 2000 }),
    ];
    const trades = [
      makeTradeRecord({ grossPnl: 5000, netPnl: 4950, commission: 50 }),
      makeTradeRecord({
        tradeId: "t-002",
        symbol: "000858",
        grossPnl: -1000,
        netPnl: -1040,
        commission: 40,
      }),
    ];

    const result = computeDailySettlement(account, positions, trades);

    expect(result.realizedPnl).toBe(4000); // 5000 + (-1000)
    expect(result.unrealizedPnlChange).toBe(2000);
    expect(result.wins).toBe(1);
    expect(result.losses).toBe(1);
    expect(result.winRate).toBe(0.5);
    expect(result.profitFactor).toBe(5); // 5000 / 1000
    expect(result.totalFees).toBe(90); // 50 + 40
    expect(result.netPnl).toBe(4000 - 90 + 2000); // 5910
    expect(result.closedTrades).toBe(2);
    expect(result.totalAssets).toBe(500000);
  });

  it("handles no trades gracefully", () => {
    const account = makeAccount({ totalAssets: 100000 });
    const positions = [makePosition("600519", { unrealizedPnl: -500 })];

    const result = computeDailySettlement(account, positions, []);

    expect(result.realizedPnl).toBe(0);
    expect(result.closedTrades).toBe(0);
    expect(result.winRate).toBe(0);
    expect(result.profitFactor).toBe(0);
    expect(result.totalFees).toBe(0);
    expect(result.unrealizedPnlChange).toBe(-500);
    expect(result.netPnl).toBe(-500);
  });

  it("handles all-winning trades (Infinity profit factor)", () => {
    const account = makeAccount({ totalAssets: 200000 });
    const trades = [
      makeTradeRecord({ grossPnl: 3000, netPnl: 2970, commission: 30 }),
    ];

    const result = computeDailySettlement(account, [], trades);

    expect(result.wins).toBe(1);
    expect(result.losses).toBe(0);
    expect(result.profitFactor).toBe(Infinity);
  });
});
