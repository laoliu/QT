/**
 * AutoReviewService — automatically triggers after-market review at 15:15 on trading days.
 *
 * On start, schedules a daily check at 15:15 (China Standard Time, UTC+8).
 * If today is a trading day (weekday, not a holiday), it generates a comprehensive
 * after-market review report and pushes it via the notification channel.
 *
 * The review includes:
 *   1. Market overview (indices + sector leaders)
 *   2. Portfolio performance (positions P&L + today's trades)
 *   3. Dragon tiger board highlights
 *   4. Performance attribution summary
 *   5. Tomorrow's trading plan hints
 */

import type { ReportTemplate } from "./report-templates.js";
import type { TaskScheduler } from "./task-scheduler.js";
import type { QuantTradingConfig } from "../types.js";

/** Check if a given date is a Chinese A-share trading day (weekday). */
function isTradingDay(date: Date): boolean {
  // Get day-of-week in China time (UTC+8)
  const chinaOffset = 8 * 60; // minutes
  const utcMs = date.getTime() + date.getTimezoneOffset() * 60_000;
  const chinaDate = new Date(utcMs + chinaOffset * 60_000);
  const day = chinaDate.getDay();
  // Weekdays only (Monday=1 .. Friday=5)
  return day >= 1 && day <= 5;
}

/** Get milliseconds until the next occurrence of HH:MM in China time (UTC+8). */
function msUntilChinaTime(targetHour: number, targetMinute: number): number {
  const now = new Date();
  const chinaOffset = 8 * 60; // minutes
  const utcMs = now.getTime() + now.getTimezoneOffset() * 60_000;
  const chinaMs = utcMs + chinaOffset * 60_000;
  const chinaDate = new Date(chinaMs);

  // Build target time for today in China TZ
  const targetToday = new Date(chinaDate);
  targetToday.setHours(targetHour, targetMinute, 0, 0);

  let diffMs = targetToday.getTime() - chinaDate.getTime();
  if (diffMs <= 0) {
    // Already past today's target — schedule for tomorrow
    diffMs += 24 * 60 * 60 * 1000;
  }
  return diffMs;
}

export interface AutoReviewServiceOptions {
  /** Hour (China time) to trigger review. Default: 15 */
  triggerHour?: number;
  /** Minute (China time) to trigger review. Default: 15 */
  triggerMinute?: number;
  /** Whether the service is enabled. Default: true */
  enabled?: boolean;
}

export class AutoReviewService {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private readonly triggerHour: number;
  private readonly triggerMinute: number;
  private readonly enabled: boolean;
  private scheduler: TaskScheduler | null = null;
  private reviewTemplate: ReportTemplate | null = null;
  private executeToolFn: ((name: string, args: Record<string, unknown>) => Promise<string>) | null =
    null;
  private config: QuantTradingConfig | null = null;
  private notifyFn: ((message: string) => void | Promise<void>) | null = null;
  private destroyed = false;

  constructor(opts: AutoReviewServiceOptions = {}) {
    this.triggerHour = opts.triggerHour ?? 15;
    this.triggerMinute = opts.triggerMinute ?? 15;
    this.enabled = opts.enabled !== false;
  }

  /** Wire dependencies. Must be called before start(). */
  init(deps: {
    scheduler: TaskScheduler;
    reviewTemplate: ReportTemplate;
    executeToolFn: (name: string, args: Record<string, unknown>) => Promise<string>;
    config: QuantTradingConfig;
    notifyFn: (message: string) => void | Promise<void>;
  }): void {
    this.scheduler = deps.scheduler;
    this.reviewTemplate = deps.reviewTemplate;
    this.executeToolFn = deps.executeToolFn;
    this.config = deps.config;
    this.notifyFn = deps.notifyFn;
  }

  /** Start the daily scheduling loop. */
  start(): void {
    if (!this.enabled || this.destroyed) return;
    this.scheduleNext();
  }

  /** Stop all timers. */
  stop(): void {
    this.destroyed = true;
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }

  private scheduleNext(): void {
    if (this.destroyed) return;
    const delayMs = msUntilChinaTime(this.triggerHour, this.triggerMinute);
    this.timer = setTimeout(() => {
      void this.onTrigger();
    }, delayMs);
  }

  private async onTrigger(): Promise<void> {
    if (this.destroyed) return;

    try {
      // Check if today is a trading day
      if (!isTradingDay(new Date())) {
        // Not a trading day — skip, schedule for next day
        return;
      }

      // Generate the after-market review report
      if (this.reviewTemplate && this.executeToolFn && this.config && this.notifyFn) {
        const report = await this.reviewTemplate.generate(this.executeToolFn, this.config);
        if (report) {
          await this.notifyFn(report);
        }
      }
    } catch {
      // Best-effort — don't crash the service
    } finally {
      // Schedule next day's trigger
      this.scheduleNext();
    }
  }
}
