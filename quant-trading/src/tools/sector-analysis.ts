/**
 * sector_analysis tool — AI-callable for sector/industry analysis.
 *
 * Actions:
 *   ranking — Today's sector ranking (涨跌幅, 成交额, 领涨股)
 *   detail  — Constituent stocks of a specific sector
 *   history — Historical K-line of a sector index
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createSectorAnalysisTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "sector_analysis",
    description:
      "板块/行业分析工具。查看行业板块排名、板块成分股、板块历史走势、板块轮动追踪。\n" +
      "• action=ranking — 今日行业板块涨跌排名（默认前20）\n" +
      "• action=detail — 查看某板块成分股详情（需指定sector）\n" +
      "• action=history — 查看板块历史K线走势（需指定sector）\n" +
      "• action=rotation — 板块轮动分析（多板块多周期动量对比，识别资金轮动方向）",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["ranking", "detail", "history", "rotation"],
          description:
            "操作类型: ranking=板块涨跌排名, detail=板块成分股, history=板块历史走势, rotation=板块轮动分析",
        },
        sector: {
          type: "string",
          description: "板块名称（action=detail/history时必填），如 '半导体', '白酒', '新能源'",
        },
        limit: {
          type: "number",
          description: "返回数量限制，默认20",
        },
        days: {
          type: "number",
          description: "历史天数（仅action=history），默认20",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "ranking";
      const sector = args.sector as string | undefined;
      const limit = (args.limit as number) ?? 20;
      const days = (args.days as number) ?? 20;

      const pyResult = await runPythonScript<{
        action?: string;
        sectors?: Array<Record<string, unknown>>;
        stocks?: Array<Record<string, unknown>>;
        history?: Array<Record<string, unknown>>;
        sector?: string;
        count?: number;
        avgChange?: number;
        upCount?: number;
        downCount?: number;
        stockCount?: number;
        error?: string;
        errors?: string[];
      }>("engines/sector_analysis.py", { action, sector, limit, days }, config);

      if (pyResult.ok && pyResult.data) {
        const data = pyResult.data;
        if (data.error) {
          return { error: data.error, errors: data.errors };
        }

        // Add display hints for large results
        if (action === "ranking" && data.sectors && data.sectors.length > 10) {
          (data as Record<string, unknown>).displayHint =
            "板块数据较多，请分组展示：先展示前10涨幅最大的板块，再展示跌幅最大的板块。";
        }
        if (action === "detail" && data.stocks && data.stocks.length > 10) {
          (data as Record<string, unknown>).displayHint =
            `${data.sector ?? ""}板块共${String(data.stockCount ?? data.stocks.length)}只成分股，请分多条消息展示。`;
        }

        return data;
      }

      return {
        action,
        note: "板块分析引擎未就绪。" + (pyResult.error ? ` 错误: ${pyResult.error}` : ""),
      };
    },
  };
}
