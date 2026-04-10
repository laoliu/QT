/**
 * Stop-loss / take-profit advisor — computes recommended stop and target levels.
 *
 * Uses ATR-based and support/resistance methods to suggest risk parameters.
 */

export interface KlineBar {
  date?: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface StopAdviceLevel {
  stopLoss: string;
  takeProfit: string;
  stopLossPct: number;
  takeProfitPct: number;
  rewardRiskRatio: string;
  method: string;
  description: string;
}

export interface StopAdviceResult {
  symbol: string;
  recommended: StopAdviceLevel;
  alternatives: StopAdviceLevel[];
}

function computeATR(bars: KlineBar[], period = 14): number {
  if (bars.length < 2) return 0;
  let atr = 0;
  const start = Math.max(1, bars.length - period);
  let count = 0;
  for (let i = start; i < bars.length; i++) {
    const prev = bars[i - 1];
    const cur = bars[i];
    const tr = Math.max(
      cur.high - cur.low,
      Math.abs(cur.high - prev.close),
      Math.abs(cur.low - prev.close),
    );
    atr += tr;
    count++;
  }
  return count > 0 ? atr / count : 0;
}

function findRecentLow(bars: KlineBar[], lookback = 20): number {
  const slice = bars.slice(-lookback);
  return Math.min(...slice.map((b) => b.low));
}

export function computeStopAdvice(
  symbol: string,
  entryPrice: number,
  currentPrice: number,
  bars: KlineBar[],
  opts?: { style?: "short" | "mid" | "long" },
): StopAdviceResult {
  const style = opts?.style ?? "mid";
  const atr = computeATR(bars);
  const recentLow = findRecentLow(bars);

  // ATR multipliers by style
  const multipliers = { short: 1.5, mid: 2.0, long: 3.0 };
  const tpMultipliers = { short: 2.0, mid: 3.0, long: 5.0 };
  const mult = multipliers[style];
  const tpMult = tpMultipliers[style];

  // ATR-based stop
  const atrStop = currentPrice - atr * mult;
  const atrTarget = currentPrice + atr * tpMult;
  const atrStopPct = (currentPrice - atrStop) / currentPrice;
  const atrTargetPct = (atrTarget - currentPrice) / currentPrice;
  const atrRR = atrStopPct > 0 ? (atrTargetPct / atrStopPct).toFixed(1) : "∞";

  // Support-based stop
  const supportStop = Math.min(recentLow, currentPrice * 0.92);
  const supportStopPct = (currentPrice - supportStop) / currentPrice;
  const supportTarget = currentPrice + (currentPrice - supportStop) * 2;
  const supportTargetPct = (supportTarget - currentPrice) / currentPrice;
  const supportRR = supportStopPct > 0 ? (supportTargetPct / supportStopPct).toFixed(1) : "∞";

  const atrLevel: StopAdviceLevel = {
    stopLoss: atrStop.toFixed(2),
    takeProfit: atrTarget.toFixed(2),
    stopLossPct: atrStopPct,
    takeProfitPct: atrTargetPct,
    rewardRiskRatio: atrRR,
    method: "ATR",
    description: `基于${Math.round(mult)}倍ATR的${style === "short" ? "短线" : style === "mid" ? "中线" : "长线"}止损`,
  };

  const supportLevel: StopAdviceLevel = {
    stopLoss: supportStop.toFixed(2),
    takeProfit: supportTarget.toFixed(2),
    stopLossPct: supportStopPct,
    takeProfitPct: supportTargetPct,
    rewardRiskRatio: supportRR,
    method: "Support",
    description: "基于近期支撑位的止损建议",
  };

  return {
    symbol,
    recommended: atrLevel,
    alternatives: [supportLevel],
  };
}
