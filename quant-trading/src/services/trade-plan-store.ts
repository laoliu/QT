/**
 * Trade Plan Store — persistence layer for daily trade execution plans.
 */

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
