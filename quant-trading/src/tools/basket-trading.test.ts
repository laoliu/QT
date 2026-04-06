import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BasketOrderService } from "../services/basket-order.js";
import type { QuantTradingConfig } from "../types.js";
import { createBasketTradingTool } from "./basket-trading.js";

function makeConfig(dataDir: string): QuantTradingConfig {
  return { dataDir } as QuantTradingConfig;
}

// Minimal mock for FavoritesStore
function makeFavoritesStore(items: Array<{ code: string; name?: string; category?: string; tags?: string[] }>) {
  return {
    listFavorites: (filters?: { category?: string; tags?: string[] }) => {
      let result = [...items];
      if (filters?.category) result = result.filter((i) => i.category === filters.category);
      if (filters?.tags?.length) result = result.filter((i) => i.tags?.some((t) => filters.tags!.includes(t)));
      return result;
    },
  } as never;
}

describe("basket_trading tool", () => {
  let tmpDir: string;
  let svc: BasketOrderService;

  beforeEach(async () => {
    tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "basket-tool-test-"));
    svc = new BasketOrderService(makeConfig(tmpDir));
  });

  afterEach(async () => {
    await fs.rm(tmpDir, { recursive: true, force: true });
  });

  function makeTool(opts?: {
    engineRunning?: boolean;
    positions?: Array<{ symbol: string; name?: string; availableQty: number }>;
    favs?: Array<{ code: string; name?: string; category?: string; tags?: string[] }>;
  }) {
    const getEngine = () => {
      if (!opts?.engineRunning) return null;
      return {
        orderManager: {
          broker: {
            queryPositions: async () =>
              (opts?.positions ?? []).map((p) => ({
                symbol: p.symbol,
                name: p.name,
                availableQty: p.availableQty,
                quantity: p.availableQty,
                frozenQty: 0,
                todayBuyQty: 0,
                avgCost: 100,
                market: "cn_stock",
                source: "broker",
                lastSyncAt: new Date().toISOString(),
              })),
          },
          submit: async (req: unknown) => ({
            ok: true,
            order: { orderId: "test-order-id" },
          }),
          cancel: async () => {},
        },
      } as never;
    };

    const favoritesStore = opts?.favs ? makeFavoritesStore(opts.favs) : undefined;

    return createBasketTradingTool(
      {} as never,
      makeConfig(tmpDir),
      svc,
      getEngine,
      favoritesStore,
    );
  }

  // ── clone_template ───────────────────────────────────────

  it("clone_template: clones an existing template", async () => {
    const tool = makeTool();
    await tool.execute({ action: "create_template", name: "原版", items: [{ symbol: "600519", side: "buy", quantity: 100 }] });
    const templates = await svc.listTemplates();
    const tid = templates[0].templateId;

    const result = await tool.execute({ action: "clone_template", template_id: tid }) as Record<string, unknown>;
    expect(result.message).toBe("模板已复制");
    expect(result.templateId).not.toBe(tid);
    expect(result.name).toBe("原版 (副本)");
  });

  it("clone_template: with custom name", async () => {
    const tool = makeTool();
    await tool.execute({ action: "create_template", name: "A", items: [{ symbol: "600519", side: "buy", quantity: 100 }] });
    const templates = await svc.listTemplates();

    const result = await tool.execute({ action: "clone_template", template_id: templates[0].templateId, name: "B版" }) as Record<string, unknown>;
    expect(result.name).toBe("B版");
  });

  it("clone_template: error on missing template_id", async () => {
    const tool = makeTool();
    const result = await tool.execute({ action: "clone_template" }) as Record<string, unknown>;
    expect(result.error).toContain("template_id");
  });

  // ── basket_from_portfolio ────────────────────────────────

  it("basket_from_portfolio: creates template from positions", async () => {
    const tool = makeTool({
      engineRunning: true,
      positions: [
        { symbol: "600519", name: "茅台", availableQty: 100 },
        { symbol: "000001", name: "平安银行", availableQty: 500 },
      ],
    });

    const result = await tool.execute({ action: "basket_from_portfolio" }) as Record<string, unknown>;
    expect(result.message).toContain("2只");
    expect(result.templateId).toBeTruthy();
    const items = result.items as Array<{ symbol: string; side: string; quantity: number }>;
    expect(items).toHaveLength(2);
    expect(items[0].side).toBe("sell"); // default is sell
    expect(items[0].quantity).toBe(100);
  });

  it("basket_from_portfolio: respects buy side", async () => {
    const tool = makeTool({
      engineRunning: true,
      positions: [{ symbol: "600519", availableQty: 100 }],
    });

    const result = await tool.execute({ action: "basket_from_portfolio", side: "buy" }) as Record<string, unknown>;
    const items = result.items as Array<{ side: string }>;
    expect(items[0].side).toBe("buy");
  });

  it("basket_from_portfolio: error when engine not running", async () => {
    const tool = makeTool({ engineRunning: false });
    const result = await tool.execute({ action: "basket_from_portfolio" }) as Record<string, unknown>;
    expect(result.error).toContain("交易引擎未启动");
  });

  it("basket_from_portfolio: error when no positions", async () => {
    const tool = makeTool({ engineRunning: true, positions: [] });
    const result = await tool.execute({ action: "basket_from_portfolio" }) as Record<string, unknown>;
    expect(result.error).toContain("无持仓");
  });

  // ── basket_from_favorites ────────────────────────────────

  it("basket_from_favorites: creates template from favorites", async () => {
    const tool = makeTool({
      favs: [
        { code: "600519", name: "茅台", category: "白酒" },
        { code: "000001", name: "平安银行", category: "金融" },
      ],
    });

    const result = await tool.execute({ action: "basket_from_favorites" }) as Record<string, unknown>;
    expect(result.message).toContain("2只");
    expect(result.templateId).toBeTruthy();
  });

  it("basket_from_favorites: filters by category", async () => {
    const tool = makeTool({
      favs: [
        { code: "600519", name: "茅台", category: "白酒" },
        { code: "000001", name: "平安银行", category: "金融" },
      ],
    });

    const result = await tool.execute({ action: "basket_from_favorites", category: "白酒" }) as Record<string, unknown>;
    expect(result.message).toContain("1只");
  });

  it("basket_from_favorites: error when no favoritesStore", async () => {
    const tool = makeTool(); // no favs → no favoritesStore
    const result = await tool.execute({ action: "basket_from_favorites" }) as Record<string, unknown>;
    expect(result.error).toContain("不可用");
  });

  it("basket_from_favorites: error when empty", async () => {
    const tool = makeTool({ favs: [] });
    const result = await tool.execute({ action: "basket_from_favorites" }) as Record<string, unknown>;
    expect(result.error).toContain("为空");
  });

  // ── reverse ──────────────────────────────────────────────

  it("reverse: creates reversed template", async () => {
    const tool = makeTool();
    await tool.execute({
      action: "create_template",
      name: "买入篮子",
      items: [
        { symbol: "600519", side: "buy", quantity: 100 },
        { symbol: "000001", side: "sell", quantity: 200 },
      ],
    });
    const templates = await svc.listTemplates();
    const tid = templates[0].templateId;

    const result = await tool.execute({ action: "reverse", template_id: tid }) as Record<string, unknown>;
    expect(result.message).toContain("反转");
    const items = result.items as Array<{ symbol: string; side: string }>;
    expect(items[0].side).toBe("sell"); // was buy
    expect(items[1].side).toBe("buy"); // was sell
  });

  // ── adjust ───────────────────────────────────────────────

  it("adjust: scales quantities", async () => {
    const tool = makeTool();
    await tool.execute({
      action: "create_template",
      name: "调整测试",
      items: [{ symbol: "600519", side: "buy", quantity: 1000, price: 100 }],
    });
    const templates = await svc.listTemplates();
    const tid = templates[0].templateId;

    const result = await tool.execute({
      action: "adjust",
      template_id: tid,
      quantity_multiplier: 0.5,
    }) as Record<string, unknown>;
    expect(result.message).toContain("数量×0.5");
    const items = result.items as Array<{ quantity: number }>;
    expect(items[0].quantity).toBe(500);
  });

  it("adjust: error when no adjustments specified", async () => {
    const tool = makeTool();
    await tool.execute({
      action: "create_template",
      name: "test",
      items: [{ symbol: "600519", side: "buy", quantity: 100 }],
    });
    const templates = await svc.listTemplates();

    const result = await tool.execute({
      action: "adjust",
      template_id: templates[0].templateId,
    }) as Record<string, unknown>;
    expect(result.error).toContain("至少提供");
  });

  // ── Existing actions still work ──────────────────────────

  it("preview: shows execution plan", async () => {
    const tool = makeTool();
    const result = await tool.execute({
      action: "preview",
      name: "测试",
      items: [{ symbol: "600519", side: "buy", quantity: 100 }],
    }) as Record<string, unknown>;
    expect(result.basketId).toBeTruthy();
    expect(result.preview).toBeTruthy();
  });

  it("list_baskets: returns empty initially", async () => {
    const tool = makeTool();
    const result = await tool.execute({ action: "list_baskets" }) as Record<string, unknown>;
    expect(result.message).toContain("暂无");
  });

  it("unknown action: returns error", async () => {
    const tool = makeTool();
    const result = await tool.execute({ action: "nonexistent" }) as Record<string, unknown>;
    expect(result.error).toContain("未知操作");
  });
});
