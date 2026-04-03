import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConditionalOrderService } from "./conditional-order.js";

describe("ConditionalOrderService", () => {
  let service: ConditionalOrderService;

  beforeEach(() => {
    service = new ConditionalOrderService({ checkIntervalMs: 100, maxOrders: 10 });
  });

  afterEach(() => {
    service.stop();
  });

  // ── addStopLoss ──

  it("adds a stop-loss order", () => {
    const id = service.addStopLoss({
      symbol: "600519",
      entryPrice: 1800,
      stopPrice: 1700,
      quantity: 100,
    });
    expect(id).toMatch(/^co-/);
    const orders = service.listOrders();
    expect(orders).toHaveLength(1);
    expect(orders[0].type).toBe("stop_loss");
    expect(orders[0].triggerValue).toBe(1700);
  });

  // ── addTakeProfit ──

  it("adds a take-profit order", () => {
    const id = service.addTakeProfit({
      symbol: "600519",
      entryPrice: 1800,
      targetPrice: 2000,
      quantity: 100,
    });
    expect(id).toMatch(/^co-/);
    const orders = service.listOrders();
    expect(orders).toHaveLength(1);
    expect(orders[0].type).toBe("take_profit");
    expect(orders[0].triggerValue).toBe(2000);
  });

  // ── addTrailingStop ──

  it("adds a trailing stop order with correct initial trailingStopPrice", () => {
    const id = service.addTrailingStop({
      symbol: "600519",
      entryPrice: 100,
      trailPct: 0.05,
      quantity: 200,
    });
    expect(id).toMatch(/^co-/);
    const order = service.listOrders()[0];
    expect(order.type).toBe("trailing_stop");
    expect(order.triggerValue).toBe(0.05);
    expect(order.peakPrice).toBe(100);
    expect(order.trailingStopPrice).toBe(95); // 100 * (1 - 0.05)
  });

  // ── removeOrder ──

  it("removes an order by ID", () => {
    const id = service.addStopLoss({ symbol: "X", entryPrice: 50, stopPrice: 45, quantity: 10 });
    expect(service.removeOrder(id)).toBe(true);
    expect(service.listOrders()).toHaveLength(0);
  });

  it("returns false when removing non-existent order", () => {
    expect(service.removeOrder("co-9999")).toBe(false);
  });

  // ── listOrders ──

  it("filters active orders only", () => {
    service.addStopLoss({ symbol: "A", entryPrice: 50, stopPrice: 45, quantity: 10 });
    service.addStopLoss({ symbol: "B", entryPrice: 50, stopPrice: 45, quantity: 10 });
    expect(service.listOrders()).toHaveLength(2);

    // Manually trigger one
    const orders = service.listOrders();
    orders[0].triggered = true;
    expect(service.listOrders(true)).toHaveLength(1);
    expect(service.listOrders(true)[0].symbol).toBe("B");
  });

  // ── activeCount ──

  it("returns correct active count", () => {
    service.addStopLoss({ symbol: "A", entryPrice: 50, stopPrice: 45, quantity: 10 });
    service.addTakeProfit({ symbol: "B", entryPrice: 50, targetPrice: 60, quantity: 10 });
    expect(service.activeCount).toBe(2);
  });

  // ── maxOrders limit ──

  it("throws when exceeding maxOrders", () => {
    for (let i = 0; i < 10; i++) {
      service.addStopLoss({ symbol: `S${i}`, entryPrice: 50, stopPrice: 45, quantity: 10 });
    }
    expect(() =>
      service.addStopLoss({ symbol: "EXTRA", entryPrice: 50, stopPrice: 45, quantity: 10 }),
    ).toThrow("条件单数量已达上限");
  });

  // ── checkAll: stop_loss trigger ──

  it("triggers stop-loss when price drops below threshold", async () => {
    const mockQuote = vi.fn().mockResolvedValue({ price: 1650 });
    service.setQuoteFn(mockQuote);

    const exitFn = vi.fn().mockResolvedValue({ ok: true });
    service.setExitFn(exitFn);

    const notifyFn = vi.fn();
    service.setNotify(notifyFn);

    service.addStopLoss({
      symbol: "600519",
      entryPrice: 1800,
      stopPrice: 1700,
      quantity: 100,
    });

    const triggered = await service.checkAll();
    expect(triggered).toHaveLength(1);
    expect(exitFn).toHaveBeenCalledWith(
      "600519",
      100,
      expect.stringContaining("止损触发"),
      undefined,
    );
    expect(notifyFn).toHaveBeenCalled();
    expect(service.activeCount).toBe(0);
  });

  it("does not trigger stop-loss when price is above threshold", async () => {
    service.setQuoteFn(vi.fn().mockResolvedValue({ price: 1750 }));
    service.addStopLoss({
      symbol: "600519",
      entryPrice: 1800,
      stopPrice: 1700,
      quantity: 100,
    });
    const triggered = await service.checkAll();
    expect(triggered).toHaveLength(0);
    expect(service.activeCount).toBe(1);
  });

  // ── checkAll: take_profit trigger ──

  it("triggers take-profit when price rises above threshold", async () => {
    service.setQuoteFn(vi.fn().mockResolvedValue({ price: 2050 }));
    const exitFn = vi.fn().mockResolvedValue({ ok: true });
    service.setExitFn(exitFn);
    service.setNotify(vi.fn());

    service.addTakeProfit({
      symbol: "600519",
      entryPrice: 1800,
      targetPrice: 2000,
      quantity: 100,
    });

    const triggered = await service.checkAll();
    expect(triggered).toHaveLength(1);
    expect(exitFn).toHaveBeenCalledWith(
      "600519",
      100,
      expect.stringContaining("止盈触发"),
      undefined,
    );
  });

  // ── checkAll: trailing_stop trigger ──

  it("updates peak price and triggers trailing stop on drop", async () => {
    // First check: price rose to 110 → peak=110, stop=110*(1-0.1)=99
    service.setQuoteFn(vi.fn().mockResolvedValue({ price: 110 }));
    service.setExitFn(vi.fn().mockResolvedValue({ ok: true }));
    service.setNotify(vi.fn());

    service.addTrailingStop({
      symbol: "000001",
      entryPrice: 100,
      trailPct: 0.1,
      quantity: 50,
    });

    // Price up: should update peak, no trigger
    let triggered = await service.checkAll();
    expect(triggered).toHaveLength(0);
    const order = service.listOrders()[0];
    expect(order.peakPrice).toBe(110);
    expect(order.trailingStopPrice).toBe(99); // 110 * 0.9

    // Price drop to 98 → ≤ 99, should trigger
    service.setQuoteFn(vi.fn().mockResolvedValue({ price: 98 }));
    triggered = await service.checkAll();
    expect(triggered).toHaveLength(1);
    expect(service.activeCount).toBe(0);
  });

  // ── checkAll: no quote function ──

  it("returns empty when no quoteFn set", async () => {
    service.addStopLoss({ symbol: "A", entryPrice: 50, stopPrice: 45, quantity: 10 });
    const triggered = await service.checkAll();
    expect(triggered).toHaveLength(0);
  });

  // ── checkAll: exit function failure ──

  it("records triggerResult on exit failure", async () => {
    service.setQuoteFn(vi.fn().mockResolvedValue({ price: 40 }));
    service.setExitFn(vi.fn().mockResolvedValue({ ok: false, reason: "风控拒绝" }));
    service.setNotify(vi.fn());

    service.addStopLoss({ symbol: "A", entryPrice: 50, stopPrice: 45, quantity: 10 });
    await service.checkAll();

    const orders = service.listOrders();
    expect(orders[0].triggered).toBe(true);
    expect(orders[0].triggerResult).toContain("exit_failed");
  });

  // ── checkAll: no exit function ──

  it("records no_exit_fn when exitFn not set", async () => {
    service.setQuoteFn(vi.fn().mockResolvedValue({ price: 40 }));
    service.setNotify(vi.fn());

    service.addStopLoss({ symbol: "A", entryPrice: 50, stopPrice: 45, quantity: 10 });
    await service.checkAll();

    const orders = service.listOrders();
    expect(orders[0].triggerResult).toBe("no_exit_fn_configured");
  });

  // ── Multiple orders, different symbols ──

  it("checks multiple orders with deduplicated quotes", async () => {
    const quoteFn = vi.fn().mockImplementation(async (symbol: string) => {
      if (symbol === "A") return { price: 40 }; // below stop
      if (symbol === "B") return { price: 70 }; // above take-profit
      return null;
    });
    service.setQuoteFn(quoteFn);
    service.setExitFn(vi.fn().mockResolvedValue({ ok: true }));
    service.setNotify(vi.fn());

    service.addStopLoss({ symbol: "A", entryPrice: 50, stopPrice: 45, quantity: 10 });
    service.addTakeProfit({ symbol: "B", entryPrice: 50, targetPrice: 65, quantity: 20 });
    service.addStopLoss({ symbol: "A", entryPrice: 60, stopPrice: 55, quantity: 15 }); // also triggers

    const triggered = await service.checkAll();
    expect(triggered).toHaveLength(3);
    // Quote for A should be fetched only once
    expect(quoteFn).toHaveBeenCalledTimes(2); // A and B
  });

  // ── start / stop ──

  it("starts and stops timer without error", () => {
    service.start();
    service.start(); // idempotent
    service.stop();
    service.stop(); // idempotent
  });

  // ── Persistence (in-memory only, no disk) ──

  it("persists with dataDir and reloads", () => {
    const tmpDir = `/tmp/test-cond-orders-${Date.now()}`;
    const svc1 = new ConditionalOrderService({ dataDir: tmpDir });
    svc1.addStopLoss({ symbol: "X", entryPrice: 100, stopPrice: 90, quantity: 50 });
    svc1.addTakeProfit({ symbol: "Y", entryPrice: 100, targetPrice: 120, quantity: 50 });

    // Create a new instance that loads from same dir
    const svc2 = new ConditionalOrderService({ dataDir: tmpDir });
    expect(svc2.listOrders()).toHaveLength(2);
    expect(svc2.listOrders()[0].symbol).toBe("X");
    expect(svc2.listOrders()[1].symbol).toBe("Y");

    // Cleanup
    svc1.stop();
    svc2.stop();
  });

  // ── addTrailingAtr ──

  it("adds an ATR trailing stop with correct initial values", () => {
    const id = service.addTrailingAtr({
      symbol: "600979",
      entryPrice: 6.02,
      atrValue: 0.4,
      atrMultiplier: 1.0,
      quantity: 1000,
    });
    expect(id).toMatch(/^co-/);
    const order = service.listOrders()[0];
    expect(order.type).toBe("trailing_atr");
    expect(order.atrValue).toBe(0.4);
    expect(order.atrMultiplier).toBe(1.0);
    expect(order.peakPrice).toBe(6.02);
    // Initial stop = 6.02 - 1.0 * 0.40 = 5.62
    expect(order.trailingStopPrice).toBeCloseTo(5.62, 2);
    expect(order.lastRatchetPrice).toBe(6.02);
    expect(order.ratchetUpAtr).toBe(0.8);
    expect(order.ratchetMoveAtr).toBe(0.5);
  });

  it("ratchets ATR trailing stop upward as price rises", async () => {
    service.setQuoteFn(vi.fn().mockResolvedValue({ price: 6.4 })); // +0.38 from entry
    service.setExitFn(vi.fn().mockResolvedValue({ ok: true }));
    service.setNotify(vi.fn());

    service.addTrailingAtr({
      symbol: "600979",
      entryPrice: 6.02,
      atrValue: 0.4,
      atrMultiplier: 1.0,
      quantity: 1000,
    });

    // Price 6.40: rise from lastRatchet(6.02) = 0.38, ratchetUp = 0.8*0.4 = 0.32
    // 0.38 >= 0.32, so 1 step: lastRatchet += 0.32 = 6.34, stop += 0.5*0.4 = 5.82
    const triggered = await service.checkAll();
    expect(triggered).toHaveLength(0);
    const order = service.listOrders()[0];
    expect(order.lastRatchetPrice).toBeCloseTo(6.34, 2);
    expect(order.trailingStopPrice).toBeCloseTo(5.82, 2);
    expect(order.peakPrice).toBe(6.4);
  });

  it("triggers ATR trailing stop when price drops below stop line", async () => {
    service.setQuoteFn(vi.fn().mockResolvedValue({ price: 5.5 }));
    service.setExitFn(vi.fn().mockResolvedValue({ ok: true }));
    service.setNotify(vi.fn());

    service.addTrailingAtr({
      symbol: "600979",
      entryPrice: 6.02,
      atrValue: 0.4,
      atrMultiplier: 1.0,
      quantity: 1000,
    });

    // Initial stop = 5.62, price 5.50 < 5.62 → trigger
    const triggered = await service.checkAll();
    expect(triggered).toHaveLength(1);
    expect(service.activeCount).toBe(0);
  });

  // ── addStagedTakeProfit ──

  it("adds staged take-profit with correct tier quantities", () => {
    const id = service.addStagedTakeProfit({
      symbol: "600979",
      entryPrice: 6.0,
      totalQuantity: 1000,
      tiers: [
        { gainPct: 0.08, sellRatio: 0.5 },
        { gainPct: 0.12, sellRatio: 1.0 },
      ],
    });
    expect(id).toMatch(/^co-/);
    const order = service.listOrders()[0];
    expect(order.type).toBe("staged_tp");
    expect(order.tiers).toHaveLength(2);
    // Tier 1: +8% → 6.48, sell 50% of 1000 = 500
    expect(order.tiers![0].triggerPrice).toBeCloseTo(6.48, 2);
    expect(order.tiers![0].quantity).toBe(500);
    expect(order.tiers![0].triggered).toBe(false);
    // Tier 2: +12% → 6.72, sell 100% of remaining 500 = 500
    expect(order.tiers![1].triggerPrice).toBeCloseTo(6.72, 2);
    expect(order.tiers![1].quantity).toBe(500);
    expect(order.tiers![1].triggered).toBe(false);
  });

  it("triggers staged tiers independently", async () => {
    // Price at +9% → only tier 1 triggers
    service.setQuoteFn(vi.fn().mockResolvedValue({ price: 6.54 })); // +9%
    const exitFn = vi.fn().mockResolvedValue({ ok: true });
    service.setExitFn(exitFn);
    service.setNotify(vi.fn());

    service.addStagedTakeProfit({
      symbol: "600979",
      entryPrice: 6.0,
      totalQuantity: 1000,
      tiers: [
        { gainPct: 0.08, sellRatio: 0.5 },
        { gainPct: 0.12, sellRatio: 1.0 },
      ],
    });

    let triggered = await service.checkAll();
    expect(triggered).toHaveLength(1); // tier 1 triggered
    expect(exitFn).toHaveBeenCalledTimes(1);
    expect(exitFn).toHaveBeenCalledWith("600979", 500, expect.stringContaining("+8%"), undefined);

    const order = service.listOrders()[0];
    expect(order.tiers![0].triggered).toBe(true);
    expect(order.tiers![1].triggered).toBe(false);
    expect(order.triggered).toBe(false); // not fully done yet
    expect(service.activeCount).toBe(1);

    // Price rises to +13% → tier 2 triggers
    service.setQuoteFn(vi.fn().mockResolvedValue({ price: 6.78 }));
    triggered = await service.checkAll();
    expect(triggered).toHaveLength(1);
    expect(exitFn).toHaveBeenCalledTimes(2);

    const final = service.listOrders()[0];
    expect(final.tiers![1].triggered).toBe(true);
    expect(final.triggered).toBe(true); // all tiers done
    expect(service.activeCount).toBe(0);
  });

  it("throws when staged take-profit has no tiers", () => {
    expect(() =>
      service.addStagedTakeProfit({
        symbol: "X",
        entryPrice: 10,
        totalQuantity: 100,
        tiers: [],
      }),
    ).toThrow("至少需要一个止盈档位");
  });

  // ── addTrailingTakeProfit ──

  it("adds a trailing take-profit with correct activation and callback", () => {
    const id = service.addTrailingTakeProfit({
      symbol: "600519",
      entryPrice: 100,
      activationPct: 0.1,
      callbackPct: 0.03,
      quantity: 500,
    });
    expect(id).toMatch(/^co-/);
    const orders = service.listOrders();
    expect(orders).toHaveLength(1);
    const o = orders[0];
    expect(o.type).toBe("trailing_tp");
    expect(o.triggerValue).toBeCloseTo(110); // activationPrice = 100 * 1.1
    expect(o.activationPct).toBe(0.1);
    expect(o.callbackPct).toBe(0.03);
    expect(o.tpActivated).toBe(false);
    expect(o.peakPrice).toBe(100);
  });

  it("trailing_tp activates and triggers on callback from peak", async () => {
    const exitFn = vi.fn().mockResolvedValue({ ok: true });
    service.setExitFn(exitFn);
    service.setQuoteFn(async () => ({ price: 115 })); // above activation price 110

    service.addTrailingTakeProfit({
      symbol: "600519",
      entryPrice: 100,
      activationPct: 0.1,
      callbackPct: 0.03,
      quantity: 500,
    });

    // First check: price=115 → activates (>110), peak=115, tpPrice=115*0.97=111.55
    await service.checkAll();
    const active = service.listOrders(true);
    expect(active[0].tpActivated).toBe(true);
    expect(active[0].peakPrice).toBe(115);
    expect(exitFn).not.toHaveBeenCalled();

    // Now price pulls back to 111 → below tpPrice 111.55 → triggers
    service.setQuoteFn(async () => ({ price: 111 }));
    await service.checkAll();
    expect(exitFn).toHaveBeenCalledWith(
      "600519",
      500,
      expect.stringContaining("移动止盈触发"),
      undefined,
    );
  });

  it("trailing_tp does not trigger before activation", async () => {
    const exitFn = vi.fn().mockResolvedValue({ ok: true });
    service.setExitFn(exitFn);
    service.setQuoteFn(async () => ({ price: 105 })); // below activation 110

    service.addTrailingTakeProfit({
      symbol: "600519",
      entryPrice: 100,
      activationPct: 0.1,
      callbackPct: 0.03,
      quantity: 500,
    });

    await service.checkAll();
    expect(exitFn).not.toHaveBeenCalled();
    const orders = service.listOrders(true);
    expect(orders[0].tpActivated).toBe(false);
  });
});
