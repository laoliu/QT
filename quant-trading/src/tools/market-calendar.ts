/**
 * market_calendar tool — trading calendar integrating market holidays,
 * ex-dividend dates, IPO dates, index rebalancing, and key disclosure deadlines.
 *
 * Use case: trader needs to know upcoming market events that affect trading,
 * including rest days, dividend dates, lock-up expiry, IPO listing dates, etc.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createMarketCalendarTool(api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "market_calendar",
    description:
      "交易日历工具 — 整合交易休市日、除权除息日、IPO申购/上市日、限售解禁日、指数调整日。\n" +
      "action=trading_days 交易日查询(是否交易日/下N个交易日), " +
      "action=holidays 休市日历, " +
      "action=events 近期市场事件(分红/IPO/解禁/指数调整), " +
      "action=weekly 本周交易日历概览。\n" +
      "帮助交易者提前规划交易节奏和资金安排。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["trading_days", "holidays", "events", "weekly"],
          description:
            "操作: trading_days=交易日查询, holidays=休市日历, events=市场事件, weekly=周概览",
        },
        date: {
          type: "string",
          description: "查询日期 YYYY-MM-DD，默认今天",
        },
        days: {
          type: "number",
          description: "未来天数（events/holidays），默认 30",
        },
        event_types: {
          type: "string",
          description:
            "事件类型过滤，逗号分隔: dividend(分红), ipo(IPO), lockup(解禁), index(指数调整)。默认全部",
        },
        symbol: {
          type: "string",
          description: "股票代码（查询特定股票的相关事件）",
        },
        count: {
          type: "number",
          description: "查询未来N个交易日（trading_days action），默认5",
        },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const date = (args.date as string) ?? new Date().toISOString().slice(0, 10);
      const days = (args.days as number) ?? 30;
      const eventTypes = args.event_types as string | undefined;
      const symbol = args.symbol as string | undefined;
      const count = (args.count as number) ?? 5;

      try {
        switch (action) {
          case "trading_days":
            return await tradingDays(date, count);
          case "holidays":
            return await holidays(date, days);
          case "events":
            return await marketEvents(date, days, eventTypes, symbol);
          case "weekly":
            return await weeklyOverview(date);
          default:
            return { error: `未知操作: ${action}` };
        }
      } catch (err) {
        return { error: `日历查询失败: ${err instanceof Error ? err.message : String(err)}` };
      }
    },
  };

  async function tradingDays(date: string, count: number) {
    const result = await runPythonScript(
      "engines/market_calendar_engine.py",
      { action: "trading_days", date, count, market: "cn" },
      config,
    );
    const data = result as { ok: boolean; data?: unknown; error?: string };
    if (!data.ok) return { error: data.error ?? "获取交易日失败" };
    return { action: "trading_days", date, count, ...(data.data as object) };
  }

  async function holidays(date: string, days: number) {
    const result = await runPythonScript(
      "engines/market_calendar_engine.py",
      { action: "holidays", date, days, market: "cn" },
      config,
    );
    const data = result as { ok: boolean; data?: unknown; error?: string };
    if (!data.ok) return { error: data.error ?? "获取休市日历失败" };
    return { action: "holidays", date, days, ...(data.data as object) };
  }

  async function marketEvents(
    date: string,
    days: number,
    eventTypes?: string,
    symbol?: string,
  ) {
    const result = await runPythonScript(
      "engines/market_calendar_engine.py",
      {
        action: "events",
        date,
        days,
        event_types: eventTypes ?? "all",
        symbol: symbol ?? "",
        market: "cn",
      },
      config,
    );
    const data = result as { ok: boolean; data?: unknown; error?: string };
    if (!data.ok) return { error: data.error ?? "获取市场事件失败" };
    return { action: "events", date, days, ...(data.data as object) };
  }

  async function weeklyOverview(date: string) {
    const result = await runPythonScript(
      "engines/market_calendar_engine.py",
      { action: "weekly", date, market: "cn" },
      config,
    );
    const data = result as { ok: boolean; data?: unknown; error?: string };
    if (!data.ok) return { error: data.error ?? "获取周概览失败" };
    return { action: "weekly", date, ...(data.data as object) };
  }
}
