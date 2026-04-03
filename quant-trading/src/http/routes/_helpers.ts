/**
 * Shared helpers, types, and constants for admin route handlers.
 */
import type { IncomingMessage, ServerResponse } from "node:http";
import type { CompositionMethod } from "../../engine/dynamic-composer.js";
import type { TickStrategyId } from "../../types.js";
import type { QuantTradingConfig } from "../../types.js";
import { parseJsonBody } from "../parse-body.js";

export function json(res: ServerResponse, data: unknown, status = 200): void {
  const body = JSON.stringify(data, null, 2);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(body),
  });
  res.end(body);
}

export function toFiniteNumber(value: unknown, fallback = 0): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

export function clampInteger(value: unknown, min: number, max: number, fallback: number): number {
  const n = Math.trunc(toFiniteNumber(value, fallback));
  return Math.min(max, Math.max(min, n));
}

export function round2(value: number): number {
  return Math.round(value * 100) / 100;
}

export function formatDateYmd(date: Date): string {
  return date.toISOString().slice(0, 10);
}

export type JsonBody = Record<string, unknown>;

export async function parseObjectBody(req: IncomingMessage): Promise<JsonBody> {
  return parseJsonBody<JsonBody>(req);
}

// ── Daily pick constants ─────────────────────────────────────────

export const DAILY_PICK_STRATEGIES = [
  "momentum",
  "value",
  "growth",
  "swing",
  "mixed",
  "manipulation",
  "event_catalyst",
  "spread-arbitrage",
  "rsi-reversal",
  "vwap-reversion",
  "mean-reversion-zscore",
  "williams-r",
  "momentum-breakout",
  "range-breakout",
  "dual-ma-crossover",
  "trend-combo",
  "double-bottom",
  "macd-divergence",
  "volume-confirmation",
  "gated",
] as const;

export const DAILY_PICK_STRATEGY_LABELS: Record<string, string> = {
  momentum: "趋势动量",
  value: "价值投资",
  growth: "成长投资",
  swing: "波段交易",
  mixed: "混合策略",
  manipulation: "跟庄策略",
  event_catalyst: "事件催化",
  "spread-arbitrage": "价差套利",
  "rsi-reversal": "RSI反转",
  "vwap-reversion": "VWAP回归",
  "mean-reversion-zscore": "均值回归Z-Score",
  "williams-r": "威廉指标反转",
  "momentum-breakout": "动量突破",
  "range-breakout": "区间突破",
  "dual-ma-crossover": "双均线交叉",
  "trend-combo": "趋势合并",
  "double-bottom": "双底双顶",
  "macd-divergence": "MACD背离",
  "volume-confirmation": "成交量确认",
  gated: "条件门控",
};

export const CTA_SCREEN_STRATEGY_MAP: Record<string, string> = {
  "spread-arbitrage": "swing",
  "rsi-reversal": "swing",
  "vwap-reversion": "swing",
  "mean-reversion-zscore": "swing",
  "williams-r": "swing",
  "momentum-breakout": "momentum",
  "range-breakout": "swing",
  "dual-ma-crossover": "momentum",
  "trend-combo": "momentum",
  "double-bottom": "swing",
  "macd-divergence": "mixed",
  "volume-confirmation": "momentum",
  gated: "mixed",
};

// ── Tick strategy helpers ────────────────────────────────────────

export type TickStrategyConfig = NonNullable<
  NonNullable<QuantTradingConfig["tickData"]>["strategies"]
>[number];

export const SUPPORTED_TICK_STRATEGY_IDS: TickStrategyId[] = [
  "tick-momentum-imbalance",
  "tick-vwap-reversion",
  "tick-momentum-1m-confirm",
];

export function normalizeTickStrategyId(raw: unknown): TickStrategyId | null {
  const id = typeof raw === "string" ? raw.trim() : "";
  if (!id) return null;
  return SUPPORTED_TICK_STRATEGY_IDS.includes(id as TickStrategyId) ? (id as TickStrategyId) : null;
}

function normalizeSymbolList(raw: unknown): string[] | undefined {
  if (!Array.isArray(raw)) return undefined;
  const symbols = raw.map((s) => String(s).trim()).filter(Boolean);
  return symbols.length > 0 ? symbols : undefined;
}

export function parseTickStrategyConfig(body: JsonBody): TickStrategyConfig | null {
  const id = normalizeTickStrategyId(body.id);
  if (!id) return null;
  const enabled = typeof body.enabled === "boolean" ? body.enabled : undefined;
  const submitOrders = typeof body.submitOrders === "boolean" ? body.submitOrders : undefined;
  const quantity = Number(body.quantity);
  const parsedQuantity =
    Number.isFinite(quantity) && quantity > 0 ? Math.trunc(quantity) : undefined;
  const parameters =
    body.parameters && typeof body.parameters === "object" && !Array.isArray(body.parameters)
      ? (body.parameters as Record<string, unknown>)
      : undefined;

  return {
    id,
    enabled,
    symbols: normalizeSymbolList(body.symbols),
    submitOrders,
    quantity: parsedQuantity,
    parameters,
  };
}

// ── AI composition helpers ───────────────────────────────────────

export type StrategyCatalogItem = {
  id: string;
  name: string;
  description?: string;
  parameters?: Array<{
    name: string;
    type?: string;
    default?: unknown;
    description?: string;
    min?: number;
    max?: number;
  }>;
};

type AiSubStrategy = {
  strategyId: string;
  weight?: number;
  role?: "primary" | "filter";
};

export type AiCompositionCandidate = {
  name: string;
  method: CompositionMethod;
  minVotes?: number;
  rationale?: string;
  strategies: AiSubStrategy[];
};

export function buildFallbackCandidates(params: {
  strategyCatalog: StrategyCatalogItem[];
  candidateCount: number;
  minStrategies: number;
  maxStrategies: number;
}): AiCompositionCandidate[] {
  const { strategyCatalog, candidateCount, minStrategies, maxStrategies } = params;
  const ids = strategyCatalog.map((s) => s.id);
  const candidates: AiCompositionCandidate[] = [];
  const methods: CompositionMethod[] = ["voting", "weighted-score", "gated", "cascade"];
  if (ids.length < 2) {
    return candidates;
  }

  for (let i = 0; i < candidateCount; i++) {
    const method = methods[i % methods.length] ?? "voting";
    const width = Math.min(maxStrategies, Math.max(minStrategies, 2));
    const pickedIds = Array.from({ length: width }, (_, idx) => ids[(i + idx) % ids.length] ?? "")
      .filter(Boolean)
      .filter((sid, idx, arr) => arr.indexOf(sid) === idx);
    if (pickedIds.length < 2) {
      continue;
    }

    const strategies: AiSubStrategy[] = pickedIds.map((strategyId, idx) => ({
      strategyId,
      weight: ((idx + i) % 3) + 1,
      role:
        method === "gated" || method === "cascade" ? (idx === 0 ? "primary" : "filter") : "primary",
    }));

    const base: AiCompositionCandidate = {
      name: `\u81EA\u52A8\u7EC4\u5408 ${i + 1}`,
      method,
      rationale:
        "AI \u5EFA\u8BAE\u4E0D\u53EF\u7528\uFF0C\u4F7F\u7528\u89C4\u5219\u56DE\u9000\u7EC4\u5408",
      strategies,
    };
    if (method === "voting") {
      base.minVotes = Math.max(1, Math.min(strategies.length, Math.ceil(strategies.length / 2)));
    }
    candidates.push(base);
  }

  return candidates;
}

export function sanitizeAiCandidates(params: {
  raw: unknown;
  strategyCatalog: StrategyCatalogItem[];
  candidateCount: number;
  minStrategies: number;
  maxStrategies: number;
}): AiCompositionCandidate[] {
  const { raw, strategyCatalog, candidateCount, minStrategies, maxStrategies } = params;
  const allowedIds = new Set(strategyCatalog.map((s) => s.id));

  const root = raw as Record<string, unknown>;
  const incoming = Array.isArray(root?.candidates) ? root.candidates : [];
  const out: AiCompositionCandidate[] = [];

  for (const item of incoming) {
    if (out.length >= candidateCount) {
      break;
    }
    const c = item as Record<string, unknown>;
    const method =
      c.method === "voting" ||
      c.method === "weighted-score" ||
      c.method === "gated" ||
      c.method === "cascade"
        ? c.method
        : "voting";

    const rawStrategies = Array.isArray(c.strategies) ? c.strategies : [];
    const strategies: AiSubStrategy[] = [];
    for (const s of rawStrategies) {
      const sub = s as Record<string, unknown>;
      const strategyId = String(sub.strategyId ?? "").trim();
      if (!strategyId || !allowedIds.has(strategyId)) {
        continue;
      }
      if (strategies.some((x) => x.strategyId === strategyId)) {
        continue;
      }
      const role = sub.role === "filter" ? "filter" : "primary";
      const weight = clampInteger(sub.weight, 1, 10, 1);
      strategies.push({ strategyId, role, weight });
      if (strategies.length >= maxStrategies) {
        break;
      }
    }

    if (strategies.length < Math.max(2, minStrategies)) {
      continue;
    }

    if (method === "gated" || method === "cascade") {
      if (!strategies.some((s) => s.role === "filter")) {
        strategies.forEach((s, idx) => {
          s.role = idx === 0 ? "primary" : "filter";
        });
      }
      if (!strategies.some((s) => s.role === "primary")) {
        strategies[0]!.role = "primary";
      }
    }

    const name = String(c.name ?? "").trim() || `AI \u7EC4\u5408 ${out.length + 1}`;
    const candidate: AiCompositionCandidate = {
      name,
      method,
      rationale: typeof c.rationale === "string" ? c.rationale.trim() : undefined,
      strategies,
    };
    if (method === "voting") {
      candidate.minVotes = clampInteger(
        c.minVotes,
        1,
        strategies.length,
        Math.ceil(strategies.length / 2),
      );
    }
    out.push(candidate);
  }

  return out;
}

// ── Symbol param parsing ─────────────────────────────────────────

export function parseSymbolsParam(raw: unknown): string[] {
  if (!raw) return [];
  if (Array.isArray(raw)) return raw.map(String).filter(Boolean);
  return String(raw)
    .split(",")
    .map((s: string) => s.trim())
    .filter(Boolean);
}

// ── AI message parsing ───────────────────────────────────────────

function extractRawMessageText(message: unknown): string {
  const m = message as Record<string, unknown>;
  const content = m.content;
  if (typeof content === "string") {
    return content;
  }
  if (Array.isArray(content)) {
    const textParts = content
      .map((part) => {
        const p = part as Record<string, unknown>;
        if (p.type === "text" && typeof p.text === "string") {
          return p.text;
        }
        return null;
      })
      .filter((v): v is string => typeof v === "string");
    if (textParts.length > 0) {
      return textParts.join("\n");
    }
  }
  return typeof m.text === "string" ? m.text : "";
}

export function extractLastAssistantText(messages: unknown[]): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i] as Record<string, unknown>;
    if (msg.role === "assistant") {
      const text = extractRawMessageText(msg).trim();
      if (text) {
        return text;
      }
    }
  }
  return "";
}

export function parseJsonFromText(text: string): unknown {
  const trimmed = text.trim();
  if (!trimmed) {
    throw new Error("AI 未返回内容");
  }
  try {
    return JSON.parse(trimmed);
  } catch {
    const fenceMatch = /```(?:json)?\s*([\s\S]*?)```/i.exec(trimmed);
    if (fenceMatch?.[1]) {
      return JSON.parse(fenceMatch[1].trim());
    }
    const firstBrace = trimmed.indexOf("{");
    const lastBrace = trimmed.lastIndexOf("}");
    if (firstBrace >= 0 && lastBrace > firstBrace) {
      return JSON.parse(trimmed.slice(firstBrace, lastBrace + 1));
    }
    throw new Error("AI 返回不是有效 JSON");
  }
}
