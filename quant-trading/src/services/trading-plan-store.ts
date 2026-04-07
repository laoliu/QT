/**
 * TradingPlanStore — persistent trading plan management.
 *
 * Supports the complete pre-market → intraday → post-market accountability loop:
 *   - Create daily trading plans with entry/exit triggers, position sizing
 *   - Track plan execution status (planned → triggered → executed → reviewed)
 *   - Link plans to actual orders for post-market accountability review
 *   - Compute plan discipline metrics (execution rate, deviation analysis)
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

export type PlanStatus = "planned" | "triggered" | "executed" | "cancelled" | "expired" | "reviewed";

export interface TradingPlanEntry {
  id: string;
  /** Date of the plan (YYYY-MM-DD). */
  date: string;
  /** Target symbol. */
  symbol: string;
  /** Plan direction. */
  direction: "buy" | "sell";
  /** Entry trigger condition description. */
  entryTrigger: string;
  /** Target entry price. */
  entryPrice?: number;
  /** Stop-loss price. */
  stopPrice?: number;
  /** Take-profit price. */
  targetPrice?: number;
  /** Max position size (shares or amount). */
  maxPosition?: number;
  /** Position sizing method description. */
  sizingMethod?: string;
  /** Reason / thesis for the trade. */
  reason: string;
  /** Risk-reward ratio (auto-calculated if entry/stop/target provided). */
  riskRewardRatio?: number;
  /** Current plan status. */
  status: PlanStatus;
  /** Strategy ID if linked to a strategy. */
  strategyId?: string;
  /** Linked order IDs (populated when executed). */
  linkedOrderIds: string[];
  /** Actual entry price (populated after execution). */
  actualEntryPrice?: number;
  /** Actual exit price (populated after close). */
  actualExitPrice?: number;
  /** Actual P&L (populated after close). */
  actualPnl?: number;
  /** Post-trade review notes. */
  reviewNotes?: string;
  /** Self-assessment score 1-5. */
  reviewRating?: number;
  /** Tags for categorization. */
  tags: string[];
  createdAt: string;
  updatedAt: string;
}

export interface TradingPlanStoreOptions {
  maxPlans?: number;
  dataDir?: string | null;
}

export class TradingPlanStore {
  private plans: TradingPlanEntry[] = [];
  private idCounter = 0;
  private readonly maxPlans: number;
  private readonly dataDir: string | null;

  constructor(opts: TradingPlanStoreOptions = {}) {
    this.maxPlans = opts.maxPlans ?? 2000;
    this.dataDir = opts.dataDir ?? null;
    this.loadFromDisk();
  }

  /** Create a new trading plan. Returns the plan ID. */
  createPlan(params: {
    date?: string;
    symbol: string;
    direction: "buy" | "sell";
    entryTrigger: string;
    entryPrice?: number;
    stopPrice?: number;
    targetPrice?: number;
    maxPosition?: number;
    sizingMethod?: string;
    reason: string;
    strategyId?: string;
    tags?: string[];
  }): string {
    if (this.plans.length >= this.maxPlans) {
      this.plans = this.plans.slice(Math.floor(this.maxPlans * 0.1));
    }

    const now = new Date().toISOString();
    const id = `tp-${String(++this.idCounter).padStart(5, "0")}`;

    let riskRewardRatio: number | undefined;
    if (params.entryPrice && params.stopPrice && params.targetPrice) {
      const risk = Math.abs(params.entryPrice - params.stopPrice);
      const reward = Math.abs(params.targetPrice - params.entryPrice);
      riskRewardRatio = risk > 0 ? Number((reward / risk).toFixed(2)) : undefined;
    }

    const plan: TradingPlanEntry = {
      id,
      date: params.date ?? now.slice(0, 10),
      symbol: params.symbol,
      direction: params.direction,
      entryTrigger: params.entryTrigger,
      entryPrice: params.entryPrice,
      stopPrice: params.stopPrice,
      targetPrice: params.targetPrice,
      maxPosition: params.maxPosition,
      sizingMethod: params.sizingMethod,
      reason: params.reason,
      riskRewardRatio,
      status: "planned",
      strategyId: params.strategyId,
      linkedOrderIds: [],
      tags: params.tags ?? [],
      createdAt: now,
      updatedAt: now,
    };

    this.plans.push(plan);
    this.saveToDisk();
    return id;
  }

  /** Update plan status. */
  updateStatus(id: string, status: PlanStatus): boolean {
    const plan = this.plans.find((p) => p.id === id);
    if (!plan) return false;
    plan.status = status;
    plan.updatedAt = new Date().toISOString();
    this.saveToDisk();
    return true;
  }

  /** Update plan fields. */
  updatePlan(
    id: string,
    patch: Partial<
      Pick<
        TradingPlanEntry,
        | "entryTrigger"
        | "entryPrice"
        | "stopPrice"
        | "targetPrice"
        | "maxPosition"
        | "reason"
        | "status"
        | "tags"
        | "actualEntryPrice"
        | "actualExitPrice"
        | "actualPnl"
        | "reviewNotes"
        | "reviewRating"
      >
    >,
  ): boolean {
    const plan = this.plans.find((p) => p.id === id);
    if (!plan) return false;
    for (const [key, value] of Object.entries(patch)) {
      if (value !== undefined) {
        (plan as unknown as Record<string, unknown>)[key] = value;
      }
    }
    // Recalculate risk-reward if prices changed
    const ep = patch.entryPrice ?? plan.entryPrice;
    const sp = patch.stopPrice ?? plan.stopPrice;
    const tp = patch.targetPrice ?? plan.targetPrice;
    if (ep && sp && tp) {
      const risk = Math.abs(ep - sp);
      const reward = Math.abs(tp - ep);
      plan.riskRewardRatio = risk > 0 ? Number((reward / risk).toFixed(2)) : undefined;
    }
    plan.updatedAt = new Date().toISOString();
    this.saveToDisk();
    return true;
  }

  /** Link an order ID to a plan. */
  linkOrder(planId: string, orderId: string): boolean {
    const plan = this.plans.find((p) => p.id === planId);
    if (!plan) return false;
    if (!plan.linkedOrderIds.includes(orderId)) {
      plan.linkedOrderIds.push(orderId);
    }
    if (plan.status === "planned" || plan.status === "triggered") {
      plan.status = "executed";
    }
    plan.updatedAt = new Date().toISOString();
    this.saveToDisk();
    return true;
  }

  /** Delete a plan. */
  deletePlan(id: string): boolean {
    const idx = this.plans.findIndex((p) => p.id === id);
    if (idx < 0) return false;
    this.plans.splice(idx, 1);
    this.saveToDisk();
    return true;
  }

  /** Get a plan by ID. */
  getPlan(id: string): TradingPlanEntry | undefined {
    return this.plans.find((p) => p.id === id);
  }

  /** Get plans for a specific date. */
  getByDate(date: string): TradingPlanEntry[] {
    return this.plans.filter((p) => p.date === date);
  }

  /** Get plans for a specific symbol. */
  getBySymbol(symbol: string, limit = 20): TradingPlanEntry[] {
    return this.plans.filter((p) => p.symbol === symbol).slice(-limit);
  }

  /** Get active (non-terminal) plans. */
  getActive(): TradingPlanEntry[] {
    return this.plans.filter((p) => p.status === "planned" || p.status === "triggered");
  }

  /** Get recent plans (newest first). */
  getRecent(limit = 20): TradingPlanEntry[] {
    return this.plans.slice(-limit).reverse();
  }

  /** Expire old plans that are still in "planned" status. */
  expireOldPlans(beforeDate: string): number {
    let count = 0;
    for (const plan of this.plans) {
      if (plan.status === "planned" && plan.date < beforeDate) {
        plan.status = "expired";
        plan.updatedAt = new Date().toISOString();
        count++;
      }
    }
    if (count > 0) this.saveToDisk();
    return count;
  }

  /** Compute discipline metrics for a date range. */
  getDisciplineMetrics(fromDate?: string, toDate?: string): {
    totalPlans: number;
    executedCount: number;
    cancelledCount: number;
    expiredCount: number;
    reviewedCount: number;
    executionRate: number;
    avgRiskReward: number;
    avgReviewRating: number;
    avgPriceDeviation: number;
    winRate: number;
  } {
    let filtered = this.plans;
    if (fromDate) filtered = filtered.filter((p) => p.date >= fromDate);
    if (toDate) filtered = filtered.filter((p) => p.date <= toDate);

    const totalPlans = filtered.length;
    const executedCount = filtered.filter(
      (p) => p.status === "executed" || p.status === "reviewed",
    ).length;
    const cancelledCount = filtered.filter((p) => p.status === "cancelled").length;
    const expiredCount = filtered.filter((p) => p.status === "expired").length;
    const reviewedCount = filtered.filter((p) => p.status === "reviewed").length;
    const executionRate = totalPlans > 0 ? executedCount / totalPlans : 0;

    const withRR = filtered.filter((p) => p.riskRewardRatio != null);
    const avgRiskReward =
      withRR.length > 0
        ? withRR.reduce((s, p) => s + p.riskRewardRatio!, 0) / withRR.length
        : 0;

    const withRating = filtered.filter((p) => p.reviewRating != null);
    const avgReviewRating =
      withRating.length > 0
        ? withRating.reduce((s, p) => s + p.reviewRating!, 0) / withRating.length
        : 0;

    // Price deviation: how far actual entry was from planned entry
    const withBothPrices = filtered.filter(
      (p) => p.entryPrice != null && p.actualEntryPrice != null,
    );
    const avgPriceDeviation =
      withBothPrices.length > 0
        ? withBothPrices.reduce((s, p) => {
            const dev = Math.abs(p.actualEntryPrice! - p.entryPrice!) / p.entryPrice!;
            return s + dev;
          }, 0) / withBothPrices.length
        : 0;

    const withPnl = filtered.filter((p) => p.actualPnl != null);
    const wins = withPnl.filter((p) => p.actualPnl! > 0).length;
    const winRate = withPnl.length > 0 ? wins / withPnl.length : 0;

    return {
      totalPlans,
      executedCount,
      cancelledCount,
      expiredCount,
      reviewedCount,
      executionRate: Number(executionRate.toFixed(4)),
      avgRiskReward: Number(avgRiskReward.toFixed(2)),
      avgReviewRating: Number(avgReviewRating.toFixed(1)),
      avgPriceDeviation: Number(avgPriceDeviation.toFixed(4)),
      winRate: Number(winRate.toFixed(4)),
    };
  }

  get size(): number {
    return this.plans.length;
  }

  // ── Persistence ────────────────────────────────────────────

  private persistPath(): string | null {
    if (!this.dataDir) return null;
    return `${this.dataDir}/trading-plans.json`;
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
        plans: TradingPlanEntry[];
      };
      this.idCounter = data.idCounter ?? 0;
      this.plans = data.plans ?? [];
    } catch {
      // Ignore corrupt data
    }
  }
}
