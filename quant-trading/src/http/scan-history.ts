import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { QuantTradingConfig } from "../types.js";

interface ScanHistoryPick {
  rank: number;
  code: string;
  name: string;
  totalScore: number;
  catalystScore: number;
  scarcityScore: number;
  sectors: string[];
  fundFlow: { trend: string } | null;
  signals: string[];
}

interface ScanHistoryTriggeredUS {
  symbol: string;
  name: string;
  changePct: number;
  newsThemes: string[];
  topNews: string[];
}

interface ScanHistoryEntry {
  id: string;
  version: string;
  timestamp: string;
  date: string;
  threshold: number;
  requireFundFlow: boolean;
  topN: number;
  triggered: boolean;
  triggeredCount: number;
  candidateCount: number;
  pickCount: number;
  triggeredUS: ScanHistoryTriggeredUS[];
  picks: ScanHistoryPick[];
}

function getHistoryPath(config: QuantTradingConfig): string {
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  return path.join(dir, "scan-history.json");
}

async function loadHistory(config: QuantTradingConfig): Promise<ScanHistoryEntry[]> {
  try {
    const raw = await fs.readFile(getHistoryPath(config), "utf-8");
    const entries = JSON.parse(raw) as ScanHistoryEntry[];
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

function extractTriggeredUS(raw: unknown): ScanHistoryTriggeredUS[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((u) => {
      if (!u || typeof u !== "object") return null;
      const row = u as Record<string, unknown>;
      return {
        symbol: String(row.symbol ?? ""),
        name: String(row.name ?? ""),
        changePct: toFiniteNumber(row.changePct),
        newsThemes: Array.isArray(row.newsThemes)
          ? row.newsThemes.map((t: unknown) => String(t))
          : [],
        topNews: Array.isArray(row.topNews) ? row.topNews.map((n: unknown) => String(n)) : [],
      };
    })
    .filter((u): u is NonNullable<typeof u> => u !== null && u.symbol !== "");
}

function extractPicks(raw: unknown): ScanHistoryPick[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((p) => {
      if (!p || typeof p !== "object") return null;
      const row = p as Record<string, unknown>;
      const code = String(row.code ?? "").trim();
      if (!code) return null;
      const flowRaw = row.fundFlow as Record<string, unknown> | null | undefined;
      return {
        rank: toFiniteNumber(row.rank, 0),
        code,
        name: String(row.name ?? ""),
        totalScore: toFiniteNumber(row.totalScore),
        catalystScore: toFiniteNumber(row.catalystScore),
        scarcityScore: toFiniteNumber(row.scarcityScore),
        sectors: Array.isArray(row.sectors) ? row.sectors.map((s: unknown) => String(s)) : [],
        fundFlow:
          flowRaw && typeof flowRaw === "object" ? { trend: String(flowRaw.trend ?? "") } : null,
        signals: Array.isArray(row.signals) ? row.signals.map((s: unknown) => String(s)) : [],
      };
    })
    .filter((p): p is NonNullable<typeof p> => p !== null);
}

export async function appendScanHistory(
  config: QuantTradingConfig,
  data: Record<string, unknown>,
): Promise<ScanHistoryEntry> {
  const history = await loadHistory(config);
  const now = new Date();
  const entry: ScanHistoryEntry = {
    id: now.toISOString().replace(/[:.]/g, "-"),
    version: generateVersion(history, now),
    timestamp: now.toISOString(),
    date: String(data.date ?? ""),
    threshold: toFiniteNumber(data.threshold ?? data.min_change_pct, 2),
    requireFundFlow: Boolean(data.requireFundFlow ?? data.require_fund_flow ?? true),
    topN: toFiniteNumber(data.topN ?? data.top_n, 10),
    triggered: Boolean(data.triggered),
    triggeredCount: toFiniteNumber(data.triggeredCount),
    candidateCount: toFiniteNumber(data.candidateCount),
    pickCount: toFiniteNumber(data.pickCount),
    triggeredUS: extractTriggeredUS(data.triggeredUS),
    picks: extractPicks(data.picks),
  };
  history.unshift(entry);
  const filePath = getHistoryPath(config);
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  // Keep last 50 entries
  await fs.writeFile(filePath, JSON.stringify(history.slice(0, 50), null, 2), "utf-8");
  return entry;
}

export async function handleGetScanHistory(
  config: QuantTradingConfig,
): Promise<Record<string, unknown>> {
  const history = await loadHistory(config);
  if (history.length === 0) {
    return { status: "ok", entries: [], count: 0 };
  }

  // Frequency analytics: which A-share codes appear most often in picks
  const codeFreq: Record<string, { name: string; count: number }> = {};
  for (const entry of history) {
    for (const p of entry.picks) {
      if (!codeFreq[p.code]) codeFreq[p.code] = { name: p.name, count: 0 };
      codeFreq[p.code].count += 1;
    }
  }
  const topPicks = Object.entries(codeFreq)
    .sort((a, b) => b[1].count - a[1].count)
    .slice(0, 10)
    .map(([code, info]) => ({
      code,
      name: info.name,
      count: info.count,
      pct: Math.round((info.count / history.length) * 100),
    }));

  // Return entries without full picks/triggeredUS to keep response light
  const entries = history.map(({ picks: _p, triggeredUS: _u, ...rest }) => rest);

  return {
    status: "ok",
    entries,
    count: history.length,
    topPicks,
  };
}

export async function handleGetScanHistoryById(
  config: QuantTradingConfig,
  historyId: string,
): Promise<Record<string, unknown>> {
  const history = await loadHistory(config);
  const entry = history.find((item) => item.id === historyId);
  if (!entry) return { status: "error", message: "未找到该全局扫描快照" };
  return { status: "ok", entry };
}

export async function handleDeleteScanHistory(
  config: QuantTradingConfig,
  historyId: string,
): Promise<Record<string, unknown>> {
  const history = await loadHistory(config);
  const index = history.findIndex((item) => item.id === historyId);
  if (index === -1) return { status: "error", message: "未找到该全局扫描快照" };
  history.splice(index, 1);
  const filePath = getHistoryPath(config);
  await fs.writeFile(filePath, JSON.stringify(history, null, 2), "utf-8");
  return { status: "ok" };
}
