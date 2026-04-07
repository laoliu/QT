/**
 * FutuBroker — 富途 OpenD broker adapter.
 *
 * Communicates with `python/brokers/futu_broker.py` via PersistentPythonProcess
 * (NDJSON over stdin/stdout). The Python side uses futu-api to interact with
 * OpenD for order management and real-time quotes.
 *
 * Supported markets: HK stock, US stock, CN stock (via Futu OpenD).
 *
 * Setup:
 *   1. Download and run Futu OpenD (https://openapi.futunn.com/)
 *   2. Ensure OpenD is running (default: 127.0.0.1:11111)
 *   3. Set config.tradingAccount.futu with host, port, and trading password
 *   4. pip install futu-api
 */
import { randomUUID } from "node:crypto";
import { PersistentPythonProcess } from "../bridge/persistent-python.js";
import { detectMarketFromSymbol } from "../data/market-data-provider.js";
import type {
  BrokerAccount,
  BrokerType,
  Fill,
  FutuBrokerConfig,
  LivePosition,
  MarketType,
  Order,
  OrderStatus,
  QuoteSnapshot,
  QuantTradingConfig,
} from "../types.js";
import type {
  IBrokerAdapter,
  OrderFilter,
  OrderRequest,
  QuoteCallback,
  FillCallback,
  OrderStatusCallback,
  ModifyOrderRequest,
} from "./adapter.js";
import { isMarketOpen } from "./paper-broker.js";

// ── Raw shapes returned by futu_broker.py ─────────────────────────

type RawOrder = {
  order_id: string;
  code: string;
  trd_side: string;
  order_type: string;
  price: number;
  qty: number;
  dealt_qty: number;
  dealt_avg_price: number;
  status: string; // already mapped by Python
  create_time: string;
  updated_time: string;
  remark: string;
};

type RawPosition = {
  code: string;
  stock_name: string;
  qty: number;
  can_sell_qty: number;
  cost_price: number;
  nominal_price: number;
  market_val: number;
  unrealized_pl: number;
  unrealized_pl_pct: number;
  today_buy_qty: number;
  today_sell_qty: number;
};

type RawAccount = {
  cash: number;
  total_assets: number;
  market_val: number;
  frozen_cash: number;
  avl_withdrawal_cash: number;
  unrealized_pl: number;
  realized_pl: number;
};

type RawQuote = {
  code: string;
  last_price: number;
  open_price: number;
  high_price: number;
  low_price: number;
  prev_close_price: number;
  volume: number;
  turnover: number;
  timestamp: string;
};

type RawFill = {
  order_id: string;
  code: string;
  trd_side: string;
  dealt_price: number;
  dealt_qty: number;
  dealt_amount: number;
  commission: number;
  time: string;
};

// ── Futu side mapping ────────────────────────────────────────────

function mapFutuSide(trdSide: string): "buy" | "sell" | "short" | "cover" {
  // Futu TrdSide values: BUY, SELL, SELL_SHORT, BUY_BACK
  switch (trdSide) {
    case "BUY":
      return "buy";
    case "SELL":
      return "sell";
    case "SELL_SHORT":
      return "short";
    case "BUY_BACK":
      return "cover";
    default:
      return "buy";
  }
}

// ── Futu symbol handling ─────────────────────────────────────────

/**
 * Futu uses HK.00700, US.AAPL, SH.600519, SZ.000001 format.
 * Convert plain symbols to Futu format if needed.
 */
function toFutuSymbol(symbol: string, market?: MarketType): string {
  if (symbol.includes(".") && /^[A-Z]{2}\./.test(symbol)) {
    return symbol; // already Futu-prefixed like HK.00700
  }

  // If symbol has exchange suffix like 600519.SH, convert to SH.600519
  const suffixMatch = symbol.match(/^(\d{6})\.(SH|SZ|BJ)$/i);
  if (suffixMatch) {
    return `${suffixMatch[2].toUpperCase()}.${suffixMatch[1]}`;
  }

  // Infer market prefix from symbol pattern
  if (market === "hk_stock") return `HK.${symbol}`;
  if (market === "us_stock") return `US.${symbol}`;

  const code = symbol.replace(/[^0-9A-Za-z]/g, "");
  // Pure letters → US stock
  if (/^[A-Za-z]+$/.test(code)) return `US.${code}`;
  // Numeric codes → CN stock
  const n = parseInt(code, 10);
  if (!isNaN(n)) {
    if (n >= 500_000) return `SH.${code}`;
    return `SZ.${code}`;
  }
  return symbol;
}

function fromFutuSymbol(s: string): string {
  // HK.00700 → 00700, SH.600519 → 600519, US.AAPL → AAPL
  return s.replace(/^(HK|US|SH|SZ|BJ)\./, "");
}

function detectMarketFromFutuCode(code: string): MarketType {
  if (code.startsWith("HK.")) return "hk_stock";
  if (code.startsWith("US.")) return "us_stock";
  if (code.startsWith("SH.") || code.startsWith("SZ.") || code.startsWith("BJ.")) {
    return "cn_stock";
  }
  return detectMarketFromSymbol(fromFutuSymbol(code));
}

// ── Retry helper ─────────────────────────────────────────────────

const MAX_RETRIES = 3;
const RETRY_DELAYS = [1_000, 2_000, 5_000]; // ms

async function withRetry<T>(
  fn: () => Promise<T>,
  label: string,
  maxRetries: number = MAX_RETRIES,
): Promise<T> {
  let lastError: Error | undefined;
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      return await fn();
    } catch (err) {
      lastError = err as Error;
      const msg = lastError.message ?? "";
      // Don't retry on logical errors (order not found, rejected, etc.)
      if (
        msg.includes("not found") ||
        msg.includes("rejected") ||
        msg.includes("Not connected") ||
        msg.includes("not installed")
      ) {
        throw lastError;
      }
      if (attempt < maxRetries) {
        const delay = RETRY_DELAYS[Math.min(attempt, RETRY_DELAYS.length - 1)];
        console.warn(
          `[FutuBroker] ${label} attempt ${attempt + 1} failed: ${msg}, retrying in ${delay}ms`,
        );
        await new Promise((r) => setTimeout(r, delay));
      }
    }
  }
  throw lastError!;
}

// ── FutuBroker ──────────────────────────────────────────────────

export class FutuBroker implements IBrokerAdapter {
  readonly brokerType: BrokerType = "futu";
  connected = false;

  private proc: PersistentPythonProcess;
  private cfg: QuantTradingConfig;
  private futuCfg: FutuBrokerConfig;
  private fillCbs: FillCallback[] = [];
  private statusCbs: OrderStatusCallback[] = [];
  private quoteSubs: Map<string, QuoteCallback[]> = new Map();

  // local cache of orders created in this session
  private orderCache: Map<string, Order> = new Map();
  private static readonly ORDER_CACHE_MAX = 500;
  private static readonly ORDER_CACHE_TTL_MS = 4 * 3600 * 1000; // 4 hours

  constructor(cfg: QuantTradingConfig) {
    this.cfg = cfg;
    this.futuCfg = cfg.tradingAccount?.futu ?? {};

    this.proc = new PersistentPythonProcess("brokers.futu_broker", cfg, {
      host: this.futuCfg.openDHost ?? "127.0.0.1",
      port: this.futuCfg.openDPort ?? 11111,
      trade_env: this.futuCfg.tradeEnv ?? "REAL",
      pwd_unlock: this.futuCfg.pwdUnlock ?? "",
    });

    // Register event handler for push notifications from Python
    this.proc.onEvent((event, data) => this.handleEvent(event, data));
  }

  async connect(): Promise<void> {
    if (this.connected) return;
    await this.proc.start();
    await this.proc.call("connect");
    this.connected = true;
  }

  async disconnect(): Promise<void> {
    if (!this.connected) return;
    try {
      await this.proc.call("disconnect");
    } finally {
      this.proc.stop();
      this.connected = false;
    }
  }

  async placeOrder(req: OrderRequest): Promise<Order> {
    const now = new Date().toISOString();
    const clientOrderId = req.clientOrderId ?? randomUUID();
    const orderId = randomUUID();

    if (req.orderType === "limit" && (!req.price || req.price <= 0)) {
      throw new Error("限价单必须指定正数 price");
    }

    const futuCode = toFutuSymbol(req.symbol, req.market);
    const futuPrice = req.orderType === "market" ? 0 : (req.price ?? 0);

    const result = await withRetry(
      () =>
        this.proc.call<{ broker_order_id: string }>("place_order", {
          code: futuCode,
          side: req.side,
          order_type: req.orderType,
          price: futuPrice,
          qty: req.quantity,
          remark: clientOrderId,
        }),
      "placeOrder",
    );

    const order: Order = {
      orderId,
      clientOrderId,
      brokerOrderId: result.broker_order_id,
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
      brokerType: "futu",
      createdAt: now,
      updatedAt: now,
      submittedAt: now,
      expiresAt: req.expiresAt,
    };
    this.orderCache.set(orderId, order);
    this.pruneOrderCache();
    return order;
  }

  async cancelOrder(orderId: string): Promise<void> {
    const order = this.orderCache.get(orderId);
    if (!order?.brokerOrderId) throw new Error(`Order ${orderId} not found or has no broker ID`);

    await withRetry(
      () =>
        this.proc.call("cancel_order", {
          order_id: order.brokerOrderId,
        }),
      "cancelOrder",
    );

    order.status = "cancelled";
    order.updatedAt = new Date().toISOString();
    for (const cb of this.statusCbs) cb(orderId, "cancelled", order);
  }

  async modifyOrder(orderId: string, update: ModifyOrderRequest): Promise<Order> {
    const original = this.orderCache.get(orderId);
    if (!original) throw new Error(`Order ${orderId} not found`);
    if (!original.brokerOrderId) throw new Error(`Order ${orderId} has no broker ID`);

    // Futu supports native order modification
    const newPrice = update.price ?? original.price ?? 0;
    const newQty = update.quantity ?? original.quantity;

    await withRetry(
      () =>
        this.proc.call("modify_order", {
          order_id: original.brokerOrderId,
          price: newPrice,
          qty: newQty,
        }),
      "modifyOrder",
    );

    // Update local cache
    original.price = newPrice;
    original.quantity = newQty;
    original.updatedAt = new Date().toISOString();
    return { ...original };
  }

  async queryOrder(orderId: string): Promise<Order | null> {
    const cached = this.orderCache.get(orderId);
    if (!cached?.brokerOrderId) return cached ?? null;
    try {
      const raw = await this.proc.call<RawOrder>("query_order", {
        order_id: cached.brokerOrderId,
      });
      return this.mapRawOrder(raw, orderId);
    } catch {
      return cached;
    }
  }

  async queryOrders(filter?: OrderFilter): Promise<Order[]> {
    const params: Record<string, unknown> = {};
    if (filter?.symbol) {
      params.code = toFutuSymbol(filter.symbol);
    }
    const raw = await this.proc.call<RawOrder[]>("query_orders", params);
    return raw.map((r) => this.mapRawOrder(r));
  }

  async queryPositions(): Promise<LivePosition[]> {
    const raw = await this.proc.call<RawPosition[]>("query_positions");
    return raw.map((r) => this.mapRawPosition(r));
  }

  async queryAccount(): Promise<BrokerAccount> {
    const raw = await this.proc.call<RawAccount>("query_account");
    return {
      accountId: this.futuCfg.openDHost ?? "futu",
      brokerType: "futu",
      cash: raw.cash,
      frozenCash: raw.frozen_cash,
      totalAssets: raw.total_assets,
      marketValue: raw.market_val,
      profit: raw.unrealized_pl,
      profitPct: raw.total_assets > 0 ? raw.unrealized_pl / raw.total_assets : 0,
      todayProfit: raw.realized_pl,
      lastSyncAt: new Date().toISOString(),
    };
  }

  subscribeQuote(symbols: string[], cb: QuoteCallback): () => void {
    const futuCodes = symbols.map((s) => toFutuSymbol(s));
    void this.proc.call("subscribe_quote", { codes: futuCodes }).catch((err) => {
      console.warn(`[FutuBroker] subscribe_quote failed: ${(err as Error).message ?? err}`);
    });
    for (const sym of symbols) {
      if (!this.quoteSubs.has(sym)) this.quoteSubs.set(sym, []);
      this.quoteSubs.get(sym)!.push(cb);
    }
    return () => {
      for (const sym of symbols) {
        const arr = this.quoteSubs.get(sym);
        if (arr) {
          const filtered = arr.filter((fn) => fn !== cb);
          if (filtered.length === 0) {
            this.quoteSubs.delete(sym);
          } else {
            this.quoteSubs.set(sym, filtered);
          }
        }
      }
      void this.proc.call("unsubscribe_quote", { codes: futuCodes }).catch((err) => {
        console.warn(`[FutuBroker] unsubscribe_quote failed: ${(err as Error).message ?? err}`);
      });
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

  async ping(): Promise<boolean> {
    try {
      await this.proc.call("query_account");
      return true;
    } catch {
      return false;
    }
  }

  // ── Private ──────────────────────────────────────────────────

  private pruneOrderCache(): void {
    if (this.orderCache.size <= FutuBroker.ORDER_CACHE_MAX) return;
    const now = Date.now();
    const terminalStatuses = new Set(["filled", "cancelled", "rejected", "expired"]);
    for (const [id, o] of this.orderCache) {
      if (this.orderCache.size <= FutuBroker.ORDER_CACHE_MAX) break;
      if (
        terminalStatuses.has(o.status) &&
        now - new Date(o.updatedAt).getTime() > FutuBroker.ORDER_CACHE_TTL_MS
      ) {
        this.orderCache.delete(id);
      }
    }
  }

  private handleEvent(event: string, data: unknown): void {
    if (event === "fill") {
      const raw = data as RawFill;
      const localId = this.findLocalOrderId(raw.order_id);
      const cachedOrder = localId ? this.orderCache.get(localId) : undefined;
      const fill: Fill = {
        fillId: randomUUID(),
        orderId: localId ?? raw.order_id,
        symbol: fromFutuSymbol(raw.code),
        side: cachedOrder?.side ?? mapFutuSide(raw.trd_side),
        price: raw.dealt_price,
        quantity: raw.dealt_qty,
        commission: raw.commission,
        timestamp: raw.time,
      };
      for (const cb of this.fillCbs) cb(fill);
    } else if (event === "order_status") {
      const raw = data as RawOrder & { status: string };
      const localId = this.findLocalOrderId(raw.order_id) ?? raw.order_id;
      const status = raw.status as OrderStatus;
      const cached = this.orderCache.get(localId);
      if (cached) {
        cached.status = status;
        cached.filledQty = raw.dealt_qty;
        cached.avgFillPrice = raw.dealt_avg_price || undefined;
        cached.updatedAt = new Date().toISOString();
        if (status === "filled") {
          cached.filledAt = new Date().toISOString();
        }
      }
      const order = cached ?? this.mapRawOrder(raw, localId);
      for (const cb of this.statusCbs) cb(localId, status, order);
    } else if (event === "quote") {
      const raw = data as RawQuote;
      const sym = fromFutuSymbol(raw.code);
      const snapshot = this.mapRawQuote(raw, sym);
      const cbs = this.quoteSubs.get(sym);
      if (cbs) for (const cb of cbs) cb(snapshot);
    }
  }

  private findLocalOrderId(brokerOrderId: string): string | undefined {
    for (const [localId, order] of this.orderCache) {
      if (order.brokerOrderId === brokerOrderId) return localId;
    }
    return undefined;
  }

  private mapRawOrder(r: RawOrder, localId?: string): Order {
    const now = new Date().toISOString();
    const sym = fromFutuSymbol(r.code);
    const market = detectMarketFromFutuCode(r.code);
    return {
      orderId: localId ?? r.order_id,
      brokerOrderId: r.order_id,
      symbol: sym,
      market,
      side: mapFutuSide(r.trd_side),
      orderType: r.price > 0 ? "limit" : "market",
      price: r.price,
      quantity: r.qty,
      filledQty: r.dealt_qty,
      avgFillPrice: r.dealt_avg_price || undefined,
      status: r.status as OrderStatus,
      brokerType: "futu",
      createdAt: r.create_time ?? now,
      updatedAt: r.updated_time ?? now,
    };
  }

  private mapRawPosition(r: RawPosition): LivePosition {
    const sym = fromFutuSymbol(r.code);
    return {
      symbol: sym,
      name: r.stock_name,
      market: detectMarketFromFutuCode(r.code),
      quantity: r.qty,
      availableQty: r.can_sell_qty,
      frozenQty: r.qty - r.can_sell_qty,
      todayBuyQty: r.today_buy_qty,
      avgCost: r.cost_price,
      currentPrice: r.nominal_price > 0 ? r.nominal_price : undefined,
      marketValue: r.market_val > 0 ? r.market_val : undefined,
      unrealizedPnl: r.unrealized_pl,
      unrealizedPnlPct: r.unrealized_pl_pct,
      source: "broker",
      lastSyncAt: new Date().toISOString(),
    };
  }

  private mapRawQuote(r: RawQuote, sym: string): QuoteSnapshot {
    const price = r.last_price;
    const change = price - r.prev_close_price;
    return {
      symbol: { code: sym, name: sym, market: detectMarketFromFutuCode(r.code ?? `HK.${sym}`) },
      price,
      change,
      changePct: r.prev_close_price ? (change / r.prev_close_price) * 100 : 0,
      open: r.open_price,
      high: r.high_price,
      low: r.low_price,
      prevClose: r.prev_close_price,
      volume: r.volume,
      amount: r.turnover,
      timestamp: Date.now(),
    };
  }
}
