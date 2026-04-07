/**
 * Unit tests for OptionsRiskControlService.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LivePosition, QuantTradingConfig } from "../types.js";

vi.mock("../broker/paper-broker.js", () => ({
  isMarketOpen: vi.fn().mockReturnValue(true),
}));
vi.mock("./notify-helper.js", () => ({
  tryNotify: vi.fn(),
}));
vi.mock("../bridge/python-bridge.js", () => ({
  runPythonScript: vi.fn().mockResolvedValue({
    ok: true,
    data: {
      portfolioGreeks: {
        delta: 45,
        gamma: 8,
        theta: -3.5,
        vega: 15,
        cashDelta: 450000,
        cashGamma: 80000,
        cashTheta: -35000,
        cashVega: 150000,
      },
      positionCount: 3,
    },
  }),
}));

const { createOptionsRiskControlService } = await import("./options-risk-control.js");
const { isMarketOpen } = await import("../broker/paper-broker.js");
const { tryNotify } = await import("./notify-helper.js");

const dummyCtx = {} as any;

function makeApi() {
  return {
    logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
  } as any;
}

function makeConfig(overrides?: Partial<QuantTradingConfig>): QuantTradingConfig {
  return {
    dataDir: "/tmp/oc-quant-test",
    dataProvider: "demo",
    broker: "paper",
    initialCash: 1_000_000,
    pythonPath: "python3",
    backtestEngine: "builtin",
    optionsConfig: {
      riskControl: {
        enabled: true,
        checkIntervalSecs: 120,
        criticalDelta: 100,
        criticalGamma: 20,
        varLimit: 0,
      },
    },
    ...overrides,
  } as QuantTradingConfig;
}

function futureDate(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
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
    expiry: futureDate(30),
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

describe("OptionsRiskControlService", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    (isMarketOpen as any).mockReturnValue(true);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("creates and starts without errors", async () => {
    const api = makeApi();
    const positions = [makeOptionPosition()];
    const service = createOptionsRiskControlService(api, makeConfig(), () => positions);
    await service.start!(dummyCtx);
    expect(api.logger.info).toHaveBeenCalledWith(expect.stringContaining("[options-risk-ctrl] started"));
    await service.stop!(dummyCtx);
  });

  it("does not run checks when market is closed", async () => {
    (isMarketOpen as any).mockReturnValue(false);
    const api = makeApi();
    const positions = [makeOptionPosition()];
    const service = createOptionsRiskControlService(api, makeConfig(), () => positions);
    await service.start!(dummyCtx);
    // Advance past the check interval
    await vi.advanceTimersByTimeAsync(130_000);
    // No warn logs should be emitted (only startup info)
    const warnCalls = (api.logger.warn as any).mock.calls;
    expect(warnCalls.length).toBe(0);
    await service.stop!(dummyCtx);
  });

  it("skips when no option positions exist", async () => {
    const api = makeApi();
    const service = createOptionsRiskControlService(api, makeConfig(), () => []);
    await service.start!(dummyCtx);
    await vi.advanceTimersByTimeAsync(130_000);
    // Should have no risk alerts
    const warnCalls = (api.logger.warn as any).mock.calls;
    expect(warnCalls.length).toBe(0);
    await service.stop!(dummyCtx);
  });

  it("respects enabled=false config", async () => {
    const api = makeApi();
    const config = makeConfig({
      optionsConfig: { riskControl: { enabled: false } },
    });
    const service = createOptionsRiskControlService(api, config, () => []);
    await service.start!(dummyCtx);
    expect(api.logger.info).toHaveBeenCalledWith(expect.stringContaining("disabled"));
    await service.stop!(dummyCtx);
  });

  it("stops cleanly", async () => {
    const api = makeApi();
    const service = createOptionsRiskControlService(api, makeConfig(), () => []);
    await service.start!(dummyCtx);
    await service.stop!(dummyCtx);
    expect(api.logger.info).toHaveBeenCalledWith(expect.stringContaining("stopped"));
  });
});
