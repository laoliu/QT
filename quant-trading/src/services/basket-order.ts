/**
 * BasketOrderService — executes a batch of orders as a single basket,
 * with per-leg retry, timeout handling, basket-level rollback on failure,
 * and execution result summary reporting.
 *
 * Flow:
 *   1. Accept a list of order legs
 *   2. Execute legs sequentially (sell first, then buy — frees cash)
 *   3. On leg failure: classify error; retry only if retryable
 *   4. On leg timeout: cancel + retry
 *   5. If rollbackOnFailure=true and any leg permanently fails:
 *      cancel all submitted/pending legs → roll back filled legs (reverse orders with retry)
 *   6. Produce a BasketExecutionReport summarising all results (including rollback detail)
 *
 * Additional features:
 *   - Partial fill detection: legs with partial fills are tracked and properly reversed
 *   - Error classification: non-retryable errors (insufficient funds, risk rejection) skip retry
 *   - Rollback retry: reverse orders are retried and their fill status is tracked
 *   - Manual cancel/rollback: external callers can cancel or roll back an executing/partial basket
 *   - Resume: partially-executed baskets can be resumed (re-runs only pending legs)
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
  BasketRollbackDetail,
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

// ── Error classification ────────────────────────────────────────

/** Errors that should NOT be retried (permanent failures). */
const NON_RETRYABLE_PATTERNS = [
  /资金不足/i,
  /insufficient/i,
  /持仓不足/i,
  /insufficient.*position/i,
  /风控拒绝/i,
  /risk.*reject/i,
  /risk.*denied/i,
  /BLOCKED_SYMBOL/i,
  /ALLOWED_SYMBOL/i,
  /MAX_POSITION_PCT/i,
  /MAX_DAILY_LOSS/i,
  /MAX_TOTAL_LOSS/i,
  /STOP_LOSS/i,
  /INSUFFICIENT_CASH/i,
  /INSUFFICIENT_POSITION/i,
  /symbol.*not.*found/i,
  /代码.*不存在/i,
  /无此证券/i,
];

function isRetryableError(errorMsg: string | undefined): boolean {
  if (!errorMsg) return true;
  return !NON_RETRYABLE_PATTERNS.some((p) => p.test(errorMsg));
}

// ── Service ─────────────────────────────────────────────────────

const DEFAULT_MAX_RETRIES = 3;
const DEFAULT_LEG_TIMEOUT_MS = 30_000;
const ROLLBACK_TIMEOUT_MS = 15_000;
const ROLLBACK_MAX_RETRIES = 2;

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

    await this.executeLegs(basket);

    const endTime = new Date().toISOString();
    const report = this.buildReport(basket, startTime, endTime, Date.now() - startMs);

    this.logFn(
      `篮子交易结束: ${basket.basketId.slice(0, 8)} 状态=${basket.status} ` +
        `成功=${report.filledLegs}/${report.totalLegs} 耗时=${report.durationMs}ms`,
    );

    return report;
  }

  /**
   * Resume a partially-executed basket (re-runs only pending/failed legs).
   */
  async resume(basketId: string): Promise<BasketExecutionReport> {
    const basket = await this.getBasket(basketId);
    if (!basket) throw new Error(`篮子 ${basketId} 未找到`);

    if (basket.status !== "partial" && basket.status !== "failed") {
      throw new Error(`篮子状态为 ${basket.status}，无法恢复执行（仅支持 partial/failed）`);
    }

    const startTime = new Date().toISOString();
    const startMs = Date.now();

    // Reset failed/timeout legs to pending for re-execution
    for (const leg of basket.legs) {
      if (leg.status === "failed" || leg.status === "timeout") {
        leg.status = "pending";
        leg.errorMsg = undefined;
        leg.retryCount = 0;
      }
    }

    basket.status = "executing";
    basket.updatedAt = new Date().toISOString();
    basket.completedAt = undefined;
    await this.persistBasket(basket);

    this.logFn(`篮子恢复执行: ${basket.basketId.slice(0, 8)}`);

    await this.executeLegs(basket);

    const endTime = new Date().toISOString();
    const report = this.buildReport(basket, startTime, endTime, Date.now() - startMs);

    this.logFn(
      `篮子恢复结束: ${basket.basketId.slice(0, 8)} 状态=${basket.status} ` +
        `成功=${report.filledLegs}/${report.totalLegs}`,
    );

    return report;
  }

  /**
   * Manually cancel a basket — cancels all pending/submitted legs.
   * Does NOT reverse filled legs. Use cancelAndRollback() for full reversal.
   */
  async cancelBasket(basketId: string): Promise<BasketExecutionReport> {
    const basket = await this.getBasket(basketId);
    if (!basket) throw new Error(`篮子 ${basketId} 未找到`);

    if (basket.status === "completed" || basket.status === "rolled_back") {
      throw new Error(`篮子状态为 ${basket.status}，无需撤销`);
    }

    const startTime = new Date().toISOString();
    const startMs = Date.now();

    this.logFn(`手动撤销篮子: ${basket.basketId.slice(0, 8)}`);

    for (const leg of basket.legs) {
      if (
        (leg.status === "submitted" || leg.status === "pending" || leg.status === "retrying") &&
        leg.orderId
      ) {
        try {
          await this.orderMgr.cancel(leg.orderId);
          leg.status = "cancelled";
          this.logFn(`撤销: ${leg.symbol} ${leg.side} 订单 ${leg.orderId.slice(0, 8)}`);
        } catch (err) {
          this.logFn(`撤销失败: ${leg.symbol} — ${(err as Error).message}`);
        }
      } else if (leg.status === "pending") {
        leg.status = "cancelled";
      }
    }

    const anyFilled = basket.legs.some((l) => l.status === "filled" || l.status === "partial_filled");
    basket.status = anyFilled ? "partial" : "failed";
    basket.completedAt = new Date().toISOString();
    basket.updatedAt = new Date().toISOString();
    await this.persistBasket(basket);

    const endTime = new Date().toISOString();
    return this.buildReport(basket, startTime, endTime, Date.now() - startMs);
  }

  /**
   * Cancel a basket AND roll back all filled legs (reverse orders).
   */
  async cancelAndRollback(basketId: string): Promise<BasketExecutionReport> {
    const basket = await this.getBasket(basketId);
    if (!basket) throw new Error(`篮子 ${basketId} 未找到`);

    if (basket.status === "rolled_back") {
      throw new Error(`篮子已回滚，无需重复操作`);
    }

    const startTime = new Date().toISOString();
    const startMs = Date.now();

    this.logFn(`手动撤销并回滚篮子: ${basket.basketId.slice(0, 8)}`);

    basket.status = "rolling_back";
    basket.updatedAt = new Date().toISOString();
    await this.persistBasket(basket);

    await this.rollback(basket);

    basket.status = "rolled_back";
    basket.completedAt = new Date().toISOString();
    basket.updatedAt = new Date().toISOString();
    await this.persistBasket(basket);

    const endTime = new Date().toISOString();
    return this.buildReport(basket, startTime, endTime, Date.now() - startMs);
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

  /**
   * Core execution loop — processes all pending legs in order (sell first, buy second).
   * Updates basket status when complete.
   */
  private async executeLegs(basket: BasketOrder): Promise<void> {
    // Sort: sells first (frees cash), then buys — only run pending legs
    const pendingLegs = basket.legs.filter(
      (l) => l.status === "pending" || l.status === "retrying",
    );
    const sellLegs = pendingLegs.filter((l) => l.side === "sell" || l.side === "sell_close");
    const buyLegs = pendingLegs.filter((l) => l.side !== "sell" && l.side !== "sell_close");
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
          return;
        }
      }
    }

    if (!hasFailure) {
      basket.status = "completed";
      basket.completedAt = new Date().toISOString();
    } else if (basket.status !== "rolled_back") {
      const anyFilled = basket.legs.some(
        (l) => l.status === "filled" || l.status === "partial_filled",
      );
      basket.status = anyFilled ? "partial" : "failed";
      basket.completedAt = new Date().toISOString();
    }
    basket.updatedAt = new Date().toISOString();
    await this.persistBasket(basket);
  }

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
   * Skips retry for non-retryable errors (risk rejection, insufficient funds, etc.).
   * Returns true if the leg was successfully filled.
   */
  private async executeLegWithRetry(basket: BasketOrder, leg: BasketLeg): Promise<boolean> {
    for (let attempt = 0; attempt <= basket.maxRetries; attempt++) {
      if (attempt > 0) {
        // Check if the error is retryable before wasting time
        if (!isRetryableError(leg.errorMsg)) {
          this.logFn(
            `篮子 ${basket.basketId.slice(0, 8)} 腿 ${leg.symbol} 不可重试错误: ${leg.errorMsg}`,
          );
          break;
        }

        leg.status = "retrying";
        leg.retryCount = attempt;
        basket.updatedAt = new Date().toISOString();
        await this.persistBasket(basket);
        this.logFn(
          `篮子 ${basket.basketId.slice(0, 8)} 腿 ${leg.symbol} 重试 ${attempt}/${basket.maxRetries}`,
        );
        // Exponential backoff: 1s, 2s, 4s (cap at 8s)
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
    if (leg.status !== "partial_filled") {
      leg.status = "failed";
    }
    leg.completedAt = new Date().toISOString();
    basket.updatedAt = new Date().toISOString();
    await this.persistBasket(basket);
    return false;
  }

  /**
   * Execute a single leg once, with timeout.
   * Detects partial fills and updates leg accordingly.
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
    const fillResult = await this.waitForFill(leg.orderId, basket.legTimeoutMs);

    if (fillResult === "filled") {
      await this.updateLegFromOrder(leg);
      leg.status = "filled";
      leg.completedAt = new Date().toISOString();
      basket.updatedAt = new Date().toISOString();
      await this.persistBasket(basket);
      return true;
    }

    if (fillResult === "partial") {
      // Partial fill — update what we got, mark accordingly
      await this.updateLegFromOrder(leg);
      leg.status = "partial_filled";
      leg.errorMsg = `部分成交 ${leg.filledQty}/${leg.quantity}`;
      this.logFn(
        `篮子 ${basket.basketId.slice(0, 8)} 腿 ${leg.symbol} 部分成交: ${leg.filledQty}/${leg.quantity}`,
      );
      // Cancel the remaining unfilled portion
      try {
        await this.orderMgr.cancel(leg.orderId);
      } catch {
        // Best effort
      }
      // Treat partial fill as a success (the filled portion stands)
      leg.completedAt = new Date().toISOString();
      basket.updatedAt = new Date().toISOString();
      await this.persistBasket(basket);
      return true;
    }

    if (fillResult === "rejected") {
      leg.status = "failed";
      leg.errorMsg = "订单被拒绝";
      return false;
    }

    // Timeout
    leg.status = "timeout";
    leg.errorMsg = `超时 (${basket.legTimeoutMs}ms)`;
    // Check if there was a partial fill during timeout
    await this.updateLegFromOrder(leg);
    if (leg.filledQty > 0) {
      leg.status = "partial_filled";
      leg.errorMsg = `超时, 部分成交 ${leg.filledQty}/${leg.quantity}`;
    }
    return false;
  }

  /** Query broker for latest order state and update leg's fill info. */
  private async updateLegFromOrder(leg: BasketLeg): Promise<void> {
    if (!leg.orderId) return;
    try {
      const order = await this.orderMgr.broker.queryOrder(leg.orderId);
      if (order) {
        leg.filledQty = order.filledQty;
        leg.avgFillPrice = order.avgFillPrice;
      }
    } catch {
      // Best effort
    }
  }

  /**
   * Poll order status until filled, partial timeout, or terminal.
   * Returns "filled", "partial", "rejected", or "timeout".
   */
  private async waitForFill(
    orderId: string,
    timeoutMs: number,
  ): Promise<"filled" | "partial" | "rejected" | "timeout"> {
    const deadline = Date.now() + timeoutMs;
    const pollInterval = Math.min(1000, timeoutMs / 10);

    while (Date.now() < deadline) {
      try {
        const order = await this.orderMgr.broker.queryOrder(orderId);
        if (!order) return "timeout";

        if (order.status === "filled") return "filled";
        if (order.status === "rejected") return "rejected";
        if (order.status === "cancelled" || order.status === "expired") {
          return order.filledQty > 0 ? "partial" : "rejected";
        }
        // Check for partial fills that have stalled
        if (order.status === "partial" && order.filledQty > 0) {
          // Keep waiting — market may still fill the rest
        }
      } catch {
        // Query failed — keep polling
      }
      await sleep(pollInterval);
    }

    // Final check on timeout — might have partially filled
    try {
      const order = await this.orderMgr.broker.queryOrder(orderId);
      if (order?.status === "filled") return "filled";
      if (order && order.filledQty > 0) return "partial";
    } catch {
      // ignore
    }
    return "timeout";
  }

  /**
   * Rollback: cancel all pending/submitted legs, then reverse filled legs.
   * Reverse orders are retried and their fill status is tracked.
   */
  private async rollback(basket: BasketOrder): Promise<void> {
    const details: BasketRollbackDetail[] = [];

    // Step 1: Cancel all non-terminal legs
    for (const leg of basket.legs) {
      if (
        (leg.status === "submitted" || leg.status === "pending" || leg.status === "retrying") &&
        leg.orderId
      ) {
        const detail: BasketRollbackDetail = {
          legId: leg.legId,
          symbol: leg.symbol,
          action: "cancel",
          success: false,
        };
        try {
          await this.orderMgr.cancel(leg.orderId);
          leg.status = "cancelled";
          detail.success = true;
          this.logFn(`回滚: 撤销 ${leg.symbol} ${leg.side} 订单 ${leg.orderId.slice(0, 8)}`);
        } catch (err) {
          detail.errorMsg = (err as Error).message;
          this.logFn(`回滚: 撤销 ${leg.symbol} 失败: ${(err as Error).message}`);
        }
        details.push(detail);
      } else if (leg.status === "pending") {
        leg.status = "cancelled";
        details.push({
          legId: leg.legId,
          symbol: leg.symbol,
          action: "cancel",
          success: true,
        });
      }
    }

    // Step 2: Reverse filled / partial-filled legs (buy→sell, sell→buy)
    const filledLegs = basket.legs.filter(
      (l) => (l.status === "filled" || l.status === "partial_filled") && l.filledQty > 0,
    );

    for (const leg of filledLegs) {
      const reverseSide = this.getReverseSide(leg.side);
      if (!reverseSide) continue;

      const detail: BasketRollbackDetail = {
        legId: leg.legId,
        symbol: leg.symbol,
        action: "reverse",
        reverseSide,
        reverseQty: leg.filledQty,
        success: false,
        reverseFilled: false,
      };

      // Retry reverse orders
      for (let attempt = 0; attempt <= ROLLBACK_MAX_RETRIES; attempt++) {
        if (attempt > 0) {
          this.logFn(`回滚: 反向 ${leg.symbol} 重试 ${attempt}/${ROLLBACK_MAX_RETRIES}`);
          await sleep(1000 * attempt);
        }

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
            detail.reverseOrderId = result.order.orderId;
            detail.success = true;
            this.logFn(
              `回滚: 反向 ${reverseSide} ${leg.symbol} ${leg.filledQty} 股 已提交`,
            );

            // Wait for the reverse order to fill
            const fillResult = await this.waitForFill(result.order.orderId, ROLLBACK_TIMEOUT_MS);
            detail.reverseFilled = fillResult === "filled" || fillResult === "partial";

            if (detail.reverseFilled) {
              this.logFn(`回滚: 反向 ${leg.symbol} 已成交`);
            } else {
              this.logFn(`回滚: 反向 ${leg.symbol} 未完全成交 (${fillResult})`);
            }
            break; // Don't retry if submit succeeded (even if fill is pending)
          } else {
            detail.errorMsg = result.reason;
            this.logFn(
              `回滚: 反向 ${reverseSide} ${leg.symbol} 失败: ${result.reason}`,
            );
            if (!isRetryableError(result.reason)) break;
          }
        } catch (err) {
          detail.errorMsg = (err as Error).message;
          this.logFn(`回滚: 反向 ${leg.symbol} 异常: ${(err as Error).message}`);
        }
      }

      details.push(detail);
    }

    basket.rollbackDetails = details;
    basket.updatedAt = new Date().toISOString();
    await this.persistBasket(basket);
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
    const filledLegs = basket.legs.filter(
      (l) => l.status === "filled" || l.status === "partial_filled",
    ).length;
    const failedLegs = basket.legs.filter((l) => l.status === "failed" || l.status === "timeout").length;
    const cancelledLegs = basket.legs.filter((l) => l.status === "cancelled").length;

    const totalValue = basket.legs
      .filter((l) => (l.status === "filled" || l.status === "partial_filled") && l.avgFillPrice)
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
      rollbackDetails: basket.rollbackDetails,
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
