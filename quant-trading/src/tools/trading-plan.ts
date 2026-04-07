/**
 * trading_plan tool — AI-callable for per-stock daily trading plan generation.
 *
 * Actions:
 *   generate — Generate trading plan for a single stock
 *   batch    — Generate plans for multiple stocks (portfolio/watchlist)
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createTradingPlanTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "trading_plan",
    description:
      "交易计划工具。为个股生成每日交易计划，包含入场区间、目标价、止损位、风险收益比。\n" +
      "• action=generate — 单只股票交易计划（需指定symbol）\n" +
      "• action=batch — 批量生成多只股票交易计划（需指定symbols，逗号分隔，最多10只）\n" +
      "• 可选 entry_price 指定计划入场价",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["generate", "batch"],
          description: "操作类型: generate=单股计划, batch=批量计划",
        },
        symbol: {
          type: "string",
          description: "股票代码（action=generate时使用），如 600519",
        },
        symbols: {
          type: "string",
          description: "股票代码列表（action=batch时使用），逗号分隔",
        },
        entry_price: {
          type: "number",
          description: "预期入场价格（可选），用于计算止损和目标价",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "generate";
      const symbol = args.symbol as string | undefined;
      const symbols = args.symbols as string | undefined;
      const entry_price = args.entry_price as number | undefined;

      const pyResult = await runPythonScript<{
        action?: string;
        plan?: Record<string, unknown>;
        plans?: Array<Record<string, unknown>>;
        count?: number;
        error?: string;
        errors?: string[];
      }>(
        "engines/trading_plan.py",
        { action, symbol, symbols, entry_price },
        config,
      );

      if (pyResult.ok && pyResult.data) {
        const data = pyResult.data;
        if (data.error) {
          return { error: data.error, errors: data.errors };
        }
        return data;
      }

      return {
        action,
        note: "交易计划引擎未就绪。" + (pyResult.error ? ` 错误: ${pyResult.error}` : ""),
      };
    },
  };
}
