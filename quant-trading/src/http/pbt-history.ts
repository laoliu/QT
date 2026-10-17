/**
 * Portfolio Backtest History — persists backtest run results to disk.
 *
 * Stores each run as a lightweight entry in a single JSON file.
 * Supports:
 *   - Listing past runs
 *   - Strategy frequency / average score analysis
 *   - Deleting old runs
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { QuantTradingConfig } from "../types.js";

// ── Types ────────────────────────────────────────────────────────

export interface PbtStrategyResult {
  rank: number;
  strategy: string;
  strategyId: string;
  totalReturn: number | null;
  annualizedReturn: number | null;
  finalValue: number | null;
  profit: number | null;
  maxDrawdown: number | null;
  sharpeRatio: number | null;
  winRate: number | null;
  totalTrades: number | null;
  compositeScore: number | null;
}

export interface PbtHistoryEntry {
  id: string;
  createdAt: string;
  date: string;
  symbols: string[];
  period: string;
  initialCash: number;
  strategyCount: number;
  successCount: number;
  failCount: number;
  bestStrategy: string | null;
  comparison: PbtStrategyResult[];
}

export interface PbtStrategyFrequency {
  strategyId: string;
  strategy: string;
  appearances: number;
  avgScore: number;
  avgRank: number;
  bestScore: number;
  worstScore: number;
  avgReturn: number;
  top3Count: number;
  lastSeen: string;
}

// ── Helpers ──────────────────────────────────────────────────────

const MAX_ENTRIES = 100;

function getFilePath(config: QuantTradingConfig): string {
  const dir = config.dataDir ?? path.join(process.cwd(), "data");
  return path.join(dir, "pbt-history.json");
}

async function loadAll(config: QuantTradingConfig): Promise<PbtHistoryEntry[]> {
  try {
    const raw = await fs.readFile(getFilePath(config), "utf-8");
    const entries = JSON.parse(raw) as PbtHistoryEntry[];
    return Array.isArray(entries) ? entries : [];
  } catch {
    return [];
  }
}

async function saveAll(
  config: QuantTradingConfig,
  entries: PbtHistoryEntry[],
): Promise<void> {
  const filePath = getFilePath(config);
  const dir = path.dirname(filePath);
  await fs.mkdir(dir, { recursive: true });
  await fs.writeFile(filePath, JSON.stringify(entries, null, 2));
}

// ── Public API ───────────────────────────────────────────────────

/** Append a new portfolio backtest result and return the entry. */
export async function appendPbtHistory(
  config: QuantTradingConfig,
  data: {
    symbols: string[];
    period: string;
    initialCash: number;
    comparison: PbtStrategyResult[];
    bestStrategy: string | null;
    successCount: number;
    failCount: number;
    totalRuns: number;
  },
): Promise<PbtHistoryEntry> {
  const entries = await loadAll(config);
  const now = new Date();
  const entry: PbtHistoryEntry = {
    id: `pbt_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
    createdAt: now.toISOString(),
    date: now.toISOString().slice(0, 10),
    symbols: data.symbols,
    period: data.period,
    initialCash: data.initialCash,
    strategyCount: data.totalRuns,
    successCount: data.successCount,
    failCount: data.failCount,
    bestStrategy: data.bestStrategy,
    comparison: data.comparison,
  };

  entries.push(entry);

  // Prune oldest if over limit
  if (entries.length > MAX_ENTRIES) {
    entries.sort(
      (a, b) => new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime(),
    );
    entries.splice(0, entries.length - MAX_ENTRIES);
  }

  await saveAll(config, entries);
  return entry;
}

/** Get all history entries (newest first). */
export async function getPbtHistory(
  config: QuantTradingConfig,
): Promise<PbtHistoryEntry[]> {
  const entries = await loadAll(config);
  entries.sort(
    (a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime(),
  );
  return entries;
}

/** Get a single entry by id. */
export async function getPbtHistoryById(
  config: QuantTradingConfig,
  id: string,
): Promise<PbtHistoryEntry | null> {
  const entries = await loadAll(config);
  return entries.find((e) => e.id === id) ?? null;
}

/** Delete a history entry. */
export async function deletePbtHistory(
  config: QuantTradingConfig,
  id: string,
): Promise<boolean> {
  const entries = await loadAll(config);
  const idx = entries.findIndex((e) => e.id === id);
  if (idx < 0) return false;
  entries.splice(idx, 1);
  await saveAll(config, entries);
  return true;
}

/**
 * Analyze strategy frequency across all (or filtered) history entries.
 * Returns strategies ranked by average composite score.
 */
export async function getPbtStrategyFrequency(
  config: QuantTradingConfig,
  opts?: { after?: string; before?: string; minAppearances?: number },
): Promise<PbtStrategyFrequency[]> {
  const entries = await loadAll(config);
  const afterTs = opts?.after ? new Date(opts.after).getTime() : 0;
  const beforeTs = opts?.before ? new Date(opts.before).getTime() : Infinity;

  const map = new Map<
    string,
    {
      strategy: string;
      scores: number[];
      ranks: number[];
      returns: number[];
      top3: number;
      lastSeen: string;
    }
  >();

  for (const entry of entries) {
    const ts = new Date(entry.createdAt).getTime();
    if (ts < afterTs || ts > beforeTs) continue;

    for (const r of entry.comparison) {
      const prev = map.get(r.strategyId);
      if (prev) {
        if (r.compositeScore != null) prev.scores.push(r.compositeScore);
        prev.ranks.push(r.rank);
        if (r.totalReturn != null) prev.returns.push(r.totalReturn);
        if (r.rank <= 3) prev.top3++;
        if (entry.createdAt > prev.lastSeen) prev.lastSeen = entry.createdAt;
        if (!prev.strategy && r.strategy) prev.strategy = r.strategy;
      } else {
        map.set(r.strategyId, {
          strategy: r.strategy,
          scores: r.compositeScore != null ? [r.compositeScore] : [],
          ranks: [r.rank],
          returns: r.totalReturn != null ? [r.totalReturn] : [],
          top3: r.rank <= 3 ? 1 : 0,
          lastSeen: entry.createdAt,
        });
      }
    }
  }

  const minApp = opts?.minAppearances ?? 1;
  const result: PbtStrategyFrequency[] = [];

  for (const [strategyId, data] of map) {
    if (data.scores.length < minApp) continue;
    const avg = (arr: number[]) =>
      arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : 0;
    result.push({
      strategyId,
      strategy: data.strategy,
      appearances: data.scores.length,
      avgScore: round2(avg(data.scores)),
      avgRank: round2(avg(data.ranks)),
      bestScore: round2(Math.max(...data.scores)),
      worstScore: round2(Math.min(...data.scores)),
      avgReturn: round2(avg(data.returns)),
      top3Count: data.top3,
      lastSeen: data.lastSeen,
    });
  }

  result.sort((a, b) => b.avgScore - a.avgScore);
  return result;
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}
