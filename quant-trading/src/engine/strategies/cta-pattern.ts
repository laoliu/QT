/**
 * CTA pattern recognition and advanced momentum strategies.
 *
 *   - ctaRangeBreakout:          Range consolidation breakout
 *   - ctaVwapReversion:          VWAP mean reversion
 *   - ctaDoubleBottom:           Double bottom/top pattern
 *   - ctaRocMomentum:            ROC momentum with thresholds
 *   - ctaCciDivergence:          CCI divergence detection
 *   - ctaEmaCrossover:           Dual EMA crossover
 *   - ctaAtrTrailing:            ATR trailing stop
 *   - ctaStochasticRsi:          Stochastic RSI oscillator
 *   - ctaMacdHistogramReversal:  MACD histogram slope reversal
 *   - ctaBollingerBandwidth:     Bollinger bandwidth squeeze
 */
import type { BuiltinStrategy } from "./indicators.js";
import { atr, bollingerBands, cci, ema, getCloses, macd, roc, rsi, sma } from "./indicators.js";

// ── Range Breakout (区间突破) ───────────────────────────────────

/**
 * 区间突破策略.
 *
 * 用前 N 根 K 线构建整理区间（最高价/最低价），
 * 当价格突破区间时入场，回归区间中轨时平仓。
 *
 * Entry:
 *   - 多: close 突破区间上沿 → buy
 *   - 空: close 跌破区间下沿 → short
 *
 * Exit:
 *   - 平多: close < 区间中轨 → sell
 *   - 平空: close > 区间中轨 → cover
 *
 * 适用于盘整后突破的品种。
 */
export function ctaRangeBreakout(rangePeriod = 20): BuiltinStrategy {
  let rangeHigh = 0;
  let rangeLow = 0;
  let rangeMid = 0;
  let rangeReady = false;
  let direction = 0;

  return {
    name: `区间突破(${rangePeriod})`,
    init(ctx) {
      direction = 0;
      rangeReady = false;
      if (ctx.bars.length >= rangePeriod) {
        let hi = -Infinity;
        let lo = Infinity;
        for (let k = 0; k < rangePeriod; k++) {
          const b = ctx.bars[k]!;
          if (b.high > hi) hi = b.high;
          if (b.low < lo) lo = b.low;
        }
        rangeHigh = hi;
        rangeLow = lo;
        rangeMid = (hi + lo) / 2;
        rangeReady = true;
      }
    },
    next(ctx) {
      const i = ctx.index;
      if (!rangeReady || i < rangePeriod) return null;

      const bar = ctx.bars[i]!;

      // Exit
      if (direction === 1 && bar.close < rangeMid) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell" as const,
          price: bar.close,
          reason:
            `[区间平多] 价格 ¥${bar.close.toFixed(2)} < 中轨` +
            ` ¥${rangeMid.toFixed(2)}，回归区间`,
          strategyId: "range-breakout",
        };
      }
      if (direction === -1 && bar.close > rangeMid) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "cover" as const,
          price: bar.close,
          reason:
            `[区间平空] 价格 ¥${bar.close.toFixed(2)} > 中轨` +
            ` ¥${rangeMid.toFixed(2)}，回归区间`,
          strategyId: "range-breakout",
        };
      }

      // Entry
      if (direction === 0) {
        if (bar.close > rangeHigh) {
          direction = 1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy" as const,
            price: bar.close,
            reason:
              `[区间做多] 价格 ¥${bar.close.toFixed(2)} 突破区间上沿` + ` ¥${rangeHigh.toFixed(2)}`,
            strategyId: "range-breakout",
          };
        }
        if (bar.close < rangeLow) {
          direction = -1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "short" as const,
            price: bar.close,
            reason:
              `[区间做空] 价格 ¥${bar.close.toFixed(2)} 跌破区间下沿` + ` ¥${rangeLow.toFixed(2)}`,
            strategyId: "range-breakout",
          };
        }
      }

      return null;
    },
  };
}

// ── VWAP Reversion (VWAP 回归) ──────────────────────────────────

/**
 * VWAP 回归策略.
 *
 * 使用成交量加权平均价 (VWAP) 作为均值参考。
 * 当价格偏离 VWAP 超过阈值时逆向入场，回归 VWAP 时平仓。
 *
 * VWAP = Σ(price × volume) / Σ(volume)  (滚动窗口)
 *
 * Entry:
 *   - 多: close < VWAP × (1 - threshold) → 超跌买入
 *   - 空: close > VWAP × (1 + threshold) → 超涨做空
 *
 * Exit:
 *   - 平多: close > VWAP → 回归均值
 *   - 平空: close < VWAP → 回归均值
 *
 * 适用于高成交量品种和日内交易。
 */
export function ctaVwapReversion(period = 20, threshold = 0.02): BuiltinStrategy {
  let vwapValues: number[] = [];
  let direction = 0;

  return {
    name: `VWAP回归(${period},${(threshold * 100).toFixed(0)}%)`,
    init(ctx) {
      // Compute rolling VWAP using sliding window — O(n)
      const n = ctx.bars.length;
      vwapValues = new Array<number>(n).fill(NaN);
      let sumPV = 0;
      let sumV = 0;
      for (let j = 0; j < n; j++) {
        const b = ctx.bars[j]!;
        const typical = (b.high + b.low + b.close) / 3;
        sumPV += typical * b.volume;
        sumV += b.volume;
        if (j >= period) {
          const leaving = ctx.bars[j - period]!;
          const leavingTypical = (leaving.high + leaving.low + leaving.close) / 3;
          sumPV -= leavingTypical * leaving.volume;
          sumV -= leaving.volume;
        }
        if (j >= period - 1) {
          vwapValues[j] = sumV > 0 ? sumPV / sumV : NaN;
        }
      }
      direction = 0;
    },
    next(ctx) {
      const i = ctx.index;
      const vwap = vwapValues[i]!;
      if (Number.isNaN(vwap)) return null;

      const bar = ctx.bars[i]!;

      // Exit
      if (direction === 1 && bar.close > vwap) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell" as const,
          price: bar.close,
          reason:
            `[VWAP平多] 价格 ¥${bar.close.toFixed(2)} > VWAP` + ` ¥${vwap.toFixed(2)}，回归均值`,
          strategyId: "vwap-reversion",
        };
      }
      if (direction === -1 && bar.close < vwap) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "cover" as const,
          price: bar.close,
          reason:
            `[VWAP平空] 价格 ¥${bar.close.toFixed(2)} < VWAP` + ` ¥${vwap.toFixed(2)}，回归均值`,
          strategyId: "vwap-reversion",
        };
      }

      // Entry
      if (direction === 0) {
        const lowerBand = vwap * (1 - threshold);
        const upperBand = vwap * (1 + threshold);
        if (bar.close < lowerBand) {
          direction = 1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy" as const,
            price: bar.close,
            reason:
              `[VWAP做多] 价格 ¥${bar.close.toFixed(2)} < VWAP下轨` +
              ` ¥${lowerBand.toFixed(2)}，超跌买入`,
            strategyId: "vwap-reversion",
          };
        }
        if (bar.close > upperBand) {
          direction = -1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "short" as const,
            price: bar.close,
            reason:
              `[VWAP做空] 价格 ¥${bar.close.toFixed(2)} > VWAP上轨` +
              ` ¥${upperBand.toFixed(2)}，超涨做空`,
            strategyId: "vwap-reversion",
          };
        }
      }

      return null;
    },
  };
}

// ── Double Bottom/Top (双底/双顶) ───────────────────────────────

/**
 * 双底/双顶形态策略.
 *
 * 检测近 N 根 K 线中的双底或双顶形态：
 *   - 双底：两个相近的低点 + 中间反弹 → 买入信号
 *   - 双顶：两个相近的高点 + 中间回落 → 做空信号
 *
 * 使用滚动窗口的最低/最高价来发现形态。
 *
 * Entry:
 *   - 多: 当前 close 从双底区域反弹至中间高点以上 → buy
 *   - 空: 当前 close 从双顶区域回落至中间低点以下 → short
 *
 * Exit:
 *   - 平多: close 跌回双底中点以下 → sell
 *   - 平空: close 涨回双顶中点以上 → cover
 *
 * 适用于震荡转趋势的品种。
 */
export function ctaDoubleBottom(lookback = 30, tolerance = 0.02): BuiltinStrategy {
  let direction = 0;
  let neckline = 0; // 颈线 (middle peak/trough between the two bottoms/tops)

  return {
    name: `双底双顶(${lookback},${(tolerance * 100).toFixed(0)}%)`,
    init() {
      direction = 0;
      neckline = 0;
    },
    next(ctx) {
      const i = ctx.index;
      if (i < lookback) return null;

      const bar = ctx.bars[i]!;

      // Exit
      if (direction === 1 && bar.close < neckline) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell" as const,
          price: bar.close,
          reason: `[双底平多] 价格 ¥${bar.close.toFixed(2)} 跌破颈线` + ` ¥${neckline.toFixed(2)}`,
          strategyId: "double-bottom",
        };
      }
      if (direction === -1 && bar.close > neckline) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "cover" as const,
          price: bar.close,
          reason: `[双顶平空] 价格 ¥${bar.close.toFixed(2)} 涨破颈线` + ` ¥${neckline.toFixed(2)}`,
          strategyId: "double-bottom",
        };
      }

      // Pattern detection only when flat
      if (direction !== 0) return null;

      // Scan for double bottom in recent bars (manual loop to avoid stack overflow with spread)
      let minLow = Infinity;
      let maxHigh = -Infinity;
      for (let k = i - lookback; k <= i; k++) {
        const b = ctx.bars[k]!;
        if (b.low < minLow) minLow = b.low;
        if (b.high > maxHigh) maxHigh = b.high;
      }

      // Collect lows/highs arrays for pattern detection
      const lows: number[] = new Array(lookback + 1);
      const highs: number[] = new Array(lookback + 1);
      for (let k = 0; k <= lookback; k++) {
        const b = ctx.bars[i - lookback + k]!;
        lows[k] = b.low;
        highs[k] = b.high;
      }

      // Find two lowest points
      const firstMinIdx = lows.indexOf(minLow);

      // Find second low (at least 5 bars away from first, within tolerance)
      let secondMinIdx = -1;
      let secondMinVal = Infinity;
      for (let k = 0; k < lows.length; k++) {
        if (Math.abs(k - firstMinIdx) < 5) continue;
        if (lows[k]! <= secondMinVal) {
          secondMinVal = lows[k]!;
          secondMinIdx = k;
        }
      }

      // Double bottom detection
      if (secondMinIdx >= 0 && Math.abs(minLow - secondMinVal) / minLow < tolerance) {
        // Find peak between the two lows (neckline)
        const [lo, hi] =
          firstMinIdx < secondMinIdx ? [firstMinIdx, secondMinIdx] : [secondMinIdx, firstMinIdx];
        let peakBetween = -Infinity;
        for (let k = lo + 1; k < hi; k++) {
          if (highs[k]! > peakBetween) peakBetween = highs[k]!;
        }
        // Confirm: current close breaks above neckline
        if (peakBetween > minLow && bar.close > peakBetween) {
          direction = 1;
          neckline = (minLow + secondMinVal) / 2;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy" as const,
            price: bar.close,
            reason:
              `[双底做多] 价格 ¥${bar.close.toFixed(2)} 突破颈线` +
              ` ¥${peakBetween.toFixed(2)}，双底确认`,
            strategyId: "double-bottom",
          };
        }
      }

      // Double top detection: reuse maxHigh from above
      const firstMaxIdx = highs.indexOf(maxHigh);
      let secondMaxIdx = -1;
      let secondMaxVal = -Infinity;
      for (let k = 0; k < highs.length; k++) {
        if (Math.abs(k - firstMaxIdx) < 5) continue;
        if (highs[k]! >= secondMaxVal) {
          secondMaxVal = highs[k]!;
          secondMaxIdx = k;
        }
      }

      if (secondMaxIdx >= 0 && Math.abs(maxHigh - secondMaxVal) / maxHigh < tolerance) {
        const [lo2, hi2] =
          firstMaxIdx < secondMaxIdx ? [firstMaxIdx, secondMaxIdx] : [secondMaxIdx, firstMaxIdx];
        let troughBetween = Infinity;
        for (let k = lo2 + 1; k < hi2; k++) {
          if (lows[k]! < troughBetween) troughBetween = lows[k]!;
        }
        if (troughBetween < maxHigh && bar.close < troughBetween) {
          direction = -1;
          neckline = (maxHigh + secondMaxVal) / 2;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "short" as const,
            price: bar.close,
            reason:
              `[双顶做空] 价格 ¥${bar.close.toFixed(2)} 跌破颈线` +
              ` ¥${troughBetween.toFixed(2)}，双顶确认`,
            strategyId: "double-bottom",
          };
        }
      }

      return null;
    },
  };
}

// ── ROC Momentum (ROC 动量) ─────────────────────────────────────

/**
 * ROC 动量策略.
 *
 * 使用变动率 (Rate of Change) 指标衡量动量。
 * ROC 超过阈值时做多（正动量），低于负阈值时做空。
 *
 * Entry:
 *   - 多: ROC > entryThreshold → 正动量做多
 *   - 空: ROC < -entryThreshold → 负动量做空
 *
 * Exit:
 *   - 平多: ROC < exitThreshold → 动量衰减
 *   - 平空: ROC > -exitThreshold → 动量衰减
 *
 * 适用于趋势启动阶段，配合成交量过滤效果更好。
 */
export function ctaRocMomentum(
  period = 12,
  entryThreshold = 5.0,
  exitThreshold = 0.0,
): BuiltinStrategy {
  let rocValues: number[] = [];
  let direction = 0;

  return {
    name: `ROC动量(${period},入${entryThreshold}%,出${exitThreshold}%)`,
    init(ctx) {
      const closes = getCloses(ctx);
      rocValues = roc(closes, period);
      direction = 0;
    },
    next(ctx) {
      const i = ctx.index;
      const r = rocValues[i]!;
      if (Number.isNaN(r)) return null;

      const bar = ctx.bars[i]!;

      // Exit
      if (direction === 1 && r < exitThreshold) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell" as const,
          price: bar.close,
          reason: `[ROC平多] ROC=${r.toFixed(2)}% < ${exitThreshold}%，动量衰减`,
          strategyId: "roc-momentum",
        };
      }
      if (direction === -1 && r > -exitThreshold) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "cover" as const,
          price: bar.close,
          reason: `[ROC平空] ROC=${r.toFixed(2)}% > ${-exitThreshold}%，动量衰减`,
          strategyId: "roc-momentum",
        };
      }

      // Entry
      if (direction === 0) {
        if (r > entryThreshold) {
          direction = 1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy" as const,
            price: bar.close,
            reason: `[ROC做多] ROC=${r.toFixed(2)}% > ${entryThreshold}%，正动量`,
            strategyId: "roc-momentum",
          };
        }
        if (r < -entryThreshold) {
          direction = -1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "short" as const,
            price: bar.close,
            reason: `[ROC做空] ROC=${r.toFixed(2)}% < ${-entryThreshold}%，负动量`,
            strategyId: "roc-momentum",
          };
        }
      }

      return null;
    },
  };
}

// ── CCI Divergence (CCI 背离) ───────────────────────────────────

/**
 * CCI 背离策略.
 *
 * 使用 CCI 指标与价格走势的背离来捕捉趋势反转。
 *   - 底背离：价格创新低但 CCI 未创新低 → 做多
 *   - 顶背离：价格创新高但 CCI 未创新高 → 做空
 *
 * Entry:
 *   - 多: CCI 底背离 + CCI 从超卖区回升 → buy
 *   - 空: CCI 顶背离 + CCI 从超买区回落 → short
 *
 * Exit:
 *   - 平多: CCI > overbought → 动量过热
 *   - 平空: CCI < oversold → 动量过冷
 *
 * 适用于震荡转趋势的品种。
 */
export function ctaCciDivergence(
  cciPeriod = 20,
  lookback = 10,
  overbought = 100,
  oversold = -100,
): BuiltinStrategy {
  let cciValues: number[] = [];
  let direction = 0;

  return {
    name: `CCI背离(${cciPeriod},OB${overbought},OS${oversold})`,
    init(ctx) {
      cciValues = cci(ctx.bars, cciPeriod);
      direction = 0;
    },
    next(ctx) {
      const i = ctx.index;
      if (i < lookback) return null;
      const curCci = cciValues[i]!;
      if (Number.isNaN(curCci)) return null;

      const bar = ctx.bars[i]!;

      // Exit
      if (direction === 1 && curCci > overbought) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell" as const,
          price: bar.close,
          reason: `[CCI平多] CCI=${curCci.toFixed(0)} > ${overbought}，` + `动量过热，获利了结`,
          strategyId: "cci-divergence",
        };
      }
      if (direction === -1 && curCci < oversold) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "cover" as const,
          price: bar.close,
          reason: `[CCI平空] CCI=${curCci.toFixed(0)} < ${oversold}，` + `动量过冷，获利了结`,
          strategyId: "cci-divergence",
        };
      }

      // Divergence detection only when flat
      if (direction !== 0) return null;

      // Look for divergence in recent lookback bars
      let priceLow = Infinity;
      let priceHigh = -Infinity;
      let cciAtPriceLow = 0;
      let cciAtPriceHigh = 0;
      let earlierPriceLow = Infinity;
      let earlierCciLow = 0;
      let earlierPriceHigh = -Infinity;
      let earlierCciHigh = 0;

      const half = Math.floor(lookback / 2);
      // First half
      for (let k = i - lookback; k < i - half; k++) {
        const c = ctx.bars[k]!.close;
        const cv = cciValues[k]!;
        if (Number.isNaN(cv)) continue;
        if (c < earlierPriceLow) {
          earlierPriceLow = c;
          earlierCciLow = cv;
        }
        if (c > earlierPriceHigh) {
          earlierPriceHigh = c;
          earlierCciHigh = cv;
        }
      }
      // Second half (recent)
      for (let k = i - half; k <= i; k++) {
        const c = ctx.bars[k]!.close;
        const cv = cciValues[k]!;
        if (Number.isNaN(cv)) continue;
        if (c < priceLow) {
          priceLow = c;
          cciAtPriceLow = cv;
        }
        if (c > priceHigh) {
          priceHigh = c;
          cciAtPriceHigh = cv;
        }
      }

      // Bullish divergence: price makes lower low, CCI makes higher low
      if (priceLow < earlierPriceLow && cciAtPriceLow > earlierCciLow && curCci > oversold) {
        direction = 1;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy" as const,
          price: bar.close,
          reason:
            `[CCI底背离] 价格新低 ¥${priceLow.toFixed(2)} 但CCI` +
            `(${cciAtPriceLow.toFixed(0)}) > 前低CCI(${earlierCciLow.toFixed(0)})`,
          strategyId: "cci-divergence",
        };
      }

      // Bearish divergence: price makes higher high, CCI makes lower high
      if (priceHigh > earlierPriceHigh && cciAtPriceHigh < earlierCciHigh && curCci < overbought) {
        direction = -1;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "short" as const,
          price: bar.close,
          reason:
            `[CCI顶背离] 价格新高 ¥${priceHigh.toFixed(2)} 但CCI` +
            `(${cciAtPriceHigh.toFixed(0)}) < 前高CCI(${earlierCciHigh.toFixed(0)})`,
          strategyId: "cci-divergence",
        };
      }

      return null;
    },
  };
}

// ── EMA Crossover (EMA 交叉) ────────────────────────────────────

/**
 * EMA 交叉策略.
 *
 * 经典双 EMA 交叉策略：快线上穿慢线做多，快线下穿慢线做空。
 * 比 SMA 交叉更灵敏，适合中短周期交易。
 *
 * Entry:
 *   - 多: EMA_fast 上穿 EMA_slow → buy
 *   - 空: EMA_fast 下穿 EMA_slow → short
 *
 * Exit:
 *   - 平多: EMA_fast 下穿 EMA_slow → sell
 *   - 平空: EMA_fast 上穿 EMA_slow → cover
 *
 * 适用于趋势明确的品种。
 */
export function ctaEmaCrossover(fastPeriod = 12, slowPeriod = 26): BuiltinStrategy {
  let emaFast: number[] = [];
  let emaSlow: number[] = [];
  let direction = 0;

  return {
    name: `EMA交叉(${fastPeriod},${slowPeriod})`,
    init(ctx) {
      const closes = getCloses(ctx);
      emaFast = ema(closes, fastPeriod);
      emaSlow = ema(closes, slowPeriod);
      direction = 0;
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1) return null;
      const curFast = emaFast[i]!;
      const curSlow = emaSlow[i]!;
      const prevFast = emaFast[i - 1]!;
      const prevSlow = emaSlow[i - 1]!;
      if (
        Number.isNaN(curFast) ||
        Number.isNaN(curSlow) ||
        Number.isNaN(prevFast) ||
        Number.isNaN(prevSlow)
      )
        return null;

      const bar = ctx.bars[i]!;
      const goldenCross = prevFast <= prevSlow && curFast > curSlow;
      const deathCross = prevFast >= prevSlow && curFast < curSlow;

      // Exit + reverse
      if (direction === 1 && deathCross) {
        direction = -1;
        return [
          {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "sell" as const,
            price: bar.close,
            reason:
              `[EMA平多] 快线EMA${fastPeriod}(${curFast.toFixed(1)}) 下穿` +
              ` 慢线EMA${slowPeriod}(${curSlow.toFixed(1)})`,
            strategyId: "ema-crossover",
          },
          {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "short" as const,
            price: bar.close,
            reason:
              `[EMA做空] 快线EMA${fastPeriod}(${curFast.toFixed(1)}) 下穿` +
              ` 慢线EMA${slowPeriod}(${curSlow.toFixed(1)})`,
            strategyId: "ema-crossover",
          },
        ];
      }
      if (direction === -1 && goldenCross) {
        direction = 1;
        return [
          {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "cover" as const,
            price: bar.close,
            reason:
              `[EMA平空] 快线EMA${fastPeriod}(${curFast.toFixed(1)}) 上穿` +
              ` 慢线EMA${slowPeriod}(${curSlow.toFixed(1)})`,
            strategyId: "ema-crossover",
          },
          {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy" as const,
            price: bar.close,
            reason:
              `[EMA做多] 快线EMA${fastPeriod}(${curFast.toFixed(1)}) 上穿` +
              ` 慢线EMA${slowPeriod}(${curSlow.toFixed(1)})`,
            strategyId: "ema-crossover",
          },
        ];
      }

      // Initial entry
      if (direction === 0) {
        if (goldenCross) {
          direction = 1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy" as const,
            price: bar.close,
            reason:
              `[EMA做多] 快线EMA${fastPeriod}(${curFast.toFixed(1)}) 上穿` +
              ` 慢线EMA${slowPeriod}(${curSlow.toFixed(1)})`,
            strategyId: "ema-crossover",
          };
        }
        if (deathCross) {
          direction = -1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "short" as const,
            price: bar.close,
            reason:
              `[EMA做空] 快线EMA${fastPeriod}(${curFast.toFixed(1)}) 下穿` +
              ` 慢线EMA${slowPeriod}(${curSlow.toFixed(1)})`,
            strategyId: "ema-crossover",
          };
        }
      }

      return null;
    },
  };
}

// ── ATR Trailing (ATR 跟踪止盈) ─────────────────────────────────

/**
 * ATR 跟踪止盈策略.
 *
 * 使用 SMA 趋势判断方向，ATR 倍数作为跟踪止盈/止损距离。
 * 入场后持续跟踪最有利价格，当回撤超过 ATR × mult 时平仓。
 *
 * Entry:
 *   - 多: close > SMA(trendPeriod) → 趋势向上，做多
 *   - 空: close < SMA(trendPeriod) → 趋势向下，做空
 *
 * Exit:
 *   - 平多: close < bestPrice - mult × ATR → 回撤止盈
 *   - 平空: close > worstPrice + mult × ATR → 反弹止盈
 *
 * 适用于需要精细止盈管理的趋势交易。
 */
export function ctaAtrTrailing(atrPeriod = 14, mult = 2.0, trendPeriod = 50): BuiltinStrategy {
  let atrValues: number[] = [];
  let trendMa: number[] = [];
  let direction = 0;
  let bestPrice = 0;
  let worstPrice = Infinity;

  return {
    name: `ATR跟踪(ATR${atrPeriod},${mult}x,MA${trendPeriod})`,
    init(ctx) {
      atrValues = atr(ctx.bars, atrPeriod);
      const closes = getCloses(ctx);
      trendMa = sma(closes, trendPeriod);
      direction = 0;
      bestPrice = 0;
      worstPrice = Infinity;
    },
    next(ctx) {
      const i = ctx.index;
      const curAtr = atrValues[i]!;
      const ma = trendMa[i]!;
      if (Number.isNaN(curAtr) || Number.isNaN(ma) || curAtr === 0) return null;

      const bar = ctx.bars[i]!;

      // Track best/worst prices while in position
      if (direction === 1) {
        if (bar.close > bestPrice) bestPrice = bar.close;
        const stopLevel = bestPrice - mult * curAtr;
        if (bar.close < stopLevel) {
          direction = 0;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "sell" as const,
            price: bar.close,
            reason:
              `[ATR平多] 价格 ¥${bar.close.toFixed(2)} < 跟踪止盈线` +
              ` ¥${stopLevel.toFixed(2)} (最高${bestPrice.toFixed(1)} - ${mult}×ATR)`,
            strategyId: "atr-trailing",
          };
        }
        return null;
      }
      if (direction === -1) {
        if (bar.close < worstPrice) worstPrice = bar.close;
        const stopLevel = worstPrice + mult * curAtr;
        if (bar.close > stopLevel) {
          direction = 0;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "cover" as const,
            price: bar.close,
            reason:
              `[ATR平空] 价格 ¥${bar.close.toFixed(2)} > 跟踪止盈线` +
              ` ¥${stopLevel.toFixed(2)} (最低${worstPrice.toFixed(1)} + ${mult}×ATR)`,
            strategyId: "atr-trailing",
          };
        }
        return null;
      }

      // Entry based on trend
      if (bar.close > ma) {
        direction = 1;
        bestPrice = bar.close;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy" as const,
          price: bar.close,
          reason:
            `[ATR做多] 价格 ¥${bar.close.toFixed(2)} > MA${trendPeriod}` +
            ` ¥${ma.toFixed(2)}，趋势向上`,
          strategyId: "atr-trailing",
        };
      }
      if (bar.close < ma) {
        direction = -1;
        worstPrice = bar.close;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "short" as const,
          price: bar.close,
          reason:
            `[ATR做空] 价格 ¥${bar.close.toFixed(2)} < MA${trendPeriod}` +
            ` ¥${ma.toFixed(2)}，趋势向下`,
          strategyId: "atr-trailing",
        };
      }

      return null;
    },
  };
}

// ── CTA Stochastic RSI Strategy ─────────────────────────────────
/**
 * StochRSI = Stochastic oscillator applied to RSI values.
 * Generates overbought/oversold signals from the StochRSI %K/%D crossover.
 * Buy when StochRSI crosses up from oversold; short when crosses down from overbought.
 */
export function ctaStochasticRsi(
  rsiPeriod = 14,
  stochPeriod = 14,
  kSmooth = 3,
  overbought = 80,
  oversold = 20,
): BuiltinStrategy {
  let stochK: number[] = [];
  let stochD: number[] = [];
  let direction = 0;

  return {
    name: `随机RSI(${rsiPeriod},${stochPeriod},${kSmooth})`,
    init(ctx) {
      const closes = getCloses(ctx);
      const rsiValues = rsi(closes, rsiPeriod);
      const n = rsiValues.length;

      // Compute stochastic on RSI values
      const rawK = new Array<number>(n).fill(NaN);
      for (let i = stochPeriod - 1; i < n; i++) {
        let hh = -Infinity;
        let ll = Infinity;
        for (let j = i - stochPeriod + 1; j <= i; j++) {
          const v = rsiValues[j]!;
          if (Number.isNaN(v)) continue;
          if (v > hh) hh = v;
          if (v < ll) ll = v;
        }
        if (hh === -Infinity || ll === Infinity || hh === ll) {
          rawK[i] = 50;
        } else {
          const rv = rsiValues[i]!;
          rawK[i] = Number.isNaN(rv) ? NaN : ((rv - ll) / (hh - ll)) * 100;
        }
      }
      stochK = sma(rawK, kSmooth);
      stochD = sma(stochK, kSmooth);
      direction = 0;
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1) return null;
      const k = stochK[i]!;
      const kPrev = stochK[i - 1]!;
      const d = stochD[i]!;
      const dPrev = stochD[i - 1]!;
      if (Number.isNaN(k) || Number.isNaN(d) || Number.isNaN(kPrev) || Number.isNaN(dPrev))
        return null;

      const bar = ctx.bars[i]!;
      const goldenCross = kPrev <= dPrev && k > d;
      const deathCross = kPrev >= dPrev && k < d;

      // Exit conditions
      if (direction === 1 && (deathCross || k > overbought)) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell" as const,
          price: bar.close,
          reason:
            `[StochRSI平多] K=${k.toFixed(1)} D=${d.toFixed(1)}` +
            (deathCross ? " K下穿D" : " 超买"),
          strategyId: "stochastic-rsi",
        };
      }
      if (direction === -1 && (goldenCross || k < oversold)) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "cover" as const,
          price: bar.close,
          reason:
            `[StochRSI平空] K=${k.toFixed(1)} D=${d.toFixed(1)}` +
            (goldenCross ? " K上穿D" : " 超卖"),
          strategyId: "stochastic-rsi",
        };
      }

      // Entry
      if (direction === 0 && goldenCross && kPrev < oversold) {
        direction = 1;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy" as const,
          price: bar.close,
          reason:
            `[StochRSI做多] K=${k.toFixed(1)} 上穿 D=${d.toFixed(1)}` +
            ` 从超卖区(${oversold})反弹`,
          strategyId: "stochastic-rsi",
        };
      }
      if (direction === 0 && deathCross && kPrev > overbought) {
        direction = -1;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "short" as const,
          price: bar.close,
          reason:
            `[StochRSI做空] K=${k.toFixed(1)} 下穿 D=${d.toFixed(1)}` +
            ` 从超买区(${overbought})回落`,
          strategyId: "stochastic-rsi",
        };
      }

      return null;
    },
  };
}

// ── CTA MACD Histogram Reversal Strategy ────────────────────────
/**
 * Detects MACD histogram reversals (direction change of histogram slope).
 * Entry: histogram changes from shrinking to expanding in trending direction.
 * Exit: histogram reverses against position.
 */
export function ctaMacdHistogramReversal(
  fastPeriod = 12,
  slowPeriod = 26,
  signalPeriod = 9,
): BuiltinStrategy {
  let histograms: number[] = [];
  let direction = 0;

  return {
    name: `MACD柱状图反转(${fastPeriod},${slowPeriod},${signalPeriod})`,
    init(ctx) {
      const closes = getCloses(ctx);
      const m = macd(closes, fastPeriod, slowPeriod, signalPeriod);
      histograms = m.histogram;
      direction = 0;
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 2) return null;
      const h0 = histograms[i]!;
      const h1 = histograms[i - 1]!;
      const h2 = histograms[i - 2]!;
      if (Number.isNaN(h0) || Number.isNaN(h1) || Number.isNaN(h2)) return null;

      const bar = ctx.bars[i]!;
      // Histogram slope reversal detection
      const prevSlope = h1 - h2;
      const curSlope = h0 - h1;

      // Bullish reversal: histogram was decreasing (negative slope), now increasing
      const bullReversal = prevSlope < 0 && curSlope > 0 && h0 < 0;
      // Bearish reversal: histogram was increasing (positive slope), now decreasing
      const bearReversal = prevSlope > 0 && curSlope < 0 && h0 > 0;

      // Zero-line crossover
      const zeroCrossUp = h1 <= 0 && h0 > 0;
      const zeroCrossDown = h1 >= 0 && h0 < 0;

      // Exit
      if (direction === 1 && (bearReversal || zeroCrossDown)) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell" as const,
          price: bar.close,
          reason:
            `[MACD柱平多] 柱状图=${h0.toFixed(2)}` + (zeroCrossDown ? " 零轴下穿" : " 柱高反转"),
          strategyId: "macd-histogram-reversal",
        };
      }
      if (direction === -1 && (bullReversal || zeroCrossUp)) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "cover" as const,
          price: bar.close,
          reason:
            `[MACD柱平空] 柱状图=${h0.toFixed(2)}` + (zeroCrossUp ? " 零轴上穿" : " 柱低反转"),
          strategyId: "macd-histogram-reversal",
        };
      }

      // Entry on zero-line cross (strong signal)
      if (direction === 0 && zeroCrossUp) {
        direction = 1;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy" as const,
          price: bar.close,
          reason: `[MACD柱做多] 柱状图=${h0.toFixed(2)} 上穿零轴，` + `斜率=${curSlope.toFixed(2)}`,
          strategyId: "macd-histogram-reversal",
        };
      }
      if (direction === 0 && zeroCrossDown) {
        direction = -1;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "short" as const,
          price: bar.close,
          reason: `[MACD柱做空] 柱状图=${h0.toFixed(2)} 下穿零轴，` + `斜率=${curSlope.toFixed(2)}`,
          strategyId: "macd-histogram-reversal",
        };
      }

      return null;
    },
  };
}

// ── CTA Bollinger Bandwidth Strategy ────────────────────────────
/**
 * Uses Bollinger Band Width to detect squeeze (low volatility) periods,
 * then enters on expansion breakout direction.
 * Bandwidth = (upper - lower) / middle. Squeeze when bandwidth falls below threshold.
 */
export function ctaBollingerBandwidth(
  period = 20,
  stdDev = 2.0,
  squeezeLookback = 20,
  squeezePercentile = 0.25,
): BuiltinStrategy {
  let bandwidths: number[] = [];
  let uppers: number[] = [];
  let lowers: number[] = [];
  let middles: number[] = [];
  let squeezeLine = 0;
  let direction = 0;
  let inSqueeze = false;

  return {
    name: `BB带宽(${period},${stdDev},${squeezeLookback})`,
    init(ctx) {
      const closes = getCloses(ctx);
      const bb = bollingerBands(closes, period, stdDev);
      uppers = bb.upper;
      lowers = bb.lower;
      middles = bb.middle;

      // Compute bandwidth series
      const n = closes.length;
      bandwidths = new Array<number>(n).fill(NaN);
      for (let i = 0; i < n; i++) {
        const mid = middles[i]!;
        if (Number.isNaN(mid) || mid === 0) continue;
        bandwidths[i] = (uppers[i]! - lowers[i]!) / mid;
      }

      // Compute squeeze threshold as percentile of recent bandwidths
      const validBw = bandwidths.filter((v) => !Number.isNaN(v));
      if (validBw.length > 0) {
        validBw.sort((a, b) => a - b);
        const idx = Math.floor(validBw.length * squeezePercentile);
        squeezeLine = validBw[Math.min(idx, validBw.length - 1)]!;
      }
      direction = 0;
      inSqueeze = false;
    },
    next(ctx) {
      const i = ctx.index;
      if (i < squeezeLookback) return null;
      const bw = bandwidths[i]!;
      const bwPrev = bandwidths[i - 1]!;
      if (Number.isNaN(bw) || Number.isNaN(bwPrev)) return null;

      const bar = ctx.bars[i]!;
      const upper = uppers[i]!;
      const lower = lowers[i]!;
      const mid = middles[i]!;

      // Detect squeeze and expansion
      const wasSqueeze = inSqueeze;
      inSqueeze = bw < squeezeLine;
      const expansion = wasSqueeze && !inSqueeze;

      // Exit: price crosses back to middle band
      if (direction === 1 && bar.close < mid) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell" as const,
          price: bar.close,
          reason:
            `[BB带宽平多] 价格 ¥${bar.close.toFixed(2)} 回落到中轨` +
            ` ¥${mid.toFixed(2)}，带宽=${bw.toFixed(4)}`,
          strategyId: "bollinger-bandwidth",
        };
      }
      if (direction === -1 && bar.close > mid) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "cover" as const,
          price: bar.close,
          reason:
            `[BB带宽平空] 价格 ¥${bar.close.toFixed(2)} 反弹到中轨` +
            ` ¥${mid.toFixed(2)}，带宽=${bw.toFixed(4)}`,
          strategyId: "bollinger-bandwidth",
        };
      }

      // Entry on expansion breakout
      if (direction === 0 && expansion) {
        if (bar.close > upper) {
          direction = 1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy" as const,
            price: bar.close,
            reason:
              `[BB带宽做多] 缩口后扩张，价格 ¥${bar.close.toFixed(2)}` +
              ` 突破上轨 ¥${upper.toFixed(2)}，带宽=${bw.toFixed(4)}`,
            strategyId: "bollinger-bandwidth",
          };
        }
        if (bar.close < lower) {
          direction = -1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "short" as const,
            price: bar.close,
            reason:
              `[BB带宽做空] 缩口后扩张，价格 ¥${bar.close.toFixed(2)}` +
              ` 突破下轨 ¥${lower.toFixed(2)}，带宽=${bw.toFixed(4)}`,
            strategyId: "bollinger-bandwidth",
          };
        }
      }

      return null;
    },
  };
}
