import type { MarketDataProvider } from "../data/market-data-provider.js";
/**
 * Strategy registry and loader.
 */
import type {
  BacktestRequest,
  BacktestResult,
  QuantTradingConfig,
  StrategyDefinition,
} from "../types.js";
import { runBacktraderBacktest } from "./backtrader-engine.js";
import { runVnpyBacktest } from "./vnpy-engine.js";
import {
  compositeStrategy,
  ctaAberration,
  ctaBollingerBreakout,
  ctaDualThrust,
  ctaKeltnerChannel,
  ctaMacdTrend,
  ctaDmiAdx,
  ctaMomentumRotation,
  ctaIchimoku,
  ctaKdj,
  ctaMeanReversionZScore,
  ctaGridTrading,
  ctaMultiMaConvergence,
  ctaDonchianMidReversion,
  ctaPivotPoint,
  ctaChandelierExit,
  ctaRangeBreakout,
  ctaVwapReversion,
  ctaDoubleBottom,
  ctaRocMomentum,
  ctaCciDivergence,
  ctaEmaCrossover,
  ctaAtrTrailing,
  ctaStochasticRsi,
  ctaMacdHistogramReversal,
  ctaBollingerBandwidth,
  ctaObvTrend,
  ctaParabolicSar,
  ctaRBreaker,
  ctaRsiReversal,
  ctaTrendFollowing,
  ctaTtmSqueeze,
  ctaTurtleBreakout,
  ctaVolatilityTarget,
  ctaWilliamsR,
  dualMaCrossover,
  enhancedCompositeStrategy,
  gatedStrategy,
  macdConfirmation,
  macdDivergence,
  meanReversion,
  momentumBreakout,
  rsiEnhanced,
  rsiFilter,
  rsiMacdCombined,
  runBuiltinBacktest,
  runPortfolioBacktest,
  scoredStrategy,
  spreadArbitrage,
  t0DayTrading,
  t0EnhancedStrategy,
  trendComboStrategy,
  volumeConfirmation,
  // Extended basic strategies
  trixMomentum,
  mfiOscillator,
  aroonTrend,
  cmfDivergence,
  elderRayStrategy,
  supertrendStrategy,
  hullMaCrossover,
  demaCrossover,
  tripleMaCrossover,
  maEnvelopeStrategy,
  connorsRsiStrategy,
  kamaStrategy,
  demarkerStrategy,
  // Extended CTA strategies
  ctaAwesomeOscillator,
  ctaElderForceIndex,
  ctaDemarkerCta,
  ctaUltimateOscillatorStrategy,
  ctaAdxBreakout,
  ctaTrixTrend,
  ctaHullMaTrend,
  ctaMassIndex,
  ctaChaikinOscillator,
  ctaCoppock,
  ctaRsiDivergence,
  ctaVolumeBreakout,
  ctaAdaptiveMa,
  ctaMaEnvelope,
  ctaConnorsRsi,
  ctaAroonOscillator,
  ctaMfiTrend,
  ctaDemaCrossover,
  ctaTripleMa,
  ctaSupertrendCta,
  swingOverlay,
} from "./builtin-engine.js";
import type { BuiltinStrategy } from "./builtin-engine.js";

type StrategyFactory = (params?: Record<string, unknown>) => BuiltinStrategy;

/** Fallback: vnpy strategy → builtin equivalent (used when vnpy is not installed). */
const vnpyBuiltinFallback: Record<
  string,
  { id: string; mapParams: (p: Record<string, unknown>) => Record<string, unknown> }
> = {
  "vnpy-turtle": {
    id: "turtle-breakout",
    mapParams: (p) => ({ entryPeriod: p.entry_window ?? 20, exitPeriod: p.exit_window ?? 10 }),
  },
  "vnpy-double-ma": {
    id: "dual-ma-crossover",
    mapParams: (p) => ({ fastPeriod: p.fast_window ?? 10, slowPeriod: p.slow_window ?? 30 }),
  },
  "vnpy-boll-channel": {
    id: "bollinger-breakout",
    mapParams: (p) => ({ period: p.boll_window ?? 18, stdDevMultiplier: p.boll_dev ?? 3.4 }),
  },
  "vnpy-king-keltner": {
    id: "keltner-channel",
    mapParams: (p) => ({ emaPeriod: p.kk_period ?? 40, atrMultiplier: p.kk_dev ?? 2.0 }),
  },
  "vnpy-atr-rsi": {
    id: "rsi-reversal",
    mapParams: (p) => ({ period: p.rsi_period ?? 5, oversold: p.rsi_entry ?? 16, overbought: 84 }),
  },
  "vnpy-multi-timeframe": {
    id: "cta-trend",
    mapParams: (p) => ({ trendPeriod: p.slow_period ?? 20 }),
  },
};

/** Fallback: backtrader strategy → builtin equivalent (used when backtrader is not installed). */
const backtraderBuiltinFallback: Record<
  string,
  { id: string; mapParams: (p: Record<string, unknown>) => Record<string, unknown> }
> = {
  "macd": {
    id: "macd-trend",
    mapParams: (p) => ({ fastPeriod: p.fast_period ?? 12, slowPeriod: p.slow_period ?? 26, signalPeriod: p.signal_period ?? 9 }),
  },
  "rsi": {
    id: "rsi-reversal",
    mapParams: (p) => ({ period: p.period ?? 14, oversold: p.oversold ?? 30, overbought: p.overbought ?? 70 }),
  },
  "kdj": {
    id: "rsi-reversal",
    mapParams: (p) => ({ period: p.period ?? 9, oversold: p.oversold ?? 20, overbought: p.overbought ?? 80 }),
  },
  "turtle": {
    id: "turtle-breakout",
    mapParams: (p) => ({ entryPeriod: p.entry_period ?? 20, exitPeriod: p.exit_period ?? 10 }),
  },
  "grid": {
    id: "grid-trading",
    mapParams: (p) => ({ gridSize: p.grid_size ?? 5, numGrids: p.num_grids ?? 4 }),
  },
  "pair-trading": {
    id: "mean-reversion-zscore",
    mapParams: (p) => ({ lookback: p.lookback ?? 60, entryZ: p.entry_z ?? 2.0, exitZ: p.exit_z ?? 0.5 }),
  },
  "auction": {
    id: "momentum-breakout",
    mapParams: (p) => ({ lookback: p.lookback ?? 20 }),
  },
  "dragon-return": {
    id: "momentum-breakout",
    mapParams: (p) => ({ lookback: p.lookback ?? 10 }),
  },
  "mean-reversion-cn": {
    id: "mean-reversion",
    mapParams: (p) => ({ period: p.period ?? 20, stdDev: p.std_dev ?? 2 }),
  },
  "multi-factor": {
    id: "scored",
    mapParams: (p) => ({ ...p }),
  },
  "bollinger": {
    id: "bollinger-breakout",
    mapParams: (p) => ({ period: p.period ?? 20, stdDevMultiplier: p.devfactor ?? 2.0 }),
  },
  "dragon_master": {
    id: "momentum-breakout",
    mapParams: (p) => ({ lookback: p.target_board ?? 3 }),
  },
  "lianban_relay": {
    id: "momentum-breakout",
    mapParams: (p) => ({ lookback: p.min_boards ?? 2 }),
  },
  // ── CTA趋势策略 (Python) ──
  "ema-cross": {
    id: "dual-ma-crossover",
    mapParams: (p) => ({ fastPeriod: p.fast_period ?? 10, slowPeriod: p.slow_period ?? 30 }),
  },
  "adx-trend": {
    id: "dmi-adx",
    mapParams: (p) => ({ period: p.period ?? 14, adxThreshold: p.adx_threshold ?? 25 }),
  },
  "parabolic-sar": {
    id: "parabolic-sar",
    mapParams: (p) => ({ afStart: p.af_start ?? 0.02, afStep: p.af_step ?? 0.02, afMax: p.af_max ?? 0.2 }),
  },
  "ichimoku": {
    id: "ichimoku",
    mapParams: (p) => ({ tenkanPeriod: p.tenkan_period ?? 9, kijunPeriod: p.kijun_period ?? 26, senkouBPeriod: p.senkou_period ?? 52 }),
  },
  "supertrend": {
    id: "supertrend",
    mapParams: (p) => ({ atrPeriod: p.atr_period ?? 10, multiplier: p.multiplier ?? 3.0 }),
  },
  "dema-cross": {
    id: "dema-crossover",
    mapParams: (p) => ({ fastPeriod: p.fast_period ?? 10, slowPeriod: p.slow_period ?? 30 }),
  },
  "tema-cross": {
    id: "dema-crossover",
    mapParams: (p) => ({ fastPeriod: p.fast_period ?? 10, slowPeriod: p.slow_period ?? 30 }),
  },
  "aroon": {
    id: "aroon-trend",
    mapParams: (p) => ({ period: p.period ?? 25 }),
  },
  "vortex": {
    id: "dmi-adx",
    mapParams: (p) => ({ period: p.period ?? 14 }),
  },
  "cci": {
    id: "cci-divergence",
    mapParams: (p) => ({ cciPeriod: p.period ?? 20 }),
  },
  "trix": {
    id: "trix-momentum",
    mapParams: (p) => ({ period: p.period ?? 15, signalPeriod: p.signal_period ?? 9 }),
  },
  "momentum": {
    id: "momentum-breakout",
    mapParams: (p) => ({ lookback: p.period ?? 12 }),
  },
  "dmi": {
    id: "dmi-adx",
    mapParams: (p) => ({ period: p.period ?? 14 }),
  },
  // ── CTA通道策略 (Python) ──
  "keltner-channel": {
    id: "keltner-channel",
    mapParams: (p) => ({ emaPeriod: p.ema_period ?? 20, atrPeriod: p.atr_period ?? 20, atrMultiplier: p.atr_mult ?? 2.0 }),
  },
  "donchian-breakout": {
    id: "donchian-mid-reversion",
    mapParams: (p) => ({ period: p.upper_period ?? 20 }),
  },
  "atr-channel": {
    id: "atr-trailing",
    mapParams: (p) => ({ atrPeriod: p.atr_period ?? 14, mult: p.atr_mult ?? 2.5 }),
  },
  "envelope": {
    id: "ma-envelope",
    mapParams: (p) => ({ period: p.ma_period ?? 20, percent: p.pct ?? 3.0 }),
  },
  "price-channel-breakout": {
    id: "range-breakout",
    mapParams: (p) => ({ rangePeriod: p.breakout_period ?? 20 }),
  },
  // ── CTA指标策略 (Python) ──
  "stochastic": {
    id: "stochastic-rsi",
    mapParams: (p) => ({ rsiPeriod: p.k_period ?? 14, stochPeriod: p.k_period ?? 14 }),
  },
  "williams-r": {
    id: "williams-r",
    mapParams: (p) => ({ period: p.period ?? 14, overbought: p.overbought ?? -20, oversold: p.oversold ?? -80 }),
  },
  "obv": {
    id: "obv-trend",
    mapParams: (p) => ({ maPeriod: p.obv_ma_period ?? 20 }),
  },
  "mfi": {
    id: "mfi-oscillator",
    mapParams: (p) => ({ period: p.period ?? 14, oversold: p.oversold ?? 20, overbought: p.overbought ?? 80 }),
  },
  "cmf": {
    id: "cmf-divergence",
    mapParams: (p) => ({ period: p.period ?? 20 }),
  },
  "adl": {
    id: "obv-trend",
    mapParams: (p) => ({ maPeriod: p.ma_period ?? 20 }),
  },
  "roc": {
    id: "roc-momentum",
    mapParams: (p) => ({ period: p.period ?? 12 }),
  },
  "vwap": {
    id: "vwap-reversion",
    mapParams: (p) => ({ period: p.period ?? 20 }),
  },
  "adaptive-ma": {
    id: "kama-trend",
    mapParams: (p) => ({ period: p.period ?? 10, fastPeriod: p.fast_period ?? 2, slowPeriod: p.slow_period ?? 30 }),
  },
  // ── CTA形态策略 (Python) ──
  "engulfing": {
    id: "double-bottom",
    mapParams: (p) => ({ lookback: p.trend_period ?? 10 }),
  },
  "hammer": {
    id: "double-bottom",
    mapParams: (p) => ({ lookback: p.trend_period ?? 10 }),
  },
  "doji": {
    id: "double-bottom",
    mapParams: (p) => ({ lookback: p.trend_period ?? 10 }),
  },
  "morning-star": {
    id: "double-bottom",
    mapParams: (p) => ({ lookback: p.trend_period ?? 10 }),
  },
  "three-soldiers": {
    id: "momentum-breakout",
    mapParams: (p) => ({ lookback: 10 }),
  },
  "harami": {
    id: "double-bottom",
    mapParams: (p) => ({ lookback: p.trend_period ?? 10 }),
  },
  // ── 组合策略 (Python) ──
  "macd-rsi-combo": {
    id: "macd-confirmation",
    mapParams: (p) => ({ fastPeriod: p.macd_fast ?? 12, slowPeriod: p.macd_slow ?? 26, signalPeriod: p.macd_signal ?? 9 }),
  },
  "bollinger-rsi-combo": {
    id: "bollinger-breakout",
    mapParams: (p) => ({ period: p.bb_period ?? 20, stdDevMultiplier: p.bb_dev ?? 2.0 }),
  },
  "ma-volume-combo": {
    id: "volume-confirmation",
    mapParams: (p) => ({ volumeMaPeriod: p.slow_period ?? 30, volumeMultiplier: p.vol_mult ?? 1.5 }),
  },
  "kdj-macd-combo": {
    id: "cta-kdj",
    mapParams: (p) => ({ period: p.k_period ?? 9, kSmooth: 3, dPeriod: p.d_period ?? 3 }),
  },
  // ── 高阶组合策略 (Python) ──
  "ichimoku-adx-combo": {
    id: "ichimoku",
    mapParams: (p) => ({ tenkanPeriod: p.tenkan_period ?? 9, kijunPeriod: p.kijun_period ?? 26 }),
  },
  "multi-tf-ma": {
    id: "triple-ma-crossover",
    mapParams: (p) => ({ shortPeriod: p.short_period ?? 5, midPeriod: p.medium_period ?? 20, longPeriod: p.long_period ?? 60 }),
  },
  "volatility-breakout": {
    id: "volatility-target",
    mapParams: (p) => ({ atrPeriod: p.atr_period ?? 14 }),
  },
  "regime-switch": {
    id: "mean-reversion",
    mapParams: (p) => ({ period: p.ema_slow ?? 30, stdDev: 2 }),
  },
  "dual-thrust": {
    id: "dual-thrust",
    mapParams: (p) => ({ lookback: p.lookback ?? 4, k1: p.k1 ?? 0.5, k2: p.k2 ?? 0.5 }),
  },
  "r-breaker": {
    id: "r-breaker",
    mapParams: () => ({}),
  },
  "pivot-point": {
    id: "pivot-point",
    mapParams: () => ({}),
  },
};

/** Map from strategy ID → factory function that creates the strategy with params. */
const strategyMap = new Map<string, StrategyFactory>([
  [
    "dual-ma-crossover",
    (p) => dualMaCrossover((p?.fastPeriod as number) ?? 10, (p?.slowPeriod as number) ?? 30),
  ],
  ["momentum-breakout", (p) => momentumBreakout((p?.lookback as number) ?? 20)],
  ["mean-reversion", (p) => meanReversion((p?.period as number) ?? 20, (p?.stdDev as number) ?? 2)],
  [
    "composite",
    (p) =>
      compositeStrategy(
        (p?.fastPeriod as number) ?? 10,
        (p?.slowPeriod as number) ?? 30,
        (p?.breakoutLookback as number) ?? 20,
        (p?.bbPeriod as number) ?? 20,
        (p?.bbStdDev as number) ?? 2,
        (p?.minVotes as number) ?? 2,
      ),
  ],
  [
    "gated",
    (p) =>
      gatedStrategy(
        (p?.fastPeriod as number) ?? 10,
        (p?.slowPeriod as number) ?? 30,
        (p?.bbPeriod as number) ?? 20,
        (p?.bbStdDev as number) ?? 2,
        (p?.breakoutLookback as number) ?? 20,
      ),
  ],
  [
    "trend-combo",
    (p) =>
      trendComboStrategy(
        (p?.fastPeriod as number) ?? 10,
        (p?.slowPeriod as number) ?? 30,
        (p?.breakoutLookback as number) ?? 20,
      ),
  ],
  [
    "scored",
    (p) =>
      scoredStrategy(
        (p?.fastPeriod as number) ?? 10,
        (p?.slowPeriod as number) ?? 30,
        (p?.bbPeriod as number) ?? 20,
        (p?.bbStdDev as number) ?? 2,
        (p?.breakoutLookback as number) ?? 20,
      ),
  ],
  [
    "rsi-filter",
    (p) =>
      rsiFilter(
        (p?.period as number) ?? 14,
        (p?.oversold as number) ?? 30,
        (p?.overbought as number) ?? 70,
      ),
  ],
  [
    "rsi-enhanced",
    (p) =>
      rsiEnhanced(
        (p?.rsiPeriod as number) ?? 14,
        (p?.oversold as number) ?? 35,
        (p?.overbought as number) ?? 65,
        (p?.trendMaPeriod as number) ?? 20,
        (p?.volMaPeriod as number) ?? 5,
        (p?.volMultiplier as number) ?? 1.0,
        (p?.atrPeriod as number) ?? 14,
        (p?.atrStopMultiplier as number) ?? 2.0,
      ),
  ],
  [
    "macd-confirmation",
    (p) =>
      macdConfirmation(
        (p?.fastPeriod as number) ?? 12,
        (p?.slowPeriod as number) ?? 26,
        (p?.signalPeriod as number) ?? 9,
      ),
  ],
  [
    "macd-divergence",
    (p) =>
      macdDivergence(
        (p?.fastPeriod as number) ?? 12,
        (p?.slowPeriod as number) ?? 26,
        (p?.signalPeriod as number) ?? 9,
        (p?.lookback as number) ?? 60,
        (p?.swingWindow as number) ?? 5,
      ),
  ],
  [
    "volume-confirmation",
    (p) =>
      volumeConfirmation(
        (p?.volumeMaPeriod as number) ?? 20,
        (p?.volumeMultiplier as number) ?? 1.5,
      ),
  ],
  [
    "enhanced-composite",
    (p) =>
      enhancedCompositeStrategy(
        (p?.fastPeriod as number) ?? 10,
        (p?.slowPeriod as number) ?? 30,
        (p?.breakoutLookback as number) ?? 20,
        (p?.rsiPeriod as number) ?? 14,
        (p?.rsiOversold as number) ?? 30,
        (p?.rsiOverbought as number) ?? 70,
        (p?.macdFast as number) ?? 12,
        (p?.macdSlow as number) ?? 26,
        (p?.macdSignal as number) ?? 9,
        (p?.volumeMaPeriod as number) ?? 20,
        (p?.volumeMultiplier as number) ?? 1.5,
        (p?.minVotes as number) ?? 3,
        (p?.useRegimeFilter as boolean) ?? true,
        (p?.longMaPeriod as number) ?? 60,
      ),
  ],
  [
    "rsi-macd-combined",
    (p) =>
      rsiMacdCombined(
        (p?.rsiPeriod as number) ?? 14,
        (p?.oversold as number) ?? 35,
        (p?.overbought as number) ?? 65,
        (p?.trendMaPeriod as number) ?? 20,
        (p?.volMaPeriod as number) ?? 5,
        (p?.volMultiplier as number) ?? 1.0,
        (p?.atrPeriod as number) ?? 14,
        (p?.atrStopMultiplier as number) ?? 2.0,
        (p?.macdFast as number) ?? 12,
        (p?.macdSlow as number) ?? 26,
        (p?.macdSignal as number) ?? 9,
        (p?.divergenceLookback as number) ?? 60,
        (p?.confirmWindow as number) ?? 10,
      ),
  ],
  [
    "cta-trend",
    (p) =>
      ctaTrendFollowing(
        (p?.trendPeriod as number) ?? 20,
        (p?.atrPeriod as number) ?? 14,
        (p?.atrMultiplier as number) ?? 2.0,
        (p?.trailAtrMultiplier as number) ?? 1.5,
      ),
  ],
  [
    "dual-thrust",
    (p) =>
      ctaDualThrust(
        (p?.lookback as number) ?? 4,
        (p?.k1 as number) ?? 0.5,
        (p?.k2 as number) ?? 0.5,
      ),
  ],
  [
    "spread-arbitrage",
    (p) =>
      spreadArbitrage(
        (p?.maPeriod as number) ?? 20,
        (p?.entryStdDevs as number) ?? 2.0,
        (p?.exitStdDevs as number) ?? 0.5,
      ),
  ],
  [
    "turtle-breakout",
    (p) => ctaTurtleBreakout((p?.entryPeriod as number) ?? 20, (p?.exitPeriod as number) ?? 10),
  ],
  [
    "macd-trend",
    (p) =>
      ctaMacdTrend(
        (p?.fastPeriod as number) ?? 12,
        (p?.slowPeriod as number) ?? 26,
        (p?.signalPeriod as number) ?? 9,
      ),
  ],
  [
    "rsi-reversal",
    (p) =>
      ctaRsiReversal(
        (p?.period as number) ?? 14,
        (p?.overbought as number) ?? 70,
        (p?.oversold as number) ?? 30,
      ),
  ],
  [
    "bollinger-breakout",
    (p) =>
      ctaBollingerBreakout((p?.period as number) ?? 20, (p?.stdDevMultiplier as number) ?? 2.0),
  ],
  [
    "keltner-channel",
    (p) =>
      ctaKeltnerChannel(
        (p?.emaPeriod as number) ?? 20,
        (p?.atrPeriod as number) ?? 14,
        (p?.atrMultiplier as number) ?? 2.0,
      ),
  ],
  ["r-breaker", () => ctaRBreaker()],
  [
    "aberration",
    (p) => ctaAberration((p?.bbPeriod as number) ?? 20, (p?.bbStdDev as number) ?? 2.0),
  ],
  [
    "ttm-squeeze",
    (p) =>
      ctaTtmSqueeze(
        (p?.bbPeriod as number) ?? 20,
        (p?.bbStdDev as number) ?? 2.0,
        (p?.keltnerEmaPeriod as number) ?? 20,
        (p?.keltnerAtrPeriod as number) ?? 14,
        (p?.keltnerAtrMultiplier as number) ?? 1.5,
        (p?.cciPeriod as number) ?? 20,
      ),
  ],
  ["dmi-adx", (p) => ctaDmiAdx((p?.period as number) ?? 14, (p?.adxThreshold as number) ?? 25)],
  [
    "parabolic-sar",
    (p) =>
      ctaParabolicSar(
        (p?.afStart as number) ?? 0.02,
        (p?.afStep as number) ?? 0.02,
        (p?.afMax as number) ?? 0.2,
      ),
  ],
  [
    "volatility-target",
    (p) =>
      ctaVolatilityTarget(
        (p?.trendPeriod as number) ?? 20,
        (p?.atrPeriod as number) ?? 14,
        (p?.targetVol as number) ?? 0.15,
      ),
  ],
  [
    "momentum-rotation",
    (p) => ctaMomentumRotation((p?.lookback as number) ?? 20, (p?.threshold as number) ?? 0),
  ],
  [
    "williams-r",
    (p) =>
      ctaWilliamsR(
        (p?.period as number) ?? 14,
        (p?.overbought as number) ?? -20,
        (p?.oversold as number) ?? -80,
      ),
  ],
  ["obv-trend", (p) => ctaObvTrend((p?.maPeriod as number) ?? 20)],
  [
    "ichimoku",
    (p) =>
      ctaIchimoku(
        (p?.tenkanPeriod as number) ?? 9,
        (p?.kijunPeriod as number) ?? 26,
        (p?.senkouBPeriod as number) ?? 52,
      ),
  ],
  [
    "cta-kdj",
    (p) =>
      ctaKdj(
        (p?.period as number) ?? 9,
        (p?.kSmooth as number) ?? 3,
        (p?.dPeriod as number) ?? 3,
        (p?.overbought as number) ?? 80,
        (p?.oversold as number) ?? 20,
      ),
  ],
  [
    "mean-reversion-zscore",
    (p) =>
      ctaMeanReversionZScore(
        (p?.period as number) ?? 20,
        (p?.entryZ as number) ?? 2.0,
        (p?.exitZ as number) ?? 0.5,
      ),
  ],
  [
    "grid-trading",
    (p) => ctaGridTrading((p?.gridSize as number) ?? 5, (p?.numGrids as number) ?? 4),
  ],
  [
    "multi-ma-convergence",
    (p) =>
      ctaMultiMaConvergence(
        (p?.shortPeriod as number) ?? 5,
        (p?.midPeriod as number) ?? 20,
        (p?.longPeriod as number) ?? 60,
      ),
  ],
  ["donchian-mid-reversion", (p) => ctaDonchianMidReversion((p?.period as number) ?? 20)],
  ["pivot-point", () => ctaPivotPoint()],
  [
    "chandelier-exit",
    (p) =>
      ctaChandelierExit(
        (p?.atrPeriod as number) ?? 22,
        (p?.mult as number) ?? 3.0,
        (p?.lookback as number) ?? 22,
      ),
  ],
  ["range-breakout", (p) => ctaRangeBreakout((p?.rangePeriod as number) ?? 20)],
  [
    "vwap-reversion",
    (p) => ctaVwapReversion((p?.period as number) ?? 20, (p?.threshold as number) ?? 0.02),
  ],
  [
    "double-bottom",
    (p) => ctaDoubleBottom((p?.lookback as number) ?? 30, (p?.tolerance as number) ?? 0.02),
  ],
  [
    "roc-momentum",
    (p) =>
      ctaRocMomentum(
        (p?.period as number) ?? 12,
        (p?.entryThreshold as number) ?? 5.0,
        (p?.exitThreshold as number) ?? 0.0,
      ),
  ],
  [
    "cci-divergence",
    (p) =>
      ctaCciDivergence(
        (p?.cciPeriod as number) ?? 20,
        (p?.lookback as number) ?? 10,
        (p?.overbought as number) ?? 100,
        (p?.oversold as number) ?? -100,
      ),
  ],
  [
    "ema-crossover",
    (p) => ctaEmaCrossover((p?.fastPeriod as number) ?? 12, (p?.slowPeriod as number) ?? 26),
  ],
  [
    "atr-trailing",
    (p) =>
      ctaAtrTrailing(
        (p?.atrPeriod as number) ?? 14,
        (p?.mult as number) ?? 2.0,
        (p?.trendPeriod as number) ?? 50,
      ),
  ],
  [
    "stochastic-rsi",
    (p) =>
      ctaStochasticRsi(
        (p?.rsiPeriod as number) ?? 14,
        (p?.stochPeriod as number) ?? 14,
        (p?.kSmooth as number) ?? 3,
        (p?.overbought as number) ?? 80,
        (p?.oversold as number) ?? 20,
      ),
  ],
  [
    "macd-histogram-reversal",
    (p) =>
      ctaMacdHistogramReversal(
        (p?.fastPeriod as number) ?? 12,
        (p?.slowPeriod as number) ?? 26,
        (p?.signalPeriod as number) ?? 9,
      ),
  ],
  [
    "bollinger-bandwidth",
    (p) =>
      ctaBollingerBandwidth(
        (p?.period as number) ?? 20,
        (p?.stdDev as number) ?? 2.0,
        (p?.squeezeLookback as number) ?? 20,
        (p?.squeezePercentile as number) ?? 0.25,
      ),
  ],
  [
    "t0-day-trading",
    (p) =>
      t0DayTrading(
        (p?.fastMaPeriod as number) ?? 3,
        (p?.mediumMaPeriod as number) ?? 7,
        (p?.rsiPeriod as number) ?? 9,
        (p?.rsiOversold as number) ?? 35,
        (p?.rsiOverbought as number) ?? 65,
        (p?.volumeMaPeriod as number) ?? 10,
        (p?.volumeMultiplier as number) ?? 1.2,
        (p?.profitTargetPct as number) ?? 2.5,
        (p?.stopLossPct as number) ?? 1.0,
      ),
  ],
  [
    "t0-enhanced",
    (p) =>
      t0EnhancedStrategy(
        (p?.fastMaPeriod as number) ?? 3,
        (p?.mediumMaPeriod as number) ?? 7,
        (p?.rsiPeriod as number) ?? 9,
        (p?.rsiOversold as number) ?? 35,
        (p?.rsiOverbought as number) ?? 65,
        (p?.volumeMaPeriod as number) ?? 10,
        (p?.volumeMultiplier as number) ?? 1.2,
        (p?.profitTargetPct as number) ?? 2.5,
        (p?.stopLossPct as number) ?? 1.0,
        (p?.minVotes as number) ?? 2,
      ),
  ],
  // ── Extended Basic Strategies ──────────────────────────────────
  [
    "trix-momentum",
    (p) => trixMomentum((p?.period as number) ?? 15, (p?.signalPeriod as number) ?? 9),
  ],
  [
    "mfi-oscillator",
    (p) =>
      mfiOscillator(
        (p?.period as number) ?? 14,
        (p?.oversold as number) ?? 20,
        (p?.overbought as number) ?? 80,
      ),
  ],
  ["aroon-trend", (p) => aroonTrend((p?.period as number) ?? 25)],
  [
    "cmf-divergence",
    (p) => cmfDivergence((p?.period as number) ?? 20, (p?.threshold as number) ?? 0.05),
  ],
  ["elder-ray", (p) => elderRayStrategy((p?.emaPeriod as number) ?? 13)],
  [
    "supertrend",
    (p) =>
      supertrendStrategy((p?.atrPeriod as number) ?? 10, (p?.multiplier as number) ?? 3.0),
  ],
  [
    "hull-ma-crossover",
    (p) =>
      hullMaCrossover((p?.fastPeriod as number) ?? 9, (p?.slowPeriod as number) ?? 21),
  ],
  [
    "dema-crossover",
    (p) =>
      demaCrossover((p?.fastPeriod as number) ?? 10, (p?.slowPeriod as number) ?? 30),
  ],
  [
    "triple-ma-crossover",
    (p) =>
      tripleMaCrossover(
        (p?.shortPeriod as number) ?? 5,
        (p?.midPeriod as number) ?? 20,
        (p?.longPeriod as number) ?? 60,
      ),
  ],
  [
    "ma-envelope",
    (p) => maEnvelopeStrategy((p?.period as number) ?? 20, (p?.percent as number) ?? 3),
  ],
  [
    "connors-rsi",
    (p) =>
      connorsRsiStrategy(
        (p?.rsiPeriod as number) ?? 3,
        (p?.streakPeriod as number) ?? 2,
        (p?.percentRankPeriod as number) ?? 100,
        (p?.oversold as number) ?? 15,
        (p?.overbought as number) ?? 85,
      ),
  ],
  [
    "kama-trend",
    (p) =>
      kamaStrategy(
        (p?.period as number) ?? 10,
        (p?.fastPeriod as number) ?? 2,
        (p?.slowPeriod as number) ?? 30,
      ),
  ],
  [
    "demarker",
    (p) =>
      demarkerStrategy(
        (p?.period as number) ?? 14,
        (p?.oversold as number) ?? 0.3,
        (p?.overbought as number) ?? 0.7,
      ),
  ],
  // ── Extended CTA Strategies ────────────────────────────────────
  [
    "cta-awesome-oscillator",
    (p) =>
      ctaAwesomeOscillator((p?.fastPeriod as number) ?? 5, (p?.slowPeriod as number) ?? 34),
  ],
  ["cta-elder-force", (p) => ctaElderForceIndex((p?.emaPeriod as number) ?? 13)],
  [
    "cta-demarker",
    (p) =>
      ctaDemarkerCta(
        (p?.period as number) ?? 14,
        (p?.oversold as number) ?? 0.3,
        (p?.overbought as number) ?? 0.7,
      ),
  ],
  [
    "cta-ultimate-oscillator",
    (p) =>
      ctaUltimateOscillatorStrategy(
        (p?.period1 as number) ?? 7,
        (p?.period2 as number) ?? 14,
        (p?.period3 as number) ?? 28,
        (p?.oversold as number) ?? 30,
        (p?.overbought as number) ?? 70,
      ),
  ],
  [
    "cta-adx-breakout",
    (p) =>
      ctaAdxBreakout(
        (p?.period as number) ?? 14,
        (p?.adxThreshold as number) ?? 25,
        (p?.diThreshold as number) ?? 20,
      ),
  ],
  [
    "cta-trix-trend",
    (p) => ctaTrixTrend((p?.period as number) ?? 12, (p?.signalPeriod as number) ?? 9),
  ],
  ["cta-hull-ma-trend", (p) => ctaHullMaTrend((p?.period as number) ?? 16)],
  [
    "cta-mass-index",
    (p) => ctaMassIndex((p?.emaPeriod as number) ?? 9, (p?.threshold as number) ?? 27),
  ],
  [
    "cta-chaikin-oscillator",
    (p) =>
      ctaChaikinOscillator((p?.fastPeriod as number) ?? 3, (p?.slowPeriod as number) ?? 10),
  ],
  [
    "cta-coppock",
    (p) =>
      ctaCoppock(
        (p?.longRoc as number) ?? 14,
        (p?.shortRoc as number) ?? 11,
        (p?.wmaPeriod as number) ?? 10,
      ),
  ],
  [
    "cta-rsi-divergence",
    (p) =>
      ctaRsiDivergence(
        (p?.rsiPeriod as number) ?? 14,
        (p?.lookback as number) ?? 20,
        (p?.oversold as number) ?? 30,
        (p?.overbought as number) ?? 70,
      ),
  ],
  [
    "cta-volume-breakout",
    (p) =>
      ctaVolumeBreakout(
        (p?.maPeriod as number) ?? 20,
        (p?.volMultiplier as number) ?? 2.0,
        (p?.lookback as number) ?? 20,
      ),
  ],
  [
    "cta-adaptive-ma",
    (p) =>
      ctaAdaptiveMa(
        (p?.period as number) ?? 10,
        (p?.fastPeriod as number) ?? 2,
        (p?.slowPeriod as number) ?? 30,
      ),
  ],
  [
    "cta-ma-envelope",
    (p) => ctaMaEnvelope((p?.period as number) ?? 20, (p?.percent as number) ?? 2.5),
  ],
  [
    "cta-connors-rsi",
    (p) =>
      ctaConnorsRsi(
        (p?.rsiPeriod as number) ?? 3,
        (p?.streakPeriod as number) ?? 2,
        (p?.percentRankPeriod as number) ?? 100,
        (p?.oversold as number) ?? 10,
        (p?.overbought as number) ?? 90,
      ),
  ],
  ["cta-aroon-oscillator", (p) => ctaAroonOscillator((p?.period as number) ?? 25)],
  [
    "cta-mfi-trend",
    (p) =>
      ctaMfiTrend(
        (p?.period as number) ?? 14,
        (p?.oversold as number) ?? 20,
        (p?.overbought as number) ?? 80,
      ),
  ],
  [
    "cta-dema-crossover",
    (p) =>
      ctaDemaCrossover((p?.fastPeriod as number) ?? 10, (p?.slowPeriod as number) ?? 30),
  ],
  [
    "cta-triple-ma",
    (p) =>
      ctaTripleMa(
        (p?.shortPeriod as number) ?? 5,
        (p?.midPeriod as number) ?? 20,
        (p?.longPeriod as number) ?? 60,
      ),
  ],
  [
    "cta-supertrend",
    (p) =>
      ctaSupertrendCta((p?.atrPeriod as number) ?? 10, (p?.multiplier as number) ?? 3.0),
  ],
  [
    "swing-overlay",
    (p) =>
      swingOverlay(
        (p?.coreRatio as number) ?? 0.6,
        (p?.gridPct as number) ?? 3.0,
        (p?.gridCount as number) ?? 3,
        (p?.biasBuy as number) ?? -5.0,
        (p?.biasSell as number) ?? 3.5,
        (p?.volMult as number) ?? 1.5,
        (p?.maxHoldBars as number) ?? 8,
        (p?.maPeriod as number) ?? 20,
      ),
  ],
]);

// ── Register vnpy/backtrader strategy aliases → builtin equivalents ───
// So composed strategies can reference vnpy-*/backtrader IDs as sub-strategies.
for (const [id, fb] of [
  ...Object.entries(vnpyBuiltinFallback),
  ...Object.entries(backtraderBuiltinFallback),
]) {
  if (!strategyMap.has(id)) {
    const builtinId = fb.id;
    const mapParams = fb.mapParams;
    strategyMap.set(id, (p) => {
      const mapped = mapParams(p ?? {});
      const factory = strategyMap.get(builtinId);
      if (!factory) throw new Error(`Builtin fallback ${builtinId} not registered`);
      return factory(mapped);
    });
  }
}

import {
  composeStrategy,
  type CompositionConfig,
  type SubStrategyConfig,
} from "./dynamic-composer.js";
import { buildRuleStrategy, type RuleStrategyConfig } from "./rule-strategy.js";
import { builtinStrategies } from "./strategy-definitions.js";

// ── Composed strategy runtime registry ───────────────────────────

const composedStrategyMap = new Map<string, CompositionConfig>();
const composedDefinitions = new Map<string, StrategyDefinition>();

/**
 * Register a dynamically composed strategy so it can be resolved by
 * `listStrategies()`, `getStrategy()`, and `runBacktest()`.
 */
export function registerComposedStrategy(config: CompositionConfig): void {
  composedStrategyMap.set(config.id, config);

  // Build a synthetic StrategyDefinition visible to the UI / tool layer
  composedDefinitions.set(config.id, {
    id: config.id,
    name: config.name,
    description: config.description ?? `动态组合策略 (${config.method})`,
    version: "1.0.0",
    engine: "builtin",
    entryFile: "",
    parameters: [],
    markets: ["cn_stock"],
  });

  // Register a factory that resolves sub-strategies and composes them
  strategyMap.set(config.id, (params) => {
    const subs = config.strategies.map((sub: SubStrategyConfig) => {
      const mergedParams = { ...sub.params, ...params };
      const factory = strategyMap.get(sub.strategyId);
      if (!factory) throw new Error(`组合引用的子策略不存在: ${sub.strategyId}`);
      return { config: sub, strategy: factory(mergedParams) };
    });
    return composeStrategy(config, subs);
  });
}

/** Unregister a composed strategy. */
export function unregisterComposedStrategy(id: string): boolean {
  const had = composedStrategyMap.delete(id);
  composedDefinitions.delete(id);
  strategyMap.delete(id);
  return had;
}

/** Get a composed strategy config by ID. */
export function getComposedStrategy(id: string): CompositionConfig | undefined {
  return composedStrategyMap.get(id);
}

/** List all composed strategy configs. */
export function listComposedStrategies(): CompositionConfig[] {
  return [...composedStrategyMap.values()];
}

// ── Rule-based (custom) strategy runtime registry ────────────────

const ruleStrategyMap = new Map<string, RuleStrategyConfig>();
const ruleDefinitions = new Map<string, StrategyDefinition>();

/**
 * Register a user-defined rule-based strategy so it can be resolved by
 * `listStrategies()`, `getStrategy()`, and `runBacktest()`.
 */
export function registerRuleStrategy(config: RuleStrategyConfig): void {
  ruleStrategyMap.set(config.id, config);

  // Build a synthetic StrategyDefinition visible to the UI / tool layer
  ruleDefinitions.set(config.id, {
    id: config.id,
    name: config.name,
    description: config.description ?? `自定义规则策略`,
    version: "1.0.0",
    engine: "builtin",
    entryFile: "",
    parameters: [],
    markets: ["cn_stock"],
  });

  // Register a factory that builds the rule strategy
  strategyMap.set(config.id, () => buildRuleStrategy(config));
}

/** Unregister a rule-based strategy. */
export function unregisterRuleStrategy(id: string): boolean {
  const had = ruleStrategyMap.delete(id);
  ruleDefinitions.delete(id);
  strategyMap.delete(id);
  return had;
}

/** Get a rule strategy config by ID. */
export function getRuleStrategy(id: string): RuleStrategyConfig | undefined {
  return ruleStrategyMap.get(id);
}

/** List all rule strategy configs. */
export function listRuleStrategies(): RuleStrategyConfig[] {
  return [...ruleStrategyMap.values()];
}

// ── Registry ─────────────────────────────────────────────────────

export function listStrategies(): StrategyDefinition[] {
  // Only include builtin strategies that have a registered factory or use backtrader/vnpy engine
  const available = builtinStrategies.filter(
    (s) =>
      !s.disabled &&
      (s.engine === "backtrader" || s.engine === "vnpy" || strategyMap.has(s.id)),
  );
  return [...available, ...composedDefinitions.values(), ...ruleDefinitions.values()];
}

export function getStrategy(id: string): StrategyDefinition | undefined {
  return (
    builtinStrategies.find((s) => s.id === id) ??
    composedDefinitions.get(id) ??
    ruleDefinitions.get(id)
  );
}

/** Check whether a strategy ID has a registered builtin factory (composable). */
export function hasStrategyFactory(id: string): boolean {
  return strategyMap.has(id);
}

// ── Runner ───────────────────────────────────────────────────────

export async function runBacktest(
  request: BacktestRequest,
  config: QuantTradingConfig,
  sharedProvider?: MarketDataProvider,
): Promise<BacktestResult> {
  const strategy = getStrategy(request.strategyId);
  const engine = request.engine ?? strategy?.engine ?? config.backtestEngine ?? "builtin";

  if (engine === "backtrader" || strategy?.engine === "backtrader") {
    const fallback = backtraderBuiltinFallback[request.strategyId];
    try {
      return await runBacktraderBacktest(request, config);
    } catch (err: unknown) {
      if (!fallback) throw err;
      // backtrader unavailable — fallback to builtin equivalent
      const mappedParams = fallback.mapParams(request.parameters ?? {});
      const builtinStrategy = resolveBuiltinStrategy(fallback.id, mappedParams);
      if (request.symbols.length > 1) {
        return runPortfolioBacktest(builtinStrategy, request, config, sharedProvider);
      }
      return runBuiltinBacktest(builtinStrategy, request, config, sharedProvider);
    }
  }

  if (engine === "vnpy" || strategy?.engine === "vnpy") {
    const fallback = vnpyBuiltinFallback[request.strategyId];
    try {
      return await runVnpyBacktest(request, config);
    } catch (err: unknown) {
      if (!fallback) throw err;
      // vnpy unavailable or failed — fallback to builtin equivalent
      const mappedParams = fallback.mapParams(request.parameters ?? {});
      const builtinStrategy = resolveBuiltinStrategy(fallback.id, mappedParams);
      if (request.symbols.length > 1) {
        return runPortfolioBacktest(builtinStrategy, request, config, sharedProvider);
      }
      return runBuiltinBacktest(builtinStrategy, request, config, sharedProvider);
    }
  }

  // Builtin engine
  const builtinStrategy = resolveBuiltinStrategy(request.strategyId, request.parameters);
  // Multi-symbol: run portfolio backtest (equal-weight per symbol)
  if (request.symbols.length > 1) {
    return runPortfolioBacktest(builtinStrategy, request, config, sharedProvider);
  }
  return runBuiltinBacktest(builtinStrategy, request, config, sharedProvider);
}

export function resolveBuiltinStrategy(strategyId: string, params?: Record<string, unknown>) {
  const factory = strategyMap.get(strategyId);
  if (!factory) {
    const known = [...strategyMap.keys()].join(", ");
    throw new Error(`未知策略: "${strategyId}"。可用策略: ${known}`);
  }
  return factory(params);
}
