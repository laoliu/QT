/**
 * DataStalenessMonitor — tracks the freshness of live market data feeds
 * and alerts when data hasn't been received within configurable thresholds.
 *
 * Works with the push-based quote subscription in LiveEngine: each incoming
 * QuoteSnapshot updates a per-symbol "last seen" timestamp.  A periodic
 * check compares current time against those timestamps to detect staleness.
 *
 * Alert levels:
 *   - warning:  No data for a symbol in `warningThresholdMs` (default 60s)
 *   - critical: No data for a symbol in `criticalThresholdMs` (default 300s)
 *               or *all* subscribed symbols are stale
 */

// ── Config ───────────────────────────────────────────────────────

export type DataStalenessConfig = {
  /** Enable/disable the monitor (default true) */
  enabled?: boolean;
  /** How often to check staleness (ms, default 30_000) */
  checkIntervalMs?: number;
  /** Warning threshold per symbol (ms, default 60_000) */
  warningThresholdMs?: number;
  /** Critical threshold per symbol (ms, default 300_000) */
  criticalThresholdMs?: number;
  /**
   * Optional callback that returns true when the market is open.
   * When provided, staleness checks are skipped outside trading hours
   * to avoid false alarms on weekends / holidays / after-hours.
   */
  isMarketOpen?: () => boolean;
};

const DEFAULTS: Omit<Required<DataStalenessConfig>, "isMarketOpen"> & { isMarketOpen?: () => boolean } = {
  enabled: true,
  checkIntervalMs: 30_000,
  warningThresholdMs: 60_000,
  criticalThresholdMs: 300_000,
};

// ── Staleness status per symbol ──────────────────────────────────

export type SymbolStaleness = {
  symbol: string;
  lastReceivedAt: number; // unix ms (Date.now())
  ageMs: number;
  status: "fresh" | "warning" | "critical";
};

export type StalenessReport = {
  checkedAt: string;
  totalSymbols: number;
  freshCount: number;
  warningCount: number;
  criticalCount: number;
  overallStatus: "fresh" | "warning" | "critical" | "no_data";
  symbols: SymbolStaleness[];
};

// ── Logger interface ────────────────────────────────────────────

export interface StalenessLogger {
  info(...args: unknown[]): void;
  warn(...args: unknown[]): void;
  error(...args: unknown[]): void;
}

export interface StalenessNotifier {
  notify?(message: string): void;
}

/** Callback fired for each staleness alert — used to wire to AlertStore. */
export type StalenessAlertCallback = (
  severity: "warning" | "critical",
  message: string,
  symbol: string,
) => void;

// ── Monitor class ────────────────────────────────────────────────

export class DataStalenessMonitor {
  private readonly config: DataStalenessConfig & { enabled: boolean; checkIntervalMs: number; warningThresholdMs: number; criticalThresholdMs: number };
  private readonly logger: StalenessLogger;
  private readonly notifier: StalenessNotifier;
  private readonly onAlert: StalenessAlertCallback | undefined;

  /** Per-symbol last-received timestamps (Date.now()) */
  private readonly lastReceived = new Map<string, number>();

  /** Track which alerts have already fired to avoid spam */
  private readonly firedAlerts = new Set<string>();

  private checkInterval: ReturnType<typeof setInterval> | null = null;

  constructor(
    config: DataStalenessConfig | undefined,
    logger: StalenessLogger,
    notifier: StalenessNotifier = {},
    onAlert?: StalenessAlertCallback,
  ) {
    this.config = { ...DEFAULTS, ...(config ?? {}) };
    this.logger = logger;
    this.notifier = notifier;
    this.onAlert = onAlert;
  }

  // ── Lifecycle ─────────────────────────────────────────────────

  start(): void {
    if (!this.config.enabled) return;
    if (this.checkInterval) return; // already running
    this.checkInterval = setInterval(() => this.check(), this.config.checkIntervalMs);
    this.logger.info(
      `DataStalenessMonitor started (check every ${this.config.checkIntervalMs}ms, ` +
        `warn=${this.config.warningThresholdMs}ms, critical=${this.config.criticalThresholdMs}ms)`,
    );
  }

  stop(): void {
    if (this.checkInterval) {
      clearInterval(this.checkInterval);
      this.checkInterval = null;
    }
    this.firedAlerts.clear();
    this.logger.info("DataStalenessMonitor stopped");
  }

  // ── Data feed ─────────────────────────────────────────────────

  /**
   * Record that a quote was received for a symbol.
   * Call this from LiveEngine.handleQuote() on each incoming quote.
   */
  recordQuote(symbol: string): void {
    this.lastReceived.set(symbol, Date.now());

    // If a warning/critical alert was previously fired, clear it on fresh data
    const warnKey = `warn:${symbol}`;
    const critKey = `crit:${symbol}`;
    if (this.firedAlerts.has(warnKey) || this.firedAlerts.has(critKey)) {
      this.firedAlerts.delete(warnKey);
      this.firedAlerts.delete(critKey);
      // Any fresh data means "all critical" no longer applies
      this.firedAlerts.delete("all_critical");
      this.logger.info(`Data feed restored for ${symbol}`);
    }
  }

  /**
   * Register symbols that are expected to have active data feeds.
   * Initialises entries without a received timestamp so they appear
   * as "no_data" on the first check.
   */
  registerSymbols(symbols: string[]): void {
    for (const s of symbols) {
      if (!this.lastReceived.has(s)) {
        this.lastReceived.set(s, 0); // 0 = never received
      }
    }
  }

  // ── Reporting ─────────────────────────────────────────────────

  /**
   * Generate a point-in-time staleness report for all tracked symbols.
   */
  getReport(): StalenessReport {
    const now = Date.now();
    const symbols: SymbolStaleness[] = [];

    for (const [symbol, ts] of this.lastReceived) {
      const ageMs = ts === 0 ? Infinity : now - ts;
      let status: SymbolStaleness["status"] = "fresh";

      if (ageMs >= this.config.criticalThresholdMs) {
        status = "critical";
      } else if (ageMs >= this.config.warningThresholdMs) {
        status = "warning";
      }

      symbols.push({ symbol, lastReceivedAt: ts, ageMs: ts === 0 ? Infinity : ageMs, status });
    }

    const freshCount = symbols.filter((s) => s.status === "fresh").length;
    const warningCount = symbols.filter((s) => s.status === "warning").length;
    const criticalCount = symbols.filter((s) => s.status === "critical").length;

    let overallStatus: StalenessReport["overallStatus"] = "fresh";
    if (symbols.length === 0) {
      overallStatus = "no_data";
    } else if (criticalCount > 0) {
      overallStatus = "critical";
    } else if (warningCount > 0) {
      overallStatus = "warning";
    }

    return {
      checkedAt: new Date(now).toISOString(),
      totalSymbols: symbols.length,
      freshCount,
      warningCount,
      criticalCount,
      overallStatus,
      symbols,
    };
  }

  // ── Internal check ────────────────────────────────────────────

  /** Periodic staleness check — called by the interval timer. */
  check(): void {
    // Skip checks outside trading hours to avoid false alarms
    if (this.config.isMarketOpen && !this.config.isMarketOpen()) {
      // Only clear fired alerts when the full trading day is over (after 15:00)
      // so midday break (11:30-13:00) doesn't re-trigger the same alerts
      if (this.firedAlerts.size > 0 && isAfterCnClose()) {
        this.firedAlerts.clear();
      }
      return;
    }

    const report = this.getReport();

    for (const sym of report.symbols) {
      const warnKey = `warn:${sym.symbol}`;
      const critKey = `crit:${sym.symbol}`;

      // Grace period: skip alerts for symbols that have NEVER received data
      // within 120s of market open, to allow data feeds time to connect.
      if (sym.lastReceivedAt === 0 && this.isWithinMarketOpenGrace()) {
        continue;
      }

      if (sym.status === "critical" && !this.firedAlerts.has(critKey)) {
        this.firedAlerts.add(critKey);
        const msg = `🔴 数据中断: ${sym.symbol} 已 ${formatDuration(sym.ageMs)} 无行情数据`;
        this.logger.error(msg);
        this.notifier.notify?.(msg);
        this.onAlert?.("critical", msg, sym.symbol);
      } else if (sym.status === "warning" && !this.firedAlerts.has(warnKey)) {
        this.firedAlerts.add(warnKey);
        const msg = `⚠️ 数据延迟: ${sym.symbol} 已 ${formatDuration(sym.ageMs)} 无行情更新`;
        this.logger.warn(msg);
        this.notifier.notify?.(msg);
        this.onAlert?.("warning", msg, sym.symbol);
      }
    }

    // All symbols stale → extra critical alert
    if (
      report.totalSymbols > 0 &&
      report.criticalCount === report.totalSymbols &&
      !this.firedAlerts.has("all_critical")
    ) {
      this.firedAlerts.add("all_critical");
      const msg = `🚨 所有行情数据源中断 (${report.totalSymbols} 只标的无数据)`;
      this.logger.error(msg);
      this.notifier.notify?.(msg);
      this.onAlert?.("critical", msg, "*");
    }

    // Reset "all_critical" if at least one symbol is fresh
    if (report.freshCount > 0 && this.firedAlerts.has("all_critical")) {
      this.firedAlerts.delete("all_critical");
    }
  }
  // ── Grace period ─────────────────────────────────────────────

  /** Grace period (ms) after market open for "never received" symbols. */
  private static readonly MARKET_OPEN_GRACE_MS = 120_000;

  /**
   * Returns true if we are within the grace window after a CN market
   * session start (09:30 or 13:00).  During this window, symbols that
   * have *never* received a quote are not yet alarmed — the data feed
   * simply hasn't had time to deliver its first tick.
   */
  private isWithinMarketOpenGrace(): boolean {
    const now = new Date();
    const cst = new Date(now.getTime() + 8 * 60 * 60_000);
    const hmm = cst.getUTCHours() * 60 + cst.getUTCMinutes(); // minutes since midnight
    const morning = 9 * 60 + 30;  // 09:30
    const afternoon = 13 * 60;    // 13:00
    const graceMin = DataStalenessMonitor.MARKET_OPEN_GRACE_MS / 60_000;
    return (
      (hmm >= morning && hmm < morning + graceMin) ||
      (hmm >= afternoon && hmm < afternoon + graceMin)
    );
  }
}

// ── Helpers ──────────────────────────────────────────────────────

/** Returns true after 15:00 CST — i.e. the trading day is fully over. */
function isAfterCnClose(): boolean {
  const now = new Date();
  const cst = new Date(now.getTime() + 8 * 60 * 60 * 1000);
  const hm = cst.getUTCHours() * 100 + cst.getUTCMinutes();
  return hm > 1500;
}

function formatDuration(ms: number): string {
  if (ms < 0 || !Number.isFinite(ms)) return "从未收到数据";
  const secs = Math.floor(ms / 1000);
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m${secs % 60}s`;
  const hrs = Math.floor(mins / 60);
  return `${hrs}h${mins % 60}m`;
}
