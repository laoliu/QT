/**
 * EquityHistory — tracks and persists daily equity snapshots.
 *
 * Records the account's total equity at regular intervals (configurable,
 * defaults to every 5 minutes during active sessions) and produces a
 * time-series suitable for equity curve charts and performance analysis.
 *
 * Persistence: flat JSON file at `{dataDir}/equity-history.json`.
 * Each entry: { date, equity, cash, marketValue, dailyPnl, benchmark? }
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { QuantTradingConfig, BrokerAccount, LivePosition } from "../types.js";

// ── Types ────────────────────────────────────────────────────

export interface EquitySnapshot {
  /** ISO date string (YYYY-MM-DD) for daily, ISO datetime for intraday */
  date: string;
  /** Total account equity (cash + market value) */
  equity: number;
  /** Available cash */
  cash: number;
  /** Total market value of positions */
  marketValue: number;
  /** Unrealized + realized PnL for the day */
  dailyPnl: number;
  /** Number of open positions */
  positionCount: number;
  /** Optional benchmark value (e.g. CSI 300 index) for comparison */
  benchmark?: number;
}

export interface EquityHistoryOptions {
  /** Base data directory; defaults to ~/.openclaw/quant-trading */
  dataDir?: string;
  /** Maximum number of snapshots to retain (default: 2000 ≈ 4 years daily) */
  maxSnapshots?: number;
}

// ── Service ──────────────────────────────────────────────────

export class EquityHistory {
  private snapshots: EquitySnapshot[] = [];
  private filePath: string;
  private maxSnapshots: number;
  private loaded = false;
  private dirty = false;
  private saveTimer: ReturnType<typeof setInterval> | null = null;

  constructor(opts: EquityHistoryOptions = {}) {
    const dir = opts.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
    this.filePath = path.join(dir, "equity-history.json");
    this.maxSnapshots = opts.maxSnapshots ?? 2000;
  }

  /** Load existing history from disk. */
  async load(): Promise<void> {
    try {
      const raw = await fs.readFile(this.filePath, "utf-8");
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        this.snapshots = parsed;
      }
    } catch {
      this.snapshots = [];
    }
    this.loaded = true;
  }

  /** Start periodic auto-save (every 60s). */
  startAutoSave(intervalMs = 60_000): void {
    this.saveTimer = setInterval(() => {
      if (this.dirty) void this.save();
    }, intervalMs);
  }

  /** Stop auto-save timer. */
  stopAutoSave(): void {
    if (this.saveTimer) {
      clearInterval(this.saveTimer);
      this.saveTimer = null;
    }
  }

  /** Persist current history to disk. */
  async save(): Promise<void> {
    await fs.mkdir(path.dirname(this.filePath), { recursive: true });
    await fs.writeFile(this.filePath, JSON.stringify(this.snapshots, null, 2));
    this.dirty = false;
  }

  /**
   * Record an equity snapshot from account + positions data.
   * De-duplicates by date: if a snapshot for the same date already exists,
   * it is updated (last write wins for intraday re-snapshots).
   */
  record(account: BrokerAccount, positions: LivePosition[], benchmark?: number): EquitySnapshot {
    const now = new Date();
    const date = now.toISOString().slice(0, 10);

    // Compute daily P&L: use todayProfit if available, otherwise compute
    // delta from the previous snapshot's equity. Never fall back to lifetime
    // `profit` which would corrupt win/loss day calculations.
    let dailyPnl = account.todayProfit ?? 0;
    if (account.todayProfit == null && this.snapshots.length > 0) {
      const prev = this.snapshots[this.snapshots.length - 1];
      // Only use delta if the previous snapshot is from a different date
      if (prev.date !== date) {
        dailyPnl = account.totalAssets - prev.equity;
      }
    }

    const snap: EquitySnapshot = {
      date,
      equity: account.totalAssets,
      cash: account.cash,
      marketValue: account.marketValue,
      dailyPnl,
      positionCount: positions.length,
      benchmark,
    };

    // Update if same date exists (intraday re-snapshot)
    const existingIdx = this.snapshots.findIndex((s) => s.date === date);
    if (existingIdx >= 0) {
      this.snapshots[existingIdx] = snap;
    } else {
      this.snapshots.push(snap);
    }

    // Prune oldest if over limit
    if (this.snapshots.length > this.maxSnapshots) {
      this.snapshots = this.snapshots.slice(-this.maxSnapshots);
    }

    this.dirty = true;
    return snap;
  }

  /**
   * Add a raw snapshot directly (for testing or manual injection).
   */
  addSnapshot(snap: EquitySnapshot): void {
    const existingIdx = this.snapshots.findIndex((s) => s.date === snap.date);
    if (existingIdx >= 0) {
      this.snapshots[existingIdx] = snap;
    } else {
      this.snapshots.push(snap);
    }
    if (this.snapshots.length > this.maxSnapshots) {
      this.snapshots = this.snapshots.slice(-this.maxSnapshots);
    }
    this.dirty = true;
  }

  /** Get all snapshots (sorted by date ascending). */
  getAll(): EquitySnapshot[] {
    return [...this.snapshots].sort((a, b) => a.date.localeCompare(b.date));
  }

  /** Get snapshots within a date range (inclusive). */
  getRange(startDate: string, endDate: string): EquitySnapshot[] {
    return this.getAll().filter((s) => s.date >= startDate && s.date <= endDate);
  }

  /** Get the most recent N snapshots. */
  getRecent(limit = 30): EquitySnapshot[] {
    const sorted = this.getAll();
    return sorted.slice(-limit);
  }

  /** Get the latest snapshot. */
  getLatest(): EquitySnapshot | null {
    if (this.snapshots.length === 0) return null;
    return this.getAll().slice(-1)[0] ?? null;
  }

  /** Get total number of snapshots. */
  get count(): number {
    return this.snapshots.length;
  }

  /**
   * Compute performance summary from the equity history.
   */
  getSummary(): {
    totalDays: number;
    startEquity: number;
    endEquity: number;
    totalReturn: number;
    totalReturnPct: number;
    maxDrawdown: number;
    maxDrawdownPct: number;
    bestDay: { date: string; pnl: number } | null;
    worstDay: { date: string; pnl: number } | null;
    avgDailyPnl: number;
    winDays: number;
    lossDays: number;
    winRate: number;
  } {
    const sorted = this.getAll();
    if (sorted.length === 0) {
      return {
        totalDays: 0,
        startEquity: 0,
        endEquity: 0,
        totalReturn: 0,
        totalReturnPct: 0,
        maxDrawdown: 0,
        maxDrawdownPct: 0,
        bestDay: null,
        worstDay: null,
        avgDailyPnl: 0,
        winDays: 0,
        lossDays: 0,
        winRate: 0,
      };
    }

    const first = sorted[0]!;
    const last = sorted[sorted.length - 1]!;
    const totalReturn = last.equity - first.equity;
    const totalReturnPct = first.equity > 0 ? (totalReturn / first.equity) * 100 : 0;

    // Max drawdown
    let peak = -Infinity;
    let maxDrawdown = 0;
    let maxDrawdownPct = 0;
    for (const s of sorted) {
      if (s.equity > peak) peak = s.equity;
      const dd = peak - s.equity;
      if (dd > maxDrawdown) {
        maxDrawdown = dd;
        maxDrawdownPct = peak > 0 ? (dd / peak) * 100 : 0;
      }
    }

    // Daily PnL stats
    let bestDay: { date: string; pnl: number } | null = null;
    let worstDay: { date: string; pnl: number } | null = null;
    let totalPnl = 0;
    let winDays = 0;
    let lossDays = 0;

    for (const s of sorted) {
      totalPnl += s.dailyPnl;
      if (!bestDay || s.dailyPnl > bestDay.pnl) bestDay = { date: s.date, pnl: s.dailyPnl };
      if (!worstDay || s.dailyPnl < worstDay.pnl) worstDay = { date: s.date, pnl: s.dailyPnl };
      if (s.dailyPnl > 0) winDays++;
      else if (s.dailyPnl < 0) lossDays++;
    }

    return {
      totalDays: sorted.length,
      startEquity: first.equity,
      endEquity: last.equity,
      totalReturn,
      totalReturnPct,
      maxDrawdown,
      maxDrawdownPct,
      bestDay,
      worstDay,
      avgDailyPnl: sorted.length > 0 ? totalPnl / sorted.length : 0,
      winDays,
      lossDays,
      winRate: winDays + lossDays > 0 ? (winDays / (winDays + lossDays)) * 100 : 0,
    };
  }

  /** Close: stop auto-save and flush. */
  async close(): Promise<void> {
    this.stopAutoSave();
    if (this.dirty) await this.save();
  }
}
