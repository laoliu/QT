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
 *   - tax_calculation: Capital gains & dividend tax calculation
 */
import type { QuantTradingRuntime } from "../runtime.js";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { TradeJournal } from "../services/trade-journal.js";
import type { QuantTradingConfig } from "../types.js";

export function createFinancialCalculatorTool(
  _api: QuantTradingRuntime,
  config: QuantTradingConfig,
  journal?: TradeJournal,
) {
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
      "• action=compare — 多模型估值对比（汇总DCF/PE/PB/PEG等）\n" +
      "• action=tax_calculation — 资本利得税务计算（股息红利税、港股通红利税、大宗交易税费、年度税务摘要）",
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
            "tax_calculation",
          ],
          description: "估值/计算类型",
        },
        symbol: {
          type: "string",
          description: "标的代码，如 600519、AAPL、00700.HK（tax_calculation时可选，不填则计算全部）",
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
        // Tax calculation params
        tax_type: {
          type: "string",
          enum: ["dividend_tax", "hk_connect_dividend", "block_trade", "annual_summary"],
          description:
            "税务计算类型：dividend_tax=股息红利税（持股期限差异化税率）, " +
            "hk_connect_dividend=港股通红利税（20%）, " +
            "block_trade=大宗交易税费, " +
            "annual_summary=年度税务摘要（自动从trade_journal提取）",
        },
        tax_year: {
          type: "number",
          description: "税务年度（如 2025），默认当前年份",
        },
        holding_months: {
          type: "number",
          description: "持股月数（股息红利税用）：≤1个月按20%, 1-12个月按10%, >12个月免征",
        },
        dividend_amount: {
          type: "number",
          description: "股息红利金额（元）",
        },
        trade_amount: {
          type: "number",
          description: "交易金额（大宗交易税费计算用）",
        },
        is_institutional: {
          type: "boolean",
          description: "是否为机构投资者（影响税率），默认false",
        },
        trade_records: {
          type: "array",
          items: {
            type: "object",
            properties: {
              symbol: { type: "string" },
              direction: { type: "string" },
              entryPrice: { type: "number" },
              exitPrice: { type: "number" },
              quantity: { type: "number" },
              pnl: { type: "number" },
              date: { type: "string" },
              market: { type: "string" },
              holdingMonths: { type: "number" },
              dividendAmount: { type: "number" },
            },
          },
          description: "手动传入交易记录（不传则自动从trade_journal提取）",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      // For tax_calculation, gather trade records from journal if not provided
      if (args.action === "tax_calculation") {
        let tradeRecords = args.trade_records as Array<Record<string, unknown>> | undefined;

        if (!tradeRecords && journal) {
          const taxYear = (args.tax_year as number) ?? new Date().getFullYear();
          const startDate = `${taxYear}-01-01`;
          const endDate = `${taxYear}-12-31`;
          const notes = journal.getByDateRange(startDate, endDate);
          tradeRecords = notes
            .filter((n) => n.direction && (n.entryPrice || n.exitPrice || n.pnl != null))
            .map((n) => ({
              symbol: n.symbol,
              direction: n.direction,
              entryPrice: n.entryPrice,
              exitPrice: n.exitPrice,
              pnl: n.pnl,
              date: n.date,
            }));
        }

        const pyResult = await runPythonScript<Record<string, unknown>>(
          "engines/financial_calculator_engine.py",
          {
            action: "tax_calculation",
            symbol: args.symbol ?? "",
            tax_type: args.tax_type ?? "annual_summary",
            tax_year: (args.tax_year as number) ?? new Date().getFullYear(),
            holding_months: args.holding_months,
            dividend_amount: args.dividend_amount,
            trade_amount: args.trade_amount,
            is_institutional: args.is_institutional ?? false,
            trade_records: tradeRecords ?? [],
          },
          config,
        );

        if (!pyResult.ok) {
          return { error: pyResult.error ?? "税务计算失败" };
        }
        return pyResult.data;
      }

      // Original valuation actions require symbol
      if (!args.symbol) {
        return { error: "请提供 symbol 参数" };
      }

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
