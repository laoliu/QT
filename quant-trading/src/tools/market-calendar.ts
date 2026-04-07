/**
 * market_calendar tool — AI-callable for querying market trading calendars.
 *
 * Actions:
 *   is_trading_day  — Check if a date is a trading day
 *   next_trading    — Get the next trading day
 *   prev_trading    — Get the previous trading day
 *   trading_days    — List trading days in a range
 *   count_days      — Count trading days in a range
 *   holidays        — Get holiday schedule
 *   upcoming        — Get upcoming holidays
 *   trading_hours   — Get market trading hours
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { MarketCalendar, CalendarMarket } from "../services/market-calendar.js";

export function createMarketCalendarTool(_api: OpenClawPluginApi, calendar: MarketCalendar) {
  return {
    name: "market_calendar",
    description:
      "交易日历工具。查询交易日、节假日、交易时间。支持A股(cn)、港股(hk)、美股(us)。\n" +
      "• action=is_trading_day — 查询某日是否为交易日\n" +
      "• action=next_trading — 查询下一个交易日\n" +
      "• action=prev_trading — 查询上一个交易日\n" +
      "• action=trading_days — 列出日期范围内的交易日\n" +
      "• action=count_days — 统计日期范围内的交易日天数\n" +
      "• action=holidays — 查询节假日安排\n" +
      "• action=upcoming — 查询即将到来的假期\n" +
      "• action=trading_hours — 查询市场交易时间",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: [
            "is_trading_day",
            "next_trading",
            "prev_trading",
            "trading_days",
            "count_days",
            "holidays",
            "upcoming",
            "trading_hours",
          ],
          description: "操作类型",
        },
        date: { type: "string", description: "日期 YYYY-MM-DD (默认今天)" },
        from_date: { type: "string", description: "起始日期 (trading_days/count_days)" },
        to_date: { type: "string", description: "截止日期 (trading_days/count_days)" },
        market: {
          type: "string",
          enum: ["cn", "hk", "us"],
          description: "市场: cn=A股(默认), hk=港股, us=美股",
        },
        year: { type: "number", description: "年份 (holidays)" },
        limit: { type: "number", description: "返回数量 (upcoming)" },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const market = (args.market as CalendarMarket) ?? "cn";
      const today = new Date().toISOString().slice(0, 10);

      switch (action) {
        case "is_trading_day": {
          const date = (args.date as string) ?? today;
          const isTradingDay = calendar.isTradingDay(date, market);
          const isHalfDay = calendar.isHalfDay(date, market);
          return {
            date,
            market,
            isTradingDay,
            isHalfDay,
            ...(isTradingDay
              ? {}
              : { nextTradingDay: calendar.nextTradingDay(date, market) }),
          };
        }

        case "next_trading": {
          const date = (args.date as string) ?? today;
          const nextDay = calendar.nextTradingDay(date, market);
          return { fromDate: date, market, nextTradingDay: nextDay };
        }

        case "prev_trading": {
          const date = (args.date as string) ?? today;
          const prevDay = calendar.prevTradingDay(date, market);
          return { fromDate: date, market, prevTradingDay: prevDay };
        }

        case "trading_days": {
          const fromDate = (args.from_date as string) ?? today;
          const toDate =
            (args.to_date as string) ??
            new Date(Date.now() + 30 * 86400000).toISOString().slice(0, 10);
          const days = calendar.getTradingDays(fromDate, toDate, market);
          return { market, fromDate, toDate, count: days.length, tradingDays: days };
        }

        case "count_days": {
          const fromDate = (args.from_date as string) ?? today;
          const toDate =
            (args.to_date as string) ??
            new Date(Date.now() + 30 * 86400000).toISOString().slice(0, 10);
          const count = calendar.countTradingDays(fromDate, toDate, market);
          return { market, fromDate, toDate, tradingDayCount: count };
        }

        case "holidays": {
          const year = args.year as number | undefined;
          const holidays = calendar.getHolidays(market, year);
          return { market, year: year ?? "all", count: holidays.length, holidays };
        }

        case "upcoming": {
          const limit = (args.limit as number) ?? 10;
          const fromDate = (args.date as string) ?? today;
          const holidays = calendar.getUpcomingHolidays(market, limit, fromDate);
          return { market, count: holidays.length, holidays };
        }

        case "trading_hours": {
          return calendar.getTradingHours(market);
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
