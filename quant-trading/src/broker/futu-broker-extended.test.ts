/**
 * Extended unit tests for FutuBroker — covers edge cases, boundary conditions,
 * error handling, symbol normalisation, status mapping, order cache pruning,
 * position mapping, quote mapping, and multi-subscriber scenarios.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { QuantTradingConfig, OrderStatus, Fill, LivePosition } from "../types.js";

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

vi.mock("../data/market-data-provider.js", () => ({
  detectMarketFromSymbol: vi.fn((sym: string) => {
    if (/^[A-Za-z]+$/.test(sym)) return "us_stock";
    const n = parseInt(sym, 10);
    if (!isNaN(n) && n >= 500000) return "cn_stock";
    return "cn_stock";
  }),
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

// ── Helper ────────────────────────────────────────────────────
function createBroker(cfg = config) {
  return new FutuBroker(cfg);
}

async function connectBroker(cfg = config) {
  const broker = createBroker(cfg);
  await broker.connect();
  return broker;
}

describe("FutuBroker — Extended Tests", () => {
  // ─────────────────────────────────────────────────────────────
  // Symbol normalisation (toFutuSymbol / fromFutuSymbol / detectMarketFromFutuSymbol)
  // ─────────────────────────────────────────────────────────────
  describe("symbol normalisation via placeOrder", () => {
    beforeEach(() => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return Promise.resolve({ broker_order_id: "B-1" });
        return Promise.resolve({});
      });
    });

    it("converts HK stock code with market hint", async () => {
      const broker = await connectBroker();
      await broker.placeOrder({ symbol: "00700", market: "hk_stock", side: "buy", orderType: "market", quantity: 100 });
      expect(mockCall).toHaveBeenCalledWith("place_order", expect.objectContaining({ stock_code: "HK.00700" }));
    });

    it("converts US stock ticker with market hint", async () => {
      const broker = await connectBroker();
      await broker.placeOrder({ symbol: "AAPL", market: "us_stock", side: "buy", orderType: "market", quantity: 10 });
      expect(mockCall).toHaveBeenCalledWith("place_order", expect.objectContaining({ stock_code: "US.AAPL" }));
    });

    it("auto-detects SH prefix for codes >= 500000", async () => {
      const broker = await connectBroker();
      await broker.placeOrder({ symbol: "600519", market: "cn_stock", side: "buy", orderType: "market", quantity: 100 });
      expect(mockCall).toHaveBeenCalledWith("place_order", expect.objectContaining({ stock_code: "SH.600519" }));
    });

    it("auto-detects SZ prefix for codes < 500000", async () => {
      const broker = await connectBroker();
      await broker.placeOrder({ symbol: "000001", market: "cn_stock", side: "buy", orderType: "market", quantity: 100 });
      expect(mockCall).toHaveBeenCalledWith("place_order", expect.objectContaining({ stock_code: "SZ.000001" }));
    });

    it("passes through already-formatted Futu symbols (HK.00700)", async () => {
      const broker = await connectBroker();
      await broker.placeOrder({ symbol: "HK.00700", market: "hk_stock", side: "buy", orderType: "market", quantity: 100 });
      expect(mockCall).toHaveBeenCalledWith("place_order", expect.objectContaining({ stock_code: "HK.00700" }));
    });

    it("converts QMT-style .SH suffix", async () => {
      const broker = await connectBroker();
      // subscribeQuote calls toFutuSymbol; use it to test the conversion
      broker.subscribeQuote(["600519.SH"], () => {});
      expect(mockCall).toHaveBeenCalledWith(
        "subscribe_quote",
        expect.objectContaining({ stock_codes: ["SH.600519"] }),
      );
    });

    it("converts QMT-style .SZ suffix", async () => {
      const broker = await connectBroker();
      broker.subscribeQuote(["000001.SZ"], () => {});
      expect(mockCall).toHaveBeenCalledWith(
        "subscribe_quote",
        expect.objectContaining({ stock_codes: ["SZ.000001"] }),
      );
    });

    it("detects alphabetic-only symbol as US stock", async () => {
      const broker = await connectBroker();
      // No market hint, purely alphabetic
      await broker.placeOrder({ symbol: "TSLA", market: "us_stock", side: "buy", orderType: "market", quantity: 5 });
      expect(mockCall).toHaveBeenCalledWith("place_order", expect.objectContaining({ stock_code: "US.TSLA" }));
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Order status mapping (mapFutuStatus)
  // ─────────────────────────────────────────────────────────────
  describe("order status mapping via order_status event", () => {
    const statusTests: Array<[string, OrderStatus]> = [
      ["NONE", "pending"],
      ["UNSUBMITTED", "pending"],
      ["WAITING_SUBMIT", "pending"],
      ["SUBMITTING", "submitted"],
      ["SUBMIT_FAILED", "rejected"],
      ["SUBMITTED", "submitted"],
      ["FILLED_PART", "partial"],
      ["FILLED_ALL", "filled"],
      ["CANCELLING_PART", "submitted"],
      ["CANCELLING_ALL", "submitted"],
      ["CANCELLED_PART", "cancelled"],
      ["CANCELLED_ALL", "cancelled"],
      ["FAILED", "rejected"],
      ["DISABLED", "rejected"],
      ["DELETED", "cancelled"],
    ];

    for (const [futuStatus, expected] of statusTests) {
      it(`maps Futu "${futuStatus}" → "${expected}"`, async () => {
        mockCall.mockImplementation((method: string) => {
          if (method === "place_order") return { broker_order_id: `BO-${futuStatus}` };
          return {};
        });
        const broker = await connectBroker();
        await broker.placeOrder({ symbol: "AAPL", market: "us_stock", side: "buy", orderType: "limit", price: 180, quantity: 10 });

        const statuses: OrderStatus[] = [];
        broker.subscribeOrderStatus((_id, status) => statuses.push(status));

        eventHandler?.("order_status", {
          order_id: `BO-${futuStatus}`,
          stock_code: "US.AAPL",
          order_type: "BUY",
          price: 180,
          order_volume: 10,
          traded_volume: 0,
          traded_price: 0,
          order_status: futuStatus,
        });

        expect(statuses).toHaveLength(1);
        expect(statuses[0]).toBe(expected);
      });
    }

    it("maps unknown Futu status to 'submitted' as default", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { broker_order_id: "BO-UNK" };
        return {};
      });
      const broker = await connectBroker();
      await broker.placeOrder({ symbol: "AAPL", market: "us_stock", side: "buy", orderType: "limit", price: 180, quantity: 10 });

      const statuses: OrderStatus[] = [];
      broker.subscribeOrderStatus((_id, status) => statuses.push(status));

      eventHandler?.("order_status", {
        order_id: "BO-UNK",
        stock_code: "US.AAPL",
        order_type: "BUY",
        price: 180,
        order_volume: 10,
        traded_volume: 0,
        traded_price: 0,
        order_status: "SOME_UNKNOWN_STATUS",
      });

      expect(statuses[0]).toBe("submitted");
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Futu side mapping (mapFutuSide)
  // ─────────────────────────────────────────────────────────────
  describe("side mapping via order_status event", () => {
    it("maps SELL to sell", async () => {
      const broker = await connectBroker();
      const orders: unknown[] = [];
      broker.subscribeOrderStatus((_id, _status, order) => orders.push(order));

      eventHandler?.("order_status", {
        order_id: "EXT-1",
        stock_code: "HK.00700",
        order_type: "SELL",
        price: 355,
        order_volume: 100,
        traded_volume: 0,
        traded_price: 0,
        order_status: "SUBMITTED",
      });

      expect((orders[0] as { side: string }).side).toBe("sell");
    });

    it("maps SELL_SHORT to sell", async () => {
      const broker = await connectBroker();
      const orders: unknown[] = [];
      broker.subscribeOrderStatus((_id, _status, order) => orders.push(order));

      eventHandler?.("order_status", {
        order_id: "EXT-2",
        stock_code: "US.AAPL",
        order_type: "SELL_SHORT",
        price: 180,
        order_volume: 10,
        traded_volume: 0,
        traded_price: 0,
        order_status: "SUBMITTED",
      });

      expect((orders[0] as { side: string }).side).toBe("sell");
    });

    it("maps BUY (and any unknown) to buy", async () => {
      const broker = await connectBroker();
      const orders: unknown[] = [];
      broker.subscribeOrderStatus((_id, _status, order) => orders.push(order));

      eventHandler?.("order_status", {
        order_id: "EXT-3",
        stock_code: "US.AAPL",
        order_type: "RANDOM_SIDE",
        price: 180,
        order_volume: 10,
        traded_volume: 0,
        traded_price: 0,
        order_status: "SUBMITTED",
      });

      expect((orders[0] as { side: string }).side).toBe("buy");
    });
  });

  // ─────────────────────────────────────────────────────────────
  // placeOrder — edge cases & validation
  // ─────────────────────────────────────────────────────────────
  describe("placeOrder — edge cases", () => {
    it("rejects limit order with price = 0", async () => {
      const broker = await connectBroker();
      await expect(
        broker.placeOrder({ symbol: "00700", market: "hk_stock", side: "buy", orderType: "limit", price: 0, quantity: 100 }),
      ).rejects.toThrow("限价单必须指定正数 price");
    });

    it("rejects limit order with negative price", async () => {
      const broker = await connectBroker();
      await expect(
        broker.placeOrder({ symbol: "00700", market: "hk_stock", side: "buy", orderType: "limit", price: -10, quantity: 100 }),
      ).rejects.toThrow("限价单必须指定正数 price");
    });

    it("market order sends price=0 to Python bridge", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { broker_order_id: "MKT-1" };
        return {};
      });
      const broker = await connectBroker();
      await broker.placeOrder({ symbol: "00700", market: "hk_stock", side: "sell", orderType: "market", quantity: 100 });
      expect(mockCall).toHaveBeenCalledWith("place_order", expect.objectContaining({ price: 0 }));
    });

    it("uses provided clientOrderId", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { broker_order_id: "CID-1" };
        return {};
      });
      const broker = await connectBroker();
      const order = await broker.placeOrder({
        symbol: "00700", market: "hk_stock", side: "buy", orderType: "market", quantity: 100,
        clientOrderId: "my-custom-id",
      });
      expect(order.clientOrderId).toBe("my-custom-id");
      expect(mockCall).toHaveBeenCalledWith("place_order", expect.objectContaining({ remark: "my-custom-id" }));
    });

    it("generates clientOrderId when not provided", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { broker_order_id: "AUTO-1" };
        return {};
      });
      const broker = await connectBroker();
      const order = await broker.placeOrder({
        symbol: "00700", market: "hk_stock", side: "buy", orderType: "market", quantity: 100,
      });
      expect(order.clientOrderId).toBeDefined();
      expect(order.clientOrderId!.length).toBeGreaterThan(0);
    });

    it("propagates Python bridge errors", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") throw new Error("Connection lost");
        return {};
      });
      const broker = await connectBroker();
      await expect(
        broker.placeOrder({ symbol: "00700", market: "hk_stock", side: "buy", orderType: "market", quantity: 100 }),
      ).rejects.toThrow("Connection lost");
    });
  });

  // ─────────────────────────────────────────────────────────────
  // cancelOrder — edge cases
  // ─────────────────────────────────────────────────────────────
  describe("cancelOrder — edge cases", () => {
    it("notifies status callbacks on cancel", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { broker_order_id: "CANCEL-1" };
        return {};
      });
      const broker = await connectBroker();
      const order = await broker.placeOrder({
        symbol: "00700", market: "hk_stock", side: "buy", orderType: "limit", price: 350, quantity: 100,
      });

      const statuses: Array<{ id: string; status: string }> = [];
      broker.subscribeOrderStatus((id, status) => statuses.push({ id, status }));

      await broker.cancelOrder(order.orderId);

      expect(statuses).toHaveLength(1);
      expect(statuses[0]).toMatchObject({ id: order.orderId, status: "cancelled" });
    });
  });

  // ─────────────────────────────────────────────────────────────
  // queryOrder — cache & fallback
  // ─────────────────────────────────────────────────────────────
  describe("queryOrder", () => {
    it("returns null for non-existent order", async () => {
      const broker = await connectBroker();
      const result = await broker.queryOrder("nonexistent-id");
      expect(result).toBeNull();
    });

    it("returns cached order when Python bridge query fails", async () => {
      let callCount = 0;
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { broker_order_id: "QO-1" };
        if (method === "query_order") throw new Error("timeout");
        return {};
      });
      const broker = await connectBroker();
      const order = await broker.placeOrder({
        symbol: "00700", market: "hk_stock", side: "buy", orderType: "limit", price: 350, quantity: 100,
      });

      const result = await broker.queryOrder(order.orderId);
      expect(result).toBeDefined();
      expect(result!.orderId).toBe(order.orderId);
    });

    it("updates order from Python bridge response", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { broker_order_id: "QO-2" };
        if (method === "query_order") {
          return {
            order_id: "QO-2",
            stock_code: "HK.00700",
            order_type: "BUY",
            price: 350,
            order_volume: 100,
            traded_volume: 50,
            traded_price: 349.5,
            order_status: "FILLED_PART",
          };
        }
        return {};
      });
      const broker = await connectBroker();
      const order = await broker.placeOrder({
        symbol: "00700", market: "hk_stock", side: "buy", orderType: "limit", price: 350, quantity: 100,
      });

      const result = await broker.queryOrder(order.orderId);
      expect(result!.status).toBe("partial");
      expect(result!.filledQty).toBe(50);
    });
  });

  // ─────────────────────────────────────────────────────────────
  // queryOrders
  // ─────────────────────────────────────────────────────────────
  describe("queryOrders", () => {
    it("maps raw orders from Python bridge", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "query_orders") {
          return [
            {
              order_id: "QOS-1",
              stock_code: "HK.00700",
              order_type: "BUY",
              price: 350,
              order_volume: 100,
              traded_volume: 100,
              traded_price: 349.5,
              order_status: "FILLED_ALL",
            },
            {
              order_id: "QOS-2",
              stock_code: "US.AAPL",
              order_type: "SELL",
              price: 180,
              order_volume: 50,
              traded_volume: 0,
              traded_price: 0,
              order_status: "SUBMITTED",
            },
          ];
        }
        return {};
      });
      const broker = await connectBroker();
      const orders = await broker.queryOrders();
      expect(orders).toHaveLength(2);
      expect(orders[0].status).toBe("filled");
      expect(orders[0].market).toBe("hk_stock");
      expect(orders[1].status).toBe("submitted");
      expect(orders[1].market).toBe("us_stock");
      expect(orders[1].side).toBe("sell");
    });

    it("passes symbol filter to Python bridge", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "query_orders") return [];
        return {};
      });
      const broker = await connectBroker();
      await broker.queryOrders({ symbol: "600519" });
      expect(mockCall).toHaveBeenCalledWith("query_orders", expect.objectContaining({ stock_code: "SH.600519" }));
    });
  });

  // ─────────────────────────────────────────────────────────────
  // queryPositions — position mapping edge cases
  // ─────────────────────────────────────────────────────────────
  describe("queryPositions — mapping", () => {
    it("maps raw position with all fields", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "query_positions") {
          return [{
            stock_code: "HK.00700",
            stock_name: "腾讯控股",
            volume: 200,
            can_use_volume: 200,
            frozen_volume: 0,
            today_buy_volume: 100,
            avg_price: 350,
            market_value: 71000,
            last_price: 355,
            unrealized_pnl: 1000,
            unrealized_pnl_pct: 0.0143,
          }];
        }
        return {};
      });
      const broker = await connectBroker();
      const positions = await broker.queryPositions();
      expect(positions).toHaveLength(1);
      expect(positions[0]).toMatchObject({
        symbol: "00700",
        name: "腾讯控股",
        quantity: 200,
        availableQty: 200,
        frozenQty: 0,
        todayBuyQty: 100,
        avgCost: 350,
        currentPrice: 355,
        unrealizedPnl: 1000,
        unrealizedPnlPct: 0.0143,
      });
    });

    it("calculates currentPrice from marketValue/volume when last_price is null", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "query_positions") {
          return [{
            stock_code: "SH.600519",
            stock_name: "贵州茅台",
            volume: 10,
            can_use_volume: 10,
            frozen_volume: 0,
            today_buy_volume: 0,
            avg_price: 1800,
            market_value: 18500,
            last_price: null,
            unrealized_pnl: null,
            unrealized_pnl_pct: null,
          }];
        }
        return {};
      });
      const broker = await connectBroker();
      const positions = await broker.queryPositions();
      expect(positions[0].currentPrice).toBe(1850); // 18500 / 10
    });

    it("computes unrealizedPnl when not provided but currentPrice and avgPrice available", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "query_positions") {
          return [{
            stock_code: "SH.600519",
            stock_name: "贵州茅台",
            volume: 10,
            can_use_volume: 10,
            frozen_volume: 0,
            today_buy_volume: 0,
            avg_price: 1800,
            market_value: 18500,
            last_price: 1850,
            unrealized_pnl: null,
            unrealized_pnl_pct: null,
          }];
        }
        return {};
      });
      const broker = await connectBroker();
      const positions = await broker.queryPositions();
      expect(positions[0].unrealizedPnl).toBeCloseTo(500); // (1850 - 1800) * 10
      expect(positions[0].unrealizedPnlPct).toBeCloseTo(50 / 1800);
    });

    it("handles position with zero volume gracefully", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "query_positions") {
          return [{
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
          }];
        }
        return {};
      });
      const broker = await connectBroker();
      const positions = await broker.queryPositions();
      expect(positions[0].quantity).toBe(0);
      expect(positions[0].currentPrice).toBeUndefined();
      expect(positions[0].marketValue).toBeUndefined();
    });
  });

  // ─────────────────────────────────────────────────────────────
  // queryAccount
  // ─────────────────────────────────────────────────────────────
  describe("queryAccount", () => {
    it("maps raw account data", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "query_account") {
          return {
            account_id: "HK-001",
            cash: 100000,
            frozen_cash: 5000,
            total_asset: 250000,
            market_value: 150000,
            profit: 12000,
            profit_ratio: 0.048,
          };
        }
        return {};
      });
      const broker = await connectBroker();
      const account = await broker.queryAccount();
      expect(account).toMatchObject({
        accountId: "HK-001",
        brokerType: "futu",
        cash: 100000,
        frozenCash: 5000,
        totalAssets: 250000,
        marketValue: 150000,
        profit: 12000,
        profitPct: 0.048,
      });
      expect(account.lastSyncAt).toBeDefined();
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Quote subscription — multi-subscriber & unsubscribe
  // ─────────────────────────────────────────────────────────────
  describe("subscribeQuote — multi-subscriber", () => {
    it("delivers quote to multiple subscribers for same symbol", async () => {
      const broker = await connectBroker();
      const q1: unknown[] = [];
      const q2: unknown[] = [];
      broker.subscribeQuote(["600519"], (q) => q1.push(q));
      broker.subscribeQuote(["600519"], (q) => q2.push(q));

      eventHandler?.("quote", {
        stock_code: "SH.600519",
        last_price: 1800,
        open: 1790,
        high: 1810,
        low: 1785,
        last_close: 1795,
        volume: 50000,
        amount: 90000000,
        bid_price: [1800],
        ask_price: [1801],
        bid_vol: [100],
        ask_vol: [200],
        time: 1700000000000,
      });

      expect(q1).toHaveLength(1);
      expect(q2).toHaveLength(1);
    });

    it("only removes specific subscriber on unsubscribe", async () => {
      const broker = await connectBroker();
      const q1: unknown[] = [];
      const q2: unknown[] = [];
      const unsub1 = broker.subscribeQuote(["600519"], (q) => q1.push(q));
      broker.subscribeQuote(["600519"], (q) => q2.push(q));

      unsub1();

      eventHandler?.("quote", {
        stock_code: "SH.600519",
        last_price: 1800,
        open: 1790,
        high: 1810,
        low: 1785,
        last_close: 1795,
        volume: 50000,
        amount: 90000000,
        bid_price: [1800],
        ask_price: [1801],
        bid_vol: [100],
        ask_vol: [200],
        time: 1700000000000,
      });

      expect(q1).toHaveLength(0);
      expect(q2).toHaveLength(1);
    });

    it("cleans up quoteSubs map when last subscriber unsubscribes", async () => {
      const broker = await connectBroker();
      const unsub = broker.subscribeQuote(["AAPL"], () => {});
      unsub();

      // After last unsub, no callback should be invoked
      const q: unknown[] = [];
      eventHandler?.("quote", {
        stock_code: "US.AAPL",
        last_price: 180,
        open: 179,
        high: 181,
        low: 178,
        last_close: 179,
        volume: 10000,
        amount: 1800000,
        bid_price: [180],
        ask_price: [180.5],
        bid_vol: [100],
        ask_vol: [200],
        time: 1700000000000,
      });
      // No crash, no callbacks
      expect(q).toHaveLength(0);
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Quote mapping (mapRawQuote)
  // ─────────────────────────────────────────────────────────────
  describe("quote mapping", () => {
    it("calculates change and changePct correctly", async () => {
      const broker = await connectBroker();
      const quotes: unknown[] = [];
      broker.subscribeQuote(["600519"], (q) => quotes.push(q));

      eventHandler?.("quote", {
        stock_code: "SH.600519",
        last_price: 1810,
        open: 1790,
        high: 1815,
        low: 1785,
        last_close: 1800,
        volume: 50000,
        amount: 90000000,
        bid_price: [1809],
        ask_price: [1811],
        bid_vol: [100],
        ask_vol: [200],
        time: 1700000000000,
      });

      const q = quotes[0] as { change: number; changePct: number; bid1: number; ask1: number };
      expect(q.change).toBeCloseTo(10);
      expect(q.changePct).toBeCloseTo((10 / 1800) * 100);
      expect(q.bid1).toBe(1809);
      expect(q.ask1).toBe(1811);
    });

    it("handles zero last_close gracefully (changePct = 0)", async () => {
      const broker = await connectBroker();
      const quotes: unknown[] = [];
      broker.subscribeQuote(["600519"], (q) => quotes.push(q));

      eventHandler?.("quote", {
        stock_code: "SH.600519",
        last_price: 100,
        open: 100,
        high: 100,
        low: 100,
        last_close: 0,
        volume: 100,
        amount: 10000,
        bid_price: [],
        ask_price: [],
        bid_vol: [],
        ask_vol: [],
        time: 1700000000000,
      });

      const q = quotes[0] as { changePct: number; bid1: number | undefined };
      expect(q.changePct).toBe(0);
      expect(q.bid1).toBeUndefined();
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Fill event — with and without cached order
  // ─────────────────────────────────────────────────────────────
  describe("fill event handling", () => {
    it("resolves side from cached order when broker_order_id matches", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { broker_order_id: "FILL-SIDE" };
        return {};
      });
      const broker = await connectBroker();
      await broker.placeOrder({ symbol: "00700", market: "hk_stock", side: "sell", orderType: "limit", price: 360, quantity: 100 });

      const fills: Fill[] = [];
      broker.subscribeFills((f) => fills.push(f));

      eventHandler?.("fill", {
        order_id: "FILL-SIDE",
        stock_code: "HK.00700",
        traded_price: 360,
        traded_volume: 100,
        traded_amount: 36000,
        commission: 18,
        time: "2026-04-07 10:30:00",
      });

      expect(fills[0].side).toBe("sell");
    });

    it("defaults side to buy when order not in cache", async () => {
      const broker = await connectBroker();
      const fills: Fill[] = [];
      broker.subscribeFills((f) => fills.push(f));

      eventHandler?.("fill", {
        order_id: "UNKNOWN-ORDER",
        stock_code: "HK.00700",
        traded_price: 360,
        traded_volume: 100,
        traded_amount: 36000,
        commission: 18,
        time: "2026-04-07 10:30:00",
      });

      expect(fills[0].side).toBe("buy");
      expect(fills[0].orderId).toBe("UNKNOWN-ORDER");
    });

    it("unsubscribe from fills works correctly", async () => {
      const broker = await connectBroker();
      const fills: Fill[] = [];
      const unsub = broker.subscribeFills((f) => fills.push(f));
      unsub();

      eventHandler?.("fill", {
        order_id: "NO-RECV",
        stock_code: "HK.00700",
        traded_price: 360,
        traded_volume: 100,
        traded_amount: 36000,
        commission: 18,
        time: "2026-04-07 10:30:00",
      });

      expect(fills).toHaveLength(0);
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Order status event — cache updates
  // ─────────────────────────────────────────────────────────────
  describe("order_status event — cache update", () => {
    it("updates cached order fields on status push", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { broker_order_id: "CACHE-UPD" };
        if (method === "query_order") throw new Error("should use cache");
        return {};
      });
      const broker = await connectBroker();
      const order = await broker.placeOrder({
        symbol: "00700", market: "hk_stock", side: "buy", orderType: "limit", price: 350, quantity: 100,
      });

      eventHandler?.("order_status", {
        order_id: "CACHE-UPD",
        stock_code: "HK.00700",
        order_type: "BUY",
        price: 350,
        order_volume: 100,
        traded_volume: 100,
        traded_price: 349.5,
        order_status: "FILLED_ALL",
      });

      // Query should see updated cache
      const result = await broker.queryOrder(order.orderId);
      expect(result!.status).toBe("filled");
      expect(result!.filledQty).toBe(100);
      expect(result!.avgFillPrice).toBe(349.5);
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Position change event — null current (position closed)
  // ─────────────────────────────────────────────────────────────
  describe("position_change event", () => {
    it("delivers null position when current is null (position closed)", async () => {
      const broker = await connectBroker();
      const changes: Array<{ code: string; pos: LivePosition | null }> = [];
      broker.onPositionChange((code, pos) => changes.push({ code, pos }));

      eventHandler?.("position_change", {
        stock_code: "HK.00700",
        previous: {
          stock_code: "HK.00700",
          stock_name: "腾讯控股",
          volume: 100,
          can_use_volume: 100,
          frozen_volume: 0,
          today_buy_volume: 0,
          avg_price: 350,
          market_value: 35000,
          last_price: 350,
        },
        current: null,
      });

      expect(changes).toHaveLength(1);
      expect(changes[0].code).toBe("00700");
      expect(changes[0].pos).toBeNull();
    });

    it("unsubscribe from positionChange works", async () => {
      const broker = await connectBroker();
      const changes: unknown[] = [];
      const unsub = broker.onPositionChange((code, pos) => changes.push({ code, pos }));
      unsub();

      eventHandler?.("position_change", {
        stock_code: "HK.00700",
        previous: null,
        current: null,
      });

      expect(changes).toHaveLength(0);
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Order book & ticker — unsubscribe
  // ─────────────────────────────────────────────────────────────
  describe("subscribeOrderBook — unsubscribe", () => {
    it("unsubscribe stops delivery", async () => {
      const broker = await connectBroker();
      const books: unknown[] = [];
      const unsub = broker.subscribeOrderBook((code, bids, asks, time) =>
        books.push({ code, bids, asks, time }),
      );
      unsub();

      eventHandler?.("order_book", {
        stock_code: "SH.600519",
        bids: [{ price: 1800, volume: 100, order_count: 5 }],
        asks: [{ price: 1801, volume: 200, order_count: 3 }],
        time: 1700000000000,
      });

      expect(books).toHaveLength(0);
    });
  });

  describe("subscribeTicker — unsubscribe", () => {
    it("unsubscribe stops delivery", async () => {
      const broker = await connectBroker();
      const tickers: unknown[] = [];
      const unsub = broker.subscribeTicker((data) => tickers.push(data));
      unsub();

      eventHandler?.("ticker", {
        stock_code: "HK.00700",
        price: 355.2,
        volume: 500,
        turnover: 177600,
        ticker_direction: "BUY",
        sequence: 12345,
        time: "2026-04-07 10:30:00",
      });

      expect(tickers).toHaveLength(0);
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Disconnect — error propagation
  // ─────────────────────────────────────────────────────────────
  describe("disconnect — error handling", () => {
    it("sets connected=false even if Python bridge call fails", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "disconnect") return Promise.reject(new Error("process already dead"));
        return Promise.resolve({});
      });
      const broker = await connectBroker();
      expect(broker.connected).toBe(true);

      // disconnect should not throw, and should still mark as disconnected
      await broker.disconnect();
      expect(broker.connected).toBe(false);
      expect(mockStop).toHaveBeenCalled();
    });

    it("is idempotent on double disconnect", async () => {
      const broker = await connectBroker();
      await broker.disconnect();
      await broker.disconnect();
      expect(mockStop).toHaveBeenCalledTimes(1);
    });
  });

  // ─────────────────────────────────────────────────────────────
  // modifyOrder — edge cases
  // ─────────────────────────────────────────────────────────────
  describe("modifyOrder — edge cases", () => {
    it("throws for non-existent order", async () => {
      const broker = await connectBroker();
      await expect(broker.modifyOrder("nonexistent", { price: 100 })).rejects.toThrow("not found");
    });

    it("preserves original fields when partially modifying", async () => {
      let callCount = 0;
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") {
          callCount++;
          return { broker_order_id: `MOD-${callCount}` };
        }
        return {};
      });
      const broker = await connectBroker();
      const order = await broker.placeOrder({
        symbol: "AAPL",
        market: "us_stock",
        side: "buy",
        orderType: "limit",
        price: 180,
        quantity: 50,
        strategyId: "strat-1",
      });

      // Only modify price, keep quantity
      const modified = await broker.modifyOrder(order.orderId, { price: 185 });
      expect(modified.price).toBe(185);
      expect(modified.quantity).toBe(50);
      expect(modified.strategyId).toBe("strat-1");
    });
  });

  // ─────────────────────────────────────────────────────────────
  // isMarketOpen
  // ─────────────────────────────────────────────────────────────
  describe("isMarketOpen", () => {
    it("returns true for hk_stock (simplified)", async () => {
      const broker = await connectBroker();
      expect(await broker.isMarketOpen("hk_stock")).toBe(true);
    });

    it("returns true for us_stock (simplified)", async () => {
      const broker = await connectBroker();
      expect(await broker.isMarketOpen("us_stock")).toBe(true);
    });

    it("delegates to isCnMarketOpen for cn_stock", async () => {
      const broker = await connectBroker();
      const result = await broker.isMarketOpen("cn_stock");
      expect(result).toBe(true); // mocked to return true
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Default config fallback
  // ─────────────────────────────────────────────────────────────
  describe("config defaults", () => {
    it("uses default config when futu config is missing", async () => {
      // Should not throw during construction
      const broker = createBroker(configNoFutu);
      expect(broker.brokerType).toBe("futu");
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Unknown event type (no crash)
  // ─────────────────────────────────────────────────────────────
  describe("unknown event handling", () => {
    it("ignores unknown event types without crashing", async () => {
      const broker = await connectBroker();
      // Should not throw
      eventHandler?.("unknown_event_type", { some: "data" });
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Market detection from Futu symbol
  // ─────────────────────────────────────────────────────────────
  describe("detectMarketFromFutuSymbol via queryOrders", () => {
    it("detects SH/SZ as cn_stock", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "query_orders") {
          return [{
            order_id: "DM-1",
            stock_code: "SH.600519",
            order_type: "BUY",
            price: 1800,
            order_volume: 100,
            traded_volume: 0,
            traded_price: 0,
            order_status: "SUBMITTED",
          }];
        }
        return {};
      });
      const broker = await connectBroker();
      const orders = await broker.queryOrders();
      expect(orders[0].market).toBe("cn_stock");
    });
  });

  // ─────────────────────────────────────────────────────────────
  // getQuoteSnapshot — empty input
  // ─────────────────────────────────────────────────────────────
  describe("getQuoteSnapshot — edge cases", () => {
    it("handles empty symbol list", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "get_quote_snapshot") return [];
        return {};
      });
      const broker = await connectBroker();
      const snapshots = await broker.getQuoteSnapshot([]);
      expect(snapshots).toHaveLength(0);
    });

    it("maps multiple symbols correctly", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "get_quote_snapshot") {
          return [
            {
              stock_code: "HK.00700",
              last_price: 355,
              open: 350,
              high: 360,
              low: 348,
              last_close: 352,
              volume: 100000,
              amount: 35500000,
              bid_price: [354.8],
              ask_price: [355.2],
              bid_vol: [500],
              ask_vol: [300],
              time: 1700000000000,
            },
            {
              stock_code: "US.AAPL",
              last_price: 180,
              open: 178,
              high: 182,
              low: 177,
              last_close: 179,
              volume: 50000,
              amount: 9000000,
              bid_price: [179.9],
              ask_price: [180.1],
              bid_vol: [1000],
              ask_vol: [800],
              time: 1700000000000,
            },
          ];
        }
        return {};
      });
      const broker = await connectBroker();
      const snapshots = await broker.getQuoteSnapshot(["00700", "AAPL"]);
      expect(snapshots).toHaveLength(2);
      expect(snapshots[0].symbol.market).toBe("hk_stock");
      expect(snapshots[1].symbol.market).toBe("us_stock");
    });
  });
});
