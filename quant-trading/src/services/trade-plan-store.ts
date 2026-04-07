/**
 * TradePlanStore — persistent pre-market trade plans.
 *
 * Each plan contains:
 *   - date, target stocks (code, entry/stop/take-profit prices, position ratio, reason)
 *   - overall budget, status (open/closed)
 *   - post-market execution tracking with deviation report
 *
 * Data persisted to trade-plans.json in the configured dataDir.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

export interface TradePlanStock {
  /** Stock code (e.g. "600519"). */
  code: string;
  /** Stock name (optional). */
  name?: string;
  /** Planned entry price. */
  entryPrice: number;
  /** Stop-loss price. */
  stopPrice: number;
  /** Take-profit price. */
  takeProfitPrice: number;
  /** Position ratio (0-1, e.g. 0.2 = 20% of budget). */
  positionRatio: number;
  /** Reason / thesis for the trade. */
  reason: string;
  /** Post-market: actual entry price (null if not executed). */
  actualEntryPrice?: number | null;
  /** Post-market: actual exit price (null if still holding). */
  actualExitPrice?: number | null;
  /** Post-market: executed or not. */
  executed?: boolean;
  /** Post-market: notes on execution. */
  executionNote?: string;
}

export interface TradePlan {
  id: string;
  /** Plan date (YYYY-MM-DD). */
  date: string;
  /** Target stocks with entry/exit plan. */
  stocks: TradePlanStock[];
  /** Overall position budget (in yuan). */
  budget: number;
  /** Plan status: open = active, closed = reviewed. */
  status: "open" | "closed";
  /** Free-form notes for the day. */
  notes?: string;
  /** Deviation report generated when closing the plan. */
  deviationReport?: DeviationReport;
  createdAt: string;
  updatedAt: string;
}

export interface DeviationItem {
  code: string;
  name?: string;
  planned: { entryPrice: number; stopPrice: number; takeProfitPrice: number; positionRatio: number };
  actual: { entryPrice: number | null; exitPrice: number | null; executed: boolean };
  entryDeviation: number | null;
  executionRate: boolean;
}

export interface DeviationReport {
  generatedAt: string;
  totalPlanned: number;
  totalExecuted: number;
  executionRate: number;
  items: DeviationItem[];
}

export interface TradePlanStoreOptions {
  maxPlans?: number;
  dataDir?: string | null;
}

export class TradePlanStore {
  private plans: TradePlan[] = [];
  private idCounter = 0;
  private readonly maxPlans: number;
  private readonly dataDir: string | null;

  constructor(opts: TradePlanStoreOptions = {}) {
    this.maxPlans = opts.maxPlans ?? 500;
    this.dataDir = opts.dataDir ?? null;
    this.loadFromDisk();
  }

  /** Create a new trade plan. Returns the plan ID. */
  createPlan(params: {
    date: string;
    stocks: TradePlanStock[];
    budget: number;
    notes?: string;
  }): string {
    if (this.plans.length >= this.maxPlans) {
      this.plans = this.plans.slice(Math.floor(this.maxPlans * 0.1));
    }

    const now = new Date().toISOString();
    const id = `tp-${String(++this.idCounter).padStart(5, "0")}`;
    const plan: TradePlan = {
      id,
      date: params.date,
      stocks: params.stocks.map((s) => ({
        ...s,
        executed: false,
        actualEntryPrice: null,
        actualExitPrice: null,
      })),
      budget: params.budget,
      status: "open",
      notes: params.notes,
      createdAt: now,
      updatedAt: now,
    };

    this.plans.push(plan);
    this.saveToDisk();
    return id;
  }

  /** List plans, optionally filtered by date or status. */
  listPlans(filter?: { date?: string; status?: "open" | "closed"; limit?: number }): TradePlan[] {
    let result = this.plans;
    if (filter?.date) {
      result = result.filter((p) => p.date === filter.date);
    }
    if (filter?.status) {
      result = result.filter((p) => p.status === filter.status);
    }
    const limit = filter?.limit ?? 20;
    return result.slice(-limit);
  }

  /** Get a plan by ID. */
  getPlan(id: string): TradePlan | undefined {
    return this.plans.find((p) => p.id === id);
  }

  /** Get today's plan. */
  getTodayPlan(): TradePlan | undefined {
    const today = new Date().toISOString().slice(0, 10);
    return this.plans.filter((p) => p.date === today).pop();
  }

  /** Update plan fields (notes, stocks, budget). */
  updatePlan(
    id: string,
    patch: Partial<Pick<TradePlan, "notes" | "budget">> & {
      stocks?: TradePlanStock[];
    },
  ): boolean {
    const plan = this.plans.find((p) => p.id === id);
    if (!plan) return false;
    if (patch.notes !== undefined) plan.notes = patch.notes;
    if (patch.budget !== undefined) plan.budget = patch.budget;
    if (patch.stocks !== undefined) plan.stocks = patch.stocks;
    plan.updatedAt = new Date().toISOString();
    this.saveToDisk();
    return true;
  }

  /** Mark execution status for a stock in a plan. */
  markExecution(
    planId: string,
    code: string,
    execution: {
      executed: boolean;
      actualEntryPrice?: number | null;
      actualExitPrice?: number | null;
      executionNote?: string;
    },
  ): boolean {
    const plan = this.plans.find((p) => p.id === planId);
    if (!plan) return false;
    const stock = plan.stocks.find((s) => s.code === code);
    if (!stock) return false;
    stock.executed = execution.executed;
    if (execution.actualEntryPrice !== undefined) stock.actualEntryPrice = execution.actualEntryPrice;
    if (execution.actualExitPrice !== undefined) stock.actualExitPrice = execution.actualExitPrice;
    if (execution.executionNote !== undefined) stock.executionNote = execution.executionNote;
    plan.updatedAt = new Date().toISOString();
    this.saveToDisk();
    return true;
  }

  /** Close a plan and generate a deviation report comparing planned vs actual. */
  closePlan(id: string): DeviationReport | null {
    const plan = this.plans.find((p) => p.id === id);
    if (!plan) return null;

    const items: DeviationItem[] = plan.stocks.map((s) => {
      const entryDev =
        s.executed && s.actualEntryPrice != null
          ? ((s.actualEntryPrice - s.entryPrice) / s.entryPrice) * 100
          : null;
      return {
        code: s.code,
        name: s.name,
        planned: {
          entryPrice: s.entryPrice,
          stopPrice: s.stopPrice,
          takeProfitPrice: s.takeProfitPrice,
          positionRatio: s.positionRatio,
        },
        actual: {
          entryPrice: s.actualEntryPrice ?? null,
          exitPrice: s.actualExitPrice ?? null,
          executed: s.executed ?? false,
        },
        entryDeviation: entryDev,
        executionRate: s.executed ?? false,
      };
    });

    const totalPlanned = items.length;
    const totalExecuted = items.filter((i) => i.executionRate).length;
    const report: DeviationReport = {
      generatedAt: new Date().toISOString(),
      totalPlanned,
      totalExecuted,
      executionRate: totalPlanned > 0 ? totalExecuted / totalPlanned : 0,
      items,
    };

    plan.status = "closed";
    plan.deviationReport = report;
    plan.updatedAt = new Date().toISOString();
    this.saveToDisk();
    return report;
  }

  get size(): number {
    return this.plans.length;
  }

  // ── Persistence ────────────────────────────────────────────

  private persistPath(): string | null {
    if (!this.dataDir) return null;
    return `${this.dataDir}/trade-plans.json`;
  }

  private saveToDisk(): void {
    const p = this.persistPath();
    if (!p) return;
    const dir = dirname(p);
    if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
    writeFileSync(p, JSON.stringify({ idCounter: this.idCounter, plans: this.plans }, null, 2));
  }

  private loadFromDisk(): void {
    const p = this.persistPath();
    if (!p || !existsSync(p)) return;
    try {
      const data = JSON.parse(readFileSync(p, "utf-8")) as {
        idCounter: number;
        plans: TradePlan[];
      };
      this.idCounter = data.idCounter ?? 0;
      this.plans = data.plans ?? [];
    } catch {
      // Ignore corrupt data
    }
  }
}
