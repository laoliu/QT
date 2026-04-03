/**
 * Unit tests for OrderManager — covers the full order lifecycle:
 *   signal → risk check → optional confirmation → broker placeOrder
 *           → fill tracking → trade record generation
 *
 * Uses a mock IBrokerAdapter and temp filesystem for JSON persistence.
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  IBrokerAdapter,
  OrderRequest,
  FillCallback,
  OrderStatusCallback,
} from "../broker/adapter.js";
import type {
  BrokerAccount,
  Fill,
  LivePosition,
  Order,
  OrderStatus,
  QuantTradingConfig,
  TradeRecord,
  TradingSignal,
} from "../types.js";

// Mock market-hours so tests are not time-dependent
vi.mock("../broker/paper-broker.js", () => ({
  isMarketOpen: vi.fn().mockReturnValue(true),
}));

import { OrderManager } from "./order-manager.js";

// ── Fixtures ────────────────────────────────────────────────────

let tmpDir: string;

function makeConfig(overrides?: Partial<QuantTradingConfig>): QuantTradingConfig {
  return {
    dataDir: tmpDir,
    dataProvider: "demo",
    broker: "paper",
    initialCash: 1_000_000,
    pythonPath: "python3",
    backtestEngine: "builtin",
    tradingAccount: {
      mode: "paper",
      broker: "paper",
      riskLimits: {
        maxOrderValue: 500_000,
        maxPositionPct: 0.3,
        maxDailyLoss: 50_000,
        maxTotalLoss: 200_000,
        confirmationThreshold: 100_000,
        // Disable new rules that interact with test fixtures' avgCost
        stopLossPct: 0,
        priceLimitPct: 0,
      },
    },
    ...overrides,
  } as QuantTradingConfig;
}

function makeAccount(overrides?: Partial<BrokerAccount>): BrokerAccount {
  return {
    accountId: "test",
    brokerType: "paper",
    cash: 500_000,
    frozenCash: 0,
    totalAssets: 500_000,
    marketValue: 0,
    profit: 0,
    profitPct: 0,
    lastSyncAt: new Date().toISOString(),
    ...overrides,
  };
}

function makePosition(symbol: string, overrides?: Partial<LivePosition>): LivePosition {
  return {
    symbol,
    market: "cn_stock",
    quantity: 1000,
    availableQty: 1000,
    frozenQty: 0,
    todayBuyQty: 0,
    avgCost: 100,
    marketValue: 100_000,
    source: "local",
    lastSyncAt: new Date().toISOString(),
    ...overrides,
  };
}

function makeBrokerMock(
  account: BrokerAccount = makeAccount(),
  positions: LivePosition[] = [],
): IBrokerAdapter & {
  _fillCbs: FillCallback[];
  _statusCbs: OrderStatusCallback[];
  _simulateFill: (fill: Fill) => void;
  _simulateStatus: (id: string, status: OrderStatus, order: Order) => void;
} {
  const fillCbs: FillCallback[] = [];
  const statusCbs: OrderStatusCallback[] = [];
  let orderCounter = 0;

  const broker = {
    brokerType: "paper" as const,
    connected: true,
    _fillCbs: fillCbs,
    _statusCbs: statusCbs,
    _simulateFill(fill: Fill) {
      for (const cb of fillCbs) cb(fill);
    },
    _simulateStatus(id: string, status: OrderStatus, order: Order) {
      for (const cb of statusCbs) cb(id, status, order);
    },
    connect: vi.fn().mockResolvedValue(undefined),
    disconnect: vi.fn().mockResolvedValue(undefined),
    queryAccount: vi.fn().mockResolvedValue(account),
    queryPositions: vi.fn().mockResolvedValue(positions),
    placeOrder: vi.fn().mockImplementation(async (req: OrderRequest): Promise<Order> => {
      const now = new Date().toISOString();
      orderCounter++;
      return {
        orderId: `order-${orderCounter}`,
        symbol: req.symbol,
        market: req.market,
        side: req.side,
        orderType: req.orderType,
        price: req.price,
        quantity: req.quantity,
        filledQty: 0,
        status: "submitted" as OrderStatus,
        strategyId: req.strategyId,
        signalId: req.signalId,
        brokerType: "paper",
        createdAt: now,
        updatedAt: now,
        submittedAt: now,
      };
    }),
    cancelOrder: vi.fn().mockResolvedValue(undefined),
    modifyOrder: vi.fn().mockResolvedValue(undefined),
    queryOrder: vi.fn().mockResolvedValue(null),
    queryOrders: vi.fn().mockResolvedValue([]),
    subscribeQuote: vi.fn().mockReturnValue(() => {}),
    subscribeFills: vi.fn().mockImplementation((cb: FillCallback) => {
      fillCbs.push(cb);
      return () => {
        const idx = fillCbs.indexOf(cb);
        if (idx >= 0) fillCbs.splice(idx, 1);
      };
    }),
    subscribeOrderStatus: vi.fn().mockImplementation((cb: OrderStatusCallback) => {
      statusCbs.push(cb);
      return () => {
        const idx = statusCbs.indexOf(cb);
        if (idx >= 0) statusCbs.splice(idx, 1);
      };
    }),
    isMarketOpen: vi.fn().mockResolvedValue(true),
  } as unknown as IBrokerAdapter & {
    _fillCbs: FillCallback[];
    _statusCbs: OrderStatusCallback[];
    _simulateFill: (fill: Fill) => void;
    _simulateStatus: (id: string, status: OrderStatus, order: Order) => void;
  };
  return broker;
}

// ── Setup / Teardown ────────────────────────────────────────────

beforeEach(async () => {
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "oc-quant-om-test-"));
});

afterEach(async () => {
  await fs.rm(tmpDir, { recursive: true, force: true });
});

// ── Helper ──────────────────────────────────────────────────────

async function loadOrders(): Promise<Order[]> {
  try {
    return JSON.parse(await fs.readFile(path.join(tmpDir, "orders.json"), "utf-8"));
  } catch {
    return [];
  }
}

async function loadTrades(): Promise<TradeRecord[]> {
  try {
    return JSON.parse(await fs.readFile(path.join(tmpDir, "trades.json"), "utf-8"));
  } catch {
    return [];
  }
}

// ── Tests ───────────────────────────────────────────────────────

describe("OrderManager", () => {
  describe("submit()", () => {
    it("places a valid buy order through broker", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);

      const req: OrderRequest = {
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        quantity: 100,
        price: 100,
      };
      const result = await mgr.submit(req);
      expect(result.ok).toBe(true);
      if (result.ok) {
        expect(result.order.symbol).toBe("600519");
        expect(result.order.side).toBe("buy");
        expect(result.order.quantity).toBe(100);
      }

      // Verify order persisted to disk
      const orders = await loadOrders();
      expect(orders).toHaveLength(1);
      expect(orders[0].symbol).toBe("600519");
    });

    it("rejects order when risk check fails (insufficient cash)", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock(makeAccount({ cash: 100 }));
      const mgr = new OrderManager(cfg, broker, null);

      const req: OrderRequest = {
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        quantity: 100,
        price: 1000, // ¥100,000 > cash of ¥100
      };
      const result = await mgr.submit(req);
      expect(result.ok).toBe(false);
      if (!result.ok) {
        expect(result.ruleId).toBe("INSUFFICIENT_CASH");
      }

      // Verify no order stored
      const orders = await loadOrders();
      expect(orders).toHaveLength(0);
    });

    it("rejects sell when no position held", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock(makeAccount(), []);
      const mgr = new OrderManager(cfg, broker, null);

      const result = await mgr.submit({
        symbol: "600519",
        market: "cn_stock",
        side: "sell",
        orderType: "limit",
        quantity: 100,
        price: 100,
      });
      expect(result.ok).toBe(false);
      if (!result.ok) {
        expect(result.ruleId).toBe("INSUFFICIENT_POSITION");
      }
    });

    it("returns error when account query fails", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      (broker as any).queryAccount = vi.fn().mockRejectedValue(new Error("Network error"));
      const mgr = new OrderManager(cfg, broker, null);

      const result = await mgr.submit({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        quantity: 10,
        price: 10,
      });
      expect(result.ok).toBe(false);
      if (!result.ok) {
        expect(result.reason).toContain("无法获取账户信息");
      }
    });

    it("returns error when broker.placeOrder fails", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      (broker as any).placeOrder = vi.fn().mockRejectedValue(new Error("下单超时"));
      const mgr = new OrderManager(cfg, broker, null);

      const result = await mgr.submit({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        quantity: 10,
        price: 10,
      });
      expect(result.ok).toBe(false);
      if (!result.ok) {
        expect(result.reason).toContain("下单失败");
      }
    });
  });

  describe("submitFromSignal()", () => {
    it("converts signal to order request and submits", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);

      const signal: TradingSignal = {
        timestamp: Date.now(),
        symbol: "000858",
        direction: "buy",
        price: 150,
        quantity: 200,
        strategyId: "macd",
      };
      const result = await mgr.submitFromSignal(signal);
      expect(result.ok).toBe(true);
      if (result.ok) {
        expect(result.order.symbol).toBe("000858");
        expect(result.order.quantity).toBe(200);
        expect(result.order.strategyId).toBe("macd");
      }
    });

    it("defaults to 100 shares when signal has no quantity", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);

      const signal: TradingSignal = {
        timestamp: Date.now(),
        symbol: "000858",
        direction: "buy",
        price: 10,
        strategyId: "rsi",
      };
      const result = await mgr.submitFromSignal(signal);
      expect(result.ok).toBe(true);
      if (result.ok) {
        expect(result.order.quantity).toBe(100);
      }
    });

    it("uses market order type when price is 0", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);

      const signal: TradingSignal = {
        timestamp: Date.now(),
        symbol: "000858",
        direction: "buy",
        price: 0,
        strategyId: "rsi",
      };
      const result = await mgr.submitFromSignal(signal);
      expect(result.ok).toBe(true);
      // placeOrder should have been called — check orderType
      expect(broker.placeOrder).toHaveBeenCalledWith(
        expect.objectContaining({ orderType: "market" }),
      );
    });
  });

  describe("handleFill (via broker callbacks)", () => {
    it("updates order status to filled on full fill", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);

      // Place order first
      const result = await mgr.submit({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        quantity: 100,
        price: 50,
      });
      expect(result.ok).toBe(true);

      const orderId = result.ok ? result.order.orderId : "";

      // Simulate fill callback
      const fill: Fill = {
        fillId: "fill-1",
        orderId,
        symbol: "600519",
        side: "buy",
        price: 50,
        quantity: 100,
        commission: 1.25,
        timestamp: new Date().toISOString(),
      };
      broker._simulateFill(fill);

      // Wait for async fill handler
      await new Promise((r) => setTimeout(r, 50));

      const orders = await loadOrders();
      const updated = orders.find((o) => o.orderId === orderId);
      expect(updated?.status).toBe("filled");
      expect(updated?.filledQty).toBe(100);
    });

    it("creates trade record on sell fill", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock(makeAccount(), [makePosition("600519", { availableQty: 200 })]);
      const mgr = new OrderManager(cfg, broker, null);

      // Place a buy order (to create entry tracking)
      const buyResult = await mgr.submit({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        quantity: 100,
        price: 50,
      });
      expect(buyResult.ok).toBe(true);

      // Now place a sell order
      const sellResult = await mgr.submit({
        symbol: "600519",
        market: "cn_stock",
        side: "sell",
        orderType: "limit",
        quantity: 100,
        price: 60,
      });
      expect(sellResult.ok).toBe(true);
      const sellOrderId = sellResult.ok ? sellResult.order.orderId : "";

      // Simulate sell fill
      broker._simulateFill({
        fillId: "fill-sell-1",
        orderId: sellOrderId,
        symbol: "600519",
        side: "sell",
        price: 60,
        quantity: 100,
        commission: 1.85,
        timestamp: new Date().toISOString(),
      });

      await new Promise((r) => setTimeout(r, 50));

      const trades = await loadTrades();
      expect(trades).toHaveLength(1);
      expect(trades[0].symbol).toBe("600519");
      expect(trades[0].exitPrice).toBe(60);
      expect(trades[0].quantity).toBe(100);
      expect(trades[0].grossPnl).toBeCloseTo(1000); // (60 - 50) * 100
    });

    it("updates to partial status on partial fill", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);

      const result = await mgr.submit({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        quantity: 200,
        price: 30,
      });
      expect(result.ok).toBe(true);
      const orderId = result.ok ? result.order.orderId : "";

      // Partial fill: 50 of 200
      broker._simulateFill({
        fillId: "fill-p1",
        orderId,
        symbol: "600519",
        side: "buy",
        price: 30,
        quantity: 50,
        commission: 0.5,
        timestamp: new Date().toISOString(),
      });

      await new Promise((r) => setTimeout(r, 50));

      const orders = await loadOrders();
      const updated = orders.find((o) => o.orderId === orderId);
      expect(updated?.status).toBe("partial");
      expect(updated?.filledQty).toBe(50);
    });
  });

  describe("handleStatusChange (via broker callbacks)", () => {
    it("updates order status when broker reports rejection", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);

      const result = await mgr.submit({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        quantity: 10,
        price: 10,
      });
      expect(result.ok).toBe(true);
      const orderId = result.ok ? result.order.orderId : "";
      const order = result.ok ? result.order : ({} as Order);

      broker._simulateStatus(orderId, "rejected", order);
      await new Promise((r) => setTimeout(r, 50));

      const orders = await loadOrders();
      const updated = orders.find((o) => o.orderId === orderId);
      expect(updated?.status).toBe("rejected");
    });
  });

  describe("cancel()", () => {
    it("cancels an order via broker and updates status", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);

      const result = await mgr.submit({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        quantity: 10,
        price: 10,
      });
      expect(result.ok).toBe(true);
      const orderId = result.ok ? result.order.orderId : "";

      await mgr.cancel(orderId);
      expect(broker.cancelOrder).toHaveBeenCalledWith(orderId);

      const orders = await loadOrders();
      const updated = orders.find((o) => o.orderId === orderId);
      expect(updated?.status).toBe("cancelled");
    });

    it("marks cancelled locally when broker cache misses (restart scenario)", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);

      const result = await mgr.submit({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        quantity: 10,
        price: 10,
      });
      expect(result.ok).toBe(true);
      const orderId = result.ok ? result.order.orderId : "";

      // Simulate broker restart — cache is empty, throws "not found"
      vi.mocked(broker.cancelOrder).mockRejectedValueOnce(
        new Error(`Order ${orderId} not found or has no broker ID`),
      );

      await mgr.cancel(orderId);
      const orders = await loadOrders();
      const updated = orders.find((o) => o.orderId === orderId);
      expect(updated?.status).toBe("cancelled");
    });

    it("skips broker call for pending order with no brokerOrderId", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);
      // Make placeOrder return a pending order (never submitted to broker)
      vi.mocked(broker.placeOrder).mockImplementationOnce(async (req: OrderRequest) => ({
        orderId: "pending-order-1",
        symbol: req.symbol,
        market: req.market,
        side: req.side,
        orderType: req.orderType,
        price: req.price,
        quantity: req.quantity,
        filledQty: 0,
        status: "pending" as OrderStatus,
        brokerType: "paper" as const,
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
      }));

      const result = await mgr.submit({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        quantity: 10,
        price: 10,
      });
      expect(result.ok).toBe(true);

      await mgr.cancel("pending-order-1");
      expect(broker.cancelOrder).not.toHaveBeenCalled();
      const orders = await loadOrders();
      const updated = orders.find((o) => o.orderId === "pending-order-1");
      expect(updated?.status).toBe("cancelled");
    });
  });

  describe("cancelAll()", () => {
    it("cancels all open orders", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);

      // Place 2 orders
      await mgr.submit({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        quantity: 10,
        price: 10,
      });
      await mgr.submit({
        symbol: "000858",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        quantity: 10,
        price: 10,
      });

      await mgr.cancelAll();
      expect(broker.cancelOrder).toHaveBeenCalledTimes(2);
    });

    it("only cancels orders for specified symbol", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);

      await mgr.submit({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        quantity: 10,
        price: 10,
      });
      await mgr.submit({
        symbol: "000858",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        quantity: 10,
        price: 10,
      });

      await mgr.cancelAll("600519");
      expect(broker.cancelOrder).toHaveBeenCalledTimes(1);
    });
  });

  describe("getDailyPnl()", () => {
    it("returns 0 when no trades exist", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);

      const pnl = await mgr.getDailyPnl();
      expect(pnl).toBe(0);
    });

    it("sums today's trade PnLs", async () => {
      const cfg = makeConfig();
      const today = new Date().toISOString().slice(0, 10);

      // Write pre-existing trade records
      const trades: TradeRecord[] = [
        {
          tradeId: "t1",
          symbol: "600519",
          market: "cn_stock",
          side: "sell",
          entryTime: `${today}T09:30:00Z`,
          exitTime: `${today}T10:00:00Z`,
          entryPrice: 100,
          exitPrice: 110,
          quantity: 100,
          grossPnl: 1000,
          commission: 10,
          netPnl: 990,
          entryOrderId: "order-1",
        },
        {
          tradeId: "t2",
          symbol: "000858",
          market: "cn_stock",
          side: "sell",
          entryTime: `${today}T10:00:00Z`,
          exitTime: `${today}T10:30:00Z`,
          entryPrice: 50,
          exitPrice: 45,
          quantity: 200,
          grossPnl: -1000,
          commission: 5,
          netPnl: -1005,
          entryOrderId: "order-2",
        },
      ];
      await fs.writeFile(path.join(tmpDir, "trades.json"), JSON.stringify(trades));

      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);
      const pnl = await mgr.getDailyPnl();
      expect(pnl).toBeCloseTo(-15); // 990 + (-1005) = -15
    });
  });

  describe("getOrders()", () => {
    it("returns all persisted orders", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);

      await mgr.submit({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        quantity: 10,
        price: 10,
      });
      await mgr.submit({
        symbol: "000858",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        quantity: 10,
        price: 10,
      });

      const orders = await mgr.getOrders();
      expect(orders).toHaveLength(2);
    });

    it("filters by symbol", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);

      await mgr.submit({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        quantity: 10,
        price: 10,
      });
      await mgr.submit({
        symbol: "000858",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        quantity: 10,
        price: 10,
      });

      const orders = await mgr.getOrders({ symbol: "600519" });
      expect(orders).toHaveLength(1);
      expect(orders[0].symbol).toBe("600519");
    });

    it("filters by status", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);

      await mgr.submit({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        quantity: 10,
        price: 10,
      });
      const orders = await mgr.getOrders({ status: ["filled"] });
      expect(orders).toHaveLength(0);
    });
  });

  describe("market type detection", () => {
    it("detects cn_stock for 6-digit numeric codes", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);

      const signal: TradingSignal = {
        timestamp: Date.now(),
        symbol: "600519",
        direction: "buy",
        price: 10,
        strategyId: "test",
      };
      await mgr.submitFromSignal(signal);
      expect(broker.placeOrder).toHaveBeenCalledWith(
        expect.objectContaining({ market: "cn_stock" }),
      );
    });

    it("detects us_stock for letter-only codes", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);

      const signal: TradingSignal = {
        timestamp: Date.now(),
        symbol: "AAPL",
        direction: "buy",
        price: 10,
        strategyId: "test",
      };
      await mgr.submitFromSignal(signal);
      expect(broker.placeOrder).toHaveBeenCalledWith(
        expect.objectContaining({ market: "us_stock" }),
      );
    });
  });

  describe("concurrent order serialization", () => {
    it("does not lose orders when submitting concurrently", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);

      // Submit 5 orders concurrently — all should be persisted
      const signals = Array.from(
        { length: 5 },
        (_, i): TradingSignal => ({
          timestamp: Date.now() + i,
          symbol: `60000${i}`,
          direction: "buy",
          price: 10 + i,
          strategyId: "test",
        }),
      );
      const results = await Promise.all(signals.map((s) => mgr.submitFromSignal(s)));
      expect(results.every((r) => r.ok)).toBe(true);

      // Verify all orders persisted
      const allOrders = await mgr.getOrders({});
      expect(allOrders.length).toBe(5);
    });
  });

  describe("option order side tracking", () => {
    it("tracks buy_open as entry and creates trade on sell_close", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock(makeAccount(), [
        makePosition("10005646", { market: "etf_option", availableQty: 10 }),
      ]);
      const mgr = new OrderManager(cfg, broker, null);

      // buy_open entry
      const buyResult = await mgr.submit({
        symbol: "10005646",
        market: "etf_option",
        side: "buy_open",
        orderType: "limit",
        quantity: 10,
        price: 0.2,
      });
      expect(buyResult.ok).toBe(true);

      // sell_close exit
      const sellResult = await mgr.submit({
        symbol: "10005646",
        market: "etf_option",
        side: "sell_close",
        orderType: "limit",
        quantity: 10,
        price: 0.3,
      });
      expect(sellResult.ok).toBe(true);
      const sellOrderId = sellResult.ok ? sellResult.order.orderId : "";

      // Simulate sell_close fill
      broker._simulateFill({
        fillId: "fill-sc-1",
        orderId: sellOrderId,
        symbol: "10005646",
        side: "sell_close",
        price: 0.3,
        quantity: 10,
        commission: 17,
        timestamp: new Date().toISOString(),
      });

      await new Promise((r) => setTimeout(r, 50));

      const trades = await loadTrades();
      expect(trades).toHaveLength(1);
      expect(trades[0].exitPrice).toBe(0.3);
      expect(trades[0].grossPnl).toBeCloseTo(1); // (0.3 - 0.2) * 10
    });

    it("tracks sell_open as entry and inverts PnL on buy_close", async () => {
      const cfg = makeConfig({
        tradingAccount: {
          mode: "paper",
          broker: "paper",
          riskLimits: {
            maxOrderValue: 500_000,
            maxPositionPct: 0.3,
            maxDailyLoss: 50_000,
            maxTotalLoss: 200_000,
            confirmationThreshold: 100_000,
            stopLossPct: 0,
            priceLimitPct: 0,
            maxNakedShort: 100, // allow short option positions
          },
        },
      } as Partial<QuantTradingConfig>);
      const broker = makeBrokerMock(makeAccount(), [
        makePosition("10005646", { market: "etf_option", availableQty: 10 }),
      ]);
      const mgr = new OrderManager(cfg, broker, null);

      // sell_open entry (short) — opens a new short, no position needed
      const sellResult = await mgr.submit({
        symbol: "10005646",
        market: "etf_option",
        side: "sell_open",
        orderType: "limit",
        quantity: 10,
        price: 0.3,
      });
      expect(sellResult.ok).toBe(true);

      // buy_close exit (close short)
      const buyResult = await mgr.submit({
        symbol: "10005646",
        market: "etf_option",
        side: "buy_close",
        orderType: "limit",
        quantity: 10,
        price: 0.2,
      });
      expect(buyResult.ok).toBe(true);
      const buyOrderId = buyResult.ok ? buyResult.order.orderId : "";

      // Simulate buy_close fill
      broker._simulateFill({
        fillId: "fill-bc-1",
        orderId: buyOrderId,
        symbol: "10005646",
        side: "buy_close",
        price: 0.2,
        quantity: 10,
        commission: 17,
        timestamp: new Date().toISOString(),
      });

      await new Promise((r) => setTimeout(r, 50));

      const trades = await loadTrades();
      expect(trades).toHaveLength(1);
      // PnL inverted for buy_close: (entry 0.3 - exit 0.2) * 10 = 1.0
      expect(trades[0].grossPnl).toBeCloseTo(1);
    });
  });

  // ── VWAP partial fill test ──────────────────────────────────────

  describe("partial fills — VWAP avgFillPrice", () => {
    it("computes volume-weighted average price across partial fills", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock(makeAccount({ cash: 500_000 }));
      const mgr = new OrderManager(cfg, broker, null);

      const result = await mgr.submit({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        quantity: 200,
        price: 100,
      });
      expect(result.ok).toBe(true);
      const orderId = result.ok ? result.order.orderId : "";

      // First partial fill: 100 @ 99
      broker._simulateFill({
        fillId: "f1",
        orderId,
        symbol: "600519",
        side: "buy",
        price: 99,
        quantity: 100,
        commission: 3,
        timestamp: new Date().toISOString(),
      });

      await new Promise((r) => setTimeout(r, 30));

      // Second partial fill: 100 @ 101
      broker._simulateFill({
        fillId: "f2",
        orderId,
        symbol: "600519",
        side: "buy",
        price: 101,
        quantity: 100,
        commission: 3,
        timestamp: new Date().toISOString(),
      });

      await new Promise((r) => setTimeout(r, 30));

      // Check avgFillPrice is VWAP: (99*100 + 101*100) / 200 = 100
      const orders = JSON.parse(
        await fs.readFile(path.join(tmpDir, "orders.json"), "utf-8"),
      ) as Order[];
      const order = orders.find((o) => o.orderId === orderId);
      expect(order).toBeDefined();
      expect(order!.avgFillPrice).toBeCloseTo(100); // VWAP, not 101 (last fill)
      expect(order!.filledQty).toBe(200);
      expect(order!.status).toBe("filled");
    });
  });

  describe("setTradeCallback", () => {
    it("fires callback when a trade is completed", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock(makeAccount(), [makePosition("600519", { availableQty: 200 })]);
      const mgr = new OrderManager(cfg, broker, null);

      const trades: TradeRecord[] = [];
      mgr.setTradeCallback((trade) => trades.push(trade));

      // Place a buy order
      const buyResult = await mgr.submit({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        quantity: 100,
        price: 50,
      });
      expect(buyResult.ok).toBe(true);

      // Place a sell order
      const sellResult = await mgr.submit({
        symbol: "600519",
        market: "cn_stock",
        side: "sell",
        orderType: "limit",
        quantity: 100,
        price: 55,
      });
      expect(sellResult.ok).toBe(true);
      const sellOrderId = sellResult.ok ? sellResult.order.orderId : "";

      // Simulate sell fill → triggers trade record + callback
      broker._simulateFill({
        fillId: "fill-cb-1",
        orderId: sellOrderId,
        symbol: "600519",
        side: "sell",
        price: 55,
        quantity: 100,
        commission: 1.5,
        timestamp: new Date().toISOString(),
      });

      await new Promise((r) => setTimeout(r, 50));

      expect(trades).toHaveLength(1);
      expect(trades[0].symbol).toBe("600519");
      expect(trades[0].netPnl).toBeDefined();
      expect(trades[0].strategyId).toBeUndefined(); // manual order, no strategy
    });
  });
});
