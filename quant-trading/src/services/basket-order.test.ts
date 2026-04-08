/**
 * Unit tests for BasketOrderService 66covers basket lifecycle:
 *   create →add items →submit →refresh →cancel →delete
 *
 * Uses a mock OrderManager and temp filesystem for JSON persistence.
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { QuantTradingConfig } from "../types.js";
import { BasketOrderService } from "./basket-order.js";
import type { OrderManager, SubmitResult } from "./order-manager.js";

// ── Fixtures ────────────────────────────────────────────────────

let tmpDir: string;

function makeConfig(): QuantTradingConfig {
  return {
    dataDir: tmpDir,
    dataProvider: "demo",
    backtestEngine: "builtin",
    tradingAccount: { mode: "paper", broker: "paper" },
  };
}

let submitCallCount: number;

function makeMockOrderMgr(overrides?: {
  submitFn?: (req: unknown) => Promise<SubmitResult>;
  cancelFn?: (id: string) => Promise<void>;
  getOrdersFn?: (filter?: unknown) => Promise<unknown[]>;
}): OrderManager {
  submitCallCount = 0;
  return {
    submit: overrides?.submitFn ??
      (async () => {
        submitCallCount++;
        return {
          ok: true,
          order: {
            orderId: `order-${submitCallCount}`,
            status: "submitted",
            symbol: "",
            market: "cn_stock",
            side: "buy",
            orderType: "limit",
            quantity: 100,
            filledQty: 0,
            brokerType: "paper",
            createdAt: new Date().toISOString(),
            updatedAt: new Date().toISOString(),
          },
        } as SubmitResult;
      }),
    cancel: overrides?.cancelFn ?? (async () => {}),
    getOrders: overrides?.getOrdersFn ?? (async () => []),
  } as unknown as OrderManager;
}

// ── Setup / teardown ────────────────────────────────────────────

beforeEach(async () => {
  tmpDir = path.join(os.tmpdir(), `basket-test-${Date.now()}-${Math.random().toString(36).slice(2)}`);
  await fs.mkdir(tmpDir, { recursive: true });
});

afterEach(async () => {
  await fs.rm(tmpDir, { recursive: true, force: true });
});

// ── Tests ───────────────────────────────────────────────────────

describe("BasketOrderService", () => {
  it("creates a basket in draft status", async () => {
    const svc = new BasketOrderService(makeConfig(), () => null);
    await svc.load();

    const basket = await svc.create({
      name: "测试篮子",
      items: [
        { symbol: "600519", side: "buy", orderType: "limit", quantity: 100, price: 1800 },
        { symbol: "000858", side: "buy", orderType: "limit", quantity: 200, price: 120 },
      ],
    });

    expect(basket.status).toBe("draft");
    expect(basket.totalItems).toBe(2);
    expect(basket.items[0].symbol).toBe("600519");
    expect(basket.items[1].symbol).toBe("000858");
    expect(basket.basketId).toBeTruthy();
  });

  it("rejects creating an empty basket", async () => {
    const svc = new BasketOrderService(makeConfig(), () => null);
    await svc.load();

    await expect(svc.create({ name: "空篮", items: [] })).rejects.toThrow("至少需要一个标");
  });

  it("adds items to a draft basket", async () => {
    const svc = new BasketOrderService(makeConfig(), () => null);
    await svc.load();

    const basket = await svc.create({
      name: "扩充篮子",
      items: [{ symbol: "600519", side: "buy", orderType: "limit", quantity: 100, price: 1800 }],
    });

    const updated = await svc.addItems(basket.basketId, [
      { symbol: "601398", side: "buy", orderType: "limit", quantity: 500, price: 5.5 },
    ]);

    expect(updated.totalItems).toBe(2);
    expect(updated.items[1].symbol).toBe("601398");
  });

  it("removes an item from a draft basket", async () => {
    const svc = new BasketOrderService(makeConfig(), () => null);
    await svc.load();

    const basket = await svc.create({
      name: "删减篮子",
      items: [
        { symbol: "600519", side: "buy", orderType: "limit", quantity: 100, price: 1800 },
        { symbol: "000858", side: "buy", orderType: "limit", quantity: 200, price: 120 },
      ],
    });

    const updated = await svc.removeItem(basket.basketId, basket.items[0].itemId);
    expect(updated.totalItems).toBe(1);
    expect(updated.items[0].symbol).toBe("000858");
  });

  it("submits a basket and tracks results", async () => {
    const mgr = makeMockOrderMgr();
    const svc = new BasketOrderService(makeConfig(), () => mgr);
    await svc.load();

    const basket = await svc.create({
      name: "提交篮子",
      items: [
        { symbol: "600519", side: "buy", orderType: "limit", quantity: 100, price: 1800 },
        { symbol: "000858", side: "buy", orderType: "limit", quantity: 200, price: 120 },
      ],
      interOrderDelayMs: 0, // no delay in tests
    });

    const result = await svc.submit(basket.basketId);

    expect(result.status).toBe("active");
    expect(result.submittedCount).toBe(2);
    expect(result.items[0].status).toBe("submitted");
    expect(result.items[1].status).toBe("submitted");
    expect(result.items[0].orderId).toBeTruthy();
    expect(submitCallCount).toBe(2);
  });

  it("handles partial submission failures", async () => {
    let callCount = 0;
    const mgr = makeMockOrderMgr({
      submitFn: async () => {
        callCount++;
        if (callCount === 2) {
          return { ok: false, reason: "1218: 超过单笔限额" } as SubmitResult;
        }
        return {
          ok: true,
          order: {
            orderId: `order-${callCount}`,
            status: "submitted",
            symbol: "",
            market: "cn_stock",
            side: "buy",
            orderType: "limit",
            quantity: 100,
            filledQty: 0,
            brokerType: "paper",
            createdAt: new Date().toISOString(),
            updatedAt: new Date().toISOString(),
          },
        } as SubmitResult;
      },
    });

    const svc = new BasketOrderService(makeConfig(), () => mgr);
    await svc.load();

    const basket = await svc.create({
      name: "部分失败",
      items: [
        { symbol: "600519", side: "buy", orderType: "limit", quantity: 100, price: 1800 },
        { symbol: "000858", side: "buy", orderType: "limit", quantity: 99999, price: 120 },
        { symbol: "601398", side: "buy", orderType: "limit", quantity: 500, price: 5.5 },
      ],
      interOrderDelayMs: 0,
    });

    const result = await svc.submit(basket.basketId);

    expect(result.submittedCount).toBe(2);
    expect(result.failedCount).toBe(1);
    expect(result.items[1].status).toBe("rejected");
    expect(result.items[1].errorMsg).toContain("1218");
  });

  it("cancels a basket and its submitted orders", async () => {
    const cancelledIds: string[] = [];
    const mgr = makeMockOrderMgr({
      cancelFn: async (id: string) => {
        cancelledIds.push(id);
      },
    });

    const svc = new BasketOrderService(makeConfig(), () => mgr);
    await svc.load();

    const basket = await svc.create({
      name: "取消篮子",
      items: [
        { symbol: "600519", side: "buy", orderType: "limit", quantity: 100, price: 1800 },
      ],
      interOrderDelayMs: 0,
    });

    await svc.submit(basket.basketId);
    const result = await svc.cancel(basket.basketId);

    expect(result.status).toBe("cancelled");
    expect(cancelledIds.length).toBe(1);
  });

  it("cannot submit a non-draft basket", async () => {
    const mgr = makeMockOrderMgr();
    const svc = new BasketOrderService(makeConfig(), () => mgr);
    await svc.load();

    const basket = await svc.create({
      name: "1663",
      items: [{ symbol: "600519", side: "buy", orderType: "limit", quantity: 100, price: 1800 }],
      interOrderDelayMs: 0,
    });

    await svc.submit(basket.basketId);
    await expect(svc.submit(basket.basketId)).rejects.toThrow("0163600800");
  });

  it("lists baskets with optional status filter", async () => {
    const svc = new BasketOrderService(makeConfig(), () => null);
    await svc.load();

    await svc.create({
      name: "篮子A",
      items: [{ symbol: "600519", side: "buy", orderType: "limit", quantity: 100, price: 1800 }],
    });
    await svc.create({
      name: "篮子B",
      items: [{ symbol: "000858", side: "buy", orderType: "limit", quantity: 200, price: 120 }],
    });

    expect(svc.list().length).toBe(2);
    expect(svc.list("draft").length).toBe(2);
    expect(svc.list("active").length).toBe(0);
  });

  it("deletes a draft basket", async () => {
    const svc = new BasketOrderService(makeConfig(), () => null);
    await svc.load();

    const basket = await svc.create({
      name: "删除篮子",
      items: [{ symbol: "600519", side: "buy", orderType: "limit", quantity: 100, price: 1800 }],
    });

    await svc.delete(basket.basketId);
    expect(svc.get(basket.basketId)).toBeNull();
    expect(svc.list().length).toBe(0);
  });

  it("cannot delete an active basket", async () => {
    const mgr = makeMockOrderMgr();
    const svc = new BasketOrderService(makeConfig(), () => mgr);
    await svc.load();

    const basket = await svc.create({
      name: "活跃篮子",
      items: [{ symbol: "600519", side: "buy", orderType: "limit", quantity: 100, price: 1800 }],
      interOrderDelayMs: 0,
    });

    await svc.submit(basket.basketId);
    await expect(svc.delete(basket.basketId)).rejects.toThrow("0101");
  });

  it("persists and reloads baskets", async () => {
    const cfg = makeConfig();
    const svc1 = new BasketOrderService(cfg, () => null);
    await svc1.load();

    const basket = await svc1.create({
      name: "0404",
      items: [
        { symbol: "600519", side: "buy", orderType: "limit", quantity: 100, price: 1800 },
      ],
    });

    // Create a new service instance and reload
    const svc2 = new BasketOrderService(cfg, () => null);
    await svc2.load();

    const reloaded = svc2.get(basket.basketId);
    expect(reloaded).toBeTruthy();
    expect(reloaded!.name).toBe("0404");
    expect(reloaded!.items.length).toBe(1);
  });

  it("detects market type from symbol", async () => {
    const svc = new BasketOrderService(makeConfig(), () => null);
    await svc.load();

    const basket = await svc.create({
      name: "г",
      items: [
        { symbol: "600519", side: "buy", orderType: "limit", quantity: 100, price: 1800 },
        { symbol: "AAPL", side: "buy", orderType: "limit", quantity: 10, price: 150 },
      ],
    });

    expect(basket.items[0].market).toBe("cn_stock");
    expect(basket.items[1].market).toBe("us_stock");
  });

  it("rejects adding items to a non-draft basket", async () => {
    const mgr = makeMockOrderMgr();
    const svc = new BasketOrderService(makeConfig(), () => mgr);
    await svc.load();

    const basket = await svc.create({
      name: "04",
      items: [{ symbol: "600519", side: "buy", orderType: "limit", quantity: 100, price: 1800 }],
      interOrderDelayMs: 0,
    });

    await svc.submit(basket.basketId);

    await expect(
      svc.addItems(basket.basketId, [
        { symbol: "000858", side: "buy", orderType: "limit", quantity: 200, price: 120 },
      ]),
    ).rejects.toThrow("01100416080009");
  });

  it("submit rejects when OrderManager is not available", async () => {
    const svc = new BasketOrderService(makeConfig(), () => null);
    await svc.load();

    const basket = await svc.create({
      name: "无引",
      items: [{ symbol: "600519", side: "buy", orderType: "limit", quantity: 100, price: 1800 }],
    });

    await expect(svc.submit(basket.basketId)).rejects.toThrow("OrderManager ā03");
  });
});
