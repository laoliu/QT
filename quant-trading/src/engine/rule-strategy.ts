/**
 * Rule-based Strategy Engine — lets users define custom entry/exit logic
 * at runtime via a declarative JSON DSL, without writing code.
 *
 * Users describe indicator conditions (e.g. "SMA5 crosses above SMA20 AND RSI < 30")
 * through structured rules. The engine computes the referenced indicators once per
 * backtest init, then evaluates the conditions on each bar.
 *
 * This is complementary to the dynamic-composer (which combines existing strategies
 * via voting/gated/weighted). Rule strategies define *new* atomic logic.
 *
 * Supported indicators: sma, ema, rsi, macd, bollinger, atr, cci, roc, obv
 * Supported conditions: crosses_above, crosses_below, above, below, between
 * Supported combinators: all (AND), any (OR)
 */
import type { TradingSignal } from "../types.js";
import type { BuiltinStrategy, BuiltinStrategyContext } from "./strategies/indicators.js";
export type { BuiltinStrategyContext } from "./strategies/indicators.js";
import {
  atr,
  bollingerBands,
  cci,
  ema,
  getCloses,
  macd,
  obv,
  roc,
  rsi,
  sma,
} from "./strategies/indicators.js";

// ── DSL Types ────────────────────────────────────────────────────

/**
 * An indicator reference that produces a numeric time series.
 * Example: { indicator: "sma", period: 20 }
 *          { indicator: "rsi", period: 14 }
 *          { indicator: "macd", period: [12, 26, 9], output: "histogram" }
 *          { indicator: "bollinger", period: 20, param: 2, output: "upper" }
 */
export interface IndicatorRef {
  /** Indicator function name. */
  indicator:
    | "sma"
    | "ema"
    | "rsi"
    | "macd"
    | "bollinger"
    | "atr"
    | "cci"
    | "roc"
    | "obv"
    | "price"
    | "volume";
  /** Primary period (ignored for price/volume/obv). */
  period?: number;
  /** Secondary parameter (stdDev for bollinger, etc.). */
  param?: number;
  /** For multi-output indicators: which output to use.
   *  macd: "line" | "signal" | "histogram" (default "line")
   *  bollinger: "middle" | "upper" | "lower" (default "middle")
   */
  output?: string;
}

/** Comparison operator for a single condition. */
export type ConditionOp =
  | "above" // current value > threshold/other
  | "below" // current value < threshold/other
  | "crosses_above" // was <= on prev bar, now > (golden cross)
  | "crosses_below" // was >= on prev bar, now < (death cross)
  | "between"; // value is between two thresholds

/**
 * A single condition: compare indicator A against indicator B or a constant.
 *
 * Examples:
 *   { left: {indicator:"sma",period:5}, op: "crosses_above", right: {indicator:"sma",period:20} }
 *   { left: {indicator:"rsi",period:14}, op: "below", value: 30 }
 *   { left: {indicator:"price"}, op: "above", right: {indicator:"bollinger",period:20,param:2,output:"upper"} }
 */
export interface RuleCondition {
  /** Left-hand side indicator. */
  left: IndicatorRef;
  /** Comparison operator. */
  op: ConditionOp;
  /** Right-hand side: another indicator (for cross comparisons). */
  right?: IndicatorRef;
  /** Right-hand side: a constant numeric value (for threshold comparisons). */
  value?: number;
  /** Upper bound for "between" operator (lower bound is `value`). */
  upperValue?: number;
}

/**
 * A rule group that combines conditions with a logical operator.
 *
 * Example (buy when SMA5 > SMA20 AND RSI < 30):
 *   { logic: "all", conditions: [
 *       { left: {indicator:"sma",period:5}, op: "above", right: {indicator:"sma",period:20} },
 *       { left: {indicator:"rsi",period:14}, op: "below", value: 30 }
 *   ]}
 */
export interface RuleGroup {
  /** "all" = AND (every condition must be true), "any" = OR (at least one). */
  logic: "all" | "any";
  /** Conditions to evaluate. */
  conditions: RuleCondition[];
}

/**
 * Full rule-based strategy configuration.
 * Defines entry (buy) and exit (sell) rules separately.
 */
export interface RuleStrategyConfig {
  /** Unique ID for this custom strategy. */
  id: string;
  /** Display name. */
  name: string;
  /** Description. */
  description?: string;
  /** Entry (buy) rules — signal a buy when these conditions are met and no position is held. */
  buyRules: RuleGroup;
  /** Exit (sell) rules — signal a sell when these conditions are met and a position is held. */
  sellRules: RuleGroup;
  /** ISO timestamp. */
  createdAt?: string;
  updatedAt?: string;
}

// ── Indicator Resolution ─────────────────────────────────────────

/** Cache key for a resolved indicator series. */
function indicatorKey(ref: IndicatorRef): string {
  return `${ref.indicator}:${ref.period ?? ""}:${ref.param ?? ""}:${ref.output ?? ""}`;
}

/**
 * Resolve an IndicatorRef to a numeric time series.
 * Caches results so the same indicator is computed only once per backtest.
 */
function resolveIndicator(
  ref: IndicatorRef,
  ctx: BuiltinStrategyContext,
  cache: Map<string, number[]>,
): number[] {
  const key = indicatorKey(ref);
  const cached = cache.get(key);
  if (cached) return cached;

  const closes = getCloses(ctx);
  let result: number[];

  switch (ref.indicator) {
    case "price":
      result = closes;
      break;

    case "volume":
      result = ctx.bars.map((b) => b.volume);
      break;

    case "sma":
      result = sma(closes, ref.period ?? 20);
      break;

    case "ema":
      result = ema(closes, ref.period ?? 20);
      break;

    case "rsi":
      result = rsi(closes, ref.period ?? 14);
      break;

    case "atr":
      result = atr(ctx.bars, ref.period ?? 14);
      break;

    case "cci":
      result = cci(ctx.bars, ref.period ?? 20);
      break;

    case "roc":
      result = roc(closes, ref.period ?? 12);
      break;

    case "obv":
      result = obv(ctx.bars);
      break;

    case "macd": {
      const fast = ref.period ?? 12;
      const slow = ref.param ?? 26;
      const sig = 9;
      const m = macd(closes, fast, slow, sig);
      const output = ref.output ?? "line";
      if (output === "signal") result = m.signalLine;
      else if (output === "histogram") result = m.histogram;
      else result = m.macdLine;
      break;
    }

    case "bollinger": {
      const period = ref.period ?? 20;
      const stdDev = ref.param ?? 2;
      const bb = bollingerBands(closes, period, stdDev);
      const output = ref.output ?? "middle";
      if (output === "upper") result = bb.upper;
      else if (output === "lower") result = bb.lower;
      else result = bb.middle;
      break;
    }

    default:
      // Unknown indicator — return NaN series
      result = new Array(ctx.bars.length).fill(NaN);
  }

  cache.set(key, result);
  return result;
}

// ── Condition Evaluation ─────────────────────────────────────────

/**
 * Evaluate a single condition at bar index `i`.
 * Returns true if the condition is met.
 */
function evalCondition(
  cond: RuleCondition,
  i: number,
  ctx: BuiltinStrategyContext,
  cache: Map<string, number[]>,
): boolean {
  const leftSeries = resolveIndicator(cond.left, ctx, cache);
  const leftVal = leftSeries[i];
  if (leftVal == null || Number.isNaN(leftVal)) return false;

  // Resolve right-hand side
  let rightVal: number;
  let rightPrevVal: number | undefined;

  if (cond.right) {
    const rightSeries = resolveIndicator(cond.right, ctx, cache);
    const rv = rightSeries[i];
    if (rv == null || Number.isNaN(rv)) return false;
    rightVal = rv;
    if (i > 0) rightPrevVal = rightSeries[i - 1];
  } else if (cond.value != null) {
    rightVal = cond.value;
    rightPrevVal = cond.value; // constant doesn't change
  } else {
    return false; // no right-hand side specified
  }

  const leftPrevVal = i > 0 ? leftSeries[i - 1] : undefined;

  switch (cond.op) {
    case "above":
      return leftVal > rightVal;

    case "below":
      return leftVal < rightVal;

    case "crosses_above":
      if (leftPrevVal == null || Number.isNaN(leftPrevVal)) return false;
      if (rightPrevVal == null || Number.isNaN(rightPrevVal)) return false;
      return leftPrevVal <= rightPrevVal && leftVal > rightVal;

    case "crosses_below":
      if (leftPrevVal == null || Number.isNaN(leftPrevVal)) return false;
      if (rightPrevVal == null || Number.isNaN(rightPrevVal)) return false;
      return leftPrevVal >= rightPrevVal && leftVal < rightVal;

    case "between":
      if (cond.upperValue == null) return false;
      return leftVal >= rightVal && leftVal <= cond.upperValue;

    default:
      return false;
  }
}

/**
 * Evaluate a rule group (AND/OR combination of conditions).
 */
function evalGroup(
  group: RuleGroup,
  i: number,
  ctx: BuiltinStrategyContext,
  cache: Map<string, number[]>,
): boolean {
  if (group.conditions.length === 0) return false;

  if (group.logic === "all") {
    return group.conditions.every((c) => evalCondition(c, i, ctx, cache));
  }
  // "any"
  return group.conditions.some((c) => evalCondition(c, i, ctx, cache));
}

// ── Strategy Builder ─────────────────────────────────────────────

/**
 * Build a BuiltinStrategy from a RuleStrategyConfig.
 * The returned strategy can be registered in the strategy registry
 * and used with backtest/live-trading just like any builtin strategy.
 */
export function buildRuleStrategy(config: RuleStrategyConfig): BuiltinStrategy {
  const cache = new Map<string, number[]>();

  return {
    name: config.name,

    init(_ctx) {
      // Clear indicator cache for fresh backtest run
      cache.clear();
    },

    next(ctx) {
      const i = ctx.index;
      if (i < 1) return null; // need at least 1 previous bar for crossover checks

      const bar = ctx.bars[i]!;

      // Check buy rules (only when flat)
      if (ctx.position <= 0 && evalGroup(config.buyRules, i, ctx, cache)) {
        // Build a human-readable reason from the conditions
        const reason = describeGroup(config.buyRules, "买入");
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy" as const,
          price: bar.close,
          reason,
          strategyId: config.id,
        };
      }

      // Check sell rules (only when holding)
      if (ctx.position > 0 && evalGroup(config.sellRules, i, ctx, cache)) {
        const reason = describeGroup(config.sellRules, "卖出");
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell" as const,
          price: bar.close,
          reason,
          strategyId: config.id,
        };
      }

      return null;
    },
  };
}

// ── Human-Readable Description ───────────────────────────────────

function describeIndicator(ref: IndicatorRef): string {
  switch (ref.indicator) {
    case "price":
      return "价格";
    case "volume":
      return "成交量";
    case "sma":
      return `SMA${ref.period ?? 20}`;
    case "ema":
      return `EMA${ref.period ?? 20}`;
    case "rsi":
      return `RSI${ref.period ?? 14}`;
    case "atr":
      return `ATR${ref.period ?? 14}`;
    case "cci":
      return `CCI${ref.period ?? 20}`;
    case "roc":
      return `ROC${ref.period ?? 12}`;
    case "obv":
      return "OBV";
    case "macd": {
      const out = ref.output ?? "line";
      const label = out === "histogram" ? "柱" : out === "signal" ? "信号线" : "线";
      return `MACD${label}`;
    }
    case "bollinger": {
      const out = ref.output ?? "middle";
      const label = out === "upper" ? "上轨" : out === "lower" ? "下轨" : "中轨";
      return `BOLL${ref.period ?? 20}${label}`;
    }
    default:
      return ref.indicator;
  }
}

function describeOp(op: ConditionOp): string {
  switch (op) {
    case "above":
      return ">";
    case "below":
      return "<";
    case "crosses_above":
      return "上穿";
    case "crosses_below":
      return "下穿";
    case "between":
      return "介于";
    default:
      return op;
  }
}

function describeCondition(cond: RuleCondition): string {
  const left = describeIndicator(cond.left);
  const op = describeOp(cond.op);
  if (cond.right) {
    return `${left} ${op} ${describeIndicator(cond.right)}`;
  }
  if (cond.op === "between" && cond.upperValue != null) {
    return `${left} ${op} ${cond.value ?? "?"}~${cond.upperValue}`;
  }
  return `${left} ${op} ${cond.value ?? "?"}`;
}

function describeGroup(group: RuleGroup, prefix: string): string {
  const joiner = group.logic === "all" ? " 且 " : " 或 ";
  const parts = group.conditions.map(describeCondition);
  return `[自定义规则${prefix}] ${parts.join(joiner)}`;
}

// ── Validation ───────────────────────────────────────────────────

const VALID_INDICATORS = new Set([
  "sma",
  "ema",
  "rsi",
  "macd",
  "bollinger",
  "atr",
  "cci",
  "roc",
  "obv",
  "price",
  "volume",
]);
const VALID_OPS = new Set(["above", "below", "crosses_above", "crosses_below", "between"]);

/** Validate a RuleStrategyConfig. Returns an error message or null if valid. */
export function validateRuleConfig(config: RuleStrategyConfig): string | null {
  if (!config.id || typeof config.id !== "string") return "缺少策略 ID";
  if (!config.name || typeof config.name !== "string") return "缺少策略名称";
  if (!config.buyRules?.conditions?.length && !config.sellRules?.conditions?.length) {
    return "至少需要定义买入或卖出规则";
  }

  const validateGroup = (group: RuleGroup, label: string): string | null => {
    if (group.logic !== "all" && group.logic !== "any") {
      return `${label}: logic 必须是 "all" 或 "any"`;
    }
    for (let i = 0; i < group.conditions.length; i++) {
      const c = group.conditions[i]!;
      if (!c.left?.indicator || !VALID_INDICATORS.has(c.left.indicator)) {
        return `${label} 条件${i + 1}: 无效的左侧指标 "${c.left?.indicator}"`;
      }
      if (!c.op || !VALID_OPS.has(c.op)) {
        return `${label} 条件${i + 1}: 无效的比较运算符 "${c.op}"`;
      }
      if (c.right && !VALID_INDICATORS.has(c.right.indicator)) {
        return `${label} 条件${i + 1}: 无效的右侧指标 "${c.right.indicator}"`;
      }
      if (!c.right && c.value == null) {
        return `${label} 条件${i + 1}: 需要 right（指标）或 value（数值）`;
      }
      if (c.op === "between" && c.upperValue == null) {
        return `${label} 条件${i + 1}: between 运算符需要 upperValue`;
      }
    }
    return null;
  };

  if (config.buyRules) {
    const err = validateGroup(config.buyRules, "买入规则");
    if (err) return err;
  }
  if (config.sellRules) {
    const err = validateGroup(config.sellRules, "卖出规则");
    if (err) return err;
  }

  return null;
}
