/**
 * market_regime tool — AI-callable for A-share market-wide sentiment & regime analysis.
 *
 * Actions:
 *   dashboard   — Full market sentiment dashboard (limit-up ladder, sentiment score, etc.)
 *   fear_greed  — Fear/Greed index based on multiple market signals
 *   breadth     — Market breadth indicators (advance/decline, new high/low, etc.)
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createMarketRegimeTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "market_regime",
    description:
      "市场情绪温度计与市场环境分析工具。从涨停梯队、炸板率、涨跌比、北向资金等多维度综合判断市场情绪与趋势状态。\n" +
      "• action=dashboard — 全市场情绪仪表盘（涨停梯队+情绪评分+晋级率+炸板率）\n" +
      "• action=fear_greed — 恐贪指数（基于波动率、资金流、涨跌比等多信号合成）\n" +
      "• action=breadth — 市场宽度指标（涨跌家数、涨停跌停数、新高新低数）",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["dashboard", "fear_greed", "breadth"],
          description: "操作类型: dashboard=情绪仪表盘, fear_greed=恐贪指数, breadth=市场宽度",
        },
        date: {
          type: "string",
          description: "日期，YYYY-MM-DD（默认最新交易日）",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "dashboard";

      if (action === "dashboard" || action === "breadth") {
        const pyResult = await runPythonScript<Record<string, unknown>>(
          "engines/market_sentiment.py",
          {
            action: "sentiment_dashboard",
            date: args.date ?? undefined,
          },
          config,
        );

        if (!pyResult.ok) {
          return { error: pyResult.error ?? "市场情绪数据获取失败" };
        }

        const data = pyResult.data ?? {};

        if (action === "breadth") {
          return {
            action: "breadth",
            zt_count: data.zt_count ?? 0,
            dt_count: data.dt_count ?? 0,
            zt_dt_ratio: data.zt_dt_ratio ?? null,
            board_failure_rate: data.zbgc_rate ?? null,
            promotion_rates: data.promotion_rates ?? {},
            highest_board: data.highest_board ?? 0,
            summary: data.summary ?? "无数据",
          };
        }

        return {
          action: "dashboard",
          ...data,
        };
      }

      if (action === "fear_greed") {
        // Composite fear/greed from multiple sources
        const [sentimentResult, fundFlowResult] = await Promise.allSettled([
          runPythonScript<Record<string, unknown>>(
            "engines/market_sentiment.py",
            { action: "sentiment_dashboard", date: args.date ?? undefined },
            config,
          ),
          runPythonScript<Record<string, unknown>>(
            "engines/fund_flow.py",
            { action: "rank", limit: 5 },
            config,
          ),
        ]);

        const sentiment =
          sentimentResult.status === "fulfilled" && sentimentResult.value.ok
            ? sentimentResult.value.data
            : null;
        const fundFlow =
          fundFlowResult.status === "fulfilled" && fundFlowResult.value.ok
            ? fundFlowResult.value.data
            : null;

        const sentimentScore =
          typeof (sentiment as Record<string, unknown> | null)?.composite_score === "number"
            ? ((sentiment as Record<string, number>).composite_score as number)
            : 50;

        // Map 0-100 sentiment score to fear/greed zones
        let zone = "neutral";
        if (sentimentScore >= 80) zone = "extreme_greed";
        else if (sentimentScore >= 60) zone = "greed";
        else if (sentimentScore <= 20) zone = "extreme_fear";
        else if (sentimentScore <= 40) zone = "fear";

        return {
          action: "fear_greed",
          score: sentimentScore,
          zone,
          zone_cn:
            zone === "extreme_greed"
              ? "极度贪婪"
              : zone === "greed"
                ? "贪婪"
                : zone === "fear"
                  ? "恐惧"
                  : zone === "extreme_fear"
                    ? "极度恐惧"
                    : "中性",
          components: {
            board_sentiment: sentiment ?? { note: "数据不可用" },
            fund_flow_top5: fundFlow ?? { note: "数据不可用" },
          },
          interpretation:
            sentimentScore >= 80
              ? "市场极度贪婪，短线谨防回调，控制仓位"
              : sentimentScore >= 60
                ? "市场情绪偏热，适合趋势跟随，注意止盈"
                : sentimentScore <= 20
                  ? "市场极度恐惧，可能超跌反弹机会，但需确认底部信号"
                  : sentimentScore <= 40
                    ? "市场情绪偏冷，适合低吸优质标的"
                    : "市场情绪中性，按策略正常操作",
        };
      }

      return { error: `未知操作: ${action}` };
    },
  };
}
