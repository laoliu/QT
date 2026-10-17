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
  /** Base data directory; defaults to ~/data */
  dataDir?: string;
  /** Maximum number of snapshots to retain (default: 2000 ≈ 4 years daily) */
  maxSnapshots?: number;
  /** Trading mode — determines which file to use for persistence */
  mode?: "paper" | "live";
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
    const suffix = opts.mode ? `-${opts.mode}` : "";
    this.filePath = path.join(dir, `equity-history${suffix}.json`);
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
    // delta from the previous day's equity. Never fall back to lifetime
    // `profit` which would corrupt win/loss day calculations.
    let dailyPnl = account.todayProfit ?? 0;
    if (account.todayProfit == null && this.snapshots.length > 0) {
      // Find the most recent snapshot from a PREVIOUS date (not today).
      // This is critical for intraday re-snapshots: the last entry may
      // already be today's snapshot, so we must look further back.
      let prevDay: EquitySnapshot | undefined;
      for (let i = this.snapshots.length - 1; i >= 0; i--) {
        if (this.snapshots[i]!.date !== date) {
          prevDay = this.snapshots[i];
          break;
        }
      }
      if (prevDay) {
        dailyPnl = account.totalAssets - prevDay.equity;
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

    // Daily PnL stats — use recorded dailyPnl when available, but fall
    // back to equity deltas when all recorded values are zero (common with
    // brokers like QMT that don't report todayProfit).
    const allPnlZero = sorted.length > 1 && sorted.every((s) => s.dailyPnl === 0);
    let bestDay: { date: string; pnl: number } | null = null;
    let worstDay: { date: string; pnl: number } | null = null;
    let totalPnl = 0;
    let winDays = 0;
    let lossDays = 0;

    for (let i = 0; i < sorted.length; i++) {
      const s = sorted[i]!;
      // Prefer recorded dailyPnl; when all are zero, derive from equity delta
      const pnl = allPnlZero && i > 0 ? s.equity - sorted[i - 1]!.equity : s.dailyPnl;
      totalPnl += pnl;
      if (!bestDay || pnl > bestDay.pnl) bestDay = { date: s.date, pnl };
      if (!worstDay || pnl < worstDay.pnl) worstDay = { date: s.date, pnl };
      if (pnl > 0) winDays++;
      else if (pnl < 0) lossDays++;
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

  /**
   * Compute the current cumulative max drawdown percentage.
   * Returns a value between 0 and 100 (e.g. 15 means -15% from peak).
   * If currentEquity is provided, it is used as the latest data point
   * (for real-time checks before the next snapshot is recorded).
   */
  getMaxDrawdownPct(currentEquity?: number): number {
    const sorted = this.getAll();
    if (sorted.length === 0 && currentEquity == null) return 0;

    let peak = -Infinity;
    let maxDdPct = 0;

    for (const s of sorted) {
      if (s.equity > peak) peak = s.equity;
      const dd = peak - s.equity;
      const ddPct = peak > 0 ? (dd / peak) * 100 : 0;
      if (ddPct > maxDdPct) maxDdPct = ddPct;
    }

    // Include current equity if provided (real-time update)
    if (currentEquity != null) {
      if (currentEquity > peak) peak = currentEquity;
      const dd = peak - currentEquity;
      const ddPct = peak > 0 ? (dd / peak) * 100 : 0;
      if (ddPct > maxDdPct) maxDdPct = ddPct;
    }

    return maxDdPct;
  }

  /** Close: stop auto-save and flush. */
  async close(): Promise<void> {
    this.stopAutoSave();
    if (this.dirty) await this.save();
  }
}
