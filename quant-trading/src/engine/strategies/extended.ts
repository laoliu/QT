/**
 * Extended builtin strategies — additional stock / general strategies.
 *
 *   - trixMomentum:       TRIX zero-cross momentum
 *   - mfiOscillator:      Money Flow Index overbought/oversold
 *   - aroonTrend:         Aroon Up/Down crossover
 *   - cmfDivergence:      Chaikin Money Flow direction
 *   - elderRayStrategy:   Bull/Bear power with EMA
 *   - supertrendStrategy: ATR-based dynamic Supertrend
 *   - hullMaCrossover:    Hull MA crossover
 *   - demaCrossover:      Double EMA crossover
 *   - tripleMaCrossover:  Triple SMA alignment
 *   - maEnvelopeStrategy: MA ±% Envelope
 *   - connorsRsiStrategy: Composite RSI (rsi+streak+percentile)
 *   - kamaStrategy:       Kaufman Adaptive MA trend
 *   - demarkerStrategy:   DeMarker oscillator
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
  rsi,
  sma,
  trix,
} from "./indicators.js";

// ── TRIX Momentum ────────────────────────────────────────────────

export function trixMomentum(period = 15, signalPeriod = 9): BuiltinStrategy {
  let trixValues: number[] = [];
  let signalLine: number[] = [];

  return {
    name: `TRIX动量(${period},${signalPeriod})`,
    init(ctx) {
      trixValues = trix(getCloses(ctx), period);
      signalLine = sma(trixValues, signalPeriod);
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1 || Number.isNaN(trixValues[i]!) || Number.isNaN(signalLine[i]!)) return null;
      const bar = ctx.bars[i]!;
      if (
        trixValues[i]! > signalLine[i]! &&
        trixValues[i - 1]! <= signalLine[i - 1]! &&
        ctx.position <= 0
      ) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `TRIX(${trixValues[i]!.toFixed(4)})上穿信号线(${signalLine[i]!.toFixed(4)})`,
          strategyId: "trix-momentum",
        };
      }
      if (
        trixValues[i]! < signalLine[i]! &&
        trixValues[i - 1]! >= signalLine[i - 1]! &&
        ctx.position > 0
      ) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell",
          price: bar.close,
          reason: `TRIX(${trixValues[i]!.toFixed(4)})下穿信号线(${signalLine[i]!.toFixed(4)})`,
          strategyId: "trix-momentum",
        };
      }
      return null;
    },
  };
}

// ── MFI Oscillator ───────────────────────────────────────────────

export function mfiOscillator(period = 14, oversold = 20, overbought = 80): BuiltinStrategy {
  let mfiValues: number[] = [];

  return {
    name: `MFI(${period},${oversold}/${overbought})`,
    init(ctx) {
      mfiValues = mfi(ctx.bars, period);
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1 || Number.isNaN(mfiValues[i]!)) return null;
      const bar = ctx.bars[i]!;
      if (mfiValues[i]! < oversold && mfiValues[i - 1]! >= oversold && ctx.position <= 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `MFI(${mfiValues[i]!.toFixed(1)})进入超卖区<${oversold}`,
          strategyId: "mfi-oscillator",
        };
      }
      if (mfiValues[i]! > overbought && mfiValues[i - 1]! <= overbought && ctx.position > 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell",
          price: bar.close,
          reason: `MFI(${mfiValues[i]!.toFixed(1)})进入超买区>${overbought}`,
          strategyId: "mfi-oscillator",
        };
      }
      return null;
    },
  };
}

// ── Aroon Trend ──────────────────────────────────────────────────

export function aroonTrend(period = 25): BuiltinStrategy {
  let aroonUp: number[] = [];
  let aroonDown: number[] = [];

  return {
    name: `Aroon趋势(${period})`,
    init(ctx) {
      const a = aroon(ctx.bars, period);
      aroonUp = a.up;
      aroonDown = a.down;
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1 || Number.isNaN(aroonUp[i]!) || Number.isNaN(aroonDown[i]!)) return null;
      const bar = ctx.bars[i]!;
      if (
        aroonUp[i]! > aroonDown[i]! &&
        aroonUp[i - 1]! <= aroonDown[i - 1]! &&
        ctx.position <= 0
      ) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `AroonUp(${aroonUp[i]!.toFixed(0)})上穿AroonDown(${aroonDown[i]!.toFixed(0)})`,
          strategyId: "aroon-trend",
        };
      }
      if (
        aroonUp[i]! < aroonDown[i]! &&
        aroonUp[i - 1]! >= aroonDown[i - 1]! &&
        ctx.position > 0
      ) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell",
          price: bar.close,
          reason: `AroonDown(${aroonDown[i]!.toFixed(0)})上穿AroonUp(${aroonUp[i]!.toFixed(0)})`,
          strategyId: "aroon-trend",
        };
      }
      return null;
    },
  };
}

// ── CMF Divergence ───────────────────────────────────────────────

export function cmfDivergence(period = 20, threshold = 0.05): BuiltinStrategy {
  let cmfValues: number[] = [];

  return {
    name: `CMF(${period},±${threshold})`,
    init(ctx) {
      cmfValues = cmf(ctx.bars, period);
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1 || Number.isNaN(cmfValues[i]!)) return null;
      const bar = ctx.bars[i]!;
      if (cmfValues[i]! > threshold && cmfValues[i - 1]! <= threshold && ctx.position <= 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `CMF(${cmfValues[i]!.toFixed(3)})突破+${threshold}，资金流入`,
          strategyId: "cmf-divergence",
        };
      }
      if (cmfValues[i]! < -threshold && cmfValues[i - 1]! >= -threshold && ctx.position > 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell",
          price: bar.close,
          reason: `CMF(${cmfValues[i]!.toFixed(3)})跌破-${threshold}，资金流出`,
          strategyId: "cmf-divergence",
        };
      }
      return null;
    },
  };
}

// ── Elder Ray ────────────────────────────────────────────────────

export function elderRayStrategy(emaPeriod = 13): BuiltinStrategy {
  let emaValues: number[] = [];

  return {
    name: `ElderRay(${emaPeriod})`,
    init(ctx) {
      emaValues = ema(getCloses(ctx), emaPeriod);
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1 || Number.isNaN(emaValues[i]!)) return null;
      const bar = ctx.bars[i]!;
      const prevBar = ctx.bars[i - 1]!;
      const bullPower = bar.high - emaValues[i]!;
      const bearPower = bar.low - emaValues[i]!;
      const prevBull = prevBar.high - emaValues[i - 1]!;
      // Buy: EMA rising + bear power < 0 but improving
      if (
        emaValues[i]! > emaValues[i - 1]! &&
        bearPower < 0 &&
        bearPower > (prevBar.low - emaValues[i - 1]!) &&
        ctx.position <= 0
      ) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `ElderRay 买入: EMA↑，BearPower=${bearPower.toFixed(2)}回升`,
          strategyId: "elder-ray",
        };
      }
      // Sell: EMA falling + bull power > 0 but declining
      if (
        emaValues[i]! < emaValues[i - 1]! &&
        bullPower > 0 &&
        bullPower < prevBull &&
        ctx.position > 0
      ) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell",
          price: bar.close,
          reason: `ElderRay 卖出: EMA↓，BullPower=${bullPower.toFixed(2)}衰退`,
          strategyId: "elder-ray",
        };
      }
      return null;
    },
  };
}

// ── Supertrend ───────────────────────────────────────────────────

export function supertrendStrategy(atrPeriod = 10, multiplier = 3.0): BuiltinStrategy {
  let atrValues: number[] = [];
  let supertrend: number[] = [];
  let stDirection: number[] = [];

  return {
    name: `Supertrend(${atrPeriod},×${multiplier})`,
    init(ctx) {
      const bars = ctx.bars;
      const n = bars.length;
      atrValues = atr(bars, atrPeriod);
      supertrend = new Array<number>(n).fill(NaN);
      stDirection = new Array<number>(n).fill(0); // 1=up, -1=down
      for (let j = atrPeriod; j < n; j++) {
        const mid = (bars[j]!.high + bars[j]!.low) / 2;
        const a = atrValues[j]!;
        let upperBand = mid + multiplier * a;
        let lowerBand = mid - multiplier * a;
        if (j > atrPeriod) {
          const prevUpper = supertrend[j - 1]!;
          const prevLower = supertrend[j - 1]!;
          if (stDirection[j - 1] === 1) {
            lowerBand = Math.max(lowerBand, prevLower);
          } else {
            upperBand = Math.min(upperBand, prevUpper);
          }
        }
        if (j === atrPeriod) {
          stDirection[j] = bars[j]!.close > mid ? 1 : -1;
          supertrend[j] = stDirection[j] === 1 ? lowerBand : upperBand;
        } else if (stDirection[j - 1] === 1) {
          if (bars[j]!.close < supertrend[j - 1]!) {
            stDirection[j] = -1;
            supertrend[j] = upperBand;
          } else {
            stDirection[j] = 1;
            supertrend[j] = lowerBand;
          }
        } else {
          if (bars[j]!.close > supertrend[j - 1]!) {
            stDirection[j] = 1;
            supertrend[j] = lowerBand;
          } else {
            stDirection[j] = -1;
            supertrend[j] = upperBand;
          }
        }
      }
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1 || Number.isNaN(supertrend[i]!)) return null;
      const bar = ctx.bars[i]!;
      if (stDirection[i] === 1 && stDirection[i - 1] === -1 && ctx.position <= 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `Supertrend翻多，支撑线=${supertrend[i]!.toFixed(2)}`,
          strategyId: "supertrend",
        };
      }
      if (stDirection[i] === -1 && stDirection[i - 1] === 1 && ctx.position > 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell",
          price: bar.close,
          reason: `Supertrend翻空，压力线=${supertrend[i]!.toFixed(2)}`,
          strategyId: "supertrend",
        };
      }
      return null;
    },
  };
}

// ── Hull MA Crossover ────────────────────────────────────────────

export function hullMaCrossover(fastPeriod = 9, slowPeriod = 21): BuiltinStrategy {
  let fastHull: number[] = [];
  let slowHull: number[] = [];

  return {
    name: `HullMA(${fastPeriod},${slowPeriod})`,
    init(ctx) {
      const closes = getCloses(ctx);
      fastHull = hullMa(closes, fastPeriod);
      slowHull = hullMa(closes, slowPeriod);
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1 || Number.isNaN(fastHull[i]!) || Number.isNaN(slowHull[i]!)) return null;
      const bar = ctx.bars[i]!;
      if (fastHull[i]! > slowHull[i]! && fastHull[i - 1]! <= slowHull[i - 1]! && ctx.position <= 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `HullMA快线(${fastHull[i]!.toFixed(2)})上穿慢线(${slowHull[i]!.toFixed(2)})`,
          strategyId: "hull-ma-crossover",
        };
      }
      if (fastHull[i]! < slowHull[i]! && fastHull[i - 1]! >= slowHull[i - 1]! && ctx.position > 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell",
          price: bar.close,
          reason: `HullMA快线(${fastHull[i]!.toFixed(2)})下穿慢线(${slowHull[i]!.toFixed(2)})`,
          strategyId: "hull-ma-crossover",
        };
      }
      return null;
    },
  };
}

// ── DEMA Crossover ───────────────────────────────────────────────

export function demaCrossover(fastPeriod = 10, slowPeriod = 30): BuiltinStrategy {
  let fastDema: number[] = [];
  let slowDema: number[] = [];

  return {
    name: `DEMA(${fastPeriod},${slowPeriod})`,
    init(ctx) {
      const closes = getCloses(ctx);
      fastDema = dema(closes, fastPeriod);
      slowDema = dema(closes, slowPeriod);
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1 || Number.isNaN(fastDema[i]!) || Number.isNaN(slowDema[i]!)) return null;
      const bar = ctx.bars[i]!;
      if (fastDema[i]! > slowDema[i]! && fastDema[i - 1]! <= slowDema[i - 1]! && ctx.position <= 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `DEMA快线(${fastDema[i]!.toFixed(2)})上穿慢线(${slowDema[i]!.toFixed(2)})`,
          strategyId: "dema-crossover",
        };
      }
      if (fastDema[i]! < slowDema[i]! && fastDema[i - 1]! >= slowDema[i - 1]! && ctx.position > 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell",
          price: bar.close,
          reason: `DEMA快线(${fastDema[i]!.toFixed(2)})下穿慢线(${slowDema[i]!.toFixed(2)})`,
          strategyId: "dema-crossover",
        };
      }
      return null;
    },
  };
}

// ── Triple MA Crossover ──────────────────────────────────────────

export function tripleMaCrossover(shortP = 5, midP = 20, longP = 60): BuiltinStrategy {
  let shortMa: number[] = [];
  let midMa: number[] = [];
  let longMa: number[] = [];

  return {
    name: `三均线(${shortP}/${midP}/${longP})`,
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
      // 多头排列
      if (
        shortMa[i]! > midMa[i]! &&
        midMa[i]! > longMa[i]! &&
        bar.close > shortMa[i]! &&
        ctx.position <= 0
      ) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `三均线多头排列: MA${shortP}>${midP}>${longP}`,
          strategyId: "triple-ma-crossover",
        };
      }
      // 空头排列
      if (
        shortMa[i]! < midMa[i]! &&
        midMa[i]! < longMa[i]! &&
        bar.close < shortMa[i]! &&
        ctx.position > 0
      ) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell",
          price: bar.close,
          reason: `三均线空头排列: MA${shortP}<${midP}<${longP}`,
          strategyId: "triple-ma-crossover",
        };
      }
      return null;
    },
  };
}

// ── MA Envelope ──────────────────────────────────────────────────

export function maEnvelopeStrategy(period = 20, percent = 3): BuiltinStrategy {
  let maValues: number[] = [];

  return {
    name: `MA信封(${period},±${percent}%)`,
    init(ctx) {
      maValues = sma(getCloses(ctx), period);
    },
    next(ctx) {
      const i = ctx.index;
      if (Number.isNaN(maValues[i]!)) return null;
      const bar = ctx.bars[i]!;
      const pctFactor = percent / 100;
      const upper = maValues[i]! * (1 + pctFactor);
      const lower = maValues[i]! * (1 - pctFactor);
      if (bar.close < lower && ctx.position <= 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `价格(${bar.close.toFixed(2)})跌破MA${period}下轨(${lower.toFixed(2)})`,
          strategyId: "ma-envelope",
        };
      }
      if (bar.close > upper && ctx.position > 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell",
          price: bar.close,
          reason: `价格(${bar.close.toFixed(2)})突破MA${period}上轨(${upper.toFixed(2)})`,
          strategyId: "ma-envelope",
        };
      }
      return null;
    },
  };
}

// ── Connors RSI ──────────────────────────────────────────────────

export function connorsRsiStrategy(
  rsiPeriod = 3,
  streakPeriod = 2,
  percentRankPeriod = 100,
  oversold = 15,
  overbought = 85,
): BuiltinStrategy {
  let connorsValues: number[] = [];

  return {
    name: `ConnorsRSI(${rsiPeriod},${streakPeriod},${percentRankPeriod})`,
    init(ctx) {
      const closes = getCloses(ctx);
      const n = closes.length;
      // Component 1: RSI(close, rsiPeriod)
      const rsi1 = rsi(closes, rsiPeriod);
      // Component 2: RSI of up/down streak
      const streak = new Array<number>(n).fill(0);
      for (let j = 1; j < n; j++) {
        if (closes[j]! > closes[j - 1]!) streak[j] = Math.max(1, streak[j - 1]! + 1);
        else if (closes[j]! < closes[j - 1]!) streak[j] = Math.min(-1, streak[j - 1]! - 1);
        else streak[j] = 0;
      }
      const rsi2 = rsi(streak, streakPeriod);
      // Component 3: percent rank of 1-day ROC
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
      // Combine
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
      if (connorsValues[i]! < oversold && connorsValues[i - 1]! >= oversold && ctx.position <= 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `ConnorsRSI(${connorsValues[i]!.toFixed(1)})进入超卖区<${oversold}`,
          strategyId: "connors-rsi",
        };
      }
      if (connorsValues[i]! > overbought && connorsValues[i - 1]! <= overbought && ctx.position > 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell",
          price: bar.close,
          reason: `ConnorsRSI(${connorsValues[i]!.toFixed(1)})进入超买区>${overbought}`,
          strategyId: "connors-rsi",
        };
      }
      return null;
    },
  };
}

// ── KAMA Strategy ────────────────────────────────────────────────

export function kamaStrategy(period = 10, fastPeriod = 2, slowPeriod = 30): BuiltinStrategy {
  let kamaValues: number[] = [];

  return {
    name: `KAMA(${period},${fastPeriod},${slowPeriod})`,
    init(ctx) {
      kamaValues = kama(getCloses(ctx), period, fastPeriod, slowPeriod);
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1 || Number.isNaN(kamaValues[i]!)) return null;
      const bar = ctx.bars[i]!;
      const prevBar = ctx.bars[i - 1]!;
      // Price crosses above KAMA
      if (bar.close > kamaValues[i]! && prevBar.close <= kamaValues[i - 1]! && ctx.position <= 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `价格上穿KAMA(${kamaValues[i]!.toFixed(2)})，自适应趋势看多`,
          strategyId: "kama-trend",
        };
      }
      if (bar.close < kamaValues[i]! && prevBar.close >= kamaValues[i - 1]! && ctx.position > 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell",
          price: bar.close,
          reason: `价格下穿KAMA(${kamaValues[i]!.toFixed(2)})，自适应趋势看空`,
          strategyId: "kama-trend",
        };
      }
      return null;
    },
  };
}

// ── DeMarker Strategy ────────────────────────────────────────────

export function demarkerStrategy(period = 14, oversold = 0.3, overbought = 0.7): BuiltinStrategy {
  let dmValues: number[] = [];

  return {
    name: `DeMarker(${period},${oversold}/${overbought})`,
    init(ctx) {
      dmValues = demarker(ctx.bars, period);
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1 || Number.isNaN(dmValues[i]!)) return null;
      const bar = ctx.bars[i]!;
      if (dmValues[i]! < oversold && dmValues[i - 1]! >= oversold && ctx.position <= 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `DeMarker(${dmValues[i]!.toFixed(3)})进入超卖区<${oversold}`,
          strategyId: "demarker",
        };
      }
      if (dmValues[i]! > overbought && dmValues[i - 1]! <= overbought && ctx.position > 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell",
          price: bar.close,
          reason: `DeMarker(${dmValues[i]!.toFixed(3)})进入超买区>${overbought}`,
          strategyId: "demarker",
        };
      }
      return null;
    },
  };
}
