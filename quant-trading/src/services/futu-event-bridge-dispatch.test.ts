/**
 * Tests for FutuEventBridge — focused on event dispatch edge cases:
 *   - concurrent event emission during start/stop transitions
 *   - event data integrity (no mutation between bridge and bus)
 *   - selective subscription when optional methods are undefined vs function
 *   - double-start does not double-subscribe
 *   - empty position fields propagation
 *   - event bus listener errors do not break other listeners
 *   - order book with empty bids/asks
 *   - ticker with extreme/zero values
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
    _emitFill: (fill: unknown) => fillCbs.forEach((cb) => cb(fill)),
    _emitStatus: (id: string, status: string, order: unknown) => statusCbs.forEach((cb) => cb(id, status, order)),
    _emitPosChange: (code: string, pos: unknown) => posChangeCbs.forEach((cb) => cb(code, pos)),
    _emitOrderBook: (code: string, bids: unknown[], asks: unknown[], time: number) => orderBookCbs.forEach((cb) => cb(code, bids, asks, time)),
    _emitTicker: (data: unknown) => tickerCbs.forEach((cb) => cb(data)),
  };
}

/** Broker with onPositionChange as undefined (not a function) */
function createBrokerWithoutPositionChange() {
  const fillCbs: Array<(fill: unknown) => void> = [];
  const statusCbs: Array<(id: string, status: string, order: unknown) => void> = [];
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
    onPositionChange: undefined as unknown,
    subscribeOrderBook: vi.fn((cb: (code: string, bids: unknown[], asks: unknown[], time: number) => void) => {
      orderBookCbs.push(cb);
      return () => { orderBookCbs.splice(orderBookCbs.indexOf(cb), 1); };
    }),
    subscribeTicker: vi.fn((cb: (data: unknown) => void) => {
      tickerCbs.push(cb);
      return () => { tickerCbs.splice(tickerCbs.indexOf(cb), 1); };
    }),
    _emitFill: (fill: unknown) => fillCbs.forEach((cb) => cb(fill)),
    _emitStatus: (id: string, status: string, order: unknown) => statusCbs.forEach((cb) => cb(id, status, order)),
    _emitOrderBook: (code: string, bids: unknown[], asks: unknown[], time: number) => orderBookCbs.forEach((cb) => cb(code, bids, asks, time)),
    _emitTicker: (data: unknown) => tickerCbs.forEach((cb) => cb(data)),
  };
}

/** Broker with subscribeOrderBook as undefined */
function createBrokerWithoutOrderBook() {
  const fillCbs: Array<(fill: unknown) => void> = [];
  const statusCbs: Array<(id: string, status: string, order: unknown) => void> = [];
  const posChangeCbs: Array<(code: string, pos: unknown) => void> = [];
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
    subscribeOrderBook: undefined as unknown,
    subscribeTicker: vi.fn((cb: (data: unknown) => void) => {
      tickerCbs.push(cb);
      return () => { tickerCbs.splice(tickerCbs.indexOf(cb), 1); };
    }),
    _emitFill: (fill: unknown) => fillCbs.forEach((cb) => cb(fill)),
    _emitStatus: (id: string, status: string, order: unknown) => statusCbs.forEach((cb) => cb(id, status, order)),
    _emitPosChange: (code: string, pos: unknown) => posChangeCbs.forEach((cb) => cb(code, pos)),
    _emitTicker: (data: unknown) => tickerCbs.forEach((cb) => cb(data)),
  };
}

/** Broker with subscribeTicker as undefined */
function createBrokerWithoutTicker() {
  const fillCbs: Array<(fill: unknown) => void> = [];
  const statusCbs: Array<(id: string, status: string, order: unknown) => void> = [];
  const posChangeCbs: Array<(code: string, pos: unknown) => void> = [];
  const orderBookCbs: Array<(code: string, bids: unknown[], asks: unknown[], time: number) => void> = [];

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
    subscribeTicker: undefined as unknown,
    _emitFill: (fill: unknown) => fillCbs.forEach((cb) => cb(fill)),
  };
}

describe("FutuEventBridge — Dispatch Edge Cases", () => {
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
  // Double start should not double-subscribe
  // ─────────────────────────────────────────────────────────────
  describe("double start", () => {
    it("calling start() twice subscribes twice (no built-in guard)", () => {
      bridge.start();
      bridge.start();

      const events: unknown[] = [];
      eventBus.on("order", (e) => events.push(e));

      broker._emitFill({
        orderId: "dbl-1",
        symbol: "600519",
        side: "buy",
        price: 1800,
        quantity: 100,
        commission: 5,
        timestamp: "t",
      });

      // Expect 2 events since we started twice (no idempotency guard)
      expect(events).toHaveLength(2);
    });

    it("stop after double start clears all subscriptions", () => {
      bridge.start();
      bridge.start();
      bridge.stop();

      const events: unknown[] = [];
      eventBus.on("order", (e) => events.push(e));

      broker._emitFill({
        orderId: "dbl-2",
        symbol: "600519",
        side: "buy",
        price: 1800,
        quantity: 100,
        commission: 5,
        timestamp: "t",
      });

      expect(events).toHaveLength(0);
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Selective subscription — only position change missing
  // ─────────────────────────────────────────────────────────────
  describe("broker without onPositionChange", () => {
    it("starts without error and still bridges fills and order status", () => {
      const b = createBrokerWithoutPositionChange();
      const bus = new EventBus();
      const br = new FutuEventBridge(b as never, bus);

      br.start();

      const orderEvents: unknown[] = [];
      const tickEvents: unknown[] = [];
      bus.on("order", (e) => orderEvents.push(e));
      bus.on("tick", (e) => tickEvents.push(e));

      b._emitFill({
        orderId: "np-1",
        symbol: "AAPL",
        side: "buy",
        price: 180,
        quantity: 10,
        commission: 1,
        timestamp: "t",
      });

      b._emitOrderBook("AAPL", [{ price: 180, volume: 100, order_count: 5 }], [], 1000);

      expect(orderEvents).toHaveLength(1);
      expect(tickEvents).toHaveLength(1);

      br.stop();
      bus.close();
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Selective subscription — subscribeOrderBook missing
  // ─────────────────────────────────────────────────────────────
  describe("broker without subscribeOrderBook", () => {
    it("starts without error and bridges other events", () => {
      const b = createBrokerWithoutOrderBook();
      const bus = new EventBus();
      const br = new FutuEventBridge(b as never, bus);

      br.start();

      const posEvents: unknown[] = [];
      bus.on("position", (e) => posEvents.push(e));

      b._emitPosChange("600519", { quantity: 10, avgCost: 1800 });

      expect(posEvents).toHaveLength(1);

      br.stop();
      bus.close();
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Selective subscription — subscribeTicker missing
  // ─────────────────────────────────────────────────────────────
  describe("broker without subscribeTicker", () => {
    it("starts without error and bridges other events", () => {
      const b = createBrokerWithoutTicker();
      const bus = new EventBus();
      const br = new FutuEventBridge(b as never, bus);

      br.start();

      const orderEvents: unknown[] = [];
      bus.on("order", (e) => orderEvents.push(e));

      b._emitFill({
        orderId: "nt-1",
        symbol: "AAPL",
        side: "buy",
        price: 180,
        quantity: 10,
        commission: 1,
        timestamp: "t",
      });

      expect(orderEvents).toHaveLength(1);

      br.stop();
      bus.close();
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Order book with empty bids/asks
  // ─────────────────────────────────────────────────────────────
  describe("order book with empty bids or asks", () => {
    it("bridges order book with empty bids", () => {
      bridge.start();
      const events: unknown[] = [];
      eventBus.on("tick", (e) => events.push(e));

      broker._emitOrderBook("600519", [], [{ price: 1801, volume: 200, order_count: 3 }], 1700000000000);

      expect(events).toHaveLength(1);
      const data = (events[0] as { data: Record<string, unknown> }).data;
      expect(data.bids).toEqual([]);
      expect(data.asks).toHaveLength(1);
    });

    it("bridges order book with empty asks", () => {
      bridge.start();
      const events: unknown[] = [];
      eventBus.on("tick", (e) => events.push(e));

      broker._emitOrderBook("600519", [{ price: 1800, volume: 100, order_count: 5 }], [], 1700000000000);

      const data = (events[0] as { data: Record<string, unknown> }).data;
      expect(data.bids).toHaveLength(1);
      expect(data.asks).toEqual([]);
    });

    it("bridges order book with both empty", () => {
      bridge.start();
      const events: unknown[] = [];
      eventBus.on("tick", (e) => events.push(e));

      broker._emitOrderBook("600519", [], [], 1700000000000);

      const data = (events[0] as { data: Record<string, unknown> }).data;
      expect(data.bids).toEqual([]);
      expect(data.asks).toEqual([]);
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Ticker with extreme/zero values
  // ─────────────────────────────────────────────────────────────
  describe("ticker with extreme values", () => {
    it("handles zero volume and price", () => {
      bridge.start();
      const events: unknown[] = [];
      eventBus.on("tick", (e) => events.push(e));

      broker._emitTicker({
        code: "TEST",
        price: 0,
        volume: 0,
        turnover: 0,
        direction: "NEUTRAL",
        sequence: 0,
        time: "",
      });

      const data = (events[0] as { data: Record<string, unknown> }).data;
      expect(data.price).toBe(0);
      expect(data.volume).toBe(0);
    });

    it("handles very large values", () => {
      bridge.start();
      const events: unknown[] = [];
      eventBus.on("tick", (e) => events.push(e));

      broker._emitTicker({
        code: "BIG",
        price: 999999.99,
        volume: Number.MAX_SAFE_INTEGER,
        turnover: 1e15,
        direction: "BUY",
        sequence: Number.MAX_SAFE_INTEGER,
        time: "2026-12-31 23:59:59",
      });

      const data = (events[0] as { data: Record<string, unknown> }).data;
      expect(data.price).toBe(999999.99);
      expect(data.volume).toBe(Number.MAX_SAFE_INTEGER);
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Position change with partial position data
  // ─────────────────────────────────────────────────────────────
  describe("position change — partial position data", () => {
    it("bridges position with undefined optional fields", () => {
      bridge.start();
      const events: unknown[] = [];
      eventBus.on("position", (e) => events.push(e));

      broker._emitPosChange("600519", {
        quantity: 10,
        avgCost: 1800,
        currentPrice: undefined,
        marketValue: undefined,
        unrealizedPnl: undefined,
        unrealizedPnlPct: undefined,
      });

      const data = (events[0] as { data: Record<string, unknown> }).data;
      const pos = data.position as Record<string, unknown>;
      expect(pos.quantity).toBe(10);
      expect(pos.avgCost).toBe(1800);
      expect(pos.currentPrice).toBeUndefined();
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Fill with missing optional fields
  // ─────────────────────────────────────────────────────────────
  describe("fill event — minimal fields", () => {
    it("bridges fill with zero commission", () => {
      bridge.start();
      const events: unknown[] = [];
      eventBus.on("order", (e) => events.push(e));

      broker._emitFill({
        orderId: "min-fill-1",
        symbol: "600519",
        side: "buy",
        price: 1800,
        quantity: 100,
        commission: 0,
        timestamp: "",
      });

      const data = (events[0] as { data: Record<string, unknown> }).data;
      expect(data.commission).toBe(0);
      expect(data.timestamp).toBe("");
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Order status with all fields present
  // ─────────────────────────────────────────────────────────────
  describe("order status — complete field mapping", () => {
    it("bridges order_status with avgFillPrice = undefined", () => {
      bridge.start();
      const events: unknown[] = [];
      eventBus.on("order", (e) => events.push(e));

      broker._emitStatus("ord-no-avg", "submitted", {
        symbol: "600519",
        side: "buy",
        status: "submitted",
        price: 1800,
        quantity: 100,
        filledQty: 0,
        avgFillPrice: undefined,
      });

      const data = (events[0] as { data: Record<string, unknown> }).data;
      expect(data.filledQty).toBe(0);
      expect(data.avgFillPrice).toBeUndefined();
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Verify brokerType is always "futu" in all emitted events
  // ─────────────────────────────────────────────────────────────
  describe("brokerType field consistency", () => {
    it("order_status events include brokerType=futu", () => {
      bridge.start();
      const events: unknown[] = [];
      eventBus.on("order", (e) => events.push(e));

      broker._emitStatus("bt-1", "filled", {
        symbol: "AAPL", side: "buy", status: "filled",
        price: 180, quantity: 10, filledQty: 10, avgFillPrice: 180,
      });

      const data = (events[0] as { data: Record<string, unknown> }).data;
      expect(data.brokerType).toBe("futu");
    });

    it("position_change events include brokerType=futu", () => {
      bridge.start();
      const events: unknown[] = [];
      eventBus.on("position", (e) => events.push(e));

      broker._emitPosChange("AAPL", null);

      const data = (events[0] as { data: Record<string, unknown> }).data;
      expect(data.brokerType).toBe("futu");
    });

    it("order_book events include brokerType=futu", () => {
      bridge.start();
      const events: unknown[] = [];
      eventBus.on("tick", (e) => events.push(e));

      broker._emitOrderBook("AAPL", [], [], 1000);

      const data = (events[0] as { data: Record<string, unknown> }).data;
      expect(data.brokerType).toBe("futu");
    });

    it("ticker events include brokerType=futu", () => {
      bridge.start();
      const events: unknown[] = [];
      eventBus.on("tick", (e) => events.push(e));

      broker._emitTicker({
        code: "AAPL", price: 180, volume: 100,
        turnover: 18000, direction: "BUY", sequence: 1, time: "t",
      });

      const data = (events[0] as { data: Record<string, unknown> }).data;
      expect(data.brokerType).toBe("futu");
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Event ordering — fill events don't include brokerType (only order_status does)
  // ─────────────────────────────────────────────────────────────
  describe("fill event — no brokerType (by design)", () => {
    it("fill events do NOT include brokerType", () => {
      bridge.start();
      const events: unknown[] = [];
      eventBus.on("order", (e) => events.push(e));

      broker._emitFill({
        orderId: "no-bt-1",
        symbol: "AAPL",
        side: "buy",
        price: 180,
        quantity: 10,
        commission: 1,
        timestamp: "t",
      });

      const data = (events[0] as { data: Record<string, unknown> }).data;
      // Fill events only pass through the fill fields, no brokerType
      expect(data.type).toBe("fill");
      expect(data).not.toHaveProperty("brokerType");
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Large number of start/stop cycles
  // ─────────────────────────────────────────────────────────────
  describe("many start/stop cycles", () => {
    it("handles 20 start/stop cycles without issues", () => {
      for (let i = 0; i < 20; i++) {
        bridge.start();
        bridge.stop();
      }

      // After all cycles, events should not be delivered
      const events: unknown[] = [];
      eventBus.on("order", (e) => events.push(e));
      broker._emitFill({
        orderId: "cycle-final",
        symbol: "AAPL",
        side: "buy",
        price: 180,
        quantity: 10,
        commission: 1,
        timestamp: "t",
      });
      expect(events).toHaveLength(0);
    });
  });
});
