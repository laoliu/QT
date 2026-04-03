/**
 * OptimizationStore — persistent store for pool optimization (寻优) results.
 *
 * Every time a user runs the pool optimizer, the full result is saved here.
 * This enables:
 *   - Reviewing past optimization results
 *   - Comparing best combos across different runs
 *   - Tracking which pools/strategies perform best over time
 *
 * Architecture mirrors BacktestStore: in-memory index + per-run JSON files.
 */
import { randomUUID } from "node:crypto";
import {
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";
import { AsyncMutex } from "../utils/async-mutex.js";

// ── Types ────────────────────────────────────────────────────────

/** Best combo summary from a single optimization run */
export type OptimizationBest = {
  poolLabel: string;
  poolKey?: string;
  strategyLabel: string;
  strategy?: string;
  riskLevel?: string;
  riskLabel?: string;
  rebalanceDays?: number;
  totalReturn: number;
  sharpeRatio: number;
  maxDrawdown: number;
  winRate?: number;
  profitFactor?: number;
  symbolCount: number;
  symbols?: string[];
  compositeScore?: number;
};

/** A single round/combo result */
export type OptimizationRound = {
  rank: number;
  poolLabel: string;
  poolKey?: string;
  symbolCount: number;
  strategyLabel: string;
  riskLabel: string;
  rebalanceDays: number;
  totalReturn: number;
  sharpeRatio: number;
  maxDrawdown: number;
  winRate?: number;
  profitFactor?: number;
  compositeScore: number;
  error?: string;
};

/** Auto-screening summary */
export type ScreeningSummary = {
  totalFetched: number;
  afterFilter: number;
  autoPoolsCreated: number;
  autoPoolNames?: Array<{ label: string; count: number }>;
  source?: string;
};

/** Full record of a single optimization run */
export type OptimizationRecord = {
  id: string;
  createdAt: string;
  /** Input parameters */
  params: {
    capital: number;
    startDate: string;
    endDate?: string;
    maxRounds: number;
    autoScreen: boolean;
  };
  /** Total combos tested */
  totalRounds: number;
  /** Best combo recommendation */
  best: OptimizationBest;
  /** Human-readable recommendation text */
  recommendText?: string;
  /** Screening summary (auto mode) */
  screeningSummary?: ScreeningSummary;
  /** All round results */
  rounds: OptimizationRound[];
  /** Optional user notes */
  notes?: string;
  /** Optional user tags */
  tags: string[];
  /** Whether starred */
  starred: boolean;
};

/** Lightweight index entry (no per-round details) */
export type OptimizationIndexEntry = {
  id: string;
  createdAt: string;
  capital: number;
  startDate: string;
  endDate?: string;
  autoScreen: boolean;
  totalRounds: number;
  /** Best combo headline metrics */
  bestPoolLabel: string;
  bestStrategyLabel: string;
  bestReturn: number;
  bestSharpe: number;
  bestMaxDrawdown: number;
  bestWinRate?: number;
  bestSymbolCount: number;
  bestCompositeScore?: number;
  tags: string[];
  starred: boolean;
  notes?: string;
};

export type OptimizationQuery = {
  /** Only starred results */
  starred?: boolean;
  /** Filter by tag */
  tag?: string;
  /** Results created after this ISO timestamp */
  after?: string;
  /** Results created before this ISO timestamp */
  before?: string;
  /** Sort field (default: createdAt) */
  sortBy?: "createdAt" | "bestReturn" | "bestSharpe" | "bestMaxDrawdown";
  /** Sort direction (default: desc) */
  sortDir?: "asc" | "desc";
  /** Max results (default: 50) */
  limit?: number;
  /** Pagination offset */
  offset?: number;
};

// ── Config ───────────────────────────────────────────────────────

export type OptimizationStoreConfig = {
  /** Directory for storing optimization files (undefined disables persistence) */
  dataDir?: string;
  /** Maximum number of results to keep (default: 100) */
  maxResults?: number;
};

const DEFAULT_MAX_RESULTS = 100;

// ── Store implementation ─────────────────────────────────────────

function generateOptId(): string {
  return `opt_${Date.now()}_${randomUUID().slice(0, 8)}`;
}

export class OptimizationStore {
  private readonly dataDir: string | undefined;
  private readonly maxResults: number;
  private index: OptimizationIndexEntry[] = [];
  private readonly writeMutex = new AsyncMutex();

  constructor(config: OptimizationStoreConfig) {
    this.dataDir = config.dataDir;
    this.maxResults = config.maxResults ?? DEFAULT_MAX_RESULTS;
  }

  // ── Lifecycle ─────────────────────────────────────────────────

  /** Load index from disk. Call once at startup. */
  start(): void {
    this.ensureDir();
    this.loadIndex();
  }

  get size(): number {
    return this.index.length;
  }

  // ── Write operations ──────────────────────────────────────────

  /** Save an optimization result. Returns the generated id. */
  async save(
    data: Omit<OptimizationRecord, "id" | "createdAt" | "tags" | "starred">,
    opts?: { tags?: string[]; notes?: string },
  ): Promise<string> {
    const release = await this.writeMutex.acquire();
    try {
      const id = generateOptId();
      const record: OptimizationRecord = {
        ...data,
        id,
        createdAt: new Date().toISOString(),
        tags: opts?.tags ?? [],
        starred: false,
        notes: opts?.notes,
      };

      this.writeRecord(record);
      const entry = this.toIndexEntry(record);
      this.index.push(entry);
      this.prune();
      this.saveIndex();

      return id;
    } finally {
      release();
    }
  }

  /** Delete an optimization result. */
  async delete(id: string): Promise<boolean> {
    const release = await this.writeMutex.acquire();
    try {
      const idx = this.index.findIndex((e) => e.id === id);
      if (idx < 0) return false;
      this.index.splice(idx, 1);
      this.deleteRecordFile(id);
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

      const record = this.loadRecord(id);
      if (record) {
        record.starred = entry.starred;
        this.writeRecord(record);
      }

      this.saveIndex();
      return true;
    } finally {
      release();
    }
  }

  /** Update notes. */
  async setNotes(id: string, notes: string): Promise<boolean> {
    const release = await this.writeMutex.acquire();
    try {
      const entry = this.index.find((e) => e.id === id);
      if (!entry) return false;
      entry.notes = notes;

      const record = this.loadRecord(id);
      if (record) {
        record.notes = notes;
        this.writeRecord(record);
      }

      this.saveIndex();
      return true;
    } finally {
      release();
    }
  }

  // ── Read operations ───────────────────────────────────────────

  /** Query index with filters. */
  query(q: OptimizationQuery = {}): OptimizationIndexEntry[] {
    const afterTs = q.after ? new Date(q.after).getTime() : 0;
    const beforeTs = q.before ? new Date(q.before).getTime() : Infinity;

    const results = this.index.filter((e) => {
      if (q.starred !== undefined && e.starred !== q.starred) return false;
      if (q.tag && !e.tags.includes(q.tag)) return false;
      const ts = new Date(e.createdAt).getTime();
      if (ts < afterTs || ts > beforeTs) return false;
      return true;
    });

    const sortBy = q.sortBy ?? "createdAt";
    const mult = (q.sortDir ?? "desc") === "desc" ? -1 : 1;
    results.sort((a, b) => {
      if (sortBy === "createdAt") {
        return mult * (new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime());
      }
      return mult * ((a[sortBy] as number) - (b[sortBy] as number));
    });

    const offset = q.offset ?? 0;
    const limit = q.limit ?? 50;
    return results.slice(offset, offset + limit);
  }

  /** Get the full optimization record (including all rounds). */
  getFullResult(id: string): OptimizationRecord | null {
    return this.loadRecord(id);
  }

  /** Get a lightweight index entry. */
  getEntry(id: string): OptimizationIndexEntry | undefined {
    return this.index.find((e) => e.id === id);
  }

  // ── Private helpers ───────────────────────────────────────────

  private ensureDir(): void {
    if (this.dataDir && !existsSync(this.dataDir)) {
      mkdirSync(this.dataDir, { recursive: true });
    }
  }

  private loadIndex(): void {
    if (!this.dataDir) return;
    const indexPath = join(this.dataDir, "_index.json");
    if (!existsSync(indexPath)) {
      this.rebuildIndex();
      return;
    }
    try {
      this.index = JSON.parse(readFileSync(indexPath, "utf-8")) as OptimizationIndexEntry[];
    } catch {
      this.rebuildIndex();
    }
  }

  private rebuildIndex(): void {
    if (!this.dataDir) return;
    const files = readdirSync(this.dataDir).filter(
      (f) => f.startsWith("opt_") && f.endsWith(".json"),
    );
    this.index = [];
    for (const file of files) {
      try {
        const record = JSON.parse(
          readFileSync(join(this.dataDir, file), "utf-8"),
        ) as OptimizationRecord;
        this.index.push(this.toIndexEntry(record));
      } catch {
        // Skip corrupt files
      }
    }
    this.index.sort((a, b) => new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime());
    this.saveIndex();
  }

  private saveIndex(): void {
    if (!this.dataDir) return;
    this.ensureDir();
    writeFileSync(join(this.dataDir, "_index.json"), JSON.stringify(this.index, null, 2));
  }

  private writeRecord(record: OptimizationRecord): void {
    if (!this.dataDir) return;
    this.ensureDir();
    writeFileSync(join(this.dataDir, `${record.id}.json`), JSON.stringify(record, null, 2));
  }

  private loadRecord(id: string): OptimizationRecord | null {
    if (!this.dataDir) return null;
    const filePath = join(this.dataDir, `${id}.json`);
    if (!existsSync(filePath)) return null;
    try {
      return JSON.parse(readFileSync(filePath, "utf-8")) as OptimizationRecord;
    } catch {
      return null;
    }
  }

  private deleteRecordFile(id: string): void {
    if (!this.dataDir) return;
    const filePath = join(this.dataDir, `${id}.json`);
    try {
      if (existsSync(filePath)) unlinkSync(filePath);
    } catch {
      // best-effort
    }
  }

  private toIndexEntry(record: OptimizationRecord): OptimizationIndexEntry {
    const b = record.best;
    return {
      id: record.id,
      createdAt: record.createdAt,
      capital: record.params.capital,
      startDate: record.params.startDate,
      endDate: record.params.endDate,
      autoScreen: record.params.autoScreen,
      totalRounds: record.totalRounds,
      bestPoolLabel: b.poolLabel,
      bestStrategyLabel: b.strategyLabel,
      bestReturn: b.totalReturn,
      bestSharpe: b.sharpeRatio,
      bestMaxDrawdown: b.maxDrawdown,
      bestWinRate: b.winRate,
      bestSymbolCount: b.symbolCount,
      bestCompositeScore: b.compositeScore,
      tags: record.tags,
      starred: record.starred,
      notes: record.notes,
    };
  }

  /** Remove oldest non-starred results when exceeding maxResults. */
  private prune(): void {
    if (this.index.length <= this.maxResults) return;
    const sorted = [...this.index].sort(
      (a, b) => new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime(),
    );
    const excess = this.index.length - this.maxResults;
    const toRemove: string[] = [];
    for (let i = 0; i < sorted.length - 1; i++) {
      if (toRemove.length >= excess) break;
      if (!sorted[i].starred) toRemove.push(sorted[i].id);
    }
    for (const id of toRemove) {
      const idx = this.index.findIndex((e) => e.id === id);
      if (idx >= 0) this.index.splice(idx, 1);
      this.deleteRecordFile(id);
    }
  }
}
