/**
 * basket-order.ts — Basket Order Service interface and data model utilities.
 *
 * A basket order groups multiple stock trades into a single logical unit,
 * enabling batch execution with configurable split strategies (all-at-once,
 * sequential, tranche, TWAP, VWAP).
 *
 * Typical use cases for stock traders:
 *   - One-click sector / thematic batch buying (e.g. "买入半导体板块")
 *   - Portfolio rebalancing (buy underweight + sell overweight in one basket)
 *   - Screener output → batch execution (strategy signals → basket)
 *   - TWAP / VWAP algorithmic execution for large orders
 *   - Index tracking / ETF arbitrage (basket of constituents)
 *
 * This file defines:
 *   1. Helper functions to create / validate basket orders
 *   2. The IBasketOrderService interface (for future implementation)
 *   3. Basket preset templates (quick-create common basket patterns)
 */

import { randomUUID } from "node:crypto";
import type {
  BasketChildOrder,
  BasketItem,
  BasketOrder,
  BasketOrderStatus,
  BasketSplitStrategy,
  MarketType,
  OrderSide,
} from "../types.js";

// ── Factory / helper functions ──────────────────────────────────

/**
 * Create a new BasketOrder in "draft" status with sensible defaults.
 */
export function createBasketOrder(params: {
  name: string;
  description?: string;
  items: BasketItem[];
  splitStrategy?: BasketSplitStrategy;
  allocationMode?: boolean;
  totalAmount?: number;
  trancheSize?: number;
  twapDurationMinutes?: number;
  trancheIntervalSecs?: number;
  vwapParticipationRate?: number;
  maxPriceDeviationPct?: number;
  skipRiskCheck?: boolean;
  strategyId?: string;
}): BasketOrder {
  const now = new Date().toISOString();
  return {
    basketId: randomUUID(),
    name: params.name,
    description: params.description,
    items: params.items,
    splitStrategy: params.splitStrategy ?? "all_at_once",
    status: "draft",

    // Split strategy parameters
    trancheSize: params.trancheSize,
    twapDurationMinutes: params.twapDurationMinutes,
    trancheIntervalSecs: params.trancheIntervalSecs,
    vwapParticipationRate: params.vwapParticipationRate,

    // Allocation mode
    allocationMode: params.allocationMode,
    totalAmount: params.totalAmount,

    // Risk / execution controls
    maxPriceDeviationPct: params.maxPriceDeviationPct,
    skipRiskCheck: params.skipRiskCheck,
    strategyId: params.strategyId,

    // Child order tracking (empty at creation)
    childOrders: [],

    // Execution summary
    totalItems: params.items.length,
    filledItems: 0,
    failedItems: 0,
    filledNotional: 0,
    totalCommission: 0,

    // Timestamps
    createdAt: now,
    updatedAt: now,
  };
}

// ── Validation ──────────────────────────────────────────────────

export type BasketValidationError = {
  itemIndex?: number;
  field: string;
  message: string;
};

/**
 * Validate a basket order before submission.
 * Returns an empty array if valid, or a list of validation errors.
 */
export function validateBasketOrder(basket: BasketOrder): BasketValidationError[] {
  const errors: BasketValidationError[] = [];

  if (!basket.name.trim()) {
    errors.push({ field: "name", message: "篮子名称不能为空" });
  }

  if (basket.items.length === 0) {
    errors.push({ field: "items", message: "篮子中至少需要一个交易项" });
  }

  // Validate allocation mode
  if (basket.allocationMode) {
    if (!basket.totalAmount || basket.totalAmount <= 0) {
      errors.push({ field: "totalAmount", message: "配置模式下总金额必须大于0" });
    }
    const totalWeight = basket.items.reduce((sum, item) => sum + (item.weight ?? 0), 0);
    if (Math.abs(totalWeight - 1) > 0.01) {
      errors.push({
        field: "items",
        message: `配置模式下各项权重之和应为1，当前为 ${totalWeight.toFixed(4)}`,
      });
    }
  }

  // Validate individual items
  for (let i = 0; i < basket.items.length; i++) {
    const item = basket.items[i];

    if (!item.symbol.trim()) {
      errors.push({ itemIndex: i, field: "symbol", message: `第${i + 1}项: 股票代码不能为空` });
    }

    if (!basket.allocationMode && item.quantity <= 0) {
      errors.push({ itemIndex: i, field: "quantity", message: `第${i + 1}项: 数量必须大于0` });
    }

    if (item.orderType === "limit" && (item.price === undefined || item.price <= 0)) {
      errors.push({ itemIndex: i, field: "price", message: `第${i + 1}项: 限价单必须指定价格` });
    }

    if (basket.allocationMode && (item.weight === undefined || item.weight <= 0)) {
      errors.push({
        itemIndex: i,
        field: "weight",
        message: `第${i + 1}项: 配置模式下权重必须大于0`,
      });
    }
  }

  // Validate split strategy parameters
  if (basket.splitStrategy === "tranche") {
    if (basket.trancheSize !== undefined && basket.trancheSize <= 0) {
      errors.push({ field: "trancheSize", message: "分批大小必须大于0" });
    }
  }

  if (basket.splitStrategy === "twap") {
    if (basket.twapDurationMinutes !== undefined && basket.twapDurationMinutes <= 0) {
      errors.push({ field: "twapDurationMinutes", message: "TWAP执行时长必须大于0" });
    }
  }

  if (basket.splitStrategy === "vwap") {
    if (
      basket.vwapParticipationRate !== undefined &&
      (basket.vwapParticipationRate <= 0 || basket.vwapParticipationRate > 1)
    ) {
      errors.push({
        field: "vwapParticipationRate",
        message: "VWAP参与率必须在 0 到 1 之间",
      });
    }
  }

  return errors;
}

// ── Execution summary helpers ───────────────────────────────────

/**
 * Recompute the basket's execution summary fields from its childOrders.
 * Call this after every child order status update.
 */
export function recomputeBasketSummary(basket: BasketOrder): void {
  const itemStatuses = new Map<number, { filled: boolean; failed: boolean }>();

  let filledNotional = 0;
  let totalCommission = 0;

  for (const child of basket.childOrders) {
    const current = itemStatuses.get(child.itemIndex) ?? { filled: false, failed: false };

    if (child.status === "filled") {
      current.filled = true;
    } else if (child.status === "rejected" || child.status === "cancelled") {
      current.failed = true;
    }

    if (child.filledQty > 0 && child.avgFillPrice) {
      filledNotional += child.filledQty * child.avgFillPrice;
    }

    itemStatuses.set(child.itemIndex, current);
  }

  basket.filledItems = 0;
  basket.failedItems = 0;
  for (const [, s] of itemStatuses) {
    if (s.filled) basket.filledItems++;
    else if (s.failed) basket.failedItems++;
  }
  basket.filledNotional = filledNotional;
  basket.totalCommission = totalCommission;
  basket.updatedAt = new Date().toISOString();
}

/**
 * Derive the overall basket status from child order states.
 */
export function deriveBasketStatus(basket: BasketOrder): BasketOrderStatus {
  if (basket.status === "draft" || basket.status === "cancelled") {
    return basket.status;
  }

  const total = basket.totalItems;
  if (basket.childOrders.length === 0) {
    return "pending";
  }

  const filled = basket.filledItems;
  const failed = basket.failedItems;
  const resolved = filled + failed;

  if (filled === total) return "completed";
  if (resolved === total && failed > 0) return "partial_done";
  if (failed === total) return "failed";

  return "executing";
}

// ── Basket preset templates ─────────────────────────────────────

export type BasketPreset = {
  id: string;
  name: string;
  description: string;
  /** Default split strategy for this preset */
  splitStrategy: BasketSplitStrategy;
  /** Default side for items (can be overridden per item) */
  defaultSide: OrderSide;
  /** Default order type */
  defaultOrderType: "limit" | "market";
  /** Default market */
  defaultMarket: MarketType;
  /** Whether this preset uses allocation mode */
  allocationMode: boolean;
};

/** Built-in basket presets for common trading scenarios */
export const BASKET_PRESETS: BasketPreset[] = [
  {
    id: "sector-buy",
    name: "板块批量买入",
    description: "同时买入一组同板块股票，适用于板块轮动策略",
    splitStrategy: "all_at_once",
    defaultSide: "buy",
    defaultOrderType: "limit",
    defaultMarket: "cn_stock",
    allocationMode: false,
  },
  {
    id: "allocation-rebalance",
    name: "组合再平衡",
    description: "按目标权重配置资金，自动计算各股票数量",
    splitStrategy: "all_at_once",
    defaultSide: "buy",
    defaultOrderType: "limit",
    defaultMarket: "cn_stock",
    allocationMode: true,
  },
  {
    id: "screener-batch",
    name: "选股结果批量执行",
    description: "将选股器/策略信号输出批量下单",
    splitStrategy: "tranche",
    defaultSide: "buy",
    defaultOrderType: "limit",
    defaultMarket: "cn_stock",
    allocationMode: false,
  },
  {
    id: "large-order-twap",
    name: "大单TWAP拆分",
    description: "将大额订单按时间均匀拆分执行，降低市场冲击",
    splitStrategy: "twap",
    defaultSide: "buy",
    defaultOrderType: "limit",
    defaultMarket: "cn_stock",
    allocationMode: false,
  },
  {
    id: "large-order-vwap",
    name: "大单VWAP拆分",
    description: "按市场成交量比例拆分执行，跟踪VWAP",
    splitStrategy: "vwap",
    defaultSide: "buy",
    defaultOrderType: "limit",
    defaultMarket: "cn_stock",
    allocationMode: false,
  },
  {
    id: "batch-sell",
    name: "批量卖出",
    description: "一键卖出多只持仓，适用于止损或清仓",
    splitStrategy: "all_at_once",
    defaultSide: "sell",
    defaultOrderType: "limit",
    defaultMarket: "cn_stock",
    allocationMode: false,
  },
];

// ── Service interface ───────────────────────────────────────────

/**
 * Result of a basket submission attempt.
 */
export type BasketSubmitResult =
  | { ok: true; basket: BasketOrder }
  | { ok: false; reason: string; validationErrors?: BasketValidationError[] };

/**
 * Filter options for querying basket orders.
 */
export type BasketOrderFilter = {
  status?: BasketOrderStatus[];
  strategyId?: string;
  dateFrom?: string; // YYYY-MM-DD
  dateTo?: string;
};

/**
 * IBasketOrderService — the basket order lifecycle manager.
 *
 * Responsible for:
 *   - Creating and validating basket orders
 *   - Executing basket orders according to the chosen split strategy
 *   - Tracking child order execution progress
 *   - Pausing / resuming / cancelling basket execution
 *   - Persisting basket state for crash recovery
 *
 * The service delegates individual order placement to OrderManager,
 * which in turn handles risk checks and broker interaction.
 */
export interface IBasketOrderService {
  /**
   * Create a new basket order in "draft" status.
   * Does not start execution — call submit() to begin.
   */
  create(params: {
    name: string;
    description?: string;
    items: BasketItem[];
    splitStrategy?: BasketSplitStrategy;
    allocationMode?: boolean;
    totalAmount?: number;
    trancheSize?: number;
    twapDurationMinutes?: number;
    trancheIntervalSecs?: number;
    vwapParticipationRate?: number;
    maxPriceDeviationPct?: number;
    skipRiskCheck?: boolean;
    strategyId?: string;
  }): Promise<BasketOrder>;

  /**
   * Validate and submit a basket order for execution.
   * Transitions status from "draft" → "pending" → "executing".
   *
   * If allocationMode is true, resolves current prices and computes
   * quantities from weights before submitting.
   */
  submit(basketId: string): Promise<BasketSubmitResult>;

  /**
   * Pause a currently executing basket.
   * No new child orders will be placed; already-submitted orders are NOT cancelled.
   */
  pause(basketId: string): Promise<void>;

  /**
   * Resume a paused basket. Continues placing remaining child orders.
   */
  resume(basketId: string): Promise<void>;

  /**
   * Cancel an entire basket.
   * Attempts to cancel all open child orders and marks the basket as cancelled.
   */
  cancel(basketId: string): Promise<void>;

  /**
   * Query a single basket order by ID.
   */
  get(basketId: string): Promise<BasketOrder | null>;

  /**
   * Query basket orders with optional filters.
   * Returns most recent first.
   */
  list(filter?: BasketOrderFilter): Promise<BasketOrder[]>;

  /**
   * Delete a basket order (only allowed in draft / cancelled / completed status).
   */
  delete(basketId: string): Promise<void>;

  /**
   * Create a basket from a preset template.
   * Returns a draft basket pre-configured with the preset's defaults.
   */
  createFromPreset(
    presetId: string,
    items: BasketItem[],
    overrides?: Partial<BasketOrder>,
  ): Promise<BasketOrder>;

  /**
   * Get all available basket presets.
   */
  getPresets(): BasketPreset[];

  /**
   * Get the execution progress summary for a basket.
   * Useful for UI progress bars and status displays.
   */
  getProgress(basketId: string): Promise<{
    totalItems: number;
    filledItems: number;
    failedItems: number;
    pendingItems: number;
    filledNotional: number;
    totalCommission: number;
    /** Overall completion percentage (0-100) */
    completionPct: number;
    /** Estimated time remaining in seconds (for TWAP/VWAP), null if unknown */
    estimatedRemainingSecs: number | null;
  }>;
}
