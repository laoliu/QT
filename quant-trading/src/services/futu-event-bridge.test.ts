/**
 * Unit tests for FutuEventBridge — verifies that FutuBroker push events
 * are correctly dispatched to the EventBus.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EventBus } from "./event-bus.js";
import { FutuEventBridge } from "./futu-event-bridge.js";

// ── Mock FutuBroker ───────────────────────────────────────────────
function createMockBroker() {
  const fillCbs: Array<(fill: unknown) => void> = [];
  const statusCbs: Array<(id: string, status: string, order: unknown) => void> = [];
  const posChangeCbs: Array<(code: string, pos: unknown) => void> = [];
  const orderBookCbs: Array<(code: string, bids: unknown[], asks: unknown[], time: number) => void> = [];
  const tickerCbs: Array<(data: unknown) => void> = [];

  return {
    brokerType: "futu" as const,
    connected: true,
    subscribeFills: vi.fn((cb: (fill: unknown) => void) => {
      fillCbs.push(cb);
      return () => { fillCbs.splice(fillCbs.indexOf(cb), 1); };
    }),
    subscribeOrderStatus: vi.fn((cb: (id: string, status: string, order: unknown) => void) => {
      statusCbs.push(cb);
      return () => { statusCbs.splice(statusCbs.indexOf(cb), 1); };
    }),
    onPositionChange: vi.fn((cb: (code: string, pos: unknown) => void) => {
      posChangeCbs.push(cb);
      return () => { posChangeCbs.splice(posChangeCbs.indexOf(cb), 1); };
    }),
    subscribeOrderBook: vi.fn((cb: (code: string, bids: unknown[], asks: unknown[], time: number) => void) => {
      orderBookCbs.push(cb);
      return () => { orderBookCbs.splice(orderBookCbs.indexOf(cb), 1); };
    }),
    subscribeTicker: vi.fn((cb: (data: unknown) => void) => {
      tickerCbs.push(cb);
      return () => { tickerCbs.splice(tickerCbs.indexOf(cb), 1); };
    }),
    // Emit helpers for testing
    _emitFill: (fill: unknown) => fillCbs.forEach((cb) => cb(fill)),
    _emitStatus: (id: string, status: string, order: unknown) => statusCbs.forEach((cb) => cb(id, status, order)),
    _emitPosChange: (code: string, pos: unknown) => posChangeCbs.forEach((cb) => cb(code, pos)),
    _emitOrderBook: (code: string, bids: unknown[], asks: unknown[], time: number) => orderBookCbs.forEach((cb) => cb(code, bids, asks, time)),
    _emitTicker: (data: unknown) => tickerCbs.forEach((cb) => cb(data)),
  };
}

describe("FutuEventBridge", () => {
  let broker: ReturnType<typeof createMockBroker>;
  let eventBus: EventBus;
  let bridge: FutuEventBridge;

  beforeEach(() => {
    broker = createMockBroker();
    eventBus = new EventBus();
    bridge = new FutuEventBridge(broker as never, eventBus);
  });

  afterEach(() => {
    bridge.stop();
    eventBus.close();
  });

  it("bridges fill events to EventBus order category", () => {
    bridge.start();
    const events: unknown[] = [];
    eventBus.on("order", (e) => events.push(e));

    broker._emitFill({
      orderId: "ord-1",
      symbol: "600519",
      side: "buy",
      price: 1800,
      quantity: 100,
      commission: 5.4,
      timestamp: "2026-04-07 10:30:00",
    });

    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      data: expect.objectContaining({
        type: "fill",
        orderId: "ord-1",
        symbol: "600519",
      }),
    });
  });

  it("bridges order status events to EventBus order category", () => {
    bridge.start();
    const events: unknown[] = [];
    eventBus.on("order", (e) => events.push(e));

    broker._emitStatus("ord-2", "filled", {
      symbol: "AAPL",
      side: "buy",
      status: "filled",
      price: 180,
      quantity: 10,
      filledQty: 10,
      avgFillPrice: 179.5,
    });

    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      data: expect.objectContaining({
        type: "order_status",
        orderId: "ord-2",
        status: "filled",
      }),
    });
  });

  it("bridges position change events to EventBus position category", () => {
    bridge.start();
    const events: unknown[] = [];
    eventBus.on("position", (e) => events.push(e));

    broker._emitPosChange("600519", {
      quantity: 100,
      avgCost: 1800,
      currentPrice: 1810,
      marketValue: 181000,
      unrealizedPnl: 1000,
      unrealizedPnlPct: 0.0056,
    });

    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      data: expect.objectContaining({
        type: "position_change",
        symbol: "600519",
        brokerType: "futu",
      }),
    });
  });

  it("bridges order book events to EventBus tick category", () => {
    bridge.start();
    const events: unknown[] = [];
    eventBus.on("tick", (e) => events.push(e));

    broker._emitOrderBook(
      "600519",
      [{ price: 1800, volume: 100, order_count: 5 }],
      [{ price: 1801, volume: 200, order_count: 3 }],
      1700000000000,
    );

    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      data: expect.objectContaining({
        type: "order_book",
        symbol: "600519",
      }),
    });
  });

  it("bridges ticker events to EventBus tick category", () => {
    bridge.start();
    const events: unknown[] = [];
    eventBus.on("tick", (e) => events.push(e));

    broker._emitTicker({
      code: "00700",
      price: 355,
      volume: 500,
      turnover: 177500,
      direction: "BUY",
      sequence: 123,
      time: "2026-04-07 10:30:00",
    });

    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      data: expect.objectContaining({
        type: "ticker",
        symbol: "00700",
      }),
    });
  });

  it("stop() removes all subscriptions", () => {
    bridge.start();
    const events: unknown[] = [];
    eventBus.on("order", (e) => events.push(e));

    bridge.stop();

    broker._emitFill({
      orderId: "ord-3",
      symbol: "600519",
      side: "buy",
      price: 1800,
      quantity: 100,
      commission: 5.4,
      timestamp: "2026-04-07 10:30:00",
    });

    expect(events).toHaveLength(0);
  });
});
