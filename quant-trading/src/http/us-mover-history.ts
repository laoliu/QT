import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { QuantTradingConfig } from "../types.js";

interface UsMoverHistoryEntry {
  id: string;
  version: string;
  timestamp: string;
  threshold: number;
  date: string;
  totalCount: number;
  significantCount: number;
  movers: Array<{
    symbol: string;
    name: string;
    price: number;
    changePct: number;
    significant: boolean;
    newsCount: number;
    detectedThemes: string[];
  }>;
}

function getHistoryPath(config: QuantTradingConfig): string {
  const dir = config.dataDir ?? path.join(process.cwd(), "data");
  return path.join(dir, "us-mover-history.json");
}

async function loadHistory(config: QuantTradingConfig): Promise<UsMoverHistoryEntry[]> {
  try {
    const raw = await fs.readFile(getHistoryPath(config), "utf-8");
    const entries = JSON.parse(raw) as UsMoverHistoryEntry[];
    return Array.isArray(entries) ? entries : [];
  } catch {
    return [];
  }
}

function toFiniteNumber(value: unknown, fallback = 0): number {
  const num = typeof value === "number" ? value : Number(value);
  return Number.isFinite(num) ? num : fallback;
}

function generateVersion(existing: Array<{ version: string }>, now: Date): string {
  const dateStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  const todayCount = existing.filter((e) => e.version.startsWith(dateStr + "-v")).length;
  return `${dateStr}-v${todayCount + 1}`;
}

function extractMovers(allMovers: unknown): UsMoverHistoryEntry["movers"] {
  if (!Array.isArray(allMovers)) return [];
  return allMovers
    .map((m) => {
      if (!m || typeof m !== "object") return null;
      const row = m as Record<string, unknown>;
      const symbol = String(row.symbol ?? "").trim();
      if (!symbol) return null;
      return {
        symbol,
        name: String(row.name ?? ""),
        price: toFiniteNumber(row.price),
        changePct: toFiniteNumber(row.changePct),
        significant: Boolean(row.significant),
        newsCount: toFiniteNumber(row.newsCount),
        detectedThemes: Array.isArray(row.detectedThemes)
          ? row.detectedThemes.map((t: unknown) => String(t))
          : [],
      };
    })
    .filter((m): m is NonNullable<typeof m> => m !== null);
}

export async function appendUsMoverHistory(
  config: QuantTradingConfig,
  data: Record<string, unknown>,
): Promise<UsMoverHistoryEntry> {
  const history = await loadHistory(config);
  const now = new Date();
  const movers = extractMovers(data.allMovers);
  const entry: UsMoverHistoryEntry = {
    id: now.toISOString().replace(/[:.]/g, "-"),
    version: generateVersion(history, now),
    timestamp: now.toISOString(),
    threshold: toFiniteNumber(data.threshold, 2),
    date: String(data.date ?? ""),
    totalCount: movers.length,
    significantCount: movers.filter((m) => m.significant).length,
    movers,
  };
  history.unshift(entry);
  const filePath = getHistoryPath(config);
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  // Keep last 50 entries
  await fs.writeFile(filePath, JSON.stringify(history.slice(0, 50), null, 2), "utf-8");
  return entry;
}

export async function handleGetUsMoverHistory(
  config: QuantTradingConfig,
): Promise<Record<string, unknown>> {
  const history = await loadHistory(config);
  if (history.length === 0) {
    return { status: "ok", entries: [], count: 0, comparison: null };
  }

  // Frequency analytics: which symbols appear most often as significant movers
  const sigFreq: Record<string, number> = {};
  for (const entry of history) {
    for (const m of entry.movers) {
      if (m.significant) {
        sigFreq[m.symbol] = (sigFreq[m.symbol] ?? 0) + 1;
      }
    }
  }
  const topSignificant = Object.entries(sigFreq)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 10)
    .map(([symbol, count]) => ({
      symbol,
      count,
      pct: Math.round((count / history.length) * 100),
    }));

  // Comparison between latest and previous
  const latest = history[0];
  const previous = history[1] ?? null;
  const latestSigSet = new Set(latest.movers.filter((m) => m.significant).map((m) => m.symbol));
  const prevSigSet = new Set(
    (previous?.movers ?? []).filter((m) => m.significant).map((m) => m.symbol),
  );

  // Return entries without the full movers array to keep response light
  const entries = history.map(({ movers: _m, ...rest }) => rest);

  return {
    status: "ok",
    entries,
    count: history.length,
    topSignificant,
    comparison: previous
      ? {
          newSignificant: [...latestSigSet].filter((s) => !prevSigSet.has(s)),
          keptSignificant: [...latestSigSet].filter((s) => prevSigSet.has(s)),
          droppedSignificant: [...prevSigSet].filter((s) => !latestSigSet.has(s)),
        }
      : null,
  };
}

export async function handleGetUsMoverHistoryById(
  config: QuantTradingConfig,
  historyId: string,
): Promise<Record<string, unknown>> {
  const history = await loadHistory(config);
  const entry = history.find((item) => item.id === historyId);
  if (!entry) return { status: "error", message: "未找到该美股异动快照" };
  return { status: "ok", entry };
}

export async function handleDeleteUsMoverHistory(
  config: QuantTradingConfig,
  historyId: string,
): Promise<Record<string, unknown>> {
  const history = await loadHistory(config);
  const index = history.findIndex((item) => item.id === historyId);
  if (index === -1) return { status: "error", message: "未找到该美股异动快照" };
  history.splice(index, 1);
  const filePath = getHistoryPath(config);
  await fs.writeFile(filePath, JSON.stringify(history, null, 2), "utf-8");
  return { status: "ok" };
}
