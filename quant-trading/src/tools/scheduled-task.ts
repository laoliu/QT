/**
 * scheduled_task tool — AI-callable tool for scheduling delayed/periodic tasks.
 *
 * Allows the AI to:
 * - Schedule a retry of a failed operation (e.g., "retry in 15 minutes")
 * - Set up periodic reports (e.g., "ÿ30分钟推送一次行情")
 * - List/cancel pending tasks
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { REPORT_TEMPLATES, findReportTemplate } from "../services/report-templates.js";
import type { TaskScheduler } from "../services/task-scheduler.js";
import type { QuantTradingConfig } from "../types.js";

export function createScheduledTaskTool(
  api: OpenClawPluginApi,
  config: QuantTradingConfig,
  scheduler: TaskScheduler,
  executeToolFn: (toolName: string, args: Record<string, unknown>) => Promise<string>,
) {
  return {
    name: "scheduled_task",
    description:
      "定时任务管理。用于安排延迟重试、定期推送、报告模板。\n" +
      "action=schedule 创建定时任务（延迟执行指定工具调用），\n" +
      "action=report 使用预置报告模板创建定期推送，\n" +
      "action=list_reports 查看可用报告模板，\n" +
      "action=list 查看所有任务，\n" +
      "action=cancel 取消指定任务。\n" +
      "报告模板: morning_brief(早盘简报), market_close(收盘总结), " +
      "weekly_review(周度回顾), watchlist_update(自选股行情), daily_scan(每日选股), institutional_top3(机构行为Top3), daily_journal(交易日志), monthly_report(月度绩效), after_market_review(收盘自动复盘)",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["schedule", "report", "list_reports", "list", "cancel"],
          description:
            "操作: schedule=创建定时任务, report=报告模板推送, " +
            "list_reports=查看可用模板, list=查看任务, cancel=取消任务",
        },
        delayMinutes: {
          type: "number",
          description: "延迟分钟数（action=schedule 时使用），默认15分钟",
        },
        description: {
          type: "string",
          description: "任务描述（action=schedule 时使用），用于用户可读的通知",
        },
        toolName: {
          type: "string",
          description: "要执行的工具名称（action=schedule 时使用），如 market_data, stock_screen",
        },
        toolArgs: {
          type: "string",
          description: "工具参数 JSON 字符串（action=schedule 时使用）",
        },
        intervalMinutes: {
          type: "number",
          description: "重复间隔分钟数（可选，设置后任务会周期性执行）",
        },
        taskId: {
          type: "string",
          description: "任务ID（action=cancel 时使用）",
        },
        reportId: {
          type: "string",
          enum: [
            "morning_brief",
            "market_close",
            "weekly_review",
            "watchlist_update",
            "daily_scan",
            "institutional_top3",
            "daily_journal",
            "monthly_report",
            "after_market_review",
          ],
          description:
            "报告模板ID（action=report 时使用）：" +
            "morning_brief=早盘简报, market_close=收盘总结, weekly_review=周度回顾, " +
            "watchlist_update=自选股行情, daily_scan=每日选股, institutional_top3=机构行为Top3, daily_journal=交易日志, monthly_report=月度绩效, after_market_review=收盘自动复盘",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      switch (action) {
        case "schedule": {
          const delayMin = (args.delayMinutes as number) ?? 15;
          const description = (args.description as string) ?? "定时任务";
          const toolName = args.toolName as string;
          const intervalMin = args.intervalMinutes as number | undefined;

          if (!toolName) {
            return { error: "请指定要执行的工具名称 (toolName)" };
          }

          let toolArgs: Record<string, unknown> = {};
          if (args.toolArgs) {
            try {
              toolArgs = JSON.parse(args.toolArgs as string);
            } catch {
              return { error: "toolArgs 解析失败，请提供有效的 JSON 字符串" };
            }
          }

          const delayMs = delayMin * 60 * 1000;
          const intervalMs = intervalMin ? intervalMin * 60 * 1000 : undefined;

          const taskId = scheduler.scheduleDelay(
            description,
            delayMs,
            async () => {
              return await executeToolFn(toolName, toolArgs);
            },
            {
              ...(intervalMs ? { intervalMs } : {}),
              meta: { toolName, toolArgs },
            },
          );

          const scheduledTime = new Date(Date.now() + delayMs);
          return {
            success: true,
            taskId,
            description,
            scheduledAt: scheduledTime.toISOString(),
            delayMinutes: delayMin,
            ...(intervalMin ? { repeatEveryMinutes: intervalMin } : {}),
            message: `✅ 已安排任务「${description}」，将在 ${String(delayMin)} 分钟后执行`,
          };
        }

        case "list": {
          const tasks = scheduler.list();
          if (tasks.length === 0) {
            return { tasks: [], message: "暂无定时任务" };
          }
          return {
            tasks: tasks.map((t) => ({
              id: t.id,
              description: t.description,
              status: t.status,
              scheduledAt: t.scheduledAt,
              createdAt: t.createdAt,
              ...(t.intervalMs ? { repeatEveryMinutes: t.intervalMs / 60_000 } : {}),
              ...(t.lastError ? { lastError: t.lastError } : {}),
            })),
            pending: scheduler.pendingCount,
          };
        }

        case "cancel": {
          const taskId = args.taskId as string;
          if (!taskId) {
            return { error: "请指定要取消的任务ID (taskId)" };
          }
          const success = scheduler.cancel(taskId);
          if (!success) {
            return { error: `无法取消任务 ${taskId}（可能已完成或不存在）` };
          }
          return { success: true, message: `✅ 已取消任务 ${taskId}` };
        }

        case "report": {
          const reportId = args.reportId as string;
          if (!reportId) {
            return {
              error: "请指定报告模板ID (reportId)",
              availableReports: REPORT_TEMPLATES.map((t) => ({
                id: t.id,
                name: t.name,
                description: t.description,
              })),
            };
          }

          const template = findReportTemplate(reportId);
          if (!template) {
            return {
              error: `未知报告模板: ${reportId}`,
              availableReports: REPORT_TEMPLATES.map((t) => ({
                id: t.id,
                name: t.name,
                description: t.description,
              })),
            };
          }

          const delayMin = (args.delayMinutes as number) ?? 0;
          const intervalMin = args.intervalMinutes as number | undefined;
          const delayMs = delayMin * 60 * 1000;
          const intervalMs = intervalMin ? intervalMin * 60 * 1000 : undefined;

          const taskId = scheduler.scheduleDelay(
            `报告: ${template.name}`,
            delayMs,
            async () => {
              return await template.generate(executeToolFn, config);
            },
            {
              ...(intervalMs ? { intervalMs } : {}),
              meta: { reportId },
            },
          );

          return {
            success: true,
            taskId,
            reportId,
            reportName: template.name,
            scheduledAt: new Date(Date.now() + delayMs).toISOString(),
            ...(delayMin > 0 ? { delayMinutes: delayMin } : {}),
            ...(intervalMin ? { repeatEveryMinutes: intervalMin } : {}),
            message:
              delayMin > 0
                ? `✅ 已安排「${template.name}」，将在 ${String(delayMin)} 分钟后推送`
                : intervalMin
                  ? `✅ 已安排「${template.name}」，每 ${String(intervalMin)} 分钟推送一次`
                  : `✅ 已安排「${template.name}」，即将推送`,
          };
        }

        case "list_reports": {
          return {
            reports: REPORT_TEMPLATES.map((t) => ({
              id: t.id,
              name: t.name,
              description: t.description,
              defaultIntervalMinutes: t.defaultIntervalMinutes || "一次性",
            })),
          };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
