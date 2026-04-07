/**
 * Unit & integration tests for BasketOrderService.
 *
 * Covers:
 *   1. Order splitting logic (lotSize, maxChildQty)
 *   2. Concurrent execution with concurrency limits
 *   3. Partial fill / partial success tracking
 *   4. Rollback when failure threshold is exceeded
 *   5. Progress callbacks
 *   6. Edge cases (empty basket, single item, zero quantity)
 *   7. Integration with OrderManager (mock broker)
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
} from "../types.js";
import {
  BasketOrderService,
  type BasketConfig,
  type BasketItem,
  type BasketOrder,
  type SplitPolicy,
} from "./basket-order.js";

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
        maxOrderValue: 5_000_000,
        maxPositionPct: 0.5,
        maxDailyLoss: 500_000,
        maxTotalLoss: 2_000_000,
        confirmationThreshold: 10_000_000,
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
    cash: 5_000_000,
    frozenCash: 0,
    totalAssets: 5_000_000,
    marketValue: 0,
    profit: 0,
    profitPct: 0,
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

function makeBasketItem(symbol: string, quantity: number, price: number): BasketItem {
  return {
    symbol,
    market: "cn_stock",
    side: "buy",
    orderType: "limit",
    quantity,
    price,
  };
}

// ── Setup / Teardown ────────────────────────────────────────────

beforeEach(async () => {
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "oc-quant-basket-test-"));
});

afterEach(async () => {
  await fs.rm(tmpDir, { recursive: true, force: true });
});

// ── Tests ───────────────────────────────────────────────────────

describe("BasketOrderService", () => {
  // ── 1. Order Splitting Logic ──────────────────────────────────

  describe("splitItems()", () => {
    it("does not split when quantity is below maxChildQty", () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);
      const svc = new BasketOrderService(mgr);

      const items: BasketItem[] = [makeBasketItem("600519", 200, 100)];
      const policy: SplitPolicy = { maxChildQty: 500, lotSize: 100 };

      const children = svc.splitItems(items, policy);
      expect(children).toHaveLength(1);
      expect(children[0].request.quantity).toBe(200);
      expect(children[0].parentItemIndex).toBe(0);
    });

    it("splits large orders into multiple children", () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);
      const svc = new BasketOrderService(mgr);

      const items: BasketItem[] = [makeBasketItem("600519", 1000, 100)];
      const policy: SplitPolicy = { maxChildQty: 300, lotSize: 100 };

      const children = svc.splitItems(items, policy);
      // 1000 → 300 + 300 + 300 + 100
      expect(children).toHaveLength(4);
      expect(children[0].request.quantity).toBe(300);
      expect(children[1].request.quantity).toBe(300);
      expect(children[2].request.quantity).toBe(300);
      expect(children[3].request.quantity).toBe(100);
    });

    it("rounds quantities to lot size", () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);
      const svc = new BasketOrderService(mgr);

      const items: BasketItem[] = [makeBasketItem("600519", 350, 100)];
      const policy: SplitPolicy = { maxChildQty: 500, lotSize: 100 };

      const children = svc.splitItems(items, policy);
      expect(children).toHaveLength(1);
      expect(children[0].request.quantity).toBe(300); // 350 → floor to 300
    });

    it("handles lot size of 1 (no rounding)", () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);
      const svc = new BasketOrderService(mgr);

      const items: BasketItem[] = [makeBasketItem("AAPL", 73, 150)];
      const policy: SplitPolicy = { maxChildQty: 500, lotSize: 1 };

      const children = svc.splitItems(items, policy);
      expect(children).toHaveLength(1);
      expect(children[0].request.quantity).toBe(73);
    });

    it("filters out zero-quantity children after rounding", () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);
      const svc = new BasketOrderService(mgr);

      const items: BasketItem[] = [makeBasketItem("600519", 50, 100)];
      const policy: SplitPolicy = { maxChildQty: 500, lotSize: 100 };

      const children = svc.splitItems(items, policy);
      // 50 rounds down to 0 with lotSize 100
      expect(children).toHaveLength(0);
    });

    it("handles multiple items with different split needs", () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);
      const svc = new BasketOrderService(mgr);

      const items: BasketItem[] = [
        makeBasketItem("600519", 200, 100), // no split
        makeBasketItem("000858", 800, 50), // split into 300+300+200
      ];
      const policy: SplitPolicy = { maxChildQty: 300, lotSize: 100 };

      const children = svc.splitItems(items, policy);
      expect(children).toHaveLength(4); // 1 + 3
      expect(children[0].parentItemIndex).toBe(0);
      expect(children[1].parentItemIndex).toBe(1);
      expect(children[2].parentItemIndex).toBe(1);
      expect(children[3].parentItemIndex).toBe(1);
    });

    it("preserves all fields from original OrderRequest", () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);
      const svc = new BasketOrderService(mgr);

      const items: BasketItem[] = [
        {
          symbol: "600519",
          market: "cn_stock",
          side: "buy",
          orderType: "limit",
          quantity: 200,
          price: 1500,
          strategyId: "my-strategy",
          label: "贵州茅台",
        },
      ];
      const policy: SplitPolicy = { maxChildQty: 500, lotSize: 100 };

      const children = svc.splitItems(items, policy);
      expect(children[0].request.symbol).toBe("600519");
      expect(children[0].request.price).toBe(1500);
      expect(children[0].request.strategyId).toBe("my-strategy");
      expect(children[0].request.side).toBe("buy");
    });
  });

  // ── 2. Concurrent Execution ───────────────────────────────────

  describe("concurrent execution", () => {
    it("executes all items in a basket and returns completed status", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);
      const svc = new BasketOrderService(mgr);

      const items: BasketItem[] = [
        makeBasketItem("600519", 100, 100),
        makeBasketItem("000858", 200, 50),
        makeBasketItem("300750", 300, 80),
      ];

      const result = await svc.execute(items);

      expect(result.status).toBe("completed");
      expect(result.stats.totalChildren).toBe(3);
      expect(result.stats.filled).toBe(3);
      expect(result.stats.failed).toBe(0);
      expect(result.basketId).toBeDefined();
      expect(result.completedAt).toBeDefined();
    });

    it("respects concurrency limit", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      let maxConcurrent = 0;
      let currentConcurrent = 0;
      let orderSeq = 0;

      // Track concurrent broker.placeOrder calls via the OrderManager path
      vi.mocked(broker.placeOrder).mockImplementation(async (req: OrderRequest) => {
        currentConcurrent++;
        maxConcurrent = Math.max(maxConcurrent, currentConcurrent);
        // Small delay so concurrent workers overlap
        await new Promise((r) => setTimeout(r, 5));
        currentConcurrent--;
        orderSeq++;
        return {
          orderId: `order-${orderSeq}`,
          symbol: req.symbol,
          market: req.market,
          side: req.side,
          orderType: req.orderType,
          price: req.price,
          quantity: req.quantity,
          filledQty: 0,
          status: "submitted" as const,
          brokerType: "paper" as const,
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
          submittedAt: new Date().toISOString(),
        };
      });

      const mgr = new OrderManager(cfg, broker, null);
      const svc = new BasketOrderService(mgr);

      const items: BasketItem[] = Array.from({ length: 10 }, (_, i) =>
        makeBasketItem(`60000${i}`, 100, 10 + i),
      );

      await svc.execute(items, { concurrency: 3 });

      // Due to the async nature, maxConcurrent should be <= concurrency limit
      expect(maxConcurrent).toBeLessThanOrEqual(3);
      // All items should have been processed
      expect(orderSeq).toBe(10);
    }, 10000);

    it("submits orders through OrderManager (broker.placeOrder called)", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);
      const svc = new BasketOrderService(mgr);

      const items: BasketItem[] = [
        makeBasketItem("600519", 100, 100),
        makeBasketItem("000858", 200, 50),
      ];

      await svc.execute(items);

      expect(broker.placeOrder).toHaveBeenCalledTimes(2);
      expect(broker.placeOrder).toHaveBeenCalledWith(
        expect.objectContaining({ symbol: "600519", quantity: 100 }),
      );
      expect(broker.placeOrder).toHaveBeenCalledWith(
        expect.objectContaining({ symbol: "000858", quantity: 200 }),
      );
    });
  });

  // ── 3. Partial Success Tracking ───────────────────────────────

  describe("partial success", () => {
    it("marks basket as partial when some orders fail risk check", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock(makeAccount({ cash: 15_000 }));
      const mgr = new OrderManager(cfg, broker, null);
      const svc = new BasketOrderService(mgr);

      const items: BasketItem[] = [
        makeBasketItem("600519", 100, 100), // ¥10,000 — should pass
        makeBasketItem("000858", 100, 200), // ¥20,000 — exceeds cash, should fail
      ];

      const result = await svc.execute(items, { failureThreshold: 0.8 });

      expect(result.status).toBe("partial");
      expect(result.stats.filled).toBe(1);
      expect(result.stats.failed).toBe(1);
    });

    it("tracks child results individually", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);
      const svc = new BasketOrderService(mgr);

      const items: BasketItem[] = [
        makeBasketItem("600519", 100, 100),
        makeBasketItem("000858", 200, 50),
      ];

      const result = await svc.execute(items);

      for (const child of result.children) {
        expect(child.result).toBeDefined();
        expect(child.result!.ok).toBe(true);
        expect(child.status).toBe("filled");
      }
    });

    it("includes failure reason in child result", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock(makeAccount({ cash: 100 }));
      const mgr = new OrderManager(cfg, broker, null);
      const svc = new BasketOrderService(mgr);

      const items: BasketItem[] = [
        makeBasketItem("600519", 100, 1000), // ¥100,000 > cash ¥100
      ];

      const result = await svc.execute(items, { failureThreshold: 1.0 });

      expect(result.children[0].status).toBe("failed");
      expect(result.children[0].result!.ok).toBe(false);
      if (!result.children[0].result!.ok) {
        expect(result.children[0].result!.reason).toBeDefined();
      }
    });
  });

  // ── 4. Rollback on Failure Threshold ──────────────────────────

  describe("rollback", () => {
    it("rolls back when failure ratio exceeds threshold", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      let callCount = 0;
      // Make the first 3 orders fail, then succeed
      vi.mocked(broker.placeOrder).mockImplementation(async (req: OrderRequest) => {
        callCount++;
        if (callCount <= 3) {
          throw new Error("模拟失败");
        }
        return {
          orderId: `order-${callCount}`,
          symbol: req.symbol,
          market: req.market,
          side: req.side,
          orderType: req.orderType,
          price: req.price,
          quantity: req.quantity,
          filledQty: 0,
          status: "submitted" as OrderStatus,
          brokerType: "paper" as const,
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
          submittedAt: new Date().toISOString(),
        };
      });

      const mgr = new OrderManager(cfg, broker, null);
      const svc = new BasketOrderService(mgr);

      const items: BasketItem[] = Array.from({ length: 6 }, (_, i) =>
        makeBasketItem(`60000${i}`, 100, 10),
      );

      // failureThreshold = 0.5: 3/6 = 50% → triggers rollback
      const result = await svc.execute(items, {
        failureThreshold: 0.5,
        concurrency: 1, // Sequential so failure order is deterministic
      });

      expect(result.status).toBe("rolled_back");
      expect(result.stats.failed).toBeGreaterThanOrEqual(3);
    });

    it("does not roll back when failures are below threshold", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      let callCount = 0;
      // Make only the first order fail
      vi.mocked(broker.placeOrder).mockImplementation(async (req: OrderRequest) => {
        callCount++;
        if (callCount === 1) {
          throw new Error("模拟失败");
        }
        return {
          orderId: `order-${callCount}`,
          symbol: req.symbol,
          market: req.market,
          side: req.side,
          orderType: req.orderType,
          price: req.price,
          quantity: req.quantity,
          filledQty: 0,
          status: "submitted" as OrderStatus,
          brokerType: "paper" as const,
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
          submittedAt: new Date().toISOString(),
        };
      });

      const mgr = new OrderManager(cfg, broker, null);
      const svc = new BasketOrderService(mgr);

      const items: BasketItem[] = Array.from({ length: 5 }, (_, i) =>
        makeBasketItem(`60000${i}`, 100, 10),
      );

      // 1/5 = 20% < 50% threshold → no rollback
      const result = await svc.execute(items, {
        failureThreshold: 0.5,
        concurrency: 1,
      });

      expect(result.status).toBe("partial");
      expect(result.stats.failed).toBe(1);
      expect(result.stats.filled).toBe(4);
    });

    it("cancels submitted orders during rollback", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      let callCount = 0;

      vi.mocked(broker.placeOrder).mockImplementation(async (req: OrderRequest) => {
        callCount++;
        // First two succeed, rest fail
        if (callCount > 2) {
          throw new Error("模拟失败");
        }
        return {
          orderId: `order-${callCount}`,
          symbol: req.symbol,
          market: req.market,
          side: req.side,
          orderType: req.orderType,
          price: req.price,
          quantity: req.quantity,
          filledQty: 0,
          status: "submitted" as OrderStatus,
          brokerType: "paper" as const,
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
          submittedAt: new Date().toISOString(),
        };
      });

      const mgr = new OrderManager(cfg, broker, null);
      const svc = new BasketOrderService(mgr);

      const items: BasketItem[] = Array.from({ length: 4 }, (_, i) =>
        makeBasketItem(`60000${i}`, 100, 10),
      );

      // 2/4 = 50% ≥ 50% threshold → rollback. The 2 submitted orders should be cancelled.
      const result = await svc.execute(items, {
        failureThreshold: 0.5,
        concurrency: 1,
      });

      expect(result.status).toBe("rolled_back");
      // cancelOrder should have been called for the successfully submitted orders
      expect(broker.cancelOrder).toHaveBeenCalled();
    });
  });

  // ── 5. Progress Callbacks ─────────────────────────────────────

  describe("progress callbacks", () => {
    it("fires progress callback during execution", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);
      const svc = new BasketOrderService(mgr);

      const items: BasketItem[] = [
        makeBasketItem("600519", 100, 100),
        makeBasketItem("000858", 200, 50),
      ];

      const progressSnapshots: BasketOrder[] = [];
      const cb = (basket: Readonly<BasketOrder>) => {
        progressSnapshots.push(JSON.parse(JSON.stringify(basket)));
      };

      await svc.execute(items, undefined, cb);

      // Should have at least the initial and final callbacks
      expect(progressSnapshots.length).toBeGreaterThanOrEqual(2);
      // First should be "executing"
      expect(progressSnapshots[0].status).toBe("executing");
      // Last should be terminal
      const last = progressSnapshots[progressSnapshots.length - 1];
      expect(["completed", "partial", "rolled_back"]).toContain(last.status);
    });

    it("callback receives updated stats as children complete", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);
      const svc = new BasketOrderService(mgr);

      const items: BasketItem[] = [
        makeBasketItem("600519", 100, 100),
        makeBasketItem("000858", 200, 50),
        makeBasketItem("300750", 300, 80),
      ];

      const filledCounts: number[] = [];
      await svc.execute(items, { concurrency: 1 }, (basket) => {
        filledCounts.push(basket.stats.filled);
      });

      // With concurrency=1, filled count should increase monotonically
      for (let i = 1; i < filledCounts.length; i++) {
        expect(filledCounts[i]).toBeGreaterThanOrEqual(filledCounts[i - 1]);
      }
    });
  });

  // ── 6. Edge Cases ─────────────────────────────────────────────

  describe("edge cases", () => {
    it("handles empty basket", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);
      const svc = new BasketOrderService(mgr);

      const result = await svc.execute([]);

      expect(result.status).toBe("completed");
      expect(result.stats.totalChildren).toBe(0);
      expect(result.stats.filled).toBe(0);
      expect(broker.placeOrder).not.toHaveBeenCalled();
    });

    it("handles single item basket", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);
      const svc = new BasketOrderService(mgr);

      const items: BasketItem[] = [makeBasketItem("600519", 100, 100)];

      const result = await svc.execute(items);

      expect(result.status).toBe("completed");
      expect(result.stats.totalChildren).toBe(1);
      expect(result.stats.filled).toBe(1);
    });

    it("handles item with quantity that rounds to zero", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);
      const svc = new BasketOrderService(mgr);

      // 50 shares with lotSize 100 → rounds to 0 → filtered out
      const items: BasketItem[] = [makeBasketItem("600519", 50, 100)];

      const result = await svc.execute(items, {
        splitPolicy: { maxChildQty: 500, lotSize: 100 },
      });

      expect(result.status).toBe("completed");
      expect(result.stats.totalChildren).toBe(0);
      expect(broker.placeOrder).not.toHaveBeenCalled();
    });

    it("handles mixed buy and sell orders in same basket", async () => {
      const cfg = makeConfig();
      const positions: LivePosition[] = [
        {
          symbol: "000858",
          market: "cn_stock",
          quantity: 1000,
          availableQty: 1000,
          frozenQty: 0,
          todayBuyQty: 0,
          avgCost: 50,
          marketValue: 50_000,
          source: "local",
          lastSyncAt: new Date().toISOString(),
        },
      ];
      const broker = makeBrokerMock(makeAccount(), positions);
      const mgr = new OrderManager(cfg, broker, null);
      const svc = new BasketOrderService(mgr);

      const items: BasketItem[] = [
        makeBasketItem("600519", 100, 100), // buy
        {
          symbol: "000858",
          market: "cn_stock",
          side: "sell",
          orderType: "limit",
          quantity: 200,
          price: 60,
        },
      ];

      const result = await svc.execute(items);

      expect(result.status).toBe("completed");
      expect(result.stats.filled).toBe(2);
    });
  });

  // ── 7. Integration: splitting + concurrent execution ──────────

  describe("integration: split + execute", () => {
    it("splits large order and submits all children", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);
      const svc = new BasketOrderService(mgr);

      const items: BasketItem[] = [makeBasketItem("600519", 1000, 100)];

      const result = await svc.execute(items, {
        splitPolicy: { maxChildQty: 300, lotSize: 100 },
      });

      // 1000 → 300 + 300 + 300 + 100 = 4 children
      expect(result.stats.totalChildren).toBe(4);
      expect(result.stats.filled).toBe(4);
      expect(result.status).toBe("completed");
      expect(broker.placeOrder).toHaveBeenCalledTimes(4);
    });

    it("handles mixed split and non-split items together", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);
      const svc = new BasketOrderService(mgr);

      const items: BasketItem[] = [
        makeBasketItem("600519", 200, 100), // no split
        makeBasketItem("000858", 700, 50), // 300 + 300 + 100
      ];

      const result = await svc.execute(items, {
        splitPolicy: { maxChildQty: 300, lotSize: 100 },
        concurrency: 2,
      });

      expect(result.stats.totalChildren).toBe(4);
      expect(result.stats.filled).toBe(4);
      expect(result.status).toBe("completed");
    });

    it("rollback cancels children from split order", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      let callCount = 0;

      // First 2 children succeed, then all fail
      vi.mocked(broker.placeOrder).mockImplementation(async (req: OrderRequest) => {
        callCount++;
        if (callCount > 2) {
          throw new Error("模拟失败");
        }
        return {
          orderId: `order-${callCount}`,
          symbol: req.symbol,
          market: req.market,
          side: req.side,
          orderType: req.orderType,
          price: req.price,
          quantity: req.quantity,
          filledQty: 0,
          status: "submitted" as OrderStatus,
          brokerType: "paper" as const,
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
          submittedAt: new Date().toISOString(),
        };
      });

      const mgr = new OrderManager(cfg, broker, null);
      const svc = new BasketOrderService(mgr);

      // This will be split into 4 children (300+300+300+100)
      const items: BasketItem[] = [makeBasketItem("600519", 1000, 10)];

      const result = await svc.execute(items, {
        splitPolicy: { maxChildQty: 300, lotSize: 100 },
        failureThreshold: 0.5,
        concurrency: 1,
      });

      expect(result.status).toBe("rolled_back");
    });
  });

  // ── 8. Basket retrieval ───────────────────────────────────────

  describe("basket retrieval", () => {
    it("getBasket returns executed basket by ID", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);
      const svc = new BasketOrderService(mgr);

      const items: BasketItem[] = [makeBasketItem("600519", 100, 100)];
      const result = await svc.execute(items);

      const retrieved = svc.getBasket(result.basketId);
      expect(retrieved).toBeDefined();
      expect(retrieved!.basketId).toBe(result.basketId);
      expect(retrieved!.status).toBe("completed");
    });

    it("getBasket returns undefined for unknown ID", () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);
      const svc = new BasketOrderService(mgr);

      expect(svc.getBasket("nonexistent")).toBeUndefined();
    });

    it("listBaskets returns all baskets", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);
      const svc = new BasketOrderService(mgr);

      await svc.execute([makeBasketItem("600519", 100, 100)]);
      await svc.execute([makeBasketItem("000858", 200, 50)]);

      const baskets = svc.listBaskets();
      expect(baskets).toHaveLength(2);
    });
  });

  // ── 9. Broker exception handling ──────────────────────────────

  describe("broker exceptions", () => {
    it("handles broker.placeOrder throwing for individual children", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      let callCount = 0;

      vi.mocked(broker.placeOrder).mockImplementation(async (req: OrderRequest) => {
        callCount++;
        if (req.symbol === "000858") {
          throw new Error("Broker connection lost");
        }
        return {
          orderId: `order-${callCount}`,
          symbol: req.symbol,
          market: req.market,
          side: req.side,
          orderType: req.orderType,
          price: req.price,
          quantity: req.quantity,
          filledQty: 0,
          status: "submitted" as OrderStatus,
          brokerType: "paper" as const,
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
          submittedAt: new Date().toISOString(),
        };
      });

      const mgr = new OrderManager(cfg, broker, null);
      const svc = new BasketOrderService(mgr);

      const items: BasketItem[] = [
        makeBasketItem("600519", 100, 100),
        makeBasketItem("000858", 200, 50), // will throw
        makeBasketItem("300750", 300, 80),
      ];

      const result = await svc.execute(items, { failureThreshold: 0.8, concurrency: 1 });

      expect(result.stats.failed).toBe(1);
      expect(result.stats.filled).toBe(2);
      expect(result.status).toBe("partial");

      // The failed child should have a result with reason
      const failedChild = result.children.find((c) => c.request.symbol === "000858");
      expect(failedChild!.status).toBe("failed");
    });

    it("handles queryAccount failure gracefully in risk check", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      vi.mocked(broker.queryAccount).mockRejectedValue(new Error("Network error"));

      const mgr = new OrderManager(cfg, broker, null);
      const svc = new BasketOrderService(mgr);

      const items: BasketItem[] = [makeBasketItem("600519", 100, 100)];

      const result = await svc.execute(items, { failureThreshold: 1.0 });

      // All children should fail because account query fails
      expect(result.stats.failed).toBe(1);
      expect(result.stats.filled).toBe(0);
      const child = result.children[0];
      expect(child.status).toBe("failed");
      if (!child.result!.ok) {
        expect(child.result!.reason).toContain("无法获取账户信息");
      }
    });
  });

  // ── 10. Config defaults ───────────────────────────────────────

  describe("config defaults", () => {
    it("uses default config when no overrides provided", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);
      const svc = new BasketOrderService(mgr);

      const items: BasketItem[] = [makeBasketItem("600519", 100, 100)];

      const result = await svc.execute(items);

      expect(result.config.concurrency).toBe(5);
      expect(result.config.failureThreshold).toBe(0.5);
      expect(result.config.splitPolicy.lotSize).toBe(100);
    });

    it("allows partial config overrides", async () => {
      const cfg = makeConfig();
      const broker = makeBrokerMock();
      const mgr = new OrderManager(cfg, broker, null);
      const svc = new BasketOrderService(mgr);

      const items: BasketItem[] = [makeBasketItem("600519", 100, 100)];

      const result = await svc.execute(items, { concurrency: 2 });

      expect(result.config.concurrency).toBe(2);
      // Other defaults preserved
      expect(result.config.failureThreshold).toBe(0.5);
    });
  });
});
