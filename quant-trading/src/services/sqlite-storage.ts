/**
 * SQLite-based persistence layer for quant-trading data.
 *
 * Provides a unified storage abstraction that can use either:
 *  - SQLite (via better-sqlite3, if available) — for production use
 *  - In-memory storage with JSON file persistence — for environments without SQLite
 *
 * Tables:
 *  - kline_cache       — historical K-line data (symbol, date, OHLCV)
 *  - signals           — trading signals
 *  - orders            — broker orders
 *  - trades            — closed trade records
 *  - equity_snapshots  — daily equity curve points
 *  - alerts            — alert history
 *
 * Schema versioning via `schema_version` table enables forward migrations.
 */
import fs from "node:fs/promises";
import path from "node:path";

// ── Schema ───────────────────────────────────────────────────

export const CURRENT_SCHEMA_VERSION = 1;

export const SCHEMA_SQL = `
-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
  version INTEGER NOT NULL,
  applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- K-line cache (historical OHLCV data)
CREATE TABLE IF NOT EXISTS kline_cache (
  symbol    TEXT NOT NULL,
  date      TEXT NOT NULL,
  interval  TEXT NOT NULL DEFAULT '1d',
  open      REAL NOT NULL,
  high      REAL NOT NULL,
  low       REAL NOT NULL,
  close     REAL NOT NULL,
  volume    REAL NOT NULL DEFAULT 0,
  amount    REAL,
  source    TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (symbol, date, interval)
);
CREATE INDEX IF NOT EXISTS idx_kline_symbol_date ON kline_cache(symbol, date);
CREATE INDEX IF NOT EXISTS idx_kline_date ON kline_cache(date);

-- Trading signals
CREATE TABLE IF NOT EXISTS signals (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp   INTEGER NOT NULL,
  date        TEXT,
  symbol      TEXT NOT NULL,
  direction   TEXT NOT NULL,
  price       REAL NOT NULL,
  quantity    INTEGER,
  reason      TEXT,
  confidence  REAL,
  strategy_id TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol);
CREATE INDEX IF NOT EXISTS idx_signals_strategy ON signals(strategy_id);
CREATE INDEX IF NOT EXISTS idx_signals_date ON signals(date);

-- Orders
CREATE TABLE IF NOT EXISTS orders (
  order_id    TEXT PRIMARY KEY,
  symbol      TEXT NOT NULL,
  market      TEXT,
  side        TEXT NOT NULL,
  order_type  TEXT NOT NULL,
  quantity    INTEGER NOT NULL,
  price       REAL,
  status      TEXT NOT NULL DEFAULT 'pending',
  strategy_id TEXT,
  signal_id   TEXT,
  filled_qty  INTEGER DEFAULT 0,
  filled_price REAL,
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders(symbol);
CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at);

-- Closed trades
CREATE TABLE IF NOT EXISTS trades (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol       TEXT NOT NULL,
  side         TEXT NOT NULL,
  entry_price  REAL NOT NULL,
  exit_price   REAL NOT NULL,
  quantity     INTEGER NOT NULL,
  entry_time   TEXT,
  exit_time    TEXT,
  net_pnl      REAL NOT NULL,
  pnl_pct      REAL,
  commission   REAL DEFAULT 0,
  strategy_id  TEXT,
  entry_order  TEXT,
  exit_order   TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_exit ON trades(exit_time);

-- Equity snapshots
CREATE TABLE IF NOT EXISTS equity_snapshots (
  date           TEXT PRIMARY KEY,
  equity         REAL NOT NULL,
  cash           REAL NOT NULL,
  market_value   REAL NOT NULL,
  daily_pnl      REAL NOT NULL DEFAULT 0,
  position_count INTEGER NOT NULL DEFAULT 0,
  benchmark      REAL,
  created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Alerts
CREATE TABLE IF NOT EXISTS alerts (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  severity     TEXT NOT NULL,
  source       TEXT NOT NULL,
  message      TEXT NOT NULL,
  details      TEXT,
  acknowledged INTEGER NOT NULL DEFAULT 0,
  timestamp    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity);
CREATE INDEX IF NOT EXISTS idx_alerts_ack ON alerts(acknowledged);

-- Trade plans (daily trade plan management)
CREATE TABLE IF NOT EXISTS trade_plans (
  id              TEXT PRIMARY KEY,
  symbol          TEXT NOT NULL,
  direction       TEXT NOT NULL,
  plan_price      REAL NOT NULL,
  stop_price      REAL NOT NULL,
  target_price    REAL NOT NULL,
  position_ratio  REAL NOT NULL DEFAULT 10,
  reason          TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'pending',
  date            TEXT NOT NULL,
  linked_order_id TEXT,
  strategy_id     TEXT,
  executed_price  REAL,
  executed_at     TEXT,
  cancel_reason   TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_trade_plans_symbol ON trade_plans(symbol);
CREATE INDEX IF NOT EXISTS idx_trade_plans_date ON trade_plans(date);
CREATE INDEX IF NOT EXISTS idx_trade_plans_status ON trade_plans(status);
`;

// ── Types ────────────────────────────────────────────────────

export interface KlineRow {
  symbol: string;
  date: string;
  interval: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  amount?: number;
  source?: string;
}

export interface SignalRow {
  id?: number;
  timestamp: number;
  date?: string;
  symbol: string;
  direction: string;
  price: number;
  quantity?: number;
  reason?: string;
  confidence?: number;
  strategyId?: string;
}

export interface OrderRow {
  orderId: string;
  symbol: string;
  market?: string;
  side: string;
  orderType: string;
  quantity: number;
  price?: number;
  status: string;
  strategyId?: string;
  signalId?: string;
  filledQty?: number;
  filledPrice?: number;
  createdAt?: string;
  updatedAt?: string;
}

export interface TradeRow {
  id?: number;
  symbol: string;
  side: string;
  entryPrice: number;
  exitPrice: number;
  quantity: number;
  entryTime?: string;
  exitTime?: string;
  netPnl: number;
  pnlPct?: number;
  commission?: number;
  strategyId?: string;
  entryOrder?: string;
  exitOrder?: string;
}

export interface EquityRow {
  date: string;
  equity: number;
  cash: number;
  marketValue: number;
  dailyPnl: number;
  positionCount: number;
  benchmark?: number;
}

export interface TradePlanRow {
  id: string;
  symbol: string;
  direction: "buy" | "sell";
  planPrice: number;
  stopPrice: number;
  targetPrice: number;
  positionRatio: number;
  reason: string;
  status: "pending" | "executed" | "cancelled";
  date: string;
  linkedOrderId?: string;
  strategyId?: string;
  executedPrice?: number;
  executedAt?: string;
  cancelReason?: string;
  createdAt: string;
  updatedAt: string;
}

// ── Storage interface ────────────────────────────────────────

export interface QuantStorage {
  /** Initialize storage (create tables, run migrations). */
  initialize(): Promise<void>;

  // ── K-line data ──────────────────────────
  upsertKlines(rows: KlineRow[]): Promise<void>;
  getKlines(symbol: string, interval: string, start: string, end: string): Promise<KlineRow[]>;
  getKlineCount(symbol?: string): Promise<number>;
  getKlineDateRange(symbol: string, interval: string): Promise<{ min: string; max: string } | null>;
  /** List all distinct symbol:interval pairs present in cache. */
  listCachedSymbols(): Promise<Array<{ symbol: string; interval: string }>>;
  /** Delete klines for a given symbol/interval (or all if omitted). Returns rows deleted. */
  deleteKlines(symbol?: string, interval?: string): Promise<number>;

  // ── Signals ──────────────────────────────
  insertSignal(signal: SignalRow): Promise<number>;
  getSignals(opts?: { symbol?: string; strategyId?: string; limit?: number }): Promise<SignalRow[]>;

  // ── Orders ───────────────────────────────
  upsertOrder(order: OrderRow): Promise<void>;
  getOrders(opts?: { symbol?: string; status?: string[]; limit?: number }): Promise<OrderRow[]>;
  updateOrderStatus(
    orderId: string,
    status: string,
    filledQty?: number,
    filledPrice?: number,
  ): Promise<void>;

  // ── Trades ───────────────────────────────
  insertTrade(trade: TradeRow): Promise<number>;
  getTrades(opts?: { symbol?: string; limit?: number }): Promise<TradeRow[]>;

  // ── Equity snapshots ─────────────────────
  upsertEquitySnapshot(row: EquityRow): Promise<void>;
  getEquitySnapshots(opts?: { start?: string; end?: string; limit?: number }): Promise<EquityRow[]>;

  // ── Trade plans ─────────────────────────
  insertTradePlan(plan: TradePlanRow): Promise<void>;
  updateTradePlan(id: string, patch: Partial<TradePlanRow>): Promise<boolean>;
  deleteTradePlan(id: string): Promise<boolean>;
  getTradePlan(id: string): Promise<TradePlanRow | undefined>;
  getTradePlans(opts?: {
    symbol?: string;
    date?: string;
    status?: string;
    limit?: number;
  }): Promise<TradePlanRow[]>;
  getTradePlanCount(): Promise<{ total: number; pending: number; executed: number; cancelled: number }>;

  // ── Lifecycle ────────────────────────────
  close(): Promise<void>;
}

// ── In-memory implementation (JSON-backed fallback) ──────────

export class InMemoryStorage implements QuantStorage {
  private klines: Map<string, KlineRow[]> = new Map();
  private signals: SignalRow[] = [];
  private orders: Map<string, OrderRow> = new Map();
  private trades: TradeRow[] = [];
  private equity: Map<string, EquityRow> = new Map();
  private tradePlans: Map<string, TradePlanRow> = new Map();
  private nextSignalId = 1;
  private nextTradeId = 1;
  private persistPath: string | null;
  private dirty = false;

  constructor(persistPath: string | null = null) {
    this.persistPath = persistPath;
  }

  async initialize(): Promise<void> {
    if (this.persistPath) {
      try {
        const raw = await fs.readFile(this.persistPath, "utf-8");
        const data = JSON.parse(raw);
        if (data.klines) {
          for (const [key, rows] of Object.entries(data.klines)) {
            this.klines.set(key, rows as KlineRow[]);
          }
        }
        if (data.signals) this.signals = data.signals;
        if (data.orders) {
          for (const o of data.orders) {
            this.orders.set(o.orderId, o);
          }
        }
        if (data.trades) this.trades = data.trades;
        if (data.equity) {
          for (const e of data.equity) {
            this.equity.set(e.date, e);
          }
        }
        if (data.tradePlans) {
          for (const p of data.tradePlans) {
            this.tradePlans.set(p.id, p);
          }
        }
        this.nextSignalId = this.signals.length + 1;
        this.nextTradeId = this.trades.length + 1;
      } catch {
        // No existing data
      }
    }
  }

  private flushTimer: ReturnType<typeof setTimeout> | null = null;
  private static readonly FLUSH_DEBOUNCE_MS = 5_000;
  private flushing = false;

  private async maybePersist(): Promise<void> {
    if (!this.persistPath) return;
    this.dirty = true;
    // Debounced auto-flush: write at most once per 5 s
    if (!this.flushTimer) {
      this.flushTimer = setTimeout(() => {
        this.flushTimer = null;
        void this.flush();
      }, InMemoryStorage.FLUSH_DEBOUNCE_MS);
    }
  }

  async flush(): Promise<void> {
    if (!this.persistPath || !this.dirty || this.flushing) return;
    this.flushing = true;
    try {
      const data = {
        klines: Object.fromEntries(this.klines),
        signals: this.signals,
        orders: [...this.orders.values()],
        trades: this.trades,
        equity: [...this.equity.values()],
        tradePlans: [...this.tradePlans.values()],
      };
      await fs.mkdir(path.dirname(this.persistPath), { recursive: true });
      await fs.writeFile(this.persistPath, JSON.stringify(data));
      this.dirty = false;
    } finally {
      this.flushing = false;
    }
  }

  // ── K-lines ───────────────────────────────────────────────

  async upsertKlines(rows: KlineRow[]): Promise<void> {
    for (const row of rows) {
      const key = `${row.symbol}:${row.interval}`;
      let arr = this.klines.get(key);
      if (!arr) {
        arr = [];
        this.klines.set(key, arr);
      }
      const idx = arr.findIndex((r) => r.date === row.date);
      if (idx >= 0) {
        arr[idx] = row;
      } else {
        arr.push(row);
        arr.sort((a, b) => a.date.localeCompare(b.date));
      }
    }
    await this.maybePersist();
  }

  async getKlines(
    symbol: string,
    interval: string,
    start: string,
    end: string,
  ): Promise<KlineRow[]> {
    const key = `${symbol}:${interval}`;
    const arr = this.klines.get(key) ?? [];
    return arr.filter((r) => r.date >= start && r.date <= end);
  }

  async getKlineCount(symbol?: string): Promise<number> {
    if (symbol) {
      let count = 0;
      for (const [key, arr] of this.klines) {
        if (key.startsWith(symbol + ":")) count += arr.length;
      }
      return count;
    }
    let total = 0;
    for (const arr of this.klines.values()) total += arr.length;
    return total;
  }

  async getKlineDateRange(
    symbol: string,
    interval: string,
  ): Promise<{ min: string; max: string } | null> {
    const key = `${symbol}:${interval}`;
    const arr = this.klines.get(key);
    if (!arr || arr.length === 0) return null;
    return { min: arr[0]!.date, max: arr[arr.length - 1]!.date };
  }

  async listCachedSymbols(): Promise<Array<{ symbol: string; interval: string }>> {
    const result: Array<{ symbol: string; interval: string }> = [];
    for (const key of this.klines.keys()) {
      const [symbol, interval] = key.split(":");
      if (symbol && interval) result.push({ symbol, interval });
    }
    return result;
  }

  async deleteKlines(symbol?: string, interval?: string): Promise<number> {
    let deleted = 0;
    if (symbol && interval) {
      const key = `${symbol}:${interval}`;
      deleted = this.klines.get(key)?.length ?? 0;
      this.klines.delete(key);
    } else if (symbol) {
      for (const [key, arr] of this.klines) {
        if (key.startsWith(symbol + ":")) {
          deleted += arr.length;
          this.klines.delete(key);
        }
      }
    } else {
      for (const arr of this.klines.values()) deleted += arr.length;
      this.klines.clear();
    }
    if (deleted > 0) await this.maybePersist();
    return deleted;
  }

  // ── Signals ───────────────────────────────────────────────

  async insertSignal(signal: SignalRow): Promise<number> {
    const id = this.nextSignalId++;
    this.signals.push({ ...signal, id });
    if (this.signals.length > 1000) this.signals = this.signals.slice(-1000);
    await this.maybePersist();
    return id;
  }

  async getSignals(opts?: {
    symbol?: string;
    strategyId?: string;
    limit?: number;
  }): Promise<SignalRow[]> {
    let result = [...this.signals];
    if (opts?.symbol) result = result.filter((s) => s.symbol === opts.symbol);
    if (opts?.strategyId) result = result.filter((s) => s.strategyId === opts.strategyId);
    result.sort((a, b) => b.timestamp - a.timestamp);
    if (opts?.limit) result = result.slice(0, opts.limit);
    return result;
  }

  // ── Orders ────────────────────────────────────────────────

  async upsertOrder(order: OrderRow): Promise<void> {
    this.orders.set(order.orderId, order);
    await this.maybePersist();
  }

  async getOrders(opts?: {
    symbol?: string;
    status?: string[];
    limit?: number;
  }): Promise<OrderRow[]> {
    let result = [...this.orders.values()];
    if (opts?.symbol) result = result.filter((o) => o.symbol === opts.symbol);
    if (opts?.status?.length) result = result.filter((o) => opts.status!.includes(o.status));
    result.sort((a, b) => (b.createdAt ?? "").localeCompare(a.createdAt ?? ""));
    if (opts?.limit) result = result.slice(0, opts.limit);
    return result;
  }

  async updateOrderStatus(
    orderId: string,
    status: string,
    filledQty?: number,
    filledPrice?: number,
  ): Promise<void> {
    const order = this.orders.get(orderId);
    if (order) {
      order.status = status;
      if (filledQty !== undefined) order.filledQty = filledQty;
      if (filledPrice !== undefined) order.filledPrice = filledPrice;
      order.updatedAt = new Date().toISOString();
    }
    await this.maybePersist();
  }

  // ── Trades ────────────────────────────────────────────────

  async insertTrade(trade: TradeRow): Promise<number> {
    const id = this.nextTradeId++;
    this.trades.push({ ...trade, id });
    await this.maybePersist();
    return id;
  }

  async getTrades(opts?: { symbol?: string; limit?: number }): Promise<TradeRow[]> {
    let result = [...this.trades];
    if (opts?.symbol) result = result.filter((t) => t.symbol === opts.symbol);
    result.sort((a, b) => (b.exitTime ?? "").localeCompare(a.exitTime ?? ""));
    if (opts?.limit) result = result.slice(0, opts.limit);
    return result;
  }

  // ── Equity ────────────────────────────────────────────────

  async upsertEquitySnapshot(row: EquityRow): Promise<void> {
    this.equity.set(row.date, row);
    await this.maybePersist();
  }

  async getEquitySnapshots(opts?: {
    start?: string;
    end?: string;
    limit?: number;
  }): Promise<EquityRow[]> {
    let result = [...this.equity.values()];
    if (opts?.start) result = result.filter((e) => e.date >= opts.start!);
    if (opts?.end) result = result.filter((e) => e.date <= opts.end!);
    result.sort((a, b) => a.date.localeCompare(b.date));
    if (opts?.limit) result = result.slice(-opts.limit);
    return result;
  }

  // ── Trade plans ──────────────────────────────────────────

  async insertTradePlan(plan: TradePlanRow): Promise<void> {
    this.tradePlans.set(plan.id, plan);
    await this.maybePersist();
  }

  async updateTradePlan(id: string, patch: Partial<TradePlanRow>): Promise<boolean> {
    const plan = this.tradePlans.get(id);
    if (!plan) return false;
    Object.assign(plan, patch, { updatedAt: new Date().toISOString() });
    await this.maybePersist();
    return true;
  }

  async deleteTradePlan(id: string): Promise<boolean> {
    const ok = this.tradePlans.delete(id);
    if (ok) await this.maybePersist();
    return ok;
  }

  async getTradePlan(id: string): Promise<TradePlanRow | undefined> {
    return this.tradePlans.get(id);
  }

  async getTradePlans(opts?: {
    symbol?: string;
    date?: string;
    status?: string;
    limit?: number;
  }): Promise<TradePlanRow[]> {
    let result = [...this.tradePlans.values()];
    if (opts?.symbol) result = result.filter((p) => p.symbol === opts.symbol);
    if (opts?.date) result = result.filter((p) => p.date === opts.date);
    if (opts?.status) result = result.filter((p) => p.status === opts.status);
    result.sort((a, b) => b.createdAt.localeCompare(a.createdAt));
    if (opts?.limit) result = result.slice(0, opts.limit);
    return result;
  }

  async getTradePlanCount(): Promise<{ total: number; pending: number; executed: number; cancelled: number }> {
    const all = [...this.tradePlans.values()];
    return {
      total: all.length,
      pending: all.filter((p) => p.status === "pending").length,
      executed: all.filter((p) => p.status === "executed").length,
      cancelled: all.filter((p) => p.status === "cancelled").length,
    };
  }

  // ── Lifecycle ─────────────────────────────────────────────

  async close(): Promise<void> {
    await this.flush();
  }
}

// ── SQLite implementation (requires better-sqlite3 at runtime) ──

export class SqliteStorage implements QuantStorage {
  private db: unknown = null;
  private dbPath: string;

  constructor(dbPath: string) {
    this.dbPath = dbPath;
  }

  async initialize(): Promise<void> {
    // Dynamic import so the module doesn't fail if better-sqlite3 isn't installed
    let Database: { new (path: string): unknown };
    try {
      const mod = await import("better-sqlite3");
      Database = mod.default ?? mod;
    } catch {
      throw new Error("better-sqlite3 not installed. Install it with: npm install better-sqlite3");
    }

    await fs.mkdir(path.dirname(this.dbPath), { recursive: true });
    this.db = new Database(this.dbPath);

    // Enable WAL mode for better concurrent read performance
    (this.db as Record<string, Function>).pragma("journal_mode = WAL");

    // Run schema
    (this.db as Record<string, Function>).exec(SCHEMA_SQL);

    // Record schema version if not present
    const row = (this.db as Record<string, Function>)
      .prepare("SELECT MAX(version) as v FROM schema_version")
      .get() as { v: number | null } | undefined;
    if (!row?.v) {
      (this.db as Record<string, Function>)
        .prepare("INSERT INTO schema_version (version) VALUES (?)")
        .run(CURRENT_SCHEMA_VERSION);
    }
  }

  // ── K-lines ───────────────────────────────────────────────

  async upsertKlines(rows: KlineRow[]): Promise<void> {
    const stmt = (this.db as Record<string, Function>).prepare(`
      INSERT OR REPLACE INTO kline_cache (symbol, date, interval, open, high, low, close, volume, amount, source)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `);
    const txn = (this.db as Record<string, Function>).transaction((items: KlineRow[]) => {
      for (const r of items) {
        stmt.run(
          r.symbol,
          r.date,
          r.interval,
          r.open,
          r.high,
          r.low,
          r.close,
          r.volume,
          r.amount ?? null,
          r.source ?? null,
        );
      }
    });
    txn(rows);
  }

  async getKlines(
    symbol: string,
    interval: string,
    start: string,
    end: string,
  ): Promise<KlineRow[]> {
    return (this.db as Record<string, Function>)
      .prepare(
        "SELECT * FROM kline_cache WHERE symbol = ? AND interval = ? AND date BETWEEN ? AND ? ORDER BY date",
      )
      .all(symbol, interval, start, end) as KlineRow[];
  }

  async getKlineCount(symbol?: string): Promise<number> {
    if (symbol) {
      const row = (this.db as Record<string, Function>)
        .prepare("SELECT COUNT(*) as cnt FROM kline_cache WHERE symbol = ?")
        .get(symbol) as { cnt: number };
      return row.cnt;
    }
    const row = (this.db as Record<string, Function>)
      .prepare("SELECT COUNT(*) as cnt FROM kline_cache")
      .get() as { cnt: number };
    return row.cnt;
  }

  async getKlineDateRange(
    symbol: string,
    interval: string,
  ): Promise<{ min: string; max: string } | null> {
    const row = (this.db as Record<string, Function>)
      .prepare(
        "SELECT MIN(date) as min, MAX(date) as max FROM kline_cache WHERE symbol = ? AND interval = ?",
      )
      .get(symbol, interval) as { min: string | null; max: string | null };
    if (!row?.min) return null;
    return { min: row.min, max: row.max! };
  }

  async listCachedSymbols(): Promise<Array<{ symbol: string; interval: string }>> {
    return (this.db as Record<string, Function>)
      .prepare("SELECT DISTINCT symbol, interval FROM kline_cache ORDER BY symbol, interval")
      .all() as Array<{ symbol: string; interval: string }>;
  }

  async deleteKlines(symbol?: string, interval?: string): Promise<number> {
    if (symbol && interval) {
      const result = (this.db as Record<string, Function>)
        .prepare("DELETE FROM kline_cache WHERE symbol = ? AND interval = ?")
        .run(symbol, interval) as { changes: number };
      return result.changes;
    } else if (symbol) {
      const result = (this.db as Record<string, Function>)
        .prepare("DELETE FROM kline_cache WHERE symbol = ?")
        .run(symbol) as { changes: number };
      return result.changes;
    } else {
      const result = (this.db as Record<string, Function>)
        .prepare("DELETE FROM kline_cache")
        .run() as { changes: number };
      return result.changes;
    }
  }

  // ── Signals ───────────────────────────────────────────────

  async insertSignal(signal: SignalRow): Promise<number> {
    const result = (this.db as Record<string, Function>)
      .prepare(
        "INSERT INTO signals (timestamp, date, symbol, direction, price, quantity, reason, confidence, strategy_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
      )
      .run(
        signal.timestamp,
        signal.date ?? null,
        signal.symbol,
        signal.direction,
        signal.price,
        signal.quantity ?? null,
        signal.reason ?? null,
        signal.confidence ?? null,
        signal.strategyId ?? null,
      );
    return (result as { lastInsertRowid: number }).lastInsertRowid;
  }

  async getSignals(opts?: {
    symbol?: string;
    strategyId?: string;
    limit?: number;
  }): Promise<SignalRow[]> {
    let sql = "SELECT * FROM signals WHERE 1=1";
    const params: unknown[] = [];
    if (opts?.symbol) {
      sql += " AND symbol = ?";
      params.push(opts.symbol);
    }
    if (opts?.strategyId) {
      sql += " AND strategy_id = ?";
      params.push(opts.strategyId);
    }
    sql += " ORDER BY timestamp DESC";
    if (opts?.limit) {
      sql += " LIMIT ?";
      params.push(opts.limit);
    }
    const rows = (this.db as Record<string, Function>).prepare(sql).all(...params) as Array<
      Record<string, unknown>
    >;
    return rows.map((r) => ({
      id: r.id as number,
      timestamp: r.timestamp as number,
      date: r.date as string | undefined,
      symbol: r.symbol as string,
      direction: r.direction as string,
      price: r.price as number,
      quantity: r.quantity as number | undefined,
      reason: r.reason as string | undefined,
      confidence: r.confidence as number | undefined,
      strategyId: r.strategy_id as string | undefined,
    }));
  }

  // ── Orders ────────────────────────────────────────────────

  async upsertOrder(order: OrderRow): Promise<void> {
    (this.db as Record<string, Function>)
      .prepare(`
      INSERT OR REPLACE INTO orders (order_id, symbol, market, side, order_type, quantity, price, status, strategy_id, signal_id, filled_qty, filled_price)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `)
      .run(
        order.orderId,
        order.symbol,
        order.market ?? null,
        order.side,
        order.orderType,
        order.quantity,
        order.price ?? null,
        order.status,
        order.strategyId ?? null,
        order.signalId ?? null,
        order.filledQty ?? 0,
        order.filledPrice ?? null,
      );
  }

  async getOrders(opts?: {
    symbol?: string;
    status?: string[];
    limit?: number;
  }): Promise<OrderRow[]> {
    let sql = "SELECT * FROM orders WHERE 1=1";
    const params: unknown[] = [];
    if (opts?.symbol) {
      sql += " AND symbol = ?";
      params.push(opts.symbol);
    }
    if (opts?.status?.length) {
      sql += ` AND status IN (${opts.status.map(() => "?").join(",")})`;
      params.push(...opts.status);
    }
    sql += " ORDER BY created_at DESC";
    if (opts?.limit) {
      sql += " LIMIT ?";
      params.push(opts.limit);
    }
    const rows = (this.db as Record<string, Function>).prepare(sql).all(...params) as Array<
      Record<string, unknown>
    >;
    return rows.map((r) => ({
      orderId: r.order_id as string,
      symbol: r.symbol as string,
      market: r.market as string | undefined,
      side: r.side as string,
      orderType: r.order_type as string,
      quantity: r.quantity as number,
      price: r.price as number | undefined,
      status: r.status as string,
      strategyId: r.strategy_id as string | undefined,
      signalId: r.signal_id as string | undefined,
      filledQty: r.filled_qty as number | undefined,
      filledPrice: r.filled_price as number | undefined,
      createdAt: r.created_at as string | undefined,
      updatedAt: r.updated_at as string | undefined,
    }));
  }

  async updateOrderStatus(
    orderId: string,
    status: string,
    filledQty?: number,
    filledPrice?: number,
  ): Promise<void> {
    let sql = "UPDATE orders SET status = ?, updated_at = datetime('now')";
    const params: unknown[] = [status];
    if (filledQty !== undefined) {
      sql += ", filled_qty = ?";
      params.push(filledQty);
    }
    if (filledPrice !== undefined) {
      sql += ", filled_price = ?";
      params.push(filledPrice);
    }
    sql += " WHERE order_id = ?";
    params.push(orderId);
    (this.db as Record<string, Function>).prepare(sql).run(...params);
  }

  // ── Trades ────────────────────────────────────────────────

  async insertTrade(trade: TradeRow): Promise<number> {
    const result = (this.db as Record<string, Function>)
      .prepare(
        "INSERT INTO trades (symbol, side, entry_price, exit_price, quantity, entry_time, exit_time, net_pnl, pnl_pct, commission, strategy_id, entry_order, exit_order) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
      )
      .run(
        trade.symbol,
        trade.side,
        trade.entryPrice,
        trade.exitPrice,
        trade.quantity,
        trade.entryTime ?? null,
        trade.exitTime ?? null,
        trade.netPnl,
        trade.pnlPct ?? null,
        trade.commission ?? 0,
        trade.strategyId ?? null,
        trade.entryOrder ?? null,
        trade.exitOrder ?? null,
      );
    return (result as { lastInsertRowid: number }).lastInsertRowid;
  }

  async getTrades(opts?: { symbol?: string; limit?: number }): Promise<TradeRow[]> {
    let sql = "SELECT * FROM trades WHERE 1=1";
    const params: unknown[] = [];
    if (opts?.symbol) {
      sql += " AND symbol = ?";
      params.push(opts.symbol);
    }
    sql += " ORDER BY exit_time DESC";
    if (opts?.limit) {
      sql += " LIMIT ?";
      params.push(opts.limit);
    }
    const rows = (this.db as Record<string, Function>).prepare(sql).all(...params) as Array<
      Record<string, unknown>
    >;
    return rows.map((r) => ({
      id: r.id as number,
      symbol: r.symbol as string,
      side: r.side as string,
      entryPrice: r.entry_price as number,
      exitPrice: r.exit_price as number,
      quantity: r.quantity as number,
      entryTime: r.entry_time as string | undefined,
      exitTime: r.exit_time as string | undefined,
      netPnl: r.net_pnl as number,
      pnlPct: r.pnl_pct as number | undefined,
      commission: r.commission as number | undefined,
      strategyId: r.strategy_id as string | undefined,
      entryOrder: r.entry_order as string | undefined,
      exitOrder: r.exit_order as string | undefined,
    }));
  }

  // ── Equity ────────────────────────────────────────────────

  async upsertEquitySnapshot(row: EquityRow): Promise<void> {
    (this.db as Record<string, Function>)
      .prepare(`
      INSERT OR REPLACE INTO equity_snapshots (date, equity, cash, market_value, daily_pnl, position_count, benchmark)
      VALUES (?, ?, ?, ?, ?, ?, ?)
    `)
      .run(
        row.date,
        row.equity,
        row.cash,
        row.marketValue,
        row.dailyPnl,
        row.positionCount,
        row.benchmark ?? null,
      );
  }

  async getEquitySnapshots(opts?: {
    start?: string;
    end?: string;
    limit?: number;
  }): Promise<EquityRow[]> {
    let sql = "SELECT * FROM equity_snapshots WHERE 1=1";
    const params: unknown[] = [];
    if (opts?.start) {
      sql += " AND date >= ?";
      params.push(opts.start);
    }
    if (opts?.end) {
      sql += " AND date <= ?";
      params.push(opts.end);
    }
    sql += " ORDER BY date ASC";
    if (opts?.limit) {
      sql += " LIMIT ?";
      params.push(opts.limit);
    }
    const rows = (this.db as Record<string, Function>).prepare(sql).all(...params) as Array<
      Record<string, unknown>
    >;
    return rows.map((r) => ({
      date: r.date as string,
      equity: r.equity as number,
      cash: r.cash as number,
      marketValue: r.market_value as number,
      dailyPnl: r.daily_pnl as number,
      positionCount: r.position_count as number,
      benchmark: r.benchmark as number | undefined,
    }));
  }

  // ── Trade plans ──────────────────────────────────────────

  private mapTradePlanRow(r: Record<string, unknown>): TradePlanRow {
    return {
      id: r.id as string,
      symbol: r.symbol as string,
      direction: r.direction as "buy" | "sell",
      planPrice: r.plan_price as number,
      stopPrice: r.stop_price as number,
      targetPrice: r.target_price as number,
      positionRatio: r.position_ratio as number,
      reason: r.reason as string,
      status: r.status as "pending" | "executed" | "cancelled",
      date: r.date as string,
      linkedOrderId: r.linked_order_id as string | undefined,
      strategyId: r.strategy_id as string | undefined,
      executedPrice: r.executed_price as number | undefined,
      executedAt: r.executed_at as string | undefined,
      cancelReason: r.cancel_reason as string | undefined,
      createdAt: r.created_at as string,
      updatedAt: r.updated_at as string,
    };
  }

  async insertTradePlan(plan: TradePlanRow): Promise<void> {
    (this.db as Record<string, Function>)
      .prepare(`
      INSERT OR REPLACE INTO trade_plans
        (id, symbol, direction, plan_price, stop_price, target_price, position_ratio, reason, status, date, linked_order_id, strategy_id, executed_price, executed_at, cancel_reason, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `)
      .run(
        plan.id,
        plan.symbol,
        plan.direction,
        plan.planPrice,
        plan.stopPrice,
        plan.targetPrice,
        plan.positionRatio,
        plan.reason,
        plan.status,
        plan.date,
        plan.linkedOrderId ?? null,
        plan.strategyId ?? null,
        plan.executedPrice ?? null,
        plan.executedAt ?? null,
        plan.cancelReason ?? null,
        plan.createdAt,
        plan.updatedAt,
      );
  }

  async updateTradePlan(id: string, patch: Partial<TradePlanRow>): Promise<boolean> {
    const setClauses: string[] = [];
    const params: unknown[] = [];
    const fieldMap: Record<string, string> = {
      planPrice: "plan_price",
      stopPrice: "stop_price",
      targetPrice: "target_price",
      positionRatio: "position_ratio",
      reason: "reason",
      direction: "direction",
      status: "status",
      linkedOrderId: "linked_order_id",
      strategyId: "strategy_id",
      executedPrice: "executed_price",
      executedAt: "executed_at",
      cancelReason: "cancel_reason",
    };
    for (const [key, col] of Object.entries(fieldMap)) {
      if ((patch as Record<string, unknown>)[key] !== undefined) {
        setClauses.push(`${col} = ?`);
        params.push((patch as Record<string, unknown>)[key]);
      }
    }
    if (setClauses.length === 0) return false;
    setClauses.push("updated_at = datetime('now')");
    params.push(id);
    const result = (this.db as Record<string, Function>)
      .prepare(`UPDATE trade_plans SET ${setClauses.join(", ")} WHERE id = ?`)
      .run(...params) as { changes: number };
    return result.changes > 0;
  }

  async deleteTradePlan(id: string): Promise<boolean> {
    const result = (this.db as Record<string, Function>)
      .prepare("DELETE FROM trade_plans WHERE id = ?")
      .run(id) as { changes: number };
    return result.changes > 0;
  }

  async getTradePlan(id: string): Promise<TradePlanRow | undefined> {
    const row = (this.db as Record<string, Function>)
      .prepare("SELECT * FROM trade_plans WHERE id = ?")
      .get(id) as Record<string, unknown> | undefined;
    return row ? this.mapTradePlanRow(row) : undefined;
  }

  async getTradePlans(opts?: {
    symbol?: string;
    date?: string;
    status?: string;
    limit?: number;
  }): Promise<TradePlanRow[]> {
    let sql = "SELECT * FROM trade_plans WHERE 1=1";
    const params: unknown[] = [];
    if (opts?.symbol) {
      sql += " AND symbol = ?";
      params.push(opts.symbol);
    }
    if (opts?.date) {
      sql += " AND date = ?";
      params.push(opts.date);
    }
    if (opts?.status) {
      sql += " AND status = ?";
      params.push(opts.status);
    }
    sql += " ORDER BY created_at DESC";
    if (opts?.limit) {
      sql += " LIMIT ?";
      params.push(opts.limit);
    }
    const rows = (this.db as Record<string, Function>).prepare(sql).all(...params) as Array<
      Record<string, unknown>
    >;
    return rows.map((r) => this.mapTradePlanRow(r));
  }

  async getTradePlanCount(): Promise<{ total: number; pending: number; executed: number; cancelled: number }> {
    const row = (this.db as Record<string, Function>)
      .prepare(`
        SELECT
          COUNT(*) as total,
          SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
          SUM(CASE WHEN status = 'executed' THEN 1 ELSE 0 END) as executed,
          SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) as cancelled
        FROM trade_plans
      `)
      .get() as Record<string, number>;
    return {
      total: row.total ?? 0,
      pending: row.pending ?? 0,
      executed: row.executed ?? 0,
      cancelled: row.cancelled ?? 0,
    };
  }

  // ── Lifecycle ─────────────────────────────────────────────

  async close(): Promise<void> {
    if (this.db) {
      (this.db as Record<string, Function>).close();
      this.db = null;
    }
  }
}

// ── Factory ──────────────────────────────────────────────────

/**
 * Create a storage instance. Tries SQLite first, falls back to in-memory.
 * @param dataDir - Directory for data files
 * @param preferSqlite - Whether to try SQLite first (default: true)
 */
export async function createStorage(
  dataDir: string | undefined,
  preferSqlite = true,
): Promise<QuantStorage> {
  if (preferSqlite && dataDir) {
    try {
      const storage = new SqliteStorage(path.join(dataDir, "quant-trading.db"));
      await storage.initialize();
      return storage;
    } catch {
      // Fall through to in-memory
    }
  }

  const persistPath = dataDir ? path.join(dataDir, "quant-storage.json") : null;
  const storage = new InMemoryStorage(persistPath);
  await storage.initialize();
  return storage;
}
