import { randomUUID } from "node:crypto";
/**
 * AlertStore — persistent, queryable store for trading alerts.
 *
 * Stores alerts from all sources (PnL monitor, connection health,
 * data staleness, risk gate, etc.) with:
 *   - In-memory ring buffer for fast access
 *   - JSON file persistence for restart survival
 *   - Configurable retention (max entries, max age)
 *   - Query by severity, source, time range, and read status
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync, renameSync } from "node:fs";
import { dirname, join } from "node:path";

// ── Alert types ──────────────────────────────────────────────────

export type AlertSeverity = "info" | "warning" | "critical";

export type AlertSource =
  | "pnl_monitor"
  | "connection_health"
  | "data_staleness"
  | "risk_gate"
  | "order_manager"
  | "strategy"
  | "system"
  | "price_alert"
  | "signal";

export type Alert = {
  /** Unique alert id */
  id: string;
  /** When the alert was created (ISO 8601) */
  timestamp: string;
  /** Severity level */
  severity: AlertSeverity;
  /** Which subsystem generated the alert */
  source: AlertSource;
  /** Human-readable message */
  message: string;
  /** Whether the user has acknowledged the alert */
  acknowledged: boolean;
  /** Optional structured metadata */
  metadata?: Record<string, unknown>;
};

export type AlertQuery = {
  /** Filter by severity(ies) */
  severity?: AlertSeverity | AlertSeverity[];
  /** Filter by source(s) */
  source?: AlertSource | AlertSource[];
  /** Only alerts after this ISO timestamp */
  after?: string;
  /** Only alerts before this ISO timestamp */
  before?: string;
  /** Filter by acknowledged state */
  acknowledged?: boolean;
  /** Maximum number of results (default: 100) */
  limit?: number;
  /** Offset for pagination */
  offset?: number;
};

export type AlertStoreSummary = {
  total: number;
  unacknowledged: number;
  bySeverity: Record<AlertSeverity, number>;
  bySource: Partial<Record<AlertSource, number>>;
};

// ── Config ───────────────────────────────────────────────────────

export type AlertStoreConfig = {
  /** Maximum number of alerts to keep in memory (default: 1000) */
  maxAlerts?: number;
  /** Maximum age in ms before auto-pruning (default: 7 days) */
  maxAgeMs?: number;
  /** Path to the JSON persistence file. Null = no persistence. */
  persistPath?: string | null;
  /** How often to auto-save to disk in ms (default: 60_000). 0 = manual only. */
  autoSaveIntervalMs?: number;
};

const DEFAULTS: Required<AlertStoreConfig> = {
  maxAlerts: 1000,
  maxAgeMs: 7 * 24 * 60 * 60 * 1000, // 7 days
  persistPath: null,
  autoSaveIntervalMs: 60_000,
};

// ── Store implementation ────────────────────────────────────────

export class AlertStore {
  private readonly config: Required<AlertStoreConfig>;
  private alerts: Alert[] = [];
  private saveInterval: ReturnType<typeof setInterval> | null = null;
  private dirty = false;

  private generateId(): string {
    return `alert_${randomUUID().slice(0, 8)}`;
  }

  constructor(config?: AlertStoreConfig) {
    this.config = { ...DEFAULTS, ...(config ?? {}) };
  }

  // ── Lifecycle ─────────────────────────────────────────────────

  /** Load persisted alerts from disk and start auto-save if configured. */
  start(): void {
    this.loadFromDisk();
    if (this.config.autoSaveIntervalMs > 0 && this.config.persistPath) {
      this.saveInterval = setInterval(() => this.saveToDisk(), this.config.autoSaveIntervalMs);
    }
  }

  /** Save remaining alerts and stop auto-save. */
  stop(): void {
    if (this.saveInterval) {
      clearInterval(this.saveInterval);
      this.saveInterval = null;
    }
    this.saveToDisk();
  }

  // ── Write operations ──────────────────────────────────────────

  /** Add a new alert.  Returns the generated alert id. */
  add(
    severity: AlertSeverity,
    source: AlertSource,
    message: string,
    metadata?: Record<string, unknown>,
  ): string {
    const alert: Alert = {
      id: this.generateId(),
      timestamp: new Date().toISOString(),
      severity,
      source,
      message,
      acknowledged: false,
      metadata,
    };
    this.alerts.push(alert);
    this.dirty = true;
    this.prune();
    return alert.id;
  }

  /** Mark one alert as acknowledged. */
  acknowledge(id: string): boolean {
    const alert = this.alerts.find((a) => a.id === id);
    if (!alert) return false;
    alert.acknowledged = true;
    this.dirty = true;
    return true;
  }

  /** Mark all unacknowledged alerts as acknowledged. */
  acknowledgeAll(): number {
    let count = 0;
    for (const a of this.alerts) {
      if (!a.acknowledged) {
        a.acknowledged = true;
        count++;
      }
    }
    if (count > 0) this.dirty = true;
    return count;
  }

  /** Remove all alerts. */
  clear(): void {
    this.alerts = [];
    this.dirty = true;
  }

  // ── Read operations ───────────────────────────────────────────

  /** Query alerts with filters. Returns newest-first by default. */
  query(q: AlertQuery = {}): Alert[] {
    // Pre-compute filter values once
    const sevs = q.severity ? (Array.isArray(q.severity) ? q.severity : [q.severity]) : null;
    const srcs = q.source ? (Array.isArray(q.source) ? q.source : [q.source]) : null;
    const afterTs = q.after ? new Date(q.after).getTime() : 0;
    const beforeTs = q.before ? new Date(q.before).getTime() : Infinity;

    // Single-pass filter instead of 5 chained .filter() calls
    const results = this.alerts.filter((a) => {
      if (sevs && !sevs.includes(a.severity)) return false;
      if (srcs && !srcs.includes(a.source)) return false;
      const ts = new Date(a.timestamp).getTime();
      if (ts < afterTs || ts > beforeTs) return false;
      if (q.acknowledged !== undefined && a.acknowledged !== q.acknowledged) return false;
      return true;
    });

    // Sort newest first
    results.sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());

    // Pagination
    const offset = q.offset ?? 0;
    const limit = q.limit ?? 100;
    return results.slice(offset, offset + limit);
  }

  /** Get a single alert by id. */
  getById(id: string): Alert | undefined {
    return this.alerts.find((a) => a.id === id);
  }

  /** Get aggregate summary. */
  getSummary(): AlertStoreSummary {
    const bySeverity: Record<AlertSeverity, number> = { info: 0, warning: 0, critical: 0 };
    const bySource: Partial<Record<AlertSource, number>> = {};
    let unacknowledged = 0;

    for (const a of this.alerts) {
      bySeverity[a.severity]++;
      bySource[a.source] = (bySource[a.source] ?? 0) + 1;
      if (!a.acknowledged) unacknowledged++;
    }

    return { total: this.alerts.length, unacknowledged, bySeverity, bySource };
  }

  /** Total number of stored alerts. */
  get size(): number {
    return this.alerts.length;
  }

  // ── Pruning ───────────────────────────────────────────────────

  /** Remove oldest alerts that exceed maxAlerts or maxAgeMs. */
  private prune(): void {
    const now = Date.now();

    // Remove old alerts
    if (this.config.maxAgeMs > 0) {
      const cutoff = now - this.config.maxAgeMs;
      this.alerts = this.alerts.filter((a) => new Date(a.timestamp).getTime() >= cutoff);
    }

    // Remove excess alerts (keep newest)
    if (this.alerts.length > this.config.maxAlerts) {
      // Sort by timestamp ascending, remove oldest
      this.alerts.sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());
      this.alerts = this.alerts.slice(this.alerts.length - this.config.maxAlerts);
    }
  }

  // ── Persistence ───────────────────────────────────────────────

  /** Load alerts from disk (if persistPath is set and file exists). */
  private loadFromDisk(): void {
    if (!this.config.persistPath) return;
    try {
      if (existsSync(this.config.persistPath)) {
        const raw = readFileSync(this.config.persistPath, "utf-8");
        const data = JSON.parse(raw) as unknown;
        if (Array.isArray(data)) {
          this.alerts = data as Alert[];
          this.prune();
        }
      }
    } catch {
      // Corrupt file — start fresh
      this.alerts = [];
    }
  }

  /** Save alerts to disk (if persistPath is set and data has changed). */
  saveToDisk(): void {
    if (!this.config.persistPath || !this.dirty) return;
    try {
      const dir = dirname(this.config.persistPath);
      if (!existsSync(dir)) {
        mkdirSync(dir, { recursive: true });
      }
      const tmpPath = this.config.persistPath + ".tmp";
      writeFileSync(tmpPath, JSON.stringify(this.alerts, null, 2), "utf-8");
      renameSync(tmpPath, this.config.persistPath);
      this.dirty = false;
    } catch {
      // Best-effort — will retry on next auto-save
    }
  }
}
