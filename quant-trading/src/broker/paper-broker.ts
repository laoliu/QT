import { randomUUID } from "node:crypto";
/**
 * PaperBroker — simulation broker backed by the local portfolio.json file.
 *
 * Behaviour:
 * - Market orders fill immediately at the requested price.
 * - Limit orders are queued and matched against the next quote update.
 * - Direction guards: buy/sell_open reject when a conflicting position exists.
 * - Over-cover/over-sell are clamped to available position quantity.
 * - Commission: 0.025% buy + 0.025% sell + 0.1% stamp duty on sell (A-share defaults).
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createDataProvider } from "../data/market-data-provider.js";
import type {
  BrokerAccount,
  BrokerType,
  Fill,
  LivePosition,
  MarketType,
  Order,
  OrderStatus,
  Portfolio,
  PortfolioPosition,
  QuoteSnapshot,
  QuantTradingConfig,
} from "../types.js";
import { AsyncMutex } from "../utils/async-mutex.js";
import type {
  IBrokerAdapter,
  OrderFilter,
  OrderRequest,
  QuoteCallback,
  FillCallback,
  OrderStatusCallback,
  ModifyOrderRequest,
} from "./adapter.js";

// Per-file mutexes to serialize portfolio/orders I/O
const portfolioMutex = new AsyncMutex();
const ordersMutex = new AsyncMutex();

// ── File helpers ─────────────────────────────────────────────────

function dataDir(config: QuantTradingConfig): string {
  return config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
}

function portfolioPath(config: QuantTradingConfig): string {
  return path.join(dataDir(config), "portfolio-paper.json");
}

function ordersPath(config: QuantTradingConfig): string {
  return path.join(dataDir(config), "orders-paper.json");
}

/** Legacy paths — for migration from old single-file layout */
function legacyPortfolioPath(config: QuantTradingConfig): string {
  return path.join(dataDir(config), "portfolio.json");
}

async function ensureDir(cfg: QuantTradingConfig): Promise<void> {
  await fs.mkdir(dataDir(cfg), { recursive: true });
}

async function loadPortfolio(cfg: QuantTradingConfig): Promise<Portfolio> {
  const filePath = portfolioPath(cfg);
  try {
    const raw = await fs.readFile(filePath, "utf-8");
    return JSON.parse(raw) as Portfolio;
  } catch (err: unknown) {
    const code = (err as NodeJS.ErrnoException).code;
    if (code === "ENOENT") {
      // New file doesn't exist — try migrating from legacy path
      try {
        const legacyPath = legacyPortfolioPath(cfg);
        const legacyRaw = await fs.readFile(legacyPath, "utf-8");
        const legacy = JSON.parse(legacyRaw) as Portfolio;
        // Save to new path
        await ensureDir(cfg);
        await fs.writeFile(filePath, JSON.stringify(legacy, null, 2));
        console.log(`[PaperBroker] 已迁移 portfolio.json → portfolio-paper.json`);
        return legacy;
      } catch {
        // No legacy file either — use default
      }
    } else {
      // Corrupted JSON or other error — backup and warn
      console.warn(
        `[PaperBroker] portfolio.json 读取失败，已重置为默认: ${(err as Error).message}`,
      );
      try {
        const bakPath = `${filePath}.bak-${Date.now()}`;
        await fs.copyFile(filePath, bakPath);
        console.warn(`[PaperBroker] 已备份损坏文件至 ${bakPath}`);
      } catch {
        // Backup failed — file may not exist or be unreadable
      }
    }
    return {
      id: "default",
      name: "模拟组合",
      cash: 100_000,
      totalValue: 100_000,
      positions: [],
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
  }
}

async function savePortfolio(p: Portfolio, cfg: QuantTradingConfig): Promise<void> {
  await ensureDir(cfg);
  p.updatedAt = new Date().toISOString();
  await fs.writeFile(portfolioPath(cfg), JSON.stringify(p, null, 2));
}

async function loadOrders(cfg: QuantTradingConfig): Promise<Order[]> {
  try {
    const raw = await fs.readFile(ordersPath(cfg), "utf-8");
    return JSON.parse(raw) as Order[];
  } catch {
    return [];
  }
}

// Terminal order statuses that can be pruned
const TERMINAL_STATUSES: Set<OrderStatus> = new Set(["filled", "cancelled", "rejected"]);

// Maximum number of terminal orders to keep on disk
const MAX_TERMINAL_ORDERS = 2000;

async function saveOrders(orders: Order[], cfg: QuantTradingConfig): Promise<void> {
  await ensureDir(cfg);
  // Prune: keep all active orders + newest MAX_TERMINAL_ORDERS terminal orders
  const active: Order[] = [];
  const terminal: Order[] = [];
  for (const o of orders) {
    if (TERMINAL_STATUSES.has(o.status)) terminal.push(o);
    else active.push(o);
  }
  const pruned =
    terminal.length > MAX_TERMINAL_ORDERS
      ? [...active, ...terminal.slice(terminal.length - MAX_TERMINAL_ORDERS)]
      : orders;
  await fs.writeFile(ordersPath(cfg), JSON.stringify(pruned, null, 2));
}

// ── Commission model (A-share defaults) ──────────────────────────

const CN_BUY_COMMISSION = 0.00025;
const CN_SELL_COMMISSION = 0.00025;
const CN_STAMP_DUTY = 0.001; // 0.1% on sell
/** Per-contract commission for CN exchange options (¥1.7 to be conservative). */
const CN_OPTION_FEE_PER_CONTRACT = 1.7;
/** Simplified futures commission: per-lot fee (covers most products). */
const CN_FUTURES_FEE_PER_LOT = 5;

/** Default futures margin rate when no FuturesConfig is available. */
const DEFAULT_FUTURES_MARGIN_RATE = 0.1;
/** Default futures contract multiplier when no FuturesConfig is available. */
const DEFAULT_FUTURES_MULTIPLIER = 10;

function calcCommission(
  side: "buy" | string,
  price: number,
  qty: number,
  market: MarketType,
): number {
  // Option markets: per-contract flat fee
  if (market === "etf_option" || market === "index_option" || market === "commodity_option") {
    return Math.max(1, qty * CN_OPTION_FEE_PER_CONTRACT);
  }
  // Futures: per-lot flat fee
  if (market === "futures") {
    return Math.max(1, qty * CN_FUTURES_FEE_PER_LOT);
  }
  const value = price * qty;
  if (market === "cn_stock" || market === "fund") {
    // Stamp duty is tracked separately in fill.tax — don't include it here
    return side === "buy" ? value * CN_BUY_COMMISSION : value * CN_SELL_COMMISSION;
  }
  // US/HK: simplified flat $1 per 100 shares
  return Math.max(1, Math.ceil(qty / 100));
}

// ── PaperBroker ──────────────────────────────────────────────────

export class PaperBroker implements IBrokerAdapter {
  readonly brokerType: BrokerType = "paper";
  connected = false;

  private cfg: QuantTradingConfig;
  private fillCbs: FillCallback[] = [];
  private statusCbs: OrderStatusCallback[] = [];
  private quoteSubs: Map<
    string,
    { interval: ReturnType<typeof setInterval>; cbs: QuoteCallback[] }
  > = new Map();
  // pending limit orders awaiting price match
  private pendingLimitOrders: Order[] = [];
  private limitMatchInterval: ReturnType<typeof setInterval> | null = null;
  /** Timer for daily mark-to-market settlement (futures). */
  private settlementInterval: ReturnType<typeof setInterval> | null = null;
  /** Flag to prevent duplicate settlement on the same calendar day. */
  private lastSettlementDate: string | null = null;

  private dataProvider: ReturnType<typeof createDataProvider> | null = null;

  constructor(cfg: QuantTradingConfig) {
    this.cfg = cfg;
  }

  async connect(): Promise<void> {
    if (this.connected) return;
    this.connected = true;
    // Create shared data provider instance (reused across getLastPrice & matchLimitOrders)
    this.dataProvider = createDataProvider(this.cfg);
    // Reload any pending limit orders from disk
    const orders = await loadOrders(this.cfg);
    this.pendingLimitOrders = orders.filter(
      (o) => o.status === "submitted" && o.orderType === "limit",
    );
    // Limit order matching loop — every 10 s
    this.limitMatchInterval = setInterval(() => void this.matchLimitOrders(), 10_000);
    // Daily settlement timer — check every 60 s, settle when 15:00 CST and enabled
    if (this.cfg.futuresConfig?.enableDailySettlement) {
      this.settlementInterval = setInterval(() => void this.tryDailySettlement(), 60_000);
    }
  }

  async disconnect(): Promise<void> {
    this.connected = false;
    if (this.limitMatchInterval) {
      clearInterval(this.limitMatchInterval);
      this.limitMatchInterval = null;
    }
    if (this.settlementInterval) {
      clearInterval(this.settlementInterval);
      this.settlementInterval = null;
    }
    for (const [, sub] of this.quoteSubs) clearInterval(sub.interval);
    this.quoteSubs.clear();
    this.dataProvider = null;
  }

  async placeOrder(req: OrderRequest): Promise<Order> {
    // Input validation — reject obviously invalid orders
    if (!req.symbol) throw new Error("下单失败: symbol 不能为空");
    if (!Number.isFinite(req.quantity) || req.quantity <= 0) {
      throw new Error(`下单失败: quantity 必须为正数，收到 ${req.quantity}`);
    }
    if (req.price != null && (!Number.isFinite(req.price) || req.price < 0)) {
      throw new Error(`下单失败: price 必须为非负数，收到 ${req.price}`);
    }
    if (req.orderType === "limit" && (!req.price || req.price <= 0)) {
      throw new Error("下单失败: 限价单必须指定正数 price");
    }

    const now = new Date().toISOString();
    const orderId = randomUUID();
    const order: Order = {
      orderId,
      clientOrderId: req.clientOrderId,
      symbol: req.symbol,
      market: req.market,
      side: req.side,
      orderType: req.orderType,
      price: req.price,
      stopPrice: req.stopPrice,
      quantity: req.quantity,
      filledQty: 0,
      status: "submitted",
      strategyId: req.strategyId,
      signalId: req.signalId,
      brokerType: "paper",
      createdAt: now,
      updatedAt: now,
      submittedAt: now,
      // Option metadata
      strike: req.strike,
      expiry: req.expiry,
      optionType: req.optionType,
      underlying: req.underlying,
    };

    // Append to orders file
    const orders = await loadOrders(this.cfg);
    orders.push(order);
    await saveOrders(orders, this.cfg);

    // Market orders fill immediately
    if (req.orderType === "market") {
      const fillPrice = req.price ?? (await this.getLastPrice(req.symbol, req.market));
      await this.executeFill(order, fillPrice, req.quantity);
    } else {
      // Limit / stop orders go to the pending queue
      this.pendingLimitOrders.push(order);
    }

    return order;
  }

  async cancelOrder(orderId: string): Promise<void> {
    const orders = await loadOrders(this.cfg);
    const idx = orders.findIndex((o) => o.orderId === orderId);
    if (idx < 0) throw new Error(`Order ${orderId} not found`);
    const o = orders[idx];
    if (!["submitted", "partial", "pending"].includes(o.status)) {
      throw new Error(`Cannot cancel order in status ${o.status}`);
    }
    const now = new Date().toISOString();
    o.status = "cancelled";
    o.updatedAt = now;
    orders[idx] = o;
    await saveOrders(orders, this.cfg);
    // Remove from pending limit queue
    this.pendingLimitOrders = this.pendingLimitOrders.filter((p) => p.orderId !== orderId);
    for (const cb of this.statusCbs) cb(orderId, "cancelled", o);
  }

  async modifyOrder(orderId: string, update: ModifyOrderRequest): Promise<Order> {
    // Paper: cancel + resubmit with new params
    const original = await this.queryOrder(orderId);
    if (!original) throw new Error(`Order ${orderId} not found`);
    await this.cancelOrder(orderId);
    return this.placeOrder({
      symbol: original.symbol,
      market: original.market,
      side: original.side,
      orderType: original.orderType,
      quantity: update.quantity ?? original.quantity,
      price: update.price ?? original.price,
      stopPrice: update.stopPrice ?? original.stopPrice,
      strategyId: original.strategyId,
      signalId: original.signalId,
    });
  }

  async queryOrder(orderId: string): Promise<Order | null> {
    const orders = await loadOrders(this.cfg);
    return orders.find((o) => o.orderId === orderId) ?? null;
  }

  async queryOrders(filter?: OrderFilter): Promise<Order[]> {
    const orders = await loadOrders(this.cfg);
    if (!filter) return orders;
    // Single-pass filter instead of chaining multiple .filter() calls
    return orders.filter(
      (o) =>
        (!filter.symbol || o.symbol === filter.symbol) &&
        (!filter.status?.length || filter.status.includes(o.status)) &&
        (!filter.strategyId || o.strategyId === filter.strategyId) &&
        (!filter.dateFrom || o.createdAt >= filter.dateFrom) &&
        (!filter.dateTo || o.createdAt <= filter.dateTo + "T99"),
    );
  }

  async queryPositions(): Promise<LivePosition[]> {
    const p = await loadPortfolio(this.cfg);
    const now = new Date().toISOString();

    // Refresh currentPrice from live quotes and compute PnL
    if (p.positions.length > 0) {
      const provider = this.getProvider();
      await Promise.all(
        p.positions.map(async (pos) => {
          try {
            const q = await provider.getQuote(pos.symbol, pos.market);
            if (q?.price) {
              pos.currentPrice = q.price;
              pos.marketValue = pos.quantity * q.price;
              if (pos.avgCost > 0) {
                pos.unrealizedPnl = (q.price - pos.avgCost) * pos.quantity;
                pos.unrealizedPnlPct = (q.price - pos.avgCost) / pos.avgCost;
              }
            }
          } catch {
            // Keep stale data if quote fails
          }
        }),
      );
    }

    return p.positions.map((pos) => this.paperPositionToLive(pos, now));
  }

  async queryAccount(): Promise<BrokerAccount> {
    const p = await loadPortfolio(this.cfg);

    // Refresh currentPrice for held positions from live quotes
    if (p.positions.length > 0) {
      const provider = this.getProvider();
      await Promise.all(
        p.positions.map(async (pos) => {
          try {
            const q = await provider.getQuote(pos.symbol, pos.market);
            if (q?.price) pos.currentPrice = q.price;
          } catch (err) {
            // Keep stale price — warn so users know a position price is stale
            console.warn(
              `[PaperBroker] quote refresh failed for ${pos.symbol}: ${(err as Error).message}`,
            );
          }
        }),
      );
    }

    const mv = p.positions.reduce((s, pos) => {
      // Shorts: use frozen margin for futures, abs(qty)*price for others
      if (pos.quantity < 0) {
        return s + (pos.frozenMargin ?? Math.abs(pos.quantity) * (pos.currentPrice ?? pos.avgCost));
      }
      return s + pos.quantity * (pos.currentPrice ?? pos.avgCost);
    }, 0);
    const totalAssets = p.cash + mv;
    // Use initial cash from portfolio creation (new portfolios default to 100k)
    const initCash = p.initialCash ?? 100_000;
    const profit = totalAssets - initCash;
    return {
      accountId: "paper",
      brokerType: "paper",
      cash: p.cash,
      frozenCash: 0,
      totalAssets,
      marketValue: mv,
      profit,
      profitPct: profit / initCash,
      lastSyncAt: new Date().toISOString(),
    };
  }

  subscribeQuote(symbols: string[], cb: QuoteCallback): () => void {
    const provider = this.getProvider();
    for (const sym of symbols) {
      const existing = this.quoteSubs.get(sym);
      if (existing) {
        existing.cbs.push(cb);
      } else {
        const cbs = [cb];
        // Poll every 30 s
        const interval = setInterval(async () => {
          try {
            const q = await provider.getQuote(sym);
            if (q) for (const fn of cbs) fn(q);
          } catch (err) {
            console.error(`[PaperBroker] quote poll failed for ${sym}: ${(err as Error).message}`);
          }
        }, 30_000);
        // Fire once immediately
        void provider
          .getQuote(sym)
          .then((q) => {
            if (q) for (const fn of cbs) fn(q);
          })
          .catch(() => {
            /* initial quote best-effort */
          });
        this.quoteSubs.set(sym, { interval, cbs });
      }
    }
    return () => {
      for (const sym of symbols) {
        const sub = this.quoteSubs.get(sym);
        if (sub) {
          sub.cbs = sub.cbs.filter((fn) => fn !== cb);
          if (sub.cbs.length === 0) {
            clearInterval(sub.interval);
            this.quoteSubs.delete(sym);
          }
        }
      }
    };
  }

  subscribeFills(cb: FillCallback): () => void {
    this.fillCbs.push(cb);
    return () => {
      this.fillCbs = this.fillCbs.filter((fn) => fn !== cb);
    };
  }

  subscribeOrderStatus(cb: OrderStatusCallback): () => void {
    this.statusCbs.push(cb);
    return () => {
      this.statusCbs = this.statusCbs.filter((fn) => fn !== cb);
    };
  }

  async isMarketOpen(market?: MarketType): Promise<boolean> {
    return isMarketOpen(market);
  }

  /** Paper broker supports option trading in simulation mode. */
  supportsOptions(): boolean {
    return true;
  }

  // ── Private helpers ─────────────────────────────────────────

  /** Get or lazily create the shared data provider instance. */
  private getProvider(): ReturnType<typeof createDataProvider> {
    if (!this.dataProvider) this.dataProvider = createDataProvider(this.cfg);
    return this.dataProvider;
  }

  private async getLastPrice(symbol: string, market: MarketType): Promise<number> {
    try {
      const q = await this.getProvider().getQuote(symbol, market);
      if (!q?.price) throw new Error(`no quote data for ${symbol}`);
      return q.price;
    } catch (err) {
      throw new Error(`getLastPrice failed for ${symbol}: ${(err as Error).message}`);
    }
  }

  /**
   * Extract futures contract multiplier and margin rate for a symbol.
   * Looks up FuturesConfig.contracts first, falls back to defaults.
   */
  private getFuturesSpec(symbol: string): { multiplier: number; marginRate: number } {
    const fc = this.cfg.futuresConfig;
    // Extract product code from symbol (e.g. "IF2503" → "IF", "RB2505" → "RB")
    const productCode = symbol.replace(/\d+/g, "").toUpperCase();
    const spec = fc?.contracts?.[productCode];
    return {
      multiplier: spec?.multiplier ?? fc?.defaultMultiplier ?? DEFAULT_FUTURES_MULTIPLIER,
      marginRate: spec?.marginRate ?? fc?.defaultMarginRate ?? DEFAULT_FUTURES_MARGIN_RATE,
    };
  }

  private async executeFill(order: Order, fillPrice: number, qty: number): Promise<void> {
    const commission = calcCommission(order.side, fillPrice, qty, order.market);
    const now = new Date().toISOString();
    const fill: Fill = {
      fillId: randomUUID(),
      orderId: order.orderId,
      symbol: order.symbol,
      side: order.side,
      price: fillPrice,
      quantity: qty,
      commission,
      tax:
        order.side === "sell" && order.market === "cn_stock"
          ? fillPrice * qty * CN_STAMP_DUTY
          : undefined,
      timestamp: now,
    };

    // Update portfolio (serialized to prevent concurrent I/O corruption)
    const releasePf = await portfolioMutex.acquire();
    try {
      const portfolio = await loadPortfolio(this.cfg);
      const isFutures = order.market === "futures";
      const value = fillPrice * qty;

      // Futures use margin-based model: cash deducted = margin, not full notional.
      // For non-futures, value = price × qty (full notional).
      let cashDelta = 0;

      // Cache position lookup — reused across all branches below
      const pos = portfolio.positions.find((p) => p.symbol === order.symbol) ?? null;

      // buy / buy_open: spend cash, add long position
      // cover / buy_close: release margin, reduce short (negative-qty) position
      if (
        order.side === "buy" ||
        order.side === "cover" ||
        order.side === "buy_open" ||
        order.side === "buy_close"
      ) {
        if (order.side === "buy_close" || order.side === "cover") {
          // Closing short position — guard: must have a short position to close
          if (!pos || pos.quantity >= 0) {
            // No short position to close — reject silently (risk gate should have caught this)
            console.warn(
              `[PaperBroker] ${order.side} rejected: no short position for ${order.symbol}`,
            );
            return;
          }
          // Clamp qty to available short quantity to prevent over-cover
          const absQtyBefore = Math.abs(pos.quantity);
          const clampedQty = Math.min(qty, absQtyBefore);
          if (isFutures) {
            // Futures buy_close/cover: release margin + realize P&L
            const { multiplier, marginRate } = this.getFuturesSpec(order.symbol);
            const marginReturn =
              absQtyBefore > 0 && pos.frozenMargin
                ? (pos.frozenMargin * clampedQty) / absQtyBefore
                : pos.avgCost * clampedQty * multiplier * marginRate;
            const pnl = (pos.avgCost - fillPrice) * clampedQty * multiplier; // short P&L
            cashDelta = marginReturn + pnl - commission;
          } else {
            cashDelta = -(fillPrice * clampedQty + commission);
          }
          pos.quantity += clampedQty;
          if (pos.quantity >= 0) {
            portfolio.positions = portfolio.positions.filter((p) => p.symbol !== order.symbol);
          } else if (isFutures && pos.frozenMargin) {
            // Reduce frozen margin proportionally
            const absQtyAfter = Math.abs(pos.quantity);
            pos.frozenMargin =
              absQtyBefore > 0 ? (pos.frozenMargin * absQtyAfter) / absQtyBefore : 0;
          }
        } else if (order.side === "buy" || order.side === "buy_open") {
          // buy / buy_open: open or add to long position
          // Guard: if a short position already exists, reject (use cover/buy_close instead)
          if (pos && pos.quantity < 0) {
            console.warn(
              `[PaperBroker] buy rejected: short position exists for ${order.symbol}. Use cover/buy_close to close short first.`,
            );
            return;
          }
          if (isFutures) {
            const { multiplier, marginRate } = this.getFuturesSpec(order.symbol);
            const marginCost = fillPrice * qty * multiplier * marginRate;
            cashDelta = -(marginCost + commission);
          } else {
            cashDelta = -(value + commission);
          }
          // Reject if insufficient funds
          if (portfolio.cash + cashDelta < 0) {
            console.warn(
              `[PaperBroker] buy rejected: insufficient funds for ${order.symbol}. Need ¥${(-cashDelta).toFixed(2)}, available ¥${portfolio.cash.toFixed(2)}`,
            );
            return;
          }
          if (pos) {
            const total = pos.quantity + qty;
            pos.avgCost = (pos.avgCost * pos.quantity + fillPrice * qty) / total;
            pos.quantity = total;
            if (isFutures) {
              const { multiplier, marginRate } = this.getFuturesSpec(order.symbol);
              pos.frozenMargin = pos.avgCost * total * multiplier * marginRate;
            }
          } else {
            const newPos: PortfolioPosition = {
              symbol: order.symbol,
              market: order.market,
              quantity: qty,
              avgCost: fillPrice,
              currentPrice: fillPrice,
              // Propagate option metadata from order
              strike: order.strike,
              expiry: order.expiry,
              optionType: order.optionType,
              underlying: order.underlying,
            };
            if (isFutures) {
              const { multiplier, marginRate } = this.getFuturesSpec(order.symbol);
              newPos.frozenMargin = fillPrice * qty * multiplier * marginRate;
            }
            portfolio.positions.push(newPos);
          }
        }
      } else if (order.side === "sell_open") {
        // Guard: reject sell_open when a long position exists (use sell/sell_close first)
        if (pos && pos.quantity > 0) {
          console.warn(
            `[PaperBroker] sell_open rejected: long position exists for ${order.symbol}. Use sell/sell_close to close long first.`,
          );
          return;
        }
        if (isFutures) {
          // Futures sell_open: deduct margin (short also requires margin)
          const { multiplier, marginRate } = this.getFuturesSpec(order.symbol);
          const marginCost = fillPrice * qty * multiplier * marginRate;
          cashDelta = -(marginCost + commission);
        } else {
          // Options sell_open: receive premium (cash increases)
          cashDelta = value - commission;
        }
        // Reject if insufficient funds for margin/commission
        if (cashDelta < 0 && portfolio.cash + cashDelta < 0) {
          console.warn(
            `[PaperBroker] sell_open rejected: insufficient funds for ${order.symbol}. Need ¥${(-cashDelta).toFixed(2)}, available ¥${portfolio.cash.toFixed(2)}`,
          );
          return;
        }
        if (pos) {
          // Existing position: make more negative (more short)
          pos.avgCost =
            (pos.avgCost * Math.abs(pos.quantity) + fillPrice * qty) /
            (Math.abs(pos.quantity) + qty);
          pos.quantity -= qty;
          if (isFutures) {
            const { multiplier, marginRate } = this.getFuturesSpec(order.symbol);
            pos.frozenMargin = pos.avgCost * Math.abs(pos.quantity) * multiplier * marginRate;
          }
        } else {
          const newPos: PortfolioPosition = {
            symbol: order.symbol,
            market: order.market,
            quantity: -qty, // negative = short
            avgCost: fillPrice,
            currentPrice: fillPrice,
            // Propagate option metadata from order
            strike: order.strike,
            expiry: order.expiry,
            optionType: order.optionType,
            underlying: order.underlying,
          };
          if (isFutures) {
            const { multiplier, marginRate } = this.getFuturesSpec(order.symbol);
            newPos.frozenMargin = fillPrice * qty * multiplier * marginRate;
          }
          portfolio.positions.push(newPos);
        }
      } else {
        // sell / sell_close: receive cash, reduce long position
        if (!pos || pos.quantity <= 0) {
          // No long position to sell — reject silently
          console.warn(
            `[PaperBroker] ${order.side} rejected: no long position for ${order.symbol}`,
          );
          return;
        }
        // Clamp qty to available position to prevent over-selling
        const clampedQty = Math.min(qty, pos.quantity);
        const tax = fill.tax ?? 0;
        if (isFutures) {
          // Futures sell_close: release margin + realize P&L
          const { multiplier, marginRate } = this.getFuturesSpec(order.symbol);
          const avgCost = pos.avgCost;
          const qtyBefore = pos.quantity;
          const marginReturn =
            qtyBefore > 0 && pos.frozenMargin
              ? (pos.frozenMargin * clampedQty) / qtyBefore
              : avgCost * clampedQty * multiplier * marginRate;
          const pnl = (fillPrice - avgCost) * clampedQty * multiplier; // long P&L
          cashDelta = marginReturn + pnl - commission;
        } else {
          cashDelta = fillPrice * clampedQty - commission - tax;
        }
        const qtyBeforeSell = pos.quantity;
        pos.quantity -= clampedQty;
        if (pos.quantity <= 0) {
          portfolio.positions = portfolio.positions.filter((p) => p.symbol !== order.symbol);
        } else if (isFutures && pos.frozenMargin && qtyBeforeSell > 0) {
          // Reduce frozen margin proportionally
          pos.frozenMargin = (pos.frozenMargin * pos.quantity) / qtyBeforeSell;
        }
      }
      portfolio.cash += cashDelta;
      portfolio.totalValue =
        portfolio.cash +
        portfolio.positions.reduce((s, p) => {
          // Shorts: use frozen margin for futures, abs(qty)*price for others
          if (p.quantity < 0) {
            return s + (p.frozenMargin ?? Math.abs(p.quantity) * (p.currentPrice ?? p.avgCost));
          }
          return s + p.quantity * (p.currentPrice ?? p.avgCost);
        }, 0);
      await savePortfolio(portfolio, this.cfg);
    } finally {
      releasePf();
    }

    // Update order record (serialized)
    const releaseOrd = await ordersMutex.acquire();
    try {
      const orders = await loadOrders(this.cfg);
      const idx = orders.findIndex((o) => o.orderId === order.orderId);
      if (idx >= 0) {
        const prevFilled = orders[idx].filledQty ?? 0;
        const prevAvg = orders[idx].avgFillPrice ?? fillPrice;
        const newFilledQty = prevFilled + qty;
        // VWAP across partial fills
        orders[idx].avgFillPrice =
          prevFilled > 0 ? (prevAvg * prevFilled + fillPrice * qty) / newFilledQty : fillPrice;
        orders[idx].filledQty = newFilledQty;
        orders[idx].status = newFilledQty >= order.quantity ? "filled" : "partial";
        orders[idx].filledAt = orders[idx].status === "filled" ? now : undefined;
        orders[idx].updatedAt = now;
        await saveOrders(orders, this.cfg);
        const updated = orders[idx];
        for (const cb of this.statusCbs) cb(order.orderId, "filled", updated);
      }
    } finally {
      releaseOrd();
    }
    for (const cb of this.fillCbs) cb(fill);
  }

  // ── Daily Mark-to-Market Settlement (Futures) ──────────────────

  /**
   * Check if it's time to settle (15:00 CST) and hasn't been done today.
   * Called every 60 s by the settlement timer.
   */
  async tryDailySettlement(): Promise<void> {
    const now = new Date();
    const cst = new Date(now.getTime() + 8 * 60 * 60 * 1000);
    const day = cst.getUTCDay();
    // Only Mon-Fri
    if (day === 0 || day === 6) return;
    const hm = cst.getUTCHours() * 100 + cst.getUTCMinutes();
    // Settlement window: 15:00-15:15 CST
    if (hm < 1500 || hm > 1515) return;
    const todayStr = cst.toISOString().slice(0, 10);
    if (this.lastSettlementDate === todayStr) return;
    this.lastSettlementDate = todayStr;
    await this.performDailySettlement();
  }

  /**
   * Daily mark-to-market settlement for futures positions.
   *
   * For each futures position:
   * 1. Fetch current settlement price (falls back to last traded price)
   * 2. Calculate P&L since last settlement (or since entry if first day)
   * 3. Adjust cash by the daily P&L (盈亏划转)
   * 4. Update position's settlementPrice for next day's reference
   * 5. Recalculate frozen margin at the new settlement price
   * 6. Check maintenance margin — trigger forced liquidation if breached
   */
  async performDailySettlement(): Promise<void> {
    const releasePf = await portfolioMutex.acquire();
    try {
      const portfolio = await loadPortfolio(this.cfg);
      const futuresPositions = portfolio.positions.filter((p) => p.market === "futures");
      if (futuresPositions.length === 0) return;

      const provider = this.getProvider();
      const maintenanceRatio = this.cfg.futuresConfig?.maintenanceMarginRatio ?? 0.8;

      for (const pos of futuresPositions) {
        const { multiplier, marginRate } = this.getFuturesSpec(pos.symbol);
        const absQty = Math.abs(pos.quantity);

        // Fetch settlement price (fallback to last price)
        let settlPrice: number;
        try {
          const q = await provider.getQuote(pos.symbol, "futures");
          settlPrice = q?.settlementPrice ?? q?.price ?? pos.currentPrice ?? pos.avgCost;
        } catch {
          settlPrice = pos.currentPrice ?? pos.avgCost;
        }

        // Reference price: previous settlement or entry avgCost
        const refPrice = pos.settlementPrice ?? pos.avgCost;

        // Daily P&L: (settlPrice - refPrice) × qty × multiplier
        // For long positions (qty > 0): positive when price rises
        // For short positions (qty < 0): positive when price drops
        const dailyPnl = (settlPrice - refPrice) * pos.quantity * multiplier;
        portfolio.cash += dailyPnl;

        // Update margin to reflect new settlement price
        const newMargin = settlPrice * absQty * multiplier * marginRate;
        const oldMargin = pos.frozenMargin ?? refPrice * absQty * multiplier * marginRate;
        // Adjust cash for margin change (负差补缴，正差释放)
        portfolio.cash -= newMargin - oldMargin;

        // Update position fields
        pos.settlementPrice = settlPrice;
        pos.frozenMargin = newMargin;
        pos.currentPrice = settlPrice;
        pos.unrealizedPnl = (settlPrice - pos.avgCost) * pos.quantity * multiplier;
        const costBasis = pos.avgCost * absQty * multiplier;
        pos.unrealizedPnlPct = costBasis > 0 ? (pos.unrealizedPnl ?? 0) / costBasis : 0;
      }

      // Recalculate total value
      portfolio.totalValue =
        portfolio.cash +
        portfolio.positions.reduce(
          (s, p) =>
            s +
            (p.market === "futures"
              ? (p.frozenMargin ?? 0)
              : Math.abs(p.quantity) * (p.currentPrice ?? p.avgCost)),
          0,
        );

      await savePortfolio(portfolio, this.cfg);

      // Check maintenance margin and force-liquidate if needed
      await this.checkMaintenanceMargin(portfolio, maintenanceRatio);
    } finally {
      releasePf();
    }
  }

  // ── Forced Liquidation ─────────────────────────────────────────

  /**
   * Check if any futures position breaches maintenance margin.
   * If total margin requirement exceeds available equity, force-close
   * the position with the largest margin requirement first.
   *
   * Maintenance margin = initial margin × maintenanceRatio.
   * Available equity = cash (after settlement).
   */
  private async checkMaintenanceMargin(
    portfolio: Portfolio,
    maintenanceRatio: number,
  ): Promise<void> {
    const futuresPositions = portfolio.positions.filter((p) => p.market === "futures");
    if (futuresPositions.length === 0) return;

    // Total maintenance margin requirement
    const totalMaintenanceMargin = futuresPositions.reduce((sum, p) => {
      const margin = p.frozenMargin ?? 0;
      return sum + margin * maintenanceRatio;
    }, 0);

    // Available funds = cash
    if (portfolio.cash >= totalMaintenanceMargin) return;

    // Margin breach — force-liquidate positions starting with the largest margin
    console.warn(
      `[PaperBroker] ⚠️ 保证金不足: 可用资金 ¥${portfolio.cash.toFixed(2)} < ` +
        `维持保证金 ¥${totalMaintenanceMargin.toFixed(2)}，触发强制平仓`,
    );

    // Sort by margin descending — close the biggest drain first
    const sorted = [...futuresPositions].sort(
      (a, b) => (b.frozenMargin ?? 0) - (a.frozenMargin ?? 0),
    );

    for (const pos of sorted) {
      // Re-check after each liquidation
      const currentCash = portfolio.cash;
      const currentMaintenance = portfolio.positions
        .filter((p) => p.market === "futures" && p.symbol !== pos.symbol)
        .reduce((sum, p) => sum + (p.frozenMargin ?? 0) * maintenanceRatio, 0);

      if (currentCash >= currentMaintenance) break;

      // Force-close this position
      const absQty = Math.abs(pos.quantity);
      const closePrice = pos.currentPrice ?? pos.avgCost;
      const { multiplier, marginRate } = this.getFuturesSpec(pos.symbol);
      const marginReturn = pos.frozenMargin ?? closePrice * absQty * multiplier * marginRate;

      // P&L at close
      const pnl = (closePrice - pos.avgCost) * pos.quantity * multiplier;
      const commission = calcCommission("sell", closePrice, absQty, "futures");

      portfolio.cash += marginReturn + pnl - commission;
      portfolio.positions = portfolio.positions.filter((p) => p.symbol !== pos.symbol);

      console.warn(
        `[PaperBroker] 强制平仓: ${pos.symbol} ${absQty}手 @ ¥${closePrice.toFixed(2)}，` +
          `释放保证金 ¥${marginReturn.toFixed(2)}，盈亏 ¥${pnl.toFixed(2)}`,
      );
    }

    // Recalculate total value
    portfolio.totalValue =
      portfolio.cash +
      portfolio.positions.reduce(
        (s, p) =>
          s +
          (p.market === "futures"
            ? (p.frozenMargin ?? 0)
            : Math.abs(p.quantity) * (p.currentPrice ?? p.avgCost)),
        0,
      );

    await savePortfolio(portfolio, this.cfg);
  }

  /** Try to match pending limit/stop/stop-limit orders against current market prices. */
  private async matchLimitOrders(): Promise<void> {
    if (this.pendingLimitOrders.length === 0) return;
    const provider = this.getProvider();
    for (const order of [...this.pendingLimitOrders]) {
      try {
        const q = await provider.getQuote(order.symbol, order.market);
        if (!q) continue;
        const isBuyLike =
          order.side === "buy" ||
          order.side === "cover" ||
          order.side === "buy_open" ||
          order.side === "buy_close";
        const isSellLike =
          order.side === "sell" ||
          order.side === "short" ||
          order.side === "sell_open" ||
          order.side === "sell_close";

        let triggered = false;

        if (order.orderType === "stop" || order.orderType === "stop_limit") {
          // ── Stop / Stop-Limit: first check if stop price is triggered ──
          const stopHit =
            order.stopPrice != null &&
            ((isBuyLike && q.price >= order.stopPrice) ||
              (isSellLike && q.price <= order.stopPrice));
          if (!stopHit) continue; // stop not yet activated

          if (order.orderType === "stop") {
            // Pure stop → fill at market
            triggered = true;
          } else {
            // Stop-limit → stop activated, now check limit price
            triggered =
              (isBuyLike && order.price !== undefined && q.price <= order.price) ||
              (isSellLike && order.price !== undefined && q.price >= order.price);
            if (!triggered) {
              // Stop activated but limit not yet matched; convert to plain limit
              // (stop condition already met, just waiting for limit fill)
              order.orderType = "limit";
              continue;
            }
          }
        } else {
          // ── Plain limit order ──
          triggered =
            (isBuyLike && order.price !== undefined && q.price <= order.price) ||
            (isSellLike && order.price !== undefined && q.price >= order.price);
        }

        if (triggered) {
          this.pendingLimitOrders = this.pendingLimitOrders.filter(
            (o) => o.orderId !== order.orderId,
          );
          await this.executeFill(order, q.price, order.quantity);
        }
      } catch (err) {
        // Will retry next tick; warn for debugging
        console.warn(
          `[PaperBroker] limit order match failed for ${order.orderId}: ${(err as Error).message}`,
        );
      }
    }
  }

  private paperPositionToLive(pos: PortfolioPosition, now: string): LivePosition {
    return {
      symbol: pos.symbol,
      name: pos.name,
      market: pos.market,
      quantity: pos.quantity,
      availableQty: Math.abs(pos.quantity), // paper: all available (abs for shorts)
      frozenQty: 0,
      todayBuyQty: 0,
      avgCost: pos.avgCost,
      currentPrice: pos.currentPrice,
      marketValue: pos.marketValue,
      unrealizedPnl: pos.unrealizedPnl,
      unrealizedPnlPct: pos.unrealizedPnlPct,
      source: "local",
      lastSyncAt: now,
      // Option metadata
      strike: pos.strike,
      expiry: pos.expiry,
      optionType: pos.optionType,
      underlying: pos.underlying,
      exerciseType: pos.exerciseType,
      // Futures metadata
      frozenMargin: pos.frozenMargin,
    };
  }
}

// ── Market hours helpers ─────────────────────────────────────────

/**
 * Returns true if the CN stock market is currently open (Beijing time).
 * Morning: 09:30–11:30, Afternoon: 13:00–15:00, Mon–Fri.
 */
export function isCnMarketOpen(_market?: MarketType): boolean {
  const now = new Date();
  // Convert to CST (UTC+8)
  const cst = new Date(now.getTime() + 8 * 60 * 60 * 1000);
  const day = cst.getUTCDay(); // 0=Sun, 6=Sat
  if (day === 0 || day === 6) return false;
  const h = cst.getUTCHours();
  const m = cst.getUTCMinutes();
  const hm = h * 100 + m;
  return (hm >= 930 && hm <= 1130) || (hm >= 1300 && hm <= 1500);
}

/**
 * Returns true if the US stock market is currently open (ET).
 * Regular session: 09:30–16:00 ET, Mon–Fri.
 * Uses proper DST rules (2nd Sunday of March – 1st Sunday of November).
 */
export function isUsMarketOpen(): boolean {
  const now = new Date();
  const year = now.getUTCFullYear();

  // Compute DST start: 2nd Sunday of March in UTC
  const mar1 = new Date(Date.UTC(year, 2, 1)); // March 1
  const firstSunMar = ((7 - mar1.getUTCDay()) % 7) + 1;
  const dstStart = Date.UTC(year, 2, firstSunMar + 7, 7); // 2nd Sunday at 02:00 EST = 07:00 UTC

  // Compute DST end: 1st Sunday of November in UTC
  const nov1 = new Date(Date.UTC(year, 10, 1)); // November 1
  const firstSunNov = ((7 - nov1.getUTCDay()) % 7) + 1;
  const dstEnd = Date.UTC(year, 10, firstSunNov, 6); // 1st Sunday at 02:00 EDT = 06:00 UTC

  const utcMs = now.getTime();
  const isDST = utcMs >= dstStart && utcMs < dstEnd;
  const offsetHours = isDST ? -4 : -5;
  const et = new Date(utcMs + offsetHours * 60 * 60 * 1000);
  const day = et.getUTCDay();
  if (day === 0 || day === 6) return false;
  const hm = et.getUTCHours() * 100 + et.getUTCMinutes();
  return hm >= 930 && hm < 1600;
}

/**
 * Returns true if the HK stock market is currently open (HKT = UTC+8).
 * Morning: 09:30–12:00, Afternoon: 13:00–16:00, Mon–Fri.
 */
export function isHkMarketOpen(): boolean {
  const now = new Date();
  const hkt = new Date(now.getTime() + 8 * 60 * 60 * 1000);
  const day = hkt.getUTCDay();
  if (day === 0 || day === 6) return false;
  const hm = hkt.getUTCHours() * 100 + hkt.getUTCMinutes();
  return (hm >= 930 && hm <= 1200) || (hm >= 1300 && hm <= 1600);
}

/**
 * Night session tiers for Chinese futures products (Beijing time).
 *
 * Tier 1 (21:00–23:00): most products (RB, I, J, JM, HC, FG, SA, etc.)
 * Tier 2 (21:00–01:00): base metals Cu, Al, Zn, Pb, Ni, Sn, BC, SS
 * Tier 3 (21:00–02:30): precious metals & crude — Au, Ag, SC, LU, NR, EC
 *
 * Products not listed (e.g. CFFEX financial futures) have no night session.
 */
const NIGHT_TIER_2: ReadonlySet<string> = new Set(["CU", "AL", "ZN", "PB", "NI", "SN", "BC", "SS"]);
const NIGHT_TIER_3: ReadonlySet<string> = new Set(["AU", "AG", "SC", "LU", "NR", "EC"]);
/** All products that have a night session (tiers 1-3). */
const NIGHT_SESSION_PRODUCTS: ReadonlySet<string> = new Set([
  // Tier 1 — 21:00-23:00
  "RB",
  "HC",
  "I",
  "J",
  "JM",
  "SP",
  "BU",
  "RU",
  "FU",
  "AO",
  "BR",
  "A",
  "B",
  "M",
  "Y",
  "C",
  "CS",
  "P",
  "V",
  "L",
  "PP",
  "EG",
  "EB",
  "PG",
  "JD",
  "RR",
  "LH",
  "CF",
  "SR",
  "TA",
  "MA",
  "OI",
  "RM",
  "FG",
  "ZC",
  "SA",
  "UR",
  "AP",
  "CJ",
  "PF",
  "PK",
  "SH",
  "PX",
  "SF",
  "SM",
  "SI",
  "LC",
  // Tier 2 & 3 explicitly included above
  ...NIGHT_TIER_2,
  ...NIGHT_TIER_3,
]);

/**
 * Get the night session end time (as hhmm, early morning) for a product code.
 * Returns 0 if the product has no night session.
 */
export function getNightSessionEnd(productCode?: string): number {
  if (!productCode) return 230; // conservative default: 02:30
  const code = productCode.replace(/\d+/g, "").toUpperCase();
  if (NIGHT_TIER_3.has(code)) return 230; // 02:30
  if (NIGHT_TIER_2.has(code)) return 100; // 01:00
  if (NIGHT_SESSION_PRODUCTS.has(code)) return 2300; // same-day 23:00 — no carry-over
  return 0; // no night session (e.g. CFFEX financial futures)
}

/**
 * Returns true during the CN commodity futures/options night session (Beijing time).
 *
 * When `productCode` is provided, uses per-product tier:
 *   Tier 1 (21:00–23:00): most SHFE/DCE/CZCE/GFEX commodities
 *   Tier 2 (21:00–01:00): base metals (Cu, Al, Zn, Pb, Ni, Sn, BC, SS)
 *   Tier 3 (21:00–02:30): Au, Ag, SC, LU, NR, EC
 *
 * When `productCode` is omitted, falls back to the conservative 21:00–02:30 window.
 */
export function isCnNightSessionOpen(productCode?: string): boolean {
  const now = new Date();
  const cst = new Date(now.getTime() + 8 * 60 * 60 * 1000);
  const day = cst.getUTCDay(); // 0=Sun, 6=Sat
  const hm = cst.getUTCHours() * 100 + cst.getUTCMinutes();

  const nightEnd = getNightSessionEnd(productCode);
  if (nightEnd === 0) return false; // no night session for this product

  // Night session starts Mon-Fri 21:00
  if (hm >= 2100 && day >= 1 && day <= 5) {
    // For Tier 1 (nightEnd = 2300), check that we're before the end time
    if (nightEnd === 2300) return hm < nightEnd;
    return true; // Tier 2/3 carry past midnight, so always open from 21:00 onward
  }

  // For Tier 1 (nightEnd = 2300), session ends same day — no early-morning carry-over
  if (nightEnd === 2300) return false;

  // Early morning carry-over: 00:00-nightEnd, Tue-Sat (after Mon-Fri night)
  if (hm <= nightEnd && day >= 2 && day <= 6) return true;

  return false;
}

/**
 * Unified market-hours check — returns true when the given market is in session.
 * Falls back to CN market hours for unrecognized types.
 */
export function isMarketOpen(market?: MarketType): boolean {
  switch (market) {
    case "us_stock":
      return isUsMarketOpen();
    case "hk_stock":
      return isHkMarketOpen();
    case "etf_option":
    case "index_option":
      // ETF/index options follow CN stock hours (9:30-11:30, 13:00-15:00)
      return isCnMarketOpen();
    case "commodity_option":
      // Commodity options include night session
      return isCnMarketOpen() || isCnNightSessionOpen();
    case "cn_stock":
    case "fund":
    default:
      return isCnMarketOpen();
    case "futures":
      // Chinese futures include night session (21:00-02:30 CST)
      return isCnMarketOpen() || isCnNightSessionOpen();
  }
}
