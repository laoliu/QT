/**
 * trading_plan tool — AI-callable for creating and tracking trade plans.
 *
 * A disciplined stock trader plans trades before executing them:
 * - Entry price, stop-loss, take-profit targets
 * - Position size, reason/thesis
 * - Track execution vs plan
 * - Review plan outcomes
 *
 * Actions:
 *   create     — create a new trading plan
 *   list       — list active plans
 *   update     — update plan status (executed, cancelled, partial)
 *   review     — review completed plans with actual outcomes
 *   delete     — remove a plan
 *   stats      — plan execution statistics
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { QuantTradingConfig } from "../types.js";

interface TradingPlan {
  id: string;
  symbol: string;
  name: string;
  direction: "buy" | "sell" | "short" | "cover";
  status: "planned" | "executed" | "partial" | "cancelled" | "expired";
  // Plan details
  entryPrice: number;
  stopLoss: number;
  takeProfit: number;
  quantity: number;
  positionPct: number | null; // target weight in portfolio
  // Execution details
  actualEntryPrice: number | null;
  actualQuantity: number | null;
  executedAt: string | null;
  // Outcome tracking
  exitPrice: number | null;
  pnl: number | null;
  pnlPct: number | null;
  // Context
  thesis: string;
  timeframe: string; // "1d", "1w", "1m", "3m"
  tags: string[];
  createdAt: string;
  updatedAt: string;
  expiresAt: string | null;
}

export function createTradingPlanTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  const plansPath = path.join(dir, "trading-plans.json");

  async function loadPlans(): Promise<TradingPlan[]> {
    try {
      const raw = await fs.readFile(plansPath, "utf-8");
      return JSON.parse(raw) as TradingPlan[];
    } catch {
      return [];
    }
  }

  async function savePlans(plans: TradingPlan[]): Promise<void> {
    await fs.mkdir(dir, { recursive: true });
    await fs.writeFile(plansPath, JSON.stringify(plans, null, 2));
  }

  function generateId(): string {
    return `plan_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  }

  function computeRiskReward(plan: TradingPlan): { riskPct: number; rewardPct: number; ratio: number } | null {
    if (plan.entryPrice <= 0) return null;
    const riskPct = Math.abs((plan.entryPrice - plan.stopLoss) / plan.entryPrice) * 100;
    const rewardPct = Math.abs((plan.takeProfit - plan.entryPrice) / plan.entryPrice) * 100;
    const ratio = riskPct > 0 ? rewardPct / riskPct : 0;
    return { riskPct: Number(riskPct.toFixed(2)), rewardPct: Number(rewardPct.toFixed(2)), ratio: Number(ratio.toFixed(2)) };
  }

  return {
    name: "trading_plan",
    description:
      "交易计划管理工具。制定交易计划（入场价/止损/止盈/仓位），跟踪执行情况，复盘计划效果。" +
      "action=create 创建计划, action=list 查看活跃计划, action=update 更新状态, " +
      "action=review 复盘已完成计划, action=delete 删除计划, action=stats 计划执行统计。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["create", "list", "update", "review", "delete", "stats"],
          description: "操作类型",
        },
        symbol: { type: "string", description: "股票代码" },
        name: { type: "string", description: "股票名称" },
        direction: {
          type: "string",
          enum: ["buy", "sell", "short", "cover"],
          description: "交易方向",
        },
        entry_price: { type: "number", description: "计划入场价" },
        stop_loss: { type: "number", description: "止损价" },
        take_profit: { type: "number", description: "止盈价" },
        quantity: { type: "number", description: "计划数量" },
        position_pct: { type: "number", description: "目标仓位比例 (%)" },
        thesis: { type: "string", description: "交易逻辑/理由" },
        timeframe: { type: "string", description: "持仓周期: 1d/1w/1m/3m" },
        tags: {
          type: "array",
          items: { type: "string" },
          description: "标签",
        },
        plan_id: { type: "string", description: "计划ID（update/delete）" },
        status: {
          type: "string",
          enum: ["executed", "partial", "cancelled", "expired"],
          description: "新状态（update时）",
        },
        actual_entry_price: { type: "number", description: "实际入场价" },
        actual_quantity: { type: "number", description: "实际成交数量" },
        exit_price: { type: "number", description: "实际出场价" },
        pnl: { type: "number", description: "实际盈亏金额" },
        expires_in_days: { type: "number", description: "计划有效期（天数，默认7天）" },
        limit: { type: "number", description: "返回数量" },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      let plans = await loadPlans();

      switch (action) {
        case "create": {
          const symbol = args.symbol as string;
          const entryPrice = args.entry_price as number;
          const stopLoss = args.stop_loss as number;
          const takeProfit = args.take_profit as number;
          if (!symbol || !entryPrice || !stopLoss || !takeProfit) {
            return { error: "请提供 symbol, entry_price, stop_loss, take_profit" };
          }

          const expiresInDays = (args.expires_in_days as number) ?? 7;
          const now = new Date();
          const plan: TradingPlan = {
            id: generateId(),
            symbol: symbol.trim().toUpperCase(),
            name: (args.name as string) ?? symbol,
            direction: (args.direction as TradingPlan["direction"]) ?? "buy",
            status: "planned",
            entryPrice,
            stopLoss,
            takeProfit,
            quantity: (args.quantity as number) ?? 100,
            positionPct: (args.position_pct as number) ?? null,
            actualEntryPrice: null,
            actualQuantity: null,
            executedAt: null,
            exitPrice: null,
            pnl: null,
            pnlPct: null,
            thesis: (args.thesis as string) ?? "",
            timeframe: (args.timeframe as string) ?? "1w",
            tags: (args.tags as string[]) ?? [],
            createdAt: now.toISOString(),
            updatedAt: now.toISOString(),
            expiresAt: new Date(now.getTime() + expiresInDays * 24 * 60 * 60 * 1000).toISOString(),
          };

          const rr = computeRiskReward(plan);
          plans.push(plan);
          await savePlans(plans);

          return {
            action: "create",
            plan: {
              id: plan.id,
              symbol: plan.symbol,
              name: plan.name,
              direction: plan.direction,
              entryPrice: plan.entryPrice,
              stopLoss: plan.stopLoss,
              takeProfit: plan.takeProfit,
              quantity: plan.quantity,
              riskReward: rr,
              thesis: plan.thesis,
              timeframe: plan.timeframe,
              expiresAt: plan.expiresAt,
            },
          };
        }

        case "list": {
          const activeStatuses = ["planned", "partial"];
          const now = new Date();

          // Auto-expire old plans
          let changed = false;
          for (const p of plans) {
            if (
              activeStatuses.includes(p.status) &&
              p.expiresAt &&
              new Date(p.expiresAt) < now
            ) {
              p.status = "expired";
              p.updatedAt = now.toISOString();
              changed = true;
            }
          }
          if (changed) await savePlans(plans);

          const active = plans.filter((p) => activeStatuses.includes(p.status));
          return {
            action: "list",
            count: active.length,
            plans: active.map((p) => ({
              id: p.id,
              symbol: p.symbol,
              name: p.name,
              direction: p.direction,
              status: p.status,
              entryPrice: p.entryPrice,
              stopLoss: p.stopLoss,
              takeProfit: p.takeProfit,
              quantity: p.quantity,
              riskReward: computeRiskReward(p),
              thesis: p.thesis,
              timeframe: p.timeframe,
              expiresAt: p.expiresAt,
              createdAt: p.createdAt,
            })),
          };
        }

        case "update": {
          const planId = args.plan_id as string;
          if (!planId) return { error: "请提供 plan_id" };

          const plan = plans.find((p) => p.id === planId);
          if (!plan) return { error: `未找到计划 ${planId}` };

          if (args.status) plan.status = args.status as TradingPlan["status"];
          if (args.actual_entry_price != null) plan.actualEntryPrice = args.actual_entry_price as number;
          if (args.actual_quantity != null) plan.actualQuantity = args.actual_quantity as number;
          if (args.exit_price != null) plan.exitPrice = args.exit_price as number;
          if (args.pnl != null) plan.pnl = args.pnl as number;

          // Compute pnl percentage
          if (plan.exitPrice != null && plan.actualEntryPrice != null && plan.actualEntryPrice > 0) {
            plan.pnlPct = ((plan.exitPrice - plan.actualEntryPrice) / plan.actualEntryPrice) * 100;
            if (plan.direction === "sell" || plan.direction === "short") {
              plan.pnlPct = -plan.pnlPct;
            }
          }

          if (plan.status === "executed" && !plan.executedAt) {
            plan.executedAt = new Date().toISOString();
          }

          plan.updatedAt = new Date().toISOString();
          await savePlans(plans);

          return { action: "update", plan };
        }

        case "review": {
          const limit = (args.limit as number) ?? 20;
          const completed = plans
            .filter((p) => ["executed", "cancelled", "expired"].includes(p.status))
            .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime())
            .slice(0, limit);

          return {
            action: "review",
            count: completed.length,
            plans: completed.map((p) => ({
              id: p.id,
              symbol: p.symbol,
              name: p.name,
              direction: p.direction,
              status: p.status,
              entryPrice: p.entryPrice,
              actualEntryPrice: p.actualEntryPrice,
              exitPrice: p.exitPrice,
              pnl: p.pnl,
              pnlPct: p.pnlPct != null ? Number(p.pnlPct.toFixed(2)) : null,
              executedVsPlan: p.actualEntryPrice != null
                ? { deviation: Number(((p.actualEntryPrice - p.entryPrice) / p.entryPrice * 100).toFixed(2)) }
                : null,
              thesis: p.thesis,
              createdAt: p.createdAt,
              executedAt: p.executedAt,
            })),
          };
        }

        case "delete": {
          const planId = args.plan_id as string;
          if (!planId) return { error: "请提供 plan_id" };
          const before = plans.length;
          plans = plans.filter((p) => p.id !== planId);
          if (plans.length === before) return { error: `未找到计划 ${planId}` };
          await savePlans(plans);
          return { action: "delete", deleted: planId };
        }

        case "stats": {
          const executed = plans.filter((p) => p.status === "executed");
          const cancelled = plans.filter((p) => p.status === "cancelled");
          const expired = plans.filter((p) => p.status === "expired");
          const active = plans.filter((p) => p.status === "planned" || p.status === "partial");

          const withPnl = executed.filter((p) => p.pnlPct != null);
          const winners = withPnl.filter((p) => (p.pnlPct ?? 0) > 0);
          const losers = withPnl.filter((p) => (p.pnlPct ?? 0) < 0);

          const avgWin = winners.length > 0
            ? Number((winners.reduce((s, p) => s + (p.pnlPct ?? 0), 0) / winners.length).toFixed(2))
            : 0;
          const avgLoss = losers.length > 0
            ? Number((losers.reduce((s, p) => s + (p.pnlPct ?? 0), 0) / losers.length).toFixed(2))
            : 0;

          // Execution discipline — how closely actual prices match planned prices
          const withActual = executed.filter((p) => p.actualEntryPrice != null && p.entryPrice > 0);
          const avgDeviation = withActual.length > 0
            ? Number((withActual.reduce((s, p) => s + Math.abs((p.actualEntryPrice! - p.entryPrice) / p.entryPrice * 100), 0) / withActual.length).toFixed(2))
            : null;

          return {
            action: "stats",
            total: plans.length,
            active: active.length,
            executed: executed.length,
            cancelled: cancelled.length,
            expired: expired.length,
            executionRate: plans.length > 0
              ? Number(((executed.length / (executed.length + cancelled.length + expired.length || 1)) * 100).toFixed(1))
              : 0,
            winRate: withPnl.length > 0
              ? Number(((winners.length / withPnl.length) * 100).toFixed(1))
              : null,
            avgWinPct: avgWin,
            avgLossPct: avgLoss,
            profitFactor: avgLoss !== 0
              ? Number((Math.abs(avgWin / avgLoss)).toFixed(2))
              : null,
            avgExecutionDeviation: avgDeviation,
          };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
