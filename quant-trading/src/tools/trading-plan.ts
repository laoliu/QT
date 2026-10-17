/**
 * trading_plan — AI tool for managing trading plans
 *
 * Actions: create, list, update, activate, complete, cancel, review, discipline
 */
import type { QuantTradingRuntime } from "../runtime.js";
import type { TradingPlanService, PlanStatus } from "../services/trading-plan.js";

export function createTradingPlanTool(api: QuantTradingRuntime, planService: TradingPlanService) {
  return {
    description: "0607 — 创建/51/04/070607010209",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["create", "list", "get", "update", "activate", "complete", "cancel", "review", "discipline", "remove"],
          description: "操作类型",
        },
        planId: { type: "string", description: "07ID (update/activate/complete/cancel/review/remove)" },
        symbol: { type: "string", description: "标的代码 (create/list)" },
        name: { type: "string", description: "计划名称 (create)" },
        side: { type: "string", enum: ["buy", "sell"], description: "方向 (create)" },
        targetPrice: { type: "number", description: "07" },
        stopLoss: { type: "number", description: "09" },
        takeProfit: { type: "number", description: "0907" },
        quantity: { type: "number", description: "数量 (股)" },
        amount: { type: "number", description: "金额 (06)" },
        rationale: { type: "string", description: "交易理由/18" },
        timeframe: { type: "string", description: "时间框架 (如 '短线1-3天')" },
        status: { type: "string", description: "0800 (list)" },
        note: { type: "string", description: "0503 (review)" },
        executedPrice: { type: "number", description: "0604 (complete)" },
        executedQuantity: { type: "number", description: "0604 (complete)" },
        pnl: { type: "number", description: "盈亏金额 (complete)" },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      switch (action) {
        case "create": {
          if (!args.symbol || !args.name || !args.side) {
            return { error: "59 symbol, name, side" };
          }
          const plan = await planService.create({
            name: args.name as string,
            symbol: args.symbol as string,
            side: args.side as "buy" | "sell",
            targetPrice: args.targetPrice as number | undefined,
            stopLoss: args.stopLoss as number | undefined,
            takeProfit: args.takeProfit as number | undefined,
            quantity: args.quantity as number | undefined,
            amount: args.amount as number | undefined,
            rationale: args.rationale as string | undefined,
            timeframe: args.timeframe as string | undefined,
          });
          return { status: "ok", plan };
        }

        case "list": {
          const plans = planService.list({
            status: args.status as PlanStatus | undefined,
            symbol: args.symbol as string | undefined,
          });
          return {
            count: plans.length,
            plans: plans.map((p) => ({
              planId: p.planId,
              name: p.name,
              symbol: p.symbol,
              side: p.side,
              status: p.status,
              targetPrice: p.targetPrice,
              stopLoss: p.stopLoss,
              takeProfit: p.takeProfit,
              createdAt: p.createdAt,
            })),
          };
        }

        case "get": {
          if (!args.planId) return { error: "59 planId" };
          const plan = planService.get(args.planId as string);
          return plan ? { plan } : { error: "计划不存在" };
        }

        case "update": {
          if (!args.planId) return { error: "59 planId" };
          const updates: Record<string, unknown> = {};
          for (const key of ["name", "symbol", "side", "targetPrice", "stopLoss", "takeProfit", "quantity", "amount", "rationale", "timeframe"]) {
            if (args[key] !== undefined) updates[key] = args[key];
          }
          const updated = await planService.update(args.planId as string, updates);
          return updated ? { status: "ok", plan: updated } : { error: "计划不存在" };
        }

        case "activate": {
          if (!args.planId) return { error: "59 planId" };
          const activated = await planService.activate(args.planId as string);
          return activated ? { status: "ok", plan: activated } : { error: "计划不存在" };
        }

        case "complete": {
          if (!args.planId) return { error: "59 planId" };
          const result = {
            executedPrice: args.executedPrice as number | undefined,
            executedQuantity: args.executedQuantity as number | undefined,
            pnl: args.pnl as number | undefined,
            note: args.note as string | undefined,
          };
          const completed = await planService.complete(args.planId as string, result);
          return completed ? { status: "ok", plan: completed } : { error: "计划不存在" };
        }

        case "cancel": {
          if (!args.planId) return { error: "59 planId" };
          const cancelled = await planService.cancel(args.planId as string, args.rationale as string);
          return cancelled ? { status: "ok", plan: cancelled } : { error: "计划不存在" };
        }

        case "review": {
          if (!args.planId || !args.note) return { error: "59 planId 和 note" };
          const reviewed = await planService.review(args.planId as string, args.note as string);
          return reviewed ? { status: "ok", plan: reviewed } : { error: "计划不存在" };
        }

        case "discipline": {
          return planService.getDisciplineReport();
        }

        case "remove": {
          if (!args.planId) return { error: "59 planId" };
          const removed = await planService.remove(args.planId as string);
          return removed ? { status: "ok" } : { error: "计划不存在" };
        }

        default:
          return { error: `ā04: ${action}` };
      }
    },
  };
}
