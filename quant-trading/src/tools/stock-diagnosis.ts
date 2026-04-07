/**
 * stock_diagnosis tool — One-click comprehensive stock diagnosis.
 *
 * Aggregates data from market_data, cfo_assistant, chip_accumulation,
 * fund_flow, social_sentiment, sector_analysis to produce a multi-dimensional
 * score and buy/sell recommendation.
 *
 * Dimensions:
 *   1. Technical (技术面) — MA alignment, MACD, RSI pattern
 *   2. Fundamental (基本面) — PE/PB/ROE/revenue growth
 *   3. Capital flow (资金面) — main force net inflow, northbound, margin
 *   4. Sentiment (消息面) — social heat, recent announcements
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { QuantTradingConfig } from "../types.js";

// ── Types ────────────────────────────────────────────────────────

interface ToolLike {
  execute: (args: Record<string, unknown>) => Promise<unknown>;
}

interface ToolMap {
  market_data: ToolLike;
  cfo_assistant: ToolLike;
  chip_accumulation: ToolLike;
  fund_flow: ToolLike;
  social_sentiment: ToolLike;
  sector_analysis: ToolLike;
}

interface DimensionScore {
  score: number; // 0-100
  weight: number;
  details: Record<string, unknown>;
  summary: string;
}

type Recommendation = "强烈买入" | "买入" | "观望" | "减持" | "卖出";

// ── Helpers ──────────────────────────────────────────────────────

function num(v: unknown, fallback = 0): number {
  if (typeof v === "number" && !Number.isNaN(v)) return v;
  if (typeof v === "string") {
    const n = Number(v);
    if (!Number.isNaN(n)) return n;
  }
  return fallback;
}

function get(obj: unknown, ...keys: string[]): unknown {
  let cur = obj;
  for (const k of keys) {
    if (cur == null || typeof cur !== "object") return undefined;
    cur = (cur as Record<string, unknown>)[k];
  }
  return cur;
}

function clamp(v: number, min = 0, max = 100): number {
  return Math.max(min, Math.min(max, v));
}

async function safeCall(tool: ToolLike, args: Record<string, unknown>): Promise<Record<string, unknown>> {
  try {
    const result = await tool.execute(args);
    if (result && typeof result === "object" && !("error" in result && (result as Record<string, unknown>).error)) {
      return result as Record<string, unknown>;
    }
    return { _failed: true, _error: get(result, "error") ?? "unknown" };
  } catch (e) {
    return { _failed: true, _error: String(e) };
  }
}

// ── Score: Technical (技术面) ────────────────────────────────────

function scoreTechnical(klineData: Record<string, unknown>, quoteData: Record<string, unknown>): DimensionScore {
  const details: Record<string, unknown> = {};
  let score = 50; // neutral baseline

  // Extract indicators from kline data
  const klines = (get(klineData, "klines") ?? get(klineData, "data") ?? []) as Array<Record<string, unknown>>;
  const indicators = get(klineData, "indicators") as Record<string, unknown> | undefined;

  const currentPrice = num(get(quoteData, "price") ?? get(quoteData, "current") ?? get(quoteData, "close"));
  const change = num(get(quoteData, "changePercent") ?? get(quoteData, "change_percent") ?? get(quoteData, "pct_chg"));

  // --- MA alignment ---
  const ma5 = num(get(indicators, "ma5"));
  const ma10 = num(get(indicators, "ma10"));
  const ma20 = num(get(indicators, "ma20"));
  const ma60 = num(get(indicators, "ma60"));
  const ma120 = num(get(indicators, "ma120"));

  if (ma5 && ma20 && currentPrice) {
    // Bull: price > ma5 > ma20 > ma60
    const bullAlignment =
      (currentPrice > ma5 ? 1 : 0) +
      (ma5 > ma10 ? 1 : 0) +
      (ma10 > ma20 ? 1 : 0) +
      (ma20 > ma60 ? 1 : 0);
    const maScore = bullAlignment * 5; // 0-20
    score += maScore - 10; // center at 0
    details.maAlignment = bullAlignment;
    details.maScore = maScore;
  }

  // --- RSI ---
  const rsi = num(get(indicators, "rsi14"));
  if (rsi) {
    if (rsi < 30) score += 15; // oversold → bullish
    else if (rsi < 40) score += 8;
    else if (rsi > 80) score -= 15; // overbought → bearish
    else if (rsi > 70) score -= 8;
    details.rsi14 = rsi;
  }

  // --- MACD from kline tail ---
  if (klines.length >= 2) {
    const last = klines[klines.length - 1];
    const prev = klines[klines.length - 2];
    const macdHist = num(get(last, "macd_hist") ?? get(last, "macdHist"));
    const prevMacdHist = num(get(prev, "macd_hist") ?? get(prev, "macdHist"));
    if (macdHist !== 0 || prevMacdHist !== 0) {
      if (macdHist > 0 && macdHist > prevMacdHist) score += 8;
      else if (macdHist > 0 && macdHist < prevMacdHist) score += 3;
      else if (macdHist < 0 && macdHist > prevMacdHist) score += 5; // improving
      else if (macdHist < 0 && macdHist < prevMacdHist) score -= 8;
      details.macdHist = macdHist;
      details.macdTrend = macdHist > prevMacdHist ? "improving" : "weakening";
    }
  }

  // --- Price vs 52-week range ---
  const high52w = num(get(indicators, "high52w"));
  const low52w = num(get(indicators, "low52w"));
  if (high52w && low52w && currentPrice) {
    const range = high52w - low52w;
    if (range > 0) {
      const position = (currentPrice - low52w) / range; // 0=low, 1=high
      // Mid-range is neutral; near low is slightly bullish (value); near high is cautious
      if (position < 0.2) score += 5;
      else if (position > 0.9) score -= 5;
      details.pricePosition52w = Math.round(position * 100) + "%";
    }
  }

  // --- Recent momentum ---
  if (change) {
    details.todayChange = change;
  }

  // --- ATR volatility context ---
  const atr = num(get(indicators, "atr14"));
  if (atr && currentPrice) {
    details.atrPercent = Math.round((atr / currentPrice) * 10000) / 100;
  }

  const summary = buildTechnicalSummary(clamp(score), details);
  return { score: clamp(score), weight: 0.30, details, summary };
}

function buildTechnicalSummary(score: number, d: Record<string, unknown>): string {
  const parts: string[] = [];
  if (d.rsi14) parts.push(`RSI=${d.rsi14}`);
  if (d.maAlignment !== undefined) parts.push(`均线多排${d.maAlignment}/4`);
  if (d.macdTrend) parts.push(`MACD${d.macdTrend === "improving" ? "改善" : "转弱"}`);
  if (d.pricePosition52w) parts.push(`52周位置${d.pricePosition52w}`);
  return `技术面${score}分。${parts.join("，")}`;
}

// ── Score: Fundamental (基本面) ──────────────────────────────────

function scoreFundamental(
  fundamentals: Record<string, unknown>,
  cfoData: Record<string, unknown>,
): DimensionScore {
  const details: Record<string, unknown> = {};
  let score = 50;

  // --- PE ---
  const pe = num(get(fundamentals, "pe") ?? get(fundamentals, "PE") ?? get(fundamentals, "pe_ttm"));
  if (pe > 0) {
    if (pe < 15) score += 12;
    else if (pe < 25) score += 5;
    else if (pe > 60) score -= 12;
    else if (pe > 40) score -= 5;
    details.pe = pe;
  }

  // --- PB ---
  const pb = num(get(fundamentals, "pb") ?? get(fundamentals, "PB"));
  if (pb > 0) {
    if (pb < 1) score += 10;
    else if (pb < 2) score += 5;
    else if (pb > 8) score -= 8;
    details.pb = pb;
  }

  // --- ROE ---
  const roe = num(get(fundamentals, "roe") ?? get(fundamentals, "ROE"));
  if (roe) {
    if (roe > 20) score += 12;
    else if (roe > 15) score += 8;
    else if (roe > 10) score += 3;
    else if (roe < 5) score -= 8;
    details.roe = roe;
  }

  // --- Revenue growth ---
  const revGrowth = num(
    get(fundamentals, "revenueGrowth") ??
    get(fundamentals, "revenue_growth") ??
    get(fundamentals, "营收增速"),
  );
  if (revGrowth) {
    if (revGrowth > 30) score += 10;
    else if (revGrowth > 15) score += 5;
    else if (revGrowth < -10) score -= 10;
    else if (revGrowth < 0) score -= 5;
    details.revenueGrowth = revGrowth;
  }

  // --- CFO red flags ---
  if (!cfoData._failed) {
    const redFlags = get(cfoData, "red_flags") ?? get(cfoData, "redFlags");
    if (Array.isArray(redFlags) && redFlags.length > 0) {
      score -= Math.min(redFlags.length * 5, 20);
      details.redFlagCount = redFlags.length;
      details.redFlags = redFlags.slice(0, 3);
    }

    // DuPont ROE quality
    const dupontROE = num(get(cfoData, "dupont", "roe") ?? get(cfoData, "roe"));
    if (dupontROE > 0 && !roe) {
      details.dupontROE = dupontROE;
    }

    const healthScore = num(get(cfoData, "health_score") ?? get(cfoData, "healthScore"));
    if (healthScore > 0) {
      // CFO health contributes partially
      score += (healthScore - 50) * 0.2;
      details.cfoHealthScore = healthScore;
    }
  }

  const summary = buildFundamentalSummary(clamp(score), details);
  return { score: clamp(score), weight: 0.25, details, summary };
}

function buildFundamentalSummary(score: number, d: Record<string, unknown>): string {
  const parts: string[] = [];
  if (d.pe) parts.push(`PE=${d.pe}`);
  if (d.pb) parts.push(`PB=${d.pb}`);
  if (d.roe) parts.push(`ROE=${d.roe}%`);
  if (d.revenueGrowth) parts.push(`营收增速${d.revenueGrowth}%`);
  if (d.redFlagCount) parts.push(`${d.redFlagCount}项财务预警`);
  return `基本面${score}分。${parts.join("，")}`;
}

// ── Score: Capital Flow (资金面) ─────────────────────────────────

function scoreCapitalFlow(
  fundFlowData: Record<string, unknown>,
  chipData: Record<string, unknown>,
): DimensionScore {
  const details: Record<string, unknown> = {};
  let score = 50;

  // --- Fund flow ---
  if (!fundFlowData._failed) {
    const flows = (get(fundFlowData, "flows") ?? []) as Array<Record<string, unknown>>;
    if (flows.length > 0) {
      const latest = flows[0];
      const mainNetInflow = num(
        get(latest, "main_net_inflow") ?? get(latest, "主力净流入") ?? get(latest, "net_mf_amount"),
      );
      if (mainNetInflow !== 0) {
        if (mainNetInflow > 0) score += Math.min(mainNetInflow / 1e8 * 5, 15);
        else score -= Math.min(Math.abs(mainNetInflow) / 1e8 * 5, 15);
        details.mainNetInflow = Math.round(mainNetInflow / 1e4) / 1e4 + "亿";
      }
    }

    // Northbound data (if present from northbound action result)
    const northbound = get(fundFlowData, "northbound") as Record<string, unknown> | undefined;
    if (northbound) {
      const nbNetInflow = num(get(northbound, "net_inflow") ?? get(northbound, "net"));
      if (nbNetInflow !== 0) {
        if (nbNetInflow > 0) score += 5;
        else score -= 5;
        details.northboundNet = nbNetInflow;
      }
    }
  }

  // --- Chip accumulation ---
  if (!chipData._failed) {
    const accumScore = num(
      get(chipData, "score") ?? get(chipData, "accumulation_score") ?? get(chipData, "total_score"),
    );
    if (accumScore > 0) {
      // Chip score is 0-100, map to adjustment
      score += (accumScore - 50) * 0.3;
      details.chipAccumulationScore = accumScore;
    }
    const isAccumulating = get(chipData, "is_accumulating") ?? get(chipData, "accumulating");
    if (isAccumulating) {
      score += 8;
      details.isAccumulating = true;
    }
    const confidence = get(chipData, "confidence");
    if (confidence) details.chipConfidence = confidence;
  }

  const summary = buildCapitalSummary(clamp(score), details);
  return { score: clamp(score), weight: 0.25, details, summary };
}

function buildCapitalSummary(score: number, d: Record<string, unknown>): string {
  const parts: string[] = [];
  if (d.mainNetInflow) parts.push(`主力净流入${d.mainNetInflow}`);
  if (d.isAccumulating) parts.push("检测到主力吸筹");
  if (d.chipAccumulationScore) parts.push(`吸筹评分${d.chipAccumulationScore}`);
  if (d.northboundNet) parts.push(`北向资金${num(d.northboundNet) > 0 ? "净流入" : "净流出"}`);
  return `资金面${score}分。${parts.join("，")}`;
}

// ── Score: Sentiment (消息面) ────────────────────────────────────

function scoreSentiment(sentimentData: Record<string, unknown>): DimensionScore {
  const details: Record<string, unknown> = {};
  let score = 50;

  if (!sentimentData._failed) {
    // Overall sentiment score
    const sentimentScore = num(
      get(sentimentData, "sentiment_score") ??
      get(sentimentData, "score") ??
      get(sentimentData, "composite_score"),
    );
    if (sentimentScore > 0) {
      // Normalize: assume 0-100 scale
      score += (sentimentScore - 50) * 0.4;
      details.sentimentScore = sentimentScore;
    }

    // Popularity / heat
    const heat = num(
      get(sentimentData, "popularity") ??
      get(sentimentData, "heat") ??
      get(sentimentData, "hot_rank"),
    );
    if (heat > 0) {
      details.popularity = heat;
      // High popularity can be contrarian signal — very high heat is risky
      if (heat > 90) score -= 5;
    }

    // Positive/negative ratio
    const positive = num(get(sentimentData, "positive") ?? get(sentimentData, "bullish"));
    const negative = num(get(sentimentData, "negative") ?? get(sentimentData, "bearish"));
    if (positive + negative > 0) {
      const ratio = positive / (positive + negative);
      score += (ratio - 0.5) * 20;
      details.bullishRatio = Math.round(ratio * 100) + "%";
    }

    // Attention change
    const attentionChange = num(
      get(sentimentData, "attention_change") ?? get(sentimentData, "heat_change"),
    );
    if (attentionChange) {
      details.attentionChange = attentionChange;
    }
  }

  const summary = buildSentimentSummary(clamp(score), details);
  return { score: clamp(score), weight: 0.20, details, summary };
}

function buildSentimentSummary(score: number, d: Record<string, unknown>): string {
  const parts: string[] = [];
  if (d.sentimentScore) parts.push(`情绪评分${d.sentimentScore}`);
  if (d.bullishRatio) parts.push(`看多比例${d.bullishRatio}`);
  if (d.popularity) parts.push(`热度${d.popularity}`);
  return `消息面${score}分。${parts.join("，") || "数据有限"}`;
}

// ── Recommendation ───────────────────────────────────────────────

function getRecommendation(totalScore: number): Recommendation {
  if (totalScore >= 80) return "强烈买入";
  if (totalScore >= 65) return "买入";
  if (totalScore >= 45) return "观望";
  if (totalScore >= 30) return "减持";
  return "卖出";
}

function getRecommendationReason(
  total: number,
  tech: DimensionScore,
  fund: DimensionScore,
  capital: DimensionScore,
  sentiment: DimensionScore,
): string {
  const strongest = [
    { name: "技术面", s: tech },
    { name: "基本面", s: fund },
    { name: "资金面", s: capital },
    { name: "消息面", s: sentiment },
  ].sort((a, b) => b.s.score - a.s.score);

  const bullish = strongest.filter((d) => d.s.score >= 60);
  const bearish = strongest.filter((d) => d.s.score < 40);

  const parts: string[] = [];
  if (bullish.length > 0) {
    parts.push(`利好: ${bullish.map((d) => `${d.name}(${d.s.score}分)`).join("、")}`);
  }
  if (bearish.length > 0) {
    parts.push(`利空: ${bearish.map((d) => `${d.name}(${d.s.score}分)`).join("、")}`);
  }
  if (parts.length === 0) {
    parts.push("各维度信号中性，建议继续观察");
  }
  return parts.join("；");
}

// ── Tool factory ─────────────────────────────────────────────────

export function createStockDiagnosisTool(
  _api: OpenClawPluginApi,
  _config: QuantTradingConfig,
  getTools: () => ToolMap,
) {
  return {
    name: "stock_diagnosis",
    description:
      "个股综合诊断工具。输入股票代码，一键获取全景诊断报告。\n" +
      "自动聚合行情、财务、主力资金、资金流向、舆情、板块等多维度数据，输出综合评分和买卖建议。\n" +
      "包含四大维度评分：\n" +
      "  1. 技术面（均线排列/MACD/RSI等形态）— 权重30%\n" +
      "  2. 基本面（PE/PB/ROE/营收增速/财务预警）— 权重25%\n" +
      "  3. 资金面（主力净流入/北向资金/主力吸筹检测）— 权重25%\n" +
      "  4. 消息面（舆情热度/社区情绪/看多比例）— 权重20%\n" +
      "最终给出综合得分(0-100)和操作建议（强烈买入/买入/观望/减持/卖出）。",
    parameters: {
      type: "object" as const,
      properties: {
        symbol: {
          type: "string",
          description: "股票代码，如 600519（茅台）、000858（五粮液）",
        },
        market: {
          type: "string",
          enum: ["cn_stock", "us_stock"],
          description: "市场类型，默认 cn_stock",
        },
      },
      required: ["symbol"],
    },
    async execute(args: Record<string, unknown>) {
      const symbol = args.symbol as string;
      if (!symbol) return { error: "请提供股票代码 (symbol)" };

      const market = (args.market as string) ?? "cn_stock";
      const tools = getTools();

      // ── Parallel data fetching ─────────────────────────────
      const [quoteResult, klineResult, fundamentalsResult, cfoResult, chipResult, fundFlowResult, sentimentResult] =
        await Promise.all([
          // 1. Real-time quote
          safeCall(tools.market_data, { action: "quote", symbol, market }),
          // 2. Klines with indicators (120 bars for MA120)
          safeCall(tools.market_data, {
            action: "klines",
            symbol,
            market,
            interval: "1d",
            limit: 120,
            indicators: true,
          }),
          // 3. Fundamentals
          safeCall(tools.market_data, { action: "fundamentals", symbol, market }),
          // 4. CFO red flags
          safeCall(tools.cfo_assistant, { action: "red_flags", symbol }),
          // 5. Chip accumulation
          safeCall(tools.chip_accumulation, { action: "detect", symbol }),
          // 6. Fund flow
          safeCall(tools.fund_flow, { action: "stock", symbol }),
          // 7. Social sentiment
          safeCall(tools.social_sentiment, { action: "stock_sentiment", symbol }),
        ]);

      // ── Score each dimension ───────────────────────────────
      const technical = scoreTechnical(klineResult, quoteResult);
      const fundamental = scoreFundamental(fundamentalsResult, cfoResult);
      const capitalFlow = scoreCapitalFlow(fundFlowResult, chipResult);
      const sentiment = scoreSentiment(sentimentResult);

      // ── Weighted total ─────────────────────────────────────
      const totalScore = Math.round(
        technical.score * technical.weight +
        fundamental.score * fundamental.weight +
        capitalFlow.score * capitalFlow.weight +
        sentiment.score * sentiment.weight,
      );

      const recommendation = getRecommendation(totalScore);
      const reason = getRecommendationReason(totalScore, technical, fundamental, capitalFlow, sentiment);

      // ── Basic stock info ───────────────────────────────────
      const stockName = (get(quoteResult, "name") ?? get(quoteResult, "stock_name") ?? symbol) as string;
      const currentPrice = num(get(quoteResult, "price") ?? get(quoteResult, "current") ?? get(quoteResult, "close"));
      const changePercent = num(
        get(quoteResult, "changePercent") ?? get(quoteResult, "change_percent") ?? get(quoteResult, "pct_chg"),
      );

      return {
        symbol,
        name: stockName,
        market,
        currentPrice: currentPrice || undefined,
        changePercent: changePercent || undefined,
        diagnosis: {
          totalScore,
          recommendation,
          reason,
          dimensions: {
            technical: {
              score: technical.score,
              weight: "30%",
              summary: technical.summary,
              details: technical.details,
            },
            fundamental: {
              score: fundamental.score,
              weight: "25%",
              summary: fundamental.summary,
              details: fundamental.details,
            },
            capitalFlow: {
              score: capitalFlow.score,
              weight: "25%",
              summary: capitalFlow.summary,
              details: capitalFlow.details,
            },
            sentiment: {
              score: sentiment.score,
              weight: "20%",
              summary: sentiment.summary,
              details: sentiment.details,
            },
          },
        },
        dataAvailability: {
          quote: !quoteResult._failed,
          klines: !klineResult._failed,
          fundamentals: !fundamentalsResult._failed,
          cfo: !cfoResult._failed,
          chipAccumulation: !chipResult._failed,
          fundFlow: !fundFlowResult._failed,
          sentiment: !sentimentResult._failed,
        },
        displayHint:
          `【${stockName} 综合诊断】综合评分 ${totalScore}/100 → ${recommendation}\n` +
          `${technical.summary}\n${fundamental.summary}\n${capitalFlow.summary}\n${sentiment.summary}\n` +
          `综合判断: ${reason}`,
      };
    },
  };
}
