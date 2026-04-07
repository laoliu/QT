/**
 * stock_compare tool — AI-callable for multi-stock comparison.
 *
 * Actions:
 *   price_compare       — Normalized price performance comparison
 *   technical_compare   — Technical indicators side-by-side
 *   fundamental_compare — Key fundamental metrics comparison
 *   comprehensive       — Full comparison (price + technical + fundamental)
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createStockCompareTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "stock_compare",
    description:
      "多股对比工具。对比多只股票的价格走势、技术指标、基本面指标。\n" +
      "• action=price_compare — 区间涨跌幅对比（归一化收益率）\n" +
      "• action=technical_compare — 技术指标对比（MA/MACD/RSI/量比）\n" +
      "• action=fundamental_compare — 基本面对比（PE/PB/市值/涨跌幅）\n" +
      "• action=comprehensive — 综合对比（价格+技术+基本面）",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["price_compare", "technical_compare", "fundamental_compare", "comprehensive"],
          description:
            "操作类型: price_compare=价格对比, technical_compare=技术对比, fundamental_compare=基本面对比, comprehensive=综合对比",
        },
        symbols: {
          type: "string",
          description: '股票代码列表（逗号分隔），如 "600519,000858,002304"',
        },
        days: {
          type: "number",
          description: "对比区间天数（仅价格对比），默认60",
        },
      },
      required: ["action", "symbols"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "comprehensive";
      const symbolsRaw = args.symbols as string;
      const days = (args.days as number) ?? 60;

      if (!symbolsRaw) {
        return { error: "请提供 symbols 参数（逗号分隔的股票代码）" };
      }

      const symbols = symbolsRaw
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      if (symbols.length < 2) {
        return { error: "请至少提供2只股票进行对比" };
      }
      if (symbols.length > 10) {
        return { error: "最多支持10只股票同时对比" };
      }

      const pyResult = await runPythonScript<Record<string, unknown>>(
        "engines/stock_compare.py",
        { action, symbols, days },
        config,
      );

      if (pyResult.ok && pyResult.data) {
        const data = pyResult.data;
        if (data.error) {
          return { error: data.error };
        }
        return data;
      }

      return {
        error: pyResult.error ?? "股票对比数据获取失败",
      };
    },
  };
}
