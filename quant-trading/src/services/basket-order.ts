/**
 * BasketOrderService — batch/basket order management.
 *
 * Supports submitting multiple stock orders at once with three modes:
 *  - quantity: exact share count per stock
 *  - amount: target dollar amount per stock (auto-calc lots)
 *  - weight: percentage weight with total amount (auto-calc lots)
 *
 * Each sub-order independently passes through risk gate before submission.
 */
import crypto from "node:crypto";
import type { OrderSide, OrderStatus } from "../types.js";

// ── Types ────────────────────────────────────────────────────

export interface BasketItem {
  symbol: string;
  side: OrderSide;
  quantity?: number;
  amount?: number;
  weight?: number;
}

export interface BasketOrderRequest {
  basketId?: string;
  items: BasketItem[];
  totalAmount?: number;
  mode: "quantity" | "amount" | "weight";
  accountId?: string;
  dryRun?: boolean;
}

export interface BasketPreviewItem {
  symbol: string;
  name?: string;
  side: OrderSide;
  quantity: number;
  estimatedPrice: number;
  estimatedAmount: number;
  estimatedCommission: number;
  riskCheckPassed: boolean;
  riskRejectReason?: string;
}

export interface BasketPreview {
  basketId: string;
  items: BasketPreviewItem[];
  totalEstimatedAmount: number;
  totalEstimatedCommission: number;
  warnings: string[];
}

export interface BasketOrderItemResult {
  symbol: string;
  orderId?: string;
  status: OrderStatus | "skipped";
  reason?: string;
}

export interface BasketOrderResult {
  basketId: string;
  status: "all_filled" | "partial" | "all_rejected" | "pending";
  items: BasketOrderItemResult[];
  summary: { submitted: number; filled: number; rejected: number; pending: number };
}

// ── Quote + Risk + Order interfaces (duck-typed for testability) ──

export interface QuoteProvider {
  getPrice(symbol: string): Promise<number>;
}

export interface RiskChecker {
  check(symbol: string, side: OrderSide, quantity: number, price: number): { passed: boolean; reason?: string };
}

export interface OrderSubmitter {
  submit(symbol: string, side: OrderSide, quantity: number, price: number): Promise<{ orderId: string; status: OrderStatus }>;
  cancel?(orderId: string): Promise<void>;
}

// ── Helpers ──────────────────────────────────────────────────

/** Round down to nearest lot size (100 for A-shares). */
export function roundToLot(qty: number, lotSize = 100): number {
  return Math.floor(qty / lotSize) * lotSize;
}

/** Estimate A-share buy commission (0.025% min 5 yuan). */
export function estimateCommission(amount: number, rate = 0.00025, min = 5): number {
  return Math.max(amount * rate, min);
}

// ── Service ─────────────────────────────────────────────────

export class BasketOrderService {
  private quotes: QuoteProvider;
  private risk: RiskChecker;
  private orders: OrderSubmitter;
  private baskets: Map<string, BasketOrderResult> = new Map();

  constructor(deps: { quotes: QuoteProvider; risk: RiskChecker; orders: OrderSubmitter }) {
    this.quotes = deps.quotes;
    this.risk = deps.risk;
    this.orders = deps.orders;
  }

  async preview(req: BasketOrderRequest): Promise<BasketPreview> {
    const basketId = req.basketId ?? crypto.randomUUID();
    const items: BasketPreviewItem[] = [];
    const warnings: string[] = [];
    let totalEstimatedAmount = 0;
    let totalEstimatedCommission = 0;

    // Validate weight mode
    if (req.mode === "weight") {
      if (!req.totalAmount || req.totalAmount <= 0) {
        throw new Error("weight mode requires a positive totalAmount");
      }
      const totalWeight = req.items.reduce((sum, item) => sum + (item.weight ?? 0), 0);
      if (Math.abs(totalWeight - 1) > 0.001) {
        warnings.push(`Weights sum to ${(totalWeight * 100).toFixed(1)}%, not 100%`);
      }
    }

    for (const item of req.items) {
      const price = await this.quotes.getPrice(item.symbol);
      if (price <= 0) {
        warnings.push(`${item.symbol}: no valid price (suspended?)`);
        items.push({
          symbol: item.symbol,
          side: item.side,
          quantity: 0,
          estimatedPrice: 0,
          estimatedAmount: 0,
          estimatedCommission: 0,
          riskCheckPassed: false,
          riskRejectReason: "No valid price available (possibly suspended)",
        });
        continue;
      }

      let quantity: number;
      switch (req.mode) {
        case "quantity":
          quantity = item.quantity ?? 0;
          break;
        case "amount":
          quantity = roundToLot(Math.floor((item.amount ?? 0) / price));
          break;
        case "weight":
          quantity = roundToLot(Math.floor(((item.weight ?? 0) * (req.totalAmount ?? 0)) / price));
          break;
      }

      if (quantity <= 0) {
        warnings.push(`${item.symbol}: calculated quantity is 0 (amount too small for one lot)`);
      }

      const estimatedAmount = quantity * price;
      const commission = estimateCommission(estimatedAmount);
      const riskResult = this.risk.check(item.symbol, item.side, quantity, price);

      items.push({
        symbol: item.symbol,
        side: item.side,
        quantity,
        estimatedPrice: price,
        estimatedAmount,
        estimatedCommission: commission,
        riskCheckPassed: riskResult.passed,
        riskRejectReason: riskResult.reason,
      });

      if (riskResult.passed && quantity > 0) {
        totalEstimatedAmount += estimatedAmount;
        totalEstimatedCommission += commission;
      }
    }

    return { basketId, items, totalEstimatedAmount, totalEstimatedCommission, warnings };
  }

  async submit(req: BasketOrderRequest): Promise<BasketOrderResult> {
    const preview = await this.preview({ ...req, dryRun: true });
    const basketId = preview.basketId;

    // Check total amount against available funds
    // (caller should validate this externally; we just proceed)

    const resultItems: BasketOrderItemResult[] = [];
    let submitted = 0;
    let filled = 0;
    let rejected = 0;
    let pending = 0;

    for (const item of preview.items) {
      if (!item.riskCheckPassed) {
        resultItems.push({
          symbol: item.symbol,
          status: "rejected",
          reason: item.riskRejectReason ?? "Risk check failed",
        });
        rejected++;
        continue;
      }

      if (item.quantity <= 0) {
        resultItems.push({
          symbol: item.symbol,
          status: "skipped",
          reason: "Quantity is 0",
        });
        continue;
      }

      try {
        const orderResult = await this.orders.submit(
          item.symbol,
          item.side,
          item.quantity,
          item.estimatedPrice,
        );
        submitted++;
        if (orderResult.status === "filled") filled++;
        else pending++;
        resultItems.push({
          symbol: item.symbol,
          orderId: orderResult.orderId,
          status: orderResult.status,
        });
      } catch (err) {
        rejected++;
        resultItems.push({
          symbol: item.symbol,
          status: "rejected",
          reason: err instanceof Error ? err.message : String(err),
        });
      }
    }

    let status: BasketOrderResult["status"];
    if (rejected === 0 && filled === submitted && submitted > 0) status = "all_filled";
    else if (rejected === resultItems.length) status = "all_rejected";
    else if (filled > 0 || pending > 0 || (submitted > 0 && rejected > 0)) status = "partial";
    else status = "all_rejected";

    const result: BasketOrderResult = {
      basketId,
      status,
      items: resultItems,
      summary: { submitted, filled, rejected, pending },
    };

    this.baskets.set(basketId, result);
    return result;
  }

  /** Cancel unfilled sub-orders in a basket. */
  async cancel(basketId: string): Promise<void> {
    const basket = this.baskets.get(basketId);
    if (!basket) throw new Error(`Basket '${basketId}' not found`);

    let cancelledCount = 0;
    for (const item of basket.items) {
      if (item.status === "submitted" || item.status === "pending" || item.status === "partial") {
        if (item.orderId && this.orders.cancel) {
          try {
            await this.orders.cancel(item.orderId);
            item.status = "cancelled" as OrderStatus;
            cancelledCount++;
          } catch {
            // Order may already be filled/cancelled; silently continue
          }
        }
      }
    }

    // Update basket status
    const filled = basket.items.filter((i) => i.status === "filled").length;
    const rejected = basket.items.filter((i) => i.status === "rejected" || i.status === "skipped").length;
    if (filled === 0 && cancelledCount > 0) basket.status = "all_rejected";
    else if (filled > 0) basket.status = "partial";
  }

  getStatus(basketId: string): BasketOrderResult | undefined {
    return this.baskets.get(basketId);
  }

  listBaskets(): BasketOrderResult[] {
    return [...this.baskets.values()];
  }
}
