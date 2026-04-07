/**
 * FutuBroker — 富途 OpenD broker adapter.
 *
 * Communicates with `python/brokers/futu_broker.py` via PersistentPythonProcess
 * (NDJSON over stdin/stdout). The Python side uses futu-api (OpenD) for
 * real-time quotes, order placement, and push subscriptions.
 *
 * Supported markets: HK stock, US stock, CN stock (via SH/SZ connect),
 * plus real-time quote push and order/fill/position-change notifications.
 *
 * Setup:
 *   1. Install FutuOpenD from https://openapi.futunn.com
 *   2. Keep OpenD running (default port 11111).
 *   3. pip install futu-api
 *   4. Set config.tradingAccount.futu with host/port/tradeEnv/pwdUnlock.
 */
import { randomUUID } from "node:crypto";
import { PersistentPythonProcess } from "../bridge/persistent-python.js";
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

// ── Raw shapes returned by futu_broker.py ───────────────────────

type RawOrder = {
  order_id: string;
  code: string;
  trd_side: string; // "BUY" | "SELL" | "SELL_SHORT" | "BUY_BACK"
  order_type: string; // "NORMAL" | "MARKET" | ...
  price: number;
  qty: number;
  dealt_qty: number;
  dealt_avg_price: number;
  order_status: string; // Futu TrdEnv status strings
  create_time: string;
  updated_time: string;
  remark?: string;
};

type RawPosition = {
  code: string;
  stock_name: string;
  qty: number;
  can_sell_qty: number;
  today_buy_qty: number;
  cost_price: number;
  market_val: number;
  current_price: number;
  unrealized_pl: number;
  pl_ratio: number;
  position_side?: string; // "LONG" | "SHORT"
};

type RawAccount = {
  acc_id: string;
  cash: number;
  frozen_cash: number;
  total_assets: number;
  market_val: number;
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
  timestamp: number;
  bid_price: number;
  ask_price: number;
};

type RawFill = {
  order_id: string;
  code: string;
  trd_side: string;
  price: number;
  qty: number;
  create_time: string;
  counter_broker_id?: number;
  counter_broker_name?: string;
};

// ── Futu order status mapping ────────────────────────────────────

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

// ── Futu side mapping ────────────────────────────────────────────

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
    case "SELL":
      return "sell";
    case "SELL_SHORT":
      return "short";
    case "BUY_BACK":
      return "cover";
    case "BUY":
    default:
      return "buy";
  }
}

// ── Futu order type mapping ──────────────────────────────────────

function mapOrderTypeToFutu(ot: string): string {
  switch (ot) {
    case "market":
      return "MARKET";
    case "limit":
      return "NORMAL";
    case "stop":
      return "STOP";
    case "stop_limit":
      return "STOP_LIMIT";
    default:
      return "NORMAL";
  }
}

function mapFutuOrderType(s: string): "market" | "limit" | "stop" | "stop_limit" {
  switch (s) {
    case "MARKET":
      return "market";
    case "STOP":
      return "stop";
    case "STOP_LIMIT":
      return "stop_limit";
    case "NORMAL":
    case "ABSOLUTE_LIMIT":
    case "AUCTION":
    case "AUCTION_LIMIT":
    case "ENHANCED_LIMIT":
    case "AT_AUCTION":
    case "AT_AUCTION_LIMIT":
    default:
      return "limit";
  }
}

// ── Symbol / market helpers ──────────────────────────────────────

/**
 * Futu uses "HK.00700", "US.AAPL", "SH.600519", "SZ.000001" format.
 * Detect market from Futu code prefix.
 */
function detectMarketFromFutuCode(code: string): MarketType {
  const prefix = code.split(".")[0]?.toUpperCase();
  switch (prefix) {
    case "HK":
      return "hk_stock";
    case "US":
      return "us_stock";
    case "SH":
    case "SZ":
      return "cn_stock";
    default:
      return "hk_stock";
  }
}

/**
 * Convert plain symbol + market to Futu code format.
 * e.g. ("00700", "hk_stock") → "HK.00700"
 *      ("AAPL", "us_stock") → "US.AAPL"
 *      ("600519", "cn_stock") → "SH.600519"
 */
function toFutuCode(symbol: string, market: MarketType): string {
  if (symbol.includes(".")) return symbol; // already prefixed
  switch (market) {
    case "hk_stock":
      return `HK.${symbol}`;
    case "us_stock":
      return `US.${symbol}`;
    case "cn_stock": {
      const n = parseInt(symbol, 10);
      if (!isNaN(n) && n >= 500_000) return `SH.${symbol}`;
      return `SZ.${symbol}`;
    }
    default:
      return `HK.${symbol}`;
  }
}

/**
 * Strip Futu prefix to get plain symbol code.
 * "HK.00700" → "00700", "US.AAPL" → "AAPL"
 */
function fromFutuCode(code: string): string {
  const dot = code.indexOf(".");
  return dot >= 0 ? code.slice(dot + 1) : code;
}

// ── Market hours ─────────────────────────────────────────────────

function isHkMarketOpen(): boolean {
  const now = new Date();
  // HK market hours: 09:30-12:00, 13:00-16:00 HKT (UTC+8)
  const hkt = new Date(now.getTime() + 8 * 60 * 60 * 1000);
  const day = hkt.getUTCDay();
  if (day === 0 || day === 6) return false;
  const h = hkt.getUTCHours();
  const m = hkt.getUTCMinutes();
  const hm = h * 100 + m;
  return (hm >= 930 && hm <= 1200) || (hm >= 1300 && hm <= 1600);
}

function isUsMarketOpen(): boolean {
  const now = new Date();
  // US ET = UTC-5 (EST) or UTC-4 (EDT). Simplified: use UTC-5 as baseline.
  const et = new Date(now.getTime() - 5 * 60 * 60 * 1000);
  const day = et.getUTCDay();
  if (day === 0 || day === 6) return false;
  const h = et.getUTCHours();
  const m = et.getUTCMinutes();
  const hm = h * 100 + m;
  return hm >= 930 && hm <= 1600;
}

function isCnMarketOpen(): boolean {
  const now = new Date();
  const cst = new Date(now.getTime() + 8 * 60 * 60 * 1000);
  const day = cst.getUTCDay();
  if (day === 0 || day === 6) return false;
  const h = cst.getUTCHours();
  const m = cst.getUTCMinutes();
  const hm = h * 100 + m;
  return (hm >= 930 && hm <= 1130) || (hm >= 1300 && hm <= 1500);
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

  // local cache of orders (keyed by local orderId)
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

    const result = await this.proc.call<{ broker_order_id: string }>("place_order", {
      code: toFutuCode(req.symbol, req.market),
      trd_side: mapSideToFutu(req.side),
      order_type: mapOrderTypeToFutu(req.orderType),
      price: req.price ?? 0,
      qty: req.quantity,
      remark: clientOrderId,
    });

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
    });
    order.status = "cancelled";
    order.updatedAt = new Date().toISOString();
    for (const cb of this.statusCbs) cb(orderId, "cancelled", order);
  }

  async modifyOrder(orderId: string, update: ModifyOrderRequest): Promise<Order> {
    const original = this.orderCache.get(orderId);
    if (!original) throw new Error(`Order ${orderId} not found`);

    // Futu supports in-place modify for pending orders
    const result = await this.proc.call<{ broker_order_id: string }>("modify_order", {
      order_id: original.brokerOrderId,
      price: update.price ?? original.price ?? 0,
      qty: update.quantity ?? original.quantity,
    });

    original.price = update.price ?? original.price;
    original.quantity = update.quantity ?? original.quantity;
    original.updatedAt = new Date().toISOString();
    if (result.broker_order_id) {
      original.brokerOrderId = result.broker_order_id;
    }
    return original;
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
    const raw = await this.proc.call<RawOrder[]>("query_orders", {
      code: filter?.symbol ? toFutuCode(filter.symbol, "hk_stock") : undefined,
      status_filter: filter?.status,
    });
    return raw.map((r) => this.mapRawOrder(r));
  }

  async queryPositions(): Promise<LivePosition[]> {
    const raw = await this.proc.call<RawPosition[]>("query_positions");
    return raw.map((r) => this.mapRawPosition(r));
  }

  async queryAccount(): Promise<BrokerAccount> {
    const raw = await this.proc.call<RawAccount>("query_account");
    const totalPl = (raw.unrealized_pl ?? 0) + (raw.realized_pl ?? 0);
    return {
      accountId: String(raw.acc_id),
      brokerType: "futu",
      cash: raw.cash,
      frozenCash: raw.frozen_cash,
      totalAssets: raw.total_assets,
      marketValue: raw.market_val,
      profit: totalPl,
      profitPct: raw.total_assets > 0 ? totalPl / raw.total_assets : 0,
      lastSyncAt: new Date().toISOString(),
    };
  }

  subscribeQuote(symbols: string[], cb: QuoteCallback): () => void {
    // Determine Futu codes; default to HK if no market hint available
    const futuCodes = symbols.map((sym) => {
      if (sym.includes(".")) return sym;
      // Guess market from symbol pattern
      if (/^[A-Za-z]+$/.test(sym)) return `US.${sym}`;
      const n = parseInt(sym, 10);
      if (!isNaN(n)) {
        if (n >= 500_000) return `SH.${sym}`;
        if (n >= 100_000) return `SZ.${sym}`;
        return `HK.${sym}`;
      }
      return `HK.${sym}`;
    });

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
          if (filtered.length === 0) this.quoteSubs.delete(sym);
          else this.quoteSubs.set(sym, filtered);
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
    switch (market) {
      case "hk_stock":
        return isHkMarketOpen();
      case "us_stock":
        return isUsMarketOpen();
      case "cn_stock":
        return isCnMarketOpen();
      default:
        // Default to HK for Futu
        return isHkMarketOpen();
    }
  }

  // ── Private: event dispatch ────────────────────────────────────

  private handleEvent(event: string, data: unknown): void {
    if (event === "fill") {
      const raw = data as RawFill;
      const localId = this.findLocalOrderId(raw.order_id);
      const cachedOrder = localId ? this.orderCache.get(localId) : undefined;
      const fill: Fill = {
        fillId: randomUUID(),
        orderId: localId ?? raw.order_id,
        symbol: fromFutuCode(raw.code),
        side: cachedOrder?.side ?? mapFutuSide(raw.trd_side),
        price: raw.price,
        quantity: raw.qty,
        commission: 0, // Futu doesn't push commission in fill callback; resolved on settlement
        timestamp: raw.create_time,
      };
      for (const cb of this.fillCbs) cb(fill);

      // Update cached order filled qty
      if (cachedOrder) {
        cachedOrder.filledQty += raw.qty;
        if (cachedOrder.filledQty >= cachedOrder.quantity) {
          cachedOrder.status = "filled";
          cachedOrder.filledAt = new Date().toISOString();
        } else {
          cachedOrder.status = "partial";
        }
        cachedOrder.avgFillPrice = raw.price;
        cachedOrder.updatedAt = new Date().toISOString();
      }
    } else if (event === "order_status") {
      const raw = data as RawOrder;
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
      const raw = data as RawQuote;
      const sym = fromFutuCode(raw.code);
      const snapshot = this.mapRawQuote(raw, sym);
      const cbs = this.quoteSubs.get(sym);
      if (cbs) for (const cb of cbs) cb(snapshot);
    } else if (event === "position_change") {
      // Position change events: re-dispatch as order status updates
      // so higher-level listeners (OrderManager, LiveEngine) stay in sync.
      // The Python side emits these when OpenD pushes position updates.
      console.log(`[FutuBroker] Position change event for: ${JSON.stringify(data)}`);
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
    const sym = fromFutuCode(r.code);
    const market = detectMarketFromFutuCode(r.code);
    return {
      orderId: localId ?? r.order_id,
      brokerOrderId: r.order_id,
      symbol: sym,
      market,
      side: mapFutuSide(r.trd_side),
      orderType: mapFutuOrderType(r.order_type),
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

  private mapRawPosition(r: RawPosition): LivePosition {
    const code = r.code;
    const sym = fromFutuCode(code);
    const market = detectMarketFromFutuCode(code);
    const unrealizedPnlPct = r.pl_ratio;
    return {
      symbol: sym,
      name: r.stock_name,
      market,
      quantity: r.qty,
      availableQty: r.can_sell_qty,
      frozenQty: r.qty - r.can_sell_qty,
      todayBuyQty: r.today_buy_qty,
      avgCost: r.cost_price,
      currentPrice: r.current_price,
      marketValue: r.market_val,
      unrealizedPnl: r.unrealized_pl,
      unrealizedPnlPct,
      source: "broker",
      lastSyncAt: new Date().toISOString(),
    };
  }

  private mapRawQuote(r: RawQuote, sym: string): QuoteSnapshot {
    const price = r.last_price;
    const change = price - r.prev_close_price;
    const market = detectMarketFromFutuCode(r.code);
    return {
      symbol: { code: sym, name: sym, market },
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
