/**
 * CTA trend-following strategies.
 *
 *   - ctaTrendFollowing:    ATR-based channel breakout with trailing stop
 *   - ctaDualThrust:        Range-based opening breakout (经典 Dual Thrust)
 *   - spreadArbitrage:      Inter-period spread mean-reversion
 *   - ctaTurtleBreakout:    Donchian channel breakout (海龟交易系统)
 *   - ctaMacdTrend:         MACD histogram crossover multi-directional
 *   - ctaRsiReversal:       RSI overbought/oversold reversal
 *   - ctaBollingerBreakout: Bollinger Band breakout multi-directional
 *   - ctaKeltnerChannel:    Keltner Channel (EMA+ATR) breakout
 *
 * These strategies support both long and short positions — they emit
 * "buy"/"sell" for long trades and "short"/"cover" for short trades.
 */
import type { BuiltinStrategy } from "./indicators.js";
import {
  atr,
  bollingerBands,
  ema,
  getCloses,
  macd,
  rollingHighLow,
  rsi,
  sma,
} from "./indicators.js";

// ── CTA Trend Following (ATR Channel Breakout) ──────────────────

/**
 * Classic CTA trend-following strategy using ATR channels.
 *
 * Entry:
 *   - Long:  close > MA(trendPeriod) + atrMultiplier × ATR  → buy
 *   - Short: close < MA(trendPeriod) - atrMultiplier × ATR  → short
 *
 * Exit:
 *   - Stop-loss: trailAtrMultiplier × ATR trailing stop
 *   - Reversal: opposite channel breakout
 *
 * Suitable for trending futures markets (metals, energy, financials).
 */
export function ctaTrendFollowing(
  trendPeriod = 20,
  atrPeriod = 14,
  atrMultiplier = 2.0,
  trailAtrMultiplier = 1.5,
): BuiltinStrategy {
  let ma: number[] = [];
  let atrValues: number[] = [];
  let trailingStop = 0;
  // 1 = long, -1 = short, 0 = flat
  let direction = 0;

  return {
    name: `CTA趋势跟踪(MA${trendPeriod},ATR${atrPeriod}×${atrMultiplier})`,
    init(ctx) {
      const closes = getCloses(ctx);
      ma = sma(closes, trendPeriod);
      atrValues = atr(ctx.bars, atrPeriod);
      direction = 0;
      trailingStop = 0;
    },
    next(ctx) {
      const i = ctx.index;
      if (Number.isNaN(ma[i]!) || Number.isNaN(atrValues[i]!)) return null;

      const bar = ctx.bars[i]!;
      const curMa = ma[i]!;
      const curAtr = atrValues[i]!;
      const upperChannel = curMa + atrMultiplier * curAtr;
      const lowerChannel = curMa - atrMultiplier * curAtr;

      // Update trailing stop
      if (direction === 1) {
        const newStop = bar.close - trailAtrMultiplier * curAtr;
        trailingStop = Math.max(trailingStop, newStop);
      } else if (direction === -1) {
        const newStop = bar.close + trailAtrMultiplier * curAtr;
        trailingStop = trailingStop === 0 ? newStop : Math.min(trailingStop, newStop);
      }

      // Check trailing stop first
      if (direction === 1 && bar.close < trailingStop) {
        const stopPrice = trailingStop;
        direction = 0;
        trailingStop = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell" as const,
          price: bar.close,
          reason: `[CTA平多] 触发移动止损 ¥${stopPrice.toFixed(2)}，ATR=${curAtr.toFixed(2)}`,
          strategyId: "cta-trend",
        };
      }
      if (direction === -1 && bar.close > trailingStop) {
        const stopPrice = trailingStop;
        direction = 0;
        trailingStop = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "cover" as const,
          price: bar.close,
          reason: `[CTA平空] 触发移动止损 ¥${stopPrice.toFixed(2)}，ATR=${curAtr.toFixed(2)}`,
          strategyId: "cta-trend",
        };
      }

      // Long entry: breakout above upper channel
      if (direction <= 0 && bar.close > upperChannel) {
        if (direction === -1) {
          // Close short first, then open long — emit cover signal
          // Next bar will pick up the long if still breaking out
          direction = 0;
          trailingStop = 0;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "cover" as const,
            price: bar.close,
            reason: `[CTA反手] 价格突破上通道 ¥${upperChannel.toFixed(2)}，先平空`,
            strategyId: "cta-trend",
          };
        }
        direction = 1;
        trailingStop = bar.close - trailAtrMultiplier * curAtr;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy" as const,
          price: bar.close,
          reason:
            `[CTA做多] 价格 ¥${bar.close.toFixed(2)} 突破上通道 ` +
            `MA${trendPeriod}+${atrMultiplier}ATR(¥${upperChannel.toFixed(2)})`,
          strategyId: "cta-trend",
        };
      }

      // Short entry: breakout below lower channel
      if (direction >= 0 && bar.close < lowerChannel) {
        if (direction === 1) {
          direction = 0;
          trailingStop = 0;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "sell" as const,
            price: bar.close,
            reason: `[CTA反手] 价格跌破下通道 ¥${lowerChannel.toFixed(2)}，先平多`,
            strategyId: "cta-trend",
          };
        }
        direction = -1;
        trailingStop = bar.close + trailAtrMultiplier * curAtr;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "short" as const,
          price: bar.close,
          reason:
            `[CTA做空] 价格 ¥${bar.close.toFixed(2)} 跌破下通道 ` +
            `MA${trendPeriod}-${atrMultiplier}ATR(¥${lowerChannel.toFixed(2)})`,
          strategyId: "cta-trend",
        };
      }

      return null;
    },
  };
}

// ── Dual Thrust (Opening Range Breakout) ─────────────────────────

/**
 * Classic Dual Thrust strategy — popular in Chinese futures markets.
 *
 * 1. Compute N-day range: highN-day high, lowN-day low, closeN-day close
 * 2. Range = max(highN - closeN, closeN - lowN)
 * 3. Upper trigger = open + k1 × Range
 *    Lower trigger = open - k2 × Range
 * 4. Intraday: buy when price > upper, short when price < lower
 *
 * In daily-bar mode, approximated by comparing close to triggers derived
 * from the previous day's open and the N-day range.
 */
export function ctaDualThrust(lookback = 4, k1 = 0.5, k2 = 0.5): BuiltinStrategy {
  let nDayHigh: number[] = [];
  let nDayLow: number[] = [];
  let direction = 0; // 1=long, -1=short, 0=flat

  return {
    name: `DualThrust(N=${lookback},K1=${k1},K2=${k2})`,
    init(ctx) {
      const hl = rollingHighLow(ctx.bars, lookback);
      nDayHigh = hl.highs;
      nDayLow = hl.lows;
      direction = 0;
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1 || Number.isNaN(nDayHigh[i]!) || Number.isNaN(nDayLow[i]!)) return null;

      const bar = ctx.bars[i]!;
      const prevBar = ctx.bars[i - 1]!;
      const rangeHigh = nDayHigh[i]!;
      const rangeLow = nDayLow[i]!;
      const prevClose = prevBar.close;

      // Range = max(high - close, close - low) using N-day extremes
      const range = Math.max(rangeHigh - prevClose, prevClose - rangeLow);
      if (range <= 0) return null;

      const upperTrigger = bar.open + k1 * range;
      const lowerTrigger = bar.open - k2 * range;

      // Check exit first
      if (direction === 1 && bar.close < lowerTrigger) {
        // Long exit + short entry
        direction = -1;
        return [
          {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "sell" as const,
            price: bar.close,
            reason: `[DT平多] 价格跌破下轨 ¥${lowerTrigger.toFixed(2)}`,
            strategyId: "dual-thrust",
          },
          {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "short" as const,
            price: bar.close,
            reason:
              `[DT做空] 价格 ¥${bar.close.toFixed(2)} < 下轨 ¥${lowerTrigger.toFixed(2)}，` +
              `Range=${range.toFixed(2)}`,
            strategyId: "dual-thrust",
          },
        ];
      }

      if (direction === -1 && bar.close > upperTrigger) {
        // Short exit + long entry
        direction = 1;
        return [
          {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "cover" as const,
            price: bar.close,
            reason: `[DT平空] 价格突破上轨 ¥${upperTrigger.toFixed(2)}`,
            strategyId: "dual-thrust",
          },
          {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy" as const,
            price: bar.close,
            reason:
              `[DT做多] 价格 ¥${bar.close.toFixed(2)} > 上轨 ¥${upperTrigger.toFixed(2)}，` +
              `Range=${range.toFixed(2)}`,
            strategyId: "dual-thrust",
          },
        ];
      }

      // New entries when flat
      if (direction === 0) {
        if (bar.close > upperTrigger) {
          direction = 1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy" as const,
            price: bar.close,
            reason:
              `[DT做多] 价格 ¥${bar.close.toFixed(2)} 突破上轨 ¥${upperTrigger.toFixed(2)}，` +
              `Range=${range.toFixed(2)}`,
            strategyId: "dual-thrust",
          };
        }
        if (bar.close < lowerTrigger) {
          direction = -1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "short" as const,
            price: bar.close,
            reason:
              `[DT做空] 价格 ¥${bar.close.toFixed(2)} 跌破下轨 ¥${lowerTrigger.toFixed(2)}，` +
              `Range=${range.toFixed(2)}`,
            strategyId: "dual-thrust",
          };
        }
      }

      return null;
    },
  };
}

// ── Spread Arbitrage (Cross-period mean reversion) ───────────────

/**
 * Cross-period spread strategy for futures.
 *
 * Works in backtest mode with a single symbol's bars, where the "spread"
 * is approximated by the price deviation from its N-period moving average
 * (a proxy for the near-far month basis).
 *
 * When the spread (price - MA) exceeds `entryStdDevs` standard deviations
 * from its own mean, enter a mean-reversion position:
 *   - Spread too high → short (expect convergence)
 *   - Spread too low  → buy  (expect convergence)
 * Exit when spread returns within `exitStdDevs` (default 0.5).
 *
 * For real dual-leg spread trading, use the live engine with two symbols.
 */
export function spreadArbitrage(
  maPeriod = 20,
  entryStdDevs = 2.0,
  exitStdDevs = 0.5,
): BuiltinStrategy {
  let maValues: number[] = [];
  let direction = 0; // 1=long spread, -1=short spread
  // Welford online statistics for O(1) per-bar mean/std computation
  let wCount = 0;
  let wMean = 0;
  let wM2 = 0;

  return {
    name: `价差套利(MA${maPeriod},入场${entryStdDevs}σ,出场${exitStdDevs}σ)`,
    init(ctx) {
      const closes = getCloses(ctx);
      maValues = ema(closes, maPeriod);
      direction = 0;
      wCount = 0;
      wMean = 0;
      wM2 = 0;
    },
    next(ctx) {
      const i = ctx.index;
      if (Number.isNaN(maValues[i]!) || i < maPeriod) return null;

      const bar = ctx.bars[i]!;
      const spread = bar.close - maValues[i]!;

      // Welford online update: O(1) per bar instead of O(n)
      wCount++;
      const delta = spread - wMean;
      wMean += delta / wCount;
      const delta2 = spread - wMean;
      wM2 += delta * delta2;

      if (wCount < 2) return null;

      const spreadMean = wMean;
      const spreadStd = Math.sqrt(wM2 / wCount);
      if (spreadStd <= 0) return null;

      const zScore = (spread - spreadMean) / spreadStd;

      // Exit conditions
      if (direction === 1 && zScore >= -exitStdDevs) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell" as const,
          price: bar.close,
          reason: `[套利平多] 价差Z=${zScore.toFixed(2)} 回归至 ` + `${exitStdDevs}σ 范围内`,
          strategyId: "spread-arbitrage",
        };
      }
      if (direction === -1 && zScore <= exitStdDevs) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "cover" as const,
          price: bar.close,
          reason: `[套利平空] 价差Z=${zScore.toFixed(2)} 回归至 ` + `${exitStdDevs}σ 范围内`,
          strategyId: "spread-arbitrage",
        };
      }

      // Entry conditions
      if (direction === 0) {
        if (zScore < -entryStdDevs) {
          // Spread is abnormally low → long (expect convergence upward)
          direction = 1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy" as const,
            price: bar.close,
            reason:
              `[套利做多] 价差Z=${zScore.toFixed(2)} < -${entryStdDevs}σ，` +
              `预期均值回归（价差=¥${spread.toFixed(2)}）`,
            strategyId: "spread-arbitrage",
          };
        }
        if (zScore > entryStdDevs) {
          // Spread is abnormally high → short (expect convergence downward)
          direction = -1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "short" as const,
            price: bar.close,
            reason:
              `[套利做空] 价差Z=${zScore.toFixed(2)} > ${entryStdDevs}σ，` +
              `预期均值回归（价差=¥${spread.toFixed(2)}）`,
            strategyId: "spread-arbitrage",
          };
        }
      }

      return null;
    },
  };
}

// ── Turtle Breakout (海龟交易系统) ───────────────────────────────

/**
 * Classic Turtle Trading System using Donchian channels.
 *
 * Entry:
 *   - Long:  close > max(high of last entryPeriod bars) → buy
 *   - Short: close < min(low of last entryPeriod bars) → short
 *
 * Exit:
 *   - Long exit:  close < min(low of last exitPeriod bars)  → sell
 *   - Short exit: close > max(high of last exitPeriod bars) → cover
 *
 * Position sizing is handled externally; this strategy only emits signals.
 * The original Turtle system used 20-day entry / 10-day exit (System 1)
 * and 55-day entry / 20-day exit (System 2).
 */
export function ctaTurtleBreakout(entryPeriod = 20, exitPeriod = 10): BuiltinStrategy {
  let entryHighs: number[] = [];
  let entryLows: number[] = [];
  let exitHighs: number[] = [];
  let exitLows: number[] = [];
  let direction = 0; // 1=long, -1=short, 0=flat

  return {
    name: `海龟突破(入场${entryPeriod}日,出场${exitPeriod}日)`,
    init(ctx) {
      const entryHL = rollingHighLow(ctx.bars, entryPeriod);
      entryHighs = entryHL.highs;
      entryLows = entryHL.lows;
      const exitHL = rollingHighLow(ctx.bars, exitPeriod);
      exitHighs = exitHL.highs;
      exitLows = exitHL.lows;
      direction = 0;
    },
    next(ctx) {
      const i = ctx.index;
      if (
        Number.isNaN(entryHighs[i]!) ||
        Number.isNaN(entryLows[i]!) ||
        Number.isNaN(exitHighs[i]!) ||
        Number.isNaN(exitLows[i]!)
      )
        return null;

      const bar = ctx.bars[i]!;
      const entryHigh = entryHighs[i]!;
      const entryLow = entryLows[i]!;
      const exitHigh = exitHighs[i]!;
      const exitLow = exitLows[i]!;

      // Exit first
      if (direction === 1 && bar.close < exitLow) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell" as const,
          price: bar.close,
          reason: `[海龟平多] 价格 ¥${bar.close.toFixed(2)} 跌破${exitPeriod}日最低 ¥${exitLow.toFixed(2)}`,
          strategyId: "turtle-breakout",
        };
      }
      if (direction === -1 && bar.close > exitHigh) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "cover" as const,
          price: bar.close,
          reason: `[海龟平空] 价格 ¥${bar.close.toFixed(2)} 突破${exitPeriod}日最高 ¥${exitHigh.toFixed(2)}`,
          strategyId: "turtle-breakout",
        };
      }

      // Entry: only when flat
      if (direction === 0) {
        if (bar.close > entryHigh) {
          direction = 1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy" as const,
            price: bar.close,
            reason: `[海龟做多] 价格 ¥${bar.close.toFixed(2)} 突破${entryPeriod}日最高 ¥${entryHigh.toFixed(2)}`,
            strategyId: "turtle-breakout",
          };
        }
        if (bar.close < entryLow) {
          direction = -1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "short" as const,
            price: bar.close,
            reason: `[海龟做空] 价格 ¥${bar.close.toFixed(2)} 跌破${entryPeriod}日最低 ¥${entryLow.toFixed(2)}`,
            strategyId: "turtle-breakout",
          };
        }
      }

      return null;
    },
  };
}

// ── MACD Trend (多空版) ───────────────────────────────────────────

/**
 * MACD histogram-based long/short strategy.
 *
 * Entry:
 *   - Long:  histogram crosses above 0 (MACD > signal) → buy
 *   - Short: histogram crosses below 0 (MACD < signal) → short
 *
 * Exit:
 *   - Long exit:  histogram crosses below 0 → sell (then may short)
 *   - Short exit: histogram crosses above 0 → cover (then may buy)
 *
 * Uses zero-line crossover of histogram (= MACD/signal crossover)
 * to generate trend signals, suitable for trending futures.
 */
export function ctaMacdTrend(fastPeriod = 12, slowPeriod = 26, signalPeriod = 9): BuiltinStrategy {
  let histogram: number[] = [];
  let direction = 0; // 1=long, -1=short, 0=flat

  return {
    name: `MACD多空(${fastPeriod},${slowPeriod},${signalPeriod})`,
    init(ctx) {
      const closes = getCloses(ctx);
      const m = macd(closes, fastPeriod, slowPeriod, signalPeriod);
      histogram = m.histogram;
      direction = 0;
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1 || Number.isNaN(histogram[i]!) || Number.isNaN(histogram[i - 1]!)) return null;

      const bar = ctx.bars[i]!;
      const curHist = histogram[i]!;
      const prevHist = histogram[i - 1]!;

      // Cross above zero: histogram goes from <=0 to >0
      if (prevHist <= 0 && curHist > 0) {
        if (direction === -1) {
          // Cover short, then long
          direction = 1;
          return [
            {
              timestamp: bar.timestamp,
              symbol: "",
              direction: "cover" as const,
              price: bar.close,
              reason: `[MACD平空] 柱状线由负转正，MACD金叉`,
              strategyId: "macd-trend",
            },
            {
              timestamp: bar.timestamp,
              symbol: "",
              direction: "buy" as const,
              price: bar.close,
              reason:
                `[MACD做多] 柱状线=${curHist.toFixed(4)} 上穿零轴，` +
                `价格 ¥${bar.close.toFixed(2)}`,
              strategyId: "macd-trend",
            },
          ];
        }
        if (direction === 0) {
          direction = 1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy" as const,
            price: bar.close,
            reason:
              `[MACD做多] 柱状线=${curHist.toFixed(4)} 上穿零轴，` +
              `价格 ¥${bar.close.toFixed(2)}`,
            strategyId: "macd-trend",
          };
        }
      }

      // Cross below zero: histogram goes from >=0 to <0
      if (prevHist >= 0 && curHist < 0) {
        if (direction === 1) {
          // Sell long, then short
          direction = -1;
          return [
            {
              timestamp: bar.timestamp,
              symbol: "",
              direction: "sell" as const,
              price: bar.close,
              reason: `[MACD平多] 柱状线由正转负，MACD死叉`,
              strategyId: "macd-trend",
            },
            {
              timestamp: bar.timestamp,
              symbol: "",
              direction: "short" as const,
              price: bar.close,
              reason:
                `[MACD做空] 柱状线=${curHist.toFixed(4)} 下穿零轴，` +
                `价格 ¥${bar.close.toFixed(2)}`,
              strategyId: "macd-trend",
            },
          ];
        }
        if (direction === 0) {
          direction = -1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "short" as const,
            price: bar.close,
            reason:
              `[MACD做空] 柱状线=${curHist.toFixed(4)} 下穿零轴，` +
              `价格 ¥${bar.close.toFixed(2)}`,
            strategyId: "macd-trend",
          };
        }
      }

      return null;
    },
  };
}

// ── RSI Reversal (多空反转版) ─────────────────────────────────────

/**
 * RSI overbought/oversold reversal strategy for futures.
 *
 * Entry:
 *   - Long:  RSI drops below oversold, then crosses back above → buy
 *   - Short: RSI rises above overbought, then crosses back below → short
 *
 * Exit:
 *   - Long exit:  RSI reaches overbought → sell
 *   - Short exit: RSI reaches oversold   → cover
 *
 * The "cross back" logic avoids catching a falling knife: we wait for RSI
 * to actually turn from extremes before entering.
 */
export function ctaRsiReversal(period = 14, overbought = 70, oversold = 30): BuiltinStrategy {
  let rsiValues: number[] = [];
  let direction = 0; // 1=long, -1=short, 0=flat
  let wasOversold = false;
  let wasOverbought = false;

  return {
    name: `RSI反转(${period},超买${overbought},超卖${oversold})`,
    init(ctx) {
      const closes = getCloses(ctx);
      rsiValues = rsi(closes, period);
      direction = 0;
      wasOversold = false;
      wasOverbought = false;
    },
    next(ctx) {
      const i = ctx.index;
      if (Number.isNaN(rsiValues[i]!)) return null;

      const bar = ctx.bars[i]!;
      const curRsi = rsiValues[i]!;

      // Track if we've been in extreme zones
      if (curRsi < oversold) wasOversold = true;
      if (curRsi > overbought) wasOverbought = true;

      // Exit conditions first
      if (direction === 1 && curRsi >= overbought) {
        direction = 0;
        wasOverbought = true;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell" as const,
          price: bar.close,
          reason: `[RSI平多] RSI=${curRsi.toFixed(1)} 达到超买区(${overbought})`,
          strategyId: "rsi-reversal",
        };
      }
      if (direction === -1 && curRsi <= oversold) {
        direction = 0;
        wasOversold = true;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "cover" as const,
          price: bar.close,
          reason: `[RSI平空] RSI=${curRsi.toFixed(1)} 达到超卖区(${oversold})`,
          strategyId: "rsi-reversal",
        };
      }

      // Entry conditions: require previous "was in extreme" + cross back
      if (direction === 0) {
        // Long: RSI was below oversold, now crosses back above
        if (wasOversold && curRsi > oversold) {
          wasOversold = false;
          direction = 1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy" as const,
            price: bar.close,
            reason:
              `[RSI做多] RSI=${curRsi.toFixed(1)} 从超卖区反弹，` + `价格 ¥${bar.close.toFixed(2)}`,
            strategyId: "rsi-reversal",
          };
        }
        // Short: RSI was above overbought, now crosses back below
        if (wasOverbought && curRsi < overbought) {
          wasOverbought = false;
          direction = -1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "short" as const,
            price: bar.close,
            reason:
              `[RSI做空] RSI=${curRsi.toFixed(1)} 从超买区回落，` + `价格 ¥${bar.close.toFixed(2)}`,
            strategyId: "rsi-reversal",
          };
        }
      }

      return null;
    },
  };
}

// ── Bollinger Band Breakout (多空版) ──────────────────────────────

/**
 * Bollinger Band breakout strategy for futures.
 *
 * Entry:
 *   - Long:  close breaks above upper band → buy
 *   - Short: close breaks below lower band → short
 *
 * Exit:
 *   - Long exit:  close crosses back below middle band → sell
 *   - Short exit: close crosses back above middle band → cover
 *
 * Momentum-based: assumes breakouts from the bands signal trend
 * continuation. Uses middle band (MA) as the exit/reversal reference.
 */
export function ctaBollingerBreakout(period = 20, stdDevMultiplier = 2.0): BuiltinStrategy {
  let middle: number[] = [];
  let upper: number[] = [];
  let lower: number[] = [];
  let direction = 0; // 1=long, -1=short, 0=flat

  return {
    name: `布林突破(${period},${stdDevMultiplier}σ)`,
    init(ctx) {
      const closes = getCloses(ctx);
      const bb = bollingerBands(closes, period, stdDevMultiplier);
      middle = bb.middle;
      upper = bb.upper;
      lower = bb.lower;
      direction = 0;
    },
    next(ctx) {
      const i = ctx.index;
      if (Number.isNaN(middle[i]!) || Number.isNaN(upper[i]!) || Number.isNaN(lower[i]!))
        return null;

      const bar = ctx.bars[i]!;
      const curMiddle = middle[i]!;
      const curUpper = upper[i]!;
      const curLower = lower[i]!;

      // Exit conditions
      if (direction === 1 && bar.close < curMiddle) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell" as const,
          price: bar.close,
          reason:
            `[布林平多] 价格 ¥${bar.close.toFixed(2)} 回落至中轨下方 ` +
            `MA=${curMiddle.toFixed(2)}`,
          strategyId: "bollinger-breakout",
        };
      }
      if (direction === -1 && bar.close > curMiddle) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "cover" as const,
          price: bar.close,
          reason:
            `[布林平空] 价格 ¥${bar.close.toFixed(2)} 反弹至中轨上方 ` +
            `MA=${curMiddle.toFixed(2)}`,
          strategyId: "bollinger-breakout",
        };
      }

      // Entry conditions
      if (direction === 0) {
        if (bar.close > curUpper) {
          direction = 1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy" as const,
            price: bar.close,
            reason:
              `[布林做多] 价格 ¥${bar.close.toFixed(2)} 突破上轨 ` +
              `¥${curUpper.toFixed(2)}，波动率扩张`,
            strategyId: "bollinger-breakout",
          };
        }
        if (bar.close < curLower) {
          direction = -1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "short" as const,
            price: bar.close,
            reason:
              `[布林做空] 价格 ¥${bar.close.toFixed(2)} 跌破下轨 ` +
              `¥${curLower.toFixed(2)}，波动率扩张`,
            strategyId: "bollinger-breakout",
          };
        }
      }

      return null;
    },
  };
}

// ── Keltner Channel Breakout ─────────────────────────────────────

/**
 * Keltner Channel breakout strategy for futures.
 *
 * Like Bollinger Bands but uses ATR instead of standard deviation
 * for channel width, making it smoother and less sensitive to spikes.
 *
 * Channel:
 *   - Middle: EMA(close, emaPeriod)
 *   - Upper:  middle + atrMultiplier × ATR(atrPeriod)
 *   - Lower:  middle - atrMultiplier × ATR(atrPeriod)
 *
 * Entry:
 *   - Long:  close > upper channel → buy
 *   - Short: close < lower channel → short
 *
 * Exit:
 *   - Long exit:  close crosses back below middle → sell
 *   - Short exit: close crosses back above middle → cover
 */
export function ctaKeltnerChannel(
  emaPeriod = 20,
  atrPeriod = 14,
  atrMultiplier = 2.0,
): BuiltinStrategy {
  let emaValues: number[] = [];
  let atrValues: number[] = [];
  let direction = 0; // 1=long, -1=short, 0=flat

  return {
    name: `肯特纳通道(EMA${emaPeriod},ATR${atrPeriod}×${atrMultiplier})`,
    init(ctx) {
      const closes = getCloses(ctx);
      emaValues = ema(closes, emaPeriod);
      atrValues = atr(ctx.bars, atrPeriod);
      direction = 0;
    },
    next(ctx) {
      const i = ctx.index;
      if (Number.isNaN(emaValues[i]!) || Number.isNaN(atrValues[i]!)) return null;

      const bar = ctx.bars[i]!;
      const mid = emaValues[i]!;
      const curAtr = atrValues[i]!;
      const upperChannel = mid + atrMultiplier * curAtr;
      const lowerChannel = mid - atrMultiplier * curAtr;

      // Exit conditions
      if (direction === 1 && bar.close < mid) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell" as const,
          price: bar.close,
          reason: `[肯特纳平多] 价格 ¥${bar.close.toFixed(2)} 跌回中轨 EMA=${mid.toFixed(2)}`,
          strategyId: "keltner-channel",
        };
      }
      if (direction === -1 && bar.close > mid) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "cover" as const,
          price: bar.close,
          reason: `[肯特纳平空] 价格 ¥${bar.close.toFixed(2)} 涨回中轨 EMA=${mid.toFixed(2)}`,
          strategyId: "keltner-channel",
        };
      }

      // Entry conditions
      if (direction === 0) {
        if (bar.close > upperChannel) {
          direction = 1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy" as const,
            price: bar.close,
            reason:
              `[肯特纳做多] 价格 ¥${bar.close.toFixed(2)} 突破上通道 ` +
              `¥${upperChannel.toFixed(2)} (EMA+${atrMultiplier}×ATR)`,
            strategyId: "keltner-channel",
          };
        }
        if (bar.close < lowerChannel) {
          direction = -1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "short" as const,
            price: bar.close,
            reason:
              `[肯特纳做空] 价格 ¥${bar.close.toFixed(2)} 跌破下通道 ` +
              `¥${lowerChannel.toFixed(2)} (EMA-${atrMultiplier}×ATR)`,
            strategyId: "keltner-channel",
          };
        }
      }

      return null;
    },
  };
}
