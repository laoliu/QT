/**
 * stock_compare tool — AI-callable for head-to-head multi-dimensional stock comparison.
 *
 * Actions:
 *   compare — Compare 2-5 stocks across technicals, fundamentals, capital flow, and valuation
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createStockCompareTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "stock_compare",
    description:
      "股票对比工具。支持2-5只股票多维度并排对比（技术面、基本面、资金流、估值）。\n" +
      "• action=compare — 多维度对比（需指定symbols，逗号分隔的股票代码）\n" +
      "• dimensions可选: technicals, fundamentals, capital_flow, valuation（默认全部）",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["compare"],
          description: "操作类型: compare=多维度对比",
        },
        symbols: {
          type: "string",
          description: "股票代码列表，逗号分隔，如 600519,000858,603288（2-5只）",
        },
        dimensions: {
          type: "string",
          description:
            "对比维度，逗号分隔，如 technicals,fundamentals,capital_flow,valuation（默认全部）",
        },
      },
      required: ["symbols"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "compare";
      const symbols = args.symbols as string;
      const dimensions = args.dimensions as string | undefined;

      const pyResult = await runPythonScript<{
        action?: string;
        stocks?: Array<Record<string, unknown>>;
        rankings?: Record<string, unknown>;
        count?: number;
        error?: string;
        errors?: string[];
      }>("engines/stock_compare.py", { action, symbols, dimensions }, config);

      if (pyResult.ok && pyResult.data) {
        const data = pyResult.data;
        if (data.error) {
          return { error: data.error, errors: data.errors };
        }

        const count = data.count ?? 0;
        if (count > 0) {
          (data as Record<string, unknown>).displayHint =
            `${String(count)}只股票多维度对比结果。请用表格展示各维度排名，并给出综合对比结论。`;
        }

        return data;
      }

      return {
        action,
        note: "股票对比引擎未就绪。" + (pyResult.error ? ` 错误: ${pyResult.error}` : ""),
      };
    },
  };
}
