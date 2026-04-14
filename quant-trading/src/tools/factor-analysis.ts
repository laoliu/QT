/**
 * factor_analysis tool — AI-callable for Aipha factor research.
 *
 * Actions:
 *   list    — List all registered factors and categories
 *   compute — Compute factor values for given symbols
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createFactorAnalysisTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "factor_analysis",
    description:
      "因子分析工具（Aipha 因子框架）。分析股票的基本面因子、量价因子、动量因子。\n" +
      "• action=list — 列出所有可用因子及分类\n" +
      "• action=compute — 计算指定股票的因子值（可归一化 rank/zscore）",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["list", "compute"],
          description: "操作类型: list=列出因子, compute=计算因子值",
        },
        factorName: {
          type: "string",
          description: "因子名称（action=compute 时使用），如 'pe', 'momentum', 'roe'",
        },
        symbols: {
          type: "array",
          items: { type: "string" },
          description: "股票代码列表（action=compute 时使用），如 ['600519', '000858']",
        },
        tradeDate: {
          type: "string",
          description: "交易日期（YYYY-MM-DD），默认今天",
        },
        normalize: {
          type: "string",
          enum: ["rank", "zscore"],
          description: "归一化方法（可选）",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "list";
      const factorName = args.factorName as string | undefined;
      const symbols = args.symbols as string[] | undefined;
      const tradeDate = args.tradeDate as string | undefined;
      const normalize = args.normalize as string | undefined;

      const pyResult = await runPythonScript<{
        ok: boolean;
        data?: Record<string, unknown>;
        error?: string;
      }>(
        "engines/aipha_factor_engine.py",
        {
          action,
          factor_name: factorName,
          symbols: symbols ?? [],
          trade_date: tradeDate,
          normalize,
        },
        config,
      );

      if (pyResult.ok && pyResult.data) {
        return pyResult.data;
      }

      return {
        error: pyResult.error ?? "因子分析引擎执行失败",
      };
    },
  };
}
