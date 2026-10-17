/**
 * options_strategy tool — AI-callable tool for option strategy analysis.
 *
 * Covers strategy recommendation, payoff calculation, scenario analysis,
 * comparison, optimal strike finding, and Greeks aggregation.
 * Engine delegates to option_strategy_engine.py + greeks_engine.py.
 */
import type { QuantTradingRuntime } from "../runtime.js";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

// ── Python result shapes ─────────────────────────────────────────
interface StrategyTemplate {
  id: string;
  name: string;
  nameZh: string;
  legs: number;
  direction: string;
  riskProfile: string;
  description: string;
}

interface StrategyRecommendation {
  strategyId: string;
  name: string;
  nameZh: string;
  score: number;
  reason: string;
  legs: Array<{
    optionType: string;
    side: string;
    strike: number;
    quantity: number;
    premium?: number;
  }>;
  maxProfit?: number | string;
  maxLoss?: number | string;
  breakeven?: number[];
}

interface PayoffResult {
  strategyId: string;
  legs: Array<{
    optionType: string;
    side: string;
    strike: number;
    quantity: number;
    premium: number;
  }>;
  payoffCurve: Array<{ price: number; pnl: number }>;
  maxProfit: number | string;
  maxLoss: number | string;
  breakeven: number[];
  netPremium: number;
}

interface ScenarioResult {
  scenarios: Array<{
    label: string;
    spotPrice: number;
    pnl: number;
    greeks?: Record<string, number>;
  }>;
}

interface CompareResult {
  strategies: Array<{
    strategyId: string;
    name: string;
    maxProfit: number | string;
    maxLoss: number | string;
    breakeven: number[];
    netPremium: number;
    riskReward?: number;
  }>;
}

export function createOptionsStrategyTool(api: QuantTradingRuntime, config: QuantTradingConfig) {
  return {
    name: "options_strategy",
    description:
      "期权策略分析工具：策略推荐、收益图计算、情景分析、策略比较、最优行权价。\n" +
      "支持20+种策略：看涨/看跌、牛市价差、熊市价差、跨式、宽跨式、\n" +
      "Iron Condor、Iron Butterfly、Calendar Spread、Ratio Spread 等。\n\n" +
      "action=recommend 根据市场观点推荐策略，\n" +
      "action=payoff 计算策略收益图，\n" +
      "action=scenario 情景分析（不同价格/时间/波动率），\n" +
      "action=compare 多策略对比，\n" +
      "action=optimal_strike 寻找最优行权价，\n" +
      "action=greeks 计算组合希腊值，\n" +
      "action=list 列出可用策略模板。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["recommend", "payoff", "scenario", "compare", "optimal_strike", "greeks", "list"],
          description:
            "操作类型: recommend=策略推荐, payoff=收益图, scenario=情景分析, " +
            "compare=策略比较, optimal_strike=最优行权价, greeks=希腊值, list=策略列表",
        },
        // ── Shared fields ──────────────────────────────────────
        underlying: {
          type: "string",
          description: "标的代码（大部分操作都需要）",
        },
        spotPrice: {
          type: "number",
          description: "标的现价（若不提供则自动获取）",
        },
        // ── recommend fields ───────────────────────────────────
        outlook: {
          type: "string",
          enum: ["bullish", "bearish", "neutral", "volatile"],
          description:
            "市场观点（action=recommend）: bullish 看涨, bearish 看跌, neutral 中性, volatile 波动",
        },
        riskTolerance: {
          type: "string",
          enum: ["low", "medium", "high"],
          description: "风险承受度（action=recommend），默认 medium",
        },
        capitalBudget: {
          type: "number",
          description: "资金预算（action=recommend），用于筛选可负担的策略",
        },
        targetReturn: {
          type: "number",
          description: "目标收益率（action=recommend），如 0.1 表示 10%",
        },
        // ── payoff fields ──────────────────────────────────────
        strategyId: {
          type: "string",
          description:
            "策略ID（action=payoff/scenario/compare/optimal_strike），如 bull_call_spread、iron_condor",
        },
        legs: {
          type: "string",
          description:
            "自定义腿（JSON数组），每腿: {optionType, side, strike, quantity, premium}。" +
            "若不提供，将根据 strategyId + spotPrice 自动构建腿。",
        },
        // ── scenario fields ────────────────────────────────────
        priceRange: {
          type: "string",
          description: "价格范围（action=scenario），如 '-10%,+10%' 或 '2.5,3.5'",
        },
        daysToExpiry: {
          type: "number",
          description: "剩余天数（多处用到）",
        },
        volatilityShock: {
          type: "number",
          description: "波动率冲击（action=scenario），如 0.05 表示 +5%",
        },
        // ── compare fields ─────────────────────────────────────
        strategies: {
          type: "string",
          description:
            "策略ID列表（action=compare），逗号分隔，如 'bull_call_spread,long_straddle,iron_condor'",
        },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      switch (action) {
        // ── List Strategy Templates ───────────────────────────
        case "list": {
          const result = await runPythonScript<StrategyTemplate[]>(
            "engines/option_strategy_engine.py",
            { action: "list_templates" },
            config,
          );
          if (!result.ok) return { error: result.error ?? "获取策略列表失败" };
          return {
            count: result.data?.length ?? 0,
            strategies: result.data,
          };
        }

        // ── Recommend ─────────────────────────────────────────
        case "recommend": {
          const outlook = args.outlook as string;
          if (!outlook) {
            return {
              error: "请提供市场观点 (outlook): bullish/bearish/neutral/volatile",
            };
          }

          const result = await runPythonScript<StrategyRecommendation[]>(
            "engines/option_strategy_engine.py",
            {
              action: "recommend",
              outlook,
              underlyingPrice: args.spotPrice,
              riskTolerance: (args.riskTolerance as string) ?? "medium",
              capitalBudget: args.capitalBudget,
              targetReturn: args.targetReturn,
              underlying: args.underlying,
              riskFreeRate: config.optionsConfig?.riskFreeRate ?? 0.025,
            },
            config,
          );
          if (!result.ok) return { error: result.error ?? "策略推荐失败" };

          const recs = result.data ?? [];
          return {
            outlook,
            count: recs.length,
            recommendations: recs.map((r) => {
              const rec = r as unknown as Record<string, unknown>;
              return {
                strategy: rec.id ?? rec.strategyId,
                name: rec.name ?? rec.nameZh,
                score: rec.score,
                reason: rec.description ?? rec.reason,
                outlook: rec.outlook,
                risk: rec.risk,
                numLegs: rec.numLegs,
                legs: rec.legs,
                maxProfit: rec.maxProfit,
                maxLoss: rec.maxLoss,
                breakeven: rec.breakeven,
              };
            }),
          };
        }

        // ── Payoff ────────────────────────────────────────────
        case "payoff": {
          const strategyId = args.strategyId as string;
          const legsStr = args.legs as string | undefined;
          let parsedLegs: unknown[] | undefined;

          if (legsStr) {
            try {
              parsedLegs = JSON.parse(legsStr);
            } catch {
              return { error: "legs 格式错误，请提供合法 JSON 数组" };
            }
          }

          if (!strategyId && !parsedLegs) {
            return { error: "请提供策略ID (strategyId) 或自定义腿 (legs)" };
          }

          const result = await runPythonScript<PayoffResult>(
            "engines/option_strategy_engine.py",
            {
              action: "payoff",
              templateId: strategyId,
              legs: parsedLegs,
              underlyingPrice: args.spotPrice,
              daysToExpiry: args.daysToExpiry,
              riskFreeRate: config.optionsConfig?.riskFreeRate ?? 0.025,
            },
            config,
          );
          if (!result.ok) return { error: result.error ?? "收益图计算失败" };

          const data = result.data;
          if (!data) return { error: "收益图数据为空" };

          // Return summary + sampled curve (full curve can be huge)
          const curve = data.payoffCurve ?? [];
          const sampledCurve = curve.length > 50 ? sampleCurve(curve, 50) : curve;

          return {
            strategy: data.strategyId,
            legs: data.legs,
            maxProfit: data.maxProfit,
            maxLoss: data.maxLoss,
            breakeven: data.breakeven,
            netPremium: data.netPremium?.toFixed(4),
            payoffCurve: sampledCurve.map((p) => ({
              price: p.price.toFixed(2),
              pnl: p.pnl.toFixed(2),
            })),
          };
        }

        // ── Scenario Analysis ─────────────────────────────────
        case "scenario": {
          const strategyId = args.strategyId as string;
          if (!strategyId) return { error: "请提供策略ID (strategyId)" };

          const result = await runPythonScript<ScenarioResult>(
            "engines/option_strategy_engine.py",
            {
              action: "scenario",
              templateId: strategyId,
              underlyingPrice: args.spotPrice,
              daysToExpiry: args.daysToExpiry,
              priceRangePct: args.priceRange,
              volatilityShock: args.volatilityShock,
              riskFreeRate: config.optionsConfig?.riskFreeRate ?? 0.025,
            },
            config,
          );
          if (!result.ok) return { error: result.error ?? "情景分析失败" };

          return result.data;
        }

        // ── Compare Strategies ────────────────────────────────
        case "compare": {
          const strategiesStr = args.strategies as string;
          if (!strategiesStr) {
            return { error: "请提供策略ID列表 (strategies)，逗号分隔" };
          }
          const ids = strategiesStr.split(",").map((s) => s.trim());

          const result = await runPythonScript<CompareResult>(
            "engines/option_strategy_engine.py",
            {
              action: "compare",
              strategies: ids,
              underlyingPrice: args.spotPrice,
              daysToExpiry: args.daysToExpiry,
              riskFreeRate: config.optionsConfig?.riskFreeRate ?? 0.025,
            },
            config,
          );
          if (!result.ok) return { error: result.error ?? "策略比较失败" };

          return result.data;
        }

        // ── Optimal Strike ────────────────────────────────────
        case "optimal_strike": {
          const strategyId = args.strategyId as string;
          if (!strategyId) return { error: "请提供策略ID (strategyId)" };

          const result = await runPythonScript<{
            strategyId: string;
            optimalStrikes: number[];
            expectedPnl: number;
            winProbability: number;
          }>(
            "engines/option_strategy_engine.py",
            {
              action: "optimal_strike",
              templateId: strategyId,
              underlyingPrice: args.spotPrice,
              daysToExpiry: args.daysToExpiry,
              riskFreeRate: config.optionsConfig?.riskFreeRate ?? 0.025,
            },
            config,
          );
          if (!result.ok) return { error: result.error ?? "最优行权价计算失败" };

          return result.data;
        }

        // ── Composite Greeks ──────────────────────────────────
        case "greeks": {
          const legsStr = args.legs as string;
          if (!legsStr) {
            return { error: "请提供策略腿 (legs) JSON数组" };
          }
          let parsedLegs: unknown[];
          try {
            parsedLegs = JSON.parse(legsStr);
          } catch {
            return { error: "legs 格式错误，请提供合法 JSON 数组" };
          }

          const result = await runPythonScript<{
            delta: number;
            gamma: number;
            theta: number;
            vega: number;
            rho: number;
          }>(
            "engines/option_strategy_engine.py",
            {
              action: "greeks",
              legs: parsedLegs,
              underlyingPrice: args.spotPrice,
              daysToExpiry: args.daysToExpiry,
              riskFreeRate: config.optionsConfig?.riskFreeRate ?? 0.025,
              volatility: 0.25, // default; overridden by individual leg IVs if set
            },
            config,
          );
          if (!result.ok) return { error: result.error ?? "组合希腊值计算失败" };

          const g = result.data;
          if (!g) return { error: "希腊值数据为空" };

          return {
            delta: g.delta?.toFixed(4),
            gamma: g.gamma?.toFixed(6),
            theta: g.theta?.toFixed(4),
            vega: g.vega?.toFixed(4),
            rho: g.rho?.toFixed(4),
          };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}

// ── Helpers ──────────────────────────────────────────────────────

/** Down-sample a payoff curve to at most `n` points, keeping extremes. */
function sampleCurve(
  curve: Array<{ price: number; pnl: number }>,
  n: number,
): Array<{ price: number; pnl: number }> {
  if (curve.length <= n) return curve;
  const step = (curve.length - 1) / (n - 1);
  const result: Array<{ price: number; pnl: number }> = [];
  for (let i = 0; i < n; i++) {
    result.push(curve[Math.round(i * step)]);
  }
  return result;
}
