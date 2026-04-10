/**
 * ConditionalOrderService — monitors prices and auto-triggers exit orders.
 *
 * Supports seven conditional order types:
 *   stop_loss      — sell when price falls below a threshold
 *   take_profit    — sell when price rises above a threshold
 *   trailing_stop  — sell when price drops a fixed % from its peak since entry
 *   trailing_atr   — sell when price drops N×ATR from its peak (dynamic distance)
 *   trailing_tp    — trailing take-profit: activates at +X%, then trails upward
 *   staged_tp      — staged take-profit: auto-creates multiple sell tiers
 *   indicator_stop — sell when price breaks below a moving average (SMA/EMA)
 *
 * Runs as a periodic check (default: every 30s). When triggered, submits
 * a market exit order via a user-provided callback (wired to OrderManager).
 *
 * Persists conditonal orders to disk for crash recovery.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

export type ConditionalOrderType =
  | "stop_loss"
  | "take_profit"
  | "trailing_stop"
  | "trailing_atr"
  | "trailing_tp"
  | "staged_tp"
  | "indicator_stop";

/** A single tier in a staged take-profit order. */
export interface StagedTier {
  /** Target gain percentage (0-1, e.g. 0.08 = +8%). */
  gainPct: number;
  /** Fraction of remaining position to sell (0-1, e.g. 0.5 = 50%). */
  sellRatio: number;
  /** Whether this tier has been triggered. */
  triggered: boolean;
  /** Computed absolute trigger price. */
  triggerPrice: number;
  /** Actual quantity to sell for this tier (computed at creation). */
  quantity: number;
}

export interface ConditionalOrder {
  id: string;
  symbol: string;
  type: ConditionalOrderType;
  /** Entry price (for reference / trailing calculation). */
  entryPrice: number;
  /** Trigger price for stop_loss / take_profit. For trailing_stop, this is the drop percentage (0-1). */
  triggerValue: number;
  /** Shares to sell on trigger. */
  quantity: number;
  /** Exit side when triggered. Defaults to "sell" for long positions.
   *  Use "cover" for short positions, "sell_close" for options. */
  exitSide?: "sell" | "cover" | "buy_close" | "sell_close";
  /** Strategy that created this conditional order (optional). */
  strategyId?: string;
  /** Human-readable description. */
  description: string;
  /** When the order was created. */
  createdAt: string;
  /** Whether the order has been triggered. */
  triggered: boolean;
  /** Trigger timestamp. */
  triggeredAt?: string;
  /** Result of the exit order submission. */
  triggerResult?: string;

  // ── Trailing stop state ──
  /** Highest price observed since creation (for trailing_stop / trailing_atr). */
  peakPrice?: number;
  /** Computed trailing stop price (peak * (1 - triggerValue) or peak - atrDistance). */
  trailingStopPrice?: number;

  // ── ATR trailing state ──
  /** ATR value at creation time (for trailing_atr). */
  atrValue?: number;
  /** ATR multiplier for trailing distance (default 1.0). */
  atrMultiplier?: number;
  /** ATR multiplier for stop move-up step (default: atrMultiplier × 0.625). */
  atrStepMultiplier?: number;
  /** Last threshold at which the stop was ratcheted up (for step-based trailing). */
  lastRatchetPrice?: number;
  /** Ratchet step: price must rise by ratchetUpAtr × ATR to trigger stop move-up. */
  ratchetUpAtr?: number;
  /** When stop moves up, it moves by ratchetMoveAtr × ATR. */
  ratchetMoveAtr?: number;

  // ── Indicator-based stop state ──
  /** Moving average period (e.g. 10, 20, 60). */
  maPeriod?: number;
  /** MA type: "sma" | "ema" (default "sma"). */
  maType?: "sma" | "ema";
  /** Sell when price drops below MA by this buffer percentage (0-1, default 0). */
  maBufferPct?: number;

  // ── Trailing take-profit state ──
  /** Activation profit percentage (0-1, e.g. 0.1 = +10%). Once reached, trailing starts. */
  activationPct?: number;
  /** Callback percentage from peak profit (0-1, e.g. 0.03 = 3%). Triggers sell when profit pulls back. */
  callbackPct?: number;
  /** Whether trailing take-profit has been activated (price reached activation level). */
  tpActivated?: boolean;
  /** The trailing take-profit sell price (peak * (1 - callbackPct)). */
  trailingTpPrice?: number;

  // ── Staged take-profit state ──
  /** All tiers for staged take-profit. */
  tiers?: StagedTier[];
}

export interface ConditionalOrderServiceOptions {
  /** Check interval in ms (default: 30_000 = 30s). */
  checkIntervalMs?: number;
  /** Maximum conditional orders. */
  maxOrders?: number;
  /** Data directory for persistence. */
  dataDir?: string | null;
}

export class ConditionalOrderService {
  private orders = new Map<string, ConditionalOrder>();
  private idCounter = 0;
  private timer: ReturnType<typeof setInterval> | null = null;
  private readonly checkIntervalMs: number;
  private readonly maxOrders: number;
  private readonly dataDir: string | null;
  private notifyFn?: (message: string) => void | Promise<void>;
  private quoteFn?: (symbol: string) => Promise<{ price: number } | null>;
  private exitFn?: (
    symbol: string,
    quantity: number,
    reason: string,
    strategyId?: string,
    exitSide?: string,
  ) => Promise<{ ok: boolean; reason?: string }>;

  constructor(opts: ConditionalOrderServiceOptions = {}) {
    this.checkIntervalMs = opts.checkIntervalMs ?? 30_000;
    this.maxOrders = opts.maxOrders ?? 100;
    this.dataDir = opts.dataDir ?? null;
    this.loadFromDisk();
  }

  /** Set the notification callback. */
  setNotify(fn: (message: string) => void | Promise<void>): void {
    this.notifyFn = fn;
  }

  /** Set the quote fetching function. */
  setQuoteFn(fn: (symbol: string) => Promise<{ price: number } | null>): void {
    this.quoteFn = fn;
  }

  /** Set the exit order function (wires to OrderManager.submit for sell/sell_close). */
  setExitFn(
    fn: (
      symbol: string,
      quantity: number,
      reason: string,
      strategyId?: string,
      exitSide?: string,
    ) => Promise<{ ok: boolean; reason?: string }>,
  ): void {
    this.exitFn = fn;
  }

  /**
   * Set the indicator fetching function.
   * Returns MA values for a symbol. Used by indicator_stop orders.
   */
  private indicatorFn?: (
    symbol: string,
    maPeriod: number,
    maType: "sma" | "ema",
  ) => Promise<{ maValue: number } | null>;

  setIndicatorFn(
    fn: (
      symbol: string,
      maPeriod: number,
      maType: "sma" | "ema",
    ) => Promise<{ maValue: number } | null>,
  ): void {
    this.indicatorFn = fn;
  }

  /** Add a stop-loss order. Returns the order ID. */
  addStopLoss(params: {
    symbol: string;
    entryPrice: number;
    stopPrice: number;
    quantity: number;
    strategyId?: string;
    description?: string;
  }): string {
    return this.addOrder({
      symbol: params.symbol,
      type: "stop_loss",
      entryPrice: params.entryPrice,
      triggerValue: params.stopPrice,
      quantity: params.quantity,
      strategyId: params.strategyId,
      description:
        params.description ??
        `止损: ${params.symbol} 跌破 ¥${params.stopPrice.toFixed(2)} 卖出 ${String(params.quantity)} 股`,
    });
  }

  /** Add a take-profit order. Returns the order ID. */
  addTakeProfit(params: {
    symbol: string;
    entryPrice: number;
    targetPrice: number;
    quantity: number;
    strategyId?: string;
    description?: string;
  }): string {
    return this.addOrder({
      symbol: params.symbol,
      type: "take_profit",
      entryPrice: params.entryPrice,
      triggerValue: params.targetPrice,
      quantity: params.quantity,
      strategyId: params.strategyId,
      description:
        params.description ??
        `止盈: ${params.symbol} 涨到 ¥${params.targetPrice.toFixed(2)} 卖出 ${String(params.quantity)} 股`,
    });
  }

  /** Add a trailing stop order. Returns the order ID. */
  addTrailingStop(params: {
    symbol: string;
    entryPrice: number;
    trailPct: number; // 0-1, e.g. 0.05 = 5%
    quantity: number;
    strategyId?: string;
    description?: string;
  }): string {
    const trailingStopPrice = params.entryPrice * (1 - params.trailPct);
    return this.addOrder({
      symbol: params.symbol,
      type: "trailing_stop",
      entryPrice: params.entryPrice,
      triggerValue: params.trailPct,
      quantity: params.quantity,
      strategyId: params.strategyId,
      description:
        params.description ??
        `跟踪止损: ${params.symbol} 回撤 ${(params.trailPct * 100).toFixed(1)}% 自动卖出`,
      peakPrice: params.entryPrice,
      trailingStopPrice,
    });
  }

  /**
   * Add an ATR-based trailing stop order. Returns the order ID.
   *
   * The stop starts at `entryPrice - atrMultiplier * atrValue`.
   * As price rises, the stop ratchets up:
   *   - Every time price rises by `ratchetUpAtr * atrValue` above the last
   *     ratchet point, the stop moves up by `ratchetMoveAtr * atrValue`.
   *
   * Defaults:  ratchetUpAtr = 0.8, ratchetMoveAtr = 0.5 (per the user's
   * "price up 0.8×ATR → stop up 0.5×ATR" rule).
   */
  addTrailingAtr(params: {
    symbol: string;
    entryPrice: number;
    atrValue: number;
    atrMultiplier?: number; // default 1.0
    ratchetUpAtr?: number; // default 0.8
    ratchetMoveAtr?: number; // default 0.5
    quantity: number;
    strategyId?: string;
    description?: string;
  }): string {
    const mult = params.atrMultiplier ?? 1.0;
    const ratchetUp = params.ratchetUpAtr ?? 0.8;
    const ratchetMove = params.ratchetMoveAtr ?? 0.5;
    const initialStop = params.entryPrice - mult * params.atrValue;
    return this.addOrder({
      symbol: params.symbol,
      type: "trailing_atr",
      entryPrice: params.entryPrice,
      triggerValue: 0, // not used for ATR trailing; kept for compat
      quantity: params.quantity,
      strategyId: params.strategyId,
      description:
        params.description ??
        `ATR跟踪止损: ${params.symbol} ATR=${params.atrValue.toFixed(2)} ×${mult.toFixed(1)} 初始止损 ¥${initialStop.toFixed(2)}`,
      peakPrice: params.entryPrice,
      trailingStopPrice: initialStop,
      atrValue: params.atrValue,
      atrMultiplier: mult,
      ratchetUpAtr: ratchetUp,
      ratchetMoveAtr: ratchetMove,
      lastRatchetPrice: params.entryPrice,
    });
  }

  /**
   * Add a staged (multi-tier) take-profit order. Returns the order ID.
   *
   * Example: tiers = [{gainPct: 0.08, sellRatio: 0.5}, {gainPct: 0.12, sellRatio: 1.0}]
   * → at +8% sell 50% of position, at +12% sell remaining 100%.
   */
  addStagedTakeProfit(params: {
    symbol: string;
    entryPrice: number;
    totalQuantity: number;
    tiers: { gainPct: number; sellRatio: number }[];
    strategyId?: string;
    description?: string;
  }): string {
    if (params.tiers.length === 0) {
      throw new Error("至少需要一个止盈档位");
    }
    // Sort tiers by gainPct ascending
    const sorted = [...params.tiers].sort((a, b) => a.gainPct - b.gainPct);

    // Compute quantities for each tier (sellRatio is fraction of REMAINING)
    let remaining = params.totalQuantity;
    const tiers: StagedTier[] = sorted.map((t) => {
      let qty = Math.floor(remaining * t.sellRatio);
      // Round to lot of 100 for A-shares
      if (qty >= 100) qty = Math.floor(qty / 100) * 100;
      qty = Math.max(qty, 0);
      remaining -= qty;
      return {
        gainPct: t.gainPct,
        sellRatio: t.sellRatio,
        triggered: false,
        triggerPrice: params.entryPrice * (1 + t.gainPct),
        quantity: qty,
      };
    });

    // Assign any remainder to last tier
    if (remaining > 0 && tiers.length > 0) {
      tiers[tiers.length - 1].quantity += remaining;
    }

    const tierDesc = tiers
      .map((t) => `+${(t.gainPct * 100).toFixed(0)}%卖${t.quantity}股`)
      .join(", ");

    return this.addOrder({
      symbol: params.symbol,
      type: "staged_tp",
      entryPrice: params.entryPrice,
      triggerValue: 0,
      quantity: params.totalQuantity,
      strategyId: params.strategyId,
      description: params.description ?? `分批止盈: ${params.symbol} ${tierDesc}`,
      tiers,
    });
  }

  /**
   * Add a trailing take-profit order. Returns the order ID.
   *
   * Behavior: once the stock rises by `activationPct` (e.g. +10%) from entry,
   * tracking begins. The system records the peak price. If the price then
   * pulls back by `callbackPct` (e.g. 3%) from the peak, a sell is triggered.
   *
   * Example: entry=¥100, activation=10%, callback=3%
   *   → activates at ¥110. If peak reaches ¥120, sell triggers at ¥120×0.97=¥116.4
   */
  addTrailingTakeProfit(params: {
    symbol: string;
    entryPrice: number;
    activationPct: number; // 0-1, e.g. 0.1 = +10%
    callbackPct: number; // 0-1, e.g. 0.03 = 3%
    quantity: number;
    strategyId?: string;
    description?: string;
  }): string {
    const activationPrice = params.entryPrice * (1 + params.activationPct);
    return this.addOrder({
      symbol: params.symbol,
      type: "trailing_tp",
      entryPrice: params.entryPrice,
      triggerValue: activationPrice,
      quantity: params.quantity,
      strategyId: params.strategyId,
      description:
        params.description ??
        `移动止盈: ${params.symbol} 涨 ${(params.activationPct * 100).toFixed(0)}% (¥${activationPrice.toFixed(2)}) 激活, 回撤 ${(params.callbackPct * 100).toFixed(1)}% 卖出`,
      peakPrice: params.entryPrice,
      activationPct: params.activationPct,
      callbackPct: params.callbackPct,
      tpActivated: false,
    });
  }

  /**
   * Add an indicator-based stop order (MA stop-loss). Returns the order ID.
   *
   * Triggers a sell when the current price drops below the moving average
   * (with optional buffer). For example, maPeriod=20, maBufferPct=0.02
   * sells when price < MA20 × 0.98.
   */
  addIndicatorStop(params: {
    symbol: string;
    entryPrice: number;
    quantity: number;
    maPeriod: number;
    maType?: "sma" | "ema";
    maBufferPct?: number;
    strategyId?: string;
    description?: string;
  }): string {
    const maType = params.maType ?? "sma";
    const bufferPct = params.maBufferPct ?? 0;
    return this.addOrder({
      symbol: params.symbol,
      type: "indicator_stop",
      entryPrice: params.entryPrice,
      triggerValue: 0, // not used; MA value fetched dynamically
      quantity: params.quantity,
      strategyId: params.strategyId,
      description:
        params.description ??
        `均线止损: ${params.symbol} 跌破 ${maType.toUpperCase()}${params.maPeriod}${bufferPct > 0 ? `×${(1 - bufferPct).toFixed(2)}` : ""} 卖出 ${String(params.quantity)} 股`,
      maPeriod: params.maPeriod,
      maType,
      maBufferPct: bufferPct,
    });
  }

  private addOrder(
    params: Omit<ConditionalOrder, "id" | "createdAt" | "triggered"> & {
      peakPrice?: number;
      trailingStopPrice?: number;
      atrValue?: number;
      atrMultiplier?: number;
      ratchetUpAtr?: number;
      ratchetMoveAtr?: number;
      lastRatchetPrice?: number;
      tiers?: StagedTier[];
      maPeriod?: number;
      maType?: "sma" | "ema";
      maBufferPct?: number;
      activationPct?: number;
      callbackPct?: number;
      tpActivated?: boolean;
      trailingTpPrice?: number;
    },
  ): string {
    if (this.activeCount >= this.maxOrders) {
      throw new Error(`条件单数量已达上限 (${String(this.maxOrders)})`);
    }

    const id = `co-${String(++this.idCounter).padStart(4, "0")}`;
    const order: ConditionalOrder = {
      id,
      symbol: params.symbol,
      type: params.type,
      entryPrice: params.entryPrice,
      triggerValue: params.triggerValue,
      quantity: params.quantity,
      strategyId: params.strategyId,
      description: params.description,
      createdAt: new Date().toISOString(),
      triggered: false,
      peakPrice: params.peakPrice,
      trailingStopPrice: params.trailingStopPrice,
      atrValue: params.atrValue,
      atrMultiplier: params.atrMultiplier,
      ratchetUpAtr: params.ratchetUpAtr,
      ratchetMoveAtr: params.ratchetMoveAtr,
      lastRatchetPrice: params.lastRatchetPrice,
      tiers: params.tiers,
      maPeriod: params.maPeriod,
      maType: params.maType,
      maBufferPct: params.maBufferPct,
      activationPct: params.activationPct,
      callbackPct: params.callbackPct,
      tpActivated: params.tpActivated,
      trailingTpPrice: params.trailingTpPrice,
    };

    this.orders.set(id, order);
    this.saveToDisk();
    return id;
  }

  /** Remove a conditional order by ID. */
  removeOrder(id: string): boolean {
    const ok = this.orders.delete(id);
    if (ok) this.saveToDisk();
    return ok;
  }

  /** List all conditional orders. */
  listOrders(activeOnly = false): ConditionalOrder[] {
    const all = [...this.orders.values()];
    if (activeOnly) return all.filter((o) => !o.triggered);
    return all;
  }

  /** Get active (non-triggered) order count. */
  get activeCount(): number {
    return [...this.orders.values()].filter((o) => !o.triggered).length;
  }

  /** Start the periodic check loop. */
  start(): void {
    if (this.timer) return;
    console.log(
      `[conditional-order] started (poll=${this.checkIntervalMs / 1000}s, active=${this.activeCount}, total=${this.orders.size})`,
    );
    this.timer = setInterval(() => {
      void this.checkAll();
    }, this.checkIntervalMs);
  }

  /** Stop the periodic check loop. */
  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  /** Run a single check cycle against all active conditional orders. */
  async checkAll(): Promise<string[]> {
    if (!this.quoteFn) return [];

    const triggered: string[] = [];
    const active = this.listOrders(true);
    if (active.length === 0) return [];

    // Deduplicate symbols to minimize quote calls
    const uniqueSymbols = [...new Set(active.map((o) => o.symbol))];
    const quotes = new Map<string, number>();

    await Promise.allSettled(
      uniqueSymbols.map(async (symbol) => {
        try {
          const q = await this.quoteFn!(symbol);
          if (q) quotes.set(symbol, q.price);
        } catch (err) {
          console.warn(`[conditional-order] quote failed for ${symbol}:`, err);
        }
      }),
    );

    const missedSymbols = uniqueSymbols.filter((s) => !quotes.has(s));
    if (missedSymbols.length > 0) {
      console.warn(
        `[conditional-order] check: ${active.length} orders, ${quotes.size}/${uniqueSymbols.length} quotes ok, missed: ${missedSymbols.join(",")}`,
      );
    } else {
      console.log(
        `[conditional-order] check: ${active.length} orders, ${quotes.size}/${uniqueSymbols.length} quotes ok`,
      );
    }

    for (const order of active) {
      const price = quotes.get(order.symbol);
      if (price == null) continue;

      let shouldTrigger = false;
      let triggerReason = "";

      switch (order.type) {
        case "stop_loss":
          if (price <= order.triggerValue) {
            shouldTrigger = true;
            triggerReason = `止损触发: ${order.symbol} 当前价 ¥${price.toFixed(2)} ≤ 止损价 ¥${order.triggerValue.toFixed(2)}`;
          }
          break;

        case "take_profit":
          if (price >= order.triggerValue) {
            shouldTrigger = true;
            triggerReason = `止盈触发: ${order.symbol} 当前价 ¥${price.toFixed(2)} ≥ 目标价 ¥${order.triggerValue.toFixed(2)}`;
          }
          break;

        case "trailing_stop": {
          // Update peak price
          if (price > (order.peakPrice ?? order.entryPrice)) {
            order.peakPrice = price;
            order.trailingStopPrice = price * (1 - order.triggerValue);
          }
          const stopPrice = order.trailingStopPrice ?? order.entryPrice * (1 - order.triggerValue);
          if (price <= stopPrice) {
            shouldTrigger = true;
            triggerReason = `跟踪止损触发: ${order.symbol} 当前价 ¥${price.toFixed(2)} ≤ 止损线 ¥${stopPrice.toFixed(2)} (峰值 ¥${(order.peakPrice ?? order.entryPrice).toFixed(2)})`;
          }
          break;
        }

        case "trailing_atr": {
          const atr = order.atrValue ?? 0;
          const ratchetUp = (order.ratchetUpAtr ?? 0.8) * atr;
          const ratchetMove = (order.ratchetMoveAtr ?? 0.5) * atr;
          const lastRatchet = order.lastRatchetPrice ?? order.entryPrice;

          // Update peak price
          if (price > (order.peakPrice ?? order.entryPrice)) {
            order.peakPrice = price;
          }

          // Ratchet up: if price moved ratchetUp above lastRatchetPrice, move stop up by ratchetMove
          if (price >= lastRatchet + ratchetUp) {
            const steps = Math.floor((price - lastRatchet) / ratchetUp);
            order.lastRatchetPrice = lastRatchet + steps * ratchetUp;
            const currentStop =
              order.trailingStopPrice ?? order.entryPrice - (order.atrMultiplier ?? 1) * atr;
            order.trailingStopPrice = currentStop + steps * ratchetMove;
          }

          const atrStop =
            order.trailingStopPrice ?? order.entryPrice - (order.atrMultiplier ?? 1) * atr;
          if (price <= atrStop) {
            shouldTrigger = true;
            triggerReason = `ATR跟踪止损触发: ${order.symbol} 当前价 ¥${price.toFixed(2)} ≤ ATR止损线 ¥${atrStop.toFixed(2)} (峰值 ¥${(order.peakPrice ?? order.entryPrice).toFixed(2)}, ATR=${atr.toFixed(2)})`;
          }
          break;
        }

        case "trailing_tp": {
          const activationPrice = order.triggerValue; // entryPrice * (1 + activationPct)
          const cbPct = order.callbackPct ?? 0.03;

          // Phase 1: wait for activation
          if (!order.tpActivated) {
            if (price >= activationPrice) {
              order.tpActivated = true;
              order.peakPrice = price;
              order.trailingTpPrice = price * (1 - cbPct);
              void this.notifyFn?.(
                `📈 移动止盈激活: ${order.symbol} 当前价 ¥${price.toFixed(2)} ≥ 激活价 ¥${activationPrice.toFixed(2)}, 跟踪回撤 ${(cbPct * 100).toFixed(1)}%`,
              );
            }
            break;
          }

          // Phase 2: tracking — update peak and trailing TP price
          if (price > (order.peakPrice ?? order.entryPrice)) {
            order.peakPrice = price;
            order.trailingTpPrice = price * (1 - cbPct);
          }

          const tpSellPrice = order.trailingTpPrice ?? activationPrice * (1 - cbPct);
          if (price <= tpSellPrice) {
            shouldTrigger = true;
            triggerReason = `移动止盈触发: ${order.symbol} 当前价 ¥${price.toFixed(2)} ≤ 止盈线 ¥${tpSellPrice.toFixed(2)} (峰值 ¥${(order.peakPrice ?? order.entryPrice).toFixed(2)}, 回撤 ${(cbPct * 100).toFixed(1)}%)`;
          }
          break;
        }

        case "staged_tp": {
          // Check each untriggered tier
          if (order.tiers) {
            for (const tier of order.tiers) {
              if (!tier.triggered && price >= tier.triggerPrice && tier.quantity > 0) {
                tier.triggered = true;
                // Submit partial exit for this tier
                if (this.exitFn) {
                  const reason = `分批止盈触发: ${order.symbol} 当前价 ¥${price.toFixed(2)} ≥ +${(tier.gainPct * 100).toFixed(0)}% 目标 ¥${tier.triggerPrice.toFixed(2)}, 卖出 ${String(tier.quantity)} 股`;
                  const result = await this.exitFn(
                    order.symbol,
                    tier.quantity,
                    reason,
                    order.strategyId,
                    order.exitSide,
                  );
                  const resultSuffix = result.ok
                    ? "，卖出委托已提交"
                    : `（exit_failed: ${result.reason}）`;
                  void this.notifyFn?.(`🔔 ${reason}${resultSuffix}`);
                }
                triggered.push(order.id);
                this.saveToDisk();
              }
            }
            // Mark whole order as triggered only when ALL tiers are done
            if (order.tiers.every((t) => t.triggered)) {
              order.triggered = true;
              order.triggeredAt = new Date().toISOString();
              order.triggerResult = "all_tiers_completed";
            }
          }
          continue; // staged_tp handles its own notification; skip the generic path
        }

        case "indicator_stop": {
          // Fetch the current MA value via indicatorFn
          if (!this.indicatorFn) break;
          const maPeriod = order.maPeriod ?? 20;
          const maType = order.maType ?? "sma";
          const bufferPct = order.maBufferPct ?? 0;
          const indicator = await this.indicatorFn(order.symbol, maPeriod, maType);
          if (!indicator) break;
          const maStop = indicator.maValue * (1 - bufferPct);
          if (price <= maStop) {
            shouldTrigger = true;
            triggerReason = `均线止损触发: ${order.symbol} 当前价 ¥${price.toFixed(2)} ≤ ${maType.toUpperCase()}${maPeriod} ¥${indicator.maValue.toFixed(2)}${bufferPct > 0 ? ` ×${(1 - bufferPct).toFixed(2)} = ¥${maStop.toFixed(2)}` : ""}`;
          }
          break;
        }
      }

      if (shouldTrigger) {
        order.triggered = true;
        order.triggeredAt = new Date().toISOString();
        triggered.push(order.id);

        // Submit exit order
        if (this.exitFn) {
          const result = await this.exitFn(
            order.symbol,
            order.quantity,
            triggerReason,
            order.strategyId,
            order.exitSide,
          );
          order.triggerResult = result.ok ? "exit_submitted" : `exit_failed: ${result.reason}`;
        } else {
          order.triggerResult = "no_exit_fn_configured";
        }

        // Notify user
        const resultSuffix =
          order.triggerResult === "exit_submitted"
            ? "，卖出委托已提交"
            : `（${order.triggerResult}）`;
        void this.notifyFn?.(`🔔 ${triggerReason}${resultSuffix}`);
      }
    }

    if (triggered.length > 0) {
      console.log(
        `[conditional-order] triggered ${triggered.length} order(s): ${triggered.join(", ")}`,
      );
      this.saveToDisk();
    }
    return triggered;
  }

  // ── Persistence ────────────────────────────────────────────

  private persistPath(): string | null {
    if (!this.dataDir) return null;
    return `${this.dataDir}/conditional-orders.json`;
  }

  private saveToDisk(): void {
    const p = this.persistPath();
    if (!p) return;
    const dir = dirname(p);
    if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
    writeFileSync(
      p,
      JSON.stringify(
        {
          idCounter: this.idCounter,
          orders: [...this.orders.values()],
        },
        null,
        2,
      ),
    );
  }

  private loadFromDisk(): void {
    const p = this.persistPath();
    if (!p || !existsSync(p)) return;
    try {
      const data = JSON.parse(readFileSync(p, "utf-8")) as {
        idCounter: number;
        orders: ConditionalOrder[];
      };
      this.idCounter = data.idCounter ?? 0;
      for (const order of data.orders) {
        this.orders.set(order.id, order);
      }
    } catch {
      // Ignore corrupt data
    }
  }
}
