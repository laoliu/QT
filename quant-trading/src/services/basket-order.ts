/**
 * BasketOrderService — submit a batch of orders as a single "basket".
 *
 * Features:
 *   - Order splitting: large orders are split into smaller child orders
 *     to avoid market impact (configurable max-per-child quantity).
 *   - Concurrent execution: child orders are submitted in parallel
 *     with a configurable concurrency limit.
 *   - Partial-fill tracking: the basket tracks how many children succeeded,
 *     partially filled, or failed.
 *   - Rollback on failure: if too many children fail, outstanding orders
 *     are cancelled automatically.
 *   - Callbacks for progress reporting.
 */
import { randomUUID } from "node:crypto";
import type { OrderRequest } from "../broker/adapter.js";
import type { Order, OrderStatus } from "../types.js";
import type { OrderManager, SubmitResult } from "./order-manager.js";

// ── Types ───────────────────────────────────────────────────────

export type BasketItem = OrderRequest & {
  /** Optional label for display / logging */
  label?: string;
};

export type SplitPolicy = {
  /** Max quantity per child order. Orders exceeding this are split. Default: Infinity (no split). */
  maxChildQty: number;
  /** Round child quantity to nearest multiple of this (e.g. 100 for A-share lots). Default: 100. */
  lotSize: number;
};

export type BasketConfig = {
  /** Max number of child orders submitted concurrently. Default: 5. */
  concurrency: number;
  /** Split policy for large orders. */
  splitPolicy: SplitPolicy;
  /** If the ratio of failed children exceeds this threshold (0-1), trigger rollback. Default: 0.5. */
  failureThreshold: number;
  /** Risk overrides passed to each child order submission. */
  riskOverrides?: { maxOrderValue?: number; maxPositionPct?: number; skipMarketHours?: boolean };
};

export type ChildOrder = {
  childId: string;
  parentItemIndex: number;
  request: OrderRequest;
  result?: SubmitResult;
  status: "pending" | "submitted" | "filled" | "failed" | "cancelled";
};

export type BasketStatus = "pending" | "executing" | "completed" | "partial" | "rolled_back";

export type BasketOrder = {
  basketId: string;
  items: BasketItem[];
  children: ChildOrder[];
  config: BasketConfig;
  status: BasketStatus;
  createdAt: string;
  completedAt?: string;
  /** Summary stats */
  stats: {
    totalChildren: number;
    submitted: number;
    filled: number;
    failed: number;
    cancelled: number;
  };
};

export type BasketProgressCallback = (basket: Readonly<BasketOrder>) => void;

// ── Default config ──────────────────────────────────────────────

const DEFAULT_CONFIG: BasketConfig = {
  concurrency: 5,
  splitPolicy: { maxChildQty: Infinity, lotSize: 100 },
  failureThreshold: 0.5,
};

// ── Service ─────────────────────────────────────────────────────

export class BasketOrderService {
  private readonly orderManager: OrderManager;
  private readonly baskets: Map<string, BasketOrder> = new Map();

  constructor(orderManager: OrderManager) {
    this.orderManager = orderManager;
  }

  /**
   * Create and execute a basket order.
   * Returns the completed BasketOrder with all child results.
   */
  async execute(
    items: BasketItem[],
    configOverrides?: Partial<BasketConfig>,
    progressCb?: BasketProgressCallback,
  ): Promise<BasketOrder> {
    const config: BasketConfig = { ...DEFAULT_CONFIG, ...configOverrides };
    const basketId = randomUUID();
    const now = new Date().toISOString();

    // Split items into children
    const children = this.splitItems(items, config.splitPolicy);

    const basket: BasketOrder = {
      basketId,
      items,
      children,
      config,
      status: "executing",
      createdAt: now,
      stats: {
        totalChildren: children.length,
        submitted: 0,
        filled: 0,
        failed: 0,
        cancelled: 0,
      },
    };

    this.baskets.set(basketId, basket);
    progressCb?.(basket);

    // Execute children with concurrency limit
    await this.executeChildren(basket, progressCb);

    // Determine final status
    const { stats } = basket;
    if (stats.failed > 0 && stats.failed / stats.totalChildren >= config.failureThreshold) {
      // Rollback: cancel remaining open orders
      await this.rollback(basket);
      basket.status = "rolled_back";
    } else if (stats.failed > 0 || stats.cancelled > 0) {
      basket.status = "partial";
    } else {
      basket.status = "completed";
    }

    basket.completedAt = new Date().toISOString();
    progressCb?.(basket);
    return basket;
  }

  /** Get a basket by ID. */
  getBasket(basketId: string): BasketOrder | undefined {
    return this.baskets.get(basketId);
  }

  /** List all baskets. */
  listBaskets(): BasketOrder[] {
    return Array.from(this.baskets.values());
  }

  // ── Internal: split ───────────────────────────────────────────

  /** Visible for testing */
  splitItems(items: BasketItem[], policy: SplitPolicy): ChildOrder[] {
    const children: ChildOrder[] = [];

    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      const qty = item.quantity;
      const maxChild = policy.maxChildQty;
      const lot = policy.lotSize;

      if (qty <= maxChild) {
        // No split needed — round to lot size
        const roundedQty = roundToLot(qty, lot);
        if (roundedQty > 0) {
          children.push({
            childId: randomUUID(),
            parentItemIndex: i,
            request: { ...item, quantity: roundedQty },
            status: "pending",
          });
        }
      } else {
        // Split into chunks
        let remaining = qty;
        while (remaining > 0) {
          const chunk = Math.min(remaining, maxChild);
          const roundedChunk = roundToLot(chunk, lot);
          if (roundedChunk > 0) {
            children.push({
              childId: randomUUID(),
              parentItemIndex: i,
              request: { ...item, quantity: roundedChunk },
              status: "pending",
            });
          }
          remaining -= chunk;
        }
      }
    }

    return children;
  }

  // ── Internal: concurrent execution ────────────────────────────

  private async executeChildren(
    basket: BasketOrder,
    progressCb?: BasketProgressCallback,
  ): Promise<void> {
    const { children, config } = basket;
    const concurrency = config.concurrency;
    let idx = 0;

    const worker = async (): Promise<void> => {
      while (idx < children.length) {
        // Check if we should abort (too many failures)
        if (
          basket.stats.failed > 0 &&
          basket.stats.failed / basket.stats.totalChildren >= config.failureThreshold
        ) {
          // Mark remaining as cancelled
          for (let j = idx; j < children.length; j++) {
            if (children[j].status === "pending") {
              children[j].status = "cancelled";
              basket.stats.cancelled++;
            }
          }
          idx = children.length; // stop all workers
          progressCb?.(basket);
          return;
        }

        const childIdx = idx++;
        if (childIdx >= children.length) break;
        const child = children[childIdx];

        await this.submitChild(child, basket, config);
        progressCb?.(basket);
      }
    };

    // Launch up to `concurrency` workers
    const workers = Array.from({ length: Math.min(concurrency, children.length) }, () => worker());
    await Promise.all(workers);
  }

  private async submitChild(
    child: ChildOrder,
    basket: BasketOrder,
    config: BasketConfig,
  ): Promise<void> {
    if (child.status !== "pending") return;

    try {
      child.status = "submitted";
      basket.stats.submitted++;
      const result = await this.orderManager.submit(child.request, config.riskOverrides);
      child.result = result;

      if (result.ok) {
        child.status = "filled";
        basket.stats.filled++;
      } else {
        child.status = "failed";
        basket.stats.failed++;
      }
    } catch {
      child.status = "failed";
      basket.stats.failed++;
      child.result = { ok: false, reason: "Unexpected error during submission" };
    }
  }

  // ── Internal: rollback ────────────────────────────────────────

  private async rollback(basket: BasketOrder): Promise<void> {
    const cancelPromises: Promise<void>[] = [];

    for (const child of basket.children) {
      // Cancel children that were successfully submitted
      if (child.result?.ok) {
        const order = (child.result as { ok: true; order: Order }).order;
        const status = order.status;
        if (status === "submitted" || status === "partial" || status === "pending") {
          cancelPromises.push(
            this.orderManager.cancel(order.orderId).catch(() => {
              // Best-effort cancel
            }),
          );
          child.status = "cancelled";
          basket.stats.cancelled++;
        }
      }

      // Mark pending children as cancelled
      if (child.status === "pending") {
        child.status = "cancelled";
        basket.stats.cancelled++;
      }
    }

    await Promise.allSettled(cancelPromises);
  }
}

// ── Utility ─────────────────────────────────────────────────────

function roundToLot(qty: number, lot: number): number {
  if (lot <= 0 || lot === 1) return qty;
  return Math.floor(qty / lot) * lot;
}
