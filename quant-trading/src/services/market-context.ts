/**
 * MarketContextService — unified market enrichment data layer.
 *
 * Aggregates real-time data from 6 analytical modules:
 *   - 市场情绪 (Market Sentiment)
 *   - 连板分析 (Lianban / Consecutive Limit-Up Analysis)
 *   - 竞价抢筹 (Auction Bidding)
 *   - 时间催化 (Event Catalyst)
 *   - ST重整跟踪 (ST Restructuring Tracking)
 *   - 宏观环境 (Macro Regime)
 *
 * Downstream consumers (LiveEngine, RiskGate, PositionSizer, ConditionalOrder)
 * call `getContext(symbol)` which reads from in-memory cache (<1ms).
 * Data is refreshed in the background at module-specific intervals.
 *
 * Refresh cadence (during trading hours only):
 *   sentiment + lianban   — every 5 min
 *   auction               — every 10s during 9:15-9:25 / 14:57-15:00
 *   catalyst + ST         — every 60 min
 *   macro regime          — every 30 min
 */

import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

// ── Types ────────────────────────────────────────────────────────

export interface SentimentSnapshot {
  /** Composite sentiment score (0-100). */
  score: number;
  /** Tone label. */
  tone: "强势" | "偏多" | "中性" | "偏空" | "弱势";
  /** Board failure rate (炸板率, 0-1). */
  zbgcRate: number;
  /** Promotion rate from N→N+1 boards (0-1). */
  promotionRate: number;
  /** Limit-up / limit-down ratio. */
  ztDtRatio: number;
  /** Total limit-up count today. */
  totalZt: number;
  /** Highest consecutive board count. */
  highestBoard: number;
  /** Data timestamp. */
  updatedAt: number;
}

export interface LianbanEntry {
  /** Current consecutive board level. */
  level: number;
  /** Board quality score (0-100). */
  boardQuality: number;
  /** Whether the stock is currently in the limit-up pool. */
  inPool: boolean;
}

export interface AuctionSignal {
  /** Signal strength (0-100). */
  score: number;
  /** Signal type. */
  signalType: "strong" | "moderate" | "weak" | "none";
  /** Gap-up percentage. */
  gapUpPct: number;
  /** Descriptive signals. */
  signals: string[];
  /** Risk notes. */
  riskNotes: string[];
}

export interface CatalystEvent {
  /** Event type (earnings, policy, corporate_action, us_mover, etc.). */
  eventType: string;
  /** Days until event (negative = past). */
  daysUntil: number;
  /** Estimated impact level. */
  impact: "high" | "medium" | "low";
  /** Brief description. */
  description: string;
}

export interface STStatus {
  /** Restructuring lifecycle stage (PRE-0 to MAIN-7, REMOVED). */
  stage: string;
  /** Estimated next milestone. */
  nextMilestone: string;
  /** Risk level for position sizing. */
  riskLevel: "extreme" | "high" | "medium" | "low";
}

export type MacroRegime = "BULL" | "BEAR" | "SIDEWAYS" | "VOLATILE";
export type VolatilityRegime = "LOW" | "NORMAL" | "HIGH" | "EXTREME";

/**
 * Per-symbol market context used by downstream consumers.
 * Cheap to construct from cached data.
 */
export interface MarketContext {
  // ── Market-wide ──
  sentiment: SentimentSnapshot | null;
  macroRegime: MacroRegime | null;
  volatilityRegime: VolatilityRegime | null;

  // ── Per-symbol ──
  lianban: LianbanEntry | null;
  auction: AuctionSignal | null;
  catalyst: CatalystEvent | null;
  stStatus: STStatus | null;

  /** Cache freshness — epoch ms of oldest data component. */
  oldestDataAt: number;
}

// ── Internal cache entry with TTL ────────────────────────────────

interface CacheEntry<T> {
  data: T;
  updatedAt: number;
  ttlMs: number;
}

function isStale<T>(entry: CacheEntry<T> | null): boolean {
  if (!entry) return true;
  return Date.now() - entry.updatedAt > entry.ttlMs;
}

// ── Service ──────────────────────────────────────────────────────

export class MarketContextService {
  private cfg: QuantTradingConfig;

  // ── Market-wide caches ──
  private sentimentCache: CacheEntry<SentimentSnapshot> | null = null;
  private lianbanCache: CacheEntry<Map<string, LianbanEntry>> | null = null;
  private macroCache: CacheEntry<{ regime: MacroRegime; volatility: VolatilityRegime }> | null =
    null;

  // ── Per-symbol caches ──
  private auctionCache: CacheEntry<Map<string, AuctionSignal>> | null = null;
  private catalystCache: CacheEntry<Map<string, CatalystEvent>> | null = null;
  private stCache: CacheEntry<Map<string, STStatus>> | null = null;

  // ── Refresh intervals ──
  private refreshTimer: ReturnType<typeof setInterval> | null = null;
  private auctionTimer: ReturnType<typeof setInterval> | null = null;
  private loading = false;

  // TTL constants (ms)
  private static readonly SENTIMENT_TTL = 5 * 60_000; // 5 min
  private static readonly LIANBAN_TTL = 5 * 60_000;
  private static readonly MACRO_TTL = 30 * 60_000; // 30 min
  private static readonly AUCTION_TTL = 15_000; // 15s
  private static readonly CATALYST_TTL = 60 * 60_000; // 1 hour
  private static readonly ST_TTL = 60 * 60_000;

  constructor(cfg: QuantTradingConfig) {
    this.cfg = cfg;
  }

  // ── Public API ─────────────────────────────────────────────────

  /**
   * Get enriched market context for a given symbol.
   * Reads only from in-memory cache — sub-millisecond latency.
   */
  getContext(symbol: string): MarketContext {
    const sentiment = this.sentimentCache?.data ?? null;
    const macro = this.macroCache?.data ?? null;
    const lianban = this.lianbanCache?.data?.get(symbol) ?? null;
    const auction = this.auctionCache?.data?.get(symbol) ?? null;
    const catalyst = this.catalystCache?.data?.get(symbol) ?? null;
    const stStatus = this.stCache?.data?.get(symbol) ?? null;

    const timestamps = [
      this.sentimentCache?.updatedAt,
      this.lianbanCache?.updatedAt,
      this.macroCache?.updatedAt,
      this.auctionCache?.updatedAt,
      this.catalystCache?.updatedAt,
      this.stCache?.updatedAt,
    ].filter((t): t is number => t != null);

    return {
      sentiment,
      macroRegime: macro?.regime ?? null,
      volatilityRegime: macro?.volatility ?? null,
      lianban,
      auction,
      catalyst,
      stStatus,
      oldestDataAt: timestamps.length > 0 ? Math.min(...timestamps) : 0,
    };
  }

  /** Get the current market-wide sentiment snapshot. */
  getSentiment(): SentimentSnapshot | null {
    return this.sentimentCache?.data ?? null;
  }

  /** Get the macro regime label. */
  getMacroRegime(): MacroRegime | null {
    return this.macroCache?.data?.regime ?? null;
  }

  /** Get the full lianban pool map. */
  getLianbanPool(): Map<string, LianbanEntry> {
    return this.lianbanCache?.data ?? new Map();
  }

  /** Check if any cached data is available (i.e. at least one refresh succeeded). */
  hasData(): boolean {
    return this.sentimentCache != null || this.lianbanCache != null || this.macroCache != null;
  }

  // ── Lifecycle ──────────────────────────────────────────────────

  /**
   * Start background refresh timers.
   * Call once after plugin activation.
   */
  start(): void {
    // Immediate first load
    void this.refreshAll();

    // Main refresh: sentiment + lianban every 5 min, macro/catalyst/ST every 30/60 min
    this.refreshTimer = setInterval(() => {
      if (!this.isTradingWindow()) return;
      void this.refreshCore();
    }, 5 * 60_000);

    // Auction refresh: every 10s during auction windows
    this.auctionTimer = setInterval(() => {
      if (!this.isAuctionWindow()) return;
      void this.refreshAuction();
    }, 10_000);
  }

  /** Stop all background timers. */
  stop(): void {
    if (this.refreshTimer) {
      clearInterval(this.refreshTimer);
      this.refreshTimer = null;
    }
    if (this.auctionTimer) {
      clearInterval(this.auctionTimer);
      this.auctionTimer = null;
    }
  }

  /** Force a full refresh of all data. Returns when complete. */
  async refreshAll(): Promise<void> {
    await Promise.all([this.refreshCore(), this.refreshSlow(), this.refreshAuction()]);
  }

  // ── Internal refresh methods ───────────────────────────────────

  /** Refresh high-frequency data: sentiment + lianban. */
  private async refreshCore(): Promise<void> {
    if (this.loading) return;
    this.loading = true;
    try {
      const result = await runPythonScript<{
        sentiment?: RawSentiment;
        lianban_pool?: Record<string, RawLianbanEntry>;
      }>(
        "scripts/market_context_provider.py",
        { action: "core_snapshot" },
        this.cfg,
        { timeoutMs: 30_000 },
      );

      if (result.ok && result.data) {
        const now = Date.now();
        const d = result.data;

        if (d.sentiment) {
          this.sentimentCache = {
            data: {
              score: d.sentiment.score ?? 50,
              tone: mapTone(d.sentiment.tone),
              zbgcRate: d.sentiment.zbgc_rate ?? 0,
              promotionRate: d.sentiment.promotion_rate ?? 0,
              ztDtRatio: d.sentiment.zt_dt_ratio ?? 1,
              totalZt: d.sentiment.total_zt ?? 0,
              highestBoard: d.sentiment.highest_board ?? 0,
              updatedAt: now,
            },
            updatedAt: now,
            ttlMs: MarketContextService.SENTIMENT_TTL,
          };
        }

        if (d.lianban_pool) {
          const pool = new Map<string, LianbanEntry>();
          for (const [sym, entry] of Object.entries(d.lianban_pool)) {
            pool.set(sym, {
              level: entry.level ?? 0,
              boardQuality: entry.board_quality ?? 0,
              inPool: entry.in_pool ?? true,
            });
          }
          this.lianbanCache = { data: pool, updatedAt: now, ttlMs: MarketContextService.LIANBAN_TTL };
        }
      }
    } catch {
      // Non-critical — stale data is better than no data
    } finally {
      this.loading = false;
    }
  }

  /** Refresh low-frequency data: macro regime, catalyst, ST. */
  private async refreshSlow(): Promise<void> {
    try {
      const result = await runPythonScript<{
        macro_regime?: string;
        volatility_regime?: string;
        catalysts?: Record<string, RawCatalyst>;
        st_statuses?: Record<string, RawSTStatus>;
      }>(
        "scripts/market_context_provider.py",
        { action: "slow_snapshot" },
        this.cfg,
        { timeoutMs: 60_000 },
      );

      if (result.ok && result.data) {
        const now = Date.now();
        const d = result.data;

        if (d.macro_regime) {
          this.macroCache = {
            data: {
              regime: mapRegime(d.macro_regime),
              volatility: mapVolatility(d.volatility_regime),
            },
            updatedAt: now,
            ttlMs: MarketContextService.MACRO_TTL,
          };
        }

        if (d.catalysts) {
          const map = new Map<string, CatalystEvent>();
          for (const [sym, c] of Object.entries(d.catalysts)) {
            map.set(sym, {
              eventType: c.event_type ?? "unknown",
              daysUntil: c.days_until ?? 999,
              impact: mapImpact(c.impact),
              description: c.description ?? "",
            });
          }
          this.catalystCache = { data: map, updatedAt: now, ttlMs: MarketContextService.CATALYST_TTL };
        }

        if (d.st_statuses) {
          const map = new Map<string, STStatus>();
          for (const [sym, s] of Object.entries(d.st_statuses)) {
            map.set(sym, {
              stage: s.stage ?? "unknown",
              nextMilestone: s.next_milestone ?? "",
              riskLevel: mapSTRisk(s.risk_level),
            });
          }
          this.stCache = { data: map, updatedAt: now, ttlMs: MarketContextService.ST_TTL };
        }
      }
    } catch {
      // Non-critical
    }
  }

  /** Refresh auction signals during auction windows. */
  private async refreshAuction(): Promise<void> {
    try {
      const result = await runPythonScript<{
        auction_signals?: Record<string, RawAuction>;
      }>(
        "scripts/market_context_provider.py",
        { action: "auction_snapshot" },
        this.cfg,
        { timeoutMs: 10_000 },
      );

      if (result.ok && result.data?.auction_signals) {
        const now = Date.now();
        const map = new Map<string, AuctionSignal>();
        for (const [sym, a] of Object.entries(result.data.auction_signals)) {
          map.set(sym, {
            score: a.score ?? 0,
            signalType: mapSignalType(a.signal_type),
            gapUpPct: a.gap_up_pct ?? 0,
            signals: a.signals ?? [],
            riskNotes: a.risk_notes ?? [],
          });
        }
        this.auctionCache = { data: map, updatedAt: now, ttlMs: MarketContextService.AUCTION_TTL };
      }
    } catch {
      // Non-critical
    }
  }

  // ── Time window helpers ────────────────────────────────────────

  /** Returns true during A-share trading hours (9:15-15:05 CST weekdays). */
  private isTradingWindow(): boolean {
    const now = new Date();
    const day = now.getDay();
    if (day === 0 || day === 6) return false;
    // Use CST (UTC+8) for A-share schedule
    const cstHour = (now.getUTCHours() + 8) % 24;
    const cstMin = now.getUTCMinutes();
    const hm = cstHour * 100 + cstMin;
    return hm >= 915 && hm <= 1505;
  }

  /** Returns true during auction windows (9:15-9:30, 14:57-15:00 CST). */
  private isAuctionWindow(): boolean {
    const now = new Date();
    const day = now.getDay();
    if (day === 0 || day === 6) return false;
    const cstHour = (now.getUTCHours() + 8) % 24;
    const cstMin = now.getUTCMinutes();
    const hm = cstHour * 100 + cstMin;
    return (hm >= 915 && hm <= 930) || (hm >= 1457 && hm <= 1500);
  }
}

// ── Raw Python response shapes ───────────────────────────────────

interface RawSentiment {
  score?: number;
  tone?: string;
  zbgc_rate?: number;
  promotion_rate?: number;
  zt_dt_ratio?: number;
  total_zt?: number;
  highest_board?: number;
}

interface RawLianbanEntry {
  level?: number;
  board_quality?: number;
  in_pool?: boolean;
}

interface RawCatalyst {
  event_type?: string;
  days_until?: number;
  impact?: string;
  description?: string;
}

interface RawSTStatus {
  stage?: string;
  next_milestone?: string;
  risk_level?: string;
}

interface RawAuction {
  score?: number;
  signal_type?: string;
  gap_up_pct?: number;
  signals?: string[];
  risk_notes?: string[];
}

// ── Mapping helpers ──────────────────────────────────────────────

function mapTone(t?: string): SentimentSnapshot["tone"] {
  const valid = ["强势", "偏多", "中性", "偏空", "弱势"] as const;
  return valid.includes(t as never) ? (t as SentimentSnapshot["tone"]) : "中性";
}

function mapRegime(r?: string): MacroRegime {
  const map: Record<string, MacroRegime> = {
    BULL: "BULL",
    BEAR: "BEAR",
    SIDEWAYS: "SIDEWAYS",
    VOLATILE: "VOLATILE",
    "强势": "BULL",
    "偏多": "BULL",
    "中性": "SIDEWAYS",
    "偏空": "BEAR",
    "弱势": "BEAR",
  };
  return map[r ?? ""] ?? "SIDEWAYS";
}

function mapVolatility(v?: string): VolatilityRegime {
  const map: Record<string, VolatilityRegime> = {
    LOW: "LOW",
    NORMAL: "NORMAL",
    HIGH: "HIGH",
    EXTREME: "EXTREME",
  };
  return map[v ?? ""] ?? "NORMAL";
}

function mapImpact(i?: string): CatalystEvent["impact"] {
  if (i === "high" || i === "medium" || i === "low") return i;
  return "medium";
}

function mapSTRisk(r?: string): STStatus["riskLevel"] {
  if (r === "extreme" || r === "high" || r === "medium" || r === "low") return r;
  return "high";
}

function mapSignalType(s?: string): AuctionSignal["signalType"] {
  if (s === "strong" || s === "moderate" || s === "weak" || s === "none") return s;
  return "none";
}
