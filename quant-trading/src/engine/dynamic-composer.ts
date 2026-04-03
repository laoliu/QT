/**
 * Dynamic Strategy Composer — combine any N builtin strategies at runtime.
 *
 * Four combination methods:
 *   - voting:         Majority vote — trade when ≥ minVotes strategies agree
 *   - weighted-score: Weighted signal sum — trade when score exceeds threshold
 *   - gated:          Primary strategy + filter strategies must all confirm
 *   - cascade:        Sequential filters — signal passes through each stage
 */
import type { TradingSignal } from "../types.js";
import type { BuiltinStrategy, BuiltinStrategyContext } from "./strategies/index.js";

// ── Types ────────────────────────────────────────────────────────

export type CompositionMethod = "voting" | "weighted-score" | "gated" | "cascade";

export interface SubStrategyConfig {
  /** Strategy ID from the registry. */
  strategyId: string;
  /** Weight for weighted-score method (default 1). */
  weight?: number;
  /** Role in gated method: "primary" emits the signal, "filter" must confirm. */
  role?: "primary" | "filter";
  /** Parameter overrides for this sub-strategy. */
  params?: Record<string, unknown>;
}

export interface CompositionConfig {
  /** Unique ID for this composition. */
  id: string;
  /** Display name. */
  name: string;
  /** Description. */
  description?: string;
  /** How to combine sub-strategy signals. */
  method: CompositionMethod;
  /** Sub-strategies to combine. */
  strategies: SubStrategyConfig[];
  /** Minimum votes required (voting method, default: ceil(N/2)). */
  minVotes?: number;
  /** Score threshold to trigger trade (weighted-score method, default: 0.3). */
  scoreThreshold?: number;
  /** Lookback window (bars) for signal memory — a sub-strategy's signal is
   *  remembered for this many bars when counting votes/scores (default: 5). */
  signalLookback?: number;
  /** Optional: only apply to stocks matching this profile. */
  targetProfile?: StockProfileFilter;
  createdAt?: string;
  updatedAt?: string;
}

/** Filter to match stock profiles for targeted strategy routing. */
export interface StockProfileFilter {
  volatility?: string[];
  trendRegime?: string[];
  marketCap?: string[];
  sectors?: string[];
}

/** Stock profile produced by the profiler. */
export interface StockProfile {
  symbol: string;
  name: string;
  ok: boolean;
  error?: string;
  endDate?: string;
  dataPoints?: number;
  volatility?: { class: string; annualized: number };
  trend?: { class: string; ma20?: number; ma60?: number; recentReturn?: number };
  marketCap?: { class: string; value_yi: number };
  liquidity?: { class: string; value: number };
  momentum?: { class: string; return20d: number };
  volumeTrend?: { class: string; ratio: number };
  sector?: string;
  profileKey?: string;
}

/** Strategy recommendation for a single stock. */
export interface StrategyRecommendation {
  symbol: string;
  ok: boolean;
  error?: string;
  profile?: StockProfile;
  combo?: {
    name: string;
    method: string;
    strategies: SubStrategyConfig[];
    minVotes?: number;
  };
  reason?: string;
  source?: "data-driven" | "rule-based" | "fallback";
  performance?: {
    avgReturn: number;
    avgSharpe: number;
    avgMaxDD: number;
    avgWinRate: number;
    positiveRate: string;
  };
}

/**
 * Resolved sub-strategy: config paired with its instantiated BuiltinStrategy.
 */
interface ResolvedSub {
  config: SubStrategyConfig;
  strategy: BuiltinStrategy;
}

/** Remembered signal from a sub-strategy. */
interface SignalMemoryEntry {
  direction: "buy" | "sell";
  barIndex: number;
  reason?: string;
}

/** Default lookback window (bars) for signal memory. */
const DEFAULT_LOOKBACK = 5;

/**
 * Update signal memory for a sub-strategy based on its current bar output,
 * and return the current bar's signals.
 */
function updateMemory(
  memory: Map<string, SignalMemoryEntry>,
  sub: ResolvedSub,
  ctx: BuiltinStrategyContext,
): TradingSignal[] {
  const result = sub.strategy.next(ctx);
  const sigs = result ? (Array.isArray(result) ? result : [result]) : [];
  for (const sig of sigs) {
    if (sig.direction === "buy" || sig.direction === "sell") {
      memory.set(sub.config.strategyId, {
        direction: sig.direction,
        barIndex: ctx.index,
        reason: sig.reason,
      });
    }
  }
  return sigs;
}

/**
 * Check whether a sub-strategy has a recent signal within the lookback window.
 */
function getRecentSignal(
  memory: Map<string, SignalMemoryEntry>,
  strategyId: string,
  currentIndex: number,
  lookback: number,
): SignalMemoryEntry | undefined {
  const entry = memory.get(strategyId);
  if (entry && currentIndex - entry.barIndex <= lookback) return entry;
  return undefined;
}

// ── Composer ─────────────────────────────────────────────────────

/**
 * Build a BuiltinStrategy from a CompositionConfig + resolved sub-strategies.
 *
 * @param config   Composition configuration
 * @param subs     Resolved sub-strategies (order matches config.strategies)
 */
export function composeStrategy(config: CompositionConfig, subs: ResolvedSub[]): BuiltinStrategy {
  switch (config.method) {
    case "voting":
      return buildVotingStrategy(config, subs);
    case "weighted-score":
      return buildWeightedScoreStrategy(config, subs);
    case "gated":
      return buildGatedStrategy(config, subs);
    case "cascade":
      return buildCascadeStrategy(config, subs);
    default:
      throw new Error(`Unknown composition method: ${config.method}`);
  }
}

// ── Voting ───────────────────────────────────────────────────────

function buildVotingStrategy(config: CompositionConfig, subs: ResolvedSub[]): BuiltinStrategy {
  const minVotes = config.minVotes ?? Math.ceil(subs.length / 2);
  const lookback = config.signalLookback ?? DEFAULT_LOOKBACK;
  const ids = subs.map((s) => s.config.strategyId);
  const memory = new Map<string, SignalMemoryEntry>();

  return {
    name: `Composed:Voting(${ids.join("+")},votes≥${minVotes})`,
    init(ctx) {
      memory.clear();
      for (const sub of subs) sub.strategy.init?.(ctx);
    },
    next(ctx) {
      // Always call all subs to keep their internal state updated
      for (const sub of subs) updateMemory(memory, sub, ctx);

      // Count votes from signal memory (within lookback window)
      let buyVotes = 0;
      let sellVotes = 0;
      const buyReasons: string[] = [];
      const sellReasons: string[] = [];

      for (const sub of subs) {
        const recent = getRecentSignal(memory, sub.config.strategyId, ctx.index, lookback);
        if (!recent) continue;
        if (recent.direction === "buy") {
          buyVotes++;
          if (recent.reason) buyReasons.push(recent.reason);
        } else if (recent.direction === "sell") {
          sellVotes++;
          if (recent.reason) sellReasons.push(recent.reason);
        }
      }

      const bar = ctx.bars[ctx.index]!;

      if (buyVotes >= minVotes && ctx.position <= 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy" as const,
          price: bar.close,
          reason: `[组合投票买入 ${buyVotes}/${subs.length}] ${buyReasons.join(" | ")}`,
          strategyId: config.id,
        };
      }

      if (sellVotes >= minVotes && ctx.position > 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell" as const,
          price: bar.close,
          reason: `[组合投票卖出 ${sellVotes}/${subs.length}] ${sellReasons.join(" | ")}`,
          strategyId: config.id,
        };
      }

      return null;
    },
  };
}

// ── Weighted Score ───────────────────────────────────────────────

function buildWeightedScoreStrategy(
  config: CompositionConfig,
  subs: ResolvedSub[],
): BuiltinStrategy {
  const threshold = config.scoreThreshold ?? 0.3;
  const lookback = config.signalLookback ?? DEFAULT_LOOKBACK;
  const totalWeight = subs.reduce((sum, s) => sum + (s.config.weight ?? 1), 0);
  const ids = subs.map((s) => s.config.strategyId);
  const memory = new Map<string, SignalMemoryEntry>();

  return {
    name: `Composed:Weighted(${ids.join("+")},thr=${threshold})`,
    init(ctx) {
      memory.clear();
      for (const sub of subs) sub.strategy.init?.(ctx);
    },
    next(ctx) {
      // Always call all subs to keep their internal state updated
      for (const sub of subs) updateMemory(memory, sub, ctx);

      // Score from signal memory (within lookback window)
      let buyScore = 0;
      let sellScore = 0;
      const buyReasons: string[] = [];
      const sellReasons: string[] = [];

      for (const sub of subs) {
        const weight = sub.config.weight ?? 1;
        const recent = getRecentSignal(memory, sub.config.strategyId, ctx.index, lookback);
        if (!recent) continue;
        if (recent.direction === "buy") {
          buyScore += weight;
          if (recent.reason)
            buyReasons.push(`${sub.config.strategyId}(w=${weight}): ${recent.reason}`);
        } else if (recent.direction === "sell") {
          sellScore += weight;
          if (recent.reason)
            sellReasons.push(`${sub.config.strategyId}(w=${weight}): ${recent.reason}`);
        }
      }

      const normalizedBuy = totalWeight > 0 ? buyScore / totalWeight : 0;
      const normalizedSell = totalWeight > 0 ? sellScore / totalWeight : 0;
      const bar = ctx.bars[ctx.index]!;

      if (normalizedBuy >= threshold && ctx.position <= 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy" as const,
          price: bar.close,
          reason: `[加权买入 score=${normalizedBuy.toFixed(2)}≥${threshold}] ${buyReasons.join(" | ")}`,
          strategyId: config.id,
        };
      }

      if (normalizedSell >= threshold && ctx.position > 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell" as const,
          price: bar.close,
          reason: `[加权卖出 score=${normalizedSell.toFixed(2)}≥${threshold}] ${sellReasons.join(" | ")}`,
          strategyId: config.id,
        };
      }

      return null;
    },
  };
}

// ── Gated ────────────────────────────────────────────────────────

function buildGatedStrategy(config: CompositionConfig, subs: ResolvedSub[]): BuiltinStrategy {
  const primary = subs.filter((s) => s.config.role === "primary");
  const filters = subs.filter((s) => s.config.role !== "primary");
  const lookback = config.signalLookback ?? DEFAULT_LOOKBACK;

  // Fallback: first strategy is primary if none marked
  if (primary.length === 0 && subs.length > 0) {
    primary.push(subs[0]!);
    filters.length = 0;
    for (let i = 1; i < subs.length; i++) filters.push(subs[i]!);
  }

  const memory = new Map<string, SignalMemoryEntry>();

  return {
    name: `Composed:Gated(primary=${primary.map((s) => s.config.strategyId).join("+")},filters=${filters.map((s) => s.config.strategyId).join("+")})`,
    init(ctx) {
      memory.clear();
      for (const sub of subs) sub.strategy.init?.(ctx);
    },
    next(ctx) {
      // Always call all subs to keep their internal state updated
      for (const sub of subs) updateMemory(memory, sub, ctx);

      // Check primary signals (with memory)
      let primaryBuy = false;
      let primarySell = false;
      const reasons: string[] = [];

      for (const p of primary) {
        const recent = getRecentSignal(memory, p.config.strategyId, ctx.index, lookback);
        if (!recent) continue;
        if (recent.direction === "buy") {
          primaryBuy = true;
          if (recent.reason) reasons.push(recent.reason);
        } else if (recent.direction === "sell") {
          primarySell = true;
          if (recent.reason) reasons.push(recent.reason);
        }
      }

      if (!primaryBuy && !primarySell) return null;

      // Check filters — only block if filter's recent signal actively opposes
      for (const f of filters) {
        const recent = getRecentSignal(memory, f.config.strategyId, ctx.index, lookback);
        if (!recent) continue; // no recent signal = no objection
        if (primaryBuy && recent.direction === "sell") return null;
        if (primarySell && recent.direction === "buy") return null;
      }

      const bar = ctx.bars[ctx.index]!;

      if (primaryBuy && ctx.position <= 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy" as const,
          price: bar.close,
          reason: `[门控买入 全部确认] ${reasons.join(" | ")}`,
          strategyId: config.id,
        };
      }

      if (primarySell && ctx.position > 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell" as const,
          price: bar.close,
          reason: `[门控卖出 全部确认] ${reasons.join(" | ")}`,
          strategyId: config.id,
        };
      }

      return null;
    },
  };
}

// ── Cascade ──────────────────────────────────────────────────────

function buildCascadeStrategy(config: CompositionConfig, subs: ResolvedSub[]): BuiltinStrategy {
  const lookback = config.signalLookback ?? DEFAULT_LOOKBACK;
  const ids = subs.map((s) => s.config.strategyId);
  const memory = new Map<string, SignalMemoryEntry>();

  return {
    name: `Composed:Cascade(${ids.join("→")})`,
    init(ctx) {
      memory.clear();
      for (const sub of subs) sub.strategy.init?.(ctx);
    },
    next(ctx) {
      // Always call all subs to keep their internal state updated
      for (const sub of subs) updateMemory(memory, sub, ctx);

      // Cascade through stages using signal memory
      let currentDirection: "buy" | "sell" | null = null;
      const reasons: string[] = [];

      for (const sub of subs) {
        const recent = getRecentSignal(memory, sub.config.strategyId, ctx.index, lookback);

        if (currentDirection === null) {
          // First stage: set direction
          if (recent?.direction === "buy") {
            currentDirection = "buy";
            if (recent.reason) reasons.push(recent.reason);
          } else if (recent?.direction === "sell") {
            currentDirection = "sell";
            if (recent.reason) reasons.push(recent.reason);
          } else {
            return null; // No signal from first stage
          }
        } else {
          // Subsequent stages: block only if they explicitly oppose
          if (recent) {
            if (recent.reason) reasons.push(recent.reason);
            if (currentDirection === "buy" && recent.direction === "sell") return null;
            if (currentDirection === "sell" && recent.direction === "buy") return null;
          }
        }
      }

      if (!currentDirection) return null;

      const bar = ctx.bars[ctx.index]!;
      const isBuy = currentDirection === "buy";

      if (isBuy && ctx.position <= 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy" as const,
          price: bar.close,
          reason: `[级联买入 ${subs.length}级全通过] ${reasons.join(" | ")}`,
          strategyId: config.id,
        };
      }

      if (!isBuy && ctx.position > 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell" as const,
          price: bar.close,
          reason: `[级联卖出 ${subs.length}级全通过] ${reasons.join(" | ")}`,
          strategyId: config.id,
        };
      }

      return null;
    },
  };
}
