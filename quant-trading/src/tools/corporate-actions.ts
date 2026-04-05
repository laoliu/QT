/**
 * corporate_actions tool — dividend tracking, rights issues, stock splits, name changes.
 *
 * Actions:
 *   dividends        — query dividend history and upcoming dividends for a stock
 *   rights_issue     — upcoming rights issue (配股) events
 *   stock_split      — stock split/reverse split history
 *   allotment        — share allotment (增发) schedule
 *   name_changes     — stock name change history
 *   summary          — all recent corporate actions for a symbol
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createCorporateActionsTool(api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "corporate_actions",
    description:
      "公司行动/分红送转工具。查询分红派息、配股、增发、股票拆分、更名记录。" +
      "action=dividends 分红记录, action=rights_issue 配股, " +
      "action=stock_split 拆分/缩股, action=allotment 增发, " +
      "action=name_changes 更名历史, action=summary 综合查询。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["dividends", "rights_issue", "stock_split", "allotment", "name_changes", "summary"],
          description: "操作类型",
        },
        symbol: { type: "string", description: "标的代码 (如 600519)" },
        year: { type: "string", description: "查询年份 (如 2025)" },
        upcoming_only: {
          type: "boolean",
          description: "仅显示未来的事件（默认 true）",
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
          if (!symbol) return { error: "请提供 symbol" };
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
          if (!result.ok) return { error: result.error ?? "查询分红记录失败" };
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
          if (!result.ok) return { error: result.error ?? "查询配股信息失败" };
          return result.data;
        }

        case "stock_split": {
          if (!symbol) return { error: "请提供 symbol" };
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
          if (!result.ok) return { error: result.error ?? "查询拆分记录失败" };
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
          if (!result.ok) return { error: result.error ?? "查询增发信息失败" };
          return result.data;
        }

        case "name_changes": {
          if (!symbol) return { error: "请提供 symbol" };
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
          if (!result.ok) return { error: result.error ?? "查询更名历史失败" };
          return result.data;
        }

        case "summary": {
          if (!symbol) return { error: "请提供 symbol" };
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
          if (!result.ok) return { error: result.error ?? "查询公司行动失败" };
          return result.data;
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
