/**
 * A-share trading calendar utility.
 *
 * Provides:
 * - Holiday awareness for A-share markets
 * - Trading day checks and next/prev trading day lookups
 * - Used by DCA scheduler, scheduled tasks, and market-hours checks
 *
 * Holiday list covers 2024–2027. Beyond that range, falls back to
 * weekend-only checks (still useful as a baseline).
 */

// ── A-share statutory holidays (YYYY-MM-DD) ────────────────────
// Source: China Securities Regulatory Commission (CSRC) annual notices.
// Each entry is a non-trading day that falls on a weekday.

const CN_HOLIDAYS: ReadonlySet<string> = new Set([
  // ── 2024 ──────────────────────────────────────────────────────
  "2024-01-01", // 元旦
  "2024-02-09", "2024-02-12", "2024-02-13", "2024-02-14", "2024-02-15", "2024-02-16", // 春节
  "2024-04-04", "2024-04-05", // 清明节
  "2024-05-01", "2024-05-02", "2024-05-03", // 劳动节
  "2024-06-10", // 端午节
  "2024-09-16", "2024-09-17", // 中秋节
  "2024-10-01", "2024-10-02", "2024-10-03", "2024-10-04", "2024-10-07", // 国庆节

  // ── 2025 ──────────────────────────────────────────────────────
  "2025-01-01", // 元旦
  "2025-01-28", "2025-01-29", "2025-01-30", "2025-01-31", "2025-02-03", "2025-02-04", // 春节
  "2025-04-04", // 清明节
  "2025-05-01", "2025-05-02", // 劳动节
  "2025-05-31", "2025-06-02", // 端午节
  "2025-10-01", "2025-10-02", "2025-10-03", "2025-10-06", "2025-10-07", // 国庆节+中秋

  // ── 2026 ──────────────────────────────────────────────────────
  "2026-01-01", "2026-01-02", // 元旦
  "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20", "2026-02-23", "2026-02-24", // 春节
  "2026-04-06", // 清明节
  "2026-05-01", // 劳动节
  "2026-06-19", // 端午节
  "2026-09-25", // 中秋节 (Friday)
  "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07", // 国庆节

  // ── 2027 ──────────────────────────────────────────────────────
  "2027-01-01", // 元旦
  "2027-02-08", "2027-02-09", "2027-02-10", "2027-02-11", "2027-02-12", // 春节
  "2027-04-05", // 清明节
  "2027-05-03", // 劳动节
  "2027-06-07", // 端午节
  "2027-09-27", // 中秋节
  "2027-10-01", "2027-10-04", "2027-10-05", "2027-10-06", "2027-10-07", // 国庆节
]);

// Special working Saturdays/Sundays (调休 — make-up workdays)
const CN_MAKEUP_WORKDAYS: ReadonlySet<string> = new Set([
  // 2024
  "2024-02-04", "2024-02-18", "2024-04-07", "2024-04-28", "2024-05-11",
  "2024-09-14", "2024-09-29",
  // 2025
  "2025-01-26", "2025-02-08", "2025-04-27", "2025-09-28", "2025-10-11",
  // 2026
  "2026-02-15", "2026-02-28", "2026-10-10",
  // 2027
  "2027-02-06", "2027-02-20", "2027-10-09",
]);

/**
 * Check if a date string (YYYY-MM-DD) is a weekend day.
 */
function isWeekend(dateStr: string): boolean {
  const d = new Date(dateStr + "T12:00:00Z"); // noon UTC to avoid TZ edge cases
  const day = d.getUTCDay();
  return day === 0 || day === 6;
}

/**
 * Returns true if the given date is an A-share trading day.
 * - Not a weekend (unless it's a make-up workday)
 * - Not a statutory holiday
 */
export function isTradingDay(dateStr: string): boolean {
  // Make-up workday overrides weekend
  if (CN_MAKEUP_WORKDAYS.has(dateStr)) return true;
  // Weekend = not trading
  if (isWeekend(dateStr)) return false;
  // Holiday = not trading
  if (CN_HOLIDAYS.has(dateStr)) return false;
  // Normal weekday
  return true;
}

/**
 * Get today's date in CST (YYYY-MM-DD).
 */
export function todayInCST(): string {
  const now = new Date();
  const cst = new Date(now.getTime() + 8 * 60 * 60 * 1000);
  return cst.toISOString().slice(0, 10);
}

/**
 * Returns true if today is an A-share trading day.
 */
export function isTodayTradingDay(): boolean {
  return isTradingDay(todayInCST());
}

/**
 * Find the next trading day on or after the given date.
 * @param dateStr - Starting date (YYYY-MM-DD)
 * @param maxLookAhead - Maximum days to search forward (default 15)
 */
export function nextTradingDay(dateStr: string, maxLookAhead = 15): string | null {
  const d = new Date(dateStr + "T12:00:00Z");
  for (let i = 0; i < maxLookAhead; i++) {
    const str = d.toISOString().slice(0, 10);
    if (isTradingDay(str)) return str;
    d.setUTCDate(d.getUTCDate() + 1);
  }
  return null;
}

/**
 * Find the next trading day strictly after the given date.
 */
export function nextTradingDayAfter(dateStr: string, maxLookAhead = 15): string | null {
  const d = new Date(dateStr + "T12:00:00Z");
  d.setUTCDate(d.getUTCDate() + 1);
  return nextTradingDay(d.toISOString().slice(0, 10), maxLookAhead);
}

/**
 * Find the previous trading day on or before the given date.
 * @param dateStr - Starting date (YYYY-MM-DD)
 * @param maxLookBack - Maximum days to search backward (default 15)
 */
export function prevTradingDay(dateStr: string, maxLookBack = 15): string | null {
  const d = new Date(dateStr + "T12:00:00Z");
  for (let i = 0; i < maxLookBack; i++) {
    const str = d.toISOString().slice(0, 10);
    if (isTradingDay(str)) return str;
    d.setUTCDate(d.getUTCDate() - 1);
  }
  return null;
}

/**
 * Count trading days between two dates (inclusive of both endpoints).
 */
export function tradingDaysBetween(startDate: string, endDate: string): number {
  const d = new Date(startDate + "T12:00:00Z");
  const end = new Date(endDate + "T12:00:00Z");
  let count = 0;
  while (d <= end) {
    if (isTradingDay(d.toISOString().slice(0, 10))) count++;
    d.setUTCDate(d.getUTCDate() + 1);
  }
  return count;
}

/**
 * Check if the A-share market is open right now, considering holidays.
 * Morning: 09:30–11:30, Afternoon: 13:00–15:00, trading days only.
 */
export function isCnMarketOpenWithCalendar(): boolean {
  const now = new Date();
  const cst = new Date(now.getTime() + 8 * 60 * 60 * 1000);
  const dateStr = cst.toISOString().slice(0, 10);
  if (!isTradingDay(dateStr)) return false;
  const h = cst.getUTCHours();
  const m = cst.getUTCMinutes();
  const hm = h * 100 + m;
  return (hm >= 930 && hm <= 1130) || (hm >= 1300 && hm <= 1500);
}

/**
 * Get the set of known CN holidays (for UI display / calendar rendering).
 */
export function getCnHolidays(): ReadonlySet<string> {
  return CN_HOLIDAYS;
}
