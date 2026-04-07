/**
 * stock_compare tool — AI-callable for multi-stock comparison (个股对比).
 *
 * Compare any N arbitrary stocks across price performance, technical indicators,
 * and fundamentals side-by-side (not limited to same-industry peers).
 *
 * Actions:
 *   compare       — full comparison (performance + fundamentals + technicals)
 *   performance   — price performance comparison over a period
 *   fundamentals  — fundamental metrics comparison
 *   technical     — technical indicators comparison
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createStockCompareTool(
  _api: OpenClawPluginApi,
  config: QuantTradingConfig,
) {
  return {
    name: "stock_compare",
    description:
      "个股对比工具。支持任意N只股票的多维度横向对比（不限于同行业），包括价格表现、技术指标、基本面。\n" +
      "action=compare 全方位对比（收益率/回撤/波动率/PE/PB/RSI/MACD等），\n" +
      "action=performance 价格涨跌对比（支持1月/3月/6月/1年），\n" +
      "action=fundamentals 基本面对比（PE/PB/ROE/市值/换手率），\n" +
      "action=technical 技术面对比（均线/RSI/MACD/布林带）。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["compare", "performance", "fundamentals", "technical"],
          description:
            "操作: compare=全方位对比, performance=涨跌对比, " +
            "fundamentals=基本面对比, technical=技术面对比",
        },
        symbols: {
          type: "string",
          description:
            "股票代码列表，逗号分隔（2-10只），如 '600519,000858,000333'",
        },
        period: {
          type: "string",
          enum: ["1m", "3m", "6m", "1y", "2y"],
          description: "对比周期 (action=compare/performance, 默认3m)",
        },
      },
      required: ["action", "symbols"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const symbolsStr = args.symbols as string;

      if (!symbolsStr) return JSON.stringify({ error: "需要 symbols 参数 (逗号分隔)" });

      const symbols = symbolsStr
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);

      if (symbols.length < 2) return JSON.stringify({ error: "至少需要2个股票代码" });
      if (symbols.length > 10) return JSON.stringify({ error: "最多支持10个股票同时对比" });

      const pyResult = await runPythonScript<Record<string, unknown>>(
        "engines/stock_compare.py",
        {
          action,
          symbols,
          period: args.period ?? "3m",
        },
        config,
      );

      if (pyResult.ok && pyResult.data) {
        return pyResult.data;
      }

      return {
        action,
        note: "个股对比引擎未就绪。" + (pyResult.error ? ` 错误: ${pyResult.error}` : ""),
      };
    },
  };
}
