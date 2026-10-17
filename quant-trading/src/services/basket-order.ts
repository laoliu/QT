/**
 * BasketOrderService 66batch order management for multiple symbols.
 *
 * Use cases for stock traders:
 *   1. Create a basket of orders (e.g. buy a sector rotation portfolio)
 *   2. Submit all orders in a basket as a batch
 *   3. Track progress of each order within the basket
 *   4. Cancel all remaining orders in a basket
 *   5. Preset baskets from portfolio rebalance suggestions
 *
 * Persistence: baskets are stored to disk and restored on restart.
 * Execution: each order is submitted via OrderManager.submit() with
 * configurable concurrency and inter-order delay.
 */
import { randomUUID } from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { OrderRequest } from "../broker/adapter.js";
import type { MarketType, OrderSide, OrderType, QuantTradingConfig } from "../types.js";
import { AsyncMutex } from "../utils/async-mutex.js";
import type { OrderManager, SubmitResult } from "./order-manager.js";
import { nowCSTISO } from "../utils/cst-time.js";

// ── Types ────────────────────────────────────────────────────────

export type BasketOrderItemStatus =
  | "pending" // not yet submitted
  | "submitted" // sent to OrderManager
  | "filled" // fully filled
  | "partial" // partially filled
  | "cancelled" // cancelled
  | "rejected" // rejected by risk gate or broker
  | "skipped"; // skipped (e.g. basket cancelled before this item was submitted)

export interface BasketOrderItem {
  /** Unique item ID within the basket. */
  itemId: string;
  symbol: string;
  market: MarketType;
  side: OrderSide;
  orderType: OrderType;
  quantity: number;
  price?: number;
  /** Current status of this item. */
  status: BasketOrderItemStatus;
  /** The orderId returned by OrderManager after submission. */
  orderId?: string;
  /** Rejection / error reason. */
  errorMsg?: string;
  /** Human-readable note for this item. */
  note?: string;
}

export type BasketStatus =
  | "draft" // basket created, not yet submitted
  | "submitting" // currently submitting orders
  | "active" // all orders submitted, some still pending fills
  | "completed" // all orders reached terminal state
  | "cancelled"; // basket cancelled by user

export interface BasketOrder {
  basketId: string;
  name: string;
  description?: string;
  status: BasketStatus;
  items: BasketOrderItem[];
  /** Total number of items. */
  totalItems: number;
  /** Count of items that have been submitted or reached terminal state. */
  submittedCount: number;
  /** Count of items that have been filled. */
  filledCount: number;
  /** Count of items that failed (rejected/cancelled/skipped). */
  failedCount: number;
  createdAt: string;
  updatedAt: string;
  /** When the basket was submitted for execution. */
  submittedAt?: string;
  /** When the basket reached terminal state. */
  completedAt?: string;
  /** Delay in ms between each order submission (default 500). */
  interOrderDelayMs?: number;
}

// ── Persistence ─────────────────────────────────────────────────

const basketMutex = new AsyncMutex();

function dataDir(cfg: QuantTradingConfig): string {
  return cfg.dataDir ?? path.join(process.cwd(), "data");
}

function basketsPath(cfg: QuantTradingConfig): string {
  return path.join(dataDir(cfg), "baskets.json");
}

async function loadBaskets(cfg: QuantTradingConfig): Promise<BasketOrder[]> {
  try {
    return JSON.parse(await fs.readFile(basketsPath(cfg), "utf-8")) as BasketOrder[];
  } catch {
    return [];
  }
}

async function saveBaskets(baskets: BasketOrder[], cfg: QuantTradingConfig): Promise<void> {
  await fs.mkdir(dataDir(cfg), { recursive: true });
  await fs.writeFile(basketsPath(cfg), JSON.stringify(baskets, null, 2));
}

// ── Service ─────────────────────────────────────────────────────

export class BasketOrderService {
  private baskets = new Map<string, BasketOrder>();
  private cfg: QuantTradingConfig;
  private getOrderMgr: () => OrderManager | null;
  private notifyFn?: (msg: string) => void | Promise<void>;
  private loaded = false;

  constructor(cfg: QuantTradingConfig, getOrderMgr: () => OrderManager | null) {
    this.cfg = cfg;
    this.getOrderMgr = getOrderMgr;
  }

  setNotify(fn: (msg: string) => void | Promise<void>): void {
    this.notifyFn = fn;
  }

  /** Load persisted baskets from disk. */
  async load(): Promise<void> {
    const release = await basketMutex.acquire();
    try {
      const baskets = await loadBaskets(this.cfg);
      this.baskets.clear();
      for (const b of baskets) {
        this.baskets.set(b.basketId, b);
      }
      this.loaded = true;
    } finally {
      release();
    }
  }

  /** Persist all baskets to disk. */
  private async persist(): Promise<void> {
    await saveBaskets([...this.baskets.values()], this.cfg);
  }

  /**
   * Create a new basket (draft state).
   */
  async create(params: {
    name: string;
    description?: string;
    items: Array<{
      symbol: string;
      side: OrderSide;
      orderType: OrderType;
      quantity: number;
      price?: number;
      market?: MarketType;
      note?: string;
    }>;
    interOrderDelayMs?: number;
  }): Promise<BasketOrder> {
    if (params.items.length === 0) {
      throw new Error("篮子订单至少需要一个标");
    }

    const now = nowCSTISO();
    const basket: BasketOrder = {
      basketId: randomUUID(),
      name: params.name,
      description: params.description,
      status: "draft",
      items: params.items.map((item) => ({
        itemId: randomUUID(),
        symbol: item.symbol,
        market: item.market ?? detectMarketType(item.symbol),
        side: item.side,
        orderType: item.orderType,
        quantity: item.quantity,
        price: item.price,
        status: "pending",
        note: item.note,
      })),
      totalItems: params.items.length,
      submittedCount: 0,
      filledCount: 0,
      failedCount: 0,
      createdAt: now,
      updatedAt: now,
      interOrderDelayMs: params.interOrderDelayMs ?? 500,
    };

    const release = await basketMutex.acquire();
    try {
      this.baskets.set(basket.basketId, basket);
      await this.persist();
    } finally {
      release();
    }

    return basket;
  }

  /**
   * Add items to an existing draft basket.
   */
  async addItems(
    basketId: string,
    items: Array<{
      symbol: string;
      side: OrderSide;
      orderType: OrderType;
      quantity: number;
      price?: number;
      market?: MarketType;
      note?: string;
    }>,
  ): Promise<BasketOrder> {
    const release = await basketMutex.acquire();
    try {
      const basket = this.baskets.get(basketId);
      if (!basket) throw new Error(`篮子 ${basketId.slice(0, 8)} 不存在`);
      if (basket.status !== "draft") throw new Error("01100416080009");

      for (const item of items) {
        basket.items.push({
          itemId: randomUUID(),
          symbol: item.symbol,
          market: item.market ?? detectMarketType(item.symbol),
          side: item.side,
          orderType: item.orderType,
          quantity: item.quantity,
          price: item.price,
          status: "pending",
          note: item.note,
        });
      }
      basket.totalItems = basket.items.length;
      basket.updatedAt = nowCSTISO();
      await this.persist();
      return basket;
    } finally {
      release();
    }
  }

  /**
   * Remove an item from a draft basket.
   */
  async removeItem(basketId: string, itemId: string): Promise<BasketOrder> {
    const release = await basketMutex.acquire();
    try {
      const basket = this.baskets.get(basketId);
      if (!basket) throw new Error(`篮子 ${basketId.slice(0, 8)} 不存在`);
      if (basket.status !== "draft") throw new Error("01100416080009");

      const idx = basket.items.findIndex((i) => i.itemId === itemId);
      if (idx < 0) throw new Error(`标的 ${itemId.slice(0, 8)} 不存在`);

      basket.items.splice(idx, 1);
      basket.totalItems = basket.items.length;
      basket.updatedAt = nowCSTISO();
      await this.persist();
      return basket;
    } finally {
      release();
    }
  }

  /**
   * Submit a basket 66sends all pending items to OrderManager sequentially
   * with an inter-order delay.
   */
  async submit(basketId: string): Promise<BasketOrder> {
    const basket = this.baskets.get(basketId);
    if (!basket) throw new Error(`篮子 ${basketId.slice(0, 8)} 不存在`);
    if (basket.status !== "draft") throw new Error("0163600800");

    const orderMgr = this.getOrderMgr();
    if (!orderMgr) throw new Error("OrderManager 未初始化，请先启动引");

    const pendingItems = basket.items.filter((i) => i.status === "pending");
    if (pendingItems.length === 0) throw new Error("04096308");

    basket.status = "submitting";
    basket.submittedAt = nowCSTISO();
    basket.updatedAt = basket.submittedAt;
    await this.persistSafe();

    const delay = basket.interOrderDelayMs ?? 500;
    const results: Array<{ itemId: string; result: SubmitResult }> = [];

    for (let i = 0; i < pendingItems.length; i++) {
      const item = pendingItems[i];

      // Check if basket was cancelled mid-submission (cancel() mutates status externally)
      if ((basket.status as BasketStatus) === "cancelled") {
        item.status = "skipped";
        basket.failedCount++;
        continue;
      }

      const req: OrderRequest = {
        symbol: item.symbol,
        market: item.market,
        side: item.side,
        orderType: item.orderType,
        quantity: item.quantity,
        price: item.price,
        strategyId: `basket_${basket.basketId.slice(0, 8)}`,
        signalId: `basket_${basket.basketId.slice(0, 8)}_${item.itemId.slice(0, 8)}`,
      };

      try {
        const result = await orderMgr.submit(req);
        results.push({ itemId: item.itemId, result });

        if (result.ok) {
          item.status = "submitted";
          item.orderId = result.order.orderId;
          basket.submittedCount++;
        } else {
          item.status = "rejected";
          item.errorMsg = result.reason;
          basket.failedCount++;
        }
      } catch (err) {
        item.status = "rejected";
        item.errorMsg = (err as Error).message;
        basket.failedCount++;
      }

      basket.updatedAt = nowCSTISO();
      await this.persistSafe();

      // Inter-order delay (skip after last item)
      if (i < pendingItems.length - 1 && delay > 0) {
        await sleep(delay);
      }
    }

    // Determine basket status
    this.recalcStatus(basket);
    await this.persistSafe();

    // Notify user
    const filled = basket.items.filter((i) => i.status === "submitted" || i.status === "filled").length;
    const failed = basket.items.filter((i) => i.status === "rejected" || i.status === "skipped").length;
    await this.notify(
      `03{basket.name}」提交完" ${filled}/${basket.totalItems} 06, ${failed} 02`,
    );

    return basket;
  }

  /**
   * Cancel a basket 66cancels all submitted but unfilled orders.
   */
  async cancel(basketId: string): Promise<BasketOrder> {
    const release = await basketMutex.acquire();
    try {
      const basket = this.baskets.get(basketId);
      if (!basket) throw new Error(`篮子 ${basketId.slice(0, 8)} 不存在`);

      const terminal: BasketStatus[] = ["completed", "cancelled"];
      if (terminal.includes(basket.status)) {
        return basket;
      }

      basket.status = "cancelled";
      basket.updatedAt = nowCSTISO();

      // Cancel submitted orders via OrderManager
      const orderMgr = this.getOrderMgr();
      if (orderMgr) {
        for (const item of basket.items) {
          if (item.status === "submitted" && item.orderId) {
            try {
              await orderMgr.cancel(item.orderId);
              item.status = "cancelled";
              basket.failedCount++;
            } catch {
              // best-effort cancel
            }
          }
          if (item.status === "pending") {
            item.status = "skipped";
            basket.failedCount++;
          }
        }
      }

      basket.completedAt = nowCSTISO();
      await this.persist();
      return basket;
    } finally {
      release();
    }
  }

  /**
   * Refresh basket item statuses from OrderManager.
   * Call periodically to update fill/cancel status.
   */
  async refresh(basketId: string): Promise<BasketOrder> {
    const basket = this.baskets.get(basketId);
    if (!basket) throw new Error(`篮子 ${basketId.slice(0, 8)} 不存在`);

    const orderMgr = this.getOrderMgr();
    if (!orderMgr) return basket;

    let changed = false;
    for (const item of basket.items) {
      if (!item.orderId || item.status === "filled" || item.status === "rejected") continue;

      const orders = await orderMgr.getOrders({ symbol: item.symbol });
      const order = orders.find((o) => o.orderId === item.orderId);
      if (!order) continue;

      const newStatus = mapOrderStatus(order.status);
      if (newStatus !== item.status) {
        item.status = newStatus;
        changed = true;
      }
    }

    if (changed) {
      this.recalcStatus(basket);
      basket.updatedAt = nowCSTISO();
      await this.persistSafe();
    }

    return basket;
  }

  /** Get a basket by ID. */
  get(basketId: string): BasketOrder | null {
    return this.baskets.get(basketId) ?? null;
  }

  /** List all baskets, optionally filtered by status. */
  list(status?: BasketStatus): BasketOrder[] {
    const all = [...this.baskets.values()];
    if (status) return all.filter((b) => b.status === status);
    return all.sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  }

  /** Delete a basket (only draft or completed/cancelled). */
  async delete(basketId: string): Promise<void> {
    const release = await basketMutex.acquire();
    try {
      const basket = this.baskets.get(basketId);
      if (!basket) throw new Error(`篮子 ${basketId.slice(0, 8)} 不存在`);

      const deletable: BasketStatus[] = ["draft", "completed", "cancelled"];
      if (!deletable.includes(basket.status)) {
        throw new Error("010160、已完成或已取消的篮");
      }

      this.baskets.delete(basketId);
      await this.persist();
    } finally {
      release();
    }
  }

  // ── Private helpers ─────────────────────────────────────────────

  /** Recalculate counters and basket status from item statuses. */
  private recalcStatus(basket: BasketOrder): void {
    basket.filledCount = basket.items.filter((i) => i.status === "filled").length;
    basket.submittedCount = basket.items.filter(
      (i) => i.status === "submitted" || i.status === "filled" || i.status === "partial",
    ).length;
    basket.failedCount = basket.items.filter(
      (i) => i.status === "rejected" || i.status === "cancelled" || i.status === "skipped",
    ).length;

    const allTerminal = basket.items.every(
      (i) =>
        i.status === "filled" ||
        i.status === "rejected" ||
        i.status === "cancelled" ||
        i.status === "skipped",
    );

    if (basket.status !== "cancelled") {
      if (allTerminal) {
        basket.status = "completed";
        basket.completedAt = nowCSTISO();
      } else if (basket.submittedCount > 0) {
        basket.status = "active";
      }
    }
  }

  private async persistSafe(): Promise<void> {
    const release = await basketMutex.acquire();
    try {
      await this.persist();
    } finally {
      release();
    }
  }

  private async notify(msg: string): Promise<void> {
    if (this.notifyFn) {
      try {
        await this.notifyFn(msg);
      } catch {
        // non-critical
      }
    }
  }
}

// ── Helpers ─────────────────────────────────────────────────────

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function mapOrderStatus(
  orderStatus: import("../types.js").OrderStatus,
): BasketOrderItemStatus {
  switch (orderStatus) {
    case "pending":
    case "submitted":
      return "submitted";
    case "partial":
      return "partial";
    case "filled":
      return "filled";
    case "cancelled":
    case "expired":
      return "cancelled";
    case "rejected":
      return "rejected";
    default:
      return "submitted";
  }
}

/**
 * Infer the MarketType from a symbol code.
 * (Same logic as order-manager.ts)
 */
function detectMarketType(symbol: string): MarketType {
  const s = symbol.toUpperCase().trim();
  if (/\.OF$/i.test(s)) return "fund";
  if (/\.HK$/i.test(s) || /^\d{4,5}$/.test(s)) return "hk_stock";
  if (/^[A-Z]{1,5}$/.test(s)) return "us_stock";
  if (
    /^(IF|IH|IC|IM|RB|HC|CU|AL|ZN|NI|SN|PB|RU|FU|BU|AU|AG|NR|UR|SA|MA|CF|SR|ZC|JD|LH|AP|CJ|PF)(\d{2,4})?$/i.test(
      s,
    )
  )
    return "futures";
  return "cn_stock";
}
