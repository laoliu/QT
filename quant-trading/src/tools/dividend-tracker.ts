/**
 * dividend_tracker tool — AI-callable interface for tracking dividend information
 * for held positions and any stock.
 *
 * Actions:
 *   upcoming      — show upcoming ex-dividend dates for held positions or a symbol list
 *   history       — show dividend history for a specific symbol
 *   portfolio     — show dividend summary for the entire portfolio (yield-on-cost, total income)
 *   calendar      — show the dividend calendar for the next N days across all A-shares
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { LivePosition, QuantTradingConfig } from "../types.js";

export function createDividendTrackerTool(
  _api: OpenClawPluginApi,
  config: QuantTradingConfig,
  getPositions: () => LivePosition[],
) {
  return {
    name: "dividend_tracker",
    description:
      "股息/分红追踪工具。" +
      "action=upcoming 查看持仓或指定股票的即将到来的除权除息日，" +
      "action=history 查看某只股票的历史分红记录，" +
      "action=portfolio 查看当前持仓的分红汇总（股息率、持仓成本收益率），" +
      "action=calendar 查看未来N天的全市场分红日历。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["upcoming", "history", "portfolio", "calendar"],
          description: "操作类型",
        },
        symbol: {
          type: "string",
          description: "股票代码 (history 必填; upcoming 可选，不填则查持仓全部)",
        },
        days: {
          type: "number",
          description: "向前/向后查看天数 (calendar 默认30; history 无限制)",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const symbol = args.symbol as string | undefined;
      const days = (args.days as number) ?? 30;

      switch (action) {
        case "upcoming": {
          const symbols = symbol
            ? [symbol]
            : getPositions()
                .filter((p) => p.quantity > 0 && p.market === "cn_stock")
                .map((p) => p.symbol);

          if (symbols.length === 0) {
            return { message: "当前无持仓或未指定股票代码" };
          }

          try {
            const result = await runPythonScript<{
              dividends?: Array<{
                symbol: string;
                name?: string;
                exDate?: string;
                recordDate?: string;
                payDate?: string;
                dividend?: number;
                bonus?: number;
                allotment?: number;
                allotmentPrice?: number;
              }>;
            }>(
              "engines/dividend_tracker.py",
              {
                action: "upcoming",
                symbols: symbols.join(","),
                days,
              },
              config,
            );

            if (!result.ok || !result.data?.dividends) {
              return { message: "获取分红数据失败", error: result.error };
            }

            const upcoming = result.data.dividends;
            if (upcoming.length === 0) {
              return {
                message: `未来 ${days} 天内所查询股票无除权除息安排`,
                symbols_checked: symbols.length,
              };
            }

            return {
              message: `发现 ${upcoming.length} 条即将到来的除权除息记录`,
              dividends: upcoming,
            };
          } catch (err) {
            return { error: `查询失败: ${(err as Error).message}` };
          }
        }

        case "history": {
          if (!symbol) {
            return { error: "请指定股票代码 (symbol)" };
          }

          try {
            const result = await runPythonScript<{
              history?: Array<{
                exDate: string;
                dividend?: number;
                bonus?: number;
                allotment?: number;
                allotmentPrice?: number;
                year?: string;
              }>;
              summary?: {
                totalYears: number;
                avgDividend: number;
                dividendYield: number;
                consecutiveYears: number;
              };
            }>(
              "engines/dividend_tracker.py",
              { action: "history", symbol },
              config,
            );

            if (!result.ok || !result.data?.history) {
              return { message: "获取分红历史失败", error: result.error };
            }

            return {
              symbol,
              history: result.data.history,
              summary: result.data.summary,
            };
          } catch (err) {
            return { error: `查询失败: ${(err as Error).message}` };
          }
        }

        case "portfolio": {
          const positions = getPositions().filter(
            (p) => p.quantity > 0 && p.market === "cn_stock",
          );

          if (positions.length === 0) {
            return { message: "当前无A股持仓" };
          }

          try {
            const symbols = positions.map((p) => p.symbol);
            const result = await runPythonScript<{
              portfolio_dividends?: Array<{
                symbol: string;
                name?: string;
                annualDividend?: number;
                dividendYield?: number;
                yieldOnCost?: number;
                lastExDate?: string;
                nextExDate?: string;
              }>;
              summary?: {
                totalAnnualDividend: number;
                weightedYield: number;
                weightedYieldOnCost: number;
              };
            }>(
              "engines/dividend_tracker.py",
              {
                action: "portfolio",
                symbols: symbols.join(","),
                positions: JSON.stringify(
                  positions.map((p) => ({
                    symbol: p.symbol,
                    quantity: p.quantity,
                    avgCost: p.avgCost,
                    currentPrice: p.currentPrice,
                    marketValue: p.marketValue,
                  })),
                ),
              },
              config,
            );

            if (!result.ok || !result.data?.portfolio_dividends) {
              return { message: "获取持仓分红汇总失败", error: result.error };
            }

            return {
              positions: result.data.portfolio_dividends,
              summary: result.data.summary,
              positionsCount: positions.length,
            };
          } catch (err) {
            return { error: `查询失败: ${(err as Error).message}` };
          }
        }

        case "calendar": {
          try {
            const result = await runPythonScript<{
              calendar?: Array<{
                symbol: string;
                name?: string;
                exDate: string;
                dividend?: number;
                bonus?: number;
              }>;
            }>(
              "engines/dividend_tracker.py",
              { action: "calendar", days },
              config,
            );

            if (!result.ok || !result.data?.calendar) {
              return { message: "获取分红日历失败", error: result.error };
            }

            const calendar = result.data.calendar;
            return {
              message: `未来 ${days} 天共有 ${calendar.length} 只股票除权除息`,
              calendar,
            };
          } catch (err) {
            return { error: `查询失败: ${(err as Error).message}` };
          }
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
