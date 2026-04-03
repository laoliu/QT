/**
 * Basic builtin strategies — fundamental building blocks.
 *
 *   - DualMA:              Fast/slow MA crossover (golden/death cross)
 *   - MomentumBreakout:    N-day high breakout buy, N-day low breakdown sell
 *   - MeanReversion:       Bollinger Band mean-reversion (buy lower, sell upper)
 *   - RSI Filter:          RSI oversold buy, overbought sell
 *   - RSI Enhanced:        RSI + trend + volume + ATR trailing stop
 *   - MACD Confirmation:   MACD histogram crossover with signal line
 *   - MACD Divergence:     Top/bottom divergence detection
 *   - Volume Confirmation: Volume breakout confirming price movement
 */
import type { BuiltinStrategy } from "./indicators.js";
import {
  atr,
  bollingerBands,
  getCloses,
  macd,
  obv,
  rollingHighLow,
  rsi,
  sma,
} from "./indicators.js";

// ── Dual Moving Average Crossover ────────────────────────────────

/**
 * Buy when fast MA crosses above slow MA; sell on cross below.
 */
export function dualMaCrossover(fastPeriod = 10, slowPeriod = 30): BuiltinStrategy {
  let fastMa: number[] = [];
  let slowMa: number[] = [];

  return {
    name: `DualMA(${fastPeriod},${slowPeriod})`,
    init(ctx) {
      const closes = getCloses(ctx);
      fastMa = sma(closes, fastPeriod);
      slowMa = sma(closes, slowPeriod);
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1 || Number.isNaN(fastMa[i]!) || Number.isNaN(slowMa[i]!)) return null;

      const bar = ctx.bars[i]!;
      // Golden cross
      if (fastMa[i]! > slowMa[i]! && fastMa[i - 1]! <= slowMa[i - 1]! && ctx.position <= 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `Fast MA (${fastMa[i]!.toFixed(2)}) crossed above Slow MA (${slowMa[i]!.toFixed(2)})`,
          strategyId: "dual-ma-crossover",
        };
      }
      // Death cross
      if (fastMa[i]! < slowMa[i]! && fastMa[i - 1]! >= slowMa[i - 1]! && ctx.position > 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell",
          price: bar.close,
          reason: `Fast MA (${fastMa[i]!.toFixed(2)}) crossed below Slow MA (${slowMa[i]!.toFixed(2)})`,
          strategyId: "dual-ma-crossover",
        };
      }
      return null;
    },
  };
}

// ── Momentum Breakout ────────────────────────────────────────────

/**
 * Buy when price breaks N-day high; sell when price breaks N-day low.
 */
export function momentumBreakout(lookback = 20): BuiltinStrategy {
  let highs: number[] = [];
  let lows: number[] = [];

  return {
    name: `MomentumBreakout(${lookback})`,
    init(ctx) {
      const hl = rollingHighLow(ctx.bars, lookback);
      highs = hl.highs;
      lows = hl.lows;
    },
    next(ctx) {
      const i = ctx.index;
      if (Number.isNaN(highs[i]!) || Number.isNaN(lows[i]!)) return null;

      const bar = ctx.bars[i]!;

      if (bar.close > highs[i]! && ctx.position <= 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `Price ${bar.close.toFixed(2)} broke above ${lookback}-day high ${highs[i]!.toFixed(2)}`,
          strategyId: "momentum-breakout",
        };
      }

      if (bar.close < lows[i]! && ctx.position > 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell",
          price: bar.close,
          reason: `Price ${bar.close.toFixed(2)} broke below ${lookback}-day low ${lows[i]!.toFixed(2)}`,
          strategyId: "momentum-breakout",
        };
      }

      return null;
    },
  };
}

// ── Mean Reversion (Bollinger Band) ──────────────────────────────

/**
 * Buy at lower Bollinger Band, sell at upper band.
 */
export function meanReversion(period = 20, stdDev = 2): BuiltinStrategy {
  let upper: number[] = [];
  let lower: number[] = [];

  return {
    name: `MeanReversion(${period},${stdDev})`,
    init(ctx) {
      const closes = getCloses(ctx);
      const bb = bollingerBands(closes, period, stdDev);
      upper = bb.upper;
      lower = bb.lower;
    },
    next(ctx) {
      const i = ctx.index;
      if (Number.isNaN(upper[i]!) || Number.isNaN(lower[i]!)) return null;

      const bar = ctx.bars[i]!;

      if (bar.close < lower[i]! && ctx.position <= 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `Price ${bar.close.toFixed(2)} at/below lower Bollinger Band ${lower[i]!.toFixed(2)}`,
          strategyId: "mean-reversion",
        };
      }

      if (bar.close > upper[i]! && ctx.position > 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell",
          price: bar.close,
          reason: `Price ${bar.close.toFixed(2)} at/above upper Bollinger Band ${upper[i]!.toFixed(2)}`,
          strategyId: "mean-reversion",
        };
      }

      return null;
    },
  };
}

// ── RSI Filter ───────────────────────────────────────────────────

/**
 * Buy when RSI crosses up from oversold; sell when RSI crosses down from overbought.
 * Acts as a momentum filter — confirms that the market has enough
 * directional energy before entering.
 */
export function rsiFilter(period = 14, oversold = 30, overbought = 70): BuiltinStrategy {
  let rsiValues: number[] = [];

  return {
    name: `RSIFilter(${period},${oversold}/${overbought})`,
    init(ctx) {
      const closes = getCloses(ctx);
      rsiValues = rsi(closes, period);
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1 || Number.isNaN(rsiValues[i]!) || Number.isNaN(rsiValues[i - 1]!)) return null;

      const bar = ctx.bars[i]!;
      const curRsi = rsiValues[i]!;
      const prevRsi = rsiValues[i - 1]!;

      // Buy: RSI crosses up from oversold zone
      if (prevRsi <= oversold && curRsi > oversold && ctx.position <= 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `RSI(${period}) crossed above ${oversold} (${curRsi.toFixed(1)} ← ${prevRsi.toFixed(1)})`,
          strategyId: "rsi-filter",
        };
      }

      // Sell: RSI crosses down from overbought zone
      if (prevRsi >= overbought && curRsi < overbought && ctx.position > 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell",
          price: bar.close,
          reason: `RSI(${period}) crossed below ${overbought} (${curRsi.toFixed(1)} ← ${prevRsi.toFixed(1)})`,
          strategyId: "rsi-filter",
        };
      }

      return null;
    },
  };
}

// ── RSI Enhanced (RSI + Trend + Volume + ATR Stop) ───────────────

/**
 * Enhanced RSI strategy that adds:
 *   1. Trend filter: price must be above MA(trendMaPeriod) to buy
 *   2. Volume confirmation: volume > volMa × volMultiplier
 *   3. ATR trailing stop: dynamic stop-loss based on ATR
 *
 * Reduces "catching falling knives" by requiring trend alignment
 * and institutional participation before entering.
 */
export function rsiEnhanced(
  rsiPeriod = 14,
  oversold = 35,
  overbought = 65,
  trendMaPeriod = 20,
  volMaPeriod = 5,
  volMultiplier = 1.0,
  atrPeriod = 14,
  atrStopMultiplier = 2.0,
): BuiltinStrategy {
  let rsiValues: number[] = [];
  let trendMa: number[] = [];
  let volMa: number[] = [];
  let atrValues: number[] = [];
  let trailingStop = 0;
  let entryPrice = 0;

  return {
    name: `RSIEnhanced(${rsiPeriod},${oversold}/${overbought},MA${trendMaPeriod},ATR×${atrStopMultiplier})`,
    init(ctx) {
      const closes = getCloses(ctx);
      rsiValues = rsi(closes, rsiPeriod);
      trendMa = sma(closes, trendMaPeriod);
      const volumes = ctx.bars.map((b) => b.volume);
      volMa = sma(volumes, volMaPeriod);
      atrValues = atr(ctx.bars, atrPeriod);
      trailingStop = 0;
      entryPrice = 0;
    },
    next(ctx) {
      const i = ctx.index;
      if (
        i < 1 ||
        Number.isNaN(rsiValues[i]!) ||
        Number.isNaN(rsiValues[i - 1]!) ||
        Number.isNaN(trendMa[i]!) ||
        Number.isNaN(atrValues[i]!)
      )
        return null;

      const bar = ctx.bars[i]!;
      const curRsi = rsiValues[i]!;
      const prevRsi = rsiValues[i - 1]!;
      const curAtr = atrValues[i]!;

      // Update trailing stop for open position
      if (ctx.position > 0 && curAtr > 0) {
        const newStop = bar.close - curAtr * atrStopMultiplier;
        if (newStop > trailingStop) trailingStop = newStop;

        // Trailing stop hit → sell
        if (bar.close <= trailingStop) {
          const pnl = (((bar.close - entryPrice) / entryPrice) * 100).toFixed(1);
          trailingStop = 0;
          entryPrice = 0;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "sell" as const,
            price: bar.close,
            reason: `ATR trailing stop hit (stop=${trailingStop.toFixed(2)}, PnL=${pnl}%)`,
            strategyId: "rsi-enhanced",
          };
        }
      }

      // Buy conditions:
      //   1. RSI crosses up from oversold (required)
      //   2. At least one supporting signal: trend MA rising OR volume above average
      //   ATR trailing stop is the primary risk protection, so entry filters are OR-based
      const rsiCrossUp = prevRsi <= oversold && curRsi > oversold;
      const maSlopeLen = 5;
      const maRising =
        i >= maSlopeLen &&
        !Number.isNaN(trendMa[i - maSlopeLen]!) &&
        trendMa[i]! > trendMa[i - maSlopeLen]!;
      const avgVol = volMa[i] ?? 0;
      const highVolume = avgVol > 0 && bar.volume > avgVol * volMultiplier;
      const hasSupport = maRising || highVolume;

      if (rsiCrossUp && hasSupport && ctx.position <= 0) {
        entryPrice = bar.close;
        trailingStop = bar.close - curAtr * atrStopMultiplier;
        const volRatio = avgVol > 0 ? (bar.volume / avgVol).toFixed(1) : "?";
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy" as const,
          price: bar.close,
          reason:
            `RSI(${rsiPeriod}) ${prevRsi.toFixed(1)}→${curRsi.toFixed(1)} ` +
            `${maRising ? `MA${trendMaPeriod}↑ ` : ""}` +
            `${highVolume ? `vol ${volRatio}× avg` : ""}`,
          strategyId: "rsi-enhanced",
        };
      }

      // Sell: RSI enters overbought (take profit)
      if (prevRsi >= overbought && curRsi < overbought && ctx.position > 0) {
        const pnl =
          entryPrice > 0 ? (((bar.close - entryPrice) / entryPrice) * 100).toFixed(1) : "?";
        trailingStop = 0;
        entryPrice = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell" as const,
          price: bar.close,
          reason: `RSI(${rsiPeriod}) overbought exit ${curRsi.toFixed(1)} (PnL=${pnl}%)`,
          strategyId: "rsi-enhanced",
        };
      }

      return null;
    },
  };
}

// ── MACD Confirmation ────────────────────────────────────────────

/**
 * Buy when MACD histogram turns positive (bullish momentum);
 * sell when histogram turns negative (bearish momentum).
 * Confirms that underlying momentum supports the trade direction.
 */
export function macdConfirmation(
  fastPeriod = 12,
  slowPeriod = 26,
  signalPeriod = 9,
): BuiltinStrategy {
  let histogram: number[] = [];

  return {
    name: `MACDConfirm(${fastPeriod},${slowPeriod},${signalPeriod})`,
    init(ctx) {
      const closes = getCloses(ctx);
      const result = macd(closes, fastPeriod, slowPeriod, signalPeriod);
      histogram = result.histogram;
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1 || Number.isNaN(histogram[i]!) || Number.isNaN(histogram[i - 1]!)) return null;

      const bar = ctx.bars[i]!;
      const curHist = histogram[i]!;
      const prevHist = histogram[i - 1]!;

      // Buy: histogram turns positive (bearish → bullish)
      if (prevHist <= 0 && curHist > 0 && ctx.position <= 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `MACD histogram turned positive (${curHist.toFixed(3)} ← ${prevHist.toFixed(3)})`,
          strategyId: "macd-confirmation",
        };
      }

      // Sell: histogram turns negative (bullish → bearish)
      if (prevHist >= 0 && curHist < 0 && ctx.position > 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell",
          price: bar.close,
          reason: `MACD histogram turned negative (${curHist.toFixed(3)} ← ${prevHist.toFixed(3)})`,
          strategyId: "macd-confirmation",
        };
      }

      return null;
    },
  };
}

// ── MACD Divergence ──────────────────────────────────────────────

/**
 * Detects MACD top/bottom divergence for counter-trend entry.
 *
 * Bottom divergence (bullish): price makes a lower low but MACD makes a higher low
 *   → exhaustion of selling pressure, likely reversal up
 *
 * Top divergence (bearish): price makes a higher high but MACD makes a lower high
 *   → exhaustion of buying pressure, likely reversal down
 *
 * Lookback window scans the last N bars for swing highs/lows.
 */
export function macdDivergence(
  fastPeriod = 12,
  slowPeriod = 26,
  signalPeriod = 9,
  lookback = 60,
  swingWindow = 5,
): BuiltinStrategy {
  let macdLine: number[] = [];
  let histogram: number[] = [];

  /** Find local minima indices within [start..end] */
  function findSwingLows(data: number[], start: number, end: number, win: number): number[] {
    const lows: number[] = [];
    for (let j = start + win; j <= end - win; j++) {
      let isLow = true;
      for (let k = 1; k <= win; k++) {
        if (data[j]! >= data[j - k]! || data[j]! >= data[j + k]!) {
          isLow = false;
          break;
        }
      }
      if (isLow) lows.push(j);
    }
    return lows;
  }

  /** Find local maxima indices within [start..end] */
  function findSwingHighs(data: number[], start: number, end: number, win: number): number[] {
    const highs: number[] = [];
    for (let j = start + win; j <= end - win; j++) {
      let isHigh = true;
      for (let k = 1; k <= win; k++) {
        if (data[j]! <= data[j - k]! || data[j]! <= data[j + k]!) {
          isHigh = false;
          break;
        }
      }
      if (isHigh) highs.push(j);
    }
    return highs;
  }

  return {
    name: `MACDDivergence(${fastPeriod},${slowPeriod},${signalPeriod},lb=${lookback})`,
    init(ctx) {
      const closes = getCloses(ctx);
      const result = macd(closes, fastPeriod, slowPeriod, signalPeriod);
      macdLine = result.macdLine;
      histogram = result.histogram;
    },
    next(ctx) {
      const i = ctx.index;
      if (i < lookback + swingWindow || Number.isNaN(macdLine[i]!)) return null;

      const bar = ctx.bars[i]!;
      const closes = ctx.bars.map((b) => b.close);
      const rangeStart = i - lookback;

      // Also require histogram to confirm direction change
      const histTurning =
        !Number.isNaN(histogram[i]!) && !Number.isNaN(histogram[i - 1]!) && histogram[i]! !== 0;

      // ── Bottom divergence (buy signal) ──
      // Price: lower low, MACD: higher low
      if (ctx.position <= 0 && histTurning && histogram[i]! > histogram[i - 1]!) {
        const priceLows = findSwingLows(closes, rangeStart, i, swingWindow);
        const macdLows = findSwingLows(macdLine, rangeStart, i, swingWindow);

        if (priceLows.length >= 2 && macdLows.length >= 2) {
          const [prevPL, curPL] = [
            priceLows[priceLows.length - 2]!,
            priceLows[priceLows.length - 1]!,
          ];
          const [prevML, curML] = [macdLows[macdLows.length - 2]!, macdLows[macdLows.length - 1]!];

          // Price made lower low but MACD made higher low
          if (closes[curPL]! < closes[prevPL]! && macdLine[curML]! > macdLine[prevML]!) {
            return {
              timestamp: bar.timestamp,
              symbol: "",
              direction: "buy" as const,
              price: bar.close,
              reason:
                `MACD底背离: 价格低点 ${closes[curPL]!.toFixed(2)}<${closes[prevPL]!.toFixed(2)} ` +
                `但MACD低点 ${macdLine[curML]!.toFixed(3)}>${macdLine[prevML]!.toFixed(3)}`,
              strategyId: "macd-divergence",
            };
          }
        }
      }

      // ── Top divergence (sell signal) ──
      // Price: higher high, MACD: lower high
      if (ctx.position > 0 && histTurning && histogram[i]! < histogram[i - 1]!) {
        const priceHighs = findSwingHighs(closes, rangeStart, i, swingWindow);
        const macdHighs = findSwingHighs(macdLine, rangeStart, i, swingWindow);

        if (priceHighs.length >= 2 && macdHighs.length >= 2) {
          const [prevPH, curPH] = [
            priceHighs[priceHighs.length - 2]!,
            priceHighs[priceHighs.length - 1]!,
          ];
          const [prevMH, curMH] = [
            macdHighs[macdHighs.length - 2]!,
            macdHighs[macdHighs.length - 1]!,
          ];

          // Price made higher high but MACD made lower high
          if (closes[curPH]! > closes[prevPH]! && macdLine[curMH]! < macdLine[prevMH]!) {
            return {
              timestamp: bar.timestamp,
              symbol: "",
              direction: "sell" as const,
              price: bar.close,
              reason:
                `MACD顶背离: 价格高点 ${closes[curPH]!.toFixed(2)}>${closes[prevPH]!.toFixed(2)} ` +
                `但MACD高点 ${macdLine[curMH]!.toFixed(3)}<${macdLine[prevMH]!.toFixed(3)}`,
              strategyId: "macd-divergence",
            };
          }
        }
      }

      return null;
    },
  };
}

// ── Volume Confirmation ──────────────────────────────────────────

/**
 * Confirms price moves with above-average volume.
 * Buy when price rises on volume > N-day average × multiplier;
 * sell when price drops on elevated volume.
 * Volume validates that the price move has institutional participation.
 */
export function volumeConfirmation(volumeMaPeriod = 20, volumeMultiplier = 1.5): BuiltinStrategy {
  let volumeMa: number[] = [];

  return {
    name: `VolumeConfirm(MA${volumeMaPeriod},×${volumeMultiplier})`,
    init(ctx) {
      const volumes = ctx.bars.map((b) => b.volume);
      volumeMa = sma(volumes, volumeMaPeriod);
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1 || Number.isNaN(volumeMa[i]!)) return null;

      const bar = ctx.bars[i]!;
      const prevBar = ctx.bars[i - 1]!;
      const avgVol = volumeMa[i]!;
      const isHighVolume = bar.volume > avgVol * volumeMultiplier;

      if (!isHighVolume) return null;

      const priceUp = bar.close > prevBar.close;
      const priceDown = bar.close < prevBar.close;
      const volRatio = (bar.volume / avgVol).toFixed(1);

      // Buy: price up on high volume
      if (priceUp && ctx.position <= 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `Price up on ${volRatio}× avg volume (vol=${bar.volume}, avg=${avgVol.toFixed(0)})`,
          strategyId: "volume-confirmation",
        };
      }

      // Sell: price down on high volume
      if (priceDown && ctx.position > 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell",
          price: bar.close,
          reason: `Price down on ${volRatio}× avg volume (vol=${bar.volume}, avg=${avgVol.toFixed(0)})`,
          strategyId: "volume-confirmation",
        };
      }

      return null;
    },
  };
}

// ── T+0 Day Trading (Intraday momentum) ──────────────────────────

/**
 * T+0 Day Trading Strategy: Fast intraday momentum trading leveraging high-frequency signals.
 *
 * Features:
 *   - Short-term MA (3, 7 periods) for quick trend detection
 *   - RSI for overbought/oversold conditions
 *   - Volume surge detection for confirmation
 *   - Aggressive profit-taking at 2-3% (typical intraday target)
 *   - Strict stop-loss at 1% to protect capital
 *
 * Best used with 5-15 minute K-lines for maximum signal frequency.
 */
export function t0DayTrading(
  fastMaPeriod = 3,
  mediumMaPeriod = 7,
  rsiPeriod = 9,
  rsiOversold = 35,
  rsiOverbought = 65,
  volumeMaPeriod = 10,
  volumeMultiplier = 1.2,
  profitTargetPct = 2.5,
  stopLossPct = 1.0,
): BuiltinStrategy {
  let fastMa: number[] = [];
  let mediumMa: number[] = [];
  let rsiValues: number[] = [];
  let volumeMa: number[] = [];
  let entryPrice = 0;
  let profitTarget = 0;
  let stopLoss = 0;

  return {
    name: `T0DayTrading(${fastMaPeriod}/${mediumMaPeriod},RSI${rsiPeriod},T${profitTargetPct}%,SL${stopLossPct}%)`,
    init(ctx) {
      const closes = getCloses(ctx);
      fastMa = sma(closes, fastMaPeriod);
      mediumMa = sma(closes, mediumMaPeriod);
      rsiValues = rsi(closes, rsiPeriod);
      const volumes = ctx.bars.map((b) => b.volume);
      volumeMa = sma(volumes, volumeMaPeriod);
      entryPrice = 0;
      profitTarget = 0;
      stopLoss = 0;
    },
    next(ctx) {
      const i = ctx.index;
      if (
        i < 2 ||
        Number.isNaN(fastMa[i]!) ||
        Number.isNaN(mediumMa[i]!) ||
        Number.isNaN(rsiValues[i]!) ||
        Number.isNaN(volumeMa[i]!)
      )
        return null;

      const bar = ctx.bars[i]!;
      const prevBar = ctx.bars[i - 1]!;
      const curRsi = rsiValues[i]!;
      const isHighVolume = bar.volume > volumeMa[i]! * volumeMultiplier;

      // ── Close existing position on profit target / stop loss ──
      if (ctx.position > 0 && entryPrice > 0) {
        const pnlPct = ((bar.close - entryPrice) / entryPrice) * 100;

        // Profit target hit
        if (bar.close >= profitTarget) {
          entryPrice = 0;
          profitTarget = 0;
          stopLoss = 0;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "sell",
            price: bar.close,
            reason: `T+0 profit target ${profitTargetPct}% achieved (${pnlPct.toFixed(1)}%)`,
            strategyId: "t0-day-trading",
          };
        }

        // Stop loss hit
        if (bar.close <= stopLoss) {
          entryPrice = 0;
          profitTarget = 0;
          stopLoss = 0;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "sell",
            price: bar.close,
            reason: `T+0 stop loss ${stopLossPct}% triggered (${pnlPct.toFixed(1)}%)`,
            strategyId: "t0-day-trading",
          };
        }
      }

      // ── Buy signal: fast MA > medium MA + RSI oversold + volume surge ──
      if (ctx.position <= 0 && fastMa[i]! > mediumMa[i]! && curRsi < rsiOversold && isHighVolume) {
        entryPrice = bar.close;
        profitTarget = bar.close * (1 + profitTargetPct / 100);
        stopLoss = bar.close * (1 - stopLossPct / 100);

        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason:
            `T+0快速买入: 快MA(${fastMa[i]!.toFixed(2)})>${mediumMa[i]!.toFixed(2)} + ` +
            `RSI(${curRsi.toFixed(0)})<${rsiOversold} + 放量(${(bar.volume / volumeMa[i]!).toFixed(1)}×)`,
          strategyId: "t0-day-trading",
        };
      }

      // ── Sell signal: fast MA < medium MA + RSI overbought ──
      if (ctx.position <= 0 && fastMa[i]! < mediumMa[i]! && curRsi > rsiOverbought) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell",
          price: bar.close,
          reason:
            `T+0快速卖出: 快MA(${fastMa[i]!.toFixed(2)})<${mediumMa[i]!.toFixed(2)} + ` +
            `RSI(${curRsi.toFixed(0)})>${rsiOverbought}`,
          strategyId: "t0-day-trading",
        };
      }

      return null;
    },
  };
}
