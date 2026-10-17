/**
 * data_cache tool — AI-callable tool for managing the local stock data cache.
 *
 * Provides cache statistics, clearing, and cleanup operations.
 * Cache is stored in SQLite at ~/data/cache/stock_data.db
 */
import type { QuantTradingRuntime } from "../runtime.js";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createDataCacheTool(_api: QuantTradingRuntime, config: QuantTradingConfig) {
  return {
    name: "data_cache",
    description:
      "管理本地股票数据缓存。\n" +
      "action=stats 查看缓存统计（大小、条目数、最后更新时间），\n" +
      "action=clear 清除缓存（可指定类型：stock_list/fundamentals/klines/code_name），\n" +
      "action=cleanup 清理N天前的旧缓存数据。\n" +
      "数据会自动缓存到本地 SQLite，后续查询优先使用缓存，减少网络请求。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["stats", "clear", "cleanup"],
          description: "操作类型: stats=查看统计, clear=清除缓存, cleanup=清理旧数据",
        },
        table: {
          type: "string",
          enum: ["stock_list", "stock_fundamentals", "kline_data", "stock_code_name"],
          description: "要清除的缓存类型（仅 action=clear 时使用，不指定则清除全部）",
        },
        days: {
          type: "number",
          description: "清理多少天前的旧缓存（仅 action=cleanup，默认 30）",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "stats";

      const pyResult = await runPythonScript<{
        db_path?: string;
        db_size_mb?: number;
        stock_list?: { unique_stocks: number; cached_dates: number };
        fundamentals?: { unique_stocks: number };
        klines?: { total_bars: number; unique_symbols: number };
        code_name_map?: { entries: number };
        status?: string;
        cleared?: string;
        error?: string;
      }>(
        "cache/stock_cache.py",
        {
          action,
          table: args.table,
          days: args.days ?? 30,
        },
        config,
      );

      if (pyResult.ok && pyResult.data) {
        return pyResult.data;
      }

      return {
        error: "缓存操作失败" + (pyResult.error ? `: ${pyResult.error}` : ""),
      };
    },
  };
}
