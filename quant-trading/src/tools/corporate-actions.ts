/**
 * corporate_actions tool 66dividend tracking, rights issues, stock splits, name changes.
 *
 * Actions:
 *   dividends        66query dividend history and upcoming dividends for a stock
 *   rights_issue     66upcoming rights issue (配股) events
 *   stock_split      66stock split/reverse split history
 *   allotment        66share allotment (增发) schedule
 *   name_changes     66stock name change history
 *   summary          66all recent corporate actions for a symbol
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createCorporateActionsTool(api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "corporate_actions",
    description:
      "04/000811090004070504" +
      "action=dividends 0004, action=rights_issue 配股, " +
      "action=stock_split 拆分/缩股, action=allotment 增发, " +
      "action=name_changes 更名历史, action=summary 100009",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["dividends", "rights_issue", "stock_split", "allotment", "name_changes", "summary"],
          description: "操作类型",
        },
        symbol: { type: "string", description: "标的代码 (j600519)" },
        year: { type: "string", description: "查询年份 (j2025)" },
        upcoming_only: {
          type: "boolean",
          description: "05ā0408xtrue锛",
        },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const symbol = args.symbol as string | undefined;
      const year = args.year as string | undefined;
      const upcomingOnly = (args.upcoming_only as boolean) ?? true;

      switch (action) {
        case "dividends": {
          if (!symbol) return { error: "请提供symbol" };
          const result = await runPythonScript<{
            dividends: Array<{
              report_year: string;
              announce_date: string;
              ex_date?: string;
              record_date?: string;
              pay_date?: string;
              dividend_per_share: number;
              bonus_ratio?: number;
              transfer_ratio?: number;
              status: string;
            }>;
            total_dividend_yield?: number;
          }>(
            "engines/corporate_actions.py",
            { action: "dividends", symbol, year, upcoming_only: upcomingOnly },
            config,
          );
          if (!result.ok) return { error: result.error ?? "09000402" };
          return result.data;
        }

        case "rights_issue": {
          const result = await runPythonScript<{
            rights_issues: Array<{
              symbol: string;
              name: string;
              announce_date: string;
              ex_date?: string;
              price: number;
              ratio: string;
            }>;
          }>(
            "engines/corporate_actions.py",
            { action: "rights_issue", symbol, upcoming_only: upcomingOnly },
            config,
          );
          if (!result.ok) return { error: result.error ?? "090402" };
          return result.data;
        }

        case "stock_split": {
          if (!symbol) return { error: "请提供symbol" };
          const result = await runPythonScript<{
            splits: Array<{
              date: string;
              split_ratio: string;
              type: string;
            }>;
          }>(
            "engines/corporate_actions.py",
            { action: "stock_split", symbol },
            config,
          );
          if (!result.ok) return { error: result.error ?? "09020402" };
          return result.data;
        }

        case "allotment": {
          const result = await runPythonScript<{
            allotments: Array<{
              symbol: string;
              name: string;
              announce_date: string;
              issue_date?: string;
              issue_price?: number;
              shares: number;
              purpose: string;
            }>;
          }>(
            "engines/corporate_actions.py",
            { action: "allotment", symbol, upcoming_only: upcomingOnly },
            config,
          );
          if (!result.ok) return { error: result.error ?? "090402" };
          return result.data;
        }

        case "name_changes": {
          if (!symbol) return { error: "请提供symbol" };
          const result = await runPythonScript<{
            name_changes: Array<{
              date: string;
              old_name: string;
              new_name: string;
            }>;
          }>(
            "engines/corporate_actions.py",
            { action: "name_changes", symbol },
            config,
          );
          if (!result.ok) return { error: result.error ?? "090802" };
          return result.data;
        }

        case "summary": {
          if (!symbol) return { error: "请提供symbol" };
          const result = await runPythonScript<{
            symbol: string;
            name: string;
            recent_dividends: unknown[];
            upcoming_events: unknown[];
            last_split?: unknown;
          }>(
            "engines/corporate_actions.py",
            { action: "summary", symbol },
            config,
          );
          if (!result.ok) return { error: result.error ?? "09040102" };
          return result.data;
        }

        default:
          return { error: `ā04: ${action}` };
      }
    },
  };
}
