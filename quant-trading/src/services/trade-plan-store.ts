/**
 * TradePlanStore — persistent daily trade plan management.
 *
 * Allows users to:
 *   - Create daily trade plans with target, direction, price levels, sizing
 *   - Track plan execution status (pending → executed / cancelled)
 *   - Link plans to conditional orders for automatic execution
 *   - Review and update plans
 *
 * Persistence follows the same JSON-file pattern as TradeJournal.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

export type PlanDirection = "buy" | "sell";
export type PlanStatus = "pending" | "executed" | "cancelled";

export interface TradePlan {
  id: string;
  /** Symbol code (e.g. 600519). */
  symbol: string;
  /** Trade direction. */
  direction: PlanDirection;
  /** Planned entry/exit price. */
  planPrice: number;
  /** Stop-loss price. */
  stopPrice: number;
  /** Target price. */
  targetPrice: number;
  /** Position ratio (0-100, e.g. 20 means 20% of total capital). */
  positionRatio: number;
  /** Reason / rationale for the trade. */
  reason: string;
  /** Execution status. */
  status: PlanStatus;
  /** Date of the plan (YYYY-MM-DD). */
  date: string;
  /** Linked conditional order ID (if auto-order was created). */
  linkedOrderId?: string;
  /** Strategy ID (optional). */
  strategyId?: string;
  /** Actual execution price (filled when executed). */
  executedPrice?: number;
  /** Execution timestamp. */
  executedAt?: string;
  /** Cancellation reason. */
  cancelReason?: string;
  createdAt: string;
  updatedAt: string;
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
    this.maxPlans = opts.maxPlans ?? 2000;
    this.dataDir = opts.dataDir ?? null;
    this.loadFromDisk();
  }

  /** Create a new trade plan. Returns the plan ID. */
  addPlan(params: {
    symbol: string;
    direction: PlanDirection;
    planPrice: number;
    stopPrice: number;
    targetPrice: number;
    positionRatio: number;
    reason: string;
    date?: string;
    strategyId?: string;
  }): string {
    if (this.plans.length >= this.maxPlans) {
      this.plans = this.plans.slice(Math.floor(this.maxPlans * 0.1));
    }

    const now = new Date().toISOString();
    const id = `tp-${String(++this.idCounter).padStart(5, "0")}`;
    const plan: TradePlan = {
      id,
      symbol: params.symbol,
      direction: params.direction,
      planPrice: params.planPrice,
      stopPrice: params.stopPrice,
      targetPrice: params.targetPrice,
      positionRatio: params.positionRatio,
      reason: params.reason,
      status: "pending",
      date: params.date ?? now.slice(0, 10),
      strategyId: params.strategyId,
      createdAt: now,
      updatedAt: now,
    };

    this.plans.push(plan);
    this.saveToDisk();
    return id;
  }

  /** Update an existing plan. */
  updatePlan(
    id: string,
    patch: Partial<
      Pick<
        TradePlan,
        | "planPrice"
        | "stopPrice"
        | "targetPrice"
        | "positionRatio"
        | "reason"
        | "direction"
        | "linkedOrderId"
        | "strategyId"
      >
    >,
  ): boolean {
    const plan = this.plans.find((p) => p.id === id);
    if (!plan || plan.status !== "pending") return false;
    if (patch.planPrice !== undefined) plan.planPrice = patch.planPrice;
    if (patch.stopPrice !== undefined) plan.stopPrice = patch.stopPrice;
    if (patch.targetPrice !== undefined) plan.targetPrice = patch.targetPrice;
    if (patch.positionRatio !== undefined) plan.positionRatio = patch.positionRatio;
    if (patch.reason !== undefined) plan.reason = patch.reason;
    if (patch.direction !== undefined) plan.direction = patch.direction;
    if (patch.linkedOrderId !== undefined) plan.linkedOrderId = patch.linkedOrderId;
    if (patch.strategyId !== undefined) plan.strategyId = patch.strategyId;
    plan.updatedAt = new Date().toISOString();
    this.saveToDisk();
    return true;
  }

  /** Mark a plan as executed. */
  markExecuted(id: string, executedPrice?: number): boolean {
    const plan = this.plans.find((p) => p.id === id);
    if (!plan || plan.status !== "pending") return false;
    plan.status = "executed";
    plan.executedPrice = executedPrice;
    plan.executedAt = new Date().toISOString();
    plan.updatedAt = plan.executedAt;
    this.saveToDisk();
    return true;
  }

  /** Mark a plan as cancelled. */
  markCancelled(id: string, cancelReason?: string): boolean {
    const plan = this.plans.find((p) => p.id === id);
    if (!plan || plan.status !== "pending") return false;
    plan.status = "cancelled";
    plan.cancelReason = cancelReason;
    plan.updatedAt = new Date().toISOString();
    this.saveToDisk();
    return true;
  }

  /** Delete a plan by ID. */
  deletePlan(id: string): boolean {
    const idx = this.plans.findIndex((p) => p.id === id);
    if (idx < 0) return false;
    this.plans.splice(idx, 1);
    this.saveToDisk();
    return true;
  }

  /** Get a plan by ID. */
  getPlan(id: string): TradePlan | undefined {
    return this.plans.find((p) => p.id === id);
  }

  /** Get plans for a specific date. */
  getByDate(date: string): TradePlan[] {
    return this.plans.filter((p) => p.date === date);
  }

  /** Get plans for a specific symbol. */
  getBySymbol(symbol: string, limit = 20): TradePlan[] {
    return this.plans.filter((p) => p.symbol === symbol).slice(-limit);
  }

  /** Get plans by status. */
  getByStatus(status: PlanStatus, limit = 50): TradePlan[] {
    return this.plans.filter((p) => p.status === status).slice(-limit);
  }

  /** Get recent plans (newest first). */
  getRecent(limit = 20): TradePlan[] {
    return this.plans.slice(-limit).reverse();
  }

  /** Link a conditional order to a plan. */
  linkOrder(planId: string, orderId: string): boolean {
    const plan = this.plans.find((p) => p.id === planId);
    if (!plan) return false;
    plan.linkedOrderId = orderId;
    plan.updatedAt = new Date().toISOString();
    this.saveToDisk();
    return true;
  }

  /** Get summary statistics. */
  getSummary(): {
    totalPlans: number;
    pending: number;
    executed: number;
    cancelled: number;
    executionRate: number;
    plansByDate: Record<string, number>;
  } {
    const totalPlans = this.plans.length;
    const pending = this.plans.filter((p) => p.status === "pending").length;
    const executed = this.plans.filter((p) => p.status === "executed").length;
    const cancelled = this.plans.filter((p) => p.status === "cancelled").length;
    const decided = executed + cancelled;
    const executionRate = decided > 0 ? executed / decided : 0;

    const plansByDate: Record<string, number> = {};
    for (const p of this.plans) {
      plansByDate[p.date] = (plansByDate[p.date] ?? 0) + 1;
    }

    return { totalPlans, pending, executed, cancelled, executionRate, plansByDate };
  }

  get size(): number {
    return this.plans.length;
  }

  // ── Persistence ────────────────────────────────────────────

  private persistPath(): string | null {
    if (!this.dataDir) return null;
    return `${this.dataDir}/trade-plan.json`;
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
