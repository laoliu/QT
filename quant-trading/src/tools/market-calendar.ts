/**
 * market_calendar tool — trading calendar, holidays, IPO dates, ex-dividend dates.
 *
 * Actions:
 *   trading_days     — check if a date is a trading day, list trading days in range
 *   holidays         — list upcoming market holidays
 *   ipo_calendar     — upcoming IPO subscription dates
 *   ex_dividend      — upcoming ex-dividend dates for a symbol or market-wide
 *   index_rebalance  — upcoming index rebalancing dates (e.g. CSI 300)
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createMarketCalendarTool(api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "market_calendar",
    description:
      "交易日历工具。查询交易日、节假日休市安排、新股IPO申购日历、除权除息日、指数调仓日。" +
      "action=trading_days 交易日查询, action=holidays 休市日历, " +
      "action=ipo_calendar IPO申购日历, action=ex_dividend 除权除息日, " +
      "action=index_rebalance 指数调仓日。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["trading_days", "holidays", "ipo_calendar", "ex_dividend", "index_rebalance"],
          description: "操作类型",
        },
        date: { type: "string", description: "查询日期 YYYY-MM-DD（trading_days时判断该日是否交易日）" },
        start_date: { type: "string", description: "起始日期 YYYY-MM-DD" },
        end_date: { type: "string", description: "结束日期 YYYY-MM-DD" },
        symbol: { type: "string", description: "标的代码（ex_dividend时查指定股票）" },
        index_code: {
          type: "string",
          description: "指数代码，如 000300(沪深300), 000016(上证50), 000905(中证500)",
        },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      switch (action) {
        case "trading_days": {
          const result = await runPythonScript<{
            is_trading_day?: boolean;
            trading_days?: string[];
            date?: string;
          }>(
            "engines/market_calendar.py",
            {
              action: "trading_days",
              date: args.date as string | undefined,
              start_date: args.start_date as string | undefined,
              end_date: args.end_date as string | undefined,
            },
            config,
          );
          if (!result.ok) return { error: result.error ?? "查询交易日失败" };
          return result.data;
        }

        case "holidays": {
          const year = args.date
            ? (args.date as string).slice(0, 4)
            : new Date().getFullYear().toString();
          const result = await runPythonScript<{
            holidays: Array<{ date: string; name: string; days: number }>;
          }>(
            "engines/market_calendar.py",
            { action: "holidays", year },
            config,
          );
          if (!result.ok) return { error: result.error ?? "查询休市日历失败" };
          return result.data;
        }

        case "ipo_calendar": {
          const result = await runPythonScript<{
            ipo_list: Array<{
              code: string;
              name: string;
              apply_date: string;
              listing_date?: string;
              issue_price?: number;
              pe_ratio?: number;
            }>;
          }>(
            "engines/market_calendar.py",
            { action: "ipo_calendar" },
            config,
          );
          if (!result.ok) return { error: result.error ?? "查询IPO日历失败" };
          return result.data;
        }

        case "ex_dividend": {
          const result = await runPythonScript<{
            ex_dividend_list: Array<{
              symbol: string;
              name: string;
              ex_date: string;
              dividend_per_share?: number;
              bonus_ratio?: number;
              transfer_ratio?: number;
              record_date?: string;
            }>;
          }>(
            "engines/market_calendar.py",
            {
              action: "ex_dividend",
              symbol: args.symbol as string | undefined,
              start_date: args.start_date as string | undefined,
              end_date: args.end_date as string | undefined,
            },
            config,
          );
          if (!result.ok) return { error: result.error ?? "查询除权除息日失败" };
          return result.data;
        }

        case "index_rebalance": {
          const indexCode = (args.index_code as string) ?? "000300";
          const result = await runPythonScript<{
            index_name: string;
            next_rebalance_date?: string;
            recent_changes?: Array<{
              date: string;
              added: string[];
              removed: string[];
            }>;
          }>(
            "engines/market_calendar.py",
            { action: "index_rebalance", index_code: indexCode },
            config,
          );
          if (!result.ok) return { error: result.error ?? "查询指数调仓日失败" };
          return result.data;
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
