/**
 * trade_plan tool — AI-callable interface for creating a complete trade plan.
 *
 * A trade plan combines entry + stop-loss + take-profit in one atomic action,
 * which is the most natural way a trader thinks about a trade.
 *
 * Actions:
 *   create   — Create a trade plan (buy + auto-set stop-loss and take-profit)
 *   list     — List all active trade plans
 *   close    — Manually close a trade plan (sell the position)
 *   detail   — View details of a specific plan
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { ConditionalOrderService } from "../services/conditional-order.js";
import type { QuantTradingConfig } from "../types.js";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

interface TradePlan {
  id: string;
  symbol: string;
  name?: string;
  side: "long" | "short";
  entryPrice: number;
  quantity: number;
  stopLossPrice?: number;
  takeProfitPrice?: number;
  trailingStopPct?: number;
  stopLossOrderId?: string;
  takeProfitOrderId?: string;
  trailingStopOrderId?: string;
  riskRewardRatio?: number;
  riskAmount?: number;
  status: "active" | "stopped_out" | "profit_taken" | "closed" | "cancelled";
  notes?: string;
  createdAt: string;
  closedAt?: string;
  closedPrice?: number;
  pnl?: number;
}

function getPlansPath(config: QuantTradingConfig): string {
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  return path.join(dir, "trade-plans.json");
}

async function loadPlans(config: QuantTradingConfig): Promise<TradePlan[]> {
  try {
    const raw = await fs.readFile(getPlansPath(config), "utf-8");
    return JSON.parse(raw) as TradePlan[];
  } catch {
    return [];
  }
}

async function savePlans(plans: TradePlan[], config: QuantTradingConfig): Promise<void> {
  const filePath = getPlansPath(config);
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  await fs.writeFile(filePath, JSON.stringify(plans, null, 2));
}

let planCounter = 0;

export function createTradePlanTool(
  _api: OpenClawPluginApi,
  config: QuantTradingConfig,
  condOrderService: ConditionalOrderService,
) {
  return {
    name: "trade_plan",
    description:
      "交易计划工具。一键创建完整交易计划：买入 + 自动设置止损/止盈。\n" +
      "• action=create — 创建交易计划（买入并自动设置止损和止盈条件单）\n" +
      "• action=list — 查看所有交易计划\n" +
      "• action=detail — 查看计划详情\n" +
      "• action=close — 手动关闭计划\n" +
      "• action=cancel — 取消计划（删除关联的条件单）",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["create", "list", "detail", "close", "cancel"],
          description: "操作类型",
        },
        symbol: { type: "string", description: "股票代码" },
        name: { type: "string", description: "股票名称（可选）" },
        entry_price: { type: "number", description: "入场价格" },
        quantity: { type: "number", description: "买入数量（股）" },
        stop_loss_price: {
          type: "number",
          description: "止损价格（低于入场价）",
        },
        take_profit_price: {
          type: "number",
          description: "止盈价格（高于入场价）",
        },
        trailing_stop_pct: {
          type: "number",
          description: "跟踪止损百分比 (0-100, 如 5 表示 5%回撤止损)。与 stop_loss_price 二选一",
        },
        notes: { type: "string", description: "交易备注/理由" },
        plan_id: { type: "string", description: "计划ID（detail/close/cancel 操作）" },
        close_price: { type: "number", description: "平仓价格（close 操作）" },
        active_only: { type: "boolean", description: "仅显示活跃计划 (list 操作, 默认 true)" },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      switch (action) {
        case "create": {
          const symbol = args.symbol as string;
          const entryPrice = args.entry_price as number;
          const quantity = args.quantity as number;
          const stopLossPrice = args.stop_loss_price as number | undefined;
          const takeProfitPrice = args.take_profit_price as number | undefined;
          const trailingStopPctRaw = args.trailing_stop_pct as number | undefined;
          const notes = args.notes as string | undefined;
          const name = args.name as string | undefined;

          if (!symbol || !entryPrice || !quantity) {
            return { error: "请提供 symbol, entry_price, quantity" };
          }
          if (!stopLossPrice && !trailingStopPctRaw) {
            return { error: "请至少提供 stop_loss_price 或 trailing_stop_pct 作为风险控制" };
          }
          if (stopLossPrice && stopLossPrice >= entryPrice) {
            return { error: "止损价应低于入场价" };
          }
          if (takeProfitPrice && takeProfitPrice <= entryPrice) {
            return { error: "止盈价应高于入场价" };
          }

          const plans = await loadPlans(config);
          const planId = `plan-${Date.now()}-${++planCounter}`;

          const plan: TradePlan = {
            id: planId,
            symbol,
            name,
            side: "long",
            entryPrice,
            quantity,
            stopLossPrice,
            takeProfitPrice,
            status: "active",
            notes,
            createdAt: new Date().toISOString(),
          };

          // Set up stop-loss conditional order
          if (stopLossPrice) {
            const slId = condOrderService.addStopLoss({
              symbol,
              entryPrice,
              stopPrice: stopLossPrice,
              quantity,
              strategyId: `trade-plan:${planId}`,
            });
            plan.stopLossOrderId = slId;
            plan.riskAmount = (entryPrice - stopLossPrice) * quantity;
          } else if (trailingStopPctRaw) {
            const trailPct =
              trailingStopPctRaw > 1 ? trailingStopPctRaw / 100 : trailingStopPctRaw;
            const tsId = condOrderService.addTrailingStop({
              symbol,
              entryPrice,
              trailPct,
              quantity,
              strategyId: `trade-plan:${planId}`,
            });
            plan.trailingStopOrderId = tsId;
            plan.trailingStopPct = trailPct;
            plan.riskAmount = entryPrice * trailPct * quantity;
          }

          // Set up take-profit conditional order
          if (takeProfitPrice) {
            const tpId = condOrderService.addTakeProfit({
              symbol,
              entryPrice,
              targetPrice: takeProfitPrice,
              quantity,
              strategyId: `trade-plan:${planId}`,
            });
            plan.takeProfitOrderId = tpId;
          }

          // Calculate risk/reward ratio
          if (stopLossPrice && takeProfitPrice) {
            const risk = entryPrice - stopLossPrice;
            const reward = takeProfitPrice - entryPrice;
            plan.riskRewardRatio = reward / risk;
          }

          plans.push(plan);
          await savePlans(plans, config);

          const result: Record<string, unknown> = {
            message: "交易计划已创建",
            planId,
            symbol,
            entryPrice: `¥${entryPrice.toFixed(2)}`,
            quantity,
            totalCost: `¥${(entryPrice * quantity).toFixed(2)}`,
          };

          if (stopLossPrice) {
            result.stopLoss = `¥${stopLossPrice.toFixed(2)} (${(((entryPrice - stopLossPrice) / entryPrice) * 100).toFixed(1)}%)`;
          }
          if (trailingStopPctRaw) {
            const pct = trailingStopPctRaw > 1 ? trailingStopPctRaw : trailingStopPctRaw * 100;
            result.trailingStop = `${pct.toFixed(1)}%`;
          }
          if (takeProfitPrice) {
            result.takeProfit = `¥${takeProfitPrice.toFixed(2)} (+${(((takeProfitPrice - entryPrice) / entryPrice) * 100).toFixed(1)}%)`;
          }
          if (plan.riskRewardRatio) {
            result.riskRewardRatio = `1:${plan.riskRewardRatio.toFixed(2)}`;
          }
          if (plan.riskAmount) {
            result.maxRisk = `¥${plan.riskAmount.toFixed(2)}`;
          }
          if (notes) result.notes = notes;

          return result;
        }

        case "list": {
          const activeOnly = (args.active_only as boolean) ?? true;
          const plans = await loadPlans(config);
          const filtered = activeOnly
            ? plans.filter((p) => p.status === "active")
            : plans;

          return {
            total: filtered.length,
            plans: filtered.map((p) => ({
              id: p.id,
              symbol: p.symbol,
              name: p.name,
              entry: `¥${p.entryPrice.toFixed(2)}`,
              quantity: p.quantity,
              stopLoss: p.stopLossPrice ? `¥${p.stopLossPrice.toFixed(2)}` : (p.trailingStopPct ? `跟踪 ${(p.trailingStopPct * 100).toFixed(1)}%` : "无"),
              takeProfit: p.takeProfitPrice ? `¥${p.takeProfitPrice.toFixed(2)}` : "无",
              riskReward: p.riskRewardRatio ? `1:${p.riskRewardRatio.toFixed(2)}` : "N/A",
              status: p.status,
              notes: p.notes,
              createdAt: p.createdAt,
              pnl: p.pnl != null ? `¥${p.pnl.toFixed(2)}` : undefined,
            })),
          };
        }

        case "detail": {
          const planId = args.plan_id as string;
          if (!planId) return { error: "请提供 plan_id" };
          const plans = await loadPlans(config);
          const plan = plans.find((p) => p.id === planId);
          if (!plan) return { error: `未找到计划 ${planId}` };

          // Check conditional order status
          const condOrders = condOrderService.listOrders();
          const slOrder = plan.stopLossOrderId
            ? condOrders.find((o) => o.id === plan.stopLossOrderId)
            : undefined;
          const tpOrder = plan.takeProfitOrderId
            ? condOrders.find((o) => o.id === plan.takeProfitOrderId)
            : undefined;
          const tsOrder = plan.trailingStopOrderId
            ? condOrders.find((o) => o.id === plan.trailingStopOrderId)
            : undefined;

          return {
            ...plan,
            entryPrice: `¥${plan.entryPrice.toFixed(2)}`,
            stopLossPrice: plan.stopLossPrice ? `¥${plan.stopLossPrice.toFixed(2)}` : undefined,
            takeProfitPrice: plan.takeProfitPrice ? `¥${plan.takeProfitPrice.toFixed(2)}` : undefined,
            riskRewardRatio: plan.riskRewardRatio ? `1:${plan.riskRewardRatio.toFixed(2)}` : undefined,
            riskAmount: plan.riskAmount ? `¥${plan.riskAmount.toFixed(2)}` : undefined,
            conditionalOrders: {
              stopLoss: slOrder ? { id: slOrder.id, triggered: slOrder.triggered } : undefined,
              takeProfit: tpOrder ? { id: tpOrder.id, triggered: tpOrder.triggered } : undefined,
              trailingStop: tsOrder
                ? {
                    id: tsOrder.id,
                    triggered: tsOrder.triggered,
                    currentStopPrice: tsOrder.trailingStopPrice
                      ? `¥${tsOrder.trailingStopPrice.toFixed(2)}`
                      : undefined,
                  }
                : undefined,
            },
          };
        }

        case "close": {
          const planId = args.plan_id as string;
          const closePrice = args.close_price as number;
          if (!planId) return { error: "请提供 plan_id" };
          if (!closePrice) return { error: "请提供 close_price (平仓价格)" };

          const plans = await loadPlans(config);
          const plan = plans.find((p) => p.id === planId);
          if (!plan) return { error: `未找到计划 ${planId}` };
          if (plan.status !== "active") return { error: `计划状态为 ${plan.status}，无法关闭` };

          // Remove associated conditional orders
          if (plan.stopLossOrderId) condOrderService.removeOrder(plan.stopLossOrderId);
          if (plan.takeProfitOrderId) condOrderService.removeOrder(plan.takeProfitOrderId);
          if (plan.trailingStopOrderId) condOrderService.removeOrder(plan.trailingStopOrderId);

          plan.status = "closed";
          plan.closedAt = new Date().toISOString();
          plan.closedPrice = closePrice;
          plan.pnl = (closePrice - plan.entryPrice) * plan.quantity;

          await savePlans(plans, config);

          return {
            message: "交易计划已关闭",
            planId,
            symbol: plan.symbol,
            entryPrice: `¥${plan.entryPrice.toFixed(2)}`,
            closePrice: `¥${closePrice.toFixed(2)}`,
            pnl: `¥${plan.pnl.toFixed(2)}`,
            pnlPct: `${(((closePrice - plan.entryPrice) / plan.entryPrice) * 100).toFixed(2)}%`,
            holdingDays: Math.ceil(
              (new Date(plan.closedAt).getTime() - new Date(plan.createdAt).getTime()) /
                (1000 * 60 * 60 * 24),
            ),
          };
        }

        case "cancel": {
          const planId = args.plan_id as string;
          if (!planId) return { error: "请提供 plan_id" };

          const plans = await loadPlans(config);
          const plan = plans.find((p) => p.id === planId);
          if (!plan) return { error: `未找到计划 ${planId}` };
          if (plan.status !== "active") return { error: `计划状态为 ${plan.status}，无法取消` };

          // Remove associated conditional orders
          if (plan.stopLossOrderId) condOrderService.removeOrder(plan.stopLossOrderId);
          if (plan.takeProfitOrderId) condOrderService.removeOrder(plan.takeProfitOrderId);
          if (plan.trailingStopOrderId) condOrderService.removeOrder(plan.trailingStopOrderId);

          plan.status = "cancelled";
          await savePlans(plans, config);

          return {
            message: "交易计划已取消，关联的条件单已删除",
            planId,
            symbol: plan.symbol,
          };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
