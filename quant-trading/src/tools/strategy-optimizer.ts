/**
 * strategy-optimizer tool — AI-callable tool for strategy parameter optimization
 * and signal backtesting validation.
 *
 * Actions:
 *   - optimize: Grid/random search over strategy parameters to find best config
 *   - validate_signals: Validate historical signals against subsequent price movements
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import { runBatchBacktest, type BatchBacktestItem } from "../services/batch-backtest.js";
import type { ComposedStrategyStore } from "../services/composed-strategy-store.js";
import type { QuantTradingConfig } from "../types.js";
import { todayCST } from "../utils/cst-time.js";

// ── Result Types ─────────────────────────────────────────────────

interface OptimizeResult {
  ok: boolean;
  error?: string;
  strategyId?: string;
  bestParams?: Record<string, number>;
  bestMetric?: number;
  metric?: string;
  method?: string;
  totalTrials?: number;
  totalCombinations?: number;
  symbols?: string[];
  dateRange?: string;
  topResults?: Array<{
    params: Record<string, number>;
    totalReturn: number;
    sharpeRatio: number;
    maxDrawdown: number;
    winRate: number;
    totalTrades: number;
  }>;
}

interface ValidateSignalsResult {
  ok: boolean;
  error?: string;
  strategyId?: string;
  totalSignals?: number;
  buySignals?: number;
  sellSignals?: number;
  accuracy?: Record<string, { buy: number | null; sell: number | null }>;
  avgReturn?: Record<string, { buy: number | null; sell: number | null }>;
  signals?: Array<{
    date: string;
    direction: string;
    price: number;
    returns: Record<string, number | null>;
    correct: Record<string, boolean | null>;
  }>;
}

// ── Tool Factory ─────────────────────────────────────────────────

export function createStrategyOptimizerTool(
  api: OpenClawPluginApi,
  config: QuantTradingConfig,
  composedStore?: ComposedStrategyStore,
) {
  return {
    name: "strategy_optimizer",
    description:
      "策略参数优化与信号回测验证。" +
      "action=optimize 使用网格搜索或随机搜索找到策略的最优参数组合。" +
      "action=validate_signals 验证策略历史信号的准确率和收益表现。" +
      "action=optimize_weights 对组合策略的子策略权重进行优化。" +
      "支持所有注册策略和组合策略。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["optimize", "validate_signals"],
          description: "操作: optimize=参数优化, validate_signals=信号验证",
        },
        strategyId: {
          type: "string",
          description: "策略ID (如 macd, rsi, kdj, turtle, grid)",
        },
        symbols: {
          type: "string",
          description: "标的代码，逗号分隔 (如 600519,000858)",
        },
        startDate: {
          type: "string",
          description: "起始日期 YYYY-MM-DD",
        },
        endDate: {
          type: "string",
          description: "结束日期 YYYY-MM-DD",
        },
        initialCash: {
          type: "number",
          description: "初始资金，默认 100000",
        },
        method: {
          type: "string",
          enum: ["grid", "random"],
          description: "优化方法: grid=网格搜索, random=随机搜索 (默认 grid)",
        },
        maxTrials: {
          type: "number",
          description: "最大试验次数 (随机搜索时使用，默认 50)",
        },
        metric: {
          type: "string",
          enum: ["sharpeRatio", "totalReturn", "annualizedReturn", "maxDrawdown", "winRate"],
          description: "优化目标指标 (默认 sharpeRatio)",
        },
        paramRanges: {
          type: "string",
          description:
            '参数范围 JSON 字符串，如 {"fast_period":{"min":5,"max":20,"step":5}}。' +
            "不提供时自动从策略元数据获取默认范围。",
        },
        holdingDays: {
          type: "string",
          description: "信号验证持仓天数，逗号分隔 (默认 1,3,5,10)",
        },
        compositionId: {
          type: "string",
          description: "组合策略 ID（optimize_weights 时使用）",
        },
        weightRange: {
          type: "string",
          description: '权重搜索范围 JSON，如 {"min":1,"max":5,"step":1}（默认 1-5 步长 1）',
        },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const strategyId = args.strategyId as string;

      if (!strategyId) {
        return { error: "请提供策略ID (strategyId)" };
      }

      const symbols = ((args.symbols as string) ?? "000300").split(",").map((s) => s.trim());
      const startDate = (args.startDate as string) ?? getDefaultStartDate();
      const endDate = (args.endDate as string) ?? todayCST();
      const initialCash = (args.initialCash as number) ?? 100000;

      if (action === "optimize") {
        return await runOptimize(
          strategyId,
          symbols,
          startDate,
          endDate,
          initialCash,
          args,
          config,
        );
      }

      if (action === "validate_signals") {
        return await runValidateSignals(
          strategyId,
          symbols,
          startDate,
          endDate,
          initialCash,
          args,
          config,
        );
      }

      if (action === "optimize_weights") {
        return await runOptimizeWeights(
          args,
          symbols,
          startDate,
          endDate,
          initialCash,
          config,
          composedStore,
        );
      }

      return { error: `未知操作: ${action}` };
    },
  };
}

// ── Optimize Handler ─────────────────────────────────────────────

async function runOptimize(
  strategyId: string,
  symbols: string[],
  startDate: string,
  endDate: string,
  initialCash: number,
  args: Record<string, unknown>,
  config: QuantTradingConfig,
) {
  const method = (args.method as string) ?? "grid";
  const maxTrials = (args.maxTrials as number) ?? 50;
  const metric = (args.metric as string) ?? "sharpeRatio";

  // Parse paramRanges if provided as JSON string
  let paramRanges: Record<string, unknown> | undefined;
  if (args.paramRanges) {
    try {
      paramRanges = JSON.parse(args.paramRanges as string);
    } catch {
      return { error: "paramRanges 解析失败，请提供有效的 JSON 字符串" };
    }
  }

  try {
    const result = await runPythonScript<OptimizeResult>(
      "engines/param_optimizer.py",
      {
        action: "optimize",
        strategyId,
        symbols,
        startDate,
        endDate,
        initialCash,
        method,
        maxTrials,
        metric,
        paramRanges,
        market: detectMarketHint(symbols),
      },
      config,
    );

    if (!result.ok || !result.data) {
      return { error: `参数优化失败: ${result.error ?? "未知错误"}` };
    }

    const data = result.data;
    if (!data.ok) {
      return { error: data.error ?? "优化失败" };
    }

    return {
      summary: {
        strategy: strategyId,
        symbols,
        period: `${startDate} ~ ${endDate}`,
        method: data.method,
        metric: data.metric,
        totalTrials: data.totalTrials,
        bestParams: data.bestParams,
        bestMetricValue: data.bestMetric,
      },
      topResults: data.topResults?.slice(0, 5).map((r, i) => ({
        rank: i + 1,
        params: r.params,
        totalReturn: `${r.totalReturn}%`,
        sharpeRatio: r.sharpeRatio,
        maxDrawdown: `${r.maxDrawdown}%`,
        winRate: `${r.winRate}%`,
        totalTrades: r.totalTrades,
      })),
    };
  } catch (err) {
    return { error: `参数优化失败: ${(err as Error).message}` };
  }
}

// ── Validate Signals Handler ─────────────────────────────────────

async function runValidateSignals(
  strategyId: string,
  symbols: string[],
  startDate: string,
  endDate: string,
  initialCash: number,
  args: Record<string, unknown>,
  config: QuantTradingConfig,
) {
  const holdingDays = ((args.holdingDays as string) ?? "1,3,5,10")
    .split(",")
    .map((s) => parseInt(s.trim(), 10))
    .filter((n) => !isNaN(n));

  try {
    const result = await runPythonScript<ValidateSignalsResult>(
      "engines/signal_validator.py",
      {
        action: "validate_signals",
        strategyId,
        symbols,
        startDate,
        endDate,
        initialCash,
        holdingDays,
        parameters: {},
        market: detectMarketHint(symbols),
      },
      config,
    );

    if (!result.ok || !result.data) {
      return { error: `信号验证失败: ${result.error ?? "未知错误"}` };
    }

    const data = result.data;
    if (!data.ok) {
      return { error: data.error ?? "验证失败" };
    }

    // Format accuracy table
    const accuracyTable = Object.entries(data.accuracy ?? {}).map(([horizon, acc]) => ({
      horizon,
      buyAccuracy: acc.buy != null ? `${acc.buy}%` : "N/A",
      sellAccuracy: acc.sell != null ? `${acc.sell}%` : "N/A",
    }));

    const avgReturnTable = Object.entries(data.avgReturn ?? {}).map(([horizon, ret]) => ({
      horizon,
      buyAvgReturn: ret.buy != null ? `${ret.buy}%` : "N/A",
      sellAvgReturn: ret.sell != null ? `${ret.sell}%` : "N/A",
    }));

    return {
      summary: {
        strategy: strategyId,
        symbols,
        totalSignals: data.totalSignals,
        buySignals: data.buySignals,
        sellSignals: data.sellSignals,
      },
      accuracy: accuracyTable,
      avgReturn: avgReturnTable,
      recentSignals: data.signals?.slice(-10).map((s) => ({
        date: s.date,
        direction: s.direction,
        price: s.price,
        returns: s.returns,
      })),
    };
  } catch (err) {
    return { error: `信号验证失败: ${(err as Error).message}` };
  }
}

// ── Helpers ──────────────────────────────────────────────────────

function getDefaultStartDate(): string {
  const d = new Date();
  d.setFullYear(d.getFullYear() - 1);
  return d.toISOString().slice(0, 10);
}

function detectMarketHint(symbols: string[]): string | undefined {
  if (!symbols.length) return undefined;
  const sym = symbols[0];
  if (/^\d{6}$/.test(sym)) return "cn_stock";
  if (/\.HK$/i.test(sym)) return "hk_stock";
  if (/^[A-Za-z.]+$/.test(sym)) return "us_stock";
  return undefined;
}

// ── Weight Optimization for Composed Strategies ──────────────────

async function runOptimizeWeights(
  args: Record<string, unknown>,
  symbols: string[],
  startDate: string,
  endDate: string,
  initialCash: number,
  config: QuantTradingConfig,
  composedStore?: ComposedStrategyStore,
) {
  const compositionId = args.compositionId as string;
  if (!compositionId) {
    return { error: "请提供组合策略 ID (compositionId)" };
  }

  if (!composedStore) {
    return { error: "组合策略存储未初始化" };
  }

  const composition = composedStore.get(compositionId);
  if (!composition) {
    return { error: `未找到组合策略: ${compositionId}` };
  }

  // Parse weight range
  let weightRange = { min: 1, max: 5, step: 1 };
  if (args.weightRange) {
    try {
      weightRange = { ...weightRange, ...JSON.parse(args.weightRange as string) };
    } catch {
      return { error: "weightRange 解析失败" };
    }
  }

  // Generate weight combinations
  const subCount = composition.strategies.length;
  const weightValues: number[] = [];
  for (let w = weightRange.min; w <= weightRange.max; w += weightRange.step) {
    weightValues.push(w);
  }

  const metric = (args.metric as string) ?? "sharpeRatio";
  const maxTrials = (args.maxTrials as number) ?? 200;

  // Generate all possible weight combos (or random sample if too many)
  const totalCombos = Math.pow(weightValues.length, subCount);
  const useRandom = totalCombos > maxTrials;

  const combos: number[][] = [];
  if (useRandom) {
    for (let i = 0; i < maxTrials; i++) {
      const combo = Array.from(
        { length: subCount },
        () => weightValues[Math.floor(Math.random() * weightValues.length)]!,
      );
      combos.push(combo);
    }
  } else {
    // Full grid (recursive)
    function generate(depth: number, current: number[]): void {
      if (depth === subCount) {
        combos.push([...current]);
        return;
      }
      for (const w of weightValues) {
        current.push(w);
        generate(depth + 1, current);
        current.pop();
      }
    }
    generate(0, []);
  }

  // Build batch backtest items — each combo creates a temporary composed strategy
  const { registerComposedStrategy, unregisterComposedStrategy } =
    await import("../engine/strategy-registry.js");
  const batchItems: BatchBacktestItem[] = [];
  const tempIds: string[] = [];

  for (let i = 0; i < combos.length; i++) {
    const weights = combos[i]!;
    const tempId = `opt-wt-${Date.now().toString(36)}-${i}`;
    tempIds.push(tempId);

    const tempConfig = {
      ...composition,
      id: tempId,
      name: `权重优化 #${i + 1}`,
      strategies: composition.strategies.map((s, j) => ({
        ...s,
        weight: weights[j],
      })),
    };

    registerComposedStrategy(tempConfig);
    batchItems.push({
      label: `w=[${weights.join(",")}]`,
      request: { strategyId: tempId, symbols, startDate, endDate, initialCash },
    });
  }

  // Run batch
  const summary = await runBatchBacktest(batchItems, config, { maxConcurrency: 4 });

  // Cleanup all temporary strategies
  for (const id of tempIds) {
    unregisterComposedStrategy(id);
  }

  // Extract top results with their weight configurations
  const topResults = summary.ranked.slice(0, 5).map((r, rank) => {
    const idx = batchItems.findIndex((b) => b.label === r.label);
    const weights = idx >= 0 ? combos[idx] : [];
    return {
      rank: rank + 1,
      weights: composition.strategies.map((s, j) => ({
        strategyId: s.strategyId,
        weight: weights?.[j] ?? 1,
      })),
      totalReturn: r.totalReturn,
      sharpeRatio: r.sharpeRatio,
      maxDrawdown: r.maxDrawdown,
      winRate: r.winRate,
      totalTrades: r.totalTrades,
    };
  });

  // Auto-update the composition with best weights
  if (topResults.length > 0) {
    const best = topResults[0]!;
    const updatedStrategies = composition.strategies.map((s, j) => ({
      ...s,
      weight: best.weights[j]?.weight ?? s.weight ?? 1,
    }));
    await composedStore.upsert({ ...composition, strategies: updatedStrategies });
  }

  return {
    ok: true,
    compositionId,
    method: useRandom ? "random" : "grid",
    totalTrials: combos.length,
    optimizedMetric: metric,
    topResults,
    hint: `最优权重已自动应用到组合 ${compositionId}`,
  };
}
