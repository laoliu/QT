/**
 * trading_plan tool — AI-callable for managing daily trading plans.
 *
 * Actions:
 *   create     — Create a new trading plan with entry/exit triggers
 *   update     — Update an existing plan
 *   delete     — Delete a plan
 *   view       — View plans by ID, date, or symbol
 *   active     — List all active (non-terminal) plans
 *   recent     — List recent plans
 *   link_order — Link an order ID to a plan
 *   review     — Add post-trade review to a plan
 *   expire     — Expire old unexecuted plans
 *   metrics    — View plan discipline metrics
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { TradingPlanStore } from "../services/trading-plan-store.js";

export function createTradingPlanTool(_api: OpenClawPluginApi, planStore: TradingPlanStore) {
  return {
    name: "trading_plan",
    description:
      "交易计划管理工具。盘前制定计划，盘中跟踪执行，盘后复盘问责。\n" +
      "• action=create — 创建交易计划（symbol, direction, entry_trigger, reason 必填）\n" +
      "• action=update — 修改计划\n" +
      "• action=delete — 删除计划\n" +
      "• action=view — 查看计划（按plan_id/date/symbol）\n" +
      "• action=active — 查看所有活跃计划\n" +
      "• action=recent — 最近的计划\n" +
      "• action=link_order — 关联订单到计划\n" +
      "• action=review — 添加事后复盘评价\n" +
      "• action=expire — 过期未执行的旧计划\n" +
      "• action=metrics — 查看计划纪律性指标",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: [
            "create",
            "update",
            "delete",
            "view",
            "active",
            "recent",
            "link_order",
            "review",
            "expire",
            "metrics",
          ],
          description: "操作类型",
        },
        plan_id: { type: "string", description: "计划ID" },
        symbol: { type: "string", description: "标的代码" },
        direction: {
          type: "string",
          enum: ["buy", "sell"],
          description: "方向: buy=买入, sell=卖出",
        },
        entry_trigger: { type: "string", description: "入场触发条件描述" },
        entry_price: { type: "number", description: "计划入场价" },
        stop_price: { type: "number", description: "止损价" },
        target_price: { type: "number", description: "目标价" },
        max_position: { type: "number", description: "最大仓位（股数或金额）" },
        sizing_method: { type: "string", description: "仓位管理方法描述" },
        reason: { type: "string", description: "交易理由/逻辑" },
        strategy_id: { type: "string", description: "关联策略ID" },
        tags: {
          type: "array",
          items: { type: "string" },
          description: "标签",
        },
        status: {
          type: "string",
          enum: ["planned", "triggered", "executed", "cancelled", "expired", "reviewed"],
          description: "计划状态",
        },
        order_id: { type: "string", description: "关联的订单ID (link_order)" },
        actual_entry_price: { type: "number", description: "实际入场价 (review)" },
        actual_exit_price: { type: "number", description: "实际出场价 (review)" },
        actual_pnl: { type: "number", description: "实际盈亏 (review)" },
        review_notes: { type: "string", description: "复盘笔记 (review)" },
        review_rating: { type: "number", description: "自评分 1-5 (review)" },
        date: { type: "string", description: "日期 YYYY-MM-DD" },
        from_date: { type: "string", description: "起始日期 (metrics)" },
        to_date: { type: "string", description: "截止日期 (metrics)" },
        limit: { type: "number", description: "返回数量限制" },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      switch (action) {
        case "create": {
          const symbol = args.symbol as string;
          const direction = args.direction as "buy" | "sell";
          const entryTrigger = args.entry_trigger as string;
          const reason = args.reason as string;
          if (!symbol || !direction || !entryTrigger || !reason) {
            return { error: "请提供 symbol, direction, entry_trigger, reason" };
          }
          const id = planStore.createPlan({
            date: args.date as string | undefined,
            symbol: symbol.trim().toUpperCase(),
            direction,
            entryTrigger,
            entryPrice: args.entry_price as number | undefined,
            stopPrice: args.stop_price as number | undefined,
            targetPrice: args.target_price as number | undefined,
            maxPosition: args.max_position as number | undefined,
            sizingMethod: args.sizing_method as string | undefined,
            reason,
            strategyId: args.strategy_id as string | undefined,
            tags: args.tags as string[] | undefined,
          });
          const plan = planStore.getPlan(id);
          return { message: "交易计划已创建", id, plan };
        }

        case "update": {
          const planId = args.plan_id as string;
          if (!planId) return { error: "请提供 plan_id" };
          const ok = planStore.updatePlan(planId, {
            entryTrigger: args.entry_trigger as string | undefined,
            entryPrice: args.entry_price as number | undefined,
            stopPrice: args.stop_price as number | undefined,
            targetPrice: args.target_price as number | undefined,
            maxPosition: args.max_position as number | undefined,
            reason: args.reason as string | undefined,
            status: args.status as
              | "planned"
              | "triggered"
              | "executed"
              | "cancelled"
              | "expired"
              | "reviewed"
              | undefined,
            tags: args.tags as string[] | undefined,
          });
          return ok
            ? { message: `计划 ${planId} 已更新`, plan: planStore.getPlan(planId) }
            : { error: `未找到计划 ${planId}` };
        }

        case "delete": {
          const planId = args.plan_id as string;
          if (!planId) return { error: "请提供 plan_id" };
          const ok = planStore.deletePlan(planId);
          return ok ? { message: `计划 ${planId} 已删除` } : { error: `未找到计划 ${planId}` };
        }

        case "view": {
          const planId = args.plan_id as string;
          const symbol = args.symbol as string;
          const date = args.date as string;

          if (planId) {
            const plan = planStore.getPlan(planId);
            return plan ?? { error: `未找到计划 ${planId}` };
          }
          if (date) {
            const plans = planStore.getByDate(date);
            return { count: plans.length, plans };
          }
          if (symbol) {
            const limit = (args.limit as number) ?? 20;
            const plans = planStore.getBySymbol(symbol.trim().toUpperCase(), limit);
            return { count: plans.length, plans };
          }
          return { error: "请提供 plan_id, date, 或 symbol" };
        }

        case "active": {
          const plans = planStore.getActive();
          return { count: plans.length, plans };
        }

        case "recent": {
          const limit = (args.limit as number) ?? 20;
          const plans = planStore.getRecent(limit);
          return { count: plans.length, plans };
        }

        case "link_order": {
          const planId = args.plan_id as string;
          const orderId = args.order_id as string;
          if (!planId || !orderId) return { error: "请提供 plan_id 和 order_id" };
          const ok = planStore.linkOrder(planId, orderId);
          return ok
            ? { message: `订单 ${orderId} 已关联到计划 ${planId}` }
            : { error: `未找到计划 ${planId}` };
        }

        case "review": {
          const planId = args.plan_id as string;
          if (!planId) return { error: "请提供 plan_id" };
          const ok = planStore.updatePlan(planId, {
            actualEntryPrice: args.actual_entry_price as number | undefined,
            actualExitPrice: args.actual_exit_price as number | undefined,
            actualPnl: args.actual_pnl as number | undefined,
            reviewNotes: args.review_notes as string | undefined,
            reviewRating: args.review_rating as number | undefined,
            status: "reviewed",
          });
          return ok
            ? { message: `计划 ${planId} 复盘已保存`, plan: planStore.getPlan(planId) }
            : { error: `未找到计划 ${planId}` };
        }

        case "expire": {
          const beforeDate =
            (args.date as string) ?? new Date().toISOString().slice(0, 10);
          const count = planStore.expireOldPlans(beforeDate);
          return { message: `已过期 ${count} 个未执行计划`, expiredCount: count };
        }

        case "metrics": {
          const fromDate = args.from_date as string | undefined;
          const toDate = args.to_date as string | undefined;
          return planStore.getDisciplineMetrics(fromDate, toDate);
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
