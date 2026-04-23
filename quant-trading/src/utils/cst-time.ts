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
  const cst = toCSTDate();
  // Manual format with +08:00 offset instead of toISOString()'s Z suffix.
  // Using Z caused frontends (new Date(ts).toLocaleString()) to double-shift
  // by 8 hours because the browser treated the CST value as UTC.
  const p = (n: number, w = 2) => String(n).padStart(w, "0");
  return (
    `${cst.getUTCFullYear()}-${p(cst.getUTCMonth() + 1)}-${p(cst.getUTCDate())}` +
    `T${p(cst.getUTCHours())}:${p(cst.getUTCMinutes())}:${p(cst.getUTCSeconds())}` +
    `.${p(cst.getUTCMilliseconds(), 3)}+08:00`
  );
}

/** Convert a UTC ISO timestamp to CST for display. */
export function formatTimestampToDateCST(timestamp: number): string {
  return toCSTDate(new Date(timestamp)).toISOString().slice(0, 10);
}
