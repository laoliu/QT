import {
  dualMaCrossover,
  macdConfirmation,
  macdDivergence,
  meanReversion,
  momentumBreakout,
  rsiEnhanced,
  rsiFilter,
  t0DayTrading,
  volumeConfirmation,
} from "./basic.js";
/**
 * Advanced builtin strategies — combinations of the basic strategies.
 *
 *   - Composite:             Multi-strategy voting (≥ minVotes to trade)
 *   - Gated:                 BB gate + trend confirmation
 *   - TrendCombo:            DualMA + Momentum (same-direction, no MR cross-cancel)
 *   - Scored:                Weighted consensus with proportional position sizing
 *   - EnhancedComposite:     5-strategy voting with market regime awareness
 *   - RsiMacdCombined:       Dual-signal RSI+MACD verification
 */
import type { BuiltinStrategy } from "./indicators.js";
import { atr, bollingerBands, getCloses, rollingHighLow, sma } from "./indicators.js";

// ── Composite (Multi-Strategy Voting) ────────────────────────────

/**
 * Runs DualMA + MomentumBreakout + MeanReversion in parallel.
 * Only trades when at least `minVotes` strategies agree.
 */
export function compositeStrategy(
  fastPeriod = 10,
  slowPeriod = 30,
  breakoutLookback = 20,
  bbPeriod = 20,
  bbStdDev = 2,
  minVotes = 2,
): BuiltinStrategy {
  const strategies = [
    dualMaCrossover(fastPeriod, slowPeriod),
    momentumBreakout(breakoutLookback),
    meanReversion(bbPeriod, bbStdDev),
  ];

  return {
    name: `Composite(DualMA+Breakout+BollingerMR,votes≥${minVotes})`,
    init(ctx) {
      for (const s of strategies) s.init?.(ctx);
    },
    next(ctx) {
      let buyVotes = 0;
      let sellVotes = 0;
      const buyReasons: string[] = [];
      const sellReasons: string[] = [];

      for (const s of strategies) {
        const result = s.next(ctx);
        const sigs = result ? (Array.isArray(result) ? result : [result]) : [];
        for (const sig of sigs) {
          if (sig.direction === "buy") {
            buyVotes++;
            if (sig.reason) buyReasons.push(sig.reason);
          } else if (sig.direction === "sell") {
            sellVotes++;
            if (sig.reason) sellReasons.push(sig.reason);
          }
        }
      }

      const bar = ctx.bars[ctx.index]!;

      if (buyVotes >= minVotes && ctx.position <= 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `[综合买入 ${buyVotes}/3] ${buyReasons.join(" | ")}`,
          strategyId: "composite",
        };
      }

      if (sellVotes >= minVotes && ctx.position > 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell",
          price: bar.close,
          reason: `[综合卖出 ${sellVotes}/3] ${sellReasons.join(" | ")}`,
          strategyId: "composite",
        };
      }

      return null;
    },
  };
}

// ── Gated (Condition Gate + Confirm) ─────────────────────────────

/**
 * Uses BB upper band as an overbought gate, then requires at least one
 * trend confirmation (golden cross or N-day breakout) to enter.
 * Exits on overbought (upper band touch) or death cross.
 */
export function gatedStrategy(
  fastPeriod = 10,
  slowPeriod = 30,
  bbPeriod = 20,
  bbStdDev = 2,
  breakoutLookback = 20,
): BuiltinStrategy {
  let fastMa: number[] = [];
  let slowMa: number[] = [];
  let bbUpper: number[] = [];
  let nDayHigh: number[] = [];

  return {
    name: `Gated(BB${bbPeriod}×${bbStdDev}|MA${fastPeriod}/${slowPeriod}|BK${breakoutLookback})`,
    init(ctx) {
      const closes = getCloses(ctx);
      fastMa = sma(closes, fastPeriod);
      slowMa = sma(closes, slowPeriod);

      const bb = bollingerBands(closes, bbPeriod, bbStdDev);
      bbUpper = bb.upper;

      const hl = rollingHighLow(ctx.bars, breakoutLookback);
      nDayHigh = hl.highs;
    },

    next(ctx) {
      const i = ctx.index;
      if (i < 1) return null;
      if (
        Number.isNaN(fastMa[i]!) ||
        Number.isNaN(slowMa[i]!) ||
        Number.isNaN(bbUpper[i]!) ||
        Number.isNaN(nDayHigh[i]!)
      )
        return null;

      const bar = ctx.bars[i]!;

      // Gate: not yet overbought
      const notOverbought = bar.close < bbUpper[i]!;

      // Trend confirmations
      const goldenCross = fastMa[i]! > slowMa[i]! && fastMa[i - 1]! <= slowMa[i - 1]!;
      const momentumBreak = bar.close > nDayHigh[i]!;
      const deathCross = fastMa[i]! < slowMa[i]! && fastMa[i - 1]! >= slowMa[i - 1]!;

      // BUY: price not overbought AND trend confirms
      if (ctx.position <= 0 && notOverbought && (goldenCross || momentumBreak)) {
        const reasons: string[] = [];
        if (goldenCross) reasons.push(`金叉(MA${fastPeriod}上穿MA${slowPeriod})`);
        if (momentumBreak) reasons.push(`突破${breakoutLookback}日高点${nDayHigh[i]!.toFixed(2)}`);
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `[门控买入] 价格${bar.close.toFixed(2)}<上轨${bbUpper[i]!.toFixed(2)} 且 ${reasons.join(" + ")}`,
          strategyId: "gated",
        };
      }

      // SELL: overbought OR death cross
      const overbought = bar.close > bbUpper[i]!;
      if (ctx.position > 0 && (overbought || deathCross)) {
        const sellReasons: string[] = [];
        if (overbought) sellReasons.push(`触及上轨${bbUpper[i]!.toFixed(2)}超买`);
        if (deathCross) sellReasons.push(`死叉止损(MA${fastPeriod}下穿MA${slowPeriod})`);
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell",
          price: bar.close,
          reason: `[门控卖出] ${sellReasons.join(" + ")}`,
          strategyId: "gated",
        };
      }

      return null;
    },
  };
}

// ── Trend Combo (same-direction pool only) ───────────────────────

/**
 * Combines DualMA and Momentum Breakout (both trend-following).
 * Mean-reversion excluded to avoid cross-cancellation.
 */
export function trendComboStrategy(
  fastPeriod = 10,
  slowPeriod = 30,
  breakoutLookback = 20,
): BuiltinStrategy {
  let fastMa: number[] = [];
  let slowMa: number[] = [];
  let nDayHigh: number[] = [];
  let nDayLow: number[] = [];

  return {
    name: `TrendCombo(MA${fastPeriod}/${slowPeriod}|BK${breakoutLookback})`,
    init(ctx) {
      const closes = getCloses(ctx);
      fastMa = sma(closes, fastPeriod);
      slowMa = sma(closes, slowPeriod);
      const hl = rollingHighLow(ctx.bars, breakoutLookback);
      nDayHigh = hl.highs;
      nDayLow = hl.lows;
    },
    next(ctx) {
      const i = ctx.index;
      if (
        i < 1 ||
        Number.isNaN(fastMa[i]!) ||
        Number.isNaN(slowMa[i]!) ||
        Number.isNaN(nDayHigh[i]!) ||
        Number.isNaN(nDayLow[i]!)
      )
        return null;

      const bar = ctx.bars[i]!;
      const goldenCross = fastMa[i]! > slowMa[i]! && fastMa[i - 1]! <= slowMa[i - 1]!;
      const momentumBreak = bar.close > nDayHigh[i]!;
      const deathCross = fastMa[i]! < slowMa[i]! && fastMa[i - 1]! >= slowMa[i - 1]!;
      const momentumBreakdown = bar.close < nDayLow[i]!;

      if (ctx.position <= 0 && (goldenCross || momentumBreak)) {
        const reasons: string[] = [];
        if (goldenCross) reasons.push(`金叉(MA${fastPeriod}↑MA${slowPeriod})`);
        if (momentumBreak) reasons.push(`突破${breakoutLookback}日高${nDayHigh[i]!.toFixed(2)}`);
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `[趋势合并买入] ${reasons.join(" | ")}`,
          strategyId: "trend-combo",
        };
      }

      if (ctx.position > 0 && (deathCross || momentumBreakdown)) {
        const reasons: string[] = [];
        if (deathCross) reasons.push(`死叉(MA${fastPeriod}↓MA${slowPeriod})`);
        if (momentumBreakdown) reasons.push(`跌破${breakoutLookback}日低${nDayLow[i]!.toFixed(2)}`);
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell",
          price: bar.close,
          reason: `[趋势合并卖出] ${reasons.join(" | ")}`,
          strategyId: "trend-combo",
        };
      }

      return null;
    },
  };
}

// ── Scored (Weighted Position Sizing) ────────────────────────────

/**
 * Weighted consensus across sub-strategies.
 * Trend signals (DualMA, Momentum) carry 2pts each;
 * mean-reversion (Bollinger) carries 1pt (max total = 5).
 * Position size proportional to score. Sell on any sell signal.
 */
export function scoredStrategy(
  fastPeriod = 10,
  slowPeriod = 30,
  bbPeriod = 20,
  bbStdDev = 2,
  breakoutLookback = 20,
): BuiltinStrategy {
  const sub: Array<{ s: BuiltinStrategy; weight: number }> = [
    { s: dualMaCrossover(fastPeriod, slowPeriod), weight: 2 },
    { s: momentumBreakout(breakoutLookback), weight: 2 },
    { s: meanReversion(bbPeriod, bbStdDev), weight: 1 },
  ];
  let entryScore = 0;

  return {
    name: `Scored(DualMA×2+Breakout×2+BollingerMR×1,max=5)`,
    init(ctx) {
      entryScore = 0;
      for (const { s } of sub) s.init?.(ctx);
    },
    next(ctx) {
      let buyScore = 0;
      let sellScore = 0;
      const buyReasons: string[] = [];
      const sellReasons: string[] = [];

      for (const { s, weight } of sub) {
        const result = s.next(ctx);
        const sigs = result ? (Array.isArray(result) ? result : [result]) : [];
        for (const sig of sigs) {
          if (sig.direction === "buy") {
            buyScore += weight;
            if (sig.reason) buyReasons.push(sig.reason);
          }
          if (sig.direction === "sell") {
            sellScore += weight;
            if (sig.reason) sellReasons.push(sig.reason);
          }
        }
      }

      const bar = ctx.bars[ctx.index]!;

      if (buyScore > 0 && ctx.position <= 0) {
        entryScore = buyScore;
        const sizePct = Math.round(Math.min(95, (buyScore / 5) * 95));
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          sizePct,

          reason: `[加权买入 score=${buyScore}/5 → ${sizePct}%仓] ${buyReasons.join(" | ")}`,
          strategyId: "scored",
        };
      }

      if (sellScore > 0 && entryScore > 0 && ctx.position > 0) {
        entryScore = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell",
          price: bar.close,
          reason: `[加权卖出 score=${sellScore}] ${sellReasons.join(" | ")}`,
          strategyId: "scored",
        };
      }

      return null;
    },
  };
}

// ── Market Regime Detection ──────────────────────────────────────

export type MarketRegime = "trending-up" | "trending-down" | "ranging";

/**
 * Detects market regime by combining:
 *   1. Long MA slope (trending vs ranging)
 *   2. ATR / price ratio (volatility level)
 *   3. Price position relative to long MA
 *
 * Returns regime classification per bar.
 */
export function detectMarketRegime(
  bars: import("../../types.js").KlineBar[],
  longMaPeriod = 60,
  atrPeriod = 14,
  slopeWindow = 10,
  trendSlopeThreshold = 0.001,
): MarketRegime[] {
  const closes = bars.map((b) => b.close);
  const longMa = sma(closes, longMaPeriod);
  const atrValues = atr(bars, atrPeriod);
  const regimes: MarketRegime[] = [];

  for (let i = 0; i < bars.length; i++) {
    if (i < longMaPeriod + slopeWindow || Number.isNaN(longMa[i]!) || Number.isNaN(atrValues[i]!)) {
      regimes.push("ranging");
      continue;
    }

    // MA slope: how fast the long MA is rising or falling (normalized by price level)
    const maSlope = (longMa[i]! - longMa[i - slopeWindow]!) / (longMa[i - slopeWindow]! || 1);
    const normalizedSlope = maSlope / slopeWindow;

    if (normalizedSlope > trendSlopeThreshold) {
      regimes.push("trending-up");
    } else if (normalizedSlope < -trendSlopeThreshold) {
      regimes.push("trending-down");
    } else {
      regimes.push("ranging");
    }
  }

  return regimes;
}

// ── Enhanced Composite (5-Strategy + Market Regime) ──────────────

/**
 * Enhanced composite strategy with 5 sub-strategies and market regime awareness.
 *
 * Sub-strategies (total weight = 9):
 *   1. DualMA crossover  (weight 2, trend)
 *   2. Momentum breakout  (weight 2, trend)
 *   3. MACD confirmation  (weight 2, momentum)
 *   4. RSI filter         (weight 2, momentum)
 *   5. Volume confirm     (weight 1, volume validation)
 *
 * Improvements over basic composite:
 *   - More sub-strategy diversity (trend + momentum + volume)
 *   - Market regime filter: only take trend signals in trending markets,
 *     only take mean-reversion signals in ranging markets
 *   - Position sizing proportional to consensus score
 *   - Requires higher minimum votes for entry (configurable, default 3)
 */
export function enhancedCompositeStrategy(
  fastPeriod = 10,
  slowPeriod = 30,
  breakoutLookback = 20,
  rsiPeriod = 14,
  rsiOversold = 30,
  rsiOverbought = 70,
  macdFast = 12,
  macdSlow = 26,
  macdSignal = 9,
  volumeMaPeriod = 20,
  volumeMultiplier = 1.5,
  minVotes = 3,
  useRegimeFilter = true,
  longMaPeriod = 60,
): BuiltinStrategy {
  const sub: Array<{
    s: BuiltinStrategy;
    weight: number;
    /** "trend" strategies favored in trending regime; "any" always counted */
    regimeAffinity: "trend" | "any";
  }> = [
    { s: dualMaCrossover(fastPeriod, slowPeriod), weight: 2, regimeAffinity: "trend" },
    { s: momentumBreakout(breakoutLookback), weight: 2, regimeAffinity: "trend" },
    { s: macdConfirmation(macdFast, macdSlow, macdSignal), weight: 2, regimeAffinity: "any" },
    { s: rsiFilter(rsiPeriod, rsiOversold, rsiOverbought), weight: 2, regimeAffinity: "any" },
    { s: volumeConfirmation(volumeMaPeriod, volumeMultiplier), weight: 1, regimeAffinity: "any" },
  ];
  const maxWeight = sub.reduce((sum, s) => sum + s.weight, 0);
  let regimes: MarketRegime[] = [];

  return {
    name: `EnhancedComposite(5策略,votes≥${minVotes},regime=${useRegimeFilter ? "on" : "off"})`,
    init(ctx) {
      for (const { s } of sub) s.init?.(ctx);
      if (useRegimeFilter) {
        regimes = detectMarketRegime(ctx.bars, longMaPeriod);
      }
    },
    next(ctx) {
      const i = ctx.index;
      const regime = regimes[i] ?? "ranging";

      let buyScore = 0;
      let sellScore = 0;
      let buyVotes = 0;
      let sellVotes = 0;
      const buyReasons: string[] = [];
      const sellReasons: string[] = [];

      for (const { s, weight, regimeAffinity } of sub) {
        const result = s.next(ctx);
        const sigs = result ? (Array.isArray(result) ? result : [result]) : [];

        // In ranging market, trend-affinity signals get reduced weight
        let effectiveWeight = weight;
        if (useRegimeFilter && regimeAffinity === "trend" && regime === "ranging") {
          effectiveWeight = Math.max(1, Math.floor(weight * 0.5));
        }

        for (const sig of sigs) {
          if (sig.direction === "buy") {
            buyScore += effectiveWeight;
            buyVotes++;
            if (sig.reason) buyReasons.push(sig.reason);
          } else if (sig.direction === "sell") {
            sellScore += effectiveWeight;
            sellVotes++;
            if (sig.reason) sellReasons.push(sig.reason);
          }
        }
      }

      const bar = ctx.bars[i]!;
      const regimeTag = useRegimeFilter ? ` [${regime}]` : "";

      // In a downtrend, require even stronger consensus to buy
      const effectiveMinVotes =
        useRegimeFilter && regime === "trending-down"
          ? Math.min(minVotes + 1, sub.length)
          : minVotes;

      if (buyVotes >= effectiveMinVotes && ctx.position <= 0) {
        const sizePct = Math.round(Math.min(95, (buyScore / maxWeight) * 95));
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          sizePct,
          reason:
            `[增强综合买入 ${buyVotes}/${sub.length} score=${buyScore}/${maxWeight} → ${sizePct}%仓]${regimeTag} ` +
            buyReasons.join(" | "),
          strategyId: "enhanced-composite",
        };
      }

      // For sells, lower threshold — protect capital faster
      const sellMinVotes = Math.max(2, effectiveMinVotes - 1);
      if (sellVotes >= sellMinVotes && ctx.position > 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell",
          price: bar.close,
          reason:
            `[增强综合卖出 ${sellVotes}/${sub.length} score=${sellScore}/${maxWeight}]${regimeTag} ` +
            sellReasons.join(" | "),
          strategyId: "enhanced-composite",
        };
      }

      return null;
    },
  };
}

// ── RSI + MACD Combined (Dual-Signal Verification) ─────────────

/**
 * Combines RSI Enhanced (trend + volume filtered) with MACD Divergence
 * for double-confirmation stock selection.
 *
 * Buy requires BOTH:
 *   - RSI Enhanced buy signal (oversold + uptrend + volume)
 *   - AND MACD Divergence bottom signal
 *   - Both signals must fire within `confirmWindow` bars of each other
 *
 * This dramatically reduces false signals by requiring two independent
 * technical systems to agree within a short time window.
 *
 * Sell requires EITHER:
 *   - RSI overbought exit (take profit)
 *   - OR MACD top divergence (momentum exhaustion)
 *   - OR ATR trailing stop (capital protection from RSI Enhanced)
 *
 * Asymmetric design: strict entry, loose exit (protect capital).
 */
export function rsiMacdCombined(
  rsiPeriod = 14,
  oversold = 35,
  overbought = 65,
  trendMaPeriod = 20,
  volMaPeriod = 5,
  volMultiplier = 1.0,
  atrPeriod = 14,
  atrStopMultiplier = 2.0,
  macdFast = 12,
  macdSlow = 26,
  macdSignal = 9,
  divergenceLookback = 60,
  confirmWindow = 10,
): BuiltinStrategy {
  const rsiStrat = rsiEnhanced(
    rsiPeriod,
    oversold,
    overbought,
    trendMaPeriod,
    volMaPeriod,
    volMultiplier,
    atrPeriod,
    atrStopMultiplier,
  );
  const macdStrat = macdDivergence(macdFast, macdSlow, macdSignal, divergenceLookback);

  // Track recent signals from each sub-strategy
  let lastRsiBuyBar = -Infinity;
  let lastMacdBuyBar = -Infinity;
  let lastRsiBuyReason = "";
  let lastMacdBuyReason = "";
  let entered = false;

  return {
    name: `RSI+MACD(RSI${rsiPeriod},MACD${macdFast}/${macdSlow},window=${confirmWindow})`,
    init(ctx) {
      rsiStrat.init?.(ctx);
      macdStrat.init?.(ctx);
      lastRsiBuyBar = -Infinity;
      lastMacdBuyBar = -Infinity;
      lastRsiBuyReason = "";
      lastMacdBuyReason = "";
      entered = false;
    },
    next(ctx) {
      const i = ctx.index;
      const bar = ctx.bars[i]!;

      // Collect sub-strategy signals and normalize to handle arrays
      const rsiResult = rsiStrat.next(ctx);
      const macdResult = macdStrat.next(ctx);

      const rsiSigs = rsiResult ? (Array.isArray(rsiResult) ? rsiResult : [rsiResult]) : [];
      const macdSigs = macdResult ? (Array.isArray(macdResult) ? macdResult : [macdResult]) : [];

      const rsiSig = rsiSigs[0];
      const macdSig = macdSigs[0];

      // Record buy signals with their bar index
      if (rsiSig && rsiSig.direction === "buy") {
        lastRsiBuyBar = i;
        lastRsiBuyReason = rsiSig.reason || "";
      }
      if (macdSig && macdSig.direction === "buy") {
        lastMacdBuyBar = i;
        lastMacdBuyReason = macdSig.reason || "";
      }

      // ── Buy: both signals within confirmation window ──
      if (ctx.position <= 0) {
        const rsiFresh = i - lastRsiBuyBar <= confirmWindow;
        const macdFresh = i - lastMacdBuyBar <= confirmWindow;

        if (rsiFresh && macdFresh) {
          // Reset to prevent re-triggering on same signal pair
          const rReason = lastRsiBuyReason;
          const mReason = lastMacdBuyReason;
          lastRsiBuyBar = -Infinity;
          lastMacdBuyBar = -Infinity;
          entered = true;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy" as const,
            price: bar.close,
            reason: `[RSI+MACD双确认] ${rReason} | ${mReason}`,
            strategyId: "rsi-macd-combined",
          };
        }
      }

      // ── Sell: either signal fires (asymmetric — protect capital) ──
      if (ctx.position > 0 && entered) {
        if (rsiSig && rsiSig.direction === "sell") {
          entered = false;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "sell" as const,
            price: bar.close,
            reason: `[RSI+MACD卖出] RSI: ${rsiSig.reason}`,
            strategyId: "rsi-macd-combined",
          };
        }
        if (macdSig && macdSig.direction === "sell") {
          entered = false;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "sell" as const,
            price: bar.close,
            reason: `[RSI+MACD卖出] MACD: ${macdSig.reason}`,
            strategyId: "rsi-macd-combined",
          };
        }
      }

      return null;
    },
  };
}

// ── T+0 Enhanced (Intraday composite with multiple fast signals) ──

/**
 * T+0 Enhanced Strategy: Multi-signal intraday trading for maximum frequency and flexibility.
 *
 * Combines:
 *   1. T+0 Day Trading base (fast MAs + RSI)
 *   2. RSI Filter for momentum confirmation
 *   3. Volume Confirmation for institutional participation
 *
 * Uses optimized parameters for 5-15 minute K-lines:
 *   - Faster RSI period (9 vs 14) for quicker oscillation
 *   - Aggressive profit targets (2-3%) and tight stops (1%)
 *   - Voting gate: requires ≥2 signals for entry (reduce false positives)
 *   - Asymmetric exit: any single sell signal triggers exit (capital protection)
 */
export function t0EnhancedStrategy(
  fastMaPeriod = 3,
  mediumMaPeriod = 7,
  rsiPeriod = 9,
  rsiOversold = 35,
  rsiOverbought = 65,
  volumeMaPeriod = 10,
  volumeMultiplier = 1.2,
  profitTargetPct = 2.5,
  stopLossPct = 1.0,
  minVotes = 2,
): BuiltinStrategy {
  const t0Strategy = t0DayTrading(
    fastMaPeriod,
    mediumMaPeriod,
    rsiPeriod,
    rsiOversold,
    rsiOverbought,
    volumeMaPeriod,
    volumeMultiplier,
    profitTargetPct,
    stopLossPct,
  );
  const rsiStrat = rsiFilter(rsiPeriod, rsiOversold, rsiOverbought);
  const volStrat = volumeConfirmation(volumeMaPeriod, volumeMultiplier);

  let entered = false;

  return {
    name: `T0Enhanced(T+0+RSI+Vol,votes≥${minVotes},T${profitTargetPct}%,SL${stopLossPct}%)`,
    init(ctx) {
      t0Strategy.init?.(ctx);
      rsiStrat.init?.(ctx);
      volStrat.init?.(ctx);
    },
    next(ctx) {
      if (ctx.index < 2) return null;

      const bar = ctx.bars[ctx.index]!;
      const t0Result = t0Strategy.next(ctx);
      const rsiResult = rsiStrat.next(ctx);
      const volResult = volStrat.next(ctx);

      // Normalize results to handle both single signals and arrays
      const t0Sigs = t0Result ? (Array.isArray(t0Result) ? t0Result : [t0Result]) : [];
      const rsiSigs = rsiResult ? (Array.isArray(rsiResult) ? rsiResult : [rsiResult]) : [];
      const volSigs = volResult ? (Array.isArray(volResult) ? volResult : [volResult]) : [];

      // Get first signal from each (if any)
      const t0Sig = t0Sigs[0];
      const rsiSig = rsiSigs[0];
      const volSig = volSigs[0];

      // ── Buy: voting gate (need ≥2 buy signals) ──
      if (!entered && ctx.position <= 0) {
        let buyVotes = 0;
        const buyReasons: string[] = [];

        if (t0Sig?.direction === "buy") {
          buyVotes++;
          if (t0Sig.reason) buyReasons.push(`[T+0] ${t0Sig.reason}`);
        }
        if (rsiSig?.direction === "buy") {
          buyVotes++;
          if (rsiSig.reason) buyReasons.push(`[RSI] ${rsiSig.reason}`);
        }
        if (volSig?.direction === "buy") {
          buyVotes++;
          if (volSig.reason) buyReasons.push(`[Vol] ${volSig.reason}`);
        }

        if (buyVotes >= minVotes) {
          entered = true;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy",
            price: bar.close,
            reason: `[T+0增强买入 ${buyVotes}/3] ${buyReasons.join(" | ")}`,
            strategyId: "t0-enhanced",
          };
        }
      }

      // ── Sell: asymmetric gate (any single sell signal triggers exit) ──
      if (entered && ctx.position > 0) {
        if (t0Sig?.direction === "sell") {
          entered = false;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "sell",
            price: bar.close,
            reason: `[T+0增强卖出] ${t0Sig.reason}`,
            strategyId: "t0-enhanced",
          };
        }
        if (rsiSig?.direction === "sell") {
          entered = false;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "sell",
            price: bar.close,
            reason: `[T+0增强卖出] ${rsiSig.reason}`,
            strategyId: "t0-enhanced",
          };
        }
      }

      return null;
    },
  };
}
