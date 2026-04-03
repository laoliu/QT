/**
 * fund_flow tool — AI-callable for fund/money flow analysis (资金流向).
 *
 * Actions:
 *   rank    — Today's individual stock fund flow ranking
 *   stock   — Historical fund flow for a specific stock
 *   sector  — Sector-level fund flow ranking
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createFundFlowTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "fund_flow",
    description:
      "资金流向分析工具。查看个股/行业的主力资金、超大单、大单、中小单资金流入流出，以及北向资金（沪深港通）。\n" +
      "• action=rank — 今日个股主力资金净流入排名\n" +
      "• action=stock — 个股历史资金流向（需指定symbol）\n" +
      "• action=sector — 行业板块资金流向排名\n" +
      "• action=northbound — 北向资金（沪深港通）实时流入+近5日历史",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["rank", "stock", "sector", "northbound"],
          description:
            "操作类型: rank=今日资金排名, stock=个股资金流向, sector=行业资金流向, northbound=北向资金",
        },
        symbol: {
          type: "string",
          description: "股票代码（action=stock时必填），如 600519",
        },
        limit: {
          type: "number",
          description: "返回数量限制，默认20",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "rank";
      const symbol = args.symbol as string | undefined;
      const limit = (args.limit as number) ?? 20;

      const pyResult = await runPythonScript<{
        action?: string;
        symbol?: string;
        stocks?: Array<Record<string, unknown>>;
        sectors?: Array<Record<string, unknown>>;
        flows?: Array<Record<string, unknown>>;
        count?: number;
        error?: string;
        errors?: string[];
      }>("engines/fund_flow.py", { action, symbol, limit }, config);

      if (pyResult.ok && pyResult.data) {
        const data = pyResult.data;
        if (data.error) {
          return { error: data.error, errors: data.errors };
        }

        // Display hints for large results
        const count = data.count ?? 0;
        if (count > 10) {
          (data as Record<string, unknown>).displayHint =
            `资金流向数据共${String(count)}条，请分组展示：主力净流入前10名 + 主力净流出前10名。`;
        }

        return data;
      }

      return {
        action,
        note: "资金流向引擎未就绪。" + (pyResult.error ? ` 错误: ${pyResult.error}` : ""),
      };
    },
  };
}
