/**
 * IBrokerAdapter — universal broker interface.
 *
 * All concrete broker implementations (Paper, QMT, Futu, IB) must satisfy
 * this interface. Callers in OrderManager / LiveEngine only depend on this
 * abstraction; never on a concrete class.
 */
import type {
  BrokerAccount,
  BrokerType,
  Fill,
  LivePosition,
  Order,
  OrderSide,
  OrderStatus,
  OrderType,
  MarketType,
  QuoteSnapshot,
} from "../types.js";

// ── Request / Filter shapes ──────────────────────────────────────

export type OrderRequest = {
  symbol: string;
  market: MarketType;
  side: OrderSide;
  orderType: OrderType;
  quantity: number;
  price?: number; // required for limit / stop_limit
  stopPrice?: number; // required for stop / stop_limit
  strategyId?: string;
  signalId?: string;
  runId?: string;
  strategyVersion?: string;
  dataTimestamp?: number;
  clientOrderId?: string; // caller-supplied idempotency key
  // ── Option-specific fields ──
  strike?: number;
  expiry?: string;
  optionType?: "call" | "put";
  underlying?: string;
  exerciseType?: "european" | "american";
  /** ISO datetime after which the order should be auto-cancelled (GTC → GTD). */
  expiresAt?: string;
};

export type ModifyOrderRequest = {
  price?: number;
  quantity?: number;
  stopPrice?: number;
};

export type BatchOrderResult = {
  ok: boolean;
  order?: Order;
  error?: string;
  symbol: string;
};

export type OrderFilter = {
  symbol?: string;
  status?: OrderStatus[];
  dateFrom?: string; // YYYY-MM-DD
  dateTo?: string;
  strategyId?: string;
};

// ── Callback types ───────────────────────────────────────────────

export type QuoteCallback = (quote: QuoteSnapshot) => void;
export type FillCallback = (fill: Fill) => void;
export type OrderStatusCallback = (orderId: string, status: OrderStatus, order: Order) => void;

/**
 * Universal broker adapter interface.
 * All methods are async-first; implementations may call REST, Python bridge,
 * or local file storage internally.
 */
export interface IBrokerAdapter {
  /** Unique identifier for this broker type */
  readonly brokerType: BrokerType;

  /** Whether the adapter is currently connected and ready */
  readonly connected: boolean;

  /**
   * Connect to the broker. Must be called before any order/position methods.
   * Idempotent — calling again when already connected is a no-op.
   */
  connect(): Promise<void>;

  /**
   * Gracefully disconnect. Cancels all quote subscriptions.
   */
  disconnect(): Promise<void>;

  /**
   * Place a new order. Returns the local Order record (which may be in
   * "submitted" state; fills arrive via the FillCallback).
   */
  placeOrder(req: OrderRequest): Promise<Order>;

  /**
   * Cancel an open order by its local orderId.
   * Throws if the order is already filled/cancelled/rejected.
   */
  cancelOrder(orderId: string): Promise<void>;

  /**
   * Modify price or quantity of a pending/submitted order.
   * Not all brokers support modification; implementations may cancel+resubmit.
   */
  modifyOrder(orderId: string, update: ModifyOrderRequest): Promise<Order>;

  /**
   * Query a single order by local orderId.
   */
  queryOrder(orderId: string): Promise<Order | null>;

  /**
   * Query orders with optional filters.
   */
  queryOrders(filter?: OrderFilter): Promise<Order[]>;

  /**
   * Query current live positions from the broker.
   */
  queryPositions(): Promise<LivePosition[]>;

  /**
   * Query cash and asset summary from the broker.
   */
  queryAccount(): Promise<BrokerAccount>;

  /**
   * Subscribe to real-time quote updates for a set of symbols.
   * Returns an unsubscribe function.
   *
   * Note: implementations that rely on polling should do so internally;
   * callers only see the push model.
   */
  subscribeQuote(symbols: string[], cb: QuoteCallback): () => void;

  /**
   * Subscribe to fill (trade execution) notifications.
   * Returns an unsubscribe function.
   */
  subscribeFills(cb: FillCallback): () => void;

  /**
   * Subscribe to order-status change notifications.
   * Returns an unsubscribe function.
   */
  subscribeOrderStatus(cb: OrderStatusCallback): () => void;

  /**
   * Check whether the market is currently open for the given symbol/market.
   * Defaults to CN stock exchange hours if not provided.
   */
  isMarketOpen(market?: MarketType): Promise<boolean>;

  /**
   * Optional health-check ping. Implementations may override this
   * to verify broker connectivity without side effects.
   * Returns true if healthy, false if unreachable.
   * Default: not implemented (falls back to queryAccount in health check).
   */
  ping?(): Promise<boolean>;

  /**
   * Place multiple orders in batch. Default implementation falls back to
   * sequential placeOrder calls. Brokers that support native batch APIs
   * should override for better performance.
   *
   * Returns results in the same order as the input requests.
   */
  placeBatchOrders?(reqs: OrderRequest[]): Promise<BatchOrderResult[]>;

  // ── Optional: option-specific methods ─────────────────────────
  // Brokers that support option trading should implement these.

  /**
   * Whether this broker supports option trading.
   * Used by the risk gate and options_trading tool to check capability.
   */
  supportsOptions?(): boolean;

  /**
   * Exercise an option position (American-style only).
   * Returns the exercise confirmation or throws on failure.
   */
  exerciseOption?(symbol: string, quantity: number): Promise<{ success: boolean; message: string }>;

  /**
   * Query option-specific positions (with Greeks, expiry, strike info).
   * Implementations may enrich from the broker's native option position data.
   */
  queryOptionPositions?(): Promise<LivePosition[]>;
}
