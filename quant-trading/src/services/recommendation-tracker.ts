/**
 * RecommendationTracker — persistent recommendation tracking & win-rate statistics.
 *
 * Every time stock_recommend produces a recommendation, the tracker records it.
 * Later, the tracker fetches current prices to compute outcome:
 *   - unrealised P&L (if still within horizon)
 *   - realised P&L (if horizon expired or stop hit)
 *   - aggregate win-rate, avg return, best/worst picks
 *
 * Data model mirrors ScanHistoryStore: in-memory index + per-record JSON files.
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
import { todayCST } from "../utils/cst-time.js";

// ── Types ────────────────────────────────────────────────────────

export interface RecommendationEntry {
  /** Unique recommendation id */
  id: string;
  /** Stock symbol (e.g. "600519") */
  symbol: string;
  /** Stock name */
  name: string;
  /** Recommendation timestamp (ISO-8601) */
  createdAt: string;
  /** Date portion "YYYY-MM-DD" */
  date: string;
  /** Source mode: "classic" or "predictive" */
  mode: string;
  /** Screener / pipeline that produced this recommendation */
  screenerId: string;
  /** Rank within the recommendation batch */
  rank: number;
  /** Composite score at time of recommendation */
  score: number;
  /** Price at time of recommendation */
  priceAtRecommend: number;
  /** Assigned holding horizon */
  horizon: "short" | "mid" | "long";
  /** Horizon expiry date (YYYY-MM-DD) */
  horizonExpiry: string;
  /** Stop-loss price (if available) */
  stopLoss?: number;
  /** Take-profit price (if available) */
  takeProfit?: number;
  /** Current / last-checked price */
  currentPrice?: number;
  /** Last price-check timestamp */
  lastCheckedAt?: string;
  /** Return % = (currentPrice - priceAtRecommend) / priceAtRecommend * 100 */
  returnPct?: number;
  /** Max price reached after recommendation */
  maxPrice?: number;
  /** Min price reached after recommendation */
  minPrice?: number;
  /** Outcome status */
  status: "active" | "win" | "loss" | "expired" | "stopped";
  /** Resolution reason */
  resolution?: string;
  /** Backtest metrics at time of recommendation (classic mode) */
  backtestMetrics?: {
    totalReturn: number;
    sharpeRatio: number;
    maxDrawdown: number;
    winRate: number;
  };
  /** Strategy info */
  strategy?: string;
  /** Batch id — groups recommendations from the same call */
  batchId: string;
}

export interface RecommendationStats {
  /** Total recommendations tracked */
  total: number;
  /** Resolved (win + loss + expired + stopped) */
  resolved: number;
  /** Still active */
  active: number;
  /** Win count (returnPct > 0 at expiry / take-profit hit) */
  wins: number;
  /** Loss count */
  losses: number;
  /** Win rate % */
  winRate: number;
  /** Average return % (resolved only) */
  avgReturn: number;
  /** Median return % */
  medianReturn: number;
  /** Best single return % */
  bestReturn: number;
  /** Worst single return % */
  worstReturn: number;
  /** Average holding days for resolved */
  avgHoldingDays: number;
  /** Stats by mode */
  byMode: Record<string, { total: number; wins: number; winRate: number; avgReturn: number }>;
  /** Stats by horizon */
  byHorizon: Record<string, { total: number; wins: number; winRate: number; avgReturn: number }>;
  /** Stats by screener */
  byScreener: Record<string, { total: number; wins: number; winRate: number; avgReturn: number }>;
  /** Top 5 best picks */
  topPicks: Array<{ symbol: string; name: string; returnPct: number; date: string }>;
  /** Top 5 worst picks */
  worstPicks: Array<{ symbol: string; name: string; returnPct: number; date: string }>;
}

export interface TrackerQuery {
  /** Filter by status */
  status?: RecommendationEntry["status"];
  /** Filter by mode */
  mode?: string;
  /** Filter by horizon */
  horizon?: string;
  /** Filter by symbol */
  symbol?: string;
  /** Filter by date range start */
  after?: string;
  /** Filter by date range end */
  before?: string;
  /** Filter by screener */
  screenerId?: string;
  /** Max results */
  limit?: number;
  /** Sort: "date" | "return" */
  sortBy?: "date" | "return";
  sortDir?: "asc" | "desc";
}

export interface RecommendationTrackerConfig {
  dataDir?: string;
  maxRecords?: number;
}

// ── Horizon helpers ──────────────────────────────────────────────

const HORIZON_DAYS: Record<string, number> = {
  short: 10,
  mid: 60,
  long: 120,
};

function computeExpiry(date: string, horizon: string): string {
  const d = new Date(date);
  d.setDate(d.getDate() + (HORIZON_DAYS[horizon] ?? 60));
  return d.toISOString().slice(0, 10);
}

// ── Store implementation ─────────────────────────────────────────

const DEFAULT_MAX = 500;
const SUBDIR = "recommendation-tracker";

export class RecommendationTracker {
  private readonly dataDir: string | undefined;
  private readonly maxRecords: number;
  private records: RecommendationEntry[] = [];
  private readonly writeMutex = new AsyncMutex();
  private notify: ((msg: string) => void) | null = null;

  constructor(config: RecommendationTrackerConfig) {
    this.dataDir = config.dataDir ? join(config.dataDir, SUBDIR) : undefined;
    this.maxRecords = config.maxRecords ?? DEFAULT_MAX;
  }

  setNotify(fn: (msg: string) => void): void {
    this.notify = fn;
  }

  /** Load from disk on startup */
  start(): void {
    this.loadFromDisk();
  }

  get size(): number {
    return this.records.length;
  }

  // ── Record a new recommendation ───────────────────────────────

  async record(params: {
    symbol: string;
    name: string;
    mode: string;
    screenerId: string;
    rank: number;
    score: number;
    priceAtRecommend: number;
    horizon?: string;
    stopLoss?: number;
    takeProfit?: number;
    backtestMetrics?: RecommendationEntry["backtestMetrics"];
    strategy?: string;
    batchId: string;
  }): Promise<string> {
    const release = await this.writeMutex.acquire();
    try {
      const now = new Date();
      const date = now.toISOString().slice(0, 10);
      const horizon = (params.horizon ?? "mid") as "short" | "mid" | "long";
      const entry: RecommendationEntry = {
        id: `rec_${Date.now()}_${randomUUID().slice(0, 8)}`,
        symbol: params.symbol,
        name: params.name,
        createdAt: now.toISOString(),
        date,
        mode: params.mode,
        screenerId: params.screenerId,
        rank: params.rank,
        score: params.score,
        priceAtRecommend: params.priceAtRecommend,
        horizon,
        horizonExpiry: computeExpiry(date, horizon),
        stopLoss: params.stopLoss,
        takeProfit: params.takeProfit,
        status: "active",
        backtestMetrics: params.backtestMetrics,
        strategy: params.strategy,
        batchId: params.batchId,
      };

      this.records.push(entry);
      this.prune();
      this.saveToDisk();
      return entry.id;
    } finally {
      release();
    }
  }

  // ── Update prices & resolve outcomes ──────────────────────────

  async updatePrices(
    quoteFn: (symbol: string) => Promise<{ price: number } | null>,
  ): Promise<{ updated: number; resolved: number }> {
    const release = await this.writeMutex.acquire();
    try {
      const today = todayCST();
      let updated = 0;
      let resolved = 0;

      for (const rec of this.records) {
        if (rec.status !== "active") continue;
        try {
          const quote = await quoteFn(rec.symbol);
          if (!quote) continue;

          rec.currentPrice = quote.price;
          rec.lastCheckedAt = new Date().toISOString();
          rec.returnPct =
            ((quote.price - rec.priceAtRecommend) / rec.priceAtRecommend) * 100;
          rec.maxPrice = Math.max(rec.maxPrice ?? 0, quote.price);
          rec.minPrice = Math.min(rec.minPrice ?? Infinity, quote.price);
          updated++;

          // Check resolution conditions
          if (rec.takeProfit && quote.price >= rec.takeProfit) {
            rec.status = "win";
            rec.resolution = `触达止盈价 ¥${rec.takeProfit}`;
            resolved++;
          } else if (rec.stopLoss && quote.price <= rec.stopLoss) {
            rec.status = "stopped";
            rec.resolution = `触达止损价 ¥${rec.stopLoss}`;
            resolved++;
          } else if (today >= rec.horizonExpiry) {
            rec.status = rec.returnPct >= 0 ? "win" : "loss";
            rec.resolution = `持有周期到期(${rec.horizon}), 收益 ${rec.returnPct.toFixed(1)}%`;
            resolved++;
          }
        } catch {
          // Non-fatal — skip this symbol
        }
      }

      if (updated > 0) this.saveToDisk();

      if (resolved > 0 && this.notify) {
        const newlyResolved = this.records.filter(
          (r) => r.status !== "active" && r.lastCheckedAt === new Date().toISOString().slice(0, 19),
        );
        if (newlyResolved.length > 0) {
          const lines = newlyResolved.map(
            (r) =>
              `${r.name}(${r.symbol}): ${r.status === "win" ? "✅盈利" : "❌亏损"} ${r.returnPct?.toFixed(1)}%`,
          );
          this.notify(`📊 推荐追踪更新:\n${lines.join("\n")}`);
        }
      }

      return { updated, resolved };
    } finally {
      release();
    }
  }

  // ── Query ─────────────────────────────────────────────────────

  query(q: TrackerQuery = {}): RecommendationEntry[] {
    let results = this.records.filter((r) => {
      if (q.status && r.status !== q.status) return false;
      if (q.mode && r.mode !== q.mode) return false;
      if (q.horizon && r.horizon !== q.horizon) return false;
      if (q.symbol && r.symbol !== q.symbol) return false;
      if (q.screenerId && r.screenerId !== q.screenerId) return false;
      if (q.after && r.date < q.after) return false;
      if (q.before && r.date > q.before) return false;
      return true;
    });

    const sortBy = q.sortBy ?? "date";
    const mult = (q.sortDir ?? "desc") === "desc" ? -1 : 1;
    results.sort((a, b) => {
      if (sortBy === "return") return mult * ((a.returnPct ?? 0) - (b.returnPct ?? 0));
      return mult * (new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime());
    });

    return results.slice(0, q.limit ?? 50);
  }

  /** Get a single record by id */
  get(id: string): RecommendationEntry | undefined {
    return this.records.find((r) => r.id === id);
  }

  // ── Statistics ────────────────────────────────────────────────

  computeStats(q: TrackerQuery = {}): RecommendationStats {
    const all = this.query({ ...q, limit: 99999 });
    const resolved = all.filter((r) => r.status !== "active");
    const active = all.filter((r) => r.status === "active");
    const wins = resolved.filter((r) => r.status === "win");
    const losses = resolved.filter((r) => r.status === "loss" || r.status === "stopped" || r.status === "expired");

    const returns = resolved
      .map((r) => r.returnPct ?? 0)
      .sort((a, b) => a - b);

    const avgReturn = returns.length > 0
      ? returns.reduce((s, v) => s + v, 0) / returns.length
      : 0;
    const medianReturn = returns.length > 0
      ? returns[Math.floor(returns.length / 2)]
      : 0;

    // Holding days
    const holdingDays = resolved.map((r) => {
      const start = new Date(r.date).getTime();
      const end = r.lastCheckedAt
        ? new Date(r.lastCheckedAt).getTime()
        : new Date(r.horizonExpiry).getTime();
      return Math.max(1, Math.round((end - start) / (24 * 60 * 60 * 1000)));
    });
    const avgHoldingDays = holdingDays.length > 0
      ? Math.round(holdingDays.reduce((s, v) => s + v, 0) / holdingDays.length)
      : 0;

    // Group-by helper
    const groupStats = (
      key: (r: RecommendationEntry) => string,
    ): Record<string, { total: number; wins: number; winRate: number; avgReturn: number }> => {
      const groups = new Map<string, RecommendationEntry[]>();
      for (const r of resolved) {
        const k = key(r);
        const arr = groups.get(k) ?? [];
        arr.push(r);
        groups.set(k, arr);
      }
      const result: Record<string, { total: number; wins: number; winRate: number; avgReturn: number }> = {};
      for (const [k, items] of groups) {
        const w = items.filter((i) => i.status === "win").length;
        const avg = items.reduce((s, i) => s + (i.returnPct ?? 0), 0) / items.length;
        result[k] = {
          total: items.length,
          wins: w,
          winRate: items.length > 0 ? Math.round((w / items.length) * 1000) / 10 : 0,
          avgReturn: Math.round(avg * 10) / 10,
        };
      }
      return result;
    };

    // Top / worst picks
    const sortedReturns = [...resolved].sort(
      (a, b) => (b.returnPct ?? 0) - (a.returnPct ?? 0),
    );
    const topPicks = sortedReturns.slice(0, 5).map((r) => ({
      symbol: r.symbol,
      name: r.name,
      returnPct: Math.round((r.returnPct ?? 0) * 10) / 10,
      date: r.date,
    }));
    const worstPicks = sortedReturns
      .slice(-5)
      .reverse()
      .map((r) => ({
        symbol: r.symbol,
        name: r.name,
        returnPct: Math.round((r.returnPct ?? 0) * 10) / 10,
        date: r.date,
      }));

    return {
      total: all.length,
      resolved: resolved.length,
      active: active.length,
      wins: wins.length,
      losses: losses.length,
      winRate: resolved.length > 0 ? Math.round((wins.length / resolved.length) * 1000) / 10 : 0,
      avgReturn: Math.round(avgReturn * 10) / 10,
      medianReturn: Math.round(medianReturn * 10) / 10,
      bestReturn: returns.length > 0 ? Math.round(returns[returns.length - 1] * 10) / 10 : 0,
      worstReturn: returns.length > 0 ? Math.round(returns[0] * 10) / 10 : 0,
      avgHoldingDays,
      byMode: groupStats((r) => r.mode),
      byHorizon: groupStats((r) => r.horizon),
      byScreener: groupStats((r) => r.screenerId),
      topPicks,
      worstPicks,
    };
  }

  // ── Persistence ───────────────────────────────────────────────

  private loadFromDisk(): void {
    if (!this.dataDir) return;
    const filePath = join(this.dataDir, "recommendations.json");
    if (!existsSync(filePath)) return;
    try {
      const raw = JSON.parse(readFileSync(filePath, "utf-8"));
      if (Array.isArray(raw)) {
        this.records = raw;
      }
    } catch {
      // Corrupted — start fresh
      this.records = [];
    }
  }

  private saveToDisk(): void {
    if (!this.dataDir) return;
    try {
      if (!existsSync(this.dataDir)) mkdirSync(this.dataDir, { recursive: true });
      writeFileSync(
        join(this.dataDir, "recommendations.json"),
        JSON.stringify(this.records, null, 2),
        "utf-8",
      );
    } catch {
      // Non-fatal
    }
  }

  private prune(): void {
    if (this.records.length > this.maxRecords) {
      // Keep most recent
      this.records.sort(
        (a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime(),
      );
      this.records = this.records.slice(0, this.maxRecords);
    }
  }
}
