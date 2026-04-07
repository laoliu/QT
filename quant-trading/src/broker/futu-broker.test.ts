/**
 * Unit tests for FutuBroker — 富途 OpenD broker adapter.
 *
 * Mocks PersistentPythonProcess to verify order flow, position queries,
 * event handling, symbol conversion and account queries without connecting
 * to real Futu OpenD.
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
  isMarketOpen: vi.fn(() => true),
}));

vi.mock("../data/market-data-provider.js", () => ({
  detectMarketFromSymbol: vi.fn((code: string) => {
    if (/^[A-Z]+$/.test(code)) return "us_stock";
    const n = parseInt(code, 10);
    if (!isNaN(n) && n >= 0 && n < 10000) return "hk_stock";
    return "cn_stock";
  }),
}));

import { FutuBroker } from "./futu-broker.js";

// ── Config ─────────────────────────────────────────────────────
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
      pwdUnlock: "test123",
    },
    riskLimits: {},
  },
} as QuantTradingConfig;

const configNoFutu: QuantTradingConfig = {
  dataDir: "/tmp/oc-futu-test",
  pythonPath: "python3",
  tradingAccount: {
    mode: "live",
    broker: "futu",
    riskLimits: {},
  },
} as QuantTradingConfig;

beforeEach(() => {
  vi.clearAllMocks();
  mockCall.mockResolvedValue({});
  mockStart.mockResolvedValue(undefined);
  eventHandler = null;
});

// ── Helpers ─────────────────────────────────────────────────────

function makeBroker(cfg = config) {
  return new FutuBroker(cfg);
}

async function connectBroker(broker?: FutuBroker) {
  const b = broker ?? makeBroker();
  mockCall.mockResolvedValueOnce({
    status: "connected",
    accounts: [
      { acc_id: 12345, acc_type: "CASH", trd_env: "SIMULATE", card_num: "XX-1234", sim_acc_type: "STOCK" },
    ],
  });
  await b.connect();
  return b;
}

// ── Tests ───────────────────────────────────────────────────────

describe("FutuBroker", () => {
  // ── Constructor ──────────────────────────────────────────────

  describe("constructor", () => {
    it("creates instance with futu config", () => {
      const broker = makeBroker();
      expect(broker.brokerType).toBe("futu");
      expect(broker.connected).toBe(false);
    });

    it("uses default futu config when futu section is missing", () => {
      const broker = makeBroker(configNoFutu);
      expect(broker.brokerType).toBe("futu");
      expect(broker.connected).toBe(false);
    });
  });

  // ── Connect / Disconnect ─────────────────────────────────────

  describe("connect/disconnect", () => {
    it("connects via python process and caches accounts", async () => {
      const broker = makeBroker();
      expect(broker.connected).toBe(false);

      mockCall.mockResolvedValueOnce({
        status: "connected",
        accounts: [
          { acc_id: 111, acc_type: "CASH", trd_env: "REAL", card_num: "", sim_acc_type: "" },
        ],
      });
      await broker.connect();

      expect(broker.connected).toBe(true);
      expect(mockStart).toHaveBeenCalledTimes(1);
      expect(mockCall).toHaveBeenCalledWith("connect");
    });

    it("is idempotent on double connect", async () => {
      const broker = await connectBroker();
      await broker.connect();
      expect(mockStart).toHaveBeenCalledTimes(1);
    });

    it("handles connect result with empty accounts", async () => {
      const broker = makeBroker();
      mockCall.mockResolvedValueOnce({ status: "connected" });
      await broker.connect();
      expect(broker.connected).toBe(true);
      const accounts = await broker.getAccountList();
      // Should attempt to fetch from Python since empty
      expect(mockCall).toHaveBeenCalledWith("get_account_list");
    });

    it("disconnects cleanly", async () => {
      const broker = await connectBroker();
      await broker.disconnect();
      expect(broker.connected).toBe(false);
      expect(mockStop).toHaveBeenCalledTimes(1);
      expect(mockCall).toHaveBeenCalledWith("disconnect");
    });

    it("disconnect is no-op when not connected", async () => {
      const broker = makeBroker();
      await broker.disconnect();
      expect(mockCall).not.toHaveBeenCalledWith("disconnect");
      expect(mockStop).not.toHaveBeenCalled();
    });

    it("disconnect calls stop even if python disconnect throws", async () => {
      const broker = await connectBroker();
      mockCall.mockRejectedValueOnce(new Error("Python down"));
      await broker.disconnect();
      expect(broker.connected).toBe(false);
      expect(mockStop).toHaveBeenCalledTimes(1);
    });
  });

  // ── Account queries ──────────────────────────────────────────

  describe("getAccountList", () => {
    it("returns cached accounts from connect", async () => {
      const broker = await connectBroker();
      const accounts = await broker.getAccountList();
      expect(accounts).toHaveLength(1);
      expect(accounts[0].acc_id).toBe(12345);
    });

    it("fetches account list if cache is empty", async () => {
      const broker = makeBroker();
      mockCall.mockResolvedValueOnce({ status: "connected", accounts: [] });
      await broker.connect();

      const freshAccounts = [
        { acc_id: 999, acc_type: "MARGIN", trd_env: "REAL", card_num: "YY", sim_acc_type: "" },
      ];
      mockCall.mockResolvedValueOnce(freshAccounts);
      const result = await broker.getAccountList();
      expect(result).toEqual(freshAccounts);
    });
  });

  describe("queryAccount", () => {
    it("returns mapped BrokerAccount", async () => {
      const broker = await connectBroker();
      mockCall.mockResolvedValueOnce({
        account_id: "12345",
        cash: 100000,
        frozen_cash: 5000,
        total_asset: 250000,
        market_value: 150000,
        profit: 10000,
        profit_ratio: 0.04,
        available_funds: 95000,
      });

      const account = await broker.queryAccount();
      expect(account.accountId).toBe("12345");
      expect(account.brokerType).toBe("futu");
      expect(account.cash).toBe(100000);
      expect(account.frozenCash).toBe(5000);
      expect(account.totalAssets).toBe(250000);
      expect(account.marketValue).toBe(150000);
      expect(account.profit).toBe(10000);
      expect(account.profitPct).toBe(0.04);
      expect(account.lastSyncAt).toBeTruthy();
    });
  });

  // ── Order placement ──────────────────────────────────────────

  describe("placeOrder", () => {
    it("places a limit order", async () => {
      const broker = await connectBroker();
      mockCall.mockResolvedValueOnce({ broker_order_id: "FT-001" });

      const order = await broker.placeOrder({
        symbol: "00700",
        market: "hk_stock",
        side: "buy",
        orderType: "limit",
        price: 350,
        quantity: 100,
      });

      expect(order.brokerOrderId).toBe("FT-001");
      expect(order.symbol).toBe("00700");
      expect(order.side).toBe("buy");
      expect(order.orderType).toBe("limit");
      expect(order.price).toBe(350);
      expect(order.quantity).toBe(100);
      expect(order.filledQty).toBe(0);
      expect(order.status).toBe("submitted");
      expect(order.brokerType).toBe("futu");
      expect(order.orderId).toBeTruthy();
      expect(order.clientOrderId).toBeTruthy();

      // Verify Python was called with Futu-format symbol
      expect(mockCall).toHaveBeenCalledWith("place_order", expect.objectContaining({
        stock_code: "HK.00700",
        side: "buy",
        order_type: "limit",
        price: 350,
        volume: 100,
      }));
    });

    it("places a market order with price 0", async () => {
      const broker = await connectBroker();
      mockCall.mockResolvedValueOnce({ broker_order_id: "FT-002" });

      const order = await broker.placeOrder({
        symbol: "AAPL",
        market: "us_stock",
        side: "buy",
        orderType: "market",
        quantity: 10,
      });

      expect(mockCall).toHaveBeenCalledWith("place_order", expect.objectContaining({
        stock_code: "US.AAPL",
        price: 0,
      }));
      expect(order.orderType).toBe("market");
    });

    it("throws on limit order without price", async () => {
      const broker = await connectBroker();
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
      const broker = await connectBroker();
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

    it("throws on limit order with negative price", async () => {
      const broker = await connectBroker();
      await expect(
        broker.placeOrder({
          symbol: "00700",
          market: "hk_stock",
          side: "buy",
          orderType: "limit",
          price: -10,
          quantity: 100,
        }),
      ).rejects.toThrow("限价单必须指定正数 price");
    });

    it("uses custom clientOrderId if provided", async () => {
      const broker = await connectBroker();
      mockCall.mockResolvedValueOnce({ broker_order_id: "FT-003" });

      const order = await broker.placeOrder({
        symbol: "00700",
        market: "hk_stock",
        side: "sell",
        orderType: "limit",
        price: 360,
        quantity: 50,
        clientOrderId: "my-custom-id",
      });

      expect(order.clientOrderId).toBe("my-custom-id");
    });

    it("attaches strategyId and signalId to created order", async () => {
      const broker = await connectBroker();
      mockCall.mockResolvedValueOnce({ broker_order_id: "FT-004" });

      const order = await broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        price: 1800,
        quantity: 100,
        strategyId: "strategy-1",
        signalId: "signal-1",
      });

      expect(order.strategyId).toBe("strategy-1");
      expect(order.signalId).toBe("signal-1");
    });
  });

  // ── Cancel order ─────────────────────────────────────────────

  describe("cancelOrder", () => {
    it("cancels an existing order", async () => {
      const broker = await connectBroker();
      mockCall.mockResolvedValueOnce({ broker_order_id: "FT-100" });
      const order = await broker.placeOrder({
        symbol: "00700",
        market: "hk_stock",
        side: "buy",
        orderType: "limit",
        price: 350,
        quantity: 100,
      });

      mockCall.mockResolvedValueOnce({ result: "ok" });
      await broker.cancelOrder(order.orderId);

      expect(mockCall).toHaveBeenCalledWith("cancel_order", { order_id: "FT-100" });
    });

    it("throws for unknown orderId", async () => {
      const broker = await connectBroker();
      await expect(broker.cancelOrder("unknown-id")).rejects.toThrow("not found");
    });

    it("notifies status callbacks on cancel", async () => {
      const broker = await connectBroker();
      const statusCb = vi.fn();
      broker.subscribeOrderStatus(statusCb);

      mockCall.mockResolvedValueOnce({ broker_order_id: "FT-200" });
      const order = await broker.placeOrder({
        symbol: "AAPL",
        market: "us_stock",
        side: "buy",
        orderType: "limit",
        price: 180,
        quantity: 10,
      });

      mockCall.mockResolvedValueOnce({ result: "ok" });
      await broker.cancelOrder(order.orderId);

      expect(statusCb).toHaveBeenCalledWith(order.orderId, "cancelled", expect.anything());
    });
  });

  // ── Modify order ─────────────────────────────────────────────

  describe("modifyOrder", () => {
    it("cancels then resubmits with new params", async () => {
      const broker = await connectBroker();
      mockCall.mockResolvedValueOnce({ broker_order_id: "FT-300" });
      const original = await broker.placeOrder({
        symbol: "00700",
        market: "hk_stock",
        side: "buy",
        orderType: "limit",
        price: 350,
        quantity: 100,
      });

      // cancel call + place_order call
      mockCall.mockResolvedValueOnce({ result: "ok" });
      mockCall.mockResolvedValueOnce({ broker_order_id: "FT-301" });

      const modified = await broker.modifyOrder(original.orderId, {
        price: 355,
        quantity: 200,
      });

      expect(modified.price).toBe(355);
      expect(modified.quantity).toBe(200);
      expect(modified.brokerOrderId).toBe("FT-301");
      expect(modified.orderId).not.toBe(original.orderId);
    });

    it("throws for unknown orderId", async () => {
      const broker = await connectBroker();
      await expect(
        broker.modifyOrder("no-such-id", { price: 100 }),
      ).rejects.toThrow("not found");
    });
  });

  // ── Query order ──────────────────────────────────────────────

  describe("queryOrder", () => {
    it("returns cached order when broker query succeeds", async () => {
      const broker = await connectBroker();
      mockCall.mockResolvedValueOnce({ broker_order_id: "FT-400" });
      const placed = await broker.placeOrder({
        symbol: "00700",
        market: "hk_stock",
        side: "buy",
        orderType: "limit",
        price: 350,
        quantity: 100,
      });

      mockCall.mockResolvedValueOnce({
        order_id: "FT-400",
        stock_code: "HK.00700",
        order_type: "BUY",
        price: 350,
        order_volume: 100,
        traded_volume: 50,
        traded_price: 349,
        order_status: "FILLED_PART",
        order_status_mapped: "partial",
      });

      const result = await broker.queryOrder(placed.orderId);
      expect(result).not.toBeNull();
      expect(result!.filledQty).toBe(50);
      expect(result!.status).toBe("partial");
    });

    it("returns cached order on Python query failure", async () => {
      const broker = await connectBroker();
      mockCall.mockResolvedValueOnce({ broker_order_id: "FT-401" });
      const placed = await broker.placeOrder({
        symbol: "AAPL",
        market: "us_stock",
        side: "sell",
        orderType: "limit",
        price: 180,
        quantity: 10,
      });

      mockCall.mockRejectedValueOnce(new Error("timeout"));
      const result = await broker.queryOrder(placed.orderId);
      expect(result).not.toBeNull();
      expect(result!.orderId).toBe(placed.orderId);
    });

    it("returns null for unknown orderId", async () => {
      const broker = await connectBroker();
      const result = await broker.queryOrder("no-such-id");
      expect(result).toBeNull();
    });
  });

  // ── Query orders ─────────────────────────────────────────────

  describe("queryOrders", () => {
    it("returns mapped order list", async () => {
      const broker = await connectBroker();
      mockCall.mockResolvedValueOnce([
        {
          order_id: "FT-501",
          stock_code: "HK.00700",
          order_type: "BUY",
          price: 350,
          order_volume: 100,
          traded_volume: 100,
          traded_price: 349.5,
          order_status: "FILLED_ALL",
          order_status_mapped: "filled",
        },
        {
          order_id: "FT-502",
          stock_code: "US.AAPL",
          order_type: "SELL",
          price: 180,
          order_volume: 50,
          traded_volume: 0,
          traded_price: 0,
          order_status: "SUBMITTED",
          order_status_mapped: "submitted",
        },
      ]);

      const orders = await broker.queryOrders();
      expect(orders).toHaveLength(2);
      expect(orders[0].symbol).toBe("00700");
      expect(orders[0].status).toBe("filled");
      expect(orders[1].symbol).toBe("AAPL");
      expect(orders[1].side).toBe("sell");
    });

    it("passes symbol filter in Futu format", async () => {
      const broker = await connectBroker();
      mockCall.mockResolvedValueOnce([]);

      await broker.queryOrders({ symbol: "00700" });
      expect(mockCall).toHaveBeenCalledWith("query_orders", expect.objectContaining({
        stock_code: "HK.00700",
      }));
    });
  });

  // ── Query positions ──────────────────────────────────────────

  describe("queryPositions", () => {
    it("maps raw positions correctly", async () => {
      const broker = await connectBroker();
      mockCall.mockResolvedValueOnce([
        {
          stock_code: "HK.00700",
          plain_code: "00700",
          market_prefix: "HK",
          stock_name: "腾讯控股",
          volume: 1000,
          can_use_volume: 800,
          frozen_volume: 200,
          today_buy_volume: 500,
          today_sell_volume: 0,
          avg_price: 340,
          market_value: 350000,
          last_price: 350,
          unrealized_pnl: 10000,
          unrealized_pnl_pct: 0.029,
        },
      ]);

      const positions = await broker.queryPositions();
      expect(positions).toHaveLength(1);
      const pos = positions[0];
      expect(pos.symbol).toBe("00700");
      expect(pos.name).toBe("腾讯控股");
      expect(pos.market).toBe("hk_stock");
      expect(pos.quantity).toBe(1000);
      expect(pos.availableQty).toBe(800);
      expect(pos.frozenQty).toBe(200);
      expect(pos.avgCost).toBe(340);
      expect(pos.currentPrice).toBe(350);
      expect(pos.marketValue).toBe(350000);
      expect(pos.unrealizedPnl).toBe(10000);
      expect(pos.source).toBe("broker");
    });

    it("derives currentPrice from market_value/volume when last_price is missing", async () => {
      const broker = await connectBroker();
      mockCall.mockResolvedValueOnce([
        {
          stock_code: "SH.600519",
          plain_code: "600519",
          market_prefix: "SH",
          stock_name: "贵州茅台",
          volume: 100,
          can_use_volume: 100,
          frozen_volume: 0,
          today_buy_volume: 0,
          today_sell_volume: 0,
          avg_price: 1800,
          market_value: 185000,
          last_price: null,
          unrealized_pnl: 5000,
          unrealized_pnl_pct: null,
        },
      ]);

      const positions = await broker.queryPositions();
      expect(positions[0].currentPrice).toBe(1850); // 185000 / 100
      expect(positions[0].market).toBe("cn_stock");
    });

    it("handles position with zero volume and zero market_value", async () => {
      const broker = await connectBroker();
      mockCall.mockResolvedValueOnce([
        {
          stock_code: "US.TSLA",
          plain_code: "TSLA",
          market_prefix: "US",
          stock_name: "Tesla",
          volume: 0,
          can_use_volume: 0,
          frozen_volume: 0,
          today_buy_volume: 0,
          today_sell_volume: 0,
          avg_price: 250,
          market_value: 0,
          last_price: 0,
          unrealized_pnl: 0,
          unrealized_pnl_pct: 0,
        },
      ]);

      const positions = await broker.queryPositions();
      // last_price is 0 which is not > 0, market_value is 0 → currentPrice undefined
      expect(positions[0].currentPrice).toBeUndefined();
    });
  });

  // ── Quote subscription ───────────────────────────────────────

  describe("subscribeQuote", () => {
    it("subscribes to symbols and dispatches quote events", async () => {
      const broker = await connectBroker();
      mockCall.mockResolvedValueOnce({ subscribed: ["HK.00700"] });

      const quoteCb = vi.fn();
      const unsub = broker.subscribeQuote(["00700"], quoteCb);
      expect(typeof unsub).toBe("function");

      // Simulate a quote push event
      expect(eventHandler).not.toBeNull();
      eventHandler!("quote", {
        stock_code: "HK.00700",
        last_price: 355,
        open: 348,
        high: 358,
        low: 346,
        last_close: 350,
        volume: 1000000,
        amount: 350000000,
        bid_price: [354.8],
        ask_price: [355.2],
        bid_vol: [500],
        ask_vol: [300],
        time: 1700000000000,
      });

      expect(quoteCb).toHaveBeenCalledTimes(1);
      const snapshot = quoteCb.mock.calls[0][0];
      expect(snapshot.price).toBe(355);
      expect(snapshot.prevClose).toBe(350);
      expect(snapshot.change).toBe(5);
      expect(snapshot.changePct).toBeCloseTo(1.4286, 2);
    });

    it("unsubscribe removes callback", async () => {
      const broker = await connectBroker();
      mockCall.mockResolvedValueOnce({});

      const cb = vi.fn();
      const unsub = broker.subscribeQuote(["AAPL"], cb);
      unsub();

      eventHandler!("quote", {
        stock_code: "US.AAPL",
        last_price: 180,
        open: 178,
        high: 182,
        low: 177,
        last_close: 179,
        volume: 500000,
        amount: 90000000,
        bid_price: [179.9],
        ask_price: [180.1],
        bid_vol: [100],
        ask_vol: [200],
        time: 1700000000000,
      });

      expect(cb).not.toHaveBeenCalled();
    });
  });

  // ── Fill subscription ────────────────────────────────────────

  describe("subscribeFills", () => {
    it("dispatches fill events to subscribers", async () => {
      const broker = await connectBroker();
      mockCall.mockResolvedValueOnce({ broker_order_id: "FT-600" });
      const order = await broker.placeOrder({
        symbol: "00700",
        market: "hk_stock",
        side: "buy",
        orderType: "limit",
        price: 350,
        quantity: 100,
      });

      const fillCb = vi.fn();
      broker.subscribeFills(fillCb);

      eventHandler!("fill", {
        order_id: "FT-600",
        stock_code: "HK.00700",
        traded_price: 349.5,
        traded_volume: 100,
        traded_amount: 34950,
        commission: 15.5,
        time: "2024-01-15 10:30:00",
      });

      expect(fillCb).toHaveBeenCalledTimes(1);
      const fill = fillCb.mock.calls[0][0];
      expect(fill.orderId).toBe(order.orderId);
      expect(fill.symbol).toBe("00700");
      expect(fill.price).toBe(349.5);
      expect(fill.quantity).toBe(100);
      expect(fill.commission).toBe(15.5);
      expect(fill.side).toBe("buy");
    });

    it("unsubscribe removes fill callback", async () => {
      const broker = await connectBroker();
      const cb = vi.fn();
      const unsub = broker.subscribeFills(cb);
      unsub();

      eventHandler!("fill", {
        order_id: "unknown",
        stock_code: "HK.00700",
        traded_price: 349,
        traded_volume: 50,
        traded_amount: 17450,
        commission: 10,
        time: "2024-01-15 10:30:00",
      });

      expect(cb).not.toHaveBeenCalled();
    });

    it("handles fill for untracked order gracefully", async () => {
      const broker = await connectBroker();
      const fillCb = vi.fn();
      broker.subscribeFills(fillCb);

      // Fill for an order not placed through this broker instance
      eventHandler!("fill", {
        order_id: "EXTERNAL-ORDER",
        stock_code: "US.AAPL",
        traded_price: 180,
        traded_volume: 10,
        traded_amount: 1800,
        commission: 1,
        time: "2024-01-15 15:00:00",
      });

      expect(fillCb).toHaveBeenCalledTimes(1);
      const fill = fillCb.mock.calls[0][0];
      expect(fill.orderId).toBe("EXTERNAL-ORDER");
      expect(fill.side).toBe("buy"); // default when no cached order
    });
  });

  // ── Order status subscription ────────────────────────────────

  describe("subscribeOrderStatus", () => {
    it("dispatches order status events and updates cache", async () => {
      const broker = await connectBroker();
      mockCall.mockResolvedValueOnce({ broker_order_id: "FT-700" });
      const order = await broker.placeOrder({
        symbol: "00700",
        market: "hk_stock",
        side: "buy",
        orderType: "limit",
        price: 350,
        quantity: 100,
      });

      const statusCb = vi.fn();
      broker.subscribeOrderStatus(statusCb);

      eventHandler!("order_status", {
        order_id: "FT-700",
        stock_code: "HK.00700",
        order_type: "BUY",
        price: 350,
        order_volume: 100,
        traded_volume: 100,
        traded_price: 349.5,
        order_status: "FILLED_ALL",
        order_status_mapped: "filled",
      });

      expect(statusCb).toHaveBeenCalledWith(
        order.orderId,
        "filled",
        expect.objectContaining({ status: "filled", filledQty: 100 }),
      );

      // Verify cache was updated
      const cached = await broker.queryOrder(order.orderId);
      expect(cached!.status).toBe("filled");
    });

    it("unsubscribe removes status callback", async () => {
      const broker = await connectBroker();
      const cb = vi.fn();
      const unsub = broker.subscribeOrderStatus(cb);
      unsub();

      eventHandler!("order_status", {
        order_id: "unknown",
        stock_code: "HK.00700",
        order_type: "BUY",
        price: 350,
        order_volume: 100,
        traded_volume: 0,
        traded_price: 0,
        order_status: "SUBMITTED",
        order_status_mapped: "submitted",
      });

      expect(cb).not.toHaveBeenCalled();
    });
  });

  // ── Event handling (reconnection events) ─────────────────────

  describe("handleEvent - connection events", () => {
    it("logs reconnection events without crashing", async () => {
      const broker = await connectBroker();
      const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

      eventHandler!("reconnecting", { host: "127.0.0.1", port: 11111 });
      eventHandler!("reconnected", { host: "127.0.0.1", port: 11111 });
      eventHandler!("connection_error", { message: "timeout", attempt: 1 });

      expect(warnSpy).toHaveBeenCalledTimes(3);
      warnSpy.mockRestore();
    });
  });

  // ── Ping ─────────────────────────────────────────────────────

  describe("ping", () => {
    it("returns true when healthy", async () => {
      const broker = await connectBroker();
      mockCall.mockResolvedValueOnce({ ok: true });
      expect(await broker.ping()).toBe(true);
    });

    it("returns false on error", async () => {
      const broker = await connectBroker();
      mockCall.mockRejectedValueOnce(new Error("down"));
      expect(await broker.ping()).toBe(false);
    });
  });

  // ── isMarketOpen ─────────────────────────────────────────────

  describe("isMarketOpen", () => {
    it("delegates to isMarketOpen helper", async () => {
      const broker = makeBroker();
      const result = await broker.isMarketOpen("hk_stock");
      expect(result).toBe(true);
    });
  });

  // ── Symbol conversion ────────────────────────────────────────

  describe("symbol conversion (via placeOrder calls)", () => {
    it("converts HK stock code to Futu format", async () => {
      const broker = await connectBroker();
      mockCall.mockResolvedValueOnce({ broker_order_id: "X" });
      await broker.placeOrder({
        symbol: "00700",
        market: "hk_stock",
        side: "buy",
        orderType: "market",
        quantity: 100,
      });
      expect(mockCall).toHaveBeenCalledWith("place_order", expect.objectContaining({
        stock_code: "HK.00700",
      }));
    });

    it("converts US stock code to Futu format", async () => {
      const broker = await connectBroker();
      mockCall.mockResolvedValueOnce({ broker_order_id: "X" });
      await broker.placeOrder({
        symbol: "AAPL",
        market: "us_stock",
        side: "buy",
        orderType: "market",
        quantity: 10,
      });
      expect(mockCall).toHaveBeenCalledWith("place_order", expect.objectContaining({
        stock_code: "US.AAPL",
      }));
    });

    it("converts CN stock code to SH/SZ format", async () => {
      const broker = await connectBroker();
      mockCall.mockResolvedValueOnce({ broker_order_id: "X" });
      await broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "market",
        quantity: 100,
      });
      expect(mockCall).toHaveBeenCalledWith("place_order", expect.objectContaining({
        stock_code: "SH.600519",
      }));
    });

    it("converts SZ code correctly", async () => {
      const broker = await connectBroker();
      mockCall.mockResolvedValueOnce({ broker_order_id: "X" });
      await broker.placeOrder({
        symbol: "000001",
        market: "cn_stock",
        side: "buy",
        orderType: "market",
        quantity: 100,
      });
      expect(mockCall).toHaveBeenCalledWith("place_order", expect.objectContaining({
        stock_code: "SZ.000001",
      }));
    });

    it("converts BJ code correctly for 8xx codes", async () => {
      const broker = await connectBroker();
      mockCall.mockResolvedValueOnce({ broker_order_id: "X" });
      await broker.placeOrder({
        symbol: "830799",
        market: "cn_stock",
        side: "buy",
        orderType: "market",
        quantity: 100,
      });
      expect(mockCall).toHaveBeenCalledWith("place_order", expect.objectContaining({
        stock_code: "BJ.830799",
      }));
    });

    it("passes through already-Futu-format symbol unchanged", async () => {
      const broker = await connectBroker();
      mockCall.mockResolvedValueOnce({ broker_order_id: "X" });
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

    it("strips QMT-style suffix before converting", async () => {
      const broker = await connectBroker();
      mockCall.mockResolvedValueOnce({ broker_order_id: "X" });
      await broker.placeOrder({
        symbol: "600519.SH",
        market: "cn_stock",
        side: "buy",
        orderType: "market",
        quantity: 100,
      });
      expect(mockCall).toHaveBeenCalledWith("place_order", expect.objectContaining({
        stock_code: "SH.600519",
      }));
    });
  });

  // ── Order side mapping (via queryOrders) ─────────────────────

  describe("order side mapping", () => {
    it("maps SELL to sell", async () => {
      const broker = await connectBroker();
      mockCall.mockResolvedValueOnce([{
        order_id: "1", stock_code: "HK.00700", order_type: "SELL",
        price: 350, order_volume: 100, traded_volume: 0, traded_price: 0,
        order_status: "SUBMITTED", order_status_mapped: "submitted",
      }]);
      const [o] = await broker.queryOrders();
      expect(o.side).toBe("sell");
    });

    it("maps SELL_SHORT to short", async () => {
      const broker = await connectBroker();
      mockCall.mockResolvedValueOnce([{
        order_id: "2", stock_code: "US.AAPL", order_type: "SELL_SHORT",
        price: 180, order_volume: 10, traded_volume: 0, traded_price: 0,
        order_status: "SUBMITTED", order_status_mapped: "submitted",
      }]);
      const [o] = await broker.queryOrders();
      expect(o.side).toBe("short");
    });

    it("maps BUY_BACK to cover", async () => {
      const broker = await connectBroker();
      mockCall.mockResolvedValueOnce([{
        order_id: "3", stock_code: "US.AAPL", order_type: "BUY_BACK",
        price: 170, order_volume: 10, traded_volume: 0, traded_price: 0,
        order_status: "SUBMITTED", order_status_mapped: "submitted",
      }]);
      const [o] = await broker.queryOrders();
      expect(o.side).toBe("cover");
    });
  });

  // ── Order cache pruning ──────────────────────────────────────

  describe("order cache pruning", () => {
    it("does not crash when placing many orders", async () => {
      const broker = await connectBroker();
      // Place many orders to exercise pruneOrderCache
      for (let i = 0; i < 10; i++) {
        mockCall.mockResolvedValueOnce({ broker_order_id: `FT-PRUNE-${i}` });
        await broker.placeOrder({
          symbol: "00700",
          market: "hk_stock",
          side: "buy",
          orderType: "limit",
          price: 350,
          quantity: 100,
        });
      }
      // All orders placed without error
      expect(true).toBe(true);
    });
  });
});
