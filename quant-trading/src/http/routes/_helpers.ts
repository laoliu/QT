/**
 * Shared helpers, types, and constants for admin route handlers.
 */
import type { IncomingMessage, ServerResponse } from "node:http";
import type { CompositionMethod } from "../../engine/dynamic-composer.js";
import type { TickStrategyId } from "../../types.js";
import type { QuantTradingConfig } from "../../types.js";
import { parseJsonBody } from "../parse-body.js";
import { formatDateCST } from "../../utils/cst-time.js";

export function json(res: ServerResponse, data: unknown, status = 200): void {
  const body = JSON.stringify(data, null, 2);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(body),
    "Cache-Control": "no-store",
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
  return formatDateCST(date);
}

export type JsonBody = Record<string, unknown>;

export async function parseObjectBody(req: IncomingMessage): Promise<JsonBody> {
  return parseJsonBody<JsonBody>(req);
}

// ── Daily pick constants ─────────────────────────────────────────

export const DAILY_PICK_STRATEGIES = [
  // ── 基础策略 ──
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
  "scored",
  "mean-reversion",
  "macd-confirmation",
  "cta-kdj",
  // ── 组合 / RSI 增强 ──
  "composite",
  "enhanced-composite",
  "rsi-filter",
  "rsi-enhanced",
  "rsi-macd-combined",
  // ── 扩展技术指标 ──
  "trix-momentum",
  "mfi-oscillator",
  "aroon-trend",
  "cmf-divergence",
  "elder-ray",
  "supertrend",
  "hull-ma-crossover",
  "dema-crossover",
  "triple-ma-crossover",
  "ma-envelope",
  "connors-rsi",
  "kama-trend",
  "demarker",
  // ── CTA 经典策略 ──
  "cta-trend",
  "dual-thrust",
  "turtle-breakout",
  "macd-trend",
  "bollinger-breakout",
  "keltner-channel",
  "r-breaker",
  "aberration",
  "ttm-squeeze",
  "dmi-adx",
  "parabolic-sar",
  "volatility-target",
  "momentum-rotation",
  "obv-trend",
  "ichimoku",
  "grid-trading",
  "multi-ma-convergence",
  "donchian-mid-reversion",
  "pivot-point",
  "chandelier-exit",
  "roc-momentum",
  "cci-divergence",
  "ema-crossover",
  "atr-trailing",
  "stochastic-rsi",
  "macd-histogram-reversal",
  "bollinger-bandwidth",
  // ── CTA 扩展策略 ──
  "cta-awesome-oscillator",
  "cta-elder-force",
  "cta-demarker",
  "cta-ultimate-oscillator",
  "cta-adx-breakout",
  "cta-trix-trend",
  "cta-hull-ma-trend",
  "cta-mass-index",
  "cta-chaikin-oscillator",
  "cta-coppock",
  "cta-rsi-divergence",
  "cta-volume-breakout",
  "cta-adaptive-ma",
  "cta-ma-envelope",
  "cta-connors-rsi",
  "cta-aroon-oscillator",
  "cta-mfi-trend",
  "cta-dema-crossover",
  "cta-triple-ma",
  "cta-supertrend",
  // ── T0 日内策略 (A股暂不支持T+0，已禁用) ──
  // "t0-day-trading",
  // "t0-enhanced",
  // ── Python Backtrader CTA趋势 ──
  "ema-cross",
  "adx-trend",
  "aroon",
  "vortex",
  "cci",
  "trix",
  "dmi",
  "dema-cross",
  "tema-cross",
  // ── Python Backtrader 通道 ──
  "donchian-breakout",
  "atr-channel",
  "envelope",
  "price-channel-breakout",
  // ── Python Backtrader 指标 ──
  "stochastic",
  "mfi",
  "cmf",
  "adl",
  "roc",
  "vwap",
  "adaptive-ma",
  "obv",
  // ── Python Backtrader 形态 ──
  "engulfing",
  "hammer",
  "doji",
  "morning-star",
  "three-soldiers",
  "harami",
  // ── Python Backtrader 组合 ──
  "macd-rsi-combo",
  "bollinger-rsi-combo",
  "ma-volume-combo",
  "kdj-macd-combo",
  "ichimoku-adx-combo",
  "multi-tf-ma",
  "volatility-breakout",
  "regime-switch",
  // ── Aipha 策略适配 ──
  "fitness_optimal",
] as const;

// ── AIPHA 动态策略同步 ──────────────────────────────────────────

export interface AiphaStrategyInfo {
  id: string;
  name: string;
}

/** Cached AIPHA A-stock strategies loaded from Python registry. */
let _aiphaStrategies: AiphaStrategyInfo[] = [];
let _aiphaLoaded = false;

/** Set the AIPHA strategies cache (called when loaded from Python). */
export function setAiphaStrategies(strategies: AiphaStrategyInfo[]): void {
  _aiphaStrategies = strategies;
  _aiphaLoaded = true;
  // Sync labels
  for (const s of strategies) {
    if (!DAILY_PICK_STRATEGY_LABELS[s.id]) {
      DAILY_PICK_STRATEGY_LABELS[s.id] = s.name;
    }
  }
}

/** Get cached AIPHA strategy list. */
export function getAiphaStrategies(): AiphaStrategyInfo[] {
  return _aiphaStrategies;
}

/** Check if AIPHA strategies have been loaded. */
export function isAiphaLoaded(): boolean {
  return _aiphaLoaded;
}

/** Check if a strategy ID is a known AIPHA strategy. */
export function isAiphaStrategy(id: string): boolean {
  return _aiphaStrategies.some((s) => s.id === id);
}

/** Get the full set of allowed strategy IDs (hardcoded + AIPHA). */
export function getAllAllowedStrategies(): Set<string> {
  const set = new Set<string>(DAILY_PICK_STRATEGIES);
  for (const s of _aiphaStrategies) {
    set.add(s.id);
  }
  return set;
}

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
  scored: "加权仓位",
  "mean-reversion": "均值回归",
  "macd-confirmation": "MACD确认",
  "cta-kdj": "KDJ随机",
  // ── 组合 / RSI 增强 ──
  composite: "综合投票",
  "enhanced-composite": "增强综合",
  "rsi-filter": "RSI动量过滤",
  "rsi-enhanced": "RSI增强选股",
  "rsi-macd-combined": "RSI+MACD组合",
  // ── 扩展技术指标 ──
  "trix-momentum": "TRIX动量",
  "mfi-oscillator": "MFI资金流",
  "aroon-trend": "阿隆趋势",
  "cmf-divergence": "蔡金资金流背离",
  "elder-ray": "Elder Ray力量",
  supertrend: "超级趋势",
  "hull-ma-crossover": "Hull MA交叉",
  "dema-crossover": "DEMA交叉",
  "triple-ma-crossover": "三重均线交叉",
  "ma-envelope": "MA包络",
  "connors-rsi": "Connors RSI",
  "kama-trend": "KAMA自适应趋势",
  demarker: "DeMARK指标",
  // ── CTA 经典策略 ──
  "cta-trend": "CTA趋势跟踪",
  "dual-thrust": "Dual Thrust双推力",
  "turtle-breakout": "海龟突破",
  "macd-trend": "MACD趋势",
  "bollinger-breakout": "布林带突破",
  "keltner-channel": "肯特纳通道",
  "r-breaker": "R-Breaker反转",
  aberration: "标准差偏离",
  "ttm-squeeze": "TTM挤压",
  "dmi-adx": "DMI+ADX",
  "parabolic-sar": "抛物线SAR",
  "volatility-target": "波动率目标",
  "momentum-rotation": "动量轮动",
  "obv-trend": "OBV趋势",
  ichimoku: "一目均衡表",
  "grid-trading": "网格交易",
  "multi-ma-convergence": "多均线收敛",
  "donchian-mid-reversion": "唐奇安中线回归",
  "pivot-point": "枢轴点",
  "chandelier-exit": "烛台止损",
  "roc-momentum": "ROC动量",
  "cci-divergence": "CCI背离",
  "ema-crossover": "EMA均线交叉",
  "atr-trailing": "ATR追踪止损",
  "stochastic-rsi": "随机RSI",
  "macd-histogram-reversal": "MACD柱状反转",
  "bollinger-bandwidth": "布林带带宽",
  // ── CTA 扩展策略 ──
  "cta-awesome-oscillator": "AO振荡器",
  "cta-elder-force": "Elder力量指数",
  "cta-demarker": "CTA DeMARK",
  "cta-ultimate-oscillator": "终极振荡器",
  "cta-adx-breakout": "ADX突破",
  "cta-trix-trend": "TRIX趋势",
  "cta-hull-ma-trend": "Hull MA趋势",
  "cta-mass-index": "质量指数",
  "cta-chaikin-oscillator": "蔡金振荡器",
  "cta-coppock": "Coppock曲线",
  "cta-rsi-divergence": "RSI背离",
  "cta-volume-breakout": "成交量突破",
  "cta-adaptive-ma": "自适应均线",
  "cta-ma-envelope": "CTA MA包络",
  "cta-connors-rsi": "CTA Connors RSI",
  "cta-aroon-oscillator": "阿隆振荡器",
  "cta-mfi-trend": "MFI趋势",
  "cta-dema-crossover": "CTA DEMA交叉",
  "cta-triple-ma": "CTA三重均线",
  "cta-supertrend": "CTA超级趋势",
  // ── T0 日内 ──
  "t0-day-trading": "T0日内交易",
  "t0-enhanced": "T0增强",
  // ── Python Backtrader CTA趋势 ──
  "ema-cross": "EMA均线交叉",
  "adx-trend": "ADX趋势强度",
  "aroon": "阿隆指标",
  "vortex": "漩涡指标",
  "cci": "CCI顺势",
  "trix": "TRIX平滑",
  "dmi": "趋向指标DMI",
  "dema-cross": "DEMA交叉",
  "tema-cross": "TEMA交叉",
  // ── Python Backtrader 通道 ──
  "donchian-breakout": "唐奇安突破",
  "atr-channel": "ATR通道",
  "envelope": "包络线",
  "price-channel-breakout": "价格通道突破",
  // ── Python Backtrader 指标 ──
  "stochastic": "随机振荡器",
  "mfi": "MFI资金流",
  "cmf": "蔡金资金流",
  "adl": "聚散指标ADL",
  "roc": "变化率ROC",
  "vwap": "VWAP均价",
  "adaptive-ma": "自适应均线",
  "obv": "能量潮OBV",
  // ── Python Backtrader 形态 ──
  "engulfing": "吞没形态",
  "hammer": "锤子线",
  "doji": "十字星",
  "morning-star": "启明星",
  "three-soldiers": "三兵三鸦",
  "harami": "孕线形态",
  // ── Python Backtrader 组合 ──
  "macd-rsi-combo": "MACD+RSI组合",
  "bollinger-rsi-combo": "布林+RSI组合",
  "ma-volume-combo": "均线+量能组合",
  "kdj-macd-combo": "KDJ+MACD组合",
  "ichimoku-adx-combo": "一目+ADX组合",
  "multi-tf-ma": "多周期均线",
  "volatility-breakout": "波动率突破",
  "regime-switch": "趋势/震荡切换",
  // ── Aipha 策略适配 ──
  fitness_optimal: "个股最优(适配)",
  stable_compounder: "稳健复利",
  high_delivery_expect: "高送转预期",
  buyback_signal: "回购信号",
  turnaround_play: "困境反转",
  dividend_quality: "红利质量",
  value_quality: "价值质量",
  insider_buying: "内部人增持",
  earnings_momentum: "盈利动量",
  garp_select: "GARP选股",
  sector_rotation: "行业轮动",
};

export const CTA_SCREEN_STRATEGY_MAP: Record<string, string> = {
  // ── 基础 CTA → 筛选策略映射 ──
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
  scored: "mixed",
  "mean-reversion": "swing",
  "macd-confirmation": "mixed",
  "cta-kdj": "swing",
  // ── 组合 / RSI 增强 ──
  composite: "mixed",
  "enhanced-composite": "mixed",
  "rsi-filter": "swing",
  "rsi-enhanced": "swing",
  "rsi-macd-combined": "mixed",
  // ── 扩展技术指标 ──
  "trix-momentum": "momentum",
  "mfi-oscillator": "swing",
  "aroon-trend": "momentum",
  "cmf-divergence": "swing",
  "elder-ray": "momentum",
  supertrend: "momentum",
  "hull-ma-crossover": "momentum",
  "dema-crossover": "momentum",
  "triple-ma-crossover": "momentum",
  "ma-envelope": "swing",
  "connors-rsi": "swing",
  "kama-trend": "momentum",
  demarker: "swing",
  // ── CTA 经典策略 ──
  "cta-trend": "momentum",
  "dual-thrust": "momentum",
  "turtle-breakout": "momentum",
  "macd-trend": "momentum",
  "bollinger-breakout": "swing",
  "keltner-channel": "swing",
  "r-breaker": "swing",
  aberration: "swing",
  "ttm-squeeze": "swing",
  "dmi-adx": "momentum",
  "parabolic-sar": "momentum",
  "volatility-target": "mixed",
  "momentum-rotation": "momentum",
  "obv-trend": "momentum",
  ichimoku: "momentum",
  "grid-trading": "swing",
  "multi-ma-convergence": "momentum",
  "donchian-mid-reversion": "swing",
  "pivot-point": "swing",
  "chandelier-exit": "swing",
  "roc-momentum": "momentum",
  "cci-divergence": "swing",
  "ema-crossover": "momentum",
  "atr-trailing": "momentum",
  "stochastic-rsi": "swing",
  "macd-histogram-reversal": "swing",
  "bollinger-bandwidth": "swing",
  // ── CTA 扩展策略 ──
  "cta-awesome-oscillator": "mixed",
  "cta-elder-force": "momentum",
  "cta-demarker": "swing",
  "cta-ultimate-oscillator": "swing",
  "cta-adx-breakout": "momentum",
  "cta-trix-trend": "momentum",
  "cta-hull-ma-trend": "momentum",
  "cta-mass-index": "swing",
  "cta-chaikin-oscillator": "swing",
  "cta-coppock": "momentum",
  "cta-rsi-divergence": "swing",
  "cta-volume-breakout": "momentum",
  "cta-adaptive-ma": "momentum",
  "cta-ma-envelope": "swing",
  "cta-connors-rsi": "swing",
  "cta-aroon-oscillator": "momentum",
  "cta-mfi-trend": "swing",
  "cta-dema-crossover": "momentum",
  "cta-triple-ma": "momentum",
  "cta-supertrend": "momentum",
  // ── T0 日内 ──
  "t0-day-trading": "swing",
  "t0-enhanced": "swing",
  // ── Python Backtrader CTA趋势 ──
  "ema-cross": "momentum",
  "adx-trend": "momentum",
  "aroon": "momentum",
  "vortex": "momentum",
  "cci": "momentum",
  "trix": "momentum",
  "dmi": "momentum",
  "dema-cross": "momentum",
  "tema-cross": "momentum",
  // ── Python Backtrader 通道 ──
  "donchian-breakout": "momentum",
  "atr-channel": "momentum",
  "envelope": "swing",
  "price-channel-breakout": "momentum",
  // ── Python Backtrader 指标 ──
  "stochastic": "swing",
  "mfi": "swing",
  "cmf": "swing",
  "adl": "swing",
  "roc": "momentum",
  "vwap": "swing",
  "adaptive-ma": "momentum",
  "obv": "momentum",
  // ── Python Backtrader 形态 ──
  "engulfing": "swing",
  "hammer": "swing",
  "doji": "swing",
  "morning-star": "swing",
  "three-soldiers": "momentum",
  "harami": "swing",
  // ── Python Backtrader 组合 ──
  "macd-rsi-combo": "mixed",
  "bollinger-rsi-combo": "swing",
  "ma-volume-combo": "momentum",
  "kdj-macd-combo": "mixed",
  "ichimoku-adx-combo": "momentum",
  "multi-tf-ma": "momentum",
  "volatility-breakout": "momentum",
  "regime-switch": "mixed",
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

  // ── Classify strategies by category for smarter combinations ──
  const categoryKeywords: Record<string, string[]> = {
    trend: ["ma_crossover", "dual-ma", "triple_ma", "ema", "trend", "moving_average", "macd", "supertrend", "ichimoku"],
    oscillator: ["rsi", "kdj", "bollinger", "stochastic", "cci", "williams", "oscillat", "oversold", "overbought", "mfi"],
    volume: ["volume", "vwap", "obv", "money_flow", "turnover", "accumulation", "vol_breakout"],
    momentum: ["momentum", "breakout", "channel", "donchian", "atr", "adx", "acceleration"],
    mean_reversion: ["mean_reversion", "reversion", "zscore", "deviation", "regression"],
    composite: ["combo", "combined", "multi", "fusion", "ensemble", "scored"],
  };
  const categorized: Record<string, string[]> = {};
  const uncategorized: string[] = [];
  for (const cat of Object.keys(categoryKeywords)) categorized[cat] = [];

  for (const sid of ids) {
    const lower = sid.toLowerCase();
    let matched = false;
    for (const [cat, keywords] of Object.entries(categoryKeywords)) {
      if (keywords.some((kw) => lower.includes(kw))) {
        categorized[cat]!.push(sid);
        matched = true;
        break;
      }
    }
    if (!matched) uncategorized.push(sid);
  }

  const nonEmptyCategories = Object.entries(categorized).filter(([, arr]) => arr.length > 0);

  // ── Template-based generation: cross-category complementary combos ──
  const comboTemplates = [
    { name: "趋势+震荡互补", cats: ["trend", "oscillator"], method: "voting" as CompositionMethod },
    { name: "动量突破+均值回归对冲", cats: ["momentum", "mean_reversion"], method: "weighted-score" as CompositionMethod },
    { name: "趋势确认+量价验证", cats: ["trend", "volume"], method: "gated" as CompositionMethod },
    { name: "多维度融合", cats: ["trend", "oscillator", "volume"], method: "cascade" as CompositionMethod },
    { name: "震荡+动量互补", cats: ["oscillator", "momentum"], method: "voting" as CompositionMethod },
    { name: "全方位信号融合", cats: ["trend", "oscillator", "momentum", "volume"], method: "weighted-score" as CompositionMethod },
    { name: "均线+动量+量价", cats: ["trend", "momentum", "volume"], method: "voting" as CompositionMethod },
    { name: "回归过滤+趋势主导", cats: ["mean_reversion", "trend"], method: "gated" as CompositionMethod },
    { name: "震荡过滤+动量主导", cats: ["oscillator", "momentum"], method: "gated" as CompositionMethod },
    { name: "多因子评分", cats: ["trend", "oscillator", "momentum", "mean_reversion"], method: "weighted-score" as CompositionMethod },
  ];

  // Seeded pseudo-random for deterministic-ish but varied picks
  let seed = ids.length * 7 + candidateCount * 13;
  const nextRand = () => { seed = (seed * 1664525 + 1013904223) & 0x7fffffff; return seed / 0x7fffffff; };

  for (let i = 0; i < candidateCount && candidates.length < candidateCount; i++) {
    const template = comboTemplates[i % comboTemplates.length]!;
    const method = template.method;
    const width = Math.min(maxStrategies, Math.max(minStrategies, 2));

    // Pick strategies from template categories
    const pickedIds: string[] = [];
    for (const cat of template.cats) {
      const pool = categorized[cat] ?? [];
      if (pool.length === 0) continue;
      // Pick 1-2 from each category
      const pick = Math.min(2, pool.length, width - pickedIds.length);
      for (let p = 0; p < pick; p++) {
        const idx = Math.floor(nextRand() * pool.length);
        const sid = pool[idx]!;
        if (!pickedIds.includes(sid)) pickedIds.push(sid);
      }
      if (pickedIds.length >= width) break;
    }

    // Fill from uncategorized if needed
    if (pickedIds.length < minStrategies) {
      const filler = uncategorized.length > 0 ? uncategorized : ids;
      for (let f = 0; f < filler.length && pickedIds.length < minStrategies; f++) {
        const idx = Math.floor(nextRand() * filler.length);
        const sid = filler[idx]!;
        if (!pickedIds.includes(sid)) pickedIds.push(sid);
      }
    }

    if (pickedIds.length < 2) continue;

    // Check duplicates against existing candidates
    const sigKey = method + "|" + [...pickedIds].sort().join("+");
    if (candidates.some((c) => c.method + "|" + c.strategies.map((s) => s.strategyId).sort().join("+") === sigKey)) {
      continue;
    }

    const strategies: AiSubStrategy[] = pickedIds.map((strategyId, idx) => ({
      strategyId,
      weight: method === "weighted-score" ? ((idx % 3) + 1) : 1,
      role:
        method === "gated" || method === "cascade" ? (idx === 0 ? "primary" : "filter") : "primary",
    }));

    const base: AiCompositionCandidate = {
      name: `${template.name} #${i + 1}`,
      method,
      rationale: `按策略类型互补规则自动组合（${template.cats.join("+")}）`,
      strategies,
    };
    if (method === "voting") {
      base.minVotes = Math.max(1, Math.min(strategies.length, Math.ceil(strategies.length / 2)));
    }
    candidates.push(base);
  }

  // If templates didn't produce enough, fill with simple rotation
  for (let i = candidates.length; i < candidateCount; i++) {
    const method = methods[i % methods.length] ?? "voting";
    const width = Math.min(maxStrategies, Math.max(minStrategies, 2));
    const start = Math.floor(nextRand() * ids.length);
    const pickedIds = Array.from({ length: width }, (_, idx) => ids[(start + idx) % ids.length] ?? "")
      .filter(Boolean)
      .filter((sid, idx, arr) => arr.indexOf(sid) === idx);
    if (pickedIds.length < 2) continue;

    const strategies: AiSubStrategy[] = pickedIds.map((strategyId, idx) => ({
      strategyId,
      weight: ((idx + i) % 3) + 1,
      role:
        method === "gated" || method === "cascade" ? (idx === 0 ? "primary" : "filter") : "primary",
    }));

    candidates.push({
      name: `组合 ${i + 1}`,
      method,
      rationale: `按轮转规则自动组合`,
      strategies,
      ...(method === "voting" ? { minVotes: Math.max(1, Math.ceil(strategies.length / 2)) } : {}),
    });
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
