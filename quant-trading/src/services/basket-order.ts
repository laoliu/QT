/**
 * BasketOrderService — executes a batch of orders as a single basket,
 * with per-leg retry, timeout handling, basket-level rollback on failure,
 * and execution result summary reporting.
 *
 * Flow:
 *   1. Accept a list of order legs
 *   2. Execute legs sequentially (sell first, then buy — frees cash)
 *   3. On leg failure: retry up to N times
 *   4. On leg timeout: cancel + retry
 *   5. If rollbackOnFailure=true and any leg permanently fails:
 *      cancel all submitted/pending legs → roll back filled legs (reverse orders)
 *   6. Produce a BasketExecutionReport summarising all results
 *
 * Persists basket state to disk for crash recovery.
 */
import { randomUUID } from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { OrderRequest } from "../broker/adapter.js";
import type {
  BasketExecutionReport,
  BasketLeg,
  BasketLegStatus,
  BasketOrder,
  BasketOrderStatus,
  OrderSide,
  OrderType,
  MarketType,
  QuantTradingConfig,
} from "../types.js";
import type { OrderManager, SubmitResult } from "./order-manager.js";

// ── Persistence helpers ─────────────────────────────────────────

function dataDir(cfg: QuantTradingConfig): string {
  return cfg.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
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

// ── Input types ─────────────────────────────────────────────────

export interface BasketLegInput {
  symbol: string;
  side: OrderSide;
  quantity: number;
  orderType?: OrderType;
  price?: number;
  market?: MarketType;
}

export interface BasketOrderOptions {
  name?: string;
  maxRetries?: number;
  legTimeoutMs?: number;
  rollbackOnFailure?: boolean;
  strategyId?: string;
}

// ── Default market detection (mirrors OrderManager logic) ───────

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

// ── Service ─────────────────────────────────────────────────────

const DEFAULT_MAX_RETRIES = 3;
const DEFAULT_LEG_TIMEOUT_MS = 30_000;

export class BasketOrderService {
  private cfg: QuantTradingConfig;
  private orderMgr: OrderManager;
  private logFn: (msg: string) => void;

  constructor(
    cfg: QuantTradingConfig,
    orderMgr: OrderManager,
    logFn?: (msg: string) => void,
  ) {
    this.cfg = cfg;
    this.orderMgr = orderMgr;
    this.logFn = logFn ?? ((msg) => console.log(`[BasketOrder] ${msg}`));
  }

  /**
   * Create and execute a basket order.
   * Returns the execution report when complete.
   */
  async execute(
    legs: BasketLegInput[],
    opts?: BasketOrderOptions,
  ): Promise<BasketExecutionReport> {
    const startTime = new Date().toISOString();
    const startMs = Date.now();

    const basket = this.createBasket(legs, opts);
    await this.persistBasket(basket);

    this.logFn(`篮子交易开始: ${basket.basketId.slice(0, 8)} (${basket.legs.length} 笔)`);

    basket.status = "executing";
    basket.updatedAt = new Date().toISOString();
    await this.persistBasket(basket);

    // Sort: sells first (frees cash), then buys
    const sellLegs = basket.legs.filter((l) => l.side === "sell" || l.side === "sell_close");
    const buyLegs = basket.legs.filter((l) => l.side !== "sell" && l.side !== "sell_close");
    const orderedLegs = [...sellLegs, ...buyLegs];

    let hasFailure = false;

    for (const leg of orderedLegs) {
      const result = await this.executeLegWithRetry(basket, leg);

      if (!result) {
        hasFailure = true;
        this.logFn(
          `篮子 ${basket.basketId.slice(0, 8)} 腿 ${leg.symbol} ${leg.side} 最终失败: ${leg.errorMsg}`,
        );

        if (basket.rollbackOnFailure) {
          this.logFn(`篮子 ${basket.basketId.slice(0, 8)} 触发回滚...`);
          basket.status = "rolling_back";
          basket.updatedAt = new Date().toISOString();
          await this.persistBasket(basket);

          await this.rollback(basket);

          basket.status = "rolled_back";
          basket.updatedAt = new Date().toISOString();
          basket.completedAt = new Date().toISOString();
          await this.persistBasket(basket);
          break;
        }
      }
    }

    if (!hasFailure) {
      basket.status = "completed";
      basket.completedAt = new Date().toISOString();
    } else if (basket.status !== "rolled_back") {
      // Some legs failed but rollback was not requested
      const anyFilled = basket.legs.some((l) => l.status === "filled");
      basket.status = anyFilled ? "partial" : "failed";
      basket.completedAt = new Date().toISOString();
    }
    basket.updatedAt = new Date().toISOString();
    await this.persistBasket(basket);

    const endTime = new Date().toISOString();
    const report = this.buildReport(basket, startTime, endTime, Date.now() - startMs);

    this.logFn(
      `篮子交易结束: ${basket.basketId.slice(0, 8)} 状态=${basket.status} ` +
        `成功=${report.filledLegs}/${report.totalLegs} 耗时=${report.durationMs}ms`,
    );

    return report;
  }

  /** Query a basket order by ID. */
  async getBasket(basketId: string): Promise<BasketOrder | null> {
    const baskets = await loadBaskets(this.cfg);
    return baskets.find((b) => b.basketId === basketId) ?? null;
  }

  /** List all basket orders. */
  async listBaskets(limit = 20): Promise<BasketOrder[]> {
    const baskets = await loadBaskets(this.cfg);
    return baskets.slice(-limit);
  }

  // ── Internal ──────────────────────────────────────────────────

  private createBasket(legs: BasketLegInput[], opts?: BasketOrderOptions): BasketOrder {
    const now = new Date().toISOString();
    return {
      basketId: randomUUID(),
      name: opts?.name,
      status: "pending",
      legs: legs.map((l) => ({
        legId: randomUUID(),
        symbol: l.symbol,
        side: l.side,
        quantity: l.quantity,
        orderType: l.orderType ?? "market",
        price: l.price,
        market: l.market ?? detectMarketType(l.symbol),
        status: "pending" as BasketLegStatus,
        retryCount: 0,
        filledQty: 0,
        errorMsg: undefined,
      })),
      maxRetries: opts?.maxRetries ?? DEFAULT_MAX_RETRIES,
      legTimeoutMs: opts?.legTimeoutMs ?? DEFAULT_LEG_TIMEOUT_MS,
      rollbackOnFailure: opts?.rollbackOnFailure ?? true,
      strategyId: opts?.strategyId,
      createdAt: now,
      updatedAt: now,
    };
  }

  /**
   * Execute a single leg with retry logic and timeout.
   * Returns true if the leg was successfully filled.
   */
  private async executeLegWithRetry(basket: BasketOrder, leg: BasketLeg): Promise<boolean> {
    for (let attempt = 0; attempt <= basket.maxRetries; attempt++) {
      if (attempt > 0) {
        leg.status = "retrying";
        leg.retryCount = attempt;
        basket.updatedAt = new Date().toISOString();
        await this.persistBasket(basket);
        this.logFn(
          `篮子 ${basket.basketId.slice(0, 8)} 腿 ${leg.symbol} 重试 ${attempt}/${basket.maxRetries}`,
        );
        // Exponential backoff: 1s, 2s, 4s
        await sleep(Math.min(1000 * 2 ** (attempt - 1), 8000));
      }

      const result = await this.executeLegOnce(basket, leg);
      if (result) return true;

      // If it's a timeout, the order might still be pending — try to cancel
      if (leg.status === "timeout" && leg.orderId) {
        try {
          await this.orderMgr.cancel(leg.orderId);
        } catch {
          // Best effort cancel
        }
      }
    }

    // All retries exhausted
    leg.status = "failed";
    leg.completedAt = new Date().toISOString();
    basket.updatedAt = new Date().toISOString();
    await this.persistBasket(basket);
    return false;
  }

  /**
   * Execute a single leg once, with timeout.
   * Returns true if filled successfully.
   */
  private async executeLegOnce(basket: BasketOrder, leg: BasketLeg): Promise<boolean> {
    const req: OrderRequest = {
      symbol: leg.symbol,
      market: leg.market ?? detectMarketType(leg.symbol),
      side: leg.side,
      orderType: leg.orderType,
      quantity: leg.quantity,
      price: leg.price,
      strategyId: basket.strategyId ?? "basket",
      signalId: `basket_${basket.basketId.slice(0, 8)}_${leg.legId.slice(0, 8)}`,
    };

    // Submit order
    let submitResult: SubmitResult;
    try {
      submitResult = await this.orderMgr.submit(req, { skipMarketHours: true });
    } catch (err) {
      leg.status = "failed";
      leg.errorMsg = `提交异常: ${(err as Error).message}`;
      return false;
    }

    if (!submitResult.ok) {
      leg.status = "failed";
      leg.errorMsg = submitResult.reason;
      return false;
    }

    leg.orderId = submitResult.order.orderId;
    leg.status = "submitted";
    leg.submittedAt = new Date().toISOString();
    basket.updatedAt = new Date().toISOString();
    await this.persistBasket(basket);

    // Wait for fill with timeout
    const filled = await this.waitForFill(leg.orderId, basket.legTimeoutMs);

    if (filled) {
      // Query final order state
      try {
        const order = await this.orderMgr.broker.queryOrder(leg.orderId);
        if (order) {
          leg.filledQty = order.filledQty;
          leg.avgFillPrice = order.avgFillPrice;
        }
      } catch {
        // Best effort; filledQty may not be updated
      }
      leg.status = "filled";
      leg.completedAt = new Date().toISOString();
      basket.updatedAt = new Date().toISOString();
      await this.persistBasket(basket);
      return true;
    }

    // Timeout
    leg.status = "timeout";
    leg.errorMsg = `超时 (${basket.legTimeoutMs}ms)`;
    return false;
  }

  /**
   * Poll order status until filled or timeout.
   */
  private async waitForFill(orderId: string, timeoutMs: number): Promise<boolean> {
    const deadline = Date.now() + timeoutMs;
    const pollInterval = Math.min(1000, timeoutMs / 10);

    while (Date.now() < deadline) {
      try {
        const order = await this.orderMgr.broker.queryOrder(orderId);
        if (!order) return false;

        if (order.status === "filled") return true;
        if (order.status === "rejected" || order.status === "cancelled" || order.status === "expired") {
          return false;
        }
      } catch {
        // Query failed — keep polling
      }
      await sleep(pollInterval);
    }
    return false;
  }

  /**
   * Rollback: cancel all pending/submitted legs, then reverse filled legs.
   */
  private async rollback(basket: BasketOrder): Promise<void> {
    // Step 1: Cancel all non-terminal legs
    for (const leg of basket.legs) {
      if (
        (leg.status === "submitted" || leg.status === "pending" || leg.status === "retrying") &&
        leg.orderId
      ) {
        try {
          await this.orderMgr.cancel(leg.orderId);
          leg.status = "cancelled";
          this.logFn(`回滚: 撤销 ${leg.symbol} ${leg.side} 订单 ${leg.orderId.slice(0, 8)}`);
        } catch (err) {
          this.logFn(`回滚: 撤销 ${leg.symbol} 失败: ${(err as Error).message}`);
        }
      }
    }

    // Step 2: Reverse filled legs (buy→sell, sell→buy)
    const filledLegs = basket.legs.filter((l) => l.status === "filled" && l.filledQty > 0);

    for (const leg of filledLegs) {
      const reverseSide = this.getReverseSide(leg.side);
      if (!reverseSide) continue;

      const reverseReq: OrderRequest = {
        symbol: leg.symbol,
        market: leg.market ?? detectMarketType(leg.symbol),
        side: reverseSide,
        orderType: "market",
        quantity: leg.filledQty,
        strategyId: "basket-rollback",
        signalId: `rollback_${basket.basketId.slice(0, 8)}_${leg.legId.slice(0, 8)}`,
      };

      try {
        const result = await this.orderMgr.submit(reverseReq, { skipMarketHours: true });
        if (result.ok) {
          this.logFn(
            `回滚: 反向 ${reverseSide} ${leg.symbol} ${leg.filledQty} 股 已提交`,
          );
        } else {
          this.logFn(
            `回滚: 反向 ${reverseSide} ${leg.symbol} 失败: ${result.reason}`,
          );
        }
      } catch (err) {
        this.logFn(`回滚: 反向 ${leg.symbol} 异常: ${(err as Error).message}`);
      }
    }
  }

  private getReverseSide(side: OrderSide): OrderSide | null {
    switch (side) {
      case "buy":
        return "sell";
      case "sell":
        return "buy";
      case "buy_open":
        return "sell_close";
      case "sell_open":
        return "buy_close";
      case "sell_close":
        return "buy_open";
      case "buy_close":
        return "sell_open";
      case "short":
        return "cover";
      case "cover":
        return "short";
      default:
        return null;
    }
  }

  private buildReport(
    basket: BasketOrder,
    startTime: string,
    endTime: string,
    durationMs: number,
  ): BasketExecutionReport {
    const filledLegs = basket.legs.filter((l) => l.status === "filled").length;
    const failedLegs = basket.legs.filter((l) => l.status === "failed" || l.status === "timeout").length;
    const cancelledLegs = basket.legs.filter((l) => l.status === "cancelled").length;

    const totalValue = basket.legs
      .filter((l) => l.status === "filled" && l.avgFillPrice)
      .reduce((sum, l) => sum + l.filledQty * (l.avgFillPrice ?? 0), 0);

    return {
      basketId: basket.basketId,
      name: basket.name,
      status: basket.status,
      totalLegs: basket.legs.length,
      filledLegs,
      failedLegs,
      cancelledLegs,
      totalValue,
      legs: basket.legs.map((l) => ({
        legId: l.legId,
        symbol: l.symbol,
        side: l.side,
        requestedQty: l.quantity,
        filledQty: l.filledQty,
        avgFillPrice: l.avgFillPrice,
        status: l.status,
        retryCount: l.retryCount,
        errorMsg: l.errorMsg,
      })),
      startTime,
      endTime,
      durationMs,
    };
  }

  private async persistBasket(basket: BasketOrder): Promise<void> {
    const baskets = await loadBaskets(this.cfg);
    const idx = baskets.findIndex((b) => b.basketId === basket.basketId);
    if (idx >= 0) {
      baskets[idx] = basket;
    } else {
      baskets.push(basket);
    }
    await saveBaskets(baskets, this.cfg);
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
