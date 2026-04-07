/**
 * StopAdvisor — 智能止盈止损建议引擎
 *
 * 根据个股波动率(ATR)、支撑阻力位、布林带、均线等多种方法，
 * 结合用户风险偏好（短线3-5%止损，盈亏比>2:1），自动计算最优止盈止损位。
 *
 * Methods:
 *   atr        — 基于 ATR 倍数（经典趋势跟踪）
 *   support    — 基于前低/前高支撑阻力位
 *   bollinger  — 基于布林带上下轨
 *   ma         — 基于均线支撑
 *   composite  — 综合多方法加权推荐
 */

import type { KlineBar } from "../types.js";

// ── Types ────────────────────────────────────────────────────────

export interface RiskPreference {
  /** 最大单票止损比例，默认 0.05 (5%) */
  maxStopLossPct: number;
  /** 最小盈亏比，默认 2.0 */
  minRewardRiskRatio: number;
  /** 交易风格: short=短线(1-10日), mid=中线(10-60日), long=长线(60日+) */
  style: "short" | "mid" | "long";
}

export interface StopLevel {
  /** 止损价 */
  stopLoss: number;
  /** 止盈价 */
  takeProfit: number;
  /** 止损幅度 (0-1) */
  stopLossPct: number;
  /** 止盈幅度 (0-1) */
  takeProfitPct: number;
  /** 盈亏比 */
  rewardRiskRatio: number;
  /** 计算方法 */
  method: string;
  /** 方法说明 */
  description: string;
  /** 置信度 (0-100) */
  confidence: number;
}

export interface StopAdvisorResult {
  symbol: string;
  entryPrice: number;
  currentPrice: number;
  methods: StopLevel[];
  recommended: StopLevel;
  riskPreference: RiskPreference;
  marketStats: {
    atr14: number;
    atrPct: number;
    volatility20: number;
    recentHigh: number;
    recentLow: number;
    ma5: number;
    ma10: number;
    ma20: number;
    ma60: number;
    bollingerUpper: number;
    bollingerLower: number;
    bollingerMid: number;
  };
}

// ── Default risk preferences per style (from USER.md) ────────────

const DEFAULT_RISK: Record<string, RiskPreference> = {
  short: { maxStopLossPct: 0.03, minRewardRiskRatio: 2.0, style: "short" },
  mid: { maxStopLossPct: 0.05, minRewardRiskRatio: 2.0, style: "mid" },
  long: { maxStopLossPct: 0.08, minRewardRiskRatio: 2.5, style: "long" },
};

// ── Indicator Calculations ───────────────────────────────────────

function calcSMA(closes: number[], period: number): number {
  if (closes.length < period) return closes[closes.length - 1] ?? 0;
  const slice = closes.slice(-period);
  return slice.reduce((s, v) => s + v, 0) / period;
}

function calcEMA(closes: number[], period: number): number {
  if (closes.length === 0) return 0;
  if (closes.length < period) return calcSMA(closes, closes.length);
  const k = 2 / (period + 1);
  let ema = calcSMA(closes.slice(0, period), period);
  for (let i = period; i < closes.length; i++) {
    ema = closes[i] * k + ema * (1 - k);
  }
  return ema;
}

function calcATR(bars: KlineBar[], period: number = 14): number {
  if (bars.length < 2) return 0;
  const trs: number[] = [];
  for (let i = 1; i < bars.length; i++) {
    const high = bars[i].high;
    const low = bars[i].low;
    const prevClose = bars[i - 1].close;
    const tr = Math.max(high - low, Math.abs(high - prevClose), Math.abs(low - prevClose));
    trs.push(tr);
  }
  if (trs.length < period) {
    return trs.reduce((s, v) => s + v, 0) / trs.length;
  }
  // Wilder's smoothing
  let atr = trs.slice(0, period).reduce((s, v) => s + v, 0) / period;
  for (let i = period; i < trs.length; i++) {
    atr = (atr * (period - 1) + trs[i]) / period;
  }
  return atr;
}

function calcBollinger(
  closes: number[],
  period: number = 20,
  stdMult: number = 2,
): { upper: number; mid: number; lower: number } {
  if (closes.length < period) {
    const mid = closes[closes.length - 1] ?? 0;
    return { upper: mid, mid, lower: mid };
  }
  const slice = closes.slice(-period);
  const mid = slice.reduce((s, v) => s + v, 0) / period;
  const variance = slice.reduce((s, v) => s + (v - mid) ** 2, 0) / period;
  const std = Math.sqrt(variance);
  return {
    upper: mid + stdMult * std,
    mid,
    lower: mid - stdMult * std,
  };
}

function calcVolatility(closes: number[], period: number = 20): number {
  if (closes.length < 2) return 0;
  const slice = closes.slice(-Math.min(period + 1, closes.length));
  const returns: number[] = [];
  for (let i = 1; i < slice.length; i++) {
    if (slice[i - 1] > 0) {
      returns.push(Math.log(slice[i] / slice[i - 1]));
    }
  }
  if (returns.length < 2) return 0;
  const mean = returns.reduce((s, v) => s + v, 0) / returns.length;
  const variance = returns.reduce((s, v) => s + (v - mean) ** 2, 0) / (returns.length - 1);
  return Math.sqrt(variance) * Math.sqrt(252); // 年化波动率
}

function findRecentHighLow(
  bars: KlineBar[],
  lookback: number,
): { high: number; low: number } {
  const recent = bars.slice(-lookback);
  let high = -Infinity;
  let low = Infinity;
  for (const b of recent) {
    if (b.high > high) high = b.high;
    if (b.low < low) low = b.low;
  }
  return { high, low };
}

/** 找近期支撑位：前N日低点中的次低点或显著低点 */
function findSupportLevels(bars: KlineBar[], lookback: number): number[] {
  const recent = bars.slice(-lookback);
  const lows = recent.map((b) => b.low).sort((a, b) => a - b);
  // 去重并取前3个支撑
  const unique: number[] = [];
  for (const v of lows) {
    if (unique.length === 0 || Math.abs(v - unique[unique.length - 1]) / v > 0.005) {
      unique.push(v);
    }
    if (unique.length >= 3) break;
  }
  return unique;
}

/** 找近期阻力位：前N日高点中的次高点或显著高点 */
function findResistanceLevels(bars: KlineBar[], lookback: number): number[] {
  const recent = bars.slice(-lookback);
  const highs = recent.map((b) => b.high).sort((a, b) => b - a);
  const unique: number[] = [];
  for (const v of highs) {
    if (unique.length === 0 || Math.abs(v - unique[unique.length - 1]) / v > 0.005) {
      unique.push(v);
    }
    if (unique.length >= 3) break;
  }
  return unique;
}

// ── Stop-loss / Take-profit Methods ──────────────────────────────

function atrMethod(
  entryPrice: number,
  bars: KlineBar[],
  risk: RiskPreference,
): StopLevel | null {
  const atr = calcATR(bars);
  if (atr <= 0) return null;

  // ATR 倍数根据风格调整
  const slMultiplier = risk.style === "short" ? 1.5 : risk.style === "mid" ? 2.0 : 2.5;
  const tpMultiplier = slMultiplier * risk.minRewardRiskRatio;

  let stopLoss = entryPrice - atr * slMultiplier;
  const takeProfit = entryPrice + atr * tpMultiplier;

  // 确保止损不超过最大止损比例
  const maxLoss = entryPrice * risk.maxStopLossPct;
  if (entryPrice - stopLoss > maxLoss) {
    stopLoss = entryPrice - maxLoss;
  }

  const stopLossPct = (entryPrice - stopLoss) / entryPrice;
  const takeProfitPct = (takeProfit - entryPrice) / entryPrice;
  const rewardRiskRatio = takeProfitPct / (stopLossPct || 0.01);

  return {
    stopLoss: round2(stopLoss),
    takeProfit: round2(takeProfit),
    stopLossPct: round4(stopLossPct),
    takeProfitPct: round4(takeProfitPct),
    rewardRiskRatio: round2(rewardRiskRatio),
    method: "atr",
    description: `ATR(14)=${round2(atr)}, 止损${slMultiplier}倍ATR, 止盈${tpMultiplier.toFixed(1)}倍ATR`,
    confidence: 75,
  };
}

function supportResistanceMethod(
  entryPrice: number,
  bars: KlineBar[],
  risk: RiskPreference,
): StopLevel | null {
  const lookback = risk.style === "short" ? 10 : risk.style === "mid" ? 30 : 60;
  const supports = findSupportLevels(bars, lookback);
  const resistances = findResistanceLevels(bars, lookback);

  if (supports.length === 0) return null;

  // 止损：最近的支撑位下方留一定缓冲
  const nearestSupport = supports.find((s) => s < entryPrice) ?? supports[0];
  const buffer = entryPrice * 0.005; // 0.5%缓冲
  let stopLoss = nearestSupport - buffer;

  // 止盈：最近的阻力位
  const nearestResistance = resistances.find((r) => r > entryPrice);
  let takeProfit: number;
  if (nearestResistance) {
    takeProfit = nearestResistance;
  } else {
    // 无明确阻力位，用盈亏比推算
    const riskAmt = entryPrice - stopLoss;
    takeProfit = entryPrice + riskAmt * risk.minRewardRiskRatio;
  }

  // 确保止损不超过最大止损比例
  const maxLoss = entryPrice * risk.maxStopLossPct;
  if (entryPrice - stopLoss > maxLoss) {
    stopLoss = entryPrice - maxLoss;
  }

  const stopLossPct = (entryPrice - stopLoss) / entryPrice;
  const takeProfitPct = (takeProfit - entryPrice) / entryPrice;
  const rewardRiskRatio = takeProfitPct / (stopLossPct || 0.01);

  return {
    stopLoss: round2(stopLoss),
    takeProfit: round2(takeProfit),
    stopLossPct: round4(stopLossPct),
    takeProfitPct: round4(takeProfitPct),
    rewardRiskRatio: round2(rewardRiskRatio),
    method: "support_resistance",
    description: `支撑位${round2(nearestSupport)}(${lookback}日), 阻力位${nearestResistance ? round2(nearestResistance) : "N/A"}`,
    confidence: 70,
  };
}

function bollingerMethod(
  entryPrice: number,
  bars: KlineBar[],
  risk: RiskPreference,
): StopLevel | null {
  const closes = bars.map((b) => b.close);
  const bb = calcBollinger(closes);

  if (bb.lower >= entryPrice || bb.upper <= entryPrice) return null;

  let stopLoss = bb.lower;
  const takeProfit = bb.upper;

  // 确保止损不超过最大止损比例
  const maxLoss = entryPrice * risk.maxStopLossPct;
  if (entryPrice - stopLoss > maxLoss) {
    stopLoss = entryPrice - maxLoss;
  }

  const stopLossPct = (entryPrice - stopLoss) / entryPrice;
  const takeProfitPct = (takeProfit - entryPrice) / entryPrice;
  const rewardRiskRatio = takeProfitPct / (stopLossPct || 0.01);

  return {
    stopLoss: round2(stopLoss),
    takeProfit: round2(takeProfit),
    stopLossPct: round4(stopLossPct),
    takeProfitPct: round4(takeProfitPct),
    rewardRiskRatio: round2(rewardRiskRatio),
    method: "bollinger",
    description: `布林带(20,2): 下轨${round2(bb.lower)}, 上轨${round2(bb.upper)}`,
    confidence: 65,
  };
}

function maMethod(
  entryPrice: number,
  bars: KlineBar[],
  risk: RiskPreference,
): StopLevel | null {
  const closes = bars.map((b) => b.close);

  // 根据交易风格选择参考均线
  const slPeriod = risk.style === "short" ? 10 : risk.style === "mid" ? 20 : 60;
  const tpPeriod = risk.style === "short" ? 5 : risk.style === "mid" ? 10 : 20;

  const slMa = calcEMA(closes, slPeriod);
  const tpMa = calcSMA(closes, tpPeriod);

  if (slMa <= 0) return null;

  // 止损：均线下方留缓冲
  const bufferPct = risk.style === "short" ? 0.01 : 0.015;
  let stopLoss = slMa * (1 - bufferPct);

  // 止盈：基于盈亏比推算
  const riskAmt = entryPrice - stopLoss;
  if (riskAmt <= 0) {
    // 入场价低于均线，不适用此方法
    return null;
  }
  const takeProfit = entryPrice + riskAmt * risk.minRewardRiskRatio;

  // 确保止损不超过最大止损比例
  const maxLoss = entryPrice * risk.maxStopLossPct;
  if (entryPrice - stopLoss > maxLoss) {
    stopLoss = entryPrice - maxLoss;
  }

  const stopLossPct = (entryPrice - stopLoss) / entryPrice;
  const takeProfitPct = (takeProfit - entryPrice) / entryPrice;
  const rewardRiskRatio = takeProfitPct / (stopLossPct || 0.01);

  return {
    stopLoss: round2(stopLoss),
    takeProfit: round2(takeProfit),
    stopLossPct: round4(stopLossPct),
    takeProfitPct: round4(takeProfitPct),
    rewardRiskRatio: round2(rewardRiskRatio),
    method: "ma",
    description: `EMA${slPeriod}=${round2(slMa)}做止损参考, 盈亏比${risk.minRewardRiskRatio}:1推算止盈`,
    confidence: 60,
  };
}

// ── Composite Recommendation ─────────────────────────────────────

function computeComposite(
  methods: StopLevel[],
  risk: RiskPreference,
): StopLevel {
  if (methods.length === 0) {
    throw new Error("No valid stop methods computed");
  }

  // 权重：confidence 越高权重越大
  const totalWeight = methods.reduce((s, m) => s + m.confidence, 0);

  let weightedSL = 0;
  let weightedTP = 0;
  for (const m of methods) {
    const w = m.confidence / totalWeight;
    weightedSL += m.stopLoss * w;
    weightedTP += m.takeProfit * w;
  }

  const bestMethod = methods.reduce((a, b) =>
    scoreMethod(b, risk) > scoreMethod(a, risk) ? b : a,
  );

  const stopLoss = round2(weightedSL);
  const takeProfit = round2(weightedTP);
  const entryPrice = methods[0].stopLoss / (1 - methods[0].stopLossPct);
  const stopLossPct = (entryPrice - stopLoss) / entryPrice;
  const takeProfitPct = (takeProfit - entryPrice) / entryPrice;
  const rewardRiskRatio = takeProfitPct / (stopLossPct || 0.01);

  const avgConfidence = Math.round(totalWeight / methods.length);

  return {
    stopLoss,
    takeProfit,
    stopLossPct: round4(stopLossPct),
    takeProfitPct: round4(takeProfitPct),
    rewardRiskRatio: round2(rewardRiskRatio),
    method: "composite",
    description:
      `综合${methods.length}种方法加权推荐（主要参考: ${bestMethod.method}）`,
    confidence: avgConfidence,
  };
}

function scoreMethod(m: StopLevel, risk: RiskPreference): number {
  let score = m.confidence;
  // 盈亏比达标加分
  if (m.rewardRiskRatio >= risk.minRewardRiskRatio) score += 20;
  // 止损在合理范围加分
  if (m.stopLossPct <= risk.maxStopLossPct) score += 10;
  // 盈亏比越高越好（但不过度追求）
  score += Math.min(m.rewardRiskRatio * 5, 15);
  return score;
}

// ── Public API ───────────────────────────────────────────────────

/**
 * 为指定股票计算智能止盈止损建议。
 *
 * @param symbol 股票代码
 * @param entryPrice 买入价 / 当前价
 * @param currentPrice 当前市价
 * @param bars 历史K线数据 (至少需要60根日K)
 * @param riskOverride 可选的风险偏好覆盖
 */
export function computeStopAdvice(
  symbol: string,
  entryPrice: number,
  currentPrice: number,
  bars: KlineBar[],
  riskOverride?: Partial<RiskPreference>,
): StopAdvisorResult {
  if (bars.length < 5) {
    throw new Error("K线数据不足，至少需要5根日K");
  }

  // 默认中短线风格
  const style = riskOverride?.style ?? "mid";
  const risk: RiskPreference = {
    ...DEFAULT_RISK[style],
    ...riskOverride,
    style,
  };

  const closes = bars.map((b) => b.close);
  const atr14 = calcATR(bars, 14);
  const volatility20 = calcVolatility(closes, 20);
  const bb = calcBollinger(closes, 20, 2);
  const lookback = style === "short" ? 10 : style === "mid" ? 30 : 60;
  const { high: recentHigh, low: recentLow } = findRecentHighLow(bars, lookback);

  const marketStats = {
    atr14: round2(atr14),
    atrPct: round4(atr14 / currentPrice),
    volatility20: round4(volatility20),
    recentHigh: round2(recentHigh),
    recentLow: round2(recentLow),
    ma5: round2(calcSMA(closes, 5)),
    ma10: round2(calcSMA(closes, 10)),
    ma20: round2(calcSMA(closes, 20)),
    ma60: round2(calcSMA(closes, 60)),
    bollingerUpper: round2(bb.upper),
    bollingerLower: round2(bb.lower),
    bollingerMid: round2(bb.mid),
  };

  // 计算各方法的止盈止损建议
  const methods: StopLevel[] = [];
  const atrResult = atrMethod(entryPrice, bars, risk);
  if (atrResult) methods.push(atrResult);
  const srResult = supportResistanceMethod(entryPrice, bars, risk);
  if (srResult) methods.push(srResult);
  const bbResult = bollingerMethod(entryPrice, bars, risk);
  if (bbResult) methods.push(bbResult);
  const maResult = maMethod(entryPrice, bars, risk);
  if (maResult) methods.push(maResult);

  // 综合推荐
  const recommended = methods.length > 1
    ? computeComposite(methods, risk)
    : methods[0];

  return {
    symbol,
    entryPrice: round2(entryPrice),
    currentPrice: round2(currentPrice),
    methods,
    recommended,
    riskPreference: risk,
    marketStats,
  };
}

/**
 * 批量计算多只股票的止盈止损建议（用于 portfolio diagnose）。
 */
export function computeBatchStopAdvice(
  positions: Array<{
    symbol: string;
    entryPrice: number;
    currentPrice: number;
    bars: KlineBar[];
  }>,
  riskOverride?: Partial<RiskPreference>,
): Array<StopAdvisorResult | { symbol: string; error: string }> {
  return positions.map((pos) => {
    try {
      return computeStopAdvice(
        pos.symbol,
        pos.entryPrice,
        pos.currentPrice,
        pos.bars,
        riskOverride,
      );
    } catch (err) {
      return {
        symbol: pos.symbol,
        error: (err as Error).message,
      };
    }
  });
}

// ── Helpers ──────────────────────────────────────────────────────

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

function round4(n: number): number {
  return Math.round(n * 10000) / 10000;
}
