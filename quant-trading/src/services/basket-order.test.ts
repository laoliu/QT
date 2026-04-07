/**
 * Tests for BasketOrderService — basket order execution scheduling & status aggregation.
 *
 * Covers:
 *   - createAndExecute: basic creation, concurrency control, failure policies
 *   - cancelBasket: cancel active/terminal baskets
 *   - handleOrderStatusChange: leg status mapping, basket aggregation
 *   - handleFill: fill qty accumulation, avgFillPrice VWAP, partial/filled transitions
 *   - aggregateBasketStatus: all status combinations
 *   - detectMarket: symbol → market type detection
 *   - persistence: load/save round-trip
 *   - edge cases: empty legs, missing OrderManager, unknown orderId
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { IBrokerAdapter } from "../broker/adapter.js";
import type { Order, OrderStatus, QuantTradingConfig } from "../types.js";
import { BasketOrderService, type CreateBasketRequest } from "./basket-order.js";
import type { OrderManager, SubmitResult } from "./order-manager.js";

// ── Helpers ──────────────────────────────────────────────────────

function makeCfg(dataDir: string): QuantTradingConfig {
  return { dataDir };
}

function makeOrder(overrides: Partial<Order> = {}): Order {
  return {
    orderId: overrides.orderId ?? `order-${Math.random().toString(36).slice(2, 8)}`,
    symbol: "600519",
    market: "cn_stock",
    side: "buy",
    orderType: "limit",
    quantity: 100,
    filledQty: 0,
    status: "submitted",
    brokerType: "paper",
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    ...overrides,
  };
}

function mockOrderManager(opts: {
  submitFn?: (req: unknown) => Promise<SubmitResult>;
  cancelFn?: (id: string) => Promise<void>;
  getOrdersFn?: () => Promise<Order[]>;
} = {}): OrderManager {
  return {
    submit: opts.submitFn ?? (async () => ({ ok: true, order: makeOrder() })),
    cancel: opts.cancelFn ?? (async () => {}),
    getOrders: opts.getOrdersFn ?? (async () => []),
  } as unknown as OrderManager;
}

function mockBroker(): IBrokerAdapter {
  const fillCbs: Array<(fill: { orderId: string; quantity: number; price: number }) => void> = [];
  const statusCbs: Array<(orderId: string, status: OrderStatus, order: Order) => void> = [];

  return {
    subscribeFills(cb: (fill: { orderId: string; quantity: number; price: number }) => void) {
      fillCbs.push(cb);
      return () => {
        const idx = fillCbs.indexOf(cb);
        if (idx >= 0) fillCbs.splice(idx, 1);
      };
    },
    subscribeOrderStatus(cb: (orderId: string, status: OrderStatus, order: Order) => void) {
      statusCbs.push(cb);
      return () => {
        const idx = statusCbs.indexOf(cb);
        if (idx >= 0) statusCbs.splice(idx, 1);
      };
    },
    // Expose for test triggering
    _fillCbs: fillCbs,
    _statusCbs: statusCbs,
  } as unknown as IBrokerAdapter;
}

// ── Test Suite ───────────────────────────────────────────────────

describe("BasketOrderService", () => {
  let tmpDir: string;
  let cfg: QuantTradingConfig;
  let service: BasketOrderService;
  let orderMgr: OrderManager;
  let notified: string[];

  beforeEach(async () => {
    tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "basket-test-"));
    cfg = makeCfg(tmpDir);
    notified = [];

    orderMgr = mockOrderManager();
    service = new BasketOrderService(cfg, () => orderMgr);
    service.setNotify((msg) => {
      notified.push(msg);
    });
    await service.load();
  });

  afterEach(async () => {
    service.destroy();
    await fs.rm(tmpDir, { recursive: true, force: true });
  });

  // ── createAndExecute ──────────────────────────────────────────

  describe("createAndExecute", () => {
    it("should create a basket with correct defaults and return immediately", async () => {
      const req: CreateBasketRequest = {
        name: "test-basket",
        legs: [
          { symbol: "600519", side: "buy", orderType: "limit", quantity: 100, price: 1800 },
          { symbol: "000858", side: "buy", orderType: "market", quantity: 200 },
        ],
      };

      const basket = await service.createAndExecute(req);

      expect(basket.basketId).toBeTruthy();
      expect(basket.name).toBe("test-basket");
      expect(basket.legs).toHaveLength(2);
      expect(basket.concurrency).toBe(5); // default
      expect(basket.failurePolicy).toBe("continue"); // default
      expect(basket.createdAt).toBeTruthy();
      expect(basket.legs[0].index).toBe(0);
      expect(basket.legs[0].symbol).toBe("600519");
      expect(basket.legs[0].side).toBe("buy");
      expect(basket.legs[0].quantity).toBe(100);
      expect(basket.legs[0].price).toBe(1800);
      expect(basket.legs[0].filledQty).toBe(0);
      expect(basket.legs[1].index).toBe(1);
      expect(basket.legs[1].price).toBeUndefined();
    });

    it("should use custom concurrency and failurePolicy", async () => {
      const req: CreateBasketRequest = {
        legs: [{ symbol: "600519", side: "buy", orderType: "market", quantity: 100 }],
        concurrency: 2,
        failurePolicy: "cancel_remaining",
        strategyId: "my-strategy",
      };

      const basket = await service.createAndExecute(req);

      expect(basket.concurrency).toBe(2);
      expect(basket.failurePolicy).toBe("cancel_remaining");
      expect(basket.strategyId).toBe("my-strategy");
    });

    it("should persist the basket to disk", async () => {
      await service.createAndExecute({
        legs: [{ symbol: "600519", side: "buy", orderType: "market", quantity: 100 }],
      });

      // Wait a tick for async execution to complete
      await new Promise((r) => setTimeout(r, 100));

      const filePath = path.join(tmpDir, "basket-orders.json");
      const data = JSON.parse(await fs.readFile(filePath, "utf-8"));
      expect(Array.isArray(data)).toBe(true);
      expect(data.length).toBe(1);
    });

    it("should submit all legs through OrderManager", async () => {
      const submitted: string[] = [];
      orderMgr = mockOrderManager({
        submitFn: async (req: unknown) => {
          const r = req as { symbol: string };
          submitted.push(r.symbol);
          return { ok: true, order: makeOrder({ symbol: r.symbol }) };
        },
      });
      service = new BasketOrderService(cfg, () => orderMgr);
      await service.load();

      await service.createAndExecute({
        legs: [
          { symbol: "600519", side: "buy", orderType: "market", quantity: 100 },
          { symbol: "000858", side: "sell", orderType: "limit", quantity: 200, price: 150 },
          { symbol: "300750", side: "buy", orderType: "market", quantity: 50 },
        ],
      });

      // Wait for async execution
      await new Promise((r) => setTimeout(r, 200));

      expect(submitted).toContain("600519");
      expect(submitted).toContain("000858");
      expect(submitted).toContain("300750");
    });

    it("should set leg status to rejected when submit returns ok=false", async () => {
      orderMgr = mockOrderManager({
        submitFn: async () => ({ ok: false, reason: "insufficient cash" }),
      });
      service = new BasketOrderService(cfg, () => orderMgr);
      await service.load();

      const basket = await service.createAndExecute({
        legs: [{ symbol: "600519", side: "buy", orderType: "market", quantity: 100 }],
      });

      await new Promise((r) => setTimeout(r, 200));

      const updated = service.getBasket(basket.basketId);
      expect(updated?.legs[0].status).toBe("rejected");
      expect(updated?.legs[0].errorMsg).toBe("insufficient cash");
    });

    it("should set leg status to failed when submit throws", async () => {
      orderMgr = mockOrderManager({
        submitFn: async () => {
          throw new Error("network error");
        },
      });
      service = new BasketOrderService(cfg, () => orderMgr);
      await service.load();

      const basket = await service.createAndExecute({
        legs: [{ symbol: "600519", side: "buy", orderType: "market", quantity: 100 }],
      });

      await new Promise((r) => setTimeout(r, 200));

      const updated = service.getBasket(basket.basketId);
      expect(updated?.legs[0].status).toBe("failed");
      expect(updated?.legs[0].errorMsg).toBe("network error");
    });

    it("should mark basket as failed when OrderManager is null", async () => {
      service = new BasketOrderService(cfg, () => null as unknown as OrderManager);
      await service.load();

      const basket = await service.createAndExecute({
        legs: [{ symbol: "600519", side: "buy", orderType: "market", quantity: 100 }],
      });

      await new Promise((r) => setTimeout(r, 200));

      const updated = service.getBasket(basket.basketId);
      expect(updated?.status).toBe("failed");
      expect(updated?.legs[0].status).toBe("failed");
      expect(updated?.legs[0].errorMsg).toBe("交易引擎未启动");
    });
  });

  // ── Concurrency control ───────────────────────────────────────

  describe("concurrency control", () => {
    it("should limit concurrent leg submissions", async () => {
      let maxConcurrent = 0;
      let currentConcurrent = 0;

      orderMgr = mockOrderManager({
        submitFn: async (req: unknown) => {
          currentConcurrent++;
          maxConcurrent = Math.max(maxConcurrent, currentConcurrent);
          await new Promise((r) => setTimeout(r, 50));
          currentConcurrent--;
          return { ok: true, order: makeOrder({ symbol: (req as { symbol: string }).symbol }) };
        },
      });
      service = new BasketOrderService(cfg, () => orderMgr);
      await service.load();

      await service.createAndExecute({
        legs: Array.from({ length: 10 }, (_, i) => ({
          symbol: `SYM${i}`,
          side: "buy" as const,
          orderType: "market" as const,
          quantity: 100,
        })),
        concurrency: 3,
      });

      await new Promise((r) => setTimeout(r, 1000));

      expect(maxConcurrent).toBeLessThanOrEqual(3);
    });
  });

  // ── Failure policy ────────────────────────────────────────────

  describe("failure policy", () => {
    it("continue: should keep submitting after leg failure", async () => {
      let callCount = 0;
      orderMgr = mockOrderManager({
        submitFn: async () => {
          callCount++;
          if (callCount === 1) return { ok: false, reason: "rejected" };
          return { ok: true, order: makeOrder() };
        },
      });
      service = new BasketOrderService(cfg, () => orderMgr);
      await service.load();

      const basket = await service.createAndExecute({
        legs: [
          { symbol: "A", side: "buy", orderType: "market", quantity: 100 },
          { symbol: "B", side: "buy", orderType: "market", quantity: 100 },
          { symbol: "C", side: "buy", orderType: "market", quantity: 100 },
        ],
        failurePolicy: "continue",
        concurrency: 1,
      });

      await new Promise((r) => setTimeout(r, 300));

      expect(callCount).toBe(3); // all 3 submitted
      const updated = service.getBasket(basket.basketId);
      expect(updated?.legs[0].status).toBe("rejected");
      // legs 1 and 2 should have been submitted
    });

    it("cancel_remaining: should cancel pending legs after failure", async () => {
      let callCount = 0;
      orderMgr = mockOrderManager({
        submitFn: async () => {
          callCount++;
          if (callCount === 1) return { ok: false, reason: "rejected" };
          return { ok: true, order: makeOrder() };
        },
      });
      service = new BasketOrderService(cfg, () => orderMgr);
      await service.load();

      const basket = await service.createAndExecute({
        legs: [
          { symbol: "A", side: "buy", orderType: "market", quantity: 100 },
          { symbol: "B", side: "buy", orderType: "market", quantity: 100 },
          { symbol: "C", side: "buy", orderType: "market", quantity: 100 },
        ],
        failurePolicy: "cancel_remaining",
        concurrency: 1,
      });

      await new Promise((r) => setTimeout(r, 300));

      // First leg failed → remaining should be cancelled
      expect(callCount).toBe(1);
      const updated = service.getBasket(basket.basketId);
      expect(updated?.legs[0].status).toBe("rejected");
      expect(updated?.legs[1].status).toBe("cancelled");
      expect(updated?.legs[2].status).toBe("cancelled");
    });
  });

  // ── cancelBasket ──────────────────────────────────────────────

  describe("cancelBasket", () => {
    it("should return false for non-existent basket", async () => {
      const result = await service.cancelBasket("non-existent");
      expect(result).toBe(false);
    });

    it("should return false for already completed basket", async () => {
      orderMgr = mockOrderManager({
        submitFn: async () => ({
          ok: true,
          order: makeOrder({ status: "filled", filledQty: 100 }),
        }),
      });
      service = new BasketOrderService(cfg, () => orderMgr);
      await service.load();

      const basket = await service.createAndExecute({
        legs: [{ symbol: "600519", side: "buy", orderType: "market", quantity: 100 }],
      });

      await new Promise((r) => setTimeout(r, 200));

      // Manually set to completed for testing
      const b = service.getBasket(basket.basketId);
      if (b) b.status = "completed";

      const result = await service.cancelBasket(basket.basketId);
      expect(result).toBe(false);
    });

    it("should cancel executing basket and mark pending legs as cancelled", async () => {
      // Create a basket but ensure legs are in different states
      const orderId = "order-to-cancel";
      orderMgr = mockOrderManager({
        submitFn: async () => ({
          ok: true,
          order: makeOrder({ orderId, status: "submitted" }),
        }),
        cancelFn: async () => {},
      });
      service = new BasketOrderService(cfg, () => orderMgr);
      await service.load();

      const basket = await service.createAndExecute({
        legs: [
          { symbol: "600519", side: "buy", orderType: "market", quantity: 100 },
        ],
      });

      await new Promise((r) => setTimeout(r, 200));

      const b = service.getBasket(basket.basketId);
      if (b) b.status = "executing"; // ensure it's not terminal

      const result = await service.cancelBasket(basket.basketId);
      expect(result).toBe(true);

      const updated = service.getBasket(basket.basketId);
      expect(updated?.status).toBe("cancelled");
      expect(updated?.completedAt).toBeTruthy();
    });

    it("should return false for terminal statuses", async () => {
      const terminalStatuses = ["completed", "failed", "cancelled", "partial_filled"] as const;

      for (const status of terminalStatuses) {
        orderMgr = mockOrderManager();
        service = new BasketOrderService(cfg, () => orderMgr);
        await service.load();

        const basket = await service.createAndExecute({
          legs: [{ symbol: "600519", side: "buy", orderType: "market", quantity: 100 }],
        });
        await new Promise((r) => setTimeout(r, 200));

        const b = service.getBasket(basket.basketId);
        if (b) b.status = status;

        const result = await service.cancelBasket(basket.basketId);
        expect(result).toBe(false);
      }
    });
  });

  // ── getBasket / listBaskets ───────────────────────────────────

  describe("getBasket / listBaskets", () => {
    it("should return undefined for unknown basketId", () => {
      expect(service.getBasket("unknown")).toBeUndefined();
    });

    it("should list all baskets", async () => {
      await service.createAndExecute({
        name: "b1",
        legs: [{ symbol: "A", side: "buy", orderType: "market", quantity: 100 }],
      });
      await service.createAndExecute({
        name: "b2",
        legs: [{ symbol: "B", side: "sell", orderType: "limit", quantity: 50, price: 10 }],
      });

      await new Promise((r) => setTimeout(r, 200));

      const all = service.listBaskets();
      expect(all).toHaveLength(2);
    });

    it("should filter baskets by status", async () => {
      orderMgr = mockOrderManager({
        submitFn: async () => ({
          ok: true,
          order: makeOrder({ status: "filled", filledQty: 100 }),
        }),
      });
      service = new BasketOrderService(cfg, () => orderMgr);
      await service.load();

      await service.createAndExecute({
        legs: [{ symbol: "A", side: "buy", orderType: "market", quantity: 100 }],
      });

      await new Promise((r) => setTimeout(r, 200));

      // One basket should exist with some status
      const all = service.listBaskets();
      expect(all.length).toBeGreaterThanOrEqual(1);

      const pending = service.listBaskets("pending");
      // pending filter should return baskets matching that status
      for (const b of pending) {
        expect(b.status).toBe("pending");
      }
    });
  });

  // ── handleOrderStatusChange ───────────────────────────────────

  describe("handleOrderStatusChange", () => {
    it("should ignore unknown orderId", async () => {
      // Should not throw
      await service.handleOrderStatusChange("unknown-order", "filled");
    });

    it("should update leg status from order status", async () => {
      const orderId = `order-${Date.now()}`;
      orderMgr = mockOrderManager({
        submitFn: async () => ({
          ok: true,
          order: makeOrder({ orderId, status: "submitted" }),
        }),
        getOrdersFn: async () => [
          makeOrder({ orderId, status: "filled", filledQty: 100, avgFillPrice: 1800 }),
        ],
      });
      service = new BasketOrderService(cfg, () => orderMgr);
      await service.load();

      const basket = await service.createAndExecute({
        legs: [{ symbol: "600519", side: "buy", orderType: "limit", quantity: 100, price: 1800 }],
      });

      await new Promise((r) => setTimeout(r, 200));

      // Now simulate status change to "filled"
      await service.handleOrderStatusChange(orderId, "filled");

      const updated = service.getBasket(basket.basketId);
      expect(updated?.legs[0].status).toBe("filled");
    });

    it("should map 'expired' order status to 'cancelled' leg status", async () => {
      const orderId = `order-expired-${Date.now()}`;
      orderMgr = mockOrderManager({
        submitFn: async () => ({
          ok: true,
          order: makeOrder({ orderId, status: "submitted" }),
        }),
        getOrdersFn: async () => [],
      });
      service = new BasketOrderService(cfg, () => orderMgr);
      await service.load();

      const basket = await service.createAndExecute({
        legs: [{ symbol: "600519", side: "buy", orderType: "limit", quantity: 100, price: 1800 }],
      });

      await new Promise((r) => setTimeout(r, 200));

      await service.handleOrderStatusChange(orderId, "expired");

      const updated = service.getBasket(basket.basketId);
      expect(updated?.legs[0].status).toBe("cancelled"); // expired → cancelled
    });

    it("should aggregate basket status after leg status change", async () => {
      const orderId1 = "ord-1";
      const orderId2 = "ord-2";
      let submitCount = 0;
      orderMgr = mockOrderManager({
        submitFn: async () => {
          submitCount++;
          const oid = submitCount === 1 ? orderId1 : orderId2;
          return { ok: true, order: makeOrder({ orderId: oid, status: "submitted" }) };
        },
        getOrdersFn: async () => [],
      });
      service = new BasketOrderService(cfg, () => orderMgr);
      await service.load();

      const basket = await service.createAndExecute({
        legs: [
          { symbol: "600519", side: "buy", orderType: "market", quantity: 100 },
          { symbol: "000858", side: "buy", orderType: "market", quantity: 200 },
        ],
        concurrency: 1,
      });

      await new Promise((r) => setTimeout(r, 300));

      // Fill first leg only
      await service.handleOrderStatusChange(orderId1, "filled");

      const updated = service.getBasket(basket.basketId);
      // One filled, one still submitted → partial or executing
      expect(["partial", "executing"]).toContain(updated?.status);
    });
  });

  // ── handleFill ────────────────────────────────────────────────

  describe("handleFill", () => {
    it("should ignore unknown orderId", async () => {
      await service.handleFill("unknown-order", 50, 100);
      // Should not throw
    });

    it("should accumulate fill quantity correctly", async () => {
      const orderId = `fill-order-${Date.now()}`;
      orderMgr = mockOrderManager({
        submitFn: async () => ({
          ok: true,
          order: makeOrder({ orderId, status: "submitted" }),
        }),
      });
      service = new BasketOrderService(cfg, () => orderMgr);
      await service.load();

      const basket = await service.createAndExecute({
        legs: [{ symbol: "600519", side: "buy", orderType: "limit", quantity: 100, price: 1800 }],
      });

      await new Promise((r) => setTimeout(r, 200));

      // First partial fill
      await service.handleFill(orderId, 30, 1795);
      let updated = service.getBasket(basket.basketId);
      expect(updated?.legs[0].filledQty).toBe(30);
      expect(updated?.legs[0].avgFillPrice).toBe(1795);
      expect(updated?.legs[0].status).toBe("partial");

      // Second partial fill
      await service.handleFill(orderId, 70, 1805);
      updated = service.getBasket(basket.basketId);
      expect(updated?.legs[0].filledQty).toBe(100);
      // VWAP: (1795*30 + 1805*70) / 100 = 180200/100 = 1802
      expect(updated?.legs[0].avgFillPrice).toBeCloseTo(1802, 2);
      expect(updated?.legs[0].status).toBe("filled");
    });

    it("should mark leg as filled when filledQty >= quantity", async () => {
      const orderId = `fill-full-${Date.now()}`;
      orderMgr = mockOrderManager({
        submitFn: async () => ({
          ok: true,
          order: makeOrder({ orderId, status: "submitted" }),
        }),
      });
      service = new BasketOrderService(cfg, () => orderMgr);
      await service.load();

      const basket = await service.createAndExecute({
        legs: [{ symbol: "600519", side: "buy", orderType: "market", quantity: 100 }],
      });

      await new Promise((r) => setTimeout(r, 200));

      await service.handleFill(orderId, 100, 1800);

      const updated = service.getBasket(basket.basketId);
      expect(updated?.legs[0].status).toBe("filled");
      expect(updated?.legs[0].filledQty).toBe(100);
    });

    it("should compute avgFillPrice correctly for first fill", async () => {
      const orderId = `first-fill-${Date.now()}`;
      orderMgr = mockOrderManager({
        submitFn: async () => ({
          ok: true,
          order: makeOrder({ orderId, status: "submitted" }),
        }),
      });
      service = new BasketOrderService(cfg, () => orderMgr);
      await service.load();

      const basket = await service.createAndExecute({
        legs: [{ symbol: "600519", side: "buy", orderType: "market", quantity: 100 }],
      });

      await new Promise((r) => setTimeout(r, 200));

      await service.handleFill(orderId, 50, 1800);

      const updated = service.getBasket(basket.basketId);
      expect(updated?.legs[0].avgFillPrice).toBe(1800); // first fill: just the fill price
    });
  });

  // ── aggregateBasketStatus ─────────────────────────────────────

  describe("aggregateBasketStatus (via handleOrderStatusChange/handleFill)", () => {
    it("should set completed when all legs filled", async () => {
      const orderId1 = "agg-ord-1";
      const orderId2 = "agg-ord-2";
      let submitCount = 0;
      orderMgr = mockOrderManager({
        submitFn: async () => {
          submitCount++;
          const oid = submitCount === 1 ? orderId1 : orderId2;
          return { ok: true, order: makeOrder({ orderId: oid }) };
        },
        getOrdersFn: async () => [],
      });
      service = new BasketOrderService(cfg, () => orderMgr);
      await service.load();

      const basket = await service.createAndExecute({
        legs: [
          { symbol: "A", side: "buy", orderType: "market", quantity: 100 },
          { symbol: "B", side: "buy", orderType: "market", quantity: 100 },
        ],
        concurrency: 1,
      });

      await new Promise((r) => setTimeout(r, 300));

      await service.handleFill(orderId1, 100, 10);
      await service.handleFill(orderId2, 100, 20);

      const updated = service.getBasket(basket.basketId);
      expect(updated?.status).toBe("completed");
    });

    it("should set failed when all legs failed/cancelled", async () => {
      const orderId1 = "fail-1";
      const orderId2 = "fail-2";
      let submitCount = 0;
      orderMgr = mockOrderManager({
        submitFn: async () => {
          submitCount++;
          const oid = submitCount === 1 ? orderId1 : orderId2;
          return { ok: true, order: makeOrder({ orderId: oid }) };
        },
        getOrdersFn: async () => [],
      });
      service = new BasketOrderService(cfg, () => orderMgr);
      await service.load();

      const basket = await service.createAndExecute({
        legs: [
          { symbol: "A", side: "buy", orderType: "market", quantity: 100 },
          { symbol: "B", side: "buy", orderType: "market", quantity: 100 },
        ],
        concurrency: 1,
      });

      await new Promise((r) => setTimeout(r, 300));

      await service.handleOrderStatusChange(orderId1, "rejected");
      await service.handleOrderStatusChange(orderId2, "cancelled");

      const updated = service.getBasket(basket.basketId);
      expect(updated?.status).toBe("failed");
    });

    it("should set partial_filled when some legs filled and rest failed", async () => {
      const orderId1 = "pf-1";
      const orderId2 = "pf-2";
      let submitCount = 0;
      orderMgr = mockOrderManager({
        submitFn: async () => {
          submitCount++;
          const oid = submitCount === 1 ? orderId1 : orderId2;
          return { ok: true, order: makeOrder({ orderId: oid }) };
        },
        getOrdersFn: async () => [],
      });
      service = new BasketOrderService(cfg, () => orderMgr);
      await service.load();

      const basket = await service.createAndExecute({
        legs: [
          { symbol: "A", side: "buy", orderType: "market", quantity: 100 },
          { symbol: "B", side: "buy", orderType: "market", quantity: 100 },
        ],
        concurrency: 1,
      });

      await new Promise((r) => setTimeout(r, 300));

      await service.handleFill(orderId1, 100, 10);
      await service.handleOrderStatusChange(orderId2, "rejected");

      const updated = service.getBasket(basket.basketId);
      expect(updated?.status).toBe("partial_filled");
    });
  });

  // ── Broker subscription ───────────────────────────────────────

  describe("subscribeToBroker / unsubscribeBroker", () => {
    it("should wire broker callbacks for fills and order status", async () => {
      const broker = mockBroker();
      service.subscribeToBroker(broker);

      const orderId = `broker-sub-${Date.now()}`;
      orderMgr = mockOrderManager({
        submitFn: async () => ({
          ok: true,
          order: makeOrder({ orderId }),
        }),
        getOrdersFn: async () => [],
      });
      service = new BasketOrderService(cfg, () => orderMgr);
      service.subscribeToBroker(broker);
      await service.load();

      const basket = await service.createAndExecute({
        legs: [{ symbol: "600519", side: "buy", orderType: "market", quantity: 100 }],
      });

      await new Promise((r) => setTimeout(r, 200));

      // Trigger broker fill callback
      for (const cb of (broker as unknown as { _fillCbs: Function[] })._fillCbs) {
        cb({ orderId, quantity: 100, price: 1800 });
      }

      await new Promise((r) => setTimeout(r, 100));

      const updated = service.getBasket(basket.basketId);
      expect(updated?.legs[0].filledQty).toBe(100);
    });

    it("should clean up previous subscriptions when called again", () => {
      const broker = mockBroker();
      service.subscribeToBroker(broker);

      // First subscription adds callbacks
      expect((broker as unknown as { _fillCbs: unknown[] })._fillCbs.length).toBe(1);

      // Re-subscribing should unsubscribe first then subscribe again
      service.subscribeToBroker(broker);
      expect((broker as unknown as { _fillCbs: unknown[] })._fillCbs.length).toBe(1);
    });

    it("should remove all callbacks on unsubscribeBroker", () => {
      const broker = mockBroker();
      service.subscribeToBroker(broker);
      expect((broker as unknown as { _fillCbs: unknown[] })._fillCbs.length).toBe(1);

      service.unsubscribeBroker();
      expect((broker as unknown as { _fillCbs: unknown[] })._fillCbs.length).toBe(0);
    });
  });

  // ── Persistence / load ────────────────────────────────────────

  describe("persistence", () => {
    it("should load baskets from disk on startup", async () => {
      // Create a basket and let it persist
      await service.createAndExecute({
        name: "persistent-basket",
        legs: [{ symbol: "600519", side: "buy", orderType: "market", quantity: 100 }],
      });

      await new Promise((r) => setTimeout(r, 200));

      // Create a new service instance and load
      const service2 = new BasketOrderService(cfg, () => orderMgr);
      await service2.load();

      const all = service2.listBaskets();
      expect(all.length).toBeGreaterThanOrEqual(1);
      expect(all[0].name).toBe("persistent-basket");

      service2.destroy();
    });

    it("should handle missing data file gracefully", async () => {
      const emptyCfg = makeCfg(path.join(tmpDir, "nonexistent"));
      const svc = new BasketOrderService(emptyCfg, () => orderMgr);
      await svc.load(); // should not throw
      expect(svc.listBaskets()).toHaveLength(0);
      svc.destroy();
    });

    it("should rebuild orderToLeg index on load for status routing", async () => {
      const orderId = `persist-order-${Date.now()}`;
      orderMgr = mockOrderManager({
        submitFn: async () => ({
          ok: true,
          order: makeOrder({ orderId }),
        }),
        getOrdersFn: async () => [],
      });
      service = new BasketOrderService(cfg, () => orderMgr);
      await service.load();

      const basket = await service.createAndExecute({
        legs: [{ symbol: "600519", side: "buy", orderType: "market", quantity: 100 }],
      });

      await new Promise((r) => setTimeout(r, 200));

      // Load new instance
      const service2 = new BasketOrderService(cfg, () => orderMgr);
      await service2.load();

      // Should be able to route status changes to the loaded basket
      await service2.handleOrderStatusChange(orderId, "filled");

      const updated = service2.getBasket(basket.basketId);
      expect(updated?.legs[0].status).toBe("filled");

      service2.destroy();
    });
  });

  // ── detectMarket (via executeBasket signalId) ─────────────────

  describe("detectMarket (internal, tested via execution)", () => {
    it("should detect cn_stock for 6-digit codes", async () => {
      const submitted: { market: string }[] = [];
      orderMgr = mockOrderManager({
        submitFn: async (req: unknown) => {
          submitted.push(req as { market: string });
          return { ok: true, order: makeOrder() };
        },
      });
      service = new BasketOrderService(cfg, () => orderMgr);
      await service.load();

      await service.createAndExecute({
        legs: [{ symbol: "600519", side: "buy", orderType: "market", quantity: 100 }],
      });

      await new Promise((r) => setTimeout(r, 200));

      expect(submitted[0]?.market).toBe("cn_stock");
    });

    it("should detect us_stock for alphabetic symbols", async () => {
      const submitted: { market: string }[] = [];
      orderMgr = mockOrderManager({
        submitFn: async (req: unknown) => {
          submitted.push(req as { market: string });
          return { ok: true, order: makeOrder() };
        },
      });
      service = new BasketOrderService(cfg, () => orderMgr);
      await service.load();

      await service.createAndExecute({
        legs: [{ symbol: "AAPL", side: "buy", orderType: "market", quantity: 100 }],
      });

      await new Promise((r) => setTimeout(r, 200));

      expect(submitted[0]?.market).toBe("us_stock");
    });

    it("should detect fund for .OF suffix", async () => {
      const submitted: { market: string }[] = [];
      orderMgr = mockOrderManager({
        submitFn: async (req: unknown) => {
          submitted.push(req as { market: string });
          return { ok: true, order: makeOrder() };
        },
      });
      service = new BasketOrderService(cfg, () => orderMgr);
      await service.load();

      await service.createAndExecute({
        legs: [{ symbol: "510050.OF", side: "buy", orderType: "market", quantity: 100 }],
      });

      await new Promise((r) => setTimeout(r, 200));

      expect(submitted[0]?.market).toBe("fund");
    });

    it("should detect futures for known product codes", async () => {
      const submitted: { market: string }[] = [];
      orderMgr = mockOrderManager({
        submitFn: async (req: unknown) => {
          submitted.push(req as { market: string });
          return { ok: true, order: makeOrder() };
        },
      });
      service = new BasketOrderService(cfg, () => orderMgr);
      await service.load();

      await service.createAndExecute({
        legs: [{ symbol: "IF2401", side: "buy", orderType: "market", quantity: 1 }],
      });

      await new Promise((r) => setTimeout(r, 200));

      expect(submitted[0]?.market).toBe("futures");
    });

    it("should detect hk_stock for .HK suffix", async () => {
      const submitted: { market: string }[] = [];
      orderMgr = mockOrderManager({
        submitFn: async (req: unknown) => {
          submitted.push(req as { market: string });
          return { ok: true, order: makeOrder() };
        },
      });
      service = new BasketOrderService(cfg, () => orderMgr);
      await service.load();

      await service.createAndExecute({
        legs: [{ symbol: "0700.HK", side: "buy", orderType: "market", quantity: 100 }],
      });

      await new Promise((r) => setTimeout(r, 200));

      expect(submitted[0]?.market).toBe("hk_stock");
    });
  });

  // ── Empty legs edge case ──────────────────────────────────────

  describe("edge cases", () => {
    it("should handle basket with empty legs (aggregation → completed)", async () => {
      // Directly test with 0 legs via the aggregation path
      const basket = await service.createAndExecute({
        name: "empty-basket",
        legs: [],
      });

      await new Promise((r) => setTimeout(r, 200));

      const updated = service.getBasket(basket.basketId);
      expect(updated?.status).toBe("completed");
      expect(updated?.legs).toHaveLength(0);
    });

    it("should send notification on basket completion", async () => {
      orderMgr = mockOrderManager({
        submitFn: async () => ({
          ok: true,
          order: makeOrder({ status: "filled", filledQty: 100 }),
        }),
      });
      service = new BasketOrderService(cfg, () => orderMgr);
      service.setNotify((msg) => {
        notified.push(msg);
      });
      await service.load();

      await service.createAndExecute({
        name: "notify-test",
        legs: [{ symbol: "600519", side: "buy", orderType: "market", quantity: 100 }],
      });

      await new Promise((r) => setTimeout(r, 300));

      // Should have received at least one notification
      expect(notified.some((m) => m.includes("篮子订单"))).toBe(true);
    });
  });
});

// ── basket-order tool tests ─────────────────────────────────────

describe("createBasketOrderTool", () => {
  let tmpDir: string;
  let cfg: QuantTradingConfig;
  let service: BasketOrderService;
  let tool: ReturnType<typeof import("../tools/basket-order.js").createBasketOrderTool>;

  beforeEach(async () => {
    const { createBasketOrderTool } = await import("../tools/basket-order.js");

    tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "basket-tool-test-"));
    cfg = makeCfg(tmpDir);

    const orderMgr = mockOrderManager();
    service = new BasketOrderService(cfg, () => orderMgr);
    await service.load();

    const fakeApi = {} as unknown as import("openclaw/plugin-sdk").OpenClawPluginApi;
    tool = createBasketOrderTool(fakeApi, service);
  });

  afterEach(async () => {
    service.destroy();
    await fs.rm(tmpDir, { recursive: true, force: true });
  });

  it("should have correct tool metadata", () => {
    expect(tool.name).toBe("basket_order");
    expect(tool.description).toBeTruthy();
    expect(tool.parameters).toBeTruthy();
  });

  it("create action: should require legs", async () => {
    const result = await tool.execute({ action: "create" });
    expect(result).toContain("请提供订单腿列表");
  });

  it("create action: should reject empty legs", async () => {
    const result = await tool.execute({ action: "create", legs: [] });
    expect(result).toContain("请提供订单腿列表");
  });

  it("create action: should create basket", async () => {
    const result = await tool.execute({
      action: "create",
      name: "tool-basket",
      legs: [
        { symbol: "600519", side: "buy", order_type: "limit", quantity: 100, price: 1800 },
      ],
      concurrency: 3,
      failure_policy: "cancel_remaining",
    });

    expect(result).toContain("篮子订单已创建");
    expect(result).toContain("tool-basket");
  });

  it("list action: should show empty message", async () => {
    const result = await tool.execute({ action: "list" });
    expect(result).toContain("暂无篮子订单");
  });

  it("list action: should filter by status", async () => {
    const result = await tool.execute({ action: "list", status: "executing" });
    expect(result).toContain("没有");
  });

  it("get action: should require basket_id", async () => {
    const result = await tool.execute({ action: "get" });
    expect(result).toContain("请提供篮子ID");
  });

  it("get action: should return not found for unknown id", async () => {
    const result = await tool.execute({ action: "get", basket_id: "unknown" });
    expect(result).toContain("不存在");
  });

  it("cancel action: should require basket_id", async () => {
    const result = await tool.execute({ action: "cancel" });
    expect(result).toContain("请提供篮子ID");
  });

  it("cancel action: should return failure for non-existent basket", async () => {
    const result = await tool.execute({ action: "cancel", basket_id: "unknown" });
    expect(result).toContain("取消失败");
  });

  it("should handle unknown action", async () => {
    const result = await tool.execute({ action: "unknown" });
    expect(result).toContain("未知操作");
  });

  it("list action: should show baskets after creation", async () => {
    await tool.execute({
      action: "create",
      name: "list-test",
      legs: [{ symbol: "600519", side: "buy", order_type: "market", quantity: 100 }],
    });

    const result = await tool.execute({ action: "list" });
    expect(result).toContain("篮子订单列表");
  });

  it("get action: should show basket details", async () => {
    const createResult = await tool.execute({
      action: "create",
      name: "detail-test",
      legs: [{ symbol: "600519", side: "buy", order_type: "limit", quantity: 100, price: 1800 }],
    });

    // Extract basket ID from create result
    const idMatch = createResult.match(/ID: ([a-f0-9-]+)/);
    if (idMatch) {
      const result = await tool.execute({ action: "get", basket_id: idMatch[1] });
      expect(result).toContain("detail-test");
      expect(result).toContain("600519");
    }
  });
});
