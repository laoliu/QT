import { describe, it, expect, vi, beforeEach } from "vitest";
import { AuctionMonitor } from "./auction-monitor.js";
import type { AuctionQuote } from "./auction-monitor.js";

describe("AuctionMonitor", () => {
  let monitor: AuctionMonitor;
  const mockQuoteFn = vi.fn<(symbols: string[]) => Promise<AuctionQuote[]>>();
  const mockWatchlistFn = vi.fn<() => string[]>();
  const mockPositionsFn = vi.fn<() => string[]>();
  const mockNotifyFn = vi.fn<(message: string) => Promise<void>>();
  const mockAvgVolumeFn = vi.fn<(symbol: string) => Promise<number | null>>();
  const mockAlertCallback = vi.fn();

  beforeEach(() => {
    monitor = new AuctionMonitor({
      checkIntervalMs: 100,
      gapThresholdPct: 3,
      volumeSurgeRatio: 0.5,
      dataDir: null,
    });
    monitor.setQuoteFn(mockQuoteFn);
    monitor.setWatchlistFn(mockWatchlistFn);
    monitor.setPositionsFn(mockPositionsFn);
    monitor.setNotifyFn(mockNotifyFn);
    monitor.setAvgVolumeFn(mockAvgVolumeFn);
    monitor.setAlertCallback(mockAlertCallback);
    vi.resetAllMocks();
  });

  describe("high open detection", () => {
    it("should detect high open > 3%", async () => {
      mockWatchlistFn.mockReturnValue(["600519"]);
      mockPositionsFn.mockReturnValue([]);
      mockAvgVolumeFn.mockResolvedValue(100000);
      mockQuoteFn.mockResolvedValue([
        {
          symbol: "600519",
          name: "贵州茅台",
          price: 1560,
          prevClose: 1500,
          volume: 1000,
          changePct: 4.0,
        },
      ]);

      const alerts = await monitor.checkOnce();
      expect(alerts.length).toBeGreaterThanOrEqual(1);
      const highOpen = alerts.find((a) => a.type === "high_open");
      expect(highOpen).toBeDefined();
      expect(highOpen!.symbol).toBe("600519");
      expect(mockNotifyFn).toHaveBeenCalled();
    });

    it("should NOT detect high open when < 3%", async () => {
      mockWatchlistFn.mockReturnValue(["600519"]);
      mockPositionsFn.mockReturnValue([]);
      mockAvgVolumeFn.mockResolvedValue(100000);
      mockQuoteFn.mockResolvedValue([
        {
          symbol: "600519",
          price: 1520,
          prevClose: 1500,
          volume: 1000,
          changePct: 1.33,
        },
      ]);

      const alerts = await monitor.checkOnce();
      const highOpen = alerts.find((a) => a.type === "high_open");
      expect(highOpen).toBeUndefined();
    });
  });

  describe("low open detection", () => {
    it("should detect low open > 3%", async () => {
      mockWatchlistFn.mockReturnValue(["600036"]);
      mockPositionsFn.mockReturnValue([]);
      mockAvgVolumeFn.mockResolvedValue(80000);
      mockQuoteFn.mockResolvedValue([
        {
          symbol: "600036",
          name: "招商银行",
          price: 33.5,
          prevClose: 35.0,
          volume: 500,
          changePct: -4.29,
        },
      ]);

      const alerts = await monitor.checkOnce();
      const lowOpen = alerts.find((a) => a.type === "low_open");
      expect(lowOpen).toBeDefined();
      expect(lowOpen!.symbol).toBe("600036");
    });
  });

  describe("volume surge detection", () => {
    it("should detect volume surge when > 50% of 5-day avg", async () => {
      mockWatchlistFn.mockReturnValue(["000858"]);
      mockPositionsFn.mockReturnValue([]);
      mockAvgVolumeFn.mockResolvedValue(10000);
      mockQuoteFn.mockResolvedValue([
        {
          symbol: "000858",
          name: "五粮液",
          price: 150,
          prevClose: 149,
          volume: 6000, // 60% of avg — above 50% threshold
          changePct: 0.67,
        },
      ]);

      const alerts = await monitor.checkOnce();
      const volumeSurge = alerts.find((a) => a.type === "volume_surge");
      expect(volumeSurge).toBeDefined();
      expect(volumeSurge!.symbol).toBe("000858");
    });

    it("should NOT detect volume surge when < 50% of avg", async () => {
      mockWatchlistFn.mockReturnValue(["000858"]);
      mockPositionsFn.mockReturnValue([]);
      mockAvgVolumeFn.mockResolvedValue(10000);
      mockQuoteFn.mockResolvedValue([
        {
          symbol: "000858",
          price: 150,
          prevClose: 149,
          volume: 3000, // 30% of avg — below threshold
          changePct: 0.67,
        },
      ]);

      const alerts = await monitor.checkOnce();
      const volumeSurge = alerts.find((a) => a.type === "volume_surge");
      expect(volumeSurge).toBeUndefined();
    });
  });

  describe("limit bid detection", () => {
    it("should detect limit up bid", async () => {
      mockWatchlistFn.mockReturnValue(["300001"]);
      mockPositionsFn.mockReturnValue([]);
      mockAvgVolumeFn.mockResolvedValue(50000);
      mockQuoteFn.mockResolvedValue([
        {
          symbol: "300001",
          name: "特锐德",
          price: 22.0,
          prevClose: 20.0,
          volume: 5000,
          changePct: 10.0,
          bidVolume1: 80000,
        },
      ]);

      const alerts = await monitor.checkOnce();
      const limitUp = alerts.find((a) => a.type === "limit_up_bid");
      expect(limitUp).toBeDefined();
    });

    it("should detect limit bid gone on second check", async () => {
      mockWatchlistFn.mockReturnValue(["300001"]);
      mockPositionsFn.mockReturnValue([]);
      mockAvgVolumeFn.mockResolvedValue(50000);

      // First check: limit up
      mockQuoteFn.mockResolvedValue([
        {
          symbol: "300001",
          price: 22.0,
          prevClose: 20.0,
          volume: 5000,
          changePct: 10.0,
          bidVolume1: 80000,
        },
      ]);
      await monitor.checkOnce();

      // Second check: limit bid gone (reset sent alerts for "limit_bid_gone" key)
      mockQuoteFn.mockResolvedValue([
        {
          symbol: "300001",
          price: 21.5,
          prevClose: 20.0,
          volume: 6000,
          changePct: 7.5,
          bidVolume1: 0,
        },
      ]);
      const alerts2 = await monitor.checkOnce();
      const bidGone = alerts2.find((a) => a.type === "limit_bid_gone");
      expect(bidGone).toBeDefined();
    });
  });

  describe("deduplication", () => {
    it("should not send the same alert type for the same symbol twice", async () => {
      mockWatchlistFn.mockReturnValue(["600519"]);
      mockPositionsFn.mockReturnValue([]);
      mockAvgVolumeFn.mockResolvedValue(100000);
      mockQuoteFn.mockResolvedValue([
        {
          symbol: "600519",
          price: 1560,
          prevClose: 1500,
          volume: 1000,
          changePct: 4.0,
        },
      ]);

      const alerts1 = await monitor.checkOnce();
      const alerts2 = await monitor.checkOnce();

      const highOpens1 = alerts1.filter((a) => a.type === "high_open");
      const highOpens2 = alerts2.filter((a) => a.type === "high_open");
      expect(highOpens1).toHaveLength(1);
      expect(highOpens2).toHaveLength(0);
    });
  });

  describe("symbol collection", () => {
    it("should merge watchlist and positions (deduplicated)", async () => {
      mockWatchlistFn.mockReturnValue(["600519", "000858"]);
      mockPositionsFn.mockReturnValue(["000858", "600036"]);
      mockAvgVolumeFn.mockResolvedValue(50000);
      mockQuoteFn.mockResolvedValue([]);

      await monitor.checkOnce();

      // quoteFn should be called with 3 unique symbols
      expect(mockQuoteFn).toHaveBeenCalledWith(["600519", "000858", "600036"]);
    });
  });

  describe("no quoteFn", () => {
    it("should return empty when no quoteFn is set", async () => {
      const emptyMonitor = new AuctionMonitor({ dataDir: null });
      emptyMonitor.setWatchlistFn(() => ["600519"]);
      const alerts = await emptyMonitor.checkOnce();
      expect(alerts).toHaveLength(0);
    });
  });

  describe("getTodayAlerts", () => {
    it("should accumulate alerts across checks", async () => {
      mockWatchlistFn.mockReturnValue(["600519"]);
      mockPositionsFn.mockReturnValue(["600036"]);
      mockAvgVolumeFn.mockResolvedValue(100000);
      mockQuoteFn.mockResolvedValue([
        {
          symbol: "600519",
          price: 1560,
          prevClose: 1500,
          volume: 1000,
          changePct: 4.0,
        },
        {
          symbol: "600036",
          price: 33.5,
          prevClose: 35.0,
          volume: 500,
          changePct: -4.29,
        },
      ]);

      await monitor.checkOnce();
      const todayAlerts = monitor.getTodayAlerts();
      expect(todayAlerts.length).toBeGreaterThanOrEqual(2);
    });
  });

  describe("alertCallback", () => {
    it("should emit alert callback with correct severity", async () => {
      mockWatchlistFn.mockReturnValue(["600036"]);
      mockPositionsFn.mockReturnValue([]);
      mockAvgVolumeFn.mockResolvedValue(100000);
      mockQuoteFn.mockResolvedValue([
        {
          symbol: "600036",
          price: 33.5,
          prevClose: 35.0,
          volume: 500,
          changePct: -4.29,
        },
      ]);

      await monitor.checkOnce();
      expect(mockAlertCallback).toHaveBeenCalledWith(
        "warning",
        expect.stringContaining("低开"),
        expect.objectContaining({
          symbol: "600036",
          alertType: "low_open",
          source: "auction-monitor",
        }),
      );
    });
  });

  describe("auction time detection", () => {
    it("isAuctionTime returns boolean", () => {
      // Just ensure it doesn't throw
      const result = monitor.isAuctionTime();
      expect(typeof result).toBe("boolean");
    });
  });

  describe("destroy", () => {
    it("should clean up all state", () => {
      monitor.destroy();
      expect(monitor.getTodayAlerts()).toHaveLength(0);
    });
  });
});
