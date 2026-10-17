/**
 * custom_strategy tool — AI-callable tool for creating and managing
 * user-defined rule-based strategies via a declarative DSL.
 *
 * Users describe entry/exit conditions using indicators and comparison operators.
 * The tool validates the rules, persists them, and registers them in the strategy
 * registry so they can be used with backtest / live_trading like any builtin strategy.
 *
 * Actions:
 *   create  — Define a new custom strategy from buy/sell rules
 *   list    — List all custom strategies
 *   detail  — View the full rule configuration
 *   delete  — Remove a custom strategy
 *
 * Example conversation:
 *   User: "帮我创建一个策略：5日均线上穿20日均线且RSI低于30时买入，RSI超过70时卖出"
 *   AI calls custom_strategy with:
 *     action=create, name="MA金叉+RSI超卖",
 *     buy_rules={"logic":"all","conditions":[
 *       {"left":{"indicator":"sma","period":5},"op":"crosses_above","right":{"indicator":"sma","period":20}},
 *       {"left":{"indicator":"rsi","period":14},"op":"below","value":30}
 *     ]},
 *     sell_rules={"logic":"all","conditions":[
 *       {"left":{"indicator":"rsi","period":14},"op":"above","value":70}
 *     ]}
 */
import type { QuantTradingRuntime } from "../runtime.js";
import type { RuleStrategyConfig, RuleGroup } from "../engine/rule-strategy.js";
import { validateRuleConfig } from "../engine/rule-strategy.js";
import type { CustomStrategyStore } from "../services/custom-strategy-store.js";

export function createCustomStrategyTool(_api: QuantTradingRuntime, store: CustomStrategyStore) {
  return {
    name: "custom_strategy",
    description:
      "自定义规则策略工具。通过声明式规则定义买入/卖出条件，无需编写代码。\n" +
      "支持指标: sma, ema, rsi, macd, bollinger, atr, cci, roc, obv, price, volume\n" +
      "支持比较: above(大于), below(小于), crosses_above(上穿), crosses_below(下穿), between(区间)\n" +
      "支持逻辑: all(全部满足/AND), any(任一满足/OR)\n\n" +
      "action=create 创建自定义策略, action=list 列出策略, action=detail 查看详情, action=delete 删除\n" +
      "创建后的策略ID可直接用于 backtest 回测或 live_trading 实盘。\n\n" +
      "示例: 5日均线上穿20日均线且RSI<30买入，RSI>70卖出\n" +
      "buy_rules: {logic:'all', conditions:[{left:{indicator:'sma',period:5},op:'crosses_above',right:{indicator:'sma',period:20}},{left:{indicator:'rsi',period:14},op:'below',value:30}]}\n" +
      "sell_rules: {logic:'all', conditions:[{left:{indicator:'rsi',period:14},op:'above',value:70}]}",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["create", "list", "detail", "delete"],
          description: "操作: create=创建策略, list=列出策略, detail=查看详情, delete=删除",
        },
        id: {
          type: "string",
          description: "策略ID (create时可自定义或自动生成; detail/delete时必填)",
        },
        name: {
          type: "string",
          description: "策略名称 (create时必填)",
        },
        description: {
          type: "string",
          description: "策略描述 (可选)",
        },
        buy_rules: {
          type: "string",
          description:
            "买入规则 JSON (create时必填)。格式: " +
            '{"logic":"all|any","conditions":[{"left":{"indicator":"sma","period":5},"op":"crosses_above","right":{"indicator":"sma","period":20}}]}. ' +
            "指标: sma/ema/rsi/macd/bollinger/atr/cci/roc/obv/price/volume. " +
            "比较: above/below/crosses_above/crosses_below/between. " +
            "MACD output: line/signal/histogram. Bollinger output: middle/upper/lower.",
        },
        sell_rules: {
          type: "string",
          description: "卖出规则 JSON (create时必填)。格式同 buy_rules。",
        },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      switch (action) {
        case "create": {
          const name = args.name as string;
          if (!name) return { error: "请提供策略名称 (name)" };

          // Parse buy/sell rules from JSON strings
          let buyRules: RuleGroup;
          let sellRules: RuleGroup;
          try {
            const buyRaw = args.buy_rules as string;
            const sellRaw = args.sell_rules as string;
            if (!buyRaw && !sellRaw) {
              return { error: "请提供 buy_rules 和/或 sell_rules" };
            }
            buyRules = buyRaw
              ? (JSON.parse(buyRaw) as RuleGroup)
              : { logic: "all", conditions: [] };
            sellRules = sellRaw
              ? (JSON.parse(sellRaw) as RuleGroup)
              : { logic: "all", conditions: [] };
          } catch (err) {
            return {
              error: `规则 JSON 解析失败: ${err instanceof Error ? err.message : String(err)}`,
            };
          }

          const id = (args.id as string) ?? `rule-${Date.now().toString(36)}`;

          const config: RuleStrategyConfig = {
            id,
            name,
            description: args.description as string | undefined,
            buyRules,
            sellRules,
          };

          // Validate the rule config
          const validationError = validateRuleConfig(config);
          if (validationError) {
            return { error: `规则验证失败: ${validationError}` };
          }

          await store.upsert(config);

          return {
            message: `✅ 自定义策略已创建`,
            id,
            name,
            buyConditions: config.buyRules.conditions.length,
            buyLogic: config.buyRules.logic,
            sellConditions: config.sellRules.conditions.length,
            sellLogic: config.sellRules.logic,
            hint: `可直接使用 backtest(strategy_id="${id}") 回测，或 live_trading(action=add_strategy, strategy_id="${id}") 部署实盘`,
          };
        }

        case "list": {
          const strategies = store.list();
          return {
            total: strategies.length,
            strategies: strategies.map((s) => ({
              id: s.id,
              name: s.name,
              description: s.description,
              buyConditions: s.buyRules.conditions.length,
              buyLogic: s.buyRules.logic,
              sellConditions: s.sellRules.conditions.length,
              sellLogic: s.sellRules.logic,
              createdAt: s.createdAt,
              updatedAt: s.updatedAt,
            })),
            hint: "策略ID可直接用于 backtest 或 live_trading",
          };
        }

        case "detail": {
          const id = args.id as string;
          if (!id) return { error: "请提供策略ID" };
          const config = store.get(id);
          if (!config) return { error: `未找到策略: ${id}` };
          return { strategy: config };
        }

        case "delete": {
          const id = args.id as string;
          if (!id) return { error: "请提供策略ID" };
          const deleted = await store.delete(id);
          if (!deleted) return { error: `未找到策略: ${id}` };
          return { message: `🗑️ 策略 ${id} 已删除` };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
