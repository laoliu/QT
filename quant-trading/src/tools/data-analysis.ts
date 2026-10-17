/**
 * data_analysis tool — general-purpose financial data analysis.
 *
 * Extends the quant-trading extension with broader analytical capabilities:
 *   - Statistical summaries
 *   - Cross-market comparison
 *   - Custom data exploration (regression, distribution, anomaly detection)
 *   - Market regime classification
 *   - Macro indicator analysis
 */
import type { QuantTradingRuntime } from "../runtime.js";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createDataAnalysisTool(_api: QuantTradingRuntime, config: QuantTradingConfig) {
  return {
    name: "data_analysis",
    description:
      "通用金融数据分析工具。\n" +
      "• action=statistics — 统计摘要（均值/中位数/标准差/偏度/峰度等）\n" +
      "• action=cross_market — 跨市场对比分析（A股vs美股vs港股、不同行业对比）\n" +
      "• action=regression — 回归分析（单因子/多因子回归）\n" +
      "• action=distribution — 收益率分布分析（正态性检验、厚尾分析）\n" +
      "• action=anomaly — 异常值检测（价格/成交量异常）\n" +
      "• action=regime — 市场状态分类（牛市/熊市/震荡市判定）\n" +
      "• action=macro — 宏观指标分析（GDP/CPI/PMI/M2等）\n" +
      "• action=macro_correlation — 宏观指标与股价相关性分析（分析宏观指标变化对个股/行业收益率的影响）\n" +
      "• action=sentiment_backtest — 舆情信号回测（分析热度/情绪变化后的短期收益表现）",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: [
            "statistics",
            "cross_market",
            "regression",
            "distribution",
            "anomaly",
            "regime",
            "macro",
            "macro_correlation",
            "sentiment_backtest",
          ],
          description: "分析类型",
        },
        symbols: {
          type: "string",
          description: "标的代码（逗号分隔，可多个），如 '600519,000858,AAPL'",
        },
        indicator: {
          type: "string",
          description: "宏观指标名称（action=macro时使用），如 'gdp', 'cpi', 'pmi', 'm2'",
        },
        period: {
          type: "number",
          description: "分析周期（天），默认252（一年）",
        },
        factors: {
          type: "string",
          description:
            "回归因子（逗号分隔），action=regression时使用\n" +
            "可选: 'market,size,value,momentum,volatility'",
        },
        startDate: {
          type: "string",
          description: "起始日期 YYYY-MM-DD",
        },
        endDate: {
          type: "string",
          description: "结束日期 YYYY-MM-DD",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const period = (args.period as number) ?? 252;

      const pyResult = await runPythonScript<Record<string, unknown>>(
        "engines/data_analysis_engine.py",
        {
          action,
          symbols: args.symbols,
          indicator: args.indicator,
          period,
          factors: args.factors,
          startDate: args.startDate,
          endDate: args.endDate,
        },
        config,
      );

      if (!pyResult.ok) {
        return { error: pyResult.error ?? "数据分析失败" };
      }

      return pyResult.data;
    },
  };
}
