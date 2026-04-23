/**
 * SignalHitRateTracker — tracks live signal prediction accuracy.
 *
 * After a signal is issued, records the signal and schedules
 * lookback checks at T+1, T+3, T+5, T+10 days.
 * On each check, compares actual price movement to the predicted direction.
 *
 * Records:
 *   - hit: actual price moved in predicted direction (buy→up, sell→down)
 *   - miss: actual price moved against predicted direction
 *   - pending: not yet checked (future date)
 *
 * Provides per-strategy and aggregate hit rate statistics.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import type { TradingSignal } from "../types.js";
import { todayCST } from "../utils/cst-time.js";

export type HitCheckResult = "pending" | "hit" | "miss" | "no_data";

export interface SignalTrackRecord {
  /** Original signal timestamp (unix ms). */
  signalTimestamp: number;
  symbol: string;
  direction: "buy" | "sell" | "short" | "cover";
  signalPrice: number;
  strategyId: string;
  reason?: string;
  confidence?: number;
  /** Actual prices at T+N days (filled when checked). */
  checkpoints: Record<
    string,
    {
      targetDate: string; // YYYY-MM-DD
      price?: number;
      result: HitCheckResult;
      checkedAt?: string;
    }
  >;
}

export interface HitRateStats {
  totalSignals: number;
  checkedSignals: number;
  /** Per-horizon hit rates. */
  horizons: Record<
    string,
    {
      checked: number;
      hits: number;
      misses: number;
      noData: number;
      hitRate: number; // 0-1
    }
  >;
  /** Per-strategy breakdown. */
  strategies: Record<
    string,
    {
      total: number;
      hits: number;
      misses: number;
      hitRate: number;
    }
  >;
}

export interface SignalHitRateTrackerOptions {
  /** Horizons in trading days (default: [1, 3, 5, 10]). */
  horizons?: number[];
  /** Maximum tracked signals (oldest pruned first). */
  maxRecords?: number;
  /** Data directory for persistence. */
  dataDir?: string | null;
}

/** The horizons (in calendar days) at which we check signal accuracy. */
const DEFAULT_HORIZONS = [1, 3, 5, 10];

export class SignalHitRateTracker {
  private records: SignalTrackRecord[] = [];
  private readonly horizons: number[];
  private readonly maxRecords: number;
  private readonly dataDir: string | null;
  private quoteFn?: (symbol: string) => Promise<{ price: number } | null>;

  constructor(opts: SignalHitRateTrackerOptions = {}) {
    this.horizons = opts.horizons ?? DEFAULT_HORIZONS;
    this.maxRecords = opts.maxRecords ?? 500;
    this.dataDir = opts.dataDir ?? null;
    this.loadFromDisk();
  }

  /** Set quote function for price lookups. */
  setQuoteFn(fn: (symbol: string) => Promise<{ price: number } | null>): void {
    this.quoteFn = fn;
  }

  /** Record a new signal for future accuracy tracking. */
  trackSignal(signal: TradingSignal): void {
    const now = new Date();
    const checkpoints: SignalTrackRecord["checkpoints"] = {};

    for (const d of this.horizons) {
      const target = new Date(now);
      target.setDate(target.getDate() + d);
      checkpoints[`T+${d}`] = {
        targetDate: target.toISOString().slice(0, 10),
        result: "pending",
      };
    }

    this.records.push({
      signalTimestamp: signal.timestamp,
      symbol: signal.symbol,
      direction: signal.direction,
      signalPrice: signal.price,
      strategyId: signal.strategyId,
      reason: signal.reason,
      confidence: signal.confidence,
      checkpoints,
    });

    // Prune oldest if over limit
    if (this.records.length > this.maxRecords) {
      this.records = this.records.slice(-this.maxRecords);
    }

    this.saveToDisk();
  }

  /**
   * Run accuracy checks on all pending checkpoints whose target date has passed.
   * Returns number of checkpoints updated.
   */
  async checkAccuracy(): Promise<number> {
    if (!this.quoteFn) return 0;

    const today = todayCST();
    let updated = 0;

    // Collect symbols needing quotes
    const symbolsToCheck = new Set<string>();
    for (const rec of this.records) {
      for (const cp of Object.values(rec.checkpoints)) {
        if (cp.result === "pending" && cp.targetDate <= today) {
          symbolsToCheck.add(rec.symbol);
        }
      }
    }

    if (symbolsToCheck.size === 0) return 0;

    // Fetch quotes in parallel
    const quotes = new Map<string, number>();
    await Promise.allSettled(
      [...symbolsToCheck].map(async (symbol) => {
        const q = await this.quoteFn!(symbol);
        if (q) quotes.set(symbol, q.price);
      }),
    );

    // Update checkpoints
    for (const rec of this.records) {
      for (const [horizon, cp] of Object.entries(rec.checkpoints)) {
        if (cp.result !== "pending" || cp.targetDate > today) continue;

        const currentPrice = quotes.get(rec.symbol);
        if (currentPrice == null) {
          cp.result = "no_data";
          cp.checkedAt = new Date().toISOString();
          updated++;
          continue;
        }

        cp.price = currentPrice;
        cp.checkedAt = new Date().toISOString();

        // Determine hit/miss based on signal direction
        const priceUp = currentPrice > rec.signalPrice;
        const priceDown = currentPrice < rec.signalPrice;
        const isBullish = rec.direction === "buy" || rec.direction === "cover";

        if (isBullish) {
          cp.result = priceUp ? "hit" : priceDown ? "miss" : "hit"; // flat counts as hit for buy
        } else {
          cp.result = priceDown ? "hit" : priceUp ? "miss" : "hit"; // flat counts as hit for sell
        }

        updated++;
      }
    }

    if (updated > 0) this.saveToDisk();
    return updated;
  }

  /** Get comprehensive hit rate statistics. */
  getStats(): HitRateStats {
    const horizons: HitRateStats["horizons"] = {};
    const stratMap = new Map<string, { total: number; hits: number; misses: number }>();

    // Initialize horizons
    for (const d of this.horizons) {
      const key = `T+${d}`;
      horizons[key] = { checked: 0, hits: 0, misses: 0, noData: 0, hitRate: 0 };
    }

    for (const rec of this.records) {
      // Per-strategy accumulator
      if (!stratMap.has(rec.strategyId)) {
        stratMap.set(rec.strategyId, { total: 0, hits: 0, misses: 0 });
      }
      const strat = stratMap.get(rec.strategyId)!;
      strat.total++;

      let anyHit = false;
      let anyMiss = false;

      for (const [horizonKey, cp] of Object.entries(rec.checkpoints)) {
        const h = horizons[horizonKey];
        if (!h) continue;

        if (cp.result === "hit") {
          h.checked++;
          h.hits++;
          anyHit = true;
        } else if (cp.result === "miss") {
          h.checked++;
          h.misses++;
          anyMiss = true;
        } else if (cp.result === "no_data") {
          h.noData++;
        }
      }

      // Strategy-level: count as hit if majority of horizons are hits
      if (anyHit && !anyMiss) strat.hits++;
      else if (anyMiss && !anyHit) strat.misses++;
      else if (anyHit && anyMiss) strat.hits++; // mixed → count as partial hit
    }

    // Calculate hit rates
    for (const h of Object.values(horizons)) {
      h.hitRate = h.checked > 0 ? h.hits / h.checked : 0;
    }

    const strategies: HitRateStats["strategies"] = {};
    for (const [id, s] of stratMap) {
      strategies[id] = {
        total: s.total,
        hits: s.hits,
        misses: s.misses,
        hitRate: s.total > 0 ? s.hits / s.total : 0,
      };
    }

    return {
      totalSignals: this.records.length,
      checkedSignals: this.records.filter((r) =>
        Object.values(r.checkpoints).some((cp) => cp.result !== "pending"),
      ).length,
      horizons,
      strategies,
    };
  }

  /** Get recent tracking records (newest first). */
  getRecent(limit = 20): SignalTrackRecord[] {
    return this.records.slice(-limit).reverse();
  }

  /** Total tracked records count. */
  get size(): number {
    return this.records.length;
  }

  // ── Persistence ────────────────────────────────────────────

  private persistPath(): string | null {
    if (!this.dataDir) return null;
    return `${this.dataDir}/signal-hitrate.json`;
  }

  private saveToDisk(): void {
    const p = this.persistPath();
    if (!p) return;
    const dir = dirname(p);
    if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
    writeFileSync(p, JSON.stringify(this.records, null, 2));
  }

  private loadFromDisk(): void {
    const p = this.persistPath();
    if (!p || !existsSync(p)) return;
    try {
      this.records = JSON.parse(readFileSync(p, "utf-8")) as SignalTrackRecord[];
    } catch {
      // Ignore corrupt data
    }
  }
}
