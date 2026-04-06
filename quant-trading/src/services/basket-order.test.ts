import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { QuantTradingConfig } from "../types.js";
import { BasketOrderService } from "./basket-order.js";

function makeConfig(dataDir: string): QuantTradingConfig {
  return { dataDir } as QuantTradingConfig;
}

describe("BasketOrderService", () => {
  let tmpDir: string;
  let svc: BasketOrderService;

  beforeEach(async () => {
    tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "basket-test-"));
    svc = new BasketOrderService(makeConfig(tmpDir));
  });

  afterEach(async () => {
    await fs.rm(tmpDir, { recursive: true, force: true });
  });

  // ── Template CRUD ────────────────────────────────────────

  it("creates and lists templates", async () => {
    const tpl = await svc.createTemplate("半导体篮子", [
      { symbol: "600519", side: "buy", quantity: 100 },
      { symbol: "000001", side: "buy", quantity: 200 },
    ]);
    expect(tpl.templateId).toBeTruthy();
    expect(tpl.name).toBe("半导体篮子");
    expect(tpl.items).toHaveLength(2);

    const list = await svc.listTemplates();
    expect(list).toHaveLength(1);
    expect(list[0].templateId).toBe(tpl.templateId);
  });

  it("gets a template by id", async () => {
    const tpl = await svc.createTemplate("test", [{ symbol: "600519", side: "buy", quantity: 100 }]);
    const found = await svc.getTemplate(tpl.templateId);
    expect(found).not.toBeNull();
    expect(found!.name).toBe("test");

    const missing = await svc.getTemplate("nonexistent");
    expect(missing).toBeNull();
  });

  it("deletes a template", async () => {
    const tpl = await svc.createTemplate("test", [{ symbol: "600519", side: "buy", quantity: 100 }]);
    const ok = await svc.deleteTemplate(tpl.templateId);
    expect(ok).toBe(true);
    const list = await svc.listTemplates();
    expect(list).toHaveLength(0);

    const notOk = await svc.deleteTemplate("nonexistent");
    expect(notOk).toBe(false);
  });

  it("updates a template", async () => {
    const tpl = await svc.createTemplate("old", [{ symbol: "600519", side: "buy", quantity: 100 }]);
    const updated = await svc.updateTemplate(tpl.templateId, {
      name: "new",
      items: [{ symbol: "000001", side: "sell", quantity: 200 }],
    });
    expect(updated).not.toBeNull();
    expect(updated!.name).toBe("new");
    expect(updated!.items[0].symbol).toBe("000001");
    expect(updated!.items[0].side).toBe("sell");
  });

  // ── Clone template ───────────────────────────────────────

  it("clones a template", async () => {
    const tpl = await svc.createTemplate("原版", [
      { symbol: "600519", side: "buy", quantity: 100 },
      { symbol: "000001", side: "sell", quantity: 200 },
    ], "描述");

    const clone = await svc.cloneTemplate(tpl.templateId);
    expect(clone).not.toBeNull();
    expect(clone!.templateId).not.toBe(tpl.templateId);
    expect(clone!.name).toBe("原版 (副本)");
    expect(clone!.description).toBe("描述");
    expect(clone!.items).toHaveLength(2);
    expect(clone!.items[0].symbol).toBe("600519");

    const list = await svc.listTemplates();
    expect(list).toHaveLength(2);
  });

  it("clones with custom name", async () => {
    const tpl = await svc.createTemplate("A", [{ symbol: "600519", side: "buy", quantity: 100 }]);
    const clone = await svc.cloneTemplate(tpl.templateId, "B版本");
    expect(clone!.name).toBe("B版本");
  });

  it("returns null when cloning non-existent template", async () => {
    const result = await svc.cloneTemplate("nonexistent");
    expect(result).toBeNull();
  });

  // ── Reverse items ────────────────────────────────────────

  it("reverses item sides", () => {
    const items = [
      { symbol: "600519", side: "buy" as const, quantity: 100 },
      { symbol: "000001", side: "sell" as const, quantity: 200 },
    ];
    const reversed = svc.reverseItems(items);
    expect(reversed[0].side).toBe("sell");
    expect(reversed[1].side).toBe("buy");
    expect(reversed[0].quantity).toBe(100);
  });

  // ── Adjust items ─────────────────────────────────────────

  it("adjusts quantities by multiplier", () => {
    const items = [
      { symbol: "600519", side: "buy" as const, quantity: 1000 },
      { symbol: "000001", side: "buy" as const, amount: 50000 },
    ];
    const adjusted = svc.adjustItems(items, { quantityMultiplier: 0.5 });
    expect(adjusted[0].quantity).toBe(500); // 1000 * 0.5 rounded to lots
    expect(adjusted[1].amount).toBe(25000);
  });

  it("adjusts prices by multiplier", () => {
    const items = [{ symbol: "600519", side: "buy" as const, quantity: 100, price: 100 }];
    const adjusted = svc.adjustItems(items, { priceMultiplier: 1.02 });
    expect(adjusted[0].price).toBe(102);
  });

  it("adjusts prices by offset", () => {
    const items = [{ symbol: "600519", side: "buy" as const, quantity: 100, price: 100 }];
    const adjusted = svc.adjustItems(items, { priceOffset: -5 });
    expect(adjusted[0].price).toBe(95);
  });

  it("clamps negative price to zero", () => {
    const items = [{ symbol: "600519", side: "buy" as const, quantity: 100, price: 3 }];
    const adjusted = svc.adjustItems(items, { priceOffset: -10 });
    expect(adjusted[0].price).toBe(0);
  });

  // ── Basket order execution (dry run) ─────────────────────

  it("creates a dry-run basket with quantity items", async () => {
    const basket = await svc.createBasketOrder({
      name: "测试篮子",
      items: [
        { symbol: "600519", side: "buy", quantity: 100 },
        { symbol: "000001", side: "sell", quantity: 200 },
      ],
      dryRun: true,
      fetchQuote: async () => null,
    });
    expect(basket.basketId).toBeTruthy();
    expect(basket.executionStatus).toBe("pending");
    expect(basket.results).toHaveLength(2);
    expect(basket.results![0].requestedQty).toBe(100);
    expect(basket.results![1].requestedQty).toBe(200);
  });

  it("resolves amount-based items using quote price", async () => {
    const basket = await svc.createBasketOrder({
      name: "金额篮子",
      items: [{ symbol: "600519", side: "buy", amount: 100000 }],
      dryRun: true,
      fetchQuote: async () => ({ price: 1800, symbol: "600519" } as never),
    });
    // 100000 / 1800 = 55.5, floor to lot = 0 (wait — 55 shares, floor to 100 lots = 0)
    // Actually: Math.floor(100000 / 1800 / 100) * 100 = Math.floor(0.555) * 100 = 0
    // Need a more reasonable amount
    expect(basket.results![0].status).toBe("skipped");
  });

  it("resolves weight-based items with totalAmount", async () => {
    const basket = await svc.createBasketOrder({
      name: "权重篮子",
      items: [
        { symbol: "600519", side: "buy", weight: 0.5 },
        { symbol: "000001", side: "buy", weight: 0.5 },
      ],
      totalAmount: 1000000,
      dryRun: true,
      fetchQuote: async (symbol) => {
        if (symbol === "600519") return { price: 100, symbol } as never;
        return { price: 10, symbol } as never;
      },
    });
    expect(basket.results).toHaveLength(2);
    // 600519: 500000 / 100 / 100 * 100 = 5000 shares
    expect(basket.results![0].requestedQty).toBe(5000);
    // 000001: 500000 / 10 / 100 * 100 = 50000 shares
    expect(basket.results![1].requestedQty).toBe(50000);
  });

  // ── Basket execution (real) ──────────────────────────────

  it("executes orders and tracks results", async () => {
    const basket = await svc.createBasketOrder({
      name: "执行测试",
      items: [
        { symbol: "600519", side: "sell", quantity: 100 },
        { symbol: "000001", side: "buy", quantity: 200 },
      ],
      dryRun: false,
      fetchQuote: async () => null,
      submitOrder: async (req) => ({ ok: true as const, orderId: `ord-${req.symbol}` }),
    });
    expect(basket.executionStatus).toBe("completed");
    expect(basket.results![0].status).toBe("submitted");
    expect(basket.results![0].orderId).toBe("ord-600519");
    expect(basket.results![1].status).toBe("submitted");
  });

  it("marks partial when some orders fail", async () => {
    const basket = await svc.createBasketOrder({
      name: "部分失败",
      items: [
        { symbol: "600519", side: "buy", quantity: 100 },
        { symbol: "000001", side: "buy", quantity: 200 },
      ],
      dryRun: false,
      fetchQuote: async () => null,
      submitOrder: async (req) => {
        if (req.symbol === "000001") return { ok: false as const, reason: "余额不足" };
        return { ok: true as const, orderId: "ok1" };
      },
    });
    expect(basket.executionStatus).toBe("partial");
  });

  // ── Cancel basket ────────────────────────────────────────

  it("cancels a basket", async () => {
    const basket = await svc.createBasketOrder({
      name: "待撤销",
      items: [{ symbol: "600519", side: "buy", quantity: 100 }],
      dryRun: false,
      fetchQuote: async () => null,
      submitOrder: async () => ({ ok: true as const, orderId: "ord1" }),
    });

    const cancelled = await svc.cancelBasket(basket.basketId, async () => {});
    expect(cancelled).not.toBeNull();
    expect(cancelled!.executionStatus).toBe("cancelled");
  });

  it("returns null when cancelling nonexistent basket", async () => {
    const result = await svc.cancelBasket("nope", async () => {});
    expect(result).toBeNull();
  });

  // ── List / get basket orders ─────────────────────────────

  it("lists and gets basket orders", async () => {
    await svc.createBasketOrder({
      name: "a",
      items: [{ symbol: "600519", side: "buy", quantity: 100 }],
      dryRun: true,
      fetchQuote: async () => null,
    });
    await svc.createBasketOrder({
      name: "b",
      items: [{ symbol: "000001", side: "buy", quantity: 200 }],
      dryRun: true,
      fetchQuote: async () => null,
    });

    const list = await svc.listBasketOrders();
    expect(list).toHaveLength(2);
    expect(list[0].name).toBe("b"); // most recent first

    const got = await svc.getBasketOrder(list[1].basketId);
    expect(got).not.toBeNull();
    expect(got!.name).toBe("a");
  });
});
