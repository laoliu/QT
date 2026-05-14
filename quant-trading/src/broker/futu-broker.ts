/**
 * FutuBroker 66Futu OpenD broker adapter.
 *
 * Communicates with `python/brokers/futu_broker.py` via PersistentPythonProcess
 * (NDJSON over stdin/stdout). The Python side uses futu-api to connect to
 * Futu OpenD for orders, positions, and real-time quotes.
 *
 * Supported markets: HK stock, US stock, CN stock (A-share via Futu).
 *
 * Setup:
 *   1. Install and start Futu OpenD (from Futu official site).
 *   2. pip install futu-api
 *   3. Set config.tradingAccount.futu.openDHost and openDPort if non-default.
 *   4. For real trading, set tradeEnv to "REAL" and provide pwdUnlock.
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
import { nowCSTISO } from "../utils/cst-time.js";

// ── Raw shapes returned by futu_broker.py ─────────────────────────

type RawOrder = {
  order_id: string;
  stock_code: string;
  side: string;
  price: number;
  order_volume: number;
  traded_volume: number;
  traded_price: number;
  order_status: string; // already mapped in Python: "pending" | "submitted" | etc.
  order_time?: string;
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
  today_profit?: number;
  power?: number;
  max_power_short?: number | null;
  maintenance_margin?: number | null;
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
  time: number; // unix ms
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

type RawDeal = {
  deal_id: string;
  order_id: string;
  stock_code: string;
  stock_name: string;
  side: string;
  price: number;
  qty: number;
  amount: number;
  time: string;
};

type RawKline = {
  time_key: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  turnover: number;
  turnover_rate: number | null;
};

type RawOrderBookEntry = {
  price: number;
  volume: number;
  order_num: number;
};

type RawOrderBook = {
  stock_code: string;
  bids: RawOrderBookEntry[];
  asks: RawOrderBookEntry[];
};

type RawMarketSnapshot = {
  stock_code: string;
  stock_name: string;
  last_price: number;
  open: number;
  high: number;
  low: number;
  prev_close: number;
  volume: number;
  turnover: number;
  turnover_rate: number | null;
  amplitude: number | null;
  pe_ratio: number | null;
  pb_ratio: number | null;
  time: number;
};

// ── Symbol normalisation ──────────────────────────────────────────

/**
 * Convert plain symbol to Futu format.
 * HK: 5-digit →HK.00700
 * CN: 6-digit →SH.600519 or SZ.000001
 * US: alpha →US.AAPL
 */
function toFutuSymbol(symbol: string): string {
  if (/^(HK|US|SH|SZ|SG|JP)\./.test(symbol)) return symbol;

  // Handle QMT-format suffixes: 600519.SH →SH.600519
  if (symbol.endsWith(".SH")) return `SH.${symbol.slice(0, -3)}`;
  if (symbol.endsWith(".SZ")) return `SZ.${symbol.slice(0, -3)}`;
  if (symbol.endsWith(".HK")) return `HK.${symbol.slice(0, -3)}`;

  const code = symbol.replace(/[^0-9A-Za-z]/g, "");

  // Pure digits
  if (/^\d+$/.test(code)) {
    if (code.length === 5) return `HK.${code}`;
    if (code.length === 6) {
      const n = parseInt(code, 10);
      return n >= 500_000 ? `SH.${code}` : `SZ.${code}`;
    }
    // Pad to 5 digits for HK
    return `HK.${code.padStart(5, "0")}`;
  }

  // Alphabetic →US
  if (/^[A-Za-z]+$/.test(code)) return `US.${code.toUpperCase()}`;

  return symbol;
}

function fromFutuSymbol(s: string): string {
  const parts = s.split(".");
  if (parts.length === 2 && ["HK", "US", "SH", "SZ", "SG", "JP"].includes(parts[0])) {
    return parts[1];
  }
  return s;
}

/**
 * Detect market type from Futu symbol prefix.
 */
function detectMarketFromFutu(futuSymbol: string): MarketType {
  if (futuSymbol.startsWith("HK.")) return "hk_stock";
  if (futuSymbol.startsWith("US.")) return "us_stock";
  if (futuSymbol.startsWith("SH.") || futuSymbol.startsWith("SZ.")) return "cn_stock";
  return "hk_stock";
}

// ── FutuBroker ────────────────────────────────────────────────────

export type OrderBookEntry = {
  price: number;
  volume: number;
  orderNum: number;
};

export type OrderBookSnapshot = {
  symbol: string;
  bids: OrderBookEntry[];
  asks: OrderBookEntry[];
  timestamp: number;
};

export type OrderBookCallback = (snapshot: OrderBookSnapshot) => void;

export class FutuBroker implements IBrokerAdapter {
  readonly brokerType: BrokerType = "futu";
  connected = false;

  private proc: PersistentPythonProcess;
  private cfg: QuantTradingConfig;
  private futuCfg: FutuBrokerConfig;
  private fillCbs: FillCallback[] = [];
  private statusCbs: OrderStatusCallback[] = [];
  private quoteSubs: Map<string, QuoteCallback[]> = new Map();
  private orderbookCbs: Map<string, OrderBookCallback[]> = new Map();

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
      trade_pwd: this.futuCfg.pwdUnlock ?? "",
      account_id: cfg.tradingAccount?.accountId ?? "",
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
      this.orderbookCbs.clear();
    }
  }

  async placeOrder(req: OrderRequest): Promise<Order> {
    const now = nowCSTISO();
    const clientOrderId = req.clientOrderId ?? randomUUID();
    const orderId = randomUUID();

    if (req.orderType === "limit" && (!req.price || req.price <= 0)) {
      throw new Error("141508price");
    }
    if ((req.orderType === "auction_limit" || req.orderType === "special_limit") && (!req.price || req.price <= 0)) {
      throw new Error("竞价限价单碎股单必须指定正数price");
    }
    if ((req.orderType === "stop" || req.orderType === "stop_limit") && (!req.stopPrice || req.stopPrice <= 0)) {
      throw new Error("092908stopPrice");
    }

    const futuSymbol = toFutuSymbol(req.symbol);
    const needsPrice = req.orderType !== "market" && req.orderType !== "auction";
    const result = await this.proc.call<{ broker_order_id: string }>("place_order", {
      stock_code: futuSymbol,
      side: req.side,
      order_type: req.orderType,
      price: needsPrice ? (req.price ?? 0) : 0,
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
      stock_code: toFutuSymbol(order.symbol),
    });
    order.status = "cancelled";
    order.updatedAt = nowCSTISO();
    for (const cb of this.statusCbs) cb(orderId, "cancelled", order);
  }

  async modifyOrder(orderId: string, update: ModifyOrderRequest): Promise<Order> {
    const original = this.orderCache.get(orderId);
    if (!original) throw new Error(`Order ${orderId} not found`);

    // Futu supports in-place modification via modify_order
    await this.proc.call("modify_order", {
      order_id: original.brokerOrderId,
      price: update.price ?? original.price ?? 0,
      volume: update.quantity ?? original.quantity,
    });

    // Update cached order
    if (update.price !== undefined) original.price = update.price;
    if (update.quantity !== undefined) original.quantity = update.quantity;
    original.updatedAt = nowCSTISO();

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
    const params: Record<string, unknown> = {};
    if (filter?.symbol) {
      params.stock_code = toFutuSymbol(filter.symbol);
    }
    const raw = await this.proc.call<RawOrder[]>("query_orders", params);
    let orders = raw.map((r) => this.mapRawOrder(r));

    // Apply client-side filters that the Python bridge does not support
    if (filter) {
      orders = orders.filter(
        (o) =>
          (!filter.status?.length || filter.status.includes(o.status)) &&
          (!filter.strategyId || o.strategyId === filter.strategyId) &&
          (!filter.dateFrom || o.createdAt >= filter.dateFrom) &&
          (!filter.dateTo || o.createdAt <= filter.dateTo + "T99"),
      );
    }
    return orders;
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
      todayProfit: raw.today_profit ?? undefined,
      lastSyncAt: nowCSTISO(),
    };
  }

  subscribeQuote(symbols: string[], cb: QuoteCallback): () => void {
    const futuSymbols = symbols.map(toFutuSymbol);
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
      const result = await this.proc.call<{ alive: boolean }>("ping");
      return result.alive;
    } catch {
      return false;
    }
  }

  // ── Option support ──────────────────────────────────────────

  supportsOptions(): boolean {
    // Futu OpenD supports HK and US options trading
    return true;
  }

  async exerciseOption(
    symbol: string,
    quantity: number,
  ): Promise<{ success: boolean; message: string }> {
    if (!this.connected) throw new Error("Not connected");
    const futuSymbol = toFutuSymbol(symbol);
    const result = await this.proc.call<{ success: boolean; message: string }>("exercise_option", {
      stock_code: futuSymbol,
      volume: quantity,
    });
    return result;
  }

  async queryOptionPositions(): Promise<LivePosition[]> {
    const raw = await this.proc.call<RawPosition[]>("query_option_positions");
    return raw.map((r) => this.mapRawPosition(r));
  }

  // ── Extended data methods (Futu-specific, beyond IBrokerAdapter) ──

  /**
   * Query today's trade/deal history.
   * Returns filled trades with price, quantity, and timing.
   */
  async queryDeals(symbol?: string): Promise<
    Array<{
      dealId: string;
      orderId: string;
      symbol: string;
      name: string;
      side: string;
      price: number;
      quantity: number;
      amount: number;
      time: string;
    }>
  > {
    const params: Record<string, unknown> = {};
    if (symbol) params.stock_code = toFutuSymbol(symbol);
    const raw = await this.proc.call<RawDeal[]>("query_deals", params);
    return raw.map((r) => ({
      dealId: r.deal_id,
      orderId: r.order_id,
      symbol: fromFutuSymbol(r.stock_code),
      name: r.stock_name,
      side: r.side,
      price: r.price,
      quantity: r.qty,
      amount: r.amount,
      time: r.time,
    }));
  }

  /**
   * Fetch K-line (candlestick) data from Futu.
   * @param symbol Stock code (auto-converted to Futu format)
   * @param klType Futu K-line type: "K_1M" | "K_5M" | "K_15M" | "K_30M" | "K_60M" | "K_DAY" | "K_WEEK" | "K_MON"
   * @param count Number of bars to fetch (max 1000)
   */
  async getKlines(
    symbol: string,
    klType: string = "K_DAY",
    count: number = 100,
  ): Promise<RawKline[]> {
    return this.proc.call<RawKline[]>("get_klines", {
      stock_code: toFutuSymbol(symbol),
      kl_type: klType,
      count,
    });
  }

  /**
   * Get order book (bid/ask depth) snapshot for a symbol.
   */
  async getOrderbook(symbol: string): Promise<RawOrderBook> {
    return this.proc.call<RawOrderBook>("get_orderbook", {
      stock_code: toFutuSymbol(symbol),
    });
  }

  /**
   * Subscribe to order book push updates.
   */
  async subscribeOrderbook(symbols: string[]): Promise<{ subscribed_orderbook: string[] }> {
    const futuSymbols = symbols.map(toFutuSymbol);
    return this.proc.call("subscribe_orderbook", { stock_codes: futuSymbols });
  }

  /**
   * Subscribe to real-time order book push events.
   * Returns an unsubscribe function.
   */
  subscribeOrderbookPush(symbols: string[], cb: OrderBookCallback): () => void {
    const futuSymbols = symbols.map(toFutuSymbol);
    void this.proc.call("subscribe_orderbook", { stock_codes: futuSymbols }).catch((err) => {
      console.warn(`[FutuBroker] subscribe_orderbook failed: ${(err as Error).message ?? err}`);
    });
    for (const sym of symbols) {
      if (!this.orderbookCbs.has(sym)) this.orderbookCbs.set(sym, []);
      this.orderbookCbs.get(sym)!.push(cb);
    }
    return () => {
      for (const sym of symbols) {
        const arr = this.orderbookCbs.get(sym);
        if (arr) {
          const filtered = arr.filter((fn) => fn !== cb);
          if (filtered.length === 0) {
            this.orderbookCbs.delete(sym);
          } else {
            this.orderbookCbs.set(sym, filtered);
          }
        }
      }
    };
  }

  /**
   * Get real-time market snapshot for one or more symbols.
   */
  async getMarketSnapshot(symbols: string[]): Promise<RawMarketSnapshot[]> {
    const futuSymbols = symbols.map(toFutuSymbol);
    return this.proc.call<RawMarketSnapshot[]>("get_market_snapshot", {
      stock_codes: futuSymbols,
    });
  }

  // ── Extended: historical queries ────────────────────────────

  /**
   * Query historical orders across multiple days.
   * @param start Start date in "YYYY-MM-DD? format
   * @param end End date in ?YYYY-MM-DD? format
   * @param symbol Optional stock code filter
   */
  async queryHistoryOrders(
    start: string,
    end: string,
    symbol?: string,
  ): Promise<Order[]> {
    const params: Record<string, unknown> = { start, end };
    if (symbol) params.stock_code = toFutuSymbol(symbol);
    const raw = await this.proc.call<RawOrder[]>("query_history_orders", params);
    return raw.map((r) => this.mapRawOrder(r));
  }

  /**
   * Query historical deals/fills across multiple days.
   * @param start Start date in "YYYY-MM-DD" format
   * @param end End date in "YYYY-MM-DD" format
   * @param symbol Optional stock code filter
   */
  async queryHistoryDeals(
    start: string,
    end: string,
    symbol?: string,
  ): Promise<
    Array<{
      dealId: string;
      orderId: string;
      symbol: string;
      name: string;
      side: string;
      price: number;
      quantity: number;
      amount: number;
      time: string;
    }>
  > {
    const params: Record<string, unknown> = { start, end };
    if (symbol) params.stock_code = toFutuSymbol(symbol);
    const raw = await this.proc.call<RawDeal[]>("query_history_deals", params);
    return raw.map((r) => ({
      dealId: r.deal_id,
      orderId: r.order_id,
      symbol: fromFutuSymbol(r.stock_code),
      name: r.stock_name,
      side: r.side,
      price: r.price,
      quantity: r.qty,
      amount: r.amount,
      time: r.time,
    }));
  }

  // ── Extended: fund flow ──────────────────────────────────────

  /**
   * Query account fund flow history (deposits, withdrawals, dividends, etc.).
   */
  async queryFundFlow(
    start?: string,
    end?: string,
  ): Promise<
    Array<{
      time: string;
      type: string;
      symbol: string;
      amount: number;
      balance: number;
    }>
  > {
    const params: Record<string, unknown> = {};
    if (start) params.start = start;
    if (end) params.end = end;
    const raw = await this.proc.call<
      Array<{ time: string; type: string; stock_code: string; amount: number; balance: number }>
    >("query_fund_flow", params);
    return raw.map((r) => ({
      time: r.time,
      type: r.type,
      symbol: r.stock_code ? fromFutuSymbol(r.stock_code) : "",
      amount: r.amount,
      balance: r.balance,
    }));
  }

  // ── Extended: IPO ───────────────────────────────────────────

  /**
   * Query available IPO list for a given market.
   * @param market "HK" or "US"
   */
  async queryIpoList(market: string = "HK"): Promise<
    Array<{
      symbol: string;
      name: string;
      listTime: string;
      ipoPriceMin: number | null;
      ipoPriceMax: number | null;
      lotSize: number | null;
      applyTime: string | null;
      applyEndTime: string | null;
    }>
  > {
    const raw = await this.proc.call<
      Array<{
        stock_code: string;
        stock_name: string;
        list_time: string;
        ipo_price_min: number | null;
        ipo_price_max: number | null;
        lot_size: number | null;
        apply_time: string | null;
        apply_end_time: string | null;
      }>
    >("query_ipo_list", { market });
    return raw.map((r) => ({
      symbol: fromFutuSymbol(r.stock_code),
      name: r.stock_name,
      listTime: r.list_time,
      ipoPriceMin: r.ipo_price_min,
      ipoPriceMax: r.ipo_price_max,
      lotSize: r.lot_size,
      applyTime: r.apply_time,
      applyEndTime: r.apply_end_time,
    }));
  }

  /**
   * Subscribe to an IPO (apply for shares).
   */
  async subscribeIpo(
    symbol: string,
    quantity: number,
    price?: number,
  ): Promise<{ success: boolean; message: string }> {
    return this.proc.call<{ success: boolean; message: string }>("subscribe_ipo", {
      stock_code: toFutuSymbol(symbol),
      quantity,
      price: price ?? 0,
    });
  }

  // ── Extended: currency exchange ─────────────────────────────

  /**
   * Exchange currency between HKD/USD/CNH for cross-market trading.
   */
  async currencyExchange(
    fromCurrency: string,
    toCurrency: string,
    amount: number,
  ): Promise<{ success: boolean; message: string }> {
    return this.proc.call<{ success: boolean; message: string }>("currency_exchange", {
      from_currency: fromCurrency,
      to_currency: toCurrency,
      amount,
    });
  }

  // ── Extended: position cost adjustment ──────────────────────

  /**
   * Adjust the average cost of a position (after corporate actions, etc.).
   */
  async changePositionCost(
    symbol: string,
    costPrice: number,
  ): Promise<{ success: boolean; message: string }> {
    return this.proc.call<{ success: boolean; message: string }>("change_position_cost", {
      stock_code: toFutuSymbol(symbol),
      cost_price: costPrice,
    });
  }

  // ── Extended: margin account info ───────────────────────────

  /**
   * Get margin/leverage account details.
   * Returns purchasing power, max short power, and maintenance margin.
   */
  async queryMarginInfo(): Promise<{
    power: number;
    maxPowerShort: number | null;
    maintenanceMargin: number | null;
  }> {
    const raw = await this.proc.call<RawAccount>("query_account");
    return {
      power: raw.power ?? 0,
      maxPowerShort: raw.max_power_short ?? null,
      maintenanceMargin: raw.maintenance_margin ?? null,
    };
  }

  // ── Extended: option chain ─────────────────────────────────

  /**
   * Get available option expiration dates for an underlying.
   * @param symbol Underlying stock code (e.g. "00700" for Tencent HK options)
   */
  async getOptionExpiration(symbol: string): Promise<
    Array<{
      strikeTime: string;
      daysToExpiry: number;
    }>
  > {
    const futuSymbol = toFutuSymbol(symbol);
    const raw = await this.proc.call<
      Array<{ strike_time: string; option_expiry_date_distance: number }>
    >("get_option_expiration", { stock_code: futuSymbol });
    return raw.map((r) => ({
      strikeTime: r.strike_time,
      daysToExpiry: r.option_expiry_date_distance,
    }));
  }

  /**
   * Get option chain (all call/put contracts) for an underlying.
   * @param symbol Underlying stock code
   * @param start Start expiration date (YYYY-MM-DD)
   * @param end End expiration date (YYYY-MM-DD)
   */
  async getOptionChain(
    symbol: string,
    start?: string,
    end?: string,
  ): Promise<
    Array<{
      code: string;
      name: string;
      lotSize: number;
      optionType: string;
      underlying: string;
      strikeTime: string;
      strikePrice: number;
      suspended: boolean;
    }>
  > {
    const futuSymbol = toFutuSymbol(symbol);
    const params: Record<string, unknown> = { stock_code: futuSymbol };
    if (start) params.start = start;
    if (end) params.end = end;
    const raw = await this.proc.call<
      Array<{
        code: string;
        name: string;
        lot_size: number;
        option_type: string;
        stock_owner: string;
        strike_time: string;
        strike_price: number;
        suspension: boolean;
      }>
    >("get_option_chain", params);
    return raw.map((r) => ({
      code: fromFutuSymbol(r.code),
      name: r.name,
      lotSize: r.lot_size,
      optionType: r.option_type,
      underlying: fromFutuSymbol(r.stock_owner),
      strikeTime: r.strike_time,
      strikePrice: r.strike_price,
      suspended: r.suspension,
    }));
  }

  /**
   * Get basic info for stocks (name, lot size, listing status).
   * Useful for HK/US where AKShare has weaker coverage.
   */
  async getStockBasicInfo(
    market: string = "HK",
    secType: string = "STOCK",
    codes?: string[],
  ): Promise<
    Array<{
      code: string;
      name: string;
      lotSize: number;
      stockType: string;
      listingDate: string;
    }>
  > {
    const params: Record<string, unknown> = { market, sec_type: secType };
    if (codes) params.stock_codes = codes.map(toFutuSymbol);
    const raw = await this.proc.call<
      Array<{
        code: string;
        name: string;
        lot_size: number;
        stock_type: string;
        listing_date: string;
      }>
    >("get_stock_basicinfo", params);
    return raw.map((r) => ({
      code: fromFutuSymbol(r.code),
      name: r.name,
      lotSize: r.lot_size,
      stockType: r.stock_type,
      listingDate: r.listing_date,
    }));
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
          updatedAt: nowCSTISO(),
        });
      }
      for (const cb of this.statusCbs) cb(localId, order.status, order);
    } else if (event === "quote") {
      const raw = data as RawQuote;
      const sym = fromFutuSymbol(raw.stock_code);
      const snapshot = this.mapRawQuote(raw, sym);
      const quoteCbs = this.quoteSubs.get(sym);
      if (quoteCbs) for (const cb of quoteCbs) cb(snapshot);
    } else if (event === "orderbook") {
      const raw = data as RawOrderBook;
      const sym = fromFutuSymbol(raw.stock_code);
      const snapshot: OrderBookSnapshot = {
        symbol: sym,
        bids: raw.bids.map((b) => ({ price: b.price, volume: b.volume, orderNum: b.order_num })),
        asks: raw.asks.map((a) => ({ price: a.price, volume: a.volume, orderNum: a.order_num })),
        timestamp: Date.now(),
      };
      const obCbs = this.orderbookCbs.get(sym);
      if (obCbs) for (const cb of obCbs) cb(snapshot);
    }
  }

  private findLocalOrderId(brokerOrderId: string): string | undefined {
    for (const [localId, order] of this.orderCache) {
      if (order.brokerOrderId === brokerOrderId) return localId;
    }
    return undefined;
  }

  private mapRawOrder(r: RawOrder, localId?: string): Order {
    const now = nowCSTISO();
    const sym = fromFutuSymbol(r.stock_code);
    const market = detectMarketFromFutu(r.stock_code);
    const side = (r.side as Order["side"]) || "buy";
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
      status: (r.order_status as OrderStatus) || "submitted",
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
      lastSyncAt: nowCSTISO(),
    };
  }

  private mapRawQuote(r: RawQuote, sym: string): QuoteSnapshot {
    const price = r.last_price;
    const change = price - r.last_close;
    const market = detectMarketFromFutu(r.stock_code);
    return {
      symbol: { code: sym, name: sym, market },
      price,
      change,
      changePct: r.last_close && price ? (change / r.last_close) * 100 : 0,
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
