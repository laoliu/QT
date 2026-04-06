/**
 * BasketOrderService — manages basket (batch) order creation, persistence, and execution.
 *
 * A basket order lets a trader submit a group of buy/sell orders as a single logical unit.
 * Use cases:
 *   - Buy a pre-defined set of stocks at once (e.g. "半导体篮子")
 *   - Allocate a fixed amount across multiple stocks by weight
 *   - One-click sell of a group of positions
 *   - Save & reuse basket templates
 *
 * Persistence:
 *   basket-templates.json — saved reusable templates
 *   basket-orders.json    — execution history
 */
import { randomUUID } from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { OrderRequest } from "../broker/adapter.js";
import { detectMarketFromSymbol } from "../data/market-data-provider.js";
import type {
  BasketExecutionStatus,
  BasketItem,
  BasketItemResult,
  BasketOrder,
  BasketTemplate,
  QuantTradingConfig,
  QuoteSnapshot,
} from "../types.js";
import { AsyncMutex } from "../utils/async-mutex.js";

const templateMutex = new AsyncMutex();
const orderMutex = new AsyncMutex();

function dir(cfg: QuantTradingConfig): string {
  return cfg.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
}

function templatesPath(cfg: QuantTradingConfig): string {
  return path.join(dir(cfg), "basket-templates.json");
}

function ordersPath(cfg: QuantTradingConfig): string {
  return path.join(dir(cfg), "basket-orders.json");
}

async function ensureDir(cfg: QuantTradingConfig): Promise<void> {
  await fs.mkdir(dir(cfg), { recursive: true });
}

// ── Template persistence ─────────────────────────────────────────

async function loadTemplates(cfg: QuantTradingConfig): Promise<BasketTemplate[]> {
  try {
    return JSON.parse(await fs.readFile(templatesPath(cfg), "utf-8")) as BasketTemplate[];
  } catch {
    return [];
  }
}

async function saveTemplates(
  templates: BasketTemplate[],
  cfg: QuantTradingConfig,
): Promise<void> {
  await ensureDir(cfg);
  await fs.writeFile(templatesPath(cfg), JSON.stringify(templates, null, 2));
}

// ── Order persistence ────────────────────────────────────────────

async function loadBasketOrders(cfg: QuantTradingConfig): Promise<BasketOrder[]> {
  try {
    return JSON.parse(await fs.readFile(ordersPath(cfg), "utf-8")) as BasketOrder[];
  } catch {
    return [];
  }
}

async function saveBasketOrders(
  orders: BasketOrder[],
  cfg: QuantTradingConfig,
): Promise<void> {
  await ensureDir(cfg);
  await fs.writeFile(ordersPath(cfg), JSON.stringify(orders, null, 2));
}

// ── Quote fetcher type ───────────────────────────────────────────

export type QuoteFetcher = (symbol: string) => Promise<QuoteSnapshot | null>;

// ── Order submitter type ─────────────────────────────────────────

export type OrderSubmitter = (
  req: OrderRequest,
) => Promise<{ ok: true; orderId: string } | { ok: false; reason: string }>;

// ── BasketOrderService ───────────────────────────────────────────

export class BasketOrderService {
  private cfg: QuantTradingConfig;

  constructor(cfg: QuantTradingConfig) {
    this.cfg = cfg;
  }

  // ── Template CRUD ────────────────────────────────────────────

  async createTemplate(
    name: string,
    items: BasketItem[],
    description?: string,
  ): Promise<BasketTemplate> {
    const release = await templateMutex.acquire();
    try {
      const templates = await loadTemplates(this.cfg);
      const now = new Date().toISOString();
      const template: BasketTemplate = {
        templateId: randomUUID().slice(0, 8),
        name,
        description,
        items,
        createdAt: now,
        updatedAt: now,
      };
      templates.push(template);
      await saveTemplates(templates, this.cfg);
      return template;
    } finally {
      release();
    }
  }

  async listTemplates(): Promise<BasketTemplate[]> {
    return loadTemplates(this.cfg);
  }

  async getTemplate(templateId: string): Promise<BasketTemplate | null> {
    const templates = await loadTemplates(this.cfg);
    return templates.find((t) => t.templateId === templateId) ?? null;
  }

  async cloneTemplate(
    templateId: string,
    newName?: string,
  ): Promise<BasketTemplate | null> {
    const release = await templateMutex.acquire();
    try {
      const templates = await loadTemplates(this.cfg);
      const src = templates.find((t) => t.templateId === templateId);
      if (!src) return null;
      const now = new Date().toISOString();
      const clone: BasketTemplate = {
        templateId: randomUUID().slice(0, 8),
        name: newName ?? `${src.name} (副本)`,
        description: src.description,
        items: src.items.map((it) => ({ ...it })),
        createdAt: now,
        updatedAt: now,
      };
      templates.push(clone);
      await saveTemplates(templates, this.cfg);
      return clone;
    } finally {
      release();
    }
  }

  async deleteTemplate(templateId: string): Promise<boolean> {
    const release = await templateMutex.acquire();
    try {
      const templates = await loadTemplates(this.cfg);
      const before = templates.length;
      const filtered = templates.filter((t) => t.templateId !== templateId);
      if (filtered.length === before) return false;
      await saveTemplates(filtered, this.cfg);
      return true;
    } finally {
      release();
    }
  }

  async updateTemplate(
    templateId: string,
    patch: { name?: string; description?: string; items?: BasketItem[] },
  ): Promise<BasketTemplate | null> {
    const release = await templateMutex.acquire();
    try {
      const templates = await loadTemplates(this.cfg);
      const tpl = templates.find((t) => t.templateId === templateId);
      if (!tpl) return null;
      if (patch.name !== undefined) tpl.name = patch.name;
      if (patch.description !== undefined) tpl.description = patch.description;
      if (patch.items !== undefined) tpl.items = patch.items;
      tpl.updatedAt = new Date().toISOString();
      await saveTemplates(templates, this.cfg);
      return tpl;
    } finally {
      release();
    }
  }

  // ── Basket execution ─────────────────────────────────────────

  /**
   * Create a basket order from items (or a template) and optionally execute it.
   *
   * If `totalAmount` is provided and items have `weight`, each item's quantity
   * is calculated from its weight allocation and the current market price.
   *
   * Returns the basket order with execution results.
   */
  async createBasketOrder(opts: {
    name: string;
    items: BasketItem[];
    totalAmount?: number;
    dryRun?: boolean;
    fetchQuote: QuoteFetcher;
    submitOrder?: OrderSubmitter;
  }): Promise<BasketOrder> {
    const { name, items, totalAmount, dryRun, fetchQuote, submitOrder } = opts;

    const basketId = randomUUID().slice(0, 8);
    const now = new Date().toISOString();

    // Resolve quantities for amount/weight-based items
    const resolvedItems: Array<BasketItem & { resolvedQty: number; resolvedPrice?: number }> = [];

    for (const item of items) {
      let qty = item.quantity ?? 0;
      let price = item.price;

      // If amount-based or weight-based, fetch current price to compute quantity
      if ((!item.quantity || item.quantity <= 0) && (item.amount || item.weight)) {
        const quote = await fetchQuote(item.symbol);
        if (quote?.price && quote.price > 0) {
          price = price ?? quote.price;
          let targetAmount = 0;
          if (item.amount && item.amount > 0) {
            targetAmount = item.amount;
          } else if (item.weight && totalAmount && totalAmount > 0) {
            targetAmount = totalAmount * item.weight;
          }
          if (targetAmount > 0 && price > 0) {
            // Round down to lot size (100 shares for A-shares)
            qty = Math.floor(targetAmount / price / 100) * 100;
          }
        }
      }

      resolvedItems.push({
        ...item,
        resolvedQty: qty,
        resolvedPrice: price,
      });
    }

    // Build results
    const results: BasketItemResult[] = resolvedItems.map((item) => ({
      symbol: item.symbol,
      side: item.side || "buy",
      requestedQty: item.resolvedQty,
      status: item.resolvedQty > 0 ? "pending" as const : "skipped" as const,
      reason: item.resolvedQty <= 0 ? "数量为0（价格不可用或金额不足一手）" : undefined,
      price: item.resolvedPrice,
    }));

    const basketOrder: BasketOrder = {
      basketId,
      name,
      items,
      totalAmount,
      results,
      executionStatus: "pending",
      createdAt: now,
    };

    if (dryRun || !submitOrder) {
      // Persist without executing
      await this.persistBasketOrder(basketOrder);
      return basketOrder;
    }

    // Execute: submit all orders
    basketOrder.executionStatus = "executing";
    basketOrder.executedAt = now;

    // Execute sells first, then buys (to free up cash)
    const sellIndices = results
      .map((r, i) => ({ r, i }))
      .filter(({ r }) => r.side === "sell" && r.status === "pending");
    const buyIndices = results
      .map((r, i) => ({ r, i }))
      .filter(({ r }) => r.side === "buy" && r.status === "pending");

    for (const batch of [sellIndices, buyIndices]) {
      // Submit orders in parallel within each batch
      const submissions = batch.map(async ({ r, i }) => {
        const req: OrderRequest = {
          symbol: r.symbol,
          market: detectMarketFromSymbol(r.symbol),
          side: r.side,
          orderType: r.price ? "limit" : "market",
          quantity: r.requestedQty,
          price: r.price,
          strategyId: "basket",
          signalId: `basket_${basketId}_${r.symbol}`,
        };
        const res = await submitOrder(req);
        if (res.ok) {
          results[i].status = "submitted";
          results[i].orderId = res.orderId;
        } else {
          results[i].status = "failed";
          results[i].reason = res.reason;
        }
      });
      await Promise.allSettled(submissions);
    }

    // Determine overall status
    const submitted = results.filter((r) => r.status === "submitted").length;
    const failed = results.filter((r) => r.status === "failed").length;
    const pending = results.filter((r) => r.status === "pending").length;

    if (submitted > 0 && failed === 0 && pending === 0) {
      basketOrder.executionStatus = "completed";
    } else if (submitted > 0) {
      basketOrder.executionStatus = "partial";
    } else if (failed > 0 && submitted === 0) {
      basketOrder.executionStatus = "completed"; // all attempted, all failed
    }

    await this.persistBasketOrder(basketOrder);
    return basketOrder;
  }

  /** Cancel all pending/submitted orders in a basket. */
  async cancelBasket(
    basketId: string,
    cancelOrder: (orderId: string) => Promise<void>,
  ): Promise<BasketOrder | null> {
    const release = await orderMutex.acquire();
    try {
      const orders = await loadBasketOrders(this.cfg);
      const basket = orders.find((o) => o.basketId === basketId);
      if (!basket) return null;

      if (basket.results) {
        for (const r of basket.results) {
          if (r.orderId && r.status === "submitted") {
            try {
              await cancelOrder(r.orderId);
              r.status = "failed";
              r.reason = "篮子整体撤销";
            } catch {
              // best-effort cancel
            }
          }
        }
      }

      basket.executionStatus = "cancelled";
      basket.cancelledAt = new Date().toISOString();
      await saveBasketOrders(orders, this.cfg);
      return basket;
    } finally {
      release();
    }
  }

  /** List basket orders (most recent first). */
  async listBasketOrders(limit = 20): Promise<BasketOrder[]> {
    const orders = await loadBasketOrders(this.cfg);
    return orders.slice(-limit).reverse();
  }

  /** Get a specific basket order by ID. */
  async getBasketOrder(basketId: string): Promise<BasketOrder | null> {
    const orders = await loadBasketOrders(this.cfg);
    return orders.find((o) => o.basketId === basketId) ?? null;
  }

  // ── Basket transform helpers ─────────────────────────────────

  /** Reverse all sides in a list of items (buy→sell, sell→buy). */
  reverseItems(items: BasketItem[]): BasketItem[] {
    return items.map((it) => ({
      ...it,
      side: it.side === "sell" ? ("buy" as const) : ("sell" as const),
    }));
  }

  /** Adjust quantities/prices by a multiplier. */
  adjustItems(
    items: BasketItem[],
    opts: { quantityMultiplier?: number; priceMultiplier?: number; priceOffset?: number },
  ): BasketItem[] {
    return items.map((it) => {
      const adjusted = { ...it };
      if (opts.quantityMultiplier !== undefined && adjusted.quantity) {
        adjusted.quantity = Math.floor(adjusted.quantity * opts.quantityMultiplier / 100) * 100;
        if (adjusted.quantity < 0) adjusted.quantity = 0;
      }
      if (opts.quantityMultiplier !== undefined && adjusted.amount) {
        adjusted.amount = Math.round(adjusted.amount * opts.quantityMultiplier * 100) / 100;
        if (adjusted.amount < 0) adjusted.amount = 0;
      }
      if (opts.priceMultiplier !== undefined && adjusted.price) {
        adjusted.price = Math.round(adjusted.price * opts.priceMultiplier * 100) / 100;
      }
      if (opts.priceOffset !== undefined && adjusted.price) {
        adjusted.price = Math.round((adjusted.price + opts.priceOffset) * 100) / 100;
        if (adjusted.price < 0) adjusted.price = 0;
      }
      return adjusted;
    });
  }

  // ── Private helpers ──────────────────────────────────────────

  private async persistBasketOrder(basket: BasketOrder): Promise<void> {
    const release = await orderMutex.acquire();
    try {
      const orders = await loadBasketOrders(this.cfg);
      const idx = orders.findIndex((o) => o.basketId === basket.basketId);
      if (idx >= 0) {
        orders[idx] = basket;
      } else {
        orders.push(basket);
      }
      // Keep last 200 basket orders
      if (orders.length > 200) {
        orders.splice(0, orders.length - 200);
      }
      await saveBasketOrders(orders, this.cfg);
    } finally {
      release();
    }
  }
}
