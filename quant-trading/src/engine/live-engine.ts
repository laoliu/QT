/**
 * LiveEngine — the core real-time trading service.
 *
 * State machine:
 *   stopped → starting → running ⇄ paused → stopped
 *                 ↓ (error)
 *               error → stopped
 *
 * Responsibilities:
 *  - Manages the Python live_engine.py persistent process
 *  - Connects/disconnects the broker adapter
 *  - Subscribes to real-time quotes and feeds bars to Python strategies
 *  - Collects signals from Python and routes them to OrderManager
 *  - Polls live positions & account every minute
 *  - Emits a daily P&L summary on market close
 *
 * Registered as an OpenClawPluginService via api.registerService().
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { OpenClawPluginService } from "openclaw/plugin-sdk/core";
import { PersistentPythonProcess } from "../bridge/persistent-python.js";
import type { IBrokerAdapter } from "../broker/adapter.js";
import { createBrokerAdapter } from "../broker/index.js";
import { DataStalenessMonitor } from "../services/data-staleness-monitor.js";
import type { StalenessAlertCallback } from "../services/data-staleness-monitor.js";
import { isCnMarketOpen } from "../broker/paper-broker.js";
import { tryNotify } from "../services/notify-helper.js";
import { OrderManager } from "../services/order-manager.js";
import type {
  ActiveLiveStrategy,
  BrokerAccount,
  ConnectionHealth,
  LiveEngineStatus,
  LivePosition,
  QuantTradingConfig,
  QuoteSnapshot,
  TradeRecord,
  TradingSignal,
} from "../types.js";
import { LiveStrategyRegistry } from "./live-strategy-registry.js";

// ── Re-export status type for callers ────────────────────────────
export type { LiveEngineStatus };

// ── Raw signal from Python ────────────────────────────────────────
type RawSignal = {
  timestamp: number;
  symbol: string;
  direction: "buy" | "sell" | "short" | "cover";
  price: number;
  quantity?: number;
  reason?: string;
  confidence?: number;
  strategy_id: string;
  live_strategy_id: string;
};

export class LiveEngine implements OpenClawPluginService {
  readonly id = "quant-live-engine";

  private cfg: QuantTradingConfig;
  private api: OpenClawPluginApi;
  private status: LiveEngineStatus = "stopped";
  private proc: PersistentPythonProcess | null = null;
  private orderMgr: OrderManager | null = null;
  private registry: LiveStrategyRegistry;
  /** Active broker adapter instance — created once in start(), destroyed in stop() */
  private brokerAdapter: IBrokerAdapter | null = null;

  // Cached account / position state (refreshed every minute)
  private lastAccount: BrokerAccount | null = null;
  private lastPositions: LivePosition[] = [];
  private syncInterval: ReturnType<typeof setInterval> | null = null;

  // Quote unsubscribe callbacks
  private unsubscribeQuotes: (() => void)[] = [];

  // Data staleness monitor
  private stalenessMonitor: DataStalenessMonitor | null = null;

  // ── Connection health tracking ──────────────────────────────
  private consecutiveFailures = 0;
  private lastSuccessfulSync: Date | null = null;
  private lastSyncError: string | null = null;
  private reconnectAttempts = 0;
  /** Number of consecutive failures before marking "degraded" */
  private readonly degradedThreshold = 3;
  /** Number of consecutive failures before attempting auto-reconnect */
  private readonly reconnectThreshold = 5;
  /** Maximum auto-reconnect attempts before entering error state */
  private readonly maxReconnectAttempts = 3;
  /** In error state, attempt reconnect every N sync cycles (periodic recovery) */
  private readonly errorReconnectInterval = 5;

  /** Optional callback for staleness alerts → AlertStore */
  private stalenessAlertCb: StalenessAlertCallback | undefined;
  /** Optional callback for connection health alerts → AlertStore */
  private connectionAlertCb:
    | ((severity: "warning" | "critical" | "resolved", message: string) => void)
    | undefined;
  /** Optional callback for engine status changes → EventBus */
  private statusChangeCb: ((prev: LiveEngineStatus, next: LiveEngineStatus) => void) | undefined;
  /** Optional callback for trading signals → EventBus */
  private signalCb:
    | ((signal: TradingSignal, result: { ok: boolean; reason?: string; orderId?: string }) => void)
    | undefined;
  /** Optional callback for account sync → EquityHistory */
  private accountSyncCb: ((account: BrokerAccount, positions: LivePosition[]) => void) | undefined;
  /** Optional callback for completed trades → StrategyHealthMonitor */
  private tradeCb: ((trade: TradeRecord) => void) | undefined;
  /** Optional callback when the watched-symbol set changes. */
  private watchedSymbolsCb: ((symbols: string[]) => void) | undefined;
  /** Optional callback to detect current market regime (for auto-switch). */
  private regimeFn: (() => Promise<string | null>) | undefined;
  /** Last detected market regime. */
  private lastRegime: string | null = null;
  /** Counter for regime checks (check every 10 syncs = ~10 min). */
  private regimeCheckCounter = 0;

  constructor(cfg: QuantTradingConfig, api: OpenClawPluginApi) {
    this.cfg = cfg;
    this.api = api;
    this.registry = new LiveStrategyRegistry(cfg);
  }

  /** Set a callback that fires on every staleness alert (wire to AlertStore). */
  setStalenessAlertCallback(cb: StalenessAlertCallback): void {
    this.stalenessAlertCb = cb;
  }

  /** Set a callback that fires on connection health alerts (wire to AlertStore). */
  setConnectionAlertCallback(
    cb: (severity: "warning" | "critical" | "resolved", message: string) => void,
  ): void {
    this.connectionAlertCb = cb;
  }

  /** Set a callback that fires when the engine status changes (wire to EventBus). */
  setStatusChangeCallback(cb: (prev: LiveEngineStatus, next: LiveEngineStatus) => void): void {
    this.statusChangeCb = cb;
  }

  /** Set a callback that fires when a trading signal is processed (wire to EventBus). */
  setSignalCallback(
    cb: (signal: TradingSignal, result: { ok: boolean; reason?: string; orderId?: string }) => void,
  ): void {
    this.signalCb = cb;
  }

  /** Set a callback that fires after each successful account/position sync (wire to EquityHistory). */
  setAccountSyncCallback(cb: (account: BrokerAccount, positions: LivePosition[]) => void): void {
    this.accountSyncCb = cb;
  }

  /** Set a callback that fires when a trade completes (wire to StrategyHealthMonitor). */
  setTradeCallback(cb: (trade: TradeRecord) => void): void {
    this.tradeCb = cb;
  }

  /** Set a callback that fires when active strategy symbols change. */
  setWatchedSymbolsCallback(cb: (symbols: string[]) => void): void {
    this.watchedSymbolsCb = cb;
  }

  /** Set a callback to detect market regime for auto-switch. */
  setRegimeCallback(fn: () => Promise<string | null>): void {
    this.regimeFn = fn;
  }

  /** Transition engine status with callback notification. */
  private setStatus(next: LiveEngineStatus): void {
    const prev = this.status;
    if (prev !== next) {
      this.status = next;
      this.statusChangeCb?.(prev, next);
    }
  }

  // ── OpenClawPluginService interface ───────────────────────────

  async start(): Promise<void> {
    if (this.status === "running" || this.status === "starting") return;
    this.log("Live engine starting…");
    this.setStatus("starting");

    try {
      await this.registry.load();
      const brokerAdapter = createBrokerAdapter(this.cfg);
      this.brokerAdapter = brokerAdapter;

      // Connect broker
      await brokerAdapter.connect();
      this.log(`Broker connected: ${brokerAdapter.brokerType}`);

      // Create order manager
      const mgr = new OrderManager(this.cfg, brokerAdapter, this.api);
      if (this.tradeCb) mgr.setTradeCallback(this.tradeCb);
      this.orderMgr = mgr;

      // Start Python live_engine.py
      const proc = new PersistentPythonProcess("engines.live_engine", this.cfg);
      this.proc = proc;
      proc.onEvent((event, data) => void this.handlePythonEvent(event, data));
      await proc.start();

      // Register all enabled strategies with the Python engine
      const active = this.registry.list(true);
      this.emitWatchedSymbols(active);
      for (const ls of active) {
        await this.registerStrategyInPython(ls, proc);
      }

      // Subscribe to quotes for all watched symbols
      const allSymbols = [...new Set(active.flatMap((ls) => ls.symbols))];
      if (allSymbols.length > 0) {
        const unsub = brokerAdapter.subscribeQuote(
          allSymbols,
          (q) => void this.handleQuote(q, proc),
        );
        this.unsubscribeQuotes.push(unsub);
      }

      // Start periodic account/position sync
      this.syncInterval = setInterval(() => void this.syncState(), 60_000);
      await this.syncState(); // immediate first sync

      // Start data staleness monitor
      this.stalenessMonitor = new DataStalenessMonitor(
        { ...this.cfg.dataStaleness, isMarketOpen: isCnMarketOpen },
        this.api.logger,
        { notify: (msg: string) => tryNotify(this.api, msg) },
        this.stalenessAlertCb,
      );
      if (allSymbols.length > 0) {
        this.stalenessMonitor.registerSymbols(allSymbols);
      }
      // Also register symbols from broker positions so staleness tracks them
      if (this.lastPositions.length > 0) {
        const posSymbols = this.lastPositions.map((p) => p.symbol);
        const extra = posSymbols.filter((s) => !allSymbols.includes(s));
        if (extra.length > 0) {
          this.stalenessMonitor.registerSymbols(extra);
        }
      }
      this.stalenessMonitor.start();

      this.setStatus("running");
      this.log("Live engine running");
    } catch (err) {
      this.lastSyncError = (err as Error).message;
      this.setStatus("error");
      this.log(`Live engine failed to start: ${(err as Error).message}`, "error");
      await this.teardown();
    }
  }

  async stop(): Promise<void> {
    if (this.status === "stopped") return;
    this.log("Live engine stopping…");

    // Cancel all open orders before disconnecting
    try {
      await this.orderMgr?.cancelAll();
    } catch {
      // best-effort
    }

    await this.teardown();
    this.setStatus("stopped");
    this.log("Live engine stopped");
  }

  // ── Public API (called by AI tools / CLI) ────────────────────

  getStatus(): LiveEngineStatus {
    return this.status;
  }

  pause(): void {
    if (this.status === "running") {
      this.setStatus("paused");
      this.log("Live engine paused (quotes continue; no new orders)");
    }
  }

  resume(): void {
    if (this.status === "paused") {
      this.setStatus("running");
      this.log("Live engine resumed");
    }
  }

  get strategyRegistry(): LiveStrategyRegistry {
    return this.registry;
  }

  get orderManager(): OrderManager | null {
    return this.orderMgr;
  }

  /** Expose broker adapter for option exercise and other broker-specific ops */
  get broker(): IBrokerAdapter | null {
    return this.brokerAdapter;
  }

  getAccount(): BrokerAccount | null {
    return this.lastAccount;
  }

  getPositions(): LivePosition[] {
    return this.lastPositions;
  }

  /**
   * Force an immediate broker sync and return fresh account + positions.
   * Unlike getAccount()/getPositions() which read from cache, this
   * actively queries the broker adapter.
   */
  async syncNow(): Promise<{ account: BrokerAccount | null; positions: LivePosition[] }> {
    if (!this.brokerAdapter) {
      return { account: this.lastAccount, positions: this.lastPositions };
    }
    try {
      const [account, positions] = await Promise.all([
        this.brokerAdapter.queryAccount(),
        this.brokerAdapter.queryPositions(),
      ]);
      this.lastAccount = account;
      this.lastPositions = positions;
      this.accountSyncCb?.(account, positions);
      return { account, positions };
    } catch (err) {
      this.log(`syncNow failed: ${(err as Error).message}`, "error");
      return { account: this.lastAccount, positions: this.lastPositions };
    }
  }

  /**
   * Add a new live strategy and start feeding it bars immediately.
   */
  async addStrategy(
    strategyId: string,
    symbols: string[],
    parameters?: Record<string, unknown>,
    extras?: {
      timeWindows?: string[];
      interval?: string;
      confirmationBars?: number;
      regimes?: string[];
    },
  ): Promise<ActiveLiveStrategy> {
    const entry = await this.registry.add(strategyId, symbols, {
      parameters,
      timeWindows: extras?.timeWindows,
      interval: extras?.interval as import("../types.js").KlineInterval | undefined,
      confirmationBars: extras?.confirmationBars,
      regimes: extras?.regimes,
    });
    this.emitWatchedSymbols();

    if (this.proc && this.status === "running" && this.brokerAdapter) {
      await this.registerStrategyInPython(entry, this.proc);

      // Subscribe quotes for any new symbols not already subscribed
      const unsub = this.brokerAdapter.subscribeQuote(
        symbols,
        (q) => void this.handleQuote(q, this.proc!),
      );
      this.unsubscribeQuotes.push(unsub);
    }

    return entry;
  }

  /**
   * Remove a live strategy. Stops feeding it bars.
   */
  async removeStrategy(id: string): Promise<boolean> {
    if (this.proc) {
      try {
        await this.proc.call("remove_strategy", { id });
      } catch {
        // ignore if proc isn't alive
      }
    }
    const removed = await this.registry.remove(id);
    if (removed) this.emitWatchedSymbols();
    return removed;
  }

  // ── Private ──────────────────────────────────────────────────

  private async registerStrategyInPython(
    ls: ActiveLiveStrategy,
    proc: PersistentPythonProcess,
  ): Promise<void> {
    await proc.call("add_strategy", {
      id: ls.id,
      strategy_id: ls.strategyId,
      symbols: ls.symbols,
      parameters: ls.parameters ?? {},
    });
  }

  private emitWatchedSymbols(strategies?: ActiveLiveStrategy[]): void {
    if (!this.watchedSymbolsCb) return;
    const active = strategies ?? this.registry.list(true);
    const symbols = [...new Set(active.flatMap((strategy) => strategy.symbols))];
    this.watchedSymbolsCb(symbols);
  }

  /**
   * Check the current market regime and auto-pause/resume strategies
   * that have regime preferences set.
   */
  private async applyRegimeSwitch(): Promise<void> {
    if (!this.regimeFn) return;
    const regime = await this.regimeFn();
    if (!regime) return;

    if (regime !== this.lastRegime) {
      this.log(`市场环境检测: ${this.lastRegime ?? "(初始)"} → ${regime}`);
      this.lastRegime = regime;
    }

    for (const ls of this.registry.list()) {
      if (!ls.regimes || ls.regimes.length === 0) continue;

      const matches = ls.regimes.includes(regime);
      if (!matches && !ls.regimePaused) {
        // Pause: regime no longer matches
        ls.regimePaused = true;
        this.log(
          `📉 策略 ${ls.strategyId} (${ls.id}) 因市场环境 "${regime}" 不匹配自动暂停 (允许: ${ls.regimes.join(",")})`,
        );
        void tryNotify(this.api, `📉 策略 ${ls.strategyId} 因市场环境 "${regime}" 自动暂停`);
      } else if (matches && ls.regimePaused) {
        // Resume: regime matches again
        ls.regimePaused = false;
        this.log(`📈 策略 ${ls.strategyId} (${ls.id}) 因市场环境 "${regime}" 匹配已自动恢复`);
        void tryNotify(this.api, `📈 策略 ${ls.strategyId} 因市场环境 "${regime}" 自动恢复运行`);
      }
    }
  }

  private async handlePythonEvent(event: string, data: unknown): Promise<void> {
    if (event === "signal" && this.status === "running" && this.orderMgr) {
      const raw = data as RawSignal;
      // Find matching live strategy for per-strategy risk overrides
      const ls = this.registry.list().find((s) => s.id === raw.live_strategy_id);

      const signal: TradingSignal = {
        timestamp: raw.timestamp,
        symbol: raw.symbol,
        direction: raw.direction,
        price: raw.price,
        quantity: raw.quantity,
        reason: raw.reason,
        confidence: raw.confidence,
        strategyId: raw.strategy_id,
      };

      this.log(`信号: ${raw.direction} ${raw.symbol} @ ¥${raw.price} | ${raw.reason ?? ""}`);

      // Skip regime-paused strategies
      if (ls?.regimePaused) {
        this.log(
          `信号跳过 (环境暂停): ${ls.strategyId} [${ls.id}] 当前环境=${this.lastRegime ?? "unknown"}`,
        );
        return;
      }

      // Per-strategy risk overrides
      const riskOverrides =
        ls?.maxPositionPct != null || ls?.maxOrderValue != null
          ? { maxPositionPct: ls?.maxPositionPct, maxOrderValue: ls?.maxOrderValue }
          : undefined;

      const result = await this.orderMgr.submitFromSignal(signal, riskOverrides);
      if (!result.ok) {
        this.log(`信号被拦截: ${result.reason}`, "warn");
      }

      // Notify signal callback (EventBus wiring)
      this.signalCb?.(signal, {
        ok: result.ok,
        reason: result.ok ? undefined : result.reason,
        orderId: result.ok ? result.order.orderId : undefined,
      });
    } else if (event === "error") {
      const d = data as { live_strategy_id?: string; error?: string };
      this.log(`策略错误 [${d.live_strategy_id}]: ${d.error}`, "error");
    }
  }

  private async handleQuote(quote: QuoteSnapshot, proc: PersistentPythonProcess): Promise<void> {
    // Track quote freshness for staleness monitoring
    this.stalenessMonitor?.recordQuote(quote.symbol.code);

    // Convert snapshot to bar and feed to Python engine
    const bar = {
      symbol: quote.symbol.code,
      timestamp: quote.timestamp,
      open: quote.open,
      high: quote.high,
      low: quote.low,
      close: quote.price,
      volume: quote.volume,
      amount: quote.amount,
      interval: "tick",
    };
    try {
      await proc.call("feed_bar", { bar });
    } catch {
      // non-critical; next quote will retry
    }
  }

  private async syncState(): Promise<void> {
    if (!this.brokerAdapter) return;
    try {
      // Use ping() if available, otherwise fall back to queryAccount
      if (this.brokerAdapter.ping) {
        const alive = await this.brokerAdapter.ping();
        if (!alive) throw new Error("Broker ping returned false");
      }

      const [account, positions] = await Promise.all([
        this.brokerAdapter.queryAccount(),
        this.brokerAdapter.queryPositions(),
      ]);
      this.lastAccount = account;
      this.lastPositions = positions;

      // Notify account sync callback (EquityHistory recording)
      this.accountSyncCb?.(account, positions);

      // Cancel expired orders (limit orders with expiresAt in the past)
      if (this.orderMgr) {
        void this.orderMgr.cancelExpiredOrders().catch(() => {});
      }

      // Regime auto-switch (check every 10 syncs ≈ 10 min)
      this.regimeCheckCounter++;
      if (this.regimeFn && this.regimeCheckCounter >= 10) {
        this.regimeCheckCounter = 0;
        void this.applyRegimeSwitch().catch(() => {});
      }

      // Reset failure tracking on success
      if (this.consecutiveFailures > 0) {
        this.log(`Broker connection restored after ${this.consecutiveFailures} failure(s)`);
      }
      this.consecutiveFailures = 0;
      this.reconnectAttempts = 0;
      this.lastSuccessfulSync = new Date();
      this.lastSyncError = null;

      // Auto-recover from error state when broker is healthy again
      if (this.status === "error") {
        this.setStatus("running");
        const msg = "Broker connection recovered — 引擎已自动恢复运行";
        this.log(msg);
        void tryNotify(this.api, `🟢 ${msg}`);
        this.connectionAlertCb?.("resolved", msg);
      }
    } catch (err) {
      this.consecutiveFailures++;
      this.lastSyncError = (err as Error).message;
      this.log(`账户同步失败 (${this.consecutiveFailures}x): ${(err as Error).message}`, "warn");

      // Degraded notification (first time crossing threshold)
      if (this.consecutiveFailures === this.degradedThreshold) {
        const msg = "Broker connection degraded — 连续同步失败";
        this.log("Broker connection degraded — multiple sync failures", "warn");
        void tryNotify(this.api, `⚠️ ${msg}`);
        this.connectionAlertCb?.("warning", msg);
      }

      // Auto-reconnect attempt (initial phase: before entering error state)
      if (
        this.consecutiveFailures >= this.reconnectThreshold &&
        this.reconnectAttempts < this.maxReconnectAttempts &&
        this.status !== "error"
      ) {
        await this.attemptReconnect();
      }

      // If exhausted reconnect attempts, transition to error
      if (
        this.consecutiveFailures >= this.reconnectThreshold + this.maxReconnectAttempts &&
        this.status === "running"
      ) {
        this.setStatus("error");
        const msg = "Broker connection lost — 引擎已进入错误状态";
        this.log("Broker connection lost — engine set to error state", "error");
        void tryNotify(this.api, `🔴 ${msg}`);
        this.connectionAlertCb?.("critical", msg);
      }

      // Periodic reconnect in error state: try every N sync cycles to recover
      if (this.status === "error" && this.consecutiveFailures % this.errorReconnectInterval === 0) {
        this.log(
          `Periodic reconnect attempt in error state (failures=${this.consecutiveFailures})…`,
        );
        await this.attemptReconnect();
      }
    }
  }

  /**
   * Attempt to reconnect the broker adapter.
   * Disconnects the current adapter and creates a new connection.
   */
  private async attemptReconnect(): Promise<void> {
    this.reconnectAttempts++;
    this.log(
      `Attempting broker reconnect (${this.reconnectAttempts}/${this.maxReconnectAttempts})…`,
    );

    try {
      // Disconnect current adapter
      if (this.brokerAdapter?.connected) {
        try {
          await this.brokerAdapter.disconnect();
        } catch {
          /* ignore disconnect errors */
        }
      }

      // Reconnect
      await this.brokerAdapter!.connect();
      this.log("Broker reconnected successfully");
      this.consecutiveFailures = 0;
      this.lastSyncError = null;
    } catch (err) {
      this.log(`Broker reconnect failed: ${(err as Error).message}`, "error");
    }
  }

  /**
   * Get broker connection health info for monitoring / health endpoint.
   */
  getConnectionHealth(): ConnectionHealth {
    if (!this.brokerAdapter || this.status === "stopped") {
      return {
        status: "disconnected",
        consecutiveFailures: 0,
        lastSuccessfulSync: null,
        lastError: null,
        reconnectAttempts: 0,
      };
    }

    let healthStatus: ConnectionHealth["status"] = "connected";
    if (this.consecutiveFailures >= this.reconnectThreshold) {
      healthStatus = "disconnected";
    } else if (this.consecutiveFailures >= this.degradedThreshold) {
      healthStatus = "degraded";
    }

    return {
      status: healthStatus,
      consecutiveFailures: this.consecutiveFailures,
      lastSuccessfulSync: this.lastSuccessfulSync?.toISOString() ?? null,
      lastError: this.lastSyncError,
      reconnectAttempts: this.reconnectAttempts,
    };
  }

  /**
   * Get data staleness report for monitoring / health endpoint.
   */
  getDataStalenessReport() {
    return this.stalenessMonitor?.getReport() ?? null;
  }

  private async teardown(): Promise<void> {
    // Stop staleness monitor
    this.stalenessMonitor?.stop();
    this.stalenessMonitor = null;

    // Cleanup quote subscriptions
    for (const unsub of this.unsubscribeQuotes) unsub();
    this.unsubscribeQuotes = [];

    // Stop sync interval
    if (this.syncInterval) {
      clearInterval(this.syncInterval);
      this.syncInterval = null;
    }

    // Stop Python process
    if (this.proc) {
      this.proc.stop();
      this.proc = null;
    }

    // Disconnect broker
    if (this.brokerAdapter?.connected) {
      try {
        await this.brokerAdapter.disconnect();
      } catch (err) {
        this.log(`Broker disconnect error: ${(err as Error).message}`, "warn");
      }
    }
    this.brokerAdapter = null;
    this.orderMgr = null;
  }

  private log(msg: string, level: "info" | "warn" | "error" = "info"): void {
    const prefix = "[LiveEngine]";
    if (level === "warn") {
      this.api.logger.warn(`${prefix} ${msg}`);
    } else if (level === "error") {
      this.api.logger.error(`${prefix} ${msg}`);
    } else {
      this.api.logger.info(`${prefix} ${msg}`);
    }
  }
}
