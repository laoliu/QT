/**
 * dragon_tiger tool — AI-callable for Dragon Tiger List data (龙虎榜).
 *
 * Actions:
 *   daily       — Today's (or specific date) dragon tiger list
 *   stock       — Dragon tiger history for a specific stock
 *   institution — Institutional activity summary
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createDragonTigerTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "dragon_tiger",
    description:
      "龙虎榜数据工具。查看当日龙虎榜上榜个股、个股龙虎榜历史、机构买卖统计、营业部追踪、游资排名。\n" +
      "• action=daily — 当日龙虎榜上榜个股（可指定date，格式YYYYMMDD）\n" +
      "• action=stock — 查看某只股票的龙虎榜历史记录（需指定symbol）\n" +
      "• action=institution — 近一月龙虎榜机构买卖统计\n" +
      "• action=desk — 追踪某营业部的龙虎榜记录（指定desk关键词，如'华泰'）\n" +
      "• action=hot_money — 游资（活跃营业部）排名",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["daily", "stock", "institution", "desk", "hot_money"],
          description: "操作类型: daily=当日龙虎榜, stock=个股历史, institution=机构统计, desk=营业部追踪, hot_money=游资排名",
        },
        symbol: {
          type: "string",
          description: "股票代码（action=stock时必填），如 600519",
        },
        date: {
          type: "string",
          description: "日期（action=daily时可选），格式 YYYYMMDD，如 20260303。默认最近交易日。",
        },
        desk: {
          type: "string",
          description: "营业部名称关键词（action=desk时），如 '华泰' '中信证券'。留空则返回活跃营业部排名。",
        },
        limit: {
          type: "number",
          description: "返回数量限制，默认20",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "daily";
      const symbol = args.symbol as string | undefined;
      const date = args.date as string | undefined;
      const desk = args.desk as string | undefined;
      const limit = (args.limit as number) ?? 20;

      const pyResult = await runPythonScript<{
        action?: string;
        date?: string;
        symbol?: string;
        stocks?: Array<Record<string, unknown>>;
        records?: Array<Record<string, unknown>>;
        hotMoney?: Array<Record<string, unknown>>;
        count?: number;
        error?: string;
        errors?: string[];
      }>("engines/dragon_tiger.py", { action, symbol, date, desk, limit }, config);

      if (pyResult.ok && pyResult.data) {
        const data = pyResult.data;
        if (data.error) {
          return { error: data.error, errors: data.errors };
        }

        // Display hints for large results
        const count = data.count ?? 0;
        if (count > 10) {
          (data as Record<string, unknown>).displayHint =
            `龙虎榜共${String(count)}条记录，请分多条消息展示（每条5-8只），确保完整显示。`;
        }

        return data;
      }

      return {
        action,
        note: "龙虎榜引擎未就绪。" + (pyResult.error ? ` 错误: ${pyResult.error}` : ""),
      };
    },
  };
}
