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

  /**
   * Compute drawdown periods — contiguous stretches where equity is below its running peak.
   * Returns an array of { start, end, peakEquity, troughEquity, drawdownPct, recoveryDays }.
   */
  getDrawdownPeriods(): Array<{
    start: string;
    end: string;
    peakDate: string;
    troughDate: string;
    peakEquity: number;
    troughEquity: number;
    drawdownPct: number;
    durationDays: number;
    recovered: boolean;
  }> {
    const sorted = this.getAll();
    if (sorted.length < 2) return [];

    const periods: Array<{
      start: string;
      end: string;
      peakDate: string;
      troughDate: string;
      peakEquity: number;
      troughEquity: number;
      drawdownPct: number;
      durationDays: number;
      recovered: boolean;
    }> = [];

    let peak = sorted[0]!.equity;
    let peakDate = sorted[0]!.date;
    let inDrawdown = false;
    let currentStart = "";
    let trough = Infinity;
    let troughDate = "";

    for (let i = 1; i < sorted.length; i++) {
      const s = sorted[i]!;
      if (s.equity >= peak) {
        // Recovered or new peak
        if (inDrawdown) {
          periods.push({
            start: currentStart,
            end: s.date,
            peakDate,
            troughDate,
            peakEquity: peak,
            troughEquity: trough,
            drawdownPct: peak > 0 ? ((peak - trough) / peak) * 100 : 0,
            durationDays: i - sorted.findIndex((x) => x.date === currentStart),
            recovered: true,
          });
          inDrawdown = false;
        }
        peak = s.equity;
        peakDate = s.date;
        trough = Infinity;
      } else {
        // Below peak
        if (!inDrawdown) {
          inDrawdown = true;
          currentStart = s.date;
          trough = s.equity;
          troughDate = s.date;
        }
        if (s.equity < trough) {
          trough = s.equity;
          troughDate = s.date;
        }
      }
    }

    // If still in drawdown at end of data
    if (inDrawdown) {
      const last = sorted[sorted.length - 1]!;
      periods.push({
        start: currentStart,
        end: last.date,
        peakDate,
        troughDate,
        peakEquity: peak,
        troughEquity: trough,
        drawdownPct: peak > 0 ? ((peak - trough) / peak) * 100 : 0,
        durationDays: sorted.length - sorted.findIndex((x) => x.date === currentStart),
        recovered: false,
      });
    }

    return periods;
  }

  /**
   * Compute running drawdown for each snapshot — used for drawdown overlay on chart.
   * Returns array of { date, equity, drawdown, drawdownPct, peak } for every snapshot.
   */
  getDrawdownSeries(): Array<{
    date: string;
    equity: number;
    drawdown: number;
    drawdownPct: number;
    peak: number;
  }> {
    const sorted = this.getAll();
    let peak = 0;
    return sorted.map((s) => {
      if (s.equity > peak) peak = s.equity;
      const dd = peak - s.equity;
      return {
        date: s.date,
        equity: s.equity,
        drawdown: dd,
        drawdownPct: peak > 0 ? (dd / peak) * 100 : 0,
        peak,
      };
    });
  }

  /**
   * Extended performance metrics: Sharpe, Sortino, Calmar ratios.
   * Uses daily returns with risk-free rate of 0 (common for short-term trading).
   */
  getExtendedMetrics(): {
    sharpeRatio: number;
    sortinoRatio: number;
    calmarRatio: number;
    volatility: number;
    avgDailyReturn: number;
    maxConsecutiveWins: number;
    maxConsecutiveLosses: number;
    profitFactor: number;
  } {
    const sorted = this.getAll();
    const empty = {
      sharpeRatio: 0,
      sortinoRatio: 0,
      calmarRatio: 0,
      volatility: 0,
      avgDailyReturn: 0,
      maxConsecutiveWins: 0,
      maxConsecutiveLosses: 0,
      profitFactor: 0,
    };
    if (sorted.length < 2) return empty;

    // Compute daily returns
    const dailyReturns: number[] = [];
    for (let i = 1; i < sorted.length; i++) {
      const prev = sorted[i - 1]!.equity;
      const curr = sorted[i]!.equity;
      dailyReturns.push(prev > 0 ? (curr - prev) / prev : 0);
    }

    const n = dailyReturns.length;
    const avgReturn = dailyReturns.reduce((s, r) => s + r, 0) / n;

    // Standard deviation (volatility)
    const variance = dailyReturns.reduce((s, r) => s + (r - avgReturn) ** 2, 0) / n;
    const stdDev = Math.sqrt(variance);

    // Downside deviation (for Sortino)
    const downsideVariance =
      dailyReturns.filter((r) => r < 0).reduce((s, r) => s + r ** 2, 0) / n;
    const downsideDev = Math.sqrt(downsideVariance);

    // Annualized (252 trading days)
    const annualizedReturn = avgReturn * 252;
    const annualizedVol = stdDev * Math.sqrt(252);
    const annualizedDownside = downsideDev * Math.sqrt(252);

    // Sharpe = annualized return / annualized vol
    const sharpeRatio = annualizedVol > 0 ? annualizedReturn / annualizedVol : 0;

    // Sortino = annualized return / annualized downside deviation
    const sortinoRatio = annualizedDownside > 0 ? annualizedReturn / annualizedDownside : 0;

    // Calmar = annualized return / max drawdown %
    const summary = this.getSummary();
    const calmarRatio =
      summary.maxDrawdownPct > 0 ? (annualizedReturn * 100) / summary.maxDrawdownPct : 0;

    // Max consecutive wins/losses
    let maxWins = 0;
    let maxLosses = 0;
    let curWins = 0;
    let curLosses = 0;
    for (const r of dailyReturns) {
      if (r > 0) {
        curWins++;
        curLosses = 0;
        if (curWins > maxWins) maxWins = curWins;
      } else if (r < 0) {
        curLosses++;
        curWins = 0;
        if (curLosses > maxLosses) maxLosses = curLosses;
      } else {
        curWins = 0;
        curLosses = 0;
      }
    }

    // Profit factor = gross profit / gross loss
    const grossProfit = dailyReturns.filter((r) => r > 0).reduce((s, r) => s + r, 0);
    const grossLoss = Math.abs(dailyReturns.filter((r) => r < 0).reduce((s, r) => s + r, 0));
    const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? Infinity : 0;

    return {
      sharpeRatio: Number(sharpeRatio.toFixed(3)),
      sortinoRatio: Number(sortinoRatio.toFixed(3)),
      calmarRatio: Number(calmarRatio.toFixed(3)),
      volatility: Number((annualizedVol * 100).toFixed(2)),
      avgDailyReturn: Number((avgReturn * 100).toFixed(4)),
      maxConsecutiveWins: maxWins,
      maxConsecutiveLosses: maxLosses,
      profitFactor: Number((profitFactor === Infinity ? 999 : profitFactor).toFixed(2)),
    };
  }

  /** Close: stop auto-save and flush. */
  async close(): Promise<void> {
    this.stopAutoSave();
    if (this.dirty) await this.save();
  }
}
