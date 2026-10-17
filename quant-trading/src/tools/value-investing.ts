/**
 * value_investing tool — US/China value investing framework.
 *
 * Implements Graham/Buffett-style fundamental analysis:
 *   - Intrinsic value estimation (DCF, Graham Number, EPV)
 *   - Moat assessment (ROE stability, gross margin trend, revenue growth)
 *   - Margin of safety calculation
 *   - Quality scoring (Piotroski F-Score, Altman Z-Score)
 *   - Comprehensive value report combining all metrics
 */
import type { QuantTradingRuntime } from "../runtime.js";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createValueInvestingTool(_api: QuantTradingRuntime, config: QuantTradingConfig) {
  return {
    name: "value_investing",
    description:
      "价值投资分析框架（Graham/Buffett风格）。\n" +
      "• action=intrinsic_value — 内在价值估算（DCF折现现金流、格雷厄姆数、EPV盈利能力价值）\n" +
      "• action=moat — 护城河评估（ROE稳定性、毛利率趋势、竞争优势分析）\n" +
      "• action=quality_score — 财务质量评分（Piotroski F-Score, Altman Z-Score）\n" +
      "• action=margin_of_safety — 安全边际计算（当前价 vs 内在价值）\n" +
      "• action=comprehensive — 综合价值投资报告（汇总所有指标）",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["intrinsic_value", "moat", "quality_score", "margin_of_safety", "comprehensive"],
          description: "分析类型",
        },
        symbol: {
          type: "string",
          description: "标的代码，如 600519、AAPL",
        },
        discountRate: {
          type: "number",
          description: "折现率（DCF用），默认0.10（10%）",
        },
        growthRate: {
          type: "number",
          description: "预期增长率（DCF用），默认0.05（5%）",
        },
        terminalGrowthRate: {
          type: "number",
          description: "永续增长率（DCF用），默认0.03（3%）",
        },
        years: {
          type: "number",
          description: "DCF预测年数，默认10",
        },
      },
      required: ["action", "symbol"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const symbol = args.symbol as string;
      const discountRate = (args.discountRate as number) ?? 0.1;
      const growthRate = (args.growthRate as number) ?? 0.05;
      const terminalGrowthRate = (args.terminalGrowthRate as number) ?? 0.03;
      const years = (args.years as number) ?? 10;

      const pyResult = await runPythonScript<Record<string, unknown>>(
        "engines/value_investing_engine.py",
        { action, symbol, discountRate, growthRate, terminalGrowthRate, years },
        config,
      );

      if (!pyResult.ok) {
        return { error: pyResult.error ?? "价值投资分析失败" };
      }

      return pyResult.data;
    },
  };
}
