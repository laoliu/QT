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
} from "./builtin-engine.js";
import type { BuiltinStrategy } from "./builtin-engine.js";

type StrategyFactory = (params?: Record<string, unknown>) => BuiltinStrategy;

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
    "kdj",
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
]);
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
  // Only include builtin strategies that have a registered factory or use backtrader engine
  const available = builtinStrategies.filter(
    (s) => s.engine === "backtrader" || strategyMap.has(s.id),
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
    return runBacktraderBacktest(request, config);
  }

  // Builtin engine
  const builtinStrategy = resolveBuiltinStrategy(request.strategyId, request.parameters);
  // Multi-symbol: run portfolio backtest (equal-weight per symbol)
  if (request.symbols.length > 1) {
    return runPortfolioBacktest(builtinStrategy, request, config, sharedProvider);
  }
  return runBuiltinBacktest(builtinStrategy, request, config, sharedProvider);
}

function resolveBuiltinStrategy(strategyId: string, params?: Record<string, unknown>) {
  const factory = strategyMap.get(strategyId);
  if (!factory) {
    const known = [...strategyMap.keys()].join(", ");
    throw new Error(`未知策略: "${strategyId}"。可用策略: ${known}`);
  }
  return factory(params);
}
