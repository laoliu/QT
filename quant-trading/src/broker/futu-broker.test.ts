/**
 * Unit tests for FutuBroker — the 富途 OpenD broker adapter.
 *
 * Mocks PersistentPythonProcess to verify order flow, position queries,
 * event handling, and symbol conversion without connecting to real OpenD.
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
      pwdUnlock: "",
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
      expect(mockStop).toHaveBeenCalled();
    });
  });

  describe("placeOrder", () => {
    it("places a limit order via python process", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      mockCall.mockResolvedValueOnce({ broker_order_id: "FT-123" });

      const order = await broker.placeOrder({
        symbol: "00700",
        market: "hk_stock",
        side: "buy",
        orderType: "limit",
        quantity: 100,
        price: 350.0,
      });

      expect(order.brokerOrderId).toBe("FT-123");
      expect(order.symbol).toBe("00700");
      expect(order.market).toBe("hk_stock");
      expect(order.side).toBe("buy");
      expect(order.orderType).toBe("limit");
      expect(order.quantity).toBe(100);
      expect(order.status).toBe("submitted");
      expect(order.brokerType).toBe("futu");

      expect(mockCall).toHaveBeenCalledWith("place_order", {
        code: "HK.00700",
        trd_side: "BUY",
        order_type: "NORMAL",
        price: 350.0,
        qty: 100,
        remark: expect.any(String),
      });
    });

    it("rejects limit order without price", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      await expect(
        broker.placeOrder({
          symbol: "00700",
          market: "hk_stock",
          side: "buy",
          orderType: "limit",
          quantity: 100,
        }),
      ).rejects.toThrow("限价单必须指定正数 price");
    });

    it("places a market order for US stock", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      mockCall.mockResolvedValueOnce({ broker_order_id: "FT-456" });

      const order = await broker.placeOrder({
        symbol: "AAPL",
        market: "us_stock",
        side: "buy",
        orderType: "market",
        quantity: 10,
      });

      expect(order.brokerOrderId).toBe("FT-456");
      expect(mockCall).toHaveBeenCalledWith("place_order", {
        code: "US.AAPL",
        trd_side: "BUY",
        order_type: "MARKET",
        price: 0,
        qty: 10,
        remark: expect.any(String),
      });
    });
  });

  describe("cancelOrder", () => {
    it("cancels an existing order", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      mockCall.mockResolvedValueOnce({ broker_order_id: "FT-789" });
      const order = await broker.placeOrder({
        symbol: "00700",
        market: "hk_stock",
        side: "buy",
        orderType: "limit",
        quantity: 100,
        price: 350.0,
      });

      mockCall.mockResolvedValueOnce({ result: "ok" });
      await broker.cancelOrder(order.orderId);

      const queried = await broker.queryOrder(order.orderId);
      expect(queried?.status).toBe("cancelled");
    });

    it("throws on unknown order", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      await expect(broker.cancelOrder("nonexistent")).rejects.toThrow(
        "not found or has no broker ID",
      );
    });
  });

  describe("modifyOrder", () => {
    it("modifies price of a pending order", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      mockCall.mockResolvedValueOnce({ broker_order_id: "FT-100" });
      const order = await broker.placeOrder({
        symbol: "00700",
        market: "hk_stock",
        side: "buy",
        orderType: "limit",
        quantity: 100,
        price: 350.0,
      });

      mockCall.mockResolvedValueOnce({ broker_order_id: "FT-100" });
      const modified = await broker.modifyOrder(order.orderId, { price: 355.0 });

      expect(modified.price).toBe(355.0);
      expect(modified.quantity).toBe(100);
    });
  });

  describe("queryPositions", () => {
    it("returns mapped positions", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      mockCall.mockResolvedValueOnce([
        {
          code: "HK.00700",
          stock_name: "腾讯控股",
          qty: 200,
          can_sell_qty: 200,
          today_buy_qty: 0,
          cost_price: 340.0,
          market_val: 72000.0,
          current_price: 360.0,
          unrealized_pl: 4000.0,
          pl_ratio: 0.0588,
        },
      ]);

      const positions = await broker.queryPositions();
      expect(positions).toHaveLength(1);
      expect(positions[0].symbol).toBe("00700");
      expect(positions[0].name).toBe("腾讯控股");
      expect(positions[0].market).toBe("hk_stock");
      expect(positions[0].quantity).toBe(200);
      expect(positions[0].availableQty).toBe(200);
      expect(positions[0].avgCost).toBe(340.0);
      expect(positions[0].source).toBe("broker");
    });
  });

  describe("queryAccount", () => {
    it("returns mapped account info", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      mockCall.mockResolvedValueOnce({
        acc_id: "12345",
        cash: 100000,
        frozen_cash: 5000,
        total_assets: 200000,
        market_val: 95000,
        unrealized_pl: 3000,
        realized_pl: 1000,
      });

      const acct = await broker.queryAccount();
      expect(acct.accountId).toBe("12345");
      expect(acct.brokerType).toBe("futu");
      expect(acct.cash).toBe(100000);
      expect(acct.frozenCash).toBe(5000);
      expect(acct.totalAssets).toBe(200000);
      expect(acct.marketValue).toBe(95000);
      expect(acct.profit).toBe(4000); // unrealized + realized
    });
  });

  describe("subscribeQuote", () => {
    it("subscribes and dispatches quote events", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      const quotes: unknown[] = [];
      const unsub = broker.subscribeQuote(["00700"], (q) => quotes.push(q));

      // Simulate a quote push event
      eventHandler?.("quote", {
        code: "HK.00700",
        last_price: 355.0,
        open_price: 350.0,
        high_price: 358.0,
        low_price: 348.0,
        prev_close_price: 349.0,
        volume: 1000000,
        turnover: 355000000,
        timestamp: Date.now(),
        bid_price: 354.8,
        ask_price: 355.2,
      });

      expect(quotes).toHaveLength(1);
      expect((quotes[0] as { price: number }).price).toBe(355.0);

      unsub();
    });

    it("unsubscribe stops events", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      const quotes: unknown[] = [];
      const unsub = broker.subscribeQuote(["00700"], (q) => quotes.push(q));
      unsub();

      eventHandler?.("quote", {
        code: "HK.00700",
        last_price: 355.0,
        open_price: 350.0,
        high_price: 358.0,
        low_price: 348.0,
        prev_close_price: 349.0,
        volume: 1000000,
        turnover: 355000000,
        timestamp: Date.now(),
        bid_price: 354.8,
        ask_price: 355.2,
      });

      expect(quotes).toHaveLength(0);
    });
  });

  describe("subscribeFills", () => {
    it("dispatches fill events", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      // First place an order so we have something to match
      mockCall.mockResolvedValueOnce({ broker_order_id: "FT-FILL-1" });
      const order = await broker.placeOrder({
        symbol: "00700",
        market: "hk_stock",
        side: "buy",
        orderType: "limit",
        quantity: 100,
        price: 350.0,
      });

      const fills: unknown[] = [];
      broker.subscribeFills((f) => fills.push(f));

      eventHandler?.("fill", {
        order_id: "FT-FILL-1",
        code: "HK.00700",
        trd_side: "BUY",
        price: 350.0,
        qty: 100,
        create_time: "2025-01-15 10:30:00",
      });

      expect(fills).toHaveLength(1);
      const fill = fills[0] as { orderId: string; symbol: string; price: number; quantity: number };
      expect(fill.orderId).toBe(order.orderId);
      expect(fill.symbol).toBe("00700");
      expect(fill.price).toBe(350.0);
      expect(fill.quantity).toBe(100);
    });
  });

  describe("subscribeOrderStatus", () => {
    it("dispatches order status change events", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      mockCall.mockResolvedValueOnce({ broker_order_id: "FT-STATUS-1" });
      const order = await broker.placeOrder({
        symbol: "00700",
        market: "hk_stock",
        side: "buy",
        orderType: "limit",
        quantity: 100,
        price: 350.0,
      });

      const statusChanges: Array<{ orderId: string; status: OrderStatus }> = [];
      broker.subscribeOrderStatus((oid, status) => statusChanges.push({ orderId: oid, status }));

      eventHandler?.("order_status", {
        order_id: "FT-STATUS-1",
        code: "HK.00700",
        trd_side: "BUY",
        order_type: "NORMAL",
        price: 350.0,
        qty: 100,
        dealt_qty: 100,
        dealt_avg_price: 350.0,
        order_status: "FILLED_ALL",
        create_time: "2025-01-15 10:30:00",
        updated_time: "2025-01-15 10:30:01",
      });

      expect(statusChanges).toHaveLength(1);
      expect(statusChanges[0].orderId).toBe(order.orderId);
      expect(statusChanges[0].status).toBe("filled");
    });
  });

  describe("isMarketOpen", () => {
    it("returns boolean for different markets", async () => {
      const broker = new FutuBroker(config);
      const hk = await broker.isMarketOpen("hk_stock");
      const us = await broker.isMarketOpen("us_stock");
      const cn = await broker.isMarketOpen("cn_stock");
      expect(typeof hk).toBe("boolean");
      expect(typeof us).toBe("boolean");
      expect(typeof cn).toBe("boolean");
    });
  });

  describe("symbol conversion", () => {
    it("places CN stock orders with correct Futu code", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      mockCall.mockResolvedValueOnce({ broker_order_id: "FT-CN-1" });
      await broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        quantity: 100,
        price: 1800.0,
      });

      expect(mockCall).toHaveBeenCalledWith("place_order", {
        code: "SH.600519",
        trd_side: "BUY",
        order_type: "NORMAL",
        price: 1800.0,
        qty: 100,
        remark: expect.any(String),
      });
    });

    it("places SZ stock orders with correct Futu code", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      mockCall.mockResolvedValueOnce({ broker_order_id: "FT-CN-2" });
      await broker.placeOrder({
        symbol: "000001",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        quantity: 100,
        price: 15.0,
      });

      expect(mockCall).toHaveBeenCalledWith("place_order", {
        code: "SZ.000001",
        trd_side: "BUY",
        order_type: "NORMAL",
        price: 15.0,
        qty: 100,
        remark: expect.any(String),
      });
    });
  });
});
