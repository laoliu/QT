/**
 * tax_report tool — AI-callable for tax calculation and cost reporting.
 *
 * Actions:
 *   realized_gains  — View realized capital gains/losses
 *   trade_costs     — View trade cost breakdown (commission, stamp duty, etc.)
 *   tax_summary     — Comprehensive tax summary for a period
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { TaxReportService } from "../services/tax-report.js";
import type { DividendTracker } from "../services/dividend-tracker.js";

export function createTaxReportTool(
  _api: OpenClawPluginApi,
  taxService: TaxReportService,
  dividendTracker: DividendTracker,
) {
  return {
    name: "tax_report",
    description:
      "税费/成本报告工具。查看已实现盈亏、交易费用（佣金/印花税/过户费）、年度税务汇总。\n" +
      "• action=realized_gains — 查看已实现盈亏明细\n" +
      "• action=trade_costs — 查看交易费用汇总\n" +
      "• action=tax_summary — 综合税务汇总（含分红收入）",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["realized_gains", "trade_costs", "tax_summary"],
          description: "操作类型",
        },
        from_date: { type: "string", description: "起始日期 YYYY-MM-DD" },
        to_date: { type: "string", description: "截止日期 YYYY-MM-DD" },
        mode: {
          type: "string",
          enum: ["paper", "live"],
          description: "模式: paper=模拟(默认), live=实盘",
        },
        limit: { type: "number", description: "返回数量 (realized_gains)" },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const year = new Date().getFullYear();
      const fromDate = (args.from_date as string) ?? `${year}-01-01`;
      const toDate = (args.to_date as string) ?? new Date().toISOString().slice(0, 10);
      const mode = (args.mode as "paper" | "live") ?? "paper";

      switch (action) {
        case "realized_gains": {
          const gains = await taxService.getRealizedGains(fromDate, toDate, mode);
          const limit = (args.limit as number) ?? 50;
          const sliced = gains.slice(-limit);
          const totalNet = gains.reduce((s, g) => s + g.netPnl, 0);
          return {
            fromDate,
            toDate,
            mode,
            totalRecords: gains.length,
            totalNetPnl: Number(totalNet.toFixed(2)),
            records: sliced,
          };
        }

        case "trade_costs": {
          const costs = await taxService.getTradeCosts(fromDate, toDate, mode);
          return { fromDate, toDate, mode, ...costs };
        }

        case "tax_summary": {
          // Get dividend income for the period
          const dividendSummary = dividendTracker.getIncomeSummary(fromDate, toDate, mode);
          const summary = await taxService.getTaxSummary(
            fromDate,
            toDate,
            mode,
            dividendSummary.totalIncome,
          );
          return { mode, ...summary };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
