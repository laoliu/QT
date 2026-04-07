/**
 * Unit tests for FutuBroker — the 富途 broker adapter.
 *
 * Mocks PersistentPythonProcess to verify order flow, quote subscriptions,
 * event handling (fill, order_status, quote, order_book, ticker, position_change),
 * snapshot queries, and symbol normalisation without connecting to real FutuOpenD.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { QuantTradingConfig, OrderStatus } from "../types.js";

// ── Mock PersistentPythonProcess ────────────────────────────────
let eventHandler: ((event: string, data: unknown) => void) | null = null;
const mockCall = vi.fn().mockResolvedValue({});
const mockStart = vi.fn().mockResolvedValue(undefined);
const mockStop = vi.fn();

vi.mock("../bridge/persistent-python.js", () => ({
  PersistentPythonProcess: class MockProc {
    start = mockStart;
    stop = mockStop;
    call = mockCall;
    onEvent = vi.fn((handler: (event: string, data: unknown) => void) => {
      eventHandler = handler;
    });
    constructor(_module: string, _cfg: unknown, _extra?: unknown) {}
  },
}));

vi.mock("./paper-broker.js", () => ({
  isCnMarketOpen: vi.fn(() => true),
}));

import { FutuBroker } from "./futu-broker.js";

const config: QuantTradingConfig = {
  dataDir: "/tmp/oc-futu-test",
  pythonPath: "python3",
  tradingAccount: {
    mode: "live",
    broker: "futu",
    futu: {
      openDHost: "127.0.0.1",
      openDPort: 11111,
      tradeEnv: "SIMULATE",
    },
    riskLimits: {},
  },
} as QuantTradingConfig;

beforeEach(() => {
  vi.clearAllMocks();
  mockCall.mockResolvedValue({});
  mockStart.mockResolvedValue(undefined);
  eventHandler = null;
});

describe("FutuBroker", () => {
  describe("connect/disconnect", () => {
    it("connects via python process", async () => {
      const broker = new FutuBroker(config);
      expect(broker.connected).toBe(false);

      await broker.connect();
      expect(broker.connected).toBe(true);
      expect(mockStart).toHaveBeenCalled();
      expect(mockCall).toHaveBeenCalledWith("connect");
    });

    it("is idempotent on double connect", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();
      await broker.connect();
      expect(mockStart).toHaveBeenCalledTimes(1);
    });

    it("disconnects cleanly", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();
      await broker.disconnect();
      expect(broker.connected).toBe(false);
      expect(mockCall).toHaveBeenCalledWith("disconnect");
      expect(mockStop).toHaveBeenCalled();
    });
  });

  describe("placeOrder", () => {
    it("places a limit buy order for HK stock", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { broker_order_id: "FUTU-001" };
        return {};
      });

      const broker = new FutuBroker(config);
      await broker.connect();

      const order = await broker.placeOrder({
        symbol: "00700",
        market: "hk_stock",
        side: "buy",
        orderType: "limit",
        price: 350.0,
        quantity: 100,
      });

      expect(order.status).toBe("submitted");
      expect(order.brokerOrderId).toBe("FUTU-001");
      expect(order.symbol).toBe("00700");
      expect(order.side).toBe("buy");
      expect(order.price).toBe(350.0);
      expect(order.quantity).toBe(100);

      // Verify the Python bridge was called with correct Futu symbol
      expect(mockCall).toHaveBeenCalledWith(
        "place_order",
        expect.objectContaining({
          stock_code: "HK.00700",
          side: "buy",
          order_type: "limit",
          price: 350.0,
          volume: 100,
        }),
      );
    });

    it("rejects limit order without price", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      await expect(
        broker.placeOrder({
          symbol: "600519",
          market: "cn_stock",
          side: "buy",
          orderType: "limit",
          quantity: 100,
        }),
      ).rejects.toThrow("限价单必须指定正数 price");
    });
  });

  describe("subscribeQuote", () => {
    it("subscribes and receives quote pushes", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      const quotes: unknown[] = [];
      const unsub = broker.subscribeQuote(["600519"], (q) => quotes.push(q));

      expect(mockCall).toHaveBeenCalledWith(
        "subscribe_quote",
        expect.objectContaining({ stock_codes: ["SH.600519"] }),
      );

      // Simulate a quote push from Python
      eventHandler?.("quote", {
        stock_code: "SH.600519",
        last_price: 1800.5,
        open: 1790.0,
        high: 1810.0,
        low: 1785.0,
        last_close: 1795.0,
        volume: 50000,
        amount: 90_000_000,
        bid_price: [1800.0],
        ask_price: [1801.0],
        bid_vol: [100],
        ask_vol: [200],
        time: 1700000000000,
      });

      expect(quotes).toHaveLength(1);
      expect(quotes[0]).toMatchObject({
        price: 1800.5,
        symbol: expect.objectContaining({ code: "600519" }),
      });

      // Unsubscribe
      unsub();
      eventHandler?.("quote", {
        stock_code: "SH.600519",
        last_price: 1801.0,
        open: 1790.0,
        high: 1810.0,
        low: 1785.0,
        last_close: 1795.0,
        volume: 50001,
        amount: 90_001_000,
        bid_price: [1800.5],
        ask_price: [1801.5],
        bid_vol: [100],
        ask_vol: [200],
        time: 1700000001000,
      });

      // Should not receive after unsubscribe
      expect(quotes).toHaveLength(1);
    });
  });

  describe("subscribeFills", () => {
    it("receives fill push events", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { broker_order_id: "FUTU-002" };
        return {};
      });

      const broker = new FutuBroker(config);
      await broker.connect();

      // Place an order so we have a cached entry
      await broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        price: 1800.0,
        quantity: 100,
      });

      const fills: unknown[] = [];
      broker.subscribeFills((fill) => fills.push(fill));

      // Simulate fill push
      eventHandler?.("fill", {
        order_id: "FUTU-002",
        stock_code: "SH.600519",
        traded_price: 1800.0,
        traded_volume: 100,
        traded_amount: 180000,
        commission: 5.4,
        time: "2026-04-07 10:30:00",
      });

      expect(fills).toHaveLength(1);
      expect(fills[0]).toMatchObject({
        symbol: "600519",
        price: 1800.0,
        quantity: 100,
        commission: 5.4,
      });
    });
  });

  describe("subscribeOrderStatus", () => {
    it("receives order status change pushes", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { broker_order_id: "FUTU-003" };
        return {};
      });

      const broker = new FutuBroker(config);
      await broker.connect();

      await broker.placeOrder({
        symbol: "AAPL",
        market: "us_stock",
        side: "buy",
        orderType: "limit",
        price: 180.0,
        quantity: 10,
      });

      const statuses: Array<{ id: string; status: OrderStatus }> = [];
      broker.subscribeOrderStatus((id, status) => statuses.push({ id, status }));

      eventHandler?.("order_status", {
        order_id: "FUTU-003",
        stock_code: "US.AAPL",
        order_type: "BUY",
        price: 180.0,
        order_volume: 10,
        traded_volume: 10,
        traded_price: 179.5,
        order_status: "FILLED_ALL",
        order_time: "2026-04-07 10:30:00",
      });

      expect(statuses).toHaveLength(1);
      expect(statuses[0].status).toBe("filled");
    });
  });

  describe("onPositionChange", () => {
    it("receives position change push events", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      const changes: Array<{ code: string; pos: unknown }> = [];
      broker.onPositionChange((code, pos) => changes.push({ code, pos }));

      eventHandler?.("position_change", {
        stock_code: "HK.00700",
        previous: null,
        current: {
          stock_code: "HK.00700",
          stock_name: "腾讯控股",
          volume: 100,
          can_use_volume: 100,
          frozen_volume: 0,
          today_buy_volume: 100,
          avg_price: 350.0,
          market_value: 35500.0,
          last_price: 355.0,
          unrealized_pnl: 500.0,
          unrealized_pnl_pct: 0.0143,
        },
      });

      expect(changes).toHaveLength(1);
      expect(changes[0].code).toBe("00700");
      expect(changes[0].pos).toMatchObject({
        symbol: "00700",
        quantity: 100,
        avgCost: 350.0,
      });
    });
  });

  describe("subscribeOrderBook", () => {
    it("receives order book push events", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      const books: unknown[] = [];
      broker.subscribeOrderBook((code, bids, asks, time) =>
        books.push({ code, bids, asks, time }),
      );

      eventHandler?.("order_book", {
        stock_code: "SH.600519",
        bids: [
          { price: 1800.0, volume: 100, order_count: 5 },
          { price: 1799.5, volume: 200, order_count: 10 },
        ],
        asks: [
          { price: 1800.5, volume: 150, order_count: 3 },
          { price: 1801.0, volume: 300, order_count: 8 },
        ],
        time: 1700000000000,
      });

      expect(books).toHaveLength(1);
      expect(books[0]).toMatchObject({
        code: "600519",
        bids: expect.arrayContaining([
          expect.objectContaining({ price: 1800.0, volume: 100 }),
        ]),
        asks: expect.arrayContaining([
          expect.objectContaining({ price: 1800.5, volume: 150 }),
        ]),
      });
    });
  });

  describe("subscribeTicker", () => {
    it("receives ticker push events", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      const tickers: unknown[] = [];
      broker.subscribeTicker((data) => tickers.push(data));

      eventHandler?.("ticker", {
        stock_code: "HK.00700",
        price: 355.2,
        volume: 500,
        turnover: 177600,
        ticker_direction: "BUY",
        sequence: 12345,
        time: "2026-04-07 10:30:00",
      });

      expect(tickers).toHaveLength(1);
      expect(tickers[0]).toMatchObject({
        code: "00700",
        price: 355.2,
        volume: 500,
        direction: "BUY",
      });
    });
  });

  describe("getQuoteSnapshot", () => {
    it("gets quote snapshot without subscribing", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "get_quote_snapshot") {
          return [
            {
              stock_code: "SH.600519",
              last_price: 1800.5,
              open: 1790.0,
              high: 1810.0,
              low: 1785.0,
              last_close: 1795.0,
              volume: 50000,
              amount: 90_000_000,
              bid_price: [1800.0],
              ask_price: [1801.0],
              bid_vol: [100],
              ask_vol: [200],
              time: 1700000000000,
            },
          ];
        }
        return {};
      });

      const broker = new FutuBroker(config);
      await broker.connect();

      const snapshots = await broker.getQuoteSnapshot(["600519"]);
      expect(snapshots).toHaveLength(1);
      expect(snapshots[0]).toMatchObject({
        price: 1800.5,
        symbol: expect.objectContaining({ code: "600519" }),
      });

      expect(mockCall).toHaveBeenCalledWith("get_quote_snapshot", {
        stock_codes: ["SH.600519"],
      });
    });
  });

  describe("subscribeQuoteEx", () => {
    it("subscribes with multiple sub types", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      const quotes: unknown[] = [];
      broker.subscribeQuoteEx(
        ["600519"],
        (q) => quotes.push(q),
        ["QUOTE", "ORDER_BOOK", "TICKER"],
      );

      expect(mockCall).toHaveBeenCalledWith("subscribe_quote", {
        stock_codes: ["SH.600519"],
        sub_types: ["QUOTE", "ORDER_BOOK", "TICKER"],
      });
    });
  });

  describe("cancelOrder", () => {
    it("cancels an existing order", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { broker_order_id: "FUTU-004" };
        return {};
      });

      const broker = new FutuBroker(config);
      await broker.connect();

      const order = await broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        price: 1800.0,
        quantity: 100,
      });

      await broker.cancelOrder(order.orderId);

      expect(mockCall).toHaveBeenCalledWith("cancel_order", {
        order_id: "FUTU-004",
      });
    });

    it("throws for unknown order", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      await expect(broker.cancelOrder("nonexistent")).rejects.toThrow(
        "not found",
      );
    });
  });

  describe("modifyOrder", () => {
    it("modifies via cancel + resubmit", async () => {
      let callCount = 0;
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") {
          callCount++;
          return { broker_order_id: `FUTU-${callCount}` };
        }
        return {};
      });

      const broker = new FutuBroker(config);
      await broker.connect();

      const order = await broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        price: 1800.0,
        quantity: 100,
      });

      const modified = await broker.modifyOrder(order.orderId, {
        price: 1810.0,
        quantity: 200,
      });

      expect(modified.price).toBe(1810.0);
      expect(modified.quantity).toBe(200);
      // Should have called cancel_order then place_order again
      expect(mockCall).toHaveBeenCalledWith("cancel_order", expect.anything());
    });
  });
});
