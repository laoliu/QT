/**
 * Lightweight cron expression parser.
 *
 * Supports standard 5-field cron expressions:
 *   minute hour day-of-month month day-of-week
 *
 * Features:
 *   - Wildcards: *
 *   - Ranges: 1-5
 *   - Step values: *​/5, 1-10/2
 *   - Lists: 1,3,5
 *   - Day-of-week: 0=Sunday, 1=Monday ... 6=Saturday
 *
 * Examples:
 *   "0 7 * * 1-5"   → weekdays at 07:00
 *   "10 15 * * 1-5"  → weekdays at 15:10
 *   "0 17 * * 5"     → Fridays at 17:00
 *   "*​/30 9-15 * * 1-5" → every 30 min during 09:00-15:00 on weekdays
 */

/** Parse a single cron field into a Set of allowed integer values. */
function parseField(field: string, min: number, max: number): Set<number> {
  const result = new Set<number>();

  for (const part of field.split(",")) {
    const trimmed = part.trim();

    // Handle step: */2, 1-10/3
    const [rangeStr, stepStr] = trimmed.split("/");
    const step = stepStr ? parseInt(stepStr, 10) : 1;

    if (isNaN(step) || step <= 0) {
      throw new Error(`Invalid step value in cron field: "${field}"`);
    }

    let start: number;
    let end: number;

    if (rangeStr === "*") {
      start = min;
      end = max;
    } else if (rangeStr!.includes("-")) {
      const [lo, hi] = rangeStr!.split("-").map((s) => parseInt(s, 10));
      if (isNaN(lo!) || isNaN(hi!)) {
        throw new Error(`Invalid range in cron field: "${field}"`);
      }
      start = lo!;
      end = hi!;
    } else {
      const val = parseInt(rangeStr!, 10);
      if (isNaN(val)) {
        throw new Error(`Invalid value in cron field: "${field}"`);
      }
      if (step === 1) {
        result.add(val);
        continue;
      }
      start = val;
      end = max;
    }

    for (let i = start; i <= end; i += step) {
      result.add(i);
    }
  }

  return result;
}

/** Parsed cron expression — pre-computed sets for each field. */
export interface CronExpression {
  /** Original expression string. */
  raw: string;
  minutes: Set<number>;
  hours: Set<number>;
  daysOfMonth: Set<number>;
  months: Set<number>;
  daysOfWeek: Set<number>;
}

/**
 * Parse a 5-field cron expression string.
 * @throws if the expression is invalid.
 */
export function parseCron(expr: string): CronExpression {
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) {
    throw new Error(
      `Invalid cron expression: expected 5 fields, got ${parts.length}. Format: "minute hour day-of-month month day-of-week"`,
    );
  }

  return {
    raw: expr.trim(),
    minutes: parseField(parts[0]!, 0, 59),
    hours: parseField(parts[1]!, 0, 23),
    daysOfMonth: parseField(parts[2]!, 1, 31),
    months: parseField(parts[3]!, 1, 12),
    daysOfWeek: parseField(parts[4]!, 0, 6), // 0=Sun
  };
}

/** Check if a given Date matches a parsed cron expression. */
export function cronMatches(cron: CronExpression, date: Date): boolean {
  return (
    cron.minutes.has(date.getMinutes()) &&
    cron.hours.has(date.getHours()) &&
    cron.daysOfMonth.has(date.getDate()) &&
    cron.months.has(date.getMonth() + 1) && // JS months are 0-indexed
    cron.daysOfWeek.has(date.getDay())
  );
}

/**
 * Compute the next occurrence of a cron expression after `after`.
 * Scans minute-by-minute up to 400 days ahead (trades across all months).
 * Returns null if no match is found (shouldn't happen for valid expressions).
 */
export function nextCronOccurrence(cron: CronExpression, after: Date): Date | null {
  // Start from the next full minute
  const candidate = new Date(after.getTime());
  candidate.setSeconds(0, 0);
  candidate.setMinutes(candidate.getMinutes() + 1);

  // Scan up to 400 days
  const limit = after.getTime() + 400 * 24 * 60 * 60 * 1000;

  while (candidate.getTime() < limit) {
    // Fast-skip: if month doesn't match, jump to next month
    if (!cron.months.has(candidate.getMonth() + 1)) {
      candidate.setMonth(candidate.getMonth() + 1, 1);
      candidate.setHours(0, 0, 0, 0);
      continue;
    }

    // Fast-skip: if day-of-month or day-of-week doesn't match, jump to next day
    if (!cron.daysOfMonth.has(candidate.getDate()) || !cron.daysOfWeek.has(candidate.getDay())) {
      candidate.setDate(candidate.getDate() + 1);
      candidate.setHours(0, 0, 0, 0);
      continue;
    }

    // Fast-skip: if hour doesn't match, jump to next hour
    if (!cron.hours.has(candidate.getHours())) {
      candidate.setHours(candidate.getHours() + 1, 0, 0, 0);
      continue;
    }

    // Check minute
    if (cron.minutes.has(candidate.getMinutes())) {
      return candidate;
    }

    // Jump to next minute
    candidate.setMinutes(candidate.getMinutes() + 1);
  }

  return null;
}

/**
 * Describe a cron expression in human-readable Chinese.
 */
export function describeCron(expr: string): string {
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) return expr;

  const [minute, hour, _dom, _month, dow] = parts;

  const dowMap: Record<string, string> = {
    "1-5": "周一至周五",
    "0-6": "每天",
    "*": "每天",
    "1": "周一",
    "2": "周二",
    "3": "周三",
    "4": "周四",
    "5": "周五",
    "6": "周六",
    "0": "周日",
  };

  const dayStr = dowMap[dow!] ?? `(${dow})`;
  const hourStr = hour === "*" ? "每小时" : `${hour}时`;
  const minStr = minute === "0" ? "整" : `${minute}分`;

  if (hour === "*" && minute!.startsWith("*/")) {
    return `${dayStr} 每${minute!.slice(2)}分钟`;
  }
  if (hour !== "*" && minute !== "*") {
    return `${dayStr} ${hourStr}${minStr}`;
  }

  return `${dayStr} ${hourStr}${minStr}`;
}
