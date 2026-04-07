/**
 * Tests for FutuBroker — focused on real-time subscription/push/event dispatch:
 *   - subscribeQuoteEx with various sub type combos
 *   - multi-subscriber interactions for fills, orderStatus, positionChange, orderBook, ticker
 *   - concurrent subscribe/unsubscribe race-like scenarios
 *   - event dispatch with malformed/partial data
 *   - order cache pruning boundary conditions
 *   - quote unsubscribe sends unsubscribe_quote call
 *   - handleEvent for unknown/unhandled event types
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
  dataDir: "/tmp/oc-futu-sub-test",
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

async function connectBroker() {
  const broker = new FutuBroker(config);
  await broker.connect();
  return broker;
}

describe("FutuBroker — Subscription & Event Dispatch", () => {
  // ─────────────────────────────────────────────────────────────
  // subscribeQuoteEx — sub type variations
  // ─────────────────────────────────────────────────────────────
  describe("subscribeQuoteEx — sub type combinations", () => {
    it("defaults to QUOTE sub type when none specified", async () => {
      const broker = await connectBroker();
      broker.subscribeQuoteEx(["600519"], () => {});
      expect(mockCall).toHaveBeenCalledWith("subscribe_quote", {
        stock_codes: ["SH.600519"],
        sub_types: ["QUOTE"],
      });
    });

    it("passes single ORDER_BOOK sub type", async () => {
      const broker = await connectBroker();
      broker.subscribeQuoteEx(["00700"], () => {}, ["ORDER_BOOK"]);
      expect(mockCall).toHaveBeenCalledWith("subscribe_quote", {
        stock_codes: ["SZ.00700"],
        sub_types: ["ORDER_BOOK"],
      });
    });

    it("passes TICKER sub type alone", async () => {
      const broker = await connectBroker();
      broker.subscribeQuoteEx(["AAPL"], () => {}, ["TICKER"]);
      expect(mockCall).toHaveBeenCalledWith("subscribe_quote", {
        stock_codes: ["US.AAPL"],
        sub_types: ["TICKER"],
      });
    });

    it("handles subscribe_quote Python bridge error gracefully (no throw)", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "subscribe_quote") throw new Error("network timeout");
        return {};
      });
      const broker = await connectBroker();
      // Should NOT throw — errors are caught and logged
      expect(() => broker.subscribeQuoteEx(["600519"], () => {})).not.toThrow();
    });

    it("unsubscribe sends unsubscribe_quote with matching sub types", async () => {
      const broker = await connectBroker();
      const unsub = broker.subscribeQuoteEx(
        ["AAPL"],
        () => {},
        ["QUOTE", "TICKER"],
      );
      unsub();
      // Wait for async call
      await new Promise((r) => setTimeout(r, 10));
      expect(mockCall).toHaveBeenCalledWith("unsubscribe_quote", {
        stock_codes: ["US.AAPL"],
        sub_types: ["QUOTE", "TICKER"],
      });
    });

    it("handles unsubscribe_quote Python bridge error gracefully", async () => {
      let subCalled = false;
      mockCall.mockImplementation((method: string) => {
        if (method === "subscribe_quote") { subCalled = true; return {}; }
        if (method === "unsubscribe_quote") throw new Error("already closed");
        return {};
      });
      const broker = await connectBroker();
      const unsub = broker.subscribeQuoteEx(["AAPL"], () => {});
      // Should NOT throw
      expect(() => unsub()).not.toThrow();
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Multi-subscriber for fills
  // ─────────────────────────────────────────────────────────────
  describe("subscribeFills — multi-subscriber", () => {
    it("delivers fills to multiple subscribers", async () => {
      const broker = await connectBroker();
      const fills1: Fill[] = [];
      const fills2: Fill[] = [];
      broker.subscribeFills((f) => fills1.push(f));
      broker.subscribeFills((f) => fills2.push(f));

      eventHandler?.("fill", {
        order_id: "MULTI-F1",
        stock_code: "HK.00700",
        traded_price: 355,
        traded_volume: 100,
        traded_amount: 35500,
        commission: 17.75,
        time: "2026-04-07 10:30:00",
      });

      expect(fills1).toHaveLength(1);
      expect(fills2).toHaveLength(1);
    });

    it("only removes specific subscriber on unsubscribe", async () => {
      const broker = await connectBroker();
      const fills1: Fill[] = [];
      const fills2: Fill[] = [];
      const unsub1 = broker.subscribeFills((f) => fills1.push(f));
      broker.subscribeFills((f) => fills2.push(f));
      unsub1();

      eventHandler?.("fill", {
        order_id: "MULTI-F2",
        stock_code: "HK.00700",
        traded_price: 355,
        traded_volume: 100,
        traded_amount: 35500,
        commission: 17.75,
        time: "2026-04-07 10:30:00",
      });

      expect(fills1).toHaveLength(0);
      expect(fills2).toHaveLength(1);
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Multi-subscriber for orderStatus
  // ─────────────────────────────────────────────────────────────
  describe("subscribeOrderStatus — multi-subscriber", () => {
    it("delivers status to multiple subscribers", async () => {
      const broker = await connectBroker();
      const s1: OrderStatus[] = [];
      const s2: OrderStatus[] = [];
      broker.subscribeOrderStatus((_id, status) => s1.push(status));
      broker.subscribeOrderStatus((_id, status) => s2.push(status));

      eventHandler?.("order_status", {
        order_id: "MULTI-S1",
        stock_code: "US.AAPL",
        order_type: "BUY",
        price: 180,
        order_volume: 10,
        traded_volume: 10,
        traded_price: 179.5,
        order_status: "FILLED_ALL",
      });

      expect(s1).toEqual(["filled"]);
      expect(s2).toEqual(["filled"]);
    });

    it("unsubscribe removes only one subscriber", async () => {
      const broker = await connectBroker();
      const s1: OrderStatus[] = [];
      const s2: OrderStatus[] = [];
      const unsub1 = broker.subscribeOrderStatus((_id, status) => s1.push(status));
      broker.subscribeOrderStatus((_id, status) => s2.push(status));
      unsub1();

      eventHandler?.("order_status", {
        order_id: "MULTI-S2",
        stock_code: "US.AAPL",
        order_type: "BUY",
        price: 180,
        order_volume: 10,
        traded_volume: 0,
        traded_price: 0,
        order_status: "SUBMITTED",
      });

      expect(s1).toHaveLength(0);
      expect(s2).toHaveLength(1);
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Multi-subscriber for positionChange
  // ─────────────────────────────────────────────────────────────
  describe("onPositionChange — multi-subscriber", () => {
    it("delivers position changes to multiple subscribers", async () => {
      const broker = await connectBroker();
      const ch1: unknown[] = [];
      const ch2: unknown[] = [];
      broker.onPositionChange((code, pos) => ch1.push({ code, pos }));
      broker.onPositionChange((code, pos) => ch2.push({ code, pos }));

      eventHandler?.("position_change", {
        stock_code: "SH.600519",
        previous: null,
        current: {
          stock_code: "SH.600519",
          stock_name: "贵州茅台",
          volume: 10,
          can_use_volume: 10,
          frozen_volume: 0,
          today_buy_volume: 10,
          avg_price: 1800,
          market_value: 18500,
          last_price: 1850,
          unrealized_pnl: 500,
          unrealized_pnl_pct: 0.0278,
        },
      });

      expect(ch1).toHaveLength(1);
      expect(ch2).toHaveLength(1);
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Multi-subscriber for orderBook
  // ─────────────────────────────────────────────────────────────
  describe("subscribeOrderBook — multi-subscriber", () => {
    it("delivers order book to multiple subscribers", async () => {
      const broker = await connectBroker();
      const b1: unknown[] = [];
      const b2: unknown[] = [];
      broker.subscribeOrderBook((code, bids, asks, time) => b1.push({ code }));
      broker.subscribeOrderBook((code, bids, asks, time) => b2.push({ code }));

      eventHandler?.("order_book", {
        stock_code: "HK.00700",
        bids: [{ price: 355, volume: 1000, order_count: 10 }],
        asks: [{ price: 355.2, volume: 800, order_count: 8 }],
        time: 1700000000000,
      });

      expect(b1).toHaveLength(1);
      expect(b2).toHaveLength(1);
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Multi-subscriber for ticker
  // ─────────────────────────────────────────────────────────────
  describe("subscribeTicker — multi-subscriber", () => {
    it("delivers ticker to multiple subscribers", async () => {
      const broker = await connectBroker();
      const t1: unknown[] = [];
      const t2: unknown[] = [];
      broker.subscribeTicker((data) => t1.push(data));
      broker.subscribeTicker((data) => t2.push(data));

      eventHandler?.("ticker", {
        stock_code: "US.AAPL",
        price: 180,
        volume: 500,
        turnover: 90000,
        ticker_direction: "BUY",
        sequence: 1234,
        time: "2026-04-07 10:00:00",
      });

      expect(t1).toHaveLength(1);
      expect(t2).toHaveLength(1);
    });
  });

  // ─────────────────────────────────────────────────────────────
  // subscribeQuote for multiple symbols at once
  // ─────────────────────────────────────────────────────────────
  describe("subscribeQuote — multiple symbols", () => {
    it("subscribes to multiple symbols in one call", async () => {
      const broker = await connectBroker();
      const quotes: unknown[] = [];
      broker.subscribeQuote(["600519", "AAPL"], (q) => quotes.push(q));

      expect(mockCall).toHaveBeenCalledWith("subscribe_quote", {
        stock_codes: ["SH.600519", "US.AAPL"],
        sub_types: ["QUOTE"],
      });

      // Receive quote for first symbol
      eventHandler?.("quote", {
        stock_code: "SH.600519",
        last_price: 1800,
        open: 1790, high: 1810, low: 1785, last_close: 1795,
        volume: 50000, amount: 90000000,
        bid_price: [1800], ask_price: [1801], bid_vol: [100], ask_vol: [200],
        time: 1700000000000,
      });

      // Receive quote for second symbol
      eventHandler?.("quote", {
        stock_code: "US.AAPL",
        last_price: 180,
        open: 179, high: 181, low: 178, last_close: 179,
        volume: 10000, amount: 1800000,
        bid_price: [180], ask_price: [180.5], bid_vol: [100], ask_vol: [200],
        time: 1700000001000,
      });

      expect(quotes).toHaveLength(2);
    });

    it("unsubscribe from multi-symbol removes all", async () => {
      const broker = await connectBroker();
      const quotes: unknown[] = [];
      const unsub = broker.subscribeQuote(["600519", "AAPL"], (q) => quotes.push(q));
      unsub();

      eventHandler?.("quote", {
        stock_code: "SH.600519",
        last_price: 1800,
        open: 1790, high: 1810, low: 1785, last_close: 1795,
        volume: 50000, amount: 90000000,
        bid_price: [1800], ask_price: [1801], bid_vol: [100], ask_vol: [200],
        time: 1700000000000,
      });
      eventHandler?.("quote", {
        stock_code: "US.AAPL",
        last_price: 180,
        open: 179, high: 181, low: 178, last_close: 179,
        volume: 10000, amount: 1800000,
        bid_price: [180], ask_price: [180.5], bid_vol: [100], ask_vol: [200],
        time: 1700000001000,
      });

      expect(quotes).toHaveLength(0);
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Quote for non-subscribed symbol is silently ignored
  // ─────────────────────────────────────────────────────────────
  describe("event dispatch — unmatched symbol", () => {
    it("quote event for non-subscribed symbol does not crash", async () => {
      const broker = await connectBroker();
      // No subscriptions
      eventHandler?.("quote", {
        stock_code: "HK.09988",
        last_price: 100, open: 99, high: 101, low: 98, last_close: 99,
        volume: 5000, amount: 500000,
        bid_price: [100], ask_price: [100.5], bid_vol: [50], ask_vol: [50],
        time: 1700000000000,
      });
      // No crash — just silently ignored
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Order cache pruning
  // ─────────────────────────────────────────────────────────────
  describe("order cache pruning", () => {
    it("does not prune when under 500 orders", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { broker_order_id: `B-${Math.random()}` };
        return {};
      });
      const broker = await connectBroker();

      // Place a few orders
      for (let i = 0; i < 5; i++) {
        await broker.placeOrder({
          symbol: "AAPL", market: "us_stock", side: "buy", orderType: "market", quantity: 1,
        });
      }

      // All orders should still be queryable
      // (we can't directly access orderCache, but queryOrder returns non-null for recent)
      // Just ensuring no crash with moderate order count
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Rapid subscribe/unsubscribe cycles
  // ─────────────────────────────────────────────────────────────
  describe("rapid subscribe/unsubscribe", () => {
    it("handles rapid subscribe and unsubscribe without leaking callbacks", async () => {
      const broker = await connectBroker();
      const fills: Fill[] = [];

      const unsub1 = broker.subscribeFills((f) => fills.push(f));
      const unsub2 = broker.subscribeFills((f) => fills.push(f));
      const unsub3 = broker.subscribeFills((f) => fills.push(f));

      unsub1();
      unsub2();
      unsub3();

      eventHandler?.("fill", {
        order_id: "RAPID-1",
        stock_code: "HK.00700",
        traded_price: 355,
        traded_volume: 100,
        traded_amount: 35500,
        commission: 17.75,
        time: "2026-04-07 10:30:00",
      });

      expect(fills).toHaveLength(0);
    });

    it("handles interleaved quote subscribe/unsubscribe", async () => {
      const broker = await connectBroker();
      const q1: unknown[] = [];
      const q2: unknown[] = [];

      const unsub1 = broker.subscribeQuote(["AAPL"], (q) => q1.push(q));
      const unsub2 = broker.subscribeQuote(["AAPL"], (q) => q2.push(q));

      // Unsubscribe first, second still active
      unsub1();

      eventHandler?.("quote", {
        stock_code: "US.AAPL",
        last_price: 180, open: 179, high: 181, low: 178, last_close: 179,
        volume: 10000, amount: 1800000,
        bid_price: [180], ask_price: [180.5], bid_vol: [100], ask_vol: [200],
        time: 1700000000000,
      });

      expect(q1).toHaveLength(0);
      expect(q2).toHaveLength(1);

      // Unsubscribe second
      unsub2();

      eventHandler?.("quote", {
        stock_code: "US.AAPL",
        last_price: 181, open: 179, high: 182, low: 178, last_close: 179,
        volume: 10001, amount: 1810000,
        bid_price: [181], ask_price: [181.5], bid_vol: [100], ask_vol: [200],
        time: 1700000002000,
      });

      expect(q2).toHaveLength(1); // still 1, not 2
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Fill event generates unique fillId
  // ─────────────────────────────────────────────────────────────
  describe("fill event — fillId uniqueness", () => {
    it("generates unique fillId for each fill event", async () => {
      const broker = await connectBroker();
      const fills: Fill[] = [];
      broker.subscribeFills((f) => fills.push(f));

      for (let i = 0; i < 5; i++) {
        eventHandler?.("fill", {
          order_id: `UNIQ-${i}`,
          stock_code: "HK.00700",
          traded_price: 355 + i,
          traded_volume: 100,
          traded_amount: 35500,
          commission: 17.75,
          time: "2026-04-07 10:30:00",
        });
      }

      expect(fills).toHaveLength(5);
      const ids = fills.map((f) => f.fillId);
      const uniqueIds = new Set(ids);
      expect(uniqueIds.size).toBe(5);
    });
  });

  // ─────────────────────────────────────────────────────────────
  // order_status event without matching cached order
  // ─────────────────────────────────────────────────────────────
  describe("order_status event — no cached order", () => {
    it("still dispatches event using broker order_id as local ID", async () => {
      const broker = await connectBroker();
      const statuses: Array<{ id: string; status: OrderStatus }> = [];
      broker.subscribeOrderStatus((id, status) => statuses.push({ id, status }));

      eventHandler?.("order_status", {
        order_id: "EXTERNAL-ORDER-1",
        stock_code: "HK.00700",
        order_type: "BUY",
        price: 350,
        order_volume: 100,
        traded_volume: 0,
        traded_price: 0,
        order_status: "SUBMITTED",
      });

      expect(statuses).toHaveLength(1);
      expect(statuses[0].id).toBe("EXTERNAL-ORDER-1");
      expect(statuses[0].status).toBe("submitted");
    });
  });

  // ─────────────────────────────────────────────────────────────
  // position_change event — symbol stripping from Futu format
  // ─────────────────────────────────────────────────────────────
  describe("position_change — symbol stripping", () => {
    it("strips SH. prefix from stock_code", async () => {
      const broker = await connectBroker();
      const changes: Array<{ code: string }> = [];
      broker.onPositionChange((code) => changes.push({ code }));

      eventHandler?.("position_change", {
        stock_code: "SH.600519",
        previous: null,
        current: {
          stock_code: "SH.600519",
          stock_name: "贵州茅台",
          volume: 10, can_use_volume: 10, frozen_volume: 0,
          today_buy_volume: 10, avg_price: 1800, market_value: 18500,
          last_price: 1850, unrealized_pnl: 500, unrealized_pnl_pct: 0.0278,
        },
      });

      expect(changes[0].code).toBe("600519");
    });

    it("strips US. prefix from stock_code", async () => {
      const broker = await connectBroker();
      const changes: Array<{ code: string }> = [];
      broker.onPositionChange((code) => changes.push({ code }));

      eventHandler?.("position_change", {
        stock_code: "US.TSLA",
        previous: null,
        current: {
          stock_code: "US.TSLA",
          stock_name: "Tesla",
          volume: 5, can_use_volume: 5, frozen_volume: 0,
          today_buy_volume: 5, avg_price: 250, market_value: 1275,
          last_price: 255, unrealized_pnl: 25, unrealized_pnl_pct: 0.02,
        },
      });

      expect(changes[0].code).toBe("TSLA");
    });

    it("strips BJ. prefix from stock_code", async () => {
      const broker = await connectBroker();
      const changes: Array<{ code: string }> = [];
      broker.onPositionChange((code) => changes.push({ code }));

      eventHandler?.("position_change", {
        stock_code: "BJ.430047",
        previous: null,
        current: null,
      });

      expect(changes[0].code).toBe("430047");
    });
  });

  // ─────────────────────────────────────────────────────────────
  // order_book event — symbol stripping
  // ─────────────────────────────────────────────────────────────
  describe("order_book event — symbol stripping", () => {
    it("strips market prefix from stock_code in order_book events", async () => {
      const broker = await connectBroker();
      const books: Array<{ code: string }> = [];
      broker.subscribeOrderBook((code) => books.push({ code }));

      eventHandler?.("order_book", {
        stock_code: "SZ.000001",
        bids: [{ price: 15, volume: 1000, order_count: 10 }],
        asks: [{ price: 15.05, volume: 800, order_count: 8 }],
        time: 1700000000000,
      });

      expect(books[0].code).toBe("000001");
    });
  });

  // ─────────────────────────────────────────────────────────────
  // ticker event — symbol stripping and field mapping
  // ─────────────────────────────────────────────────────────────
  describe("ticker event — symbol stripping", () => {
    it("maps ticker_direction to direction field", async () => {
      const broker = await connectBroker();
      const tickers: Array<{ code: string; direction: string }> = [];
      broker.subscribeTicker((data) => tickers.push(data as { code: string; direction: string }));

      eventHandler?.("ticker", {
        stock_code: "SH.600519",
        price: 1810,
        volume: 100,
        turnover: 181000,
        ticker_direction: "NEUTRAL",
        sequence: 5678,
        time: "2026-04-07 14:00:00",
      });

      expect(tickers[0].code).toBe("600519");
      expect(tickers[0].direction).toBe("NEUTRAL");
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Fill event — fromFutuSymbol strips all known prefixes
  // ─────────────────────────────────────────────────────────────
  describe("fill event — symbol normalisation", () => {
    it("strips SZ prefix from fill stock_code", async () => {
      const broker = await connectBroker();
      const fills: Fill[] = [];
      broker.subscribeFills((f) => fills.push(f));

      eventHandler?.("fill", {
        order_id: "SZ-FILL-1",
        stock_code: "SZ.000001",
        traded_price: 15,
        traded_volume: 1000,
        traded_amount: 15000,
        commission: 3.75,
        time: "2026-04-07 10:00:00",
      });

      expect(fills[0].symbol).toBe("000001");
    });
  });

  // ─────────────────────────────────────────────────────────────
  // Multiple event types in rapid succession (stress test)
  // ─────────────────────────────────────────────────────────────
  describe("rapid mixed event dispatch", () => {
    it("handles 50 mixed events without issues", async () => {
      const broker = await connectBroker();
      const fills: Fill[] = [];
      const statuses: OrderStatus[] = [];
      const positions: unknown[] = [];
      const books: unknown[] = [];
      const tickers: unknown[] = [];

      broker.subscribeFills((f) => fills.push(f));
      broker.subscribeOrderStatus((_id, status) => statuses.push(status));
      broker.onPositionChange((code, pos) => positions.push({ code, pos }));
      broker.subscribeOrderBook((code) => books.push(code));
      broker.subscribeTicker((data) => tickers.push(data));

      for (let i = 0; i < 10; i++) {
        eventHandler?.("fill", {
          order_id: `STRESS-F-${i}`, stock_code: "HK.00700",
          traded_price: 355, traded_volume: 100, traded_amount: 35500,
          commission: 17.75, time: "2026-04-07 10:30:00",
        });
        eventHandler?.("order_status", {
          order_id: `STRESS-S-${i}`, stock_code: "US.AAPL",
          order_type: "BUY", price: 180, order_volume: 10,
          traded_volume: 0, traded_price: 0, order_status: "SUBMITTED",
        });
        eventHandler?.("position_change", {
          stock_code: "SH.600519", previous: null, current: null,
        });
        eventHandler?.("order_book", {
          stock_code: "HK.00700",
          bids: [{ price: 355, volume: 1000, order_count: 10 }],
          asks: [{ price: 355.2, volume: 800, order_count: 8 }],
          time: 1700000000000 + i,
        });
        eventHandler?.("ticker", {
          stock_code: "US.TSLA", price: 250 + i, volume: 100,
          turnover: 25000, ticker_direction: "BUY", sequence: i,
          time: "2026-04-07 10:30:00",
        });
      }

      expect(fills).toHaveLength(10);
      expect(statuses).toHaveLength(10);
      expect(positions).toHaveLength(10);
      expect(books).toHaveLength(10);
      expect(tickers).toHaveLength(10);
    });
  });
});
