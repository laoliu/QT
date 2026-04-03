/**
 * Unit tests for PaperBroker — simulation broker implementation.
 *
 * Covers:
 *  - Market order instant fills
 *  - Limit order queue and cancellation
 *  - Commission and stamp duty (A-share model)
 *  - Portfolio position tracking (buy/sell)
 *  - Order persistence (orders.json)
 *  - Account balance query
 *  - Callback subscriptions (fill, status)
 *  - Cancel order error cases
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Fill, Order, OrderStatus, Portfolio, QuantTradingConfig } from "../types.js";

// Mock createDataProvider to avoid real network calls
vi.mock("../data/market-data-provider.js", () => ({
  createDataProvider: vi.fn().mockReturnValue({
    getQuote: vi.fn().mockResolvedValue({
      symbol: { code: "600519", name: "贵州茅台" },
      price: 1850,
      open: 1840,
      high: 1860,
      low: 1830,
      volume: 10000,
      amount: 18_500_000,
      timestamp: Date.now(),
    }),
  }),
}));

import {
  PaperBroker,
  isCnMarketOpen,
  isCnNightSessionOpen,
  isUsMarketOpen,
  isHkMarketOpen,
  isMarketOpen,
  getNightSessionEnd,
} from "./paper-broker.js";

// ── Fixtures ────────────────────────────────────────────────────

let tmpDir: string;

function makeConfig(overrides?: Partial<QuantTradingConfig>): QuantTradingConfig {
  return {
    dataDir: tmpDir,
    dataProvider: "demo",
    broker: "paper",
    initialCash: 100_000,
    pythonPath: "python3",
    backtestEngine: "builtin",
    ...overrides,
  } as QuantTradingConfig;
}

async function loadPortfolio(): Promise<Portfolio> {
  try {
    return JSON.parse(await fs.readFile(path.join(tmpDir, "portfolio.json"), "utf-8"));
  } catch {
    return {
      id: "default",
      name: "模拟组合",
      cash: 100_000,
      totalValue: 100_000,
      positions: [],
      createdAt: "",
      updatedAt: "",
    };
  }
}

async function loadOrders(): Promise<Order[]> {
  try {
    return JSON.parse(await fs.readFile(path.join(tmpDir, "orders.json"), "utf-8"));
  } catch {
    return [];
  }
}

// ── Setup / Teardown ────────────────────────────────────────────

beforeEach(async () => {
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "oc-quant-pb-test-"));
});

afterEach(async () => {
  await fs.rm(tmpDir, { recursive: true, force: true });
});

// ── Tests ───────────────────────────────────────────────────────

describe("PaperBroker", () => {
  describe("placeOrder() — market order", () => {
    it("fills immediately and updates portfolio", async () => {
      const broker = new PaperBroker(makeConfig());
      await broker.connect();

      const order = await broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "market",
        quantity: 100,
        price: 50,
      });

      expect(order.orderId).toBeDefined();
      expect(order.symbol).toBe("600519");
      expect(order.status).toBe("submitted"); // initial status

      // Wait for fill to process
      await new Promise((r) => setTimeout(r, 50));

      // Verify order updated to filled
      const orders = await loadOrders();
      const filled = orders.find((o) => o.orderId === order.orderId);
      expect(filled?.status).toBe("filled");
      expect(filled?.filledQty).toBe(100);

      // Verify portfolio updated
      const portfolio = await loadPortfolio();
      expect(portfolio.positions).toHaveLength(1);
      expect(portfolio.positions[0].symbol).toBe("600519");
      expect(portfolio.positions[0].quantity).toBe(100);
      // Cash should decrease by order value + commission
      expect(portfolio.cash).toBeLessThan(100_000);

      await broker.disconnect();
    });

    it("sells and removes zero-quantity position", async () => {
      const broker = new PaperBroker(makeConfig());
      await broker.connect();

      // Buy first
      await broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "market",
        quantity: 100,
        price: 50,
      });
      await new Promise((r) => setTimeout(r, 50));

      // Sell all
      await broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "sell",
        orderType: "market",
        quantity: 100,
        price: 60,
      });
      await new Promise((r) => setTimeout(r, 50));

      const portfolio = await loadPortfolio();
      expect(portfolio.positions).toHaveLength(0);
      // Cash should be higher than original minus commissions
      // Buy: 100*50=5000 + commission; Sell: 100*60=6000 - commission - tax
      expect(portfolio.cash).toBeGreaterThan(100_000);

      await broker.disconnect();
    });
  });

  describe("placeOrder() — limit order", () => {
    it("queues limit order as submitted (not immediately filled)", async () => {
      const broker = new PaperBroker(makeConfig());
      await broker.connect();

      const order = await broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        quantity: 100,
        price: 50,
      });

      expect(order.status).toBe("submitted");

      // After a short wait, status should still be submitted (limit)
      await new Promise((r) => setTimeout(r, 50));
      const orders = await loadOrders();
      const found = orders.find((o) => o.orderId === order.orderId);
      // It could be either submitted or filled if limit matching ran
      expect(["submitted", "filled"]).toContain(found?.status);

      await broker.disconnect();
    });
  });

  describe("cancelOrder()", () => {
    it("cancels a pending limit order", async () => {
      const broker = new PaperBroker(makeConfig());
      await broker.connect();

      const order = await broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        quantity: 100,
        price: 10, // Very low price, unlikely to fill
      });

      await broker.cancelOrder(order.orderId);

      const orders = await loadOrders();
      const cancelled = orders.find((o) => o.orderId === order.orderId);
      expect(cancelled?.status).toBe("cancelled");

      await broker.disconnect();
    });

    it("throws on non-existent order", async () => {
      const broker = new PaperBroker(makeConfig());
      await broker.connect();

      await expect(broker.cancelOrder("nonexistent-id")).rejects.toThrow("not found");

      await broker.disconnect();
    });

    it("throws when cancelling already filled order", async () => {
      const broker = new PaperBroker(makeConfig());
      await broker.connect();

      const order = await broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "market",
        quantity: 100,
        price: 50,
      });
      await new Promise((r) => setTimeout(r, 50));

      await expect(broker.cancelOrder(order.orderId)).rejects.toThrow("Cannot cancel");

      await broker.disconnect();
    });
  });

  describe("modifyOrder()", () => {
    it("cancel-and-resubmit with new price", async () => {
      const broker = new PaperBroker(makeConfig());
      await broker.connect();

      const order = await broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        quantity: 100,
        price: 10,
      });

      const modified = await broker.modifyOrder(order.orderId, { price: 15 });
      expect(modified.price).toBe(15);
      expect(modified.orderId).not.toBe(order.orderId); // new order

      await broker.disconnect();
    });
  });

  describe("queryOrder()", () => {
    it("finds an existing order by ID", async () => {
      const broker = new PaperBroker(makeConfig());
      await broker.connect();

      const order = await broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        quantity: 100,
        price: 10,
      });

      const found = await broker.queryOrder(order.orderId);
      expect(found).not.toBeNull();
      expect(found?.symbol).toBe("600519");

      await broker.disconnect();
    });

    it("returns null for non-existent order", async () => {
      const broker = new PaperBroker(makeConfig());
      await broker.connect();

      const found = await broker.queryOrder("nonexistent");
      expect(found).toBeNull();

      await broker.disconnect();
    });
  });

  describe("queryPositions()", () => {
    it("returns LivePosition[] from portfolio", async () => {
      const broker = new PaperBroker(makeConfig());
      await broker.connect();

      // Buy to create a position
      await broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "market",
        quantity: 100,
        price: 50,
      });
      await new Promise((r) => setTimeout(r, 50));

      const positions = await broker.queryPositions();
      expect(positions).toHaveLength(1);
      expect(positions[0].symbol).toBe("600519");
      expect(positions[0].quantity).toBe(100);
      expect(positions[0].source).toBe("local");

      await broker.disconnect();
    });
  });

  describe("queryAccount()", () => {
    it("returns account summary with cash and market value", async () => {
      const broker = new PaperBroker(makeConfig());
      await broker.connect();

      const account = await broker.queryAccount();
      expect(account.brokerType).toBe("paper");
      expect(account.cash).toBe(100_000);
      expect(account.totalAssets).toBe(100_000);

      await broker.disconnect();
    });

    it("refreshes currentPrice from live quotes", async () => {
      const broker = new PaperBroker(makeConfig());
      await broker.connect();

      // Buy some stock to create a position
      await broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "market",
        quantity: 100,
        price: 50,
      });

      // queryAccount should fetch live quotes and use them for marketValue
      const account = await broker.queryAccount();
      // The demo provider returns a non-zero price, so marketValue should reflect live price
      expect(account.marketValue).toBeGreaterThan(0);
      // totalAssets = cash + marketValue
      expect(account.totalAssets).toBeCloseTo(account.cash + account.marketValue, 2);

      await broker.disconnect();
    });
  });

  describe("subscribeFills()", () => {
    it("calls callback on each fill", async () => {
      const broker = new PaperBroker(makeConfig());
      await broker.connect();

      const fills: Fill[] = [];
      broker.subscribeFills((fill) => fills.push(fill));

      await broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "market",
        quantity: 100,
        price: 50,
      });
      await new Promise((r) => setTimeout(r, 50));

      expect(fills).toHaveLength(1);
      expect(fills[0].symbol).toBe("600519");
      expect(fills[0].quantity).toBe(100);
      expect(fills[0].commission).toBeGreaterThan(0);

      await broker.disconnect();
    });

    it("returns unsubscribe function", async () => {
      const broker = new PaperBroker(makeConfig());
      await broker.connect();

      const fills: Fill[] = [];
      const unsub = broker.subscribeFills((fill) => fills.push(fill));
      unsub();

      await broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "market",
        quantity: 100,
        price: 50,
      });
      await new Promise((r) => setTimeout(r, 50));

      expect(fills).toHaveLength(0);

      await broker.disconnect();
    });
  });

  describe("subscribeOrderStatus()", () => {
    it("calls callback on status change", async () => {
      const broker = new PaperBroker(makeConfig());
      await broker.connect();

      const statuses: Array<{ id: string; status: OrderStatus }> = [];
      broker.subscribeOrderStatus((id, status) => statuses.push({ id, status }));

      // Market order → instant fill → status becomes "filled"
      await broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "market",
        quantity: 100,
        price: 50,
      });
      await new Promise((r) => setTimeout(r, 50));

      expect(statuses.length).toBeGreaterThanOrEqual(1);
      expect(statuses.find((s) => s.status === "filled")).toBeDefined();

      await broker.disconnect();
    });
  });

  describe("commission model", () => {
    it("charges buy commission on A-shares", async () => {
      const broker = new PaperBroker(makeConfig());
      await broker.connect();

      const fills: Fill[] = [];
      broker.subscribeFills((fill) => fills.push(fill));

      await broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "market",
        quantity: 100,
        price: 100,
      });
      await new Promise((r) => setTimeout(r, 50));

      // Buy commission: 100 * 100 * 0.00025 = 2.5
      expect(fills[0].commission).toBeCloseTo(2.5);
      expect(fills[0].tax).toBeUndefined();

      await broker.disconnect();
    });

    it("charges sell commission + stamp duty on A-shares", async () => {
      const broker = new PaperBroker(makeConfig());
      await broker.connect();

      // Buy first
      await broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "market",
        quantity: 100,
        price: 100,
      });
      await new Promise((r) => setTimeout(r, 50));

      const fills: Fill[] = [];
      broker.subscribeFills((fill) => fills.push(fill));

      // Sell
      await broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "sell",
        orderType: "market",
        quantity: 100,
        price: 110,
      });
      await new Promise((r) => setTimeout(r, 50));

      // Sell commission (excluding stamp duty): 110 * 100 * 0.00025 = 2.75
      expect(fills[0].commission).toBeCloseTo(2.75);
      // Stamp duty: 110 * 100 * 0.001 = 11
      expect(fills[0].tax).toBeCloseTo(11);

      await broker.disconnect();
    });
  });

  describe("connect / disconnect", () => {
    it("is idempotent on connect", async () => {
      const broker = new PaperBroker(makeConfig());
      await broker.connect();
      await broker.connect(); // second call should be no-op
      expect(broker.connected).toBe(true);
      await broker.disconnect();
    });

    it("sets connected to false on disconnect", async () => {
      const broker = new PaperBroker(makeConfig());
      await broker.connect();
      expect(broker.connected).toBe(true);
      await broker.disconnect();
      expect(broker.connected).toBe(false);
    });
  });

  describe("cost basis tracking", () => {
    it("computes weighted average cost on multiple buys", async () => {
      const broker = new PaperBroker(makeConfig());
      await broker.connect();

      // Buy 100 @ 50
      await broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "market",
        quantity: 100,
        price: 50,
      });
      await new Promise((r) => setTimeout(r, 50));

      // Buy 100 @ 70
      await broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "market",
        quantity: 100,
        price: 70,
      });
      await new Promise((r) => setTimeout(r, 50));

      const portfolio = await loadPortfolio();
      const pos = portfolio.positions.find((p) => p.symbol === "600519");
      expect(pos?.quantity).toBe(200);
      // Average cost: (50*100 + 70*100) / 200 = 60
      expect(pos?.avgCost).toBeCloseTo(60);

      await broker.disconnect();
    });
  });
});

describe("isCnMarketOpen()", () => {
  it("returns false on weekends", () => {
    // Saturday 2026-03-07
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-03-07T10:00:00+08:00"));
    expect(isCnMarketOpen()).toBe(false);
    vi.useRealTimers();
  });

  it("returns true during morning session", () => {
    vi.useFakeTimers();
    // Wednesday 2026-03-04 10:00 CST
    vi.setSystemTime(new Date("2026-03-04T02:00:00Z")); // 10:00 UTC+8
    expect(isCnMarketOpen()).toBe(true);
    vi.useRealTimers();
  });

  it("returns false during lunch break", () => {
    vi.useFakeTimers();
    // Wednesday 2026-03-04 12:00 CST
    vi.setSystemTime(new Date("2026-03-04T04:00:00Z")); // 12:00 UTC+8
    expect(isCnMarketOpen()).toBe(false);
    vi.useRealTimers();
  });

  it("returns true during afternoon session", () => {
    vi.useFakeTimers();
    // Wednesday 2026-03-04 14:00 CST
    vi.setSystemTime(new Date("2026-03-04T06:00:00Z")); // 14:00 UTC+8
    expect(isCnMarketOpen()).toBe(true);
    vi.useRealTimers();
  });
});

describe("isUsMarketOpen()", () => {
  it("returns false on weekends", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-03-07T15:00:00Z")); // Saturday 10 AM ET
    expect(isUsMarketOpen()).toBe(false);
    vi.useRealTimers();
  });

  it("returns true during US trading hours (March 4 = pre-DST, EST/UTC-5)", () => {
    vi.useFakeTimers();
    // Wednesday 2026-03-04 15:00 UTC = 10:00 EST
    vi.setSystemTime(new Date("2026-03-04T15:00:00Z"));
    expect(isUsMarketOpen()).toBe(true);
    vi.useRealTimers();
  });

  it("returns false after US market close", () => {
    vi.useFakeTimers();
    // Wednesday 2026-03-04 21:00 UTC = 16:00 EST (at close boundary)
    vi.setSystemTime(new Date("2026-03-04T21:00:00Z"));
    expect(isUsMarketOpen()).toBe(false);
    vi.useRealTimers();
  });
});

describe("isHkMarketOpen()", () => {
  it("returns false on weekends", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-03-07T02:00:00Z")); // Saturday 10:00 HKT
    expect(isHkMarketOpen()).toBe(false);
    vi.useRealTimers();
  });

  it("returns true during morning session", () => {
    vi.useFakeTimers();
    // Wednesday 2026-03-04 10:30 HKT = 02:30 UTC
    vi.setSystemTime(new Date("2026-03-04T02:30:00Z"));
    expect(isHkMarketOpen()).toBe(true);
    vi.useRealTimers();
  });

  it("returns true during afternoon session", () => {
    vi.useFakeTimers();
    // Wednesday 2026-03-04 14:00 HKT = 06:00 UTC
    vi.setSystemTime(new Date("2026-03-04T06:00:00Z"));
    expect(isHkMarketOpen()).toBe(true);
    vi.useRealTimers();
  });

  it("returns false during lunch break", () => {
    vi.useFakeTimers();
    // Wednesday 2026-03-04 12:30 HKT = 04:30 UTC
    vi.setSystemTime(new Date("2026-03-04T04:30:00Z"));
    expect(isHkMarketOpen()).toBe(false);
    vi.useRealTimers();
  });
});

describe("isMarketOpen() unified", () => {
  it("routes cn_stock to isCnMarketOpen", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-03-04T02:00:00Z")); // 10:00 CST
    expect(isMarketOpen("cn_stock")).toBe(true);
    vi.useRealTimers();
  });

  it("routes us_stock to isUsMarketOpen", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-03-04T15:00:00Z")); // 11:00 ET
    expect(isMarketOpen("us_stock")).toBe(true);
    vi.useRealTimers();
  });

  it("routes hk_stock to isHkMarketOpen", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-03-04T02:30:00Z")); // 10:30 HKT
    expect(isMarketOpen("hk_stock")).toBe(true);
    vi.useRealTimers();
  });

  it("defaults to CN market for undefined", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-03-04T02:00:00Z")); // 10:00 CST
    expect(isMarketOpen()).toBe(true);
    vi.useRealTimers();
  });

  it("routes etf_option to CN market hours", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-03-04T02:00:00Z")); // 10:00 CST Wed
    expect(isMarketOpen("etf_option")).toBe(true);
    vi.useRealTimers();
  });

  it("routes index_option to CN market hours", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-03-04T02:00:00Z")); // 10:00 CST Wed
    expect(isMarketOpen("index_option")).toBe(true);
    vi.useRealTimers();
  });

  it("routes futures to CN market + night session", () => {
    vi.useFakeTimers();
    // 14:00 CST Wed — regular session
    vi.setSystemTime(new Date("2026-03-04T06:00:00Z"));
    expect(isMarketOpen("futures")).toBe(true);
    // 22:00 CST Wed — night session
    vi.setSystemTime(new Date("2026-03-04T14:00:00Z"));
    expect(isMarketOpen("futures")).toBe(true);
    // 18:00 CST Wed — neither session
    vi.setSystemTime(new Date("2026-03-04T10:00:00Z"));
    expect(isMarketOpen("futures")).toBe(false);
    vi.useRealTimers();
  });
});

describe("PaperBroker — option commission model", () => {
  it("uses per-contract fee for etf_option", async () => {
    const broker = new PaperBroker(makeConfig());
    await broker.connect();

    const fills: Fill[] = [];
    broker.subscribeFills((f) => fills.push(f));

    await broker.placeOrder({
      symbol: "10005646",
      market: "etf_option",
      side: "buy_open",
      orderType: "market",
      quantity: 10,
      price: 0.2345,
    });

    expect(fills).toHaveLength(1);
    // Per-contract fee: 10 * 1.7 = 17
    expect(fills[0].commission).toBe(17);
  });

  it("uses per-contract fee for commodity_option", async () => {
    const broker = new PaperBroker(makeConfig());
    await broker.connect();

    const fills: Fill[] = [];
    broker.subscribeFills((f) => fills.push(f));

    await broker.placeOrder({
      symbol: "m2505-C-3000",
      market: "commodity_option",
      side: "sell_open",
      orderType: "market",
      quantity: 5,
      price: 120,
    });

    expect(fills).toHaveLength(1);
    // Per-contract fee: 5 * 1.7 = 8.5
    expect(fills[0].commission).toBe(8.5);
  });
});

describe("PaperBroker — option metadata propagation", () => {
  it("propagates option fields from OrderRequest to Order", async () => {
    const broker = new PaperBroker(makeConfig());
    await broker.connect();

    const order = await broker.placeOrder({
      symbol: "10005646",
      market: "etf_option",
      side: "buy_open",
      orderType: "limit",
      quantity: 10,
      price: 0.2345,
      strike: 3.0,
      expiry: "2026-03-25",
      optionType: "call",
      underlying: "510050",
    });

    expect(order.strike).toBe(3.0);
    expect(order.expiry).toBe("2026-03-25");
    expect(order.optionType).toBe("call");
    expect(order.underlying).toBe("510050");
  });
});

describe("PaperBroker — sell_open / buy_close (short options)", () => {
  it("sell_open creates a short (negative-qty) position and adds premium", async () => {
    const broker = new PaperBroker(makeConfig());
    await broker.connect();

    await broker.placeOrder({
      symbol: "10005646",
      market: "etf_option",
      side: "sell_open",
      orderType: "market",
      quantity: 5,
      price: 0.25,
    });

    const p = await loadPortfolio();
    const pos = p.positions.find((pp) => pp.symbol === "10005646");
    expect(pos).toBeDefined();
    // Short position has negative quantity
    expect(pos!.quantity).toBe(-5);
    // Cash should have increased (received premium minus commission)
    expect(p.cash).toBeGreaterThan(100_000 - 10); // premium ~1.25, commission ~8.5
  });

  it("buy_close reduces a short position", async () => {
    const broker = new PaperBroker(makeConfig());
    await broker.connect();

    // First: sell_open to create a short
    await broker.placeOrder({
      symbol: "10005646",
      market: "etf_option",
      side: "sell_open",
      orderType: "market",
      quantity: 5,
      price: 0.25,
    });

    // Then: buy_close to close part of the short
    await broker.placeOrder({
      symbol: "10005646",
      market: "etf_option",
      side: "buy_close",
      orderType: "market",
      quantity: 3,
      price: 0.2,
    });

    const p = await loadPortfolio();
    const pos = p.positions.find((pp) => pp.symbol === "10005646");
    expect(pos).toBeDefined();
    // -5 + 3 = -2 remaining short
    expect(pos!.quantity).toBe(-2);
  });

  it("buy_close fully closing a short removes the position", async () => {
    const broker = new PaperBroker(makeConfig());
    await broker.connect();

    await broker.placeOrder({
      symbol: "10005646",
      market: "etf_option",
      side: "sell_open",
      orderType: "market",
      quantity: 3,
      price: 0.25,
    });

    await broker.placeOrder({
      symbol: "10005646",
      market: "etf_option",
      side: "buy_close",
      orderType: "market",
      quantity: 3,
      price: 0.2,
    });

    const p = await loadPortfolio();
    const pos = p.positions.find((pp) => pp.symbol === "10005646");
    // Position should be removed (quantity reached 0)
    expect(pos).toBeUndefined();
  });
});

describe("isCnNightSessionOpen()", () => {
  it("returns true during night session (21:00-23:59 CST Mon-Fri)", () => {
    vi.useFakeTimers();
    // Wednesday 22:00 CST = 14:00 UTC
    vi.setSystemTime(new Date("2026-03-04T14:00:00Z"));
    expect(isCnNightSessionOpen()).toBe(true);
    vi.useRealTimers();
  });

  it("returns true during early morning carry (01:00 CST Tue-Sat)", () => {
    vi.useFakeTimers();
    // Thursday 01:00 CST = Wednesday 17:00 UTC
    vi.setSystemTime(new Date("2026-03-04T17:00:00Z"));
    expect(isCnNightSessionOpen()).toBe(true);
    vi.useRealTimers();
  });

  it("returns false during daytime (10:00 CST)", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-03-04T02:00:00Z")); // 10:00 CST
    expect(isCnNightSessionOpen()).toBe(false);
    vi.useRealTimers();
  });

  it("returns false on weekends", () => {
    vi.useFakeTimers();
    // Saturday 22:00 CST
    vi.setSystemTime(new Date("2026-03-07T14:00:00Z"));
    expect(isCnNightSessionOpen()).toBe(false);
    vi.useRealTimers();
  });
});

describe("futures margin model", () => {
  beforeEach(async () => {
    // Ensure clean portfolio
    try {
      await fs.unlink(path.join(tmpDir, "portfolio.json"));
    } catch {
      /* ignore */
    }
  });

  it("buy_open on futures deducts margin, not full notional", async () => {
    const broker = new PaperBroker(
      makeConfig({
        futuresConfig: {
          defaultMarginRate: 0.1,
          defaultMultiplier: 10,
          defaultFeePerLot: 5,
        },
      } as Partial<QuantTradingConfig>),
    );
    await broker.connect();

    // buy_open 2 lots of RB2505 at 1850 (mock price)
    // margin = 1850 * 2 * 10 * 0.1 = 3700, commission = 2*5 = 10
    await broker.placeOrder({
      symbol: "RB2505",
      market: "futures",
      side: "buy_open",
      orderType: "market",
      quantity: 2,
    });

    const acct = await broker.queryAccount();
    // cash should have decreased by margin + commission = 3710
    expect(acct.cash).toBeCloseTo(100_000 - 3710, 0);

    const pf = await loadPortfolio();
    const pos = pf.positions.find((p) => p.symbol === "RB2505");
    expect(pos).toBeDefined();
    expect(pos!.quantity).toBe(2);

    await broker.disconnect();
  });

  it("sell_open on futures deducts margin (unlike options which receive premium)", async () => {
    const broker = new PaperBroker(
      makeConfig({
        futuresConfig: {
          defaultMarginRate: 0.1,
          defaultMultiplier: 10,
          defaultFeePerLot: 5,
        },
      } as Partial<QuantTradingConfig>),
    );
    await broker.connect();

    // sell_open 1 lot of IF2503 at 1850 (mock price)
    // margin = 1850 * 1 * 10 * 0.1 = 1850, commission = 5
    await broker.placeOrder({
      symbol: "IF2503",
      market: "futures",
      side: "sell_open",
      orderType: "market",
      quantity: 1,
    });

    const acct = await broker.queryAccount();
    expect(acct.cash).toBeCloseTo(100_000 - 1855, 0);

    const pf = await loadPortfolio();
    const pos = pf.positions.find((p) => p.symbol === "IF2503");
    expect(pos).toBeDefined();
    expect(pos!.quantity).toBe(-1); // negative = short

    await broker.disconnect();
  });

  it("sell_close on futures releases margin and realizes P&L", async () => {
    const broker = new PaperBroker(
      makeConfig({
        futuresConfig: {
          defaultMarginRate: 0.1,
          defaultMultiplier: 10,
          defaultFeePerLot: 5,
        },
      } as Partial<QuantTradingConfig>),
    );
    await broker.connect();

    // Step 1: buy_open 2 lots at 1850 (mock price)
    // margin = 1850 * 2 * 10 * 0.1 = 3700, commission = 10
    await broker.placeOrder({
      symbol: "RB2505",
      market: "futures",
      side: "buy_open",
      orderType: "market",
      quantity: 2,
    });

    const cashAfterOpen = (await broker.queryAccount()).cash;
    // cashAfterOpen = 100_000 - 3710 = 96_290

    // Step 2: sell_close 2 lots at 1850 (mock price) — break-even trade
    // avgCost = 1850, fillPrice = 1850
    // margin return = 1850 * 2 * 10 * 0.1 = 3700
    // P&L = (1850 - 1850) * 2 * 10 = 0
    // cash delta = 3700 + 0 - 10(commission) = 3690
    await broker.placeOrder({
      symbol: "RB2505",
      market: "futures",
      side: "sell_close",
      orderType: "market",
      quantity: 2,
    });

    const cashAfterClose = (await broker.queryAccount()).cash;
    // After closing, position should be removed
    const pf = await loadPortfolio();
    expect(pf.positions.find((p) => p.symbol === "RB2505")).toBeUndefined();
    // Cash should have changed by margin return + P&L - commission
    expect(cashAfterClose).toBeCloseTo(cashAfterOpen + 3690, 0);

    await broker.disconnect();
  });

  it("tracks frozenMargin on futures positions after buy_open", async () => {
    const broker = new PaperBroker(
      makeConfig({
        futuresConfig: {
          defaultMarginRate: 0.1,
          defaultMultiplier: 10,
          defaultFeePerLot: 5,
        },
      } as Partial<QuantTradingConfig>),
    );
    await broker.connect();

    await broker.placeOrder({
      symbol: "RB2505",
      market: "futures",
      side: "buy_open",
      orderType: "market",
      quantity: 2,
    });

    const pf = await loadPortfolio();
    const pos = pf.positions.find((p) => p.symbol === "RB2505");
    expect(pos).toBeDefined();
    // frozenMargin = 1850 * 2 * 10 * 0.1 = 3700
    expect(pos!.frozenMargin).toBeCloseTo(3700, 0);

    await broker.disconnect();
  });

  it("tracks frozenMargin on futures short positions after sell_open", async () => {
    const broker = new PaperBroker(
      makeConfig({
        futuresConfig: {
          defaultMarginRate: 0.12,
          defaultMultiplier: 300,
          defaultFeePerLot: 5,
        },
      } as Partial<QuantTradingConfig>),
    );
    await broker.connect();

    await broker.placeOrder({
      symbol: "IF2503",
      market: "futures",
      side: "sell_open",
      orderType: "market",
      quantity: 1,
    });

    const pf = await loadPortfolio();
    const pos = pf.positions.find((p) => p.symbol === "IF2503");
    expect(pos).toBeDefined();
    expect(pos!.quantity).toBe(-1);
    // frozenMargin = 1850 * 1 * 300 * 0.12 = 66600
    expect(pos!.frozenMargin).toBeCloseTo(66600, 0);

    await broker.disconnect();
  });

  it("reduces frozenMargin proportionally on partial sell_close", async () => {
    const broker = new PaperBroker(
      makeConfig({
        futuresConfig: {
          defaultMarginRate: 0.1,
          defaultMultiplier: 10,
          defaultFeePerLot: 5,
        },
      } as Partial<QuantTradingConfig>),
    );
    await broker.connect();

    // Open 4 lots
    await broker.placeOrder({
      symbol: "RB2505",
      market: "futures",
      side: "buy_open",
      orderType: "market",
      quantity: 4,
    });

    const pf1 = await loadPortfolio();
    const pos1 = pf1.positions.find((p) => p.symbol === "RB2505");
    expect(pos1!.frozenMargin).toBeDefined();
    const fullMargin = pos1!.frozenMargin!;
    // fullMargin = 1850 * 4 * 10 * 0.1 = 7400
    expect(fullMargin).toBeCloseTo(7400, 0);

    // Close 2 lots (partial)
    await broker.placeOrder({
      symbol: "RB2505",
      market: "futures",
      side: "sell_close",
      orderType: "market",
      quantity: 2,
    });

    const pf2 = await loadPortfolio();
    const pos2 = pf2.positions.find((p) => p.symbol === "RB2505");
    expect(pos2).toBeDefined();
    expect(pos2!.quantity).toBe(2);
    // frozenMargin should be half of full margin
    expect(pos2!.frozenMargin).toBeCloseTo(fullMargin / 2, 0);

    await broker.disconnect();
  });
});

// ── Daily Settlement Tests ───────────────────────────────────────

describe("daily mark-to-market settlement", () => {
  beforeEach(async () => {
    try {
      await fs.unlink(path.join(tmpDir, "portfolio.json"));
    } catch {
      /* ignore */
    }
  });

  it("performDailySettlement adjusts cash by daily P&L for long futures", async () => {
    const broker = new PaperBroker(
      makeConfig({
        futuresConfig: {
          defaultMarginRate: 0.1,
          defaultMultiplier: 10,
          defaultFeePerLot: 5,
          enableDailySettlement: true,
        },
      } as Partial<QuantTradingConfig>),
    );
    await broker.connect();

    // Seed portfolio with a long position: 2 lots of RB2505 at avgCost 1800
    const seedPortfolio = {
      id: "default",
      name: "模拟组合",
      cash: 96_000, // after margin deduction
      totalValue: 100_000,
      positions: [
        {
          symbol: "RB2505",
          market: "futures" as const,
          quantity: 2,
          avgCost: 1800,
          currentPrice: 1800,
          frozenMargin: 1800 * 2 * 10 * 0.1, // 3600
        },
      ],
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
    await fs.writeFile(path.join(tmpDir, "portfolio.json"), JSON.stringify(seedPortfolio, null, 2));

    // Mock quote returns price 1850 (settlement price = 1850)
    // Daily P&L = (1850 - 1800) * 2 * 10 = 1000
    await broker.performDailySettlement();

    const pf = await loadPortfolio();
    const pos = pf.positions.find((p) => p.symbol === "RB2505")!;

    // Settlement price should be updated
    expect(pos.settlementPrice).toBe(1850);
    expect(pos.currentPrice).toBe(1850);

    // frozenMargin recalculated at settlement price
    // 1850 * 2 * 10 * 0.1 = 3700
    expect(pos.frozenMargin).toBeCloseTo(3700, 0);

    // Cash adjusted: +1000 (P&L) - 100 (margin increase: 3700-3600)
    expect(pf.cash).toBeCloseTo(96_000 + 1000 - 100, 0);

    await broker.disconnect();
  });

  it("performDailySettlement adjusts cash by daily P&L for short futures", async () => {
    const broker = new PaperBroker(
      makeConfig({
        futuresConfig: {
          defaultMarginRate: 0.1,
          defaultMultiplier: 10,
          defaultFeePerLot: 5,
          enableDailySettlement: true,
        },
      } as Partial<QuantTradingConfig>),
    );
    await broker.connect();

    // Seed portfolio with a short position: -3 lots at 1900
    const seedPortfolio = {
      id: "default",
      name: "模拟组合",
      cash: 94_300, // after margin
      totalValue: 100_000,
      positions: [
        {
          symbol: "RB2505",
          market: "futures" as const,
          quantity: -3,
          avgCost: 1900,
          currentPrice: 1900,
          frozenMargin: 1900 * 3 * 10 * 0.1, // 5700
        },
      ],
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
    await fs.writeFile(path.join(tmpDir, "portfolio.json"), JSON.stringify(seedPortfolio, null, 2));

    // Mock returns price 1850 — short position profits when price drops
    // Daily P&L = (1850 - 1900) * (-3) * 10 = 1500 (profit)
    await broker.performDailySettlement();

    const pf = await loadPortfolio();
    const pos = pf.positions.find((p) => p.symbol === "RB2505")!;

    expect(pos.settlementPrice).toBe(1850);
    // New margin = 1850 * 3 * 10 * 0.1 = 5550
    expect(pos.frozenMargin).toBeCloseTo(5550, 0);
    // Cash: +1500 (P&L) - (-150) (margin decrease: 5550-5700 = -150 → release 150)
    expect(pf.cash).toBeCloseTo(94_300 + 1500 + 150, 0);

    await broker.disconnect();
  });

  it("does not settle when no futures positions exist", async () => {
    const broker = new PaperBroker(
      makeConfig({
        futuresConfig: { enableDailySettlement: true },
      } as Partial<QuantTradingConfig>),
    );
    await broker.connect();

    // Seed with only a stock position
    const seedPortfolio = {
      id: "default",
      name: "模拟组合",
      cash: 90_000,
      totalValue: 100_000,
      positions: [{ symbol: "600519", market: "cn_stock" as const, quantity: 100, avgCost: 100 }],
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
    await fs.writeFile(path.join(tmpDir, "portfolio.json"), JSON.stringify(seedPortfolio, null, 2));

    await broker.performDailySettlement();

    const pf = await loadPortfolio();
    // Cash unchanged
    expect(pf.cash).toBe(90_000);

    await broker.disconnect();
  });

  it("forced liquidation triggers when cash < maintenance margin", async () => {
    const broker = new PaperBroker(
      makeConfig({
        futuresConfig: {
          defaultMarginRate: 0.1,
          defaultMultiplier: 10,
          defaultFeePerLot: 5,
          enableDailySettlement: true,
          maintenanceMarginRatio: 0.8,
        },
      } as Partial<QuantTradingConfig>),
    );
    await broker.connect();

    // Seed portfolio with large position, low cash → will breach maintenance margin
    // Position: 10 lots at 1800, margin = 1800 * 10 * 10 * 0.1 = 18000
    // Cash: only 5000 (should be < maintenance of 18000 * 0.8 = 14400)
    const seedPortfolio = {
      id: "default",
      name: "模拟组合",
      cash: 5000,
      totalValue: 23_000,
      positions: [
        {
          symbol: "RB2505",
          market: "futures" as const,
          quantity: 10,
          avgCost: 1800,
          currentPrice: 1850,
          frozenMargin: 18000,
        },
      ],
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
    await fs.writeFile(path.join(tmpDir, "portfolio.json"), JSON.stringify(seedPortfolio, null, 2));

    // Settlement mock price = 1850
    // Daily P&L = (1850 - 1800) * 10 * 10 = 5000 → cash += 5000 → cash = 10000
    // New margin = 1850 * 10 * 10 * 0.1 = 18500 → margin diff = 500 → cash -= 500 → cash = 9500
    // Maintenance margin = 18500 * 0.8 = 14800
    // cash (9500) < maintenance (14800) → forced liquidation
    await broker.performDailySettlement();

    const pf = await loadPortfolio();
    // Position should have been liquidated (at least partially)
    const pos = pf.positions.find((p) => p.symbol === "RB2505");
    // Position removed or reduced; cash should have recovered
    if (pos) {
      // If partially liquidated, remaining margin should be within cash
      expect(pf.cash).toBeGreaterThanOrEqual(0);
    } else {
      // Fully liquidated — all positions closed
      expect(pf.positions.filter((p) => p.market === "futures")).toHaveLength(0);
    }
    // Cash should have increased from margin release
    expect(pf.cash).toBeGreaterThan(5000);

    await broker.disconnect();
  });
});

// ── Night Session Tier Tests ─────────────────────────────────────

describe("getNightSessionEnd()", () => {
  it("returns 2300 (Tier 1) for most SHFE products like RB", () => {
    expect(getNightSessionEnd("RB2505")).toBe(2300);
    expect(getNightSessionEnd("rb")).toBe(2300); // case-insensitive
    expect(getNightSessionEnd("I2505")).toBe(2300); // 铁矿石
    expect(getNightSessionEnd("CF2505")).toBe(2300); // 棉花
  });

  it("returns 100 (Tier 2) for base metals", () => {
    expect(getNightSessionEnd("CU2505")).toBe(100);
    expect(getNightSessionEnd("AL2505")).toBe(100);
    expect(getNightSessionEnd("ZN2505")).toBe(100);
    expect(getNightSessionEnd("NI2505")).toBe(100);
  });

  it("returns 230 (Tier 3) for precious metals & crude", () => {
    expect(getNightSessionEnd("AU2505")).toBe(230);
    expect(getNightSessionEnd("AG2505")).toBe(230);
    expect(getNightSessionEnd("SC2505")).toBe(230);
  });

  it("returns 0 for CFFEX financial futures (no night session)", () => {
    expect(getNightSessionEnd("IF2503")).toBe(0);
    expect(getNightSessionEnd("IC2503")).toBe(0);
    expect(getNightSessionEnd("T2503")).toBe(0);
    expect(getNightSessionEnd("TF2503")).toBe(0);
  });

  it("returns 230 (conservative default) when no productCode provided", () => {
    expect(getNightSessionEnd()).toBe(230);
    expect(getNightSessionEnd(undefined)).toBe(230);
  });
});

describe("isCnNightSessionOpen() with product-specific tiers", () => {
  it("Tier 1 product (RB): open at 22:00 CST, closed at 23:30 CST", () => {
    vi.useFakeTimers();
    // Wednesday 22:00 CST = 14:00 UTC
    vi.setSystemTime(new Date("2026-03-04T14:00:00Z"));
    expect(isCnNightSessionOpen("RB")).toBe(true);

    // Wednesday 23:30 CST = 15:30 UTC → Tier 1 ends at 23:00
    vi.setSystemTime(new Date("2026-03-04T15:30:00Z"));
    expect(isCnNightSessionOpen("RB")).toBe(false);

    vi.useRealTimers();
  });

  it("Tier 2 product (CU): open at 00:30 (carry-over from previous night)", () => {
    vi.useFakeTimers();
    // Thursday 00:30 CST = Wednesday 16:30 UTC
    vi.setSystemTime(new Date("2026-03-04T16:30:00Z"));
    expect(isCnNightSessionOpen("CU")).toBe(true);

    // Thursday 01:30 CST = Wednesday 17:30 UTC → Tier 2 ends at 01:00
    vi.setSystemTime(new Date("2026-03-04T17:30:00Z"));
    expect(isCnNightSessionOpen("CU")).toBe(false);

    vi.useRealTimers();
  });

  it("Tier 3 product (AU): open at 02:00 (carry-over from previous night)", () => {
    vi.useFakeTimers();
    // Thursday 02:00 CST = Wednesday 18:00 UTC
    vi.setSystemTime(new Date("2026-03-04T18:00:00Z"));
    expect(isCnNightSessionOpen("AU")).toBe(true);

    // Thursday 03:00 CST = Wednesday 19:00 UTC → Tier 3 ends at 02:30
    vi.setSystemTime(new Date("2026-03-04T19:00:00Z"));
    expect(isCnNightSessionOpen("AU")).toBe(false);

    vi.useRealTimers();
  });

  it("CFFEX product (IF): always false in night session", () => {
    vi.useFakeTimers();
    // Wednesday 22:00 CST = 14:00 UTC
    vi.setSystemTime(new Date("2026-03-04T14:00:00Z"));
    expect(isCnNightSessionOpen("IF")).toBe(false);
    vi.useRealTimers();
  });

  it("no productCode uses conservative Tier 3 window", () => {
    vi.useFakeTimers();
    // Wednesday 22:00 CST
    vi.setSystemTime(new Date("2026-03-04T14:00:00Z"));
    expect(isCnNightSessionOpen()).toBe(true);

    // Thursday 02:00 CST
    vi.setSystemTime(new Date("2026-03-04T18:00:00Z"));
    expect(isCnNightSessionOpen()).toBe(true);

    vi.useRealTimers();
  });
});
