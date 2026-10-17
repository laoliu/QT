/**
 * DCA (Dollar-Cost Averaging) Schedule Service — automates recurring buy orders.
 *
 * Schedules periodic "定投" orders via the TaskScheduler, e.g.:
 *   "每周一买入¥5000的600519" → weekly buy-order at market open.
 *
 * Persistence: DCA plans are stored to disk and restored on restart.
 * Execution: each tick creates a buy order via OrderManager.submit().
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { QuantTradingConfig } from "../types.js";
import { isTradingDay } from "../utils/trading-calendar.js";
import type { OrderManager } from "./order-manager.js";
import { computePositionSize } from "./position-sizer.js";
import { nowCSTISO } from "../utils/cst-time.js";

// ── Types ────────────────────────────────────────────────────────

export type DcaFrequency = "daily" | "weekly" | "biweekly" | "monthly";

export interface DcaPlan {
  id: string;
  /** Stock symbol. */
  symbol: string;
  /** Amount in CNY to buy each period. */
  amount: number;
  /** Recurrence frequency. */
  frequency: DcaFrequency;
  /** Day of week for weekly/biweekly (0=Sun..6=Sat), day of month for monthly. */
  dayOfPeriod?: number;
  /** Hour of day to execute (0-23, default 9 = market open). */
  hour?: number;
  /** Status. */
  status: "active" | "paused" | "cancelled";
  /** Total executions so far. */
  executionCount: number;
  /** Total amount invested so far. */
  totalInvested: number;
  /** Last execution timestamp. */
  lastExecutedAt?: string;
  /** Next scheduled execution ISO timestamp. */
  nextExecutionAt?: string;
  /** Optional max executions (auto-pause after N). */
  maxExecutions?: number;
  /** Created timestamp. */
  createdAt: string;
  /** Human description. */
  description: string;
}

// ── Frequency intervals ──────────────────────────────────────────

const FREQ_MS: Record<DcaFrequency, number> = {
  daily: 24 * 60 * 60 * 1000,
  weekly: 7 * 24 * 60 * 60 * 1000,
  biweekly: 14 * 24 * 60 * 60 * 1000,
  monthly: 30 * 24 * 60 * 60 * 1000, // approximate
};

const FREQ_LABEL: Record<DcaFrequency, string> = {
  daily: "每日",
  weekly: "每周",
  biweekly: "每两周",
  monthly: "每月",
};

// ── DCA Schedule Service ─────────────────────────────────────────

export class DcaScheduleService {
  private plans = new Map<string, DcaPlan>();
  private timers = new Map<string, ReturnType<typeof setTimeout>>();
  private idCounter = 0;
  private cfg: QuantTradingConfig;
  private getOrderMgr: () => OrderManager | null;
  private getPrice: (symbol: string) => Promise<number | null>;
  private notifyFn?: (msg: string) => void | Promise<void>;

  constructor(
    cfg: QuantTradingConfig,
    getOrderMgr: () => OrderManager | null,
    getPrice: (symbol: string) => Promise<number | null>,
  ) {
    this.cfg = cfg;
    this.getOrderMgr = getOrderMgr;
    this.getPrice = getPrice;
  }

  setNotify(fn: (msg: string) => void | Promise<void>): void {
    this.notifyFn = fn;
  }

  // ── CRUD ───────────────────────────────────────────────────

  createPlan(opts: {
    symbol: string;
    amount: number;
    frequency: DcaFrequency;
    dayOfPeriod?: number;
    hour?: number;
    maxExecutions?: number;
    description?: string;
  }): DcaPlan {
    const id = `dca-${String(++this.idCounter).padStart(4, "0")}`;
    const nextAt = this.computeNextExecution(opts.frequency, opts.dayOfPeriod, opts.hour);
    const plan: DcaPlan = {
      id,
      symbol: opts.symbol,
      amount: opts.amount,
      frequency: opts.frequency,
      dayOfPeriod: opts.dayOfPeriod,
      hour: opts.hour,
      status: "active",
      executionCount: 0,
      totalInvested: 0,
      nextExecutionAt: nextAt.toISOString(),
      maxExecutions: opts.maxExecutions,
      createdAt: nowCSTISO(),
      description:
        opts.description ?? `${FREQ_LABEL[opts.frequency]}定投 ${opts.symbol} ¥${opts.amount}`,
    };

    this.plans.set(id, plan);
    this.scheduleNext(plan);
    void this.save();
    return plan;
  }

  pausePlan(id: string): boolean {
    const plan = this.plans.get(id);
    if (!plan || plan.status !== "active") return false;
    plan.status = "paused";
    this.clearTimer(id);
    void this.save();
    return true;
  }

  resumePlan(id: string): boolean {
    const plan = this.plans.get(id);
    if (!plan || plan.status !== "paused") return false;
    plan.status = "active";
    plan.nextExecutionAt = this.computeNextExecution(
      plan.frequency,
      plan.dayOfPeriod,
      plan.hour,
    ).toISOString();
    this.scheduleNext(plan);
    void this.save();
    return true;
  }

  cancelPlan(id: string): boolean {
    const plan = this.plans.get(id);
    if (!plan) return false;
    plan.status = "cancelled";
    this.clearTimer(id);
    void this.save();
    return true;
  }

  listPlans(status?: DcaPlan["status"]): DcaPlan[] {
    const all = [...this.plans.values()];
    if (status) return all.filter((p) => p.status === status);
    return all;
  }

  getPlan(id: string): DcaPlan | undefined {
    return this.plans.get(id);
  }

  // ── Execution ──────────────────────────────────────────────

  private async executePlan(plan: DcaPlan): Promise<void> {
    if (plan.status !== "active") return;

    const mgr = this.getOrderMgr();
    if (!mgr) {
      void this.notifyFn?.(`[定投] ${plan.symbol}: 交易引擎未启动，跳过本次定投`);
      this.scheduleNext(plan);
      return;
    }

    // Get current price
    const price = await this.getPrice(plan.symbol);
    if (!price || price <= 0) {
      void this.notifyFn?.(`[定投] ${plan.symbol}: 无法获取价格，跳过本次定投`);
      this.scheduleNext(plan);
      return;
    }

    // Compute quantity from amount
    const result = computePositionSize({
      method: "fixed_amount",
      equity: plan.amount, // use amount as "equity" for fixed_amount
      price,
      amount: plan.amount,
    });

    if (result.quantity <= 0) {
      void this.notifyFn?.(
        `[定投] ${plan.symbol}: 金额不足买入1手 (¥${plan.amount}, 价格¥${price.toFixed(2)})`,
      );
      this.scheduleNext(plan);
      return;
    }

    // Submit order
    const { detectMarketFromSymbol } = await import("../data/market-data-provider.js");
    const submitResult = await mgr.submit({
      symbol: plan.symbol,
      market: detectMarketFromSymbol(plan.symbol),
      side: "buy",
      orderType: "market",
      quantity: result.quantity,
      strategyId: `dca_${plan.id}`,
      signalId: `dca_${plan.id}_${Date.now()}`,
    });

    plan.executionCount++;
    plan.lastExecutedAt = nowCSTISO();

    if (submitResult.ok) {
      plan.totalInvested += result.quantity * price;
      void this.notifyFn?.(
        `[定投] ${plan.symbol} 定投执行成功: 买入 ${result.quantity} 股 @ ¥${price.toFixed(2)}, ` +
          `累计第${plan.executionCount}次, 累计投入 ¥${plan.totalInvested.toFixed(0)}`,
      );
    } else {
      void this.notifyFn?.(
        `[定投] ${plan.symbol} 定投失败: ${submitResult.reason} (第${plan.executionCount}次)`,
      );
    }

    // Check max executions
    if (plan.maxExecutions && plan.executionCount >= plan.maxExecutions) {
      plan.status = "paused";
      void this.notifyFn?.(
        `[定投] ${plan.symbol} 已达最大执行次数 ${plan.maxExecutions}，自动暂停`,
      );
    } else {
      this.scheduleNext(plan);
    }

    void this.save();
  }

  // ── Scheduling ─────────────────────────────────────────────

  private scheduleNext(plan: DcaPlan): void {
    this.clearTimer(plan.id);
    if (plan.status !== "active") return;

    const nextAt = plan.nextExecutionAt
      ? new Date(plan.nextExecutionAt)
      : this.computeNextExecution(plan.frequency, plan.dayOfPeriod, plan.hour);
    const delay = Math.max(0, nextAt.getTime() - Date.now());

    const timer = setTimeout(() => {
      // Set next execution time before executing (so it's visible during execution)
      plan.nextExecutionAt = this.computeNextExecution(
        plan.frequency,
        plan.dayOfPeriod,
        plan.hour,
      ).toISOString();
      void this.executePlan(plan);
    }, delay);

    this.timers.set(plan.id, timer);
  }

  private computeNextExecution(freq: DcaFrequency, dayOfPeriod?: number, hour?: number): Date {
    const now = new Date();
    const h = hour ?? 9; // default: 9am (market open)
    const next = new Date(now);
    next.setHours(h, 30, 0, 0); // 9:30 by default (after A-share open auction)

    if (freq === "daily") {
      if (next <= now) next.setDate(next.getDate() + 1);
      // Skip non-trading days (weekends + holidays)
      for (let i = 0; i < 15; i++) {
        if (isTradingDay(next.toISOString().slice(0, 10))) break;
        next.setDate(next.getDate() + 1);
      }
    } else if (freq === "weekly" || freq === "biweekly") {
      const targetDay = dayOfPeriod ?? 1; // default Monday
      next.setDate(next.getDate() + ((targetDay - next.getDay() + 7) % 7 || 7));
      if (freq === "biweekly") {
        // Simple: if next is within 7 days from now, add another week
        const daysFromNow = (next.getTime() - now.getTime()) / (24 * 60 * 60 * 1000);
        if (daysFromNow <= 7) {
          next.setDate(next.getDate() + 7);
        }
      }
    } else if (freq === "monthly") {
      const targetDay = dayOfPeriod ?? 1;
      next.setDate(targetDay);
      if (next <= now) {
        next.setMonth(next.getMonth() + 1);
      }
    }

    // Final guard: skip forward if the computed date is a non-trading day
    for (let i = 0; i < 15; i++) {
      if (isTradingDay(next.toISOString().slice(0, 10))) break;
      next.setDate(next.getDate() + 1);
    }

    return next;
  }

  private clearTimer(id: string): void {
    const timer = this.timers.get(id);
    if (timer) {
      clearTimeout(timer);
      this.timers.delete(id);
    }
  }

  // ── Persistence ────────────────────────────────────────────

  private dataPath(): string {
    const dir = this.cfg.dataDir ?? path.join(process.cwd(), "data");
    return path.join(dir, "dca-plans.json");
  }

  async load(): Promise<void> {
    try {
      const raw = await fs.readFile(this.dataPath(), "utf-8");
      const data = JSON.parse(raw) as { idCounter: number; plans: DcaPlan[] };
      this.idCounter = data.idCounter ?? 0;
      for (const p of data.plans) {
        this.plans.set(p.id, p);
        if (p.status === "active") {
          this.scheduleNext(p);
        }
      }
    } catch {
      // No file yet or corrupted — start fresh
    }
  }

  private async save(): Promise<void> {
    try {
      const dir = path.dirname(this.dataPath());
      await fs.mkdir(dir, { recursive: true });
      const data = {
        idCounter: this.idCounter,
        plans: [...this.plans.values()],
      };
      await fs.writeFile(this.dataPath(), JSON.stringify(data, null, 2));
    } catch {
      // Ignore write errors
    }
  }

  destroy(): void {
    for (const timer of this.timers.values()) {
      clearTimeout(timer);
    }
    this.timers.clear();
  }
}
