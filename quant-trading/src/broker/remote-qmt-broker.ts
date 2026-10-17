/**
 * RemoteQMTBroker — IBrokerAdapter that delegates trading to a remote Windows
 * machine running qmt_data_bridge.py via direct HTTP POST.
 *
 * Direct mode (windowsBridgeUrl configured):
 *   Mac (RemoteQMTBroker) → POST http://windows:18888/trading {method, params}
 *   Mac (RemoteQMTBroker) ← GET  http://windows:18888/trading/events (polls)
 *
 * Fallback mode (no windowsBridgeUrl):
 *   Uses Mac-side trading-bridge-api polling relay for backward compatibility.
 */
import { randomUUID } from "node:crypto";
import { WindowsBridgeEventPoller, directPost } from "../bridge/windows-bridge-client.js";
import { detectMarketFromSymbol } from "../data/market-data-provider.js";
import type { TradingEvent } from "../http/trading-bridge-api.js";
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

// ── QMT‑specific helpers (same as qmt-broker.ts) ────────────────

function toQmtSymbol(symbol: string): string {
  if (symbol.includes(".")) return symbol;
  const code = symbol.replace(/[^0-9A-Za-z]/g, "");
  const n = parseInt(code, 10);
  if (!isNaN(n)) {
    if (code.startsWith("8") || code.startsWith("43")) return `${code}.BJ`;
    if (n >= 500_000) return `${code}.SH`;
    return `${code}.SZ`;
  }
  return symbol;
}

function fromQmtSymbol(s: string): string {
  return s.replace(/\.(SH|SZ|BJ)$/i, "");
}

function mapSideToQmt(side: string): number {
  switch (side) {
    case "buy":
      return 23;
    case "sell":
      return 24;
    case "short":
      return 26;
    case "cover":
      return 27;
    case "buy_open":
      return 48;
    case "sell_close":
      return 49;
    case "sell_open":
      return 50;
    case "buy_close":
      return 51;
    default:
      return 23;
  }
}

function mapQmtStatus(s: number): OrderStatus {
  switch (s) {
    case 48:
    case 49:
      return "pending";
    case 50:
      return "submitted";
    case 53:
      return "submitted";
    case 54:
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

function mapQmtSide(t: number): Order["side"] {
  if (t === 24) return "sell";
  if (t === 26) return "short";
  if (t === 27) return "cover";
  if (t === 48) return "buy_open";
  if (t === 49) return "sell_close";
  if (t === 50) return "sell_open";
  if (t === 51) return "buy_close";
  return "buy";
}

// ── Remote request timeout ───────────────────────────────────────

const CMD_TIMEOUT_MS = 15_000; // 15s for trading commands
const QUERY_TIMEOUT_MS = 10_000; // 10s for queries
const EVENT_POLL_INTERVAL_MS = 5_000; // 5s between event polls

// ── RemoteQMTBroker ──────────────────────────────────────────────

export class RemoteQMTBroker implements IBrokerAdapter {
  readonly brokerType: BrokerType = "qmt";
  connected = false;

  private fillCbs: FillCallback[] = [];
  private statusCbs: OrderStatusCallback[] = [];
  private quoteSubs: Map<string, QuoteCallback[]> = new Map();
  private orderCache: Map<string, Order> = new Map();
  private eventUnsub: (() => void) | null = null;
  private eventPoller: WindowsBridgeEventPoller | null = null;

  /** Windows bridge URL for direct mode, empty string for legacy polling mode */
  private readonly bridgeUrl: string;

  constructor(private readonly cfg: QuantTradingConfig) {
    this.bridgeUrl = cfg.tradingAccount?.qmt?.windowsBridgeUrl ?? "";
  }

  private get isDirect(): boolean {
    return this.bridgeUrl.length > 0;
  }

  /** Send a command to the remote Windows bridge and wait for the result. */
  private async remoteCall<T = unknown>(
    method: string,
    params: Record<string, unknown> = {},
    timeoutMs = CMD_TIMEOUT_MS,
  ): Promise<T> {
    if (this.isDirect) {
      // Direct HTTP POST to Windows qmt_data_bridge.py
      const resp = await directPost<{ ok: boolean; data?: T; error?: string }>(
        this.bridgeUrl,
        "/trading",
        { method, params },
        timeoutMs,
      );
      if (!resp.ok) {
        throw new Error(resp.error ?? `Remote QMT call ${method} failed`);
      }
      return resp.data as T;
    }
    // Legacy polling mode via trading-bridge-api
    const { submitTradingRequest, waitForTradingResponse } =
      await import("../http/trading-bridge-api.js");
    const requestId = submitTradingRequest(
      method as Parameters<typeof submitTradingRequest>[0],
      params,
    );
    const resp = await waitForTradingResponse(requestId, timeoutMs);
    if (!resp.ok) {
      throw new Error(resp.error ?? `Remote QMT call ${method} failed`);
    }
    return resp.data as T;
  }

  async connect(): Promise<void> {
    if (this.connected) return;

    // Subscribe to real-time events from Windows
    if (this.isDirect) {
      // Direct mode: poll /trading/events periodically
      this.startEventPolling();
    } else {
      // Legacy mode: use trading-bridge-api event subscription
      const { onTradingEvent } = await import("../http/trading-bridge-api.js");
      this.eventUnsub = onTradingEvent((evt) => this.handleEvent(evt));
    }

    try {
      await this.remoteCall("connect", {
        mini_qmt_path: this.cfg.tradingAccount?.qmt?.miniQmtPath ?? "",
        account_id: this.cfg.tradingAccount?.qmt?.accountId ?? "",
        account_type: this.cfg.tradingAccount?.qmt?.accountType ?? "STOCK",
        require_execution_plan: this.cfg.tradingAccount?.requireExecutionPlan === true,
      });
      this.connected = true;
      console.log(
        `[RemoteQMTBroker] Connected to remote QMT` +
          (this.isDirect ? ` (direct: ${this.bridgeUrl})` : " (polling mode)"),
      );
    } catch (err) {
      this.connected = true;
      console.warn(
        `[RemoteQMTBroker] Initial connect timed out (Windows bridge may not be running yet). ` +
          `Continuing in optimistic mode — orders will queue until bridge responds. ` +
          `Error: ${(err as Error).message}`,
      );
    }
  }

  async disconnect(): Promise<void> {
    if (!this.connected) return;
    try {
      await this.remoteCall("disconnect");
    } finally {
      this.connected = false;
      this.stopEventPolling();
      this.eventUnsub?.();
      this.eventUnsub = null;
    }
  }

  // ── Direct mode: event polling ──────────────────────────────

  private startEventPolling(): void {
    if (this.eventPoller) return;
    this.eventPoller = new WindowsBridgeEventPoller(
      this.bridgeUrl,
      (event) => this.handleEvent(event),
      EVENT_POLL_INTERVAL_MS,
    );
    this.eventPoller.start();
  }

  private stopEventPolling(): void {
    this.eventPoller?.stop();
    this.eventPoller = null;
  }

  async placeOrder(req: OrderRequest): Promise<Order> {
    const requirePlan = this.cfg.tradingAccount?.requireExecutionPlan === true;
    if (requirePlan && (!req.runId || !req.modelId || !req.signalId)) {
      throw new Error("QMT live profile requires runId, modelId and signalId from a validated execution plan");
    }
    const now = nowCSTISO();
    const clientOrderId = req.clientOrderId ?? randomUUID();
    const orderId = randomUUID();

    const qmtPrice = req.orderType === "market" ? 0 : (req.price ?? 0);
    if (req.orderType === "limit" && (!req.price || req.price <= 0)) {
      throw new Error("限价单必须指定正数 price");
    }

    const result = await this.remoteCall<{ broker_order_id: string }>("place_order", {
      stock_code: toQmtSymbol(req.symbol),
      order_type: mapSideToQmt(req.side),
      price_type: req.orderType === "market" ? 5 : 11,
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
    return order;
  }

  async cancelOrder(orderId: string): Promise<void> {
    const order = this.orderCache.get(orderId);
    if (!order?.brokerOrderId) throw new Error(`Order ${orderId} not found or has no broker ID`);
    await this.remoteCall("cancel_order", {
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
      const raw = await this.remoteCall<Record<string, unknown>>(
        "query_order",
        { order_id: cached.brokerOrderId },
        QUERY_TIMEOUT_MS,
      );
      return this.mapRawOrder(raw, orderId);
    } catch {
      return cached;
    }
  }

  async queryOrders(filter?: OrderFilter): Promise<Order[]> {
    const raw = await this.remoteCall<Array<Record<string, unknown>>>(
      "query_orders",
      { stock_code: filter?.symbol ? toQmtSymbol(filter.symbol) : undefined },
      QUERY_TIMEOUT_MS,
    );
    return raw.map((r) => this.mapRawOrder(r));
  }

  async queryPositions(): Promise<LivePosition[]> {
    const raw = await this.remoteCall<Array<Record<string, unknown>>>(
      "query_positions",
      {},
      QUERY_TIMEOUT_MS,
    );
    return raw.map((r) => this.mapRawPosition(r));
  }

  async queryAccount(): Promise<BrokerAccount> {
    const raw = await this.remoteCall<Record<string, unknown>>(
      "query_account",
      {},
      QUERY_TIMEOUT_MS,
    );
    return {
      accountId: (raw.account_id as string) ?? "",
      brokerType: "qmt",
      cash: (raw.cash as number) ?? 0,
      frozenCash: (raw.frozen_cash as number) ?? 0,
      totalAssets: (raw.total_asset as number) ?? 0,
      marketValue: (raw.market_value as number) ?? 0,
      profit: (raw.profit as number) ?? 0,
      profitPct: (raw.profit_ratio as number) ?? 0,
      lastSyncAt: nowCSTISO(),
    };
  }

  subscribeQuote(symbols: string[], cb: QuoteCallback): () => void {
    const qmtSymbols = symbols.map(toQmtSymbol);
    void this.remoteCall("subscribe_quote", { stock_codes: qmtSymbols }).catch((err) => {
      console.warn(`[RemoteQMTBroker] subscribe_quote failed: ${(err as Error).message}`);
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
      void this.remoteCall("unsubscribe_quote", { stock_codes: qmtSymbols }).catch(() => {});
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

  // ── Scheduled strategy file ─────────────────────────────────

  /**
   * Generate a strategy script and send it to the Windows bridge for saving.
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
  ): Promise<{ filepath: string; filename: string; orderCount: number; strategyName: string }> {
    const { generateQmtStrategyScript } = await import("./qmt-strategy-gen.js");
    const { content, filename } = generateQmtStrategyScript(orders, {
      strategyName,
      miniQmtPath: this.cfg.tradingAccount?.qmt?.miniQmtPath,
      accountId: this.cfg.tradingAccount?.qmt?.accountId,
      accountType: this.cfg.tradingAccount?.qmt?.accountType,
    });

    const result = await this.remoteCall<{ filepath: string; filename: string }>(
      "save_strategy_file",
      { content, filename },
      CMD_TIMEOUT_MS,
    );

    return {
      filepath: result.filepath,
      filename: result.filename,
      orderCount: orders.length,
      strategyName: strategyName ?? "quant-trading_scheduled",
    };
  }

  // ── Real-time event handling ────────────────────────────────

  private handleEvent(te: TradingEvent): void {
    const { event, data } = te;
    if (event === "fill") {
      const raw = data as Record<string, unknown>;
      const brokerOid = String(raw.order_id ?? "");
      const localId = this.findLocalOrderId(brokerOid);
      const cachedOrder = localId ? this.orderCache.get(localId) : undefined;
      const fill: Fill = {
        fillId: randomUUID(),
        orderId: localId ?? brokerOid,
        symbol: fromQmtSymbol(String(raw.stock_code ?? "")),
        side: cachedOrder?.side ?? "buy",
        price: (raw.traded_price as number) ?? 0,
        quantity: (raw.traded_volume as number) ?? 0,
        commission: (raw.commission as number) ?? 0,
        timestamp: String(raw.time ?? ""),
      };
      for (const cb of this.fillCbs) cb(fill);
    } else if (event === "order_status") {
      const raw = data as Record<string, unknown>;
      const brokerOid = String(raw.order_id ?? "");
      const localId = this.findLocalOrderId(brokerOid) ?? brokerOid;
      const status = mapQmtStatus((raw.order_status as number) ?? 0);
      const cached = this.orderCache.get(localId);
      if (cached) {
        cached.status = status;
        cached.filledQty = (raw.traded_volume as number) ?? cached.filledQty;
        cached.updatedAt = nowCSTISO();
      }
      const order =
        cached ??
        ({
          orderId: localId,
          symbol: fromQmtSymbol(String(raw.stock_code ?? "")),
          status,
        } as Order);
      for (const cb of this.statusCbs) cb(localId, status, order);
    } else if (event === "quote") {
      const raw = data as Record<string, unknown>;
      const sym = fromQmtSymbol(String(raw.stock_code ?? ""));
      const price = (raw.last_price as number) ?? 0;
      const lastClose = (raw.last_close as number) ?? 0;
      const change = price - lastClose;
      const snapshot: QuoteSnapshot = {
        symbol: { code: sym, name: sym, market: "cn_stock" },
        price,
        change,
        changePct: lastClose && price ? (change / lastClose) * 100 : 0,
        open: (raw.open as number) ?? 0,
        high: (raw.high as number) ?? 0,
        low: (raw.low as number) ?? 0,
        prevClose: lastClose,
        volume: (raw.volume as number) ?? 0,
        amount: (raw.amount as number) ?? 0,
        timestamp: (raw.time as number) ?? Date.now(),
        bid1: (raw.bid_price as number[])?.[0],
        ask1: (raw.ask_price as number[])?.[0],
      };
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

  private mapRawOrder(r: Record<string, unknown>, localId?: string): Order {
    const now = nowCSTISO();
    const sym = fromQmtSymbol(String(r.stock_code ?? ""));
    return {
      orderId: localId ?? String(r.order_id ?? ""),
      brokerOrderId: String(r.order_id ?? ""),
      symbol: sym,
      market: "cn_stock" as MarketType,
      side: mapQmtSide((r.order_type as number) ?? 23),
      orderType: "limit",
      price: (r.price as number) ?? 0,
      quantity: (r.order_volume as number) ?? 0,
      filledQty: (r.traded_volume as number) ?? 0,
      avgFillPrice: (r.traded_price as number) ?? undefined,
      status: mapQmtStatus((r.order_status as number) ?? 0),
      strategyId: (r.strategy_name as string) || undefined,
      modelId: RemoteQMTBroker.remarkProvenance(r.order_remark, "model_id"),
      runId: RemoteQMTBroker.remarkProvenance(r.order_remark, "run_id"),
      signalId: RemoteQMTBroker.remarkProvenance(r.order_remark, "signal_id"),
      strategyVersion: RemoteQMTBroker.remarkProvenance(r.order_remark, "strategy_version"),
      brokerType: "qmt",
      createdAt: now,
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

  private mapRawPosition(r: Record<string, unknown>): LivePosition {
    const symbol = fromQmtSymbol(String(r.stock_code ?? ""));
    const volume = (r.volume as number) ?? 0;
    const marketValue = (r.market_value as number) ?? 0;
    const avgPrice = (r.avg_price as number) ?? 0;

    // Prefer last_price; derive from market_value/volume otherwise
    let currentPrice: number | undefined;
    const lastPrice = r.last_price as number | undefined;
    if (lastPrice != null && lastPrice > 0) {
      currentPrice = lastPrice;
    } else if (volume > 0 && marketValue > 0) {
      currentPrice = marketValue / volume;
    }

    // Prefer pre-calculated PnL from remote; derive from price otherwise
    let unrealizedPnl: number | undefined;
    let unrealizedPnlPct: number | undefined;
    if (r.unrealized_pnl != null) {
      unrealizedPnl = r.unrealized_pnl as number;
      unrealizedPnlPct = (r.unrealized_pnl_pct as number) ?? undefined;
    } else if (currentPrice != null && currentPrice > 0 && avgPrice > 0) {
      unrealizedPnl = (currentPrice - avgPrice) * volume;
      unrealizedPnlPct = (currentPrice - avgPrice) / avgPrice;
    }

    return {
      symbol,
      name: String(r.stock_name ?? ""),
      market: detectMarketFromSymbol(symbol),
      quantity: volume,
      availableQty: (r.can_use_volume as number) ?? 0,
      frozenQty: (r.frozen_volume as number) ?? 0,
      todayBuyQty: (r.today_buy_volume as number) ?? 0,
      avgCost: avgPrice,
      currentPrice,
      marketValue: marketValue > 0 ? marketValue : undefined,
      unrealizedPnl,
      unrealizedPnlPct,
      source: "broker",
      lastSyncAt: nowCSTISO(),
    };
  }
}
