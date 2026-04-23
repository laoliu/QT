/**
 * Trade Plan Store — persistence layer for daily trade execution plans.
 */

import { randomUUID } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import { nowCSTISO } from "../utils/cst-time.js";

export interface TradePlanStock {
  code: string;
  name?: string;
  direction: "buy" | "sell";
  targetPrice?: number;
  quantity?: number;
  weight?: number;
  reason?: string;
  executed?: boolean;
  actualEntryPrice?: number;
  actualExitPrice?: number;
  executionNote?: string;
}

export interface TradePlan {
  id: string;
  date: string;
  status: "open" | "closed";
  budget: number;
  stocks: TradePlanStock[];
  notes?: string;
  createdAt: string;
  closedAt?: string;
}

export interface TradePlanCloseReport {
  id: string;
  date: string;
  executedCount: number;
  totalStocks: number;
  summary: string;
}

export interface TradePlanStore {
  listPlans(opts?: {
    date?: string;
    status?: "open" | "closed";
    limit?: number;
  }): TradePlan[];
  getPlan(id: string): TradePlan | undefined;
  getTodayPlan(): TradePlan | undefined;
  createPlan(input: {
    date: string;
    stocks: TradePlanStock[];
    budget: number;
    notes?: string;
  }): string;
  updatePlan(
    id: string,
    update: { notes?: string; budget?: number; stocks?: TradePlanStock[] },
  ): boolean;
  closePlan(id: string): TradePlanCloseReport | undefined;
  markExecution(
    planId: string,
    code: string,
    update: {
      executed?: boolean;
      actualEntryPrice?: number;
      actualExitPrice?: number;
      executionNote?: string;
    },
  ): boolean;
}

// ── Implementation ────────────────────────────────────────────────

function todayStr(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export class TradePlanStoreImpl implements TradePlanStore {
  private plans: TradePlan[] = [];
  private readonly persistPath: string | null;

  constructor(opts?: { persistPath?: string | null }) {
    this.persistPath = opts?.persistPath ?? null;
    this.load();
  }

  listPlans(opts?: { date?: string; status?: "open" | "closed"; limit?: number }): TradePlan[] {
    let result = [...this.plans];
    if (opts?.date) result = result.filter((p) => p.date === opts.date);
    if (opts?.status) result = result.filter((p) => p.status === opts.status);
    result.sort((a, b) => b.createdAt.localeCompare(a.createdAt));
    if (opts?.limit && opts.limit > 0) result = result.slice(0, opts.limit);
    return result;
  }

  getPlan(id: string): TradePlan | undefined {
    return this.plans.find((p) => p.id === id);
  }

  getTodayPlan(): TradePlan | undefined {
    const today = todayStr();
    return this.plans.find((p) => p.date === today && p.status === "open")
      ?? this.plans.find((p) => p.date === today);
  }

  createPlan(input: { date: string; stocks: TradePlanStock[]; budget: number; notes?: string }): string {
    const id = randomUUID();
    const plan: TradePlan = {
      id,
      date: input.date,
      status: "open",
      budget: input.budget,
      stocks: input.stocks,
      notes: input.notes,
      createdAt: nowCSTISO(),
    };
    this.plans.push(plan);
    this.save();
    return id;
  }

  updatePlan(id: string, update: { notes?: string; budget?: number; stocks?: TradePlanStock[] }): boolean {
    const plan = this.plans.find((p) => p.id === id);
    if (!plan) return false;
    if (update.notes !== undefined) plan.notes = update.notes;
    if (update.budget !== undefined) plan.budget = update.budget;
    if (update.stocks !== undefined) plan.stocks = update.stocks;
    this.save();
    return true;
  }

  closePlan(id: string): TradePlanCloseReport | undefined {
    const plan = this.plans.find((p) => p.id === id);
    if (!plan || plan.status === "closed") return undefined;
    plan.status = "closed";
    plan.closedAt = nowCSTISO();
    this.save();
    const executedCount = plan.stocks.filter((s) => s.executed).length;
    return {
      id: plan.id,
      date: plan.date,
      executedCount,
      totalStocks: plan.stocks.length,
      summary: `${executedCount}/${plan.stocks.length} 已执行`,
    };
  }

  markExecution(planId: string, code: string, update: { executed?: boolean; actualEntryPrice?: number; actualExitPrice?: number; executionNote?: string }): boolean {
    const plan = this.plans.find((p) => p.id === planId);
    if (!plan) return false;
    const stock = plan.stocks.find((s) => s.code === code);
    if (!stock) return false;
    if (update.executed !== undefined) stock.executed = update.executed;
    if (update.actualEntryPrice !== undefined) stock.actualEntryPrice = update.actualEntryPrice;
    if (update.actualExitPrice !== undefined) stock.actualExitPrice = update.actualExitPrice;
    if (update.executionNote !== undefined) stock.executionNote = update.executionNote;
    this.save();
    return true;
  }

  private load(): void {
    if (!this.persistPath || !existsSync(this.persistPath)) return;
    try {
      const raw = readFileSync(this.persistPath, "utf-8");
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) this.plans = parsed;
    } catch { /* start fresh */ }
  }

  private save(): void {
    if (!this.persistPath) return;
    try {
      const dir = dirname(this.persistPath);
      if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
      writeFileSync(this.persistPath, JSON.stringify(this.plans, null, 2), "utf-8");
    } catch { /* best-effort */ }
  }
}
