/**
 * performance_report tool — AI-callable consolidated performance report.
 *
 * Generates comprehensive trading performance reports with multiple output
 * formats: JSON summary, text, HTML, and chart data.
 *
 * Actions:
 *   generate       — Full performance report (JSON + text + HTML + charts)
 *   summary        — Quick text summary only
 *   html           — HTML report (self-contained page)
 *   chart_data     — Chart-ready data series (equity curve, drawdown, PnL bars, etc.)
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { QuantTradingConfig } from "../types.js";
import { generatePerformanceReport } from "../services/performance-report.js";

export function createPerformanceReportTool(
  _api: OpenClawPluginApi,
  config: QuantTradingConfig,
) {
  return {
    name: "performance_report",
    description:
      "交易绩效报告生成工具。生成含汇总报表、分时段/分标的绩效明细、图表数据和HTML报告。\n" +
      "action=generate 完整报告, action=summary 文本摘要, " +
      "action=html HTML报告页面, action=chart_data 图表数据。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["generate", "summary", "html", "chart_data"],
          description: "报告类型",
        },
        from: { type: "string", description: "起始日期 YYYY-MM-DD (可选，默认全部)" },
        to: { type: "string", description: "截止日期 YYYY-MM-DD (可选，默认今天)" },
        mode: {
          type: "string",
          enum: ["paper", "live"],
          description: "交易模式 (默认根据配置自动判断)",
        },
        top_n: { type: "number", description: "显示前N个标的/策略 (默认5)" },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      try {
        const report = await generatePerformanceReport(config, {
          from: args.from as string | undefined,
          to: args.to as string | undefined,
          mode: args.mode as "paper" | "live" | undefined,
          topN: (args.top_n as number) ?? 5,
        });

        switch (action) {
          case "generate":
            return {
              summary: report.summary,
              charts: report.charts,
              text: report.text,
              htmlLength: report.html.length,
              hint: "使用 action=html 获取完整 HTML 报告页面，或 action=summary 获取文本摘要",
            };

          case "summary":
            return { text: report.text };

          case "html":
            return { html: report.html };

          case "chart_data":
            return { charts: report.charts };

          default:
            return { error: `未知操作: ${action}` };
        }
      } catch (err) {
        return { error: `报告生成失败: ${(err as Error).message}` };
      }
    },
  };
}
