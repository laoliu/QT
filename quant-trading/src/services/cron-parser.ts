/**
 * Minimal cron expression parser for the TaskScheduler.
 *
 * Supports standard 5-field cron: minute hour day-of-month month day-of-week
 * Fields can be: * (any), number, comma-separated list, or range (e.g. 1-5).
 */

export interface CronExpression {
  raw: string;
  minute: CronField;
  hour: CronField;
  dayOfMonth: CronField;
  month: CronField;
  dayOfWeek: CronField;
}

type CronField = { kind: "any" } | { kind: "values"; values: number[] };

function parseField(field: string, min: number, max: number): CronField {
  if (field === "*") return { kind: "any" };
  const values: number[] = [];
  for (const part of field.split(",")) {
    const rangeMatch = part.match(/^(\d+)-(\d+)$/);
    if (rangeMatch) {
      const lo = Math.max(Number(rangeMatch[1]), min);
      const hi = Math.min(Number(rangeMatch[2]), max);
      for (let i = lo; i <= hi; i++) values.push(i);
    } else {
      const stepMatch = part.match(/^\*\/(\d+)$/);
      if (stepMatch) {
        const step = Number(stepMatch[1]);
        for (let i = min; i <= max; i += step) values.push(i);
      } else {
        const n = Number(part);
        if (!Number.isNaN(n) && n >= min && n <= max) values.push(n);
      }
    }
  }
  return { kind: "values", values: [...new Set(values)].sort((a, b) => a - b) };
}

export function parseCron(expr: string): CronExpression {
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) throw new Error(`Invalid cron expression: "${expr}" (expected 5 fields)`);
  return {
    raw: expr,
    minute: parseField(parts[0], 0, 59),
    hour: parseField(parts[1], 0, 23),
    dayOfMonth: parseField(parts[2], 1, 31),
    month: parseField(parts[3], 1, 12),
    dayOfWeek: parseField(parts[4], 0, 6),
  };
}

function fieldMatches(field: CronField, value: number): boolean {
  if (field.kind === "any") return true;
  return field.values.includes(value);
}

export function cronMatches(cron: CronExpression, date: Date): boolean {
  return (
    fieldMatches(cron.minute, date.getMinutes()) &&
    fieldMatches(cron.hour, date.getHours()) &&
    fieldMatches(cron.dayOfMonth, date.getDate()) &&
    fieldMatches(cron.month, date.getMonth() + 1) &&
    fieldMatches(cron.dayOfWeek, date.getDay())
  );
}

export function nextCronOccurrence(cron: CronExpression, after: Date): Date {
  const next = new Date(after);
  next.setSeconds(0, 0);
  next.setMinutes(next.getMinutes() + 1);

  // Brute-force search: check each minute for up to 366 days
  const maxIterations = 366 * 24 * 60;
  for (let i = 0; i < maxIterations; i++) {
    if (cronMatches(cron, next)) return next;
    next.setMinutes(next.getMinutes() + 1);
  }
  // Fallback: return 24h from now
  return new Date(after.getTime() + 24 * 60 * 60 * 1000);
}

export function describeCron(cron: CronExpression): string {
  const parts: string[] = [];
  const { minute, hour, dayOfWeek, dayOfMonth, month } = cron;

  const WEEKDAYS = ["日", "一", "二", "三", "四", "五", "六"];

  if (dayOfWeek.kind === "values") {
    parts.push(`每周${dayOfWeek.values.map((d) => WEEKDAYS[d]).join(",")}`);
  } else if (dayOfMonth.kind === "values") {
    parts.push(`每月${dayOfMonth.values.join(",")}日`);
  } else if (month.kind === "values") {
    parts.push(`${month.values.join(",")}月`);
  }

  if (hour.kind === "values" && minute.kind === "values") {
    const h = String(hour.values[0]).padStart(2, "0");
    const m = String(minute.values[0]).padStart(2, "0");
    parts.push(`${h}:${m}`);
  } else if (hour.kind === "values") {
    parts.push(`${hour.values[0]}时`);
  }

  return parts.length > 0 ? parts.join(" ") : cron.raw;
}
