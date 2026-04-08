/**
 * Trading calendar — basic A-share trading day checker.
 *
 * Uses a simple weekend check plus known Chinese market holidays.
 * For production use, this should be replaced with a proper holiday calendar.
 */

/** Fixed Chinese market holidays (month-day strings). Updated yearly. */
const KNOWN_HOLIDAYS = new Set([
  // New Year
  "01-01",
  // Spring Festival (approximate, varies yearly)
  "01-28", "01-29", "01-30", "01-31", "02-01", "02-02", "02-03",
  // Qingming
  "04-04", "04-05", "04-06",
  // Labor Day
  "05-01", "05-02", "05-03", "05-04", "05-05",
  // Dragon Boat
  "06-08", "06-09", "06-10",
  // Mid-Autumn
  "09-15", "09-16", "09-17",
  // National Day
  "10-01", "10-02", "10-03", "10-04", "10-05", "10-06", "10-07",
]);

/**
 * Check if a given date string (YYYY-MM-DD) is a trading day.
 * Returns `true` for weekdays that are not known public holidays.
 */
export function isTradingDay(dateStr: string): boolean {
  const d = new Date(dateStr + "T00:00:00");
  const day = d.getDay();
  // Weekends are never trading days
  if (day === 0 || day === 6) return false;
  // Check known holidays
  const mmdd = dateStr.slice(5); // "MM-DD"
  if (KNOWN_HOLIDAYS.has(mmdd)) return false;
  return true;
}
