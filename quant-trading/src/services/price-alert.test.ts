import { afterEach, describe, expect, it, vi } from "vitest";
import { PriceAlertService } from "./price-alert.js";

describe("PriceAlertService", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("adds and lists alerts", () => {
    const service = new PriceAlertService({ maxAlerts: 10 });
    const id = service.addAlert({
      symbol: "600519",
      type: "price_above",
      threshold: 2000,
    });
    expect(id).toMatch(/^alert-/);
    expect(service.listAlerts()).toHaveLength(1);
    expect(service.activeCount).toBe(1);
  });

  it("removes alerts", () => {
    const service = new PriceAlertService();
    const id = service.addAlert({
      symbol: "600519",
      type: "price_above",
      threshold: 2000,
    });
    expect(service.removeAlert(id)).toBe(true);
    expect(service.listAlerts()).toHaveLength(0);
    expect(service.removeAlert("nonexistent")).toBe(false);
  });

  it("triggers price_above alert", async () => {
    const service = new PriceAlertService();
    const notified: string[] = [];
    service.setNotify((msg) => {
      notified.push(msg);
    });
    service.setQuoteFn(async () => ({ price: 2100, changePct: 1.5, volume: 50000 }));

    service.addAlert({
      symbol: "600519",
      type: "price_above",
      threshold: 2000,
    });

    const triggered = await service.checkAll();
    expect(triggered).toHaveLength(1);
    expect(notified).toHaveLength(1);
    expect(notified[0]).toContain("条件预警触发");
  });

  it("triggers price_below alert", async () => {
    const service = new PriceAlertService();
    service.setQuoteFn(async () => ({ price: 90, changePct: -2.0, volume: 30000 }));

    service.addAlert({
      symbol: "000858",
      type: "price_below",
      threshold: 100,
    });

    const triggered = await service.checkAll();
    expect(triggered).toHaveLength(1);
  });

  it("does NOT trigger when condition not met", async () => {
    const service = new PriceAlertService();
    service.setQuoteFn(async () => ({ price: 1800, changePct: 0.5, volume: 30000 }));

    service.addAlert({
      symbol: "600519",
      type: "price_above",
      threshold: 2000,
    });

    const triggered = await service.checkAll();
    expect(triggered).toHaveLength(0);
  });

  it("one-shot alert auto-deactivates after trigger", async () => {
    const service = new PriceAlertService();
    service.setQuoteFn(async () => ({ price: 2100, changePct: 1.5, volume: 50000 }));

    service.addAlert({
      symbol: "600519",
      type: "price_above",
      threshold: 2000,
      repeating: false,
    });

    await service.checkAll();
    expect(service.activeCount).toBe(0);

    // Second check should not trigger again
    const triggered2 = await service.checkAll();
    expect(triggered2).toHaveLength(0);
  });

  it("repeating alert stays active after trigger", async () => {
    const service = new PriceAlertService({ repeatCooldownMs: 0 });
    service.setQuoteFn(async () => ({ price: 2100, changePct: 1.5, volume: 50000 }));

    service.addAlert({
      symbol: "600519",
      type: "price_above",
      threshold: 2000,
      repeating: true,
    });

    await service.checkAll();
    expect(service.activeCount).toBe(1);

    // Second check triggers again
    const triggered2 = await service.checkAll();
    expect(triggered2).toHaveLength(1);
  });

  it("respects maxAlerts limit with eviction", () => {
    const service = new PriceAlertService({ maxAlerts: 3 });

    // Fill up
    for (let i = 0; i < 3; i++) {
      service.addAlert({ symbol: `00000${i}`, type: "price_above", threshold: 100 });
    }
    expect(service.listAlerts()).toHaveLength(3);

    // Should throw when full and no evictable alerts
    expect(() =>
      service.addAlert({ symbol: "999999", type: "price_above", threshold: 100 }),
    ).toThrow("上限");
  });

  it("triggers change_pct alerts", async () => {
    const service = new PriceAlertService();
    service.setQuoteFn(async () => ({ price: 100, changePct: 8.5, volume: 50000 }));

    service.addAlert({
      symbol: "600519",
      type: "change_pct_above",
      threshold: 5,
    });

    const triggered = await service.checkAll();
    expect(triggered).toHaveLength(1);
  });

  it("triggers volume_spike alert", async () => {
    const service = new PriceAlertService();
    service.setQuoteFn(async () => ({ price: 100, changePct: 1.0, volume: 600000 }));

    service.addAlert({
      symbol: "600519",
      type: "volume_spike",
      threshold: 50, // 50万
    });

    const triggered = await service.checkAll();
    expect(triggered).toHaveLength(1);
  });

  it("start/stop controls the check interval", () => {
    const service = new PriceAlertService({ checkIntervalMs: 100000 });
    service.start();
    service.start(); // idempotent
    service.stop();
    service.destroy();
  });

  describe("addWatchlistAlert", () => {
    it("creates a change_pct alert for a new symbol", () => {
      const service = new PriceAlertService({ maxAlerts: 10 });
      const id = service.addWatchlistAlert("600519");
      expect(id).toMatch(/^alert-/);
      const alerts = service.listAlerts();
      expect(alerts).toHaveLength(1);
      expect(alerts[0].type).toBe("change_pct_above");
      expect(alerts[0].repeating).toBe(true);
      expect(alerts[0].description).toContain("自选");
    });

    it("skips if symbol already has a change_pct alert", () => {
      const service = new PriceAlertService({ maxAlerts: 10 });
      service.addAlert({
        symbol: "600519",
        type: "change_pct_above",
        threshold: 5,
        repeating: true,
      });
      const id = service.addWatchlistAlert("600519");
      expect(id).toBeNull();
      expect(service.listAlerts()).toHaveLength(1); // still just 1
    });

    it("returns null when max alerts reached", () => {
      const service = new PriceAlertService({ maxAlerts: 1 });
      service.addAlert({ symbol: "000001", type: "price_above", threshold: 100 });
      const id = service.addWatchlistAlert("600519");
      expect(id).toBeNull();
    });
  });
});
