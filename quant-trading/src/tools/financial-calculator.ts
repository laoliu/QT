/**
 * financial_calculator tool — stock valuation models & financial calculations.
 *
 * Actions:
 *   - dcf: Discounted Cash Flow valuation
 *   - ddm: Dividend Discount Model
 *   - pe_valuation: PE-based relative valuation
 *   - pb_valuation: PB-based relative valuation
 *   - ps_valuation: PS-based relative valuation
 *   - ev_ebitda: EV/EBITDA valuation
 *   - peg: PEG ratio analysis
 *   - wacc: Weighted Average Cost of Capital
 *   - compare: Multi-model comparison for a single stock
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createFinancialCalculatorTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "financial_calculator",
    description:
      "金融估值计算器（美股/A股指值模型）。\n" +
      "• action=dcf — DCF折现现金流估值\n" +
      "• action=ddm — 股利折现模型估值\n" +
      "• action=pe_valuation — PE相对估值（对比行业/历史PE）\n" +
      "• action=pb_valuation — PB相对估值\n" +
      "• action=ps_valuation — PS相对估值\n" +
      "• action=ev_ebitda — EV/EBITDA企业价值倍数估值\n" +
      "• action=peg — PEG估值分析\n" +
      "• action=wacc — WACC加权平均资本成本计算\n" +
      "• action=compare — 多模型估值对比（汇总DCF/PE/PB/PEG等）",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: [
            "dcf",
            "ddm",
            "pe_valuation",
            "pb_valuation",
            "ps_valuation",
            "ev_ebitda",
            "peg",
            "wacc",
            "compare",
          ],
          description: "估值类型",
        },
        symbol: {
          type: "string",
          description: "标的代码，如 600519、AAPL、00700.HK",
        },
        // DCF params
        freeCashFlow: {
          type: "number",
          description: "自由现金流（DCF用，不填则自动获取）",
        },
        growthRate: {
          type: "number",
          description: "预期增长率，默认0.08（8%）",
        },
        discountRate: {
          type: "number",
          description: "折现率/WACC，默认0.10（10%）",
        },
        terminalGrowthRate: {
          type: "number",
          description: "永续增长率，默认0.03（3%）",
        },
        years: {
          type: "number",
          description: "预测年数，默认10",
        },
        // DDM params
        dividend: {
          type: "number",
          description: "当前年度股利（DDM用，不填则自动获取）",
        },
        dividendGrowthRate: {
          type: "number",
          description: "股利增长率（DDM用），默认0.05",
        },
        // Relative valuation params
        peerSymbols: {
          type: "string",
          description: "可比公司代码（逗号分隔），用于相对估值对比",
        },
        // WACC params
        debtRatio: {
          type: "number",
          description: "负债比率（WACC用）",
        },
        costOfDebt: {
          type: "number",
          description: "债务成本率（WACC用），默认0.05",
        },
        taxRate: {
          type: "number",
          description: "税率（WACC用），默认0.25",
        },
        beta: {
          type: "number",
          description: "Beta系数（WACC用，不填则自动计算）",
        },
        riskFreeRate: {
          type: "number",
          description: "无风险利率，默认0.03（3%）",
        },
        marketReturn: {
          type: "number",
          description: "市场预期回报率，默认0.09（9%）",
        },
      },
      required: ["action", "symbol"],
    },
    async execute(args: Record<string, unknown>) {
      const pyResult = await runPythonScript<Record<string, unknown>>(
        "engines/financial_calculator_engine.py",
        {
          action: args.action,
          symbol: args.symbol,
          freeCashFlow: args.freeCashFlow,
          growthRate: (args.growthRate as number) ?? 0.08,
          discountRate: (args.discountRate as number) ?? 0.1,
          terminalGrowthRate: (args.terminalGrowthRate as number) ?? 0.03,
          years: (args.years as number) ?? 10,
          dividend: args.dividend,
          dividendGrowthRate: (args.dividendGrowthRate as number) ?? 0.05,
          peerSymbols: args.peerSymbols,
          debtRatio: args.debtRatio,
          costOfDebt: (args.costOfDebt as number) ?? 0.05,
          taxRate: (args.taxRate as number) ?? 0.25,
          beta: args.beta,
          riskFreeRate: (args.riskFreeRate as number) ?? 0.03,
          marketReturn: (args.marketReturn as number) ?? 0.09,
        },
        config,
      );

      if (!pyResult.ok) {
        return { error: pyResult.error ?? "估值计算失败" };
      }

      return pyResult.data;
    },
  };
}
