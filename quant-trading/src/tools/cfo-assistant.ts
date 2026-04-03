/**
 * cfo_assistant tool — Chief Financial Officer analysis assistant.
 *
 * Provides CFO-level financial statement analysis:
 *   - Three-statement analysis (income, balance sheet, cash flow)
 *   - DuPont decomposition
 *   - Financial ratio analysis (liquidity, solvency, profitability, efficiency)
 *   - Peer comparison
 *   - Trend analysis (multi-year financial health tracking)
 *   - Red flag detection (accounting anomalies, earnings quality)
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createCfoAssistantTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "cfo_assistant",
    description:
      "首席财务官助手（CFO级财报分析工具）。\n" +
      "• action=three_statements — 三大报表摘要（利润表、资产负债表、现金流量表）\n" +
      "• action=dupont — 杜邦分析（ROE拆解：利润率×周转率×杠杆）\n" +
      "• action=ratios — 财务比率分析（流动性/偿债能力/盈利能力/运营效率）\n" +
      "• action=peer_compare — 同行对比分析（vs可比公司）\n" +
      "• action=trend — 多年财务趋势分析（3-5年健康度跟踪）\n" +
      "• action=red_flags — 财务风险预警（会计异常、盈余质量检测）\n" +
      "• action=comprehensive — 综合CFO级分析报告",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: [
            "three_statements",
            "dupont",
            "ratios",
            "peer_compare",
            "trend",
            "red_flags",
            "comprehensive",
          ],
          description: "分析类型",
        },
        symbol: {
          type: "string",
          description: "标的代码，如 600519、AAPL",
        },
        peerSymbols: {
          type: "string",
          description: "可比公司代码（逗号分隔），peer_compare时使用",
        },
        years: {
          type: "number",
          description: "分析年数，默认3",
        },
        period: {
          type: "string",
          enum: ["annual", "quarterly"],
          description: "报表周期，默认annual",
        },
      },
      required: ["action", "symbol"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const symbol = args.symbol as string;
      const years = (args.years as number) ?? 3;
      const period = (args.period as string) ?? "annual";

      const pyResult = await runPythonScript<Record<string, unknown>>(
        "engines/cfo_engine.py",
        {
          action,
          symbol,
          peerSymbols: args.peerSymbols,
          years,
          period,
        },
        config,
      );

      if (!pyResult.ok) {
        return { error: pyResult.error ?? "财务分析失败" };
      }

      return pyResult.data;
    },
  };
}
