/**
 * trade_plan tool — AI-callable interface for daily trade plans.
 *
 * Actions:
 *   create       — create a new trade plan
 *   view         — view a specific plan or plans by symbol/date/status
 *   update       — update a pending plan
 *   execute      — mark a plan as executed
 *   cancel       — mark a plan as cancelled
 *   delete       — remove a plan
 *   link_order   — link a conditional order to a plan
 *   recent       — list recent plans
 *   today        — list today's plans
 *   summary      — overall plan statistics
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { TradePlanStore } from "../services/trade-plan-store.js";

export function createTradePlanTool(_api: OpenClawPluginApi, store: TradePlanStore) {
  return {
    name: "trade_plan",
    description:
      "每日交易计划管理。创建/查看/更新交易计划，包含标的、方向、计划价位、止损位、目标价、仓位比例、执行理由。" +
      "支持计划执行状态追踪(待执行/已执行/已取消)，可关联条件单自动挂单。" +
      "action=create 创建计划, action=view 查看(按plan_id/symbol/date/status), " +
      "action=update 修改待执行计划, action=execute 标记已执行, action=cancel 取消计划, " +
      "action=delete 删除, action=link_order 关联条件单, " +
      "action=recent 最近计划, action=today 今日计划, action=summary 统计概览。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: [
            "create",
            "view",
            "update",
            "execute",
            "cancel",
            "delete",
            "link_order",
            "recent",
            "today",
            "summary",
          ],
          description: "操作类型",
        },
        symbol: { type: "string", description: "标的代码 (e.g. 600519)" },
        direction: {
          type: "string",
          enum: ["buy", "sell"],
          description: "交易方向: buy=买入, sell=卖出",
        },
        plan_price: { type: "number", description: "计划价位" },
        stop_price: { type: "number", description: "止损价位" },
        target_price: { type: "number", description: "目标价位" },
        position_ratio: {
          type: "number",
          description: "仓位比例 (0-100, e.g. 20 表示 20% 仓位)",
        },
        reason: { type: "string", description: "执行理由/交易逻辑" },
        plan_id: { type: "string", description: "计划ID (view/update/execute/cancel/delete/link_order)" },
        order_id: { type: "string", description: "条件单ID (link_order)" },
        executed_price: { type: "number", description: "实际成交价 (execute)" },
        cancel_reason: { type: "string", description: "取消原因 (cancel)" },
        strategy_id: { type: "string", description: "策略ID (可选)" },
        status: {
          type: "string",
          enum: ["pending", "executed", "cancelled"],
          description: "状态过滤 (view)",
        },
        date: { type: "string", description: "日期 YYYY-MM-DD" },
        limit: { type: "number", description: "返回数量" },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const limit = (args.limit as number) ?? 20;

      switch (action) {
        case "create": {
          const symbol = args.symbol as string;
          const direction = args.direction as "buy" | "sell";
          const planPrice = args.plan_price as number;
          const stopPrice = args.stop_price as number;
          const targetPrice = args.target_price as number;
          const positionRatio = args.position_ratio as number;
          const reason = args.reason as string;
          if (!symbol || !direction || !planPrice || !stopPrice || !targetPrice || !reason) {
            return {
              error:
                "请提供 symbol, direction, plan_price, stop_price, target_price, reason",
            };
          }
          if (positionRatio != null && (positionRatio <= 0 || positionRatio > 100)) {
            return { error: "position_ratio 应在 1-100 之间" };
          }
          const riskReward =
            direction === "buy"
              ? (targetPrice - planPrice) / (planPrice - stopPrice)
              : (planPrice - targetPrice) / (stopPrice - planPrice);
          const id = await store.addPlan({
            symbol,
            direction,
            planPrice,
            stopPrice,
            targetPrice,
            positionRatio: positionRatio ?? 10,
            reason,
            date: args.date as string | undefined,
            strategyId: args.strategy_id as string | undefined,
          });
          return {
            message: "交易计划已创建",
            id,
            symbol,
            direction: direction === "buy" ? "买入" : "卖出",
            planPrice: `¥${planPrice.toFixed(2)}`,
            stopPrice: `¥${stopPrice.toFixed(2)}`,
            targetPrice: `¥${targetPrice.toFixed(2)}`,
            positionRatio: `${positionRatio ?? 10}%`,
            riskReward: isFinite(riskReward) && riskReward > 0 ? `1:${riskReward.toFixed(1)}` : "N/A",
            tip: "可用 action=link_order 关联条件单自动执行止损/止盈",
          };
        }

        case "view": {
          const planId = args.plan_id as string;
          const symbol = args.symbol as string;
          const date = args.date as string;
          const status = args.status as "pending" | "executed" | "cancelled";

          if (planId) {
            const plan = await store.getPlan(planId);
            return plan ?? { error: `未找到计划 ${planId}` };
          }
          if (status) {
            const plans = await store.getByStatus(status, limit);
            return { count: plans.length, plans };
          }
          if (symbol) {
            const plans = await store.getBySymbol(symbol, limit);
            return { count: plans.length, plans };
          }
          if (date) {
            const plans = await store.getByDate(date);
            return { count: plans.length, plans };
          }
          return { error: "请提供 plan_id, symbol, date, 或 status" };
        }

        case "update": {
          const planId = args.plan_id as string;
          if (!planId) return { error: "请提供 plan_id" };
          const ok = await store.updatePlan(planId, {
            planPrice: args.plan_price as number | undefined,
            stopPrice: args.stop_price as number | undefined,
            targetPrice: args.target_price as number | undefined,
            positionRatio: args.position_ratio as number | undefined,
            reason: args.reason as string | undefined,
            direction: args.direction as "buy" | "sell" | undefined,
            strategyId: args.strategy_id as string | undefined,
          });
          return ok
            ? { message: `计划 ${planId} 已更新` }
            : { error: `未找到待执行计划 ${planId}（仅 pending 状态可修改）` };
        }

        case "execute": {
          const planId = args.plan_id as string;
          if (!planId) return { error: "请提供 plan_id" };
          const executedPrice = args.executed_price as number | undefined;
          const ok = await store.markExecuted(planId, executedPrice);
          if (!ok) return { error: `未找到待执行计划 ${planId}` };
          const plan = await store.getPlan(planId);
          return {
            message: `计划 ${planId} 已标记为已执行`,
            executedPrice: executedPrice ? `¥${executedPrice.toFixed(2)}` : undefined,
            plan,
          };
        }

        case "cancel": {
          const planId = args.plan_id as string;
          if (!planId) return { error: "请提供 plan_id" };
          const cancelReason = args.cancel_reason as string | undefined;
          const ok = await store.markCancelled(planId, cancelReason);
          return ok
            ? { message: `计划 ${planId} 已取消`, reason: cancelReason }
            : { error: `未找到待执行计划 ${planId}` };
        }

        case "delete": {
          const planId = args.plan_id as string;
          if (!planId) return { error: "请提供 plan_id" };
          const ok = await store.deletePlan(planId);
          return ok ? { message: `计划 ${planId} 已删除` } : { error: `未找到计划 ${planId}` };
        }

        case "link_order": {
          const planId = args.plan_id as string;
          const orderId = args.order_id as string;
          if (!planId || !orderId) return { error: "请提供 plan_id 和 order_id" };
          const ok = await store.linkOrder(planId, orderId);
          return ok
            ? { message: `计划 ${planId} 已关联条件单 ${orderId}` }
            : { error: `未找到计划 ${planId}` };
        }

        case "recent": {
          const plans = await store.getRecent(limit);
          return {
            count: plans.length,
            plans: plans.map((p) => ({
              id: p.id,
              symbol: p.symbol,
              direction: p.direction === "buy" ? "买入" : "卖出",
              date: p.date,
              planPrice: `¥${p.planPrice.toFixed(2)}`,
              stopPrice: `¥${p.stopPrice.toFixed(2)}`,
              targetPrice: `¥${p.targetPrice.toFixed(2)}`,
              positionRatio: `${p.positionRatio}%`,
              status: p.status === "pending" ? "待执行" : p.status === "executed" ? "已执行" : "已取消",
              linkedOrderId: p.linkedOrderId,
              reason: p.reason.slice(0, 80) + (p.reason.length > 80 ? "…" : ""),
            })),
          };
        }

        case "today": {
          const today = new Date().toISOString().slice(0, 10);
          const plans = await store.getByDate(today);
          return {
            date: today,
            count: plans.length,
            plans: plans.map((p) => ({
              id: p.id,
              symbol: p.symbol,
              direction: p.direction === "buy" ? "买入" : "卖出",
              planPrice: `¥${p.planPrice.toFixed(2)}`,
              stopPrice: `¥${p.stopPrice.toFixed(2)}`,
              targetPrice: `¥${p.targetPrice.toFixed(2)}`,
              positionRatio: `${p.positionRatio}%`,
              status: p.status === "pending" ? "待执行" : p.status === "executed" ? "已执行" : "已取消",
              linkedOrderId: p.linkedOrderId,
              reason: p.reason,
            })),
          };
        }

        case "summary": {
          return store.getSummary();
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
