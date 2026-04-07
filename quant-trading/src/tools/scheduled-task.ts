/**
 * scheduled_task tool — AI-callable tool for scheduling delayed/periodic/cron tasks.
 *
 * Allows the AI to:
 * - Schedule a retry of a failed operation (e.g., "retry in 15 minutes")
 * - Set up periodic reports (e.g., "每30分钟推送一次行情")
 * - Set up cron-based schedules (e.g., "每天07:00晨间简报")
 * - List/cancel pending tasks and cron tasks
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { describeCron } from "../services/cron-parser.js";
import { REPORT_TEMPLATES, findReportTemplate } from "../services/report-templates.js";
import type { TaskScheduler } from "../services/task-scheduler.js";
import type { QuantTradingConfig } from "../types.js";

/** Default cron presets for trading day schedules. */
export const DEFAULT_CRON_PRESETS = [
  {
    id: "morning_brief_cron",
    cronExpr: "0 7 * * 1-5",
    reportId: "morning_brief",
    description: "周一至周五 07:00 晨间简报",
  },
  {
    id: "market_close_cron",
    cronExpr: "10 15 * * 1-5",
    reportId: "market_close",
    description: "周一至周五 15:10 收盘复盘",
  },
  {
    id: "weekly_review_cron",
    cronExpr: "0 17 * * 5",
    reportId: "weekly_review",
    description: "每周五 17:00 周度回顾",
  },
] as const;

export function createScheduledTaskTool(
  api: OpenClawPluginApi,
  config: QuantTradingConfig,
  scheduler: TaskScheduler,
  executeToolFn: (toolName: string, args: Record<string, unknown>) => Promise<string>,
) {
  return {
    name: "scheduled_task",
    description:
      "定时任务管理。用于安排延迟重试、定期推送、报告模板、cron定时报告。\n" +
      "action=schedule 创建定时任务（延迟执行指定工具调用），\n" +
      "action=report 使用预置报告模板创建定期推送，\n" +
      "action=schedule_cron 创建cron定时任务（固定时间触发），\n" +
      "action=list_cron 查看所有cron定时任务，\n" +
      "action=delete_cron 删除cron定时任务，\n" +
      "action=setup_defaults 安装默认交易日定时计划，\n" +
      "action=list_reports 查看可用报告模板，\n" +
      "action=list 查看所有延迟任务，\n" +
      "action=cancel 取消指定延迟任务。\n" +
      "报告模板: morning_brief(早盘简报), market_close(收盘总结), " +
      "weekly_review(周度回顾), watchlist_update(自选股行情), daily_scan(每日选股), institutional_top3(机构行为Top3), daily_journal(交易日志), monthly_report(月度绩效)",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: [
            "schedule",
            "report",
            "schedule_cron",
            "list_cron",
            "delete_cron",
            "setup_defaults",
            "list_reports",
            "list",
            "cancel",
          ],
          description:
            "操作: schedule=创建延迟任务, report=报告模板推送, " +
            "schedule_cron=创建cron定时任务, list_cron=查看cron任务, " +
            "delete_cron=删除cron任务, setup_defaults=安装默认定时计划, " +
            "list_reports=查看可用模板, list=查看延迟任务, cancel=取消延迟任务",
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
          description: "任务ID（action=cancel/delete_cron 时使用）",
        },
        cronExpr: {
          type: "string",
          description:
            'cron表达式（action=schedule_cron 时使用），5字段格式: "分 时 日 月 周"。' +
            '例: "0 7 * * 1-5"=工作日07:00, "10 15 * * 1-5"=工作日15:10, "0 17 * * 5"=周五17:00',
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
          ],
          description:
            "报告模板ID（action=report 时使用）：" +
            "morning_brief=早盘简报, market_close=收盘总结, weekly_review=周度回顾, " +
            "watchlist_update=自选股行情, daily_scan=每日选股, institutional_top3=机构行为Top3, daily_journal=交易日志, monthly_report=月度绩效",
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

        // ── Cron actions ──────────────────────────────────────────

        case "schedule_cron": {
          const cronExpr = args.cronExpr as string;
          if (!cronExpr) {
            return {
              error:
                '请指定cron表达式 (cronExpr)，格式: "分 时 日 月 周"。' +
                '例: "0 7 * * 1-5" = 工作日07:00',
            };
          }

          const reportId = args.reportId as string | undefined;
          const toolName = args.toolName as string | undefined;
          const description = args.description as string | undefined;

          if (!reportId && !toolName) {
            return { error: "请指定 reportId 或 toolName" };
          }

          let run: () => Promise<string>;
          let desc: string;
          let meta: { cronExpr: string; reportId?: string; toolName?: string; toolArgs?: Record<string, unknown> };

          if (reportId) {
            const template = findReportTemplate(reportId);
            if (!template) {
              return {
                error: `未知报告模板: ${reportId}`,
                availableReports: REPORT_TEMPLATES.map((t) => ({
                  id: t.id,
                  name: t.name,
                })),
              };
            }
            desc = description ?? `定时报告: ${template.name}`;
            run = async () => template.generate(executeToolFn, config);
            meta = { cronExpr, reportId };
          } else {
            let toolArgs: Record<string, unknown> = {};
            if (args.toolArgs) {
              try {
                toolArgs = JSON.parse(args.toolArgs as string);
              } catch {
                return { error: "toolArgs 解析失败，请提供有效的 JSON 字符串" };
              }
            }
            desc = description ?? `定时执行: ${toolName}`;
            const capturedToolName = toolName!;
            const capturedToolArgs = toolArgs;
            run = async () => executeToolFn(capturedToolName, capturedToolArgs);
            meta = { cronExpr, toolName, toolArgs };
          }

          try {
            const taskId = scheduler.scheduleCron(desc, cronExpr, run, meta);
            const cronTask = scheduler.getCron(taskId);
            return {
              success: true,
              taskId,
              description: desc,
              cronExpr,
              cronDescription: describeCron(cronExpr),
              nextFireAt: cronTask?.nextFireAt,
              message: `✅ 已创建定时任务「${desc}」，${describeCron(cronExpr)}`,
            };
          } catch (err) {
            return {
              error: `cron表达式无效: ${err instanceof Error ? err.message : String(err)}`,
            };
          }
        }

        case "list_cron": {
          const cronTasks = scheduler.listCron();
          if (cronTasks.length === 0) {
            return { tasks: [], message: "暂无cron定时任务" };
          }
          return {
            tasks: cronTasks.map((t) => ({
              id: t.id,
              description: t.description,
              cronExpr: t.cronExpr,
              cronDescription: describeCron(t.cronExpr),
              enabled: t.enabled,
              nextFireAt: t.nextFireAt,
              lastRunAt: t.lastRunAt,
              lastError: t.lastError,
              createdAt: t.createdAt,
            })),
            total: cronTasks.length,
          };
        }

        case "delete_cron": {
          const taskId = args.taskId as string;
          if (!taskId) {
            return { error: "请指定要删除的cron任务ID (taskId)" };
          }
          const success = scheduler.cancelCron(taskId);
          if (!success) {
            return { error: `无法删除cron任务 ${taskId}（可能不存在）` };
          }
          return { success: true, message: `✅ 已删除cron定时任务 ${taskId}` };
        }

        case "setup_defaults": {
          const created: Array<{ id: string; description: string; cronExpr: string }> = [];
          const skipped: string[] = [];

          // Check existing cron tasks to avoid duplicates
          const existingCronTasks = scheduler.listCron();
          const existingCronExprs = new Set(existingCronTasks.map((t) => t.cronExpr));

          for (const preset of DEFAULT_CRON_PRESETS) {
            if (existingCronExprs.has(preset.cronExpr)) {
              skipped.push(preset.description);
              continue;
            }

            const template = findReportTemplate(preset.reportId);
            if (!template) {
              skipped.push(`${preset.description} (模板不存在)`);
              continue;
            }

            const taskId = scheduler.scheduleCron(
              preset.description,
              preset.cronExpr,
              async () => template.generate(executeToolFn, config),
              { cronExpr: preset.cronExpr, reportId: preset.reportId },
            );
            created.push({
              id: taskId,
              description: preset.description,
              cronExpr: preset.cronExpr,
            });
          }

          return {
            success: true,
            created,
            skipped,
            message:
              created.length > 0
                ? `✅ 已安装 ${String(created.length)} 个默认定时计划` +
                  (skipped.length > 0
                    ? `，跳过 ${String(skipped.length)} 个已存在的计划`
                    : "")
                : "所有默认计划已存在，无需重复安装",
          };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
