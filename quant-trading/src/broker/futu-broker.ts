/**
 * FutuBroker — 富途 OpenD broker adapter.
 *
 * Communicates with `python/brokers/futu_broker.py` via PersistentPythonProcess
 * (NDJSON over stdin/stdout). The Python side uses futu-api to interact with
 * FutuOpenD for placing orders and subscribing to real-time quotes.
 *
 * Supported markets: HK stock, US stock, CN stock (via Futu), A-share (SZ/SH connect).
 *
 * Setup:
 *   1. Install FutuOpenD from https://www.futunn.com/download/openAPI
 *   2. Keep FutuOpenD running.
 *   3. Set config.tradingAccount.futu.openDHost / openDPort.
 *   4. pip install futu-api
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

// ── Raw shapes returned by futu_broker.py ───────────────────────

type RawOrder = {
  order_id: string;
  stock_code: string;
  order_type: string; // trd_side string
  price: number;
  order_volume: number;
  traded_volume: number;
  traded_price: number;
  order_status: string; // Futu status string
  order_time?: string;
  remark?: string;
};

type RawPosition = {
  stock_code: string;
  stock_name: string;
  volume: number;
  can_use_volume: number;
  frozen_volume: number;
  today_buy_volume: number;
  avg_price: number;
  market_value: number;
  last_price?: number | null;
  unrealized_pnl?: number | null;
  unrealized_pnl_pct?: number | null;
};

type RawAccount = {
  account_id: string;
  cash: number;
  frozen_cash: number;
  total_asset: number;
  market_value: number;
  profit: number;
  profit_ratio: number;
};

type RawQuote = {
  stock_code: string;
  last_price: number;
  open: number;
  high: number;
  low: number;
  last_close: number;
  volume: number;
  amount: number;
  bid_price: number[];
  ask_price: number[];
  bid_vol: number[];
  ask_vol: number[];
  time: number;
};

type RawFill = {
  order_id: string;
  stock_code: string;
  traded_price: number;
  traded_volume: number;
  traded_amount: number;
  commission: number;
  time: string;
};

type RawPositionChange = {
  stock_code: string;
  previous: RawPosition | null;
  current: RawPosition | null;
};

// ── Futu order status mapping ───────────────────────────────────

function mapFutuStatus(s: string): OrderStatus {
  const mapping: Record<string, OrderStatus> = {
    NONE: "pending",
    UNSUBMITTED: "pending",
    WAITING_SUBMIT: "pending",
    SUBMITTING: "submitted",
    SUBMIT_FAILED: "rejected",
    SUBMITTED: "submitted",
    FILLED_PART: "partial",
    FILLED_ALL: "filled",
    CANCELLING_PART: "submitted",
    CANCELLING_ALL: "submitted",
    CANCELLED_PART: "cancelled",
    CANCELLED_ALL: "cancelled",
    FAILED: "rejected",
    DISABLED: "rejected",
    DELETED: "cancelled",
  };
  return mapping[s] ?? "submitted";
}

// ── Futu side mapping ───────────────────────────────────────────

function mapFutuSide(s: string): "buy" | "sell" {
  if (s === "SELL" || s === "SELL_SHORT") return "sell";
  return "buy";
}

// ── Symbol normalisation ────────────────────────────────────────

/**
 * Futu uses format: HK.00700, US.AAPL, SH.600519, SZ.000001
 * Convert from plain code to Futu format based on market.
 */
function toFutuSymbol(symbol: string, market?: MarketType): string {
  if (symbol.includes(".") && /^[A-Z]{2}\./.test(symbol)) return symbol; // already Futu format
  const code = symbol.replace(/[^0-9A-Za-z]/g, "");

  // If it has a QMT-style suffix, convert
  if (symbol.endsWith(".SH")) return `SH.${code.replace(/\.SH$/i, "")}`;
  if (symbol.endsWith(".SZ")) return `SZ.${code.replace(/\.SZ$/i, "")}`;

  // Detect by market or code pattern
  if (market === "hk_stock") return `HK.${code}`;
  if (market === "us_stock") return `US.${code}`;

  // Auto-detect for A-share by code prefix
  const n = parseInt(code, 10);
  if (!isNaN(n)) {
    if (n >= 500_000) return `SH.${code}`;
    return `SZ.${code}`;
  }

  // If all alphabetic, assume US stock
  if (/^[A-Za-z]+$/.test(code)) return `US.${code}`;

  return symbol;
}

function fromFutuSymbol(s: string): string {
  return s.replace(/^(HK|US|SH|SZ|BJ)\./i, "");
}

function detectMarketFromFutuSymbol(s: string): MarketType {
  if (s.startsWith("HK.")) return "hk_stock";
  if (s.startsWith("US.")) return "us_stock";
  return "cn_stock";
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
  private positionChangeCbs: Array<(code: string, pos: LivePosition | null) => void> = [];

  // local cache of orders (keyed by local orderId)
  private orderCache: Map<string, Order> = new Map();
  private static readonly ORDER_CACHE_MAX = 500;
  private static readonly ORDER_CACHE_TTL_MS = 4 * 3600 * 1000;

  constructor(cfg: QuantTradingConfig) {
    this.cfg = cfg;
    this.futuCfg = cfg.tradingAccount?.futu ?? {};

    this.proc = new PersistentPythonProcess("brokers.futu_broker", cfg, {
      opend_host: this.futuCfg.openDHost ?? "127.0.0.1",
      opend_port: this.futuCfg.openDPort ?? 11111,
      trade_env: this.futuCfg.tradeEnv ?? "REAL",
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
      stock_code: toFutuSymbol(req.symbol, req.market),
      side: req.side,
      order_type: req.orderType,
      price: req.orderType === "market" ? 0 : (req.price ?? 0),
      volume: req.quantity,
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
    await this.proc.call("cancel_order", { order_id: order.brokerOrderId });
    order.status = "cancelled";
    order.updatedAt = new Date().toISOString();
    for (const cb of this.statusCbs) cb(orderId, "cancelled", order);
  }

  async modifyOrder(orderId: string, update: ModifyOrderRequest): Promise<Order> {
    // Futu doesn't support in-place modification for all cases: cancel + resubmit
    const original = this.orderCache.get(orderId);
    if (!original) throw new Error(`Order ${orderId} not found`);
    await this.cancelOrder(orderId);
    return this.placeOrder({
      symbol: original.symbol,
      market: original.market,
      side: original.side,
      orderType: original.orderType,
      quantity: update.quantity ?? original.quantity,
      price: update.price ?? original.price,
      strategyId: original.strategyId,
      signalId: original.signalId,
    });
  }

  async queryOrder(orderId: string): Promise<Order | null> {
    const cached = this.orderCache.get(orderId);
    if (!cached?.brokerOrderId) return cached ?? null;
    try {
      const raw = await this.proc.call<RawOrder>("query_order", { order_id: cached.brokerOrderId });
      return this.mapRawOrder(raw, orderId);
    } catch {
      return cached;
    }
  }

  async queryOrders(filter?: OrderFilter): Promise<Order[]> {
    const raw = await this.proc.call<RawOrder[]>("query_orders", {
      stock_code: filter?.symbol ? toFutuSymbol(filter.symbol) : undefined,
    });
    return raw.map((r) => this.mapRawOrder(r));
  }

  async queryPositions(): Promise<LivePosition[]> {
    const raw = await this.proc.call<RawPosition[]>("query_positions");
    return raw.map((r) => this.mapRawPosition(r));
  }

  async queryAccount(): Promise<BrokerAccount> {
    const raw = await this.proc.call<RawAccount>("query_account");
    return {
      accountId: raw.account_id,
      brokerType: "futu",
      cash: raw.cash,
      frozenCash: raw.frozen_cash,
      totalAssets: raw.total_asset,
      marketValue: raw.market_value,
      profit: raw.profit,
      profitPct: raw.profit_ratio,
      lastSyncAt: new Date().toISOString(),
    };
  }

  subscribeQuote(symbols: string[], cb: QuoteCallback): () => void {
    const futuSymbols = symbols.map((s) => toFutuSymbol(s));
    void this.proc.call("subscribe_quote", { stock_codes: futuSymbols }).catch((err) => {
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
      void this.proc.call("unsubscribe_quote", { stock_codes: futuSymbols }).catch((err) => {
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

  /**
   * Subscribe to position change notifications.
   * Returns an unsubscribe function.
   */
  onPositionChange(cb: (code: string, pos: LivePosition | null) => void): () => void {
    this.positionChangeCbs.push(cb);
    return () => {
      this.positionChangeCbs = this.positionChangeCbs.filter((fn) => fn !== cb);
    };
  }

  async isMarketOpen(market?: MarketType): Promise<boolean> {
    // HK market hours: 9:30-12:00, 13:00-16:00 HKT (same as CST)
    // US market hours: 9:30-16:00 ET → 21:30-04:00 CST
    // For simplicity, use CN market check for CN stocks, always true for others
    if (market === "hk_stock" || market === "us_stock") {
      return true; // simplified; real implementation would check specific market hours
    }
    return isCnMarketOpen(market);
  }

  // ── Private ──────────────────────────────────────────────────

  private handleEvent(event: string, data: unknown): void {
    if (event === "fill") {
      const raw = data as RawFill;
      const localId = this.findLocalOrderId(raw.order_id);
      const cachedOrder = localId ? this.orderCache.get(localId) : undefined;
      const fill: Fill = {
        fillId: randomUUID(),
        orderId: localId ?? raw.order_id,
        symbol: fromFutuSymbol(raw.stock_code),
        side: cachedOrder?.side ?? "buy",
        price: raw.traded_price,
        quantity: raw.traded_volume,
        commission: raw.commission,
        timestamp: raw.time,
      };
      for (const cb of this.fillCbs) cb(fill);
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
      const sym = fromFutuSymbol(raw.stock_code);
      const snapshot = this.mapRawQuote(raw, sym);
      const cbs = this.quoteSubs.get(sym);
      if (cbs) for (const cb of cbs) cb(snapshot);
    } else if (event === "position_change") {
      const raw = data as RawPositionChange;
      const sym = fromFutuSymbol(raw.stock_code);
      const pos = raw.current ? this.mapRawPosition(raw.current) : null;
      for (const cb of this.positionChangeCbs) cb(sym, pos);
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
    const sym = fromFutuSymbol(r.stock_code);
    return {
      orderId: localId ?? r.order_id,
      brokerOrderId: r.order_id,
      symbol: sym,
      market: detectMarketFromFutuSymbol(r.stock_code),
      side: mapFutuSide(r.order_type),
      orderType: r.price > 0 ? "limit" : "market",
      price: r.price,
      quantity: r.order_volume,
      filledQty: r.traded_volume,
      avgFillPrice: r.traded_price || undefined,
      status: mapFutuStatus(r.order_status),
      brokerType: "futu",
      createdAt: r.order_time ?? now,
      updatedAt: now,
    };
  }

  private mapRawPosition(r: RawPosition): LivePosition {
    let currentPrice: number | undefined;
    if (r.last_price != null && r.last_price > 0) {
      currentPrice = r.last_price;
    } else if (r.volume > 0 && r.market_value > 0) {
      currentPrice = r.market_value / r.volume;
    }

    let unrealizedPnl: number | undefined;
    let unrealizedPnlPct: number | undefined;
    if (r.unrealized_pnl != null) {
      unrealizedPnl = r.unrealized_pnl;
      unrealizedPnlPct = r.unrealized_pnl_pct ?? undefined;
    } else if (currentPrice != null && currentPrice > 0 && r.avg_price > 0) {
      unrealizedPnl = (currentPrice - r.avg_price) * r.volume;
      unrealizedPnlPct = (currentPrice - r.avg_price) / r.avg_price;
    }

    const symbol = fromFutuSymbol(r.stock_code);
    return {
      symbol,
      name: r.stock_name,
      market: detectMarketFromSymbol(symbol),
      quantity: r.volume,
      availableQty: r.can_use_volume,
      frozenQty: r.frozen_volume,
      todayBuyQty: r.today_buy_volume,
      avgCost: r.avg_price,
      currentPrice,
      marketValue: r.market_value > 0 ? r.market_value : undefined,
      unrealizedPnl,
      unrealizedPnlPct,
      source: "broker",
      lastSyncAt: new Date().toISOString(),
    };
  }

  private mapRawQuote(r: RawQuote, sym: string): QuoteSnapshot {
    const price = r.last_price;
    const change = price - r.last_close;
    const futuCode = r.stock_code;
    return {
      symbol: {
        code: sym,
        name: sym,
        market: detectMarketFromFutuSymbol(futuCode),
      },
      price,
      change,
      changePct: r.last_close ? (change / r.last_close) * 100 : 0,
      open: r.open,
      high: r.high,
      low: r.low,
      prevClose: r.last_close,
      volume: r.volume,
      amount: r.amount,
      timestamp: r.time,
      bid1: r.bid_price?.[0],
      ask1: r.ask_price?.[0],
    };
  }
}
