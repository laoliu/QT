/**
 * QMTBroker — ѸͶ miniQMT / xtquant broker adapter.
 *
 * Communicates with `python/brokers/qmt_broker.py` via PersistentPythonProcess
 * (NDJSON over stdin/stdout). The Python side uses xtquant.xttrader to place
 * orders and xtquant.xtdata for real-time quotes.
 *
 * Supported markets: CN stock, CN futures (via QMT).
 *
 * Setup:
 *   1. Install miniQMT from ѸͶ (requires a qualifying brokerage account).
 *   2. Keep miniQMT running in the background.
 *   3. Set config.tradingAccount.qmt.miniQmtPath pointing to the data directory.
 *   4. pip install xtquant  (bundled with miniQMT, but also on PyPI)
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
  QMTBrokerConfig,
} from "../types.js";
import type {
  IBrokerAdapter,
  OrderFilter,
  OrderRequest,
  QuoteCallback,
  FillCallback,
  OrderStatusCallback,
  ModifyOrderRequest,
  BatchOrderResult,
} from "./adapter.js";
import { isCnMarketOpen } from "./paper-broker.js";
import { nowCSTISO } from "../utils/cst-time.js";

// ── Raw shapes returned by qmt_broker.py ─────────────────────────

type RawOrder = {
  order_id: string;
  stock_code: string;
  order_type: number; // 23=buy 24=sell
  price: number;
  order_volume: number;
  traded_volume: number;
  traded_price: number;
  order_status: number; // see QMT status codes below
  strategy_name: string;
  order_remark?: string;
  strategy_error_msg?: string;
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
  on_road_volume?: number;
  yesterday_volume?: number;
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

// ── QMT order status mapping ──────────────────────────────────────

function mapQmtStatus(s: number): OrderStatus {
  // QMT order_status values (partial):
  // 48=未申报 49=待申报 50=已申报未成交 51=非交易日 52=无效申报
  // 53=待撤 54=部撤 55=已撤 56=部成 57=已成 58=废单
  switch (s) {
    case 48:
      return "pending";
    case 49:
      return "pending";
    case 50:
      return "submitted";
    case 53:
      return "submitted"; // pending cancel
    case 54:
      return "cancelled"; // partial cancel
    case 55:
      return "cancelled";
    case 56:
      return "partial";
    case 57:
      return "filled";
    case 58:
      return "rejected";
    default:
      return "submitted";
  }
}

// ── QMT order side mapping ────────────────────────────────────────

function mapSideToQmt(side: string): number {
  switch (side) {
    case "buy":
      return 23; // 买入
    case "sell":
      return 24; // 卖出
    case "short":
      return 26; // 融券卖出
    case "cover":
      return 27; // 融券买入
    // Option-specific sides (QMT codes)
    case "buy_open":
      return 48; // 买入开仓 (期权)
    case "sell_close":
      return 49; // 卖出平仓 (期权)
    case "sell_open":
      return 50; // 卖出开仓 (期权)
    case "buy_close":
      return 51; // 买入平仓 (期权)
    default:
      return 23;
  }
}

function mapQmtSide(
  t: number,
): "buy" | "sell" | "short" | "cover" | "buy_open" | "sell_close" | "sell_open" | "buy_close" {
  if (t === 23) return "buy";
  if (t === 24) return "sell";
  if (t === 26) return "short";
  if (t === 27) return "cover";
  if (t === 48) return "buy_open";
  if (t === 49) return "sell_close";
  if (t === 50) return "sell_open";
  if (t === 51) return "buy_close";
  return "buy";
}

/**
 * Detect option market type from symbol code for QMT-reported option orders.
 * ETF option codes: 8-digit starting with 1000xxxx or 9000xxxx (SSE/SZSE)
 * Index option codes: IO-C-/P- pattern (CFFEX)
 * Commodity option codes: letters+digits with -C-/-P- or C/P embedded
 */
function detectOptionMarketFromQmt(symbol: string): MarketType {
  // SSE/SZSE ETF options: 8-digit codes starting with 1000 or 9000
  if (/^[19]000\d{4}$/.test(symbol)) return "etf_option";
  // CFFEX index options: IO/MO/HO-YYMM-C/P-STRIKE
  if (/^[A-Z]{2}\d{4}-[CP]-/.test(symbol)) return "index_option";
  // DCE commodity options: m2404-C-3200
  if (/^[a-zA-Z]{1,2}\d{4}-[CP]-/.test(symbol)) return "commodity_option";
  // SHFE/CZCE commodity options: cu2404C55000, SR405C5600
  if (/^[a-zA-Z]{1,2}\d{3,4}[CP]\d+$/.test(symbol)) return "commodity_option";
  return "etf_option"; // default for option sides
}

// ── Symbol normalisation ──────────────────────────────────────────

/**
 * QMT uses suffixes: 600519.SH, 000001.SZ, 000300.SH (index), 830799.BJ.
 * Map from plain code to QMT format.
 *
 * Shanghai (SH): 5xxxxx (ETF/funds), 6xxxxx (main + STAR 688xxx), 9xxxxx (B-share)
 * Shenzhen (SZ): 0xxxxx, 1xxxxx (ETF 159xxx, bonds 12xxxx), 2xxxxx (B-share), 3xxxxx (ChiNext)
 * Beijing (BJ): 4xxxxx (430xxx), 8xxxxx (830xxx/8xxxxx)
 */
function toQmtSymbol(symbol: string): string {
  if (symbol.includes(".")) return symbol; // already suffixed
  const code = symbol.replace(/[^0-9A-Za-z]/g, "");
  const n = parseInt(code, 10);
  if (!isNaN(n)) {
    // Beijing Stock Exchange
    if (code.startsWith("8") || code.startsWith("43")) return `${code}.BJ`;
    // Shanghai: 5xxxxx (ETFs/funds), 6xxxxx (main board + STAR), 9xxxxx (B-share)
    if (n >= 500_000) return `${code}.SH`;
    // Everything else → Shenzhen (000xxx, 002xxx, 159xxx, 300xxx, etc.)
    return `${code}.SZ`;
  }
  return symbol;
}

function fromQmtSymbol(s: string): string {
  return s.replace(/\.(SH|SZ|BJ)$/i, "");
}

// ── QMTBroker ────────────────────────────────────────────────────

export class QMTBroker implements IBrokerAdapter {
  readonly brokerType: BrokerType = "qmt";
  connected = false;

  private proc: PersistentPythonProcess;
  private cfg: QuantTradingConfig;
  private qmtCfg: QMTBrokerConfig;
  private fillCbs: FillCallback[] = [];
  private statusCbs: OrderStatusCallback[] = [];
  private quoteSubs: Map<string, QuoteCallback[]> = new Map();

  // local cache of orders created in this session (keyed by local orderId → brokerOrderId)
  private orderCache: Map<string, Order> = new Map();
  private static readonly ORDER_CACHE_MAX = 500;
  private static readonly ORDER_CACHE_TTL_MS = 4 * 3600 * 1000; // 4 hours

  constructor(cfg: QuantTradingConfig) {
    this.cfg = cfg;
    this.qmtCfg = cfg.tradingAccount?.qmt ?? {};

    this.proc = new PersistentPythonProcess("brokers.qmt_broker", cfg, {
      mini_qmt_path: this.qmtCfg.miniQmtPath ?? "",
      account_id: this.qmtCfg.accountId ?? "",
      account_type: this.qmtCfg.accountType ?? "STOCK",
      require_execution_plan: cfg.tradingAccount?.requireExecutionPlan === true,
    });

    // Register event handler for push notifications from Python
    this.proc.onEvent((event, data) => this.handleEvent(event, data));
  }

  async connect(): Promise<void> {
    if (this.connected) return;
    await this.proc.start();
    // Connect to QMT via Python
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
    const requirePlan = this.cfg.tradingAccount?.requireExecutionPlan === true;
    if (requirePlan) {
      throw new Error(
        "QMT direct broker orders are disabled; submit a validated execution plan through QMTGateway",
      );
    }
    const now = nowCSTISO();
    const clientOrderId = req.clientOrderId ?? randomUUID();
    const orderId = randomUUID(); // local UUID

    // Call Python → QMT
    // For market orders (price_type=5), QMT ignores the price field;
    // pass 0 which QMT interprets as "use market price".
    const qmtPrice = req.orderType === "market" ? 0 : (req.price ?? 0);
    if (req.orderType === "limit" && (!req.price || req.price <= 0)) {
      throw new Error("限价单必须指定正数 price");
    }
    const result = await this.proc.call<{ broker_order_id: string }>("place_order", {
      stock_code: toQmtSymbol(req.symbol),
      order_type: mapSideToQmt(req.side),
      price_type: req.orderType === "market" ? 5 : 11, // 5=市价 11=限价
      price: qmtPrice,
      volume: req.quantity,
      strategy_name: req.strategyId ?? "manual",
      order_remark: clientOrderId,
      run_id: req.runId,
      model_id: req.modelId,
      signal_id: req.signalId,
      strategy_version: req.strategyVersion,
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
      modelId: req.modelId,
      signalId: req.signalId,
      runId: req.runId,
      strategyVersion: req.strategyVersion,
      dataTimestamp: req.dataTimestamp,
      brokerType: "qmt",
      createdAt: now,
      updatedAt: now,
      submittedAt: now,
    };
    this.orderCache.set(orderId, order);
    this.pruneOrderCache();
    return order;
  }

  /** Remove completed/cancelled orders older than TTL, keeping cache bounded */
  private pruneOrderCache(): void {
    if (this.orderCache.size <= QMTBroker.ORDER_CACHE_MAX) return;
    const now = Date.now();
    const terminalStatuses = new Set(["filled", "cancelled", "rejected", "expired"]);
    for (const [id, o] of this.orderCache) {
      if (this.orderCache.size <= QMTBroker.ORDER_CACHE_MAX) break;
      if (
        terminalStatuses.has(o.status) &&
        now - new Date(o.updatedAt).getTime() > QMTBroker.ORDER_CACHE_TTL_MS
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
      stock_code: toQmtSymbol(order.symbol),
    });
    order.status = "cancelled";
    order.updatedAt = nowCSTISO();
    for (const cb of this.statusCbs) cb(orderId, "cancelled", order);
  }

  async placeBatchOrders(reqs: OrderRequest[]): Promise<BatchOrderResult[]> {
    const results: BatchOrderResult[] = [];
    for (const req of reqs) {
      try {
        const order = await this.placeOrder(req);
        results.push({ ok: true, order, symbol: req.symbol });
      } catch (err) {
        results.push({ ok: false, error: (err as Error).message, symbol: req.symbol });
      }
    }
    return results;
  }

  async modifyOrder(orderId: string, update: ModifyOrderRequest): Promise<Order> {
    // QMT does not support in-place modification: cancel then resubmit
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
      modelId: original.modelId,
      signalId: original.signalId,
      runId: original.runId,
      strategyVersion: original.strategyVersion,
      dataTimestamp: original.dataTimestamp,
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
      stock_code: filter?.symbol ? toQmtSymbol(filter.symbol) : undefined,
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
      brokerType: "qmt",
      cash: raw.cash,
      frozenCash: raw.frozen_cash,
      totalAssets: raw.total_asset,
      marketValue: raw.market_value,
      profit: raw.profit,
      profitPct: raw.profit_ratio,
      lastSyncAt: nowCSTISO(),
    };
  }

  subscribeQuote(symbols: string[], cb: QuoteCallback): () => void {
    const qmtSymbols = symbols.map(toQmtSymbol);
    void this.proc.call("subscribe_quote", { stock_codes: qmtSymbols }).catch((err) => {
      console.warn(`[QMTBroker] subscribe_quote failed: ${(err as Error).message ?? err}`);
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
      void this.proc.call("unsubscribe_quote", { stock_codes: qmtSymbols }).catch((err) => {
        console.warn(`[QMTBroker] unsubscribe_quote failed: ${(err as Error).message ?? err}`);
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
    return isCnMarketOpen(market);
  }

  // ── Option support ──────────────────────────────────────────

  supportsOptions(): boolean {
    // OPTIONS account type required for option trading in QMT
    return this.qmtCfg.accountType === "OPTIONS";
  }

  async exerciseOption(
    symbol: string,
    quantity: number,
  ): Promise<{ success: boolean; message: string }> {
    if (!this.connected) throw new Error("Not connected");
    if (!this.supportsOptions()) {
      throw new Error("Option trading requires accountType=OPTIONS in QMT config");
    }
    const result = await this.proc.call<{ success: boolean; message: string }>("exercise_option", {
      stock_code: toQmtSymbol(symbol),
      volume: quantity,
    });
    return result;
  }

  // ── Scheduled strategy ──────────────────────────────────────

  /**
   * Generate a standalone Python strategy script for MiniQMT that places
   * limit orders at market open. Returns the file path on the Windows machine.
   */
  async saveScheduledStrategy(
    orders: Array<{
      stockCode: string;
      name: string;
      volume: number;
      price: number;
      remark?: string;
    }>,
    strategyName?: string,
    saveDir?: string,
  ): Promise<{ filepath: string; filename: string; orderCount: number; strategyName: string }> {
    if (!this.connected) throw new Error("Not connected");
    const { generateQmtStrategyScript } = await import("./qmt-strategy-gen.js");
    const { content, filename } = generateQmtStrategyScript(orders, {
      strategyName,
      miniQmtPath: this.qmtCfg.miniQmtPath,
      accountId: this.qmtCfg.accountId,
      accountType: this.qmtCfg.accountType,
    });

    const result = await this.proc.call<{
      filepath: string;
      filename: string;
    }>("save_scheduled_strategy", {
      content,
      filename,
      save_dir: saveDir ?? "",
    });

    return {
      filepath: result.filepath,
      filename: result.filename,
      orderCount: orders.length,
      strategyName: strategyName ?? "quant-trading_scheduled",
    };
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
        symbol: fromQmtSymbol(raw.stock_code),
        side: cachedOrder?.side ?? "buy", // fallback when no cached order found
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
      const sym = fromQmtSymbol(raw.stock_code);
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
    const now = nowCSTISO();
    localId ??= this.findLocalOrderId(r.order_id);
    const cached = localId ? this.orderCache.get(localId) : undefined;
    const sym = fromQmtSymbol(r.stock_code);
    const side = mapQmtSide(r.order_type);
    // Detect option market from order side — option sides (48-51) indicate option orders
    const isOptionSide =
      side === "buy_open" || side === "sell_open" || side === "buy_close" || side === "sell_close";
    const market = isOptionSide ? detectOptionMarketFromQmt(sym) : "cn_stock";
    return {
      orderId: localId ?? r.order_id,
      clientOrderId: cached?.clientOrderId,
      brokerOrderId: r.order_id,
      symbol: sym,
      market,
      side,
      orderType: r.price > 0 ? "limit" : "market",
      price: r.price,
      quantity: r.order_volume,
      filledQty: r.traded_volume,
      avgFillPrice: r.traded_price || undefined,
      status: mapQmtStatus(r.order_status),
      strategyId: r.strategy_name || cached?.strategyId,
      modelId: cached?.modelId ?? QMTBroker.remarkProvenance(r.order_remark, "model_id"),
      signalId: cached?.signalId ?? QMTBroker.remarkProvenance(r.order_remark, "signal_id"),
      runId: cached?.runId ?? QMTBroker.remarkProvenance(r.order_remark, "run_id"),
      strategyVersion: cached?.strategyVersion ?? QMTBroker.remarkProvenance(r.order_remark, "strategy_version"),
      dataTimestamp: cached?.dataTimestamp,
      errorMsg: r.strategy_error_msg,
      brokerType: "qmt",
      createdAt: cached?.createdAt ?? r.order_time ?? now,
      updatedAt: now,
    };
  }

  private static remarkProvenance(remark: unknown, key: string): string | undefined {
    if (typeof remark !== "string") return undefined;
    const prefix = `${key}=`;
    const field = remark.split("|").find((part) => part.startsWith(prefix));
    const value = field?.slice(prefix.length).trim();
    return value || undefined;
  }

  private mapRawPosition(r: RawPosition): LivePosition {
    // Prefer last_price from Python (includes xtdata fallback); derive from market_value/volume otherwise
    let currentPrice: number | undefined;
    if (r.last_price != null && r.last_price > 0) {
      currentPrice = r.last_price;
    } else if (r.volume > 0 && r.market_value > 0) {
      currentPrice = r.market_value / r.volume;
    }

    // Prefer pre-calculated PnL from Python (handles xtdata fallback for out-of-hours)
    let unrealizedPnl: number | undefined;
    let unrealizedPnlPct: number | undefined;
    if (r.unrealized_pnl != null) {
      unrealizedPnl = r.unrealized_pnl;
      unrealizedPnlPct = r.unrealized_pnl_pct ?? undefined;
    } else if (currentPrice != null && currentPrice > 0 && r.avg_price > 0) {
      unrealizedPnl = (currentPrice - r.avg_price) * r.volume;
      unrealizedPnlPct = (currentPrice - r.avg_price) / r.avg_price;
    }

    const symbol = fromQmtSymbol(r.stock_code);
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
    return {
      symbol: { code: sym, name: sym, market: "cn_stock" },
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
