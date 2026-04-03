/**
 * strategy_composer tool — AI-callable tool for creating and managing
 * dynamic strategy compositions.
 *
 * Actions:
 *   - compose:   Create a new composition from N strategies + method + weights
 *   - list:      List all saved compositions
 *   - delete:    Remove a composition
 *   - detail:    Get details of a composition
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { CompositionConfig, CompositionMethod } from "../engine/dynamic-composer.js";
import { listStrategies } from "../engine/strategy-registry.js";
import type { ComposedStrategyStore } from "../services/composed-strategy-store.js";
import type { QuantTradingConfig } from "../types.js";

export function createStrategyComposerTool(
  _api: OpenClawPluginApi,
  _config: QuantTradingConfig,
  store: ComposedStrategyStore,
) {
  return {
    name: "strategy_composer",
    description:
      "动态策略组合器。可以将任意 N 个内置策略组合成新策略。" +
      "action=compose 创建组合策略，action=list 列出已有组合，" +
      "action=delete 删除组合，action=detail 查看组合详情。" +
      "组合后的策略 ID 可直接用于 backtest 工具进行回测。" +
      "支持4种组合方法: voting(投票), weighted-score(加权), gated(门控), cascade(级联)。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["compose", "list", "delete", "detail"],
          description: "操作: compose=创建组合, list=列出组合, delete=删除, detail=查看详情",
        },
        id: {
          type: "string",
          description: "组合策略 ID（compose 时自动生成或自定义，delete/detail 时必填）",
        },
        name: {
          type: "string",
          description: "组合策略名称（compose 时使用）",
        },
        description: {
          type: "string",
          description: "组合策略描述",
        },
        method: {
          type: "string",
          enum: ["voting", "weighted-score", "gated", "cascade"],
          description:
            "组合方法: voting=多数投票, weighted-score=加权评分, gated=门控确认, cascade=级联过滤",
        },
        strategies: {
          type: "string",
          description:
            "子策略配置 JSON 数组，如: " +
            '[{"strategyId":"dual-ma-crossover","weight":2,"params":{"fastPeriod":10}}, ' +
            '{"strategyId":"rsi-filter","weight":1,"role":"filter"}]',
        },
        minVotes: {
          type: "number",
          description: "voting 方法: 最少投票数（默认 ceil(N/2)）",
        },
        scoreThreshold: {
          type: "number",
          description: "weighted-score 方法: 触发阈值（默认 0.5）",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      if (action === "list") {
        const compositions = store.list();
        const available = listStrategies();
        return {
          compositions: compositions.map((c) => ({
            id: c.id,
            name: c.name,
            method: c.method,
            strategyCount: c.strategies.length,
            strategies: c.strategies.map((s) => s.strategyId),
            createdAt: c.createdAt,
          })),
          totalCompositions: compositions.length,
          availableStrategies: available.length,
          hint: "使用 action=compose 创建新组合。组合后的 ID 可直接传给 backtest 工具。",
        };
      }

      if (action === "detail") {
        const id = args.id as string;
        if (!id) return { error: "请提供组合策略 ID" };
        const comp = store.get(id);
        if (!comp) return { error: `未找到组合策略: ${id}` };
        return { composition: comp };
      }

      if (action === "delete") {
        const id = args.id as string;
        if (!id) return { error: "请提供要删除的组合策略 ID" };
        const deleted = await store.delete(id);
        return deleted
          ? { ok: true, message: `已删除组合策略: ${id}` }
          : { error: `未找到组合策略: ${id}` };
      }

      if (action === "compose") {
        // Parse strategies
        let strategies: Array<{
          strategyId: string;
          weight?: number;
          role?: "primary" | "filter";
          params?: Record<string, unknown>;
        }>;
        try {
          const raw = args.strategies as string;
          strategies = JSON.parse(raw);
        } catch {
          return { error: "strategies 参数必须是有效 JSON 数组" };
        }

        if (!Array.isArray(strategies) || strategies.length < 2) {
          return { error: "至少需要 2 个子策略才能创建组合" };
        }

        // Validate strategy IDs exist
        const available = new Set(listStrategies().map((s) => s.id));
        const unknown = strategies.filter((s) => !available.has(s.strategyId));
        if (unknown.length > 0) {
          return {
            error: `未知策略 ID: ${unknown.map((s) => s.strategyId).join(", ")}`,
            availableStrategies: [...available].sort(),
          };
        }

        const method = (args.method as CompositionMethod) ?? "voting";
        const id = (args.id as string) ?? `composed-${Date.now().toString(36)}`;
        const name =
          (args.name as string) ?? `组合策略 ${strategies.map((s) => s.strategyId).join("+")}`;

        const config: CompositionConfig = {
          id,
          name,
          description: (args.description as string) ?? undefined,
          method,
          strategies,
          minVotes: args.minVotes as number | undefined,
          scoreThreshold: args.scoreThreshold as number | undefined,
        };

        await store.upsert(config);

        return {
          ok: true,
          compositionId: id,
          name,
          method,
          strategyCount: strategies.length,
          strategies: strategies.map((s) => ({
            id: s.strategyId,
            weight: s.weight ?? 1,
            role: s.role ?? "primary",
          })),
          hint: `组合策略已创建。使用 backtest 工具回测: strategyId="${id}"`,
        };
      }

      return { error: `未知操作: ${action}` };
    },
  };
}
