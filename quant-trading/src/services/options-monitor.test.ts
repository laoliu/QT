/**
 * Unit tests for OptionsMonitorService.
 *
 * Mocks the timer, market open check, and notification helper to verify
 * alert logic without real timers or market connections.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LivePosition, QuantTradingConfig } from "../types.js";

// Mock dependencies
vi.mock("../broker/paper-broker.js", () => ({
  isMarketOpen: vi.fn().mockReturnValue(true),
}));
vi.mock("./notify-helper.js", () => ({
  tryNotify: vi.fn(),
}));

const { createOptionsMonitorService } = await import("./options-monitor.js");
const { isMarketOpen } = await import("../broker/paper-broker.js");
const { tryNotify } = await import("./notify-helper.js");

function makeApi() {
  return {
    logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
  } as any;
}

const dummyCtx = {} as any;

function makeConfig(overrides?: Partial<QuantTradingConfig>): QuantTradingConfig {
  return {
    dataDir: "/tmp/oc-quant-test",
    dataProvider: "demo",
    broker: "paper",
    initialCash: 1_000_000,
    pythonPath: "python3",
    backtestEngine: "builtin",
    optionsConfig: {
      monitor: {
        pollIntervalSecs: 60,
        expiryWarningDays: 7,
        maxDelta: 50,
        maxGamma: 10,
        maxVega: 20,
        enabled: true,
      },
    },
    ...overrides,
  } as QuantTradingConfig;
}

function makeOptionPosition(overrides?: Partial<LivePosition>): LivePosition {
  return {
    symbol: "10005646",
    market: "etf_option",
    quantity: 10,
    avgCost: 0.22,
    marketValue: 2345,
    currentPrice: 3.1,
    profit: 145,
    profitPct: 0.066,
    optionType: "call",
    strike: 3.0,
    expiry: futureDate(30), // 30 days out
    underlying: "510050",
    greeks: {
      delta: 0.55,
      gamma: 0.012,
      theta: -0.003,
      vega: 0.045,
      rho: 0.001,
      impliedVol: 0.235,
      calculatedAt: Date.now(),
    },
    ...overrides,
  } as LivePosition;
}

/** Create a date string N days from now */
function futureDate(days: number): string {
  return new Date(Date.now() + days * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
}

describe("OptionsMonitorService", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    (isMarketOpen as ReturnType<typeof vi.fn>).mockReturnValue(true);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("starts and stops cleanly", async () => {
    const svc = createOptionsMonitorService(makeApi(), makeConfig(), () => []);
    await svc.start!(dummyCtx);
    await svc.stop!(dummyCtx);
  });

  it("skips when market is closed", async () => {
    (isMarketOpen as ReturnType<typeof vi.fn>).mockReturnValue(false);
    const api = makeApi();
    const positions = [makeOptionPosition({ expiry: futureDate(1) })];
    const svc = createOptionsMonitorService(api, makeConfig(), () => positions);

    await svc.start!(dummyCtx);
    // Advance timer to trigger checkOnce
    await vi.advanceTimersByTimeAsync(61_000);
    await svc.stop!(dummyCtx);

    // No notifications when market closed
    expect(tryNotify).not.toHaveBeenCalled();
  });

  it("alerts on near-expiry position (warning)", async () => {
    const api = makeApi();
    const positions = [makeOptionPosition({ expiry: futureDate(5) })]; // 5 days — within 7-day threshold
    const svc = createOptionsMonitorService(api, makeConfig(), () => positions);

    await svc.start!(dummyCtx);
    await svc.stop!(dummyCtx);

    // Should have logged a warning about near expiry
    expect(api.logger.warn).toHaveBeenCalledWith(expect.stringContaining("将在"));
  });

  it("alerts critical on very near expiry (≤2 days)", async () => {
    const api = makeApi();
    const positions = [makeOptionPosition({ expiry: futureDate(1) })]; // 1 day
    const svc = createOptionsMonitorService(api, makeConfig(), () => positions);

    await svc.start!(dummyCtx);
    await svc.stop!(dummyCtx);

    // Should have a critical-level alert
    expect(api.logger.warn).toHaveBeenCalledWith(expect.stringContaining("🚨"));
  });

  it("alerts on expired position", async () => {
    const api = makeApi();
    // Position expired yesterday
    const positions = [makeOptionPosition({ expiry: futureDate(-1) })];
    const svc = createOptionsMonitorService(api, makeConfig(), () => positions);

    await svc.start!(dummyCtx);
    await svc.stop!(dummyCtx);

    expect(api.logger.warn).toHaveBeenCalledWith(expect.stringContaining("已到期"));
  });

  it("alerts on Delta breach", async () => {
    const api = makeApi();
    // Total delta = 0.9 * 100 = 90 > maxDelta(50)
    const positions = [
      makeOptionPosition({
        quantity: 100,
        greeks: {
          delta: 0.9,
          gamma: 0,
          theta: 0,
          vega: 0,
          rho: 0,
          impliedVol: 0.3,
          calculatedAt: Date.now(),
        },
        expiry: futureDate(30),
      }),
    ];
    const svc = createOptionsMonitorService(api, makeConfig(), () => positions);

    await svc.start!(dummyCtx);
    await svc.stop!(dummyCtx);

    expect(api.logger.warn).toHaveBeenCalledWith(expect.stringContaining("Delta暴露"));
  });

  it("alerts on Gamma breach", async () => {
    const api = makeApi();
    // Total gamma = 0.5 * 100 = 50 > maxGamma(10)
    const positions = [
      makeOptionPosition({
        quantity: 100,
        greeks: {
          delta: 0.1,
          gamma: 0.5,
          theta: 0,
          vega: 0,
          rho: 0,
          impliedVol: 0.3,
          calculatedAt: Date.now(),
        },
        expiry: futureDate(30),
      }),
    ];
    const svc = createOptionsMonitorService(api, makeConfig(), () => positions);

    await svc.start!(dummyCtx);
    await svc.stop!(dummyCtx);

    expect(api.logger.warn).toHaveBeenCalledWith(expect.stringContaining("Gamma暴露"));
  });

  it("alerts on Vega breach", async () => {
    const api = makeApi();
    // Total vega = 0.5 * 100 = 50 > maxVega(20)
    const positions = [
      makeOptionPosition({
        quantity: 100,
        greeks: {
          delta: 0.1,
          gamma: 0,
          theta: 0,
          vega: 0.5,
          rho: 0,
          impliedVol: 0.3,
          calculatedAt: Date.now(),
        },
        expiry: futureDate(30),
      }),
    ];
    const svc = createOptionsMonitorService(api, makeConfig(), () => positions);

    await svc.start!(dummyCtx);
    await svc.stop!(dummyCtx);

    expect(api.logger.warn).toHaveBeenCalledWith(expect.stringContaining("Vega暴露"));
  });

  it("alerts on short ITM assignment risk", async () => {
    const api = makeApi();
    // Short call with strike 3.0 when underlying is at 3.5 — ITM
    const positions = [
      makeOptionPosition({
        quantity: -10, // short
        optionType: "call",
        strike: 3.0,
        currentPrice: 3.5, // underlying proxy > strike
        expiry: futureDate(30),
      }),
    ];
    const svc = createOptionsMonitorService(api, makeConfig(), () => positions);

    await svc.start!(dummyCtx);
    await svc.stop!(dummyCtx);

    expect(api.logger.warn).toHaveBeenCalledWith(expect.stringContaining("被行权"));
  });

  it("does not alert when no option positions", async () => {
    const api = makeApi();
    const svc = createOptionsMonitorService(api, makeConfig(), () => []);

    await svc.start!(dummyCtx);
    await svc.stop!(dummyCtx);

    // Only start/stop log, no warn-level alerts
    expect(tryNotify).not.toHaveBeenCalled();
  });

  it("deduplicates alerts within cooldown window", async () => {
    const api = makeApi();
    const positions = [makeOptionPosition({ expiry: futureDate(1) })];
    const svc = createOptionsMonitorService(api, makeConfig(), () => positions);

    await svc.start!(dummyCtx);
    // First poll fires immediately — should emit alert
    // Advance 2 minutes and trigger again — should NOT re-alert (30-min cooldown)
    await vi.advanceTimersByTimeAsync(120_000);
    await svc.stop!(dummyCtx);

    // The near_expiry alert should have been emitted exactly once
    const nearExpiryCalls = (api.logger.warn as ReturnType<typeof vi.fn>).mock.calls.filter(
      (c: string[]) => c[0].includes("将在") || c[0].includes("已到期"),
    );
    expect(nearExpiryCalls.length).toBe(1);
  });

  it("disabled by config", async () => {
    const api = makeApi();
    const config = makeConfig({
      optionsConfig: { monitor: { enabled: false } },
    } as any);
    const svc = createOptionsMonitorService(api, config, () => []);

    await svc.start!(dummyCtx);
    expect(api.logger.info).toHaveBeenCalledWith(expect.stringContaining("disabled"));
  });
});
