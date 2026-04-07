/**
 * support_resistance tool — AI-callable for support/resistance level calculation.
 *
 * Actions:
 *   levels — Calculate comprehensive support/resistance levels from price history
 *   pivot  — Calculate daily pivot points (Standard, Fibonacci, Camarilla)
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createSupportResistanceTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "support_resistance",
    description:
      "支撑压力位计算工具。基于历史价格算法化识别关键支撑位和压力位。\n" +
      "• action=levels — 综合支撑/压力位（前高前低、量价密集区、均线、斐波那契）\n" +
      "• action=pivot — 日内枢轴点（标准、斐波那契、卡玛利拉三种方法）",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["levels", "pivot"],
          description: "操作类型: levels=综合支撑压力位, pivot=枢轴点计算",
        },
        symbol: {
          type: "string",
          description: "股票代码，如 600519",
        },
        lookback: {
          type: "number",
          description: "回溯天数（action=levels时使用），默认120",
        },
      },
      required: ["symbol"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "levels";
      const symbol = args.symbol as string;
      const lookback = (args.lookback as number) ?? 120;

      const pyResult = await runPythonScript<{
        action?: string;
        symbol?: string;
        current_price?: number;
        supports?: Array<Record<string, unknown>>;
        resistances?: Array<Record<string, unknown>>;
        standard?: Record<string, unknown>;
        fibonacci?: Record<string, unknown>;
        camarilla?: Record<string, unknown>;
        error?: string;
        errors?: string[];
      }>("engines/support_resistance.py", { action, symbol, lookback }, config);

      if (pyResult.ok && pyResult.data) {
        const data = pyResult.data;
        if (data.error) {
          return { error: data.error, errors: data.errors };
        }
        return data;
      }

      return {
        action,
        symbol,
        note: "支撑压力位引擎未就绪。" + (pyResult.error ? ` 错误: ${pyResult.error}` : ""),
      };
    },
  };
}
