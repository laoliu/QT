/**
 * CTA channel breakout, squeeze, and reversal strategies.
 *
 *   - ctaRBreaker:       Pivot-point intraday reversal
 *   - ctaAberration:     菲阿里四价 + Bollinger filter
 *   - ctaTtmSqueeze:     Bollinger + Keltner squeeze breakout
 *   - ctaDmiAdx:         DMI/ADX trend strength
 *   - ctaParabolicSar:   Parabolic Stop-And-Reverse
 */
import type { BuiltinStrategy } from "./indicators.js";
import { atr, bollingerBands, cci, dmi, ema, getCloses, parabolicSar } from "./indicators.js";

// ── R-Breaker (日内经典高频策略) ───────────────────────────────────

/**
 * R-Breaker strategy — a classic pivot-point intraday strategy.
 *
 * Computes 6 price levels from the previous bar:
 *   - ssetup = high + 2×(close - low)         (突破买入价)
 *   - setup  = close + (high - low)            (观察卖出价)
 *   - enter  = 2×close - low                   (反转卖出价)
 *   - enter2 = 2×close - high                  (反转买入价)
 *   - setup2 = close - (high - low)            (观察买入价)
 *   - ssetup2= low - 2×(high - close)          (突破卖出价)
 *
 * Trend mode (flat → entry):
 *   - Long:  close > ssetup  → buy
 *   - Short: close < ssetup2 → short
 *
 * Reversal mode (already in position):
 *   - Long exit → short:  holding long, high reaches setup,
 *     then close drops below enter → sell + short
 *   - Short exit → long: holding short, low reaches setup2,
 *     then close rises above enter2 → cover + buy
 *
 * In daily-bar mode, we use the previous bar for pivot calculations
 * and the current bar for breakout/reversal checks.
 */
export function ctaRBreaker(): BuiltinStrategy {
  let direction = 0; // 1=long, -1=short, 0=flat
  let dayHighReachedSetup = false;
  let dayLowReachedSetup2 = false;

  return {
    name: "R-Breaker(枢轴突破反转)",
    init() {
      direction = 0;
      dayHighReachedSetup = false;
      dayLowReachedSetup2 = false;
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1) return null;

      const prev = ctx.bars[i - 1]!;
      const bar = ctx.bars[i]!;

      // Pivot levels from previous bar
      const pH = prev.high;
      const pL = prev.low;
      const pC = prev.close;

      const ssetup = pH + 2 * (pC - pL); // 突破买入价 (resistance breakout)
      const setup = pC + (pH - pL); // 观察卖出价 (sell observation)
      const enter = 2 * pC - pL; // 反转卖出价 (sell reversal pivot)
      const enter2 = 2 * pC - pH; // 反转买入价 (buy reversal pivot)
      const setup2 = pC - (pH - pL); // 观察买入价 (buy observation)
      const ssetup2 = pL - 2 * (pH - pC); // 突破卖出价 (support breakout)

      // Track if today's high/low reached the observation levels
      if (bar.high >= setup) dayHighReachedSetup = true;
      if (bar.low <= setup2) dayLowReachedSetup2 = true;

      // ── Reversal logic (when in position) ──
      if (direction === 1 && dayHighReachedSetup && bar.close < enter) {
        // Long → sell + short (reversal at high)
        direction = -1;
        dayHighReachedSetup = false;
        dayLowReachedSetup2 = false;
        return [
          {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "sell" as const,
            price: bar.close,
            reason: `[R-Breaker平多] 高点触及观察位 ¥${setup.toFixed(2)} 后回落至 ¥${enter.toFixed(2)} 下方`,
            strategyId: "r-breaker",
          },
          {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "short" as const,
            price: bar.close,
            reason: `[R-Breaker反转做空] 价格 ¥${bar.close.toFixed(2)} 反转入场`,
            strategyId: "r-breaker",
          },
        ];
      }
      if (direction === -1 && dayLowReachedSetup2 && bar.close > enter2) {
        // Short → cover + buy (reversal at low)
        direction = 1;
        dayHighReachedSetup = false;
        dayLowReachedSetup2 = false;
        return [
          {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "cover" as const,
            price: bar.close,
            reason: `[R-Breaker平空] 低点触及观察位 ¥${setup2.toFixed(2)} 后反弹至 ¥${enter2.toFixed(2)} 上方`,
            strategyId: "r-breaker",
          },
          {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy" as const,
            price: bar.close,
            reason: `[R-Breaker反转做多] 价格 ¥${bar.close.toFixed(2)} 反转入场`,
            strategyId: "r-breaker",
          },
        ];
      }

      // ── Breakout logic (when flat) ──
      if (direction === 0) {
        if (bar.close > ssetup) {
          direction = 1;
          dayHighReachedSetup = false;
          dayLowReachedSetup2 = false;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy" as const,
            price: bar.close,
            reason:
              `[R-Breaker突破做多] 价格 ¥${bar.close.toFixed(2)} 突破上轨 ` +
              `¥${ssetup.toFixed(2)}`,
            strategyId: "r-breaker",
          };
        }
        if (bar.close < ssetup2) {
          direction = -1;
          dayHighReachedSetup = false;
          dayLowReachedSetup2 = false;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "short" as const,
            price: bar.close,
            reason:
              `[R-Breaker突破做空] 价格 ¥${bar.close.toFixed(2)} 跌破下轨 ` +
              `¥${ssetup2.toFixed(2)}`,
            strategyId: "r-breaker",
          };
        }
      }

      return null;
    },
  };
}

// ── Aberration / 菲阿里四价 ──────────────────────────────────────

/**
 * Aberration strategy (菲阿里四价变种 + Bollinger通道).
 *
 * Uses the previous day's OHLC as key levels combined with a longer-term
 * Bollinger Band channel for trend filtering:
 *
 * Entry:
 *   - Long:  close > prevHigh AND close > BB middle → buy
 *   - Short: close < prevLow AND close < BB middle → short
 *
 * Exit:
 *   - Long exit:  close < prevLow OR close < BB lower → sell
 *   - Short exit: close > prevHigh OR close > BB upper → cover
 *
 * The Bollinger filter avoids whipsaw in ranging markets: only trade
 * breakouts aligned with the medium-term trend direction.
 */
export function ctaAberration(bbPeriod = 20, bbStdDev = 2.0): BuiltinStrategy {
  let middle: number[] = [];
  let upper: number[] = [];
  let lower: number[] = [];
  let direction = 0; // 1=long, -1=short, 0=flat

  return {
    name: `菲阿里四价(BB${bbPeriod},${bbStdDev}σ)`,
    init(ctx) {
      const closes = getCloses(ctx);
      const bb = bollingerBands(closes, bbPeriod, bbStdDev);
      middle = bb.middle;
      upper = bb.upper;
      lower = bb.lower;
      direction = 0;
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1 || Number.isNaN(middle[i]!)) return null;

      const bar = ctx.bars[i]!;
      const prev = ctx.bars[i - 1]!;
      const curMiddle = middle[i]!;
      const curUpper = upper[i]!;
      const curLower = lower[i]!;

      // Exit conditions
      if (direction === 1 && (bar.close < prev.low || bar.close < curLower)) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell" as const,
          price: bar.close,
          reason:
            `[菲阿里平多] 价格 ¥${bar.close.toFixed(2)} 跌破` +
            `${bar.close < prev.low ? `昨低 ¥${prev.low.toFixed(2)}` : `BB下轨 ¥${curLower.toFixed(2)}`}`,
          strategyId: "aberration",
        };
      }
      if (direction === -1 && (bar.close > prev.high || bar.close > curUpper)) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "cover" as const,
          price: bar.close,
          reason:
            `[菲阿里平空] 价格 ¥${bar.close.toFixed(2)} 突破` +
            `${bar.close > prev.high ? `昨高 ¥${prev.high.toFixed(2)}` : `BB上轨 ¥${curUpper.toFixed(2)}`}`,
          strategyId: "aberration",
        };
      }

      // Entry conditions
      if (direction === 0) {
        // Long: break above prevHigh + trend confirmation (above BB middle)
        if (bar.close > prev.high && bar.close > curMiddle) {
          direction = 1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy" as const,
            price: bar.close,
            reason:
              `[菲阿里做多] 价格 ¥${bar.close.toFixed(2)} 突破昨高 ` +
              `¥${prev.high.toFixed(2)}，且在BB中轨上方`,
            strategyId: "aberration",
          };
        }
        // Short: break below prevLow + trend confirmation (below BB middle)
        if (bar.close < prev.low && bar.close < curMiddle) {
          direction = -1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "short" as const,
            price: bar.close,
            reason:
              `[菲阿里做空] 价格 ¥${bar.close.toFixed(2)} 跌破昨低 ` +
              `¥${prev.low.toFixed(2)}，且在BB中轨下方`,
            strategyId: "aberration",
          };
        }
      }

      return null;
    },
  };
}

// ── TTM Squeeze (Bollinger + Keltner 挤压突破) ───────────────────

/**
 * TTM Squeeze strategy — identifies volatility contractions
 * (Bollinger Bands squeeze inside Keltner Channel) and trades the
 * breakout direction using CCI momentum.
 *
 * Squeeze detection:
 *   - Squeeze ON:  BB upper < Keltner upper AND BB lower > Keltner lower
 *   - Squeeze OFF: bands expand beyond Keltner
 *
 * Entry (on squeeze release):
 *   - Long:  squeeze fires (ON→OFF) + CCI > 0 → buy
 *   - Short: squeeze fires (ON→OFF) + CCI < 0 → short
 *
 * Exit:
 *   - Long exit:  CCI crosses below 0 → sell
 *   - Short exit: CCI crosses above 0 → cover
 *
 * Combines BB/Keltner (already in our indicator library) with CCI
 * for directional momentum confirmation.
 */
export function ctaTtmSqueeze(
  bbPeriod = 20,
  bbStdDev = 2.0,
  keltnerEmaPeriod = 20,
  keltnerAtrPeriod = 14,
  keltnerAtrMultiplier = 1.5,
  cciPeriod = 20,
): BuiltinStrategy {
  let bbUpper: number[] = [];
  let bbLower: number[] = [];
  let keltnerUpper: number[] = [];
  let keltnerLower: number[] = [];
  let cciValues: number[] = [];
  let prevSqueezeOn = false;
  let direction = 0; // 1=long, -1=short, 0=flat

  return {
    name: `TTM挤压(BB${bbPeriod}/${bbStdDev}σ,KC${keltnerEmaPeriod},CCI${cciPeriod})`,
    init(ctx) {
      const closes = getCloses(ctx);
      const bb = bollingerBands(closes, bbPeriod, bbStdDev);
      bbUpper = bb.upper;
      bbLower = bb.lower;

      const kEma = ema(closes, keltnerEmaPeriod);
      const kAtr = atr(ctx.bars, keltnerAtrPeriod);
      keltnerUpper = [];
      keltnerLower = [];
      for (let j = 0; j < closes.length; j++) {
        if (Number.isNaN(kEma[j]!) || Number.isNaN(kAtr[j]!)) {
          keltnerUpper.push(NaN);
          keltnerLower.push(NaN);
        } else {
          keltnerUpper.push(kEma[j]! + keltnerAtrMultiplier * kAtr[j]!);
          keltnerLower.push(kEma[j]! - keltnerAtrMultiplier * kAtr[j]!);
        }
      }

      cciValues = cci(ctx.bars, cciPeriod);
      prevSqueezeOn = false;
      direction = 0;
    },
    next(ctx) {
      const i = ctx.index;
      if (
        Number.isNaN(bbUpper[i]!) ||
        Number.isNaN(keltnerUpper[i]!) ||
        Number.isNaN(cciValues[i]!)
      )
        return null;

      const bar = ctx.bars[i]!;
      const curCci = cciValues[i]!;

      // Detect squeeze state
      const squeezeOn = bbUpper[i]! < keltnerUpper[i]! && bbLower[i]! > keltnerLower[i]!;
      const squeezeFired = prevSqueezeOn && !squeezeOn; // ON → OFF transition
      prevSqueezeOn = squeezeOn;

      // Exit conditions (CCI zero cross)
      if (direction === 1 && curCci < 0) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell" as const,
          price: bar.close,
          reason: `[TTM平多] CCI=${curCci.toFixed(1)} 跌破零轴，动量转弱`,
          strategyId: "ttm-squeeze",
        };
      }
      if (direction === -1 && curCci > 0) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "cover" as const,
          price: bar.close,
          reason: `[TTM平空] CCI=${curCci.toFixed(1)} 突破零轴，动量转强`,
          strategyId: "ttm-squeeze",
        };
      }

      // Entry on squeeze release
      if (direction === 0 && squeezeFired) {
        if (curCci > 0) {
          direction = 1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy" as const,
            price: bar.close,
            reason:
              `[TTM做多] 挤压释放+CCI=${curCci.toFixed(1)}>0，` +
              `价格 ¥${bar.close.toFixed(2)} 向上突破`,
            strategyId: "ttm-squeeze",
          };
        }
        if (curCci < 0) {
          direction = -1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "short" as const,
            price: bar.close,
            reason:
              `[TTM做空] 挤压释放+CCI=${curCci.toFixed(1)}<0，` +
              `价格 ¥${bar.close.toFixed(2)} 向下突破`,
            strategyId: "ttm-squeeze",
          };
        }
      }

      return null;
    },
  };
}

// ── DMI/ADX Trend Strength ───────────────────────────────────────

/**
 * DMI/ADX趋势强度策略
 *
 * Uses the Average Directional Index (ADX) to filter for trending markets,
 * then enters in the direction indicated by +DI / -DI crossover.
 *
 * Entry:
 *   - Long:  ADX > adxThreshold AND +DI crosses above -DI → buy
 *   - Short: ADX > adxThreshold AND -DI crosses above +DI → short
 *
 * Exit:
 *   - Long exit:  ADX drops below adxThreshold OR -DI crosses above +DI → sell
 *   - Short exit: ADX drops below adxThreshold OR +DI crosses above -DI → cover
 */
export function ctaDmiAdx(period = 14, adxThreshold = 25): BuiltinStrategy {
  let plusDI: number[] = [];
  let minusDI: number[] = [];
  let adxArr: number[] = [];
  let direction = 0; // 1=long, -1=short, 0=flat

  return {
    name: `DMI/ADX趋势(${period},ADX>${adxThreshold})`,
    init(ctx) {
      const result = dmi(ctx.bars, period);
      plusDI = result.plusDI;
      minusDI = result.minusDI;
      adxArr = result.adx;
      direction = 0;
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1) return null;
      if (
        Number.isNaN(plusDI[i]!) ||
        Number.isNaN(minusDI[i]!) ||
        Number.isNaN(adxArr[i]!) ||
        Number.isNaN(plusDI[i - 1]!) ||
        Number.isNaN(minusDI[i - 1]!)
      )
        return null;

      const bar = ctx.bars[i]!;
      const curPlusDI = plusDI[i]!;
      const curMinusDI = minusDI[i]!;
      const prevPlusDI = plusDI[i - 1]!;
      const prevMinusDI = minusDI[i - 1]!;
      const curAdx = adxArr[i]!;

      // DI crossover detection
      const plusCrossAbove = prevPlusDI <= prevMinusDI && curPlusDI > curMinusDI;
      const minusCrossAbove = prevMinusDI <= prevPlusDI && curMinusDI > curPlusDI;

      // Exit conditions
      if (direction === 1 && (curAdx < adxThreshold || minusCrossAbove)) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell" as const,
          price: bar.close,
          reason:
            curAdx < adxThreshold
              ? `[DMI平多] ADX=${curAdx.toFixed(1)} 跌破${adxThreshold}，趋势转弱`
              : `[DMI平多] -DI=${curMinusDI.toFixed(1)} 上穿+DI=${curPlusDI.toFixed(1)}，方向反转`,
          strategyId: "dmi-adx",
        };
      }
      if (direction === -1 && (curAdx < adxThreshold || plusCrossAbove)) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "cover" as const,
          price: bar.close,
          reason:
            curAdx < adxThreshold
              ? `[DMI平空] ADX=${curAdx.toFixed(1)} 跌破${adxThreshold}，趋势转弱`
              : `[DMI平空] +DI=${curPlusDI.toFixed(1)} 上穿-DI=${curMinusDI.toFixed(1)}，方向反转`,
          strategyId: "dmi-adx",
        };
      }

      // Entry conditions
      if (direction === 0 && curAdx >= adxThreshold) {
        if (plusCrossAbove) {
          direction = 1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy" as const,
            price: bar.close,
            reason:
              `[DMI做多] ADX=${curAdx.toFixed(1)}>${adxThreshold}，` +
              `+DI=${curPlusDI.toFixed(1)} 上穿-DI=${curMinusDI.toFixed(1)}`,
            strategyId: "dmi-adx",
          };
        }
        if (minusCrossAbove) {
          direction = -1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "short" as const,
            price: bar.close,
            reason:
              `[DMI做空] ADX=${curAdx.toFixed(1)}>${adxThreshold}，` +
              `-DI=${curMinusDI.toFixed(1)} 上穿+DI=${curPlusDI.toFixed(1)}`,
            strategyId: "dmi-adx",
          };
        }
      }

      return null;
    },
  };
}

// ── Parabolic SAR Trend Following ────────────────────────────────

/**
 * 抛物线SAR趋势跟踪策略
 *
 * Classic Parabolic Stop-And-Reverse system:
 *   - In uptrend (close > SAR): go long
 *   - In downtrend (close < SAR): go short
 *   - SAR reversal flips position
 *
 * Uses acceleration factor that speeds up as the trend extends.
 * Best suited for strongly trending markets; can whipsaw in ranges.
 */
export function ctaParabolicSar(afStart = 0.02, afStep = 0.02, afMax = 0.2): BuiltinStrategy {
  let sarValues: number[] = [];
  let trendValues: number[] = [];
  let direction = 0; // 1=long, -1=short, 0=flat

  return {
    name: `抛物线SAR(af=${afStart},step=${afStep},max=${afMax})`,
    init(ctx) {
      const result = parabolicSar(ctx.bars, afStart, afStep, afMax);
      sarValues = result.sar;
      trendValues = result.trend;
      direction = 0;
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 2) return null; // need SAR warmup
      if (Number.isNaN(sarValues[i]!) || trendValues[i] === 0) return null;

      const bar = ctx.bars[i]!;
      const curTrend = trendValues[i]!;
      const prevTrend = trendValues[i - 1]!;
      const curSar = sarValues[i]!;

      // Detect trend reversal
      const trendFlipped = prevTrend !== 0 && curTrend !== prevTrend;

      if (!trendFlipped) return null;

      // Uptrend reversal → go long (or flip from short to long)
      if (curTrend === 1) {
        const signals: ReturnType<BuiltinStrategy["next"]>[] = [];
        if (direction === -1) {
          signals.push({
            timestamp: bar.timestamp,
            symbol: "",
            direction: "cover" as const,
            price: bar.close,
            reason: `[SAR平空] SAR反转向上 ¥${curSar.toFixed(2)}，趋势转多`,
            strategyId: "parabolic-sar",
          });
        }
        direction = 1;
        signals.push({
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy" as const,
          price: bar.close,
          reason: `[SAR做多] 价格 ¥${bar.close.toFixed(2)} > SAR ¥${curSar.toFixed(2)}，趋势向上`,
          strategyId: "parabolic-sar",
        });
        return signals.length === 1
          ? (signals[0] as ReturnType<BuiltinStrategy["next"]>)
          : (signals as unknown as ReturnType<BuiltinStrategy["next"]>);
      }

      // Downtrend reversal → go short (or flip from long to short)
      if (curTrend === -1) {
        const signals: ReturnType<BuiltinStrategy["next"]>[] = [];
        if (direction === 1) {
          signals.push({
            timestamp: bar.timestamp,
            symbol: "",
            direction: "sell" as const,
            price: bar.close,
            reason: `[SAR平多] SAR反转向下 ¥${curSar.toFixed(2)}，趋势转空`,
            strategyId: "parabolic-sar",
          });
        }
        direction = -1;
        signals.push({
          timestamp: bar.timestamp,
          symbol: "",
          direction: "short" as const,
          price: bar.close,
          reason: `[SAR做空] 价格 ¥${bar.close.toFixed(2)} < SAR ¥${curSar.toFixed(2)}，趋势向下`,
          strategyId: "parabolic-sar",
        });
        return signals.length === 1
          ? (signals[0] as ReturnType<BuiltinStrategy["next"]>)
          : (signals as unknown as ReturnType<BuiltinStrategy["next"]>);
      }

      return null;
    },
  };
}
