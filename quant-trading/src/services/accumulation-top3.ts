import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";
import { toNumberOrDefault } from "../utils/number-utils.js";
import { analyzeTechMoat, loadTechMoatInputsForSymbol } from "./tech-moat-analysis.js";

const DEFAULT_TOP3_THRESHOLDS = {
  pre_accum_days: 10,
  pre_accum_count_min: 2,
  pre_accum_volume_ratio: 1.5,
  rush_change_pct: 5,
  rush_volume_ratio: 2,
  rush_turnover_pct: 3,
  pre_surge_chip_min: 8,
  pre_surge_volume_price_min: 6,
  pre_surge_turnover_min: 6,
} as const;

export interface AccumulationTop3Thresholds {
  pre_accum_days: number;
  pre_accum_count_min: number;
  pre_accum_volume_ratio: number;
  rush_change_pct: number;
  rush_volume_ratio: number;
  rush_turnover_pct: number;
  pre_surge_chip_min: number;
  pre_surge_volume_price_min: number;
  pre_surge_turnover_min: number;
}

const RESTRUCTURE_KEYWORDS = [
  "重组",
  "并购",
  "收购",
  "资产注入",
  "借壳",
  "股权转让",
  "定增",
  "重大事项",
];

const MIN_COMPOSITE_CHIP_SCORE = 20;

type GenericObject = Record<string, unknown>;

export interface AccumulationTop3Item {
  rank: number;
  symbol: string;
  name: string;
  price: number;
  chipScore: number;
  buildScore: number;
  confidence: number;
  netConviction: number;
  moatScore: number;
  moatGrade: string;
  conditionHitCount: number;
  totalScore: number;
  judgment: string;
  conditionHits: {
    preAccumulation: boolean;
    institutionalRush: boolean;
    techMoatOrRestructuring: boolean;
    preSurgeAnomaly: boolean;
  };
  conditionDetails: {
    preAccumulation: string;
    institutionalRush: string;
    techMoatOrRestructuring: string;
    preSurgeAnomaly: string;
  };
  restructureKeywords: string[];
  topEvidence: string[];
  selectionReason: string;
  reasonTrail: string[];
  ambushReasons?: string[];
  riskFlags?: string[];
  scoreComponents?: {
    base: number;
    pre_accum_hits: number;
    main_force_zljc: number;
    main_force_var6: number;
    ma_deviation: number;
    day_change: number;
  };
}

export interface AccumulationTop3Response {
  action: "top3";
  lookback: number;
  min_confidence: number;
  evaluate_count: number;
  thresholds: AccumulationTop3Thresholds;
  top3: AccumulationTop3Item[];
}

function normalizeThresholds(
  raw?: Partial<AccumulationTop3Thresholds>,
): AccumulationTop3Thresholds {
  return {
    pre_accum_days: Math.max(
      5,
      Math.min(30, Math.trunc(raw?.pre_accum_days ?? DEFAULT_TOP3_THRESHOLDS.pre_accum_days)),
    ),
    pre_accum_count_min: Math.max(
      1,
      Math.min(
        6,
        Math.trunc(raw?.pre_accum_count_min ?? DEFAULT_TOP3_THRESHOLDS.pre_accum_count_min),
      ),
    ),
    pre_accum_volume_ratio: Math.max(
      1,
      Math.min(
        5,
        toFiniteNumber(raw?.pre_accum_volume_ratio, DEFAULT_TOP3_THRESHOLDS.pre_accum_volume_ratio),
      ),
    ),
    rush_change_pct: Math.max(
      1,
      Math.min(20, toFiniteNumber(raw?.rush_change_pct, DEFAULT_TOP3_THRESHOLDS.rush_change_pct)),
    ),
    rush_volume_ratio: Math.max(
      1,
      Math.min(
        6,
        toFiniteNumber(raw?.rush_volume_ratio, DEFAULT_TOP3_THRESHOLDS.rush_volume_ratio),
      ),
    ),
    rush_turnover_pct: Math.max(
      0,
      Math.min(
        30,
        toFiniteNumber(raw?.rush_turnover_pct, DEFAULT_TOP3_THRESHOLDS.rush_turnover_pct),
      ),
    ),
    pre_surge_chip_min: Math.max(
      0,
      Math.min(
        20,
        toFiniteNumber(raw?.pre_surge_chip_min, DEFAULT_TOP3_THRESHOLDS.pre_surge_chip_min),
      ),
    ),
    pre_surge_volume_price_min: Math.max(
      0,
      Math.min(
        20,
        toFiniteNumber(
          raw?.pre_surge_volume_price_min,
          DEFAULT_TOP3_THRESHOLDS.pre_surge_volume_price_min,
        ),
      ),
    ),
    pre_surge_turnover_min: Math.max(
      0,
      Math.min(
        20,
        toFiniteNumber(raw?.pre_surge_turnover_min, DEFAULT_TOP3_THRESHOLDS.pre_surge_turnover_min),
      ),
    ),
  };
}

function toFiniteNumber(value: unknown, fallback = 0): number {
  return toNumberOrDefault(value, fallback);
}

function toStringArray(value: unknown, limit = 6): string[] {
  // Array fields are all-or-nothing: non-array inputs are dropped,
  // valid arrays are normalized to trimmed strings and capped in length.
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => String(item ?? "").trim())
    .filter(Boolean)
    .slice(0, limit);
}

function parseAmbushScoreComponents(value: unknown): AccumulationTop3Item["scoreComponents"] {
  // Keep parsing fault-tolerant at field level: malformed objects are dropped,
  // while malformed numeric fields are coerced to 0 via toFiniteNumber.
  if (!value || typeof value !== "object") return undefined;
  const raw = value as GenericObject;
  return {
    base: toFiniteNumber(raw.base),
    pre_accum_hits: toFiniteNumber(raw.pre_accum_hits),
    main_force_zljc: toFiniteNumber(raw.main_force_zljc),
    main_force_var6: toFiniteNumber(raw.main_force_var6),
    ma_deviation: toFiniteNumber(raw.ma_deviation),
    day_change: toFiniteNumber(raw.day_change),
  };
}

function extractRawEvents(detail: GenericObject | null): GenericObject[] {
  return Array.isArray(detail?.rawEvents)
    ? (detail?.rawEvents as GenericObject[])
    : Array.isArray(detail?.timeline)
      ? (detail?.timeline as GenericObject[])
      : [];
}

function findEventDetail(events: GenericObject[], eventType: string): string {
  const event = events.find((item) => String(item.eventType ?? "") === eventType);
  return String(event?.detail ?? event?.title ?? "").trim();
}

function detectRestructureKeywords(newsTitles: string[]): string[] {
  const hits = new Set<string>();
  for (const title of newsTitles) {
    for (const keyword of RESTRUCTURE_KEYWORDS) {
      if (title.includes(keyword)) hits.add(keyword);
    }
  }
  return Array.from(hits);
}

function describeMoatSignal(moat: GenericObject | null, restructureKeywords: string[]): string {
  if (restructureKeywords.length > 0) {
    return `命中重组/资本运作关键词: ${restructureKeywords.join("、")}`;
  }
  const evidenceChain = Array.isArray(moat?.evidenceChain)
    ? (moat?.evidenceChain as GenericObject[])
    : [];
  const firstEvidence = evidenceChain[0];
  if (firstEvidence) {
    return String(
      firstEvidence.detail ?? firstEvidence.factor ?? moat?.moatGrade ?? "技术壁垒证据增强",
    ).trim();
  }
  return String(moat?.moatGrade ?? "技术壁垒证据增强").trim();
}

function buildJudgment(hitCount: number): string {
  if (hitCount >= 4) return "四项共振，可优先跟踪";
  if (hitCount === 3) return "三项齐备，列入重点跟踪";
  if (hitCount === 2) return "两项命中，列入重点观察";
  if (hitCount === 1) return "单项命中，继续确认";
  return "暂无核心条件共振";
}

function detectPreSurgeAnomaly(input: {
  chipDimensionScore: number;
  volumePriceScore: number;
  turnoverScore: number;
  buildScore: number;
  confidence: number;
  thresholds: AccumulationTop3Thresholds;
}): { matched: boolean; detail: string } {
  const chipsReady = input.chipDimensionScore >= input.thresholds.pre_surge_chip_min;
  const volumePriceReady = input.volumePriceScore >= input.thresholds.pre_surge_volume_price_min;
  const turnoverReady = input.turnoverScore >= input.thresholds.pre_surge_turnover_min;
  const institutionSupport = input.buildScore >= 15 || input.confidence >= 45;

  const matched = chipsReady && volumePriceReady && turnoverReady;
  if (matched) {
    const supportText = institutionSupport ? "，且建仓/置信支持" : "";
    return {
      matched: true,
      detail: `筹码${input.chipDimensionScore.toFixed(1)}(≥${input.thresholds.pre_surge_chip_min.toFixed(1)})、量价${input.volumePriceScore.toFixed(1)}(≥${input.thresholds.pre_surge_volume_price_min.toFixed(1)})、换手${input.turnoverScore.toFixed(1)}(≥${input.thresholds.pre_surge_turnover_min.toFixed(1)})形成启动前异动${supportText}`,
    };
  }

  return {
    matched: false,
    detail: `异动未成型（筹码${input.chipDimensionScore.toFixed(1)}/${input.thresholds.pre_surge_chip_min.toFixed(1)}，量价${input.volumePriceScore.toFixed(1)}/${input.thresholds.pre_surge_volume_price_min.toFixed(1)}，换手${input.turnoverScore.toFixed(1)}/${input.thresholds.pre_surge_turnover_min.toFixed(1)}）`,
  };
}

function buildSelectionReason(input: {
  chipScore: number;
  preAccumulation: boolean;
  institutionalRush: boolean;
  techMoatOrRestructuring: boolean;
  preSurgeAnomaly: boolean;
}): { selectionReason: string; reasonTrail: string[] } {
  const reasonTrail: string[] = [];

  if (input.preAccumulation) reasonTrail.push("前期吸筹命中");
  if (input.institutionalRush) reasonTrail.push("机构抢筹命中");
  if (input.chipScore >= MIN_COMPOSITE_CHIP_SCORE) {
    reasonTrail.push(`纯吸筹评分达标(${input.chipScore.toFixed(1)})`);
  }
  if (input.techMoatOrRestructuring) reasonTrail.push("壁垒/重组增强");
  if (input.preSurgeAnomaly) reasonTrail.push("启动前异动命中");

  let selectionReason = "复合信号入围";
  if (input.preAccumulation && input.institutionalRush) {
    selectionReason = "吸筹与抢筹共振";
  } else if (input.preSurgeAnomaly) {
    selectionReason = "启动前异动主导";
  } else if (input.preAccumulation) {
    selectionReason = "前期吸筹主导";
  } else if (input.institutionalRush) {
    selectionReason = "机构抢筹主导";
  } else if (input.chipScore >= MIN_COMPOSITE_CHIP_SCORE) {
    selectionReason = "纯吸筹评分达标";
  }

  return { selectionReason, reasonTrail };
}

function computeCompositeScore(input: {
  chipScore: number;
  buildScore: number;
  confidence: number;
  netConviction: number;
  moatScore: number;
  restructureKeywordCount: number;
  preAccumulation: boolean;
  institutionalRush: boolean;
  techMoatOrRestructuring: boolean;
  preSurgeAnomaly: boolean;
}): number {
  const accumulationHitCount = [input.preAccumulation, input.institutionalRush].filter(
    Boolean,
  ).length;
  const weakChipPenalty =
    input.chipScore < MIN_COMPOSITE_CHIP_SCORE
      ? (MIN_COMPOSITE_CHIP_SCORE - input.chipScore) * 1.2
      : 0;

  const totalScore =
    accumulationHitCount * 32 +
    (input.techMoatOrRestructuring ? 10 : 0) +
    (input.preSurgeAnomaly ? 14 : 0) +
    input.chipScore * 0.55 +
    input.buildScore * 0.35 +
    input.confidence * 0.1 +
    Math.max(0, input.netConviction) * 0.45 +
    input.moatScore * 1.2 +
    input.restructureKeywordCount * 1.5 -
    weakChipPenalty;

  return Number(totalScore.toFixed(1));
}

export async function buildAccumulationTop3(params: {
  config: QuantTradingConfig;
  symbols?: string[];
  lookback?: number;
  min_confidence?: number;
  evaluate_count?: number;
  as_of_date?: string;
  thresholds?: Partial<AccumulationTop3Thresholds>;
}): Promise<AccumulationTop3Response> {
  const { config } = params;
  const lookback = Math.max(10, Math.trunc(params.lookback ?? 30));
  const minConfidence = Math.max(0, Math.trunc(params.min_confidence ?? 40));
  const evaluateCount = Math.min(20, Math.max(3, Math.trunc(params.evaluate_count ?? 15)));
  const thresholds = normalizeThresholds(params.thresholds);

  const scanResult = await runPythonScript<Record<string, unknown>>(
    "engines/chip_accumulation.py",
    {
      action: "scan",
      symbols: params.symbols?.join(","),
      lookback,
      min_confidence: minConfidence,
      as_of_date: params.as_of_date,
    },
    config,
    { timeoutMs: 300_000 },
  );

  if (!scanResult.ok || !scanResult.data || typeof scanResult.data !== "object") {
    throw new Error(scanResult.error ?? "主力吸筹条件 Top3 扫描失败");
  }

  let stocks = Array.isArray(scanResult.data.stocks)
    ? (scanResult.data.stocks as GenericObject[])
    : [];

  if (stocks.length === 0 && minConfidence > 0) {
    const fallbackScan = await runPythonScript<Record<string, unknown>>(
      "engines/chip_accumulation.py",
      {
        action: "scan",
        symbols: params.symbols?.join(","),
        lookback,
        min_confidence: 0,
        as_of_date: params.as_of_date,
      },
      config,
      { timeoutMs: 300_000 },
    );
    if (fallbackScan.ok && fallbackScan.data && typeof fallbackScan.data === "object") {
      stocks = Array.isArray(fallbackScan.data.stocks)
        ? (fallbackScan.data.stocks as GenericObject[])
        : stocks;
    }
  }

  if (stocks.length === 0) {
    const ambushScan = await runPythonScript<Record<string, unknown>>(
      "engines/chip_accumulation.py",
      {
        action: "ambush_scan",
        symbols: params.symbols?.join(","),
        lookback,
        min_confidence: 0,
        as_of_date: params.as_of_date,
      },
      config,
      { timeoutMs: 300_000 },
    );
    if (ambushScan.ok && ambushScan.data && typeof ambushScan.data === "object") {
      stocks = Array.isArray(ambushScan.data.stocks)
        ? (ambushScan.data.stocks as GenericObject[])
        : stocks;
    }
  }

  if (stocks.length === 0) {
    const institutionScan = await runPythonScript<Record<string, unknown>>(
      "scripts/institution_activity.py",
      {
        action: "scan",
        symbols: params.symbols?.join(","),
        lookback_days: thresholds.pre_accum_days,
        min_confidence: 0,
        as_of_date: params.as_of_date,
        ...thresholds,
      },
      config,
      { timeoutMs: 300_000 },
    );
    if (institutionScan.ok && institutionScan.data && typeof institutionScan.data === "object") {
      const leaders = Array.isArray(institutionScan.data.leaders)
        ? (institutionScan.data.leaders as GenericObject[])
        : [];
      const candidates = Array.isArray(institutionScan.data.candidates)
        ? (institutionScan.data.candidates as GenericObject[])
        : [];
      const pool = (leaders.length > 0 ? leaders : candidates).slice(0, evaluateCount);
      if (pool.length > 0) {
        stocks = pool.map((item) => ({
          symbol: String(item.symbol ?? "").trim(),
          name: String(item.name ?? item.symbol ?? "").trim(),
          price: toFiniteNumber(item.price),
          total_score: toFiniteNumber(item.netConviction) + toFiniteNumber(item.buildScore) * 0.5,
        }));
      }
    }
  }

  const rows: AccumulationTop3Item[] = [];
  for (const stock of stocks.slice(0, evaluateCount)) {
    const symbol = String(stock.symbol ?? "").trim();
    if (!symbol) continue;
    const name = String(stock.name ?? symbol).trim();

    const institutionDetailResult = await runPythonScript<Record<string, unknown>>(
      "scripts/institution_activity.py",
      {
        action: "detail",
        symbol,
        lookback_days: thresholds.pre_accum_days,
        as_of_date: params.as_of_date,
        ...thresholds,
      },
      config,
    );
    const institutionDetail =
      institutionDetailResult.ok &&
      institutionDetailResult.data &&
      typeof institutionDetailResult.data === "object"
        ? (institutionDetailResult.data as GenericObject)
        : null;
    const summary = institutionDetail?.summary as GenericObject | undefined;
    const rawEvents = extractRawEvents(institutionDetail);

    const moatInputs = await loadTechMoatInputsForSymbol(symbol, config);
    const moat = moatInputs.newsTitles.length
      ? await analyzeTechMoat(symbol, moatInputs.companyName || name, moatInputs.newsTitles, config)
      : null;
    const restructureKeywords = detectRestructureKeywords(moatInputs.newsTitles);
    const moatScore = toFiniteNumber(moat?.moatScore);
    const moatGrade = String(moat?.moatGrade ?? "待验证");

    const preAccumulation = rawEvents.some(
      (item) => String(item.eventType ?? "") === "pre_accumulation_threshold_hit",
    );
    const institutionalRush = rawEvents.some(
      (item) => String(item.eventType ?? "") === "institutional_rush_threshold_hit",
    );
    const techMoatOrRestructuring = moatScore >= 5 || restructureKeywords.length > 0;
    const dimensionScores =
      stock.dimension_scores && typeof stock.dimension_scores === "object"
        ? (stock.dimension_scores as GenericObject)
        : {};
    const chipDimensionScore = toFiniteNumber(dimensionScores.chip);
    const volumePriceScore = toFiniteNumber(dimensionScores.volume_price);
    const turnoverScore = toFiniteNumber(dimensionScores.turnover);

    const chipScore = toFiniteNumber(stock.total_score);
    const ambushReasons = toStringArray(stock.reasons);
    const riskFlags = toStringArray(stock.risk_flags);
    const scoreComponents = parseAmbushScoreComponents(stock.score_components);
    const buildScore = toFiniteNumber(summary?.buildScore);
    const confidence = toFiniteNumber(summary?.confidence);
    const netConviction = toFiniteNumber(summary?.netConviction);
    const preSurgeAnomalyResult = detectPreSurgeAnomaly({
      chipDimensionScore,
      volumePriceScore,
      turnoverScore,
      buildScore,
      confidence,
      thresholds,
    });
    const preSurgeAnomaly = preSurgeAnomalyResult.matched;
    const conditionHitCount = [
      preAccumulation,
      institutionalRush,
      techMoatOrRestructuring,
      preSurgeAnomaly,
    ].filter(Boolean).length;
    const hasAccumulationBackbone =
      preAccumulation || institutionalRush || chipScore >= MIN_COMPOSITE_CHIP_SCORE;

    if (!hasAccumulationBackbone && !preSurgeAnomaly) continue;

    const totalScore = computeCompositeScore({
      chipScore,
      buildScore,
      confidence,
      netConviction,
      moatScore,
      restructureKeywordCount: restructureKeywords.length,
      preAccumulation,
      institutionalRush,
      techMoatOrRestructuring,
      preSurgeAnomaly,
    });
    const { selectionReason, reasonTrail } = buildSelectionReason({
      chipScore,
      preAccumulation,
      institutionalRush,
      techMoatOrRestructuring,
      preSurgeAnomaly,
    });

    rows.push({
      rank: 0,
      symbol,
      name,
      price: toFiniteNumber(summary?.price ?? stock.price),
      chipScore,
      buildScore,
      confidence,
      netConviction,
      moatScore,
      moatGrade,
      conditionHitCount,
      totalScore,
      judgment: buildJudgment(conditionHitCount),
      conditionHits: {
        preAccumulation,
        institutionalRush,
        techMoatOrRestructuring,
        preSurgeAnomaly,
      },
      conditionDetails: {
        preAccumulation: preAccumulation
          ? findEventDetail(rawEvents, "pre_accumulation_threshold_hit") ||
            "近10天至少2次放量且 MA20 上行"
          : "未命中前期吸筹阈值",
        institutionalRush: institutionalRush
          ? findEventDetail(rawEvents, "institutional_rush_threshold_hit") || "今日强势并放量攻击"
          : "未命中机构抢筹阈值",
        techMoatOrRestructuring: techMoatOrRestructuring
          ? describeMoatSignal(moat, restructureKeywords)
          : "暂无明确技术壁垒/重组线索",
        preSurgeAnomaly: preSurgeAnomalyResult.detail,
      },
      restructureKeywords,
      topEvidence: Array.isArray(institutionDetail?.positiveEvidence)
        ? (institutionDetail?.positiveEvidence as GenericObject[])
            .map((item) => String(item.title ?? "").trim())
            .filter(Boolean)
            .slice(0, 3)
        : [],
      selectionReason,
      reasonTrail,
      ambushReasons: ambushReasons.length > 0 ? ambushReasons : undefined,
      riskFlags: riskFlags.length > 0 ? riskFlags : undefined,
      scoreComponents,
    });
  }

  const rankedWithHits = rows
    .filter((item) => item.conditionHitCount > 0)
    .sort((left, right) => {
      if (right.conditionHitCount !== left.conditionHitCount) {
        return right.conditionHitCount - left.conditionHitCount;
      }
      return right.totalScore - left.totalScore;
    })
    .slice(0, 3);

  const ranked = (
    rankedWithHits.length > 0
      ? rankedWithHits
      : rows.sort((left, right) => right.totalScore - left.totalScore).slice(0, 3)
  ).map((item, index) => ({ ...item, rank: index + 1 }));

  return {
    action: "top3",
    lookback,
    min_confidence: minConfidence,
    evaluate_count: evaluateCount,
    thresholds,
    top3: ranked,
  };
}
