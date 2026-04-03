/**
 * strategy_explorer tool — AI-callable tool for automated strategy combination
 * exploration. Generates candidate compositions, batches backtests, ranks results.
 *
 * Actions:
 *   - explore:   Generate N candidate combinations → batch backtest → rank
 *   - recommend:  Given a symbol, recommend best compositions from exploration history
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { CompositionConfig, CompositionMethod } from "../engine/dynamic-composer.js";
import { listStrategies, listComposedStrategies } from "../engine/strategy-registry.js";
import type { BacktestStore } from "../services/backtest-store.js";
import { runBatchBacktest, type BatchBacktestItem } from "../services/batch-backtest.js";
import type { ComposedStrategyStore } from "../services/composed-strategy-store.js";
import type { QuantTradingConfig } from "../types.js";

// ── Strategy categories for intelligent pairing ─────────────────

const STRATEGY_CATEGORIES: Record<string, string[]> = {
  trend: [
    "dual-ma-crossover",
    "momentum-breakout",
    "ema-crossover",
    "macd-trend",
    "cta-trend",
    "ichimoku",
    "parabolic-sar",
    "turtle-breakout",
    "atr-trailing",
  ],
  oscillator: [
    "rsi-filter",
    "rsi-enhanced",
    "rsi-reversal",
    "macd-confirmation",
    "kdj",
    "stochastic-rsi",
    "williams-r",
    "cci-divergence",
    "roc-momentum",
  ],
  meanReversion: [
    "mean-reversion",
    "bollinger-breakout",
    "bollinger-bandwidth",
    "vwap-reversion",
    "donchian-mid-reversion",
    "mean-reversion-zscore",
    "keltner-channel",
  ],
  volume: ["volume-confirmation", "obv-trend"],
  pattern: ["double-bottom", "macd-divergence", "macd-histogram-reversal", "chandelier-exit"],
};

/** Heuristic: pick strategies from different categories for diversity. */
function generateCandidateCombinations(
  count: number,
  minSize: number,
  maxSize: number,
): Array<{ strategies: Array<{ strategyId: string; weight: number }>; method: CompositionMethod }> {
  const allCategories = Object.entries(STRATEGY_CATEGORIES);
  const methods: CompositionMethod[] = ["voting", "weighted-score", "gated", "cascade"];
  const candidates: Array<{
    strategies: Array<{ strategyId: string; weight: number }>;
    method: CompositionMethod;
  }> = [];
  const seen = new Set<string>();

  for (let attempt = 0; attempt < count * 3 && candidates.length < count; attempt++) {
    const size = minSize + Math.floor(Math.random() * (maxSize - minSize + 1));
    const method = methods[Math.floor(Math.random() * methods.length)]!;

    // Pick strategies from different categories
    const shuffled = [...allCategories].sort(() => Math.random() - 0.5);
    const picked: Array<{ strategyId: string; weight: number }> = [];

    for (const [, ids] of shuffled) {
      if (picked.length >= size) break;
      const id = ids[Math.floor(Math.random() * ids.length)]!;
      if (!picked.some((p) => p.strategyId === id)) {
        picked.push({ strategyId: id, weight: 1 + Math.floor(Math.random() * 3) });
      }
    }

    if (picked.length < minSize) continue;

    const key =
      picked
        .map((p) => p.strategyId)
        .sort()
        .join("+") + `:${method}`;
    if (seen.has(key)) continue;
    seen.add(key);

    candidates.push({ strategies: picked, method });
  }

  return candidates;
}

export function createStrategyExplorerTool(
  _api: OpenClawPluginApi,
  config: QuantTradingConfig,
  composedStore: ComposedStrategyStore,
  backtestStore?: BacktestStore,
) {
  return {
    name: "strategy_explorer",
    description:
      "AI 策略探索器。自动生成多个候选策略组合，批量回测，按综合评分排序。" +
      "action=explore 生成 N 个候选组合 → 回测 → 排名。" +
      "action=recommend 基于标的/板块推荐历史最佳组合。" +
      "用于发现跨策略类别的最优组合（趋势+振荡+均值回归+量价）。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["explore", "recommend"],
          description: "操作: explore=自动探索, recommend=推荐最佳",
        },
        symbols: {
          type: "string",
          description: "标的代码，逗号分隔（如 600519,000858）",
        },
        startDate: {
          type: "string",
          description: "回测起始日期 YYYY-MM-DD",
        },
        endDate: {
          type: "string",
          description: "回测结束日期 YYYY-MM-DD",
        },
        initialCash: {
          type: "number",
          description: "初始资金（默认 100000）",
        },
        candidateCount: {
          type: "number",
          description: "生成候选组合数量（默认 10，最大 30）",
        },
        minStrategies: {
          type: "number",
          description: "每个组合最少策略数（默认 2）",
        },
        maxStrategies: {
          type: "number",
          description: "每个组合最多策略数（默认 4）",
        },
        topK: {
          type: "number",
          description: "返回 Top K 结果（默认 5）",
        },
        saveBest: {
          type: "boolean",
          description: "是否自动保存最佳组合（默认 true）",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      if (action === "explore") {
        const symbols = ((args.symbols as string) ?? "000300").split(",").map((s) => s.trim());
        const startDate =
          (args.startDate as string) ??
          (() => {
            const d = new Date();
            d.setFullYear(d.getFullYear() - 1);
            return d.toISOString().slice(0, 10);
          })();
        const endDate = (args.endDate as string) ?? new Date().toISOString().slice(0, 10);
        const initialCash = (args.initialCash as number) ?? 100000;
        const candidateCount = Math.min((args.candidateCount as number) ?? 10, 30);
        const minStrategies = (args.minStrategies as number) ?? 2;
        const maxStrategies = Math.min((args.maxStrategies as number) ?? 4, 6);
        const topK = (args.topK as number) ?? 5;
        const saveBest = args.saveBest !== false;

        // Generate candidate combinations
        const candidates = generateCandidateCombinations(
          candidateCount,
          minStrategies,
          maxStrategies,
        );

        if (candidates.length === 0) {
          return { error: "无法生成候选组合" };
        }

        // Register temporary compositions and prepare batch
        const batchItems: BatchBacktestItem[] = [];
        const tempConfigs: CompositionConfig[] = [];

        for (let i = 0; i < candidates.length; i++) {
          const c = candidates[i]!;
          const compId = `explore-${Date.now().toString(36)}-${i}`;
          const compConfig: CompositionConfig = {
            id: compId,
            name: `探索 #${i + 1}: ${c.strategies.map((s) => s.strategyId).join("+")}`,
            method: c.method,
            strategies: c.strategies,
            minVotes: c.method === "voting" ? Math.ceil(c.strategies.length / 2) : undefined,
            scoreThreshold: c.method === "weighted-score" ? 0.5 : undefined,
          };
          tempConfigs.push(compConfig);

          // Register temporarily for the backtest run
          const { registerComposedStrategy } = await import("../engine/strategy-registry.js");
          registerComposedStrategy(compConfig);

          batchItems.push({
            label: compConfig.name,
            request: {
              strategyId: compId,
              symbols,
              startDate,
              endDate,
              initialCash,
            },
          });
        }

        // Run batch backtest
        const summary = await runBatchBacktest(batchItems, config, { maxConcurrency: 4 });

        // Clean up temporary registrations (except top results)
        const { unregisterComposedStrategy } = await import("../engine/strategy-registry.js");
        const topIds = new Set(
          summary.ranked
            .slice(0, topK)
            .map((r) => {
              const idx = batchItems.findIndex((b) => b.label === r.label);
              return idx >= 0 ? tempConfigs[idx]!.id : null;
            })
            .filter(Boolean),
        );

        for (const tc of tempConfigs) {
          if (!topIds.has(tc.id)) {
            unregisterComposedStrategy(tc.id);
          }
        }

        // Save best compositions
        if (saveBest) {
          for (const ranked of summary.ranked.slice(0, Math.min(topK, 3))) {
            const idx = batchItems.findIndex((b) => b.label === ranked.label);
            if (idx >= 0) {
              await composedStore.upsert(tempConfigs[idx]!);
            }
          }
        }

        // Format results
        const topResults = summary.ranked.slice(0, topK).map((r, rank) => {
          const idx = batchItems.findIndex((b) => b.label === r.label);
          const tc = idx >= 0 ? tempConfigs[idx] : null;
          return {
            rank: rank + 1,
            compositionId: tc?.id ?? "",
            name: r.label,
            method: tc?.method ?? "",
            strategies: tc?.strategies.map((s) => s.strategyId) ?? [],
            totalReturn: r.totalReturn,
            annualizedReturn: r.annualizedReturn,
            maxDrawdown: r.maxDrawdown,
            sharpeRatio: r.sharpeRatio,
            winRate: r.winRate,
            totalTrades: r.totalTrades,
          };
        });

        return {
          ok: true,
          explored: candidates.length,
          successful: summary.successCount,
          failed: summary.failCount,
          topResults,
          bestStrategy: topResults[0]?.name ?? null,
          hint: topResults[0]
            ? `最佳组合: ${topResults[0].name}。使用 backtest 工具回测: strategyId="${topResults[0].compositionId}"`
            : "未找到有效组合",
        };
      }

      if (action === "recommend") {
        // Recommend from saved compositions
        const compositions = composedStore.list();
        if (compositions.length === 0) {
          return {
            message: "暂无保存的组合策略。请先运行 action=explore 探索最佳组合。",
            hint: "strategy_explorer action=explore symbols=600519 candidateCount=10",
          };
        }

        return {
          ok: true,
          savedCompositions: compositions.map((c) => ({
            id: c.id,
            name: c.name,
            method: c.method,
            strategies: c.strategies.map((s) => s.strategyId),
            createdAt: c.createdAt,
          })),
          hint: "使用 backtest 工具对组合进行回测验证: strategyId=<compositionId>",
        };
      }

      return { error: `未知操作: ${action}` };
    },
  };
}
