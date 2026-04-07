/**
 * FutuBroker — 富途 OpenD broker adapter.
 *
 * Communicates with `python/brokers/futu_broker.py` via PersistentPythonProcess
 * (NDJSON over stdin/stdout). The Python side uses futu-api (OpenQuoteContext /
 * OpenTradeContext) to interact with FutuOpenD gateway.
 *
 * Supported markets: HK stock, US stock, CN stock (via Futu).
 *
 * Setup:
 *   1. Download and run FutuOpenD from Futu (requires a Futu account).
 *   2. Set config.tradingAccount.futu.openDHost/openDPort (default 127.0.0.1:11111).
 *   3. pip install futu-api
 */
import { randomUUID } from "node:crypto";
import { PersistentPythonProcess } from "../bridge/persistent-python.js";
import { detectMarketFromSymbol } from "../data/market-data-provider.js";
import type {
  BrokerAccount,
  BrokerType,
  Fill,
  LivePosition,
  MarketType,
  Order,
  OrderStatus,
  QuoteSnapshot,
  QuantTradingConfig,
  FutuBrokerConfig,
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
import { isCnMarketOpen } from "./paper-broker.js";

// ── Raw shapes returned by futu_broker.py ─────────────────────────

type RawFutuOrder = {
  order_id: string;
  code: string;
  trd_side: string; // "BUY" | "SELL" | "SELL_SHORT" | "BUY_BACK"
  order_type: string; // "NORMAL" | "MARKET" | "ABSOLUTE_LIMIT" ...
  price: number;
  qty: number;
  dealt_qty: number;
  dealt_avg_price: number;
  order_status: string; // Futu order status string
  remark?: string;
  create_time?: string;
  updated_time?: string;
};

type RawFutuPosition = {
  code: string;
  stock_name: string;
  qty: number;
  can_sell_qty: number;
  today_buy_qty: number;
  cost_price: number;
  market_val: number;
  current_price?: number | null;
  unrealized_pl?: number | null;
  unrealized_pl_ratio?: number | null;
  nominal_price?: number | null;
};

type RawFutuAccount = {
  account_id: string;
  cash: number;
  frozen_cash: number;
  total_assets: number;
  market_val: number;
  unrealized_pl: number;
  realized_pl: number;
};

type RawFutuQuote = {
  code: string;
  last_price: number;
  open_price: number;
  high_price: number;
  low_price: number;
  prev_close_price: number;
  volume: number;
  turnover: number;
  bid_price: number;
  ask_price: number;
  timestamp: number; // unix ms
};

type RawFutuFill = {
  order_id: string;
  code: string;
  trd_side: string;
  price: number;
  qty: number;
  create_time: string;
  commission?: number;
};

// ── Futu order status mapping ──────────────────────────────────────

function mapFutuStatus(s: string): OrderStatus {
  switch (s) {
    case "NONE":
    case "UNSUBMITTED":
    case "WAITING_SUBMIT":
      return "pending";
    case "SUBMITTING":
    case "SUBMITTED":
      return "submitted";
    case "FILLED_PART":
      return "partial";
    case "FILLED_ALL":
      return "filled";
    case "CANCELLED_PART":
    case "CANCELLED_ALL":
      return "cancelled";
    case "FAILED":
    case "DISABLED":
    case "DELETED":
      return "rejected";
    case "TIMEOUT":
      return "expired";
    default:
      return "submitted";
  }
}

// ── Futu side mapping ──────────────────────────────────────────────

function mapSideToFutu(side: string): string {
  switch (side) {
    case "buy":
      return "BUY";
    case "sell":
      return "SELL";
    case "short":
      return "SELL_SHORT";
    case "cover":
      return "BUY_BACK";
    default:
      return "BUY";
  }
}

function mapFutuSide(s: string): "buy" | "sell" | "short" | "cover" {
  switch (s) {
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

// ── Symbol normalisation ──────────────────────────────────────────

/**
 * Futu uses "HK.00700", "US.AAPL", "SH.600519", "SZ.000001" format.
 * Convert from plain code or detect market prefix.
 */
function toFutuSymbol(symbol: string, market?: MarketType): string {
  if (symbol.includes(".") && /^[A-Z]{2}\./.test(symbol)) return symbol; // already Futu format

  // Strip any QMT-style suffix
  const code = symbol.replace(/\.(SH|SZ|BJ|HK|US)$/i, "");

  if (market === "hk_stock") return `HK.${code}`;
  if (market === "us_stock") return `US.${code}`;

  // Auto-detect CN markets
  const n = parseInt(code, 10);
  if (!isNaN(n)) {
    if (code.startsWith("6") || code.startsWith("5") || code.startsWith("9")) {
      return `SH.${code}`;
    }
    if (code.startsWith("0") || code.startsWith("3") || code.startsWith("1") || code.startsWith("2")) {
      return `SZ.${code}`;
    }
  }

  // Alphabetic codes → likely US
  if (/^[A-Za-z]+$/.test(code)) return `US.${code}`;

  return `SH.${code}`; // fallback
}

function fromFutuSymbol(s: string): string {
  return s.replace(/^(HK|US|SH|SZ)\./i, "");
}

function marketFromFutuPrefix(s: string): MarketType {
  if (s.startsWith("HK.")) return "hk_stock";
  if (s.startsWith("US.")) return "us_stock";
  return "cn_stock";
}

// ── FutuBroker ────────────────────────────────────────────────────

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
      trade_env: this.futuCfg.tradeEnv ?? "SIMULATE",
      pwd_unlock: this.futuCfg.pwdUnlock ?? "",
    });

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
    } catch {
      // Ignore disconnect errors — OpenD may already be gone
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

    const result = await this.proc.call<{ order_id: string }>("place_order", {
      code: toFutuSymbol(req.symbol, req.market),
      trd_side: mapSideToFutu(req.side),
      order_type: req.orderType === "market" ? "MARKET" : "NORMAL",
      price: req.orderType === "market" ? 0 : (req.price ?? 0),
      qty: req.quantity,
      remark: clientOrderId,
    });

    const order: Order = {
      orderId,
      clientOrderId,
      brokerOrderId: result.order_id,
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
    };
    this.orderCache.set(orderId, order);
    this.pruneOrderCache();
    return order;
  }

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

  async cancelOrder(orderId: string): Promise<void> {
    const order = this.orderCache.get(orderId);
    if (!order?.brokerOrderId) throw new Error(`Order ${orderId} not found or has no broker ID`);
    await this.proc.call("cancel_order", {
      order_id: order.brokerOrderId,
      code: toFutuSymbol(order.symbol, order.market),
    });
    order.status = "cancelled";
    order.updatedAt = new Date().toISOString();
    for (const cb of this.statusCbs) cb(orderId, "cancelled", order);
  }

  async modifyOrder(orderId: string, update: ModifyOrderRequest): Promise<Order> {
    const original = this.orderCache.get(orderId);
    if (!original) throw new Error(`Order ${orderId} not found`);

    // Try in-place modify via Futu API
    const result = await this.proc.call<{ order_id: string }>("modify_order", {
      order_id: original.brokerOrderId,
      price: update.price ?? original.price,
      qty: update.quantity ?? original.quantity,
    });

    original.price = update.price ?? original.price;
    original.quantity = update.quantity ?? original.quantity;
    original.updatedAt = new Date().toISOString();
    return original;
  }

  async queryOrder(orderId: string): Promise<Order | null> {
    const cached = this.orderCache.get(orderId);
    if (!cached?.brokerOrderId) return cached ?? null;
    try {
      const raw = await this.proc.call<RawFutuOrder>("query_order", {
        order_id: cached.brokerOrderId,
      });
      return this.mapRawOrder(raw, orderId);
    } catch {
      return cached;
    }
  }

  async queryOrders(filter?: OrderFilter): Promise<Order[]> {
    const raw = await this.proc.call<RawFutuOrder[]>("query_orders", {
      code: filter?.symbol ? toFutuSymbol(filter.symbol) : undefined,
      status_filter: filter?.status,
    });
    return raw.map((r) => this.mapRawOrder(r));
  }

  async queryPositions(): Promise<LivePosition[]> {
    const raw = await this.proc.call<RawFutuPosition[]>("query_positions");
    return raw.map((r) => this.mapRawPosition(r));
  }

  async queryAccount(): Promise<BrokerAccount> {
    const raw = await this.proc.call<RawFutuAccount>("query_account");
    const totalPnl = (raw.unrealized_pl ?? 0) + (raw.realized_pl ?? 0);
    return {
      accountId: raw.account_id,
      brokerType: "futu",
      cash: raw.cash,
      frozenCash: raw.frozen_cash,
      totalAssets: raw.total_assets,
      marketValue: raw.market_val,
      profit: totalPnl,
      profitPct: raw.total_assets > 0 ? totalPnl / raw.total_assets : 0,
      lastSyncAt: new Date().toISOString(),
    };
  }

  subscribeQuote(symbols: string[], cb: QuoteCallback): () => void {
    const futuSymbols = symbols.map((s) => toFutuSymbol(s));
    void this.proc.call("subscribe_quote", { codes: futuSymbols }).catch((err) => {
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
      void this.proc.call("unsubscribe_quote", { codes: futuSymbols }).catch((err) => {
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
    // Futu supports HK/US/CN markets — for CN, delegate to existing helper
    if (!market || market === "cn_stock") return isCnMarketOpen(market);
    // For HK/US, query Futu API
    try {
      const result = await this.proc.call<{ is_open: boolean }>("is_market_open", { market });
      return result.is_open;
    } catch {
      return false;
    }
  }

  async ping(): Promise<boolean> {
    try {
      await this.proc.call("ping");
      return true;
    } catch {
      return false;
    }
  }

  // ── Private ──────────────────────────────────────────────────

  private handleEvent(event: string, data: unknown): void {
    if (event === "fill") {
      const raw = data as RawFutuFill;
      const localId = this.findLocalOrderId(raw.order_id);
      const cachedOrder = localId ? this.orderCache.get(localId) : undefined;
      const fill: Fill = {
        fillId: randomUUID(),
        orderId: localId ?? raw.order_id,
        symbol: fromFutuSymbol(raw.code),
        side: cachedOrder?.side ?? mapFutuSide(raw.trd_side),
        price: raw.price,
        quantity: raw.qty,
        commission: raw.commission ?? 0,
        timestamp: raw.create_time,
      };
      for (const cb of this.fillCbs) cb(fill);

      // Update cached order
      if (localId && cachedOrder) {
        cachedOrder.filledQty += raw.qty;
        cachedOrder.avgFillPrice = raw.price;
        if (cachedOrder.filledQty >= cachedOrder.quantity) {
          cachedOrder.status = "filled";
          cachedOrder.filledAt = raw.create_time;
        } else {
          cachedOrder.status = "partial";
        }
        cachedOrder.updatedAt = new Date().toISOString();
        for (const cb of this.statusCbs) cb(localId, cachedOrder.status, cachedOrder);
      }
    } else if (event === "order_status") {
      const raw = data as RawFutuOrder;
      const localId = this.findLocalOrderId(raw.order_id) ?? raw.order_id;
      const order = this.mapRawOrder(raw, localId);
      const cached = this.orderCache.get(localId);
      if (cached) {
        Object.assign(cached, {
          status: order.status,
          filledQty: order.filledQty,
          avgFillPrice: order.avgFillPrice,
          updatedAt: new Date().toISOString(),
        });
      }
      for (const cb of this.statusCbs) cb(localId, order.status, order);
    } else if (event === "quote") {
      const raw = data as RawFutuQuote;
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

  private mapRawOrder(r: RawFutuOrder, localId?: string): Order {
    const now = new Date().toISOString();
    const sym = fromFutuSymbol(r.code);
    return {
      orderId: localId ?? r.order_id,
      brokerOrderId: r.order_id,
      symbol: sym,
      market: marketFromFutuPrefix(r.code),
      side: mapFutuSide(r.trd_side),
      orderType: r.order_type === "MARKET" ? "market" : "limit",
      price: r.price,
      quantity: r.qty,
      filledQty: r.dealt_qty,
      avgFillPrice: r.dealt_avg_price || undefined,
      status: mapFutuStatus(r.order_status),
      brokerType: "futu",
      createdAt: r.create_time ?? now,
      updatedAt: r.updated_time ?? now,
    };
  }

  private mapRawPosition(r: RawFutuPosition): LivePosition {
    const futuCode = r.code;
    const sym = fromFutuSymbol(futuCode);
    const currentPrice = r.current_price ?? (r.qty > 0 && r.market_val > 0 ? r.market_val / r.qty : undefined);

    return {
      symbol: sym,
      name: r.stock_name,
      market: marketFromFutuPrefix(futuCode),
      quantity: r.qty,
      availableQty: r.can_sell_qty,
      frozenQty: r.qty - r.can_sell_qty,
      todayBuyQty: r.today_buy_qty,
      avgCost: r.cost_price,
      currentPrice: currentPrice ?? undefined,
      marketValue: r.market_val > 0 ? r.market_val : undefined,
      unrealizedPnl: r.unrealized_pl ?? undefined,
      unrealizedPnlPct: r.unrealized_pl_ratio ?? undefined,
      source: "broker",
      lastSyncAt: new Date().toISOString(),
    };
  }

  private mapRawQuote(r: RawFutuQuote, sym: string): QuoteSnapshot {
    const price = r.last_price;
    const change = price - r.prev_close_price;
    return {
      symbol: { code: sym, name: sym, market: marketFromFutuPrefix(r.code) },
      price,
      change,
      changePct: r.prev_close_price ? (change / r.prev_close_price) * 100 : 0,
      open: r.open_price,
      high: r.high_price,
      low: r.low_price,
      prevClose: r.prev_close_price,
      volume: r.volume,
      amount: r.turnover,
      timestamp: r.timestamp,
      bid1: r.bid_price,
      ask1: r.ask_price,
    };
  }
}
