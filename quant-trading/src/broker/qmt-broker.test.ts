/**
 * Unit tests for QMTBroker — the real-money 迅投 broker adapter.
 *
 * Mocks PersistentPythonProcess to verify order flow, position queries,
 * event handling, and symbol normalisation without connecting to real QMT.
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

import { QMTBroker } from "./qmt-broker.js";

const config: QuantTradingConfig = {
  dataDir: "/tmp/oc-qmt-test",
  pythonPath: "python3",
  tradingAccount: {
    mode: "live",
    broker: "qmt",
    qmt: {
      miniQmtPath: "/mock/qmt",
      accountId: "test-account",
      accountType: "STOCK",
    },
    riskLimits: {},
  },
} as QuantTradingConfig;

beforeEach(() => {
  vi.clearAllMocks();
  // Restore mockCall's resolved value after clearAllMocks resets it
  mockCall.mockResolvedValue({});
  mockStart.mockResolvedValue(undefined);
  eventHandler = null;
});

describe("QMTBroker", () => {
  describe("connect/disconnect", () => {
    it("connects via python process", async () => {
      const broker = new QMTBroker(config);
      expect(broker.connected).toBe(false);

      await broker.connect();
      expect(broker.connected).toBe(true);
      expect(mockStart).toHaveBeenCalled();
      expect(mockCall).toHaveBeenCalledWith("connect");
    });

    it("is idempotent on double connect", async () => {
      const broker = new QMTBroker(config);
      await broker.connect();
      await broker.connect();
      expect(mockStart).toHaveBeenCalledTimes(1);
    });

    it("disconnects cleanly", async () => {
      const broker = new QMTBroker(config);
      await broker.connect();
      await broker.disconnect();
      expect(broker.connected).toBe(false);
      expect(mockCall).toHaveBeenCalledWith("disconnect");
      expect(mockStop).toHaveBeenCalled();
    });
  });

  describe("placeOrder", () => {
    it("places a limit buy order", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { broker_order_id: "QMT-001" };
        return {};
      });

      const broker = new QMTBroker(config);
      await broker.connect();

      const order = await broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        price: 1800,
        quantity: 100,
        strategyId: "macd",
      });

      expect(order.brokerOrderId).toBe("QMT-001");
      expect(order.symbol).toBe("600519");
      expect(order.side).toBe("buy");
      expect(order.status).toBe("submitted");
      expect(order.brokerType).toBe("qmt");
      expect(order.quantity).toBe(100);

      expect(mockCall).toHaveBeenCalledWith(
        "place_order",
        expect.objectContaining({
          stock_code: "600519.SH",
          order_type: 23, // buy
          price_type: 11, // limit
          price: 1800,
          volume: 100,
        }),
      );
    });

    it("places a market sell order", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { broker_order_id: "QMT-002" };
        return {};
      });

      const broker = new QMTBroker(config);
      await broker.connect();

      const order = await broker.placeOrder({
        symbol: "000001.SZ",
        market: "cn_stock",
        side: "sell",
        orderType: "market",
        quantity: 200,
      });

      expect(order.side).toBe("sell");
      expect(mockCall).toHaveBeenCalledWith(
        "place_order",
        expect.objectContaining({
          order_type: 24, // sell
          price_type: 5, // market
        }),
      );
    });
  });

  describe("cancelOrder", () => {
    it("cancels an order by local ID", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { broker_order_id: "QMT-003" };
        return {};
      });

      const broker = new QMTBroker(config);
      await broker.connect();
      const order = await broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        price: 1800,
        quantity: 100,
      });

      await broker.cancelOrder(order.orderId);
      expect(mockCall).toHaveBeenCalledWith(
        "cancel_order",
        expect.objectContaining({
          order_id: "QMT-003",
          stock_code: "600519.SH",
        }),
      );

      // Order should now be cancelled
      const queried = await broker.queryOrder(order.orderId);
      expect(queried?.status).toBe("cancelled");
    });

    it("throws for non-existent order", async () => {
      const broker = new QMTBroker(config);
      await broker.connect();
      await expect(broker.cancelOrder("nonexistent")).rejects.toThrow("not found");
    });
  });

  describe("modifyOrder", () => {
    it("cancels and resubmits with new price", async () => {
      let callCount = 0;
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") {
          callCount++;
          return { broker_order_id: `QMT-${callCount}` };
        }
        return {};
      });

      const broker = new QMTBroker(config);
      await broker.connect();
      const order = await broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        price: 1800,
        quantity: 100,
      });

      const modified = await broker.modifyOrder(order.orderId, { price: 1750 });
      expect(modified.price).toBe(1750);
      expect(modified.orderId).not.toBe(order.orderId); // New order
    });
  });

  describe("queryAccount", () => {
    it("maps raw account data", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "query_account") {
          return {
            account_id: "acc-001",
            cash: 500000,
            frozen_cash: 10000,
            total_asset: 800000,
            market_value: 300000,
            profit: 50000,
            profit_ratio: 0.0667,
          };
        }
        return {};
      });

      const broker = new QMTBroker(config);
      await broker.connect();
      const account = await broker.queryAccount();

      expect(account.accountId).toBe("acc-001");
      expect(account.cash).toBe(500000);
      expect(account.frozenCash).toBe(10000);
      expect(account.totalAssets).toBe(800000);
      expect(account.brokerType).toBe("qmt");
    });
  });

  describe("queryPositions", () => {
    it("maps raw position data", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "query_positions") {
          return [
            {
              stock_code: "600519.SH",
              stock_name: "贵州茅台",
              volume: 200,
              can_use_volume: 100,
              frozen_volume: 100,
              today_buy_volume: 100,
              avg_price: 1750,
              market_value: 360000,
            },
          ];
        }
        return {};
      });

      const broker = new QMTBroker(config);
      await broker.connect();
      const positions = await broker.queryPositions();

      expect(positions).toHaveLength(1);
      expect(positions[0].symbol).toBe("600519"); // stripped suffix
      expect(positions[0].name).toBe("贵州茅台");
      expect(positions[0].quantity).toBe(200);
      expect(positions[0].availableQty).toBe(100);
      expect(positions[0].avgCost).toBe(1750);
    });
  });

  describe("subscribeQuote", () => {
    it("subscribes and returns unsubscribe function", async () => {
      const broker = new QMTBroker(config);
      await broker.connect();

      const cb = vi.fn();
      const unsub = broker.subscribeQuote(["600519"], cb);

      expect(mockCall).toHaveBeenCalledWith("subscribe_quote", {
        stock_codes: ["600519.SH"],
      });

      // Unsubscribe
      unsub();
      expect(mockCall).toHaveBeenCalledWith("unsubscribe_quote", {
        stock_codes: ["600519.SH"],
      });
    });
  });

  describe("event handling", () => {
    it("dispatches fill events to subscribers", async () => {
      const broker = new QMTBroker(config);
      await broker.connect();

      const fillCb = vi.fn();
      broker.subscribeFills(fillCb);

      // Place an order first so local cache has it
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { broker_order_id: "QMT-FILL-1" };
        return {};
      });
      await broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        price: 1800,
        quantity: 100,
      });

      // Simulate fill event from Python
      eventHandler?.("fill", {
        order_id: "QMT-FILL-1",
        stock_code: "600519.SH",
        traded_price: 1800,
        traded_volume: 100,
        traded_amount: 180000,
        commission: 5.4,
        time: "2024-01-15 10:30:00",
      });

      expect(fillCb).toHaveBeenCalledTimes(1);
      const fill = fillCb.mock.calls[0][0];
      expect(fill.price).toBe(1800);
      expect(fill.quantity).toBe(100);
      expect(fill.commission).toBe(5.4);
    });

    it("dispatches quote events to subscribers", async () => {
      const broker = new QMTBroker(config);
      await broker.connect();

      const quoteCb = vi.fn();
      broker.subscribeQuote(["600519"], quoteCb);

      // Simulate quote event
      eventHandler?.("quote", {
        stock_code: "600519.SH",
        last_price: 1850,
        open: 1800,
        high: 1860,
        low: 1790,
        last_close: 1810,
        volume: 50000,
        amount: 92000000,
        bid_price: [1849],
        ask_price: [1851],
        bid_vol: [100],
        ask_vol: [200],
        time: Date.now(),
      });

      expect(quoteCb).toHaveBeenCalledTimes(1);
      const snapshot = quoteCb.mock.calls[0][0];
      expect(snapshot.price).toBe(1850);
      expect(snapshot.symbol.code).toBe("600519");
    });

    it("unsubscribe stops receiving events", async () => {
      const broker = new QMTBroker(config);
      await broker.connect();

      const cb = vi.fn();
      const unsub = broker.subscribeFills(cb);
      unsub();

      eventHandler?.("fill", {
        order_id: "X",
        stock_code: "600519.SH",
        traded_price: 100,
        traded_volume: 10,
        traded_amount: 1000,
        commission: 0.3,
        time: "2024-01-15 10:30:00",
      });

      expect(cb).not.toHaveBeenCalled();
    });
  });

  describe("market hours", () => {
    it("delegates to isCnMarketOpen", async () => {
      const broker = new QMTBroker(config);
      const open = await broker.isMarketOpen();
      expect(open).toBe(true);
    });
  });

  describe("symbol normalisation", () => {
    it("adds SH suffix for 6xxxxx codes", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { broker_order_id: "Q1" };
        return {};
      });

      const broker = new QMTBroker(config);
      await broker.connect();
      await broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        price: 1800,
        quantity: 100,
      });

      expect(mockCall).toHaveBeenCalledWith(
        "place_order",
        expect.objectContaining({ stock_code: "600519.SH" }),
      );
    });

    it("adds SZ suffix for 0xxxxx codes", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { broker_order_id: "Q2" };
        return {};
      });

      const broker = new QMTBroker(config);
      await broker.connect();
      await broker.placeOrder({
        symbol: "000001",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        price: 15,
        quantity: 100,
      });

      expect(mockCall).toHaveBeenCalledWith(
        "place_order",
        expect.objectContaining({ stock_code: "000001.SZ" }),
      );
    });

    it("passes already-suffixed symbols through", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { broker_order_id: "Q3" };
        return {};
      });

      const broker = new QMTBroker(config);
      await broker.connect();
      await broker.placeOrder({
        symbol: "000001.SZ",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        price: 15,
        quantity: 100,
      });

      expect(mockCall).toHaveBeenCalledWith(
        "place_order",
        expect.objectContaining({ stock_code: "000001.SZ" }),
      );
    });
  });
});
