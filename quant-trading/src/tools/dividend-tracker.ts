/**
 * dividend_tracker tool — track dividend history, upcoming ex-dividend dates,
 * and dividend yield analysis for income-oriented stock trading.
 *
 * Use case: trader wants to know dividend history, ex-div dates, and compare
 * dividend yields across stocks.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createDividendTrackerTool(api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "dividend_tracker",
    description:
      "分红/股息追踪工具 — 查看股票分红历史、除权除息日、股息率分析。\n" +
      "action=history 分红历史, action=upcoming 即将除权的股票, " +
      "action=yield_rank 高股息率排行, action=analysis 个股分红分析。\n" +
      "适合关注分红收益和高股息策略的投资者。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["history", "upcoming", "yield_rank", "analysis"],
          description:
            "操作: history=分红历史, upcoming=即将除权日, yield_rank=高股息排行, analysis=个股分红分析",
        },
        symbol: {
          type: "string",
          description: "股票代码（history/analysis 时使用）",
        },
        years: {
          type: "number",
          description: "查询年数（history），默认5年",
        },
        universe: {
          type: "string",
          enum: ["sz50", "hs300", "zz500", "all"],
          description: "股票池（yield_rank），默认 hs300",
        },
        limit: {
          type: "number",
          description: "返回数量，默认 20",
        },
        days: {
          type: "number",
          description: "未来天数（upcoming），默认 30",
        },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const symbol = args.symbol as string;
      const years = (args.years as number) ?? 5;
      const universe = (args.universe as string) ?? "hs300";
      const limit = (args.limit as number) ?? 20;
      const days = (args.days as number) ?? 30;

      try {
        switch (action) {
          case "history": {
            if (!symbol) return { error: "请提供 symbol" };
            return await dividendHistory(symbol, years);
          }
          case "upcoming":
            return await upcomingDividends(days, limit);
          case "yield_rank":
            return await yieldRank(universe, limit);
          case "analysis": {
            if (!symbol) return { error: "请提供 symbol" };
            return await dividendAnalysis(symbol, years);
          }
          default:
            return { error: `未知操作: ${action}` };
        }
      } catch (err) {
        return { error: `分红查询失败: ${err instanceof Error ? err.message : String(err)}` };
      }
    },
  };

  /** Fetch dividend history for a stock */
  async function dividendHistory(symbol: string, years: number) {
    const result = await runPythonScript(
      "engines/dividend_engine.py",
      { action: "history", symbol, years, market: "cn" },
      config,
    );
    const data = result as { ok: boolean; data?: unknown; error?: string };
    if (!data.ok) return { error: data.error ?? "获取分红历史失败" };
    return { action: "history", symbol, years, ...(data.data as object) };
  }

  /** Upcoming ex-dividend dates */
  async function upcomingDividends(days: number, limit: number) {
    const result = await runPythonScript(
      "engines/dividend_engine.py",
      { action: "upcoming", days, limit, market: "cn" },
      config,
    );
    const data = result as { ok: boolean; data?: unknown; error?: string };
    if (!data.ok) return { error: data.error ?? "获取除权日历失败" };
    return { action: "upcoming", days, ...(data.data as object) };
  }

  /** Rank by dividend yield */
  async function yieldRank(universe: string, limit: number) {
    const result = await runPythonScript(
      "engines/dividend_engine.py",
      { action: "yield_rank", universe, limit, market: "cn" },
      config,
    );
    const data = result as { ok: boolean; data?: unknown; error?: string };
    if (!data.ok) return { error: data.error ?? "获取股息率排行失败" };
    return { action: "yield_rank", universe, ...(data.data as object) };
  }

  /** Comprehensive dividend analysis for a single stock */
  async function dividendAnalysis(symbol: string, years: number) {
    const result = await runPythonScript(
      "engines/dividend_engine.py",
      { action: "analysis", symbol, years, market: "cn" },
      config,
    );
    const data = result as { ok: boolean; data?: unknown; error?: string };
    if (!data.ok) return { error: data.error ?? "分红分析失败" };
    return { action: "analysis", symbol, years, ...(data.data as object) };
  }
}
