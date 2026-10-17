/**
 * lianban_analysis tool — AI-callable for 连板梯队/二进三候选 analysis.
 *
 * Actions:
 *   scan        — 连板梯队全景 (最高板、各层级、晋级率、情绪评分)
 *   candidates  — 二进三候选列表 (多因子评分排序)
 *   detail      — 单只股票连板轨迹分析
 */
import type { QuantTradingRuntime } from "../runtime.js";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createLianbanAnalysisTool(_api: QuantTradingRuntime, config: QuantTradingConfig) {
  return {
    name: "lianban_analysis",
    description:
      "连板分析工具。查看连板梯队、二进三候选、单股连板轨迹。\n" +
      "• action=scan — 连板梯队全景（最高板高度、各层级数量、晋级率、市场情绪）\n" +
      "• action=candidates — 二进三候选列表（多因子量化评分排序）\n" +
      "• action=detail — 单只股票的连板轨迹分析（需指定symbol）",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["scan", "candidates", "detail"],
          description: "操作类型: scan=连板梯队, candidates=二进三候选, detail=个股分析",
        },
        symbol: {
          type: "string",
          description: "股票代码（action=detail时必填），如 600519",
        },
        date: {
          type: "string",
          description: "日期（可选），格式 YYYYMMDD。默认今天。",
        },
        min_boards: {
          type: "number",
          description: "最低连板数（action=candidates时可选），默认2",
        },
        limit: {
          type: "number",
          description: "返回数量限制，默认20",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "scan";
      const symbol = args.symbol as string | undefined;
      const date = args.date as string | undefined;
      const minBoards = (args.min_boards as number) ?? 2;
      const limit = (args.limit as number) ?? 20;

      const pyResult = await runPythonScript<Record<string, unknown>>(
        "scripts/lianban_dashboard.py",
        {
          action,
          symbol,
          date,
          min_boards: minBoards,
          limit,
        },
        config,
      );

      if (pyResult.ok && pyResult.data) {
        const data = pyResult.data;
        if (data.error) {
          return { error: data.error };
        }

        // Display hints for large candidate lists
        const candidates = data.candidates as Array<unknown> | undefined;
        if (candidates && candidates.length > 10) {
          (data as Record<string, unknown>).displayHint =
            `共${String(candidates.length)}只候选，请分多条展示（每条5-8只），确保信息完整。`;
        }

        return data;
      }

      return {
        action,
        note: "连板分析引擎未就绪。" + (pyResult.error ? ` 错误: ${pyResult.error}` : ""),
      };
    },
  };
}
