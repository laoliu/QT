/**
 * BasketOrderService — 篮子订单执行调度与状态聚合。
 *
 * 功能:
 *   1. 通过 Broker 适配器批量下单（按并发度分批提交）
 *   2. 并发执行控制（可配置最大并行腿数）
 *   3. 单腿状态回调汇总为篮子整体状态
 *   4. 部分成交处理（根据 failurePolicy 决定后续行为）
 *
 * 持久化:
 *   basket-orders.json — 篮子订单列表（支持重启恢复）
 */
import { randomUUID } from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { OrderRequest } from "../broker/adapter.js";
import type {
  BasketLeg,
  BasketLegStatus,
  BasketOrder,
  BasketOrderStatus,
  MarketType,
  OrderSide,
  OrderStatus,
  OrderType,
  QuantTradingConfig,
} from "../types.js";
import { AsyncMutex } from "../utils/async-mutex.js";
import type { OrderManager, SubmitResult } from "./order-manager.js";

const basketMutex = new AsyncMutex();

// ── File helpers ─────────────────────────────────────────────────

function dataDir(cfg: QuantTradingConfig): string {
  return cfg.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
}

function basketsPath(cfg: QuantTradingConfig): string {
  return path.join(dataDir(cfg), "basket-orders.json");
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

// ── Leg request (caller-facing input) ───────────────────────────

export type BasketLegRequest = {
  symbol: string;
  side: OrderSide;
  orderType: OrderType;
  quantity: number;
  price?: number;
  market?: MarketType;
};

export type CreateBasketRequest = {
  name?: string;
  legs: BasketLegRequest[];
  /** 并发下单数 (默认 5) */
  concurrency?: number;
  /** 单腿失败策略 (默认 continue) */
  failurePolicy?: "continue" | "cancel_remaining";
  strategyId?: string;
};

// ── Status mapping ──────────────────────────────────────────────

const ORDER_TO_LEG_STATUS: Record<OrderStatus, BasketLegStatus> = {
  pending: "pending",
  submitted: "submitted",
  partial: "partial",
  filled: "filled",
  cancelled: "cancelled",
  rejected: "rejected",
  expired: "cancelled",
};

// ── BasketOrderService ──────────────────────────────────────────

export class BasketOrderService {
  private cfg: QuantTradingConfig;
  private getOrderMgr: () => OrderManager | null;
  private notifyFn?: (msg: string) => void | Promise<void>;
  /** In-memory index: basketId → BasketOrder (kept in sync with disk). */
  private baskets = new Map<string, BasketOrder>();
  /** Maps child orderId → { basketId, legIndex } for status callback routing. */
  private orderToLeg = new Map<string, { basketId: string; legIndex: number }>();

  constructor(cfg: QuantTradingConfig, getOrderMgr: () => OrderManager | null) {
    this.cfg = cfg;
    this.getOrderMgr = getOrderMgr;
  }

  setNotify(fn: (msg: string) => void | Promise<void>): void {
    this.notifyFn = fn;
  }

  // ── Lifecycle ─────────────────────────────────────────────────

  /** Load persisted baskets from disk (call at startup). */
  async load(): Promise<void> {
    const baskets = await loadBaskets(this.cfg);
    for (const b of baskets) {
      this.baskets.set(b.basketId, b);
      // Rebuild orderToLeg index
      for (const leg of b.legs) {
        if (leg.orderId) {
          this.orderToLeg.set(leg.orderId, { basketId: b.basketId, legIndex: leg.index });
        }
      }
    }
  }

  destroy(): void {
    // No timers to clean up; persistence already flushed on each mutation.
  }

  // ── CRUD ──────────────────────────────────────────────────────

  /**
   * 创建篮子订单并立即开始执行。
   * 返回 BasketOrder（状态为 executing）。
   */
  async createAndExecute(req: CreateBasketRequest): Promise<BasketOrder> {
    const now = new Date().toISOString();
    const basket: BasketOrder = {
      basketId: randomUUID(),
      name: req.name,
      status: "pending",
      legs: req.legs.map((l, i) => ({
        index: i,
        symbol: l.symbol,
        side: l.side,
        orderType: l.orderType,
        quantity: l.quantity,
        price: l.price,
        status: "pending",
        filledQty: 0,
      })),
      concurrency: req.concurrency ?? 5,
      failurePolicy: req.failurePolicy ?? "continue",
      createdAt: now,
      updatedAt: now,
      strategyId: req.strategyId,
    };

    this.baskets.set(basket.basketId, basket);
    await this.persist();

    this.log(`篮子订单创建: ${basket.name ?? basket.basketId.slice(0, 8)}, ${basket.legs.length} 条腿`);

    // Start async execution (don't await — caller gets immediate response)
    void this.executeBasket(basket);

    return basket;
  }

  /** 取消篮子中所有未完成的腿。 */
  async cancelBasket(basketId: string): Promise<boolean> {
    const basket = this.baskets.get(basketId);
    if (!basket) return false;

    const terminal: BasketOrderStatus[] = ["completed", "failed", "cancelled", "partial_filled"];
    if (terminal.includes(basket.status)) return false;

    const mgr = this.getOrderMgr();
    // Cancel all non-terminal legs
    for (const leg of basket.legs) {
      if (leg.orderId && !isLegTerminal(leg.status)) {
        try {
          await mgr?.cancel(leg.orderId);
          leg.status = "cancelled";
          leg.updatedAt = new Date().toISOString();
        } catch {
          // best-effort cancel
        }
      } else if (leg.status === "pending") {
        leg.status = "cancelled";
        leg.updatedAt = new Date().toISOString();
      }
    }

    basket.status = "cancelled";
    basket.updatedAt = new Date().toISOString();
    basket.completedAt = basket.updatedAt;
    await this.persist();

    this.log(`篮子订单已取消: ${basket.name ?? basketId.slice(0, 8)}`);
    return true;
  }

  /** 查询篮子订单。 */
  getBasket(basketId: string): BasketOrder | undefined {
    return this.baskets.get(basketId);
  }

  /** 列出所有篮子订单（可按状态过滤）。 */
  listBaskets(status?: BasketOrderStatus): BasketOrder[] {
    const all = [...this.baskets.values()];
    return status ? all.filter((b) => b.status === status) : all;
  }

  // ── Order status callback (wire from OrderManager) ────────────

  /**
   * 处理子订单状态变更，更新腿状态并重新聚合篮子整体状态。
   * 应由 broker 的 subscribeOrderStatus 回调调用。
   */
  async handleOrderStatusChange(orderId: string, status: OrderStatus): Promise<void> {
    const mapping = this.orderToLeg.get(orderId);
    if (!mapping) return;

    const basket = this.baskets.get(mapping.basketId);
    if (!basket) return;

    const leg = basket.legs[mapping.legIndex];
    if (!leg) return;

    const legStatus = ORDER_TO_LEG_STATUS[status] ?? "pending";
    leg.status = legStatus;
    leg.updatedAt = new Date().toISOString();

    // Refresh filled qty from the OrderManager if available
    const mgr = this.getOrderMgr();
    if (mgr && leg.orderId) {
      try {
        const orders = await mgr.getOrders({ status: undefined });
        const order = orders.find((o) => o.orderId === leg.orderId);
        if (order) {
          leg.filledQty = order.filledQty;
          leg.avgFillPrice = order.avgFillPrice;
        }
      } catch {
        // best-effort
      }
    }

    // Re-aggregate basket status
    this.aggregateBasketStatus(basket);
    await this.persist();
  }

  /**
   * 处理子订单成交回调，更新腿的成交数据。
   */
  async handleFill(orderId: string, fillQty: number, fillPrice: number): Promise<void> {
    const mapping = this.orderToLeg.get(orderId);
    if (!mapping) return;

    const basket = this.baskets.get(mapping.basketId);
    if (!basket) return;

    const leg = basket.legs[mapping.legIndex];
    if (!leg) return;

    const prevFilled = leg.filledQty;
    leg.filledQty = prevFilled + fillQty;
    const prevAvg = leg.avgFillPrice ?? 0;
    leg.avgFillPrice =
      prevFilled > 0
        ? (prevAvg * prevFilled + fillPrice * fillQty) / leg.filledQty
        : fillPrice;
    leg.status = leg.filledQty >= leg.quantity ? "filled" : "partial";
    leg.updatedAt = new Date().toISOString();

    this.aggregateBasketStatus(basket);
    await this.persist();
  }

  // ── Execution engine ──────────────────────────────────────────

  private async executeBasket(basket: BasketOrder): Promise<void> {
    const mgr = this.getOrderMgr();
    if (!mgr) {
      for (const leg of basket.legs) {
        leg.status = "failed";
        leg.errorMsg = "交易引擎未启动";
      }
      basket.status = "failed";
      basket.updatedAt = new Date().toISOString();
      basket.completedAt = basket.updatedAt;
      await this.persist();
      this.log(`篮子执行失败: 交易引擎未启动`);
      return;
    }

    basket.status = "executing";
    basket.updatedAt = new Date().toISOString();
    await this.persist();

    // Split legs into batches based on concurrency
    const pendingLegs = [...basket.legs];
    let cancelled = false;

    // Process with concurrency control using a semaphore pattern
    let activeCount = 0;
    let legIdx = 0;

    const submitLeg = async (leg: BasketLeg): Promise<void> => {
      const req: OrderRequest = {
        symbol: leg.symbol,
        market: this.detectMarket(leg.symbol),
        side: leg.side,
        orderType: leg.orderType,
        quantity: leg.quantity,
        price: leg.price,
        strategyId: basket.strategyId,
        signalId: `basket_${basket.basketId}_leg${leg.index}`,
      };

      let result: SubmitResult;
      try {
        result = await mgr.submit(req);
      } catch (err) {
        leg.status = "failed";
        leg.errorMsg = (err as Error).message;
        leg.updatedAt = new Date().toISOString();
        return;
      }

      if (result.ok) {
        leg.orderId = result.order.orderId;
        leg.status = ORDER_TO_LEG_STATUS[result.order.status] ?? "submitted";
        leg.updatedAt = new Date().toISOString();
        // Register for status routing
        this.orderToLeg.set(result.order.orderId, {
          basketId: basket.basketId,
          legIndex: leg.index,
        });
      } else {
        leg.status = "rejected";
        leg.errorMsg = result.reason;
        leg.updatedAt = new Date().toISOString();
      }
    };

    // Execute legs with concurrency limit
    const executeWithConcurrency = async (): Promise<void> => {
      const promises: Promise<void>[] = [];

      const scheduleNext = (): void => {
        while (activeCount < basket.concurrency && legIdx < pendingLegs.length && !cancelled) {
          const leg = pendingLegs[legIdx++];
          activeCount++;
          const p = submitLeg(leg).then(() => {
            activeCount--;

            // Check failure policy
            if (
              leg.status === "failed" || leg.status === "rejected"
            ) {
              if (basket.failurePolicy === "cancel_remaining") {
                cancelled = true;
                // Mark remaining unstarted legs as cancelled
                for (let i = legIdx; i < pendingLegs.length; i++) {
                  if (pendingLegs[i].status === "pending") {
                    pendingLegs[i].status = "cancelled";
                    pendingLegs[i].updatedAt = new Date().toISOString();
                  }
                }
              }
            }

            // Schedule more if available
            scheduleNext();
          });
          promises.push(p);
        }
      };

      scheduleNext();
      await Promise.all(promises);
    };

    await executeWithConcurrency();

    // Final status aggregation
    this.aggregateBasketStatus(basket);
    basket.completedAt = new Date().toISOString();
    await this.persist();

    // Notify
    const summary = this.buildSummary(basket);
    this.log(summary);
    void this.notifyFn?.(summary);
  }

  // ── Status aggregation ────────────────────────────────────────

  /** 根据所有腿的状态聚合计算篮子整体状态。 */
  private aggregateBasketStatus(basket: BasketOrder): void {
    const legs = basket.legs;
    const total = legs.length;
    if (total === 0) {
      basket.status = "completed";
      return;
    }

    const filledCount = legs.filter((l) => l.status === "filled").length;
    const failedCount = legs.filter(
      (l) => l.status === "failed" || l.status === "rejected",
    ).length;
    const cancelledCount = legs.filter((l) => l.status === "cancelled").length;
    const terminalCount = filledCount + failedCount + cancelledCount;
    const pendingCount = legs.filter((l) => l.status === "pending").length;
    const executingCount = legs.filter(
      (l) => l.status === "submitted" || l.status === "partial",
    ).length;

    if (filledCount === total) {
      basket.status = "completed";
    } else if (failedCount + cancelledCount === total) {
      basket.status = "failed";
    } else if (terminalCount === total) {
      // All legs are terminal but not all filled → partial_filled
      basket.status = filledCount > 0 ? "partial_filled" : "failed";
    } else if (executingCount > 0 || (pendingCount > 0 && filledCount + failedCount + cancelledCount > 0)) {
      // Some legs still executing
      basket.status = filledCount > 0 ? "partial" : "executing";
    }
    // Otherwise keep current status (e.g. "executing")

    basket.updatedAt = new Date().toISOString();
  }

  // ── Helpers ───────────────────────────────────────────────────

  private buildSummary(basket: BasketOrder): string {
    const name = basket.name ?? basket.basketId.slice(0, 8);
    const filled = basket.legs.filter((l) => l.status === "filled").length;
    const failed = basket.legs.filter(
      (l) => l.status === "failed" || l.status === "rejected",
    ).length;
    const cancelled = basket.legs.filter((l) => l.status === "cancelled").length;
    const total = basket.legs.length;

    const statusLabel: Record<BasketOrderStatus, string> = {
      pending: "待执行",
      executing: "执行中",
      partial: "部分执行",
      completed: "全部成交",
      failed: "全部失败",
      cancelled: "已取消",
      partial_filled: "部分成交",
    };

    return (
      `[篮子订单] ${name} — ${statusLabel[basket.status]}\n` +
      `  成交: ${filled}/${total}, 失败: ${failed}, 取消: ${cancelled}`
    );
  }

  private detectMarket(symbol: string): MarketType {
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

  private async persist(): Promise<void> {
    const release = await basketMutex.acquire();
    try {
      await saveBaskets([...this.baskets.values()], this.cfg);
    } finally {
      release();
    }
  }

  private log(msg: string): void {
    console.log(`[BasketOrder] ${msg}`);
  }
}

// ── Utility ─────────────────────────────────────────────────────

function isLegTerminal(status: BasketLegStatus): boolean {
  return status === "filled" || status === "cancelled" || status === "rejected" || status === "failed";
}
