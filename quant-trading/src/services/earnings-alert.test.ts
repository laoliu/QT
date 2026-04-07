/**
 * Unit tests for EarningsAlertService — proactive holdings event monitoring.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import type { Portfolio, QuantTradingConfig } from "../types.js";
import { FavoritesStore } from "./favorites-store.js";

// Mock python-bridge
vi.mock("../bridge/python-bridge.js", () => ({
  runPythonScript: vi.fn(),
}));

import { runPythonScript } from "../bridge/python-bridge.js";
import { createEarningsAlertService } from "./earnings-alert.js";

// ── Helpers ─────────────────────────────────────────────────────────────────

function makeMockApi(): OpenClawPluginApi & { notify: ReturnType<typeof vi.fn> } {
  return {
    logger: {
      info: vi.fn(),
      warn: vi.fn(),
      error: vi.fn(),
      debug: vi.fn(),
    },
    notify: vi.fn(),
  } as unknown as OpenClawPluginApi & { notify: ReturnType<typeof vi.fn> };
}

function makePortfolio(positions: Array<{ symbol: string; name?: string }>): Portfolio {
  return {
    id: "test",
    name: "测试组合",
    cash: 100_000,
    totalValue: 200_000,
    positions: positions.map((p) => ({
      symbol: p.symbol,
      name: p.name,
      market: "cn_stock" as const,
      quantity: 100,
      avgCost: 50,
    })),
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  };
}

function makeConfig(overrides?: Partial<QuantTradingConfig>): QuantTradingConfig {
  return {
    dataDir: "/tmp/test-quant",
    earningsAlert: {
      enabled: true,
      checkIntervalMs: 86_400_000,
      firstReminderDays: 3,
      urgentReminderDays: 1,
      lookaheadDays: 30,
    },
    ...overrides,
  };
}

// ── Tests ────────────────────────────────────────────────────────────────────

describe("EarningsAlertService", () => {
  let api: ReturnType<typeof makeMockApi>;
  let favoritesStore: FavoritesStore;
  const mockRunPythonScript = vi.mocked(runPythonScript);

  beforeEach(() => {
    vi.useFakeTimers();
    api = makeMockApi();
    favoritesStore = new FavoritesStore({ dataDir: "/tmp/test-favorites" });
    mockRunPythonScript.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("should create service with correct id", () => {
    const service = createEarningsAlertService(
      api,
      makeConfig(),
      async () => makePortfolio([]),
      favoritesStore,
    );
    expect(service.id).toBe("quant-earnings-alert");
  });

  it("should skip check when no holdings or watchlist", async () => {
    const service = createEarningsAlertService(
      api,
      makeConfig(),
      async () => makePortfolio([]),
      favoritesStore,
    );

    await service.start();
    // Advance past the initial 10s delay
    await vi.advanceTimersByTimeAsync(11_000);

    expect(api.logger.debug).toHaveBeenCalledWith(
      expect.stringContaining("无持仓或自选股"),
    );
  });

  it("should fetch events for portfolio stocks", async () => {
    mockRunPythonScript.mockResolvedValue({
      ok: true,
      data: {
        events: {
          "600519": [
            {
              event_type: "earnings",
              event_name: "财报披露 (2026-03-31)",
              event_date: "2026-04-10",
              days_until: 2,
              stock_name: "贵州茅台",
              details: { report_period: "2026-03-31" },
            },
          ],
        },
      },
    });

    const service = createEarningsAlertService(
      api,
      makeConfig(),
      async () => makePortfolio([{ symbol: "600519", name: "贵州茅台" }]),
      favoritesStore,
    );

    await service.start();
    await vi.advanceTimersByTimeAsync(11_000);

    expect(mockRunPythonScript).toHaveBeenCalledWith(
      "engines/stock_events.py",
      expect.objectContaining({
        action: "check_events",
        symbols: ["600519"],
      }),
      expect.anything(),
      expect.anything(),
    );
  });

  it("should not start when disabled", async () => {
    const service = createEarningsAlertService(
      api,
      makeConfig({ earningsAlert: { enabled: false } }),
      async () => makePortfolio([]),
      favoritesStore,
    );

    await service.start();
    expect(api.logger.info).toHaveBeenCalledWith(
      expect.stringContaining("disabled"),
    );
  });

  it("should stop cleanly", async () => {
    const service = createEarningsAlertService(
      api,
      makeConfig(),
      async () => makePortfolio([]),
      favoritesStore,
    );

    await service.start();
    await service.stop();

    expect(api.logger.info).toHaveBeenCalledWith(
      expect.stringContaining("stopped"),
    );
  });
});
