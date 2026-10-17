import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { QuantTradingConfig } from "../types.js";
import { nowCSTISO } from "../utils/cst-time.js";

interface InstitutionActivityHistoryEntry {
  id: string;
  version: string;
  timestamp: string;
  params: Record<string, unknown>;
  summary: {
    asOfDate: string;
    candidateCount: number;
    leaderCount: number;
    baitCount: number;
    marketTone: string;
    northboundNetIn: number | null;
    generatedAt: string;
  };
  candidateSymbols: string[];
  leaderSymbols: string[];
  baitSymbols: string[];
  topLeaders: Array<{
    symbol: string;
    name: string;
    actionTag: string;
    confidence: number;
    netConviction: number;
    buildScore: number;
    techMoatScore: number | null;
    techMoatGrade: string;
    manualConfirmation: {
      moatConfirmed: boolean;
      restructureConfirmed: boolean;
      restructureLevel: string;
      supplyChainVerified: boolean;
      orderVerified: boolean;
      customerVerified: boolean;
      patentVerified: boolean;
      notes: string;
      score: number;
      updatedAt: string;
    } | null;
    manualScore: number;
    decisionScore: number | null;
  }>;
  fullScan: Record<string, unknown> | null;
}

type InstitutionScanPayload = Record<string, unknown>;

function getInstitutionHistoryPath(config: QuantTradingConfig): string {
  const dir = config.dataDir ?? path.join(process.cwd(), "data");
  return path.join(dir, "institution-activity-history.json");
}

async function loadInstitutionHistory(
  config: QuantTradingConfig,
): Promise<InstitutionActivityHistoryEntry[]> {
  try {
    const raw = await fs.readFile(getInstitutionHistoryPath(config), "utf-8");
    const entries = JSON.parse(raw) as InstitutionActivityHistoryEntry[];
    return Array.isArray(entries) ? entries : [];
  } catch {
    return [];
  }
}

function toFiniteNumber(value: unknown, fallback = 0): number {
  const num = typeof value === "number" ? value : Number(value);
  return Number.isFinite(num) ? num : fallback;
}

function toStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => String(item ?? "").trim()).filter(Boolean);
}

function normalizeManualConfirmation(
  value: unknown,
): InstitutionActivityHistoryEntry["topLeaders"][number]["manualConfirmation"] {
  if (!value || typeof value !== "object") return null;
  const row = value as Record<string, unknown>;
  const restructureLevelRaw = String(row.restructureLevel ?? "")
    .trim()
    .toLowerCase();
  const restructureLevel = ["none", "rumor", "announcement", "approved"].includes(
    restructureLevelRaw,
  )
    ? restructureLevelRaw
    : Boolean(row.restructureConfirmed)
      ? "announcement"
      : "none";
  return {
    moatConfirmed: Boolean(row.moatConfirmed),
    restructureConfirmed: Boolean(row.restructureConfirmed) || restructureLevel !== "none",
    restructureLevel,
    supplyChainVerified: Boolean(row.supplyChainVerified),
    orderVerified: Boolean(row.orderVerified),
    customerVerified: Boolean(row.customerVerified),
    patentVerified: Boolean(row.patentVerified),
    notes: String(row.notes ?? ""),
    score: toFiniteNumber(row.score),
    updatedAt: String(row.updatedAt ?? ""),
  };
}

function pickSymbolList(items: unknown): string[] {
  if (!Array.isArray(items)) return [];
  return items
    .map((item) => {
      if (!item || typeof item !== "object") return "";
      return String((item as Record<string, unknown>).symbol ?? "").trim();
    })
    .filter(Boolean);
}

function pickTopLeaders(items: unknown): InstitutionActivityHistoryEntry["topLeaders"] {
  if (!Array.isArray(items)) return [];
  return items.slice(0, 8).flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const row = item as Record<string, unknown>;
    const symbol = String(row.symbol ?? "").trim();
    if (!symbol) return [];
    return [
      {
        symbol,
        name: String(row.name ?? ""),
        actionTag: String(row.actionTag ?? "中性观察"),
        confidence: toFiniteNumber(row.confidence),
        netConviction: toFiniteNumber(row.netConviction),
        buildScore: toFiniteNumber(row.buildScore ?? row.netConviction),
        techMoatScore: row.techMoatScore == null ? null : toFiniteNumber(row.techMoatScore),
        techMoatGrade: String(row.techMoatGrade ?? ""),
        manualConfirmation: normalizeManualConfirmation(row.manualConfirmation),
        manualScore: toFiniteNumber(row.manualScore),
        decisionScore: row.decisionScore == null ? null : toFiniteNumber(row.decisionScore),
      },
    ];
  });
}

function averageOrNull(values: number[]): number | null {
  if (values.length === 0) return null;
  const total = values.reduce((sum, value) => sum + value, 0);
  return total / values.length;
}

function summarizeLeaderMetrics(leaders: InstitutionActivityHistoryEntry["topLeaders"]): {
  avgMoatScore: number | null;
  avgManualScore: number | null;
  avgDecisionScore: number | null;
  orderVerifiedCount: number;
  customerVerifiedCount: number;
  patentVerifiedCount: number;
  restructureCount: number;
} {
  const moatScores = leaders
    .map((leader) => leader.techMoatScore)
    .filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  const manualScores = leaders
    .map((leader) => leader.manualScore)
    .filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  const decisionScores = leaders
    .map((leader) => leader.decisionScore)
    .filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  return {
    avgMoatScore: averageOrNull(moatScores),
    avgManualScore: averageOrNull(manualScores),
    avgDecisionScore: averageOrNull(decisionScores),
    orderVerifiedCount: leaders.filter((leader) => leader.manualConfirmation?.orderVerified).length,
    customerVerifiedCount: leaders.filter((leader) => leader.manualConfirmation?.customerVerified)
      .length,
    patentVerifiedCount: leaders.filter((leader) => leader.manualConfirmation?.patentVerified)
      .length,
    restructureCount: leaders.filter(
      (leader) =>
        leader.manualConfirmation?.restructureLevel &&
        leader.manualConfirmation.restructureLevel !== "none",
    ).length,
  };
}

function pickPeakLeaderBySymbol(
  leaders: Array<
    InstitutionActivityHistoryEntry["topLeaders"][number] & {
      version: string;
      asOfDate: string;
    }
  >,
  scoreKey: "techMoatScore" | "manualScore" | "decisionScore",
): Array<{
  symbol: string;
  name: string;
  score: number;
  grade?: string;
  version: string;
  asOfDate: string;
}> {
  const peaks = new Map<
    string,
    {
      symbol: string;
      name: string;
      score: number;
      grade?: string;
      version: string;
      asOfDate: string;
    }
  >();
  for (const leader of leaders) {
    const score = leader[scoreKey];
    if (typeof score !== "number" || !Number.isFinite(score)) continue;
    const current = peaks.get(leader.symbol);
    if (current && current.score >= score) continue;
    peaks.set(leader.symbol, {
      symbol: leader.symbol,
      name: leader.name,
      score,
      grade: scoreKey === "techMoatScore" ? leader.techMoatGrade : undefined,
      version: leader.version,
      asOfDate: leader.asOfDate,
    });
  }
  return [...peaks.values()].sort((a, b) => b.score - a.score).slice(0, 10);
}

function pickEvidenceLeaderBySymbol(
  leaders: Array<
    InstitutionActivityHistoryEntry["topLeaders"][number] & {
      version: string;
      asOfDate: string;
    }
  >,
  predicate: (leader: InstitutionActivityHistoryEntry["topLeaders"][number]) => boolean,
): Array<{
  symbol: string;
  name: string;
  count: number;
  score: number;
  version: string;
  asOfDate: string;
}> {
  const peaks = new Map<
    string,
    {
      symbol: string;
      name: string;
      count: number;
      score: number;
      version: string;
      asOfDate: string;
    }
  >();
  for (const leader of leaders) {
    if (!predicate(leader)) continue;
    const current = peaks.get(leader.symbol);
    const nextScore =
      typeof leader.manualScore === "number" && Number.isFinite(leader.manualScore)
        ? leader.manualScore
        : 0;
    if (!current) {
      peaks.set(leader.symbol, {
        symbol: leader.symbol,
        name: leader.name,
        count: 1,
        score: nextScore,
        version: leader.version,
        asOfDate: leader.asOfDate,
      });
      continue;
    }
    current.count += 1;
    if (nextScore > current.score) {
      current.score = nextScore;
      current.version = leader.version;
      current.asOfDate = leader.asOfDate;
      current.name = leader.name;
    }
  }
  return [...peaks.values()].sort((a, b) => b.count - a.count || b.score - a.score).slice(0, 10);
}

function restructureLevelWeight(level: string): number {
  if (level === "approved") return 3;
  if (level === "announcement") return 2;
  if (level === "rumor") return 1;
  return 0;
}

function restructureLevelLabel(level: string): string {
  if (level === "approved") return "重组落地";
  if (level === "announcement") return "重组公告";
  if (level === "rumor") return "重组筹划";
  return "未确认";
}

function pickRestructureLeaderBySymbol(
  leaders: Array<
    InstitutionActivityHistoryEntry["topLeaders"][number] & {
      version: string;
      asOfDate: string;
    }
  >,
): Array<{
  symbol: string;
  name: string;
  count: number;
  score: number;
  label: string;
  version: string;
  asOfDate: string;
}> {
  const peaks = new Map<
    string,
    {
      symbol: string;
      name: string;
      count: number;
      score: number;
      label: string;
      version: string;
      asOfDate: string;
      levelWeight: number;
    }
  >();
  for (const leader of leaders) {
    const level = leader.manualConfirmation?.restructureLevel ?? "none";
    const levelWeight = restructureLevelWeight(level);
    if (levelWeight <= 0) continue;
    const nextScore =
      typeof leader.manualScore === "number" && Number.isFinite(leader.manualScore)
        ? leader.manualScore
        : 0;
    const current = peaks.get(leader.symbol);
    if (!current) {
      peaks.set(leader.symbol, {
        symbol: leader.symbol,
        name: leader.name,
        count: 1,
        score: nextScore,
        label: restructureLevelLabel(level),
        version: leader.version,
        asOfDate: leader.asOfDate,
        levelWeight,
      });
      continue;
    }
    current.count += 1;
    if (
      levelWeight > current.levelWeight ||
      (levelWeight === current.levelWeight && nextScore > current.score)
    ) {
      current.score = nextScore;
      current.label = restructureLevelLabel(level);
      current.version = leader.version;
      current.asOfDate = leader.asOfDate;
      current.name = leader.name;
      current.levelWeight = levelWeight;
    }
  }
  return [...peaks.values()]
    .sort((a, b) => b.count - a.count || b.score - a.score || b.label.localeCompare(a.label))
    .slice(0, 10)
    .map(({ levelWeight: _levelWeight, ...rest }) => rest);
}

function generateVersion(existingHistory: Array<{ version: string }>, now: Date): string {
  const dateStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  const todayCount = existingHistory.filter((entry) =>
    entry.version.startsWith(dateStr + "-v"),
  ).length;
  return `${dateStr}-v${todayCount + 1}`;
}

function sanitizeParams(params: Record<string, unknown>): Record<string, unknown> {
  return {
    as_of_date: typeof params.as_of_date === "string" ? params.as_of_date : params.asOfDate,
    lookback_days: toFiniteNumber(params.lookback_days ?? params.lookbackDays, 10),
    min_confidence: toFiniteNumber(params.min_confidence ?? params.minConfidence, 0),
    pre_accum_days: toFiniteNumber(params.pre_accum_days ?? params.preAccumDays, 10),
    pre_accum_count_min: toFiniteNumber(params.pre_accum_count_min ?? params.preAccumCountMin, 2),
    pre_accum_volume_ratio: toFiniteNumber(
      params.pre_accum_volume_ratio ?? params.preAccumVolumeRatio,
      1.5,
    ),
    rush_change_pct: toFiniteNumber(params.rush_change_pct ?? params.rushChangePct, 5),
    rush_volume_ratio: toFiniteNumber(params.rush_volume_ratio ?? params.rushVolumeRatio, 2),
    rush_turnover_pct: toFiniteNumber(params.rush_turnover_pct ?? params.rushTurnoverPct, 3),
    symbols: toStringArray(params.symbols),
  };
}

export async function appendInstitutionActivityHistory(
  config: QuantTradingConfig,
  scan: InstitutionScanPayload,
  params: Record<string, unknown>,
): Promise<void> {
  const history = await loadInstitutionHistory(config);
  const now = new Date();
  const nowISO = nowCSTISO();
  const candidates = Array.isArray(scan.candidates) ? scan.candidates : [];
  const leaders = Array.isArray(scan.leaders) ? scan.leaders : [];
  const storedTopLeaders = Array.isArray(scan.topLeaders) ? scan.topLeaders : leaders;
  const baits = Array.isArray(scan.highRiskBaits) ? scan.highRiskBaits : [];
  const marketSummary =
    scan.marketSummary && typeof scan.marketSummary === "object"
      ? (scan.marketSummary as Record<string, unknown>)
      : {};
  const entry: InstitutionActivityHistoryEntry = {
    id: nowISO.replace(/[:.]/g, "-"),
    version: generateVersion(history, now),
    timestamp: nowISO,
    params: sanitizeParams(params),
    summary: {
      asOfDate: String(scan.asOfDate ?? params.as_of_date ?? params.asOfDate ?? ""),
      candidateCount: candidates.length,
      leaderCount: leaders.length,
      baitCount: baits.length,
      marketTone: String(marketSummary.marketTone ?? "δ֪"),
      northboundNetIn:
        marketSummary.northboundNetIn == null
          ? null
          : toFiniteNumber(marketSummary.northboundNetIn),
      generatedAt: String(scan.generatedAt ?? nowISO),
    },
    candidateSymbols: pickSymbolList(candidates),
    leaderSymbols: pickSymbolList(leaders),
    baitSymbols: pickSymbolList(baits),
    topLeaders: pickTopLeaders(storedTopLeaders),
    fullScan: scan,
  };
  history.unshift(entry);
  const filePath = getInstitutionHistoryPath(config);
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  await fs.writeFile(filePath, JSON.stringify(history.slice(0, 100), null, 2), "utf-8");
}

export async function handleGetInstitutionActivityHistoryApi(
  config: QuantTradingConfig,
): Promise<Record<string, unknown>> {
  const history = await loadInstitutionHistory(config);
  if (history.length === 0) {
    return {
      status: "ok",
      entries: [],
      count: 0,
      topLeaderStocks: [],
      comparison: null,
    };
  }

  const leaderFreq: Record<string, number> = {};
  for (const entry of history) {
    for (const code of entry.leaderSymbols) {
      leaderFreq[code] = (leaderFreq[code] ?? 0) + 1;
    }
  }
  const topLeaderStocks = Object.entries(leaderFreq)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 15)
    .map(([code, count]) => ({ code, count, pct: Math.round((count / history.length) * 100) }));

  const latest = history[0];
  const previous = history[1] ?? null;
  const latestLeaderSet = new Set(latest.leaderSymbols);
  const previousLeaderSet = new Set(previous?.leaderSymbols ?? []);
  const latestCandidateSet = new Set(latest.candidateSymbols);
  const previousCandidateSet = new Set(previous?.candidateSymbols ?? []);
  const leaderStreaks: Record<string, number> = {};
  const rankedLeaders = history.flatMap((entry) =>
    entry.topLeaders.map((leader) => ({
      ...leader,
      version: entry.version,
      asOfDate: entry.summary.asOfDate,
    })),
  );
  const latestMetrics = summarizeLeaderMetrics(latest.topLeaders);
  const topMoatLeaders = pickPeakLeaderBySymbol(rankedLeaders, "techMoatScore");
  const topManualLeaders = pickPeakLeaderBySymbol(rankedLeaders, "manualScore");
  const topDecisionLeaders = pickPeakLeaderBySymbol(rankedLeaders, "decisionScore");
  const topOrderLeaders = pickEvidenceLeaderBySymbol(rankedLeaders, (leader) =>
    Boolean(leader.manualConfirmation?.orderVerified),
  );
  const topCustomerLeaders = pickEvidenceLeaderBySymbol(rankedLeaders, (leader) =>
    Boolean(leader.manualConfirmation?.customerVerified),
  );
  const topPatentLeaders = pickEvidenceLeaderBySymbol(rankedLeaders, (leader) =>
    Boolean(leader.manualConfirmation?.patentVerified),
  );
  const topRestructureLeaders = pickRestructureLeaderBySymbol(rankedLeaders);
  const bestMoatLeader = rankedLeaders.reduce<(typeof rankedLeaders)[number] | null>(
    (best, leader) => {
      if (leader.techMoatScore == null) return best;
      if (!best) return leader;
      return leader.techMoatScore > (best.techMoatScore ?? Number.NEGATIVE_INFINITY)
        ? leader
        : best;
    },
    null,
  );
  const bestDecisionLeader = rankedLeaders.reduce<(typeof rankedLeaders)[number] | null>(
    (best, leader) => {
      if (leader.decisionScore == null) return best;
      if (!best) return leader;
      return leader.decisionScore > (best.decisionScore ?? Number.NEGATIVE_INFINITY)
        ? leader
        : best;
    },
    null,
  );

  for (const code of latestLeaderSet) {
    let streak = 1;
    for (let index = 1; index < history.length; index++) {
      if (history[index].leaderSymbols.includes(code)) {
        streak += 1;
      } else {
        break;
      }
    }
    leaderStreaks[code] = streak;
  }

  const trendData = history
    .slice(0, 12)
    .reverse()
    .map((entry) => {
      const topLeaders = entry.topLeaders ?? [];
      return {
        version: entry.version,
        asOfDate: entry.summary?.asOfDate ?? "",
        orderVerified: topLeaders.filter((l) => l.manualConfirmation?.orderVerified).length,
        customerVerified: topLeaders.filter((l) => l.manualConfirmation?.customerVerified).length,
        patentVerified: topLeaders.filter((l) => l.manualConfirmation?.patentVerified).length,
        restructure: topLeaders.filter(
          (l) =>
            l.manualConfirmation?.restructureLevel &&
            l.manualConfirmation.restructureLevel !== "none",
        ).length,
      };
    });

  const entries = history.map(({ fullScan: _fullScan, ...rest }) => rest);
  return {
    status: "ok",
    entries,
    count: history.length,
    topLeaderStocks,
    metrics: {
      latestAvgMoatScore: latestMetrics.avgMoatScore,
      latestAvgManualScore: latestMetrics.avgManualScore,
      latestAvgDecisionScore: latestMetrics.avgDecisionScore,
      latestOrderVerifiedCount: latestMetrics.orderVerifiedCount,
      latestCustomerVerifiedCount: latestMetrics.customerVerifiedCount,
      latestPatentVerifiedCount: latestMetrics.patentVerifiedCount,
      latestRestructureCount: latestMetrics.restructureCount,
      bestMoatLeader: bestMoatLeader
        ? {
            symbol: bestMoatLeader.symbol,
            name: bestMoatLeader.name,
            score: bestMoatLeader.techMoatScore,
            grade: bestMoatLeader.techMoatGrade,
            version: bestMoatLeader.version,
            asOfDate: bestMoatLeader.asOfDate,
          }
        : null,
      bestDecisionLeader: bestDecisionLeader
        ? {
            symbol: bestDecisionLeader.symbol,
            name: bestDecisionLeader.name,
            score: bestDecisionLeader.decisionScore,
            version: bestDecisionLeader.version,
            asOfDate: bestDecisionLeader.asOfDate,
          }
        : null,
      topMoatLeaders,
      topManualLeaders,
      topDecisionLeaders,
      topOrderLeaders,
      topCustomerLeaders,
      topPatentLeaders,
      topRestructureLeaders,
      trendData,
    },
    comparison: {
      latestId: latest.id,
      prevId: previous?.id ?? null,
      latestVersion: latest.version,
      prevVersion: previous?.version ?? null,
      newLeaders: [...latestLeaderSet].filter((code) => !previousLeaderSet.has(code)),
      keptLeaders: [...latestLeaderSet].filter((code) => previousLeaderSet.has(code)),
      droppedLeaders: [...previousLeaderSet].filter((code) => !latestLeaderSet.has(code)),
      newCandidates: [...latestCandidateSet].filter((code) => !previousCandidateSet.has(code)),
      droppedCandidates: [...previousCandidateSet].filter((code) => !latestCandidateSet.has(code)),
      leaderStreaks,
    },
  };
}

export async function handleGetInstitutionActivityHistoryByIdApi(
  config: QuantTradingConfig,
  historyId: string,
): Promise<Record<string, unknown>> {
  const history = await loadInstitutionHistory(config);
  const entry = history.find((item) => item.id === historyId);
  if (!entry) return { status: "error", message: "未找到该机构行为快照" };
  return { status: "ok", entry };
}

export async function getLatestInstitutionTopLeaders(
  config: QuantTradingConfig,
): Promise<InstitutionActivityHistoryEntry["topLeaders"]> {
  const history = await loadInstitutionHistory(config);
  const latest = history[0];
  return latest?.topLeaders ?? [];
}

export async function handlePatchInstitutionActivityHistoryApi(
  config: QuantTradingConfig,
  historyId: string,
  payload: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const history = await loadInstitutionHistory(config);
  const entry = history.find((item) => item.id === historyId);
  if (!entry) return { status: "error", message: "未找到该机构行为快照" };

  const symbol = String(payload.symbol ?? "").trim();
  if (!symbol) return { status: "error", message: "缺少股票代码" };

  const leader = entry.topLeaders.find((item) => item.symbol === symbol);
  if (!leader) return { status: "error", message: "该快照中不存在目标股票" };

  leader.manualConfirmation = normalizeManualConfirmation(payload.manualConfirmation);
  leader.manualScore = toFiniteNumber(payload.manualScore);
  leader.decisionScore =
    payload.decisionScore == null ? leader.decisionScore : toFiniteNumber(payload.decisionScore);

  const fullScanTopLeaders = Array.isArray(entry.fullScan?.topLeaders)
    ? (entry.fullScan.topLeaders as Array<Record<string, unknown>>)
    : null;
  if (fullScanTopLeaders) {
    const fullLeader = fullScanTopLeaders.find(
      (item) => String(item.symbol ?? "").trim() === symbol,
    );
    if (fullLeader) {
      fullLeader.manualConfirmation = leader.manualConfirmation;
      fullLeader.manualScore = leader.manualScore;
      fullLeader.decisionScore = leader.decisionScore;
    }
  }

  const filePath = getInstitutionHistoryPath(config);
  await fs.writeFile(filePath, JSON.stringify(history, null, 2), "utf-8");
  return { status: "ok", entry };
}

export async function handleDeleteInstitutionActivityHistoryApi(
  config: QuantTradingConfig,
  historyId: string,
): Promise<Record<string, unknown>> {
  const history = await loadInstitutionHistory(config);
  const index = history.findIndex((item) => item.id === historyId);
  if (index === -1) return { status: "error", message: "未找到该机构行为快照" };
  history.splice(index, 1);
  const filePath = getInstitutionHistoryPath(config);
  await fs.writeFile(filePath, JSON.stringify(history, null, 2), "utf-8");
  return { status: "ok" };
}
