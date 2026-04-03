import { randomUUID } from "node:crypto";
/**
 * BacktestStore — persistent store for backtest results.
 *
 * Saves every backtest run with metadata for later querying,
 * comparison, and historical performance analysis.
 *
 * Features:
 *   - In-memory index for fast queries
 *   - JSON file persistence (one file per result + index)
 *   - Query by strategy, symbols, date range, tags
 *   - Comparison across multiple runs
 *   - Historical performance metrics aggregation
 */
import {
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";
import type { BacktestResult } from "../types.js";
import { AsyncMutex } from "../utils/async-mutex.js";

// ── Types ────────────────────────────────────────────────────────

/** Metadata stored alongside a backtest result */
export type BacktestRecord = {
  /** Unique run id */
  id: string;
  /** When this backtest was executed */
  createdAt: string;
  /** Optional user-assigned tags for organization */
  tags: string[];
  /** Optional user notes */
  notes?: string;
  /** Whether this run is marked as a favorite/starred */
  starred: boolean;
  /** Engine that ran the backtest */
  engine: string;
  /** The full backtest result */
  result: BacktestResult;
};

/** Lightweight index entry (no equity curve / signals) */
export type BacktestIndexEntry = {
  id: string;
  createdAt: string;
  strategyId: string;
  symbols: string[];
  startDate: string;
  endDate: string;
  engine: string;
  tags: string[];
  starred: boolean;
  notes?: string;
  // Key metrics for quick comparison
  totalReturn: number;
  annualizedReturn: number;
  maxDrawdown: number;
  sharpeRatio: number;
  winRate: number;
  totalTrades: number;
  initialCash: number;
  finalValue: number;
};

export type BacktestQuery = {
  /** Filter by strategy id */
  strategyId?: string;
  /** Filter by symbol (matches if ANY symbol in the run matches) */
  symbol?: string;
  /** Filter by engine */
  engine?: string;
  /** Filter by tag (matches if ANY tag matches) */
  tag?: string;
  /** Only starred results */
  starred?: boolean;
  /** Results created after this ISO timestamp */
  after?: string;
  /** Results created before this ISO timestamp */
  before?: string;
  /** Sort field (default: createdAt) */
  sortBy?: "createdAt" | "totalReturn" | "sharpeRatio" | "maxDrawdown" | "winRate";
  /** Sort direction (default: desc) */
  sortDir?: "asc" | "desc";
  /** Max results (default: 50) */
  limit?: number;
  /** Pagination offset */
  offset?: number;
};

export type BacktestComparison = {
  ids: string[];
  entries: BacktestIndexEntry[];
  /** Per-metric comparison: best/worst/avg */
  metrics: {
    totalReturn: MetricComparison;
    annualizedReturn: MetricComparison;
    maxDrawdown: MetricComparison;
    sharpeRatio: MetricComparison;
    winRate: MetricComparison;
    profitFactor: MetricComparison;
  };
};

export type MetricComparison = {
  best: { id: string; value: number };
  worst: { id: string; value: number };
  avg: number;
  values: Array<{ id: string; value: number }>;
};

export type PerformanceSummary = {
  /** Total number of backtests */
  totalRuns: number;
  /** Number of profitable runs */
  profitableRuns: number;
  /** Average return across all runs */
  avgReturn: number;
  /** Average Sharpe ratio across all runs */
  avgSharpe: number;
  /** Average max drawdown across all runs */
  avgMaxDrawdown: number;
  /** Average win rate across all runs */
  avgWinRate: number;
  /** Best single run */
  bestRun?: { id: string; totalReturn: number; strategyId: string };
  /** Worst single run */
  worstRun?: { id: string; totalReturn: number; strategyId: string };
  /** Per-strategy breakdown */
  byStrategy: Record<
    string,
    {
      runs: number;
      avgReturn: number;
      avgSharpe: number;
      bestReturn: number;
      worstReturn: number;
    }
  >;
};

// ── Config ───────────────────────────────────────────────────────

export type BacktestStoreConfig = {
  /** Directory for storing backtest result files (undefined disables persistence) */
  dataDir?: string;
  /** Maximum number of results to keep (default: 500) */
  maxResults?: number;
};

const DEFAULT_MAX_RESULTS = 500;

// ── Store implementation ─────────────────────────────────────────────

function generateRunId(): string {
  return `bt_${Date.now()}_${randomUUID().slice(0, 8)}`;
}

export class BacktestStore {
  private readonly dataDir: string | undefined;
  private readonly maxResults: number;
  private index: BacktestIndexEntry[] = [];
  /** Serializes write operations to prevent index corruption. */
  private readonly writeMutex = new AsyncMutex();

  constructor(config: BacktestStoreConfig) {
    this.dataDir = config.dataDir;
    this.maxResults = config.maxResults ?? DEFAULT_MAX_RESULTS;
  }

  // ── Lifecycle ─────────────────────────────────────────────────

  /** Load index from disk. Call once at startup. */
  start(): void {
    this.ensureDir();
    this.loadIndex();
  }

  // ── Write operations ──────────────────────────────────────────

  /** Save a backtest result. Returns the generated run id. */
  async save(
    result: BacktestResult,
    opts?: { tags?: string[]; notes?: string; engine?: string },
  ): Promise<string> {
    const release = await this.writeMutex.acquire();
    try {
      const id = generateRunId();
      const record: BacktestRecord = {
        id,
        createdAt: new Date().toISOString(),
        tags: opts?.tags ?? [],
        notes: opts?.notes,
        starred: false,
        engine: opts?.engine ?? "builtin",
        result,
      };

      // Write full result to individual file
      this.writeResult(id, record);

      // Add to index
      const entry = this.toIndexEntry(record);
      this.index.push(entry);
      this.prune();
      this.saveIndex();

      return id;
    } finally {
      release();
    }
  }

  /** Delete a backtest result. */
  async delete(id: string): Promise<boolean> {
    const release = await this.writeMutex.acquire();
    try {
      const idx = this.index.findIndex((e) => e.id === id);
      if (idx < 0) return false;
      this.index.splice(idx, 1);
      this.deleteResultFile(id);
      this.saveIndex();
      return true;
    } finally {
      release();
    }
  }

  /** Update tags for a result. */
  async setTags(id: string, tags: string[]): Promise<boolean> {
    const release = await this.writeMutex.acquire();
    try {
      const entry = this.index.find((e) => e.id === id);
      if (!entry) return false;
      entry.tags = tags;

      // Also update in the full record file
      const record = this.loadResult(id);
      if (record) {
        record.tags = tags;
        this.writeResult(id, record);
      }

      this.saveIndex();
      return true;
    } finally {
      release();
    }
  }

  /** Toggle starred status. */
  async toggleStar(id: string): Promise<boolean> {
    const release = await this.writeMutex.acquire();
    try {
      const entry = this.index.find((e) => e.id === id);
      if (!entry) return false;
      entry.starred = !entry.starred;

      const record = this.loadResult(id);
      if (record) {
        record.starred = entry.starred;
        this.writeResult(id, record);
      }

      this.saveIndex();
      return true;
    } finally {
      release();
    }
  }

  /** Update notes for a result. */
  async setNotes(id: string, notes: string): Promise<boolean> {
    const release = await this.writeMutex.acquire();
    try {
      const entry = this.index.find((e) => e.id === id);
      if (!entry) return false;
      entry.notes = notes;

      const record = this.loadResult(id);
      if (record) {
        record.notes = notes;
        this.writeResult(id, record);
      }

      this.saveIndex();
      return true;
    } finally {
      release();
    }
  }

  // ── Read operations ───────────────────────────────────────────

  /** Query index with filters. Returns lightweight entries (no equity curve). */
  query(q: BacktestQuery = {}): BacktestIndexEntry[] {
    // Pre-compute date thresholds once instead of per-element
    const afterTs = q.after ? new Date(q.after).getTime() : 0;
    const beforeTs = q.before ? new Date(q.before).getTime() : Infinity;

    // Single-pass filter instead of 7 chained .filter() calls
    const results = this.index.filter((e) => {
      if (q.strategyId && e.strategyId !== q.strategyId) return false;
      if (q.symbol && !e.symbols.includes(q.symbol!)) return false;
      if (q.engine && e.engine !== q.engine) return false;
      if (q.tag && !e.tags.includes(q.tag!)) return false;
      if (q.starred !== undefined && e.starred !== q.starred) return false;
      const ts = new Date(e.createdAt).getTime();
      if (ts < afterTs || ts > beforeTs) return false;
      return true;
    });

    // Sort
    const sortBy = q.sortBy ?? "createdAt";
    const sortDir = q.sortDir ?? "desc";
    const mult = sortDir === "desc" ? -1 : 1;

    results.sort((a, b) => {
      if (sortBy === "createdAt") {
        return mult * (new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime());
      }
      return mult * ((a[sortBy] as number) - (b[sortBy] as number));
    });

    // Pagination
    const offset = q.offset ?? 0;
    const limit = q.limit ?? 50;
    return results.slice(offset, offset + limit);
  }

  /** Get the full backtest record (including equity curve, signals). */
  getFullResult(id: string): BacktestRecord | null {
    return this.loadResult(id);
  }

  /** Get a lightweight index entry. */
  getEntry(id: string): BacktestIndexEntry | undefined {
    return this.index.find((e) => e.id === id);
  }

  /** Total number of stored results. */
  get size(): number {
    return this.index.length;
  }

  // ── Comparison ────────────────────────────────────────────────

  /** Compare multiple backtest runs side by side. */
  compare(ids: string[]): BacktestComparison | null {
    const entries = ids
      .map((id) => this.index.find((e) => e.id === id))
      .filter(Boolean) as BacktestIndexEntry[];
    if (entries.length < 2) return null;

    const buildMetric = (
      key: "totalReturn" | "annualizedReturn" | "maxDrawdown" | "sharpeRatio" | "winRate",
      records: BacktestRecord[],
    ): MetricComparison => {
      const values = records.map((r) => ({
        id: r.id,
        value: key === "maxDrawdown" ? r.result.maxDrawdown : (r.result[key] as number),
      }));
      // For maxDrawdown, "best" is lowest (least drawdown)
      const sorted = [...values].sort((a, b) =>
        key === "maxDrawdown" ? a.value - b.value : b.value - a.value,
      );
      const sum = values.reduce((acc, v) => acc + v.value, 0);
      const fallback = { id: "", value: 0 };
      return {
        best: sorted[0] ?? fallback,
        worst: sorted[sorted.length - 1] ?? fallback,
        avg: values.length > 0 ? sum / values.length : 0,
        values,
      };
    };

    // Load full records for profitFactor
    const records = ids.map((id) => this.loadResult(id)).filter(Boolean) as BacktestRecord[];
    if (records.length === 0) return null;

    const profitFactorValues = records.map((r) => ({
      id: r.id,
      value: r.result.profitFactor ?? 0,
    }));
    const pfSorted = [...profitFactorValues].sort((a, b) => b.value - a.value);
    const pfSum = profitFactorValues.reduce((acc, v) => acc + v.value, 0);
    const defaultMetricEntry = { id: "", value: 0 };

    return {
      ids,
      entries,
      metrics: {
        totalReturn: buildMetric("totalReturn", records),
        annualizedReturn: buildMetric("annualizedReturn", records),
        maxDrawdown: buildMetric("maxDrawdown", records),
        sharpeRatio: buildMetric("sharpeRatio", records),
        winRate: buildMetric("winRate", records),
        profitFactor: {
          best: pfSorted[0] ?? defaultMetricEntry,
          worst: pfSorted[pfSorted.length - 1] ?? defaultMetricEntry,
          avg: profitFactorValues.length > 0 ? pfSum / profitFactorValues.length : 0,
          values: profitFactorValues,
        },
      },
    };
  }

  // ── Performance summary ───────────────────────────────────────

  /** Compute aggregate performance metrics across all (or filtered) runs. */
  getPerformanceSummary(strategyId?: string): PerformanceSummary {
    const filtered = strategyId
      ? this.index.filter((e) => e.strategyId === strategyId)
      : this.index;

    if (filtered.length === 0) {
      return {
        totalRuns: 0,
        profitableRuns: 0,
        avgReturn: 0,
        avgSharpe: 0,
        avgMaxDrawdown: 0,
        avgWinRate: 0,
        byStrategy: {},
      };
    }

    let sumReturn = 0;
    let sumSharpe = 0;
    let sumDrawdown = 0;
    let sumWinRate = 0;
    let profitableRuns = 0;
    let bestRun: PerformanceSummary["bestRun"];
    let worstRun: PerformanceSummary["worstRun"];
    const byStrategy: PerformanceSummary["byStrategy"] = {};

    for (const e of filtered) {
      sumReturn += e.totalReturn;
      sumSharpe += e.sharpeRatio;
      sumDrawdown += e.maxDrawdown;
      sumWinRate += e.winRate;

      if (e.totalReturn > 0) profitableRuns++;

      if (!bestRun || e.totalReturn > bestRun.totalReturn) {
        bestRun = { id: e.id, totalReturn: e.totalReturn, strategyId: e.strategyId };
      }
      if (!worstRun || e.totalReturn < worstRun.totalReturn) {
        worstRun = { id: e.id, totalReturn: e.totalReturn, strategyId: e.strategyId };
      }

      // Per-strategy
      if (!byStrategy[e.strategyId]) {
        byStrategy[e.strategyId] = {
          runs: 0,
          avgReturn: 0,
          avgSharpe: 0,
          bestReturn: -Infinity,
          worstReturn: Infinity,
        };
      }
      const s = byStrategy[e.strategyId];
      s.runs++;
      s.avgReturn += e.totalReturn;
      s.avgSharpe += e.sharpeRatio;
      if (e.totalReturn > s.bestReturn) s.bestReturn = e.totalReturn;
      if (e.totalReturn < s.worstReturn) s.worstReturn = e.totalReturn;
    }

    // Finalize per-strategy averages
    for (const s of Object.values(byStrategy)) {
      s.avgReturn /= s.runs;
      s.avgSharpe /= s.runs;
    }

    const n = filtered.length;
    return {
      totalRuns: n,
      profitableRuns,
      avgReturn: sumReturn / n,
      avgSharpe: sumSharpe / n,
      avgMaxDrawdown: sumDrawdown / n,
      avgWinRate: sumWinRate / n,
      bestRun,
      worstRun,
      byStrategy,
    };
  }

  // ── Persistence helpers ───────────────────────────────────────

  private ensureDir(): void {
    if (!this.dataDir) return;
    if (!existsSync(this.dataDir)) {
      mkdirSync(this.dataDir, { recursive: true });
    }
  }

  private indexPath(): string | undefined {
    return this.dataDir ? join(this.dataDir, "index.json") : undefined;
  }

  private resultPath(id: string): string | undefined {
    return this.dataDir ? join(this.dataDir, `${id}.json`) : undefined;
  }

  private loadIndex(): void {
    const path = this.indexPath();
    if (!path) return;
    try {
      if (existsSync(path)) {
        const raw = readFileSync(path, "utf-8");
        const data = JSON.parse(raw) as unknown;
        if (Array.isArray(data)) {
          this.index = data as BacktestIndexEntry[];
        }
      }
    } catch {
      this.index = [];
    }
  }

  private saveIndex(): void {
    const path = this.indexPath();
    if (!path) return;
    try {
      this.ensureDir();
      writeFileSync(path, JSON.stringify(this.index, null, 2), "utf-8");
    } catch {
      // Best-effort
    }
  }

  private writeResult(id: string, record: BacktestRecord): void {
    const path = this.resultPath(id);
    if (!path) return;
    try {
      this.ensureDir();
      writeFileSync(path, JSON.stringify(record, null, 2), "utf-8");
    } catch {
      // Best-effort
    }
  }

  private loadResult(id: string): BacktestRecord | null {
    const path = this.resultPath(id);
    if (!path) return null;
    try {
      if (existsSync(path)) {
        const raw = readFileSync(path, "utf-8");
        return JSON.parse(raw) as BacktestRecord;
      }
    } catch {
      // Corrupt file
    }
    return null;
  }

  private deleteResultFile(id: string): void {
    const path = this.resultPath(id);
    if (!path) return;
    try {
      if (existsSync(path)) {
        unlinkSync(path);
      }
    } catch {
      // Best-effort
    }
  }

  private toIndexEntry(record: BacktestRecord): BacktestIndexEntry {
    const r = record.result;
    return {
      id: record.id,
      createdAt: record.createdAt,
      strategyId: r.strategyId,
      symbols: r.symbols,
      startDate: r.startDate,
      endDate: r.endDate,
      engine: record.engine,
      tags: record.tags,
      starred: record.starred,
      notes: record.notes,
      totalReturn: r.totalReturn,
      annualizedReturn: r.annualizedReturn,
      maxDrawdown: r.maxDrawdown,
      sharpeRatio: r.sharpeRatio,
      winRate: r.winRate,
      totalTrades: r.totalTrades,
      initialCash: r.initialCash,
      finalValue: r.finalValue,
    };
  }

  /** Remove oldest non-starred results when exceeding maxResults. */
  private prune(): void {
    if (this.index.length <= this.maxResults) return;

    // Sort by createdAt ascending (oldest first)
    const sorted = [...this.index].sort(
      (a, b) => new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime(),
    );

    const excess = this.index.length - this.maxResults;
    const toRemove: string[] = [];

    // Walk from oldest, skip starred, skip the very last entry (newest / just-added)
    for (let i = 0; i < sorted.length - 1; i++) {
      if (toRemove.length >= excess) break;
      if (!sorted[i].starred) {
        toRemove.push(sorted[i].id);
      }
    }

    for (const id of toRemove) {
      const idx = this.index.findIndex((e) => e.id === id);
      if (idx >= 0) this.index.splice(idx, 1);
      this.deleteResultFile(id);
    }
  }
}
