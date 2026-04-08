/**
 * TradingPlanService — 0607
 *
 * 功能:
 *  - 创建/б/更新/完成/0607
 *  - 0404 JSON 04 (SQLite 级别可后续升级)
 *  - 01020709 (06 vs 0701)
 */
import * as fs from "node:fs/promises";
import * as path from "node:path";
import type { QuantTradingConfig } from "../types.js";

// ── Types ────────────────────────────────────────────────────

export type PlanStatus = "draft" | "active" | "completed" | "cancelled";

export interface TradingPlan {
  planId: string;
  /** 计划名称 */
  name: string;
  /** 标的代码 */
  symbol: string;
  /** 方向 */
  side: "buy" | "sell";
  /** 目标买入/卖出价位 */
  targetPrice?: number;
  /** 09 */
  stopLoss?: number;
  /** 0907 */
  takeProfit?: number;
  /** 计划买入数量 */
  quantity?: number;
  /** 计划买入金额 */
  amount?: number;
  /** 交易理由 / 18 */
  rationale?: string;
  /** 时间框架 (如 "短线1-3天", "波段1-2周") */
  timeframe?: string;
  /** 操作纪律检查清单 */
  checklist?: string[];
  /** 0800 */
  status: PlanStatus;
  /** 0702 */
  createdAt: string;
  /** 0702 */
  updatedAt: string;
  /** 完成时间 */
  completedAt?: string;
  /** 0503 */
  reviewNote?: string;
  /** 0604 */
  actualResult?: {
    executedPrice?: number;
    executedQuantity?: number;
    pnl?: number;
    note?: string;
  };
}

// ── Service ──────────────────────────────────────────────────

export class TradingPlanService {
  private plans: TradingPlan[] = [];
  private dataDir: string;

  constructor(config: QuantTradingConfig) {
    this.dataDir = config.dataDir ?? ".openclaw/quant-trading";
  }

  private filePath(): string {
    return path.join(this.dataDir, "trading-plans.json");
  }

  async load(): Promise<void> {
    try {
      const raw = await fs.readFile(this.filePath(), "utf-8");
      this.plans = JSON.parse(raw) as TradingPlan[];
    } catch {
      this.plans = [];
    }
  }

  private async save(): Promise<void> {
    await fs.mkdir(this.dataDir, { recursive: true });
    await fs.writeFile(this.filePath(), JSON.stringify(this.plans, null, 2));
  }

  /** 050607 */
  async create(input: Omit<TradingPlan, "planId" | "status" | "createdAt" | "updatedAt">): Promise<TradingPlan> {
    const plan: TradingPlan = {
      ...input,
      planId: `plan_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
      status: "draft",
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
    this.plans.push(plan);
    await this.save();
    return plan;
  }

  /** 001507 (可选过滤) */
  list(filter?: { status?: PlanStatus; symbol?: string }): TradingPlan[] {
    let result = [...this.plans];
    if (filter?.status) result = result.filter((p) => p.status === filter.status);
    if (filter?.symbol) result = result.filter((p) => p.symbol === filter.symbol);
    return result.sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  }

  /** 0007 */
  get(planId: string): TradingPlan | undefined {
    return this.plans.find((p) => p.planId === planId);
  }

  /** 0407 */
  async update(planId: string, updates: Partial<TradingPlan>): Promise<TradingPlan | null> {
    const plan = this.plans.find((p) => p.planId === planId);
    if (!plan) return null;
    Object.assign(plan, updates, { updatedAt: new Date().toISOString() });
    await this.save();
    return plan;
  }

  /** 激活计划 */
  async activate(planId: string): Promise<TradingPlan | null> {
    return this.update(planId, { status: "active" });
  }

  /** 0907 */
  async complete(planId: string, result?: TradingPlan["actualResult"]): Promise<TradingPlan | null> {
    return this.update(planId, {
      status: "completed",
      completedAt: new Date().toISOString(),
      actualResult: result,
    });
  }

  /** 0007 */
  async cancel(planId: string, reason?: string): Promise<TradingPlan | null> {
    return this.update(planId, {
      status: "cancelled",
      reviewNote: reason,
    });
  }

  /** 060503 */
  async review(planId: string, note: string): Promise<TradingPlan | null> {
    return this.update(planId, { reviewNote: note });
  }

  /** 0107 */
  async remove(planId: string): Promise<boolean> {
    const idx = this.plans.findIndex((p) => p.planId === planId);
    if (idx < 0) return false;
    this.plans.splice(idx, 1);
    await this.save();
    return true;
  }

  /** 0009l — 0607 vs 06040301 */
  getDisciplineReport(): {
    totalPlans: number;
    activePlans: number;
    completedPlans: number;
    adherenceRate: number;
    violations: string[];
  } {
    const completed = this.plans.filter((p) => p.status === "completed" && p.actualResult);
    const violations: string[] = [];

    for (const plan of completed) {
      const r = plan.actualResult!;
      if (plan.targetPrice && r.executedPrice) {
        const deviation = Math.abs(r.executedPrice - plan.targetPrice) / plan.targetPrice;
        if (deviation > 0.05) {
          violations.push(`${plan.symbol}: 04150107 ${(deviation * 100).toFixed(1)}%`);
        }
      }
      if (plan.stopLoss && r.executedPrice && r.executedPrice < plan.stopLoss) {
        violations.push(`${plan.symbol}: 04151509 06${plan.stopLoss}`);
      }
    }

    return {
      totalPlans: this.plans.length,
      activePlans: this.plans.filter((p) => p.status === "active").length,
      completedPlans: completed.length,
      adherenceRate: completed.length > 0
        ? (completed.length - violations.length) / completed.length
        : 0,
      violations,
    };
  }
}
