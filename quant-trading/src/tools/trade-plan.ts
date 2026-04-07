/**
 * trade_plan tool — AI-callable interface for pre-market trade planning.
 *
 * Actions:
 *   create        — create a new daily trade plan
 *   list          — list plans (by date/status)
 *   update        — update an existing plan (stocks, budget, notes)
 *   mark_execution— mark execution result for a stock in a plan
 *   close         — close plan and generate deviation report
 *   today         — get today's plan
 *   get           — get a specific plan by ID
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { TradePlanStock, TradePlanStore } from "../services/trade-plan-store.js";

export function createTradePlanTool(_api: OpenClawPluginApi, store: TradePlanStore) {
  return {
    name: "trade_plan",
    description:
      "盘前交易计划管理。制定每日交易计划，包含目标股票、入场价、止损价、止盈价、仓位比例和买入理由。" +
      "盘后标记执行情况并生成偏差报告。" +
      "action=create 创建计划, action=list 查看列表, action=update 修改计划, " +
      "action=mark_execution 标记执行, action=close 关闭并生成偏差报告, " +
      "action=today 今日计划, action=get 查看指定计划。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["create", "list", "update", "mark_execution", "close", "today", "get"],
          description: "操作类型",
        },
        plan_id: { type: "string", description: "计划ID (update/mark_execution/close/get)" },
        date: { type: "string", description: "日期 YYYY-MM-DD (create默认今天, list可选过滤)" },
        stocks: {
          type: "array",
          items: {
            type: "object",
            properties: {
              code: { type: "string", description: "股票代码" },
              name: { type: "string", description: "股票名称" },
              entryPrice: { type: "number", description: "计划入场价" },
              stopPrice: { type: "number", description: "止损价" },
              takeProfitPrice: { type: "number", description: "止盈价" },
              positionRatio: { type: "number", description: "仓位比例 (0-1, 如0.2=20%)" },
              reason: { type: "string", description: "买入理由" },
            },
            required: ["code", "entryPrice", "stopPrice", "takeProfitPrice", "positionRatio", "reason"],
          },
          description: "目标股票列表 (create/update)",
        },
        budget: { type: "number", description: "整体仓位预算(元)" },
        notes: { type: "string", description: "计划备注" },
        status: { type: "string", enum: ["open", "closed"], description: "状态过滤 (list)" },
        limit: { type: "number", description: "返回数量 (list)" },
        // mark_execution fields
        code: { type: "string", description: "股票代码 (mark_execution)" },
        executed: { type: "boolean", description: "是否已执行 (mark_execution)" },
        actual_entry_price: { type: "number", description: "实际入场价 (mark_execution)" },
        actual_exit_price: { type: "number", description: "实际出场价 (mark_execution)" },
        execution_note: { type: "string", description: "执行备注 (mark_execution)" },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      switch (action) {
        case "create": {
          const stocks = args.stocks as TradePlanStock[] | undefined;
          const budget = args.budget as number | undefined;
          if (!stocks || !Array.isArray(stocks) || stocks.length === 0) {
            return { error: "请提供 stocks 目标股票列表" };
          }
          if (!budget || budget <= 0) {
            return { error: "请提供有效的 budget 仓位预算" };
          }
          const date = (args.date as string) ?? new Date().toISOString().slice(0, 10);
          const id = store.createPlan({
            date,
            stocks,
            budget,
            notes: args.notes as string | undefined,
          });
          return { message: "交易计划已创建", id, date };
        }

        case "list": {
          const plans = store.listPlans({
            date: args.date as string | undefined,
            status: args.status as "open" | "closed" | undefined,
            limit: args.limit as number | undefined,
          });
          return {
            count: plans.length,
            plans: plans.map((p) => ({
              id: p.id,
              date: p.date,
              status: p.status,
              stockCount: p.stocks.length,
              budget: p.budget,
              notes: p.notes,
              hasDeviationReport: !!p.deviationReport,
            })),
          };
        }

        case "get": {
          const planId = args.plan_id as string;
          if (!planId) return { error: "请提供 plan_id" };
          const plan = store.getPlan(planId);
          if (!plan) return { error: `未找到计划: ${planId}` };
          return plan;
        }

        case "today": {
          const plan = store.getTodayPlan();
          if (!plan) return { message: "今日暂无交易计划", plan: null };
          return plan;
        }

        case "update": {
          const planId = args.plan_id as string;
          if (!planId) return { error: "请提供 plan_id" };
          const ok = store.updatePlan(planId, {
            notes: args.notes as string | undefined,
            budget: args.budget as number | undefined,
            stocks: args.stocks as TradePlanStock[] | undefined,
          });
          if (!ok) return { error: `未找到计划: ${planId}` };
          return { message: "计划已更新", id: planId };
        }

        case "mark_execution": {
          const planId = args.plan_id as string;
          const code = args.code as string;
          if (!planId || !code) return { error: "请提供 plan_id 和 code" };
          const executed = args.executed as boolean | undefined;
          if (executed === undefined) return { error: "请提供 executed (true/false)" };
          const ok = store.markExecution(planId, code, {
            executed,
            actualEntryPrice: args.actual_entry_price as number | undefined,
            actualExitPrice: args.actual_exit_price as number | undefined,
            executionNote: args.execution_note as string | undefined,
          });
          if (!ok) return { error: `未找到计划或股票: ${planId} / ${code}` };
          return { message: "执行记录已更新", planId, code };
        }

        case "close": {
          const planId = args.plan_id as string;
          if (!planId) return { error: "请提供 plan_id" };
          const report = store.closePlan(planId);
          if (!report) return { error: `未找到计划: ${planId}` };
          return {
            message: "计划已关闭，偏差报告已生成",
            report,
          };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
