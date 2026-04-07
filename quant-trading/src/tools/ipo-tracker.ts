/**
 * ipo_tracker tool — AI-callable for IPO and new stock listing tracking.
 *
 * Actions:
 *   upcoming     — Upcoming IPO subscription dates (打新日历)
 *   recent       — Recently listed new stocks (次新股)
 *   performance  — IPO performance tracking (新股表现)
 *   subscription — Detailed subscription information
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createIpoTrackerTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "ipo_tracker",
    description:
      "新股追踪工具。查看即将申购的新股、近期上市次新股表现、打新信息。\n" +
      "• action=upcoming — 即将/近期申购的新股（打新日历）\n" +
      "• action=recent — 近N日上市的次新股（可指定days，默认30）\n" +
      "• action=performance — 次新股上市以来表现（收益率统计）\n" +
      "• action=subscription — 新股申购详细信息（代码、价格、中签率等）",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["upcoming", "recent", "performance", "subscription"],
          description:
            "操作类型: upcoming=打新日历, recent=次新股, performance=表现统计, subscription=申购信息",
        },
        days: {
          type: "number",
          description: "查询天数范围（recent/performance使用），默认30/60",
        },
        limit: {
          type: "number",
          description: "返回数量限制，默认20",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "upcoming";
      const days = args.days as number | undefined;
      const limit = (args.limit as number) ?? 20;

      const pyResult = await runPythonScript<{
        action?: string;
        ipos?: Array<Record<string, unknown>>;
        stocks?: Array<Record<string, unknown>>;
        summary?: Record<string, unknown>;
        count?: number;
        error?: string;
        errors?: string[];
      }>("engines/ipo_tracker.py", { action, days, limit }, config);

      if (pyResult.ok && pyResult.data) {
        const data = pyResult.data;
        if (data.error) {
          return { error: data.error, errors: data.errors };
        }

        const count = data.count ?? 0;
        if (count > 5) {
          (data as Record<string, unknown>).displayHint =
            `共${String(count)}只新股/次新股。请按时间排序展示，标注关键信息（发行价、中签率、涨幅）。`;
        }

        return data;
      }

      return {
        action,
        note: "新股追踪引擎未就绪。" + (pyResult.error ? ` 错误: ${pyResult.error}` : ""),
      };
    },
  };
}
