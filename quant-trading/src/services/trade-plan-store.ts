/**
 * TradePlanStore — persistent daily trade plan management.
 *
 * Allows users to:
 *   - Create daily trade plans with target, direction, price levels, sizing
 *   - Track plan execution status (pending → executed / cancelled)
 *   - Link plans to conditional orders for automatic execution
 *   - Review and update plans
 *
 * Persistence is delegated to QuantStorage (sqlite-storage), supporting both
 * SQLite and in-memory/JSON-backed fallback modes.
 */
import type { QuantStorage, TradePlanRow } from "./sqlite-storage.js";

export type PlanDirection = "buy" | "sell";
export type PlanStatus = "pending" | "executed" | "cancelled";

export type TradePlan = TradePlanRow;

export class TradePlanStore {
  private storage: QuantStorage;
  private idCounter = 0;
  private maxPlans: number;

  constructor(storage: QuantStorage, opts: { maxPlans?: number } = {}) {
    this.storage = storage;
    this.maxPlans = opts.maxPlans ?? 2000;
  }

  /** Restore the idCounter from existing data (call after storage.initialize). */
  async init(): Promise<void> {
    const plans = await this.storage.getTradePlans({ limit: 1 });
    if (plans.length > 0) {
      // Extract max numeric suffix from existing IDs like "tp-00042"
      const allPlans = await this.storage.getTradePlans({ limit: this.maxPlans });
      let maxId = 0;
      for (const p of allPlans) {
        const m = p.id.match(/^tp-(\d+)$/);
        if (m) maxId = Math.max(maxId, parseInt(m[1]!, 10));
      }
      this.idCounter = maxId;
    }
  }

  /** Create a new trade plan. Returns the plan ID. */
  async addPlan(params: {
    symbol: string;
    direction: PlanDirection;
    planPrice: number;
    stopPrice: number;
    targetPrice: number;
    positionRatio: number;
    reason: string;
    date?: string;
    strategyId?: string;
  }): Promise<string> {
    // Prune if over limit
    const counts = await this.storage.getTradePlanCount();
    if (counts.total >= this.maxPlans) {
      const oldest = await this.storage.getTradePlans({ limit: Math.floor(this.maxPlans * 0.1) });
      // Delete oldest plans (getTradePlans returns newest-first, so reverse)
      const toDelete = oldest.reverse();
      for (const p of toDelete) {
        await this.storage.deleteTradePlan(p.id);
      }
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

    await this.storage.insertTradePlan(plan);
    return id;
  }

  /** Update an existing pending plan. */
  async updatePlan(
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
  ): Promise<boolean> {
    const plan = await this.storage.getTradePlan(id);
    if (!plan || plan.status !== "pending") return false;
    return this.storage.updateTradePlan(id, patch);
  }

  /** Mark a plan as executed. */
  async markExecuted(id: string, executedPrice?: number): Promise<boolean> {
    const plan = await this.storage.getTradePlan(id);
    if (!plan || plan.status !== "pending") return false;
    const now = new Date().toISOString();
    return this.storage.updateTradePlan(id, {
      status: "executed",
      executedPrice,
      executedAt: now,
    });
  }

  /** Mark a plan as cancelled. */
  async markCancelled(id: string, cancelReason?: string): Promise<boolean> {
    const plan = await this.storage.getTradePlan(id);
    if (!plan || plan.status !== "pending") return false;
    return this.storage.updateTradePlan(id, {
      status: "cancelled",
      cancelReason,
    });
  }

  /** Delete a plan by ID. */
  async deletePlan(id: string): Promise<boolean> {
    return this.storage.deleteTradePlan(id);
  }

  /** Get a plan by ID. */
  async getPlan(id: string): Promise<TradePlan | undefined> {
    return this.storage.getTradePlan(id);
  }

  /** Get plans for a specific date. */
  async getByDate(date: string): Promise<TradePlan[]> {
    return this.storage.getTradePlans({ date });
  }

  /** Get plans for a specific symbol. */
  async getBySymbol(symbol: string, limit = 20): Promise<TradePlan[]> {
    return this.storage.getTradePlans({ symbol, limit });
  }

  /** Get plans by status. */
  async getByStatus(status: PlanStatus, limit = 50): Promise<TradePlan[]> {
    return this.storage.getTradePlans({ status, limit });
  }

  /** Get recent plans (newest first). */
  async getRecent(limit = 20): Promise<TradePlan[]> {
    return this.storage.getTradePlans({ limit });
  }

  /** Link a conditional order to a plan. */
  async linkOrder(planId: string, orderId: string): Promise<boolean> {
    const plan = await this.storage.getTradePlan(planId);
    if (!plan) return false;
    return this.storage.updateTradePlan(planId, { linkedOrderId: orderId });
  }

  /** Get summary statistics. */
  async getSummary(): Promise<{
    totalPlans: number;
    pending: number;
    executed: number;
    cancelled: number;
    executionRate: number;
    plansByDate: Record<string, number>;
  }> {
    const counts = await this.storage.getTradePlanCount();
    const decided = counts.executed + counts.cancelled;
    const executionRate = decided > 0 ? counts.executed / decided : 0;

    const allPlans = await this.storage.getTradePlans({ limit: this.maxPlans });
    const plansByDate: Record<string, number> = {};
    for (const p of allPlans) {
      plansByDate[p.date] = (plansByDate[p.date] ?? 0) + 1;
    }

    return {
      totalPlans: counts.total,
      pending: counts.pending,
      executed: counts.executed,
      cancelled: counts.cancelled,
      executionRate,
      plansByDate,
    };
  }
}
