/**
 * China Standard Time (UTC+8) helpers.
 *
 * `new Date().toISOString()` returns UTC, which is 8 hours behind CST.
 * These helpers ensure dates and timestamps are correct for the A-share market.
 */

const CST_OFFSET_MS = 8 * 60 * 60 * 1000;

/** Get a Date shifted to CST (for extracting Y/M/D components via UTC accessors). */
function toCSTDate(date?: Date): Date {
  const d = date ?? new Date();
  return new Date(d.getTime() + CST_OFFSET_MS);
}

/** Today's date in CST as YYYY-MM-DD. */
export function todayCST(): string {
  return toCSTDate().toISOString().slice(0, 10);
}

/** Format a Date to YYYY-MM-DD in CST. */
export function formatDateCST(date: Date): string {
  return toCSTDate(date).toISOString().slice(0, 10);
}

/** Current timestamp as ISO string in CST (for display / createdAt fields). */
export function nowCSTISO(): string {
  return toCSTDate().toISOString();
}

/** Convert a UTC ISO timestamp to CST for display. */
export function formatTimestampToDateCST(timestamp: number): string {
  return toCSTDate(new Date(timestamp)).toISOString().slice(0, 10);
}
