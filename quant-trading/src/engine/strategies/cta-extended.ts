/**
 * Extended CTA strategies — additional futures / CTA strategies.
 *
 *   - ctaAwesomeOscillator:    AO saucer / zero cross
 *   - ctaElderForceIndex:      Elder Force Index trend
 *   - ctaDemarkerCta:          DeMarker oscillator CTA
 *   - ctaUltimateOscillator:   Multi-period buying pressure
 *   - ctaAdxBreakout:          ADX + DI directional system
 *   - ctaTrixTrend:            TRIX-based trend following
 *   - ctaHullMaTrend:          Hull MA CTA trend
 *   - ctaMassIndex:            Mass Index reversal
 *   - ctaChaikinOscillator:    Chaikin Oscillator divergence
 *   - ctaCoppock:              Coppock Curve long-term
 *   - ctaRsiDivergence:        RSI + price divergence
 *   - ctaVolumeBreakout:       Volume spike + breakout
 *   - ctaAdaptiveMa:           Kaufman Adaptive MA trend
 *   - ctaMaEnvelope:           Moving average envelope CTA
 *   - ctaConnorsRsi:           Connors RSI CTA
 *   - ctaAroonOscillator:      Aroon-based CTA
 *   - ctaMfiTrend:             MFI as CTA oscillator
 *   - ctaDemaCrossover:        DEMA crossover CTA
 *   - ctaTripleMa:             Triple MA CTA alignment
 *   - ctaSupertrendCta:        Supertrend CTA
 */
import type { BuiltinStrategy } from "./indicators.js";
import {
  aroon,
  atr,
  cmf,
  dema,
  demarker,
  ema,
  getCloses,
  hullMa,
  kama,
  mfi,
  roc,
  rsi,
  sma,
  trix,
  ultimateOscillator,
  wma,
} from "./indicators.js";

// ── Awesome Oscillator CTA ───────────────────────────────────────

export function ctaAwesomeOscillator(fastPeriod = 5, slowPeriod = 34): BuiltinStrategy {
  let ao: number[] = [];

  return {
    name: `AO(${fastPeriod},${slowPeriod})`,
    init(ctx) {
      const bars = ctx.bars;
      const n = bars.length;
      const midPrice = bars.map((b) => (b.high + b.low) / 2);
      const fastSma = sma(midPrice, fastPeriod);
      const slowSma = sma(midPrice, slowPeriod);
      ao = new Array<number>(n).fill(NaN);
      for (let j = 0; j < n; j++) {
        if (!Number.isNaN(fastSma[j]!) && !Number.isNaN(slowSma[j]!))
          ao[j] = fastSma[j]! - slowSma[j]!;
      }
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1 || Number.isNaN(ao[i]!) || Number.isNaN(ao[i - 1]!)) return null;
      const bar = ctx.bars[i]!;
      // Zero cross up
      if (ao[i]! > 0 && ao[i - 1]! <= 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `[AO] 零轴上穿 AO=${ao[i]!.toFixed(2)}`,
          strategyId: "cta-awesome-oscillator",
        };
      }
      if (ao[i]! < 0 && ao[i - 1]! >= 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: ctx.position > 0 ? "sell" : "short",
          price: bar.close,
          reason: `[AO] 零轴下穿 AO=${ao[i]!.toFixed(2)}`,
          strategyId: "cta-awesome-oscillator",
        };
      }
      return null;
    },
  };
}

// ── Elder Force Index CTA ────────────────────────────────────────

export function ctaElderForceIndex(emaPeriod = 13): BuiltinStrategy {
  let forceEma: number[] = [];

  return {
    name: `ElderForce(${emaPeriod})`,
    init(ctx) {
      const bars = ctx.bars;
      const n = bars.length;
      const force = new Array<number>(n).fill(NaN);
      for (let j = 1; j < n; j++) {
        force[j] = (bars[j]!.close - bars[j - 1]!.close) * bars[j]!.volume;
      }
      forceEma = ema(force, emaPeriod);
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1 || Number.isNaN(forceEma[i]!) || Number.isNaN(forceEma[i - 1]!)) return null;
      const bar = ctx.bars[i]!;
      if (forceEma[i]! > 0 && forceEma[i - 1]! <= 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `[ElderForce] 力量指数转正=${forceEma[i]!.toFixed(0)}`,
          strategyId: "cta-elder-force",
        };
      }
      if (forceEma[i]! < 0 && forceEma[i - 1]! >= 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: ctx.position > 0 ? "sell" : "short",
          price: bar.close,
          reason: `[ElderForce] 力量指数转负=${forceEma[i]!.toFixed(0)}`,
          strategyId: "cta-elder-force",
        };
      }
      return null;
    },
  };
}

// ── DeMarker CTA ─────────────────────────────────────────────────

export function ctaDemarkerCta(period = 14, oversold = 0.3, overbought = 0.7): BuiltinStrategy {
  let dmValues: number[] = [];

  return {
    name: `DeMarkerCTA(${period})`,
    init(ctx) {
      dmValues = demarker(ctx.bars, period);
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1 || Number.isNaN(dmValues[i]!)) return null;
      const bar = ctx.bars[i]!;
      if (dmValues[i]! > oversold && dmValues[i - 1]! <= oversold) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `[DeMarker CTA] 超卖回升 DM=${dmValues[i]!.toFixed(3)}`,
          strategyId: "cta-demarker",
        };
      }
      if (dmValues[i]! < overbought && dmValues[i - 1]! >= overbought) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: ctx.position > 0 ? "sell" : "short",
          price: bar.close,
          reason: `[DeMarker CTA] 超买回落 DM=${dmValues[i]!.toFixed(3)}`,
          strategyId: "cta-demarker",
        };
      }
      return null;
    },
  };
}

// ── Ultimate Oscillator CTA ──────────────────────────────────────

export function ctaUltimateOscillatorStrategy(
  p1 = 7,
  p2 = 14,
  p3 = 28,
  oversold = 30,
  overbought = 70,
): BuiltinStrategy {
  let uoValues: number[] = [];

  return {
    name: `UltOsc(${p1}/${p2}/${p3})`,
    init(ctx) {
      uoValues = ultimateOscillator(ctx.bars, p1, p2, p3);
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1 || Number.isNaN(uoValues[i]!)) return null;
      const bar = ctx.bars[i]!;
      if (uoValues[i]! > oversold && uoValues[i - 1]! <= oversold) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `[UltOsc] 超卖回升 UO=${uoValues[i]!.toFixed(1)}`,
          strategyId: "cta-ultimate-oscillator",
        };
      }
      if (uoValues[i]! < overbought && uoValues[i - 1]! >= overbought) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: ctx.position > 0 ? "sell" : "short",
          price: bar.close,
          reason: `[UltOsc] 超买回落 UO=${uoValues[i]!.toFixed(1)}`,
          strategyId: "cta-ultimate-oscillator",
        };
      }
      return null;
    },
  };
}

// ── ADX Breakout CTA ─────────────────────────────────────────────

export function ctaAdxBreakout(
  period = 14,
  adxThreshold = 25,
  diThreshold = 20,
): BuiltinStrategy {
  let plusDI: number[] = [];
  let minusDI: number[] = [];
  let adxValues: number[] = [];

  return {
    name: `ADX突破(${period},ADX>${adxThreshold})`,
    init(ctx) {
      const bars = ctx.bars;
      const n = bars.length;
      // Compute DI+, DI-, ADX
      const trueRange = new Array<number>(n).fill(0);
      const dmPlus = new Array<number>(n).fill(0);
      const dmMinus = new Array<number>(n).fill(0);
      for (let j = 1; j < n; j++) {
        const hi = bars[j]!.high;
        const lo = bars[j]!.low;
        const pc = bars[j - 1]!.close;
        trueRange[j] = Math.max(hi - lo, Math.abs(hi - pc), Math.abs(lo - pc));
        const upMove = hi - bars[j - 1]!.high;
        const downMove = bars[j - 1]!.low - lo;
        dmPlus[j] = upMove > downMove && upMove > 0 ? upMove : 0;
        dmMinus[j] = downMove > upMove && downMove > 0 ? downMove : 0;
      }
      const smaTr = sma(trueRange, period);
      const smaPlus = sma(dmPlus, period);
      const smaMinus = sma(dmMinus, period);
      plusDI = new Array<number>(n).fill(NaN);
      minusDI = new Array<number>(n).fill(NaN);
      const dx = new Array<number>(n).fill(NaN);
      for (let j = 0; j < n; j++) {
        if (!Number.isNaN(smaTr[j]!) && smaTr[j]! > 0) {
          plusDI[j] = (smaPlus[j]! / smaTr[j]!) * 100;
          minusDI[j] = (smaMinus[j]! / smaTr[j]!) * 100;
          const diSum = plusDI[j]! + minusDI[j]!;
          dx[j] = diSum === 0 ? 0 : (Math.abs(plusDI[j]! - minusDI[j]!) / diSum) * 100;
        }
      }
      adxValues = sma(dx, period);
    },
    next(ctx) {
      const i = ctx.index;
      if (
        i < 1 ||
        Number.isNaN(adxValues[i]!) ||
        Number.isNaN(plusDI[i]!) ||
        Number.isNaN(minusDI[i]!)
      )
        return null;
      const bar = ctx.bars[i]!;
      if (
        adxValues[i]! > adxThreshold &&
        plusDI[i]! > minusDI[i]! &&
        plusDI[i]! > diThreshold &&
        plusDI[i - 1]! <= minusDI[i - 1]!
      ) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `[ADX突破] ADX=${adxValues[i]!.toFixed(1)}，+DI上穿-DI`,
          strategyId: "cta-adx-breakout",
        };
      }
      if (
        adxValues[i]! > adxThreshold &&
        minusDI[i]! > plusDI[i]! &&
        minusDI[i]! > diThreshold &&
        minusDI[i - 1]! <= plusDI[i - 1]!
      ) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: ctx.position > 0 ? "sell" : "short",
          price: bar.close,
          reason: `[ADX突破] ADX=${adxValues[i]!.toFixed(1)}，-DI上穿+DI`,
          strategyId: "cta-adx-breakout",
        };
      }
      return null;
    },
  };
}

// ── TRIX Trend CTA ───────────────────────────────────────────────

export function ctaTrixTrend(period = 12, signalPeriod = 9): BuiltinStrategy {
  let trixValues: number[] = [];
  let signalLine: number[] = [];

  return {
    name: `TRIX趋势(${period},${signalPeriod})`,
    init(ctx) {
      trixValues = trix(getCloses(ctx), period);
      signalLine = sma(trixValues, signalPeriod);
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1 || Number.isNaN(trixValues[i]!) || Number.isNaN(signalLine[i]!)) return null;
      const bar = ctx.bars[i]!;
      if (trixValues[i]! > signalLine[i]! && trixValues[i - 1]! <= signalLine[i - 1]!) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `[TRIX CTA] TRIX上穿信号线`,
          strategyId: "cta-trix-trend",
        };
      }
      if (trixValues[i]! < signalLine[i]! && trixValues[i - 1]! >= signalLine[i - 1]!) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: ctx.position > 0 ? "sell" : "short",
          price: bar.close,
          reason: `[TRIX CTA] TRIX下穿信号线`,
          strategyId: "cta-trix-trend",
        };
      }
      return null;
    },
  };
}

// ── Hull MA CTA ──────────────────────────────────────────────────

export function ctaHullMaTrend(period = 16): BuiltinStrategy {
  let hull: number[] = [];

  return {
    name: `HullMA趋势(${period})`,
    init(ctx) {
      hull = hullMa(getCloses(ctx), period);
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 2 || Number.isNaN(hull[i]!) || Number.isNaN(hull[i - 1]!)) return null;
      const bar = ctx.bars[i]!;
      const rising = hull[i]! > hull[i - 1]!;
      const wasRising = hull[i - 1]! > hull[i - 2]!;
      if (rising && !wasRising) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `[HullMA CTA] 方向翻多 HMA=${hull[i]!.toFixed(2)}`,
          strategyId: "cta-hull-ma-trend",
        };
      }
      if (!rising && wasRising) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: ctx.position > 0 ? "sell" : "short",
          price: bar.close,
          reason: `[HullMA CTA] 方向翻空 HMA=${hull[i]!.toFixed(2)}`,
          strategyId: "cta-hull-ma-trend",
        };
      }
      return null;
    },
  };
}

// ── Mass Index Reversal CTA ──────────────────────────────────────

export function ctaMassIndex(emaPeriod = 9, threshold = 27): BuiltinStrategy {
  let massIdx: number[] = [];
  let trendMa: number[] = [];

  return {
    name: `MassIndex(${emaPeriod},>${threshold})`,
    init(ctx) {
      const bars = ctx.bars;
      const n = bars.length;
      const hlRange = bars.map((b) => b.high - b.low);
      const ema1 = ema(hlRange, emaPeriod);
      const ema2 = ema(ema1, emaPeriod);
      const ratio = new Array<number>(n).fill(NaN);
      for (let j = 0; j < n; j++) {
        if (!Number.isNaN(ema1[j]!) && !Number.isNaN(ema2[j]!) && ema2[j]! > 0)
          ratio[j] = ema1[j]! / ema2[j]!;
      }
      // Rolling 25-period sum
      massIdx = new Array<number>(n).fill(NaN);
      const sumPeriod = 25;
      for (let j = sumPeriod - 1; j < n; j++) {
        let sum = 0;
        let valid = true;
        for (let k = j - sumPeriod + 1; k <= j; k++) {
          if (Number.isNaN(ratio[k]!)) { valid = false; break; }
          sum += ratio[k]!;
        }
        if (valid) massIdx[j] = sum;
      }
      trendMa = ema(getCloses(ctx), 20);
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1 || Number.isNaN(massIdx[i]!) || Number.isNaN(trendMa[i]!)) return null;
      const bar = ctx.bars[i]!;
      // Reversal bulge: mass index goes above threshold then drops back below
      if (massIdx[i]! < threshold && massIdx[i - 1]! >= threshold) {
        if (bar.close > trendMa[i]!) {
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy",
            price: bar.close,
            reason: `[MassIndex] 反转膨胀回落(${massIdx[i]!.toFixed(1)})，趋势看多`,
            strategyId: "cta-mass-index",
          };
        } else {
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: ctx.position > 0 ? "sell" : "short",
            price: bar.close,
            reason: `[MassIndex] 反转膨胀回落(${massIdx[i]!.toFixed(1)})，趋势看空`,
            strategyId: "cta-mass-index",
          };
        }
      }
      return null;
    },
  };
}

// ── Chaikin Oscillator CTA ───────────────────────────────────────

export function ctaChaikinOscillator(fastPeriod = 3, slowPeriod = 10): BuiltinStrategy {
  let chaikinOsc: number[] = [];

  return {
    name: `ChaikinOsc(${fastPeriod},${slowPeriod})`,
    init(ctx) {
      const bars = ctx.bars;
      const n = bars.length;
      // AD Line
      const adl = new Array<number>(n).fill(0);
      for (let j = 0; j < n; j++) {
        const b = bars[j]!;
        const range = b.high - b.low;
        const mfm = range === 0 ? 0 : (2 * b.close - b.low - b.high) / range;
        adl[j] = (j > 0 ? adl[j - 1]! : 0) + mfm * b.volume;
      }
      const fastEma = ema(adl, fastPeriod);
      const slowEma = ema(adl, slowPeriod);
      chaikinOsc = new Array<number>(n).fill(NaN);
      for (let j = 0; j < n; j++) {
        if (!Number.isNaN(fastEma[j]!) && !Number.isNaN(slowEma[j]!))
          chaikinOsc[j] = fastEma[j]! - slowEma[j]!;
      }
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1 || Number.isNaN(chaikinOsc[i]!)) return null;
      const bar = ctx.bars[i]!;
      if (chaikinOsc[i]! > 0 && chaikinOsc[i - 1]! <= 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `[ChaikinOsc] 零轴上穿=${chaikinOsc[i]!.toFixed(0)}`,
          strategyId: "cta-chaikin-oscillator",
        };
      }
      if (chaikinOsc[i]! < 0 && chaikinOsc[i - 1]! >= 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: ctx.position > 0 ? "sell" : "short",
          price: bar.close,
          reason: `[ChaikinOsc] 零轴下穿=${chaikinOsc[i]!.toFixed(0)}`,
          strategyId: "cta-chaikin-oscillator",
        };
      }
      return null;
    },
  };
}

// ── Coppock Curve CTA ────────────────────────────────────────────

export function ctaCoppock(longRoc = 14, shortRoc = 11, wmaPeriod = 10): BuiltinStrategy {
  let coppock: number[] = [];

  return {
    name: `Coppock(${longRoc},${shortRoc},WMA${wmaPeriod})`,
    init(ctx) {
      const closes = getCloses(ctx);
      const longRocValues = roc(closes, longRoc);
      const shortRocValues = roc(closes, shortRoc);
      const n = closes.length;
      const rocSum = new Array<number>(n).fill(NaN);
      for (let j = 0; j < n; j++) {
        if (!Number.isNaN(longRocValues[j]!) && !Number.isNaN(shortRocValues[j]!))
          rocSum[j] = longRocValues[j]! + shortRocValues[j]!;
      }
      coppock = wma(rocSum, wmaPeriod);
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1 || Number.isNaN(coppock[i]!) || Number.isNaN(coppock[i - 1]!)) return null;
      const bar = ctx.bars[i]!;
      if (coppock[i]! > 0 && coppock[i - 1]! <= 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `[Coppock] 零轴上穿=${coppock[i]!.toFixed(2)}`,
          strategyId: "cta-coppock",
        };
      }
      if (coppock[i]! < 0 && coppock[i - 1]! >= 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: ctx.position > 0 ? "sell" : "short",
          price: bar.close,
          reason: `[Coppock] 零轴下穿=${coppock[i]!.toFixed(2)}`,
          strategyId: "cta-coppock",
        };
      }
      return null;
    },
  };
}

// ── RSI Divergence CTA ───────────────────────────────────────────

export function ctaRsiDivergence(
  rsiPeriod = 14,
  lookback = 20,
  oversold = 30,
  overbought = 70,
): BuiltinStrategy {
  let rsiValues: number[] = [];

  return {
    name: `RSI背离(${rsiPeriod},回看${lookback})`,
    init(ctx) {
      rsiValues = rsi(getCloses(ctx), rsiPeriod);
    },
    next(ctx) {
      const i = ctx.index;
      if (i < lookback || Number.isNaN(rsiValues[i]!)) return null;
      const bar = ctx.bars[i]!;
      const closes = getCloses(ctx);
      // Bullish divergence: price lower low but RSI higher low in oversold
      if (rsiValues[i]! < oversold + 10) {
        let priceLow = Infinity,
          rsiAtPriceLow = 0;
        for (let j = i - lookback; j < i; j++) {
          if (closes[j]! < priceLow) {
            priceLow = closes[j]!;
            rsiAtPriceLow = rsiValues[j]!;
          }
        }
        if (closes[i]! <= priceLow && rsiValues[i]! > rsiAtPriceLow) {
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy",
            price: bar.close,
            reason: `[RSI背离] 底背离: 价格新低但RSI(${rsiValues[i]!.toFixed(1)})未创新低`,
            strategyId: "cta-rsi-divergence",
          };
        }
      }
      // Bearish divergence
      if (rsiValues[i]! > overbought - 10) {
        let priceHigh = -Infinity,
          rsiAtPriceHigh = 0;
        for (let j = i - lookback; j < i; j++) {
          if (closes[j]! > priceHigh) {
            priceHigh = closes[j]!;
            rsiAtPriceHigh = rsiValues[j]!;
          }
        }
        if (closes[i]! >= priceHigh && rsiValues[i]! < rsiAtPriceHigh) {
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: ctx.position > 0 ? "sell" : "short",
            price: bar.close,
            reason: `[RSI背离] 顶背离: 价格新高但RSI(${rsiValues[i]!.toFixed(1)})未创新高`,
            strategyId: "cta-rsi-divergence",
          };
        }
      }
      return null;
    },
  };
}

// ── Volume Breakout CTA ──────────────────────────────────────────

export function ctaVolumeBreakout(
  maPeriod = 20,
  volMultiplier = 2.0,
  lookback = 20,
): BuiltinStrategy {
  let priceMa: number[] = [];
  let volMa: number[] = [];

  return {
    name: `量价突破(MA${maPeriod},量×${volMultiplier})`,
    init(ctx) {
      priceMa = sma(getCloses(ctx), maPeriod);
      const volumes = ctx.bars.map((b) => b.volume);
      volMa = sma(volumes, maPeriod);
    },
    next(ctx) {
      const i = ctx.index;
      if (i < lookback || Number.isNaN(priceMa[i]!) || Number.isNaN(volMa[i]!)) return null;
      const bar = ctx.bars[i]!;
      const closes = getCloses(ctx);
      const isVolSpike = bar.volume > volMa[i]! * volMultiplier;
      if (!isVolSpike) return null;
      // Find recent high/low
      let recentHigh = -Infinity,
        recentLow = Infinity;
      for (let j = i - lookback; j < i; j++) {
        if (closes[j]! > recentHigh) recentHigh = closes[j]!;
        if (closes[j]! < recentLow) recentLow = closes[j]!;
      }
      if (bar.close > recentHigh) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `[量价突破] 放量(${(bar.volume / volMa[i]!).toFixed(1)}倍)突破${lookback}日高点`,
          strategyId: "cta-volume-breakout",
        };
      }
      if (bar.close < recentLow) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: ctx.position > 0 ? "sell" : "short",
          price: bar.close,
          reason: `[量价突破] 放量(${(bar.volume / volMa[i]!).toFixed(1)}倍)跌破${lookback}日低点`,
          strategyId: "cta-volume-breakout",
        };
      }
      return null;
    },
  };
}

// ── Adaptive MA CTA ──────────────────────────────────────────────

export function ctaAdaptiveMa(period = 10, fastPeriod = 2, slowPeriod = 30): BuiltinStrategy {
  let kamaValues: number[] = [];

  return {
    name: `自适应MA(${period})`,
    init(ctx) {
      kamaValues = kama(getCloses(ctx), period, fastPeriod, slowPeriod);
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1 || Number.isNaN(kamaValues[i]!)) return null;
      const bar = ctx.bars[i]!;
      const prevBar = ctx.bars[i - 1]!;
      if (bar.close > kamaValues[i]! && prevBar.close <= kamaValues[i - 1]!) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `[KAMA CTA] 价格上穿自适应MA(${kamaValues[i]!.toFixed(2)})`,
          strategyId: "cta-adaptive-ma",
        };
      }
      if (bar.close < kamaValues[i]! && prevBar.close >= kamaValues[i - 1]!) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: ctx.position > 0 ? "sell" : "short",
          price: bar.close,
          reason: `[KAMA CTA] 价格下穿自适应MA(${kamaValues[i]!.toFixed(2)})`,
          strategyId: "cta-adaptive-ma",
        };
      }
      return null;
    },
  };
}

// ── MA Envelope CTA ──────────────────────────────────────────────

export function ctaMaEnvelope(period = 20, percent = 2.5): BuiltinStrategy {
  let maValues: number[] = [];

  return {
    name: `MA信封CTA(${period},±${percent}%)`,
    init(ctx) {
      maValues = sma(getCloses(ctx), period);
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1 || Number.isNaN(maValues[i]!)) return null;
      const bar = ctx.bars[i]!;
      const pctFactor = percent / 100;
      const upper = maValues[i]! * (1 + pctFactor);
      const lower = maValues[i]! * (1 - pctFactor);
      const prevBar = ctx.bars[i - 1]!;
      if (bar.close > upper && prevBar.close <= maValues[i - 1]! * (1 + pctFactor)) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `[MA信封] 突破上轨(${upper.toFixed(2)})做多`,
          strategyId: "cta-ma-envelope",
        };
      }
      if (bar.close < lower && prevBar.close >= maValues[i - 1]! * (1 - pctFactor)) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: ctx.position > 0 ? "sell" : "short",
          price: bar.close,
          reason: `[MA信封] 跌破下轨(${lower.toFixed(2)})做空`,
          strategyId: "cta-ma-envelope",
        };
      }
      return null;
    },
  };
}

// ── Connors RSI CTA ──────────────────────────────────────────────

export function ctaConnorsRsi(
  rsiPeriod = 3,
  streakPeriod = 2,
  percentRankPeriod = 100,
  oversold = 10,
  overbought = 90,
): BuiltinStrategy {
  let connorsValues: number[] = [];

  return {
    name: `ConnorsRSI_CTA(${rsiPeriod},${streakPeriod})`,
    init(ctx) {
      const closes = getCloses(ctx);
      const n = closes.length;
      const rsi1 = rsi(closes, rsiPeriod);
      const streak = new Array<number>(n).fill(0);
      for (let j = 1; j < n; j++) {
        if (closes[j]! > closes[j - 1]!) streak[j] = Math.max(1, streak[j - 1]! + 1);
        else if (closes[j]! < closes[j - 1]!) streak[j] = Math.min(-1, streak[j - 1]! - 1);
        else streak[j] = 0;
      }
      const rsi2 = rsi(streak, streakPeriod);
      const pctRank = new Array<number>(n).fill(NaN);
      for (let j = percentRankPeriod; j < n; j++) {
        const curRoc = closes[j]! / closes[j - 1]! - 1;
        let count = 0;
        for (let k = j - percentRankPeriod; k < j; k++) {
          const prevRoc = closes[k]! / closes[k > 0 ? k - 1 : 0]! - 1;
          if (prevRoc < curRoc) count++;
        }
        pctRank[j] = (count / percentRankPeriod) * 100;
      }
      connorsValues = new Array<number>(n).fill(NaN);
      for (let j = 0; j < n; j++) {
        if (!Number.isNaN(rsi1[j]!) && !Number.isNaN(rsi2[j]!) && !Number.isNaN(pctRank[j]!))
          connorsValues[j] = (rsi1[j]! + rsi2[j]! + pctRank[j]!) / 3;
      }
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1 || Number.isNaN(connorsValues[i]!)) return null;
      const bar = ctx.bars[i]!;
      if (connorsValues[i]! < oversold && connorsValues[i - 1]! >= oversold) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `[ConnorsRSI CTA] 超卖=${connorsValues[i]!.toFixed(1)}`,
          strategyId: "cta-connors-rsi",
        };
      }
      if (connorsValues[i]! > overbought && connorsValues[i - 1]! <= overbought) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: ctx.position > 0 ? "sell" : "short",
          price: bar.close,
          reason: `[ConnorsRSI CTA] 超买=${connorsValues[i]!.toFixed(1)}`,
          strategyId: "cta-connors-rsi",
        };
      }
      return null;
    },
  };
}

// ── Aroon Oscillator CTA ─────────────────────────────────────────

export function ctaAroonOscillator(period = 25): BuiltinStrategy {
  let aroonUp: number[] = [];
  let aroonDown: number[] = [];

  return {
    name: `Aroon振荡器(${period})`,
    init(ctx) {
      const a = aroon(ctx.bars, period);
      aroonUp = a.up;
      aroonDown = a.down;
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1 || Number.isNaN(aroonUp[i]!) || Number.isNaN(aroonDown[i]!)) return null;
      const bar = ctx.bars[i]!;
      const osc = aroonUp[i]! - aroonDown[i]!;
      const prevOsc = aroonUp[i - 1]! - aroonDown[i - 1]!;
      if (osc > 50 && prevOsc <= 50) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `[Aroon CTA] 振荡器突破+50 (${osc.toFixed(0)})`,
          strategyId: "cta-aroon-oscillator",
        };
      }
      if (osc < -50 && prevOsc >= -50) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: ctx.position > 0 ? "sell" : "short",
          price: bar.close,
          reason: `[Aroon CTA] 振荡器跌破-50 (${osc.toFixed(0)})`,
          strategyId: "cta-aroon-oscillator",
        };
      }
      return null;
    },
  };
}

// ── MFI Trend CTA ────────────────────────────────────────────────

export function ctaMfiTrend(period = 14, oversold = 20, overbought = 80): BuiltinStrategy {
  let mfiValues: number[] = [];
  let trendMa: number[] = [];

  return {
    name: `MFI趋势(${period})`,
    init(ctx) {
      mfiValues = mfi(ctx.bars, period);
      trendMa = sma(getCloses(ctx), 20);
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1 || Number.isNaN(mfiValues[i]!) || Number.isNaN(trendMa[i]!)) return null;
      const bar = ctx.bars[i]!;
      // MFI recovers from oversold + price above trend
      if (
        mfiValues[i]! > oversold &&
        mfiValues[i - 1]! <= oversold &&
        bar.close > trendMa[i]!
      ) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `[MFI CTA] 超卖回升 MFI=${mfiValues[i]!.toFixed(1)}，趋势看多`,
          strategyId: "cta-mfi-trend",
        };
      }
      if (
        mfiValues[i]! < overbought &&
        mfiValues[i - 1]! >= overbought &&
        bar.close < trendMa[i]!
      ) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: ctx.position > 0 ? "sell" : "short",
          price: bar.close,
          reason: `[MFI CTA] 超买回落 MFI=${mfiValues[i]!.toFixed(1)}，趋势看空`,
          strategyId: "cta-mfi-trend",
        };
      }
      return null;
    },
  };
}

// ── DEMA Crossover CTA ───────────────────────────────────────────

export function ctaDemaCrossover(fastPeriod = 10, slowPeriod = 30): BuiltinStrategy {
  let fastDema: number[] = [];
  let slowDema: number[] = [];

  return {
    name: `DEMA交叉CTA(${fastPeriod},${slowPeriod})`,
    init(ctx) {
      const closes = getCloses(ctx);
      fastDema = dema(closes, fastPeriod);
      slowDema = dema(closes, slowPeriod);
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1 || Number.isNaN(fastDema[i]!) || Number.isNaN(slowDema[i]!)) return null;
      const bar = ctx.bars[i]!;
      if (fastDema[i]! > slowDema[i]! && fastDema[i - 1]! <= slowDema[i - 1]!) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `[DEMA CTA] 快线上穿慢线`,
          strategyId: "cta-dema-crossover",
        };
      }
      if (fastDema[i]! < slowDema[i]! && fastDema[i - 1]! >= slowDema[i - 1]!) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: ctx.position > 0 ? "sell" : "short",
          price: bar.close,
          reason: `[DEMA CTA] 快线下穿慢线`,
          strategyId: "cta-dema-crossover",
        };
      }
      return null;
    },
  };
}

// ── Triple MA CTA ────────────────────────────────────────────────

export function ctaTripleMa(shortP = 5, midP = 20, longP = 60): BuiltinStrategy {
  let shortMa: number[] = [];
  let midMa: number[] = [];
  let longMa: number[] = [];

  return {
    name: `三均线CTA(${shortP}/${midP}/${longP})`,
    init(ctx) {
      const closes = getCloses(ctx);
      shortMa = sma(closes, shortP);
      midMa = sma(closes, midP);
      longMa = sma(closes, longP);
    },
    next(ctx) {
      const i = ctx.index;
      if (Number.isNaN(shortMa[i]!) || Number.isNaN(midMa[i]!) || Number.isNaN(longMa[i]!))
        return null;
      const bar = ctx.bars[i]!;
      if (
        shortMa[i]! > midMa[i]! &&
        midMa[i]! > longMa[i]! &&
        bar.close > shortMa[i]!
      ) {
        if (i > 0 && !(shortMa[i - 1]! > midMa[i - 1]! && midMa[i - 1]! > longMa[i - 1]!)) {
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy",
            price: bar.close,
            reason: `[三均线CTA] 多头排列形成: S${shortP}>M${midP}>L${longP}`,
            strategyId: "cta-triple-ma",
          };
        }
      }
      if (
        shortMa[i]! < midMa[i]! &&
        midMa[i]! < longMa[i]! &&
        bar.close < shortMa[i]!
      ) {
        if (i > 0 && !(shortMa[i - 1]! < midMa[i - 1]! && midMa[i - 1]! < longMa[i - 1]!)) {
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: ctx.position > 0 ? "sell" : "short",
            price: bar.close,
            reason: `[三均线CTA] 空头排列形成: S${shortP}<M${midP}<L${longP}`,
            strategyId: "cta-triple-ma",
          };
        }
      }
      return null;
    },
  };
}

// ── Supertrend CTA ───────────────────────────────────────────────

export function ctaSupertrendCta(atrPeriod = 10, multiplier = 3.0): BuiltinStrategy {
  let atrValues: number[] = [];
  let stDir: number[] = [];
  let stLine: number[] = [];

  return {
    name: `SupertrendCTA(${atrPeriod},×${multiplier})`,
    init(ctx) {
      const bars = ctx.bars;
      const n = bars.length;
      atrValues = atr(bars, atrPeriod);
      stLine = new Array<number>(n).fill(NaN);
      stDir = new Array<number>(n).fill(0);
      for (let j = atrPeriod; j < n; j++) {
        const mid = (bars[j]!.high + bars[j]!.low) / 2;
        const a = atrValues[j]!;
        let upperBand = mid + multiplier * a;
        let lowerBand = mid - multiplier * a;
        if (j > atrPeriod) {
          if (stDir[j - 1] === 1) lowerBand = Math.max(lowerBand, stLine[j - 1]!);
          else upperBand = Math.min(upperBand, stLine[j - 1]!);
        }
        if (j === atrPeriod) {
          stDir[j] = bars[j]!.close > mid ? 1 : -1;
          stLine[j] = stDir[j] === 1 ? lowerBand : upperBand;
        } else if (stDir[j - 1] === 1) {
          if (bars[j]!.close < stLine[j - 1]!) {
            stDir[j] = -1;
            stLine[j] = upperBand;
          } else {
            stDir[j] = 1;
            stLine[j] = lowerBand;
          }
        } else {
          if (bars[j]!.close > stLine[j - 1]!) {
            stDir[j] = 1;
            stLine[j] = lowerBand;
          } else {
            stDir[j] = -1;
            stLine[j] = upperBand;
          }
        }
      }
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1 || Number.isNaN(stLine[i]!)) return null;
      const bar = ctx.bars[i]!;
      if (stDir[i] === 1 && stDir[i - 1] === -1) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `[Supertrend CTA] 翻多，支撑=${stLine[i]!.toFixed(2)}`,
          strategyId: "cta-supertrend",
        };
      }
      if (stDir[i] === -1 && stDir[i - 1] === 1) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: ctx.position > 0 ? "sell" : "short",
          price: bar.close,
          reason: `[Supertrend CTA] 翻空，压力=${stLine[i]!.toFixed(2)}`,
          strategyId: "cta-supertrend",
        };
      }
      return null;
    },
  };
}
