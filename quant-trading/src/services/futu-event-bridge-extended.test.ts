/**
 * Extended unit tests for FutuEventBridge — covers edge cases:
 * - position change with null position (closed)
 * - position change with full position data
 * - multiple start/stop cycles
 * - stop is idempotent
 * - missing optional broker methods (defensive checks)
 * - multiple events in sequence
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

/** Create a broker mock WITHOUT optional methods (onPositionChange, subscribeOrderBook, subscribeTicker) */
function createMinimalBroker() {
  const fillCbs: Array<(fill: unknown) => void> = [];
  const statusCbs: Array<(id: string, status: string, order: unknown) => void> = [];

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
    _emitFill: (fill: unknown) => fillCbs.forEach((cb) => cb(fill)),
    _emitStatus: (id: string, status: string, order: unknown) => statusCbs.forEach((cb) => cb(id, status, order)),
  };
}

describe("FutuEventBridge — Extended Tests", () => {
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

  // ─────────────────────────────────────────────────────────────
  // Position change — null position (closed)
  // ─────────────────────────────────────────────────────────────
  describe("position change — null (closed)", () => {
    it("emits position event with null position when position is closed", () => {
      bridge.start();
      const events: unknown[] = [];
      eventBus.on("position", (e) => events.push(e));

      broker._emitPosChange("00700", null);

      expect(events).toHaveLength(1);
      expect(events[0]).toMatchObject({
        data: expect.objectContaining({
          type: "position_change",
          symbol: "00700",
          position: null,
          brokerType: "futu",
        }),
      });
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Position change — full position data
  // ─────────────────────────────────────────────────────────────
  describe("position change — full data", () => {
    it("emits position event with full position details", () => {
      bridge.start();
      const events: unknown[] = [];
      eventBus.on("position", (e) => events.push(e));

      broker._emitPosChange("600519", {
        quantity: 200,
        avgCost: 1800,
        currentPrice: 1850,
        marketValue: 370000,
        unrealizedPnl: 10000,
        unrealizedPnlPct: 0.0278,
      });

      expect(events).toHaveLength(1);
      const data = (events[0] as { data: Record<string, unknown> }).data;
      expect(data.position).toMatchObject({
        quantity: 200,
        avgCost: 1800,
        currentPrice: 1850,
        marketValue: 370000,
        unrealizedPnl: 10000,
        unrealizedPnlPct: 0.0278,
      });
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Fill event — complete field mapping
  // ─────────────────────────────────────────────────────────────
  describe("fill event — field mapping", () => {
    it("passes all fill fields to EventBus", () => {
      bridge.start();
      const events: unknown[] = [];
      eventBus.on("order", (e) => events.push(e));

      broker._emitFill({
        fillId: "f-1",
        orderId: "ord-100",
        symbol: "TSLA",
        side: "sell",
        price: 250.5,
        quantity: 20,
        commission: 1.25,
        timestamp: "2026-04-07 14:30:00",
      });

      const data = (events[0] as { data: Record<string, unknown> }).data;
      expect(data).toMatchObject({
        type: "fill",
        orderId: "ord-100",
        symbol: "TSLA",
        side: "sell",
        price: 250.5,
        quantity: 20,
        commission: 1.25,
        timestamp: "2026-04-07 14:30:00",
      });
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Order status — field mapping
  // ─────────────────────────────────────────────────────────────
  describe("order status — field mapping", () => {
    it("passes all order status fields to EventBus", () => {
      bridge.start();
      const events: unknown[] = [];
      eventBus.on("order", (e) => events.push(e));

      broker._emitStatus("ord-200", "partial", {
        symbol: "00700",
        side: "buy",
        status: "partial",
        price: 350,
        quantity: 200,
        filledQty: 100,
        avgFillPrice: 349.5,
      });

      const data = (events[0] as { data: Record<string, unknown> }).data;
      expect(data).toMatchObject({
        type: "order_status",
        orderId: "ord-200",
        symbol: "00700",
        side: "buy",
        status: "partial",
        price: 350,
        quantity: 200,
        filledQty: 100,
        avgFillPrice: 349.5,
        brokerType: "futu",
      });
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Order book — field mapping
  // ─────────────────────────────────────────────────────────────
  describe("order book — field mapping", () => {
    it("passes bids, asks, timestamp and brokerType", () => {
      bridge.start();
      const events: unknown[] = [];
      eventBus.on("tick", (e) => events.push(e));

      const bids = [
        { price: 350, volume: 1000, order_count: 10 },
        { price: 349.8, volume: 500, order_count: 5 },
      ];
      const asks = [
        { price: 350.2, volume: 800, order_count: 8 },
      ];
      broker._emitOrderBook("00700", bids, asks, 1700000001000);

      const data = (events[0] as { data: Record<string, unknown> }).data;
      expect(data).toMatchObject({
        type: "order_book",
        symbol: "00700",
        bids,
        asks,
        timestamp: 1700000001000,
        brokerType: "futu",
      });
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Ticker — field mapping
  // ─────────────────────────────────────────────────────────────
  describe("ticker — field mapping", () => {
    it("passes all ticker fields to EventBus", () => {
      bridge.start();
      const events: unknown[] = [];
      eventBus.on("tick", (e) => events.push(e));

      broker._emitTicker({
        code: "AAPL",
        price: 180.5,
        volume: 100,
        turnover: 18050,
        direction: "SELL",
        sequence: 9999,
        time: "2026-04-07 15:30:00",
      });

      const data = (events[0] as { data: Record<string, unknown> }).data;
      expect(data).toMatchObject({
        type: "ticker",
        symbol: "AAPL",
        price: 180.5,
        volume: 100,
        turnover: 18050,
        direction: "SELL",
        sequence: 9999,
        time: "2026-04-07 15:30:00",
        brokerType: "futu",
      });
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Multiple start/stop cycles
  // ─────────────────────────────────────────────────────────────
  describe("start/stop lifecycle", () => {
    it("can start, stop, and restart without leaking subscriptions", () => {
      bridge.start();
      bridge.stop();

      // After stop, events should not be delivered
      const events: unknown[] = [];
      eventBus.on("order", (e) => events.push(e));
      broker._emitFill({
        orderId: "leak-test",
        symbol: "600519",
        side: "buy",
        price: 1800,
        quantity: 100,
        commission: 5.4,
        timestamp: "2026-04-07 10:30:00",
      });
      expect(events).toHaveLength(0);

      // Restart
      bridge.start();
      broker._emitFill({
        orderId: "after-restart",
        symbol: "600519",
        side: "buy",
        price: 1800,
        quantity: 100,
        commission: 5.4,
        timestamp: "2026-04-07 10:31:00",
      });
      expect(events).toHaveLength(1);
      expect((events[0] as { data: { orderId: string } }).data.orderId).toBe("after-restart");
    });

    it("stop is idempotent — calling twice does not crash", () => {
      bridge.start();
      bridge.stop();
      bridge.stop(); // should be harmless
    });

    it("stop before start does not crash", () => {
      bridge.stop(); // no-op
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Multiple events in sequence
  // ─────────────────────────────────────────────────────────────
  describe("multiple events in sequence", () => {
    it("handles rapid succession of different event types", () => {
      bridge.start();
      const orderEvents: unknown[] = [];
      const posEvents: unknown[] = [];
      const tickEvents: unknown[] = [];

      eventBus.on("order", (e) => orderEvents.push(e));
      eventBus.on("position", (e) => posEvents.push(e));
      eventBus.on("tick", (e) => tickEvents.push(e));

      // Rapid fire
      broker._emitFill({ orderId: "rapid-1", symbol: "A", side: "buy", price: 100, quantity: 10, commission: 0.5, timestamp: "t1" });
      broker._emitStatus("rapid-2", "filled", { symbol: "B", side: "sell", status: "filled", price: 200, quantity: 20, filledQty: 20, avgFillPrice: 199 });
      broker._emitPosChange("C", { quantity: 50, avgCost: 150, currentPrice: 155 });
      broker._emitOrderBook("D", [{ price: 10, volume: 100, order_count: 1 }], [], 1000);
      broker._emitTicker({ code: "E", price: 50, volume: 200, turnover: 10000, direction: "BUY", sequence: 1, time: "t2" });

      expect(orderEvents).toHaveLength(2); // fill + order_status
      expect(posEvents).toHaveLength(1);
      expect(tickEvents).toHaveLength(2); // order_book + ticker
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Broker without optional methods (defensive typeof checks)
  // ─────────────────────────────────────────────────────────────
  describe("broker without optional methods", () => {
    it("works when onPositionChange/subscribeOrderBook/subscribeTicker are missing", () => {
      const minBroker = createMinimalBroker();
      const bus = new EventBus();
      const minBridge = new FutuEventBridge(minBroker as never, bus);

      // Should not throw
      minBridge.start();

      const events: unknown[] = [];
      bus.on("order", (e) => events.push(e));

      // Fill should still work
      minBroker._emitFill({
        orderId: "min-1",
        symbol: "600519",
        side: "buy",
        price: 1800,
        quantity: 100,
        commission: 5.4,
        timestamp: "2026-04-07",
      });
      expect(events).toHaveLength(1);

      // Only subscribeFills and subscribeOrderStatus should have been called
      expect(minBroker.subscribeFills).toHaveBeenCalledTimes(1);
      expect(minBroker.subscribeOrderStatus).toHaveBeenCalledTimes(1);

      minBridge.stop();
      bus.close();
    });
  });

  // ─────────────────────────────────────────────────────────────
  // EventBus event IDs are monotonically increasing
  // ─────────────────────────────────────────────────────────────
  describe("event ID ordering", () => {
    it("events have increasing IDs", () => {
      bridge.start();
      const events: Array<{ id: number }> = [];
      eventBus.on("order", (e) => events.push(e as { id: number }));

      broker._emitFill({ orderId: "a", symbol: "X", side: "buy", price: 1, quantity: 1, commission: 0, timestamp: "t" });
      broker._emitFill({ orderId: "b", symbol: "Y", side: "sell", price: 2, quantity: 2, commission: 0, timestamp: "t" });

      expect(events).toHaveLength(2);
      expect(events[1].id).toBeGreaterThan(events[0].id);
    });
  });
});
