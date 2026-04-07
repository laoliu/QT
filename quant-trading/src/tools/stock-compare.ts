/**
 * stock_compare tool — Horizontally compare multiple stocks across dimensions.
 *
 * Delegates to the Python multi_compare engine for batch data fetching.
 * Supports 2-10 stocks with fundamental, technical, capital flow, and
 * performance comparison in a single call.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createStockCompareTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "stock_compare",
    description:
      "多股横向对比工具。支持2-10只股票一次性横向对比，覆盖4个维度：\n" +
      "1) 基本面：PE/PB/ROE/市值/营收增速\n" +
      "2) 技术面：趋势方向/RSI/MACD状态/布林带位置\n" +
      "3) 资金面：主力净流入/北向持仓变化\n" +
      "4) 近期涨跌幅：1日/5日/20日/60日/年初至今\n" +
      "输出结构化表格，并给出各维度最优排名摘要。\n" +
      "用法：传入 codes（JSON数组格式的股票代码列表），即可获得完整对比报告。\n" +
      "示例：codes=[\"600519\",\"000858\",\"002304\"]",
    parameters: {
      type: "object" as const,
      properties: {
        codes: {
          type: "string",
          description:
            '股票代码列表(JSON数组)，2-10只，例如: ["600519","000858","002304"]',
        },
        market: {
          type: "string",
          enum: ["cn_stock", "us_stock", "hk_stock"],
          description: "市场类型，默认cn_stock",
        },
      },
      required: ["codes"],
    },
    async execute(args: Record<string, unknown>) {
      // Parse codes
      let codes: string[] = [];
      if (args.codes) {
        try {
          const parsed = JSON.parse(args.codes as string);
          if (Array.isArray(parsed)) {
            codes = parsed.map(String);
          } else {
            return { error: "codes 格式错误，请使用 JSON 数组格式，例如 [\"600519\",\"000858\"]" };
          }
        } catch {
          // Try comma-separated fallback
          const raw = (args.codes as string).replace(/[[\]"']/g, "");
          codes = raw
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean);
        }
      }

      if (codes.length < 2) {
        return { error: "请提供至少2只股票代码进行对比" };
      }
      if (codes.length > 10) {
        return { error: "最多支持10只股票横向对比，当前传入了 " + codes.length + " 只" };
      }

      const market = (args.market as string) ?? "cn_stock";

      const pyResult = await runPythonScript<{
        stockCount?: number;
        stocks?: Array<Record<string, unknown>>;
        fundamentals?: Array<Record<string, unknown>>;
        technicals?: Array<Record<string, unknown>>;
        capitalFlow?: Array<Record<string, unknown>>;
        performance?: Array<Record<string, unknown>>;
        summary?: Record<string, unknown>;
        errors?: string[];
        error?: string;
      }>(
        "engines/multi_compare.py",
        { codes, market },
        config,
      );

      if (pyResult.ok && pyResult.data) {
        const data = pyResult.data;
        if (data.error) {
          return { error: data.error };
        }
        // Add display hint for large result sets
        (data as Record<string, unknown>).displayHint =
          `横向对比${codes.length}只股票，请用表格形式分维度展示（基本面、技术面、资金面、涨跌幅各一个表格），最后附上排名摘要。`;
        return data;
      }

      return {
        error:
          "多股对比引擎未就绪。" +
          (pyResult.error ? ` 错误: ${pyResult.error}` : "") +
          " 请确保 Python 已安装并配置 pythonPath。",
      };
    },
  };
}
