/**
 * sector_synergy tool — AI-callable for 板块协同追涨 analysis.
 *
 * Actions:
 *   scan     — 板块协同概览（各板块涨停数、热门板块排名）
 *   hot      — 热门板块详情（涨停数≥N的板块及成分股）
 *   analyze  — 个股板块协同分析（检查是否满足板块协同条件）
 *   config   — 策略参数说明（6个买入条件 + 3个卖出规则）
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createSectorSynergyTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "sector_synergy",
    description:
      "板块协同追涨分析工具。查看板块涨停协同状态,分析个股是否具备板块协同追涨条件。\n" +
      "• action=scan — 板块协同概览: 各板块涨停数量、热门板块排名、市场情绪\n" +
      "• action=hot — 热门板块详情: 涨停数≥N的板块及其涨停股列表\n" +
      "• action=analyze — 个股分析: 指定股票是否满足板块协同+涨幅+换手率条件\n" +
      "• action=config — 策略参数说明: 6个买入条件和3个卖出规则的完整配置",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["scan", "hot", "analyze", "config"],
          description:
            "操作类型: scan=板块协同概览, hot=热门板块详情, analyze=个股分析, config=策略说明",
        },
        symbols: {
          type: "string",
          description: "股票代码,逗号分隔（action=analyze时必填），如 002378,600519",
        },
        date: {
          type: "string",
          description: "日期（可选）,格式 YYYYMMDD。默认今天。",
        },
        min_zt: {
          type: "number",
          description: "热门板块最低涨停数（action=hot时可选），默认2",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "scan";
      const symbols = args.symbols as string | undefined;
      const date = args.date as string | undefined;
      const minZt = (args.min_zt as number) ?? 2;

      const pyResult = await runPythonScript<Record<string, unknown>>(
        "scripts/sector_synergy.py",
        {
          action,
          symbols: symbols ?? "",
          date,
          min_zt: minZt,
        },
        config,
      );

      if (pyResult.ok && pyResult.data) {
        const data = pyResult.data;
        if (data.error) {
          return { error: data.error };
        }

        // Display hints for scan results
        const hotSectors = data.hot_sectors as Array<unknown> | undefined;
        if (hotSectors && hotSectors.length > 5) {
          (data as Record<string, unknown>).displayHint =
            `共${String(hotSectors.length)}个热门板块,请重点展示涨停数最多的前5个板块。`;
        }

        return data;
      }

      return {
        action,
        note: "板块协同分析引擎未就绪。" + (pyResult.error ? ` 错误: ${pyResult.error}` : ""),
      };
    },
  };
}
