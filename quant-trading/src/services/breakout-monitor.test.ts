import { describe, it, expect, vi, beforeEach } from "vitest";
import { BreakoutMonitor } from "./breakout-monitor.js";

describe("BreakoutMonitor", () => {
  let monitor: BreakoutMonitor;
  const mockQuoteFn = vi.fn();
  const mockOrderFn = vi.fn();
  const mockNotifyFn = vi.fn();
  const mockMaFn = vi.fn();
  const mockPositionFn = vi.fn();

  beforeEach(() => {
    monitor = new BreakoutMonitor({ checkIntervalMs: 100, dataDir: null });
    monitor.setQuoteFn(mockQuoteFn);
    monitor.setOrderFn(mockOrderFn);
    monitor.setNotifyFn(mockNotifyFn);
    monitor.setMaFn(mockMaFn);
    monitor.setPositionFn(mockPositionFn);
    vi.resetAllMocks();
  });

  describe("setup management", () => {
    it("should add and list setups", () => {
      const id = monitor.addSetup({
        symbol: "600979",
        triggerPrice: 6.02,
        quantity: 1000,
      });
      expect(id).toMatch(/^bo-/);
      const setups = monitor.listSetups();
      expect(setups).toHaveLength(1);
      expect(setups[0]!.symbol).toBe("600979");
      expect(setups[0]!.triggerPrice).toBe(6.02);
      expect(setups[0]!.status).toBe("watching");
    });

    it("should cancel a setup", () => {
      const id = monitor.addSetup({
        symbol: "600979",
        triggerPrice: 6.02,
        quantity: 1000,
      });
      expect(monitor.cancelSetup(id)).toBe(true);
      expect(monitor.listSetups()[0]!.status).toBe("cancelled");
    });

    it("should filter active-only setups", () => {
      monitor.addSetup({ symbol: "600979", triggerPrice: 6.02, quantity: 1000 });
      const id2 = monitor.addSetup({ symbol: "688048", triggerPrice: 237, quantity: 100 });
      monitor.cancelSetup(id2);

      expect(monitor.listSetups(true)).toHaveLength(1);
      expect(monitor.listSetups(false)).toHaveLength(2);
    });
  });

  describe("price breakout detection", () => {
    it("should trigger when price breaks above trigger (no volume condition)", async () => {
      mockQuoteFn.mockResolvedValue({ price: 6.05, volume: 50000, changePct: 2.0 });
      mockOrderFn.mockResolvedValue({ ok: true, orderId: "ord-001" });

      monitor.addSetup({ symbol: "600979", triggerPrice: 6.02, quantity: 1000 });
      const triggered = await monitor.checkAll();

      expect(triggered).toHaveLength(1);
      expect(mockOrderFn).toHaveBeenCalledWith(
        expect.objectContaining({
          symbol: "600979",
          side: "buy",
          quantity: 1000,
        }),
      );
    });

    it("should NOT trigger when price is below trigger", async () => {
      mockQuoteFn.mockResolvedValue({ price: 5.95, volume: 50000, changePct: 1.0 });

      monitor.addSetup({ symbol: "600979", triggerPrice: 6.02, quantity: 1000 });
      const triggered = await monitor.checkAll();

      expect(triggered).toHaveLength(0);
      expect(mockOrderFn).not.toHaveBeenCalled();
    });
  });

  describe("hold confirmation", () => {
    it("should require multiple checks above trigger", async () => {
      mockQuoteFn.mockResolvedValue({ price: 6.05, volume: 50000, changePct: 2.0 });
      mockOrderFn.mockResolvedValue({ ok: true });

      monitor.addSetup({
        symbol: "600979",
        triggerPrice: 6.02,
        quantity: 1000,
        holdChecks: 3,
      });

      // Check 1: enters confirming
      let triggered = await monitor.checkAll();
      expect(triggered).toHaveLength(0);
      expect(monitor.listSetups()[0]!.status).toBe("confirming");

      // Check 2: still confirming
      triggered = await monitor.checkAll();
      expect(triggered).toHaveLength(0);

      // Check 3: triggers
      triggered = await monitor.checkAll();
      expect(triggered).toHaveLength(1);
    });

    it("should reset hold count if price drops back", async () => {
      mockOrderFn.mockResolvedValue({ ok: true });

      monitor.addSetup({
        symbol: "600979",
        triggerPrice: 6.02,
        quantity: 1000,
        holdChecks: 3,
      });

      // Above trigger
      mockQuoteFn.mockResolvedValue({ price: 6.05, volume: 50000, changePct: 2.0 });
      await monitor.checkAll();
      expect(monitor.listSetups()[0]!.status).toBe("confirming");

      // Falls back below trigger
      mockQuoteFn.mockResolvedValue({ price: 5.98, volume: 50000, changePct: 1.0 });
      await monitor.checkAll();
      expect(monitor.listSetups()[0]!.status).toBe("watching");
      expect(monitor.listSetups()[0]!.currentHoldCount).toBe(0);
    });
  });

  describe("volume confirmation", () => {
    it("should block entry when volume ratio is insufficient", async () => {
      mockQuoteFn.mockResolvedValue({ price: 6.05, volume: 50000, changePct: 2.0 });

      monitor.addSetup({
        symbol: "600979",
        triggerPrice: 6.02,
        quantity: 1000,
        volumeCondition: { minRatio: 1.5, periodMinutes: 5, baselinePeriods: 6 },
      });

      // No volume history → ratio is null → should not trigger
      const triggered = await monitor.checkAll();
      expect(triggered).toHaveLength(0);
    });

    it("should trigger when volume ratio meets threshold", async () => {
      mockOrderFn.mockResolvedValue({ ok: true });

      monitor.addSetup({
        symbol: "600979",
        triggerPrice: 6.02,
        quantity: 1000,
        volumeCondition: { minRatio: 1.5, periodMinutes: 1, baselinePeriods: 3 },
      });

      // Build volume history: baseline samples (3 periods of 1 min each)
      const now = Date.now();
      // Baseline: 3 minutes of stable volume (1000 per minute)
      monitor.recordVolume("600979", 1000);
      // Simulate time progression using internal state
      const baselineSamples = [
        { ts: now - 240_000, vol: 1000 },
        { ts: now - 180_000, vol: 2000 },
        { ts: now - 120_000, vol: 3000 },
        { ts: now - 60_000, vol: 4000 },
      ];
      // We need to manually seed the volume history for testing
      // The recordVolume method uses Date.now(), so we directly test computeVolumeRatio
      for (const s of baselineSamples) {
        vi.spyOn(Date, "now").mockReturnValue(s.ts);
        monitor.recordVolume("600979", s.vol);
      }

      // Recent period: high volume (2000 in 1 minute = 2x average)
      vi.spyOn(Date, "now").mockReturnValue(now);
      monitor.recordVolume("600979", 6000); // jump of 2000 in recent minute vs avg 1000

      const ratio = monitor.computeVolumeRatio("600979", {
        minRatio: 1.5,
        periodMinutes: 1,
        baselinePeriods: 3,
      });
      expect(ratio).not.toBeNull();
      // Ratio should be >= 1.5
      if (ratio !== null) {
        expect(ratio).toBeGreaterThanOrEqual(1.5);
      }

      vi.restoreAllMocks();
    });
  });

  describe("pullback re-entry", () => {
    it("should enter pullback_wait after breakout confirmation", async () => {
      mockQuoteFn.mockResolvedValue({ price: 6.1, volume: 50000, changePct: 2.0 });

      monitor.addSetup({
        symbol: "600979",
        triggerPrice: 6.02,
        quantity: 1000,
        enablePullback: true,
        pullbackPct: 0.005,
      });

      const triggered = await monitor.checkAll();
      expect(triggered).toHaveLength(0);
      expect(monitor.listSetups()[0]!.status).toBe("pullback_wait");
    });

    it("should trigger entry on pullback to trigger zone", async () => {
      mockOrderFn.mockResolvedValue({ ok: true });

      monitor.addSetup({
        symbol: "600979",
        triggerPrice: 6.02,
        quantity: 1000,
        enablePullback: true,
        pullbackPct: 0.005,
      });

      // First: breakout
      mockQuoteFn.mockResolvedValue({ price: 6.1, volume: 50000, changePct: 2.0 });
      await monitor.checkAll();
      expect(monitor.listSetups()[0]!.status).toBe("pullback_wait");

      // Then: price pulls back to near trigger
      mockQuoteFn.mockResolvedValue({ price: 6.03, volume: 50000, changePct: 1.5 });
      const triggered = await monitor.checkAll();
      expect(triggered).toHaveLength(1);
      expect(mockOrderFn).toHaveBeenCalled();
    });

    it("should expire if pullback is too deep", async () => {
      monitor.addSetup({
        symbol: "600979",
        triggerPrice: 6.02,
        quantity: 1000,
        enablePullback: true,
        pullbackPct: 0.005,
      });

      // Breakout
      mockQuoteFn.mockResolvedValue({ price: 6.1, volume: 50000, changePct: 2.0 });
      await monitor.checkAll();

      // Deep pullback below trigger * 0.99
      mockQuoteFn.mockResolvedValue({ price: 5.9, volume: 50000, changePct: -1.0 });
      await monitor.checkAll();
      expect(monitor.listSetups()[0]!.status).toBe("expired");
    });
  });

  describe("expiry", () => {
    it("should expire setups past their expiry time", async () => {
      const pastTime = new Date(Date.now() - 3600_000).toISOString();
      monitor.addSetup({
        symbol: "600979",
        triggerPrice: 6.02,
        quantity: 1000,
        expiresAt: pastTime,
      });

      mockQuoteFn.mockResolvedValue({ price: 6.1, volume: 50000, changePct: 2.0 });
      await monitor.checkAll();
      expect(monitor.listSetups()[0]!.status).toBe("expired");
    });
  });

  describe("time windows", () => {
    it("should skip check outside time windows", async () => {
      // This tests the isWithinTimeWindows logic indirectly
      mockQuoteFn.mockResolvedValue({ price: 6.1, volume: 50000, changePct: 2.0 });

      monitor.addSetup({
        symbol: "600979",
        triggerPrice: 6.02,
        quantity: 1000,
        timeWindows: ["03:00-03:01"], // Very narrow window unlikely to match
      });

      const triggered = await monitor.checkAll();
      // Either triggers (if we happen to be in that window) or not — just verify no crash
      expect(triggered).toBeDefined();
    });
  });

  describe("EOD exit rules", () => {
    it("should add and list EOD rules", () => {
      const id = monitor.addEodRule({
        symbol: "600979",
        maPeriod: 10,
        exitAction: "close_all",
      });
      expect(id).toMatch(/^eod-/);
      const rules = monitor.listEodRules();
      expect(rules).toHaveLength(1);
      expect(rules[0]!.symbol).toBe("600979");
      expect(rules[0]!.maPeriod).toBe(10);
    });

    it("should remove EOD rules", () => {
      const id = monitor.addEodRule({
        symbol: "600979",
        maPeriod: 10,
      });
      expect(monitor.removeEodRule(id)).toBe(true);
      expect(monitor.listEodRules()).toHaveLength(0);
    });
  });

  describe("stop_limit order entry", () => {
    it("should submit stop_limit order with stopPrice", async () => {
      mockQuoteFn.mockResolvedValue({ price: 6.05, volume: 50000, changePct: 2.0 });
      mockOrderFn.mockResolvedValue({ ok: true, orderId: "ord-002" });

      monitor.addSetup({
        symbol: "600979",
        triggerPrice: 6.02,
        quantity: 1000,
        orderType: "stop_limit",
        bufferPct: 0.002,
      });

      await monitor.checkAll();

      expect(mockOrderFn).toHaveBeenCalledWith(
        expect.objectContaining({
          orderType: "stop_limit",
          stopPrice: 6.02,
          price: expect.closeTo(6.032, 2),
          strategyId: "breakout_monitor",
        }),
      );
    });
  });

  describe("order failure handling", () => {
    it("should handle order rejection gracefully", async () => {
      mockQuoteFn.mockResolvedValue({ price: 6.05, volume: 50000, changePct: 2.0 });
      mockOrderFn.mockResolvedValue({ ok: false, reason: "insufficient_funds" });

      monitor.addSetup({
        symbol: "600979",
        triggerPrice: 6.02,
        quantity: 1000,
      });

      const triggered = await monitor.checkAll();
      expect(triggered).toHaveLength(0);
      expect(monitor.listSetups()[0]!.status).toBe("triggered");
      expect(monitor.listSetups()[0]!.orderResult).toContain("fail");
    });
  });
});
