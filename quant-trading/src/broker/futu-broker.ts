/**
 * FutuBroker — 富途 OpenD broker adapter.
 *
 * Communicates with `python/brokers/futu_broker.py` via PersistentPythonProcess
 * (NDJSON over stdin/stdout). The Python side uses futu-api to connect to
 * OpenD for trading and market data.
 *
 * Supported markets: HK stock, US stock, CN stock (via Futu).
 *
 * Setup:
 *   1. Install and run Futu OpenD (https://openapi.futunn.com/)
 *   2. pip install futu-api
 *   3. Set config.tradingAccount.futu with openDHost/openDPort.
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

// ── Raw shapes returned by futu_broker.py ────────────────────────

type RawOrder = {
  order_id: string;
  stock_code: string;
  order_type: string; // trd_side string from Futu
  price: number;
  order_volume: number;
  traded_volume: number;
  traded_price: number;
  order_status: string;
  order_status_mapped: string;
  order_time?: string;
  updated_time?: string;
  remark?: string;
};

type RawPosition = {
  stock_code: string;
  plain_code: string;
  market_prefix: string;
  stock_name: string;
  volume: number;
  can_use_volume: number;
  frozen_volume: number;
  today_buy_volume: number;
  today_sell_volume: number;
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
  available_funds: number;
};

type RawFutuAccountInfo = {
  acc_id: number;
  acc_type: string;
  trd_env: string;
  card_num: string;
  sim_acc_type: string;
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

// ── Futu symbol helpers ──────────────────────────────────────────

/**
 * Futu uses market-prefix notation: HK.00700, US.AAPL, SH.600519, SZ.000001.
 * Convert plain code + market to Futu format.
 */
function toFutuSymbol(symbol: string, market?: MarketType): string {
  if (symbol.includes(".") && /^(HK|US|SH|SZ|BJ)\./i.test(symbol)) {
    return symbol; // already in Futu format
  }

  // Strip any QMT-style suffix first
  const code = symbol.replace(/\.(SH|SZ|BJ|SS)$/i, "").trim();

  const m = market ?? detectMarketFromSymbol(code);
  switch (m) {
    case "hk_stock":
      return `HK.${code}`;
    case "us_stock":
      return `US.${code}`;
    case "cn_stock":
    case "fund": {
      // Shanghai vs Shenzhen detection
      const n = parseInt(code, 10);
      if (!isNaN(n)) {
        if (code.startsWith("8") || code.startsWith("43")) return `BJ.${code}`;
        if (n >= 500_000) return `SH.${code}`;
        return `SZ.${code}`;
      }
      return `SH.${code}`;
    }
    default:
      return symbol;
  }
}

/**
 * Convert Futu symbol back to plain code.
 * HK.00700 → 00700, US.AAPL → AAPL, SH.600519 → 600519
 */
function fromFutuSymbol(s: string): string {
  return s.replace(/^(HK|US|SH|SZ|BJ)\./i, "");
}

/**
 * Detect MarketType from Futu market prefix.
 */
function detectMarketFromFutuPrefix(prefix: string): MarketType {
  switch (prefix.toUpperCase()) {
    case "HK":
      return "hk_stock";
    case "US":
      return "us_stock";
    case "SH":
    case "SZ":
    case "BJ":
      return "cn_stock";
    default:
      return "cn_stock";
  }
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

  // cached account list
  private accountList: RawFutuAccountInfo[] = [];

  constructor(cfg: QuantTradingConfig) {
    this.cfg = cfg;
    this.futuCfg = cfg.tradingAccount?.futu ?? {};

    this.proc = new PersistentPythonProcess("brokers.futu_broker", cfg, {
      opend_host: this.futuCfg.openDHost ?? "127.0.0.1",
      opend_port: this.futuCfg.openDPort ?? 11111,
      trade_env: this.futuCfg.tradeEnv ?? "REAL",
      pwd_unlock: this.futuCfg.pwdUnlock ?? "",
      market_quote_type: this.futuCfg.marketQuoteType ?? 1,
    });

    // Register event handler for push notifications from Python
    this.proc.onEvent((event, data) => this.handleEvent(event, data));
  }

  async connect(): Promise<void> {
    if (this.connected) return;
    await this.proc.start();
    const result = await this.proc.call<{
      status: string;
      accounts: RawFutuAccountInfo[];
    }>("connect");
    this.accountList = result.accounts ?? [];
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

    const futuPrice = req.orderType === "market" ? 0 : (req.price ?? 0);
    if (req.orderType === "limit" && (!req.price || req.price <= 0)) {
      throw new Error("限价单必须指定正数 price");
    }

    const result = await this.proc.call<{ broker_order_id: string }>("place_order", {
      stock_code: toFutuSymbol(req.symbol, req.market),
      side: req.side,
      order_type: req.orderType,
      price: futuPrice,
      volume: req.quantity,
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
    // Futu supports order modification, but cancel+resubmit is simpler and universal
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

  /** Get the list of trading accounts available in OpenD. */
  async getAccountList(): Promise<RawFutuAccountInfo[]> {
    if (this.accountList.length === 0) {
      this.accountList = await this.proc.call<RawFutuAccountInfo[]>("get_account_list");
    }
    return this.accountList;
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

  async isMarketOpen(market?: MarketType): Promise<boolean> {
    return isMarketOpen(market);
  }

  async ping(): Promise<boolean> {
    try {
      const result = await this.proc.call<{ ok: boolean }>("ping");
      return result.ok;
    } catch {
      return false;
    }
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
    }
    // reconnecting / reconnected / connection_error events are handled
    // internally by Python; we just log them here
    else if (event === "reconnecting" || event === "reconnected" || event === "connection_error") {
      console.warn(`[FutuBroker] ${event}: ${JSON.stringify(data)}`);
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
    const futuPrefix = r.stock_code.includes(".")
      ? r.stock_code.split(".")[0]
      : "";
    const market = futuPrefix
      ? detectMarketFromFutuPrefix(futuPrefix)
      : detectMarketFromSymbol(sym);

    // Map Futu side string to our side
    const sideStr = r.order_type; // This is actually trd_side from Futu
    let side: "buy" | "sell" | "short" | "cover" = "buy";
    if (sideStr.includes("SELL_SHORT") || sideStr.includes("SHORT")) {
      side = "short";
    } else if (sideStr.includes("BUY_BACK") || sideStr.includes("COVER")) {
      side = "cover";
    } else if (sideStr.includes("SELL")) {
      side = "sell";
    }

    return {
      orderId: localId ?? r.order_id,
      brokerOrderId: r.order_id,
      symbol: sym,
      market,
      side,
      orderType: r.price > 0 ? "limit" : "market",
      price: r.price,
      quantity: r.order_volume,
      filledQty: r.traded_volume,
      avgFillPrice: r.traded_price || undefined,
      status: (r.order_status_mapped ?? "submitted") as OrderStatus,
      brokerType: "futu",
      createdAt: r.order_time ?? now,
      updatedAt: now,
    };
  }

  private mapRawPosition(r: RawPosition): LivePosition {
    const symbol = r.plain_code || fromFutuSymbol(r.stock_code);
    const market = r.market_prefix
      ? detectMarketFromFutuPrefix(r.market_prefix)
      : detectMarketFromSymbol(symbol);

    let currentPrice: number | undefined;
    if (r.last_price != null && r.last_price > 0) {
      currentPrice = r.last_price;
    } else if (r.volume > 0 && r.market_value > 0) {
      currentPrice = r.market_value / r.volume;
    }

    return {
      symbol,
      name: r.stock_name,
      market,
      quantity: r.volume,
      availableQty: r.can_use_volume,
      frozenQty: r.frozen_volume,
      todayBuyQty: r.today_buy_volume,
      avgCost: r.avg_price,
      currentPrice,
      marketValue: r.market_value > 0 ? r.market_value : undefined,
      unrealizedPnl: r.unrealized_pnl ?? undefined,
      unrealizedPnlPct: r.unrealized_pnl_pct ?? undefined,
      source: "broker",
      lastSyncAt: new Date().toISOString(),
    };
  }

  private mapRawQuote(r: RawQuote, sym: string): QuoteSnapshot {
    const price = r.last_price;
    const change = price - r.last_close;
    return {
      symbol: { code: sym, name: sym, market: detectMarketFromSymbol(sym) },
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
