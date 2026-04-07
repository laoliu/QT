/**
 * MarketCalendar — A-share / HK / US market trading calendar.
 *
 * Provides:
 *   - Trading day queries (is a given date a trading day?)
 *   - List trading days in a date range
 *   - Next/previous trading day lookup
 *   - Holiday schedule for CN/HK/US markets
 *   - Half-day session detection (HK market)
 *
 * Uses a predefined holiday list + weekend rules. The CN holiday list
 * covers major holidays; for best accuracy the list should be updated
 * annually (usually published by CSRC in late December).
 */

export type CalendarMarket = "cn" | "hk" | "us";

export interface MarketHoliday {
  date: string; // YYYY-MM-DD
  name: string;
  market: CalendarMarket;
  halfDay?: boolean; // true for half-day sessions (e.g. HK Christmas Eve)
}

/**
 * CN A-share holidays for 2024-2026 (common schedule).
 * Updated from State Council / CSRC announcements.
 */
const CN_HOLIDAYS: MarketHoliday[] = [
  // 2024
  { date: "2024-01-01", name: "元旦", market: "cn" },
  { date: "2024-02-09", name: "除夕", market: "cn" },
  { date: "2024-02-10", name: "春节", market: "cn" },
  { date: "2024-02-11", name: "春节", market: "cn" },
  { date: "2024-02-12", name: "春节", market: "cn" },
  { date: "2024-02-13", name: "春节", market: "cn" },
  { date: "2024-02-14", name: "春节", market: "cn" },
  { date: "2024-02-15", name: "春节", market: "cn" },
  { date: "2024-02-16", name: "春节", market: "cn" },
  { date: "2024-02-17", name: "春节", market: "cn" },
  { date: "2024-04-04", name: "清明节", market: "cn" },
  { date: "2024-04-05", name: "清明节", market: "cn" },
  { date: "2024-04-06", name: "清明节", market: "cn" },
  { date: "2024-05-01", name: "劳动节", market: "cn" },
  { date: "2024-05-02", name: "劳动节", market: "cn" },
  { date: "2024-05-03", name: "劳动节", market: "cn" },
  { date: "2024-05-04", name: "劳动节", market: "cn" },
  { date: "2024-05-05", name: "劳动节", market: "cn" },
  { date: "2024-06-08", name: "端午节", market: "cn" },
  { date: "2024-06-09", name: "端午节", market: "cn" },
  { date: "2024-06-10", name: "端午节", market: "cn" },
  { date: "2024-09-15", name: "中秋节", market: "cn" },
  { date: "2024-09-16", name: "中秋节", market: "cn" },
  { date: "2024-09-17", name: "中秋节", market: "cn" },
  { date: "2024-10-01", name: "国庆节", market: "cn" },
  { date: "2024-10-02", name: "国庆节", market: "cn" },
  { date: "2024-10-03", name: "国庆节", market: "cn" },
  { date: "2024-10-04", name: "国庆节", market: "cn" },
  { date: "2024-10-05", name: "国庆节", market: "cn" },
  { date: "2024-10-06", name: "国庆节", market: "cn" },
  { date: "2024-10-07", name: "国庆节", market: "cn" },
  // 2025
  { date: "2025-01-01", name: "元旦", market: "cn" },
  { date: "2025-01-28", name: "春节", market: "cn" },
  { date: "2025-01-29", name: "春节", market: "cn" },
  { date: "2025-01-30", name: "春节", market: "cn" },
  { date: "2025-01-31", name: "春节", market: "cn" },
  { date: "2025-02-01", name: "春节", market: "cn" },
  { date: "2025-02-02", name: "春节", market: "cn" },
  { date: "2025-02-03", name: "春节", market: "cn" },
  { date: "2025-02-04", name: "春节", market: "cn" },
  { date: "2025-04-04", name: "清明节", market: "cn" },
  { date: "2025-04-05", name: "清明节", market: "cn" },
  { date: "2025-04-06", name: "清明节", market: "cn" },
  { date: "2025-05-01", name: "劳动节", market: "cn" },
  { date: "2025-05-02", name: "劳动节", market: "cn" },
  { date: "2025-05-03", name: "劳动节", market: "cn" },
  { date: "2025-05-04", name: "劳动节", market: "cn" },
  { date: "2025-05-05", name: "劳动节", market: "cn" },
  { date: "2025-05-31", name: "端午节", market: "cn" },
  { date: "2025-06-01", name: "端午节", market: "cn" },
  { date: "2025-06-02", name: "端午节", market: "cn" },
  { date: "2025-10-01", name: "国庆节", market: "cn" },
  { date: "2025-10-02", name: "国庆节", market: "cn" },
  { date: "2025-10-03", name: "国庆节", market: "cn" },
  { date: "2025-10-04", name: "国庆节", market: "cn" },
  { date: "2025-10-05", name: "国庆节", market: "cn" },
  { date: "2025-10-06", name: "国庆节", market: "cn" },
  { date: "2025-10-07", name: "国庆节", market: "cn" },
  // 2026 (estimated — actual dates published by State Council late 2025)
  { date: "2026-01-01", name: "元旦", market: "cn" },
  { date: "2026-01-02", name: "元旦", market: "cn" },
  { date: "2026-02-16", name: "除夕", market: "cn" },
  { date: "2026-02-17", name: "春节", market: "cn" },
  { date: "2026-02-18", name: "春节", market: "cn" },
  { date: "2026-02-19", name: "春节", market: "cn" },
  { date: "2026-02-20", name: "春节", market: "cn" },
  { date: "2026-02-21", name: "春节", market: "cn" },
  { date: "2026-02-22", name: "春节", market: "cn" },
  { date: "2026-04-05", name: "清明节", market: "cn" },
  { date: "2026-04-06", name: "清明节", market: "cn" },
  { date: "2026-05-01", name: "劳动节", market: "cn" },
  { date: "2026-05-02", name: "劳动节", market: "cn" },
  { date: "2026-05-03", name: "劳动节", market: "cn" },
  { date: "2026-06-19", name: "端午节", market: "cn" },
  { date: "2026-06-20", name: "端午节", market: "cn" },
  { date: "2026-10-01", name: "国庆节", market: "cn" },
  { date: "2026-10-02", name: "国庆节", market: "cn" },
  { date: "2026-10-03", name: "国庆节", market: "cn" },
  { date: "2026-10-04", name: "国庆节", market: "cn" },
  { date: "2026-10-05", name: "国庆节", market: "cn" },
  { date: "2026-10-06", name: "国庆节", market: "cn" },
  { date: "2026-10-07", name: "国庆节", market: "cn" },
];

/**
 * US market holidays (NYSE/NASDAQ) for 2024-2026.
 */
const US_HOLIDAYS: MarketHoliday[] = [
  // 2024
  { date: "2024-01-01", name: "New Year's Day", market: "us" },
  { date: "2024-01-15", name: "MLK Day", market: "us" },
  { date: "2024-02-19", name: "Presidents' Day", market: "us" },
  { date: "2024-03-29", name: "Good Friday", market: "us" },
  { date: "2024-05-27", name: "Memorial Day", market: "us" },
  { date: "2024-06-19", name: "Juneteenth", market: "us" },
  { date: "2024-07-04", name: "Independence Day", market: "us" },
  { date: "2024-09-02", name: "Labor Day", market: "us" },
  { date: "2024-11-28", name: "Thanksgiving", market: "us" },
  { date: "2024-12-25", name: "Christmas", market: "us" },
  // 2025
  { date: "2025-01-01", name: "New Year's Day", market: "us" },
  { date: "2025-01-20", name: "MLK Day", market: "us" },
  { date: "2025-02-17", name: "Presidents' Day", market: "us" },
  { date: "2025-04-18", name: "Good Friday", market: "us" },
  { date: "2025-05-26", name: "Memorial Day", market: "us" },
  { date: "2025-06-19", name: "Juneteenth", market: "us" },
  { date: "2025-07-04", name: "Independence Day", market: "us" },
  { date: "2025-09-01", name: "Labor Day", market: "us" },
  { date: "2025-11-27", name: "Thanksgiving", market: "us" },
  { date: "2025-12-25", name: "Christmas", market: "us" },
  // 2026
  { date: "2026-01-01", name: "New Year's Day", market: "us" },
  { date: "2026-01-19", name: "MLK Day", market: "us" },
  { date: "2026-02-16", name: "Presidents' Day", market: "us" },
  { date: "2026-04-03", name: "Good Friday", market: "us" },
  { date: "2026-05-25", name: "Memorial Day", market: "us" },
  { date: "2026-06-19", name: "Juneteenth", market: "us" },
  { date: "2026-07-03", name: "Independence Day (observed)", market: "us" },
  { date: "2026-09-07", name: "Labor Day", market: "us" },
  { date: "2026-11-26", name: "Thanksgiving", market: "us" },
  { date: "2026-12-25", name: "Christmas", market: "us" },
];

/**
 * HK market holidays (HKEX) for 2024-2026.
 */
const HK_HOLIDAYS: MarketHoliday[] = [
  // 2024
  { date: "2024-01-01", name: "元旦", market: "hk" },
  { date: "2024-02-10", name: "农历年初一", market: "hk" },
  { date: "2024-02-12", name: "农历年初三", market: "hk" },
  { date: "2024-02-13", name: "农历年初四", market: "hk" },
  { date: "2024-03-29", name: "耶稣受难日", market: "hk" },
  { date: "2024-04-01", name: "复活节", market: "hk" },
  { date: "2024-04-04", name: "清明节", market: "hk" },
  { date: "2024-05-01", name: "劳动节", market: "hk" },
  { date: "2024-05-15", name: "佛诞", market: "hk" },
  { date: "2024-06-10", name: "端午节", market: "hk" },
  { date: "2024-07-01", name: "回归纪念日", market: "hk" },
  { date: "2024-09-18", name: "中秋节翌日", market: "hk" },
  { date: "2024-10-01", name: "国庆日", market: "hk" },
  { date: "2024-10-11", name: "重阳节", market: "hk" },
  { date: "2024-12-25", name: "圣诞节", market: "hk" },
  { date: "2024-12-26", name: "圣诞节翌日", market: "hk" },
  // 2025
  { date: "2025-01-01", name: "元旦", market: "hk" },
  { date: "2025-01-29", name: "农历年初一", market: "hk" },
  { date: "2025-01-30", name: "农历年初二", market: "hk" },
  { date: "2025-01-31", name: "农历年初三", market: "hk" },
  { date: "2025-04-04", name: "清明节", market: "hk" },
  { date: "2025-04-18", name: "耶稣受难日", market: "hk" },
  { date: "2025-04-19", name: "耶稣受难日翌日", market: "hk" },
  { date: "2025-04-21", name: "复活节", market: "hk" },
  { date: "2025-05-01", name: "劳动节", market: "hk" },
  { date: "2025-05-05", name: "佛诞", market: "hk" },
  { date: "2025-05-31", name: "端午节", market: "hk" },
  { date: "2025-07-01", name: "回归纪念日", market: "hk" },
  { date: "2025-10-01", name: "国庆日", market: "hk" },
  { date: "2025-10-07", name: "重阳节", market: "hk" },
  { date: "2025-12-25", name: "圣诞节", market: "hk" },
  { date: "2025-12-26", name: "圣诞节翌日", market: "hk" },
  // 2026 (estimated)
  { date: "2026-01-01", name: "元旦", market: "hk" },
  { date: "2026-02-17", name: "农历年初一", market: "hk" },
  { date: "2026-02-18", name: "农历年初二", market: "hk" },
  { date: "2026-02-19", name: "农历年初三", market: "hk" },
  { date: "2026-04-03", name: "耶稣受难日", market: "hk" },
  { date: "2026-04-06", name: "复活节", market: "hk" },
  { date: "2026-04-05", name: "清明节", market: "hk" },
  { date: "2026-05-01", name: "劳动节", market: "hk" },
  { date: "2026-05-24", name: "佛诞", market: "hk" },
  { date: "2026-06-19", name: "端午节", market: "hk" },
  { date: "2026-07-01", name: "回归纪念日", market: "hk" },
  { date: "2026-10-01", name: "国庆日", market: "hk" },
  { date: "2026-10-25", name: "重阳节", market: "hk" },
  { date: "2026-12-25", name: "圣诞节", market: "hk" },
  { date: "2026-12-26", name: "圣诞节翌日", market: "hk" },
];

/** HK half-day sessions (trading ends at 12:00). */
const HK_HALF_DAYS: MarketHoliday[] = [
  { date: "2024-12-24", name: "圣诞前夕", market: "hk", halfDay: true },
  { date: "2024-12-31", name: "除夕", market: "hk", halfDay: true },
  { date: "2025-01-28", name: "农历年三十", market: "hk", halfDay: true },
  { date: "2025-12-24", name: "圣诞前夕", market: "hk", halfDay: true },
  { date: "2025-12-31", name: "除夕", market: "hk", halfDay: true },
  { date: "2026-02-16", name: "农历年三十", market: "hk", halfDay: true },
  { date: "2026-12-24", name: "圣诞前夕", market: "hk", halfDay: true },
  { date: "2026-12-31", name: "除夕", market: "hk", halfDay: true },
];

const ALL_HOLIDAYS = [...CN_HOLIDAYS, ...US_HOLIDAYS, ...HK_HOLIDAYS, ...HK_HALF_DAYS];

// Build holiday lookup sets per market
const holidaySetByMarket: Record<CalendarMarket, Set<string>> = {
  cn: new Set(CN_HOLIDAYS.map((h) => h.date)),
  us: new Set(US_HOLIDAYS.map((h) => h.date)),
  hk: new Set([...HK_HOLIDAYS.map((h) => h.date)]),
};

function isWeekend(dateStr: string): boolean {
  const d = new Date(dateStr + "T12:00:00Z");
  const day = d.getUTCDay();
  return day === 0 || day === 6;
}

export class MarketCalendar {
  /** Check if a given date is a trading day for the specified market. */
  isTradingDay(date: string, market: CalendarMarket = "cn"): boolean {
    if (isWeekend(date)) return false;
    return !holidaySetByMarket[market]?.has(date);
  }

  /** Check if a given date is a half-day session. */
  isHalfDay(date: string, market: CalendarMarket = "hk"): boolean {
    return HK_HALF_DAYS.some((h) => h.date === date && h.market === market);
  }

  /** Get the next trading day from a given date (exclusive). */
  nextTradingDay(fromDate: string, market: CalendarMarket = "cn"): string {
    let d = new Date(fromDate + "T12:00:00Z");
    for (let i = 0; i < 30; i++) {
      d = new Date(d.getTime() + 86400000);
      const ds = d.toISOString().slice(0, 10);
      if (this.isTradingDay(ds, market)) return ds;
    }
    // Fallback: skip weekends only
    d = new Date(fromDate + "T12:00:00Z");
    d = new Date(d.getTime() + 86400000);
    while (isWeekend(d.toISOString().slice(0, 10))) {
      d = new Date(d.getTime() + 86400000);
    }
    return d.toISOString().slice(0, 10);
  }

  /** Get the previous trading day from a given date (exclusive). */
  prevTradingDay(fromDate: string, market: CalendarMarket = "cn"): string {
    let d = new Date(fromDate + "T12:00:00Z");
    for (let i = 0; i < 30; i++) {
      d = new Date(d.getTime() - 86400000);
      const ds = d.toISOString().slice(0, 10);
      if (this.isTradingDay(ds, market)) return ds;
    }
    d = new Date(fromDate + "T12:00:00Z");
    d = new Date(d.getTime() - 86400000);
    while (isWeekend(d.toISOString().slice(0, 10))) {
      d = new Date(d.getTime() - 86400000);
    }
    return d.toISOString().slice(0, 10);
  }

  /** List all trading days between two dates (inclusive). */
  getTradingDays(
    fromDate: string,
    toDate: string,
    market: CalendarMarket = "cn",
  ): string[] {
    const result: string[] = [];
    let d = new Date(fromDate + "T12:00:00Z");
    const end = new Date(toDate + "T12:00:00Z");
    while (d <= end) {
      const ds = d.toISOString().slice(0, 10);
      if (this.isTradingDay(ds, market)) result.push(ds);
      d = new Date(d.getTime() + 86400000);
    }
    return result;
  }

  /** Count trading days between two dates (inclusive). */
  countTradingDays(
    fromDate: string,
    toDate: string,
    market: CalendarMarket = "cn",
  ): number {
    return this.getTradingDays(fromDate, toDate, market).length;
  }

  /** Get holidays for a specific market and year. */
  getHolidays(market: CalendarMarket, year?: number): MarketHoliday[] {
    const yearStr = year ? String(year) : undefined;
    return ALL_HOLIDAYS.filter(
      (h) => h.market === market && (!yearStr || h.date.startsWith(yearStr)),
    );
  }

  /** Get upcoming holidays (from today, limit N). */
  getUpcomingHolidays(
    market: CalendarMarket = "cn",
    limit = 10,
    fromDate?: string,
  ): MarketHoliday[] {
    const from = fromDate ?? new Date().toISOString().slice(0, 10);
    return ALL_HOLIDAYS
      .filter((h) => h.market === market && h.date >= from)
      .sort((a, b) => a.date.localeCompare(b.date))
      .slice(0, limit);
  }

  /** Get market trading hours description. */
  getTradingHours(market: CalendarMarket): {
    market: CalendarMarket;
    timezone: string;
    sessions: Array<{ name: string; open: string; close: string }>;
  } {
    switch (market) {
      case "cn":
        return {
          market: "cn",
          timezone: "Asia/Shanghai (UTC+8)",
          sessions: [
            { name: "集合竞价", open: "09:15", close: "09:25" },
            { name: "上午盘", open: "09:30", close: "11:30" },
            { name: "下午盘", open: "13:00", close: "15:00" },
          ],
        };
      case "hk":
        return {
          market: "hk",
          timezone: "Asia/Hong_Kong (UTC+8)",
          sessions: [
            { name: "竞价时段", open: "09:00", close: "09:30" },
            { name: "上午盘", open: "09:30", close: "12:00" },
            { name: "下午盘", open: "13:00", close: "16:00" },
            { name: "收市竞价", open: "16:00", close: "16:10" },
          ],
        };
      case "us":
        return {
          market: "us",
          timezone: "America/New_York (UTC-5/UTC-4)",
          sessions: [
            { name: "Pre-market", open: "04:00", close: "09:30" },
            { name: "Regular", open: "09:30", close: "16:00" },
            { name: "After-hours", open: "16:00", close: "20:00" },
          ],
        };
    }
  }
}
