/**
 * CTA indicator-based strategies: volatility, momentum, oscillator,
 * mean-reversion, and grid strategies.
 *
 *   - ctaVolatilityTarget:      ATR volatility targeting
 *   - ctaMomentumRotation:      ROC momentum ranking
 *   - ctaWilliamsR:             Williams %R reversal
 *   - ctaObvTrend:              OBV volume-price trend
 *   - ctaIchimoku:              一目均衡图
 *   - ctaKdj:                   KDJ stochastic
 *   - ctaMeanReversionZScore:   Z-score mean reversion
 *   - ctaGridTrading:           Grid trading
 *   - ctaMultiMaConvergence:    Multi-MA alignment
 *   - ctaDonchianMidReversion:  Donchian mid reversion
 *   - ctaPivotPoint:            Pivot point support/resistance
 *   - ctaChandelierExit:        Chandelier trailing stop
 */
import type { BuiltinStrategy } from "./indicators.js";
import { atr, getCloses, ichimoku, obv, roc, sma, stochastic, williamsR } from "./indicators.js";

// ── Volatility Target ────────────────────────────────────────────

/**
 * 波动率目标策略
 *
 * Combines a simple MA trend filter with ATR-based volatility targeting.
 * The strategy adjusts its signal strength based on how current ATR
 * compares to a target annualized volatility:
 *
 *   scaleFactor = targetVol / (ATR/close × √252)
 *
 * Entry:
 *   - Long:  close > MA(trendPeriod)  → buy (with vol-scaled conviction)
 *   - Short: close < MA(trendPeriod)  → short
 *
 * Exit:
 *   - Trend reversal (close crosses MA)
 *
 * The scale factor is clamped to [0.1, 2.0] and reported in the reason
 * text, enabling downstream position-sizing logic.
 */
export function ctaVolatilityTarget(
  trendPeriod = 20,
  atrPeriod = 14,
  targetVol = 0.15,
): BuiltinStrategy {
  let maValues: number[] = [];
  let atrValues: number[] = [];
  let direction = 0;

  return {
    name: `波动率目标(MA${trendPeriod},ATR${atrPeriod},目标${(targetVol * 100).toFixed(0)}%)`,
    init(ctx) {
      maValues = sma(getCloses(ctx), trendPeriod);
      atrValues = atr(ctx.bars, atrPeriod);
      direction = 0;
    },
    next(ctx) {
      const i = ctx.index;
      if (Number.isNaN(maValues[i]!) || Number.isNaN(atrValues[i]!)) return null;

      const bar = ctx.bars[i]!;
      const ma = maValues[i]!;
      const curAtr = atrValues[i]!;

      // Realized vol estimate: ATR / close × √252
      const realizedVol = bar.close > 0 ? (curAtr / bar.close) * Math.sqrt(252) : 0;
      // Scale factor: how much to adjust position vs full-size
      const rawScale = realizedVol > 0 ? targetVol / realizedVol : 1;
      const scale = Math.max(0.1, Math.min(2.0, rawScale));

      // Exit check
      if (direction === 1 && bar.close < ma) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell" as const,
          price: bar.close,
          reason:
            `[波动率目标平多] 价格 ¥${bar.close.toFixed(2)} 跌破MA${trendPeriod}=` +
            `¥${ma.toFixed(2)}，vol=${(realizedVol * 100).toFixed(1)}%`,
          strategyId: "volatility-target",
        };
      }
      if (direction === -1 && bar.close > ma) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "cover" as const,
          price: bar.close,
          reason:
            `[波动率目标平空] 价格 ¥${bar.close.toFixed(2)} 突破MA${trendPeriod}=` +
            `¥${ma.toFixed(2)}，vol=${(realizedVol * 100).toFixed(1)}%`,
          strategyId: "volatility-target",
        };
      }

      // Entry check
      if (direction === 0) {
        if (bar.close > ma) {
          direction = 1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy" as const,
            price: bar.close,
            reason:
              `[波动率目标做多] 价格 ¥${bar.close.toFixed(2)} > MA${trendPeriod}=` +
              `¥${ma.toFixed(2)}，仓位系数=${scale.toFixed(2)}(vol=${(realizedVol * 100).toFixed(1)}%)`,
            strategyId: "volatility-target",
          };
        }
        if (bar.close < ma) {
          direction = -1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "short" as const,
            price: bar.close,
            reason:
              `[波动率目标做空] 价格 ¥${bar.close.toFixed(2)} < MA${trendPeriod}=` +
              `¥${ma.toFixed(2)}，仓位系数=${scale.toFixed(2)}(vol=${(realizedVol * 100).toFixed(1)}%)`,
            strategyId: "volatility-target",
          };
        }
      }

      return null;
    },
  };
}

// ── Momentum Rotation (动量轮动) ─────────────────────────────────

/**
 * 动量轮动策略
 *
 * Ranks instruments by their Rate of Change (ROC) over a lookback period.
 * In single-symbol mode, goes long when ROC > 0, short when ROC < 0,
 * with magnitude reported for multi-symbol portfolio ranking.
 *
 * Entry:
 *   - Long:  ROC(lookback) > threshold → buy
 *   - Short: ROC(lookback) < -threshold → short
 *
 * Exit:
 *   - Long exit:  ROC crosses below 0
 *   - Short exit: ROC crosses above 0
 *
 * Multi-symbol portfolio: use `runPortfolioBacktest` to rank symbols
 * and allocate to top-N momentum instruments.
 */
export function ctaMomentumRotation(lookback = 20, threshold = 0): BuiltinStrategy {
  let rocValues: number[] = [];
  let direction = 0;

  return {
    name: `动量轮动(ROC${lookback},阈值${threshold}%)`,
    init(ctx) {
      rocValues = roc(getCloses(ctx), lookback);
      direction = 0;
    },
    next(ctx) {
      const i = ctx.index;
      if (Number.isNaN(rocValues[i]!)) return null;

      const bar = ctx.bars[i]!;
      const curRoc = rocValues[i]!;

      // Exit conditions
      if (direction === 1 && curRoc < 0) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell" as const,
          price: bar.close,
          reason: `[动量平多] ROC=${curRoc.toFixed(1)}% 跌破零轴，动量消退`,
          strategyId: "momentum-rotation",
        };
      }
      if (direction === -1 && curRoc > 0) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "cover" as const,
          price: bar.close,
          reason: `[动量平空] ROC=${curRoc.toFixed(1)}% 突破零轴，动量反转`,
          strategyId: "momentum-rotation",
        };
      }

      // Entry conditions
      if (direction === 0) {
        if (curRoc > threshold) {
          direction = 1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy" as const,
            price: bar.close,
            reason:
              `[动量做多] ROC${lookback}=${curRoc.toFixed(1)}%>` +
              `${threshold}%，价格 ¥${bar.close.toFixed(2)} 动量向上`,
            strategyId: "momentum-rotation",
          };
        }
        if (curRoc < -threshold) {
          direction = -1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "short" as const,
            price: bar.close,
            reason:
              `[动量做空] ROC${lookback}=${curRoc.toFixed(1)}%<` +
              `-${threshold}%，价格 ¥${bar.close.toFixed(2)} 动量向下`,
            strategyId: "momentum-rotation",
          };
        }
      }

      return null;
    },
  };
}

// ── Williams %R Reversal ─────────────────────────────────────────

/**
 * 威廉指标反转策略
 *
 * Williams %R oscillates between 0 and -100:
 *   - > -20 = overbought
 *   - < -80 = oversold
 *
 * Entry (reversal style):
 *   - Long:  %R crosses above oversold level (-80) from below → buy
 *   - Short: %R crosses below overbought level (-20) from above → short
 *
 * Exit:
 *   - Long exit:  %R enters overbought (> -20)
 *   - Short exit: %R enters oversold (< -80)
 */
export function ctaWilliamsR(period = 14, overbought = -20, oversold = -80): BuiltinStrategy {
  let wrValues: number[] = [];
  let direction = 0;

  return {
    name: `威廉%R(${period},OB=${overbought},OS=${oversold})`,
    init(ctx) {
      wrValues = williamsR(ctx.bars, period);
      direction = 0;
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1) return null;
      if (Number.isNaN(wrValues[i]!) || Number.isNaN(wrValues[i - 1]!)) return null;

      const bar = ctx.bars[i]!;
      const cur = wrValues[i]!;
      const prev = wrValues[i - 1]!;

      // Exit conditions
      if (direction === 1 && cur > overbought) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell" as const,
          price: bar.close,
          reason: `[威廉平多] %R=${cur.toFixed(1)} 进入超买区(>${overbought})`,
          strategyId: "williams-r",
        };
      }
      if (direction === -1 && cur < oversold) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "cover" as const,
          price: bar.close,
          reason: `[威廉平空] %R=${cur.toFixed(1)} 进入超卖区(<${oversold})`,
          strategyId: "williams-r",
        };
      }

      // Entry conditions (crossover)
      if (direction === 0) {
        // Buy: %R crosses above oversold from below
        if (prev <= oversold && cur > oversold) {
          direction = 1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy" as const,
            price: bar.close,
            reason:
              `[威廉做多] %R=${cur.toFixed(1)} 从超卖区(${oversold})反弹，` +
              `价格 ¥${bar.close.toFixed(2)}`,
            strategyId: "williams-r",
          };
        }
        // Short: %R crosses below overbought from above
        if (prev >= overbought && cur < overbought) {
          direction = -1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "short" as const,
            price: bar.close,
            reason:
              `[威廉做空] %R=${cur.toFixed(1)} 从超买区(${overbought})回落，` +
              `价格 ¥${bar.close.toFixed(2)}`,
            strategyId: "williams-r",
          };
        }
      }

      return null;
    },
  };
}

// ── OBV Trend (量价趋势) ────────────────────────────────────────

/**
 * OBV量价趋势策略
 *
 * Uses On-Balance Volume (OBV) with a moving average to detect
 * volume-confirmed price trends:
 *
 * Entry:
 *   - Long:  OBV > OBV_MA AND close > close_MA → buy (volume confirms trend)
 *   - Short: OBV < OBV_MA AND close < close_MA → short
 *
 * Exit:
 *   - When OBV or price diverge from the trend direction.
 *
 * Dual confirmation: both price and volume must agree on the trend.
 */
export function ctaObvTrend(maPeriod = 20): BuiltinStrategy {
  let obvValues: number[] = [];
  let obvMa: number[] = [];
  let closeMa: number[] = [];
  let direction = 0;

  return {
    name: `OBV量价趋势(MA${maPeriod})`,
    init(ctx) {
      obvValues = obv(ctx.bars);
      obvMa = sma(obvValues, maPeriod);
      closeMa = sma(getCloses(ctx), maPeriod);
      direction = 0;
    },
    next(ctx) {
      const i = ctx.index;
      if (Number.isNaN(obvMa[i]!) || Number.isNaN(closeMa[i]!)) return null;

      const bar = ctx.bars[i]!;
      const curObv = obvValues[i]!;
      const curObvMa = obvMa[i]!;
      const curCloseMa = closeMa[i]!;

      const obvAbove = curObv > curObvMa;
      const priceAbove = bar.close > curCloseMa;
      const obvBelow = curObv < curObvMa;
      const priceBelow = bar.close < curCloseMa;

      // Exit conditions: divergence between price and volume signals
      if (direction === 1 && (obvBelow || priceBelow)) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell" as const,
          price: bar.close,
          reason: obvBelow
            ? `[OBV平多] OBV跌破均线，量能不支持上涨`
            : `[OBV平多] 价格 ¥${bar.close.toFixed(2)} 跌破MA${maPeriod}=¥${curCloseMa.toFixed(2)}`,
          strategyId: "obv-trend",
        };
      }
      if (direction === -1 && (obvAbove || priceAbove)) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "cover" as const,
          price: bar.close,
          reason: obvAbove
            ? `[OBV平空] OBV突破均线，量能转强`
            : `[OBV平空] 价格 ¥${bar.close.toFixed(2)} 突破MA${maPeriod}=¥${curCloseMa.toFixed(2)}`,
          strategyId: "obv-trend",
        };
      }

      // Entry conditions: dual confirmation
      if (direction === 0) {
        if (obvAbove && priceAbove) {
          direction = 1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy" as const,
            price: bar.close,
            reason:
              `[OBV做多] 价格 ¥${bar.close.toFixed(2)}>MA${maPeriod}=` +
              `¥${curCloseMa.toFixed(2)}，OBV确认量能向上`,
            strategyId: "obv-trend",
          };
        }
        if (obvBelow && priceBelow) {
          direction = -1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "short" as const,
            price: bar.close,
            reason:
              `[OBV做空] 价格 ¥${bar.close.toFixed(2)}<MA${maPeriod}=` +
              `¥${curCloseMa.toFixed(2)}，OBV确认量能向下`,
            strategyId: "obv-trend",
          };
        }
      }

      return null;
    },
  };
}

// ── Ichimoku Cloud (一目均衡图) ──────────────────────────────────

/**
 * Ichimoku 一目均衡图策略.
 *
 * Entry:
 *   - 多: 转换线上穿基准线 且 收盘价在云层之上 → buy
 *   - 空: 转换线下穿基准线 且 收盘价在云层之下 → short
 *
 * Exit:
 *   - 平多: 转换线下穿基准线 → sell
 *   - 平空: 转换线上穿基准线 → cover
 *
 * 适用于外汇、股指期货等趋势市场。
 */
export function ctaIchimoku(
  tenkanPeriod = 9,
  kijunPeriod = 26,
  senkouBPeriod = 52,
): BuiltinStrategy {
  let tenkan: number[] = [];
  let kijun: number[] = [];
  let senkouA: number[] = [];
  let senkouB: number[] = [];
  let direction = 0; // 1 = long, -1 = short

  return {
    name: `一目均衡(${tenkanPeriod},${kijunPeriod},${senkouBPeriod})`,
    init(ctx) {
      const result = ichimoku(ctx.bars, tenkanPeriod, kijunPeriod, senkouBPeriod);
      tenkan = result.tenkan;
      kijun = result.kijun;
      senkouA = result.senkouA;
      senkouB = result.senkouB;
      direction = 0;
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1) return null;
      const curTenkan = tenkan[i]!;
      const curKijun = kijun[i]!;
      const prevTenkan = tenkan[i - 1]!;
      const prevKijun = kijun[i - 1]!;
      if (
        Number.isNaN(curTenkan) ||
        Number.isNaN(curKijun) ||
        Number.isNaN(prevTenkan) ||
        Number.isNaN(prevKijun)
      )
        return null;

      const bar = ctx.bars[i]!;
      const cloudTop = Math.max(senkouA[i] ?? NaN, senkouB[i] ?? NaN);
      const cloudBottom = Math.min(senkouA[i] ?? NaN, senkouB[i] ?? NaN);

      const tenkanCrossUp = prevTenkan <= prevKijun && curTenkan > curKijun;
      const tenkanCrossDown = prevTenkan >= prevKijun && curTenkan < curKijun;

      // Exit
      if (direction === 1 && tenkanCrossDown) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell" as const,
          price: bar.close,
          reason:
            `[一目平多] 转换线(${curTenkan.toFixed(1)})下穿基准线` + `(${curKijun.toFixed(1)})`,
          strategyId: "ichimoku",
        };
      }
      if (direction === -1 && tenkanCrossUp) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "cover" as const,
          price: bar.close,
          reason:
            `[一目平空] 转换线(${curTenkan.toFixed(1)})上穿基准线` + `(${curKijun.toFixed(1)})`,
          strategyId: "ichimoku",
        };
      }

      // Entry
      if (direction === 0) {
        if (tenkanCrossUp && !Number.isNaN(cloudTop) && bar.close > cloudTop) {
          direction = 1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy" as const,
            price: bar.close,
            reason:
              `[一目做多] 转换线上穿基准线，价格 ¥${bar.close.toFixed(2)} > ` +
              `云层上沿 ¥${cloudTop.toFixed(2)}`,
            strategyId: "ichimoku",
          };
        }
        if (tenkanCrossDown && !Number.isNaN(cloudBottom) && bar.close < cloudBottom) {
          direction = -1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "short" as const,
            price: bar.close,
            reason:
              `[一目做空] 转换线下穿基准线，价格 ¥${bar.close.toFixed(2)} < ` +
              `云层下沿 ¥${cloudBottom.toFixed(2)}`,
            strategyId: "ichimoku",
          };
        }
      }

      return null;
    },
  };
}

// ── KDJ (Stochastic) ────────────────────────────────────────────

/**
 * KDJ 随机指标策略.
 *
 * Entry:
 *   - 多: K 上穿 D 且 J < oversold → buy
 *   - 空: K 下穿 D 且 J > overbought → short
 *
 * Exit:
 *   - 平多: K 下穿 D → sell
 *   - 平空: K 上穿 D → cover
 *
 * 适用于震荡和短线交易，国内期货最常用的振荡器之一。
 */
export function ctaKdj(
  period = 9,
  kSmooth = 3,
  dPeriod = 3,
  overbought = 80,
  oversold = 20,
): BuiltinStrategy {
  let kLine: number[] = [];
  let dLine: number[] = [];
  let jLine: number[] = [];
  let direction = 0;

  return {
    name: `KDJ(${period},${kSmooth},${dPeriod},OB${overbought},OS${oversold})`,
    init(ctx) {
      const result = stochastic(ctx.bars, period, kSmooth, dPeriod);
      kLine = result.k;
      dLine = result.d;
      jLine = result.j;
      direction = 0;
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1) return null;
      const curK = kLine[i]!;
      const curD = dLine[i]!;
      const curJ = jLine[i]!;
      const prevK = kLine[i - 1]!;
      const prevD = dLine[i - 1]!;
      if (
        Number.isNaN(curK) ||
        Number.isNaN(curD) ||
        Number.isNaN(curJ) ||
        Number.isNaN(prevK) ||
        Number.isNaN(prevD)
      )
        return null;

      const bar = ctx.bars[i]!;
      const kCrossUp = prevK <= prevD && curK > curD;
      const kCrossDown = prevK >= prevD && curK < curD;

      // Exit (and possibly reverse)
      if (direction === 1 && kCrossDown) {
        const exitSignal = {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell" as const,
          price: bar.close,
          reason:
            `[KDJ平多] K(${curK.toFixed(1)})下穿D(${curD.toFixed(1)})` + `，J=${curJ.toFixed(1)}`,
          strategyId: "kdj",
        };
        // Check reverse entry
        if (curJ > overbought) {
          direction = -1;
          return [
            exitSignal,
            {
              timestamp: bar.timestamp,
              symbol: "",
              direction: "short" as const,
              price: bar.close,
              reason:
                `[KDJ做空] K(${curK.toFixed(1)})下穿D(${curD.toFixed(1)})` +
                `，J=${curJ.toFixed(1)}>${overbought}，价格 ¥${bar.close.toFixed(2)}`,
              strategyId: "kdj",
            },
          ];
        }
        direction = 0;
        return exitSignal;
      }
      if (direction === -1 && kCrossUp) {
        const exitSignal = {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "cover" as const,
          price: bar.close,
          reason:
            `[KDJ平空] K(${curK.toFixed(1)})上穿D(${curD.toFixed(1)})` + `，J=${curJ.toFixed(1)}`,
          strategyId: "kdj",
        };
        // Check reverse entry
        if (curJ < oversold) {
          direction = 1;
          return [
            exitSignal,
            {
              timestamp: bar.timestamp,
              symbol: "",
              direction: "buy" as const,
              price: bar.close,
              reason:
                `[KDJ做多] K(${curK.toFixed(1)})上穿D(${curD.toFixed(1)})` +
                `，J=${curJ.toFixed(1)}<${oversold}，价格 ¥${bar.close.toFixed(2)}`,
              strategyId: "kdj",
            },
          ];
        }
        direction = 0;
        return exitSignal;
      }

      // Entry
      if (direction === 0) {
        if (kCrossUp && curJ < oversold) {
          direction = 1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy" as const,
            price: bar.close,
            reason:
              `[KDJ做多] K(${curK.toFixed(1)})上穿D(${curD.toFixed(1)})` +
              `，J=${curJ.toFixed(1)}<${oversold}，价格 ¥${bar.close.toFixed(2)}`,
            strategyId: "kdj",
          };
        }
        if (kCrossDown && curJ > overbought) {
          direction = -1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "short" as const,
            price: bar.close,
            reason:
              `[KDJ做空] K(${curK.toFixed(1)})下穿D(${curD.toFixed(1)})` +
              `，J=${curJ.toFixed(1)}>${overbought}，价格 ¥${bar.close.toFixed(2)}`,
            strategyId: "kdj",
          };
        }
      }

      return null;
    },
  };
}

// ── Mean Reversion Z-Score ───────────────────────────────────────

/**
 * 均值回归 Z-Score 策略.
 *
 * 计算收盘价相对其均线的 Z 分数:
 *   Z = (close − SMA) / stdDev
 *
 * Entry:
 *   - 多: Z < -entryZ → 价格偏低，均值回归做多
 *   - 空: Z > +entryZ → 价格偏高，均值回归做空
 *
 * Exit:
 *   - 平多: Z > -exitZ (回归至均值附近) → sell
 *   - 平空: Z < +exitZ → cover
 *
 * 适用于震荡品种（股指、农产品等）的统计套利。
 */
export function ctaMeanReversionZScore(period = 20, entryZ = 2.0, exitZ = 0.5): BuiltinStrategy {
  let zScores: number[] = [];
  let direction = 0;

  return {
    name: `均值回归Z(MA${period},入场${entryZ},出场${exitZ})`,
    init(ctx) {
      const closes = getCloses(ctx);
      const ma = sma(closes, period);
      const n = ctx.bars.length;
      zScores = new Array<number>(n).fill(NaN);

      // Rolling window for sum and sumSq — O(n) instead of O(n×period)
      let windowSum = 0;
      let windowSumSq = 0;
      for (let i = 0; i < n; i++) {
        const v = closes[i]!;
        windowSum += v;
        windowSumSq += v * v;
        if (i >= period) {
          const leaving = closes[i - period]!;
          windowSum -= leaving;
          windowSumSq -= leaving * leaving;
        }
        if (i >= period - 1) {
          const mean = windowSum / period;
          const variance = windowSumSq / period - mean * mean;
          const std = Math.sqrt(Math.max(0, variance));
          zScores[i] = std === 0 ? 0 : (closes[i]! - ma[i]!) / std;
        }
      }
      direction = 0;
    },
    next(ctx) {
      const i = ctx.index;
      const z = zScores[i]!;
      if (Number.isNaN(z)) return null;

      const bar = ctx.bars[i]!;

      // Exit
      if (direction === 1 && z > -exitZ) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell" as const,
          price: bar.close,
          reason:
            `[Z回归平多] Z=${z.toFixed(2)} > -${exitZ}，` +
            `价格 ¥${bar.close.toFixed(2)} 回归均值`,
          strategyId: "mean-reversion-zscore",
        };
      }
      if (direction === -1 && z < exitZ) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "cover" as const,
          price: bar.close,
          reason:
            `[Z回归平空] Z=${z.toFixed(2)} < ${exitZ}，` + `价格 ¥${bar.close.toFixed(2)} 回归均值`,
          strategyId: "mean-reversion-zscore",
        };
      }

      // Entry
      if (direction === 0) {
        if (z < -entryZ) {
          direction = 1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy" as const,
            price: bar.close,
            reason:
              `[Z回归做多] Z=${z.toFixed(2)} < -${entryZ}，` +
              `价格 ¥${bar.close.toFixed(2)} 偏离均值过低`,
            strategyId: "mean-reversion-zscore",
          };
        }
        if (z > entryZ) {
          direction = -1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "short" as const,
            price: bar.close,
            reason:
              `[Z回归做空] Z=${z.toFixed(2)} > ${entryZ}，` +
              `价格 ¥${bar.close.toFixed(2)} 偏离均值过高`,
            strategyId: "mean-reversion-zscore",
          };
        }
      }

      return null;
    },
  };
}

// ── Grid Trading (网格交易) ──────────────────────────────────────

/**
 * 网格交易策略.
 *
 * 在初始价格上下等距设置网格线，价格触及下方网格买入，
 * 触及上方网格卖出（空头方向亦然），适合区间震荡市场。
 *
 * Entry:
 *   - 多: 价格跌至某档网格以下 → buy
 *   - 空: 价格涨至某档网格以上 → short
 *
 * Exit:
 *   - 平多: 价格涨至下一档网格以上 → sell
 *   - 平空: 价格跌至下一档网格以下 → cover
 *
 * 适用于波动率稳定的震荡品种。
 */
export function ctaGridTrading(gridSize = 5, numGrids = 4): BuiltinStrategy {
  let basePrice = 0;
  let direction = 0; // 1=long, -1=short
  let entryLevel = 0; // which grid level we entered at

  return {
    name: `网格交易(格距${gridSize},${numGrids}层)`,
    init(ctx) {
      // Use the first bar's close as base price
      basePrice = ctx.bars[0]?.close ?? 100;
      direction = 0;
      entryLevel = 0;
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1) return null;
      const bar = ctx.bars[i]!;
      const price = bar.close;

      // Calculate which grid level we're at relative to base
      const gridLevel = Math.round((price - basePrice) / gridSize);

      // Exit conditions
      if (direction === 1) {
        // We bought at entryLevel — sell when price rises to entryLevel + 1
        const exitPrice = basePrice + (entryLevel + 1) * gridSize;
        if (price >= exitPrice) {
          direction = 0;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "sell" as const,
            price: bar.close,
            reason:
              `[网格平多] 价格 ¥${price.toFixed(2)} 涨至上方网格线` + ` ¥${exitPrice.toFixed(2)}`,
            strategyId: "grid-trading",
          };
        }
      }
      if (direction === -1) {
        // We shorted at entryLevel — cover when price drops to entryLevel - 1
        const exitPrice = basePrice + (entryLevel - 1) * gridSize;
        if (price <= exitPrice) {
          direction = 0;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "cover" as const,
            price: bar.close,
            reason:
              `[网格平空] 价格 ¥${price.toFixed(2)} 跌至下方网格线` + ` ¥${exitPrice.toFixed(2)}`,
            strategyId: "grid-trading",
          };
        }
      }

      // Entry conditions: only when flat
      if (direction === 0) {
        // Buy when price drops below a grid line (negative level, within numGrids)
        if (gridLevel < 0 && gridLevel >= -numGrids) {
          direction = 1;
          entryLevel = gridLevel;
          const gridPrice = basePrice + gridLevel * gridSize;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy" as const,
            price: bar.close,
            reason:
              `[网格做多] 价格 ¥${price.toFixed(2)} 跌破第${-gridLevel}层网格` +
              ` ¥${gridPrice.toFixed(2)}`,
            strategyId: "grid-trading",
          };
        }
        // Short when price rises above a grid line (positive level, within numGrids)
        if (gridLevel > 0 && gridLevel <= numGrids) {
          direction = -1;
          entryLevel = gridLevel;
          const gridPrice = basePrice + gridLevel * gridSize;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "short" as const,
            price: bar.close,
            reason:
              `[网格做空] 价格 ¥${price.toFixed(2)} 涨破第${gridLevel}层网格` +
              ` ¥${gridPrice.toFixed(2)}`,
            strategyId: "grid-trading",
          };
        }
      }

      return null;
    },
  };
}

// ── Multi-MA Convergence (多均线收敛) ────────────────────────────

/**
 * 多均线收敛策略.
 *
 * 使用短/中/长三根均线的排列确认趋势方向。
 * 当三线多头排列（短>中>长）且价格创新高时做多，
 * 空头排列（短<中<长）且价格创新低时做空。
 *
 * Entry:
 *   - 多: MA_short > MA_mid > MA_long 且 close > MA_short → buy
 *   - 空: MA_short < MA_mid < MA_long 且 close < MA_short → short
 *
 * Exit:
 *   - 平多: MA_short < MA_mid (均线纠缠) → sell
 *   - 平空: MA_short > MA_mid → cover
 *
 * 适用于趋势明确的品种，过滤假突破。
 */
export function ctaMultiMaConvergence(
  shortPeriod = 5,
  midPeriod = 20,
  longPeriod = 60,
): BuiltinStrategy {
  let maShort: number[] = [];
  let maMid: number[] = [];
  let maLong: number[] = [];
  let direction = 0;

  return {
    name: `多均线收敛(${shortPeriod},${midPeriod},${longPeriod})`,
    init(ctx) {
      const closes = getCloses(ctx);
      maShort = sma(closes, shortPeriod);
      maMid = sma(closes, midPeriod);
      maLong = sma(closes, longPeriod);
      direction = 0;
    },
    next(ctx) {
      const i = ctx.index;
      const ms = maShort[i]!;
      const mm = maMid[i]!;
      const ml = maLong[i]!;
      if (Number.isNaN(ms) || Number.isNaN(mm) || Number.isNaN(ml)) return null;

      const bar = ctx.bars[i]!;
      const bullishAlign = ms > mm && mm > ml;
      const bearishAlign = ms < mm && mm < ml;

      // Exit
      if (direction === 1 && ms < mm) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell" as const,
          price: bar.close,
          reason:
            `[多MA平多] 短期MA(${ms.toFixed(1)}) < 中期MA(${mm.toFixed(1)})` +
            `，均线纠缠，趋势减弱`,
          strategyId: "multi-ma-convergence",
        };
      }
      if (direction === -1 && ms > mm) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "cover" as const,
          price: bar.close,
          reason:
            `[多MA平空] 短期MA(${ms.toFixed(1)}) > 中期MA(${mm.toFixed(1)})` +
            `，均线转多，空头退出`,
          strategyId: "multi-ma-convergence",
        };
      }

      // Entry
      if (direction === 0) {
        if (bullishAlign && bar.close > ms) {
          direction = 1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy" as const,
            price: bar.close,
            reason:
              `[多MA做多] 多头排列 MA${shortPeriod}(${ms.toFixed(1)}) > ` +
              `MA${midPeriod}(${mm.toFixed(1)}) > MA${longPeriod}(${ml.toFixed(1)})` +
              `，价格 ¥${bar.close.toFixed(2)}`,
            strategyId: "multi-ma-convergence",
          };
        }
        if (bearishAlign && bar.close < ms) {
          direction = -1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "short" as const,
            price: bar.close,
            reason:
              `[多MA做空] 空头排列 MA${shortPeriod}(${ms.toFixed(1)}) < ` +
              `MA${midPeriod}(${mm.toFixed(1)}) < MA${longPeriod}(${ml.toFixed(1)})` +
              `，价格 ¥${bar.close.toFixed(2)}`,
            strategyId: "multi-ma-convergence",
          };
        }
      }

      return null;
    },
  };
}

// ── Donchian Mid Reversion (Donchian 中轨回归) ──────────────────

/**
 * Donchian 中轨回归策略.
 *
 * 使用 Donchian 通道（最高/最低价）的中轨作为均值参考，
 * 当价格偏离中轨过远时逆向入场，回归中轨时平仓。
 *
 * Entry:
 *   - 多: close < 下轨 (Lowest Low) → 超跌买入
 *   - 空: close > 上轨 (Highest High) → 超涨做空
 *
 * Exit:
 *   - 平多: close > 中轨 (midline) → 回归均值
 *   - 平空: close < 中轨 → 回归均值
 *
 * 适用于震荡品种和区间交易。
 */
export function ctaDonchianMidReversion(period = 20): BuiltinStrategy {
  let chHighs: number[] = [];
  let chLows: number[] = [];
  let direction = 0;

  return {
    name: `Donchian回归(${period})`,
    init(ctx) {
      // Build close-based Donchian channel using monotonic deques — O(n)
      const n = ctx.bars.length;
      chHighs = new Array<number>(n).fill(NaN);
      chLows = new Array<number>(n).fill(NaN);
      const maxDeque: number[] = []; // decreasing closes
      const minDeque: number[] = []; // increasing closes
      for (let j = 0; j < n; j++) {
        const c = ctx.bars[j]!.close;
        while (maxDeque.length > 0 && ctx.bars[maxDeque[maxDeque.length - 1]!]!.close <= c)
          maxDeque.pop();
        maxDeque.push(j);
        while (minDeque.length > 0 && ctx.bars[minDeque[minDeque.length - 1]!]!.close >= c)
          minDeque.pop();
        minDeque.push(j);
        if (maxDeque[0]! <= j - period) maxDeque.shift();
        if (minDeque[0]! <= j - period) minDeque.shift();
        if (j >= period - 1) {
          chHighs[j] = ctx.bars[maxDeque[0]!]!.close;
          chLows[j] = ctx.bars[minDeque[0]!]!.close;
        }
      }
      direction = 0;
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1) return null;
      const hh = chHighs[i]!;
      const ll = chLows[i]!;
      if (Number.isNaN(hh) || Number.isNaN(ll)) return null;

      const bar = ctx.bars[i]!;
      const mid = (hh + ll) / 2;

      // Exit: revert to midline
      if (direction === 1 && bar.close > mid) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell" as const,
          price: bar.close,
          reason:
            `[Donchian平多] 价格 ¥${bar.close.toFixed(2)} > 中轨` + ` ¥${mid.toFixed(2)}，回归均值`,
          strategyId: "donchian-mid-reversion",
        };
      }
      if (direction === -1 && bar.close < mid) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "cover" as const,
          price: bar.close,
          reason:
            `[Donchian平空] 价格 ¥${bar.close.toFixed(2)} < 中轨` + ` ¥${mid.toFixed(2)}，回归均值`,
          strategyId: "donchian-mid-reversion",
        };
      }

      // Entry: use previous bar's channel to detect breakout beyond band
      const prevHH = chHighs[i - 1]!;
      const prevLL = chLows[i - 1]!;
      if (direction === 0 && !Number.isNaN(prevHH) && !Number.isNaN(prevLL)) {
        if (bar.close <= prevLL) {
          direction = 1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy" as const,
            price: bar.close,
            reason:
              `[Donchian做多] 价格 ¥${bar.close.toFixed(2)} ≤ 下轨` +
              ` ¥${prevLL.toFixed(2)}，超跌买入`,
            strategyId: "donchian-mid-reversion",
          };
        }
        if (bar.close >= prevHH) {
          direction = -1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "short" as const,
            price: bar.close,
            reason:
              `[Donchian做空] 价格 ¥${bar.close.toFixed(2)} ≥ 上轨` +
              ` ¥${prevHH.toFixed(2)}，超涨做空`,
            strategyId: "donchian-mid-reversion",
          };
        }
      }

      return null;
    },
  };
}

// ── Pivot Point (枢轴点) ────────────────────────────────────────

/**
 * 枢轴点策略.
 *
 * 使用前一根 K 线的 H/L/C 计算经典枢轴点系统:
 *   Pivot = (H + L + C) / 3
 *   S1 = 2 * Pivot - H,  R1 = 2 * Pivot - L
 *   S2 = Pivot - (H - L), R2 = Pivot + (H - L)
 *
 * Entry:
 *   - 多: close 跌至 S1 以下 → 支撑位买入
 *   - 空: close 涨至 R1 以上 → 阻力位做空
 *
 * Exit:
 *   - 平多: close > Pivot → 回归枢轴
 *   - 平空: close < Pivot → 回归枢轴
 *
 * 适用于日内/短周期交易。
 */
export function ctaPivotPoint(): BuiltinStrategy {
  let direction = 0;

  return {
    name: "枢轴点策略",
    init() {
      direction = 0;
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 1) return null;

      const prev = ctx.bars[i - 1]!;
      const bar = ctx.bars[i]!;

      const pivot = (prev.high + prev.low + prev.close) / 3;
      const s1 = 2 * pivot - prev.high;
      const r1 = 2 * pivot - prev.low;

      // Exit
      if (direction === 1 && bar.close > pivot) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell" as const,
          price: bar.close,
          reason:
            `[枢轴平多] 价格 ¥${bar.close.toFixed(2)} > Pivot` + ` ¥${pivot.toFixed(2)}，回归枢轴`,
          strategyId: "pivot-point",
        };
      }
      if (direction === -1 && bar.close < pivot) {
        direction = 0;
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "cover" as const,
          price: bar.close,
          reason:
            `[枢轴平空] 价格 ¥${bar.close.toFixed(2)} < Pivot` + ` ¥${pivot.toFixed(2)}，回归枢轴`,
          strategyId: "pivot-point",
        };
      }

      // Entry
      if (direction === 0) {
        if (bar.close < s1) {
          direction = 1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy" as const,
            price: bar.close,
            reason:
              `[枢轴做多] 价格 ¥${bar.close.toFixed(2)} < S1` + ` ¥${s1.toFixed(2)}，支撑位买入`,
            strategyId: "pivot-point",
          };
        }
        if (bar.close > r1) {
          direction = -1;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "short" as const,
            price: bar.close,
            reason:
              `[枢轴做空] 价格 ¥${bar.close.toFixed(2)} > R1` + ` ¥${r1.toFixed(2)}，阻力位做空`,
            strategyId: "pivot-point",
          };
        }
      }

      return null;
    },
  };
}

// ── Chandelier Exit (吊灯止损) ──────────────────────────────────

/**
 * 吊灯止损策略.
 *
 * 使用 ATR 倍数从最高/最低价挂止损线（像吊灯一样悬挂）。
 * 趋势突破入场后，止损线跟随最高/最低价移动。
 *
 * Entry:
 *   - 多: close 创 lookback 周期新高 → 趋势突破做多
 *   - 空: close 创 lookback 周期新低 → 趋势突破做空
 *
 * Exit:
 *   - 平多: close < highestHigh - mult * ATR → 吊灯止损
 *   - 平空: close > lowestLow + mult * ATR → 吊灯止损
 *
 * 适用于趋势行情的跟踪止损。
 */
export function ctaChandelierExit(atrPeriod = 22, mult = 3.0, lookback = 22): BuiltinStrategy {
  let atrValues: number[] = [];
  let direction = 0;
  let highestSinceEntry = 0;
  let lowestSinceEntry = Infinity;

  return {
    name: `吊灯止损(ATR${atrPeriod},${mult}x)`,
    init(ctx) {
      atrValues = atr(ctx.bars, atrPeriod);
      direction = 0;
      highestSinceEntry = 0;
      lowestSinceEntry = Infinity;
    },
    next(ctx) {
      const i = ctx.index;
      if (i < lookback) return null;
      const bar = ctx.bars[i]!;
      const curAtr = atrValues[i]!;
      if (Number.isNaN(curAtr) || curAtr === 0) return null;

      // Track trailing highs/lows while in position
      if (direction === 1) {
        if (bar.high > highestSinceEntry) highestSinceEntry = bar.high;
        const stopLevel = highestSinceEntry - mult * curAtr;
        if (bar.close < stopLevel) {
          direction = 0;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "sell" as const,
            price: bar.close,
            reason:
              `[吊灯平多] 价格 ¥${bar.close.toFixed(2)} < 止损线` +
              ` ¥${stopLevel.toFixed(2)} (最高${highestSinceEntry.toFixed(1)} - ${mult}×ATR)`,
            strategyId: "chandelier-exit",
          };
        }
      }
      if (direction === -1) {
        if (bar.low < lowestSinceEntry) lowestSinceEntry = bar.low;
        const stopLevel = lowestSinceEntry + mult * curAtr;
        if (bar.close > stopLevel) {
          direction = 0;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "cover" as const,
            price: bar.close,
            reason:
              `[吊灯平空] 价格 ¥${bar.close.toFixed(2)} > 止损线` +
              ` ¥${stopLevel.toFixed(2)} (最低${lowestSinceEntry.toFixed(1)} + ${mult}×ATR)`,
            strategyId: "chandelier-exit",
          };
        }
      }

      // Entry: lookback-period high/low breakout
      if (direction === 0) {
        let periodHigh = -Infinity;
        let periodLow = Infinity;
        for (let k = i - lookback; k < i; k++) {
          const b = ctx.bars[k]!;
          if (b.high > periodHigh) periodHigh = b.high;
          if (b.low < periodLow) periodLow = b.low;
        }

        if (bar.close > periodHigh) {
          direction = 1;
          highestSinceEntry = bar.high;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy" as const,
            price: bar.close,
            reason:
              `[吊灯做多] 价格 ¥${bar.close.toFixed(2)} 突破` +
              ` ${lookback}周期高点 ¥${periodHigh.toFixed(2)}`,
            strategyId: "chandelier-exit",
          };
        }
        if (bar.close < periodLow) {
          direction = -1;
          lowestSinceEntry = bar.low;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "short" as const,
            price: bar.close,
            reason:
              `[吊灯做空] 价格 ¥${bar.close.toFixed(2)} 跌破` +
              ` ${lookback}周期低点 ¥${periodLow.toFixed(2)}`,
            strategyId: "chandelier-exit",
          };
        }
      }

      return null;
    },
  };
}
