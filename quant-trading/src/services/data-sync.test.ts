/**
 * Unit tests for DataSync service.
 *
 * Mocks market hours, portfolio file, and data provider to test sync behavior.
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { OpenClawPluginServiceContext } from "openclaw/plugin-sdk/core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Portfolio, QuantTradingConfig } from "../types.js";
import { createDataSyncService } from "./data-sync.js";
import type { EventBus } from "./event-bus.js";

vi.mock("../broker/paper-broker.js", () => ({
  isCnMarketOpen: vi.fn(() => false),
  isMarketOpen: vi.fn(() => false),
}));

const mockGetQuote = vi.fn().mockResolvedValue({ price: 100 });
vi.mock("../data/market-data-provider.js", () => ({
  createDataProvider: vi.fn(() => ({
    getQuote: mockGetQuote,
  })),
  detectMarketFromSymbol: vi.fn(() => "cn_stock"),
}));

import { isMarketOpen } from "../broker/paper-broker.js";
const mockMarketOpen = vi.mocked(isMarketOpen);

let tmpDir: string;
let config: QuantTradingConfig;

function makeApi() {
  return {
    logger: {
      info: vi.fn(),
      warn: vi.fn(),
      error: vi.fn(),
      debug: vi.fn(),
    },
  } as never;
}

/** Flush pending microtasks so fire-and-forget async ticks settle. */
const flush = () => new Promise((r) => setTimeout(r, 50));

const dummyCtx = {} as OpenClawPluginServiceContext;

beforeEach(async () => {
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "oc-datasync-test-"));
  config = { dataDir: tmpDir, pythonPath: "python3" } as QuantTradingConfig;
  vi.clearAllMocks();
});

afterEach(async () => {
  await fs.rm(tmpDir, { recursive: true, force: true });
});

async function writePortfolio(symbols: string[]): Promise<void> {
  const pf: Portfolio = {
    id: "default",
    name: "test",
    cash: 100000,
    totalValue: 100000,
    positions: symbols.map((s) => ({
      symbol: s,
      market: "cn_stock" as const,
      quantity: 100,
      avgCost: 50,
    })),
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  };
  await fs.writeFile(path.join(tmpDir, "portfolio.json"), JSON.stringify(pf));
}

describe("DataSync service", () => {
  it("has correct service id", () => {
    const api = makeApi();
    const svc = createDataSyncService(api, config);
    expect(svc.id).toBe("quant-data-sync");
  });

  it("starts and stops cleanly", async () => {
    const api = makeApi();
    const svc = createDataSyncService(api, config);
    await svc.start(dummyCtx);
    await svc.stop?.(dummyCtx);
  });

  describe("market closed", () => {
    it("skips sync when market is closed", async () => {
      mockMarketOpen.mockReturnValue(false);
      await writePortfolio(["600519"]);

      const api = makeApi();
      const svc = createDataSyncService(api, config);
      await svc.start(dummyCtx);
      await flush(); // let the immediate tick() settle
      await svc.stop?.(dummyCtx);

      expect(mockGetQuote).not.toHaveBeenCalled();
    });
  });

  describe("market open", () => {
    it("refreshes quotes for portfolio symbols", async () => {
      mockMarketOpen.mockReturnValue(true);
      await writePortfolio(["600519", "000001"]);

      const api = makeApi();
      const svc = createDataSyncService(api, config);
      await svc.start(dummyCtx);
      await flush();

      expect(mockGetQuote).toHaveBeenCalledTimes(2);
      expect(mockGetQuote).toHaveBeenCalledWith("600519");
      expect(mockGetQuote).toHaveBeenCalledWith("000001");

      await svc.stop?.(dummyCtx);
    });

    it("skips when portfolio is empty", async () => {
      mockMarketOpen.mockReturnValue(true);
      // No portfolio file

      const api = makeApi();
      const svc = createDataSyncService(api, config);
      await svc.start(dummyCtx);
      await flush();
      await svc.stop?.(dummyCtx);

      expect(mockGetQuote).not.toHaveBeenCalled();
    });

    it("continues after individual symbol fetch failure", async () => {
      mockMarketOpen.mockReturnValue(true);
      mockGetQuote
        .mockRejectedValueOnce(new Error("network error"))
        .mockResolvedValueOnce({ price: 50 });
      await writePortfolio(["FAIL_SYM", "600519"]);

      const api = makeApi();
      const svc = createDataSyncService(api, config);
      await svc.start(dummyCtx);
      await flush();
      await svc.stop?.(dummyCtx);

      // Both were attempted, second succeeded
      expect(mockGetQuote).toHaveBeenCalledTimes(2);
    });
  });

  describe("immediate tick", () => {
    it("runs an immediate tick on start", async () => {
      mockMarketOpen.mockReturnValue(true);
      await writePortfolio(["600519"]);

      const api = makeApi();
      const svc = createDataSyncService(api, config);
      await svc.start(dummyCtx);
      await flush();

      // Immediate tick should have been called
      expect(mockGetQuote).toHaveBeenCalledTimes(1);
      expect(mockGetQuote).toHaveBeenCalledWith("600519");

      await svc.stop?.(dummyCtx);
    });
  });

  describe("watchlist integration", () => {
    it("also syncs watchlist symbols", async () => {
      mockMarketOpen.mockReturnValue(true);
      // Write a watchlist alongside portfolio
      await writePortfolio(["600519"]);
      await fs.writeFile(path.join(tmpDir, "watchlist.json"), JSON.stringify(["000858", "000001"]));

      const api = makeApi();
      const svc = createDataSyncService(api, config);
      await svc.start(dummyCtx);
      await flush();

      // Portfolio (1) + watchlist (2) = 3 unique symbols
      expect(mockGetQuote).toHaveBeenCalledTimes(3);
      expect(mockGetQuote).toHaveBeenCalledWith("600519");
      expect(mockGetQuote).toHaveBeenCalledWith("000858");
      expect(mockGetQuote).toHaveBeenCalledWith("000001");

      await svc.stop?.(dummyCtx);
    });

    it("deduplicates symbols across portfolio and watchlist", async () => {
      mockMarketOpen.mockReturnValue(true);
      await writePortfolio(["600519"]);
      await fs.writeFile(path.join(tmpDir, "watchlist.json"), JSON.stringify(["600519", "000001"]));

      const api = makeApi();
      const svc = createDataSyncService(api, config);
      await svc.start(dummyCtx);
      await flush();

      // 600519 deduplicated: only 2 calls
      expect(mockGetQuote).toHaveBeenCalledTimes(2);

      await svc.stop?.(dummyCtx);
    });
  });

  describe("EventBus integration", () => {
    function makeMockEventBus(): EventBus {
      return { emit: vi.fn() } as unknown as EventBus;
    }

    it("emits data_sync event after successful sync", async () => {
      mockMarketOpen.mockReturnValue(true);
      await writePortfolio(["600519"]);

      const api = makeApi();
      const bus = makeMockEventBus();
      const svc = createDataSyncService(api, config, bus);
      await svc.start(dummyCtx);
      await flush();
      await svc.stop?.(dummyCtx);

      expect(bus.emit).toHaveBeenCalledWith(
        "data_sync",
        expect.objectContaining({
          status: "completed",
          refreshed: 1,
          failed: 0,
          total: 1,
        }),
      );
    });

    it("emits market_closed event when market is closed", async () => {
      mockMarketOpen.mockReturnValue(false);
      await writePortfolio(["600519"]);

      const api = makeApi();
      const bus = makeMockEventBus();
      const svc = createDataSyncService(api, config, bus);
      await svc.start(dummyCtx);
      await flush();
      await svc.stop?.(dummyCtx);

      expect(bus.emit).toHaveBeenCalledWith("data_sync", { status: "market_closed" });
    });

    it("emits alert on per-symbol failure", async () => {
      mockMarketOpen.mockReturnValue(true);
      mockGetQuote.mockRejectedValueOnce(new Error("timeout"));
      await writePortfolio(["FAIL_SYM"]);

      const api = makeApi();
      const bus = makeMockEventBus();
      const svc = createDataSyncService(api, config, bus);
      await svc.start(dummyCtx);
      await flush();
      await svc.stop?.(dummyCtx);

      expect(bus.emit).toHaveBeenCalledWith(
        "alert",
        expect.objectContaining({
          severity: "warning",
          type: "data_sync_failure",
          symbol: "FAIL_SYM",
        }),
      );
      expect(bus.emit).toHaveBeenCalledWith(
        "data_sync",
        expect.objectContaining({
          status: "completed",
          refreshed: 0,
          failed: 1,
          failures: ["FAIL_SYM"],
        }),
      );
    });
  });
});
