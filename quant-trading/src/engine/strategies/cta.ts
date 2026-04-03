/**
 * CTA (Commodity Trading Advisor) strategies – barrel re-export.
 *
 * Implementations are split across:
 *   cta-trend.ts     – trend-following & channel breakout (8)
 *   cta-channel.ts   – channel / oscillator (5)
 *   cta-indicator.ts – indicator-based (12)
 *   cta-pattern.ts   – pattern recognition & advanced momentum (10)
 */
export {
  ctaTrendFollowing,
  ctaDualThrust,
  spreadArbitrage,
  ctaTurtleBreakout,
  ctaMacdTrend,
  ctaRsiReversal,
  ctaBollingerBreakout,
  ctaKeltnerChannel,
} from "./cta-trend.js";

export {
  ctaRBreaker,
  ctaAberration,
  ctaTtmSqueeze,
  ctaDmiAdx,
  ctaParabolicSar,
} from "./cta-channel.js";

export {
  ctaVolatilityTarget,
  ctaMomentumRotation,
  ctaWilliamsR,
  ctaObvTrend,
  ctaIchimoku,
  ctaKdj,
  ctaMeanReversionZScore,
  ctaGridTrading,
  ctaMultiMaConvergence,
  ctaDonchianMidReversion,
  ctaPivotPoint,
  ctaChandelierExit,
} from "./cta-indicator.js";

export {
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
} from "./cta-pattern.js";
