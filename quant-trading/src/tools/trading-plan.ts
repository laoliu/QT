/**
 * trading_plan tool — AI-callable for structured pre-trade plan management.
 *
 * Actions:
 *   create     — Create a new trading plan
 *   list       — List active trading plans
 *   update     — Update plan status or details
 *   close      — Close a plan (executed, cancelled, expired)
 *   review     — Compare plan vs actual execution
 *   history    — View closed/historical plans
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { QuantTradingConfig } from "../types.js";

interface TradingPlan {
  id: string;
  symbol: string;
  name?: string;
  direction: "buy" | "sell";
  /** Entry zone: [low, high] */
  entryZone: [number, number];
  stopLoss: number;
  takeProfit: number;
  /** Position size in shares or percentage */
  positionSize: string;
  /** Why this trade */
  reasoning: string;
  /** Strategy basis */
  strategy?: string;
  /** Risk-reward ratio */
  riskReward?: number;
  /** Expiry date (plan becomes stale) */
  expiresAt?: string;
  /** Plan status */
  status: "active" | "triggered" | "executed" | "cancelled" | "expired";
  /** Actual execution details (filled in after execution) */
  execution?: {
    entryPrice?: number;
    entryDate?: string;
    exitPrice?: number;
    exitDate?: string;
    actualPnl?: number;
    followedPlan?: boolean;
    notes?: string;
  };
  /** Tags for filtering */
  tags?: string[];
  createdAt: string;
  updatedAt: string;
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
    await fs.writeFile(plansPath, JSON.stringify(plans, null, 2), "utf-8");
  }

  return {
    name: "trading_plan",
    description:
      "交易计划管理工具。创建结构化的交易计划，包含入场区间、止损止盈、仓位和理由。\n" +
      "• action=create — 创建新交易计划\n" +
      "• action=list — 查看当前活跃计划\n" +
      "• action=update — 更新计划（状态/执行详情）\n" +
      "• action=close — 关闭计划（执行/取消/过期）\n" +
      "• action=review — 复盘：对比计划 vs 实际执行\n" +
      "• action=history — 查看历史计划",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["create", "list", "update", "close", "review", "history"],
          description:
            "操作类型: create=创建, list=列表, update=更新, close=关闭, review=复盘, history=历史",
        },
        // create parameters
        symbol: {
          type: "string",
          description: "股票代码，如 600519",
        },
        name: {
          type: "string",
          description: "股票名称（可选）",
        },
        direction: {
          type: "string",
          enum: ["buy", "sell"],
          description: "交易方向: buy=买入, sell=卖出",
        },
        entry_low: {
          type: "number",
          description: "入场区间下限",
        },
        entry_high: {
          type: "number",
          description: "入场区间上限",
        },
        stop_loss: {
          type: "number",
          description: "止损价",
        },
        take_profit: {
          type: "number",
          description: "止盈价（目标价）",
        },
        position_size: {
          type: "string",
          description: "仓位大小，如 '1000股' 或 '10%'",
        },
        reasoning: {
          type: "string",
          description: "交易理由/逻辑",
        },
        strategy: {
          type: "string",
          description: "策略依据（可选），如 '均线金叉+放量突破'",
        },
        expires_at: {
          type: "string",
          description: "计划有效期（可选），格式 YYYY-MM-DD",
        },
        tags: {
          type: "array",
          items: { type: "string" },
          description: "标签（可选），如 [\"短线\", \"半导体\"]",
        },
        // update/close parameters
        plan_id: {
          type: "string",
          description: "计划ID（update/close/review时需要）",
        },
        status: {
          type: "string",
          enum: ["active", "triggered", "executed", "cancelled", "expired"],
          description: "计划状态（update/close时使用）",
        },
        entry_price: {
          type: "number",
          description: "实际入场价（update执行详情时使用）",
        },
        exit_price: {
          type: "number",
          description: "实际出场价（close时使用）",
        },
        actual_pnl: {
          type: "number",
          description: "实际盈亏金额（close时使用）",
        },
        followed_plan: {
          type: "boolean",
          description: "是否严格执行了计划（review时使用）",
        },
        notes: {
          type: "string",
          description: "备注",
        },
        // filter parameters
        filter_status: {
          type: "string",
          description: "按状态筛选（list/history时使用）",
        },
        filter_symbol: {
          type: "string",
          description: "按股票代码筛选",
        },
        limit: {
          type: "number",
          description: "返回数量限制，默认20",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "list";
      const plans = await loadPlans();
      const now = new Date().toISOString();

      switch (action) {
        case "create": {
          const symbol = args.symbol as string;
          if (!symbol) return { error: "请提供 symbol 参数" };

          const entryLow = args.entry_low as number;
          const entryHigh = args.entry_high as number;
          const stopLoss = args.stop_loss as number;
          const takeProfit = args.take_profit as number;

          if (!entryLow || !entryHigh || !stopLoss || !takeProfit) {
            return {
              error: "请提供完整的入场区间(entry_low/entry_high)、止损(stop_loss)和止盈(take_profit)",
            };
          }

          // Calculate risk-reward ratio
          const midEntry = (entryLow + entryHigh) / 2;
          const risk = Math.abs(midEntry - stopLoss);
          const reward = Math.abs(takeProfit - midEntry);
          const riskReward = risk > 0 ? Math.round((reward / risk) * 100) / 100 : 0;

          const plan: TradingPlan = {
            id: `plan_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
            symbol,
            name: args.name as string | undefined,
            direction: (args.direction as "buy" | "sell") ?? "buy",
            entryZone: [entryLow, entryHigh],
            stopLoss,
            takeProfit,
            positionSize: (args.position_size as string) ?? "待定",
            reasoning: (args.reasoning as string) ?? "",
            strategy: args.strategy as string | undefined,
            riskReward,
            expiresAt: args.expires_at as string | undefined,
            status: "active",
            tags: args.tags as string[] | undefined,
            createdAt: now,
            updatedAt: now,
          };

          plans.push(plan);
          await savePlans(plans);

          return {
            message: "交易计划创建成功",
            plan: {
              id: plan.id,
              symbol: plan.symbol,
              name: plan.name,
              direction: plan.direction,
              entryZone: plan.entryZone,
              stopLoss: plan.stopLoss,
              takeProfit: plan.takeProfit,
              positionSize: plan.positionSize,
              riskReward: plan.riskReward,
              reasoning: plan.reasoning,
              strategy: plan.strategy,
              expiresAt: plan.expiresAt,
            },
            riskRewardNote:
              riskReward >= 2
                ? `盈亏比 ${riskReward}:1 ✓ 符合2:1最低要求`
                : `⚠️ 盈亏比 ${riskReward}:1 低于2:1标准，建议调整止盈或止损`,
          };
        }

        case "list": {
          const filterStatus = args.filter_status as string | undefined;
          const filterSymbol = args.filter_symbol as string | undefined;
          const limit = (args.limit as number) ?? 20;

          let filtered = plans.filter((p) => p.status === "active" || p.status === "triggered");

          if (filterStatus) {
            filtered = plans.filter((p) => p.status === filterStatus);
          }
          if (filterSymbol) {
            filtered = filtered.filter((p) => p.symbol === filterSymbol);
          }

          // Auto-expire plans
          const today = now.slice(0, 10);
          for (const p of filtered) {
            if (p.expiresAt && p.expiresAt < today && p.status === "active") {
              p.status = "expired";
              p.updatedAt = now;
            }
          }
          await savePlans(plans);

          filtered = filtered.filter((p) => p.status !== "expired" || filterStatus === "expired");

          return {
            count: filtered.length,
            plans: filtered.slice(0, limit).map((p) => ({
              id: p.id,
              symbol: p.symbol,
              name: p.name,
              direction: p.direction,
              entryZone: p.entryZone,
              stopLoss: p.stopLoss,
              takeProfit: p.takeProfit,
              positionSize: p.positionSize,
              riskReward: p.riskReward,
              status: p.status,
              strategy: p.strategy,
              reasoning: p.reasoning,
              expiresAt: p.expiresAt,
              tags: p.tags,
              createdAt: p.createdAt,
            })),
          };
        }

        case "update": {
          const planId = args.plan_id as string;
          if (!planId) return { error: "请提供 plan_id 参数" };

          const plan = plans.find((p) => p.id === planId);
          if (!plan) return { error: `未找到计划 ${planId}` };

          if (args.status) plan.status = args.status as TradingPlan["status"];
          if (args.notes) {
            if (!plan.execution) plan.execution = {};
            plan.execution.notes = args.notes as string;
          }
          if (args.entry_price) {
            if (!plan.execution) plan.execution = {};
            plan.execution.entryPrice = args.entry_price as number;
            plan.execution.entryDate = now.slice(0, 10);
          }
          plan.updatedAt = now;

          await savePlans(plans);
          return { message: "计划已更新", plan };
        }

        case "close": {
          const planId = args.plan_id as string;
          if (!planId) return { error: "请提供 plan_id 参数" };

          const plan = plans.find((p) => p.id === planId);
          if (!plan) return { error: `未找到计划 ${planId}` };

          plan.status = (args.status as TradingPlan["status"]) ?? "executed";
          if (args.exit_price || args.actual_pnl) {
            if (!plan.execution) plan.execution = {};
            if (args.exit_price) {
              plan.execution.exitPrice = args.exit_price as number;
              plan.execution.exitDate = now.slice(0, 10);
            }
            if (args.actual_pnl) plan.execution.actualPnl = args.actual_pnl as number;
            if (args.followed_plan != null) {
              plan.execution.followedPlan = args.followed_plan as boolean;
            }
          }
          if (args.notes) {
            if (!plan.execution) plan.execution = {};
            plan.execution.notes = args.notes as string;
          }
          plan.updatedAt = now;

          await savePlans(plans);
          return { message: "计划已关闭", plan };
        }

        case "review": {
          const planId = args.plan_id as string;
          if (planId) {
            const plan = plans.find((p) => p.id === planId);
            if (!plan) return { error: `未找到计划 ${planId}` };
            return {
              plan,
              review: {
                hadPlan: true,
                followedPlan: plan.execution?.followedPlan ?? null,
                plannedEntry: plan.entryZone,
                actualEntry: plan.execution?.entryPrice ?? null,
                plannedStopLoss: plan.stopLoss,
                plannedTakeProfit: plan.takeProfit,
                actualExit: plan.execution?.exitPrice ?? null,
                pnl: plan.execution?.actualPnl ?? null,
                plannedRiskReward: plan.riskReward,
              },
            };
          }

          // General review: stats of all closed plans
          const closed = plans.filter(
            (p) => p.status === "executed" || p.status === "cancelled" || p.status === "expired",
          );
          const executed = closed.filter((p) => p.status === "executed");
          const withPnl = executed.filter((p) => p.execution?.actualPnl != null);
          const wins = withPnl.filter((p) => (p.execution?.actualPnl ?? 0) > 0);
          const followed = executed.filter((p) => p.execution?.followedPlan === true);

          return {
            totalPlans: plans.length,
            activePlans: plans.filter((p) => p.status === "active").length,
            executedPlans: executed.length,
            cancelledPlans: closed.filter((p) => p.status === "cancelled").length,
            expiredPlans: closed.filter((p) => p.status === "expired").length,
            winRate:
              withPnl.length > 0
                ? `${Math.round((wins.length / withPnl.length) * 100)}%`
                : "暂无数据",
            planFollowRate:
              executed.length > 0
                ? `${Math.round((followed.length / executed.length) * 100)}%`
                : "暂无数据",
            totalPnl: withPnl.reduce((sum, p) => sum + (p.execution?.actualPnl ?? 0), 0),
          };
        }

        case "history": {
          const limit = (args.limit as number) ?? 20;
          const filterSymbol = args.filter_symbol as string | undefined;

          let closed = plans.filter(
            (p) => p.status === "executed" || p.status === "cancelled" || p.status === "expired",
          );

          if (filterSymbol) {
            closed = closed.filter((p) => p.symbol === filterSymbol);
          }

          // Sort by updatedAt descending
          closed.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));

          return {
            count: closed.length,
            plans: closed.slice(0, limit),
          };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
