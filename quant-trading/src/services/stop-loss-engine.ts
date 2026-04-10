/**
 * StopLossEngine — 090709
 *
 * 功能:
 *  - 0008090709
 *  - 07080209
 *  - 010900/1901/ATR/0209
 *  - 02 LiveEngine 的 onTick 07
 */
import type { QuantTradingConfig, Order, Position } from "../types.js";
import type { ConditionalOrderService } from "./conditional-order.js";

// ── Types ────────────────────────────────────────────────────

export type StopType =
  | "fixed_price"       // 000809
  | "percentage"        // 190109
  | "trailing_pct"      // 0209 (19010611)
  | "trailing_amount"   // 0209 (金额回撤)
  | "atr_multiple";     // ATR 倍数止损

export interface StopLossConfig {
  /** 0908020709 */
  autoAttach: boolean;
  /** 0809 */
  defaultStopType: StopType;
  /** 08091901 (如 0.05 = 5%) */
  defaultStopPct?: number;
  /** 0809071901 (如 0.10 = 10%) */
  defaultTakeProfitPct?: number;
  /** 0209111901 */
  trailingPct?: number;
  /** ATR 倍数 (用于 atr_multiple 类型) */
  atrMultiple?: number;
}

export interface StopLossRule {
  symbol: string;
  entryPrice: number;
  stopType: StopType;
  stopPrice: number;
  takeProfitPrice?: number;
  /** 0209: 040818 */
  highWaterMark?: number;
  /** 关联的条件单 ID */
  conditionalOrderId?: string;
  createdAt: string;
}

// ── Default Config ───────────────────────────────────────────

const DEFAULT_CONFIG: StopLossConfig = {
  autoAttach: true,
  defaultStopType: "percentage",
  defaultStopPct: 0.05,       // 5% 09
  defaultTakeProfitPct: 0.10, // 10% 0907
  trailingPct: 0.03,          // 3% 0211
  atrMultiple: 2,
};

// ── Engine ───────────────────────────────────────────────────

export class StopLossEngine {
  private config: StopLossConfig;
  private rules: Map<string, StopLossRule> = new Map();
  private conditionalOrderSvc?: ConditionalOrderService;
  private notifyFn?: (msg: string) => void;

  constructor(config?: Partial<StopLossConfig>) {
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  /** Wire to ConditionalOrderService for actual order execution */
  setConditionalOrderService(svc: ConditionalOrderService): void {
    this.conditionalOrderSvc = svc;
  }

  /** Set notification callback */
  setNotify(fn: (msg: string) => void): void {
    this.notifyFn = fn;
  }

  /** Get all active stop-loss rules */
  getRules(): StopLossRule[] {
    return Array.from(this.rules.values());
  }

  /** Get rule for a specific symbol */
  getRule(symbol: string): StopLossRule | undefined {
    return this.rules.get(symbol);
  }

  /**
   * Called when a new position is opened (buy order filled).
   * Automatically sets stop-loss/take-profit if autoAttach is enabled.
   */
  onPositionOpened(order: Order): void {
    if (!this.config.autoAttach) return;
    if (order.side !== "buy" && order.side !== "buy_open") return;
    if (order.status !== "filled") return;

    const entryPrice = order.filledPrice ?? order.price ?? 0;
    if (entryPrice <= 0) return;

    const quantity = order.filledQty ?? order.quantity;
    const symbol = order.symbol;

    // Calculate stop/take-profit prices
    const stopPrice = this.calculateStopPrice(entryPrice, "sell");
    const takeProfitPrice = this.config.defaultTakeProfitPct
      ? entryPrice * (1 + this.config.defaultTakeProfitPct)
      : undefined;

    const rule: StopLossRule = {
      symbol,
      entryPrice,
      stopType: this.config.defaultStopType,
      stopPrice,
      takeProfitPrice,
      highWaterMark: entryPrice,
      createdAt: new Date().toISOString(),
    };

    // Register with ConditionalOrderService if available
    if (this.conditionalOrderSvc) {
      const orderId = this.conditionalOrderSvc.addStopLoss({
        symbol,
        entryPrice,
        stopPrice,
        quantity,
        strategyId: "stop-loss-engine",
        description: `0809: ${symbol} 跌破 06${stopPrice.toFixed(2)}`,
      });
      rule.conditionalOrderId = orderId;
    }

    this.rules.set(symbol, rule);
    this.notify(`97 ${symbol} 已设置止损 06${stopPrice.toFixed(2)}` +
      (takeProfitPrice ? ` / 0907 06${takeProfitPrice.toFixed(2)}` : ""));
  }

  /**
   * Called on each price tick — updates trailing stops.
   */
  onPriceUpdate(symbol: string, currentPrice: number): void {
    const rule = this.rules.get(symbol);
    if (!rule) return;

    // Update high water mark
    if (currentPrice > (rule.highWaterMark ?? 0)) {
      rule.highWaterMark = currentPrice;

      // Recalculate trailing stop
      if (rule.stopType === "trailing_pct" || rule.stopType === "percentage") {
        const pct = this.config.trailingPct ?? this.config.defaultStopPct ?? 0.05;
        const newStop = currentPrice * (1 - pct);
        if (newStop > rule.stopPrice) {
          rule.stopPrice = newStop;
          // Re-create conditional order with updated stop price
          if (this.conditionalOrderSvc && rule.conditionalOrderId) {
            this.conditionalOrderSvc.removeOrder(rule.conditionalOrderId);
          }
        }
      }
    }
  }

  /**
   * Called when a position is closed (sell order filled).
   * Removes the corresponding stop-loss rule.
   */
  onPositionClosed(symbol: string): void {
    const rule = this.rules.get(symbol);
    if (rule?.conditionalOrderId && this.conditionalOrderSvc) {
      this.conditionalOrderSvc.removeOrder(rule.conditionalOrderId);
    }
    this.rules.delete(symbol);
  }

  /** Manually set a stop-loss rule */
  setStopLoss(
    symbol: string,
    stopPrice: number,
    quantity: number,
    stopType?: StopType,
  ): StopLossRule {
    const existing = this.rules.get(symbol);
    const rule: StopLossRule = {
      symbol,
      entryPrice: existing?.entryPrice ?? stopPrice,
      stopType: stopType ?? this.config.defaultStopType,
      stopPrice,
      takeProfitPrice: existing?.takeProfitPrice,
      highWaterMark: existing?.highWaterMark,
      createdAt: existing?.createdAt ?? new Date().toISOString(),
    };

    if (this.conditionalOrderSvc) {
      // Cancel existing conditional order
      if (existing?.conditionalOrderId) {
        this.conditionalOrderSvc.removeOrder(existing.conditionalOrderId);
      }
      const orderId = this.conditionalOrderSvc.addStopLoss({
        symbol,
        entryPrice: rule.entryPrice,
        stopPrice,
        quantity,
        strategyId: "stop-loss-engine",
        description: `止损设置: ${symbol} 跌破 06${stopPrice.toFixed(2)}`,
      });
      rule.conditionalOrderId = orderId;
    }

    this.rules.set(symbol, rule);
    return rule;
  }

  /** Remove a stop-loss rule */
  removeStopLoss(symbol: string): boolean {
    const rule = this.rules.get(symbol);
    if (!rule) return false;
    if (rule.conditionalOrderId && this.conditionalOrderSvc) {
      this.conditionalOrderSvc.removeOrder(rule.conditionalOrderId);
    }
    this.rules.delete(symbol);
    return true;
  }

  // ── Private ─────────────────────────────────────────────────

  private calculateStopPrice(entryPrice: number, _side: "buy" | "sell"): number {
    switch (this.config.defaultStopType) {
      case "percentage":
      case "trailing_pct":
        return entryPrice * (1 - (this.config.defaultStopPct ?? 0.05));
      case "trailing_amount":
        return entryPrice - (entryPrice * (this.config.defaultStopPct ?? 0.05));
      case "atr_multiple":
        // ATR needs external data; fallback to percentage
        return entryPrice * (1 - (this.config.defaultStopPct ?? 0.05));
      case "fixed_price":
      default:
        return entryPrice * (1 - (this.config.defaultStopPct ?? 0.05));
    }
  }

  private notify(msg: string): void {
    if (this.notifyFn) this.notifyFn(msg);
  }
}
