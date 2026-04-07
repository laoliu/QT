/**
 * Unit tests for FutuBroker — the Futu OpenD broker adapter.
 *
 * Mocks PersistentPythonProcess to verify order flow, position queries,
 * event handling, symbol normalisation, and status mapping without
 * connecting to real Futu OpenD.
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
  isHkMarketOpen: vi.fn(() => true),
  isUsMarketOpen: vi.fn(() => false),
  isCnMarketOpen: vi.fn(() => true),
}));

vi.mock("../data/market-data-provider.js", () => ({
  detectMarketFromSymbol: vi.fn(() => "hk_stock"),
}));

import { FutuBroker } from "./futu-broker.js";

const config: QuantTradingConfig = {
  dataDir: "/tmp/oc-futu-test",
  pythonPath: "python3",
  defaultMarket: "hk_stock",
  tradingAccount: {
    mode: "live",
    broker: "futu",
    futu: {
      openDHost: "127.0.0.1",
      openDPort: 11111,
      tradeEnv: "SIMULATE",
      pwdUnlock: "",
      accountId: "test-futu-account",
      securityFirm: "FUTUSECURITIES",
      trdMarket: "hk_stock",
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

// ── Connect / Disconnect ────────────────────────────────────────

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

    it("disconnect is no-op when not connected", async () => {
      const broker = new FutuBroker(config);
      await broker.disconnect();
      expect(mockCall).not.toHaveBeenCalledWith("disconnect");
      expect(mockStop).not.toHaveBeenCalled();
    });

    it("disconnect calls stop even when disconnect call fails", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();
      mockCall.mockRejectedValueOnce(new Error("network error"));
      await broker.disconnect();
      expect(broker.connected).toBe(false);
      expect(mockStop).toHaveBeenCalled();
    });
  });

  // ── brokerType ──────────────────────────────────────────────

  describe("brokerType", () => {
    it('returns "futu"', () => {
      const broker = new FutuBroker(config);
      expect(broker.brokerType).toBe("futu");
    });
  });

  // ── placeOrder ──────────────────────────────────────────────

  describe("placeOrder", () => {
    it("places a limit buy order", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();
      mockCall.mockResolvedValueOnce({ broker_order_id: "FUTU-001" });

      const order = await broker.placeOrder({
        symbol: "00700",
        market: "hk_stock",
        side: "buy",
        orderType: "limit",
        quantity: 100,
        price: 350.0,
        strategyId: "test-strategy",
      });

      expect(order.symbol).toBe("00700");
      expect(order.market).toBe("hk_stock");
      expect(order.side).toBe("buy");
      expect(order.orderType).toBe("limit");
      expect(order.quantity).toBe(100);
      expect(order.price).toBe(350.0);
      expect(order.filledQty).toBe(0);
      expect(order.status).toBe("submitted");
      expect(order.brokerOrderId).toBe("FUTU-001");
      expect(order.brokerType).toBe("futu");
      expect(order.orderId).toBeDefined();
      expect(order.createdAt).toBeDefined();

      // Verify the python call received the Futu symbol format
      expect(mockCall).toHaveBeenCalledWith("place_order", expect.objectContaining({
        stock_code: "HK.00700",
        side: "buy",
        order_type: "limit",
        price: 350.0,
        volume: 100,
        strategy_name: "test-strategy",
      }));
    });

    it("places a market sell order with no price", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();
      mockCall.mockResolvedValueOnce({ broker_order_id: "FUTU-002" });

      const order = await broker.placeOrder({
        symbol: "AAPL",
        market: "us_stock",
        side: "sell",
        orderType: "market",
        quantity: 50,
      });

      expect(order.side).toBe("sell");
      expect(order.orderType).toBe("market");
      expect(mockCall).toHaveBeenCalledWith("place_order", expect.objectContaining({
        stock_code: "US.AAPL",
        price: 0,
      }));
    });

    it("throws on limit order without price", async () => {
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

    it("throws on limit order with zero price", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      await expect(
        broker.placeOrder({
          symbol: "00700",
          market: "hk_stock",
          side: "buy",
          orderType: "limit",
          quantity: 100,
          price: 0,
        }),
      ).rejects.toThrow("限价单必须指定正数 price");
    });

    it("throws on limit order with negative price", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      await expect(
        broker.placeOrder({
          symbol: "00700",
          market: "hk_stock",
          side: "buy",
          orderType: "limit",
          quantity: 100,
          price: -10,
        }),
      ).rejects.toThrow("限价单必须指定正数 price");
    });

    it("uses provided clientOrderId", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();
      mockCall.mockResolvedValueOnce({ broker_order_id: "FUTU-003" });

      const order = await broker.placeOrder({
        symbol: "00700",
        market: "hk_stock",
        side: "buy",
        orderType: "market",
        quantity: 100,
        clientOrderId: "my-custom-id",
      });

      expect(order.clientOrderId).toBe("my-custom-id");
    });

    it("defaults strategyId to 'manual'", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();
      mockCall.mockResolvedValueOnce({ broker_order_id: "FUTU-004" });

      await broker.placeOrder({
        symbol: "00700",
        market: "hk_stock",
        side: "buy",
        orderType: "market",
        quantity: 100,
      });

      expect(mockCall).toHaveBeenCalledWith("place_order", expect.objectContaining({
        strategy_name: "manual",
      }));
    });
  });

  // ── cancelOrder ─────────────────────────────────────────────

  describe("cancelOrder", () => {
    it("cancels an existing order", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();
      mockCall.mockResolvedValueOnce({ broker_order_id: "FUTU-100" });

      const order = await broker.placeOrder({
        symbol: "00700",
        market: "hk_stock",
        side: "buy",
        orderType: "market",
        quantity: 100,
      });

      mockCall.mockResolvedValueOnce({});
      await broker.cancelOrder(order.orderId);

      expect(mockCall).toHaveBeenCalledWith("cancel_order", { order_id: "FUTU-100" });
    });

    it("throws when order not found", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      await expect(broker.cancelOrder("nonexistent")).rejects.toThrow("not found");
    });

    it("fires order status callback on cancel", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();
      mockCall.mockResolvedValueOnce({ broker_order_id: "FUTU-101" });

      const order = await broker.placeOrder({
        symbol: "00700",
        market: "hk_stock",
        side: "buy",
        orderType: "market",
        quantity: 100,
      });

      const statusCb = vi.fn();
      broker.subscribeOrderStatus(statusCb);

      mockCall.mockResolvedValueOnce({});
      await broker.cancelOrder(order.orderId);

      expect(statusCb).toHaveBeenCalledWith(order.orderId, "cancelled", expect.objectContaining({
        status: "cancelled",
      }));
    });
  });

  // ── modifyOrder ─────────────────────────────────────────────

  describe("modifyOrder", () => {
    it("cancels and resubmits with new price", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();
      mockCall.mockResolvedValueOnce({ broker_order_id: "FUTU-200" });

      const original = await broker.placeOrder({
        symbol: "00700",
        market: "hk_stock",
        side: "buy",
        orderType: "limit",
        quantity: 100,
        price: 350,
      });

      // Cancel call, then new place_order call
      mockCall.mockResolvedValueOnce({});
      mockCall.mockResolvedValueOnce({ broker_order_id: "FUTU-201" });

      const modified = await broker.modifyOrder(original.orderId, { price: 360 });

      expect(modified.price).toBe(360);
      expect(modified.quantity).toBe(100);
      expect(modified.orderId).not.toBe(original.orderId);
    });

    it("throws when original order not found", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      await expect(broker.modifyOrder("nonexistent", { price: 100 })).rejects.toThrow("not found");
    });
  });

  // ── queryOrder ──────────────────────────────────────────────

  describe("queryOrder", () => {
    it("returns cached order updated with remote data", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();
      mockCall.mockResolvedValueOnce({ broker_order_id: "FUTU-300" });

      const order = await broker.placeOrder({
        symbol: "00700",
        market: "hk_stock",
        side: "buy",
        orderType: "limit",
        quantity: 100,
        price: 350,
      });

      mockCall.mockResolvedValueOnce({
        order_id: "FUTU-300",
        stock_code: "HK.00700",
        order_type: "BUY",
        price: 350,
        order_volume: 100,
        traded_volume: 50,
        traded_price: 349.5,
        order_status: "FILLED_PART",
        strategy_name: "",
      });

      const queried = await broker.queryOrder(order.orderId);
      expect(queried).not.toBeNull();
      expect(queried!.filledQty).toBe(50);
      expect(queried!.status).toBe("partial");
    });

    it("returns null for unknown order", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      const result = await broker.queryOrder("unknown-id");
      expect(result).toBeNull();
    });

    it("falls back to cached data on remote error", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();
      mockCall.mockResolvedValueOnce({ broker_order_id: "FUTU-301" });

      const order = await broker.placeOrder({
        symbol: "00700",
        market: "hk_stock",
        side: "buy",
        orderType: "market",
        quantity: 100,
      });

      mockCall.mockRejectedValueOnce(new Error("timeout"));
      const queried = await broker.queryOrder(order.orderId);
      expect(queried).not.toBeNull();
      expect(queried!.orderId).toBe(order.orderId);
    });
  });

  // ── queryOrders ─────────────────────────────────────────────

  describe("queryOrders", () => {
    it("maps raw orders from python", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();
      mockCall.mockResolvedValueOnce([
        {
          order_id: "FUTU-400",
          stock_code: "HK.00700",
          order_type: "BUY",
          price: 350,
          order_volume: 100,
          traded_volume: 100,
          traded_price: 349.8,
          order_status: "FILLED_ALL",
          strategy_name: "momentum",
        },
      ]);

      const orders = await broker.queryOrders();
      expect(orders).toHaveLength(1);
      expect(orders[0].status).toBe("filled");
      expect(orders[0].symbol).toBe("00700");
      expect(orders[0].side).toBe("buy");
    });
  });

  // ── queryPositions ──────────────────────────────────────────

  describe("queryPositions", () => {
    it("maps raw positions from python", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();
      mockCall.mockResolvedValueOnce([
        {
          stock_code: "HK.00700",
          stock_name: "腾讯控股",
          volume: 200,
          can_use_volume: 200,
          frozen_volume: 0,
          today_buy_volume: 100,
          avg_price: 340.0,
          market_value: 70000.0,
          last_price: 350.0,
          unrealized_pnl: 2000.0,
          unrealized_pnl_pct: 0.0294,
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
      expect(positions[0].currentPrice).toBe(350.0);
      expect(positions[0].unrealizedPnl).toBe(2000.0);
      expect(positions[0].source).toBe("broker");
    });

    it("computes currentPrice from market_value when last_price is null", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();
      mockCall.mockResolvedValueOnce([
        {
          stock_code: "HK.00700",
          stock_name: "腾讯控股",
          volume: 200,
          can_use_volume: 200,
          frozen_volume: 0,
          today_buy_volume: 0,
          avg_price: 340.0,
          market_value: 70000.0,
          last_price: null,
          unrealized_pnl: null,
          unrealized_pnl_pct: null,
        },
      ]);

      const positions = await broker.queryPositions();
      expect(positions[0].currentPrice).toBe(350.0); // 70000 / 200
      // unrealizedPnl should be computed: (350 - 340) * 200 = 2000
      expect(positions[0].unrealizedPnl).toBe(2000.0);
    });

    it("handles zero volume position", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();
      mockCall.mockResolvedValueOnce([
        {
          stock_code: "HK.00700",
          stock_name: "腾讯控股",
          volume: 0,
          can_use_volume: 0,
          frozen_volume: 0,
          today_buy_volume: 0,
          avg_price: 0,
          market_value: 0,
          last_price: null,
          unrealized_pnl: null,
          unrealized_pnl_pct: null,
        },
      ]);

      const positions = await broker.queryPositions();
      expect(positions[0].currentPrice).toBeUndefined();
      expect(positions[0].unrealizedPnl).toBeUndefined();
    });
  });

  // ── queryAccount ────────────────────────────────────────────

  describe("queryAccount", () => {
    it("maps raw account data", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();
      mockCall.mockResolvedValueOnce({
        cash: 100000,
        frozen_cash: 5000,
        total_asset: 250000,
        market_value: 150000,
        profit: 20000,
        profit_ratio: 0.08,
      });

      const account = await broker.queryAccount();
      expect(account.brokerType).toBe("futu");
      expect(account.cash).toBe(100000);
      expect(account.frozenCash).toBe(5000);
      expect(account.totalAssets).toBe(250000);
      expect(account.marketValue).toBe(150000);
      expect(account.profit).toBe(20000);
      expect(account.profitPct).toBe(0.08);
      expect(account.lastSyncAt).toBeDefined();
    });
  });

  // ── subscribeQuote / unsubscribe ────────────────────────────

  describe("subscribeQuote", () => {
    it("subscribes and receives quote events", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();
      const cb = vi.fn();
      mockCall.mockResolvedValueOnce({});

      broker.subscribeQuote(["00700"], cb);

      // Simulate quote push event
      eventHandler?.("quote", {
        stock_code: "HK.00700",
        last_price: 355.0,
        open: 350.0,
        high: 358.0,
        low: 349.0,
        last_close: 352.0,
        volume: 1000000,
        amount: 355000000,
        bid_price: [354.8],
        ask_price: [355.2],
        bid_vol: [100],
        ask_vol: [200],
        time: 1700000000,
      });

      expect(cb).toHaveBeenCalledTimes(1);
      const snapshot = cb.mock.calls[0][0];
      expect(snapshot.price).toBe(355.0);
      expect(snapshot.change).toBeCloseTo(3.0); // 355 - 352
      expect(snapshot.changePct).toBeCloseTo((3.0 / 352.0) * 100);
      expect(snapshot.bid1).toBe(354.8);
      expect(snapshot.ask1).toBe(355.2);
    });

    it("unsubscribe removes callback", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();
      const cb = vi.fn();
      mockCall.mockResolvedValueOnce({});

      const unsub = broker.subscribeQuote(["00700"], cb);
      unsub();

      mockCall.mockResolvedValueOnce({});
      eventHandler?.("quote", {
        stock_code: "HK.00700",
        last_price: 355.0,
        open: 350.0,
        high: 358.0,
        low: 349.0,
        last_close: 352.0,
        volume: 1000000,
        amount: 355000000,
        bid_price: [354.8],
        ask_price: [355.2],
        bid_vol: [100],
        ask_vol: [200],
        time: 1700000000,
      });

      expect(cb).not.toHaveBeenCalled();
    });

    it("handles changePct when last_close is zero", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();
      const cb = vi.fn();
      mockCall.mockResolvedValueOnce({});

      broker.subscribeQuote(["00700"], cb);

      eventHandler?.("quote", {
        stock_code: "HK.00700",
        last_price: 10.0,
        open: 10.0,
        high: 10.0,
        low: 10.0,
        last_close: 0,
        volume: 100,
        amount: 1000,
        bid_price: [],
        ask_price: [],
        bid_vol: [],
        ask_vol: [],
        time: 1700000000,
      });

      expect(cb.mock.calls[0][0].changePct).toBe(0);
    });
  });

  // ── subscribeFills ──────────────────────────────────────────

  describe("subscribeFills", () => {
    it("receives fill events", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();
      mockCall.mockResolvedValueOnce({ broker_order_id: "FUTU-500" });

      const order = await broker.placeOrder({
        symbol: "00700",
        market: "hk_stock",
        side: "buy",
        orderType: "limit",
        quantity: 100,
        price: 350,
      });

      const fillCb = vi.fn();
      broker.subscribeFills(fillCb);

      eventHandler?.("fill", {
        order_id: "FUTU-500",
        stock_code: "HK.00700",
        traded_price: 349.8,
        traded_volume: 100,
        traded_amount: 34980,
        commission: 10.49,
        time: "2026-04-07 10:30:00",
      });

      expect(fillCb).toHaveBeenCalledTimes(1);
      const fill = fillCb.mock.calls[0][0];
      expect(fill.symbol).toBe("00700");
      expect(fill.side).toBe("buy");
      expect(fill.price).toBe(349.8);
      expect(fill.quantity).toBe(100);
      expect(fill.commission).toBe(10.49);
      expect(fill.orderId).toBe(order.orderId);
    });

    it("unsubscribe stops receiving fills", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      const fillCb = vi.fn();
      const unsub = broker.subscribeFills(fillCb);
      unsub();

      eventHandler?.("fill", {
        order_id: "FUTU-999",
        stock_code: "HK.00700",
        traded_price: 350,
        traded_volume: 100,
        traded_amount: 35000,
        commission: 10.5,
        time: "2026-04-07 10:30:00",
      });

      expect(fillCb).not.toHaveBeenCalled();
    });
  });

  // ── subscribeOrderStatus ────────────────────────────────────

  describe("subscribeOrderStatus", () => {
    it("receives order status events", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();
      mockCall.mockResolvedValueOnce({ broker_order_id: "FUTU-600" });

      const order = await broker.placeOrder({
        symbol: "00700",
        market: "hk_stock",
        side: "buy",
        orderType: "limit",
        quantity: 100,
        price: 350,
      });

      const statusCb = vi.fn();
      broker.subscribeOrderStatus(statusCb);

      eventHandler?.("order_status", {
        order_id: "FUTU-600",
        stock_code: "HK.00700",
        order_type: "BUY",
        price: 350,
        order_volume: 100,
        traded_volume: 100,
        traded_price: 349.8,
        order_status: "FILLED_ALL",
        strategy_name: "",
      });

      expect(statusCb).toHaveBeenCalledTimes(1);
      expect(statusCb).toHaveBeenCalledWith(
        order.orderId,
        "filled",
        expect.objectContaining({ status: "filled", filledQty: 100 }),
      );
    });

    it("unsubscribe stops receiving status events", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      const statusCb = vi.fn();
      const unsub = broker.subscribeOrderStatus(statusCb);
      unsub();

      eventHandler?.("order_status", {
        order_id: "FUTU-999",
        stock_code: "HK.00700",
        order_type: "BUY",
        price: 350,
        order_volume: 100,
        traded_volume: 100,
        traded_price: 349.8,
        order_status: "FILLED_ALL",
        strategy_name: "",
      });

      expect(statusCb).not.toHaveBeenCalled();
    });
  });

  // ── isMarketOpen ────────────────────────────────────────────

  describe("isMarketOpen", () => {
    it("delegates to HK market check by default", async () => {
      const broker = new FutuBroker(config);
      const result = await broker.isMarketOpen();
      expect(result).toBe(true);
    });

    it("delegates to US market check", async () => {
      const broker = new FutuBroker(config);
      const result = await broker.isMarketOpen("us_stock");
      expect(result).toBe(false);
    });

    it("delegates to CN market check", async () => {
      const broker = new FutuBroker(config);
      const result = await broker.isMarketOpen("cn_stock");
      expect(result).toBe(true);
    });
  });

  // ── ping ────────────────────────────────────────────────────

  describe("ping", () => {
    it("returns true on success", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();
      mockCall.mockResolvedValueOnce({ status: "ok" });

      const result = await broker.ping();
      expect(result).toBe(true);
    });

    it("returns false on failure", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();
      mockCall.mockRejectedValueOnce(new Error("timeout"));

      const result = await broker.ping();
      expect(result).toBe(false);
    });
  });

  // ── Order cache pruning ─────────────────────────────────────

  describe("order cache pruning", () => {
    it("prunes old terminal orders when cache exceeds max", async () => {
      const broker = new FutuBroker(config);
      await broker.connect();

      // Place many orders to exceed the cache limit (500)
      for (let i = 0; i < 505; i++) {
        mockCall.mockResolvedValueOnce({ broker_order_id: `FUTU-${i}` });
        await broker.placeOrder({
          symbol: "00700",
          market: "hk_stock",
          side: "buy",
          orderType: "market",
          quantity: 1,
        });
      }

      // Pruning only removes terminal + old orders, so cache should still work
      // but not crash
      const result = await broker.queryOrder("nonexistent");
      expect(result).toBeNull();
    });
  });

  // ── Default config ──────────────────────────────────────────

  describe("default config", () => {
    it("uses defaults when futu config is empty", () => {
      const minimalConfig = {
        dataDir: "/tmp/test",
        pythonPath: "python3",
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
});

// ── Symbol normalisation (exported helpers via module internals) ─

describe("FutuBroker symbol normalisation (via placeOrder)", () => {
  it("converts HK stock codes to Futu format", async () => {
    const broker = new FutuBroker(config);
    await broker.connect();
    mockCall.mockResolvedValueOnce({ broker_order_id: "T1" });

    await broker.placeOrder({
      symbol: "700",
      market: "hk_stock",
      side: "buy",
      orderType: "market",
      quantity: 100,
    });

    expect(mockCall).toHaveBeenCalledWith("place_order", expect.objectContaining({
      stock_code: "HK.00700",
    }));
  });

  it("converts US stock codes to Futu format", async () => {
    const broker = new FutuBroker(config);
    await broker.connect();
    mockCall.mockResolvedValueOnce({ broker_order_id: "T2" });

    await broker.placeOrder({
      symbol: "AAPL",
      market: "us_stock",
      side: "buy",
      orderType: "market",
      quantity: 100,
    });

    expect(mockCall).toHaveBeenCalledWith("place_order", expect.objectContaining({
      stock_code: "US.AAPL",
    }));
  });

  it("passes through already-formatted Futu symbols", async () => {
    const broker = new FutuBroker(config);
    await broker.connect();
    mockCall.mockResolvedValueOnce({ broker_order_id: "T3" });

    await broker.placeOrder({
      symbol: "HK.00700",
      market: "hk_stock",
      side: "buy",
      orderType: "market",
      quantity: 100,
    });

    expect(mockCall).toHaveBeenCalledWith("place_order", expect.objectContaining({
      stock_code: "HK.00700",
    }));
  });

  it("detects SH stock codes (A-share starting with 6)", async () => {
    const broker = new FutuBroker(config);
    await broker.connect();
    mockCall.mockResolvedValueOnce({ broker_order_id: "T4" });

    await broker.placeOrder({
      symbol: "600519",
      market: "cn_stock" as any,
      side: "buy",
      orderType: "market",
      quantity: 100,
    });

    // cn_stock doesn't match "hk_stock" or "us_stock" so falls through to A-share detection
    expect(mockCall).toHaveBeenCalledWith("place_order", expect.objectContaining({
      stock_code: "SH.600519",
    }));
  });

  it("detects SZ stock codes (A-share starting with 0)", async () => {
    const broker = new FutuBroker(config);
    await broker.connect();
    mockCall.mockResolvedValueOnce({ broker_order_id: "T5" });

    await broker.placeOrder({
      symbol: "000001",
      market: "cn_stock" as any,
      side: "buy",
      orderType: "market",
      quantity: 100,
    });

    expect(mockCall).toHaveBeenCalledWith("place_order", expect.objectContaining({
      stock_code: "SZ.000001",
    }));
  });
});

// ── Status mapping (via queryOrders) ─────────────────────────

describe("FutuBroker status mapping", () => {
  const testCases: Array<[string, OrderStatus]> = [
    ["FILLED_ALL", "filled"],
    ["全部成交", "filled"],
    ["FILLED_PART", "partial"],
    ["部分成交", "partial"],
    ["CANCELLED_ALL", "cancelled"],
    ["已撤", "cancelled"],
    ["SUBMITTED", "submitted"],
    ["已提交", "submitted"],
    ["FAILED", "rejected"],
    ["失败", "rejected"],
    ["DISABLED", "rejected"],
    ["禁止", "rejected"],
    ["DELETED", "cancelled"],
    ["已删除", "cancelled"],
    ["SUBMITTING", "submitted"],
    ["提交中", "submitted"],
    ["WAITING_SUBMIT", "pending"],
    ["等待", "pending"],
    ["UNSUBMITTED", "pending"],
    ["未提交", "pending"],
    ["SOME_UNKNOWN_STATUS", "submitted"], // default fallback
  ];

  it.each(testCases)('maps "%s" to "%s"', async (futuStatus, expected) => {
    const broker = new FutuBroker(config);
    await broker.connect();
    mockCall.mockResolvedValueOnce([
      {
        order_id: "TEST",
        stock_code: "HK.00700",
        order_type: "BUY",
        price: 350,
        order_volume: 100,
        traded_volume: 0,
        traded_price: 0,
        order_status: futuStatus,
        strategy_name: "",
      },
    ]);

    const orders = await broker.queryOrders();
    expect(orders[0].status).toBe(expected);
  });
});

// ── Side mapping (via queryOrders) ───────────────────────────

describe("FutuBroker side mapping", () => {
  const sideCases: Array<[string, string]> = [
    ["BUY", "buy"],
    ["买", "buy"],
    ["SELL", "sell"],
    ["卖", "sell"],
    ["SELL_SHORT", "short"],
    ["卖空", "short"],
    ["BUY_BACK", "cover"],
    ["买回", "cover"],
  ];

  it.each(sideCases)('maps "%s" to "%s"', async (futuSide, expected) => {
    const broker = new FutuBroker(config);
    await broker.connect();
    mockCall.mockResolvedValueOnce([
      {
        order_id: "TEST",
        stock_code: "HK.00700",
        order_type: futuSide,
        price: 350,
        order_volume: 100,
        traded_volume: 0,
        traded_price: 0,
        order_status: "SUBMITTED",
        strategy_name: "",
      },
    ]);

    const orders = await broker.queryOrders();
    expect(orders[0].side).toBe(expected);
  });
});

// ── Market detection from Futu symbol (via event handling) ───

describe("FutuBroker market detection from Futu symbol", () => {
  it("detects HK market from HK. prefix", async () => {
    const broker = new FutuBroker(config);
    await broker.connect();
    mockCall.mockResolvedValueOnce([
      {
        order_id: "T1",
        stock_code: "HK.00700",
        order_type: "BUY",
        price: 350,
        order_volume: 100,
        traded_volume: 0,
        traded_price: 0,
        order_status: "SUBMITTED",
        strategy_name: "",
      },
    ]);

    const orders = await broker.queryOrders();
    expect(orders[0].market).toBe("hk_stock");
  });

  it("detects US market from US. prefix", async () => {
    const broker = new FutuBroker(config);
    await broker.connect();
    mockCall.mockResolvedValueOnce([
      {
        order_id: "T2",
        stock_code: "US.AAPL",
        order_type: "BUY",
        price: 200,
        order_volume: 10,
        traded_volume: 0,
        traded_price: 0,
        order_status: "SUBMITTED",
        strategy_name: "",
      },
    ]);

    const orders = await broker.queryOrders();
    expect(orders[0].market).toBe("us_stock");
  });

  it("detects CN market from SH. prefix", async () => {
    const broker = new FutuBroker(config);
    await broker.connect();
    mockCall.mockResolvedValueOnce([
      {
        order_id: "T3",
        stock_code: "SH.600519",
        order_type: "BUY",
        price: 1800,
        order_volume: 100,
        traded_volume: 0,
        traded_price: 0,
        order_status: "SUBMITTED",
        strategy_name: "",
      },
    ]);

    const orders = await broker.queryOrders();
    expect(orders[0].market).toBe("cn_stock");
  });

  it("detects CN market from SZ. prefix", async () => {
    const broker = new FutuBroker(config);
    await broker.connect();
    mockCall.mockResolvedValueOnce([
      {
        order_id: "T4",
        stock_code: "SZ.000001",
        order_type: "BUY",
        price: 15,
        order_volume: 100,
        traded_volume: 0,
        traded_price: 0,
        order_status: "SUBMITTED",
        strategy_name: "",
      },
    ]);

    const orders = await broker.queryOrders();
    expect(orders[0].market).toBe("cn_stock");
  });
});
