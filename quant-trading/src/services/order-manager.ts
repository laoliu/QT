import { randomUUID } from "node:crypto";
/**
 * OrderManager — orchestrates the full order lifecycle.
 *
 * Flow:
 *   signal → RiskGate.check() → [ConfirmationManager.request()] → broker.placeOrder()
 *         → track fills → update TradeRecords → emit callbacks
 *
 * Persistent storage:
 *   orders.json  — append-only list of all Order objects
 *   trades.json  — completed TradeRecord objects (entry+exit pairs)
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { IBrokerAdapter, OrderRequest } from "../broker/adapter.js";
import type {
  BrokerAccount,
  Fill,
  LivePosition,
  Order,
  OrderStatus,
  QuantTradingConfig,
  RiskLimitsConfig,
  TradeRecord,
  TradingSignal,
} from "../types.js";
import { AsyncMutex } from "../utils/async-mutex.js";
import { ConfirmationManager } from "./confirmation-manager.js";
import { computePositionSize } from "./position-sizer.js";
import { RiskGate } from "./risk-gate.js";

const ordersMutex = new AsyncMutex();
const tradesMutex = new AsyncMutex();

// ── File helpers ─────────────────────────────────────────────────

function dir(cfg: QuantTradingConfig): string {
  return cfg.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
}

/** Mode suffix based on broker type — keeps paper/live orders in separate files */
function modeSuffix(cfg: QuantTradingConfig): string {
  const broker = cfg.tradingAccount?.broker ?? cfg.tradingAccount?.mode ?? "paper";
  return broker === "paper" ? "-paper" : "-live";
}

/**
 * Account-aware file suffix. When an accountName is provided (multi-account mode),
 * files are namespaced as `-{accountName}-paper` or `-{accountName}-live`.
 * Without accountName, falls back to the legacy `-paper` / `-live` suffix.
 */
function accountModeSuffix(cfg: QuantTradingConfig, accountName?: string): string {
  const mode = modeSuffix(cfg);
  if (accountName && accountName !== "default") {
    return `-${accountName}${mode}`;
  }
  return mode;
}

function ordersPath(cfg: QuantTradingConfig, accountName?: string): string {
  return path.join(dir(cfg), `orders${accountModeSuffix(cfg, accountName)}.json`);
}

function tradesPath(cfg: QuantTradingConfig, accountName?: string): string {
  return path.join(dir(cfg), `trades${accountModeSuffix(cfg, accountName)}.json`);
}

/** Get orders/trades path for a specific mode (for API queries) */
export function ordersPathForMode(cfg: QuantTradingConfig, mode: "paper" | "live", accountName?: string): string {
  if (accountName && accountName !== "default") {
    return path.join(dir(cfg), `orders-${accountName}-${mode}.json`);
  }
  return path.join(dir(cfg), `orders-${mode}.json`);
}

export function tradesPathForMode(cfg: QuantTradingConfig, mode: "paper" | "live", accountName?: string): string {
  if (accountName && accountName !== "default") {
    return path.join(dir(cfg), `trades-${accountName}-${mode}.json`);
  }
  return path.join(dir(cfg), `trades-${mode}.json`);
}

async function ensureDir(cfg: QuantTradingConfig): Promise<void> {
  await fs.mkdir(dir(cfg), { recursive: true });
}

async function loadOrders(cfg: QuantTradingConfig, accountName?: string): Promise<Order[]> {
  try {
    return JSON.parse(await fs.readFile(ordersPath(cfg, accountName), "utf-8")) as Order[];
  } catch {
    return [];
  }
}

async function appendOrder(order: Order, cfg: QuantTradingConfig, accountName?: string): Promise<void> {
  const release = await ordersMutex.acquire();
  try {
    await ensureDir(cfg);
    const orders = await loadOrders(cfg, accountName);
    // Skip if broker already persisted this order (e.g. PaperBroker writes in placeOrder)
    if (orders.some((o) => o.orderId === order.orderId)) return;
    orders.push(order);
    await fs.writeFile(ordersPath(cfg, accountName), JSON.stringify(orders, null, 2));
  } finally {
    release();
  }
}

async function updateOrder(
  orderId: string,
  patch: Partial<Order>,
  cfg: QuantTradingConfig,
  accountName?: string,
): Promise<Order | null> {
  const release = await ordersMutex.acquire();
  try {
    const orders = await loadOrders(cfg, accountName);
    const idx = orders.findIndex((o) => o.orderId === orderId);
    if (idx < 0) return null;
    Object.assign(orders[idx], patch, { updatedAt: new Date().toISOString() });
    await fs.writeFile(ordersPath(cfg, accountName), JSON.stringify(orders, null, 2));
    return orders[idx];
  } finally {
    release();
  }
}

async function loadTrades(cfg: QuantTradingConfig, accountName?: string): Promise<TradeRecord[]> {
  try {
    return JSON.parse(await fs.readFile(tradesPath(cfg, accountName), "utf-8")) as TradeRecord[];
  } catch {
    return [];
  }
}

async function appendTrade(trade: TradeRecord, cfg: QuantTradingConfig, accountName?: string): Promise<void> {
  const release = await tradesMutex.acquire();
  try {
    await ensureDir(cfg);
    const trades = await loadTrades(cfg, accountName);
    trades.push(trade);
    await fs.writeFile(tradesPath(cfg, accountName), JSON.stringify(trades, null, 2));
  } finally {
    release();
  }
}

// ── Result type ──────────────────────────────────────────────────

export type SubmitResult =
  | { ok: true; order: Order }
  | { ok: false; reason: string; ruleId?: string };

// ── OrderManager ─────────────────────────────────────────────────

// ── Market type detection ───────────────────────────────────────

/**
 * Infer the MarketType from a symbol code:
 * - 6-digit all-numeric (e.g. 600519, 000001, 510300) → cn_stock
 * - xxxxxx.OF (fund codes)                              → fund
 * - Ends in .HK or 4-5 digit numeric                   → hk_stock
 * - Letters only / US ticker format (e.g. AAPL)        → us_stock
 * - Futures codes (e.g. RBL, IFL, CUL, IF2503)         → futures
 * - Fallback                                            → cn_stock
 */
function detectMarketType(symbol: string): import("../types.js").MarketType {
  const s = symbol.toUpperCase().trim();
  if (/\.OF$/i.test(s)) return "fund";
  if (/\.HK$/i.test(s) || /^\d{4,5}$/.test(s)) return "hk_stock";
  if (/^[A-Z]{1,5}$/.test(s)) return "us_stock";
  // CN futures: starts with letters then digits (e.g. RB2505, IF2503, IC, IH)
  if (
    /^(IF|IH|IC|IM|RB|HC|CU|AL|ZN|NI|SN|PB|RU|FU|BU|AU|AG|NR|UR|SA|MA|CF|SR|ZC|JD|LH|AP|CJ|PF)(\d{2,4})?$/i.test(
      s,
    )
  )
    return "futures";
  // Default for 6-digit A-share codes
  return "cn_stock";
}

export class OrderManager {
  private cfg: QuantTradingConfig;
  readonly broker: IBrokerAdapter;
  private riskGate: RiskGate;
  private confirmMgr: ConfirmationManager;
  private api: OpenClawPluginApi | null;
  private tradeCb?: (trade: TradeRecord) => void;
  /** Account name for multi-account file namespacing. */
  readonly accountName: string | undefined;

  // Tracks open buy orders for a symbol so we can match exits to entries
  // symbol → list of open entry order IDs (queue: FIFO)
  private openBuys: Map<string, string[]> = new Map();
  /** Resolves when openBuys rebuild completes (prevents race with early fills). */
  private readonly initialized: Promise<void>;

  constructor(
    cfg: QuantTradingConfig,
    broker: IBrokerAdapter,
    api: OpenClawPluginApi | null = null,
    accountName?: string,
  ) {
    this.cfg = cfg;
    this.broker = broker;
    this.api = api;
    this.accountName = accountName;
    this.riskGate = new RiskGate(cfg.tradingAccount?.riskLimits as RiskLimitsConfig | undefined);
    this.confirmMgr = new ConfirmationManager(cfg, api);

    // Wire broker callbacks
    this.broker.subscribeFills((fill) => void this.handleFill(fill));
    this.broker.subscribeOrderStatus(
      (id, status, order) => void this.handleStatusChange(id, status, order),
    );

    // Rebuild openBuys from persisted orders — handleFill awaits this
    this.initialized = this.rebuildOpenBuys();
  }

  /**
   * Rebuild the openBuys map from persisted orders.
   * This recovers state after a restart so that sell fills can still
   * be matched to their entry buy orders.
   */
  private async rebuildOpenBuys(): Promise<void> {
    try {
      const orders = await loadOrders(this.cfg, this.accountName);
      // Find buy/cover orders that are filled or partially filled but haven't
      // been paired with a corresponding sell yet.
      const trades = await loadTrades(this.cfg, this.accountName);
      const pairedEntryIds = new Set(trades.map((t) => t.entryOrderId));

      for (const order of orders) {
        if (
          (order.side === "buy" ||
            order.side === "cover" ||
            order.side === "buy_open" ||
            order.side === "sell_open") &&
          (order.status === "filled" || order.status === "partial") &&
          !pairedEntryIds.has(order.orderId)
        ) {
          const buys = this.openBuys.get(order.symbol) ?? [];
          buys.push(order.orderId);
          this.openBuys.set(order.symbol, buys);
        }
      }
    } catch {
      // Ignore — may not have data dir yet
    }
  }

  /** Set a callback that fires when a trade is completed (sell fill matched to entry). */
  setTradeCallback(cb: (trade: TradeRecord) => void): void {
    this.tradeCb = cb;
  }

  get confirmationManager(): ConfirmationManager {
    return this.confirmMgr;
  }

  /**
   * Submit an order from a trading signal.
   * Runs position sizing → risk check → optional human confirmation → broker submission.
   */
  async submitFromSignal(
    signal: TradingSignal,
    riskOverrides?: { maxOrderValue?: number; maxPositionPct?: number },
  ): Promise<SubmitResult> {
    // Compute quantity via position sizing (unless signal has explicit quantity)
    let quantity = signal.quantity ?? 100;
    const sizingCfg = this.cfg.tradingAccount?.positionSizing;
    if (!signal.quantity && sizingCfg && signal.price > 0) {
      try {
        const [account] = await Promise.all([this.broker.queryAccount()]);
        const equity = account.totalAssets || account.cash || 100_000;
        const result = computePositionSize({
          method: sizingCfg.method,
          equity,
          price: signal.price,
          amount: sizingCfg.amount,
          pct: sizingCfg.pct,
          riskPct: sizingCfg.riskPct,
          stopPct: sizingCfg.stopPct,
          winRate: sizingCfg.winRate,
          winLossRatio: sizingCfg.winLossRatio,
          kellyDivisor: sizingCfg.kellyDivisor,
          atrMultiplier: sizingCfg.atrMultiplier,
          maxPositionPct: sizingCfg.maxPositionPct,
          maxQuantity: sizingCfg.maxQuantity,
        });
        if (result.quantity > 0) {
          quantity = result.quantity;
        }
      } catch {
        // Sizing failed — fall back to default 100 shares
      }
    }

    const req: OrderRequest = {
      symbol: signal.symbol,
      market: detectMarketType(signal.symbol),
      side: signal.direction,
      orderType: signal.price > 0 ? "limit" : "market",
      quantity,
      price: signal.price,
      strategyId: signal.strategyId,
      signalId: `${signal.timestamp}_${signal.symbol}`,
    };
    return this.submit(req, riskOverrides);
  }

  /**
   * Submit an order directly (for manual / tool-triggered orders).
   */
  async submit(
    req: OrderRequest,
    riskOverrides?: { maxOrderValue?: number; maxPositionPct?: number; skipMarketHours?: boolean },
  ): Promise<SubmitResult> {
    // Get current account state for risk checks
    let account: BrokerAccount;
    let positions: LivePosition[];
    try {
      [account, positions] = await Promise.all([
        this.broker.queryAccount(),
        this.broker.queryPositions(),
      ]);
    } catch (err) {
      return { ok: false, reason: `无法获取账户信息: ${(err as Error).message}` };
    }

    const dailyLoss = await this.getDailyPnl().then(
      (p) => (p < 0 ? -p : 0),
      () => 0, // If PnL lookup fails, assume zero daily loss
    );

    // Fetch open orders for the max-open-orders rule.
    // Filter by current broker type so paper orders don't block real QMT orders.
    const openOrders = await this.getOrders({
      status: ["pending", "submitted", "partial"],
      brokerType: this.broker.brokerType,
    });

    // Risk check
    const riskResult = this.riskGate.check(
      req,
      account,
      positions,
      dailyLoss,
      req.market,
      openOrders,
      riskOverrides,
    );
    if (!riskResult.passed) {
      return { ok: false, reason: riskResult.reason ?? "风控拒绝", ruleId: riskResult.failedRule };
    }

    // Human confirmation if required
    const needsConfirmation =
      riskResult.needsConfirmation || this.cfg.tradingAccount?.confirmationRequired === true;

    if (needsConfirmation) {
      const placeholderId = randomUUID();
      const confirmed = await this.confirmMgr.request(
        req,
        riskResult.confirmationReason ?? "需要确认",
        placeholderId,
      );
      if (!confirmed) {
        return { ok: false, reason: "用户拒绝或确认超时" };
      }
    }

    // Place order
    let order: Order;
    try {
      order = await this.broker.placeOrder(req);
    } catch (err) {
      return { ok: false, reason: `下单失败: ${(err as Error).message}` };
    }

    // Persist expiresAt from the request onto the order
    if (req.expiresAt) {
      order.expiresAt = req.expiresAt;
    }

    // Persist
    await appendOrder(order, this.cfg, this.accountName);

    // Track open buys/sell_open for PnL matching
    if (
      order.side === "buy" ||
      order.side === "cover" ||
      order.side === "buy_open" ||
      order.side === "sell_open"
    ) {
      const buys = this.openBuys.get(order.symbol) ?? [];
      buys.push(order.orderId);
      this.openBuys.set(order.symbol, buys);
    }

    this.log(
      `下单成功: ${order.side} ${order.symbol} ${order.quantity} @ ${order.price ?? "市价"} [${order.orderId.slice(0, 8)}]`,
    );
    return { ok: true, order };
  }

  /** Cancel an open order. */
  async cancel(orderId: string): Promise<void> {
    // Load from disk to validate status before calling broker
    const orders = await loadOrders(this.cfg, this.accountName);
    const diskOrder = orders.find((o) => o.orderId === orderId);
    if (!diskOrder) throw new Error(`Order ${orderId} not found`);

    // Already terminal — nothing to do
    const terminal: OrderStatus[] = ["cancelled", "filled", "rejected", "expired"];
    if (terminal.includes(diskOrder.status)) return;

    // Pending order never submitted to broker — cancel locally only
    if (diskOrder.status === "pending" && !diskOrder.brokerOrderId) {
      await updateOrder(orderId, { status: "cancelled" }, this.cfg, this.accountName);
      return;
    }

    try {
      await this.broker.cancelOrder(orderId);
    } catch (err) {
      const msg = (err as Error).message;
      // Broker cache miss after restart — mark cancelled locally
      if (msg.includes("not found")) {
        this.log(`⚠️ Broker cache miss for ${orderId.slice(0, 8)}, marking cancelled locally`);
      } else {
        throw err;
      }
    }
    await updateOrder(orderId, { status: "cancelled" }, this.cfg, this.accountName);
  }

  /** Cancel all open orders, optionally filtered by symbol. */
  async cancelAll(symbol?: string): Promise<void> {
    const orders = await loadOrders(this.cfg, this.accountName);
    const open = orders.filter(
      (o) =>
        (o.status === "submitted" || o.status === "partial" || o.status === "pending") &&
        (!symbol || o.symbol === symbol),
    );
    await Promise.allSettled(open.map((o) => this.cancel(o.orderId)));
  }

  /**
   * Cancel all open orders whose `expiresAt` is in the past.
   * Call this periodically (e.g. every 30s from the live engine tick).
   */
  async cancelExpiredOrders(): Promise<string[]> {
    const orders = await loadOrders(this.cfg, this.accountName);
    const now = Date.now();
    const expired = orders.filter(
      (o) =>
        o.expiresAt &&
        (o.status === "submitted" || o.status === "partial" || o.status === "pending") &&
        new Date(o.expiresAt).getTime() <= now,
    );
    const cancelled: string[] = [];
    for (const o of expired) {
      try {
        await this.cancel(o.orderId);
        cancelled.push(o.orderId);
        this.log(
          `⏰ 订单超时自动撤销: ${o.symbol} ${o.side} ${o.quantity} [${o.orderId.slice(0, 8)}]`,
        );
      } catch {
        // best-effort cancel
      }
    }
    return cancelled;
  }

  /** Return today's net PnL (realised fills today). */
  async getDailyPnl(): Promise<number> {
    const trades = await loadTrades(this.cfg, this.accountName);
    const today = new Date().toISOString().slice(0, 10);
    return trades
      .filter((t) => t.exitTime?.startsWith(today))
      .reduce((sum, t) => sum + t.netPnl, 0);
  }

  /** Return all orders, with optional filter. */
  async getOrders(filter?: {
    symbol?: string;
    status?: OrderStatus[];
    brokerType?: string;
  }): Promise<Order[]> {
    let orders = await loadOrders(this.cfg, this.accountName);
    if (filter?.symbol) orders = orders.filter((o) => o.symbol === filter.symbol);
    if (filter?.status?.length) orders = orders.filter((o) => filter.status!.includes(o.status));
    if (filter?.brokerType) orders = orders.filter((o) => o.brokerType === filter.brokerType);
    return orders;
  }

  /** Return all trade records. */
  async getTrades(symbol?: string): Promise<TradeRecord[]> {
    const trades = await loadTrades(this.cfg, this.accountName);
    return symbol ? trades.filter((t) => t.symbol === symbol) : trades;
  }

  // ── Private event handlers ───────────────────────────────────

  private async handleFill(fill: Fill): Promise<void> {
    // Wait for openBuys rebuild to finish before matching fills
    await this.initialized;

    const now = new Date().toISOString();

    // Update order status (serialized via mutex to prevent stale filledQty reads)
    let order: Order | undefined;
    let allOrders: Order[] = [];
    const release = await ordersMutex.acquire();
    try {
      allOrders = await loadOrders(this.cfg, this.accountName);
      const idx = allOrders.findIndex((o) => o.orderId === fill.orderId);
      order = idx >= 0 ? allOrders[idx] : undefined;
      if (order && idx >= 0) {
        const prevFilled = order.filledQty ?? 0;
        const newFilledQty = prevFilled + fill.quantity;
        const newStatus: OrderStatus = newFilledQty >= order.quantity ? "filled" : "partial";
        // VWAP: volume-weighted average fill price across partial fills
        const prevAvg = order.avgFillPrice ?? 0;
        const vwap =
          prevFilled > 0
            ? (prevAvg * prevFilled + fill.price * fill.quantity) / newFilledQty
            : fill.price;
        Object.assign(allOrders[idx], {
          filledQty: newFilledQty,
          avgFillPrice: vwap,
          status: newStatus,
          filledAt: newStatus === "filled" ? now : undefined,
          updatedAt: now,
        });
        await fs.writeFile(ordersPath(this.cfg, this.accountName), JSON.stringify(allOrders, null, 2));
      }
    } finally {
      release();
    }

    // Generate trade record for sell/close fills
    if (
      fill.side === "sell" ||
      fill.side === "short" ||
      fill.side === "sell_close" ||
      fill.side === "buy_close"
    ) {
      const buys = this.openBuys.get(fill.symbol);
      const entryOrderId = buys?.shift() ?? "unknown";
      if (buys?.length === 0) this.openBuys.delete(fill.symbol);

      // Reuse orders loaded above instead of re-reading from disk
      const entryOrder = allOrders.find((o) => o.orderId === entryOrderId);
      const entryPrice = entryOrder?.avgFillPrice ?? entryOrder?.price ?? fill.price;

      const grossPnl =
        (fill.price - entryPrice) *
        fill.quantity *
        (fill.side === "short" || fill.side === "buy_close" ? -1 : 1);
      // Estimate entry-side commission using fill's commission as exit-side proxy
      // (same rate both sides for A-shares; the fill already includes exit commission)
      const entryCommission = entryOrder
        ? entryOrder.avgFillPrice
          ? fill.commission *
            ((entryOrder.avgFillPrice * fill.quantity) / (fill.price * fill.quantity || 1))
          : fill.commission
        : 0;
      const commission = fill.commission + entryCommission;
      const tax = fill.tax ?? 0;
      const netPnl = grossPnl - commission - tax;

      const trade: TradeRecord = {
        tradeId: randomUUID(),
        symbol: fill.symbol,
        market: order?.market ?? "cn_stock",
        side: fill.side,
        entryTime: entryOrder?.createdAt ?? now,
        exitTime: now,
        entryPrice,
        exitPrice: fill.price,
        quantity: fill.quantity,
        grossPnl,
        commission,
        netPnl,
        strategyId: order?.strategyId,
        entryOrderId,
        exitOrderId: fill.orderId,
      };
      await appendTrade(trade, this.cfg, this.accountName);
      this.tradeCb?.(trade);
      this.log(`成交记录: ${fill.symbol} 净盈亏 ¥${netPnl.toFixed(2)}`);
    }
  }

  private async handleStatusChange(
    orderId: string,
    status: OrderStatus,
    _order: Order,
  ): Promise<void> {
    await updateOrder(orderId, { status }, this.cfg, this.accountName);
    if (status === "rejected") {
      this.log(`订单被拒绝: ${orderId.slice(0, 8)}`);
    }
  }

  private log(msg: string): void {
    if (this.api) {
      this.api.logger.info(`[OrderManager] ${msg}`);
    } else {
      console.log(`[OrderManager] ${msg}`);
    }
  }
}
