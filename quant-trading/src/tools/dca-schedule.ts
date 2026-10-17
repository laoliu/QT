/**
 * dca_schedule tool — AI-callable tool for creating and managing
 * automated DCA (Dollar-Cost Averaging / 定投) plans.
 *
 * Actions:
 *   create  — Create a new recurring buy plan
 *   list    — List all DCA plans
 *   pause   — Pause an active plan
 *   resume  — Resume a paused plan
 *   cancel  — Cancel a plan permanently
 */
import type { QuantTradingRuntime } from "../runtime.js";
import type { DcaScheduleService, DcaFrequency } from "../services/dca-schedule.js";

const FREQ_LABEL: Record<DcaFrequency, string> = {
  daily: "每日",
  weekly: "每周",
  biweekly: "每两周",
  monthly: "每月",
};

export function createDcaScheduleTool(_api: QuantTradingRuntime, dcaService: DcaScheduleService) {
  return {
    name: "dca_schedule",
    description:
      "定投计划工具。创建和管理自动定投(DCA)计划，定期买入指定金额的股票。\n" +
      "action=create 创建定投计划, action=list 列出所有计划, " +
      "action=pause 暂停, action=resume 恢复, action=cancel 取消\n\n" +
      "示例: 每周一买入¥5000的600519 → create, symbol=600519, amount=5000, frequency=weekly, day_of_period=1",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["create", "list", "pause", "resume", "cancel"],
          description: "操作类型",
        },
        // create params
        symbol: { type: "string", description: "股票代码 (create)" },
        amount: { type: "number", description: "每次买入金额(Ԫ) (create)" },
        frequency: {
          type: "string",
          enum: ["daily", "weekly", "biweekly", "monthly"],
          description: "定投频率 (create, 默认weekly)",
        },
        day_of_period: {
          type: "number",
          description: "周几(weekly: 0=周日..6=周六) 或 几号(monthly: 1-28) (create)",
        },
        hour: { type: "number", description: "执行时间(0-23, 默认9) (create)" },
        max_executions: { type: "number", description: "最大执行次数, 达到后自动暂停 (create)" },
        description: { type: "string", description: "计划描述 (create)" },
        // pause/resume/cancel
        plan_id: { type: "string", description: "计划ID (pause/resume/cancel)" },
        // list filter
        status: {
          type: "string",
          enum: ["active", "paused", "cancelled"],
          description: "过滤状态 (list)",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>): Promise<string> {
      const action = args.action as string;

      switch (action) {
        case "create": {
          const symbol = args.symbol as string | undefined;
          const amount = args.amount as number | undefined;
          if (!symbol) return "请提供股票代码 (symbol)";
          if (!amount || amount <= 0) return "请提供有效买入金额 (amount > 0)";

          const plan = dcaService.createPlan({
            symbol,
            amount,
            frequency: (args.frequency as DcaFrequency) ?? "weekly",
            dayOfPeriod: args.day_of_period as number | undefined,
            hour: args.hour as number | undefined,
            maxExecutions: args.max_executions as number | undefined,
            description: args.description as string | undefined,
          });

          return (
            `✅ 定投计划已创建:\n` +
            `  ID: ${plan.id}\n` +
            `  描述: ${plan.description}\n` +
            `  股票: ${plan.symbol}\n` +
            `  金额: ¥${plan.amount}\n` +
            `  频率: ${FREQ_LABEL[plan.frequency]}\n` +
            `  下次执行: ${plan.nextExecutionAt}\n` +
            (plan.maxExecutions ? `  最大次数: ${plan.maxExecutions}\n` : "")
          );
        }

        case "list": {
          const status = args.status as string | undefined;
          const plans = dcaService.listPlans(
            status as "active" | "paused" | "cancelled" | undefined,
          );
          if (plans.length === 0) return status ? `没有${status}状态的定投计划` : "暂无定投计划";

          const lines = plans.map((p) => {
            const parts = [
              `${p.id} | ${p.symbol} | ¥${p.amount} | ${FREQ_LABEL[p.frequency]} | ${p.status}`,
            ];
            if (p.executionCount > 0) {
              parts.push(`  累计: ${p.executionCount}次, ¥${p.totalInvested.toFixed(0)}`);
            }
            if (p.nextExecutionAt && p.status === "active") {
              parts.push(`  下次: ${p.nextExecutionAt}`);
            }
            return parts.join("\n");
          });
          return `定投计划列表:\n${lines.join("\n\n")}`;
        }

        case "pause": {
          const id = args.plan_id as string;
          if (!id) return "请提供计划ID (plan_id)";
          return dcaService.pausePlan(id)
            ? `✅ 定投计划 ${id} 已暂停`
            : `❌ 暂停失败: 计划不存在或状态不是active`;
        }

        case "resume": {
          const id = args.plan_id as string;
          if (!id) return "请提供计划ID (plan_id)";
          return dcaService.resumePlan(id)
            ? `✅ 定投计划 ${id} 已恢复`
            : `❌ 恢复失败: 计划不存在或状态不是paused`;
        }

        case "cancel": {
          const id = args.plan_id as string;
          if (!id) return "请提供计划ID (plan_id)";
          return dcaService.cancelPlan(id) ? `✅ 定投计划 ${id} 已取消` : `❌ 取消失败: 计划不存在`;
        }

        default:
          return `未知操作: ${action}。支持: create, list, pause, resume, cancel`;
      }
    },
  };
}
