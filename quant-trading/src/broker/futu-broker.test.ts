/**
 * Unit tests for FutuBroker — the 富途 OpenD broker adapter.
 *
 * Mocks PersistentPythonProcess to verify order flow, position queries,
 * event handling, symbol normalisation, and connection lifecycle without
 * connecting to real FutuOpenD.
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
      pwdUnlock: "123456",
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
  // ─────────────────────────────────────────────────────────────────
  // Connection lifecycle
  // ─────────────────────────────────────────────────────────────────
  describe("connect/disconnect", () => {
    it("connects via python process", async () => {
      const broker = new FutuBroker(config);
      expect(broker.connected).toBe(false);
      expect(broker.brokerType).toBe("futu");

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

    it("disconnect is no-op when not connected", async () => {
      const broker = new FutuBroker(config);
      await broker.disconnect();
      expect(mockCall).not.toHaveBeenCalledWith("disconnect");
      expect(mockStop).not.toHaveBeenCalled();
    });

    it("disconnect calls stop even if disconnect RPC throws", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "disconnect") return Promise.reject(new Error("OpenD gone"));
        return {};
      });

      const broker = new FutuBroker(config);
      await broker.connect();
      await broker.disconnect();
      expect(broker.connected).toBe(false);
      expect(mockStop).toHaveBeenCalled();
    });
  });

  // ─────────────────────────────────────────────────────────────────
  // Connection exceptions
  // ─────────────────────────────────────────────────────────────────
  describe("connection exceptions", () => {
    it("throws when OpenD process fails to start", async () => {
      mockStart.mockRejectedValueOnce(new Error("FutuOpenD not running"));
      const broker = new FutuBroker(config);
      await expect(broker.connect()).rejects.toThrow("FutuOpenD not running");
      expect(broker.connected).toBe(false);
    });

    it("throws when connect RPC fails", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "connect") throw new Error("Connection refused");
        return {};
      });
      const broker = new FutuBroker(config);
      await expect(broker.connect()).rejects.toThrow("Connection refused");
      expect(broker.connected).toBe(false);
    });
  });

  // ─────────────────────────────────────────────────────────────────
  // placeOrder
  // ─────────────────────────────────────────────────────────────────
  describe("placeOrder", () => {
    it("places a limit buy order for HK stock", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { order_id: "FUTU-001" };
        return {};
      });

      const broker = new FutuBroker(config);
      await broker.connect();

      const order = await broker.placeOrder({
        symbol: "00700",
        market: "hk_stock",
        side: "buy",
        orderType: "limit",
        price: 350,
        quantity: 100,
        strategyId: "macd",
      });

      expect(order.brokerOrderId).toBe("FUTU-001");
      expect(order.symbol).toBe("00700");
      expect(order.side).toBe("buy");
      expect(order.status).toBe("submitted");
      expect(order.brokerType).toBe("futu");
      expect(order.quantity).toBe(100);

      expect(mockCall).toHaveBeenCalledWith(
        "place_order",
        expect.objectContaining({
          code: "HK.00700",
          trd_side: "BUY",
          order_type: "NORMAL",
          price: 350,
          qty: 100,
        }),
      );
    });

    it("places a market sell order for CN stock", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { order_id: "FUTU-002" };
        return {};
      });

      const broker = new FutuBroker(config);
      await broker.connect();

      const order = await broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "sell",
        orderType: "market",
        quantity: 200,
      });

      expect(order.side).toBe("sell");
      expect(mockCall).toHaveBeenCalledWith(
        "place_order",
        expect.objectContaining({
          code: "SH.600519",
          trd_side: "SELL",
          order_type: "MARKET",
          price: 0,
        }),
      );
    });

    it("places a US stock order", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { order_id: "FUTU-003" };
        return {};
      });

      const broker = new FutuBroker(config);
      await broker.connect();

      const order = await broker.placeOrder({
        symbol: "AAPL",
        market: "us_stock",
        side: "buy",
        orderType: "limit",
        price: 180,
        quantity: 10,
      });

      expect(mockCall).toHaveBeenCalledWith(
        "place_order",
        expect.objectContaining({
          code: "US.AAPL",
          trd_side: "BUY",
          order_type: "NORMAL",
          price: 180,
        }),
      );
    });

    it("places a short sell order", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { order_id: "FUTU-004" };
        return {};
      });

      const broker = new FutuBroker(config);
      await broker.connect();

      await broker.placeOrder({
        symbol: "00700",
        market: "hk_stock",
        side: "short",
        orderType: "limit",
        price: 350,
        quantity: 100,
      });

      expect(mockCall).toHaveBeenCalledWith(
        "place_order",
        expect.objectContaining({
          trd_side: "SELL_SHORT",
        }),
      );
    });

    it("throws for limit order without price", async () => {
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

    it("throws for limit order with zero price", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      await expect(
        broker.placeOrder({
          symbol: "00700",
          market: "hk_stock",
          side: "buy",
          orderType: "limit",
          price: 0,
          quantity: 100,
        }),
      ).rejects.toThrow("限价单必须指定正数 price");
    });
  });

  // ─────────────────────────────────────────────────────────────────
  // cancelOrder
  // ─────────────────────────────────────────────────────────────────
  describe("cancelOrder", () => {
    it("cancels an order by local ID", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { order_id: "FUTU-C01" };
        return {};
      });

      const broker = new FutuBroker(config);
      await broker.connect();
      const order = await broker.placeOrder({
        symbol: "00700",
        market: "hk_stock",
        side: "buy",
        orderType: "limit",
        price: 350,
        quantity: 100,
      });

      await broker.cancelOrder(order.orderId);
      expect(mockCall).toHaveBeenCalledWith(
        "cancel_order",
        expect.objectContaining({
          order_id: "FUTU-C01",
          code: "HK.00700",
        }),
      );

      // Order should now be cancelled
      const queried = await broker.queryOrder(order.orderId);
      expect(queried?.status).toBe("cancelled");
    });

    it("throws for non-existent order", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();
      await expect(broker.cancelOrder("nonexistent")).rejects.toThrow("not found");
    });

    it("notifies order status subscribers on cancel", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { order_id: "FUTU-C02" };
        return {};
      });

      const broker = new FutuBroker(config);
      await broker.connect();

      const statusCb = vi.fn();
      broker.subscribeOrderStatus(statusCb);

      const order = await broker.placeOrder({
        symbol: "00700",
        market: "hk_stock",
        side: "buy",
        orderType: "limit",
        price: 350,
        quantity: 100,
      });

      await broker.cancelOrder(order.orderId);
      expect(statusCb).toHaveBeenCalledWith(order.orderId, "cancelled", expect.any(Object));
    });
  });

  // ─────────────────────────────────────────────────────────────────
  // modifyOrder
  // ─────────────────────────────────────────────────────────────────
  describe("modifyOrder", () => {
    it("modifies price via Futu API", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { order_id: "FUTU-M01" };
        if (method === "modify_order") return { order_id: "FUTU-M01" };
        return {};
      });

      const broker = new FutuBroker(config);
      await broker.connect();
      const order = await broker.placeOrder({
        symbol: "00700",
        market: "hk_stock",
        side: "buy",
        orderType: "limit",
        price: 350,
        quantity: 100,
      });

      const modified = await broker.modifyOrder(order.orderId, { price: 340 });
      expect(modified.price).toBe(340);
      expect(modified.orderId).toBe(order.orderId); // in-place modify
      expect(mockCall).toHaveBeenCalledWith(
        "modify_order",
        expect.objectContaining({
          order_id: "FUTU-M01",
          price: 340,
          qty: 100,
        }),
      );
    });

    it("modifies quantity via Futu API", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { order_id: "FUTU-M02" };
        if (method === "modify_order") return { order_id: "FUTU-M02" };
        return {};
      });

      const broker = new FutuBroker(config);
      await broker.connect();
      const order = await broker.placeOrder({
        symbol: "00700",
        market: "hk_stock",
        side: "buy",
        orderType: "limit",
        price: 350,
        quantity: 100,
      });

      const modified = await broker.modifyOrder(order.orderId, { quantity: 200 });
      expect(modified.quantity).toBe(200);
    });

    it("throws for non-existent order", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();
      await expect(broker.modifyOrder("nonexistent", { price: 100 })).rejects.toThrow("not found");
    });
  });

  // ─────────────────────────────────────────────────────────────────
  // queryAccount
  // ─────────────────────────────────────────────────────────────────
  describe("queryAccount", () => {
    it("maps raw account data", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "query_account") {
          return {
            account_id: "futu-acc-001",
            cash: 500000,
            frozen_cash: 10000,
            total_assets: 800000,
            market_val: 300000,
            unrealized_pl: 45000,
            realized_pl: 5000,
          };
        }
        return {};
      });

      const broker = new FutuBroker(config);
      await broker.connect();
      const account = await broker.queryAccount();

      expect(account.accountId).toBe("futu-acc-001");
      expect(account.cash).toBe(500000);
      expect(account.frozenCash).toBe(10000);
      expect(account.totalAssets).toBe(800000);
      expect(account.marketValue).toBe(300000);
      expect(account.profit).toBe(50000); // 45000 + 5000
      expect(account.brokerType).toBe("futu");
    });
  });

  // ─────────────────────────────────────────────────────────────────
  // queryPositions
  // ─────────────────────────────────────────────────────────────────
  describe("queryPositions", () => {
    it("maps raw HK position data", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "query_positions") {
          return [
            {
              code: "HK.00700",
              stock_name: "腾讯控股",
              qty: 200,
              can_sell_qty: 100,
              today_buy_qty: 100,
              cost_price: 340,
              market_val: 70000,
              current_price: 350,
              unrealized_pl: 2000,
              unrealized_pl_ratio: 0.03,
            },
          ];
        }
        return {};
      });

      const broker = new FutuBroker(config);
      await broker.connect();
      const positions = await broker.queryPositions();

      expect(positions).toHaveLength(1);
      expect(positions[0].symbol).toBe("00700");
      expect(positions[0].name).toBe("腾讯控股");
      expect(positions[0].market).toBe("hk_stock");
      expect(positions[0].quantity).toBe(200);
      expect(positions[0].availableQty).toBe(100);
      expect(positions[0].frozenQty).toBe(100); // 200 - 100
      expect(positions[0].todayBuyQty).toBe(100);
      expect(positions[0].avgCost).toBe(340);
      expect(positions[0].currentPrice).toBe(350);
      expect(positions[0].unrealizedPnl).toBe(2000);
    });

    it("maps CN stock position data", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "query_positions") {
          return [
            {
              code: "SH.600519",
              stock_name: "贵州茅台",
              qty: 100,
              can_sell_qty: 100,
              today_buy_qty: 0,
              cost_price: 1800,
              market_val: 185000,
              current_price: 1850,
            },
          ];
        }
        return {};
      });

      const broker = new FutuBroker(config);
      await broker.connect();
      const positions = await broker.queryPositions();

      expect(positions[0].symbol).toBe("600519");
      expect(positions[0].market).toBe("cn_stock");
    });

    it("handles position with no current price — derives from market_val/qty", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "query_positions") {
          return [
            {
              code: "HK.00700",
              stock_name: "腾讯控股",
              qty: 100,
              can_sell_qty: 100,
              today_buy_qty: 0,
              cost_price: 340,
              market_val: 35000,
              current_price: null,
            },
          ];
        }
        return {};
      });

      const broker = new FutuBroker(config);
      await broker.connect();
      const positions = await broker.queryPositions();

      expect(positions[0].currentPrice).toBe(350); // 35000/100
    });
  });

  // ─────────────────────────────────────────────────────────────────
  // queryOrder / queryOrders
  // ─────────────────────────────────────────────────────────────────
  describe("queryOrder / queryOrders", () => {
    it("queries a single order from cache + broker", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { order_id: "FUTU-Q01" };
        if (method === "query_order") {
          return {
            order_id: "FUTU-Q01",
            code: "HK.00700",
            trd_side: "BUY",
            order_type: "NORMAL",
            price: 350,
            qty: 100,
            dealt_qty: 50,
            dealt_avg_price: 349,
            order_status: "FILLED_PART",
          };
        }
        return {};
      });

      const broker = new FutuBroker(config);
      await broker.connect();
      const order = await broker.placeOrder({
        symbol: "00700",
        market: "hk_stock",
        side: "buy",
        orderType: "limit",
        price: 350,
        quantity: 100,
      });

      const queried = await broker.queryOrder(order.orderId);
      expect(queried).not.toBeNull();
      expect(queried!.status).toBe("partial");
      expect(queried!.filledQty).toBe(50);
      expect(queried!.avgFillPrice).toBe(349);
    });

    it("returns null for unknown order", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();
      const result = await broker.queryOrder("unknown-id");
      expect(result).toBeNull();
    });

    it("queryOrders returns mapped broker orders", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "query_orders") {
          return [
            {
              order_id: "FUTU-QO1",
              code: "HK.00700",
              trd_side: "BUY",
              order_type: "NORMAL",
              price: 350,
              qty: 100,
              dealt_qty: 100,
              dealt_avg_price: 350,
              order_status: "FILLED_ALL",
            },
            {
              order_id: "FUTU-QO2",
              code: "US.AAPL",
              trd_side: "SELL",
              order_type: "MARKET",
              price: 0,
              qty: 10,
              dealt_qty: 10,
              dealt_avg_price: 180,
              order_status: "FILLED_ALL",
            },
          ];
        }
        return {};
      });

      const broker = new FutuBroker(config);
      await broker.connect();
      const orders = await broker.queryOrders();

      expect(orders).toHaveLength(2);
      expect(orders[0].symbol).toBe("00700");
      expect(orders[0].market).toBe("hk_stock");
      expect(orders[0].status).toBe("filled");
      expect(orders[1].symbol).toBe("AAPL");
      expect(orders[1].market).toBe("us_stock");
    });

    it("queryOrders with symbol filter", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "query_orders") return [];
        return {};
      });

      const broker = new FutuBroker(config);
      await broker.connect();
      await broker.queryOrders({ symbol: "HK.00700" });

      expect(mockCall).toHaveBeenCalledWith(
        "query_orders",
        expect.objectContaining({ code: "HK.00700" }),
      );
    });
  });

  // ─────────────────────────────────────────────────────────────────
  // subscribeQuote
  // ─────────────────────────────────────────────────────────────────
  describe("subscribeQuote", () => {
    it("subscribes and returns unsubscribe function", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      const cb = vi.fn();
      const unsub = broker.subscribeQuote(["HK.00700"], cb);

      expect(mockCall).toHaveBeenCalledWith("subscribe_quote", {
        codes: ["HK.00700"],
      });

      unsub();
      expect(mockCall).toHaveBeenCalledWith("unsubscribe_quote", {
        codes: ["HK.00700"],
      });
    });

    it("subscribes to multiple symbols", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      const cb = vi.fn();
      broker.subscribeQuote(["HK.00700", "600519"], cb);

      expect(mockCall).toHaveBeenCalledWith("subscribe_quote", {
        codes: ["HK.00700", "SH.600519"],
      });
    });
  });

  // ─────────────────────────────────────────────────────────────────
  // Event handling — push notifications from Python
  // ─────────────────────────────────────────────────────────────────
  describe("event handling", () => {
    it("dispatches fill events to subscribers", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      const fillCb = vi.fn();
      broker.subscribeFills(fillCb);

      // Place an order first so local cache has it
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { order_id: "FUTU-FILL-1" };
        return {};
      });
      const order = await broker.placeOrder({
        symbol: "00700",
        market: "hk_stock",
        side: "buy",
        orderType: "limit",
        price: 350,
        quantity: 100,
      });

      // Simulate fill event from Python
      eventHandler?.("fill", {
        order_id: "FUTU-FILL-1",
        code: "HK.00700",
        trd_side: "BUY",
        price: 350,
        qty: 100,
        create_time: "2024-01-15 10:30:00",
        commission: 12.5,
      });

      expect(fillCb).toHaveBeenCalledTimes(1);
      const fill = fillCb.mock.calls[0][0];
      expect(fill.price).toBe(350);
      expect(fill.quantity).toBe(100);
      expect(fill.commission).toBe(12.5);
      expect(fill.symbol).toBe("00700");
    });

    it("updates cached order status on fill event", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { order_id: "FUTU-FILL-2" };
        return {};
      });
      const order = await broker.placeOrder({
        symbol: "00700",
        market: "hk_stock",
        side: "buy",
        orderType: "limit",
        price: 350,
        quantity: 100,
      });

      // Partial fill
      eventHandler?.("fill", {
        order_id: "FUTU-FILL-2",
        code: "HK.00700",
        trd_side: "BUY",
        price: 350,
        qty: 50,
        create_time: "2024-01-15 10:30:00",
      });

      const queried1 = await broker.queryOrder(order.orderId);
      expect(queried1?.status).toBe("partial");
      expect(queried1?.filledQty).toBe(50);

      // Full fill
      eventHandler?.("fill", {
        order_id: "FUTU-FILL-2",
        code: "HK.00700",
        trd_side: "BUY",
        price: 350,
        qty: 50,
        create_time: "2024-01-15 10:31:00",
      });

      const queried2 = await broker.queryOrder(order.orderId);
      expect(queried2?.status).toBe("filled");
      expect(queried2?.filledQty).toBe(100);
    });

    it("dispatches order_status events to subscribers", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      const statusCb = vi.fn();
      broker.subscribeOrderStatus(statusCb);

      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { order_id: "FUTU-STATUS-1" };
        return {};
      });
      await broker.placeOrder({
        symbol: "00700",
        market: "hk_stock",
        side: "buy",
        orderType: "limit",
        price: 350,
        quantity: 100,
      });

      // Simulate order_status event from Python
      eventHandler?.("order_status", {
        order_id: "FUTU-STATUS-1",
        code: "HK.00700",
        trd_side: "BUY",
        order_type: "NORMAL",
        price: 350,
        qty: 100,
        dealt_qty: 100,
        dealt_avg_price: 349.5,
        order_status: "FILLED_ALL",
      });

      expect(statusCb).toHaveBeenCalledTimes(1);
      const [orderId, status] = statusCb.mock.calls[0];
      expect(status).toBe("filled");
    });

    it("dispatches quote events to subscribers", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      const quoteCb = vi.fn();
      broker.subscribeQuote(["00700"], quoteCb);

      eventHandler?.("quote", {
        code: "HK.00700",
        last_price: 355,
        open_price: 348,
        high_price: 358,
        low_price: 346,
        prev_close_price: 350,
        volume: 50000000,
        turnover: 17500000000,
        bid_price: 354.8,
        ask_price: 355.2,
        timestamp: Date.now(),
      });

      expect(quoteCb).toHaveBeenCalledTimes(1);
      const snapshot = quoteCb.mock.calls[0][0];
      expect(snapshot.price).toBe(355);
      expect(snapshot.symbol.code).toBe("00700");
      expect(snapshot.symbol.market).toBe("hk_stock");
      expect(snapshot.open).toBe(348);
      expect(snapshot.high).toBe(358);
      expect(snapshot.low).toBe(346);
      expect(snapshot.prevClose).toBe(350);
      expect(snapshot.changePct).toBeCloseTo((5 / 350) * 100, 2);
    });

    it("does not dispatch quotes for unsubscribed symbols", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      const cb = vi.fn();
      broker.subscribeQuote(["00700"], cb);

      // Quote for a different symbol
      eventHandler?.("quote", {
        code: "HK.09988",
        last_price: 100,
        open_price: 98,
        high_price: 101,
        low_price: 97,
        prev_close_price: 99,
        volume: 1000000,
        turnover: 100000000,
        bid_price: 99.8,
        ask_price: 100.2,
        timestamp: Date.now(),
      });

      expect(cb).not.toHaveBeenCalled();
    });

    it("unsubscribe fills stops receiving events", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      const cb = vi.fn();
      const unsub = broker.subscribeFills(cb);
      unsub();

      eventHandler?.("fill", {
        order_id: "X",
        code: "HK.00700",
        trd_side: "BUY",
        price: 350,
        qty: 100,
        create_time: "2024-01-15 10:30:00",
      });

      expect(cb).not.toHaveBeenCalled();
    });

    it("unsubscribe order status stops receiving events", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      const cb = vi.fn();
      const unsub = broker.subscribeOrderStatus(cb);
      unsub();

      eventHandler?.("order_status", {
        order_id: "X",
        code: "HK.00700",
        trd_side: "BUY",
        order_type: "NORMAL",
        price: 350,
        qty: 100,
        dealt_qty: 0,
        dealt_avg_price: 0,
        order_status: "CANCELLED_ALL",
      });

      expect(cb).not.toHaveBeenCalled();
    });
  });

  // ─────────────────────────────────────────────────────────────────
  // Market hours
  // ─────────────────────────────────────────────────────────────────
  describe("market hours", () => {
    it("delegates CN market to isCnMarketOpen", async () => {
      const broker = new FutuBroker(config);
      const open = await broker.isMarketOpen();
      expect(open).toBe(true);
    });

    it("delegates CN market explicitly to isCnMarketOpen", async () => {
      const broker = new FutuBroker(config);
      const open = await broker.isMarketOpen("cn_stock");
      expect(open).toBe(true);
    });

    it("queries Futu API for HK market hours", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "is_market_open") return { is_open: true };
        return {};
      });

      const broker = new FutuBroker(config);
      await broker.connect();
      const open = await broker.isMarketOpen("hk_stock");
      expect(open).toBe(true);
      expect(mockCall).toHaveBeenCalledWith("is_market_open", { market: "hk_stock" });
    });

    it("returns false when market hours check throws", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "is_market_open") throw new Error("API error");
        return {};
      });

      const broker = new FutuBroker(config);
      await broker.connect();
      const open = await broker.isMarketOpen("us_stock");
      expect(open).toBe(false);
    });
  });

  // ─────────────────────────────────────────────────────────────────
  // ping
  // ─────────────────────────────────────────────────────────────────
  describe("ping", () => {
    it("returns true when OpenD is healthy", async () => {
      mockCall.mockResolvedValue({});
      const broker = new FutuBroker(config);
      await broker.connect();
      const result = await broker.ping!();
      expect(result).toBe(true);
    });

    it("returns false when ping fails", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "ping") throw new Error("timeout");
        return {};
      });

      const broker = new FutuBroker(config);
      await broker.connect();
      const result = await broker.ping!();
      expect(result).toBe(false);
    });
  });

  // ─────────────────────────────────────────────────────────────────
  // Symbol normalisation
  // ─────────────────────────────────────────────────────────────────
  describe("symbol normalisation", () => {
    it("adds HK prefix for HK market", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { order_id: "F1" };
        return {};
      });

      const broker = new FutuBroker(config);
      await broker.connect();
      await broker.placeOrder({
        symbol: "00700",
        market: "hk_stock",
        side: "buy",
        orderType: "limit",
        price: 350,
        quantity: 100,
      });

      expect(mockCall).toHaveBeenCalledWith(
        "place_order",
        expect.objectContaining({ code: "HK.00700" }),
      );
    });

    it("adds US prefix for US market", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { order_id: "F2" };
        return {};
      });

      const broker = new FutuBroker(config);
      await broker.connect();
      await broker.placeOrder({
        symbol: "AAPL",
        market: "us_stock",
        side: "buy",
        orderType: "limit",
        price: 180,
        quantity: 10,
      });

      expect(mockCall).toHaveBeenCalledWith(
        "place_order",
        expect.objectContaining({ code: "US.AAPL" }),
      );
    });

    it("adds SH prefix for 6xxxxx codes", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { order_id: "F3" };
        return {};
      });

      const broker = new FutuBroker(config);
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
        expect.objectContaining({ code: "SH.600519" }),
      );
    });

    it("adds SZ prefix for 0xxxxx codes", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { order_id: "F4" };
        return {};
      });

      const broker = new FutuBroker(config);
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
        expect.objectContaining({ code: "SZ.000001" }),
      );
    });

    it("adds SZ prefix for 3xxxxx ChiNext codes", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { order_id: "F5" };
        return {};
      });

      const broker = new FutuBroker(config);
      await broker.connect();
      await broker.placeOrder({
        symbol: "300750",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        price: 200,
        quantity: 100,
      });

      expect(mockCall).toHaveBeenCalledWith(
        "place_order",
        expect.objectContaining({ code: "SZ.300750" }),
      );
    });

    it("passes already-prefixed symbols through", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { order_id: "F6" };
        return {};
      });

      const broker = new FutuBroker(config);
      await broker.connect();
      await broker.placeOrder({
        symbol: "HK.00700",
        market: "hk_stock",
        side: "buy",
        orderType: "limit",
        price: 350,
        quantity: 100,
      });

      expect(mockCall).toHaveBeenCalledWith(
        "place_order",
        expect.objectContaining({ code: "HK.00700" }),
      );
    });
  });

  // ─────────────────────────────────────────────────────────────────
  // Futu order status mapping
  // ─────────────────────────────────────────────────────────────────
  describe("order status mapping", () => {
    const statusTests: Array<[string, OrderStatus]> = [
      ["NONE", "pending"],
      ["UNSUBMITTED", "pending"],
      ["WAITING_SUBMIT", "pending"],
      ["SUBMITTING", "submitted"],
      ["SUBMITTED", "submitted"],
      ["FILLED_PART", "partial"],
      ["FILLED_ALL", "filled"],
      ["CANCELLED_PART", "cancelled"],
      ["CANCELLED_ALL", "cancelled"],
      ["FAILED", "rejected"],
      ["DISABLED", "rejected"],
      ["DELETED", "rejected"],
      ["TIMEOUT", "expired"],
    ];

    for (const [futuStatus, expectedStatus] of statusTests) {
      it(`maps ${futuStatus} to ${expectedStatus}`, async () => {
        mockCall.mockImplementation((method: string) => {
          if (method === "query_orders") {
            return [
              {
                order_id: "FUTU-ST-1",
                code: "HK.00700",
                trd_side: "BUY",
                order_type: "NORMAL",
                price: 350,
                qty: 100,
                dealt_qty: futuStatus === "FILLED_ALL" ? 100 : 0,
                dealt_avg_price: futuStatus === "FILLED_ALL" ? 350 : 0,
                order_status: futuStatus,
              },
            ];
          }
          return {};
        });

        const broker = new FutuBroker(config);
        await broker.connect();
        const orders = await broker.queryOrders();
        expect(orders[0].status).toBe(expectedStatus);
      });
    }
  });

  // ─────────────────────────────────────────────────────────────────
  // Default config values
  // ─────────────────────────────────────────────────────────────────
  describe("default config", () => {
    it("uses defaults when futu config is empty", () => {
      const minimalConfig: QuantTradingConfig = {
        dataDir: "/tmp/oc-futu-minimal",
        tradingAccount: {
          mode: "live",
          broker: "futu",
        },
      } as QuantTradingConfig;

      // Should not throw
      const broker = new FutuBroker(minimalConfig);
      expect(broker.brokerType).toBe("futu");
    });
  });

  // ─────────────────────────────────────────────────────────────────
  // Integration with broker factory
  // ─────────────────────────────────────────────────────────────────
  describe("broker factory integration", () => {
    it("createBrokerAdapter returns FutuBroker for futu config", async () => {
      const { createBrokerAdapter } = await import("./index.js");
      const broker = createBrokerAdapter({
        dataDir: "/tmp/oc-futu-factory",
        tradingAccount: {
          mode: "live",
          broker: "futu",
          futu: { tradeEnv: "SIMULATE" },
        },
      } as QuantTradingConfig);
      expect(broker).toBeInstanceOf(FutuBroker);
      expect(broker.brokerType).toBe("futu");
    });
  });
});
