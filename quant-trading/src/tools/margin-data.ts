/**
 * margin_data tool — AI-callable for margin trading data (融资融券).
 *
 * Actions:
 *   query   — 查询个股融资融券数据（融资余额、融券余量、融资买入额、融券卖出量）
 *   trend   — 两融余额变动趋势（近N日）
 *   ranking — 全市场两融数据排行（融资净买入TOP / 融券净卖出TOP）
 *   alert   — 两融变动异常检测（融资余额大幅增加/减少的标的）
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createMarginDataTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "margin_data",
    description:
      "融资融券数据查询工具。查看个股/全市场的融资融券数据，包括融资余额、融券余量、两融趋势和异动检测。\n" +
      "• action=query — 查询个股融资融券明细（融资余额、融券余量、融资买入额、融券卖出量）\n" +
      "• action=trend — 个股两融余额变动趋势（近N日，含变动率）\n" +
      "• action=ranking — 全市场两融排行（融资净买入TOP、融券净卖出TOP）\n" +
      "• action=alert — 两融异动检测（融资余额大幅变动的标的）",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["query", "trend", "ranking", "alert"],
          description:
            "操作类型: query=个股融资融券查询, trend=两融趋势, ranking=两融排行, alert=异动检测",
        },
        symbol: {
          type: "string",
          description: "股票代码（action=query/trend时必填），如 600519",
        },
        days: {
          type: "number",
          description: "天数，trend默认30天，alert默认5天",
        },
        limit: {
          type: "number",
          description: "返回数量限制，默认20",
        },
        threshold: {
          type: "number",
          description: "异动检测阈值（百分比），默认10，即融资买入额/融资余额超过10%视为异动",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "query";
      const symbol = args.symbol as string | undefined;
      const days = args.days as number | undefined;
      const limit = (args.limit as number) ?? 20;
      const threshold = args.threshold as number | undefined;

      const pyResult = await runPythonScript<{
        action?: string;
        symbol?: string;
        records?: Array<Record<string, unknown>>;
        trends?: Array<Record<string, unknown>>;
        rzBuyTop?: Array<Record<string, unknown>>;
        rqSellTop?: Array<Record<string, unknown>>;
        increase?: Array<Record<string, unknown>>;
        decrease?: Array<Record<string, unknown>>;
        count?: number;
        error?: string;
        errors?: string[];
      }>("engines/margin_data.py", { action, symbol, days, limit, threshold }, config);

      if (pyResult.ok && pyResult.data) {
        const data = pyResult.data;
        if (data.error) {
          return { error: data.error, errors: data.errors };
        }

        // Display hints for large results
        const count = data.count ?? 0;
        if (count > 10) {
          (data as Record<string, unknown>).displayHint =
            `融资融券数据共${String(count)}条，请精简展示关键指标。`;
        }

        return data;
      }

      return {
        action,
        note: "融资融券引擎未就绪。" + (pyResult.error ? ` 错误: ${pyResult.error}` : ""),
      };
    },
  };
}
